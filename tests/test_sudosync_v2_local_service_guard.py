"""Tests for the local OS-managed singleton service guard."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.local_service_guard import LocalServiceGuard


class FakeLockBackend(object):
    def __init__(self):
        self.held = set()

    def acquire(self, handle):
        path = os.path.abspath(handle.name)
        if path in self.held:
            return False
        self.held.add(path)
        return True

    def release(self, handle):
        self.held.remove(os.path.abspath(handle.name))


class TestLocalServiceGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "profile", "sudosync-service.lock")
        self.backend = FakeLockBackend()

    def tearDown(self):
        self.temp.cleanup()

    def test_second_service_instance_is_rejected(self):
        first = LocalServiceGuard(self.path, self.backend)
        second = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_release_does_not_delete_lock_file(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        guard.release()
        self.assertTrue(os.path.isfile(self.path))

    def test_nested_profile_directory_is_created(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        self.assertTrue(os.path.isdir(os.path.dirname(self.path)))
        guard.release()

    def test_empty_path_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalServiceGuard("", self.backend)


if __name__ == "__main__":
    unittest.main()
