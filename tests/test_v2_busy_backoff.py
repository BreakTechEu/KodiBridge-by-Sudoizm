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

        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
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
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
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

        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
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
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
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

        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
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
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", side_effect=fake_execute_integrated_live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=fake_time):
            sudosync_service._run_service()

        self.assertEqual(len(live_cycle_calls), 2)
        self.assertFalse(mock_monitor.dirty)
        self.assertEqual(mock_monitor.dirty_since, 0.0)
        self.assertFalse(mock_monitor.scan_refresh_pending)
        self.assertFalse(mock_monitor.playback_dirty)
        self.assertFalse(mock_monitor.user_update_dirty)

    def test_05_v2_runtime_error_returns_busy_error_for_backoff(self):
        """Case 5: When v2 sync_cycle raises RuntimeError, _execute_integrated_live_cycle catches it and returns busy+error for backoff without a toast."""
        mock_monitor = MagicMock()
        mock_monitor.abortRequested.return_value = False
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.side_effect = RuntimeError("peer snapshot unreadable")

        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service.xbmcgui, "Dialog") as mock_dialog_cls:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=mock_monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                receiver_notification_delay_ms=0,
            )
            self.assertTrue(res.get("busy"))
            self.assertEqual(res.get("error"), "peer snapshot unreadable")
            self.assertEqual(res.get("applied_items"), 0)
            self.assertEqual(_live_retry_delay_seconds(res, 60), 60)
            mock_dialog_cls.return_value.notification.assert_not_called()

    def test_05b_v2_committed_cycle_emits_single_toast(self):
        """Case 5b: A committed v2 cycle is not busy and emits one LIVE toast."""
        mock_monitor = MagicMock()
        mock_monitor.abortRequested.return_value = False
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.return_value = {"status": "committed", "applied_count": 2}

        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service.xbmcgui, "Dialog") as mock_dialog_cls:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=mock_monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                receiver_notification_delay_ms=0,
            )
            self.assertFalse(res.get("busy"))
            self.assertEqual(res.get("applied_items"), 2)
            mock_dialog_cls.return_value.notification.assert_called_once()

    def test_06_runtime_deferred_transaction_still_publishes_changed_local_snapshot(self):
        """Case 6: When TransactionRunner defers because playback started after preview, runtime.sync_cycle still publishes the updated local snapshot."""
        from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory, build_v2_runtime
        from tests.test_sudosync_v2_runtime import InMemorySharedBackend, MutableFakeKodi

        client_a = "12345678-1234-4234-8234-123456789012"
        client_b = "22345678-1234-4234-8234-123456789012"
        backend = InMemorySharedBackend()
        lock_factory = ProcessLocalLockFactory()

        kodi_a = MutableFakeKodi(movie_id=10, playcount=1)
        kodi_b = MutableFakeKodi(movie_id=20, playcount=0)

        runtime_a = build_v2_runtime(kodi_a, backend, "smb://nas/.SudoSync", lock_factory, client_a, "Kodi A")
        runtime_b = build_v2_runtime(kodi_b, backend, "smb://nas/.SudoSync", lock_factory, client_b, "Kodi B")

        # Device A publishes playcount=1
        runtime_a.sync_cycle()

        # Device B has local state (playcount=0) and receives a plan with 1 change from A,
        # but playback starts right after preview (when get_episodes finishes, before transaction_runner.run)
        orig_get_episodes = kodi_b.get_episodes
        def get_episodes_then_start_playing():
            eps = orig_get_episodes()
            kodi_b.playing = True
            return eps
        kodi_b.get_episodes = get_episodes_then_start_playing

        res_b = runtime_b.sync_cycle(publish_local=True)
        self.assertEqual(res_b["status"], "deferred")
        self.assertEqual(res_b["applied_count"], 0)
        self.assertEqual(kodi_b.writes, 0)
        self.assertIsNotNone(res_b["published_snapshot_path"])
        self.assertIsNotNone(runtime_b.catalog.load(client_b))


if __name__ == "__main__":
    unittest.main()


class TestLiveCycleRecordsErrorsInStatus(unittest.TestCase):
    """Every failing LIVE cycle path records the error in status.json (shown in the status window)."""

    def _run(self, v2_runtime):
        monitor = MagicMock()
        monitor.abortRequested.return_value = False
        monitor.scan_refresh_pending = False
        with patch.object(sudosync_service, "update_local_status_from_v2") as mock_status:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=v2_runtime,
                monitor=monitor,
                show_notification=False,
                suppress_local_changes=False,
                collect_local=True,
            )
        return res, mock_status

    def test_blocked_session_is_recorded(self):
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.return_value = {"status": "blocked", "applied_count": 0}
        res, mock_status = self._run(mock_v2)
        self.assertTrue(res["blocked"])
        self.assertIn("blocked", mock_status.call_args[1]["error"])

    def test_unexpected_status_is_recorded(self):
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.return_value = {"status": "weird", "applied_count": 0}
        res, mock_status = self._run(mock_v2)
        self.assertEqual(res["error"], "unexpected_v2_status")
        self.assertIn("weird", mock_status.call_args[1]["error"])

    def test_missing_runtime_is_recorded(self):
        res, mock_status = self._run(None)
        self.assertEqual(res["error"], "v2_unavailable")
        self.assertEqual(mock_status.call_args[1]["error"], "v2_unavailable")


class TestStartupRecoveryFailure(unittest.TestCase):
    def test_failing_startup_recovery_does_not_break_startup_synchronization(self):
        """recover() raising during startup must not cause an unbound v2_startup error."""
        runtime = MagicMock()
        runtime.recover.side_effect = RuntimeError("journal unreadable")
        monitor = MagicMock()
        monitor.abortRequested.return_value = False
        monitor.waitForAbort.return_value = True
        monitor.dirty = False
        monitor.scanning = False
        monitor.scan_refresh_pending = False
        logged = []
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": "smb://nas/.SudoSync/", "client_name": "PC", "interval_seconds": 300,
                    "live_poll_seconds": 60, "live_sync_enabled": True, "check_updates": False,
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="c1"), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", return_value={"busy": False, "applied_items": 0}) as cycle, \
                patch.object(sudosync_service, "log", side_effect=lambda msg, *a, **k: logged.append(str(msg))):
            sudosync_service._run_service()
        self.assertFalse(any("referenced before assignment" in m for m in logged))
        self.assertFalse(any("Startup synchronization failed" in m for m in logged))
        self.assertGreaterEqual(cycle.call_count, 1)
