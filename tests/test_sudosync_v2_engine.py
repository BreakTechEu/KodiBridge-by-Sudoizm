# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for the dependency-injected SudoSync v2 preview engine."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.engine import SyncEngine, SyncEngineError
from resources.lib.sudosync_v2.snapshot import build_snapshot

A = "12345678-1234-4234-8234-123456789012"
B = "22345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


class FakeKodi(object):
    def __init__(self, movies, episodes=None, playing=False):
        self.movies = movies
        self.episodes = episodes or []
        self.playing = playing
        self.calls = []

    def is_video_playing(self):
        self.calls.append("playing")
        return self.playing

    def get_movies(self):
        self.calls.append("movies")
        return self.movies

    def get_episodes(self):
        self.calls.append("episodes")
        return self.episodes


def raw_movie(kodi_id, playcount):
    return {
        "movieid": kodi_id,
        "title": "Example",
        "imdbnumber": "tt1234567",
        "uniqueid": {"imdb": "tt1234567"},
        "playcount": playcount,
        "lastplayed": "",
        "resume": {"position": 0, "total": 0},
        "userrating": 0,
    }


class TestSyncEngine(unittest.TestCase):
    def test_preview_builds_plan_without_publishing(self):
        local_kodi = FakeKodi([raw_movie(10, 0)])
        remote_base = build_snapshot([raw_movie(20, 0)], [], B, "Bedroom", generated_at=STAMP)
        remote = build_snapshot([raw_movie(20, 2)], [], B, "Bedroom", previous_snapshot=remote_base, generated_at=STAMP)
        stores = {"previous": None, "peer_calls": 0}
        engine = SyncEngine(
            local_kodi, A, "Living Room",
            load_previous_local=lambda: stores["previous"],
            load_peer_snapshots=lambda: (stores.__setitem__("peer_calls", stores["peer_calls"] + 1) or [remote]),
        )
        result = engine.preview()
        self.assertTrue(result["active"])
        self.assertEqual(result["plan"]["change_count"], 1)
        self.assertEqual(result["plan"]["changes"][0]["kodi_id"], 10)
        self.assertIsNone(stores["previous"])
        self.assertEqual(stores["peer_calls"], 1)

    def test_initialization_preview_uses_explicit_base_without_publishing(self):
        base = build_snapshot([raw_movie(20, 2)], [], B, "Base", generated_at=STAMP)
        stores = {"previous": None}
        engine = SyncEngine(
            FakeKodi([raw_movie(10, 0)]), A, "Target",
            load_previous_local=lambda: stores["previous"],
            load_peer_snapshots=lambda: self.fail("initialization preview must not load peer list"),
        )
        result = engine.initialization_preview(base)
        self.assertTrue(result["active"])
        self.assertEqual(result["plan"]["change_count"], 1)
        self.assertEqual(result["plan"]["changes"][0]["kodi_id"], 10)
        self.assertEqual(result["plan"]["changes"][0]["changes"]["playcount"], 2)
        self.assertIsNone(stores["previous"])

    def test_playback_defers_before_reading_library_or_peers(self):
        kodi = FakeKodi([raw_movie(10, 0)], playing=True)
        engine = SyncEngine(kodi, A, "Living Room", lambda: None, lambda: self.fail("must not load peers"))
        result = engine.preview()
        self.assertFalse(result["active"])
        self.assertEqual(result["reason"], "video_playing")
        self.assertEqual(kodi.calls, ["playing"])

    def test_invalid_local_kodi_id_fails_closed(self):
        kodi = FakeKodi([raw_movie(None, 0)])
        engine = SyncEngine(kodi, A, "Living Room", lambda: None, lambda: [])
        with self.assertRaises(SyncEngineError):
            engine.preview()

    def test_previous_snapshot_from_other_client_fails_closed(self):
        previous = build_snapshot([], [], B, "Bedroom", generated_at=STAMP)
        engine = SyncEngine(FakeKodi([]), A, "Living Room", lambda: previous, lambda: [])
        with self.assertRaisesRegex(SyncEngineError, "different client"):
            engine.preview()


if __name__ == "__main__":
    unittest.main()
