"""Pure deterministic merge planning for SudoSync v2."""
from __future__ import absolute_import
from copy import deepcopy
from . import SYNC_FIELDS

def version_key(version):
    """Logical clock and sequence are primary; client id breaks ties deterministically."""
    return (int(version.get("lc", 0)), int(version.get("seq", 0)), str(version.get("client_id", "")))

def plan_field_merge(records):
    """Return per-field targets and explicit conflicts without mutating inputs.

    Each record must contain state and versions mappings. If the newest version
    is tied but values disagree, report a conflict instead of picking a winner.
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
            candidates.append((version_key(version), state[field], version))
        if not candidates:
            continue
        newest_key = max(item[0] for item in candidates)
        newest = [item for item in candidates if item[0] == newest_key]
        values = []
        for _, value, _ in newest:
            if value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts.append({
                "field": field,
                "version": {"lc": newest_key[0], "seq": newest_key[1], "client_id": newest_key[2]},
                "values": deepcopy(values),
            })
            continue
        targets[field] = deepcopy(values[0])
        chosen = newest[0][2]
        target_versions[field] = {"lc": chosen["lc"], "seq": chosen["seq"], "client_id": chosen["client_id"]}
    return {"target_state": targets, "target_versions": target_versions, "conflicts": conflicts}
