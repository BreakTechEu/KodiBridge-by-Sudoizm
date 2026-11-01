# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""KodiBridge ID as <uniqueid type="kodibridge"> (UUID); the old type is ignored.

Covers the 2.2.12 change end to end: several Kodi clients read the same .nfo
files, their databases still hold the old sequential IDs, and synchronization
matches media only by the new UUID.
"""
import json
import os
import re
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

from resources.lib import sudosync_bootstrap, sudosync_core
from resources.lib.sudosync_v2.identity import (
    internal_ids_from_external,
    new_kodibridge_id,
)
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory, build_v2_runtime
from tests.test_sudosync_v2_runtime import InMemorySharedBackend

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHARED = "smb://nas/wideo/.KodiBridge"
CLIENT_A = "11111111-1111-4111-8111-111111111111"
CLIENT_B = "22222222-2222-4222-8222-222222222222"
CLIENT_C = "33333333-3333-4333-8333-333333333333"


class FakeKodi(object):
    """JSON-RPC transport of one Kodi with its own library database."""

    def __init__(self, movies):
        self.movies = {m["movieid"]: dict(m) for m in movies}
        self.writes = 0

    def __call__(self, payload):
        req = json.loads(payload)
        method, params = req["method"], req.get("params") or {}
        if method == "Player.GetActivePlayers":
            result = []
        elif method == "VideoLibrary.GetMovies":
            movies = [dict(m) for m in self.movies.values()]
            result = {"movies": movies, "limits": {"total": len(movies)}}
        elif method == "VideoLibrary.GetEpisodes":
            result = {"episodes": [], "limits": {"total": 0}}
        elif method == "VideoLibrary.GetMovieDetails":
            result = {"moviedetails": dict(self.movies[params["movieid"]])}
        elif method == "VideoLibrary.SetMovieDetails":
            movie = self.movies[params["movieid"]]
            for field in ("playcount", "lastplayed", "resume", "userrating"):
                if field in params:
                    movie[field] = params[field]
            self.writes += 1
            result = "OK"
        else:
            raise AssertionError("Unexpected RPC method: " + method)
        return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result})


def _movie(movieid, path, legacy_id, playcount=0, lastplayed=""):
    # The Kodi database still holds the old sequential ID from earlier scans.
    return {
        "movieid": movieid, "title": os.path.basename(path), "year": 2020, "file": path,
        "imdbnumber": "", "uniqueid": {"sudosync": legacy_id},
        "playcount": playcount, "lastplayed": lastplayed,
        "resume": {"position": 0.0, "total": 0.0}, "userrating": 0,
    }


def _nfo(*uniqueids):
    body = "".join('  <uniqueid type="{}">{}</uniqueid>\n'.format(t, v) for t, v in uniqueids)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<movie>\n  <title>X</title>\n' + body + "</movie>\n"


class KodiBridgeUuidSyncTests(unittest.TestCase):
    def setUp(self):
        self.film_id = new_kodibridge_id()
        self.nfo = {
            "smb://nas/wideo/Film.nfo": _nfo(("kodibridge", self.film_id)),
            # A new .nfo without the KodiBridge ID and an untouched old one.
            "smb://nas/wideo/Bez.nfo": _nfo(("tmdb", "77")),
            "smb://nas/wideo/Stary.nfo": _nfo(("sudosync", "DOM-000003")),
        }
        self.backend = InMemorySharedBackend()
        self.locks = ProcessLocalLockFactory()

    def _reader(self, path, max_bytes):
        return self.nfo.get(path)

    def _runtime(self, kodi, client, name):
        adapter = KodiJsonRpcAdapter(kodi, nfo_reader=self._reader)
        return build_v2_runtime(adapter, self.backend, SHARED, self.locks, client, name, sudosync_only=True)

    def _library(self, legacy_offset, playcount=0, lastplayed=""):
        # Each Kodi had different old numbers for the same files.
        return [
            _movie(1, "smb://nas/wideo/Film.mkv", "DOM-{:06d}".format(1 + legacy_offset), playcount, lastplayed),
            _movie(2, "smb://nas/wideo/Bez.mkv", "DOM-{:06d}".format(2 + legacy_offset), playcount, lastplayed),
            _movie(3, "smb://nas/wideo/Stary.mkv", "DOM-000003", playcount, lastplayed),
        ]

    def test_new_id_is_a_lowercase_uuid4_and_unique(self):
        ids = {new_kodibridge_id() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)
        for value in ids:
            self.assertRegex(value, UUID_RE)

    def test_external_mapping_drops_legacy_type(self):
        self.assertEqual(
            internal_ids_from_external({"sudosync": "DOM-000001", "kodibridge": "abc-1", "imdb": "tt1"}),
            {"sudosync": "abc-1", "imdb": "tt1"},
        )
        self.assertEqual(internal_ids_from_external({"SudoSync": "DOM-1"}), {})

    def test_snapshot_carries_only_the_uuid(self):
        kodi = FakeKodi(self._library(0))
        self._runtime(kodi, CLIENT_A, "Win11").publish_local_snapshot()
        snapshot = json.loads(self.backend.files[SHARED + "/clients/client-" + CLIENT_A + ".json"])
        ids = sorted(m["ids"].get("sudosync", "") for m in snapshot["movies"])
        self.assertIn(self.film_id, ids)
        self.assertFalse([v for v in ids if v.startswith("dom-")])

    def test_three_kodi_synchronize_by_uuid_despite_different_old_ids(self):
        win11 = FakeKodi(self._library(0, playcount=2, lastplayed="2026-10-30 20:00:00"))
        win10 = FakeKodi(self._library(100))
        android = FakeKodi(self._library(200))
        rt_win11 = self._runtime(win11, CLIENT_A, "Win11")
        rt_win10 = self._runtime(win10, CLIENT_B, "Win10")
        rt_android = self._runtime(android, CLIENT_C, "AndroidBox")

        rt_win11.publish_local_snapshot()

        res = rt_win10.sync_cycle()
        self.assertEqual(res["status"], "committed")
        self.assertEqual(win10.movies[1]["playcount"], 2)
        self.assertEqual(win10.movies[1]["lastplayed"], "2026-10-30 20:00:00")
        # Without the new ID (or with only the old one) nothing is matched.
        self.assertEqual(win10.movies[2]["playcount"], 0)
        self.assertEqual(win10.movies[3]["playcount"], 0)

        rt_android.sync_cycle()
        self.assertEqual(android.movies[1]["playcount"], 2)
        self.assertEqual(android.movies[2]["playcount"], 0)

        # Watched again on the Android box; both PCs receive it.
        android.movies[1]["playcount"] = 3
        android.movies[1]["lastplayed"] = "2026-11-01 21:00:00"
        rt_android.sync_cycle()
        rt_win11.sync_cycle()
        rt_win10.sync_cycle()
        for kodi in (win11, win10):
            self.assertEqual(kodi.movies[1]["playcount"], 3)
            self.assertEqual(kodi.movies[1]["lastplayed"], "2026-11-01 21:00:00")

        # No feedback loop: further cycles write nothing.
        writes = [k.writes for k in (win11, win10, android)]
        for runtime in (rt_win11, rt_win10, rt_android, rt_win11):
            self.assertEqual(runtime.sync_cycle()["applied_count"], 0)
        self.assertEqual([k.writes for k in (win11, win10, android)], writes)

    def test_id_written_later_by_another_kodi_starts_synchronization(self):
        win11 = FakeKodi(self._library(0, playcount=1, lastplayed="2026-10-31 10:00:00"))
        win10 = FakeKodi(self._library(100))
        rt_win11 = self._runtime(win11, CLIENT_A, "Win11")
        rt_win10 = self._runtime(win10, CLIENT_B, "Win10")
        rt_win11.publish_local_snapshot()
        rt_win10.sync_cycle()
        self.assertEqual(win10.movies[2]["playcount"], 0)

        # Win11 assigns an ID to Bez.nfo; Win10 picks it up from the .nfo.
        self.nfo["smb://nas/wideo/Bez.nfo"] = _nfo(("tmdb", "77"), ("kodibridge", new_kodibridge_id()))
        rt_win11 = self._runtime(win11, CLIENT_A, "Win11")
        rt_win10 = self._runtime(win10, CLIENT_B, "Win10")
        rt_win11.sync_cycle()
        rt_win10.sync_cycle()
        self.assertEqual(win10.movies[2]["playcount"], 1)


class LegacyStateIsIgnoredTests(unittest.TestCase):
    def test_nfo_cache_from_older_version_is_discarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = os.path.join(temp_dir, "nfo_id_cache.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "items": [
                    {"key": ["movie", "smb://nas/wideo/Film", None, None], "ids": {"sudosync": "dom-000001"}},
                ]}, f)
            reads = []

            def reader(path, max_bytes):
                reads.append(path)
                return None

            kodi = FakeKodi([_movie(1, "smb://nas/wideo/Film.mkv", "DOM-000001")])
            movies = KodiJsonRpcAdapter(kodi, nfo_reader=reader, cache_path=cache_file).get_movies()
            self.assertNotIn("sudosync", movies[0]["uniqueid"])
            self.assertTrue(reads)
            with open(cache_file, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["version"], 2)

    def test_manifest_from_older_version_does_not_seed_cache_or_rollback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = os.path.join(temp_dir, "assign_ids_manifest.json")
            with open(manifest, "w", encoding="utf-8") as f:
                json.dump({"modified": [{"file": "smb://nas/wideo/Film.nfo", "type": "movie",
                                         "sudosync_id": "DOM-000001"}]}, f)
            kodi = FakeKodi([_movie(1, "smb://nas/wideo/Film.mkv", "DOM-000001")])
            adapter = KodiJsonRpcAdapter(kodi, nfo_reader=lambda p, m: None,
                                         cache_path=os.path.join(temp_dir, "nfo_id_cache.json"))
            self.assertNotIn("sudosync", adapter.get_movies()[0]["uniqueid"])
            with patch.object(sudosync_core.xbmcvfs, "exists", return_value=True), \
                    patch.object(sudosync_core, "_read_local_json",
                                 return_value={"modified": [], "generated_at": "x"}):
                with self.assertRaises(RuntimeError):
                    sudosync_core.rollback_sudosync_ids(manifest)
                with patch.object(sudosync_core, "_addon_profile_path", return_value=temp_dir):
                    self.assertEqual(sudosync_core.latest_rollback_manifests(), [])

    def test_core_reader_ignores_legacy_type(self):
        raw = _nfo(("sudosync", "DOM-000001"), ("imdb", "tt1")).encode("utf-8")
        has_id, ids = sudosync_core._nfo_raw_has_sudosync_id(raw, "movie")
        self.assertFalse(has_id)
        self.assertEqual(ids, {"imdb": "tt1"})
        has_id, ids = sudosync_core._nfo_raw_has_sudosync_id(
            _nfo(("kodibridge", "abc-def-1")).encode("utf-8"), "movie")
        self.assertTrue(has_id)
        self.assertEqual(ids["sudosync"], "abc-def-1")
        self.assertEqual(sudosync_core._normalize_ids({"sudosync": "DOM-1", "kodibridge": "u-1"}), {"sudosync": "u-1"})


class AuditFollowUpTests(unittest.TestCase):
    def test_uuid_with_episode_qualifier_is_a_valid_id(self):
        from resources.lib.sudosync_v2.identity import normalize_identifier
        value = new_kodibridge_id() + ":s01e02"
        self.assertEqual(normalize_identifier("sudosync", value.upper()), value)

    def test_same_file_key_ignores_credentials_and_unc_form(self):
        from resources.lib.sudosync_v2.snapshot import same_file_key
        key = same_file_key("smb://nas/Wideo/Serial/s01e01.mkv")
        self.assertEqual(same_file_key("smb://user:secret@NAS/wideo/Serial/S01E01.mkv"), key)
        self.assertEqual(same_file_key("\\\\nas\\Wideo\\Serial\\s01e01.mkv"), key)
        self.assertNotEqual(same_file_key("smb://nas/Wideo/Serial/s01e02.mkv"), key)

    def test_repeated_examples_hide_credentials(self):
        from resources.lib.sudosync_v2.snapshot import kodibridge_id_coverage
        movies = [
            {"movieid": 1, "file": "smb://user:secret@nas/Wideo/Film.mkv", "uniqueid": {}},
            {"movieid": 2, "file": "smb://nas/wideo/film.mkv", "uniqueid": {}},
        ]
        coverage = kodibridge_id_coverage(movies, [])
        self.assertEqual(coverage["movie"]["repeated"], 1)
        self.assertNotIn("secret", json.dumps(coverage))

    def test_renumbering_chain_longer_than_eight_steps(self):
        from resources.lib.sudosync_v2.snapshot import _is_renumbered
        chain = {"id-{}".format(n): ["id-{}".format(n + 1)] for n in range(20)}
        self.assertTrue(_is_renumbered(chain, "id-0", "id-20"))
        self.assertFalse(_is_renumbered(chain, "id-0", "id-99"))

    def test_long_unc_path_has_no_dot_segments(self):
        from resources.lib.sudosync_v2.kodi_vfs import _smb_to_unc_parts
        parts = _smb_to_unc_parts("smb://nas/wideo//./Seriale/../Filmy/x.nfo")
        self.assertEqual(parts[3], "\\\\nas\\wideo\\Filmy\\x.nfo")

    def test_cache_is_not_seeded_from_assignment_older_than_a_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, stamp in (("assign_ids_manifest.json", "2026-11-01T10:00:00Z"),
                                ("repair_ids_manifest.json", "2026-11-01T11:00:00Z")):
                with open(os.path.join(temp_dir, name), "w", encoding="utf-8") as f:
                    json.dump({"id_scheme": "kodibridge-uuid", "generated_at": stamp, "modified": [
                        {"file": "smb://nas/wideo/Film.nfo", "type": "movie", "sudosync_id": "old-uuid-1"}]}, f)
            reads = []
            kodi = FakeKodi([_movie(1, "smb://nas/wideo/Film.mkv", "DOM-000001")])
            adapter = KodiJsonRpcAdapter(kodi, nfo_reader=lambda p, m: reads.append(p),
                                         cache_path=os.path.join(temp_dir, "nfo_id_cache.json"))
            self.assertNotIn("sudosync", adapter.get_movies()[0]["uniqueid"])
            self.assertTrue(reads)


class SharedFolderMigrationTests(unittest.TestCase):
    def test_kodibridge_shared_path(self):
        f = sudosync_bootstrap.kodibridge_shared_path
        self.assertEqual(f("smb://nas/wideo/.SudoSync/"), "smb://nas/wideo/.KodiBridge/")
        self.assertEqual(f("smb://nas/wideo/.SudoSync"), "smb://nas/wideo/.KodiBridge/")
        self.assertEqual(f("\\\\NAS\\wideo\\.SudoSync\\"), "\\\\NAS\\wideo\\.KodiBridge/")
        self.assertEqual(f("smb://nas/wideo/.sudosync/"), "smb://nas/wideo/.KodiBridge/")
        self.assertTrue(sudosync_bootstrap._is_shared_dir("smb://nas/wideo/.kodibridge/"))
        self.assertTrue(sudosync_bootstrap._is_shared_dir("\\\\NAS\\wideo\\.SudoSync\\"))
        self.assertFalse(sudosync_bootstrap._is_shared_dir("smb://nas/wideo/"))
        self.assertIsNone(f("smb://nas/wideo/.KodiBridge/"))
        self.assertIsNone(f("smb://nas/wideo/SudoSyncData/"))
        self.assertIsNone(f(""))

    def test_migration_copies_settings_only_and_raises_old_min_version(self):
        old_cfg = {
            "format": "SudoSync configuration", "schema_version": 1,
            "configuration": {"completed": True},
            "network_settings": {"sudosync_id_enabled": True, "sudosync_id_prefix": "DOM",
                                 "sudosync_id_last_number": 3642, "min_addon_version": "2.2.11"},
        }
        written = {}

        def read_cfg(base):
            if base.endswith("/.SudoSync/"):
                return "VALID", json.loads(json.dumps(old_cfg))
            return "MISSING", None

        def write_cfg(base, data):
            written[base] = data
            return True

        with patch.object(sudosync_bootstrap, "read_sudosync_cfg", side_effect=read_cfg), \
                patch.object(sudosync_bootstrap, "write_sudosync_cfg", side_effect=write_cfg), \
                patch.object(sudosync_bootstrap, "update_network_min_version") as raise_min, \
                patch.object(sudosync_bootstrap, "_get_local_addon_version", return_value="2.2.12"):
            ok = sudosync_bootstrap.migrate_legacy_shared_folder(
                "smb://nas/wideo/.SudoSync/", "smb://nas/wideo/.KodiBridge/")
        self.assertTrue(ok)
        new = written["smb://nas/wideo/.KodiBridge/"]
        self.assertTrue(new["configuration"]["completed"])
        self.assertEqual(new["network_settings"], {
            "sudosync_id_enabled": True, "sudosync_id_prefix": "DOM",
            "sudosync_id_last_number": 0, "min_addon_version": "2.2.12",
        })
        raise_min.assert_called_once_with("smb://nas/wideo/.SudoSync/", "2.2.12")

    def test_unc_path_with_trailing_backslash_and_missing_old_configuration(self):
        written = {}
        addon = MagicMock()
        addon.getSetting.side_effect = lambda key: {"sudosync_id_enabled": "true"}.get(key, "")
        with patch.object(sudosync_bootstrap, "read_sudosync_cfg", return_value=("MISSING", None)) as read_cfg, \
                patch.object(sudosync_bootstrap, "write_sudosync_cfg",
                             side_effect=lambda base, data: written.setdefault(base, data) is not None), \
                patch.object(sudosync_bootstrap, "update_network_min_version") as raise_min, \
                patch.object(sudosync_bootstrap.xbmcaddon, "Addon", return_value=addon), \
                patch.object(sudosync_bootstrap, "_get_local_addon_version", return_value="2.2.12"):
            self.assertTrue(sudosync_bootstrap.migrate_legacy_shared_folder(
                "\\\\NAS\\wideo\\.SudoSync\\", "\\\\NAS\\wideo\\.KodiBridge/"))
        read_paths = [call.args[0] for call in read_cfg.call_args_list]
        self.assertIn("\\\\NAS\\wideo\\.SudoSync/", read_paths)
        self.assertEqual(list(written), ["\\\\NAS\\wideo\\.KodiBridge/"])
        self.assertTrue(written["\\\\NAS\\wideo\\.KodiBridge/"]["network_settings"]["sudosync_id_enabled"])
        raise_min.assert_not_called()  # a missing old configuration cannot be raised

    def _bootstrap(self, rw_ok):
        calls = []
        with patch.object(sudosync_bootstrap, "get_base_path", return_value="smb://nas/wideo/.SudoSync/"), \
                patch.object(sudosync_bootstrap, "set_base_path", side_effect=lambda p: calls.append(("set", p))), \
                patch.object(sudosync_bootstrap, "test_vfs_rw", return_value=(rw_ok, "x")), \
                patch.object(sudosync_bootstrap, "release_network_active_kodi_lease",
                             side_effect=lambda p=None: calls.append(("release", p))), \
                patch.object(sudosync_bootstrap, "migrate_legacy_shared_folder",
                             side_effect=lambda old, new: calls.append(("migrate", old, new))), \
                patch.object(sudosync_bootstrap, "read_sudosync_cfg", return_value=("INVALID", None)), \
                patch.object(sudosync_bootstrap.xbmcvfs, "exists", return_value=False):
            sudosync_bootstrap.run_bootstrap()
        return calls

    def test_failed_write_test_keeps_old_folder_for_next_start(self):
        self.assertEqual(self._bootstrap(False), [])

    def test_successful_switch_migrates_before_changing_the_setting(self):
        self.assertEqual(self._bootstrap(True), [
            ("migrate", "smb://nas/wideo/.SudoSync/", "smb://nas/wideo/.KodiBridge/"),
            ("release", "smb://nas/wideo/.SudoSync/"),
            ("set", "smb://nas/wideo/.KodiBridge/"),
        ])

    def test_existing_new_configuration_is_kept(self):
        with patch.object(sudosync_bootstrap, "read_sudosync_cfg",
                          return_value=("VALID", {"network_settings": {}})), \
                patch.object(sudosync_bootstrap, "write_sudosync_cfg") as write_cfg, \
                patch.object(sudosync_bootstrap, "update_network_min_version"), \
                patch.object(sudosync_bootstrap, "_get_local_addon_version", return_value="2.2.12"):
            self.assertFalse(sudosync_bootstrap.migrate_legacy_shared_folder(
                "smb://nas/wideo/.SudoSync/", "smb://nas/wideo/.KodiBridge/"))
        write_cfg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
