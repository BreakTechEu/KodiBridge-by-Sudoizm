# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for the mockable Kodi JSON-RPC adapter."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter, KodiRpcError


class FakeTransport(object):
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, payload):
        request = json.loads(payload)
        self.calls.append(request)
        result = self.handler(request)
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})


class TestKodiJsonRpcAdapter(unittest.TestCase):
    def test_get_version(self):
        transport = FakeTransport(lambda req: {"name": "Kodi", "version": {"major": 21, "minor": 2, "patch": 0}})
        adapter = KodiJsonRpcAdapter(transport)
        self.assertEqual(adapter.get_version(), {"name": "Kodi", "major": 21, "minor": 2, "patch": 0})

    def test_get_movies_paginates_to_total(self):
        def handler(req):
            start = req["params"]["limits"]["start"]
            if start == 0:
                return {"movies": [{"movieid": 1}, {"movieid": 2}], "limits": {"start": 0, "end": 2, "total": 3}}
            return {"movies": [{"movieid": 3}], "limits": {"start": 2, "end": 3, "total": 3}}
        transport = FakeTransport(handler)
        adapter = KodiJsonRpcAdapter(transport, page_size=2)
        self.assertEqual([x["movieid"] for x in adapter.get_movies()], [1, 2, 3])
        self.assertEqual(transport.calls[1]["params"]["limits"]["start"], 2)

    def test_library_ids_are_enriched_from_nfo_without_losing_kodi_ids(self):
        transport = FakeTransport(lambda req: {
            "movies": [{
                "movieid": 7,
                "file": "smb://nas/Films/Example.mkv",
                "uniqueid": {"imdb": "tt12345"},
                "imdbnumber": "tt12345",
            }],
            "limits": {"total": 1},
        })
        reads = []

        def read_nfo(path, max_bytes):
            reads.append((path, max_bytes))
            if path.endswith("Example.nfo"):
                return '<movie><uniqueid type="tmdb">123</uniqueid><uniqueid type="sudosync">nas:film-7</uniqueid></movie>'
            return None

        item = KodiJsonRpcAdapter(transport, nfo_reader=read_nfo).get_movies()[0]

        self.assertEqual(item["uniqueid"], {
            "imdb": "tt12345", "tmdb": "123", "sudosync": "nas:film-7",
        })
        self.assertEqual(reads[0][0], "smb://nas/Films/Example.nfo")

    def test_conflicting_kodi_and_nfo_identifier_fails_closed(self):
        transport = FakeTransport(lambda req: {
            "movies": [{
                "movieid": 7,
                "file": "smb://nas/Films/Example.mkv",
                "uniqueid": {"tmdb": "123"},
            }],
            "limits": {"total": 1},
        })

        def read_nfo(path, max_bytes):
            return '<movie><uniqueid type="tmdb">456</uniqueid></movie>' if path.endswith("Example.nfo") else None

        with self.assertRaisesRegex(KodiRpcError, "conflicting tmdb"):
            KodiJsonRpcAdapter(transport, nfo_reader=read_nfo).get_movies()

    def test_refuses_more_items_than_reported_total(self):
        def handler(req):
            return {"movies": [{"movieid": 1}, {"movieid": 2}], "limits": {"total": 1}}
        adapter = KodiJsonRpcAdapter(FakeTransport(handler), page_size=2)
        with self.assertRaisesRegex(KodiRpcError, "more records than its reported total"):
            adapter.get_movies()

    def test_refuses_changing_total_during_pagination(self):
        def handler(req):
            start = req["params"]["limits"]["start"]
            total = 3 if start == 0 else 4
            return {"movies": [{"movieid": start + 1}, {"movieid": start + 2}], "limits": {"total": total}}
        adapter = KodiJsonRpcAdapter(FakeTransport(handler), page_size=2)
        with self.assertRaisesRegex(KodiRpcError, "total changed"):
            adapter.get_movies()

    def test_rejects_rpc_error(self):
        def handler(req):
            return {"error": {"code": -32601, "message": "Method not found"}}
        transport = lambda payload: json.dumps({
            "jsonrpc": "2.0", "id": json.loads(payload)["id"],
            "error": {"code": -32601, "message": "Method not found"},
        })
        with self.assertRaisesRegex(KodiRpcError, "Method not found"):
            KodiJsonRpcAdapter(transport).get_version()

    def test_get_movie_details_for_verification(self):
        transport = FakeTransport(lambda req: {"moviedetails": {"movieid": 4, "playcount": 2}})
        adapter = KodiJsonRpcAdapter(transport)
        self.assertEqual(adapter.get_movie_details(4)["playcount"], 2)
        self.assertEqual(transport.calls[0]["method"], "VideoLibrary.GetMovieDetails")
        self.assertEqual(transport.calls[0]["params"]["movieid"], 4)

    def test_get_episode_details_for_verification(self):
        transport = FakeTransport(lambda req: {"episodedetails": {"episodeid": 8, "playcount": 1}})
        adapter = KodiJsonRpcAdapter(transport)
        self.assertEqual(adapter.get_episode_details(8)["episodeid"], 8)
        self.assertEqual(transport.calls[0]["method"], "VideoLibrary.GetEpisodeDetails")
        self.assertEqual(transport.calls[0]["params"]["episodeid"], 8)

    def test_video_playback_detection(self):
        transport = FakeTransport(lambda req: [{"playerid": 1, "type": "video"}])
        self.assertTrue(KodiJsonRpcAdapter(transport).is_video_playing())

    def test_writes_are_limited_to_supported_fields(self):
        transport = FakeTransport(lambda req: "OK")
        adapter = KodiJsonRpcAdapter(transport)
        self.assertEqual(adapter.set_movie_details(4, {"playcount": 2}), "OK")
        self.assertEqual(transport.calls[0]["method"], "VideoLibrary.SetMovieDetails")
        self.assertEqual(transport.calls[0]["params"], {"movieid": 4, "playcount": 2})
        with self.assertRaisesRegex(ValueError, "unsupported synchronized fields"):
            adapter.set_movie_details(4, {"file": "/some/path"})

    def test_rejects_invalid_resume_data(self):
        adapter = KodiJsonRpcAdapter(FakeTransport(lambda req: "OK"))
        with self.assertRaisesRegex(ValueError, "exceeds total"):
            adapter.set_episode_details(8, {"resume": {"position": 120, "total": 100}})

    def test_malformed_json_response_fails_closed(self):
        adapter = KodiJsonRpcAdapter(lambda payload: "{bad-json")
        with self.assertRaisesRegex(KodiRpcError, "invalid JSON-RPC"):
            adapter.get_version()


if __name__ == "__main__":
    unittest.main()
