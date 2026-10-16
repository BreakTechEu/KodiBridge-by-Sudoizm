# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import datetime
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock xbmc modules
sys.modules["xbmc"] = MagicMock()
sys.modules["xbmcaddon"] = MagicMock()
sys.modules["xbmcgui"] = MagicMock()
sys.modules["xbmcvfs"] = MagicMock()

# Append the resources/lib directory
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "service.sudosync")
    ),
)
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "service.sudosync",
            "resources",
            "lib",
        )
    ),
)

import sudosync_core
import sudosync_merge


def _make_base_snapshot():
    return {
        "format": "SudoSync client snapshot",
        "schema_version": 2,
        "client": {
            "id": "12345678-1234-1234-1234-123456789012",
            "name": "Kodi",
        },
        "generated_at": "2026-10-16T10:00:00.000Z",
        "movies": [],
        "episodes": [],
    }


class TestLiveApplyIdentityAndSnapshotTimestamp(unittest.TestCase):
    def setUp(self):
        sudosync_core._NFO_ID_CACHE.clear()

    def tearDown(self):
        sudosync_core._NFO_ID_CACHE.clear()

    def test_01_build_live_plan_includes_sorted_client_aliases(self):
        snap_a = {
            "client": {"id": "client-A", "name": "Salon"},
            "movies": [
                {
                    "type": "movie",
                    "title": "Matrix",
                    "year": 1999,
                    "file": "smb://nas/movies/Matrix.mkv",
                    "ids": {"tmdb": "603", "imdb": "tt0133093"},
                    "local": {"movieid": 10},
                    "sync_local_key": "movie:10",
                    "state": {
                        "playcount": 0,
                        "lastplayed": "",
                        "resume": {"position": 0.0, "total": 0.0},
                        "userrating": 0,
                    },
                    "field_versions": {
                        "playcount": {
                            "lc": 1,
                            "ts": "2026-10-16T08:00:00.000Z",
                            "seq": 1,
                            "client_id": "client-A",
                        }
                    },
                }
            ],
            "episodes": [],
        }
        snap_b = {
            "client": {"id": "client-B", "name": "Sypialnia"},
            "movies": [
                {
                    "type": "movie",
                    "title": "Matrix",
                    "year": 1999,
                    "file": "smb://nas/movies/Matrix.mkv",
                    "ids": {
                        "imdb": "tt0133093",
                        "tmdb": "603",
                        "trakt": "481",
                    },
                    "local": {"movieid": 25},
                    "sync_local_key": "movie:25",
                    "state": {
                        "playcount": 1,
                        "lastplayed": "2026-10-16 09:00:00",
                        "resume": {"position": 0.0, "total": 0.0},
                        "userrating": 0,
                    },
                    "field_versions": {
                        "playcount": {
                            "lc": 2,
                            "ts": "2026-10-16T09:00:00.000Z",
                            "seq": 2,
                            "client_id": "client-B",
                        },
                        "lastplayed": {
                            "lc": 2,
                            "ts": "2026-10-16T09:00:00.000Z",
                            "seq": 2,
                            "client_id": "client-B",
                        },
                    },
                }
            ],
            "episodes": [],
        }

        plan = sudosync_merge.build_live_plan([snap_a, snap_b])
        items_a = plan["planned_changes"]["client-A"]
        self.assertEqual(len(items_a), 1)
        aliases_a = items_a[0].get("aliases")
        self.assertIsInstance(aliases_a, list)
        self.assertTrue(len(aliases_a) > 0)
        self.assertEqual(aliases_a, sorted(aliases_a))
        # Must contain only client A's aliases, not client B's extra trakt alias
        self.assertEqual(
            aliases_a,
            ["movie:imdb:tt0133093", "movie:tmdb:603"],
        )

    def test_02_build_dry_run_includes_sorted_client_aliases(self):
        snap_a = {
            "client": {"id": "client-A", "name": "Salon"},
            "movies": [
                {
                    "type": "movie",
                    "title": "Matrix",
                    "year": 1999,
                    "file": "smb://nas/movies/Matrix.mkv",
                    "ids": {"tmdb": "603", "imdb": "tt0133093"},
                    "local": {"movieid": 10},
                    "state": {
                        "playcount": 0,
                        "lastplayed": "",
                        "resume": {"position": 0.0, "total": 0.0},
                        "userrating": None,
                    },
                }
            ],
            "episodes": [],
        }
        snap_b = {
            "client": {"id": "client-B", "name": "Sypialnia"},
            "movies": [
                {
                    "type": "movie",
                    "title": "Matrix",
                    "year": 1999,
                    "file": "smb://nas/movies/Matrix.mkv",
                    "ids": {
                        "imdb": "tt0133093",
                        "tmdb": "603",
                        "trakt": "481",
                    },
                    "local": {"movieid": 25},
                    "state": {
                        "playcount": 1,
                        "lastplayed": "2026-10-16 09:00:00",
                        "resume": {"position": 0.0, "total": 0.0},
                        "userrating": 8,
                    },
                }
            ],
            "episodes": [],
        }

        plan = sudosync_merge.build_dry_run(
            [snap_a, snap_b], base_client_id="client-B"
        )
        items_a = plan["planned_changes"]["client-A"]
        self.assertEqual(len(items_a), 1)
        aliases_a = items_a[0].get("aliases")
        self.assertIsInstance(aliases_a, list)
        self.assertTrue(len(aliases_a) > 0)
        self.assertEqual(aliases_a, sorted(aliases_a))
        self.assertEqual(
            aliases_a,
            ["movie:imdb:tt0133093", "movie:tmdb:603"],
        )

    def test_03_apply_one_planned_item_matching_map_calls_rpc_once(self):
        item = {
            "type": "movie",
            "display": "Matrix (1999)",
            "aliases": ["movie:imdb:tt0133093"],
            "local": {"movieid": 999},
            "file": "smb://nas/movies/Matrix.mkv",
            "changes": {"playcount": {"from": 0, "to": 1}},
        }
        secure_map = {
            ("movie", "smb://nas/movies/Matrix.mkv"): {
                "id": 42,
                "aliases": {"movie:imdb:tt0133093", "movie:tmdb:603"},
            }
        }
        with patch.object(sudosync_core, "rpc", return_value="OK") as mock_rpc:
            res = sudosync_core._apply_one_planned_item(item, secure_map=secure_map)
            mock_rpc.assert_called_once_with(
                "VideoLibrary.SetMovieDetails",
                {"movieid": 42, "playcount": 1},
            )
            self.assertFalse(res["skipped"])
            self.assertEqual(res["params"]["movieid"], 42)

    def test_04_apply_one_planned_item_disjoint_aliases_raises_without_rpc(self):
        item = {
            "type": "movie",
            "display": "Matrix (1999)",
            "aliases": ["movie:imdb:tt0133093"],
            "local": {"movieid": 10},
            "file": "smb://nas/movies/Matrix.mkv",
            "changes": {"playcount": {"from": 0, "to": 1}},
        }
        secure_map = {
            ("movie", "smb://nas/movies/Matrix.mkv"): {
                "id": 77,
                "aliases": {"movie:imdb:tt9999999"},
            }
        }
        with patch.object(sudosync_core, "rpc") as mock_rpc:
            with self.assertRaises(ValueError) as ctx:
                sudosync_core._apply_one_planned_item(item, secure_map=secure_map)
            mock_rpc.assert_not_called()
            self.assertIn("Matrix (1999)", str(ctx.exception))

    def test_05_apply_one_planned_item_missing_plan_aliases_raises_without_rpc(self):
        item = {
            "type": "movie",
            "display": "Matrix (1999)",
            "local": {"movieid": 10},
            "file": "smb://nas/movies/Matrix.mkv",
            "changes": {"playcount": {"from": 0, "to": 1}},
        }
        secure_map = {
            ("movie", "smb://nas/movies/Matrix.mkv"): {
                "id": 10,
                "aliases": {"movie:imdb:tt0133093"},
            }
        }
        with patch.object(sudosync_core, "rpc") as mock_rpc:
            with self.assertRaises(ValueError) as ctx:
                sudosync_core._apply_one_planned_item(item, secure_map=secure_map)
            mock_rpc.assert_not_called()
            self.assertIn("Matrix (1999)", str(ctx.exception))

    def test_06_apply_one_planned_item_missing_local_aliases_raises_without_rpc(self):
        item = {
            "type": "movie",
            "display": "Matrix (1999)",
            "aliases": ["movie:imdb:tt0133093"],
            "local": {"movieid": 10},
            "file": "smb://nas/movies/Matrix.mkv",
            "changes": {"playcount": {"from": 0, "to": 1}},
        }
        secure_map = {
            ("movie", "smb://nas/movies/Matrix.mkv"): {
                "id": 10,
                "aliases": set(),
            }
        }
        with patch.object(sudosync_core, "rpc") as mock_rpc:
            with self.assertRaises(ValueError) as ctx:
                sudosync_core._apply_one_planned_item(item, secure_map=secure_map)
            mock_rpc.assert_not_called()
            self.assertIn("Matrix (1999)", str(ctx.exception))

    def test_07_apply_one_planned_item_empty_map_raises_not_in_kodi(self):
        item = {
            "type": "movie",
            "display": "Matrix (1999)",
            "aliases": ["movie:imdb:tt0133093"],
            "local": {"movieid": 10},
            "file": "smb://nas/movies/Matrix.mkv",
            "changes": {"playcount": {"from": 0, "to": 1}},
        }
        with patch.object(sudosync_core, "rpc") as mock_rpc:
            with self.assertRaises(ValueError) as ctx:
                sudosync_core._apply_one_planned_item(item, secure_map={})
            mock_rpc.assert_not_called()
            self.assertIn("nie istnieje w Kodi", str(ctx.exception))
            self.assertIn("Matrix (1999)", str(ctx.exception))

    def test_08_alias_format_compatibility_between_plan_and_secure_map(self):
        kodi_movie = {
            "movieid": 15,
            "title": "Matrix",
            "year": 1999,
            "file": "smb://nas/movies/Matrix.mkv",
            "uniqueid": {"imdb": "tt0133093", "tmdb": "603"},
            "imdbnumber": "tt0133093",
            "playcount": 0,
            "lastplayed": "",
            "resume": {"position": 0, "total": 0},
            "userrating": 0,
        }
        record_a = sudosync_core._record("movie", kodi_movie, first_import=False)
        record_a["field_versions"] = {
            "playcount": {
                "lc": 1,
                "ts": "2026-10-16T08:00:00.000Z",
                "seq": 1,
                "client_id": "client-A",
            }
        }
        record_b = dict(record_a)
        record_b["state"] = {
            "playcount": 1,
            "lastplayed": "2026-10-16 09:00:00",
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": 0,
        }
        record_b["field_versions"] = {
            "playcount": {
                "lc": 2,
                "ts": "2026-10-16T09:00:00.000Z",
                "seq": 2,
                "client_id": "client-B",
            },
            "lastplayed": {
                "lc": 2,
                "ts": "2026-10-16T09:00:00.000Z",
                "seq": 2,
                "client_id": "client-B",
            },
        }
        snap_a = {
            "client": {"id": "client-A", "name": "Salon"},
            "movies": [record_a],
            "episodes": [],
        }
        snap_b = {
            "client": {"id": "client-B", "name": "Sypialnia"},
            "movies": [record_b],
            "episodes": [],
        }
        plan = sudosync_merge.build_live_plan([snap_a, snap_b])
        planned_item = plan["planned_changes"]["client-A"][0]

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": [kodi_movie]}
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": []}
            return "OK"

        with patch.object(sudosync_core, "rpc", side_effect=fake_rpc):
            secure_map = sudosync_core._get_secure_local_identity_map()
            local_entry = secure_map[("movie", "smb://nas/movies/Matrix.mkv")]
            intersection = set(planned_item["aliases"]).intersection(
                local_entry["aliases"]
            )
            self.assertTrue(len(intersection) > 0)
            res = sudosync_core._apply_one_planned_item(
                planned_item, secure_map=secure_map
            )
            self.assertFalse(res["skipped"])
            self.assertEqual(res["params"]["movieid"], 15)

    def test_09_get_secure_local_identity_map_logs_warning_and_continues_on_error(self):
        kodi_episode = {
            "episodeid": 88,
            "file": "smb://nas/tv/Show/S01E01.mkv",
            "uniqueid": {"tvdb": "12345"},
        }

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                raise RuntimeError("Kodi RPC timeout on movies")
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": [kodi_episode]}
            return {}

        with patch.object(sudosync_core, "rpc", side_effect=fake_rpc), patch.object(
            sudosync_core, "log"
        ) as mock_log:
            identity_map = sudosync_core._get_secure_local_identity_map()
            mock_log.assert_called_once()
            logged_msg, logged_level = mock_log.call_args[0]
            self.assertIn("movies", logged_msg)
            self.assertIn("Kodi RPC timeout on movies", logged_msg)
            self.assertEqual(logged_level, sudosync_core.xbmc.LOGWARNING)
            self.assertIn(
                ("episode", "smb://nas/tv/Show/S01E01.mkv"), identity_map
            )
            self.assertEqual(
                identity_map[("episode", "smb://nas/tv/Show/S01E01.mkv")]["id"],
                88,
            )

    def test_10_validate_remote_snapshot_ms_2h_in_future_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future_ms = (
            (now + datetime.timedelta(hours=2))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snap = _make_base_snapshot()
        snap["generated_at"] = future_ms
        self.assertEqual(
            sudosync_core._validate_remote_snapshot(snap),
            "snapshot is from the future",
        )

    def test_11_validate_remote_snapshot_ms_30m_in_future_accepted(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        near_future_ms = (
            (now + datetime.timedelta(minutes=30))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snap = _make_base_snapshot()
        snap["generated_at"] = near_future_ms
        self.assertIsNone(sudosync_core._validate_remote_snapshot(snap))

    def test_12_validate_remote_snapshot_ms_months_ago_accepted(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        months_ago_ms = (
            (now - datetime.timedelta(days=120))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snap = _make_base_snapshot()
        snap["generated_at"] = months_ago_ms
        self.assertIsNone(sudosync_core._validate_remote_snapshot(snap))

    def test_13_validate_remote_snapshot_no_ms_2h_in_future_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future_no_ms = (now + datetime.timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        snap = _make_base_snapshot()
        snap["generated_at"] = future_no_ms
        self.assertEqual(
            sudosync_core._validate_remote_snapshot(snap),
            "snapshot is from the future",
        )

    def test_14_validate_remote_snapshot_invalid_generated_at_text_accepted(self):
        snap = _make_base_snapshot()
        snap["generated_at"] = "not-a-valid-iso-timestamp"
        self.assertIsNone(sudosync_core._validate_remote_snapshot(snap))


if __name__ == "__main__":
    unittest.main()
