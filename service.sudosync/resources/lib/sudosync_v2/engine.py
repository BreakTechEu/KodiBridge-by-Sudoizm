# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Dependency-injected read-only synchronization preview engine for v2.

This layer deliberately does not publish snapshots or mutate Kodi. A later
transaction coordinator must acquire verified cross-process exclusion, create
backups/journal state, apply writes through JSON-RPC, verify them, and only then
commit new snapshots.
"""
from __future__ import absolute_import

from .kodi_rpc import OperationAborted, is_aborted
from .planning import build_sync_plan
from .initialization import build_initialization_plan
from .snapshot import build_snapshot


class SyncEngineError(RuntimeError):
    """The read-only synchronization preview could not be built safely."""


def _invoke_with_abort(fn, abort_requested):
    try:
        return fn(abort_requested=abort_requested)
    except TypeError:
        return fn()


def _max_logical_clock(snapshots):
    maximum = 0
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for key in ("movies", "episodes"):
            for record in snapshot.get(key, []):
                versions = record.get("versions", {}) if isinstance(record, dict) else {}
                for version in versions.values():
                    if isinstance(version, dict) and type(version.get("lc")) is int:
                        maximum = max(maximum, version["lc"])
    return maximum


class SyncEngine(object):
    """Create a read-only preview from Kodi plus stored snapshots.

    load_previous_local() returns this client's last published local snapshot
    or None. load_peer_snapshots() returns validated candidate peer snapshots.
    Neither callback may silently convert I/O/parse errors into an empty result.
    """

    def __init__(self, kodi, client_id, client_name, load_previous_local,
                 load_peer_snapshots, max_records=100000, abort_requested=None):
        if kodi is None:
            raise ValueError("kodi adapter is required")
        if not callable(load_previous_local) or not callable(load_peer_snapshots):
            raise ValueError("snapshot loader callbacks are required")
        self._kodi = kodi
        self._client_id = client_id
        self._client_name = client_name
        self._load_previous_local = load_previous_local
        self._load_peer_snapshots = load_peer_snapshots
        self._max_records = max_records
        self._abort_requested = abort_requested

    def preview(self, abort_requested=None):
        """Return a candidate snapshot and plan without publishing or writing."""
        abort = abort_requested if abort_requested is not None else self._abort_requested
        if hasattr(self._kodi, "clear_skipped_nfos"):
            self._kodi.clear_skipped_nfos()
        movies = []
        episodes = []
        try:
            if is_aborted(abort):
                raise OperationAborted("Preview aborted before start")
            if _invoke_with_abort(self._kodi.is_video_playing, abort):
                skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
                return {
                    "active": False,
                    "aborted": False,
                    "reason": "video_playing",
                    "snapshot": None,
                    "plan": None,
                    "skipped_nfos": skipped_nfos,
                    "skipped_nfo_count": len(skipped_nfos),
                    "nfo_errors": skipped_nfos,
                }
            if is_aborted(abort):
                raise OperationAborted("Preview aborted before reading movies")
            try:
                movies = _invoke_with_abort(self._kodi.get_movies, abort)
            except OperationAborted as exc:
                movies = exc.partial_items
                raise

            if is_aborted(abort):
                raise OperationAborted("Preview aborted before reading episodes")
            try:
                episodes = _invoke_with_abort(self._kodi.get_episodes, abort)
            except OperationAborted as exc:
                episodes = exc.partial_items
                raise

            if is_aborted(abort):
                raise OperationAborted("Preview aborted before loading previous snapshot")
            previous = _invoke_with_abort(self._load_previous_local, abort)

            if is_aborted(abort):
                raise OperationAborted("Preview aborted before loading peer snapshots")
            peers = _invoke_with_abort(self._load_peer_snapshots, abort)

            if previous is not None and previous.get("client", {}).get("id") != self._client_id:
                raise SyncEngineError("previous local snapshot belongs to a different client")
            if not isinstance(peers, (list, tuple)):
                raise SyncEngineError("peer snapshot loader did not return a list")
            peers = list(peers)
            if any(snapshot.get("client", {}).get("id") == self._client_id for snapshot in peers):
                raise SyncEngineError("peer snapshot list unexpectedly includes this client")
            logical_clock = _max_logical_clock(([previous] if previous else []) + peers)

            if is_aborted(abort):
                raise OperationAborted("Preview aborted before building snapshot")
            candidate = build_snapshot(
                movies,
                episodes,
                self._client_id,
                self._client_name,
                previous_snapshot=previous,
                logical_clock=logical_clock,
                max_records=self._max_records,
            )
            local_kodi_ids = {
                "movie": [item.get("movieid") for item in movies],
                "episode": [item.get("episodeid") for item in episodes],
            }

            if is_aborted(abort):
                raise OperationAborted("Preview aborted before building sync plan")
            plan = build_sync_plan(
                candidate,
                peers,
                local_kodi_ids,
                max_records=self._max_records,
            )
            skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
            return {
                "active": True,
                "aborted": False,
                "reason": "",
                "snapshot": candidate,
                "plan": plan,
                "skipped_nfos": skipped_nfos,
                "skipped_nfo_count": len(skipped_nfos),
                "nfo_errors": skipped_nfos,
            }
        except OperationAborted as exc:
            skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
            partial_movies = movies or getattr(exc, "partial_items", [])
            candidate = None
            try:
                candidate = build_snapshot(
                    partial_movies if isinstance(partial_movies, list) else [],
                    episodes if isinstance(episodes, list) else [],
                    self._client_id,
                    self._client_name,
                    max_records=self._max_records,
                )
            except Exception:
                candidate = None
            return {
                "active": False,
                "aborted": True,
                "reason": "aborted",
                "snapshot": candidate,
                "plan": None,
                "skipped_nfos": skipped_nfos,
                "skipped_nfo_count": len(skipped_nfos),
                "nfo_errors": skipped_nfos,
            }
        except SyncEngineError:
            raise
        except Exception as exc:
            raise SyncEngineError("Cannot safely build synchronization preview: {}".format(exc))

    def initialization_preview(self, base_snapshot, abort_requested=None):
        """Build a one-time base-to-this-Kodi preview without writing anything."""
        abort = abort_requested if abort_requested is not None else self._abort_requested
        if hasattr(self._kodi, "clear_skipped_nfos"):
            self._kodi.clear_skipped_nfos()
        movies = []
        episodes = []
        try:
            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before start")
            if _invoke_with_abort(self._kodi.is_video_playing, abort):
                skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
                return {
                    "active": False,
                    "aborted": False,
                    "reason": "video_playing",
                    "snapshot": None,
                    "plan": None,
                    "skipped_nfos": skipped_nfos,
                    "skipped_nfo_count": len(skipped_nfos),
                    "nfo_errors": skipped_nfos,
                }
            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before reading movies")
            try:
                movies = _invoke_with_abort(self._kodi.get_movies, abort)
            except OperationAborted as exc:
                movies = exc.partial_items
                raise

            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before reading episodes")
            try:
                episodes = _invoke_with_abort(self._kodi.get_episodes, abort)
            except OperationAborted as exc:
                episodes = exc.partial_items
                raise

            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before loading previous snapshot")
            previous = _invoke_with_abort(self._load_previous_local, abort)

            if previous is not None and previous.get("client", {}).get("id") != self._client_id:
                raise SyncEngineError("previous local snapshot belongs to a different client")
            logical_clock = _max_logical_clock(([previous] if previous else []) + [base_snapshot])

            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before building snapshot")
            candidate = build_snapshot(
                movies,
                episodes,
                self._client_id,
                self._client_name,
                previous_snapshot=previous,
                logical_clock=logical_clock,
                max_records=self._max_records,
            )
            local_kodi_ids = {
                "movie": [item.get("movieid") for item in movies],
                "episode": [item.get("episodeid") for item in episodes],
            }

            if is_aborted(abort):
                raise OperationAborted("Initialization preview aborted before building initialization plan")
            plan = build_initialization_plan(
                base_snapshot,
                candidate,
                local_kodi_ids,
                max_records=self._max_records,
            )
            skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
            return {
                "active": True,
                "aborted": False,
                "reason": "",
                "snapshot": candidate,
                "plan": plan,
                "skipped_nfos": skipped_nfos,
                "skipped_nfo_count": len(skipped_nfos),
                "nfo_errors": skipped_nfos,
            }
        except OperationAborted as exc:
            skipped_nfos = list(getattr(self._kodi, "skipped_nfos", []))
            partial_movies = movies or getattr(exc, "partial_items", [])
            candidate = None
            try:
                candidate = build_snapshot(
                    partial_movies if isinstance(partial_movies, list) else [],
                    episodes if isinstance(episodes, list) else [],
                    self._client_id,
                    self._client_name,
                    max_records=self._max_records,
                )
            except Exception:
                candidate = None
            return {
                "active": False,
                "aborted": True,
                "reason": "aborted",
                "snapshot": candidate,
                "plan": None,
                "skipped_nfos": skipped_nfos,
                "skipped_nfo_count": len(skipped_nfos),
                "nfo_errors": skipped_nfos,
            }
        except SyncEngineError:
            raise
        except Exception as exc:
            raise SyncEngineError("Cannot safely build initialization preview: {}".format(exc))
