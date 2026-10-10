"""Bounded JSON storage with recoverable replacement for SudoSync v2.

The backend and lock are injected so this module can be tested without Kodi.
A production backend must provide a cross-process lock on the shared resource;
a process-local mutex is not sufficient for multiple Kodi devices.
"""
from __future__ import absolute_import
import json
import uuid


class RepositoryError(IOError):
    """Shared storage could not safely read or write a document."""


def _reject_constant(value):
    raise ValueError("non-standard JSON constant: " + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def decode_json(text, max_bytes=8 * 1024 * 1024):
    """Decode bounded UTF-8 JSON and reject duplicate keys and NaN/Infinity."""
    if isinstance(text, bytes):
        raw = text
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryError("JSON is not valid UTF-8: {}".format(exc))
    elif isinstance(text, str):
        raw = text.encode("utf-8")
    else:
        raise RepositoryError("JSON content must be text or bytes")
    if len(raw) > max_bytes:
        raise RepositoryError("JSON document exceeds the configured size limit")
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise RepositoryError("Invalid JSON document: {}".format(exc))


def encode_json(value, max_bytes=8 * 1024 * 1024):
    """Serialize deterministic UTF-8 JSON and reject non-JSON numeric values."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RepositoryError("Cannot serialize JSON document: {}".format(exc))
    if len(text.encode("utf-8")) > max_bytes:
        raise RepositoryError("JSON document exceeds the configured size limit")
    return text


class SharedJsonRepository(object):
    """JSON file repository with backup recovery and lock-required writes.

    backend methods: exists(path), read_text(path), write_text(path, text),
    copy(source, destination), rename(source, destination), delete(path).
    Mutating backend methods must return True only when the operation succeeded.
    lock_factory(path) must return a context manager implementing a cross-process
    lock for the same logical document path.
    """

    def __init__(self, backend, lock_factory, max_bytes=8 * 1024 * 1024):
        if backend is None or lock_factory is None:
            raise ValueError("backend and cross-process lock_factory are required")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self._backend = backend
        self._lock_factory = lock_factory
        self._max_bytes = max_bytes

    def _read_unlocked(self, path):
        if not self._backend.exists(path):
            return None
        try:
            text = self._backend.read_text(path)
        except Exception as exc:
            raise RepositoryError("Cannot read {}: {}".format(path, exc))
        return decode_json(text, self._max_bytes)

    def load(self, path):
        """Load an existing JSON document; recover from backup only if main is absent."""
        backup = path + ".bak"
        with self._lock_factory(path):
            if self._backend.exists(path):
                return self._read_unlocked(path)
            if self._backend.exists(backup):
                try:
                    recovered = self._read_unlocked(backup)
                except RepositoryError:
                    raise RepositoryError("Main document and its backup are unavailable or invalid: " + path)
                if not self._backend.rename(backup, path):
                    raise RepositoryError("Backup exists but recovery rename failed: " + path)
                return recovered
            return None

    def save(self, path, value):
        """Save JSON under a required lock, preserving the previous file as .bak."""
        text = encode_json(value, self._max_bytes)
        backup = path + ".bak"
        temp = path + ".tmp-" + uuid.uuid4().hex
        with self._lock_factory(path):
            try:
                if not self._backend.write_text(temp, text):
                    raise RepositoryError("Temporary JSON write failed: " + path)
                # Read-back verifies the VFS did not truncate or corrupt the write.
                verified = self._read_unlocked(temp)
                if verified != value:
                    raise RepositoryError("Temporary JSON read-back does not match the requested value: " + path)

                if self._backend.exists(path):
                    # Do not replace a malformed or unreadable current file automatically.
                    self._read_unlocked(path)
                    if self._backend.exists(backup) and not self._backend.delete(backup):
                        raise RepositoryError("Cannot remove previous backup: " + backup)
                    if not self._backend.rename(path, backup):
                        raise RepositoryError("Cannot preserve current document as backup: " + path)

                if not self._backend.rename(temp, path):
                    if self._backend.exists(backup) and not self._backend.exists(path):
                        self._backend.rename(backup, path)
                    raise RepositoryError("Cannot promote temporary document: " + path)
                return True
            except RepositoryError:
                raise
            except Exception as exc:
                raise RepositoryError("Shared JSON write failed for {}: {}".format(path, exc))
            finally:
                try:
                    if self._backend.exists(temp):
                        self._backend.delete(temp)
                except Exception:
                    pass
