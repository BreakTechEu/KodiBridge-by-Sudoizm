"""Fail-closed coordination for one shared SudoSync operation at a time.

The marker is deliberately not a lease: its age never proves that its owner is
dead. The injected lock must provide cross-process exclusion for the shared
marker path on the actual filesystem. A process-local mutex is not sufficient.
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
    returns a context manager implementing a real cross-process lock on the
    shared filesystem. The marker is retained after abnormal termination; no
    age-based automatic recovery is performed.
    """

    def __init__(self, backend, lock_factory, marker_path, clock=None, id_factory=None):
        if backend is None or lock_factory is None:
            raise ValueError("backend and cross-process lock_factory are required")
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

    def acquire(self, client_id):
        """Create and return an ownership token, or fail if any marker exists."""
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            raise ValueError("client_id must be a non-empty string of at most 128 characters")
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
