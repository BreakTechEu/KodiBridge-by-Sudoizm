# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Copies of one video: the same KodiBridge ID and the same external ID.

Such files (e.g. one on a local disk, one on the NAS) are one item for
synchronization, not a duplicate KodiBridge ID.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.modules.setdefault("xbmc", MagicMock())
sys.modules.setdefault("xbmcaddon", MagicMock())
sys.modules.setdefault("xbmcgui", MagicMock())
sys.modules.setdefault("xbmcvfs", MagicMock())

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib import sudosync_core
from resources.lib.sudosync_v2.copies import CopyAwareKodiAdapter, find_copy_groups, same_video
from resources.lib.sudosync_v2.identity import new_kodibridge_id
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory, build_v2_runtime
from tests.test_kodibridge_uuid_sync import FakeKodi
from tests.test_sudosync_v2_runtime import InMemorySharedBackend

SHARED = "smb://nas/wideo/.KodiBridge"
CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"
HDD = "D:\\Filmy\\Film.mkv"
NAS = "smb://nas/wideo/Film.mkv"


def _movie(movieid, path, playcount=0, lastplayed=""):
    return {
        "movieid": movieid, "title": "Film", "year": 2020, "file": path, "imdbnumber": "",
        "uniqueid": {}, "playcount": playcount, "lastplayed": lastplayed,
        "resume": {"position": 0.0, "total": 0.0}, "userrating": 0,
    }


def _nfo(kb_id, imdb):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<movie>\n  <title>Film</title>\n'
            '  <uniqueid type="kodibridge">{}</uniqueid>\n  <uniqueid type="imdb">{}</uniqueid>\n'
            '</movie>\n').format(kb_id, imdb)


class SameVideoTests(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(same_video([{"imdb": "tt1", "sudosync": "a"}, {"imdb": "tt1", "tmdb": "5"}]))
        self.assertFalse(same_video([{"imdb": "tt1"}, {"imdb": "tt2"}]))
        self.assertFalse(same_video([{"imdb": "tt1", "tmdb": "5"}, {"imdb": "tt1", "tmdb": "6"}]))
        self.assertFalse(same_video([{"imdb": "tt1"}, {"sudosync": "a"}]))
        self.assertFalse(same_video([{"imdb": "tt1"}]))

    def test_groups(self):
        kb = new_kodibridge_id()

        def item(movieid, path, **ids):
            ids["sudosync"] = kb
            return {"movieid": movieid, "file": path, "uniqueid": ids}

        self.assertEqual(len(find_copy_groups([item(1, HDD, imdb="tt1"), item(2, NAS, imdb="tt1")])), 1)
        self.assertEqual(find_copy_groups([item(1, HDD, imdb="tt1"), item(2, NAS, imdb="tt2")]), [])
        self.assertEqual(find_copy_groups([item(1, HDD), item(2, NAS)]), [])
        # One file listed twice (letter case) is not a copy.
        self.assertEqual(find_copy_groups([item(1, NAS, imdb="tt1"), item(2, NAS.upper(), imdb="tt1")]), [])

    def test_repair_skips_copies_but_not_duplicates(self):
        kb = new_kodibridge_id()
        sudosync_core._NFO_ID_CACHE.clear()

        def record(path, imdb):
            return {"type": "movie", "file": path, "title": "Film", "ids": {"sudosync": kb, "imdb": imdb}}

        copies = sudosync_core.find_duplicate_sudosync_id_groups([record(HDD, "tt1"), record(NAS, "tt1")], [])
        self.assertEqual(copies, [])
        duplicates = sudosync_core.find_duplicate_sudosync_id_groups([record(HDD, "tt1"), record(NAS, "tt2")], [])
        self.assertEqual(len(duplicates), 1)


class CopyAwareAdapterTests(unittest.TestCase):
    def setUp(self):
        self.kb = new_kodibridge_id()
        nfo = _nfo(self.kb, "tt1234567")
        self.files = {"D:/Filmy/Film.nfo": nfo, "smb://nas/wideo/Film.nfo": nfo}
        self.kodi = FakeKodi([_movie(1, HDD), _movie(2, NAS)])
        self.adapter = CopyAwareKodiAdapter(KodiJsonRpcAdapter(self.kodi, nfo_reader=self._reader))

    def _reader(self, path, max_bytes):
        return self.files.get(path.replace("\\", "/"))

    def test_copies_are_one_item_and_writes_reach_both(self):
        movies = self.adapter.get_movies()
        self.assertEqual([m["movieid"] for m in movies], [1])
        self.adapter.set_movie_details(1, {"playcount": 1, "lastplayed": "2026-11-02 20:00:00"})
        self.assertEqual(self.kodi.movies[1]["playcount"], 1)
        self.assertEqual(self.kodi.movies[2]["playcount"], 1)
        details = self.adapter.get_movie_details(1)
        self.assertEqual(details["playcount"], 1)

    def test_watching_the_second_copy_is_seen_and_aligned(self):
        self.adapter.get_movies()
        self.kodi.movies[2]["playcount"] = 1
        self.kodi.movies[2]["lastplayed"] = "2026-11-02 21:00:00"
        movies = self.adapter.get_movies()
        self.assertEqual(movies[0]["playcount"], 1)
        self.assertEqual(movies[0]["lastplayed"], "2026-11-02 21:00:00")
        # Reading again gives the same merged state.
        self.assertEqual(self.adapter.get_movie_details(1)["playcount"], 1)
        self.assertEqual(self.adapter.align_copies(), 1)
        self.assertEqual(self.kodi.movies[1]["playcount"], 1)
        self.assertEqual(self.adapter.align_copies(), 0)

    def test_unwatched_through_sync_does_not_come_back(self):
        self.kodi.movies[1]["playcount"] = self.kodi.movies[2]["playcount"] = 2
        self.adapter.get_movies()
        self.adapter.set_movie_details(1, {"playcount": 0})
        self.assertEqual(self.adapter.get_movies()[0]["playcount"], 0)
        self.assertEqual(self.adapter.align_copies(), 0)
        self.assertEqual((self.kodi.movies[1]["playcount"], self.kodi.movies[2]["playcount"]), (0, 0))

    def test_unwatching_one_copy_locally_wins(self):
        self.kodi.movies[1]["playcount"] = self.kodi.movies[2]["playcount"] = 1
        self.adapter.get_movies()
        self.kodi.movies[1]["playcount"] = 0
        self.assertEqual(self.adapter.get_movies()[0]["playcount"], 0)
        self.adapter.align_copies()
        self.assertEqual(self.kodi.movies[2]["playcount"], 0)

    def test_first_look_takes_the_richer_state(self):
        self.kodi.movies[2]["playcount"] = 3
        self.kodi.movies[2]["userrating"] = 8
        movie = self.adapter.get_movies()[0]
        self.assertEqual((movie["playcount"], movie["userrating"]), (3, 8))

    def test_different_imdb_stays_two_items(self):
        self.files["smb://nas/wideo/Film.nfo"] = _nfo(self.kb, "tt7654321")
        self.assertEqual(len(self.adapter.get_movies()), 2)


class CopiesEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.kb = new_kodibridge_id()
        nfo = _nfo(self.kb, "tt1234567")
        self.files = {"D:/Filmy/Film.nfo": nfo, "smb://nas/wideo/Film.nfo": nfo}
        self.backend = InMemorySharedBackend()
        self.locks = ProcessLocalLockFactory()

    def _reader(self, path, max_bytes):
        return self.files.get(path.replace("\\", "/"))

    def _runtime(self, kodi, client, name):
        adapter = CopyAwareKodiAdapter(KodiJsonRpcAdapter(kodi, nfo_reader=self._reader))
        return build_v2_runtime(adapter, self.backend, SHARED, self.locks, client, name, sudosync_only=True)

    def test_watched_copy_on_one_kodi_reaches_all_copies_everywhere(self):
        win11 = FakeKodi([_movie(1, HDD), _movie(2, NAS)])      # local copy and NAS copy
        android = FakeKodi([_movie(7, NAS)])                    # NAS copy only
        rt_win11 = self._runtime(win11, CLIENT_A, "Win11")
        rt_android = self._runtime(android, CLIENT_B, "AndroidBox")
        rt_win11.publish_local_snapshot()
        rt_android.sync_cycle()

        # Watched from the local disk on Win11.
        win11.movies[1]["playcount"] = 1
        win11.movies[1]["lastplayed"] = "2026-11-02 20:00:00"
        rt_win11.sync_cycle()
        self.assertEqual(win11.movies[2]["playcount"], 1)   # the NAS copy follows locally
        rt_android.sync_cycle()
        self.assertEqual(android.movies[7]["playcount"], 1)
        self.assertEqual(android.movies[7]["lastplayed"], "2026-11-02 20:00:00")

        # Marked unwatched on the Android box: both copies on Win11 follow.
        android.movies[7]["playcount"] = 0
        rt_android.sync_cycle()
        rt_win11.sync_cycle()
        self.assertEqual((win11.movies[1]["playcount"], win11.movies[2]["playcount"]), (0, 0))

        # Nothing more to do.
        writes = (win11.writes, android.writes)
        for runtime in (rt_win11, rt_android, rt_win11):
            self.assertEqual(runtime.sync_cycle()["applied_count"], 0)
        self.assertEqual((win11.writes, android.writes), writes)


if __name__ == "__main__":
    unittest.main()
