# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Replacing system/config.json on a Windows SMB share while it is being read."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib import sudosync_core  # noqa: E402
import service as sudosync_service  # noqa: E402

REAL_REPLACE = os.replace


def busy(times):
    """os.replace that is refused ``times`` times, like a file held open by a reader."""
    calls = []

    def replace(src, dst):
        calls.append((src, dst))
        if len(calls) <= times:
            raise PermissionError(13, "[WinError 5] Access is denied", dst)
        return REAL_REPLACE(src, dst)

    return replace, calls


class UncReplaceRetryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kb_unc_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.target = os.path.join(self.dir, "config.json")
        with open(self.target, "w", encoding="utf-8") as f:
            json.dump({"sudosync_id_last_number": 1}, f)

    def _write(self, replace):
        with patch.object(sudosync_core, "_core_win_unc", return_value=self.target), \
                patch.object(sudosync_core.os, "replace", side_effect=replace), \
                patch.object(sudosync_core.xbmc, "sleep"):
            sudosync_core._write_vfs_json("smb://nas/Wideo/.SudoSync/system/config.json",
                                          {"sudosync_id_last_number": 2})

    def test_busy_target_is_replaced_after_retries(self):
        replace, calls = busy(3)
        self._write(replace)
        self.assertEqual(len(calls), 4)
        with open(self.target, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["sudosync_id_last_number"], 2)

    def test_permanently_refused_replace_still_raises(self):
        replace, calls = busy(1000)
        with self.assertRaises(PermissionError):
            self._write(replace)
        self.assertEqual(len(calls), sudosync_core.UNC_REPLACE_ATTEMPTS)
        with open(self.target, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["sudosync_id_last_number"], 1)


class SharedConfigReadFrequencyTests(unittest.TestCase):
    def test_loop_reads_shared_config_at_most_every_30_seconds(self):
        clock = [1000.0]
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.aborted = False
        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            clock[0] += 3.0
            if iteration[0] >= 21:  # 63 s of service loop
                monitor.aborted = True
                return True
            return False

        monitor.abortRequested = lambda: monitor.aborted
        monitor.waitForAbort = fake_wait_for_abort
        runtime = MagicMock()
        runtime.recover.return_value = {"status": "ready", "recovery": {"status": "clear"}}
        read = MagicMock(return_value={"initialization": {"completed": True}})
        settings = {"base_path": "smb://nas/sudosync", "client_name": "Salon", "interval_seconds": 300,
                    "live_poll_seconds": 60, "live_sync_enabled": True, "check_updates": False}
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value=settings), \
                patch.object(sudosync_service, "get_or_create_client_id",
                             return_value="11111111-1111-4111-8111-111111111111"), \
                patch.object(sudosync_service, "read_shared_config", read), \
                patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=True), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle",
                             MagicMock(return_value={"busy": False, "applied_items": 0})), \
                patch.object(sudosync_service.time, "time", side_effect=lambda: clock[0]):
            sudosync_service._run_service()
        # Startup read + loop reads at 0 s, 30 s and 60 s (was: one read every 2-3 s, i.e. 22).
        self.assertEqual(read.call_count, 4)


if __name__ == "__main__":
    unittest.main()
