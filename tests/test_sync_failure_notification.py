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
from resources.lib.sudosync_v2.snapshot import (  # noqa: E402
    CAUSE_SHARED_EXTERNAL_WITHOUT_ID, SnapshotBuildError, build_snapshot)
import service as sudosync_service  # noqa: E402

CLIENT = "55555555-5555-4555-8555-555555555555"


def _wrapped(exc):
    """Wrap like SyncEngine does: a new error raised inside the except block."""
    try:
        raise exc
    except Exception as inner:
        try:
            raise SyncEngineError("Cannot safely build synchronization preview: {}".format(inner))
        except SyncEngineError as outer:
            return outer


class LogPathTests(unittest.TestCase):
    def test_windows_folder(self):
        with patch.object(sudosync_service.xbmcvfs, "translatePath",
                          return_value="C:\\Users\\kodi\\AppData\\Roaming\\Kodi\\"):
            self.assertEqual(sudosync_service.kodi_log_path(),
                             "C:\\Users\\kodi\\AppData\\Roaming\\Kodi\\kodi.log")

    def test_android_folder(self):
        with patch.object(sudosync_service.xbmcvfs, "translatePath",
                          return_value="/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/temp/"):
            self.assertEqual(sudosync_service.kodi_log_path(),
                             "/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/temp/kodi.log")

    def test_unknown_folder(self):
        with patch.object(sudosync_service.xbmcvfs, "translatePath", side_effect=RuntimeError("no")):
            self.assertIsNone(sudosync_service.kodi_log_path())
        with patch.object(sudosync_service.xbmcvfs, "translatePath", return_value=""):
            self.assertIsNone(sudosync_service.kodi_log_path())


class KnownCauseTests(unittest.TestCase):
    def test_shared_external_id_without_kodibridge_id_has_a_cause(self):
        episodes = [
            {"episodeid": 1, "season": 1, "episode": 1, "playcount": 0, "uniqueid": {"tvdb": "5001"}},
            {"episodeid": 2, "season": 1, "episode": 2, "playcount": 0, "uniqueid": {"tvdb": "5001"}},
        ]
        with self.assertRaises(SnapshotBuildError) as caught:
            build_snapshot([], episodes, CLIENT, "Kodi", logical_clock=1)
        self.assertEqual(caught.exception.cause, CAUSE_SHARED_EXTERNAL_WITHOUT_ID)
        self.assertEqual(sudosync_service.sync_failure_cause(_wrapped(caught.exception)),
                         CAUSE_SHARED_EXTERNAL_WITHOUT_ID)

    def test_every_cause_code_has_a_text(self):
        self.assertIn(CAUSE_SHARED_EXTERNAL_WITHOUT_ID, sudosync_service.KNOWN_SYNC_FAILURE_CAUSES)

    def test_unknown_error_has_no_cause(self):
        self.assertIsNone(sudosync_service.sync_failure_cause(_wrapped(SnapshotBuildError("conflict"))))
        self.assertIsNone(sudosync_service.sync_failure_cause(RuntimeError("x")))


class MessageTests(unittest.TestCase):
    def test_message_points_to_log_ai_and_author_with_warning(self):
        text = sudosync_service.sync_failure_message("C:\\Kodi\\kodi.log")
        self.assertIn("C:\\Kodi\\kodi.log", text)
        self.assertIn("SI", text)
        self.assertIn(sudosync_service.AUTHOR_ISSUES_URL, text)
        self.assertIn("publiczne", text)
        self.assertNotIn("Przyczyna:", text)

    def test_known_cause_names_reason_and_repair(self):
        text = sudosync_service.sync_failure_message("C:\\Kodi\\kodi.log", CAUSE_SHARED_EXTERNAL_WITHOUT_ID)
        self.assertIn("Przyczyna:", text)
        self.assertIn("Naprawa:", text)
        self.assertIn("Przypisz KodiBridge ID", text)
        self.assertIn("C:\\Kodi\\kodi.log", text)

    def test_title_marks_an_error(self):
        self.assertTrue(sudosync_service.SYNC_FAILURE_TITLE.startswith("BŁĄD KodiBridge"))

    def test_message_without_known_path(self):
        self.assertIn("kodi.log w folderze danych Kodi", sudosync_service.sync_failure_message(None))


class SyncFailureWindowTests(unittest.TestCase):
    def setUp(self):
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
        self.playing = [False]
        self.dialog = MagicMock()
        for patcher in (
            patch.object(sudosync_service, "_is_playing_video", side_effect=lambda: self.playing[0]),
            patch.object(sudosync_service.xbmcgui, "Dialog", return_value=self.dialog),
            patch.object(sudosync_service, "kodi_log_path", return_value="C:\\Kodi\\kodi.log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_shown_again_when_the_cause_changes(self):
        sudosync_service._report_sync_failure(RuntimeError("a"))
        sudosync_service._report_sync_failure(_wrapped(SnapshotBuildError("dup", cause=CAUSE_SHARED_EXTERNAL_WITHOUT_ID)))
        self.assertEqual(self.dialog.textviewer.call_count, 2)
        self.assertIn("Naprawa:", self.dialog.textviewer.call_args[0][1])

    def test_shown_once_until_a_cycle_succeeds(self):
        sudosync_service._report_sync_failure(RuntimeError("a"))
        sudosync_service._report_sync_failure(RuntimeError("b"))
        self.assertEqual(self.dialog.textviewer.call_count, 1)
        sudosync_service._clear_sync_failure()
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.assertEqual(self.dialog.textviewer.call_count, 2)

    def test_not_during_playback_but_after_it(self):
        self.playing[0] = True
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.dialog.textviewer.assert_not_called()
        self.playing[0] = False
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.assertEqual(self.dialog.textviewer.call_count, 1)

    def test_failed_live_cycle_shows_window(self):
        runtime = MagicMock()
        runtime.sync_cycle.side_effect = RuntimeError("Cannot safely build synchronization preview")
        monitor = MagicMock(scan_refresh_pending=False)
        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "log"):
            result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        self.assertEqual(self.dialog.textviewer.call_count, 1)
        title, message = self.dialog.textviewer.call_args[0]
        self.assertEqual(title, sudosync_service.SYNC_FAILURE_TITLE)
        self.assertIn("C:\\Kodi\\kodi.log", message)


if __name__ == "__main__":
    unittest.main()
