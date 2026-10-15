# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Build validated SudoSync snapshots from Kodi JSON-RPC library records.

This module is pure Python: it performs no Kodi calls and writes no files.
Callers persist the returned snapshot as the client's last-observed snapshot.
"""
from __future__ import absolute_import

from datetime import datetime, timezone
import math
import uuid

from . import FORMAT_NAME, SCHEMA_VERSION, SYNC_FIELDS
from .identity import identifiers_from_kodi_item, normalize_identifier
from .schema import validate_snapshot


class SnapshotBuildError(ValueError):
    """Kodi records could not be converted into a safe snapshot."""


def _utc_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _integer(value, label, minimum=0, maximum=2147483647):
    if type(value) is not int or not minimum <= value <= maximum:
        raise SnapshotBuildError("{} must be an integer in range {}..{}".format(label, minimum, maximum))
    return value


def _number(value, label, maximum=1000000000):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise SnapshotBuildError("{} must be a finite non-negative number".format(label))
    return float(value)


def _identifiers(item):
    try:
        return identifiers_from_kodi_item(item)
    except ValueError as exc:
        raise SnapshotBuildError(str(exc))


def _state(item):
    playcount = item.get("playcount", 0)
    if playcount is None:
        playcount = 0
    playcount = _integer(playcount, "playcount")
    lastplayed = item.get("lastplayed") or ""
    if not isinstance(lastplayed, str):
        raise SnapshotBuildError("lastplayed must be a string")
    resume_raw = item.get("resume")
    if not isinstance(resume_raw, dict):
        resume_raw = {}
    position = _number(resume_raw.get("position", 0), "resume.position")
    total = _number(resume_raw.get("total", 0), "resume.total")
    if total and position > total + 2.0:
        raise SnapshotBuildError("resume position exceeds media duration")
    rating = item.get("userrating")
    if rating in (None, 0):
        rating = None
    elif type(rating) is not int or not 1 <= rating <= 10:
        raise SnapshotBuildError("userrating must be empty or an integer from 1 to 10")
    return {
        "playcount": playcount,
        "lastplayed": lastplayed,
        "resume": {"position": position, "total": total},
        "userrating": rating,
    }


def _record_versions(state, prior, client_id, logical_clock):
    prior_state = prior.get("state", {}) if isinstance(prior, dict) else {}
    prior_versions = prior.get("versions", {}) if isinstance(prior, dict) else {}
    versions = {}
    for field in SYNC_FIELDS:
        old_version = prior_versions.get(field)
        if (isinstance(old_version, dict)
                and set(old_version) == {"lc", "seq", "client_id"}
                and type(old_version.get("lc")) is int and old_version["lc"] >= 0
                and type(old_version.get("seq")) is int and old_version["seq"] >= 0
                and isinstance(old_version.get("client_id"), str)):
            version = dict(old_version)
        else:
            version = {"lc": logical_clock, "seq": 0, "client_id": client_id}
        if field in prior_state and prior_state[field] != state[field]:
            version = {
                "lc": max(logical_clock, version["lc"]) + 1,
                "seq": version["seq"] + 1,
                "client_id": client_id,
            }
        versions[field] = version
    return versions


def build_snapshot(movies, episodes, client_id, client_name, previous_snapshot=None,
                   generated_at=None, logical_clock=0, max_records=100000):
    """Convert Kodi movie/episode arrays into a schema-validated snapshot.

    A changed field advances that field's local version relative to the previous
    local snapshot. The caller is responsible for persisting the returned
    snapshot atomically and advancing logical_clock from all observed versions.
    Duplicate strong identities within one media type are rejected rather than
    silently choosing a local item.
    """
    try:
        canonical_client = str(uuid.UUID(str(client_id)))
    except (ValueError, TypeError, AttributeError):
        raise SnapshotBuildError("client_id must be a UUID")
    if canonical_client != str(client_id).lower():
        raise SnapshotBuildError("client_id must be a canonical UUID")
    if not isinstance(client_name, str) or not 1 <= len(client_name.strip()) <= 128:
        raise SnapshotBuildError("client_name must contain 1 to 128 characters")
    if type(logical_clock) is not int or not 0 <= logical_clock <= 2147483647:
        raise SnapshotBuildError("logical_clock must be a non-negative integer")
    if not isinstance(movies, list) or not isinstance(episodes, list):
        raise SnapshotBuildError("movies and episodes must be arrays")
    if len(movies) + len(episodes) > max_records:
        raise SnapshotBuildError("library exceeds configured record limit")

    if previous_snapshot is not None:
        try:
            validate_snapshot(previous_snapshot, max_records=max_records)
        except (ValueError, TypeError) as exc:
            raise SnapshotBuildError("previous local snapshot is invalid; refusing to reset field versions: {}".format(exc))
        if previous_snapshot["client"]["id"] != canonical_client:
            raise SnapshotBuildError("previous snapshot belongs to a different client")

    previous_by_identity = {"movie": {}, "episode": {}}
    if isinstance(previous_snapshot, dict):
        for media_type, key in (("movie", "movies"), ("episode", "episodes")):
            records = previous_snapshot.get(key)
            if isinstance(records, list):
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get("ids"), dict):
                        continue
                    for alias in _identity_keys(media_type, record["ids"]):
                        previous_by_identity[media_type].setdefault(alias, []).append(record)

    seen = {"movie": {} , "episode": {}}
    output = {"movie": [], "episode": []}
    for media_type, source in (("movie", movies), ("episode", episodes)):
        for index, item in enumerate(source):
            if not isinstance(item, dict):
                raise SnapshotBuildError("{}[{}] must be an object".format(media_type, index))
            ids = _identifiers(item)
            identities = _identity_keys(media_type, ids)
            for identity in identities:
                if identity in seen[media_type]:
                    raise SnapshotBuildError("duplicate/overlapping strong identity in {} library: {}".format(media_type, identity))
            for identity in identities:
                seen[media_type][identity] = index
            prior_candidates = {}
            for identity in identities:
                for prior_record in previous_by_identity[media_type].get(identity, []):
                    prior_candidates[id(prior_record)] = prior_record
            if len(prior_candidates) > 1:
                raise SnapshotBuildError("current {} item matches multiple records in its previous snapshot".format(media_type))
            prior = next(iter(prior_candidates.values())) if prior_candidates else None
            if prior is not None:
                conflicting_kinds = sorted(
                    kind for kind in set(ids).intersection(prior["ids"])
                    if ids[kind] != prior["ids"][kind]
                )
                if conflicting_kinds:
                    raise SnapshotBuildError(
                        "current {} item has conflicting identifiers with its previous snapshot: {}"
                        .format(media_type, ", ".join(conflicting_kinds))
                    )
            state = _state(item)
            versions = _record_versions(state, prior, canonical_client, logical_clock)
            output[media_type].append({
                "type": media_type,
                "ids": ids,
                "state": state,
                "versions": versions,
            })

    snapshot = {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "client": {"id": canonical_client, "name": client_name.strip()},
        "generated_at": generated_at or _utc_timestamp(),
        "movies": output["movie"],
        "episodes": output["episode"],
    }
    try:
        validate_snapshot(snapshot, max_records=max_records)
    except (ValueError, TypeError) as exc:
        raise SnapshotBuildError("generated snapshot failed validation: {}".format(exc))
    return snapshot


def _identity_keys(media_type, ids):
    """Return every valid typed alias for conservative snapshot matching."""
    aliases = []
    for kind in ("sudosync", "imdb", "tmdb", "tvdb", "trakt"):
        value = normalize_identifier(kind, ids.get(kind))
        if value is not None:
            aliases.append((media_type, kind, value))
    return tuple(aliases)
