# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Pure deterministic merge planning for SudoSync v2."""
from __future__ import absolute_import
from copy import deepcopy
from . import SYNC_FIELDS

def version_key(version):
    """Return the total-order key used only to choose among equivalent values."""
    return (int(version.get("lc", 0)), int(version.get("seq", 0)), str(version.get("client_id", "")))


def _is_empty_initial_value(field, value):
    """Return True if value represents the default unwatched/empty state for an unedited item."""
    if field == "playcount":
        try:
            return int(value or 0) <= 0
        except (ValueError, TypeError):
            return True
    if field == "lastplayed":
        return not str(value or "").strip()
    if field == "userrating":
        if value is None:
            return True
        try:
            return int(value or 0) <= 0
        except (ValueError, TypeError):
            return True
    if field == "resume":
        if not isinstance(value, dict):
            return True
        try:
            return float(value.get("position") or 0.0) <= 0.0
        except (ValueError, TypeError):
            return True
    return False


def plan_field_merge(records):
    """Return per-field targets and explicit conflicts without mutating inputs.

    Logical clock and sequence determine freshness. Different clients reporting
    the same newest clock/sequence are concurrent: different values are reported
    as a conflict, not resolved by an arbitrary client-id ordering.
    When all newest candidates are at the initial unedited version (lc=0, seq=0)
    and one client has a non-empty state while another newly indexed client has
    an empty/unwatched state, the non-empty state is preferred unless multiple
    distinct non-empty values exist.
    """
    targets, target_versions, conflicts = {}, {}, []
    for field in SYNC_FIELDS:
        candidates = []
        for record in records:
            state = record.get("state") or {}
            versions = record.get("versions") or {}
            if field not in state or field not in versions:
                continue
            version = versions[field]
            candidates.append(((int(version.get("lc", 0)), int(version.get("seq", 0))), state[field], version))
        if not candidates:
            continue
        newest_key = max(item[0] for item in candidates)
        newest = [item for item in candidates if item[0] == newest_key]
        if newest_key == (0, 0) and len(newest) > 1:
            non_empty = [item for item in newest if not _is_empty_initial_value(field, item[1])]
            if non_empty:
                newest = non_empty
        values = []
        for _, value, _ in newest:
            if value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts.append({
                "field": field,
                "version": {"lc": newest_key[0], "seq": newest_key[1]},
                "values": [
                    {"value": deepcopy(value), "client_id": str(version.get("client_id", ""))}
                    for _, value, version in newest
                ],
            })
            continue
        targets[field] = deepcopy(values[0])
        # Identical concurrent values are safe; choose metadata deterministically.
        chosen = max((item[2] for item in newest), key=version_key)
        target_versions[field] = {"lc": chosen["lc"], "seq": chosen["seq"], "client_id": chosen["client_id"]}
    return {"target_state": targets, "target_versions": target_versions, "conflicts": conflicts}
