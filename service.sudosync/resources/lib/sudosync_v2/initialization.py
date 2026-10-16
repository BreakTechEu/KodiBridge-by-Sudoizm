# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Explicit conservative bootstrap plan for a chosen SudoSync base client."""
from __future__ import absolute_import

from copy import deepcopy

from .identity import strong_aliases
from .schema import validate_snapshot
from .planning import PlanningError, _validate_local_ids


def _is_empty(field, value):
    if field == "playcount":
        return value == 0
    if field == "lastplayed":
        return value == ""
    if field == "resume":
        return value == {"position": 0.0, "total": 0.0} or value == {"position": 0, "total": 0}
    if field == "userrating":
        return value is None
    return False


def _initial_field_decision(field, base_value, target_value):
    """Return (value-to-write or sentinel, conflict flag); never overwrite richer target data."""
    if base_value == target_value:
        return None, False
    base_empty = _is_empty(field, base_value)
    target_empty = _is_empty(field, target_value)
    if target_empty and not base_empty:
        return deepcopy(base_value), False
    if base_empty:
        return None, False
    if field == "playcount":
        # Play count is monotonic for bootstrap: retain whichever side is higher.
        return (deepcopy(base_value) if base_value > target_value else None), False
    # Two non-empty, differing timestamps/resume points/ratings are ambiguous.
    return None, True


def build_initialization_plan(base_snapshot, target_snapshot, target_kodi_ids, max_records=100000):
    """Plan a one-time import from the chosen base to a target Kodi.

    Existing target data is preserved when it is more informative. Conflicting
    non-empty lastplayed/resume/userrating values are reported, not overwritten.
    The function does not write to Kodi, update snapshots, or select the base.
    """
    try:
        validate_snapshot(base_snapshot, max_records=max_records)
        validate_snapshot(target_snapshot, max_records=max_records)
        if base_snapshot["client"]["id"] == target_snapshot["client"]["id"]:
            raise PlanningError("base and target must be different clients")
        local_ids = _validate_local_ids(target_snapshot, target_kodi_ids)
    except PlanningError:
        raise
    except (ValueError, TypeError) as exc:
        raise PlanningError("invalid initialization input: {}".format(exc))

    changes, conflicts, ambiguous = [], [], []
    matched = 0
    unmatched = 0
    for media_type, key in (("movie", "movies"), ("episode", "episodes")):
        base_records = base_snapshot[key]
        target_records = target_snapshot[key]
        base_by_alias = {}
        for index, record in enumerate(base_records):
            for alias in strong_aliases(media_type, record["ids"]):
                base_by_alias.setdefault(alias, []).append((index, record))
        for target_index, target in enumerate(target_records):
            aliases = strong_aliases(media_type, target["ids"])
            if not aliases:
                unmatched += 1
                continue
            candidates = {}
            for alias in aliases:
                for base_index, record in base_by_alias.get(alias, []):
                    candidates[base_index] = record
            if not candidates:
                unmatched += 1
                continue
            if len(candidates) != 1:
                ambiguous.append({
                    "type": media_type,
                    "target_kodi_id": local_ids[media_type][target_index],
                    "reason": "target identity matches multiple base records",
                })
                continue
            base_record = next(iter(candidates.values()))
            # Any conflicting identifier of the same kind means that the shared
            # alias may have joined two distinct media identities.
            conflict_kinds = []
            for kind in set(base_record["ids"]).intersection(target["ids"]):
                if base_record["ids"][kind] != target["ids"][kind]:
                    conflict_kinds.append(kind)
            if conflict_kinds:
                ambiguous.append({
                    "type": media_type,
                    "target_kodi_id": local_ids[media_type][target_index],
                    "reason": "conflicting identifiers: " + ", ".join(sorted(conflict_kinds)),
                })
                continue

            matched += 1
            field_changes = {}
            field_target_versions = {}
            base_versions = base_record.get("versions") if isinstance(base_record.get("versions"), dict) else {}
            for field in ("playcount", "lastplayed", "resume", "userrating"):
                base_value = base_record["state"][field]
                target_value = target["state"][field]
                value, conflict = _initial_field_decision(field, base_value, target_value)
                if conflict:
                    conflicts.append({
                        "type": media_type,
                        "target_kodi_id": local_ids[media_type][target_index],
                        "field": field,
                        "base_value": deepcopy(base_value),
                        "target_value": deepcopy(target_value),
                    })
                elif value is not None and value != target_value:
                    field_changes[field] = value
                    if isinstance(base_versions.get(field), dict):
                        field_target_versions[field] = deepcopy(base_versions[field])
            if field_changes:
                changes.append({
                    "type": media_type,
                    "kodi_id": local_ids[media_type][target_index],
                    "identity": sorted("{}:{}".format(kind, value) for kind, value in target["ids"].items()),
                    "before": deepcopy(target["state"]),
                    "changes": field_changes,
                    "target_versions": field_target_versions,
                })

    changes.sort(key=lambda item: (item["type"], item["kodi_id"]))
    conflicts.sort(key=lambda item: (item["type"], item["target_kodi_id"], item["field"]))
    ambiguous.sort(key=lambda item: (item["type"], item["target_kodi_id"]))
    field_counts = {field: 0 for field in ("playcount", "lastplayed", "resume", "userrating")}
    for item in changes:
        for field in item["changes"]:
            field_counts[field] += 1
    return {
        "format": "SudoSync v2 initialization plan",
        "schema_version": 1,
        "base_client_id": base_snapshot["client"]["id"],
        "target_client_id": target_snapshot["client"]["id"],
        "matched_records": matched,
        "unmatched_records": unmatched,
        "changes": changes,
        "change_count": len(changes),
        "field_counts": field_counts,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "ambiguous": ambiguous,
        "ambiguous_count": len(ambiguous),
    }
