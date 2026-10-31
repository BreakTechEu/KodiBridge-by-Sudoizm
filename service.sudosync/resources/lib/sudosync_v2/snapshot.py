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
import logging
import math
import uuid

from . import FORMAT_NAME, SCHEMA_VERSION, SYNC_FIELDS
from .identity import identifiers_from_kodi_item, normalize_identifier
from .schema import validate_snapshot


logger = logging.getLogger(__name__)


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


def _record_versions(state, prior, client_id, logical_clock, has_previous_snapshot=False, suppress_local_changes=False):
    if not has_previous_snapshot:
        return {
            field: {"lc": 0, "seq": 0, "client_id": client_id}
            for field in SYNC_FIELDS
        }
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
        elif prior is None and has_previous_snapshot:
            # Newly discovered library items after the initial snapshot receive
            # version 0 so a library scan / NFO import cannot overwrite a peer's
            # remembered state.
            version = {"lc": 0, "seq": 0, "client_id": client_id}
        else:
            version = {"lc": logical_clock if not suppress_local_changes else 0, "seq": 0, "client_id": client_id}
        if field in prior_state and prior_state[field] != state[field]:
            preserve_existing_rating_edit = (
                field == "userrating"
                and prior_state.get("userrating") is not None
            )
            if suppress_local_changes and not preserve_existing_rating_edit:
                version = {"lc": 0, "seq": 0, "client_id": client_id}
            else:
                version = {
                    "lc": max(logical_clock, version["lc"]) + 1,
                    "seq": version["seq"] + 1,
                    "client_id": client_id,
                }
        versions[field] = version
    return versions


def build_snapshot(movies, episodes, client_id, client_name, previous_snapshot=None,
                   generated_at=None, logical_clock=0, max_records=100000,
                   suppress_local_changes=False, kept_kodi_ids_out=None,
                   renumbered_ids=None):
    """Convert Kodi movie/episode arrays into a schema-validated snapshot.

    A changed field advances that field's local version relative to the previous
    local snapshot. The caller is responsible for persisting the returned
    snapshot atomically and advancing logical_clock from all observed versions.
    Duplicate strong identities within one media type are rejected rather than
    silently choosing a local item.

    ``renumbered_ids`` maps an old KodiBridge ID to the new IDs it was replaced
    with by the duplicate repair (published on the NAS). An item whose previous
    record carries the old ID and which now carries one of the new IDs keeps its
    field history instead of failing as an identifier conflict.
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
    shared_external = {"movie": 0, "episode": 0}
    output = {"movie": [], "episode": []}
    kept_ids = {"movie": [], "episode": []}
    for media_type, source in (("movie", movies), ("episode", episodes)):
        if media_type == "episode":
            sudo_counts = {}
            for item in source:
                if isinstance(item, dict):
                    ids_temp = _identifiers(item)
                    s_id = ids_temp.get("sudosync")
                    if s_id:
                        sudo_counts[s_id] = sudo_counts.get(s_id, 0) + 1
            if any(cnt > 1 for cnt in sudo_counts.values()):
                for item in source:
                    if isinstance(item, dict):
                        u = item.get("uniqueid")
                        if isinstance(u, dict) and u.get("sudosync"):
                            s_id = normalize_identifier("sudosync", u.get("sudosync"))
                            if s_id and sudo_counts.get(s_id, 0) > 1:
                                season = item.get("season")
                                episode = item.get("episode")
                                if season is not None and episode is not None:
                                    try:
                                        qual = "{}:s{:02d}e{:02d}".format(s_id, int(season), int(episode))
                                    except (ValueError, TypeError):
                                        qual = "{}:s{}e{}".format(s_id, season, episode)
                                    u["sudosync"] = qual
                                    logger.debug(
                                        "Disambiguated shared sudosync ID for multi-episode item '%s' -> %s",
                                        item.get("file") or item.get("label") or "episode",
                                        qual,
                                    )

        for index, item in enumerate(source):
            if not isinstance(item, dict):
                raise SnapshotBuildError("{}[{}] must be an object".format(media_type, index))
            ids = _identifiers(item)
            has_sudosync = bool(ids.get("sudosync") and str(ids.get("sudosync")).strip())
            identities = _identity_keys(media_type, ids)
            duplicate_detected = False
            for identity in identities:
                if identity in seen[media_type]:
                    prev_index, prev_has_sudosync = seen[media_type][identity]
                    if identity[1] == "sudosync":
                        logger.warning(
                            "duplicate/overlapping sudosync identity in %s library: %s "
                            "(keeping first record: '%s', skipping duplicate: '%s')",
                            media_type, identity,
                            source[prev_index].get("file") or source[prev_index].get("label") or prev_index,
                            item.get("file") or item.get("label") or index,
                        )
                        duplicate_detected = True
                        break
                    elif not (has_sudosync and prev_has_sudosync):
                        raise SnapshotBuildError("duplicate/overlapping strong identity in {} library: {}".format(media_type, identity))
                    else:
                        # Expected for one external episode split into several
                        # parts (e.g. a finale aired in parts): each part has its
                        # own SudoSync ID. Logged per pair at DEBUG and summarized
                        # once below.
                        shared_external[media_type] += 1
                        logger.debug(
                            "shared external identity in %s library: %s "
                            "(differentiated by unique SudoSync IDs: %s vs %s; treating SudoSync ID as authoritative)",
                            media_type, identity,
                            source[prev_index].get("uniqueid", {}).get("sudosync") if isinstance(source[prev_index], dict) else "",
                            ids.get("sudosync"),
                        )
            if duplicate_detected:
                continue
            for identity in identities:
                seen[media_type][identity] = (index, has_sudosync)

            prior_candidates = {}
            for identity in identities:
                for prior_record in previous_by_identity[media_type].get(identity, []):
                    prior_candidates[id(prior_record)] = prior_record
            if len(prior_candidates) > 1:
                if has_sudosync:
                    matching_sudosync = [
                        rec for rec in prior_candidates.values()
                        if rec.get("ids", {}).get("sudosync") == ids.get("sudosync")
                    ]
                    if len(matching_sudosync) == 1:
                        prior_candidates = {id(matching_sudosync[0]): matching_sudosync[0]}
                if len(prior_candidates) > 1:
                    logger.warning(
                        "current %s item matches multiple records in previous snapshot; selecting first candidate",
                        media_type,
                    )
                    prior_candidates = {id(next(iter(prior_candidates.values()))): next(iter(prior_candidates.values()))}
            prior = next(iter(prior_candidates.values())) if prior_candidates else None
            if prior is not None:
                conflicting_kinds = sorted(
                    kind for kind in set(ids).intersection(prior["ids"])
                    if ids[kind] != prior["ids"][kind]
                )
                if conflicting_kinds:
                    if has_sudosync and (
                        ids.get("sudosync") == prior.get("ids", {}).get("sudosync")
                        or ids.get("sudosync", "").startswith(prior.get("ids", {}).get("sudosync", "") + ":")
                    ):
                        logger.warning(
                            "current %s item has updated identifiers compared to previous snapshot: %s "
                            "(sudosync ID: %s); accepting updated identifiers",
                            media_type,
                            ", ".join(conflicting_kinds),
                            ids.get("sudosync"),
                        )
                    elif conflicting_kinds == ["sudosync"] and _is_renumbered(
                        renumbered_ids, prior.get("ids", {}).get("sudosync"), ids.get("sudosync")
                    ):
                        logger.info(
                            "%s item got a new KodiBridge ID from the duplicate repair: %s -> %s; "
                            "keeping its field history",
                            media_type,
                            prior.get("ids", {}).get("sudosync"),
                            ids.get("sudosync"),
                        )
                    else:
                        raise SnapshotBuildError(
                            "current {} item has conflicting identifiers with its previous snapshot: {}"
                            .format(media_type, ", ".join(conflicting_kinds))
                        )
            state = _state(item)
            versions = _record_versions(
                state,
                prior,
                canonical_client,
                logical_clock,
                has_previous_snapshot=(previous_snapshot is not None),
                suppress_local_changes=bool(suppress_local_changes),
            )
            output[media_type].append({
                "type": media_type,
                "ids": ids,
                "state": state,
                "versions": versions,
            })
            id_key = "movieid" if media_type == "movie" else "episodeid"
            kept_ids[media_type].append(item.get(id_key))

    _report_shared_external_identities(canonical_client, shared_external)

    if isinstance(kept_kodi_ids_out, dict):
        kept_kodi_ids_out["movie"] = kept_ids["movie"]
        kept_kodi_ids_out["episode"] = kept_ids["episode"]

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


_LAST_SHARED_EXTERNAL_REPORT = {}


def _sudosync_base(value):
    """KodiBridge ID without the ':sNNeNN' qualifier of a multi-episode file."""
    return str(value or "").strip().casefold().split(":", 1)[0]


def _sudosync_qualifier(value):
    parts = str(value or "").strip().casefold().split(":", 1)
    return parts[1] if len(parts) > 1 else ""


RENUMBER_CHAIN_LIMIT = 8


def _is_renumbered(renumbered_ids, old_value, new_value):
    """True when the duplicate repair replaced old_value with new_value.

    Follows chains (repair, rollback, repair again: A -> B -> A -> C) for a
    device that missed the intermediate steps. Episode qualifiers must agree
    when both IDs carry one; a qualifier may appear or disappear, because a
    repaired ID is no longer shared by several items.
    """
    if not isinstance(renumbered_ids, dict) or not old_value or not new_value:
        return False
    old_q, new_q = _sudosync_qualifier(old_value), _sudosync_qualifier(new_value)
    if old_q and new_q and old_q != new_q:
        return False
    start, goal = _sudosync_base(old_value), _sudosync_base(new_value)
    seen, frontier = {start}, [start]
    for _step in range(RENUMBER_CHAIN_LIMIT):
        following = []
        for current in frontier:
            targets = renumbered_ids.get(current)
            if not isinstance(targets, (list, tuple)):
                continue
            for target in targets:
                key = _sudosync_base(target)
                if key == goal:
                    return True
                if key not in seen:
                    seen.add(key)
                    following.append(key)
        if not following:
            return False
        frontier = following
    return False


def _report_shared_external_identities(client_id, counts):
    """Log one summary of external IDs shared by items with distinct SudoSync IDs.

    The summary is logged at INFO only when the numbers change for this client,
    so a periodic sync cycle does not repeat it; otherwise it goes to DEBUG.
    """
    summary = (int(counts.get("movie", 0)), int(counts.get("episode", 0)))
    if not any(summary):
        _LAST_SHARED_EXTERNAL_REPORT.pop(client_id, None)
        return
    changed = _LAST_SHARED_EXTERNAL_REPORT.get(client_id) != summary
    _LAST_SHARED_EXTERNAL_REPORT[client_id] = summary
    (logger.info if changed else logger.debug)(
        "items sharing external identifiers but distinguished by SudoSync IDs: "
        "%d movie pair(s), %d episode pair(s); SudoSync ID is authoritative (expected for multi-part items)",
        summary[0], summary[1],
    )


COVERAGE_MAX_EXAMPLES = 5
_LAST_COVERAGE_REPORT = {}


def _item_label(item):
    label = item.get("file") or item.get("label") or item.get("title") or ""
    if item.get("showtitle") and item.get("season") is not None and item.get("episode") is not None:
        try:
            return "{} S{:02d}E{:02d} [{}]".format(item["showtitle"], int(item["season"]), int(item["episode"]), label)
        except (TypeError, ValueError):
            pass
    return str(label)


def kodibridge_id_coverage(movies, episodes, max_examples=COVERAGE_MAX_EXAMPLES):
    """Count KodiBridge (SudoSync) IDs in the raw Kodi library items.

    Computed before the snapshot is built, so it is available even when the
    snapshot cannot be built. Per media type: ``total``, ``with_id``,
    ``missing`` (no KodiBridge ID), ``duplicates`` (one KodiBridge ID on several
    items that are not parts of one multi-episode file) and ``ambiguous``
    (an IMDb/TMDb/TVDB/Trakt ID shared by several items when at least one of
    them has no KodiBridge ID - the case that stops synchronization), each
    with a few examples.
    """
    result = {}
    for media_type, source in (("movie", movies), ("episode", episodes)):
        total = with_id = 0
        missing, by_sid, ext_owners = [], {}, {}
        for item in source if isinstance(source, list) else []:
            if not isinstance(item, dict):
                continue
            total += 1
            try:
                ids = identifiers_from_kodi_item(item)
            except ValueError:
                ids = {}
            label = _item_label(item)
            sid = ids.get("sudosync")
            if sid:
                with_id += 1
                by_sid.setdefault(sid, []).append((item.get("file") or "#{}".format(total), label))
            else:
                missing.append(label)
            for kind, value in ids.items():
                if kind != "sudosync":
                    ext_owners.setdefault((kind, value), []).append((bool(sid), label))
        duplicates = []
        for sid, entries in sorted(by_sid.items()):
            # One media file may hold several library items (episodes of a
            # multi-episode file); one ID on several files is a duplicate.
            if len({media_file for media_file, _ in entries}) < 2:
                continue
            duplicates.append("{} ({})".format(sid, "; ".join(label for _, label in entries[:3])))
        ambiguous = []
        for (kind, value), owners in sorted(ext_owners.items()):
            if len(owners) > 1 and not all(has_sid for has_sid, _ in owners):
                ambiguous.append("{}:{} ({})".format(kind, value, "; ".join(label for _, label in owners[:3])))
        result[media_type] = {
            "total": total,
            "with_id": with_id,
            "missing": len(missing),
            "duplicates": len(duplicates),
            "ambiguous": len(ambiguous),
            "examples": {
                "missing": sorted(missing)[:max_examples],
                "duplicates": duplicates[:max_examples],
                "ambiguous": ambiguous[:max_examples],
            },
        }
    return result


def coverage_has_problems(coverage):
    return any(
        isinstance(coverage.get(t), dict)
        and (coverage[t].get("missing") or coverage[t].get("duplicates") or coverage[t].get("ambiguous"))
        for t in ("movie", "episode")
    ) or bool(coverage.get("unreadable_nfo"))


def report_kodibridge_id_coverage(client_id, coverage):
    """Log the coverage once per change: WARNING with examples when incomplete."""
    key = repr(sorted((t, sorted((k, v) for k, v in coverage.get(t, {}).items() if k != "examples"))
                      for t in ("movie", "episode"))) + repr(coverage.get("unreadable_nfo"))
    if _LAST_COVERAGE_REPORT.get(client_id) == key:
        return
    _LAST_COVERAGE_REPORT[client_id] = key
    lines = []
    for media_type, name in (("movie", "movies"), ("episode", "episodes")):
        c = coverage.get(media_type) or {}
        lines.append("{}: {}/{} with KodiBridge ID, missing {}, duplicate IDs {}, ambiguous external IDs {}".format(
            name, c.get("with_id", 0), c.get("total", 0), c.get("missing", 0), c.get("duplicates", 0),
            c.get("ambiguous", 0)))
    if coverage.get("unreadable_nfo"):
        lines.append("unreadable .nfo files: {}".format(coverage["unreadable_nfo"]))
    if not coverage_has_problems(coverage):
        logger.info("KodiBridge ID coverage complete: %s", "; ".join(lines))
        return
    examples = []
    for media_type in ("movie", "episode"):
        ex = (coverage.get(media_type) or {}).get("examples") or {}
        for kind in ("missing", "duplicates", "ambiguous"):
            for entry in ex.get(kind) or []:
                examples.append("{} {} {}".format(media_type, kind, entry))
    logger.warning("KodiBridge ID coverage incomplete: %s. Examples: %s",
                   "; ".join(lines), " | ".join(examples) or "-")


def _identity_keys(media_type, ids):
    """Return every valid typed alias for conservative snapshot matching."""
    aliases = []
    for kind in ("sudosync", "imdb", "tmdb", "tvdb", "trakt"):
        value = normalize_identifier(kind, ids.get(kind))
        if value is not None:
            aliases.append((media_type, kind, value))
    return tuple(aliases)
