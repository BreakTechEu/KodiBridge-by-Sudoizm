"""Tests for bounded JSON parsing and recoverable repository writes."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.storage import (
    RepositoryError, SharedJsonRepository, decode_json, encode_json,
)


class FakeBackend(object):
    def __init__(self):
        self.files = {}
        self.fail_promote = False

    def exists(self, path):
        return path in self.files

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, text):
        self.files[path] = text
        return True

    def copy(self, source, destination):
        self.files[destination] = self.files[source]
        return True

    def rename(self, source, destination):
        if self.fail_promote and ".tmp-" in source and destination.endswith(".json"):
            return False
        if source not in self.files:
            return False
        self.files[destination] = self.files.pop(source)
        return True

    def delete(self, path):
        if path not in self.files:
            return False
        del self.files[path]
        return True


class LockFactory(object):
    """Process-local lock for unit tests only; production must use cross-process locking."""
    def __init__(self):
        self.locks = {}
        self.guard = threading.Lock()

    def __call__(self, path):
        with self.guard:
            lock = self.locks.setdefault(path, threading.RLock())
        return lock


class TestJsonCodec(unittest.TestCase):
    def test_rejects_duplicate_keys(self):
        with self.assertRaises(RepositoryError):
            decode_json('{"a": 1, "a": 2}')

    def test_rejects_non_finite_numbers(self):
        with self.assertRaises(RepositoryError):
            decode_json('{"value": NaN}')
        with self.assertRaises(RepositoryError):
            encode_json({"value": float("inf")})

    def test_enforces_size_limit(self):
        with self.assertRaises(RepositoryError):
            decode_json('"long value"', max_bytes=3)


class TestSharedJsonRepository(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.repo = SharedJsonRepository(self.backend, LockFactory(), max_bytes=1024)
        self.path = "share/SudoSync.json"

    def test_save_load_and_backup_previous_document(self):
        self.repo.save(self.path, {"generation": 1})
        self.repo.save(self.path, {"generation": 2})
        self.assertEqual(self.repo.load(self.path), {"generation": 2})
        self.assertEqual(decode_json(self.backend.files[self.path + ".bak"]), {"generation": 1})

    def test_recovers_backup_when_main_is_missing(self):
        self.backend.files[self.path + ".bak"] = '{"generation": 3}'
        self.assertEqual(self.repo.load(self.path), {"generation": 3})
        self.assertEqual(self.repo.load(self.path), {"generation": 3})
        self.assertNotIn(self.path + ".bak", self.backend.files)

    def test_failed_promotion_restores_previous_document(self):
        self.repo.save(self.path, {"generation": 1})
        self.backend.fail_promote = True
        with self.assertRaises(RepositoryError):
            self.repo.save(self.path, {"generation": 2})
        self.backend.fail_promote = False
        self.assertEqual(self.repo.load(self.path), {"generation": 1})

    def test_invalid_existing_document_is_not_silently_overwritten(self):
        self.backend.files[self.path] = '{"generation": NaN}'
        with self.assertRaises(RepositoryError):
            self.repo.save(self.path, {"generation": 2})
        self.assertEqual(self.backend.files[self.path], '{"generation": NaN}')


if __name__ == "__main__":
    unittest.main()
