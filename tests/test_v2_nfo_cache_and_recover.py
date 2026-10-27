# -*- coding: utf-8 -*-
"""Regression tests: NFO reads are cached across cycles, startup does no library scan, and recovery gates are safe."""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure xbmc stubs exist before importing addon modules
if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = MagicMock()
if "xbmcaddon" not in sys.modules:
    sys.modules["xbmcaddon"] = MagicMock()
if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = MagicMock()
if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = MagicMock()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "service.sudosync"))

from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter  # noqa: E402
from resources.lib.sudosync_v2.runtime import V2RuntimeComponents, V2ServiceRuntime, V2RuntimeError  # noqa: E402
import service as sudosync_service  # noqa: E402


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

    def test_negative_nfo_cache_expires_after_ttl(self):
        calls = []

        def reader(path, max_bytes):
            calls.append(path)
            return None

        # Short TTL of 50 ms for testing expiration
        adapter = KodiJsonRpcAdapter(_Transport([_movie(1)]), nfo_reader=reader, negative_nfo_ttl=0.05)
        adapter.get_movies()
        self.assertEqual(len(calls), 2)  # movie.nfo and film.nfo

        # Immediate second read hits cache
        adapter.get_movies()
        self.assertEqual(len(calls), 2)

        # After TTL expires, negative cache entry is refreshed
        time.sleep(0.08)
        adapter.get_movies()
        self.assertGreater(len(calls), 2)


class RecoverTests(unittest.TestCase):
    def _components(self, recovery_status, client_id="client-0000000001", runtime_client_id="client-0000000001"):
        recovery = MagicMock()
        recovery.recover_on_start.return_value = {"status": recovery_status}
        engine = MagicMock()
        if runtime_client_id:
            runtime = V2ServiceRuntime(runtime_client_id, recovery, engine)
        else:
            runtime = MagicMock()
            runtime._client_id = None
            runtime._abort_requested = None
            runtime._recovery_runner = recovery
        return V2RuntimeComponents(runtime, None, None, None, None, sync_engine=engine,
                                   client_id=client_id), engine

    def test_recover_does_not_scan_library(self):
        comps, engine = self._components("clear")
        res = comps.recover()
        self.assertEqual(res["status"], "ready")
        engine.preview.assert_not_called()

    def test_recover_reports_blocked(self):
        comps, engine = self._components("blocked")
        self.assertEqual(comps.recover()["status"], "blocked")
        engine.preview.assert_not_called()

    def test_recover_reports_deferred(self):
        comps, engine = self._components("deferred")
        res = comps.recover()
        self.assertEqual(res["status"], "deferred")
        engine.preview.assert_not_called()

    def test_recover_validates_client_id_and_uses_service_runtime_fallback(self):
        # Case A: self.client_id is None, falls back to service_runtime._client_id
        comps, _ = self._components("clear", client_id=None, runtime_client_id="client-fallback-123")
        res = comps.recover()
        self.assertEqual(res["status"], "ready")
        comps.service_runtime._recovery_runner.recover_on_start.assert_called_with("client-fallback-123")

        # Case B: both client_id and service_runtime._client_id are empty -> raises V2RuntimeError
        comps_empty, _ = self._components("clear", client_id=None, runtime_client_id=None)
        with self.assertRaises(V2RuntimeError):
            comps_empty.recover()


class InitialSnapshotAndAnalyseGateTests(unittest.TestCase):
    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_blocked(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = {"status": "blocked", "recovery": {"status": "blocked", "reason": "unrecovered_journal"}}

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "blocked")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_deferred(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = {"status": "deferred", "recovery": {"status": "deferred", "reason": "video_playing"}}

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "deferred")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
