# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""OS-managed lock for a shared directory exposed as a native filesystem path.

This is NOT a universal SMB/NFS guarantee. The caller must only enable this
provider after validating cross-host lock visibility and crash-release semantics
on the exact server, protocol, mount options, and Kodi platforms in use. Kodi
VFS URIs (for example smb:// or nfs://) are rejected because Python's native
file APIs cannot safely lock them directly.
"""
from __future__ import absolute_import

import errno
import os
import time


class SharedFileLockError(RuntimeError):
    """A shared native file lock could not be acquired or trusted."""


class SharedFileLockBusy(SharedFileLockError):
    """The shared lock file is currently owned by another process."""


class _OSLockBackend(object):
    def __init__(self):
        if os.name == "nt":
            try:
                import msvcrt
            except ImportError as exc:
                raise SharedFileLockError("Windows file-lock API unavailable: {}".format(exc))
            self._module = msvcrt
            self._windows = True
        else:
            try:
                import fcntl
            except ImportError as exc:
                raise SharedFileLockError("POSIX file-lock API unavailable: {}".format(exc))
            self._module = fcntl
            self._windows = False

    def acquire(self, handle, blocking):
        if self._windows:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    self._module.locking(handle.fileno(), self._module.LK_NBLCK, 1)
                    return True
                except OSError as exc:
                    if getattr(exc, "errno", None) not in (
                        errno.EACCES, errno.EAGAIN, errno.EDEADLK,
                    ):
                        raise
                    if not blocking:
                        return False
                    time.sleep(0.1)
        operation = self._module.LOCK_EX
        if not blocking:
            operation |= self._module.LOCK_NB
        try:
            self._module.flock(handle.fileno(), operation)
            return True
        except (BlockingIOError, OSError) as exc:
            if getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise

    def release(self, handle):
        if self._windows:
            handle.seek(0)
            self._module.locking(handle.fileno(), self._module.LK_UNLCK, 1)
        else:
            self._module.flock(handle.fileno(), self._module.LOCK_UN)


class NativeSharedFileLock(object):
    """Context manager locking one persistent file through the OS.

    Lock files must never be deleted while any client may be using them:
    deleting and recreating a lock file can split clients across different
    inodes/file objects and invalidate mutual exclusion.
    """

    def __init__(self, path, backend=None, blocking=True):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("lock path must be a non-empty native path")
        if "://" in path:
            raise ValueError("Kodi VFS URI cannot be used as a native lock path")
        if not os.path.isabs(path):
            raise ValueError("lock path must be absolute")
        self.path = os.path.abspath(path)
        self._backend = backend or _OSLockBackend()
        self._blocking = bool(blocking)
        self._handle = None
        self._held = False

    def __enter__(self):
        try:
            directory = os.path.dirname(self.path)
            if not os.path.isdir(directory):
                raise SharedFileLockError("Lock directory does not exist or is not mounted: {}".format(directory))
            self._handle = open(self.path, "a+b")
            if not self._backend.acquire(self._handle, self._blocking):
                self._handle.close()
                self._handle = None
                raise SharedFileLockBusy("Shared operation lock is already held")
            self._held = True
            return self
        except SharedFileLockError:
            raise
        except Exception as exc:
            self._close()
            raise SharedFileLockError("Cannot acquire native shared lock: {}".format(exc))

    def _close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self._held:
                self._backend.release(self._handle)
        except Exception as exc:
            raise SharedFileLockError("Cannot release native shared lock: {}".format(exc))
        finally:
            self._held = False
            self._close()
        return False


class NativeSharedFileLockFactory(object):
    """Build lock contexts for native paths; never translate Kodi VFS URIs.

    filesystem_verified must be explicitly true after cross-host physical tests
    for the exact shared filesystem and client platform combination. Unit tests
    alone are not sufficient evidence.
    """

    def __init__(self, filesystem_verified=False, backend=None, blocking=True):
        if filesystem_verified is not True:
            raise SharedFileLockError(
                "Shared filesystem locking has not been physically verified; refusing to enable it"
            )
        self._backend = backend
        self._blocking = bool(blocking)

    def __call__(self, resource_path):
        if not isinstance(resource_path, str) or "://" in resource_path:
            raise SharedFileLockError(
                "Shared lock requires a native mounted path; Kodi VFS URIs are unsupported"
            )
        if not os.path.isabs(resource_path):
            raise SharedFileLockError("Shared resource path must be absolute")
        # A persistent sidecar lock file lives beside the shared marker and is
        # never removed. Every device must resolve this path to the same file.
        return NativeSharedFileLock(
            resource_path + ".lock",
            backend=self._backend,
            blocking=self._blocking,
        )
