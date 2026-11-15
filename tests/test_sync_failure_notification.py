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

import service as sudosync_service  # noqa: E402


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


class MessageTests(unittest.TestCase):
    def test_message_points_to_log_ai_and_author(self):
        text = sudosync_service.sync_failure_message("C:\\Kodi\\kodi.log")
        self.assertIn("C:\\Kodi\\kodi.log", text)
        self.assertIn("SI", text)
        self.assertIn(sudosync_service.AUTHOR_ISSUES_URL, text)

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

    def test_shown_once_until_a_cycle_succeeds(self):
        sudosync_service._report_sync_failure(RuntimeError("a"))
        sudosync_service._report_sync_failure(RuntimeError("b"))
        self.assertEqual(self.dialog.ok.call_count, 1)
        sudosync_service._clear_sync_failure()
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.assertEqual(self.dialog.ok.call_count, 2)

    def test_not_during_playback_but_after_it(self):
        self.playing[0] = True
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.dialog.ok.assert_not_called()
        self.playing[0] = False
        sudosync_service._report_sync_failure(RuntimeError("a"))
        self.assertEqual(self.dialog.ok.call_count, 1)

    def test_failed_live_cycle_shows_window(self):
        runtime = MagicMock()
        runtime.sync_cycle.side_effect = RuntimeError("Cannot safely build synchronization preview")
        monitor = MagicMock(scan_refresh_pending=False)
        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "log"):
            result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        self.assertEqual(self.dialog.ok.call_count, 1)
        title, message = self.dialog.ok.call_args[0]
        self.assertEqual(title, sudosync_service.SYNC_FAILURE_TITLE)
        self.assertIn("C:\\Kodi\\kodi.log", message)


if __name__ == "__main__":
    unittest.main()
