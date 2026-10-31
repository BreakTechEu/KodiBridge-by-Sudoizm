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

import os
import re
import sys

_VFS_CRED_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+@)")
_WIN_UNC_SHARE_CACHE = {}


def _strip_vfs_credentials(path):
    if not isinstance(path, str) or "@" not in path:
        return path
    return _VFS_CRED_RE.sub(r"\1", path)


def _smb_to_unc_parts(path):
    if not isinstance(path, str):
        return None
    clean = _strip_vfs_credentials(path.strip())
    if not clean.lower().startswith("smb://"):
        return None
    rest = clean[6:].lstrip("/")
    parts = [p for p in rest.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return None
    host, share = parts[0], parts[1]
    tail = parts[2:]
    unc_root = "\\\\{}\\{}".format(host, share)
    unc_path = unc_root if not tail else unc_root + "\\" + "\\".join(tail)
    return host, share, unc_root, unc_path


def _resolve_win_unc(path, allow_win_unc=False):
    """Return a native Windows UNC path if the SMB share is already mounted/accessible in Windows.

    Avoids Kodi Win32SMBDirectory WNetAddConnection2W Error 1219 (session credential conflict)
    when Windows already holds an active SMB session to the same server.
    """
    if os.name != "nt" or not allow_win_unc:
        return None
    parsed = _smb_to_unc_parts(path)
    if parsed is None:
        return None
    host, share, unc_root, unc_path = parsed
    cache_key = (host.lower(), share.lower())
    accessible = _WIN_UNC_SHARE_CACHE.get(cache_key)
    if not accessible:
        try:
            accessible = bool(os.path.isdir(unc_root))
        except Exception:
            accessible = False
        if accessible:
            _WIN_UNC_SHARE_CACHE[cache_key] = True
    return _long_unc(unc_path) if accessible else None


# Windows refuses classic paths of MAX_PATH (260) characters or more, e.g. a long
# episode .nfo name under long series folders; the extended-length form
# \\?\UNC\host\share\... has no such limit (backslashes only, no "." parts).
WIN_LONG_PATH_THRESHOLD = 240


def _long_unc(unc_path):
    if not isinstance(unc_path, str) or len(unc_path) < WIN_LONG_PATH_THRESHOLD:
        return unc_path
    if unc_path.startswith("\\\\?\\") or not unc_path.startswith("\\\\"):
        return unc_path
    return "\\\\?\\UNC\\" + unc_path[2:]


_MASK_CRED_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@:\s]+):([^/@\s]+)@")


class KodiVFSBackendError(IOError):
    """Kodi VFS operation failed or returned an unverifiable result."""

    def __init__(self, message=""):
        if isinstance(message, str) and "@" in message:
            message = _MASK_CRED_RE.sub(r"\1\2:***@", message)
        super(KodiVFSBackendError, self).__init__(message)


class KodiVFSMetadataReader(object):
    """Bounded UTF-8 metadata reader for media sidecars through Kodi VFS."""

    def __init__(self, vfs_module=None, allow_win_unc=None):
        is_default_vfs = vfs_module is None
        if vfs_module is None:
            try:
                import xbmcvfs as vfs_module
            except ImportError as exc:
                raise ValueError("Kodi xbmcvfs module is required: {}".format(exc))
        self._vfs = vfs_module
        if allow_win_unc is None:
            allow_win_unc = bool(
                is_default_vfs
                and os.name == "nt"
                and "unittest" not in sys.modules
                and "pytest" not in sys.modules
            )
        self._allow_win_unc = bool(allow_win_unc)

    def __call__(self, path, max_bytes):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("metadata path must be a non-empty string")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                with open(unc_path, "rb") as handle:
                    data = handle.read(max_bytes + 1)
            except Exception as exc:
                raise KodiVFSBackendError("Cannot read metadata {}: {}".format(path, exc))
        else:
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
    def __init__(self, vfs_module=None, max_bytes=8 * 1024 * 1024, allow_win_unc=None):
        is_default_vfs = vfs_module is None
        if vfs_module is None:
            try:
                import xbmcvfs as vfs_module
            except ImportError as exc:
                raise ValueError("Kodi xbmcvfs module is required: {}".format(exc))
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self._vfs = vfs_module
        self._max_bytes = max_bytes
        if allow_win_unc is None:
            allow_win_unc = bool(
                is_default_vfs
                and os.name == "nt"
                and "unittest" not in sys.modules
                and "pytest" not in sys.modules
            )
        self._allow_win_unc = bool(allow_win_unc)

    def exists(self, path):
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                return bool(os.path.exists(unc_path))
            except Exception as exc:
                raise KodiVFSBackendError("Cannot check path existence: {}".format(exc))
        try:
            if self._vfs.exists(path):
                return True
            stripped = _strip_vfs_credentials(path)
            if stripped != path and self._vfs.exists(stripped):
                return True
            return False
        except Exception as exc:
            raise KodiVFSBackendError("Cannot check path existence: {}".format(exc))

    def read_text(self, path):
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                with open(unc_path, "rb") as handle:
                    data = handle.read(self._max_bytes + 1)
            except Exception as exc:
                raise KodiVFSBackendError("Cannot read {}: {}".format(path, exc))
        else:
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
        raw_bytes = text.encode("utf-8")
        if len(raw_bytes) > self._max_bytes:
            raise KodiVFSBackendError("Refusing oversized write: " + path)
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                parent_dir = os.path.dirname(unc_path)
                if parent_dir and not os.path.isdir(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                with open(unc_path, "wb") as handle:
                    handle.write(raw_bytes)
                    handle.flush()
                return True
            except Exception as exc:
                raise KodiVFSBackendError("Cannot write {}: {}".format(path, exc))
        if "/" in path:
            parent_vfs = path.rsplit("/", 1)[0]
            if parent_vfs and not parent_vfs.endswith(":/"):
                try:
                    if not self.exists(parent_vfs):
                        self.mkdirs(parent_vfs)
                except Exception:
                    pass
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
        unc_src = _resolve_win_unc(source, self._allow_win_unc)
        unc_dst = _resolve_win_unc(destination, self._allow_win_unc)
        if unc_src is not None and unc_dst is not None:
            try:
                if os.path.exists(unc_dst):
                    return False
                parent_dir = os.path.dirname(unc_dst)
                if parent_dir and not os.path.isdir(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                os.replace(unc_src, unc_dst)
                return True
            except Exception as exc:
                raise KodiVFSBackendError("Cannot rename {} to {}: {}".format(source, destination, exc))
        try:
            # The injected cross-process lock is required to protect this
            # check-and-rename sequence from other SudoSync writers.
            if self._vfs.exists(destination):
                return False
            for attempt in range(3):
                if self._vfs.rename(source, destination):
                    return True
                stripped_src = _strip_vfs_credentials(source)
                stripped_dst = _strip_vfs_credentials(destination)
                if (stripped_src != source or stripped_dst != destination) and not self._vfs.exists(stripped_dst):
                    if self._vfs.rename(stripped_src, stripped_dst):
                        return True
                if attempt < 2 and not self._vfs.exists(destination) and self._vfs.exists(source):
                    try:
                        import time
                        time.sleep(0.05 * (attempt + 1))
                    except Exception:
                        pass
            return False
        except Exception as exc:
            raise KodiVFSBackendError("Cannot rename {} to {}: {}".format(source, destination, exc))

    def delete(self, path):
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                if not os.path.exists(unc_path):
                    return False
                os.remove(unc_path)
                return True
            except Exception as exc:
                raise KodiVFSBackendError("Cannot delete {}: {}".format(path, exc))
        try:
            if self._vfs.exists(path):
                return bool(self._vfs.delete(path))
            stripped = _strip_vfs_credentials(path)
            if stripped != path and self._vfs.exists(stripped):
                return bool(self._vfs.delete(stripped))
            return False
        except Exception as exc:
            raise KodiVFSBackendError("Cannot delete {}: {}".format(path, exc))

    def mkdirs(self, path):
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                os.makedirs(unc_path, exist_ok=True)
                return True
            except Exception as exc:
                raise KodiVFSBackendError("Cannot create directory {}: {}".format(path, exc))
        try:
            if self.exists(path):
                return True
            if self._vfs.mkdirs(path):
                return True
            stripped = _strip_vfs_credentials(path)
            if stripped != path and self._vfs.mkdirs(stripped):
                return True
            if self.exists(path) or (stripped != path and self.exists(stripped)):
                return True
            return False
        except Exception as exc:
            raise KodiVFSBackendError("Cannot create directory {}: {}".format(path, exc))

    def listdir(self, path):
        unc_path = _resolve_win_unc(path, self._allow_win_unc)
        if unc_path is not None:
            try:
                if not os.path.exists(unc_path):
                    return [], []
                dirs = []
                files = []
                with os.scandir(unc_path) as it:
                    for entry in it:
                        if entry.is_dir():
                            dirs.append(entry.name)
                        else:
                            files.append(entry.name)
                return dirs, files
            except Exception as exc:
                raise KodiVFSBackendError("Cannot list directory {}: {}".format(path, exc))
        try:
            result = self._vfs.listdir(path)
        except Exception as exc:
            raise KodiVFSBackendError("Cannot list directory {}: {}".format(path, exc))
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise KodiVFSBackendError("Kodi VFS returned an invalid directory listing")
        directories, files = result
        if not isinstance(directories, (tuple, list)) or not isinstance(files, (tuple, list)):
            raise KodiVFSBackendError("Kodi VFS returned invalid directory entries")
        dirs_list, files_list = list(directories), list(files)
        if not dirs_list and not files_list:
            stripped = _strip_vfs_credentials(path)
            if stripped != path:
                try:
                    alt = self._vfs.listdir(stripped)
                    if isinstance(alt, (tuple, list)) and len(alt) == 2:
                        alt_d, alt_f = alt
                        if isinstance(alt_d, (tuple, list)) and isinstance(alt_f, (tuple, list)):
                            dirs_list, files_list = list(alt_d), list(alt_f)
                except Exception:
                    pass
        return dirs_list, files_list
