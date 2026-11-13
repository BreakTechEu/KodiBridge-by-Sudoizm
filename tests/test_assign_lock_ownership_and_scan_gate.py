# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Ownership of the KodiBridge ID assignment lock, the post-scan gate and the lease keep-alive."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

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
LIB_DIR = os.path.abspath(os.path.join(SERVICE_DIR, "resources", "lib"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import resources.lib.sudosync_bootstrap as sudosync_bootstrap
import resources.lib.sudosync_core as sudosync_core
import service as sudosync_service
from tests.test_audit_v2_service_and_rollback import VirtualXbmcVfs

BASE = "smb://nas/wideo/.KodiBridge/"
NAS_LOCK = BASE + "system/assign_ids.lock"


class _Vfs(VirtualXbmcVfs):
    def File(self, path, mode="r"):
        return self.make_file(path, mode)


class AssignLockOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.vfs = _Vfs()
        self.profile = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.profile, True)
        self.local_lock = os.path.join(self.profile, "assign_ids.lock")
        for patcher in (
            patch.object(sudosync_core, "xbmcvfs", self.vfs),
            patch.object(sudosync_core, "_addon_profile_path", return_value=self.profile),
            patch.object(sudosync_core, "get_or_create_client_id", return_value="this-kodi"),
            patch.object(sudosync_core, "_ASSIGN_IDS_LOCK_HELD", False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _nas_lock(self, client_id, age=0.0):
        sudosync_core._write_vfs_json(
            NAS_LOCK, {"locked": True, "timestamp": time.time() - age, "pid": 1, "client_id": client_id})

    def _local_lock(self, pid, age=0.0):
        with open(self.local_lock, "w") as handle:
            json.dump({"locked": True, "timestamp": time.time() - age, "pid": pid, "client_id": "this-kodi"}, handle)

    def test_failed_acquisition_keeps_the_lock_of_another_kodi(self):
        self._nas_lock("other-kodi")
        self.assertTrue(sudosync_core.is_assign_ids_lock_busy(BASE))
        self.assertFalse(sudosync_core._acquire_assign_ids_lock(base_path=BASE))
        self.assertTrue(self.vfs.exists(NAS_LOCK))
        self.assertFalse(os.path.exists(self.local_lock))
        self.assertFalse(sudosync_core._acquire_assign_ids_lock(base_path=BASE))
        self.assertTrue(self.vfs.exists(NAS_LOCK))

    def test_release_does_not_remove_the_lock_of_another_kodi(self):
        self._nas_lock("other-kodi")
        sudosync_core._release_assign_ids_lock(base_path=BASE)
        self.assertTrue(self.vfs.exists(NAS_LOCK))

    def test_expired_lock_of_another_kodi_is_replaced(self):
        self._nas_lock("other-kodi", age=1801)
        self.assertFalse(sudosync_core.is_assign_ids_lock_busy(BASE))
        self.assertTrue(sudosync_core._acquire_assign_ids_lock(base_path=BASE))
        self.assertEqual(sudosync_core._read_vfs_json(NAS_LOCK)["client_id"], "this-kodi")
        sudosync_core._release_assign_ids_lock(base_path=BASE)
        self.assertFalse(self.vfs.exists(NAS_LOCK))

    def test_lock_left_by_a_killed_kodi_does_not_block(self):
        self._local_lock(pid=os.getpid() + 1, age=600)
        self._nas_lock("this-kodi", age=600)
        self.assertFalse(sudosync_core.is_assign_ids_lock_busy(BASE))
        self.assertTrue(sudosync_core._acquire_assign_ids_lock(base_path=BASE))
        sudosync_core._release_assign_ids_lock(base_path=BASE)
        self.assertFalse(os.path.exists(self.local_lock))
        self.assertFalse(self.vfs.exists(NAS_LOCK))

    def test_lock_of_this_running_kodi_blocks(self):
        self._local_lock(pid=os.getpid(), age=600)
        self.assertTrue(sudosync_core.is_assign_ids_lock_busy(BASE))
        self.assertFalse(sudosync_core._acquire_assign_ids_lock(base_path=BASE))
        self.assertTrue(os.path.exists(self.local_lock))

    def test_lock_without_pid_expires_only_by_age(self):
        with open(self.local_lock, "w") as handle:
            json.dump({"locked": True, "timestamp": time.time() - 600}, handle)
        self.assertTrue(sudosync_core.is_assign_ids_lock_busy(BASE))
        with open(self.local_lock, "w") as handle:
            json.dump({"locked": True, "timestamp": time.time() - 1801}, handle)
        self.assertFalse(sudosync_core.is_assign_ids_lock_busy(BASE))


class FileHandleTest(unittest.TestCase):
    def test_read_vfs_text_closes_the_file_when_reading_fails(self):
        handle = MagicMock()
        handle.readBytes.side_effect = RuntimeError("read failed")
        vfs = MagicMock()
        vfs.File.return_value = handle
        with patch.object(sudosync_core, "xbmcvfs", vfs), \
                patch.object(sudosync_core, "_core_win_unc", return_value=None):
            self.assertEqual(sudosync_core._read_vfs_text("smb://nas/wideo/a.nfo"), "")
        handle.close.assert_called_once()

    def test_rw_test_closes_the_file_when_writing_fails(self):
        handle = MagicMock()
        handle.write.side_effect = RuntimeError("write failed")
        vfs = MagicMock()
        vfs.File.return_value = handle
        with patch.object(sudosync_bootstrap, "xbmcvfs", vfs), \
                patch.object(sudosync_bootstrap, "_b_exists", return_value=True):
            ok = sudosync_bootstrap.test_vfs_rw("smb://nas/wideo/.KodiBridge/")[0]
        self.assertFalse(ok)
        handle.close.assert_called_once()


class ScanGateTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(sudosync_service._ASSIGN_AFTER_SCAN, {"pending": False})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _scan(self, id_enabled):
        monitor = sudosync_service.SudoSyncMonitor()
        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": id_enabled}):
            monitor.onNotification("xbmc", "VideoLibrary.OnScanStarted", "{}")
            self.assertTrue(sudosync_service._ASSIGN_AFTER_SCAN["pending"])
            monitor.onNotification("xbmc", "VideoLibrary.OnScanFinished", "{}")
        return monitor

    def test_scan_with_id_option_off_reopens_the_notification_gate(self):
        monitor = self._scan(False)
        self.assertFalse(monitor.scan_assign_pending)
        self.assertFalse(sudosync_service._ASSIGN_AFTER_SCAN["pending"])

    def test_scan_with_id_option_on_keeps_the_gate_until_assignment(self):
        monitor = self._scan(True)
        self.assertTrue(monitor.scan_assign_pending)
        self.assertTrue(sudosync_service._ASSIGN_AFTER_SCAN["pending"])


class AssignAfterScanTest(unittest.TestCase):
    def setUp(self):
        self.dialog = MagicMock()
        self.dialog.iscanceled.return_value = False
        for patcher in (
            patch.object(sudosync_service.xbmcgui, "DialogProgress", return_value=self.dialog),
            patch.object(sudosync_service, "is_assign_ids_lock_busy", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _preview(items):
        result = sudosync_core._PreviewResult(items)
        result.collected_items = ([], [])
        return result

    def test_busy_lock_means_no_library_read_and_no_window(self):
        preview = MagicMock()
        assign = MagicMock()
        with patch.object(sudosync_service, "is_assign_ids_lock_busy", return_value=True), \
                patch.object(sudosync_service, "preview_sudosync_ids", preview), \
                patch.object(sudosync_service, "assign_sudosync_ids", assign):
            self.assertEqual(sudosync_service._assign_ids_after_scan(BASE, lambda: False), 0)
        preview.assert_not_called()
        assign.assert_not_called()
        self.dialog.create.assert_not_called()

    def test_lease_is_refreshed_during_a_long_assignment(self):
        clock = {"now": 1000.0}
        keep_alive = MagicMock(return_value=True)
        seen = []

        def assign(**kwargs):
            for _ in range(4):
                clock["now"] += 40
                seen.append(kwargs["is_canceled"]())
            return {"movies": 0, "episodes": 4}

        with patch.object(sudosync_service.time, "time", side_effect=lambda: clock["now"]), \
                patch.object(sudosync_service, "preview_sudosync_ids", return_value=self._preview([{"title": "x"}])), \
                patch.object(sudosync_service, "assign_sudosync_ids", side_effect=assign):
            sudosync_service._assign_ids_after_scan(BASE, lambda: False, keep_alive=keep_alive)
        self.assertEqual(keep_alive.call_count, 2)
        self.assertEqual(seen, [False, False, False, False])

    def test_lost_lease_stops_the_assignment(self):
        clock = {"now": 1000.0}
        seen = []

        def assign(**kwargs):
            for _ in range(3):
                clock["now"] += 61
                seen.append(kwargs["is_canceled"]())
            return {"movies": 0, "episodes": 0, "cancelled": True}

        with patch.object(sudosync_service.time, "time", side_effect=lambda: clock["now"]), \
                patch.object(sudosync_service, "preview_sudosync_ids", return_value=self._preview([{"title": "x"}])), \
                patch.object(sudosync_service, "assign_sudosync_ids", side_effect=assign):
            sudosync_service._assign_ids_after_scan(BASE, lambda: False, keep_alive=lambda: False)
        self.assertEqual(seen, [True, True, True])

    def test_failing_keep_alive_does_not_stop_the_assignment(self):
        clock = {"now": 1000.0}
        seen = []

        def assign(**kwargs):
            clock["now"] += 61
            seen.append(kwargs["is_canceled"]())
            return {"movies": 0, "episodes": 1}

        with patch.object(sudosync_service.time, "time", side_effect=lambda: clock["now"]), \
                patch.object(sudosync_service, "preview_sudosync_ids", return_value=self._preview([{"title": "x"}])), \
                patch.object(sudosync_service, "assign_sudosync_ids", side_effect=assign):
            sudosync_service._assign_ids_after_scan(
                BASE, lambda: False, keep_alive=MagicMock(side_effect=IOError("nas")))
        self.assertEqual(seen, [False])


if __name__ == "__main__":
    unittest.main()
