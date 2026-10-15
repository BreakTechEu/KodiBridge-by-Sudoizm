# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for durable transaction backup and journal storage."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.snapshot import build_snapshot
from resources.lib.sudosync_v2.transaction_store import TransactionStore, TransactionStoreError

CLIENT = "12345678-1234-4234-8234-123456789012"
SESSION = "32345678-1234-4234-8234-123456789012"


class MemoryRepository(object):
    def __init__(self):
        self.files = {}

    def load(self, path):
        value = self.files.get(path)
        return deepcopy(value)

    def save(self, path, value):
        self.files[path] = deepcopy(value)
        return True


def snapshot():
    return build_snapshot([], [], CLIENT, "Living room", generated_at="2026-10-11T10:00:00Z")


def journal():
    return {
        "format": "SudoSync v2 transaction journal",
        "schema_version": 1,
        "session_id": SESSION,
        "client_id": CLIENT,
        "status": "applying",
        "started_at": "2026-10-11T10:00:00Z",
        "finished_at": "",
        "backup_ref": "backup.json",
        "items": [{
            "type": "movie", "kodi_id": 5,
            "before": {"playcount": 0}, "changes": {"playcount": 1},
            "status": "pending",
        }],
        "error": "",
    }


class TestTransactionStore(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.store = TransactionStore(self.repo, "smb://nas/share/.SudoSync")

    def test_backup_is_persisted_and_read_back(self):
        value = snapshot()
        path = self.store.save_backup(value, SESSION)
        self.assertEqual(path, "smb://nas/share/.SudoSync/transactions/backups/backup-" + SESSION + ".json")
        self.assertEqual(self.repo.load(path), value)

    def test_backup_cannot_be_overwritten(self):
        self.store.save_backup(snapshot(), SESSION)
        with self.assertRaisesRegex(TransactionStoreError, "already exists"):
            self.store.save_backup(snapshot(), SESSION)

    def test_journal_round_trips(self):
        value = journal()
        self.assertTrue(self.store.persist_journal(value))
        self.assertEqual(self.store.load_journal(SESSION), value)

    def test_missing_journal_is_an_error_not_an_empty_transaction(self):
        with self.assertRaisesRegex(TransactionStoreError, "no durable transaction journal"):
            self.store.load_journal(SESSION)

    def test_wrong_session_in_journal_is_rejected(self):
        value = journal()
        value["session_id"] = "42345678-1234-4234-8234-123456789012"
        wrong_path = self.store.journal_path(SESSION)
        self.repo.files[wrong_path] = value
        with self.assertRaisesRegex(TransactionStoreError, "session ID"):
            self.store.load_journal(SESSION)

    def test_invalid_status_is_rejected(self):
        value = journal()
        value["status"] = "unknown"
        with self.assertRaisesRegex(TransactionStoreError, "status"):
            self.store.persist_journal(value)


if __name__ == "__main__":
    unittest.main()
