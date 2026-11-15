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
from resources.lib.sudosync_v2.kodi_rpc import LibraryChangedError  # noqa: E402
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
        self.assertTrue(sudosync_service.sync_failure_title().startswith("BŁĄD KodiBridge"))

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
            patch.object(sudosync_service, "save_log_copy", return_value=None),
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
        self.assertEqual(title, sudosync_service.sync_failure_title())
        self.assertIn("C:\\Kodi\\kodi.log", message)


class LibraryChangingTests(unittest.TestCase):
    """A library read during a running scan (e.g. the startup scan) is not an error."""

    def setUp(self):
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
        self.dialog = MagicMock()
        for patcher in (
            patch.object(sudosync_service, "_is_playing_video", return_value=False),
            patch.object(sudosync_service.xbmcgui, "Dialog", return_value=self.dialog),
            patch.object(sudosync_service, "save_log_copy", return_value=None),
            patch.object(sudosync_service, "update_local_status_from_v2"),
            patch.object(sudosync_service, "log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_wrapped_library_change_is_transient(self):
        self.assertTrue(sudosync_service.sync_failure_is_transient(
            _wrapped(LibraryChangedError("VideoLibrary.GetEpisodes total changed during pagination"))))
        self.assertFalse(sudosync_service.sync_failure_is_transient(_wrapped(RuntimeError("x"))))
        self.assertFalse(sudosync_service.sync_failure_is_transient(None))

    def test_live_cycle_retries_without_window(self):
        runtime = MagicMock()
        runtime.sync_cycle.side_effect = _wrapped(LibraryChangedError("total changed during pagination"))
        monitor = MagicMock(scan_refresh_pending=False)
        result = sudosync_service._run_live_cycle(runtime, monitor, False, False, True)
        self.dialog.textviewer.assert_not_called()
        self.assertTrue(result["busy"])
        self.assertNotIn("error", result)
        self.assertEqual(sudosync_service._live_retry_delay_seconds(result, 300),
                         sudosync_service.LIVE_BUSY_RETRY_SECONDS)

    def test_monitor_knows_about_a_scan_started_before_it(self):
        with patch.object(sudosync_service.xbmc, "getCondVisibility", return_value=True):
            self.assertTrue(sudosync_service.SudoSyncMonitor().scanning)
        with patch.object(sudosync_service.xbmc, "getCondVisibility", return_value=False):
            self.assertFalse(sudosync_service.SudoSyncMonitor().scanning)
        with patch.object(sudosync_service.xbmc, "getCondVisibility", side_effect=RuntimeError("no GUI")):
            self.assertFalse(sudosync_service.SudoSyncMonitor().scanning)

    def test_every_cycle_asks_kodi_about_a_running_scan(self):
        monitor = MagicMock(scanning=False)
        with patch.object(sudosync_service.xbmc, "getCondVisibility", return_value=True):
            self.assertTrue(sudosync_service._library_busy(monitor))
        with patch.object(sudosync_service.xbmc, "getCondVisibility", return_value=False):
            self.assertFalse(sudosync_service._library_busy(monitor))
            monitor.scanning = True
            self.assertTrue(sudosync_service._library_busy(monitor))


if __name__ == "__main__":
    unittest.main()


class LogCopyTests(unittest.TestCase):
    """On an error the log is copied to the shared folder (Android hides kodi.log)."""

    BASE = "smb://kodi:secret@nas/video/.KodiBridge/"

    def setUp(self):
        self.files = []
        self.copies = []
        self.deleted = []
        self.copy_result = True
        core = sys.modules["resources.lib.sudosync_core"]
        for patcher in (
            patch.object(sudosync_service, "addon_settings",
                         side_effect=lambda: {"base_path": self.base, "client_name": "Salon Box!"}),
            patch.object(sudosync_service.xbmcvfs, "copy", side_effect=self._copy),
            patch.object(core, "_vfs_mkdirs", return_value=True),
            patch.object(core, "_vfs_listdir", side_effect=lambda path: ([], list(self.files))),
            patch.object(core, "_vfs_delete", side_effect=lambda path: self.deleted.append(path) or True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.base = self.BASE

    def _copy(self, source, target):
        self.copies.append((source, target))
        if self.copy_result:
            self.files.append(target.rsplit("/", 1)[1])
        return self.copy_result

    def test_copy_lands_in_the_logs_folder_and_hides_the_password(self):
        shown = sudosync_service.save_log_copy(now=0)
        self.assertEqual(self.copies[0][0], "special://logpath/kodi.log")
        self.assertTrue(self.copies[0][1].startswith(self.BASE + "logs/kodi-Salon-Box-"))
        self.assertTrue(self.copies[0][1].endswith(".log"))
        self.assertNotIn("secret", shown)
        self.assertTrue(shown.startswith("smb://nas/video/.KodiBridge/logs/kodi-Salon-Box-"))

    def test_only_the_newest_copies_of_this_device_are_kept(self):
        older = ["kodi-Salon-Box-2026010{}-000000.log".format(day) for day in range(1, 10)]
        older += ["kodi-Salon-Box-20260110-000000.log", "kodi-Salon-Box-20260111-000000.log"]
        self.files = older + ["kodi-Other-20250101-000000.log", "notes.txt"]
        sudosync_service.save_log_copy()
        removed = [path.rsplit("/", 1)[1] for path in self.deleted]
        self.assertEqual(removed, older[:2])
        self.assertEqual(len([name for name in self.files if name.startswith("kodi-Salon-Box-")])
                         - len(removed), sudosync_service.LOG_COPIES_KEPT)

    def test_failed_copy_is_reported_and_no_shared_folder_means_no_copy(self):
        self.copy_result = False
        self.assertIs(sudosync_service.save_log_copy(), sudosync_service.LOG_COPY_FAILED)
        self.assertEqual(len(self.copies), 2)  # with and without credentials
        self.base = ""
        self.assertIsNone(sudosync_service.save_log_copy())

    def test_errors_never_escape(self):
        with patch.object(sudosync_service, "addon_settings", side_effect=RuntimeError("x")):
            self.assertIs(sudosync_service.save_log_copy(), sudosync_service.LOG_COPY_FAILED)

    def test_message_names_the_copy_or_its_failure(self):
        text = sudosync_service.sync_failure_message("C:\\Kodi\\kodi.log", None, "smb://nas/x/kodi-A.log")
        self.assertIn("smb://nas/x/kodi-A.log", text)
        self.assertIn("NAS", text)
        failed = sudosync_service.sync_failure_message(None, None, sudosync_service.LOG_COPY_FAILED)
        self.assertIn("Nie udało się zapisać kopii dziennika", failed)
        self.assertNotIn("NAS:", sudosync_service.sync_failure_message(None))

    def test_window_shows_the_copy(self):
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
        dialog = MagicMock()
        with patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service.xbmcgui, "Dialog", return_value=dialog), \
                patch.object(sudosync_service, "kodi_log_path", return_value=None):
            sudosync_service._report_sync_failure(RuntimeError("a"))
            sudosync_service._report_sync_failure(RuntimeError("b"))
        self.assertEqual(len(self.copies), 1)  # once per shown window
        self.assertIn("smb://nas/video/.KodiBridge/logs/", dialog.textviewer.call_args[0][1])
        sudosync_service._SYNC_FAILURE_NOTIFIED["key"] = None
