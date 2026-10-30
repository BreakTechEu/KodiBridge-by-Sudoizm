# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""KodiBridge ID coverage: does every library item have a KodiBridge ID?"""
import logging
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
from resources.lib.sudosync_v2 import snapshot as snapshot_module  # noqa: E402
from resources.lib.sudosync_v2.engine import SyncEngine, SyncEngineError  # noqa: E402
from resources.lib.sudosync_v2.snapshot import kodibridge_id_coverage  # noqa: E402
import default as default_module  # noqa: E402
import service as sudosync_service  # noqa: E402

CLIENT = "12345678-1234-4234-8234-123456789012"


def ep(episodeid, season, episode, file, **uniqueid):
    return {"episodeid": episodeid, "showtitle": "Show", "season": season, "episode": episode,
            "file": file, "uniqueid": dict(uniqueid), "playcount": 0}


def library():
    """Third-Kodi like library: missing IDs, a shared series IMDb, a duplicate and a multi-episode file."""
    movies = [
        {"movieid": 1, "title": "A", "file": "a.mkv", "uniqueid": {"imdb": "tt0000001", "sudosync": "MAZURY-000001"}},
        {"movieid": 2, "title": "B", "file": "b.mkv", "uniqueid": {"imdb": "tt0000002"}},
    ]
    episodes = [
        ep(10, 1, 1, "s01e01.mkv", imdb="tt1245924", sudosync="MAZURY-000100"),
        ep(11, 1, 2, "s01e02.mkv", imdb="tt1245924"),                          # no ID, shares IMDb
        ep(12, 1, 3, "s01e03-04.mkv", tvdb="31", sudosync="MAZURY-000101"),   # multi-episode file:
        ep(13, 1, 4, "s01e03-04.mkv", tvdb="32", sudosync="MAZURY-000101"),   #   same ID, not a duplicate
        ep(14, 2, 1, "copy1.mkv", tvdb="41", sudosync="MAZURY-000200"),       # duplicate ID
        ep(15, 2, 1, "copy2.mkv", tvdb="42", sudosync="MAZURY-000200"),
    ]
    return movies, episodes


class CoverageCountTests(unittest.TestCase):
    def test_counts_missing_duplicates_and_blocking_shared_ids(self):
        movies, episodes = library()
        coverage = kodibridge_id_coverage(movies, episodes)
        self.assertEqual({k: coverage["movie"][k] for k in ("total", "with_id", "missing", "duplicates", "ambiguous")},
                         {"total": 2, "with_id": 1, "missing": 1, "duplicates": 0, "ambiguous": 0})
        self.assertEqual({k: coverage["episode"][k] for k in ("total", "with_id", "missing", "duplicates", "ambiguous")},
                         {"total": 6, "with_id": 5, "missing": 1, "duplicates": 1, "ambiguous": 1})
        self.assertEqual(coverage["episode"]["examples"]["missing"], ["Show S01E02 [s01e02.mkv]"])
        self.assertIn("mazury-000200", coverage["episode"]["examples"]["duplicates"][0])
        self.assertIn("imdb:tt1245924", coverage["episode"]["examples"]["ambiguous"][0])

    def test_complete_library(self):
        coverage = kodibridge_id_coverage(
            [{"movieid": 1, "uniqueid": {"sudosync": "MAZURY-000001"}}], [])
        self.assertEqual(coverage["movie"]["missing"], 0)
        self.assertFalse(snapshot_module.coverage_has_problems(coverage))

    def test_examples_are_limited(self):
        episodes = [ep(i, 1, i, "e{}.mkv".format(i)) for i in range(1, 20)]
        coverage = kodibridge_id_coverage([], episodes)
        self.assertEqual(coverage["episode"]["missing"], 19)
        self.assertEqual(len(coverage["episode"]["examples"]["missing"]), snapshot_module.COVERAGE_MAX_EXAMPLES)

    def test_report_logs_once_per_change(self):
        snapshot_module._LAST_COVERAGE_REPORT.clear()
        movies, episodes = library()
        coverage = kodibridge_id_coverage(movies, episodes)
        with self.assertLogs("resources.lib.sudosync_v2.snapshot", level="WARNING") as cm:
            snapshot_module.report_kodibridge_id_coverage(CLIENT, coverage)
        self.assertIn("KodiBridge ID coverage incomplete", cm.output[0])
        self.assertIn("s01e02.mkv", cm.output[0])
        logger = logging.getLogger("resources.lib.sudosync_v2.snapshot")
        with patch.object(logger, "warning") as warning, patch.object(logger, "info") as info:
            snapshot_module.report_kodibridge_id_coverage(CLIENT, coverage)
        warning.assert_not_called()
        info.assert_not_called()


class FakeKodi(object):
    skipped_nfos = ["broken.nfo"]

    def __init__(self, movies, episodes):
        self.movies, self.episodes = movies, episodes

    def clear_skipped_nfos(self):
        pass

    def is_video_playing(self):
        return False

    def get_movies(self):
        return [dict(m, uniqueid=dict(m["uniqueid"])) for m in self.movies]

    def get_episodes(self):
        return [dict(e, uniqueid=dict(e["uniqueid"])) for e in self.episodes]


class EngineCoverageTests(unittest.TestCase):
    def test_coverage_is_available_when_snapshot_cannot_be_built(self):
        # The duplicate-identity error that stops synchronization on the third Kodi.
        movies, episodes = library()
        engine = SyncEngine(FakeKodi(movies, episodes), CLIENT, "Trzeci", lambda: None, lambda: [])
        with self.assertRaisesRegex(SyncEngineError, "duplicate/overlapping strong identity"):
            engine.preview()
        self.assertEqual(engine.last_coverage["episode"]["missing"], 1)
        self.assertEqual(engine.last_coverage["episode"]["ambiguous"], 1)
        self.assertEqual(engine.last_coverage["unreadable_nfo"], 1)

    def test_publish_path_records_coverage_too(self):
        engine = SyncEngine(FakeKodi([{"movieid": 1, "title": "A", "uniqueid": {"sudosync": "MAZURY-000001"}}], []),
                            CLIENT, "Trzeci", lambda: None, lambda: [])
        engine.publish_local_snapshot()
        self.assertEqual(engine.last_coverage["movie"]["with_id"], 1)


class StatusCoverageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kb_cov_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_recorded_in_status_json_only_when_changed(self):
        movies, episodes = library()
        coverage = kodibridge_id_coverage(movies, episodes)
        writes = []
        real_write = sudosync_core._write_local_json
        real_write(os.path.join(self.dir, "status.json"), {"client_id": CLIENT, "client_name": "Trzeci"})
        with patch.object(sudosync_core, "_addon_profile_path", return_value=self.dir), \
                patch.object(sudosync_core, "_write_local_json",
                             side_effect=lambda p, d: (writes.append(1), real_write(p, d))):
            self.assertTrue(sudosync_core.record_kodibridge_id_coverage(coverage))
            self.assertFalse(sudosync_core.record_kodibridge_id_coverage(dict(coverage)))
            status = sudosync_core.read_local_status()
        self.assertEqual(len(writes), 1)
        self.assertEqual(status["kodibridge_id_coverage"]["episode"]["missing"], 1)
        self.assertTrue(status["kodibridge_id_coverage"]["checked_at"])

    def test_service_records_coverage_after_a_failed_cycle(self):
        runtime = MagicMock()
        runtime.sync_cycle.side_effect = RuntimeError("Cannot safely build synchronization preview")
        runtime.sync_engine.last_coverage = {"movie": {}, "episode": {"missing": 3}}
        monitor = MagicMock()
        monitor.scan_refresh_pending = False
        with patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "record_kodibridge_id_coverage") as record:
            result = sudosync_service._execute_integrated_live_cycle(runtime, monitor, False, False, True)
        self.assertTrue(result["busy"])
        record.assert_called_once_with({"movie": {}, "episode": {"missing": 3}})

    def test_status_window_text(self):
        movies, episodes = library()
        coverage = kodibridge_id_coverage(movies, episodes)
        coverage["unreadable_nfo"] = 1
        coverage["checked_at"] = "2026-10-30T20:00:00Z"
        text = default_module._coverage_text(coverage)
        self.assertIn("filmy 1/2, odcinki 5/6", text)
        self.assertIn("bez ID: 2", text)
        self.assertIn("zdublowane ID: 1", text)
        self.assertIn("wspólne ID zewnętrzne bez KodiBridge ID: 1", text)
        self.assertIn("nieczytelne .nfo: 1", text)
        complete = kodibridge_id_coverage([{"movieid": 1, "uniqueid": {"sudosync": "MAZURY-000001"}}], [])
        self.assertIn("Wszystkie pozycje mają KodiBridge ID.", default_module._coverage_text(complete))
        self.assertIn("pojawi się po pierwszym cyklu", default_module._coverage_text(None))


if __name__ == "__main__":
    unittest.main()
