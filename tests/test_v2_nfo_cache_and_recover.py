# -*- coding: utf-8 -*-
"""Regression tests: NFO reads are cached across cycles and startup does no library scan."""
import os
import sys
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "service.sudosync"))

from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter  # noqa: E402
from resources.lib.sudosync_v2.runtime import V2RuntimeComponents, V2ServiceRuntime  # noqa: E402


class _Transport(object):
    def __init__(self, movies):
        self.movies = movies

    def __call__(self, request):
        import json
        req = json.loads(request)
        if req["method"] == "VideoLibrary.GetMovies":
            return json.dumps({"id": req["id"], "jsonrpc": "2.0", "result": {
                "movies": self.movies, "limits": {"start": 0, "end": len(self.movies), "total": len(self.movies)}}})
        return json.dumps({"id": req["id"], "jsonrpc": "2.0", "result": {}})


def _movie(i):
    return {"movieid": i, "file": "smb://nas/m/%d/film.mkv" % i, "label": "F%d" % i, "uniqueid": {}}


class NfoCacheTests(unittest.TestCase):
    def test_items_without_sudosync_id_and_without_nfo_are_read_once(self):
        calls = []

        def reader(path, max_bytes):
            calls.append(path)
            return None  # no NFO present

        adapter = KodiJsonRpcAdapter(_Transport([_movie(1), _movie(2)]), nfo_reader=reader)
        adapter.get_movies()
        first = len(calls)
        adapter.get_movies()
        self.assertGreater(first, 0)
        self.assertEqual(len(calls), first, "second cycle must not hit the NAS again")

    def test_nfo_without_sudosync_id_is_cached(self):
        calls = []
        nfo = "<movie><uniqueid type=\"tmdb\">123</uniqueid></movie>"

        def reader(path, max_bytes):
            calls.append(path)
            return nfo

        adapter = KodiJsonRpcAdapter(_Transport([_movie(1)]), nfo_reader=reader)
        adapter.get_movies()
        adapter.get_movies()
        self.assertEqual(len(calls), 1)

    def test_read_errors_are_not_cached(self):
        calls = []

        def reader(path, max_bytes):
            calls.append(path)
            raise IOError("share offline")

        adapter = KodiJsonRpcAdapter(_Transport([_movie(1)]), nfo_reader=reader)
        adapter.get_movies()
        first = len(calls)
        adapter.get_movies()
        self.assertGreater(len(calls), first, "failed reads must be retried")

    def test_clear_nfo_cache_forces_reread(self):
        calls = []

        def reader(path, max_bytes):
            calls.append(path)
            return None

        adapter = KodiJsonRpcAdapter(_Transport([_movie(1)]), nfo_reader=reader)
        adapter.get_movies()
        first = len(calls)
        adapter.clear_nfo_cache()
        adapter.get_movies()
        self.assertGreater(len(calls), first)


class RecoverTests(unittest.TestCase):
    def _components(self, recovery_status):
        recovery = MagicMock()
        recovery.recover_on_start.return_value = {"status": recovery_status}
        engine = MagicMock()
        runtime = V2ServiceRuntime("client-0000000001", recovery, engine)
        return V2RuntimeComponents(runtime, None, None, None, None, sync_engine=engine,
                                   client_id="client-0000000001"), engine

    def test_recover_does_not_scan_library(self):
        comps, engine = self._components("clear")
        res = comps.recover()
        self.assertEqual(res["status"], "ready")
        engine.preview.assert_not_called()

    def test_recover_reports_blocked(self):
        comps, engine = self._components("blocked")
        self.assertEqual(comps.recover()["status"], "blocked")
        engine.preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
