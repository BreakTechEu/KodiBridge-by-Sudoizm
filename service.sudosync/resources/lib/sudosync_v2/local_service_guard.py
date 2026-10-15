# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Local single-service guard for a Kodi installation.

Unlike the shared recovery marker, this guard is a kernel-managed lock on a
local profile file. The lock is released by the OS when the process exits or
crashes; the lock file itself is intentionally never deleted.
"""
from __future__ import absolute_import

import errno
import os
import threading


class ServiceGuardError(RuntimeError):
    """The local single-service guard could not be established safely."""


_HELD_PATHS = set()
_HELD_PATHS_LOCK = threading.Lock()


class _PlatformFileLock(object):
    def __init__(self):
        if os.name == "nt":
            try:
                import msvcrt
            except ImportError as exc:
                raise ServiceGuardError("Windows file-lock API is unavailable: {}".format(exc))
            self._module = msvcrt
            self._windows = True
        else:
            try:
                import fcntl
            except ImportError as exc:
                raise ServiceGuardError("POSIX file-lock API is unavailable: {}".format(exc))
            self._module = fcntl
            self._windows = False

    def acquire(self, handle):
        if self._windows:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                self._module.locking(handle.fileno(), self._module.LK_NBLCK, 1)
                return True
            except OSError as exc:
                if getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    return False
                raise
        try:
            self._module.flock(handle.fileno(), self._module.LOCK_EX | self._module.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        except OSError as exc:
            if getattr(exc, "errno", None) in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise

    def release(self, handle):
        if self._windows:
            handle.seek(0)
            self._module.locking(handle.fileno(), self._module.LK_UNLCK, 1)
        else:
            self._module.flock(handle.fileno(), self._module.LOCK_UN)


class LocalServiceGuard(object):
    """Hold an exclusive OS-managed lock on a local file for service lifetime."""

    def __init__(self, lock_path, lock_backend=None):
        if not isinstance(lock_path, str) or not lock_path.strip():
            raise ValueError("lock_path must be a non-empty local path")
        self._path = os.path.abspath(lock_path)
        self._backend = lock_backend or _PlatformFileLock()
        self._handle = None
        self._held = False

    def acquire(self):
        with _HELD_PATHS_LOCK:
            if self._path in _HELD_PATHS:
                return False
            try:
                directory = os.path.dirname(self._path)
                if directory and not os.path.isdir(directory):
                    os.makedirs(directory)
                self._handle = open(self._path, "a+b")
                if not self._backend.acquire(self._handle):
                    self._handle.close()
                    self._handle = None
                    return False
                _HELD_PATHS.add(self._path)
                self._held = True
                return True
            except ServiceGuardError:
                self._close_handle()
                raise
            except Exception as exc:
                self._close_handle()
                raise ServiceGuardError("Cannot acquire local service guard: {}".format(exc))

    def _close_handle(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    def release(self):
        with _HELD_PATHS_LOCK:
            if not self._held:
                self._close_handle()
                return
            try:
                self._backend.release(self._handle)
            except Exception as exc:
                raise ServiceGuardError("Cannot release local service guard: {}".format(exc))
            finally:
                self._close_handle()
                _HELD_PATHS.discard(self._path)
                self._held = False


def acquire_kodi_service_guard():
    """Resolve Kodi's local add-on profile and acquire its singleton guard.

    Returns a held guard, or None if another service instance already owns it.
    Fails closed if Kodi cannot resolve a local profile or locking is unavailable.
    """
    try:
        import xbmcaddon
        import xbmcvfs
        addon = xbmcaddon.Addon("service.sudosync")
        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        if not isinstance(profile, str) or not profile.strip():
            raise ServiceGuardError("Kodi returned an invalid local add-on profile path")
        if "://" in profile:
            raise ServiceGuardError("Kodi profile path did not resolve to a local filesystem path")
        path = os.path.join(profile, "sudosync-service.lock")
        guard = LocalServiceGuard(path)
        if guard.acquire():
            return guard
        return None
    except ServiceGuardError:
        raise
    except Exception as exc:
        raise ServiceGuardError("Cannot initialize Kodi service guard: {}".format(exc))
