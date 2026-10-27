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

    def test_persistent_nfo_cache_saves_and_reloads_across_instances(self):
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="sudosync_test_nfo_cache_")
        try:
            cache_file = os.path.join(temp_dir, "nfo_id_cache.json")
            calls = []
            nfo = "<movie><uniqueid type=\"tmdb\">456</uniqueid></movie>"

            def reader(path, max_bytes):
                calls.append(path)
                return nfo

            cached_via_cb = {}
            def on_cached(path, typ, ids):
                cached_via_cb[path] = ids

            # Instance 1: reads NFO and writes to cache_file
            adapter1 = KodiJsonRpcAdapter(
                _Transport([_movie(1)]),
                nfo_reader=reader,
                cache_path=cache_file,
                on_nfo_cached=on_cached,
            )
            movies1 = adapter1.get_movies()
            self.assertEqual(len(calls), 1)
            self.assertEqual(movies1[0]["uniqueid"].get("tmdb"), "456")
            self.assertTrue(os.path.isfile(cache_file))
            self.assertIn("smb://nas/m/1/film.mkv", cached_via_cb)

            # Instance 2: with reader that raises or tracks calls
            reader2_calls = []
            def reader2(path, max_bytes):
                reader2_calls.append(path)
                return "<movie><uniqueid type=\"tmdb\">999</uniqueid></movie>"

            cached_via_cb2 = {}
            def on_cached2(path, typ, ids):
                cached_via_cb2[path] = ids

            adapter2 = KodiJsonRpcAdapter(
                _Transport([_movie(1)]),
                nfo_reader=reader2,
                cache_path=cache_file,
                on_nfo_cached=on_cached2,
            )
            movies2 = adapter2.get_movies()
            # Must NOT call reader2 because cache was loaded from disk
            self.assertEqual(len(reader2_calls), 0)
            self.assertEqual(movies2[0]["uniqueid"].get("tmdb"), "456")
            self.assertIn("smb://nas/m/1/film.mkv", cached_via_cb2)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_persistent_nfo_cache_seeds_from_assign_ids_manifest(self):
        import json
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="sudosync_test_manifest_")
        try:
            cache_file = os.path.join(temp_dir, "nfo_id_cache.json")
            manifest_file = os.path.join(temp_dir, "assign_ids_manifest.json")
            manifest_data = {
                "modified": [
                    {"file": "smb://nas/m/1/film.nfo", "type": "movie", "sudosync_id": "MAZURY-000042"}
                ]
            }
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f)

            reader_calls = []
            def reader(path, max_bytes):
                reader_calls.append(path)
                return None

            adapter = KodiJsonRpcAdapter(
                _Transport([_movie(1)]),
                nfo_reader=reader,
                cache_path=cache_file,
            )
            movies = adapter.get_movies()
            self.assertEqual(len(reader_calls), 0)
            self.assertEqual(movies[0]["uniqueid"].get("sudosync"), "MAZURY-000042")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


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

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_aborted(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = {"status": "aborted", "recovery": {"status": "aborted"}}

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "aborted")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_recover_raises_exception_fail_closed(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.side_effect = RuntimeError("VFS storage offline")

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "error")
        self.assertIn("VFS storage offline", res.get("error", ""))
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_recover_returns_unexpected_status_fail_closed(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = {"status": "unknown_corruption_state"}

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "unknown_corruption_state")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_recover_returns_non_dict_fail_closed(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = "invalid_string_result"

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "error")
        self.assertEqual(res.get("error"), "invalid_recovery_result")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_skipped_when_v2_recover_returns_mock_fail_closed(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        # An unconfigured mock returning another MagicMock instead of a dict must fail-closed
        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        self.assertEqual(res.get("status"), "error")
        self.assertEqual(res.get("error"), "invalid_recovery_result")
        mock_collect.assert_not_called()
        mock_dry_run.assert_not_called()

    @patch.object(sudosync_service, "collect_and_write")
    @patch.object(sudosync_service, "run_dry_run")
    def test_initial_snapshot_proceeds_when_v2_recover_ready(self, mock_dry_run, mock_collect):
        mock_v2 = MagicMock()
        mock_v2.recover.return_value = {"status": "ready"}

        res = sudosync_service._initial_snapshot_and_analyse(
            v2_runtime=mock_v2,
            abort_requested=lambda: False,
            init_completed=False,
        )

        mock_collect.assert_called_once_with(show_notification=False)
        mock_dry_run.assert_called_once_with(show_notification=False)


class V2LocalStatusAndVfsTests(unittest.TestCase):
    def test_update_local_status_from_v2_populates_status_json(self):
        import resources.lib.sudosync_core as sudosync_core
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="sudosync_status_test_")
        try:
            with patch.object(sudosync_core, "_addon_profile_path", return_value=temp_dir), \
                    patch.object(sudosync_core, "addon_settings", return_value={
                        "base_path": "smb://nas/.SudoSync/",
                        "client_name": "Salon",
                    }), \
                    patch.object(sudosync_core, "get_or_create_client_id", return_value="cid-12345"), \
                    patch.object(sudosync_core, "get_kodi_info", return_value={"version": "21.0"}), \
                    patch.object(sudosync_core, "get_network_sudosync_id_last_number", return_value=1201):
                v2_result = {
                    "status": "committed",
                    "applied_count": 5,
                    "published_snapshot_path": "smb://nas/.SudoSync/clients/client-cid-12345.json",
                    "preview": {
                        "snapshot": {
                            "generated_at": "2026-10-27T19:00:00Z",
                            "movies": [{"movieid": 1}, {"movieid": 2}],
                            "episodes": [{"episodeid": 10}],
                        }
                    }
                }
                status = sudosync_core.update_local_status_from_v2(v2_result=v2_result)

                self.assertIsNotNone(status)
                self.assertEqual(status["client_id"], "cid-12345")
                self.assertEqual(status["client_name"], "Salon")
                self.assertEqual(status["counts"], {"movies": 2, "episodes": 1})
                self.assertEqual(status["last_snapshot_at"], "2026-10-27T19:00:00Z")
                self.assertEqual(status["remote_path"], "smb://nas/.SudoSync/clients/client-cid-12345.json")
                self.assertEqual(status["last_live_applied_items"], 5)
                self.assertEqual(status["sudosync_id_last_number"], 1201)

                # Verify file on disk
                disk_status = sudosync_core.read_local_status()
                self.assertEqual(disk_status["counts"], {"movies": 2, "episodes": 1})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_kodi_vfs_mkdirs_returns_true_if_already_exists(self):
        from resources.lib.sudosync_v2.kodi_vfs import KodiVFSBackend

        fake_vfs = MagicMock()
        # When path already exists, exists() returns True, mkdirs() returns False (Kodi SMB behavior)
        fake_vfs.exists.return_value = True
        fake_vfs.mkdirs.return_value = False

        backend = KodiVFSBackend(fake_vfs, allow_win_unc=False)
        self.assertTrue(backend.mkdirs("smb://nas/wideo/.SudoSync/clients"))
        fake_vfs.exists.assert_called_with("smb://nas/wideo/.SudoSync/clients")

    def test_build_live_report_single_or_no_snapshot_returns_idle_without_error(self):
        import resources.lib.sudosync_core as sudosync_core

        with patch.object(sudosync_core, "addon_settings", return_value={"base_path": "smb://nas/.SudoSync/", "client_name": "Salon"}), \
                patch.object(sudosync_core, "collect_live_snapshot"), \
                patch.object(sudosync_core, "read_remote_snapshots", return_value=([], [])), \
                patch.object(sudosync_core, "get_or_create_client_id", return_value="cid-1"), \
                patch.object(sudosync_core, "read_shared_config", return_value={}), \
                patch.object(sudosync_core, "_observe_remote_lamport"), \
                patch.object(sudosync_core, "_write_vfs_json"), \
                patch.object(sudosync_core, "read_local_status", return_value={}), \
                patch.object(sudosync_core, "_write_local_json"):
            report = sudosync_core.build_live_report(show_notification=False, collect_first=False)
            self.assertEqual(report.get("mode"), "live_idle")
            self.assertTrue(report.get("summary", {}).get("no_peers"))
            self.assertEqual(report.get("planned_changes"), {})


if __name__ == "__main__":
    unittest.main()
