# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import os
import sys
import unittest
from unittest.mock import MagicMock

# Mock xbmc modules
sys.modules['xbmc'] = MagicMock()
sys.modules['xbmcaddon'] = MagicMock()
sys.modules['xbmcgui'] = MagicMock()
sys.modules['xbmcvfs'] = MagicMock()

# Append the resources/lib directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'service.sudosync')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'service.sudosync', 'resources', 'lib')))

import sudosync_merge
import sudosync_core

class TestSudoSync(unittest.TestCase):

    def test_version_ordering(self):
        v1 = {"lc": 1, "ts": "2026-10-06T10:00:00Z", "seq": 1, "client_id": "A"}
        v2 = {"lc": 2, "ts": "2026-10-06T09:00:00Z", "seq": 2, "client_id": "B"}
        v3 = {"lc": 2, "ts": "2026-10-06T09:00:00Z", "seq": 3, "client_id": "B"}
        
        # lc=2 is newer than lc=1, even if ts is older (drifting clock)
        self.assertGreater(sudosync_merge._version_tuple(v2), sudosync_merge._version_tuple(v1))
        # lc=2, seq=3 is newer than lc=2, seq=2
        self.assertGreater(sudosync_merge._version_tuple(v3), sudosync_merge._version_tuple(v2))
        
    def test_mirror_detection(self):
        # same path
        cluster1 = [
            {"client_id": "A", "record": {"type": "movie", "title": "Test", "year": 2000, "file": "smb://A"}},
            {"client_id": "A", "record": {"type": "movie", "title": "Test", "year": 2000, "file": "smb://A"}},
        ]
        self.assertTrue(sudosync_merge._safe_mirror_cluster(cluster1))
        
        # local + network with same name
        cluster2 = [
            {"client_id": "B", "record": {"type": "movie", "title": "Film", "year": 2000, "file": "C:/Movies/film.mkv"}},
            {"client_id": "B", "record": {"type": "movie", "title": "Film", "year": 2000, "file": "smb://NAS/film.mkv"}},
        ]
        self.assertTrue(sudosync_merge._safe_mirror_cluster(cluster2))
        
        # two networks (not a safe mirror)
        cluster3 = [
            {"client_id": "B", "record": {"type": "movie", "title": "Film", "year": 2000, "file": "smb://NAS1/film.mkv"}},
            {"client_id": "B", "record": {"type": "movie", "title": "Film", "year": 2000, "file": "smb://NAS2/film.mkv"}},
        ]
        self.assertFalse(sudosync_merge._safe_mirror_cluster(cluster3))

    def test_remote_snapshot_schema(self):
        import datetime
        valid = {
            "format": "SudoSync client snapshot",
            "schema_version": 2,
            "client": {"id": "12345678-1234-1234-1234-123456789012", "name": "Kodi"},
            "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "movies": [],
            "episodes": [],
        }
        self.assertIsNone(sudosync_core._validate_remote_snapshot(valid))

        invalid = dict(valid)
        invalid["client"] = {"name": "Kodi"}
        self.assertIsNotNone(sudosync_core._validate_remote_snapshot(invalid))

        wrong_legacy_shape = dict(valid)
        wrong_legacy_shape.pop("client")
        wrong_legacy_shape["client_id"] = "12345678-1234-1234-1234-123456789012"
        wrong_legacy_shape["state"] = {"records": {}}
        self.assertIsNotNone(sudosync_core._validate_remote_snapshot(wrong_legacy_shape))

    def test_zero_version_has_lamport_clock(self):
        self.assertEqual(sudosync_core.ZERO_VERSION.get("lc"), 0)

    def test_conflict_version_tuple_contains_lamport_fields(self):
        version = {"lc": 7, "ts": "2026-10-06T10:00:00.000Z", "seq": 4, "client_id": "client-A"}
        self.assertEqual(
            sudosync_merge._version_tuple(version),
            (7, "2026-10-06T10:00:00.000Z", 4, "client-A"),
        )

    def test_numeric_version(self):
        self.assertEqual(sudosync_core._numeric_version("1.1.0-alpha"), (1, 1, 0, 0))
        self.assertEqual(sudosync_core._numeric_version("1.1.0-beta"), (1, 1, 0, 1))
        self.assertEqual(sudosync_core._numeric_version("1.1.0-rc"), (1, 1, 0, 2))
        self.assertEqual(sudosync_core._numeric_version("1.1.0"), (1, 1, 0, 3))
        self.assertEqual(sudosync_core._numeric_version("1.2.0"), (1, 2, 0, 3))
        
        self.assertGreater(sudosync_core._numeric_version("1.1.0-beta"), sudosync_core._numeric_version("1.1.0-alpha"))
        self.assertGreater(sudosync_core._numeric_version("1.1.0"), sudosync_core._numeric_version("1.1.0-beta"))

    def test_snapshots_ready_for_live(self):
        import time
        now = time.time()
        # Fresh snapshot, valid live mode
        snap1 = {
            "client": {"id": "c1", "name": "LivingRoom"},
            "mode": "live",
            "live_schema_version": sudosync_core.LIVE_SCHEMA_VERSION,
            "mtime": now,
        }
        # Fresh snapshot, old/not-live mode
        snap2 = {
            "client": {"id": "c2", "name": "Bedroom"},
            "mode": "initial",
            "live_schema_version": 0,
            "mtime": now,
        }
        # Stale snapshot (> 14 days old), old mode but should be ignored!
        snap3 = {
            "client": {"id": "c3", "name": "OldTablet"},
            "mode": "initial",
            "live_schema_version": 0,
            "mtime": now - (15 * 86400),
        }
        not_ready = sudosync_core._snapshots_ready_for_live([snap1, snap2, snap3])
        # Only snap2 should block live sync; snap3 is stale (>14d) and ignored
        self.assertEqual(not_ready, ["Bedroom"])

    def test_nfo_cache_and_clear(self):
        sudosync_core._NFO_ID_CACHE.clear()
        sudosync_core._NFO_ID_CACHE["/path/test.mkv"] = {"sudosync": "test-123"}
        self.assertEqual(len(sudosync_core._NFO_ID_CACHE), 1)
        sudosync_core._NFO_ID_CACHE.clear()
        self.assertEqual(len(sudosync_core._NFO_ID_CACHE), 0)

if __name__ == '__main__':
    unittest.main()
