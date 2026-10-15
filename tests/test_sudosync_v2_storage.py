# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
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
        self.fail_backup_rotation = False

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
        if self.fail_backup_rotation and source.endswith(".json") and destination.endswith(".json.bak"):
            return False
        if source not in self.files or destination in self.files:
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

    def test_previous_backup_survives_failed_backup_rotation(self):
        self.repo.save(self.path, {"generation": 1})
        self.repo.save(self.path, {"generation": 2})
        self.backend.fail_backup_rotation = True
        with self.assertRaises(RepositoryError):
            self.repo.save(self.path, {"generation": 3})
        self.backend.fail_backup_rotation = False
        self.assertEqual(self.repo.load(self.path), {"generation": 2})
        self.assertEqual(decode_json(self.backend.files[self.path + ".bak"]), {"generation": 1})

    def test_missing_main_with_backup_must_be_recovered_before_save(self):
        self.backend.files[self.path + ".bak"] = '{"generation": 7}'
        with self.assertRaises(RepositoryError):
            self.repo.save(self.path, {"generation": 8})
        self.assertNotIn(self.path, self.backend.files)
        self.assertEqual(decode_json(self.backend.files[self.path + ".bak"]), {"generation": 7})

    def test_previous_backup_survives_failed_promotion(self):
        self.repo.save(self.path, {"generation": 1})
        self.repo.save(self.path, {"generation": 2})
        self.backend.fail_promote = True
        with self.assertRaises(RepositoryError):
            self.repo.save(self.path, {"generation": 3})
        self.backend.fail_promote = False
        self.assertEqual(self.repo.load(self.path), {"generation": 2})
        self.assertEqual(decode_json(self.backend.files[self.path + ".bak"]), {"generation": 1})


if __name__ == "__main__":
    unittest.main()
