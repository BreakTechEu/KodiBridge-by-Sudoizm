# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Conservative cross-client dry-run planning for SudoSync v2.

The planner is pure Python. It never writes to Kodi or shared storage. Local
Kodi IDs are passed separately because they are machine-local and must never be
stored in portable snapshots.
"""
from __future__ import absolute_import

from copy import deepcopy

from . import SYNC_FIELDS
from .identity import strong_aliases
from .merge import plan_field_merge
from .schema import validate_snapshot


class PlanningError(ValueError):
    """Snapshots or local Kodi ID mappings are inconsistent."""


def _validate_local_ids(snapshot, local_kodi_ids):
    if not isinstance(local_kodi_ids, dict) or set(local_kodi_ids) != {"movie", "episode"}:
        raise PlanningError("local_kodi_ids must contain movie and episode mappings")
    result = {}
    for media_type, key in (("movie", "movies"), ("episode", "episodes")):
        ids = local_kodi_ids[media_type]
        records = snapshot[key]
        if not isinstance(ids, (list, tuple)) or len(ids) != len(records):
            raise PlanningError("{} local ID list must align exactly with snapshot records".format(media_type))
        normalized = []
        for index, kodi_id in enumerate(ids):
            if type(kodi_id) is not int or kodi_id < 1:
                raise PlanningError("{} local ID at index {} must be a positive integer".format(media_type, index))
            normalized.append(kodi_id)
        result[media_type] = normalized
    return result


def build_sync_plan(local_snapshot, peer_snapshots, local_kodi_ids, max_records=100000):
    """Build a dry-run plan to update only already-existing local library items.

    Each local_kodi_ids collection must be in the same order as the corresponding
    Kodi arrays passed to snapshot.build_snapshot. Remote-only records are never
    added to a Kodi library. Conflicted or ambiguous fields are omitted.
    """
    try:
        validate_snapshot(local_snapshot, max_records=max_records)
        if not isinstance(peer_snapshots, (list, tuple)):
            raise PlanningError("peer_snapshots must be a list")
        all_snapshots = [local_snapshot] + list(peer_snapshots)
        total_records = 0
        client_ids = set()
        for snapshot in all_snapshots:
            validate_snapshot(snapshot, max_records=max_records)
            client_id = snapshot["client"]["id"]
            if client_id in client_ids:
                raise PlanningError("duplicate client snapshot: " + client_id)
            client_ids.add(client_id)
            total_records += len(snapshot["movies"]) + len(snapshot["episodes"])
        if total_records > max_records * max(1, len(all_snapshots)):
            raise PlanningError("combined snapshots exceed the configured record limit")
    except PlanningError:
        raise
    except (ValueError, TypeError) as exc:
        raise PlanningError("invalid snapshot input: {}".format(exc))

    local_ids = _validate_local_ids(local_snapshot, local_kodi_ids)
    local_client_id = local_snapshot["client"]["id"]
    components_by_type = {"movie": [], "episode": []}
    records_without_ids = 0

    for media_type, key in (("movie", "movies"), ("episode", "episodes")):
        nodes = []
        alias_owner = {}
        parent = []

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left, right):
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_right] = root_left

        for snapshot in all_snapshots:
            for index, record in enumerate(snapshot[key]):
                aliases = strong_aliases(media_type, record["ids"])
                if not aliases:
                    records_without_ids += 1
                node_index = len(nodes)
                nodes.append({
                    "client_id": snapshot["client"]["id"],
                    "client_name": snapshot["client"]["name"],
                    "media_type": media_type,
                    "record": record,
                    "local_index": index if snapshot["client"]["id"] == local_client_id else None,
                    "aliases": aliases,
                })
                parent.append(node_index)
                for alias in aliases:
                    if alias in alias_owner:
                        union(node_index, alias_owner[alias])
                    else:
                        alias_owner[alias] = node_index

        groups = {}
        for index, node in enumerate(nodes):
            groups.setdefault(find(index), []).append(node)

        for members in groups.values():
            aliases = set()
            for member in members:
                aliases.update(member["aliases"])
            if not aliases:
                continue
            components_by_type[media_type].append(members)

    changes = []
    conflicts = []
    ambiguous = []
    shared_clusters = 0
    safe_shared_clusters = 0

    for media_type in ("movie", "episode"):
        for members in components_by_type[media_type]:
            client_set = {member["client_id"] for member in members}
            if len(client_set) < 2:
                continue
            shared_clusters += 1
            alias_values = {}
            for member in members:
                for kind, value in member["record"]["ids"].items():
                    alias_values.setdefault(kind, set()).add(value)
            reasons = []
            if len(client_set) != len(members):
                reasons.append("multiple records from one client share an identity cluster")
            conflicting_identifier_types = sorted(kind for kind, values in alias_values.items() if len(values) > 1)
            if conflicting_identifier_types:
                reasons.append("conflicting identifiers: " + ", ".join(conflicting_identifier_types))
            if reasons:
                ambiguous.append({
                    "type": media_type,
                    "clients": sorted(client_set),
                    "reasons": reasons,
                    "record_count": len(members),
                })
                continue

            merged = plan_field_merge([member["record"] for member in members])
            for conflict in merged["conflicts"]:
                conflicts.append({
                    "type": media_type,
                    "clients": sorted(client_set),
                    "field": conflict["field"],
                    "version": conflict["version"],
                    "values": conflict["values"],
                })

            local_members = [member for member in members if member["client_id"] == local_client_id]
            if len(local_members) != 1:
                continue
            local_member = local_members[0]
            local_index = local_member["local_index"]
            kodi_id = local_ids[media_type][local_index]
            field_changes = {}
            field_target_versions = {}
            for field in SYNC_FIELDS:
                if field in merged["target_state"] and merged["target_state"][field] != local_member["record"]["state"][field]:
                    field_changes[field] = merged["target_state"][field]
                    field_target_versions[field] = deepcopy(merged["target_versions"][field])
            if field_changes:
                changes.append({
                    "type": media_type,
                    "kodi_id": kodi_id,
                    "identity": sorted(["{}:{}".format(kind, value) for kind, value in local_member["record"]["ids"].items()]),
                    "before": dict(local_member["record"]["state"]),
                    "changes": field_changes,
                    "target_versions": field_target_versions,
                })
            if not merged["conflicts"]:
                safe_shared_clusters += 1

    changes.sort(key=lambda item: (item["type"], item["kodi_id"]))
    conflicts.sort(key=lambda item: (item["type"], item["field"], item["clients"]))
    ambiguous.sort(key=lambda item: (item["type"], item["clients"]))
    field_counts = {field: 0 for field in SYNC_FIELDS}
    for item in changes:
        for field in item["changes"]:
            field_counts[field] += 1

    return {
        "format": "SudoSync v2 dry-run plan",
        "schema_version": 1,
        "local_client_id": local_client_id,
        "shared_clusters": shared_clusters,
        "safe_shared_clusters": safe_shared_clusters,
        "ambiguous_clusters": len(ambiguous),
        "records_without_strong_id": records_without_ids,
        "changes": changes,
        "change_count": len(changes),
        "field_counts": field_counts,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "ambiguous": ambiguous,
    }


def apply_plan_to_snapshot(snapshot, plan, max_records=100000):
    """Return a new snapshot with applied plan changes and preserved target_versions.

    Preserving the winning version from ``target_versions`` (feedback-loop guard)
    ensures that applied remote changes do not bounce back as new local edits on
    subsequent sync cycles.
    """
    try:
        validate_snapshot(snapshot, max_records=max_records)
    except (ValueError, TypeError) as exc:
        raise PlanningError("invalid snapshot input: {}".format(exc))
    if not isinstance(plan, dict) or not isinstance(plan.get("changes"), list):
        raise PlanningError("plan must contain a changes list")
    target_client = plan.get("local_client_id") or plan.get("target_client_id")
    if target_client != snapshot["client"]["id"]:
        raise PlanningError("plan does not belong to this snapshot's client")

    updated = deepcopy(snapshot)
    records_by_alias = {"movie": {}, "episode": {}}
    for media_type, key in (("movie", "movies"), ("episode", "episodes")):
        for record in updated[key]:
            for alias in strong_aliases(media_type, record["ids"]):
                records_by_alias[media_type].setdefault(alias, []).append(record)

    for item in plan["changes"]:
        if not isinstance(item, dict) or item.get("type") not in ("movie", "episode"):
            raise PlanningError("plan contains an invalid change item")
        media_type = item["type"]
        identity = item.get("identity") or []
        if not isinstance(identity, list) or not identity:
            raise PlanningError("plan item lacks strong identity")
        candidates = {}
        for entry in identity:
            if not isinstance(entry, str) or ":" not in entry:
                raise PlanningError("plan item contains malformed identity")
            kind, value = entry.split(":", 1)
            for record in records_by_alias[media_type].get((media_type, kind, value), []):
                candidates[id(record)] = record
        if len(candidates) != 1:
            raise PlanningError("plan item identity does not uniquely match one snapshot record")
        record = next(iter(candidates.values()))
        changes = item.get("changes") or {}
        target_versions = item.get("target_versions") or {}
        for field, value in changes.items():
            if field not in SYNC_FIELDS:
                raise PlanningError("unsupported synchronized field: " + str(field))
            record["state"][field] = deepcopy(value)
            if field in target_versions and isinstance(target_versions[field], dict):
                record["versions"][field] = deepcopy(target_versions[field])

    validate_snapshot(updated, max_records=max_records)
    return updated
