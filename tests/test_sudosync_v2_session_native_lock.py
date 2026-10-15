# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Integration test: the shared-session coordinator can use the OS lock provider."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.native_shared_lock import NativeSharedFileLockFactory
from resources.lib.sudosync_v2.session import SharedOperationCoordinator, SessionBusyError


class MemoryBackend(object):
    def __init__(self):
        self.files = {}

    def exists(self, path):
        return path in self.files

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, text):
        self.files[path] = text
        return True

    def delete(self, path):
        if path not in self.files:
            return False
        del self.files[path]
        return True


class TestCoordinatorWithNativeLock(unittest.TestCase):
    def test_active_marker_blocks_second_coordinator_until_release(self):
        with tempfile.TemporaryDirectory() as root:
            marker_path = os.path.join(root, ".active.json")
            backend = MemoryBackend()
            factory = NativeSharedFileLockFactory(filesystem_verified=True, blocking=False)
            first = SharedOperationCoordinator(backend, factory, marker_path)
            second = SharedOperationCoordinator(backend, factory, marker_path)

            token = first.acquire("kodi-a")
            with self.assertRaises(SessionBusyError):
                second.acquire("kodi-b")
            first.release(token)

            token_b = second.acquire("kodi-b")
            self.assertTrue(backend.exists(marker_path))
            second.release(token_b)
            self.assertFalse(backend.exists(marker_path))


if __name__ == "__main__":
    unittest.main()
