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
from service import LIVE_BUSY_RETRY_SECONDS, _live_retry_delay_seconds


class TestV2BusyBackoff(unittest.TestCase):
    def test_01_live_retry_delay_seconds_variants(self):
        """Case 1: Verify _live_retry_delay_seconds for busy, error, blocked, idle, waiting, invalid inputs."""
        self.assertEqual(LIVE_BUSY_RETRY_SECONDS, 10)

        # Busy without error and without blocked returns LIVE_BUSY_RETRY_SECONDS
        self.assertEqual(
            _live_retry_delay_seconds({"busy": True}, 60),
            LIVE_BUSY_RETRY_SECONDS,
        )
        self.assertEqual(
            _live_retry_delay_seconds({"active": True, "busy": True, "message": "busy"}, 15),
            LIVE_BUSY_RETRY_SECONDS,
        )

        # Busy with error or blocked returns max(live_poll, 30)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, 10), 30)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, 60), 60)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "blocked": True}, 15), 30)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "blocked": True}, 45), 45)

        # Idle, waiting, empty dict, and non-dict return 0
        self.assertEqual(_live_retry_delay_seconds({"idle": True}, 60), 0)
        self.assertEqual(_live_retry_delay_seconds({"waiting": True}, 60), 0)
        self.assertEqual(_live_retry_delay_seconds({"busy": False}, 60), 0)
        self.assertEqual(_live_retry_delay_seconds({}, 60), 0)
        self.assertEqual(_live_retry_delay_seconds(None, 60), 0)
        self.assertEqual(_live_retry_delay_seconds("invalid", 60), 0)

        # Unusual live_poll values (string, None, negative) must not raise
        self.assertEqual(_live_retry_delay_seconds({"busy": True}, "not-a-number"), LIVE_BUSY_RETRY_SECONDS)
        self.assertEqual(_live_retry_delay_seconds({"busy": True}, None), LIVE_BUSY_RETRY_SECONDS)
        self.assertEqual(_live_retry_delay_seconds({"busy": True}, -20), LIVE_BUSY_RETRY_SECONDS)

        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, "45"), 45)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, "invalid"), 60)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, None), 60)
        self.assertEqual(_live_retry_delay_seconds({"busy": True, "error": "x"}, -20), 30)

    def test_02_service_loop_backs_off_on_busy_and_preserves_dirty_flags(self):
        """Case 2: When _execute_integrated_live_cycle returns {'busy': True}, the main loop backs off for LIVE_BUSY_RETRY_SECONDS and preserves all dirty flags."""
        mock_monitor = MagicMock()
        mock_monitor.aborted = False
        mock_monitor.dirty = True
        mock_monitor.dirty_since = 1000.0
        mock_monitor.scanning = False
        mock_monitor.scan_refresh_pending = True
        mock_monitor.playback_dirty = True
        mock_monitor.user_update_dirty = True

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
            return {"active": True, "busy": True, "applied_items": 0}

        # Startup at 1000.0 (init not completed -> _initial_snapshot_and_analyse).
        # Loop 1 at 1005.0 -> calls _execute_integrated_live_cycle -> sets live_retry_not_before = 1005.0 + 10 = 1015.0.
        # Loop 2 at 1010.0 (< 1015.0) -> skipped due to busy backoff.
        simulated_times = [
            1000.0,  # startup last_snapshot = time.time()
            1005.0,  # loop 1: now = time.time()
            1005.0,  # loop 1: time.time() >= live_retry_not_before
            1005.0,  # loop 1: last_live_poll = time.time()
            1005.0,  # loop 1: live_retry_not_before = time.time() + retry_delay (1015.0)
            1010.0,  # loop 2: now = time.time()
            1010.0,  # loop 2: time.time() >= live_retry_not_before (1010.0 < 1015.0 -> False)
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
                    {"initialization": {"completed": False}},  # startup
                    {"initialization": {"completed": True}},   # loop 1
                    {"initialization": {"completed": True}},   # loop 2
                ]), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "live_local_changes_detected", return_value={"changed": False}), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", side_effect=fake_execute_integrated_live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=fake_time):
            sudosync_service._run_service()

        self.assertEqual(len(live_cycle_calls), 1)
        self.assertTrue(mock_monitor.dirty)
        self.assertEqual(mock_monitor.dirty_since, 1000.0)
        self.assertTrue(mock_monitor.scan_refresh_pending)
        self.assertTrue(mock_monitor.playback_dirty)
        self.assertTrue(mock_monitor.user_update_dirty)

    def test_03_startup_cycle_busy_sets_backoff_window(self):
        """Case 3: A busy result in the startup cycle sets live_retry_not_before, preventing an immediate retry in loop 1."""
        mock_monitor = MagicMock()
        mock_monitor.aborted = False
        mock_monitor.dirty = True
        mock_monitor.dirty_since = 995.0
        mock_monitor.scanning = False
        mock_monitor.scan_refresh_pending = False
        mock_monitor.playback_dirty = False
        mock_monitor.user_update_dirty = False

        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            if iteration[0] >= 1:
                mock_monitor.aborted = True
                return True
            return False

        mock_monitor.abortRequested.side_effect = lambda: mock_monitor.aborted
        mock_monitor.waitForAbort.side_effect = fake_wait_for_abort

        live_cycle_calls = []

        def fake_execute_integrated_live_cycle(**kwargs):
            live_cycle_calls.append(kwargs)
            return {"active": True, "busy": True, "applied_items": 0}

        # Startup at 1000.0 (init completed -> runs startup live cycle -> returns busy -> live_retry_not_before = 1000.0 + 10 = 1010.0).
        # Loop 1 at 1004.0 (< 1010.0) -> skipped despite event_due being True.
        simulated_times = [
            1000.0,  # startup: last_live_poll = time.time()
            1000.0,  # startup: live_retry_not_before = time.time() + startup_delay (1010.0)
            1004.0,  # loop 1: now = time.time()
            1004.0,  # loop 1: time.time() >= live_retry_not_before (1004.0 < 1010.0 -> False)
        ]
        time_index = [0]

        def fake_time():
            if time_index[0] < len(simulated_times):
                t = simulated_times[time_index[0]]
                time_index[0] += 1
                return t
            return 1004.0

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
                patch.object(sudosync_service, "read_shared_config", return_value={
                    "initialization": {"completed": True},
                }), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "live_local_changes_detected", return_value={"changed": False}), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", side_effect=fake_execute_integrated_live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=fake_time):
            sudosync_service._run_service()

        # Only the startup call occurred; loop 1 was blocked by live_retry_not_before.
        self.assertEqual(len(live_cycle_calls), 1)
        self.assertTrue(mock_monitor.dirty)

    def test_04_service_loop_retries_after_busy_backoff_expires(self):
        """Case 4: Regression test that after LIVE_BUSY_RETRY_SECONDS elapses, the cycle is retried and clears dirty flags on success."""
        mock_monitor = MagicMock()
        mock_monitor.aborted = False
        mock_monitor.dirty = True
        mock_monitor.dirty_since = 1000.0
        mock_monitor.scanning = False
        mock_monitor.scan_refresh_pending = True
        mock_monitor.playback_dirty = True
        mock_monitor.user_update_dirty = True

        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            if iteration[0] >= 3:
                mock_monitor.aborted = True
                return True
            return False

        mock_monitor.abortRequested.side_effect = lambda: mock_monitor.aborted
        mock_monitor.waitForAbort.side_effect = fake_wait_for_abort

        live_cycle_calls = []

        def fake_execute_integrated_live_cycle(**kwargs):
            live_cycle_calls.append(kwargs)
            if len(live_cycle_calls) == 1:
                return {"active": True, "busy": True, "applied_items": 0}
            return {"active": True, "busy": False, "applied_items": 1}

        # Loop 1 at 1005.0 -> busy -> live_retry_not_before = 1015.0
        # Loop 2 at 1010.0 -> skipped (1010.0 < 1015.0)
        # Loop 3 at 1016.0 -> retried (1016.0 >= 1015.0) -> succeeds and clears dirty flags
        simulated_times = [
            1000.0,  # startup: last_snapshot = time.time()
            1005.0,  # loop 1: now = time.time()
            1005.0,  # loop 1: time.time() >= live_retry_not_before
            1005.0,  # loop 1: last_live_poll = time.time()
            1005.0,  # loop 1: live_retry_not_before = time.time() + 10 (1015.0)
            1010.0,  # loop 2: now = time.time()
            1010.0,  # loop 2: time.time() >= live_retry_not_before (False)
            1016.0,  # loop 3: now = time.time()
            1016.0,  # loop 3: time.time() >= live_retry_not_before (True)
            1016.0,  # loop 3: last_live_poll = time.time()
        ]
        time_index = [0]

        def fake_time():
            if time_index[0] < len(simulated_times):
                t = simulated_times[time_index[0]]
                time_index[0] += 1
                return t
            return 1016.0

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
                    {"initialization": {"completed": False}},  # startup
                    {"initialization": {"completed": True}},   # loop 1
                    {"initialization": {"completed": True}},   # loop 2
                    {"initialization": {"completed": True}},   # loop 3
                ]), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "live_local_changes_detected", return_value={"changed": False}), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", side_effect=fake_execute_integrated_live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=fake_time):
            sudosync_service._run_service()

        self.assertEqual(len(live_cycle_calls), 2)
        self.assertFalse(mock_monitor.dirty)
        self.assertEqual(mock_monitor.dirty_since, 0.0)
        self.assertFalse(mock_monitor.scan_refresh_pending)
        self.assertFalse(mock_monitor.playback_dirty)
        self.assertFalse(mock_monitor.user_update_dirty)


if __name__ == "__main__":
    unittest.main()
