# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------

from collections import Counter, defaultdict
import re
import unicodedata

STRONG_ID_TYPES = ("imdb", "tmdb", "tvdb", "trakt", "sudosync")


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _aliases(record):
    kind = str(record.get("type") or "").strip().lower()
    ids = record.get("ids") if isinstance(record.get("ids"), dict) else {}
    result = set()
    for key in STRONG_ID_TYPES:
        value = ids.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text:
            result.add("{}:{}:{}".format(kind, key, text))
    return result



def _norm_filename(path):
    text = str(path or "").replace("\\", "/").rstrip("/")
    name = text.rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name).casefold()
    # Ignore punctuation differences that commonly appear between Windows and SMB names.
    return re.sub(r"[^\w]+", "", name, flags=re.UNICODE)


def _norm_label(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", value)


def _semantic_signature(record):
    if record.get("type") == "episode":
        return (
            "episode",
            _norm_label(record.get("showtitle")),
            _safe_int(record.get("season"), 0),
            _safe_int(record.get("episode"), 0),
            _norm_label(record.get("title")),
        )
    return (
        "movie",
        _norm_label(record.get("title") or record.get("originaltitle")),
        _safe_int(record.get("year"), 0),
    )


def _path_family(path):
    value = str(path or "").strip().lower()
    if value.startswith(("smb://", "nfs://")):
        return "network"
    if re.match(r"^[a-z]:[\\/]", value) or value.startswith("/"):
        return "local"
    return "other"


def _safe_mirror_cluster(cluster):
    """Recognise only very conservative duplicate copies of one media item.

    A duplicate inside one Kodi is accepted as a mirror only when it is either
    the exact same path repeated, or exactly one local filesystem copy plus one
    network copy with the same filename and semantic identity.  This deliberately
    does NOT collapse bonus features, alternate season numbering or multi-episode
    files that merely share an external ID.
    """
    per_client = defaultdict(list)
    for node in cluster:
        per_client[node["client_id"]].append(node)

    has_duplicate = False
    for members in per_client.values():
        if len(members) <= 1:
            continue
        has_duplicate = True
        if len(members) != 2:
            return False
        a, b = members
        ra, rb = a["record"], b["record"]
        if _semantic_signature(ra) != _semantic_signature(rb):
            return False
        pa = str(ra.get("file") or "")
        pb = str(rb.get("file") or "")
        if pa.replace("\\", "/").casefold() == pb.replace("\\", "/").casefold():
            continue
        families = {_path_family(pa), _path_family(pb)}
        if families != {"local", "network"}:
            return False
        if not _norm_filename(pa) or _norm_filename(pa) != _norm_filename(pb):
            return False
    return has_duplicate

def _display(record):
    if record.get("type") == "episode":
        return "{} S{:02d}E{:02d} â€” {}".format(
            record.get("showtitle") or "?",
            _safe_int(record.get("season"), 0),
            _safe_int(record.get("episode"), 0),
            record.get("title") or "?",
        )
    year = _safe_int(record.get("year"), 0)
    return "{} ({})".format(record.get("title") or "?", year or "?")


def _state(record):
    value = record.get("state") if isinstance(record.get("state"), dict) else {}
    resume = value.get("resume") if isinstance(value.get("resume"), dict) else {}
    rating = value.get("userrating")
    try:
        rating = int(rating) if rating is not None else None
    except Exception:
        rating = None
    return {
        "playcount": _safe_int(value.get("playcount"), 0),
        "lastplayed": str(value.get("lastplayed") or ""),
        "resume": {
            "position": round(_safe_float(resume.get("position"), 0.0), 3),
            "total": round(_safe_float(resume.get("total"), 0.0), 3),
        },
        "userrating": rating,
    }


def _resume_key(value):
    return (
        round(_safe_float(value.get("position"), 0.0), 1),
        round(_safe_float(value.get("total"), 0.0), 1),
    )


def _merge_conservative_state(nodes):
    """Return the original conservative first-run merge for the supplied nodes."""
    states = [_state(node["record"]) for node in nodes]
    conflicts = []

    # First import: never lose an already recorded play count.
    playcount = max([s["playcount"] for s in states] or [0])

    # Kodi dates are lexicographically sortable in the exported format.
    lastplayed_values = [s["lastplayed"] for s in states if s["lastplayed"]]
    lastplayed = max(lastplayed_values) if lastplayed_values else ""

    # Snapshots already apply the 6/7/8 first-import whitelist.
    ratings = sorted(set(s["userrating"] for s in states if s["userrating"] is not None))
    if len(ratings) == 1:
        userrating = ratings[0]
    elif len(ratings) > 1:
        userrating = None
        conflicts.append({"field": "userrating", "values": ratings})
    else:
        userrating = None

    # Initial resume has no independent change timestamp. Be conservative.
    nonzero = []
    for node, st in zip(nodes, states):
        if st["resume"]["position"] > 0:
            nonzero.append((node, st))
    resume = {"position": 0.0, "total": 0.0}
    if nonzero:
        distinct = {}
        for node, st in nonzero:
            distinct.setdefault(_resume_key(st["resume"]), []).append((node, st))
        if len(distinct) == 1:
            resume = dict(nonzero[0][1]["resume"])
        else:
            latest = max([st["lastplayed"] for _, st in nonzero if st["lastplayed"]] or [""])
            newest = [(node, st) for node, st in nonzero if latest and st["lastplayed"] == latest]
            newest_keys = set(_resume_key(st["resume"]) for _, st in newest)
            if latest and len(newest_keys) == 1:
                resume = dict(newest[0][1]["resume"])
            else:
                conflicts.append({
                    "field": "resume",
                    "values": [
                        {
                            "client": node["client_name"],
                            "position": st["resume"]["position"],
                            "total": st["resume"]["total"],
                            "lastplayed": st["lastplayed"],
                        }
                        for node, st in nonzero
                    ],
                })

    return {
        "playcount": playcount,
        "lastplayed": lastplayed,
        "resume": resume,
        "userrating": userrating,
    }, conflicts


def merge_initial_state(nodes, base_client_id=None):
    """Return a first-run merged state and per-field conflicts.

    If *base_client_id* is present in this logical media cluster, that Kodi is the
    source of truth for playcount, lastplayed and resume during initialization.
    Ratings are special: a 6/7/8 rating from the base wins; if the base has no
    imported rating, one unambiguous 6/7/8 rating from another Kodi may fill it.

    If the base Kodi does not contain the media item, the original conservative
    merge is used for the remaining Kodi installations.
    """
    base_client_id = str(base_client_id or "")
    base_nodes = [node for node in nodes if str(node.get("client_id") or "") == base_client_id] if base_client_id else []
    if not base_nodes:
        return _merge_conservative_state(nodes)

    base_state, base_conflicts = _merge_conservative_state(base_nodes)
    conflicts = [c for c in base_conflicts if c.get("field") == "resume"]

    # Rating: base value wins. If base has none, accept exactly one value from all
    # snapshots; conflicting non-zero whitelisted ratings remain unresolved.
    if base_state.get("userrating") is not None:
        userrating = base_state.get("userrating")
    else:
        ratings = sorted(set(_state(node["record"])["userrating"] for node in nodes if _state(node["record"])["userrating"] is not None))
        if len(ratings) == 1:
            userrating = ratings[0]
        elif len(ratings) > 1:
            userrating = None
            conflicts.append({"field": "userrating", "values": ratings})
        else:
            userrating = None

    return {
        "playcount": base_state["playcount"],
        "lastplayed": base_state["lastplayed"],
        "resume": dict(base_state["resume"]),
        "userrating": userrating,
    }, conflicts

def _diff_state(current, target, rating_conflicted=False, resume_conflicted=False):
    current = _state({"state": current}) if not ("type" in current) else _state(current)
    changes = {}
    if current["playcount"] != target["playcount"]:
        changes["playcount"] = {"from": current["playcount"], "to": target["playcount"]}
    if current["lastplayed"] != target["lastplayed"]:
        changes["lastplayed"] = {"from": current["lastplayed"], "to": target["lastplayed"]}
    if not rating_conflicted and target["userrating"] is not None and current["userrating"] != target["userrating"]:
        changes["userrating"] = {"from": current["userrating"], "to": target["userrating"]}
    if not resume_conflicted and _resume_key(current["resume"]) != _resume_key(target["resume"]):
        # Zero -> zero with only a different 'total' is noise and should not create writes.
        if current["resume"]["position"] > 0 or target["resume"]["position"] > 0:
            changes["resume"] = {"from": current["resume"], "to": target["resume"]}
    return changes


def build_dry_run(snapshots, base_client_id=None):
    """Create a read-only synchronization plan from SudoSync client snapshots.

    base_client_id optionally makes one Kodi authoritative for the first import.
    """
    nodes = []
    no_strong_id = []
    for snapshot in snapshots:
        client = snapshot.get("client") if isinstance(snapshot.get("client"), dict) else {}
        client_id = str(client.get("id") or "")
        client_name = str(client.get("name") or client_id or "Kodi")
        for collection in ("movies", "episodes"):
            values = snapshot.get(collection) if isinstance(snapshot.get(collection), list) else []
            for record in values:
                node = {
                    "client_id": client_id,
                    "client_name": client_name,
                    "collection": collection,
                    "record": record,
                }
                if not _aliases(record):
                    no_strong_id.append(node)
                nodes.append(node)

    parent = list(range(len(nodes)))
    rank = [0] * len(nodes)

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    alias_owner = {}
    for index, node in enumerate(nodes):
        for alias in _aliases(node["record"]):
            if alias in alias_owner:
                union(index, alias_owner[alias])
            else:
                alias_owner[alias] = index

    raw_clusters = defaultdict(list)
    for index, node in enumerate(nodes):
        # Records with no strong ID intentionally remain standalone.
        raw_clusters[find(index)].append(node)

    safe_clusters = []
    ambiguous_clusters = []
    mirror_clusters = []
    singleton_clusters = []
    presence = Counter()
    type_presence = Counter()
    client_change_counts = defaultdict(Counter)
    client_change_items = defaultdict(list)
    rating_conflicts = []
    resume_conflicts = []

    for cluster in raw_clusters.values():
        per_client = Counter(node["client_id"] for node in cluster)
        names = sorted(set(node["client_name"] for node in cluster))
        media_type = str(cluster[0]["record"].get("type") or "unknown")
        display = _display(cluster[0]["record"])
        strong_aliases = sorted(set(alias for node in cluster for alias in _aliases(node["record"])))

        id_values = defaultdict(set)
        for node in cluster:
            ids = node["record"].get("ids") if isinstance(node["record"].get("ids"), dict) else {}
            for id_type in STRONG_ID_TYPES:
                value = ids.get(id_type)
                if value is not None and str(value).strip():
                    id_values[id_type].add(str(value).strip().lower())
        identity_conflicts = {key: sorted(values) for key, values in id_values.items() if len(values) > 1}

        duplicate_in_client = any(count > 1 for count in per_client.values())
        safe_mirror = duplicate_in_client and not identity_conflicts and _safe_mirror_cluster(cluster)
        if (duplicate_in_client and not safe_mirror) or identity_conflicts:
            reason = "same strong media ID occurs more than once in at least one local Kodi library"
            if identity_conflicts:
                reason = "strong media identifiers disagree inside the matched group"
            ambiguous_clusters.append({
                "type": media_type,
                "display": display,
                "clients": names,
                "aliases": strong_aliases,
                "identity_conflicts": identity_conflicts,
                "members": [
                    {
                        "client": node["client_name"],
                        "file": node["record"].get("file") or "",
                        "local": node["record"].get("local") or {},
                        "state": _state(node["record"]),
                    }
                    for node in cluster
                ],
                "reason": reason,
            })
            continue
        if safe_mirror:
            mirror_clusters.append({
                "type": media_type,
                "display": display,
                "clients": names,
                "aliases": strong_aliases,
                "members": [
                    {
                        "client": node["client_name"],
                        "file": node["record"].get("file") or "",
                        "local": node["record"].get("local") or {},
                        "state": _state(node["record"]),
                    }
                    for node in cluster
                ],
                "reason": "same media exists as a local/network mirror inside one Kodi library",
            })

        if len(cluster) == 1:
            singleton_clusters.append(cluster[0])
            presence[(cluster[0]["client_name"],)] += 1
            type_presence[(media_type, (cluster[0]["client_name"],))] += 1
            continue

        merged, conflicts = merge_initial_state(cluster, base_client_id=base_client_id)
        rating_conflicted = any(x.get("field") == "userrating" for x in conflicts)
        resume_conflicted = any(x.get("field") == "resume" for x in conflicts)
        if rating_conflicted:
            rating_conflicts.append({"display": display, "clients": names, "conflicts": conflicts})
        if resume_conflicted:
            resume_conflicts.append({"display": display, "clients": names, "conflicts": conflicts})

        cluster_plan = {
            "type": media_type,
            "display": display,
            "clients": names,
            "aliases": strong_aliases,
            "merged_state": merged,
            "conflicts": conflicts,
            "changes": {},
        }
        for node in cluster:
            changes = _diff_state(
                node["record"].get("state") or {}, merged,
                rating_conflicted=rating_conflicted,
                resume_conflicted=resume_conflicted,
            )
            if changes:
                cluster_plan["changes"][node["client_id"]] = {
                    "client": node["client_name"],
                    "local": node["record"].get("local") or {},
                    "file": node["record"].get("file") or "",
                    "changes": changes,
                }
                for field in changes:
                    client_change_counts[node["client_id"]][field] += 1
                client_change_counts[node["client_id"]]["items"] += 1
                client_change_items[node["client_id"]].append({
                    "type": media_type,
                    "display": display,
                    "local": node["record"].get("local") or {},
                    "file": node["record"].get("file") or "",
                    "changes": changes,
                })
        safe_clusters.append(cluster_plan)
        key = tuple(names)
        presence[key] += 1
        type_presence[(media_type, key)] += 1

    clients = []
    for snapshot in snapshots:
        client = snapshot.get("client") if isinstance(snapshot.get("client"), dict) else {}
        cid = str(client.get("id") or "")
        clients.append({
            "id": cid,
            "name": str(client.get("name") or cid or "Kodi"),
            "generated_at": snapshot.get("generated_at") or "",
            "counts": snapshot.get("counts") or {},
            "planned_changes": dict(client_change_counts.get(cid, {})),
        })

    def presence_rows(counter):
        rows = []
        for key, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0])):
            rows.append({"clients": list(key), "count": count})
        return rows

    def type_presence_rows(counter):
        rows = []
        for (media_type, key), count in sorted(counter.items(), key=lambda pair: (pair[0][0], -pair[1], pair[0][1])):
            rows.append({"type": media_type, "clients": list(key), "count": count})
        return rows

    return {
        "mode": "dry_run",
        "base_client_id": str(base_client_id or ""),
        "clients": clients,
        "summary": {
            "records_total": len(nodes),
            "logical_clusters_total": len(raw_clusters),
            "safe_shared_clusters": len(safe_clusters),
            "single_client_clusters": len(singleton_clusters),
            "ambiguous_clusters": len(ambiguous_clusters),
            "safe_mirror_clusters": len(mirror_clusters),
            "records_without_strong_id": len(no_strong_id),
            "rating_conflicts": len(rating_conflicts),
            "resume_conflicts": len(resume_conflicts),
        },
        "presence": presence_rows(presence),
        "presence_by_type": type_presence_rows(type_presence),
        "planned_changes": {cid: items for cid, items in client_change_items.items()},
        "mirrors": mirror_clusters,
        "ambiguous": ambiguous_clusters,
        "unidentified": [
            {
                "client": node["client_name"],
                "type": node["record"].get("type") or "",
                "display": _display(node["record"]),
                "file": node["record"].get("file") or "",
                "fallback_identity": node["record"].get("fallback_identity") or "",
            }
            for node in no_strong_id
        ],
        "rating_conflicts": rating_conflicts,
        "resume_conflicts": resume_conflicts,
    }


def _live_state(record):
    value = record.get("state") if isinstance(record.get("state"), dict) else {}
    resume = value.get("resume") if isinstance(value.get("resume"), dict) else {}
    return {
        "playcount": _safe_int(value.get("playcount"), 0),
        "lastplayed": str(value.get("lastplayed") or ""),
        "resume": {
            "position": round(_safe_float(resume.get("position"), 0.0), 3),
            "total": round(_safe_float(resume.get("total"), 0.0), 3),
        },
        "userrating": max(0, min(10, _safe_int(value.get("userrating"), 0))),
    }


def _version_tuple(version):
    version = version if isinstance(version, dict) else {}
    return (
        _safe_int(version.get("lc"), 0),
        str(version.get("ts") or ""),
        _safe_int(version.get("seq"), 0),
        str(version.get("client_id") or ""),
    )


def _field_version(record, field):
    versions = record.get("field_versions") if isinstance(record.get("field_versions"), dict) else {}
    value = versions.get(field) if isinstance(versions.get(field), dict) else {}
    return {
        "lc": _safe_int(value.get("lc"), 0),
        "ts": str(value.get("ts") or ""),
        "client_id": str(value.get("client_id") or ""),
        "seq": _safe_int(value.get("seq"), 0),
    }


def _field_value_key(field, value):
    if field == "resume":
        value = value if isinstance(value, dict) else {}
        pos = round(_safe_float(value.get("position"), 0.0), 1)
        total = round(_safe_float(value.get("total"), 0.0), 1)
        if pos == 0.0:
            return (0.0, 0.0)
        return (pos, total)
    if field in ("playcount", "userrating"):
        return _safe_int(value, 0)
    return str(value or "")


def _live_diff(current, target):
    changes = {}
    for field in ("playcount", "lastplayed", "userrating", "resume"):
        if _field_value_key(field, current.get(field)) != _field_value_key(field, target.get(field)):
            changes[field] = {"from": current.get(field), "to": target.get(field)}
    return changes


def build_live_plan(snapshots):
    """Build a version-aware peer synchronization plan after initialization.

    Each field is resolved independently using the newest observed real-user
    change. Snapshot writers carry the per-field versions; remote writes retain
    the winning version, preventing feedback loops. Ambiguous strong-ID groups
    are still skipped, while conservative local/network mirrors remain safe.
    """
    nodes = []
    no_strong_id = []
    for snapshot in snapshots:
        client = snapshot.get("client") if isinstance(snapshot.get("client"), dict) else {}
        client_id = str(client.get("id") or "")
        client_name = str(client.get("name") or client_id or "Kodi")
        for collection in ("movies", "episodes"):
            values = snapshot.get(collection) if isinstance(snapshot.get(collection), list) else []
            for record in values:
                node = {
                    "client_id": client_id,
                    "client_name": client_name,
                    "collection": collection,
                    "record": record,
                }
                if not _aliases(record):
                    no_strong_id.append(node)
                nodes.append(node)

    parent = list(range(len(nodes)))
    rank = [0] * len(nodes)

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    alias_owner = {}
    for index, node in enumerate(nodes):
        for alias in _aliases(node["record"]):
            if alias in alias_owner:
                union(index, alias_owner[alias])
            else:
                alias_owner[alias] = index

    raw_clusters = defaultdict(list)
    for index, node in enumerate(nodes):
        raw_clusters[find(index)].append(node)

    ambiguous_clusters = []
    mirror_clusters = []
    safe_clusters = []
    singleton_clusters = []
    conflicts = []
    client_change_counts = defaultdict(Counter)
    client_change_items = defaultdict(list)
    presence = Counter()
    type_presence = Counter()

    for cluster in raw_clusters.values():
        per_client = Counter(node["client_id"] for node in cluster)
        names = sorted(set(node["client_name"] for node in cluster))
        media_type = str(cluster[0]["record"].get("type") or "unknown")
        display = _display(cluster[0]["record"])
        strong_aliases = sorted(set(alias for node in cluster for alias in _aliases(node["record"])))

        id_values = defaultdict(set)
        for node in cluster:
            ids = node["record"].get("ids") if isinstance(node["record"].get("ids"), dict) else {}
            for id_type in STRONG_ID_TYPES:
                value = ids.get(id_type)
                if value is not None and str(value).strip():
                    id_values[id_type].add(str(value).strip().lower())
        identity_conflicts = {key: sorted(values) for key, values in id_values.items() if len(values) > 1}
        duplicate_in_client = any(count > 1 for count in per_client.values())
        safe_mirror = duplicate_in_client and not identity_conflicts and _safe_mirror_cluster(cluster)
        if (duplicate_in_client and not safe_mirror) or identity_conflicts:
            reason = "same strong media ID occurs more than once in at least one local Kodi library"
            if identity_conflicts:
                reason = "strong media identifiers disagree inside the matched group"
            ambiguous_clusters.append({
                "type": media_type,
                "display": display,
                "clients": names,
                "aliases": strong_aliases,
                "identity_conflicts": identity_conflicts,
                "reason": reason,
            })
            continue

        if safe_mirror:
            mirror_clusters.append({
                "type": media_type,
                "display": display,
                "clients": names,
                "aliases": strong_aliases,
                "reason": "same media exists as a local/network mirror inside one Kodi library",
            })

        # A single physical record has nobody to synchronize with. A verified
        # two-copy mirror inside one client is not a singleton and is handled.
        if len(cluster) == 1:
            singleton_clusters.append(cluster[0])
            presence[(cluster[0]["client_name"],)] += 1
            type_presence[(media_type, (cluster[0]["client_name"],))] += 1
            continue

        target = {}
        target_versions = {}
        cluster_conflicts = []
        for field in ("playcount", "lastplayed", "userrating", "resume"):
            candidates = []
            for node in cluster:
                state = _live_state(node["record"])
                version = _field_version(node["record"], field)
                candidates.append((node, state[field], version, _version_tuple(version)))
            top_key = max([candidate[3] for candidate in candidates] or [("", 0, "")])
            top = [candidate for candidate in candidates if candidate[3] == top_key]
            distinct = {}
            for candidate in top:
                distinct.setdefault(_field_value_key(field, candidate[1]), candidate)
            if len(distinct) > 1:
                detail = {
                    "field": field,
                    "version": {
                        "lc": top_key[0],
                        "ts": top_key[1],
                        "seq": top_key[2],
                        "client_id": top_key[3],
                    },
                    "values": [
                        {"client": candidate[0]["client_name"], "value": candidate[1]}
                        for candidate in top
                    ],
                }
                cluster_conflicts.append(detail)
                conflicts.append({"display": display, "type": media_type, "conflict": detail})
                continue
            winner = top[0]
            target[field] = winner[1]
            target_versions[field] = winner[2]

        cluster_plan = {
            "type": media_type,
            "display": display,
            "clients": names,
            "aliases": strong_aliases,
            "target_state": target,
            "target_versions": target_versions,
            "conflicts": cluster_conflicts,
        }

        for node in cluster:
            current = _live_state(node["record"])
            safe_target = dict(current)
            for field, value in target.items():
                safe_target[field] = value
            changes = _live_diff(current, safe_target)
            if changes:
                item_versions = {field: target_versions[field] for field in changes if field in target_versions}
                item = {
                    "type": media_type,
                    "display": display,
                    "local": node["record"].get("local") or {},
                    "local_key": node["record"].get("sync_local_key") or "",
                    "file": node["record"].get("file") or "",
                    "changes": changes,
                    "target_versions": item_versions,
                }
                client_change_items[node["client_id"]].append(item)
                client_change_counts[node["client_id"]]["items"] += 1
                for field in changes:
                    client_change_counts[node["client_id"]][field] += 1
        safe_clusters.append(cluster_plan)
        key = tuple(names)
        presence[key] += 1
        type_presence[(media_type, key)] += 1

    clients = []
    for snapshot in snapshots:
        client = snapshot.get("client") if isinstance(snapshot.get("client"), dict) else {}
        cid = str(client.get("id") or "")
        clients.append({
            "id": cid,
            "name": str(client.get("name") or cid or "Kodi"),
            "generated_at": snapshot.get("generated_at") or "",
            "counts": snapshot.get("counts") or {},
            "planned_changes": dict(client_change_counts.get(cid, {})),
        })

    def presence_rows(counter):
        return [
            {"clients": list(key), "count": count}
            for key, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
        ]

    def type_presence_rows(counter):
        return [
            {"type": media_type, "clients": list(key), "count": count}
            for (media_type, key), count in sorted(counter.items(), key=lambda pair: (pair[0][0], -pair[1], pair[0][1]))
        ]

    return {
        "mode": "live_dry_run",
        "clients": clients,
        "summary": {
            "records_total": len(nodes),
            "logical_clusters_total": len(raw_clusters),
            "safe_shared_clusters": len(safe_clusters),
            "single_client_clusters": len(singleton_clusters),
            "ambiguous_clusters": len(ambiguous_clusters),
            "safe_mirror_clusters": len(mirror_clusters),
            "records_without_strong_id": len(no_strong_id),
            "field_conflicts": len(conflicts),
            # Compatibility with existing status UI.
            "rating_conflicts": len([x for x in conflicts if (x.get("conflict") or {}).get("field") == "userrating"]),
            "resume_conflicts": len([x for x in conflicts if (x.get("conflict") or {}).get("field") == "resume"]),
        },
        "presence": presence_rows(presence),
        "presence_by_type": type_presence_rows(type_presence),
        "planned_changes": {cid: items for cid, items in client_change_items.items()},
        "mirrors": mirror_clusters,
        "ambiguous": ambiguous_clusters,
        "unidentified": [
            {
                "client": node["client_name"],
                "type": node["record"].get("type") or "",
                "display": _display(node["record"]),
                "file": node["record"].get("file") or "",
                "fallback_identity": node["record"].get("fallback_identity") or "",
            }
            for node in no_strong_id
        ],
        "field_conflicts": conflicts,
    }
