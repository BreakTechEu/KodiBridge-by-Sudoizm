# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Working mode selected by the KodiBridge ID option: with the option off the
add-on synchronizes by external IDs alone and does not use KodiBridge IDs."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("xbmc", MagicMock())
sys.modules.setdefault("xbmcaddon", MagicMock())
sys.modules.setdefault("xbmcgui", MagicMock())
sys.modules.setdefault("xbmcvfs", MagicMock())

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
LIB_DIR = os.path.abspath(os.path.join(SERVICE_DIR, "resources", "lib"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import default as sudosync_default
import service as sudosync_service
from resources.lib.sudosync_conflicts import resolvable_conflicts
from resources.lib.sudosync_v2.identity import EXTERNAL_KINDS, matching_kinds
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory, build_v2_runtime
from resources.lib.sudosync_v2.snapshot import external_id_coverage, split_external_items
from tests.test_kodibridge_uuid_sync import FakeKodi
from tests.test_sudosync_v2_runtime import InMemorySharedBackend

SHARED = "smb://nas/wideo/.KodiBridge"
CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"
KB_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
KB_2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
KB_3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"
KB_4 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"


def _movie(movieid, name, uniqueid, playcount=0, lastplayed=""):
    return {
        "movieid": movieid, "title": name, "year": 2020, "file": "smb://nas/wideo/{}.mkv".format(name),
        "imdbnumber": "", "uniqueid": dict(uniqueid),
        "playcount": playcount, "lastplayed": lastplayed,
        "resume": {"position": 0.0, "total": 0.0}, "userrating": 0,
    }


def _library(kb_ids, playcount=0, lastplayed=""):
    """Film: external ID. Domowe: KodiBridge ID only. Wersja1/2: one IMDb ID twice. Bez: no ID."""
    film, home, v1, v2 = kb_ids
    return [
        _movie(1, "Film", {"imdb": "tt0000001", "kodibridge": film}, playcount, lastplayed),
        _movie(2, "Domowe", {"kodibridge": home}, playcount, lastplayed),
        _movie(3, "Wersja1", {"imdb": "tt0000002", "kodibridge": v1}, playcount, lastplayed),
        _movie(4, "Wersja2", {"imdb": "tt0000002", "kodibridge": v2}, playcount, lastplayed),
        _movie(5, "Bez", {}, playcount, lastplayed),
    ]


class MatchingKindsTest(unittest.TestCase):
    def test_option_selects_the_identifier_kinds(self):
        self.assertEqual(matching_kinds(True), ("sudosync",))
        self.assertEqual(matching_kinds(True, external_only=True), ("sudosync",))
        self.assertEqual(matching_kinds(False, external_only=True), EXTERNAL_KINDS)
        self.assertNotIn("sudosync", EXTERNAL_KINDS)
        self.assertIn("sudosync", matching_kinds(False))


class SplitExternalItemsTest(unittest.TestCase):
    def test_only_items_with_an_external_id_of_their_own_are_kept(self):
        items = [
            {"movieid": 1, "uniqueid": {"imdb": "tt0000001", "sudosync": KB_1}},
            {"movieid": 2, "uniqueid": {"sudosync": KB_2}},
            {"movieid": 3, "uniqueid": {"imdb": "tt0000002"}},
            {"movieid": 4, "uniqueid": {"tmdb": "9"}, "imdbnumber": "tt0000002"},
            {"movieid": 5, "uniqueid": {}},
        ]
        kept, no_external, shared = split_external_items(items, "movie")
        self.assertEqual([i["movieid"] for i in kept], [1])
        self.assertEqual(kept[0]["uniqueid"], {"imdb": "tt0000001"})
        self.assertEqual([i["movieid"] for i in no_external], [2, 5])
        self.assertEqual([i["movieid"] for i in shared], [3, 4])
        # The items read from Kodi are not modified.
        self.assertEqual(items[0]["uniqueid"], {"imdb": "tt0000001", "sudosync": KB_1})

    def test_item_with_unreadable_identifiers_is_left_to_the_snapshot_builder(self):
        item = {"movieid": 1, "uniqueid": {"imdb": "tt0000001"}, "imdbnumber": "tt0000009"}
        kept, no_external, shared = split_external_items([item], "movie")
        self.assertEqual(kept, [item])
        self.assertEqual((no_external, shared), ([], []))

    def test_coverage_reports_no_kodibridge_id_problems(self):
        movies = [
            {"movieid": 1, "title": "Film", "year": 2020, "file": "smb://nas/a.mkv", "uniqueid": {"imdb": "tt0000001"}},
            {"movieid": 2, "title": "Domowe", "file": "smb://nas/b.mkv", "uniqueid": {"sudosync": KB_2}},
            {"movieid": 3, "title": "Wersja", "year": 2021, "file": "smb://nas/c.mkv", "uniqueid": {"imdb": "tt0000002"}},
            {"movieid": 4, "title": "Wersja", "year": 2021, "file": "smb://nas/d.mkv", "uniqueid": {"imdb": "tt0000002"}},
        ]
        coverage = external_id_coverage(movies, [])
        movie = coverage["movie"]
        self.assertEqual(coverage["mode"], "external")
        self.assertEqual((movie["total"], movie["with_id"]), (4, 1))
        self.assertEqual((movie["missing"], movie["duplicates"]), (0, 0))
        self.assertEqual((movie["ambiguous"], movie["no_external"]), (2, 1))
        self.assertEqual(movie["places"]["ambiguous"], ["Wersja (2021)"])
        self.assertEqual(movie["places"]["missing"], [])


class ModeWithoutKodiBridgeIdSyncTest(unittest.TestCase):
    def setUp(self):
        self.backend = InMemorySharedBackend()
        self.locks = ProcessLocalLockFactory()
        self.id_enabled = False

    def _runtime(self, kodi, client, name):
        return build_v2_runtime(
            KodiJsonRpcAdapter(kodi), self.backend, SHARED, self.locks, client, name,
            sudosync_only=lambda: self.id_enabled, external_only=lambda: not self.id_enabled)

    def _snapshot(self, client):
        return json.loads(self.backend.files[SHARED + "/clients/client-" + client + ".json"])

    def test_synchronizes_by_external_id_and_ignores_the_rest(self):
        # The same KodiBridge IDs on both Kodi: they must not be used.
        ids = (KB_1, KB_2, KB_3, KB_4)
        first = FakeKodi(_library(ids, playcount=2, lastplayed="2026-11-10 20:00:00"))
        second = FakeKodi(_library(ids))
        rt_first = self._runtime(first, CLIENT_A, "Pierwsze")
        rt_second = self._runtime(second, CLIENT_B, "Drugie")

        rt_first.publish_local_snapshot()
        snapshot = self._snapshot(CLIENT_A)
        self.assertEqual([m["ids"] for m in snapshot["movies"]], [{"imdb": "tt0000001"}])

        result = rt_second.sync_cycle()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(second.movies[1]["playcount"], 2)
        self.assertEqual(second.movies[1]["lastplayed"], "2026-11-10 20:00:00")
        for movieid in (2, 3, 4, 5):
            self.assertEqual(second.movies[movieid]["playcount"], 0)

        coverage = rt_second.sync_engine.last_coverage
        self.assertEqual(coverage["mode"], "external")
        self.assertEqual(coverage["movie"]["ambiguous"], 2)
        self.assertEqual(coverage["movie"]["no_external"], 2)

        writes = (first.writes, second.writes)
        for runtime in (rt_first, rt_second, rt_first):
            self.assertEqual(runtime.sync_cycle()["applied_count"], 0)
        self.assertEqual((first.writes, second.writes), writes)

    def test_different_kodibridge_ids_do_not_split_one_external_id(self):
        first = FakeKodi([_movie(1, "Film", {"imdb": "tt0000001", "kodibridge": KB_1}, 1, "2026-11-10 20:00:00")])
        second = FakeKodi([_movie(1, "Film", {"imdb": "tt0000001", "kodibridge": KB_2})])
        self._runtime(first, CLIENT_A, "Pierwsze").publish_local_snapshot()
        self.assertEqual(self._runtime(second, CLIENT_B, "Drugie").sync_cycle()["status"], "committed")
        self.assertEqual(second.movies[1]["playcount"], 1)

    def test_switching_the_option_later_changes_the_mode_both_ways(self):
        ids = (KB_1, KB_2, KB_3, KB_4)
        first = FakeKodi(_library(ids))
        second = FakeKodi(_library(ids))
        rt_first = self._runtime(first, CLIENT_A, "Pierwsze")
        rt_second = self._runtime(second, CLIENT_B, "Drugie")

        # Start with KodiBridge ID: every item with an ID is synchronized.
        self.id_enabled = True
        rt_first.publish_local_snapshot()
        rt_second.sync_cycle()
        first.movies[2]["playcount"] = 1
        first.movies[2]["lastplayed"] = "2026-11-11 20:00:00"
        rt_first.sync_cycle()
        rt_second.sync_cycle()
        self.assertEqual(second.movies[2]["playcount"], 1)
        self.assertEqual(len(self._snapshot(CLIENT_A)["movies"]), 5)

        # Option switched off: only the item with its own external ID remains.
        self.id_enabled = False
        first.movies[1]["playcount"] = 3
        first.movies[1]["lastplayed"] = "2026-11-12 20:00:00"
        first.movies[2]["playcount"] = 5
        first.movies[2]["lastplayed"] = "2026-11-12 21:00:00"
        first.movies[3]["playcount"] = 1
        first.movies[3]["lastplayed"] = "2026-11-12 22:00:00"
        rt_first.sync_cycle()
        rt_second.sync_cycle()
        self.assertEqual([m["ids"] for m in self._snapshot(CLIENT_A)["movies"]], [{"imdb": "tt0000001"}])
        self.assertEqual(second.movies[1]["playcount"], 3)
        self.assertEqual(second.movies[2]["playcount"], 1)
        self.assertEqual(second.movies[3]["playcount"], 0)

        # Option switched on again: KodiBridge IDs are used again.
        self.id_enabled = True
        rt_first.sync_cycle()
        result = rt_second.sync_cycle()
        rt_first.sync_cycle()
        self.assertEqual(len(self._snapshot(CLIENT_A)["movies"]), 5)
        self.assertEqual(second.movies[1]["playcount"], 3)
        # Watched only on one Kodi while ignored: the other Kodi receives it.
        self.assertEqual(second.movies[3]["playcount"], 1)
        # Watched on both with different values: nothing is overwritten, the
        # user is asked (regular conflict resolution).
        self.assertEqual(first.movies[2]["playcount"], 5)
        self.assertEqual(second.movies[2]["playcount"], 1)
        asked = resolvable_conflicts(result)
        self.assertTrue(asked)
        self.assertEqual({c["kodi_id"] for c in asked}, {2})


class MessagesTest(unittest.TestCase):
    @staticmethod
    def _coverage(shared=0, ignored=0, repeated=0):
        return {
            "mode": "external",
            "movie": {"total": 10, "with_id": 10 - shared - ignored, "missing": 0, "duplicates": 0,
                      "ambiguous": shared, "repeated": repeated, "no_external": ignored,
                      "places": {"missing": [], "duplicates": [], "repeated": [],
                                 "ambiguous": ["Wersja (2021)"] if shared else []}},
            "episode": {"total": 0, "with_id": 0, "missing": 0, "duplicates": 0, "ambiguous": 0,
                        "repeated": 0, "no_external": 0, "places": {}},
            "checked_at": "2026-11-13T12:00:00Z",
        }

    def test_ignored_items_are_not_a_problem(self):
        self.assertEqual(sudosync_service.coverage_problem_summary(self._coverage(ignored=7)), (None, None))

    def test_shared_external_id_is_reported_without_kodibridge_wording(self):
        key, message = sudosync_service.coverage_problem_summary(self._coverage(shared=2))
        self.assertEqual(key[0], "external")
        self.assertIn("wspólny identyfikator zewnętrzny", message)
        self.assertNotIn("KodiBridge ID", message)

    def test_status_window_without_the_option(self):
        text = sudosync_default._coverage_text(self._coverage(shared=2, ignored=3), id_enabled=False)
        self.assertIn("filmy 5/10", text)
        self.assertIn("Pomijane, bo bez identyfikatora zewnętrznego: 3", text)
        self.assertIn("  – Wersja (2021)", text)
        self.assertNotIn("KodiBridge ID", text)
        clean = sudosync_default._coverage_text(self._coverage(), id_enabled=False)
        self.assertNotIn("Do sprawdzenia", clean)

    def test_status_window_does_not_show_coverage_of_the_other_mode(self):
        recorded_with_id = {"movie": {"total": 3, "with_id": 0, "missing": 3}, "episode": {}}
        text = sudosync_default._coverage_text(recorded_with_id, id_enabled=False)
        self.assertNotIn("bez ID", text)
        self.assertIn("pojawi się", text)
        text = sudosync_default._coverage_text(self._coverage(shared=2), id_enabled=True)
        self.assertNotIn("niesynchronizowane", text)
        self.assertIn("pojawi się", text)


class OptionSwitchTest(unittest.TestCase):
    def setUp(self):
        for target, value in ((sudosync_service._ASSIGN_AFTER_SCAN, {"pending": True}),
                              (sudosync_service._COVERAGE_NOTIFIED, {"key": (1, 0, 0, 0, 0)})):
            patcher = patch.dict(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _switch(self, before, after):
        monitor = sudosync_service.SudoSyncMonitor()
        monitor.id_mode = before
        monitor.scan_assign_pending = True
        with patch.object(sudosync_service, "addon_settings", return_value={"sudosync_id_enabled": after}):
            monitor._note_id_mode()
        return monitor

    def test_switching_off_starts_a_cycle_and_drops_the_pending_assignment(self):
        monitor = self._switch(True, False)
        self.assertTrue(monitor.dirty)
        self.assertFalse(monitor.scan_assign_pending)
        self.assertFalse(sudosync_service._ASSIGN_AFTER_SCAN["pending"])
        self.assertIsNone(sudosync_service._COVERAGE_NOTIFIED["key"])
        self.assertFalse(monitor.id_mode)

    def test_switching_on_starts_a_cycle(self):
        monitor = self._switch(False, True)
        self.assertTrue(monitor.dirty)
        self.assertTrue(monitor.id_mode)

    def test_other_settings_change_nothing(self):
        monitor = self._switch(True, True)
        self.assertFalse(monitor.dirty)
        self.assertTrue(monitor.scan_assign_pending)


if __name__ == "__main__":
    unittest.main()
