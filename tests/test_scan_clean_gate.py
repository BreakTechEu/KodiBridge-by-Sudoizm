# -*- coding: utf-8 -*-
"""Synchronization waits for a library scan and clean, and reads .nfo files once after them."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import service as sudosync_service  # noqa: E402


class FakeMonitor(object):
    def __init__(self):
        self.aborted = False
        self.scanning = False
        self.cleaning = False
        self.cleaning_since = 0.0
        self.scan_refresh_pending = False
        self.scan_nfo_stale = False

    def abortRequested(self):
        return self.aborted


class Clock(object):
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.kodi_scanning = [False]
        self.asked = [0]

        def is_scanning():
            self.asked[0] += 1
            return self.kodi_scanning[0]

        for patcher in (
            patch.object(sudosync_service, "_is_library_scanning", side_effect=is_scanning),
            patch.object(sudosync_service.time, "monotonic", self.clock),
            patch.object(sudosync_service, "log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)


class CleanTrackingTests(GateTestBase):
    def _monitor(self):
        with patch.object(sudosync_service, "addon_settings", return_value={}):
            return sudosync_service.SudoSyncMonitor()

    def test_a_clean_keeps_the_library_busy_until_it_ends(self):
        monitor = self._monitor()
        self.assertFalse(sudosync_service._library_busy(monitor))
        monitor.onNotification("xbmc", "VideoLibrary.OnCleanStarted", "{}")
        self.assertTrue(sudosync_service._library_busy(monitor))
        monitor.onNotification("xbmc", "VideoLibrary.OnCleanFinished", "{}")
        self.assertFalse(sudosync_service._library_busy(monitor))
        self.assertTrue(monitor.scan_nfo_stale)
        self.assertTrue(monitor.scan_refresh_pending)

    def test_the_clean_callbacks_of_the_monitor(self):
        monitor = self._monitor()
        monitor.onCleanStarted("music")
        self.assertFalse(sudosync_service._library_busy(monitor))
        monitor.onCleanStarted("video")
        self.assertTrue(sudosync_service._library_busy(monitor))
        monitor.onCleanFinished("video")
        self.assertFalse(sudosync_service._library_busy(monitor))

    def test_a_clean_without_an_end_stops_counting(self):
        monitor = self._monitor()
        monitor.onCleanStarted("video")
        self.clock.now += sudosync_service.CLEAN_MAX_SECONDS + 1
        self.assertFalse(sudosync_service._library_busy(monitor))
        self.assertFalse(monitor.cleaning)

    def test_a_finished_scan_marks_the_nfo_cache_stale(self):
        monitor = self._monitor()
        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": True}):
            monitor.onScanFinished("video")
        self.assertTrue(monitor.scan_nfo_stale)


class CycleAbortTests(GateTestBase):
    def test_idle_library_does_not_stop_the_cycle(self):
        self.assertFalse(sudosync_service._cycle_abort(FakeMonitor())())

    def test_kodi_closing_scan_and_clean_stop_the_cycle(self):
        for name in ("aborted", "scanning", "cleaning"):
            monitor = FakeMonitor()
            monitor.cleaning_since = self.clock.now
            setattr(monitor, name, True)
            self.assertTrue(sudosync_service._cycle_abort(monitor)(), name)

    def test_kodi_is_asked_about_a_scan_once_per_interval(self):
        check = sudosync_service._cycle_abort(FakeMonitor())
        for _ in range(5):
            self.assertFalse(check())
        self.assertEqual(self.asked[0], 1)
        self.kodi_scanning[0] = True
        self.assertFalse(check())
        self.clock.now += sudosync_service.SCAN_CHECK_INTERVAL
        self.assertTrue(check())
        self.assertEqual(self.asked[0], 2)


class LiveCycleTests(GateTestBase):
    def setUp(self):
        super(LiveCycleTests, self).setUp()
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
        self.dialog = MagicMock()
        for patcher in (
            patch.object(sudosync_service.xbmcgui, "Dialog", return_value=self.dialog),
            patch.object(sudosync_service, "update_local_status_from_v2"),
            patch.object(sudosync_service, "_is_playing_video", return_value=False),
            patch.object(sudosync_service, "save_log_copy", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_scan_starting_during_the_cycle_postpones_it_without_an_error(self):
        monitor = FakeMonitor()
        runtime = MagicMock()

        def sync_cycle(abort_requested=None, **kwargs):
            monitor.scanning = True  # Kodi starts its startup scan
            return {"status": "aborted" if abort_requested() else "no_changes", "applied_count": 0}

        runtime.sync_cycle.side_effect = sync_cycle
        result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        self.assertTrue(result["library_changing"])
        self.assertNotIn("error", result)
        self.dialog.textviewer.assert_not_called()
        self.assertEqual(sudosync_service._live_retry_delay_seconds(result, 300),
                         sudosync_service.LIVE_BUSY_RETRY_SECONDS)

    def test_closing_kodi_during_the_cycle_is_not_an_error(self):
        monitor = FakeMonitor()
        monitor.aborted = True
        runtime = MagicMock()
        runtime.sync_cycle.return_value = {"status": "aborted", "applied_count": 0}
        result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        self.assertNotIn("error", result)
        sudosync_service.update_local_status_from_v2.assert_not_called()

    def test_an_aborted_cycle_without_a_reason_is_still_reported(self):
        runtime = MagicMock()
        runtime.sync_cycle.return_value = {"status": "aborted", "applied_count": 0}
        result = sudosync_service._run_live_cycle(runtime, FakeMonitor(), False, False, True)
        self.assertEqual(result.get("error"), "unexpected_v2_status")

    def test_the_nfo_cache_is_dropped_once_after_a_scan(self):
        monitor = FakeMonitor()
        monitor.scan_refresh_pending = True
        monitor.scan_nfo_stale = True
        runtime = MagicMock()
        runtime.sync_cycle.return_value = {"status": "no_changes", "applied_count": 0}
        for _ in range(3):
            sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertEqual(runtime.sync_engine._kodi.clear_nfo_cache.call_count, 1)
        self.assertFalse(monitor.scan_nfo_stale)


if __name__ == "__main__":
    unittest.main()
