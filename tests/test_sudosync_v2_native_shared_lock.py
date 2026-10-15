# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for the fail-closed native shared file-lock provider."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.native_shared_lock import (
    NativeSharedFileLock, NativeSharedFileLockFactory, SharedFileLockBusy,
    SharedFileLockError,
)


class FakeLockBackend(object):
    def __init__(self):
        self.held = False
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, handle, blocking):
        self.acquire_calls += 1
        if self.held:
            return False
        self.held = True
        return True

    def release(self, handle):
        self.release_calls += 1
        self.held = False


class TestNativeSharedFileLock(unittest.TestCase):
    def test_vfs_uri_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "VFS URI"):
            NativeSharedFileLock("smb://server/share/.lock")

    def test_relative_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            NativeSharedFileLock("relative/.lock")

    def test_missing_mount_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            missing = os.path.join(root, "not-mounted", "resource")
            lock = NativeSharedFileLock(missing)
            with self.assertRaisesRegex(SharedFileLockError, "directory"):
                lock.__enter__()

    def test_lock_releases_when_context_exits(self):
        with tempfile.TemporaryDirectory() as root:
            backend = FakeLockBackend()
            path = os.path.join(root, "shared.lock")
            with NativeSharedFileLock(path, backend=backend):
                self.assertTrue(backend.held)
            self.assertFalse(backend.held)
            self.assertEqual(backend.acquire_calls, 1)
            self.assertEqual(backend.release_calls, 1)

    def test_busy_nonblocking_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            backend = FakeLockBackend()
            backend.held = True
            lock = NativeSharedFileLock(
                os.path.join(root, "shared.lock"), backend=backend, blocking=False
            )
            with self.assertRaises(SharedFileLockBusy):
                lock.__enter__()
            self.assertTrue(backend.held)

    def test_factory_requires_explicit_physical_verification(self):
        with self.assertRaisesRegex(SharedFileLockError, "not been physically verified"):
            NativeSharedFileLockFactory()

    def test_factory_rejects_kodi_vfs_uri(self):
        factory = NativeSharedFileLockFactory(filesystem_verified=True)
        with self.assertRaisesRegex(SharedFileLockError, "VFS URIs are unsupported"):
            factory("smb://server/share/.sudosync-active-operation.json")

    def test_os_lock_rejects_second_nonblocking_owner_until_release(self):
        if os.name == "nt":
            self.skipTest("The OS lock conflict test uses POSIX flock semantics")
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "shared.lock")
            first = NativeSharedFileLock(path, blocking=True)
            second = NativeSharedFileLock(path, blocking=False)
            with first:
                with self.assertRaises(SharedFileLockBusy):
                    second.__enter__()
            with second:
                self.assertTrue(os.path.exists(path))

    def test_factory_places_persistent_lock_beside_resource(self):
        with tempfile.TemporaryDirectory() as root:
            factory = NativeSharedFileLockFactory(filesystem_verified=True)
            resource = os.path.join(root, ".sudosync-active-operation.json")
            context = factory(resource)
            self.assertEqual(context.path, resource + ".lock")


if __name__ == "__main__":
    unittest.main()
