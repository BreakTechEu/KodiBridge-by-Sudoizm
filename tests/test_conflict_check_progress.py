# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Progress shown after "Resolve conflicts" and NFO re-read only after real changes."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import default as default_module  # noqa: E402
import service as sudosync_service  # noqa: E402
from resources.lib import sudosync_conflicts as conflicts_module  # noqa: E402
from resources.lib import sudosync_core  # noqa: E402

LOCAL = "11111111-1111-4111-8111-111111111111"


class ProgressObjectTests(unittest.TestCase):
    def test_start_update_finish(self):
        gui = MagicMock()
        progress = conflicts_module.ConflictCheckProgress(gui)
        progress.start(1000.0)
        bg = gui.DialogProgressBG.return_value
        bg.create.assert_called_once()
        self.assertIn("kilka minut", bg.create.call_args[0][1])
        progress.update("Czekam", now=1000.0 + 125)
        self.assertIn("(trwa 2 min)", bg.update.call_args[0][2])
        self.assertFalse(progress.timed_out(1000.0 + 60))
        self.assertTrue(progress.timed_out(1000.0 + conflicts_module.CONFLICT_CHECK_TIMEOUT_SECONDS))
        progress.finish("Brak konfliktów do rozwiązania.")
        bg.close.assert_called_once()
        self.assertEqual(gui.Dialog.return_value.notification.call_args[0][1], "Brak konfliktów do rozwiązania.")
        self.assertFalse(progress.active)


class ServiceProgressTests(unittest.TestCase):
    def _run(self, cycle_results, iterations, request_at=1):
        clock = [1000.0]
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.aborted = False
        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            clock[0] += 61.0  # every iteration a poll is due
            if iteration[0] >= iterations:
                monitor.aborted = True
                return True
            return False

        monitor.abortRequested = lambda: monitor.aborted
        monitor.waitForAbort = fake_wait_for_abort
        runtime = MagicMock()
        runtime.client_id = LOCAL
        runtime.recover.return_value = {"status": "ready", "recovery": {"status": "clear"}}
        results = iter(cycle_results)
        live_cycle = MagicMock(side_effect=lambda **kw: next(results, {"busy": False, "v2": {"status": "no_changes"}}))
        progress = MagicMock()
        progress.active = False
        progress.timed_out.return_value = False
        settings = {"base_path": "smb://nas/sudosync", "client_name": "Salon", "interval_seconds": 300,
                    "live_poll_seconds": 60, "live_sync_enabled": True, "check_updates": False}
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value=settings), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value=LOCAL), \
                patch.object(sudosync_service, "read_shared_config",
                             return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", live_cycle), \
                patch.object(sudosync_service, "prompt_and_resolve", return_value=0), \
                patch.object(sudosync_service, "take_resolution_request",
                             side_effect=lambda: iteration[0] == request_at - 1 and not progress.start.called), \
                patch.object(sudosync_service, "take_nfo_cache_refresh_request", return_value=False), \
                patch.object(sudosync_service, "ConflictCheckProgress", return_value=progress), \
                patch.object(sudosync_service.time, "time", side_effect=lambda: clock[0]):
            sudosync_service._run_service()
        return progress

    def test_progress_shown_until_check_without_conflicts_ends(self):
        # Startup cycle consumes the first result.
        progress = self._run([{"busy": False, "v2": {"status": "no_changes"}}] * 3, iterations=3)
        progress.start.assert_called_once()
        progress.finish.assert_called_once_with("Brak konfliktów do rozwiązania.")

    def test_error_keeps_progress_and_retries(self):
        progress = self._run([
            {"busy": False, "v2": {"status": "no_changes"}},          # startup
            {"busy": True, "error": "NAS unavailable"},               # check fails
            {"busy": False, "v2": {"status": "no_changes"}},          # retry succeeds
        ], iterations=8)
        self.assertIn("NAS unavailable", progress.update.call_args_list[0][0][0])
        progress.finish.assert_called_once_with("Brak konfliktów do rozwiązania.")

    def test_deferred_cycle_does_not_claim_no_conflicts(self):
        progress = self._run([
            {"busy": False, "v2": {"status": "no_changes"}},
            {"busy": False, "v2": {"status": "deferred"}},
        ], iterations=1)
        progress.finish.assert_not_called()
        self.assertIn("Czekam", progress.update.call_args[0][0])

    def test_conflicts_found_close_progress_and_open_dialog(self):
        conflict = {"type": "movie", "kodi_id": 1, "identity": ["imdb:tt1"], "field": "playcount",
                    "version": {"lc": 0, "seq": 0}, "values": [], "local_value": 1}
        progress = self._run([
            {"busy": False, "v2": {"status": "no_changes"}},
            {"busy": False, "v2": {"status": "no_changes", "preview": {"plan": {"conflicts": [conflict]}}}},
        ], iterations=2)
        progress.finish.assert_called_once_with()


class NfoRefreshOnlyAfterChangesTests(unittest.TestCase):
    def _rollback(self, res):
        default_module._NFO_CHANGES["count"] = 0
        dialog = MagicMock()
        dialog.yesno.return_value = True
        with patch.object(sys, "argv", ["default.py", "rollbackids"]), \
                patch.object(default_module.xbmcgui, "Dialog", return_value=dialog), \
                patch.object(sudosync_core, "rollback_sudosync_ids", return_value=res), \
                patch.object(sudosync_core, "request_nfo_cache_refresh") as request:
            default_module.main()
        return request

    def test_no_refresh_when_nothing_changed(self):
        request = self._rollback({"rolled_back": 0, "restored": 0, "deleted": 0, "errors": 0})
        request.assert_not_called()

    def test_refresh_after_real_changes(self):
        request = self._rollback({"rolled_back": 2, "restored": 2, "deleted": 0, "errors": 0})
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
