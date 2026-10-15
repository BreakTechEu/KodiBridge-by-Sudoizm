# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Fail-closed coordination for one shared SudoSync operation at a time.

The marker is deliberately not a lease: its age never proves that its owner is
dead. The injected lock must serialize access according to the operating model.
The current single-active-Kodi model uses a process-local lock; concurrent Kodi
clients require cross-process exclusion on the actual filesystem.
"""
from __future__ import absolute_import

from datetime import datetime
import uuid

from .storage import RepositoryError, decode_json, encode_json


MARKER_FORMAT = "SudoSync v2 active operation"
MARKER_SCHEMA_VERSION = 1


class SessionError(RuntimeError):
    """An operation could not safely acquire or release the shared session."""


class SessionBusyError(SessionError):
    """A shared active-operation marker already exists."""


class SessionOwnershipError(SessionError):
    """The marker is missing or belongs to another operation."""


def _utc_now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_marker(marker):
    if not isinstance(marker, dict):
        raise SessionError("Active-operation marker must be a JSON object")
    if set(marker) != {
        "format", "schema_version", "session_id", "client_id", "started_at"
    }:
        raise SessionError("Active-operation marker has an unsupported shape")
    if marker.get("format") != MARKER_FORMAT:
        raise SessionError("Active-operation marker has an unsupported format")
    if marker.get("schema_version") != MARKER_SCHEMA_VERSION:
        raise SessionError("Active-operation marker has an unsupported schema")
    try:
        parsed = uuid.UUID(str(marker.get("session_id")))
        if str(parsed) != str(marker["session_id"]).lower():
            raise ValueError("non-canonical session id")
    except (ValueError, TypeError, AttributeError, KeyError):
        raise SessionError("Active-operation marker has an invalid session id")
    if not isinstance(marker.get("client_id"), str) or not marker["client_id"].strip():
        raise SessionError("Active-operation marker has an invalid client id")
    if not isinstance(marker.get("started_at"), str) or not marker["started_at"].endswith("Z"):
        raise SessionError("Active-operation marker has an invalid timestamp")
    return marker


class SharedOperationCoordinator(object):
    """Acquire a fail-closed, shared marker for one mutating sync operation.

    Backend contract: exists(path), read_text(path), write_text(path, text),
    delete(path). Mutations return True only on success. lock_factory(path)
    returns a context manager implementing exclusion for the configured
    operating model. The marker is retained after abnormal termination; no
    age-based automatic recovery is performed.
    """

    def __init__(self, backend, lock_factory, marker_path, clock=None, id_factory=None):
        if backend is None or lock_factory is None:
            raise ValueError("backend and lock_factory are required")
        if not isinstance(marker_path, str) or not marker_path.strip():
            raise ValueError("marker_path must be a non-empty path")
        self._backend = backend
        self._lock_factory = lock_factory
        self._marker_path = marker_path
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def _read_marker(self):
        try:
            marker = decode_json(self._backend.read_text(self._marker_path), max_bytes=4096)
        except Exception as exc:
            raise SessionError(
                "Active-operation marker exists but cannot be trusted; refusing to continue: {}".format(exc)
            )
        return _validate_marker(marker)

    def acquire(self, client_id, prepare=None):
        """Create an ownership token, preparing its durable journal before the marker.

        prepare(session_id) runs while the shared cross-process lock is held,
        after confirming no active marker exists and before creating the marker.
        It must persist the backup and a recoverable 'prepared' journal, and must
        not mutate the Kodi library. If the process stops during preparation,
        no marker exists and the orphaned session files are harmless.
        """
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            raise ValueError("client_id must be a non-empty string of at most 128 characters")
        if prepare is not None and not callable(prepare):
            raise ValueError("prepare must be callable")
        with self._lock_factory(self._marker_path):
            if self._backend.exists(self._marker_path):
                marker = self._read_marker()
                raise SessionBusyError(
                    "A previous SudoSync operation is unresolved (client {}, started {}). "
                    "No age-based takeover is allowed; verify that no operation is running before manual recovery."
                    .format(marker["client_id"], marker["started_at"])
                )
            session_id = str(self._id_factory())
            try:
                canonical = str(uuid.UUID(session_id))
            except (ValueError, TypeError, AttributeError):
                raise SessionError("Session ID generator returned an invalid UUID")
            if canonical != session_id.lower():
                raise SessionError("Session ID generator returned a non-canonical UUID")
            marker = {
                "format": MARKER_FORMAT,
                "schema_version": MARKER_SCHEMA_VERSION,
                "session_id": canonical,
                "client_id": client_id,
                "started_at": self._clock(),
            }
            if prepare is not None:
                try:
                    prepare(canonical)
                except Exception as exc:
                    raise SessionError("Cannot prepare transaction before acquiring marker: {}".format(exc))
            text = encode_json(marker, max_bytes=4096)
            try:
                written = self._backend.write_text(self._marker_path, text)
            except Exception as exc:
                raise SessionError("Cannot create active-operation marker: {}".format(exc))
            if written is not True:
                raise SessionError("Cannot create active-operation marker")
            stored = self._read_marker()
            if stored != marker:
                raise SessionError("Active-operation marker read-back did not match")
            return canonical


    def inspect(self, client_id):
        """Classify the shared session for this stable Kodi client ID.

        A matching client owns recovery after a Kodi/service restart. A marker
        owned by another client blocks this Kodi. Marker age is never used to
        decide ownership or liveness.
        """
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            raise ValueError("client_id must be a non-empty string of at most 128 characters")
        with self._lock_factory(self._marker_path):
            if not self._backend.exists(self._marker_path):
                return {"state": "clear", "marker": None}
            marker = self._read_marker()
            state = "owned_recovery" if marker["client_id"] == client_id else "blocked"
            return {"state": state, "marker": dict(marker)}

    def resume_owned(self, client_id, session_id):
        """Return the existing session token only to its original Kodi client.

        This does not clear or replace the marker. It is solely an ownership
        check before journal-based recovery starts.
        """
        state = self.inspect(client_id)
        if state["state"] == "clear":
            raise SessionOwnershipError("No unfinished session exists to resume")
        if state["state"] == "blocked":
            marker = state["marker"]
            raise SessionBusyError(
                "Unfinished operation belongs to another Kodi client (client {}, session {})."
                .format(marker["client_id"], marker["session_id"])
            )
        marker = state["marker"]
        if marker["session_id"] != session_id:
            raise SessionOwnershipError("Recovery session ID does not match this Kodi client's active session")
        return session_id

    def recover_abandoned(self, session_id, confirmed_no_process_running=False):
        """Explicitly clear a matching abandoned marker after operator review.

        This is never called automatically. The caller must require an explicit
        operator confirmation that no Kodi instance is still executing the
        operation and should retain the corresponding journal/backup for audit.
        """
        if confirmed_no_process_running is not True:
            raise SessionError("Explicit confirmation that no operation is running is required")
        with self._lock_factory(self._marker_path):
            if not self._backend.exists(self._marker_path):
                raise SessionOwnershipError("Active-operation marker is missing")
            marker = self._read_marker()
            if marker["session_id"] != session_id:
                raise SessionOwnershipError("Recovery session ID does not match the active marker")
            try:
                deleted = self._backend.delete(self._marker_path)
            except Exception as exc:
                raise SessionError("Cannot recover active-operation marker: {}".format(exc))
            if deleted is not True or self._backend.exists(self._marker_path):
                raise SessionError("Active-operation marker remains after recovery")
            return {
                "recovered_session_id": marker["session_id"],
                "client_id": marker["client_id"],
                "started_at": marker["started_at"],
                "recovered_at": self._clock(),
                "operator_confirmed_no_process_running": True,
            }

    def release(self, session_id):
        """Remove only this operation's marker; any uncertainty leaves it intact."""
        with self._lock_factory(self._marker_path):
            if not self._backend.exists(self._marker_path):
                raise SessionOwnershipError("Active-operation marker is missing; refusing implicit recovery")
            marker = self._read_marker()
            if marker["session_id"] != session_id:
                raise SessionOwnershipError("Active-operation marker belongs to another session")
            try:
                deleted = self._backend.delete(self._marker_path)
            except Exception as exc:
                raise SessionError("Cannot remove active-operation marker: {}".format(exc))
            if deleted is not True:
                raise SessionError("Cannot remove active-operation marker")
            if self._backend.exists(self._marker_path):
                raise SessionError("Active-operation marker remains after release")
