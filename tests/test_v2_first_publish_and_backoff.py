# -*- coding: utf-8 -*-
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
from unittest.mock import MagicMock, patch

# Ensure xbmc stubs exist before importing addon modules
if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = MagicMock()
if "xbmcaddon" not in sys.modules:
    sys.modules["xbmcaddon"] = MagicMock()
if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = MagicMock()
if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = MagicMock()

SERVICE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "service.sudosync")
)
LIB_DIR = os.path.abspath(
    os.path.join(SERVICE_DIR, "resources", "lib")
)
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import service as sudosync_service
import sudosync_core
from resources.lib.sudosync_v2.snapshot import build_snapshot
from resources.lib.sudosync_v2.planning import build_sync_plan
from service import _live_retry_delay_seconds


class TestV2FirstPublishAndBackoff(unittest.TestCase):
    def setUp(self):
        self.client_a = "11111111-1111-4111-8111-111111111111"
        self.client_b = "22222222-2222-4222-8222-222222222222"

    def test_01_first_snapshot_gets_zero_versions_regardless_of_logical_clock(self):
        """Case 1: First snapshot (previous_snapshot=None) with logical_clock=7 and suppress_local_changes=False receives lc=0, seq=0 for all field versions."""
        item = {
            "movieid": 10,
            "title": "Movie 1",
            "imdbnumber": "tt1111111",
            "uniqueid": {"imdb": "tt1111111"},
            "playcount": 1,
            "lastplayed": "2026-10-16T10:00:00Z",
            "resume": {"position": 120.0, "total": 3600.0},
            "userrating": 8,
        }
        snap = build_snapshot(
            [item], [], self.client_a, "ClientA",
            previous_snapshot=None,
            logical_clock=7,
            suppress_local_changes=False,
        )
        versions = snap["movies"][0]["versions"]
        for field in ("playcount", "lastplayed", "resume", "userrating"):
            self.assertEqual(versions[field], {"lc": 0, "seq": 0, "client_id": self.client_a})

    def test_02_audit_scenario_fresh_peer_first_publish_does_not_overwrite_client_history(self):
        """Case 2: Audit scenario where Client A has edit history (Movie X: lc=3, seq=1; Movie Y: lc=7, seq=1).

        Fresh Client B publishes its first snapshot with logical_clock=7 (all unwatched).
        build_sync_plan for A with peer B must yield 0 changes.
        NOTE: Before the fix, Client B's first snapshot inherited logical_clock=7, causing
        build_sync_plan to overwrite Movie X on Client A with playcount=0 and clear lastplayed.
        """
        movie_x_item = {
            "movieid": 1,
            "title": "Movie X",
            "imdbnumber": "tt1111111",
            "uniqueid": {"imdb": "tt1111111"},
            "playcount": 0,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 7200.0},
            "userrating": None,
        }
        movie_y_item = {
            "movieid": 2,
            "title": "Movie Y",
            "imdbnumber": "tt2222222",
            "uniqueid": {"imdb": "tt2222222"},
            "playcount": 0,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 7200.0},
            "userrating": None,
        }

        # Client A: initial snapshot (all fields at lc=0)
        snap_a_init = build_snapshot([movie_x_item, movie_y_item], [], self.client_a, "ClientA", previous_snapshot=None)

        # Client A: edit Movie X (watched) with logical_clock=2 -> lc becomes max(2,0)+1 = 3, seq=1
        movie_x_watched = dict(movie_x_item, playcount=1, lastplayed="2026-10-15T12:00:00Z")
        snap_a_x = build_snapshot(
            [movie_x_watched, movie_y_item],
            [], self.client_a, "ClientA",
            previous_snapshot=snap_a_init,
            logical_clock=2,
        )

        # Client A: edit Movie Y (watched) with logical_clock=6 -> lc becomes max(6,0)+1 = 7, seq=1
        movie_y_watched = dict(movie_y_item, playcount=2, lastplayed="2026-10-16T18:00:00Z", userrating=9)
        snap_a_final = build_snapshot(
            [movie_x_watched, movie_y_watched],
            [], self.client_a, "ClientA",
            previous_snapshot=snap_a_x,
            logical_clock=6,
        )

        # Verify Client A versions: Movie X has playcount lc=3, seq=1; Movie Y has playcount lc=7, seq=1
        self.assertEqual(snap_a_final["movies"][0]["versions"]["playcount"], {"lc": 3, "seq": 1, "client_id": self.client_a})
        self.assertEqual(snap_a_final["movies"][1]["versions"]["playcount"], {"lc": 7, "seq": 1, "client_id": self.client_a})

        # Fresh Client B: initial snapshot (all unwatched) with logical_clock=7
        movie_x_unwatched = {
            "movieid": 10,
            "title": "Movie X",
            "imdbnumber": "tt1111111",
            "uniqueid": {"imdb": "tt1111111"},
            "playcount": 0,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": None,
        }
        movie_y_unwatched = {
            "movieid": 20,
            "title": "Movie Y",
            "imdbnumber": "tt2222222",
            "uniqueid": {"imdb": "tt2222222"},
            "playcount": 0,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": None,
        }
        snap_b = build_snapshot(
            [movie_x_unwatched, movie_y_unwatched], [], self.client_b, "ClientB",
            previous_snapshot=None,
            logical_clock=7,
        )

        # All fields in fresh Client B snapshot must be at lc=0
        for m in snap_b["movies"]:
            for field in ("playcount", "lastplayed", "resume", "userrating"):
                self.assertEqual(m["versions"][field]["lc"], 0)

        # Plan for Client A against peer Client B must produce 0 modifications
        plan = build_sync_plan(snap_a_final, [snap_b], {"movie": [1, 2], "episode": []})
        self.assertEqual(plan["change_count"], 0)
        self.assertEqual(len(plan["changes"]), 0)

    def test_03_second_snapshot_advances_changed_field_lc(self):
        """Case 3: Second snapshot with predecessor and changed field advances lc to max(logical_clock, lc) + 1."""
        item = {
            "movieid": 10,
            "title": "Movie 1",
            "imdbnumber": "tt1111111",
            "uniqueid": {"imdb": "tt1111111"},
            "playcount": 0,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": None,
        }
        first = build_snapshot([item], [], self.client_a, "ClientA", previous_snapshot=None)
        self.assertEqual(first["movies"][0]["versions"]["playcount"], {"lc": 0, "seq": 0, "client_id": self.client_a})

        item_watched = dict(item, playcount=1)
        second = build_snapshot(
            [item_watched], [], self.client_a, "ClientA",
            previous_snapshot=first,
            logical_clock=4,
        )
        self.assertEqual(second["movies"][0]["versions"]["playcount"], {"lc": 5, "seq": 1, "client_id": self.client_a})
        self.assertEqual(second["movies"][0]["versions"]["lastplayed"], {"lc": 0, "seq": 0, "client_id": self.client_a})

    def test_04_new_item_added_after_first_snapshot_gets_zero_lc(self):
        """Case 4: New library item discovered after the first snapshot gets lc == 0."""
        item1 = {
            "movieid": 1,
            "title": "Movie 1",
            "imdbnumber": "tt1111111",
            "uniqueid": {"imdb": "tt1111111"},
            "playcount": 1,
            "lastplayed": None,
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": None,
        }
        first = build_snapshot([item1], [], self.client_a, "ClientA", previous_snapshot=None)

        item2 = {
            "movieid": 2,
            "title": "Movie 2",
            "imdbnumber": "tt2222222",
            "uniqueid": {"imdb": "tt2222222"},
            "playcount": 5,
            "lastplayed": "2026-10-16T12:00:00Z",
            "resume": {"position": 10.0, "total": 100.0},
            "userrating": 7,
        }
        second = build_snapshot(
            [item1, item2], [], self.client_a, "ClientA",
            previous_snapshot=first,
            logical_clock=8,
        )
        self.assertEqual(second["movies"][1]["versions"]["playcount"], {"lc": 0, "seq": 0, "client_id": self.client_a})
        self.assertEqual(second["movies"][1]["versions"]["userrating"], {"lc": 0, "seq": 0, "client_id": self.client_a})

    def test_05_initial_snapshot_and_analyse_gate_on_init_completed(self):
        """Case 5: _initial_snapshot_and_analyse with init_completed=False does NOT call publish_local_snapshot; with True it does."""
        mock_v2 = MagicMock()
        mock_v2.publish_local_snapshot.return_value = {"status": "published"}

        with patch.object(sudosync_service, "collect_and_write"), \
                patch.object(sudosync_service, "run_dry_run"):
            # With init_completed=False
            sudosync_service._initial_snapshot_and_analyse(
                v2_runtime=mock_v2,
                abort_requested=lambda: False,
                init_completed=False,
            )
            mock_v2.publish_local_snapshot.assert_not_called()

            # With init_completed=True
            sudosync_service._initial_snapshot_and_analyse(
                v2_runtime=mock_v2,
                abort_requested=lambda: False,
                init_completed=True,
            )
            mock_v2.publish_local_snapshot.assert_called_once_with(
                abort_requested=unittest.mock.ANY,
                suppress_local_changes=False,
            )

    def test_06_sync_v2_after_initial_apply_suppresses_local_changes(self):
        """Case 6: _sync_v2_after_initial_apply calls publish_local_snapshot with suppress_local_changes=True."""
        mock_v2 = MagicMock()
        mock_v2.publish_local_snapshot.return_value = {"status": "published"}

        with patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=mock_v2):
            sudosync_core._sync_v2_after_initial_apply(
                settings={"base_path": "smb://nas/sudosync", "client_name": "Salon"},
                client_id=self.client_a,
            )
            mock_v2.publish_local_snapshot.assert_called_once_with(
                suppress_local_changes=True,
            )

    def test_07_live_retry_delay_seconds(self):
        """Case 7: _live_retry_delay_seconds returns >= 30 and >= live_poll for error or blocked, and 0 for normal busy or empty."""
        # For error
        res_error = {"busy": True, "error": "x"}
        self.assertGreaterEqual(_live_retry_delay_seconds(res_error, 10), 30)
        self.assertGreaterEqual(_live_retry_delay_seconds(res_error, 10), 10)
        self.assertEqual(_live_retry_delay_seconds(res_error, 60), 60)

        # For blocked
        res_blocked = {"busy": True, "blocked": True}
        self.assertGreaterEqual(_live_retry_delay_seconds(res_blocked, 15), 30)
        self.assertGreaterEqual(_live_retry_delay_seconds(res_blocked, 15), 15)
        self.assertEqual(_live_retry_delay_seconds(res_blocked, 45), 45)

        # For busy without error/blocked
        self.assertEqual(_live_retry_delay_seconds({"busy": True}, 60), sudosync_service.LIVE_BUSY_RETRY_SECONDS)

        # For empty dict
        self.assertEqual(_live_retry_delay_seconds({}, 60), 0)

        # For non-dict
        self.assertEqual(_live_retry_delay_seconds(None, 60), 0)

    def test_08_service_loop_backs_off_on_live_error(self):
        """Case 8: In _run_service, an error from live cycle causes backoff so second iteration in backoff window does not call live cycle again."""
        mock_monitor = MagicMock()
        mock_monitor.aborted = False
        mock_monitor.dirty = True
        mock_monitor.dirty_since = 1000.0
        mock_monitor.scanning = False
        mock_monitor.scan_refresh_pending = False
        mock_monitor.playback_dirty = False
        mock_monitor.user_update_dirty = False

        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            if iteration[0] >= 2:
                mock_monitor.aborted = True
                return True
            return False

        mock_monitor.abortRequested.side_effect = lambda: mock_monitor.aborted
        mock_monitor.waitForAbort.side_effect = fake_wait_for_abort

        live_cycle_calls = []

        def fake_execute_integrated_live_cycle(**kwargs):
            live_cycle_calls.append(kwargs)
            return {"busy": True, "error": "live_error", "applied_items": 0}

        # Simulated clock: startup at 1000.0, loop iteration 1 at 1005.0, loop iteration 2 at 1010.0 (within 60s backoff)
        simulated_times = [
            1000.0,  # settings / init
            1000.0,  # startup sync check
            1000.0,  # startup snapshot time
            1005.0,  # loop 1: now
            1005.0,  # loop 1: event_due check
            1005.0,  # loop 1: time.time() >= live_retry_not_before check
            1005.0,  # loop 1: last_live_poll = time.time()
            1005.0,  # loop 1: live_retry_not_before = time.time() + retry_delay (1005 + 60 = 1065)
            1010.0,  # loop 2: now
            1010.0,  # loop 2: event_due check
            1010.0,  # loop 2: time.time() >= live_retry_not_before check (1010 < 1065 -> FALSE!)
            1010.0,  # loop 2: remaining
        ]
        time_index = [0]

        def fake_time():
            if time_index[0] < len(simulated_times):
                t = simulated_times[time_index[0]]
                time_index[0] += 1
                return t
            return 1010.0

        with patch("resources.lib.sudosync_bootstrap.check_and_enforce_updates_before_bootstrap", return_value="ok"), \
                patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=MagicMock()), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=mock_monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": "smb://nas/sudosync",
                    "client_name": "Salon",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                }), \
                patch.object(sudosync_service, "read_shared_config", side_effect=[
                    {"initialization": {"completed": False}},  # startup: init not completed
                    {"initialization": {"completed": True}},   # loop 1: init completed
                    {"initialization": {"completed": True}},   # loop 2: init completed
                    {"initialization": {"completed": True}},
                ]), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "live_local_changes_detected", return_value={"changed": False}), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", side_effect=fake_execute_integrated_live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=fake_time):
            sudosync_service._run_service()

        # Iteration 1 called live cycle once. Iteration 2 skipped it because it was within the backoff window.
        self.assertEqual(len(live_cycle_calls), 1)
        # Dirty flag was preserved so it can retry after backoff expires
        self.assertTrue(mock_monitor.dirty)


if __name__ == "__main__":
    unittest.main()
