# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Copies of one video: library items sharing a KodiBridge ID and an external ID.

Two media files with the same KodiBridge ID are a duplicate ID, unless they
also share an external ID (IMDb/TMDb/TVDB/Trakt) and no external ID differs:
then they are copies of one video (e.g. on a local disk and on the NAS). The
sync engine sees such copies as one library item. CopyAwareKodiAdapter shows
it the first copy (by path) with the state merged from all copies, and every
write to that item goes to all copies.
"""
from __future__ import absolute_import

from copy import deepcopy
import logging

from .identity import identifiers_from_kodi_item, normalize_identifier

logger = logging.getLogger(__name__)

EXTERNAL_KINDS = ("imdb", "tmdb", "tvdb", "trakt")
STATE_FIELDS = ("playcount", "lastplayed", "resume", "userrating")


def _external_ids(ids):
    result = {}
    for kind in EXTERNAL_KINDS:
        value = normalize_identifier(kind, (ids or {}).get(kind))
        if value is not None:
            result[kind] = value
    return result


def same_video(id_dicts):
    """True when all identifier sets share an external ID and none contradicts another."""
    externals = [_external_ids(ids) for ids in id_dicts]
    if len(externals) < 2 or not all(externals):
        return False
    common = set(externals[0].items())
    for ext in externals[1:]:
        common &= set(ext.items())
    if not common:
        return False
    for kind in EXTERNAL_KINDS:
        values = {ext[kind] for ext in externals if kind in ext}
        if len(values) > 1:
            return False
    return True


def _same_file_key(path):
    from .snapshot import same_file_key
    return same_file_key(path)


def find_copy_groups(items):
    """Group library items that are copies of one video.

    items are Kodi library records with internal identifiers (as returned by
    KodiJsonRpcAdapter). Returns lists of items, each with two or more files;
    a file listed under several spellings or holding several items (a
    multi-episode file) is never treated as a copy.
    """
    by_sid = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        try:
            ids = identifiers_from_kodi_item(item)
        except ValueError:
            continue
        sid = ids.get("sudosync")
        if not sid:
            continue
        by_sid.setdefault(sid, []).append((item, ids))
    groups = []
    for sid in sorted(by_sid):
        entries = by_sid[sid]
        files = {}
        for item, ids in entries:
            files.setdefault(_same_file_key(item["file"]), []).append((item, ids))
        if len(files) < 2 or any(len(records) > 1 for records in files.values()):
            continue
        if not same_video([ids for _, ids in entries]):
            continue
        groups.append(sorted((item for item, _ in entries), key=lambda it: str(it.get("file") or "").casefold()))
    return groups


def _resume_position(value):
    try:
        return float((value or {}).get("position") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _richer(field, values):
    """The value to keep when several copies changed the same field."""
    if field == "playcount":
        return max(int(v or 0) for v in values)
    if field == "lastplayed":
        return max(str(v or "") for v in values)
    if field == "resume":
        return max(values, key=_resume_position)
    if field == "userrating":
        rated = [v for v in values if v]
        return max(rated) if rated else None
    return values[0]


def _norm(field, value):
    """Comparable value of a synchronized field; userrating 0 means none."""
    if field == "resume":
        value = value if isinstance(value, dict) else {}
        return {"position": value.get("position", 0) or 0, "total": value.get("total", 0) or 0}
    if field == "playcount":
        return int(value or 0)
    if field == "userrating":
        return value if value else None
    if field == "lastplayed":
        return value or ""
    return value


def _value(item, field):
    return _norm(field, item.get(field))


def _same(field, left, right):
    if field == "resume":
        return (abs(_resume_position(left) - _resume_position(right)) < 0.001
                and abs(float(left.get("total") or 0) - float(right.get("total") or 0)) < 0.001)
    return left == right


class CopyAwareKodiAdapter(object):
    """Kodi adapter that presents copies of one video as a single library item.

    Merge rule per field: a copy whose value changed since it was last seen
    wins (several changed copies: the richer value); otherwise the value
    merged last time stays; on the first look the richer value is taken.
    """

    def __init__(self, inner):
        self._inner = inner
        self._groups = {"movie": {}, "episode": {}}   # primary id -> [copy ids]
        self._seen = {"movie": {}, "episode": {}}     # copy id -> {field: value}
        self._merged = {"movie": {}, "episode": {}}   # primary id -> {field: value}
        self._items = {"movie": {}, "episode": {}}    # copy id -> last item read

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def copy_groups(self):
        return {kind: {pid: list(ids) for pid, ids in groups.items()} for kind, groups in self._groups.items()}

    @staticmethod
    def _id_key(media_type):
        return "movieid" if media_type == "movie" else "episodeid"

    def _merge(self, media_type, primary, copies):
        seen = self._seen[media_type]
        previous = self._merged[media_type].get(primary)
        merged = {}
        for field in STATE_FIELDS:
            current = [_value(item, field) for item in copies]
            changed = []
            for item, value in zip(copies, current):
                last = seen.get(item[self._id_key(media_type)], {}).get(field)
                if last is not None and not _same(field, last, value):
                    changed.append(value)
            if changed:
                merged[field] = _richer(field, changed)
            elif all(_same(field, current[0], value) for value in current[1:]):
                merged[field] = current[0]
            elif previous is not None and field in previous:
                merged[field] = previous[field]
            else:
                merged[field] = _richer(field, current)
        for item, value in zip(copies, [{f: _value(i, f) for f in STATE_FIELDS} for i in copies]):
            seen[item[self._id_key(media_type)]] = value
        self._merged[media_type][primary] = merged
        return merged

    def _collapse(self, items, media_type):
        id_key = self._id_key(media_type)
        groups = find_copy_groups(items)
        self._groups[media_type] = {}
        hidden = set()
        merged_by_primary = {}
        for group in groups:
            ids = [item.get(id_key) for item in group]
            if any(i is None for i in ids):
                continue
            primary = ids[0]
            self._groups[media_type][primary] = ids
            for item in group:
                self._items[media_type][item[id_key]] = item
            merged_by_primary[primary] = self._merge(media_type, primary, group)
            hidden.update(ids[1:])
        if groups:
            logger.info("%s library: %d videos with copies under one KodiBridge ID treated as single items",
                        media_type, len(self._groups[media_type]))
        result = []
        for item in items:
            if not isinstance(item, dict):
                result.append(item)
                continue
            item_id = item.get(id_key)
            if item_id in hidden:
                continue
            if item_id in merged_by_primary:
                item = dict(item)
                for field, value in merged_by_primary[item_id].items():
                    item[field] = 0 if field == "userrating" and value is None else deepcopy(value)
            result.append(item)
        return result

    def get_movies(self, abort_requested=None, progress_callback=None):
        items = self._inner.get_movies(abort_requested=abort_requested, progress_callback=progress_callback)
        return self._collapse(items, "movie")

    def get_episodes(self, abort_requested=None, progress_callback=None):
        items = self._inner.get_episodes(abort_requested=abort_requested, progress_callback=progress_callback)
        return self._collapse(items, "episode")

    def _details(self, media_type, item_id):
        getter = self._inner.get_movie_details if media_type == "movie" else self._inner.get_episode_details
        copies = self._groups[media_type].get(item_id)
        if not copies:
            return getter(item_id)
        records = [getter(copy_id) for copy_id in copies]
        details = dict(records[0])
        for field, value in self._merge(media_type, item_id, records).items():
            details[field] = 0 if field == "userrating" and value is None else deepcopy(value)
        return details

    def get_movie_details(self, movie_id):
        return self._details("movie", movie_id)

    def get_episode_details(self, episode_id):
        return self._details("episode", episode_id)

    def _set(self, media_type, item_id, changes):
        setter = self._inner.set_movie_details if media_type == "movie" else self._inner.set_episode_details
        copies = self._groups[media_type].get(item_id)
        if not copies:
            return setter(item_id, changes)
        result = None
        for copy_id in copies:
            outcome = setter(copy_id, changes)
            if copy_id == item_id:
                result = outcome
            seen = self._seen[media_type].setdefault(copy_id, {})
            for field, value in changes.items():
                seen[field] = _norm(field, deepcopy(value))
        merged = self._merged[media_type].setdefault(item_id, {})
        for field, value in changes.items():
            merged[field] = _norm(field, deepcopy(value))
        return result

    def set_movie_details(self, movie_id, details):
        return self._set("movie", movie_id, details)

    def set_episode_details(self, episode_id, details):
        return self._set("episode", episode_id, details)

    def align_copies(self):
        """Write the merged state to copies that differ from it (local only).

        Returns the number of copies written. Skipped during playback.
        """
        if self._inner.is_video_playing():
            return 0
        written = 0
        for media_type in ("movie", "episode"):
            setter = self._inner.set_movie_details if media_type == "movie" else self._inner.set_episode_details
            for primary, copies in self._groups[media_type].items():
                merged = self._merged[media_type].get(primary)
                if not merged:
                    continue
                for copy_id in copies:
                    item = self._items[media_type].get(copy_id) or {}
                    seen = self._seen[media_type].get(copy_id, {})
                    changes = {}
                    for field in STATE_FIELDS:
                        current = seen.get(field, _value(item, field))
                        if field in merged and not _same(field, current, merged[field]):
                            changes[field] = deepcopy(merged[field])
                    if changes:
                        setter(copy_id, changes)
                        seen.update(deepcopy(changes))
                        self._seen[media_type][copy_id] = seen
                        written += 1
        return written
