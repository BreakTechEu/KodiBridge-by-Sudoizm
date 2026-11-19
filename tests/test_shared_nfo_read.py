# -*- coding: utf-8 -*-
"""After a scan every .nfo is read once and the post-scan check reuses the identifiers."""
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

import resources.lib.sudosync_core as core  # noqa: E402
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter, OperationAborted  # noqa: E402
import service as sudosync_service  # noqa: E402

ID_A = "0a0a0a0a-1111-4111-8111-111111111111"
ID_B = "0b0b0b0b-2222-4222-8222-222222222222"
MOVIES = [
    {"movieid": 1, "title": "A", "file": "smb://nas/Films/A.mkv", "uniqueid": {}},
    {"movieid": 2, "title": "B", "file": "smb://nas/Films/B.mkv", "uniqueid": {}},
    {"movieid": 3, "title": "C", "file": "smb://nas/Films/C.mkv", "uniqueid": {}},
]
NFO = {
    "smb://nas/Films/A.nfo": '<movie><uniqueid type="kodibridge">{}</uniqueid></movie>'.format(ID_A),
    "smb://nas/Films/B.nfo": '<movie><uniqueid type="kodibridge">{}</uniqueid></movie>'.format(ID_B),
    "smb://nas/Films/C.nfo": "<movie><title>C</title></movie>",
}


class FakeTransport(object):
    def __call__(self, payload):
        request = json.loads(payload)
        if request["method"] == "VideoLibrary.GetMovies":
            result = {"movies": MOVIES, "limits": {"total": len(MOVIES)}}
        else:
            result = {"episodes": [], "limits": {"total": 0}}
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})


def _core_rpc(method, params=None):
    if method == "VideoLibrary.GetMovies":
        return {"movies": [dict(m) for m in MOVIES]}
    return {"episodes": []}


class SharedReadTestBase(unittest.TestCase):
    def setUp(self):
        core._NFO_ID_CACHE.clear()
        self.addCleanup(core._NFO_ID_CACHE.clear)
        self.reads = []

        def read_nfo(path, max_bytes):
            self.reads.append(path)
            return NFO.get(path)

        def seed(path, kind, ids):
            core._NFO_ID_CACHE[path] = dict(ids)

        self.adapter = KodiJsonRpcAdapter(FakeTransport(), nfo_reader=read_nfo, on_nfo_cached=seed)
        self.runtime = MagicMock()
        self.runtime.sync_engine._kodi = self.adapter
        for patcher in (patch.object(sudosync_service, "log"),):
            patcher.start()
            self.addCleanup(patcher.stop)


class SharedReadTests(SharedReadTestBase):
    def test_the_check_reads_only_the_nfo_files_without_identifiers(self):
        core._NFO_ID_CACHE["smb://nas/Films/A.mkv"] = {"sudosync": "stale"}
        self.assertTrue(sudosync_service._read_nfo_once_for_scan(self.runtime, lambda: False))
        self.assertEqual(core._NFO_ID_CACHE["smb://nas/Films/A.mkv"], {"sudosync": ID_A})
        core_reads = []
        real = core._find_best_nfo

        def counting(media_file, kind):
            core_reads.append(media_file)
            return real(media_file, kind)

        with patch.object(core, "addon_settings", return_value={"sudosync_id_enabled": True}), \
                patch.object(core, "rpc", side_effect=_core_rpc), \
                patch.object(core, "_find_best_nfo", side_effect=counting), \
                patch.object(core, "_vfs_exists", return_value=False), \
                patch.object(core.xbmcvfs, "exists", return_value=False):
            preview = core.preview_sudosync_ids(keep_nfo_cache=True)
        self.assertEqual(sorted(set(core_reads)), ["smb://nas/Films/C.mkv"])
        self.assertEqual([item["path"] for item in preview], ["smb://nas/Films/C.nfo"])

    def test_the_next_sync_read_uses_the_cache(self):
        sudosync_service._read_nfo_once_for_scan(self.runtime, lambda: False)
        count = len(self.reads)
        self.adapter.get_movies()
        self.adapter.get_episodes()
        self.assertEqual(len(self.reads), count)

    def test_a_failed_read_leaves_the_check_to_read_the_files(self):
        self.runtime.sync_engine._kodi = MagicMock()
        self.runtime.sync_engine._kodi.get_movies.side_effect = RuntimeError("nas gone")
        core._NFO_ID_CACHE["x"] = {"sudosync": "old"}
        self.assertFalse(sudosync_service._read_nfo_once_for_scan(self.runtime, lambda: False))
        self.assertEqual(core._NFO_ID_CACHE, {})

    def test_an_abort_is_passed_on(self):
        with self.assertRaises(OperationAborted):
            sudosync_service._read_nfo_once_for_scan(self.runtime, lambda: True)

    def test_without_the_v2_reader_nothing_is_read(self):
        self.assertFalse(sudosync_service._read_nfo_once_for_scan(MagicMock(sync_engine=None), lambda: False))
        self.assertEqual(self.reads, [])


class AssignAfterScanTests(SharedReadTestBase):
    def setUp(self):
        super(AssignAfterScanTests, self).setUp()
        self.monitor = MagicMock(scan_nfo_stale=True)
        for patcher in (
            patch.object(sudosync_service, "is_assign_ids_lock_busy", return_value=False),
            patch.object(sudosync_service, "assign_sudosync_ids", return_value=0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _preview(self):
        result = core._PreviewResult([])
        result.collected_items = ([], [])
        return result

    def test_the_check_keeps_the_identifiers_read_once(self):
        preview = MagicMock(return_value=self._preview())
        with patch.object(sudosync_service, "preview_sudosync_ids", preview):
            sudosync_service._assign_ids_after_scan(
                "smb://nas/x/", lambda: False, v2_runtime=self.runtime, monitor=self.monitor)
        self.assertTrue(preview.call_args.kwargs["keep_nfo_cache"])
        self.assertFalse(self.monitor.scan_nfo_stale)

    def test_without_the_shared_read_the_check_reads_everything(self):
        preview = MagicMock(return_value=self._preview())
        with patch.object(sudosync_service, "preview_sudosync_ids", preview):
            sudosync_service._assign_ids_after_scan("smb://nas/x/", lambda: False)
        self.assertFalse(preview.call_args.kwargs["keep_nfo_cache"])

    def test_an_abort_during_the_shared_read_assigns_nothing(self):
        preview = MagicMock(return_value=self._preview())
        with patch.object(sudosync_service, "preview_sudosync_ids", preview):
            result = sudosync_service._assign_ids_after_scan(
                "smb://nas/x/", lambda: True, v2_runtime=self.runtime, monitor=self.monitor)
        self.assertIsNone(result)
        preview.assert_not_called()
        sudosync_service.assign_sudosync_ids.assert_not_called()
        self.assertTrue(self.monitor.scan_nfo_stale)


if __name__ == "__main__":
    unittest.main()
