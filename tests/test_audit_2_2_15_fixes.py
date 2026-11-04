# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Fixes after the external audit of 2.2.15 (released as 2.2.16)."""
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("xbmc", MagicMock())
sys.modules.setdefault("xbmcaddon", MagicMock())
sys.modules.setdefault("xbmcgui", MagicMock())
sys.modules.setdefault("xbmcvfs", MagicMock())

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import resources.lib.sudosync_bootstrap as bootstrap
from resources.lib import sudosync_core
from resources.lib.sudosync_v2.copies import CopyAwareKodiAdapter, find_copy_groups
from resources.lib.sudosync_v2.identity import new_kodibridge_id
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter, has_xml_dtd
from resources.lib.sudosync_v2.kodi_vfs import KodiVFSBackendError
from tests.test_kodibridge_copies import HDD, NAS, _movie, _nfo
from tests.test_kodibridge_uuid_sync import FakeKodi

BASE = "smb://nas/video/.KodiBridge/"


class CopyFixesTests(unittest.TestCase):
    def setUp(self):
        self.kb = new_kodibridge_id()
        nfo = _nfo(self.kb, "tt1234567")
        self.files = {"D:/Filmy/Film.nfo": nfo, "smb://nas/wideo/Film.nfo": nfo}
        self.kodi = FakeKodi([_movie(1, HDD), _movie(2, NAS)])
        self.adapter = CopyAwareKodiAdapter(KodiJsonRpcAdapter(self.kodi, nfo_reader=self._reader))

    def _reader(self, path, max_bytes):
        return self.files.get(path.replace("\\", "/"))

    def test_rating_a_copy_without_rating_is_kept_and_aligned(self):
        self.adapter.get_movies()
        self.kodi.movies[2]["userrating"] = 8
        self.assertEqual(self.adapter.get_movies()[0]["userrating"], 8)
        self.adapter.align_copies()
        self.assertEqual((self.kodi.movies[1]["userrating"], self.kodi.movies[2]["userrating"]), (8, 8))

    def test_removing_the_rating_of_a_copy_is_kept(self):
        self.kodi.movies[1]["userrating"] = self.kodi.movies[2]["userrating"] = 7
        self.adapter.get_movies()
        self.kodi.movies[1]["userrating"] = 0
        self.assertEqual(self.adapter.get_movies()[0]["userrating"], 0)
        self.adapter.align_copies()
        self.assertEqual(self.kodi.movies[2]["userrating"], 0)

    def _item(self, item_id, path, media="movie", **extra):
        item = {"file": path, "uniqueid": {"sudosync": self.kb, "imdb": "tt1"}}
        item["movieid" if media == "movie" else "episodeid"] = item_id
        item.update(extra)
        return item

    def test_file_listed_twice_plus_a_copy_is_one_group(self):
        groups = find_copy_groups([self._item(1, NAS), self._item(2, NAS.upper()), self._item(3, HDD)])
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(i["movieid"] for i in groups[0]), [1, 2, 3])

    def test_multi_episode_file_is_still_not_a_copy(self):
        items = [self._item(1, NAS, "episode", season=1, episode=1),
                 self._item(2, NAS, "episode", season=1, episode=2),
                 self._item(3, HDD, "episode", season=1, episode=1)]
        self.assertEqual(find_copy_groups(items), [])

    def test_repair_skips_a_copy_whose_file_is_listed_twice(self):
        sudosync_core._NFO_ID_CACHE.clear()

        def record(path):
            return {"type": "movie", "file": path, "title": "Film", "ids": {"sudosync": self.kb, "imdb": "tt1"}}

        groups = sudosync_core.find_duplicate_sudosync_id_groups([record(NAS), record(NAS.upper()), record(HDD)], [])
        self.assertEqual(groups, [])

    def test_nfo_identifiers_win_over_the_kodi_database(self):
        item = {"movieid": 5, "file": NAS, "title": "Film", "uniqueid": {"imdb": "tt0000001", "tmdb": "11"},
                "playcount": 0, "resume": {}, "userrating": 0}
        with patch.object(sudosync_core, "_ids_from_nfo", return_value={"imdb": "tt0000002", "sudosync": self.kb}):
            rec = sudosync_core._record("movie", item)
        self.assertEqual(rec["ids"]["imdb"], "tt0000002")
        self.assertEqual(rec["ids"]["tmdb"], "11")
        self.assertEqual(rec["ids"]["sudosync"], self.kb)


class LeaseFixesTests(unittest.TestCase):
    PEER = {"client_id": "client-BOX", "client_name": "Box", "updated_at_epoch": 1000.0}

    def test_unreadable_lease_is_retried_then_stops_start(self):
        dialog = MagicMock()
        with patch.object(bootstrap, "_local_client_identity", return_value=("client-PC", "PC")), \
                patch.object(bootstrap, "_read_json_file", return_value=("IO_ERROR", None)), \
                patch.object(bootstrap.xbmc, "sleep") as mock_sleep, \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            self.assertFalse(bootstrap.ensure_no_active_peer_kodi(BASE, now_epoch=1050.0))
        self.assertEqual(mock_sleep.call_count, bootstrap.LEASE_READ_ATTEMPTS - 1)
        dialog.ok.assert_called_once()

    def test_unreadable_lease_that_recovers_lets_start_continue(self):
        reads = iter([("INVALID", None), ("NOT_FOUND", None)])
        with patch.object(bootstrap, "_local_client_identity", return_value=("client-PC", "PC")), \
                patch.object(bootstrap, "_read_json_file", side_effect=lambda _p: next(reads)), \
                patch.object(bootstrap.xbmc, "sleep"):
            self.assertTrue(bootstrap.ensure_no_active_peer_kodi(BASE, now_epoch=1050.0))

    def _fresh(self, nas_age, now=1050.0, doc=None):
        with patch.object(bootstrap, "_nas_clock_age", return_value=nas_age):
            return bootstrap._lease_is_fresh(BASE, BASE + "active_kodi_lock.json", dict(doc or self.PEER), 180, now)

    def test_nas_clock_decides_over_device_clocks(self):
        self.assertTrue(self._fresh(50.0, now=99999.0))     # device clock far ahead
        self.assertFalse(self._fresh(500.0, now=1001.0))    # device clocks say fresh, NAS says old
        self.assertTrue(self._fresh(None, now=1050.0))      # fallback to device clocks
        self.assertFalse(self._fresh(None, now=1300.0))
        future = dict(self.PEER, updated_at_epoch=9999999999.0)
        self.assertFalse(self._fresh(None, doc=future))     # far-future lease does not block forever

    def test_nas_clock_age_uses_server_times_and_removes_the_probe(self):
        def mtime(path):
            return 5000.0 if path.endswith("active_kodi_lock.json") else 5040.0

        with patch.object(bootstrap, "_local_client_identity", return_value=("client-PC", "PC")), \
                patch.object(bootstrap, "_file_mtime", side_effect=mtime), \
                patch.object(bootstrap, "_write_probe", return_value=True) as mock_probe, \
                patch.object(bootstrap, "_b_delete") as mock_delete:
            age = bootstrap._nas_clock_age(BASE, BASE + "active_kodi_lock.json")
        self.assertEqual(age, 40.0)
        probe = mock_probe.call_args[0][0]
        self.assertTrue(probe.startswith(BASE + "clock_probe_client-PC"))
        mock_delete.assert_called_once_with(probe)

    def test_no_probe_without_a_lease_file_time(self):
        with patch.object(bootstrap, "_file_mtime", return_value=None), \
                patch.object(bootstrap, "_write_probe") as mock_probe:
            self.assertIsNone(bootstrap._nas_clock_age(BASE, BASE + "active_kodi_lock.json"))
        mock_probe.assert_not_called()

    def test_lease_taken_over_right_after_writing_gives_way(self):
        reads = iter([("NOT_FOUND", None), ("VALID", dict(self.PEER))])
        dialog = MagicMock()
        with patch.object(bootstrap, "_local_client_identity", return_value=("client-PC", "PC")), \
                patch.object(bootstrap, "_read_json_file", side_effect=lambda _p: next(reads)), \
                patch.object(bootstrap, "_write_json_file", return_value=True), \
                patch.object(bootstrap.xbmc, "sleep"), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            self.assertFalse(bootstrap.acquire_network_active_kodi_lease(BASE, now_epoch=1050.0))
        dialog.ok.assert_called_once()

    def test_refresh_failing_longer_than_ttl_stops_the_service(self):
        bootstrap._LEASE_STATE["last_ok_epoch"] = 1000.0
        try:
            with patch.object(bootstrap, "_local_client_identity", return_value=("client-PC", "PC")), \
                    patch.object(bootstrap, "_get_local_addon_version", return_value=""), \
                    patch.object(bootstrap, "_read_json_file", return_value=("NOT_FOUND", None)), \
                    patch.object(bootstrap, "_write_json_file", return_value=False):
                self.assertTrue(bootstrap.refresh_network_active_kodi_lease(BASE, now_epoch=1100.0))
                self.assertFalse(bootstrap.refresh_network_active_kodi_lease(BASE, now_epoch=1200.0))
        finally:
            bootstrap._LEASE_STATE["last_ok_epoch"] = None

    def test_device_name_from_the_lease_is_cleaned(self):
        name = bootstrap._clean_client_name("[COLOR red][B]Salon[/B][/COLOR]\nx" + "y" * 100)
        self.assertTrue(name.startswith("Salonx"))
        self.assertNotIn("[", name)
        self.assertLessEqual(len(name), 64)

    def test_changing_the_folder_waits_for_the_peer_check(self):
        dialog = MagicMock()
        dialog.yesno.return_value = True
        dialog.browse.return_value = "smb://nas/other/"
        with patch.object(bootstrap, "get_base_path", return_value=BASE), \
                patch.object(bootstrap, "ensure_no_active_peer_kodi", return_value=False), \
                patch.object(bootstrap, "release_network_active_kodi_lease") as mock_release, \
                patch.object(bootstrap, "set_base_path") as mock_set, \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            self.assertFalse(bootstrap.run_bootstrap(force_select_path=True))
        mock_release.assert_not_called()
        mock_set.assert_not_called()

        with patch.object(bootstrap, "get_base_path", return_value=BASE), \
                patch.object(bootstrap, "ensure_no_active_peer_kodi", return_value=True), \
                patch.object(bootstrap, "release_network_active_kodi_lease") as mock_release, \
                patch.object(bootstrap, "set_base_path") as mock_set, \
                patch.object(bootstrap, "test_vfs_rw", return_value=(False, "x")), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            self.assertFalse(bootstrap.run_bootstrap(force_select_path=True))
        mock_release.assert_called_once_with(BASE)
        mock_set.assert_called_once_with("smb://nas/other/.KodiBridge/")

    def test_oversized_json_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"a": "' + "x" * 200 + '"}')
            with patch.object(bootstrap, "_bootstrap_win_unc", return_value=path), \
                    patch.object(bootstrap, "MAX_BOOTSTRAP_JSON_BYTES", 100):
                self.assertEqual(bootstrap._read_json_file("smb://nas/big.json"), ("INVALID", None))
            with patch.object(bootstrap, "_bootstrap_win_unc", return_value=path):
                self.assertEqual(bootstrap._read_json_file("smb://nas/big.json")[0], "VALID")


class SecurityFixesTests(unittest.TestCase):
    BOMB = ('<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;">]>\n<movie><title>&lol2;</title></movie>')

    def test_nfo_with_dtd_is_never_parsed(self):
        self.assertTrue(has_xml_dtd(self.BOMB))
        self.assertTrue(has_xml_dtd(self.BOMB.encode("utf-8")))
        self.assertFalse(has_xml_dtd("<movie><title>Film</title></movie>"))
        self.assertIsNone(sudosync_core._safe_parse_nfo_xml(self.BOMB))

    def test_v2_reader_reports_dtd_nfo_as_unreadable(self):
        kodi = FakeKodi([_movie(1, NAS)])
        adapter = KodiJsonRpcAdapter(kodi, nfo_reader=lambda path, max_bytes: self.BOMB)
        with patch("resources.lib.sudosync_v2.kodi_rpc.ET.fromstring") as mock_parse:
            movies = adapter.get_movies()
        mock_parse.assert_not_called()
        self.assertNotIn("sudosync", movies[0].get("uniqueid") or {})

    def test_download_and_local_zip_size_limits(self):
        with patch.object(sudosync_core, "MAX_ZIP_ARCHIVE_BYTES", 10):
            with self.assertRaises(IOError):
                sudosync_core._copy_limited(io.BytesIO(b"x" * 11), io.BytesIO(), limit=10)
        self.assertEqual(sudosync_core._copy_limited(io.BytesIO(b"x" * 10), io.BytesIO(), limit=10), 10)
        with tempfile.TemporaryDirectory() as tmp:
            big = os.path.join(tmp, "kodibridge.zip")
            with open(big, "wb") as f:
                f.write(b"x" * 200)
            with patch.object(sudosync_core, "MAX_ZIP_ARCHIVE_BYTES", 100), \
                    patch.object(sudosync_core.xbmcvfs, "translatePath", return_value=os.path.join(tmp, "pk")), \
                    patch.object(sudosync_core, "_validate_addon_zip_package") as mock_validate:
                with self.assertRaises(ValueError):
                    sudosync_core.install_update_from_zip_path(big)
            mock_validate.assert_not_called()

    def test_login_without_password_is_masked(self):
        self.assertIn("smb://***@nas/x", str(KodiVFSBackendError("failed smb://user@nas/x")))
        self.assertIn("smb://admin:***@nas/x", str(KodiVFSBackendError("failed smb://admin:secret@nas/x")))


if __name__ == "__main__":
    unittest.main()
