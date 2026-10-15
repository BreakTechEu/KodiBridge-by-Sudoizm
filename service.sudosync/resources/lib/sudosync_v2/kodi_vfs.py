# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Kodi xbmcvfs backend for the dependency-injected v2 repository.

This adapter implements file I/O only. It deliberately does not implement or
pretend to implement a cross-process shared lock; callers must inject a lock
provider with verified semantics for the actual shared filesystem.
"""
from __future__ import absolute_import


class KodiVFSBackendError(IOError):
    """Kodi VFS operation failed or returned an unverifiable result."""


class KodiVFSMetadataReader(object):
    """Bounded UTF-8 metadata reader for media sidecars through Kodi VFS."""

    def __init__(self, vfs_module=None):
        if vfs_module is None:
            try:
                import xbmcvfs as vfs_module
            except ImportError as exc:
                raise ValueError("Kodi xbmcvfs module is required: {}".format(exc))
        self._vfs = vfs_module

    def __call__(self, path, max_bytes):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("metadata path must be a non-empty string")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        handle = None
        try:
            handle = self._vfs.File(path, "r")
            data = handle.read(max_bytes + 1)
        except Exception as exc:
            raise KodiVFSBackendError("Cannot read metadata {}: {}".format(path, exc))
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        if isinstance(data, bytes):
            if len(data) > max_bytes:
                raise KodiVFSBackendError("Metadata file exceeds configured size limit: " + path)
            try:
                return data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise KodiVFSBackendError("Metadata file is not valid UTF-8: {}".format(exc))
        if not isinstance(data, str):
            raise KodiVFSBackendError("Kodi VFS returned invalid metadata for " + path)
        if len(data.encode("utf-8")) > max_bytes:
            raise KodiVFSBackendError("Metadata file exceeds configured size limit: " + path)
        return data


class KodiVFSBackend(object):
    def __init__(self, vfs_module=None, max_bytes=8 * 1024 * 1024):
        if vfs_module is None:
            try:
                import xbmcvfs as vfs_module
            except ImportError as exc:
                raise ValueError("Kodi xbmcvfs module is required: {}".format(exc))
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self._vfs = vfs_module
        self._max_bytes = max_bytes

    def exists(self, path):
        try:
            return bool(self._vfs.exists(path))
        except Exception as exc:
            raise KodiVFSBackendError("Cannot check path existence: {}".format(exc))

    def read_text(self, path):
        handle = None
        try:
            handle = self._vfs.File(path, "r")
            data = handle.read(self._max_bytes + 1)
        except Exception as exc:
            raise KodiVFSBackendError("Cannot read {}: {}".format(path, exc))
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        if isinstance(data, bytes):
            if len(data) > self._max_bytes:
                raise KodiVFSBackendError("File exceeds configured size limit: " + path)
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise KodiVFSBackendError("File is not valid UTF-8: {}".format(exc))
        if not isinstance(data, str):
            raise KodiVFSBackendError("Kodi VFS returned non-text data for " + path)
        if len(data.encode("utf-8")) > self._max_bytes:
            raise KodiVFSBackendError("File exceeds configured size limit: " + path)
        return data

    def write_text(self, path, text):
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text.encode("utf-8")) > self._max_bytes:
            raise KodiVFSBackendError("Refusing oversized write: " + path)
        handle = None
        try:
            handle = self._vfs.File(path, "w")
            result = handle.write(text)
            if result is False:
                return False
            flush = getattr(handle, "flush", None)
            if callable(flush):
                flush()
        except Exception as exc:
            raise KodiVFSBackendError("Cannot write {}: {}".format(path, exc))
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        return True

    def rename(self, source, destination):
        try:
            # The injected cross-process lock is required to protect this
            # check-and-rename sequence from other SudoSync writers.
            if self._vfs.exists(destination):
                return False
            return bool(self._vfs.rename(source, destination))
        except Exception as exc:
            raise KodiVFSBackendError("Cannot rename {} to {}: {}".format(source, destination, exc))

    def delete(self, path):
        try:
            if not self._vfs.exists(path):
                return False
            return bool(self._vfs.delete(path))
        except Exception as exc:
            raise KodiVFSBackendError("Cannot delete {}: {}".format(path, exc))

    def mkdirs(self, path):
        try:
            return bool(self._vfs.mkdirs(path))
        except Exception as exc:
            raise KodiVFSBackendError("Cannot create directory {}: {}".format(path, exc))

    def listdir(self, path):
        try:
            result = self._vfs.listdir(path)
        except Exception as exc:
            raise KodiVFSBackendError("Cannot list directory {}: {}".format(path, exc))
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise KodiVFSBackendError("Kodi VFS returned an invalid directory listing")
        directories, files = result
        if not isinstance(directories, (tuple, list)) or not isinstance(files, (tuple, list)):
            raise KodiVFSBackendError("Kodi VFS returned invalid directory entries")
        return list(directories), list(files)
