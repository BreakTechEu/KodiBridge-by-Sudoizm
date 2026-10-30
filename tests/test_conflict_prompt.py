# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Conflict selection dialog and its scheduling in the service loop."""
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
from resources.lib import sudosync_conflicts as conflicts_module  # noqa: E402

LOCAL = "11111111-1111-4111-8111-111111111111"
PEER = "22222222-2222-4222-8222-222222222222"


def make_conflict(kodi_id=99, lc=0, values=((LOCAL, "Salon", 2), (PEER, "Sypialnia", 1))):
    return {
        "type": "movie",
        "kodi_id": kodi_id,
        "identity": ["imdb:tt1234567"],
        "field": "playcount",
        "version": {"lc": lc, "seq": 0},
        "local_value": 2,
        "clients": sorted(v[0] for v in values),
        "values": [{"client_id": c, "client_name": n, "value": v} for c, n, v in values],
    }


class ConflictDialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = MagicMock()
        patcher = patch.object(conflicts_module.xbmcgui, "Dialog", return_value=self.dialog)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(conflicts_module, "item_label", return_value="Film (2020)")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = MagicMock()
        self.runtime.resolve_conflict.return_value = {"status": "resolved"}

    def test_describe_values(self):
        d = conflicts_module.describe_value
        self.assertEqual(d("playcount", 0), "nieobejrzane")
        self.assertEqual(d("playcount", 2), "obejrzane, odtworzeń: 2")
        self.assertEqual(d("userrating", None), "brak oceny")
        self.assertEqual(d("userrating", 8), "ocena 8/10")
        self.assertEqual(d("resume", {"position": 0, "total": 0}), "od początku")
        self.assertEqual(d("resume", {"position": 3725, "total": 7200}), "1:02:05 z 2:00:00")
        self.assertEqual(d("lastplayed", ""), "brak")

    def test_options_mark_local_device_and_merge_equal_values(self):
        conflict = make_conflict(values=((LOCAL, "Salon", 2), (PEER, "Sypialnia", 1), ("3" * 36, "Kuchnia", 1)))
        labels, values = conflicts_module.choice_options(conflict, LOCAL)
        self.assertEqual(labels, [
            "Salon (to Kodi): obejrzane, odtworzeń: 2",
            "Sypialnia, Kuchnia: obejrzane, odtworzeń: 1",
        ])
        self.assertEqual(values, [2, 1])

    def test_selected_value_is_resolved(self):
        self.dialog.select.return_value = 1
        resolved = conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], set(), LOCAL)
        self.assertEqual(resolved, 1)
        self.runtime.resolve_conflict.assert_called_once_with(
            "movie", 99, "playcount", 1, abort_requested=None,
            expected_identity=["imdb:tt1234567"], expected_version={"lc": 0, "seq": 0},
        )
        heading, options = self.dialog.select.call_args[0]
        self.assertIn("Film (2020)", heading)
        self.assertIn("liczba odtworzeń", heading)
        self.assertTrue(options[-1].startswith("Później"))

    def test_later_dismisses_remaining_conflicts_for_the_session(self):
        dismissed = set()
        self.dialog.select.return_value = 2  # "Później"
        conflicts = [make_conflict(kodi_id=99), make_conflict(kodi_id=100)]
        self.assertEqual(conflicts_module.prompt_and_resolve(self.runtime, conflicts, dismissed, LOCAL), 0)
        self.assertEqual(self.dialog.select.call_count, 1)
        self.runtime.resolve_conflict.assert_not_called()
        # Not asked again in this session...
        conflicts_module.prompt_and_resolve(self.runtime, conflicts, dismissed, LOCAL)
        self.assertEqual(self.dialog.select.call_count, 1)
        # ...unless the conflict itself changes (new version).
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict(kodi_id=99, lc=5)], dismissed, LOCAL)
        self.assertEqual(self.dialog.select.call_count, 2)

    def test_closing_dialog_counts_as_later(self):
        dismissed = set()
        self.dialog.select.return_value = -1
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL)
        self.assertEqual(len(dismissed), 1)

    def test_no_dialog_while_video_is_playing(self):
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], set(), LOCAL, is_busy=lambda: True)
        self.dialog.select.assert_not_called()

    def test_busy_state_is_checked_before_every_dialog(self):
        # A library scan starting after the first answer stops further dialogs.
        busy = [False]
        self.dialog.select.side_effect = lambda *a: busy.__setitem__(0, True) or 0
        conflicts = [make_conflict(kodi_id=99), make_conflict(kodi_id=100)]
        conflicts_module.prompt_and_resolve(self.runtime, conflicts, set(), LOCAL, is_busy=lambda: busy[0])
        self.assertEqual(self.dialog.select.call_count, 1)

    def test_lease_is_refreshed_while_dialog_is_open_and_before_writing(self):
        import time as _time
        calls = []

        def keep_alive():
            calls.append(1)
            return True

        def slow_select(*args):
            _time.sleep(0.35)
            return 1

        self.dialog.select.side_effect = slow_select
        conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict()], set(), LOCAL, keep_alive=keep_alive, keep_alive_interval=0.05,
        )
        self.assertGreaterEqual(len(calls), 3)  # background refreshes + one before writing
        self.runtime.resolve_conflict.assert_called_once()

    def test_lost_lease_prevents_writing(self):
        self.dialog.select.return_value = 1
        resolved = conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict()], set(), LOCAL, keep_alive=lambda: False,
        )
        self.assertEqual(resolved, 0)
        self.runtime.resolve_conflict.assert_not_called()

    def test_failed_resolution_is_not_postponed_until_retries_run_out(self):
        self.dialog.select.return_value = 1
        self.runtime.resolve_conflict.side_effect = RuntimeError("NAS unavailable")
        dismissed, failures = set(), {}
        with patch.object(conflicts_module, "_record_error") as record_error:
            for _ in range(conflicts_module.MAX_RESOLVE_FAILURES - 1):
                conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL, failures=failures)
                self.assertEqual(dismissed, set())
            conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL, failures=failures)
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(record_error.call_count, conflicts_module.MAX_RESOLVE_FAILURES)
        message = self.dialog.notification.call_args[0][1]
        self.assertIn("Błąd zapisu: 1", message)

    def test_changed_conflict_is_reported_and_offered_again(self):
        self.dialog.select.return_value = 1
        self.runtime.resolve_conflict.return_value = {"status": "changed"}
        dismissed = set()
        self.assertEqual(conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL), 0)
        self.assertEqual(dismissed, set())
        self.assertIn("Nieaktualne", self.dialog.notification.call_args[0][1])

    def test_summary_counts_saved_and_outdated_choices(self):
        self.dialog.select.return_value = 1
        self.runtime.resolve_conflict.side_effect = [{"status": "not_found"}, {"status": "resolved"}]
        conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict(kodi_id=99), make_conflict(kodi_id=100)], set(), LOCAL,
        )
        self.assertEqual(
            self.dialog.notification.call_args[0][1],
            "Zapisano wybór: 1; nieaktualne, bez zapisu: 1.",
        )

    def test_deferred_resolution_stops_and_asks_again_later(self):
        self.dialog.select.return_value = 0
        self.runtime.resolve_conflict.return_value = {"status": "deferred"}
        dismissed = set()
        conflicts = [make_conflict(kodi_id=99), make_conflict(kodi_id=100)]
        conflicts_module.prompt_and_resolve(self.runtime, conflicts, dismissed, LOCAL)
        self.assertEqual(self.dialog.select.call_count, 1)
        self.assertEqual(dismissed, set())

    def test_resolvable_conflicts_require_local_item(self):
        foreign = make_conflict()
        foreign["kodi_id"] = None
        foreign["identity"] = []
        result = {"preview": {"plan": {"conflicts": [make_conflict(), foreign]}}}
        self.assertEqual(len(conflicts_module.resolvable_conflicts(result)), 1)
        self.assertEqual(conflicts_module.resolvable_conflicts(None), [])
        self.assertEqual(conflicts_module.resolvable_conflicts({"preview": None}), [])


SETTINGS = {
    "base_path": "smb://nas/sudosync",
    "client_name": "Salon",
    "interval_seconds": 300,
    "live_poll_seconds": 60,
    "live_sync_enabled": True,
    "check_updates": False,
}


class ServiceConflictSchedulingTests(unittest.TestCase):
    def _run(self, iterations, playing_until=0, requests=()):
        clock = [1000.0]
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.aborted = False
        iteration = [0]

        def fake_wait_for_abort(timeout=None):
            iteration[0] += 1
            clock[0] += 30.0
            if iteration[0] >= iterations:
                monitor.aborted = True
                return True
            return False

        monitor.abortRequested = lambda: monitor.aborted
        monitor.waitForAbort = fake_wait_for_abort
        runtime = MagicMock()
        runtime.client_id = LOCAL
        runtime.recover.return_value = {"status": "ready", "recovery": {"status": "clear"}}
        cycle_result = {"busy": False, "applied_items": 0,
                        "v2": {"status": "no_changes", "preview": {"plan": {"conflicts": [make_conflict()]}}}}
        live_cycle = MagicMock(return_value=cycle_result)
        prompts = []

        def fake_prompt(rt, items, dismissed, local_id, is_busy=None, abort_requested=None,
                        keep_alive=None, failures=None):
            assert callable(keep_alive) and isinstance(failures, dict)
            prompts.append((iteration[0], [c["kodi_id"] for c in items], local_id))
            for c in items:
                dismissed.add(sudosync_service_conflict_key(c))
            return 0

        request_iter = iter(requests)
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=True), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease"), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=runtime), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=monitor), \
                patch.object(sudosync_service, "addon_settings", return_value=dict(SETTINGS)), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value=LOCAL), \
                patch.object(sudosync_service, "read_shared_config",
                             return_value={"initialization": {"completed": True}}), \
                patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "_is_playing_video", side_effect=lambda: clock[0] < playing_until), \
                patch.object(sudosync_service, "_execute_integrated_live_cycle", live_cycle), \
                patch.object(sudosync_service, "prompt_and_resolve", side_effect=fake_prompt), \
                patch.object(sudosync_service, "take_resolution_request",
                             side_effect=lambda: next(request_iter, False)), \
                patch.object(sudosync_service.time, "time", side_effect=lambda: clock[0]):
            sudosync_service._run_service()
        return prompts, live_cycle

    def test_conflict_found_at_startup_is_asked_in_first_loop_iteration(self):
        prompts, _ = self._run(iterations=4)
        self.assertEqual(prompts[0], (0, [99], LOCAL))

    def test_dialog_waits_until_playback_stops(self):
        prompts, _ = self._run(iterations=6, playing_until=1100.0)
        self.assertTrue(prompts)
        # Clock: 1000 + 30 * iteration; playback ends at 1100 (iteration 4).
        self.assertGreaterEqual(prompts[0][0], 4)

    def test_settings_request_triggers_cycle_and_clears_postponed(self):
        prompts, live_cycle = self._run(iterations=3, requests=(False, True))
        # Startup cycle + immediate cycle requested from settings in iteration 1.
        self.assertGreaterEqual(live_cycle.call_count, 2)
        self.assertGreaterEqual(len(prompts), 2)


def sudosync_service_conflict_key(conflict):
    return conflicts_module.conflict_key(conflict)


if __name__ == "__main__":
    unittest.main()
