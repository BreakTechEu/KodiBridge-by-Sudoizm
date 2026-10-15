# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for SudoSync v2 startup preview resilience and abort handling."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.engine import SyncEngine
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_v2.snapshot import build_snapshot

CLIENT_A = "12345678-1234-4234-8234-123456789012"
CLIENT_B = "22345678-1234-4234-8234-123456789012"
STAMP = "2026-10-15T10:00:00Z"


def make_movie(kodi_id, title, file_path, imdb=None, tmdb=None, playcount=0):
    uniqueid = {}
    if imdb:
        uniqueid["imdb"] = imdb
    if tmdb:
        uniqueid["tmdb"] = tmdb
    return {
        "movieid": kodi_id,
        "title": title,
        "file": file_path,
        "imdbnumber": imdb or "",
        "uniqueid": uniqueid,
        "playcount": playcount,
        "lastplayed": "",
        "resume": {"position": 0, "total": 0},
        "userrating": 0,
    }


class FakeTransport(object):
    def __init__(self, movies=None, episodes=None, playing=False):
        self.movies = movies or []
        self.episodes = episodes or []
        self.playing = playing
        self.calls = []

    def __call__(self, payload):
        request = json.loads(payload)
        method = request["method"]
        self.calls.append(request)
        request_id = request["id"]
        if method == "Player.GetActivePlayers":
            players = [{"playerid": 1, "type": "video"}] if self.playing else []
            return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": players})
        if method == "VideoLibrary.GetMovies":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"movies": self.movies, "limits": {"start": 0, "end": len(self.movies), "total": len(self.movies)}},
            })
        if method == "VideoLibrary.GetEpisodes":
            return json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"episodes": self.episodes, "limits": {"start": 0, "end": len(self.episodes), "total": len(self.episodes)}},
            })
        if method in ("VideoLibrary.SetMovieDetails", "VideoLibrary.SetEpisodeDetails"):
            raise AssertionError("Preview must not mutate Kodi library")
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {}})


class TestPreviewResilience(unittest.TestCase):
    def test_abort_in_middle_of_preview_returns_partial_result_and_no_writes(self):
        movies = [
            make_movie(1, "Movie One", "smb://nas/Films/MovieOne.mkv", tmdb="101"),
            make_movie(2, "Movie Two", "smb://nas/Films/MovieTwo.mkv", tmdb="102"),
            make_movie(3, "Movie Three", "smb://nas/Films/MovieThree.mkv", tmdb="103"),
        ]
        transport = FakeTransport(movies=movies)

        abort_state = {"aborted": False}

        def mock_nfo_reader(path, max_bytes):
            if "MovieTwo" in path:
                abort_state["aborted"] = True
            return None

        writes_attempted = []

        def disallow_peer_write():
            writes_attempted.append("peer_write")
            return []

        kodi_adapter = KodiJsonRpcAdapter(transport, nfo_reader=mock_nfo_reader, abort_requested=lambda: abort_state["aborted"])
        engine = SyncEngine(
            kodi_adapter,
            CLIENT_A,
            "Living Room",
            load_previous_local=lambda: None,
            load_peer_snapshots=disallow_peer_write,
            abort_requested=lambda: abort_state["aborted"],
        )

        result = engine.preview()

        self.assertFalse(result["active"])
        self.assertTrue(result["aborted"])
        self.assertEqual(result["reason"], "aborted")
        self.assertIsNone(result["plan"])
        self.assertEqual(writes_attempted, [])
        self.assertIsNotNone(result["snapshot"])
        self.assertEqual(len(result["snapshot"]["movies"]), 1)
        self.assertEqual(result["snapshot"]["movies"][0]["ids"]["tmdb"], "101")

    def test_one_corrupt_nfo_among_valid_is_skipped_and_recorded(self):
        movies = [
            make_movie(1, "Corrupted Movie", "smb://nas/Films/Corrupted.mkv", imdb="tt0000001"),
            make_movie(2, "Valid Movie", "smb://nas/Films/Valid.mkv", imdb="tt0000002"),
        ]
        transport = FakeTransport(movies=movies)

        def mock_nfo_reader(path, max_bytes):
            if "Corrupted.nfo" in path:
                return "<movie><uniqueid>broken xml without closing tags"
            if "Valid.nfo" in path:
                return '<movie><uniqueid type="tmdb">777</uniqueid></movie>'
            return None

        kodi_adapter = KodiJsonRpcAdapter(transport, nfo_reader=mock_nfo_reader)
        engine = SyncEngine(
            kodi_adapter,
            CLIENT_A,
            "Living Room",
            load_previous_local=lambda: None,
            load_peer_snapshots=lambda: [],
        )

        result = engine.preview()

        self.assertTrue(result["active"])
        self.assertFalse(result["aborted"])
        self.assertEqual(result["skipped_nfo_count"], 1)
        self.assertEqual(len(result["skipped_nfos"]), 1)
        self.assertEqual(result["skipped_nfos"][0]["path"], "smb://nas/Films/Corrupted.nfo")
        self.assertIn("parsing error", result["skipped_nfos"][0]["reason"])

        snapshot_movies = result["snapshot"]["movies"]
        self.assertEqual(len(snapshot_movies), 2)
        # Movie 1 kept its original Kodi identifier
        self.assertEqual(snapshot_movies[0]["ids"], {"imdb": "tt0000001"})
        # Movie 2 was successfully enriched with TMDb from valid NFO
        self.assertEqual(snapshot_movies[1]["ids"], {"imdb": "tt0000002", "tmdb": "777"})

    def test_multiple_corrupt_nfos_of_different_kinds_all_skipped_with_reasons(self):
        movies = [
            make_movie(1, "Empty NFO", "smb://nas/Films/Empty.mkv", tmdb="1"),
            make_movie(2, "Bad XML", "smb://nas/Films/BadXml.mkv", tmdb="2"),
            make_movie(3, "Bad Encoding", "smb://nas/Films/BadEncoding.mkv", tmdb="3"),
            make_movie(4, "Unreadable NFO", "smb://nas/Films/Unreadable.mkv", tmdb="4"),
            make_movie(5, "Good NFO", "smb://nas/Films/Good.mkv", imdb="tt0000005"),
        ]
        transport = FakeTransport(movies=movies)

        def mock_nfo_reader(path, max_bytes):
            if "Empty.nfo" in path:
                return ""
            if "BadXml.nfo" in path:
                return "<movie><unclosed>"
            if "BadEncoding.nfo" in path:
                return b"\x80\x81\xfe\xff"
            if "Unreadable.nfo" in path:
                raise IOError("Permission denied accessing NFO")
            if "Good.nfo" in path:
                return '<movie><uniqueid type="tmdb">555</uniqueid></movie>'
            return None

        kodi_adapter = KodiJsonRpcAdapter(transport, nfo_reader=mock_nfo_reader)
        engine = SyncEngine(
            kodi_adapter,
            CLIENT_A,
            "Living Room",
            load_previous_local=lambda: None,
            load_peer_snapshots=lambda: [],
        )

        result = engine.preview()

        self.assertTrue(result["active"])
        self.assertFalse(result["aborted"])
        self.assertEqual(result["skipped_nfo_count"], 4)
        self.assertEqual(len(result["skipped_nfos"]), 4)

        paths = [item["path"] for item in result["skipped_nfos"]]
        self.assertIn("smb://nas/Films/Empty.nfo", paths)
        self.assertIn("smb://nas/Films/BadXml.nfo", paths)
        self.assertIn("smb://nas/Films/BadEncoding.nfo", paths)
        self.assertIn("smb://nas/Films/Unreadable.nfo", paths)

        reasons_by_path = {item["path"]: item["reason"] for item in result["skipped_nfos"]}
        self.assertIn("empty", reasons_by_path["smb://nas/Films/Empty.nfo"].lower())
        self.assertIn("parsing error", reasons_by_path["smb://nas/Films/BadXml.nfo"].lower())
        self.assertIn("not valid utf-8", reasons_by_path["smb://nas/Films/BadEncoding.nfo"].lower())
        self.assertIn("permission denied", reasons_by_path["smb://nas/Films/Unreadable.nfo"].lower())

        # All 5 movies are in the candidate snapshot
        self.assertEqual(len(result["snapshot"]["movies"]), 5)
        # Good movie was enriched
        good_movie = [m for m in result["snapshot"]["movies"] if m["ids"].get("imdb") == "tt0000005"][0]
        self.assertEqual(good_movie["ids"]["tmdb"], "555")

    def test_no_abort_and_no_corrupt_nfos_identical_to_baseline(self):
        movies = [
            make_movie(10, "Standard Movie", "smb://nas/Films/Standard.mkv", imdb="tt1234567", playcount=0),
        ]
        transport = FakeTransport(movies=movies)

        def mock_nfo_reader(path, max_bytes):
            if "Standard.nfo" in path:
                return '<movie><uniqueid type="tmdb">999</uniqueid></movie>'
            return None

        remote_base = build_snapshot([make_movie(20, "Standard Movie", "smb://nas/Films/Standard.mkv", imdb="tt1234567", tmdb="999", playcount=0)], [], CLIENT_B, "Bedroom", generated_at=STAMP)
        remote = build_snapshot([make_movie(20, "Standard Movie", "smb://nas/Films/Standard.mkv", imdb="tt1234567", tmdb="999", playcount=2)], [], CLIENT_B, "Bedroom", previous_snapshot=remote_base, generated_at=STAMP)

        kodi_adapter = KodiJsonRpcAdapter(transport, nfo_reader=mock_nfo_reader)
        engine = SyncEngine(
            kodi_adapter,
            CLIENT_A,
            "Living Room",
            load_previous_local=lambda: None,
            load_peer_snapshots=lambda: [remote],
        )

        result = engine.preview()

        self.assertTrue(result["active"])
        self.assertFalse(result["aborted"])
        self.assertEqual(result["reason"], "")
        self.assertEqual(result["skipped_nfo_count"], 0)
        self.assertEqual(result["skipped_nfos"], [])
        self.assertEqual(result["plan"]["change_count"], 1)
        self.assertEqual(result["plan"]["changes"][0]["kodi_id"], 10)
        self.assertEqual(result["plan"]["changes"][0]["changes"]["playcount"], 2)


if __name__ == "__main__":
    unittest.main()
