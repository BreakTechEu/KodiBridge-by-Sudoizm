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


def make_field_conflict(field, values, kodi_id=99, lc=0, local=None):
    conflict = make_conflict(kodi_id=kodi_id, lc=lc, values=values)
    conflict["field"] = field
    conflict["local_value"] = local
    return conflict


class FakeControl(object):
    _next_id = [100]

    def __init__(self, *args, **kwargs):
        FakeControl._next_id[0] += 1
        self._id = FakeControl._next_id[0]
        self.args = args
        self.kwargs = kwargs
        self.label = args[4] if len(args) > 4 else ""
        self.nav = {}

    def getId(self):
        return self._id

    def setLabel(self, label):
        self.label = label

    def controlUp(self, other):
        self.nav["up"] = other

    def controlDown(self, other):
        self.nav["down"] = other

    def controlLeft(self, other):
        self.nav["left"] = other

    def controlRight(self, other):
        self.nav["right"] = other


class FakeWindowDialog(object):
    def __init__(self):
        self.controls = []
        self.focused = None
        self.closed = False

    def addControl(self, control):
        self.controls.append(control)

    def setFocus(self, control):
        self.focused = control

    def close(self):
        self.closed = True

    def doModal(self):
        pass


class FakeGui(object):
    WindowDialog = FakeWindowDialog
    ControlImage = ControlLabel = ControlButton = FakeControl


class FakeAction(object):
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


THIRD = "33333333-3333-4333-8333-333333333333"


def threads_group():
    # The real case from the 2.2.3 test: Threads (1984), three fields.
    return conflicts_module.group_conflicts([
        make_field_conflict("lastplayed", ((LOCAL, "PC z Windows 11", "2018-11-05 00:00:00"),
                                           (PEER, "Kodi (10.2.0.2)", "2026-09-13 14:30:36")),
                            local="2018-11-05 00:00:00"),
        make_field_conflict("playcount", ((LOCAL, "PC z Windows 11", 1), (PEER, "Kodi (10.2.0.2)", 2)), local=1),
        make_field_conflict("userrating", ((LOCAL, "PC z Windows 11", 7), (PEER, "Kodi (10.2.0.2)", 9)), local=7),
    ])[0]


class ConflictMatrixTests(unittest.TestCase):
    def test_describe_values(self):
        d = conflicts_module.describe_value
        self.assertEqual(d("playcount", 0), "nieobejrzane")
        self.assertEqual(d("playcount", 2), "obejrzane, odtworzeń: 2")
        self.assertEqual(d("userrating", None), "brak oceny")
        self.assertEqual(d("userrating", 8), "ocena 8/10")
        self.assertEqual(d("resume", {"position": 0, "total": 0}), "od początku")
        self.assertEqual(d("resume", {"position": 3725, "total": 7200}), "1:02:05 z 2:00:00")
        self.assertEqual(d("lastplayed", ""), "brak")

    def test_fields_of_one_item_form_one_table(self):
        group = threads_group()
        self.assertEqual(sorted(group["conflicts"]), ["lastplayed", "playcount", "userrating"])
        matrix = conflicts_module.ConflictMatrix(group, LOCAL)
        self.assertEqual(matrix.fields, ["playcount", "lastplayed", "userrating"])
        self.assertEqual([name for _, name in matrix.columns], ["PC z Windows 11 (to Kodi)", "Kodi (10.2.0.2)"])

    def test_defaults_newest_date_and_local_values(self):
        matrix = conflicts_module.ConflictMatrix(threads_group(), LOCAL)
        self.assertEqual(matrix.choices(), {"playcount": 1, "lastplayed": "2026-09-13 14:30:36", "userrating": 7})

    def test_values_can_be_mixed_per_row(self):
        matrix = conflicts_module.ConflictMatrix(threads_group(), LOCAL)
        self.assertTrue(matrix.select(0, 1))  # playcount from the other device
        self.assertEqual(matrix.choices()["playcount"], 2)
        self.assertEqual(matrix.choices()["userrating"], 7)

    def test_device_missing_from_a_field_has_no_cell(self):
        group = conflicts_module.group_conflicts([
            make_field_conflict("playcount", ((LOCAL, "Salon", 1), (PEER, "Sypialnia", 2)), local=1),
            make_field_conflict("userrating", ((PEER, "Sypialnia", 9), (THIRD, "Kuchnia", 5)), local=7),
        ])[0]
        matrix = conflicts_module.ConflictMatrix(group, LOCAL)
        self.assertEqual([n for _, n in matrix.columns], ["Salon (to Kodi)", "Kuchnia", "Sypialnia"])
        row = matrix.fields.index("userrating")
        self.assertFalse(matrix.has_cell(row, 0))
        self.assertFalse(matrix.select(row, 0))
        self.assertEqual(matrix.cell_text(row, 0), "—")
        self.assertIn(matrix.choices()["userrating"], (5, 9))


class ConflictTableDialogTests(unittest.TestCase):
    def _dialog(self):
        cls = conflicts_module.build_dialog_class(FakeGui)
        dialog = cls()
        dialog.setup(conflicts_module.ConflictMatrix(threads_group(), LOCAL), "Threads (1984)", "1/1")
        return dialog

    def test_layout_title_and_selection_marks(self):
        dialog = self._dialog()
        labels = [c.args[4] for c in dialog.controls if len(c.args) > 4 and isinstance(c.args[4], str)]
        self.assertTrue(any("Threads (1984)" in label for label in labels))
        playcount_row = dialog.grid[0]
        self.assertIn("»", playcount_row[0].label)
        self.assertNotIn("»", playcount_row[1].label)
        self.assertIs(dialog.focused, playcount_row[0])

    def test_click_selects_cell_and_save_returns_choices(self):
        dialog = self._dialog()
        dialog.onControl(dialog.grid[0][1])  # mouse click or OK on "2 plays"
        self.assertIn("»", dialog.grid[0][1].label)
        self.assertNotIn("»", dialog.grid[0][0].label)
        dialog.onControl(dialog.save_button)
        self.assertTrue(dialog.closed)
        self.assertEqual(dialog.result["playcount"], 2)

    def test_later_back_and_right_click_return_none(self):
        for close in ("later", 92, 10, 101):
            dialog = self._dialog()
            if close == "later":
                dialog.onControl(dialog.later_button)
            else:
                dialog.onAction(FakeAction(close))
            self.assertTrue(dialog.closed)
            self.assertIsNone(dialog.result)

    def test_remote_navigation_covers_grid_and_buttons(self):
        dialog = self._dialog()
        grid = dialog.grid
        self.assertIs(grid[0][0].nav["right"], grid[0][1])
        self.assertIs(grid[0][1].nav["left"], grid[0][0])
        self.assertIs(grid[0][0].nav["down"], grid[1][0])
        self.assertIs(grid[1][0].nav["up"], grid[0][0])
        self.assertIs(grid[2][1].nav["down"], dialog.save_button)
        self.assertIs(dialog.save_button.nav["right"], dialog.later_button)
        self.assertIs(dialog.later_button.nav["left"], dialog.save_button)
        self.assertIs(dialog.save_button.nav["up"], grid[2][0])

    def test_table_failure_falls_back_to_selection_dialogs(self):
        dialog = MagicMock()
        dialog.select.side_effect = [1, 0, 1]
        with patch.object(conflicts_module, "show_conflict_table", side_effect=RuntimeError("no WindowDialog")), \
                patch.object(conflicts_module.xbmcgui, "Dialog", return_value=dialog):
            choices = conflicts_module.ask_item(threads_group(), LOCAL, "Threads (1984)", "1/1")
        self.assertEqual(choices, {"playcount": 2, "lastplayed": "2018-11-05 00:00:00", "userrating": 9})


class ConflictDialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = MagicMock()
        patcher = patch.object(conflicts_module.xbmcgui, "Dialog", return_value=self.dialog)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(conflicts_module, "item_label", return_value="Film (2020)")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.answers = []
        patcher = patch.object(conflicts_module, "ask_item", side_effect=self._ask)
        self.ask = patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = MagicMock()
        self.runtime.resolve_item_conflicts.return_value = {"status": "resolved"}

    def _ask(self, group, local_client_id, title, counter):
        return self.answers.pop(0) if self.answers else None

    def notification_text(self):
        return self.dialog.notification.call_args[0][1]

    def test_one_question_per_item_and_one_resolution(self):
        self.answers = [{"playcount": 2, "lastplayed": "2026-09-13 14:30:36", "userrating": 9}]
        group = threads_group()
        resolved = conflicts_module.prompt_and_resolve(self.runtime, list(group["conflicts"].values()), set(), LOCAL)
        self.assertEqual(resolved, 1)
        self.assertEqual(self.ask.call_count, 1)
        args, kwargs = self.runtime.resolve_item_conflicts.call_args
        self.assertEqual(args[:2], ("movie", 99))
        self.assertEqual(args[2], {"playcount": 2, "lastplayed": "2026-09-13 14:30:36", "userrating": 9})
        self.assertEqual(kwargs["expected_identity"], ["imdb:tt1234567"])
        self.assertEqual(set(kwargs["expected_versions"]), {"playcount", "lastplayed", "userrating"})
        self.assertEqual(self.notification_text(), "Zapisano wybór: 1.")

    def test_progress_is_announced_before_saving(self):
        self.answers = [{"playcount": 1}]
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], set(), LOCAL)
        first_notification = self.dialog.notification.call_args_list[0][0][1]
        self.assertIn("Zapisuję wybór", first_notification)

    def test_later_postpones_remaining_items_for_the_session(self):
        dismissed = set()
        conflicts = [make_conflict(kodi_id=99), make_conflict(kodi_id=100)]
        self.assertEqual(conflicts_module.prompt_and_resolve(self.runtime, conflicts, dismissed, LOCAL), 0)
        self.assertEqual(self.ask.call_count, 1)
        self.assertEqual(len(dismissed), 2)
        conflicts_module.prompt_and_resolve(self.runtime, conflicts, dismissed, LOCAL)
        self.assertEqual(self.ask.call_count, 1)
        # A conflict that changed (new version) is asked again.
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict(kodi_id=99, lc=5)], dismissed, LOCAL)
        self.assertEqual(self.ask.call_count, 2)
        self.runtime.resolve_item_conflicts.assert_not_called()

    def test_no_dialog_while_busy(self):
        conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], set(), LOCAL, is_busy=lambda: True)
        self.ask.assert_not_called()

    def test_busy_state_is_checked_before_every_item(self):
        busy = [False]

        def answer(*args):
            busy[0] = True
            return {"playcount": 1}

        self.ask.side_effect = answer
        conflicts = [make_conflict(kodi_id=99), make_conflict(kodi_id=100)]
        conflicts_module.prompt_and_resolve(self.runtime, conflicts, set(), LOCAL, is_busy=lambda: busy[0])
        self.assertEqual(self.ask.call_count, 1)

    def test_lease_is_refreshed_while_dialog_is_open_and_before_writing(self):
        import time as _time
        calls = []

        def keep_alive():
            calls.append(1)
            return True

        def slow_answer(*args):
            _time.sleep(0.35)
            return {"playcount": 1}

        self.ask.side_effect = slow_answer
        conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict()], set(), LOCAL, keep_alive=keep_alive, keep_alive_interval=0.05,
        )
        self.assertGreaterEqual(len(calls), 3)
        self.runtime.resolve_item_conflicts.assert_called_once()

    def test_lost_lease_prevents_writing(self):
        self.answers = [{"playcount": 1}]
        resolved = conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict()], set(), LOCAL, keep_alive=lambda: False,
        )
        self.assertEqual(resolved, 0)
        self.runtime.resolve_item_conflicts.assert_not_called()

    def test_failed_resolution_is_not_postponed_until_retries_run_out(self):
        self.ask.side_effect = lambda *a: {"playcount": 1}
        self.runtime.resolve_item_conflicts.side_effect = RuntimeError("NAS unavailable")
        dismissed, failures = set(), {}
        with patch.object(conflicts_module, "_record_error") as record_error:
            for _ in range(conflicts_module.MAX_RESOLVE_FAILURES - 1):
                conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL, failures=failures)
                self.assertEqual(dismissed, set())
            conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL, failures=failures)
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(record_error.call_count, conflicts_module.MAX_RESOLVE_FAILURES)
        self.assertIn("Błąd zapisu: 1", self.notification_text())

    def test_changed_conflict_is_reported_and_offered_again(self):
        self.answers = [{"playcount": 1}]
        self.runtime.resolve_item_conflicts.return_value = {"status": "changed"}
        dismissed = set()
        self.assertEqual(conflicts_module.prompt_and_resolve(self.runtime, [make_conflict()], dismissed, LOCAL), 0)
        self.assertEqual(dismissed, set())
        self.assertIn("Nieaktualne", self.notification_text())

    def test_summary_counts_saved_and_outdated_choices(self):
        self.ask.side_effect = lambda *a: {"playcount": 1}
        self.runtime.resolve_item_conflicts.side_effect = [{"status": "not_found"}, {"status": "resolved"}]
        conflicts_module.prompt_and_resolve(
            self.runtime, [make_conflict(kodi_id=99), make_conflict(kodi_id=100)], set(), LOCAL,
        )
        self.assertEqual(self.notification_text(), "Zapisano wybór: 1; nieaktualne, bez zapisu: 1.")

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
    def _run(self, iterations, playing_until=0, requests=(), nfo_requests=()):
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
        nfo_iter = iter(nfo_requests)
        self.runtime = runtime
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
                patch.object(sudosync_service, "take_nfo_cache_refresh_request",
                             side_effect=lambda: next(nfo_iter, False)), \
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


    def test_nfo_id_change_in_settings_clears_service_cache_and_runs_cycle(self):
        _, live_cycle = self._run(iterations=3, nfo_requests=(False, True))
        self.runtime.sync_engine._kodi.clear_nfo_cache.assert_called_once()
        self.assertGreaterEqual(live_cycle.call_count, 2)


class NfoCacheRefreshRequestTests(unittest.TestCase):
    def test_request_is_taken_once(self):
        from resources.lib import sudosync_core
        window = MagicMock()
        props = {}
        window.setProperty.side_effect = lambda k, v: props.__setitem__(k, v)
        window.getProperty.side_effect = lambda k: props.get(k, "")
        window.clearProperty.side_effect = lambda k: props.pop(k, None)
        with patch.object(sudosync_core.xbmcgui, "Window", return_value=window):
            self.assertFalse(sudosync_core.take_nfo_cache_refresh_request())
            sudosync_core.request_nfo_cache_refresh()
            self.assertTrue(sudosync_core.take_nfo_cache_refresh_request())
            self.assertFalse(sudosync_core.take_nfo_cache_refresh_request())


def sudosync_service_conflict_key(conflict):
    return conflicts_module.conflict_key(conflict)



class ChoiceOptionsTests(unittest.TestCase):
    """Fallback single-field list used when the conflict table cannot be shown."""

    def test_options_mark_local_device_and_merge_equal_values(self):
        conflict = make_conflict(values=((LOCAL, "Salon", 2), (PEER, "Sypialnia", 1), ("3" * 36, "Kuchnia", 1)))
        labels, values = conflicts_module.choice_options(conflict, LOCAL)
        self.assertEqual(labels, [
            "Salon (to Kodi): obejrzane, odtworzeń: 2",
            "Sypialnia, Kuchnia: obejrzane, odtworzeń: 1",
        ])
        self.assertEqual(values, [2, 1])

if __name__ == "__main__":
    unittest.main()
