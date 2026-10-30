# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Watched-status/rating changes are published immediately during playback."""
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

SETTINGS = {
    "base_path": "smb://nas/sudosync",
    "client_name": "Salon",
    "interval_seconds": 300,
    "live_poll_seconds": 60,
    "live_sync_enabled": True,
    "check_updates": False,
}


class PlaybackUserUpdatePublishTests(unittest.TestCase):
    def _run(self, events, iterations, publish_side_effect=None):
        """Run the service loop while a video plays.

        events maps a loop iteration number (1-based) to notifications that
        arrive just before it. The clock advances 3 s per iteration.
        """
        clock = [1000.0]
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.aborted = False
        iteration = [0]

        def fire(n):
            for method in events.get(n, ()):
                monitor.onNotification("xbmc", method, "{}")

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            clock[0] += 3.0
            if iteration[0] >= iterations:
                monitor.aborted = True
                return True
            fire(iteration[0] + 1)
            return False

        monitor.abortRequested = lambda: monitor.aborted
        monitor.waitForAbort = fake_wait_for_abort
        runtime = MagicMock()
        runtime.recover.return_value = {"status": "ready", "recovery": {"status": "clear"}}
        runtime.publish_local_snapshot.return_value = {
            "status": "published", "published_snapshot_path": "x", "snapshot": {},
        }
        if publish_side_effect is not None:
            runtime.publish_local_snapshot.side_effect = publish_side_effect
        live_cycle = MagicMock(return_value={"busy": False, "applied_items": 0})
        fire(1)
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value=dict(SETTINGS)), \
                patch.object(sudosync_service, "read_shared_config",
                             return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value="11111111-1111-4111-8111-111111111111"), \
                patch.object(sudosync_service, "_is_playing_video", return_value=True), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", live_cycle), \
                patch.object(sudosync_service.time, "time", side_effect=lambda: clock[0]):
            sudosync_service._run_service()
        return monitor, runtime, live_cycle

    def test_user_update_is_published_without_waiting_for_poll(self):
        # Startup cycle runs at t=1000; the poll would be due only at t=1060.
        monitor, runtime, live_cycle = self._run({2: ["VideoLibrary.OnUpdate"]}, iterations=5)
        self.assertEqual(live_cycle.call_count, 1)  # startup only; nothing applied during playback
        self.assertEqual(runtime.publish_local_snapshot.call_count, 1)
        # Remote changes are still applied by the regular cycle after playback.
        self.assertTrue(monitor.dirty)
        self.assertTrue(monitor.user_update_dirty)

    def test_each_new_user_update_is_published_once(self):
        _, runtime, _ = self._run(
            {2: ["VideoLibrary.OnUpdate"], 6: ["VideoLibrary.OnUpdate"]}, iterations=9
        )
        self.assertEqual(runtime.publish_local_snapshot.call_count, 2)

    def test_no_user_update_waits_for_poll_interval(self):
        _, runtime, _ = self._run({}, iterations=5)
        runtime.publish_local_snapshot.assert_not_called()

    def test_progress_is_still_published_on_poll_interval(self):
        # 22 iterations x 3 s = 66 s > live_poll_seconds.
        _, runtime, _ = self._run({}, iterations=22)
        self.assertEqual(runtime.publish_local_snapshot.call_count, 1)

    def test_player_stop_alone_does_not_trigger_publish_during_playback(self):
        _, runtime, _ = self._run({2: ["Player.OnStop"]}, iterations=5)
        runtime.publish_local_snapshot.assert_not_called()


    def test_failed_publish_is_retried_shortly_and_then_marked_done(self):
        ok = {"status": "published", "published_snapshot_path": "x", "snapshot": {}}
        # 1st attempt fails (NAS hiccup), 2nd succeeds; nothing more afterwards.
        _, runtime, _ = self._run(
            {2: ["VideoLibrary.OnUpdate"]}, iterations=10,
            publish_side_effect=[IOError("NAS unavailable"), ok, ok, ok],
        )
        self.assertEqual(runtime.publish_local_snapshot.call_count, 2)

    def test_deferred_publish_is_retried(self):
        ok = {"status": "published", "published_snapshot_path": "x", "snapshot": {}}
        _, runtime, _ = self._run(
            {2: ["VideoLibrary.OnUpdate"]}, iterations=10,
            publish_side_effect=[{"status": "deferred", "snapshot": None}, ok, ok],
        )
        self.assertEqual(runtime.publish_local_snapshot.call_count, 2)

if __name__ == "__main__":
    unittest.main()
