# -*- coding: utf-8 -*-
"""A stopped synchronization is shown on screen, not only written to the log."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib.sudosync_v2.engine import SyncEngineError  # noqa: E402
from resources.lib.sudosync_v2.snapshot import SnapshotBuildError, build_snapshot  # noqa: E402
import service as sudosync_service  # noqa: E402

CLIENT = "55555555-5555-4555-8555-555555555555"
OLD = "aaaaaaaa-1111-4111-8111-111111111111"
NEW = "bbbbbbbb-2222-4222-8222-222222222222"


def _episode(sid, tvdb):
    return {"episodeid": 1, "showtitle": "Show", "season": 2, "episode": 5, "playcount": 0,
            "uniqueid": {"sudosync": sid, "tvdb": tvdb}}


def _wrapped(exc):
    """Wrap like SyncEngine does: a new error raised inside the except block."""
    try:
        raise exc
    except Exception as inner:
        try:
            raise SyncEngineError("Cannot safely build synchronization preview: {}".format(inner))
        except SyncEngineError as outer:
            return outer


class SnapshotErrorPlaceTests(unittest.TestCase):
    def test_conflict_names_series_and_season(self):
        previous = build_snapshot([], [_episode(OLD, "5001")], CLIENT, "Kodi", logical_clock=1)
        library = [_episode(NEW, "5001"), dict(_episode(OLD, "5002"), episodeid=2, episode=6)]
        with self.assertRaises(SnapshotBuildError) as caught:
            build_snapshot([], library, CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)
        self.assertEqual(caught.exception.place, "Show, sezon 2")

    def test_place_found_through_engine_wrapper(self):
        wrapped = _wrapped(SnapshotBuildError("conflict", place="Show, sezon 2"))
        self.assertEqual(sudosync_service.sync_failure_place(wrapped), "Show, sezon 2")
        key, message = sudosync_service.sync_failure_summary(wrapped)
        self.assertIn("Show, sezon 2", message)
        self.assertNotIn("smb://", message)

    def test_error_without_place_points_to_the_log(self):
        key, message = sudosync_service.sync_failure_summary(_wrapped(IOError("NAS unreachable")))
        self.assertEqual(key, "other")
        self.assertIn("dzienniku Kodi", message)


class SyncFailureNotificationTests(unittest.TestCase):
    def setUp(self):
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
        self.playing = [False]
        self.dialog = MagicMock()
        for patcher in (
            patch.object(sudosync_service, "_is_playing_video", side_effect=lambda: self.playing[0]),
            patch.object(sudosync_service.xbmcgui, "Dialog", return_value=self.dialog),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def error(self, place="Show, sezon 2"):
        return _wrapped(SnapshotBuildError("conflict", place=place))

    def test_shown_once_per_problem(self):
        sudosync_service._report_sync_failure(self.error())
        sudosync_service._report_sync_failure(self.error())
        self.assertEqual(self.dialog.notification.call_count, 1)
        sudosync_service._report_sync_failure(self.error("Other, sezon 1"))
        self.assertEqual(self.dialog.notification.call_count, 2)

    def test_not_during_playback_but_after_it(self):
        self.playing[0] = True
        sudosync_service._report_sync_failure(self.error())
        self.dialog.notification.assert_not_called()
        self.playing[0] = False
        sudosync_service._report_sync_failure(self.error())
        self.assertEqual(self.dialog.notification.call_count, 1)

    def test_shown_again_after_a_successful_cycle(self):
        sudosync_service._report_sync_failure(self.error())
        sudosync_service._clear_sync_failure()
        sudosync_service._report_sync_failure(self.error())
        self.assertEqual(self.dialog.notification.call_count, 2)

    def test_failed_live_cycle_shows_notification(self):
        runtime = MagicMock()
        runtime.sync_cycle.side_effect = self.error()
        monitor = MagicMock(scan_refresh_pending=False)
        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "log"):
            result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        self.assertEqual(self.dialog.notification.call_count, 1)
        self.assertIn("Show, sezon 2", self.dialog.notification.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
