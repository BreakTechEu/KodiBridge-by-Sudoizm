# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for the shared client snapshot catalog."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.catalog import SnapshotCatalog, SnapshotCatalogError
from resources.lib.sudosync_v2.storage import SharedJsonRepository
from resources.lib.sudosync_v2.snapshot import build_snapshot

A = "12345678-1234-4234-8234-123456789012"
B = "22345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


class Backend(object):
    def __init__(self):
        self.files = {}
        self.directories = {}

    def exists(self, path):
        return path in self.files or path in self.directories

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, text):
        self.files[path] = text
        return True

    def rename(self, source, destination):
        if source not in self.files or destination in self.files:
            return False
        self.files[destination] = self.files.pop(source)
        return True

    def delete(self, path):
        if path not in self.files:
            return False
        del self.files[path]
        return True

    def mkdirs(self, path):
        self.directories[path] = True
        return True

    def listdir(self, path):
        prefix = path.rstrip("/") + "/"
        return [], [name[len(prefix):] for name in self.files if name.startswith(prefix)]


class Locks(object):
    def __init__(self):
        self.guard = threading.Lock()
        self.locks = {}

    def __call__(self, path):
        with self.guard:
            return self.locks.setdefault(path, threading.RLock())


def snap(client_id, name):
    return build_snapshot([], [], client_id, name, generated_at=STAMP)


class TestSnapshotCatalog(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.repository = SharedJsonRepository(self.backend, Locks())
        self.catalog = SnapshotCatalog(self.repository, self.backend, "smb://nas/share/SudoSync")

    def test_publish_load_and_list_peers(self):
        path_a = self.catalog.publish(snap(A, "Living Room"))
        path_b = self.catalog.publish(snap(B, "Bedroom"))
        self.assertTrue(path_a.endswith("client-" + A + ".json"))
        self.assertEqual(self.catalog.load(A)["client"]["id"], A)
        peers = self.catalog.load_peers(exclude_client_id=A)
        self.assertEqual([peer["client"]["id"] for peer in peers], [B])
        self.assertTrue(path_b in self.backend.files)

    def test_missing_clients_directory_is_empty(self):
        self.assertEqual(self.catalog.load_peers(), [])

    def test_rejects_noncanonical_or_invalid_client_id(self):
        with self.assertRaises(SnapshotCatalogError):
            self.catalog.load("../escape")

    def test_invalid_snapshot_is_not_published(self):
        with self.assertRaises(SnapshotCatalogError):
            self.catalog.publish({"client": {"id": A}})


if __name__ == "__main__":
    unittest.main()
