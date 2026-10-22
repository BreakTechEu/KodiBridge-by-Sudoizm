# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import json
import os
import shutil
import sys
import tempfile
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
import resources.lib.sudosync_core as sudosync_core
from tests.test_audit_v2_service_and_rollback import VirtualXbmcVfs


class RichTestVfs(VirtualXbmcVfs):
    def __init__(self):
        super(RichTestVfs, self).__init__()
        self.dirs = set()

    def exists(self, path):
        if not path:
            return False
        norm = self._norm(path)
        return norm in self.files or norm.rstrip("/") in self.dirs

    def mkdirs(self, path):
        norm = self._norm(path).rstrip("/")
        if norm in self.dirs and norm.endswith(".lockdir"):
            return False
        self.dirs.add(norm)
        return True

    def rmdir(self, path):
        norm = self._norm(path).rstrip("/")
        if norm in self.dirs:
            self.dirs.remove(norm)
            return True
        return False

    def copy(self, src, dst):
        s_norm = self._norm(src)
        d_norm = self._norm(dst)
        if s_norm not in self.files:
            return False
        self.files[d_norm] = bytes(self.files[s_norm])
        return True


class TestOnScanFinishedAndSilentSudoSyncId(unittest.TestCase):
    def setUp(self):
        sudosync_core._NFO_ID_CACHE.clear()
        self.temp_dir = tempfile.mkdtemp(prefix="sudosync_test_scan_")
        self.profile_dir = os.path.join(self.temp_dir, "profile")
        os.makedirs(self.profile_dir, exist_ok=True)
        self.nas_dir = "smb://nas/wideo/.SudoSync/"

        self.vfs = RichTestVfs()
        self.vfs.mkdirs(self.nas_dir)
        self.vfs.mkdirs(self.nas_dir + "system")
        self.vfs.mkdirs(self.nas_dir + "clients")
        self.vfs.mkdirs(self.nas_dir + "live")

        cfg_payload = {
            "format": "SudoSync configuration",
            "schema_version": 1,
            "configuration": {"completed": True, "completed_at": "2026-10-22T08:00:00Z"},
            "sudosync_network_id": "MAZURY",
            "sudosync_alias": "Salon",
            "network_settings": {
                "sudosync_id_enabled": True,
                "sudosync_id_prefix": "MAZURY",
                "sudosync_id_last_number": 0,
            },
        }
        self.vfs.files[self.nas_dir + "SudoSync.cfg"] = json.dumps(cfg_payload).encode("utf-8")
        self.vfs.files[self.nas_dir + "system/config.json"] = json.dumps(cfg_payload).encode("utf-8")

    def tearDown(self):
        sudosync_core._NFO_ID_CACHE.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_on_scan_finished_video_sets_debounce_flag_when_enabled(self):
        """onScanFinished('video') sets scan_assign_pending and debounce timestamp when SudoSync ID is enabled."""
        monitor = sudosync_service.SudoSyncMonitor()
        self.assertFalse(monitor.scan_assign_pending)
        self.assertEqual(monitor.scan_finished_at, 0.0)

        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": True}):
            monitor.onScanFinished("video")

        self.assertTrue(monitor.scan_assign_pending)
        self.assertGreater(monitor.scan_finished_at, 0.0)
        self.assertTrue(monitor.dirty)
        self.assertTrue(monitor.scan_refresh_pending)
        self.assertFalse(monitor.scanning)

    def test_02_on_scan_finished_music_or_disabled_does_not_set_flag(self):
        """onScanFinished does not set scan_assign_pending for music library or when feature is disabled."""
        monitor = sudosync_service.SudoSyncMonitor()

        # Call with library='music'
        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": True}):
            monitor.onScanFinished("music")
        self.assertFalse(monitor.scan_assign_pending)

        # Call with library='video' but sudosync_id_enabled is False
        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": False}):
            monitor.onScanFinished("video")
        self.assertFalse(monitor.scan_assign_pending)

    def test_03_on_notification_video_scan_finished_delegates_to_on_scan_finished(self):
        """Notification VideoLibrary.OnScanFinished triggers onScanFinished('video')."""
        monitor = sudosync_service.SudoSyncMonitor()
        with patch.object(monitor, "onScanFinished") as mock_scan_finished:
            monitor.onNotification(None, "VideoLibrary.OnScanFinished", None)
            mock_scan_finished.assert_called_once_with("video")

    def test_04_service_loop_triggers_assign_ids_after_debounce_and_publishes_snapshot(self):
        """Main service loop waits for debounce, executes assign_sudosync_ids(silent=True), and publishes v2 snapshot."""
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.scan_assign_pending = True
        monitor.scan_finished_at = 1000.0
        monitor.scanning = False

        # Controlled loop termination
        loop_iterations = [0]
        def fake_abort_requested():
            return loop_iterations[0] >= 1

        def fake_wait_for_abort(timeout=0):
            loop_iterations[0] += 1
            return True

        monitor.abortRequested = fake_abort_requested
        monitor.waitForAbort = fake_wait_for_abort

        mock_v2_runtime = MagicMock()
        mock_v2_runtime.publish_local_snapshot.return_value = {"status": "published"}

        assign_mock = MagicMock(return_value={"movies": 1, "episodes": 0, "last_number": 105})
        collect_live_mock = MagicMock()

        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=mock_v2_runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "TestKodi",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                    "sudosync_id_enabled": True,
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="client-123"), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", return_value={"busy": False, "applied_items": 0}), \
                patch.object(sudosync_service, "assign_sudosync_ids", assign_mock), \
                patch.object(sudosync_service, "collect_live_snapshot", collect_live_mock), \
                patch.object(sudosync_service.time, "time", return_value=1004.0):
            sudosync_service._run_service()

        # Verify assign_sudosync_ids was invoked silently in background
        assign_mock.assert_called_once()
        _, kwargs = assign_mock.call_args
        self.assertTrue(kwargs.get("silent"))
        self.assertTrue(kwargs.get("auto_mode"))
        self.assertFalse(kwargs.get("show_notification"))

        # Verify v2 snapshot rebuild & publication was immediately triggered
        mock_v2_runtime.publish_local_snapshot.assert_called_once()
        _, pub_kwargs = mock_v2_runtime.publish_local_snapshot.call_args
        self.assertFalse(pub_kwargs.get("suppress_local_changes"))

        # Verify v1 snapshot was also refreshed
        collect_live_mock.assert_called_once()

        # Verify flag was reset
        self.assertFalse(monitor.scan_assign_pending)

    def test_05_service_loop_does_not_trigger_while_playing_or_within_debounce(self):
        """Service loop does not trigger ID assignment if debounce has not elapsed or video is playing."""
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.scan_assign_pending = True
        monitor.scan_finished_at = 1000.0
        monitor.scanning = False

        assign_mock = MagicMock()
        loop_iterations = [0]
        def fake_abort_requested():
            return loop_iterations[0] >= 1

        def fake_wait_for_abort(timeout=0):
            loop_iterations[0] += 1
            return True

        monitor.abortRequested = fake_abort_requested
        monitor.waitForAbort = fake_wait_for_abort

        # 1. Within debounce window: t = 1001.0 (< 3s after 1000.0)
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=MagicMock()), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "TestKodi",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                    "sudosync_id_enabled": True,
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="client-123"), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", return_value={"busy": False}), \
                patch.object(sudosync_service, "assign_sudosync_ids", assign_mock), \
                patch.object(sudosync_service.time, "time", return_value=1001.0):
            sudosync_service._run_service()

        assign_mock.assert_not_called()
        self.assertTrue(monitor.scan_assign_pending)

        # 2. Debounce elapsed (t = 1005.0) but video is currently playing
        loop_iterations[0] = 0
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=MagicMock()), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "TestKodi",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                    "sudosync_id_enabled": True,
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="client-123"), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=True), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", return_value={"busy": False}), \
                patch.object(sudosync_service, "assign_sudosync_ids", assign_mock), \
                patch.object(sudosync_service.time, "time", return_value=1005.0):
            sudosync_service._run_service()

        assign_mock.assert_not_called()
        self.assertTrue(monitor.scan_assign_pending)

    def test_06_assign_sudosync_ids_silent_mode_never_calls_any_dialog_method(self):
        """assign_sudosync_ids(..., silent=True) never calls xbmcgui.Dialog methods and writes manifest."""
        import xbmcgui
        dialog_mock = MagicMock()
        xbmcgui.Dialog = MagicMock(return_value=dialog_mock)

        # Setup movie file and NFO in virtual VFS
        movie_path = "smb://nas/wideo/Matrix.mkv"
        nfo_path = "smb://nas/wideo/Matrix.nfo"
        self.vfs.files[self.vfs._norm(movie_path)] = b"fake-movie-data"
        self.vfs.files[self.vfs._norm(nfo_path)] = b"<movie><title>Matrix</title></movie>"

        sample_movie = {
            "title": "Matrix",
            "file": movie_path,
            "ids": {},
            "local": {"movieid": 1},
        }

        with patch.object(sudosync_core.xbmcvfs, "exists", side_effect=self.vfs.exists, create=True), \
                patch.object(sudosync_core.xbmcvfs, "delete", side_effect=self.vfs.delete, create=True), \
                patch.object(sudosync_core.xbmcvfs, "rename", side_effect=self.vfs.rename, create=True), \
                patch.object(sudosync_core.xbmcvfs, "File", side_effect=self.vfs.make_file, create=True), \
                patch.object(sudosync_core.xbmcvfs, "copy", side_effect=self.vfs.copy, create=True), \
                patch.object(sudosync_core.xbmcvfs, "mkdirs", side_effect=self.vfs.mkdirs, create=True), \
                patch.object(sudosync_core.xbmcvfs, "rmdir", side_effect=self.vfs.rmdir, create=True), \
                patch.object(sudosync_core, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "Salon",
                    "sudosync_id_enabled": True,
                    "sudosync_id_prefix": "MAZURY",
                }), \
                patch.object(sudosync_core, "_addon_profile_path", return_value=self.profile_dir), \
                patch.object(sudosync_core, "collect_library", return_value=([sample_movie], [])), \
                patch.object(sudosync_core, "rpc"):
            res = sudosync_core.assign_sudosync_ids(
                show_notification=True,
                auto_mode=True,
                silent=True,
                base_path=self.nas_dir,
            )

        # Confirm ID was successfully assigned
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("movies"), 1)
        self.assertEqual(res.get("episodes"), 0)

        # Verify xbmcgui.Dialog was NEVER called for any popup, progress, or toast
        dialog_mock.ok.assert_not_called()
        dialog_mock.notification.assert_not_called()
        dialog_mock.yesno.assert_not_called()

        # Verify assign_ids_manifest.json was written in profile directory
        manifest_path = os.path.join(self.profile_dir, "assign_ids_manifest.json")
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertTrue(manifest.get("silent"))
        self.assertEqual(manifest.get("newly_assigned"), 1)
        self.assertEqual(manifest.get("checked_items"), 1)
        self.assertEqual(len(manifest.get("modified")), 1)
        self.assertTrue(manifest["modified"][0]["sudosync_id"].startswith("MAZURY-"))

    def test_07_assign_sudosync_ids_silent_mode_suppresses_progress_callback(self):
        """assign_sudosync_ids(..., silent=True) never executes progress_callback even if provided."""
        progress_cb = MagicMock()
        movie_path = "smb://nas/wideo/Inception.mkv"
        nfo_path = "smb://nas/wideo/Inception.nfo"
        self.vfs.files[self.vfs._norm(movie_path)] = b"fake-movie-data"
        self.vfs.files[self.vfs._norm(nfo_path)] = b"<movie><title>Inception</title></movie>"

        sample_movie = {
            "title": "Inception",
            "file": movie_path,
            "ids": {},
            "local": {"movieid": 2},
        }

        with patch.object(sudosync_core.xbmcvfs, "exists", side_effect=self.vfs.exists, create=True), \
                patch.object(sudosync_core.xbmcvfs, "delete", side_effect=self.vfs.delete, create=True), \
                patch.object(sudosync_core.xbmcvfs, "rename", side_effect=self.vfs.rename, create=True), \
                patch.object(sudosync_core.xbmcvfs, "File", side_effect=self.vfs.make_file, create=True), \
                patch.object(sudosync_core.xbmcvfs, "copy", side_effect=self.vfs.copy, create=True), \
                patch.object(sudosync_core.xbmcvfs, "mkdirs", side_effect=self.vfs.mkdirs, create=True), \
                patch.object(sudosync_core.xbmcvfs, "rmdir", side_effect=self.vfs.rmdir, create=True), \
                patch.object(sudosync_core, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "Salon",
                    "sudosync_id_enabled": True,
                    "sudosync_id_prefix": "MAZURY",
                }), \
                patch.object(sudosync_core, "_addon_profile_path", return_value=self.profile_dir), \
                patch.object(sudosync_core, "collect_library", return_value=([sample_movie], [])), \
                patch.object(sudosync_core, "rpc"):
            res = sudosync_core.assign_sudosync_ids(
                show_notification=False,
                auto_mode=True,
                silent=True,
                base_path=self.nas_dir,
                progress_callback=progress_cb,
            )

        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("movies"), 1)
        # progress callback MUST NOT be called in silent mode
        progress_cb.assert_not_called()

    def test_08_assign_sudosync_ids_silent_mode_on_lock_collision_returns_zero_and_no_dialog(self):
        """When another process holds the assign_ids lock, silent mode returns 0 without showing dialogs."""
        import xbmcgui
        dialog_mock = MagicMock()
        xbmcgui.Dialog = MagicMock(return_value=dialog_mock)

        # Simulate lock held
        with patch.object(sudosync_core, "_acquire_assign_ids_lock", return_value=False), \
                patch.object(sudosync_core, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "sudosync_id_enabled": True,
                }):
            result = sudosync_core.assign_sudosync_ids(
                show_notification=True,
                auto_mode=True,
                silent=True,
                base_path=self.nas_dir,
            )

        self.assertEqual(result, 0)
        dialog_mock.notification.assert_not_called()
        dialog_mock.ok.assert_not_called()

    def test_09_service_loop_rearms_debounce_when_assign_ids_is_busy(self):
        """When assign_sudosync_ids returns 0 (busy), main loop sets scan_assign_pending = True with backoff."""
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.scan_assign_pending = True
        monitor.scan_finished_at = 1000.0
        monitor.scanning = False

        loop_iterations = [0]
        def fake_abort_requested():
            return loop_iterations[0] >= 1

        def fake_wait_for_abort(timeout=0):
            loop_iterations[0] += 1
            return True

        monitor.abortRequested = fake_abort_requested
        monitor.waitForAbort = fake_wait_for_abort

        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=MagicMock()), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": self.nas_dir,
                    "client_name": "TestKodi",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                    "sudosync_id_enabled": True,
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="client-123"), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", return_value={"busy": False}), \
                patch.object(sudosync_service, "assign_sudosync_ids", return_value=0), \
                patch.object(sudosync_service.time, "time", return_value=1005.0):
            sudosync_service._run_service()

        # The loop must have re-armed the pending flag with 10s backoff (1005 + 10 = 1015)
        self.assertTrue(monitor.scan_assign_pending)
        self.assertEqual(monitor.scan_finished_at, 1015.0)


if __name__ == "__main__":
    unittest.main()
