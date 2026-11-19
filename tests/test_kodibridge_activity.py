# -*- coding: utf-8 -*-
"""Background progress window of KodiBridge work and the stage times in the log."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib import kodibridge_activity as activity  # noqa: E402
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter, progress_listener  # noqa: E402


class FakeTransport(object):
    def __init__(self, movies=(), episodes=()):
        self.movies = list(movies)
        self.episodes = list(episodes)

    def __call__(self, payload):
        request = json.loads(payload)
        if request["method"] == "VideoLibrary.GetMovies":
            result = {"movies": self.movies, "limits": {"total": len(self.movies)}}
        else:
            result = {"episodes": self.episodes, "limits": {"total": len(self.episodes)}}
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})


class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ActivityTestBase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dialog = MagicMock()
        self.gui = MagicMock()
        self.gui.DialogProgressBG.return_value = self.dialog
        self.xbmc = MagicMock()
        for patcher in (
            patch.object(activity, "xbmcgui", self.gui),
            patch.object(activity, "xbmc", self.xbmc),
            patch.object(activity.time, "monotonic", self.clock),
            patch.dict(activity._LAST_TOTALS, {}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def logged(self):
        return [c.args[0] for c in self.xbmc.log.call_args_list]


class ProgressWindowTests(ActivityTestBase):
    def test_the_window_shows_the_count_and_the_percentage(self):
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(1, 4, "movie")
        self.dialog.create.assert_called_once()
        percent, heading, message = self.dialog.update.call_args.args
        self.assertEqual(percent, 25)
        self.assertEqual(heading, "KodiBridge")
        self.assertIn("1/4", message)
        self.dialog.close.assert_called_once()

    def test_episodes_continue_the_percentage_of_the_movies(self):
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(10, 10, "movie")
            work.report_progress(30, 90, "episode")
        percent, _, message = self.dialog.update.call_args.args
        self.assertEqual(percent, 40)
        self.assertIn("30/90", message)

    def test_the_next_stage_counts_episodes_while_reading_movies(self):
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(10, 10, "movie")
            work.report_progress(90, 90, "episode")
        self.dialog.update.reset_mock()
        with activity.progress(32802, lambda: False) as work:
            work.report_progress(10, 10, "movie")
        self.assertEqual(self.dialog.update.call_args.args[0], 10)

    def test_counts_without_a_media_type(self):
        with activity.progress(32800, lambda: False) as work:
            work.report_progress(50, 200)
        percent, _, message = self.dialog.update.call_args.args
        self.assertEqual(percent, 25)
        self.assertIn("50/200", message)

    def test_the_same_percentage_is_not_redrawn_for_every_item(self):
        with activity.progress(32801, lambda: False) as work:
            for done in range(1, 6):
                work.report_progress(done, 1000, "episode")
        self.assertEqual(self.dialog.update.call_count, 1)

    def test_the_same_percentage_is_redrawn_after_the_interval(self):
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(1, 1000, "episode")
            self.clock.now += activity.UPDATE_INTERVAL
            work.report_progress(2, 1000, "episode")
        self.assertEqual(self.dialog.update.call_count, 2)

    def test_the_window_waits_for_the_delay(self):
        with activity.progress(32801, lambda: False, delay=2) as work:
            work.report_progress(1, 4, "movie")
            self.gui.DialogProgressBG.assert_not_called()
            self.clock.now += 2
            work.report_progress(2, 4, "movie")
        self.gui.DialogProgressBG.assert_called_once()
        self.assertEqual(self.dialog.update.call_args.args[0], 50)

    def test_a_broken_window_never_stops_the_work(self):
        self.dialog.update.side_effect = RuntimeError("gui gone")
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(1, 4, "movie")
            self.assertFalse(work())

    def test_the_check_returns_the_abort_state(self):
        state = {"abort": False}
        with activity.progress(32801, lambda: state["abort"]) as work:
            self.assertFalse(work())
            state["abort"] = True
            self.assertTrue(work())


class StageLogTests(ActivityTestBase):
    def test_the_stage_time_and_counts_are_logged(self):
        with activity.progress(32801, lambda: False) as work:
            work.report_progress(40, 40, "movie")
            work.report_progress(1200, 1200, "episode")
            self.clock.now += 95.25
        line = self.logged()[-1]
        self.assertIn("Stage 'library synchronization' finished after 95.2 s", line)
        self.assertIn("movies 40/40, episodes 1200/1200", line)

    def test_a_stage_ended_by_an_error_is_logged_as_stopped(self):
        with self.assertRaises(ValueError):
            with activity.progress(32802, lambda: False):
                self.clock.now += 3
                raise ValueError("boom")
        self.assertIn("Stage 'library snapshot' stopped after 3.0 s", self.logged()[-1])
        self.dialog.close.assert_called_once()

    def test_a_stage_stopped_by_the_abort_check_is_logged_as_stopped(self):
        state = {"abort": False}
        with activity.progress(32801, lambda: state["abort"]) as work:
            state["abort"] = True
            work()
        self.assertIn("Stage 'library synchronization' stopped after", self.logged()[-1])


class LibraryReaderProgressTests(ActivityTestBase):
    def test_the_nfo_reader_reports_every_item_to_the_activity(self):
        transport = FakeTransport(movies=[
            {"movieid": i, "file": "smb://nas/Films/M{}.mkv".format(i), "uniqueid": {}} for i in range(4)
        ])
        adapter = KodiJsonRpcAdapter(transport, nfo_reader=lambda path, max_bytes: None)
        with activity.progress(32801, lambda: False) as work:
            adapter.get_movies(abort_requested=work)
        self.assertEqual(self.dialog.update.call_args.args[0], 100)
        self.assertIn("movies 4/4", self.logged()[-1])

    def test_other_abort_checks_get_no_progress_report(self):
        self.assertIsNone(progress_listener(MagicMock()))
        self.assertIsNone(progress_listener(lambda: False))
        self.assertIsNone(progress_listener(None))
        work = activity.Activity(32801, lambda: False)
        self.assertEqual(progress_listener(work), work.report_progress)


class PreviewCollectProgressTests(unittest.TestCase):
    def test_the_library_read_of_the_post_scan_check_reports_progress(self):
        import resources.lib.sudosync_core as core
        seen = []

        def collect(first_import=True, progress_callback=None, is_canceled=None):
            progress_callback(1, 2, "")
            progress_callback(2, 2, "")
            return [], []

        with patch.object(core, "addon_settings", return_value={"sudosync_id_enabled": True}), \
                patch.object(core, "collect_library", side_effect=collect):
            core.preview_sudosync_ids(collect_progress_callback=lambda d, t, _n="": seen.append((d, t)))
        self.assertEqual(seen, [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
