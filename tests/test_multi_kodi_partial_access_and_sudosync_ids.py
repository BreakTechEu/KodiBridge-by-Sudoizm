# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for multi-Kodi partial/broader library access, sequential SudoSync IDs, and shared ID counter."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = MagicMock()
if "xbmcaddon" not in sys.modules:
    sys.modules["xbmcaddon"] = MagicMock()
if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = MagicMock()
if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = MagicMock()

SERVICE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "service.sudosync")
)
LIB_DIR = os.path.abspath(os.path.join(SERVICE_DIR, "resources", "lib"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import resources.lib.sudosync_bootstrap as sudosync_bootstrap
import resources.lib.sudosync_core as sudosync_core
from resources.lib.sudosync_merge import build_live_plan
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_v2.merge import plan_field_merge
from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory, build_v2_runtime
from tests.test_audit_v2_service_and_rollback import VirtualXbmcVfs
from tests.test_sudosync_v2_runtime import InMemorySharedBackend


class RichVirtualVfs(VirtualXbmcVfs):
    """Virtual VFS supporting mkdirs, rmdir, copy, and listdir for multi-Kodi NAS tests."""

    def __init__(self):
        super(RichVirtualVfs, self).__init__()
        self.dirs = set()

    def exists(self, path):
        if not path:
            return False
        norm = self._norm(path)
        return norm in self.files or norm.rstrip("/") in self.dirs

    def mkdirs(self, path):
        norm = self._norm(path).rstrip("/")
        if norm in self.dirs and norm.endswith(".lockdir"):
            return False
        self.dirs.add(norm)
        return True

    def rmdir(self, path):
        norm = self._norm(path).rstrip("/")
        if norm in self.dirs:
            self.dirs.remove(norm)
            return True
        return False

    def copy(self, src, dst):
        s_norm = self._norm(src)
        d_norm = self._norm(dst)
        if s_norm not in self.files:
            return False
        self.files[d_norm] = bytes(self.files[s_norm])
        return True

    def listdir(self, path):
        prefix = self._norm(path).rstrip("/") + "/"
        files = []
        dirs = []
        for k in list(self.files.keys()):
            if k.startswith(prefix):
                rest = k[len(prefix):]
                if "/" not in rest:
                    files.append(rest)
        return dirs, sorted(files)


class FakeAddon(object):
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def getSetting(self, key):
        return str(self.store.get(key, ""))

    def setSetting(self, key, value):
        self.store[key] = str(value)

    def getAddonInfo(self, key):
        if key == "version":
            return "2.0.8"
        return ""


class TestMultiKodiPartialAccessAndSudoSyncIds(unittest.TestCase):
    def setUp(self):
        sudosync_core._NFO_ID_CACHE.clear()
        self.vfs = RichVirtualVfs()
        self.base_path = "smb://nas/wideo/.SudoSync/"
        self.vfs.mkdirs(self.base_path)
        self.vfs.mkdirs(self.base_path + "system")
        self.vfs.mkdirs(self.base_path + "clients")
        self.vfs.mkdirs(self.base_path + "live")
        self.local_profiles = {"/profile_kodi1": {}, "/profile_kodi2": {}}
        self.active_profile = "/profile_kodi1"

        # Initial shared SudoSync.cfg and system/config.json on NAS
        cfg_payload = {
            "format": "SudoSync configuration",
            "schema_version": 1,
            "configuration": {"completed": True, "completed_at": "2026-10-18T08:00:00Z"},
            "network_settings": {
                "sudosync_id_enabled": True,
                "sudosync_id_prefix": "DOM",
                "sudosync_id_last_number": 0,
            },
        }
        self.vfs.files[self.base_path + "SudoSync.cfg"] = json.dumps(cfg_payload).encode("utf-8")
        shared_cfg = {
            "format": "SudoSync shared config",
            "schema_version": 1,
            "sudosync_network_id": "DOM",
            "sudosync_network_alias": "DOM",
            "sudosync_id_last_number": 0,
            "network_settings": {
                "sudosync_id_enabled": True,
                "sudosync_id_prefix": "DOM",
                "sudosync_id_last_number": 0,
            },
            "initialization": {
                "completed": True,
                "base_client_id": "11111111-1111-4111-8111-111111111111",
                "base_client_name": "Kodi1",
                "completed_at": "2026-10-18T08:00:00Z",
            },
        }
        self.vfs.files[self.base_path + "system/config.json"] = json.dumps(shared_cfg).encode("utf-8")

    def tearDown(self):
        sudosync_core._NFO_ID_CACHE.clear()

    def _patch_env(self, addon_obj):
        def fake_write_local_json(path, data):
            norm = self.vfs._norm(path)
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.vfs.files[norm] = raw
            self.local_profiles.setdefault(self.active_profile, {})[norm] = json.loads(raw.decode("utf-8"))

        def fake_read_local_json(path, default=None):
            norm = self.vfs._norm(path)
            prof = self.local_profiles.get(self.active_profile, {})
            if norm in prof:
                return prof[norm]
            if norm in self.vfs.files:
                return json.loads(self.vfs.files[norm].decode("utf-8"))
            return default

        return (
            patch.object(sudosync_core.xbmcvfs, "exists", side_effect=self.vfs.exists),
            patch.object(sudosync_core.xbmcvfs, "delete", side_effect=self.vfs.delete),
            patch.object(sudosync_core.xbmcvfs, "rename", side_effect=self.vfs.rename),
            patch.object(sudosync_core.xbmcvfs, "copy", side_effect=self.vfs.copy, create=True),
            patch.object(sudosync_core.xbmcvfs, "mkdirs", side_effect=self.vfs.mkdirs),
            patch.object(sudosync_core.xbmcvfs, "rmdir", side_effect=self.vfs.rmdir, create=True),
            patch.object(sudosync_core.xbmcvfs, "listdir", side_effect=self.vfs.listdir),
            patch.object(sudosync_core.xbmcvfs, "File", side_effect=self.vfs.make_file),
            patch.object(sudosync_core.xbmcaddon, "Addon", return_value=addon_obj),
            patch.object(sudosync_bootstrap.xbmcaddon, "Addon", return_value=addon_obj),
            patch.object(sudosync_core, "_write_local_json", side_effect=fake_write_local_json),
            patch.object(sudosync_core, "_read_local_json", side_effect=fake_read_local_json),
            patch.object(sudosync_core, "_addon_profile_path", side_effect=lambda: self.active_profile),
        )

    def test_01_sequential_indexing_on_kodi1_then_kodi2_with_broader_access(self):
        """After Kodi 1 indexes its subset of videos, Kodi 2 with broader access indexes everything without failure and continues numbering."""
        addon_kodi1 = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Dzieci",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })
        addon_kodi2 = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        # Kodi 1 only has access to 2 kids movies
        kodi1_rpc_movies = [
            {
                "movieid": 1,
                "title": "Bajka Pierwsza",
                "file": "smb://nas/wideo/dzieci/Bajka1.mkv",
                "uniqueid": {},
                "playcount": 1,
                "lastplayed": "2026-10-18 10:00:00",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
            {
                "movieid": 2,
                "title": "Bajka Druga",
                "file": "smb://nas/wideo/dzieci/Bajka2.mkv",
                "uniqueid": {},
                "playcount": 0,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
        ]

        def fake_kodi1_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": kodi1_rpc_movies}
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": []}
            return {}

        self.active_profile = "/profile_kodi1"
        p1 = self._patch_env(addon_kodi1)
        with p1[0], p1[1], p1[2], p1[3], p1[4], p1[5], p1[6], p1[7], p1[8], p1[9], p1[10], p1[11], p1[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_kodi1_rpc):
            res1 = sudosync_core.assign_sudosync_ids(show_notification=False)

        self.assertEqual(res1["movies"], 2)
        self.assertEqual(res1["episodes"], 0)
        self.assertEqual(res1["last_number"], 2)
        self.assertIn(b"DOM-000001", self.vfs.files["smb://nas/wideo/dzieci/Bajka1.nfo"])
        self.assertIn(b"DOM-000002", self.vfs.files["smb://nas/wideo/dzieci/Bajka2.nfo"])

        # Verify shared counter in SudoSync.cfg and system/config.json was updated to 2
        cfg_after_kodi1 = json.loads(self.vfs.files[self.base_path + "SudoSync.cfg"].decode("utf-8"))
        self.assertEqual(cfg_after_kodi1["network_settings"]["sudosync_id_last_number"], 2)
        shared_after_kodi1 = json.loads(self.vfs.files[self.base_path + "system/config.json"].decode("utf-8"))
        self.assertEqual(shared_after_kodi1["sudosync_id_last_number"], 2)

        # Now Kodi 2 (Salon) has broader access:
        # - FilmDorosli1.mkv (appears BEFORE Bajka1 in Kodi 2's list!)
        # - Bajka1.mkv and Bajka2.mkv (already have .nfo with DOM-000001 and DOM-000002 on NAS,
        #   even though Kodi 2's local SQLite DB still reports uniqueid={})
        # - DomoweWideo.mkv (has an existing .nfo with a weak scraper uniqueid type="unknown")
        # - LokalnyTylkoSalon.mkv (exists ONLY on Kodi 2!)
        self.vfs.files["smb://nas/wideo/domowe/DomoweWideo.nfo"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<movie>\n"
            b"  <title>Domowe Wideo</title>\n"
            b'  <uniqueid type="unknown" default="true">local-temp-99</uniqueid>\n'
            b"</movie>\n"
        )

        kodi2_rpc_movies = [
            {
                "movieid": 10,
                "title": "Film Dorosli 1",
                "file": "smb://nas/wideo/dorosli/FilmDorosli1.mkv",
                "uniqueid": {},
                "playcount": 0,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
            {
                "movieid": 11,
                "title": "Bajka Pierwsza",
                "file": "smb://nas/wideo/dzieci/Bajka1.mkv",
                "uniqueid": {},  # Kodi 2 SQLite DB hasn't scanned the NFO yet
                "playcount": 0,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
            {
                "movieid": 12,
                "title": "Bajka Druga",
                "file": "smb://nas/wideo/dzieci/Bajka2.mkv",
                "uniqueid": {},
                "playcount": 0,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
            {
                "movieid": 13,
                "title": "Domowe Wideo",
                "file": "smb://nas/wideo/domowe/DomoweWideo.mkv",
                "uniqueid": {"unknown": "local-temp-99"},
                "playcount": 0,
                "lastplayed": "",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
            {
                "movieid": 14,
                "title": "Nagranie Tylko Na Tym Kodi",
                "file": "special://profile/local_videos/LokalnyTylkoSalon.mkv",
                "uniqueid": {},
                "playcount": 1,
                "lastplayed": "2026-10-18 11:00:00",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            },
        ]

        def fake_kodi2_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": kodi2_rpc_movies}
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": []}
            return {}

        self.active_profile = "/profile_kodi2"
        p2 = self._patch_env(addon_kodi2)
        with p2[0], p2[1], p2[2], p2[3], p2[4], p2[5], p2[6], p2[7], p2[8], p2[9], p2[10], p2[11], p2[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_kodi2_rpc):
            # Preview on Kodi 2 must list only the 3 unindexed movies (skipping Bajka1 and Bajka2)
            preview2 = sudosync_core.preview_sudosync_ids()
            self.assertEqual(len(preview2), 3)

            res2 = sudosync_core.assign_sudosync_ids(show_notification=False)

        self.assertEqual(res2["movies"], 3)
        self.assertEqual(res2["episodes"], 0)
        self.assertEqual(res2["last_number"], 5)

        # Bajka1 and Bajka2 kept their original IDs DOM-000001 and DOM-000002 untouched
        self.assertIn(b"DOM-000001", self.vfs.files["smb://nas/wideo/dzieci/Bajka1.nfo"])
        self.assertIn(b"DOM-000002", self.vfs.files["smb://nas/wideo/dzieci/Bajka2.nfo"])
        self.assertFalse(self.vfs.exists("smb://nas/wideo/dzieci/Bajka1.nfo.bak"))
        self.assertFalse(self.vfs.exists("smb://nas/wideo/dzieci/Bajka2.nfo.bak"))

        # The 3 new items on Kodi 2 received DOM-000003, DOM-000004, DOM-000005
        self.assertIn(b"DOM-000003", self.vfs.files["smb://nas/wideo/dorosli/FilmDorosli1.nfo"])
        self.assertIn(b"DOM-000004", self.vfs.files["smb://nas/wideo/domowe/DomoweWideo.nfo"])
        self.assertIn(b"DOM-000005", self.vfs.files["special://profile/local_videos/LokalnyTylkoSalon.nfo"])

        # Shared config on NAS now reflects sudosync_id_last_number == 5, and Kodi 1 learns it
        cfg_after_kodi2 = json.loads(self.vfs.files[self.base_path + "SudoSync.cfg"].decode("utf-8"))
        self.assertEqual(cfg_after_kodi2["network_settings"]["sudosync_id_last_number"], 5)
        with patch.object(sudosync_bootstrap.xbmcaddon, "Addon", return_value=addon_kodi1):
            sudosync_bootstrap.apply_network_settings(cfg_after_kodi2)
        self.assertEqual(addon_kodi1.getSetting("sudosync_id_last_number"), "5")

    def test_02_auto_assign_sudosync_id_for_local_only_resource_during_snapshot_collection(self):
        """Collecting a snapshot when sudosync_id_enabled=True automatically assigns the next sequential SudoSync ID to new local-only items."""
        addon_kodi1 = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi1",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "10",
        })
        # Update NAS counter to 10
        cfg = json.loads(self.vfs.files[self.base_path + "SudoSync.cfg"].decode("utf-8"))
        cfg["network_settings"]["sudosync_id_last_number"] = 10
        self.vfs.files[self.base_path + "SudoSync.cfg"] = json.dumps(cfg).encode("utf-8")

        rpc_movies = [
            {
                "movieid": 50,
                "title": " Prywatne Nagranie Lokalnego Kodi ",
                "file": "special://profile/videos/PrywatneNagranie.mp4",
                "uniqueid": {},
                "playcount": 1,
                "lastplayed": "2026-10-18 12:00:00",
                "resume": {"position": 0, "total": 0},
                "userrating": 0,
            }
        ]

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": rpc_movies}
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": []}
            return {}

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi1)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_rpc), \
                patch.object(sudosync_core.xbmc, "Player") as mock_player:
            mock_player.return_value.isPlayingVideo.return_value = False
            status = sudosync_core.collect_live_snapshot(show_notification=False)

        self.assertEqual(status["sudosync_id_last_number"], 11)
        self.assertTrue(self.vfs.exists("special://profile/videos/PrywatneNagranie.nfo"))
        self.assertIn(b"DOM-000011", self.vfs.files["special://profile/videos/PrywatneNagranie.nfo"])

        # Snapshot written to NAS already contains the newly assigned sudosync ID
        snap_data = json.loads(self.vfs.files[status["remote_path"]].decode("utf-8"))
        self.assertEqual(snap_data["movies"][0]["ids"]["sudosync"], "DOM-000011")

    def test_03_v2_kodi_rpc_enriches_details_with_nfo_ids_preventing_transaction_error(self):
        """KodiJsonRpcAdapter.get_movie_details and get_episode_details enrich uniqueid from NFO so v2 TransactionRunner succeeds even when Kodi SQLite lacks the NFO ID."""
        movie_state = {
            "movieid": 7,
            "title": "Bajka Pierwsza",
            "year": 2024,
            "file": "smb://nas/wideo/dzieci/Bajka1.mkv",
            "imdbnumber": "",
            "uniqueid": {},  # SQLite has not imported the NFO ID yet
            "playcount": 0,
            "lastplayed": "",
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": 0,
        }

        def fake_execute_jsonrpc(payload_str):
            req = json.loads(payload_str)
            method = req["method"]
            req_id = req["id"]
            if method == "Player.GetActivePlayers":
                return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": []})
            if method == "VideoLibrary.GetMovies":
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"movies": [dict(movie_state)], "limits": {"total": 1}},
                })
            if method == "VideoLibrary.GetEpisodes":
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"episodes": [], "limits": {"total": 0}},
                })
            if method == "VideoLibrary.GetMovieDetails":
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"moviedetails": dict(movie_state)},
                })
            if method == "VideoLibrary.SetMovieDetails":
                params = req.get("params") or {}
                for field in ("playcount", "lastplayed", "resume", "userrating"):
                    if field in params:
                        movie_state[field] = params[field]
                return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": "OK"})
            raise AssertionError("Unexpected RPC method: " + method)

        def fake_nfo_reader(nfo_path, max_nfo_bytes):
            if nfo_path == "smb://nas/wideo/dzieci/Bajka1.nfo":
                return (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    "<movie>\n"
                    "  <title>Bajka 1</title>\n"
                    '  <uniqueid type="sudosync" default="true">DOM-000001</uniqueid>\n'
                    "</movie>\n"
                )
            return None

        backend = InMemorySharedBackend()
        lock_factory = ProcessLocalLockFactory()
        client_a = "11111111-1111-4111-8111-111111111111"
        client_b = "22222222-2222-4222-8222-222222222222"

        # Client A already watched Bajka1 (sudosync: dom-000001)
        adapter_a_state = dict(movie_state, playcount=2, lastplayed="2026-10-18 15:00:00")
        adapter_a = KodiJsonRpcAdapter(
            lambda payload: json.dumps({
                "jsonrpc": "2.0",
                "id": json.loads(payload)["id"],
                "result": (
                    [] if json.loads(payload)["method"] == "Player.GetActivePlayers"
                    else {"movies": [dict(adapter_a_state)], "limits": {"total": 1}}
                    if json.loads(payload)["method"] == "VideoLibrary.GetMovies"
                    else {"episodes": [], "limits": {"total": 0}}
                ),
            }),
            nfo_reader=fake_nfo_reader,
        )
        runtime_a = build_v2_runtime(adapter_a, backend, "smb://nas/SudoSync", lock_factory, client_a, "Kodi A")
        runtime_a.publish_local_snapshot()

        # Client B has Bajka1 with uniqueid={} in SQLite, enriched only via fake_nfo_reader
        adapter_b = KodiJsonRpcAdapter(fake_execute_jsonrpc, nfo_reader=fake_nfo_reader)
        runtime_b = build_v2_runtime(adapter_b, backend, "smb://nas/SudoSync", lock_factory, client_b, "Kodi B")
        cycle_res = runtime_b.sync_cycle()

        self.assertEqual(cycle_res["status"], "committed")
        self.assertEqual(cycle_res["applied_count"], 1)
        self.assertEqual(movie_state["playcount"], 2)
        self.assertEqual(movie_state["lastplayed"], "2026-10-18 15:00:00")

    def test_04_initial_lc0_watched_vs_newly_indexed_unwatched_merges_without_conflict(self):
        """When one Kodi has watched state at lc=0 and a second Kodi newly indexes the item with unwatched state at lc=0, both v2 and v1 merge planners adopt the watched state."""
        records_v2 = [
            {
                "state": {
                    "playcount": 3,
                    "lastplayed": "2026-10-18 14:00:00",
                    "resume": {"position": 180.0, "total": 1200.0},
                    "userrating": 8,
                },
                "versions": {
                    "playcount": {"lc": 0, "seq": 0, "client_id": "kodi-1"},
                    "lastplayed": {"lc": 0, "seq": 0, "client_id": "kodi-1"},
                    "resume": {"lc": 0, "seq": 0, "client_id": "kodi-1"},
                    "userrating": {"lc": 0, "seq": 0, "client_id": "kodi-1"},
                },
            },
            {
                "state": {
                    "playcount": 0,
                    "lastplayed": "",
                    "resume": {"position": 0.0, "total": 0.0},
                    "userrating": None,
                },
                "versions": {
                    "playcount": {"lc": 0, "seq": 0, "client_id": "kodi-2"},
                    "lastplayed": {"lc": 0, "seq": 0, "client_id": "kodi-2"},
                    "resume": {"lc": 0, "seq": 0, "client_id": "kodi-2"},
                    "userrating": {"lc": 0, "seq": 0, "client_id": "kodi-2"},
                },
            },
        ]
        merged_v2 = plan_field_merge(records_v2)
        self.assertEqual(merged_v2["conflicts"], [])
        self.assertEqual(merged_v2["target_state"]["playcount"], 3)
        self.assertEqual(merged_v2["target_state"]["lastplayed"], "2026-10-18 14:00:00")
        self.assertEqual(merged_v2["target_state"]["resume"], {"position": 180.0, "total": 1200.0})
        self.assertEqual(merged_v2["target_state"]["userrating"], 8)

        # Verify v1 build_live_plan with identical baseline versions (lc=0, seq=0)
        baseline_ver = {"lc": 0, "ts": "2026-10-18T08:00:00.000Z", "client_id": "baseline", "seq": 0}
        snapshots_v1 = [
            {
                "client": {"id": "kodi-1", "name": "Kodi 1"},
                "movies": [
                    {
                        "type": "movie",
                        "title": "Bajka 1",
                        "file": "smb://nas/Bajka1.mkv",
                        "ids": {"sudosync": "DOM-000001"},
                        "local": {"movieid": 1},
                        "state": {
                            "playcount": 3,
                            "lastplayed": "2026-10-18 14:00:00",
                            "resume": {"position": 0.0, "total": 0.0},
                            "userrating": 8,
                        },
                        "field_versions": {f: dict(baseline_ver) for f in ("playcount", "lastplayed", "resume", "userrating")},
                    }
                ],
                "episodes": [],
            },
            {
                "client": {"id": "kodi-2", "name": "Kodi 2"},
                "movies": [
                    {
                        "type": "movie",
                        "title": "Bajka 1",
                        "file": "smb://nas/Bajka1.mkv",
                        "ids": {"sudosync": "DOM-000001"},
                        "local": {"movieid": 10},
                        "state": {
                            "playcount": 0,
                            "lastplayed": "",
                            "resume": {"position": 0.0, "total": 0.0},
                            "userrating": 0,
                        },
                        "field_versions": {f: dict(baseline_ver) for f in ("playcount", "lastplayed", "resume", "userrating")},
                    }
                ],
                "episodes": [],
            },
        ]
        plan_v1 = build_live_plan(snapshots_v1)
        self.assertEqual(plan_v1["field_conflicts"], [])
        kodi2_changes = plan_v1["planned_changes"]["kodi-2"]
        self.assertEqual(len(kodi2_changes), 1)
        self.assertEqual(kodi2_changes[0]["changes"]["playcount"]["to"], 3)
        self.assertEqual(kodi2_changes[0]["changes"]["lastplayed"]["to"], "2026-10-18 14:00:00")
        self.assertEqual(kodi2_changes[0]["changes"]["userrating"]["to"], 8)

    def test_05_assign_sudosync_id_to_items_with_existing_external_ids_and_skip_existing_sudosync(self):
        """SudoSync ID is assigned unconditionally to all items missing sudosync ID (preserving external IDs), and skips items already possessing sudosync ID."""
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        # Film A: has IMDb and TMDb in NFO and Kodi
        self.vfs.files["smb://nas/wideo/filmy/FilmA.nfo"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<movie>\n"
            b"  <title>Film z IMDb</title>\n"
            b'  <uniqueid type="imdb" default="true">tt1234567</uniqueid>\n'
            b'  <uniqueid type="tmdb">99999</uniqueid>\n'
            b"</movie>\n"
        )
        # Film B: already has sudosync ID DOM-000007 in NFO
        self.vfs.files["smb://nas/wideo/filmy/FilmB.nfo"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<movie>\n"
            b"  <title>Film z istniejacym SudoSync</title>\n"
            b'  <uniqueid type="imdb">tt7654321</uniqueid>\n'
            b'  <uniqueid type="sudosync" default="true">DOM-000007</uniqueid>\n'
            b"</movie>\n"
        )
        # Episode C: has TVDb without default="true"
        self.vfs.files["smb://nas/wideo/seriale/EpisodeC.nfo"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<episodedetails>\n"
            b"  <title>Odcinek z TVDb</title>\n"
            b'  <uniqueid type="tvdb">112233</uniqueid>\n'
            b"</episodedetails>\n"
        )

        rpc_movies = [
            {
                "movieid": 101,
                "title": "Film z IMDb",
                "file": "smb://nas/wideo/filmy/FilmA.mkv",
                "uniqueid": {"imdb": "tt1234567", "tmdb": "99999"},
            },
            {
                "movieid": 102,
                "title": "Film z istniejacym SudoSync",
                "file": "smb://nas/wideo/filmy/FilmB.mkv",
                "uniqueid": {"imdb": "tt7654321", "sudosync": "DOM-000007"},
            },
        ]
        rpc_episodes = [
            {
                "episodeid": 201,
                "title": "Odcinek z TVDb",
                "file": "smb://nas/wideo/seriale/EpisodeC.mkv",
                "uniqueid": {"tvdb": "112233"},
            }
        ]

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": rpc_movies}
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": rpc_episodes}
            return {}

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_rpc):
            # Preview must list Film A and Episode C, but NOT Film B
            preview = sudosync_core.preview_sudosync_ids()
            self.assertEqual(len(preview), 2)
            titles = [x["title"] for x in preview]
            self.assertIn("Film z IMDb", titles)
            self.assertIn("Odcinek z TVDb", titles)
            self.assertNotIn("Film z istniejacym SudoSync", titles)

            # Assign SudoSync IDs
            res = sudosync_core.assign_sudosync_ids(show_notification=False)

        self.assertEqual(res["movies"], 1)
        self.assertEqual(res["episodes"], 1)
        self.assertEqual(res["last_number"], 9)

        # Verify Film A: IMDb and TMDb are preserved, sudosync ID DOM-000008 is added with default="false"
        nfo_a = self.vfs.files["smb://nas/wideo/filmy/FilmA.nfo"].decode("utf-8")
        self.assertIn('tt1234567', nfo_a)
        self.assertIn('99999', nfo_a)
        self.assertIn('<uniqueid type="sudosync" default="false">DOM-000008</uniqueid>', nfo_a)

        # Verify Film B: unchanged DOM-000007
        nfo_b = self.vfs.files["smb://nas/wideo/filmy/FilmB.nfo"].decode("utf-8")
        self.assertIn('DOM-000007', nfo_b)
        self.assertNotIn('DOM-000008', nfo_b)
        self.assertNotIn('DOM-000009', nfo_b)

        # Verify Episode C: TVDb preserved, sudosync ID DOM-000009 added with default="true"
        nfo_c = self.vfs.files["smb://nas/wideo/seriale/EpisodeC.nfo"].decode("utf-8")
        self.assertIn('112233', nfo_c)
        self.assertIn('<uniqueid type="sudosync" default="true">DOM-000009</uniqueid>', nfo_c)

    def test_06_default_assignids_uses_foreground_progress_dialog_with_auto_close_notice(self):
        """Action 'assignids' displays a foreground DialogProgress with auto-close note and progresses."""
        import default
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        mock_dialog = MagicMock()
        mock_progress = MagicMock()
        mock_progress.iscanceled.return_value = False

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(default.xbmcgui, "Dialog", return_value=mock_dialog), \
                patch.object(default.xbmcgui, "DialogProgress", return_value=mock_progress), \
                patch.object(sudosync_core, "collect_library", return_value=([], [])), \
                patch.object(sys, "argv", ["default.py", "assignids"]):
            default.main()

        # DialogProgress created with title and message containing auto-close note
        mock_progress.create.assert_called_once()
        create_args = mock_progress.create.call_args[0]
        self.assertIn("SudoSync", create_args[0])
        self.assertTrue("zamknie się automatycznie" in create_args[1] or "close automatically" in create_args[1])

        # DialogProgress is closed cleanly
        mock_progress.close.assert_called()

        # Final OK dialog informs user no new files need assignment
        mock_dialog.ok.assert_called_once()
        self.assertIn("Nie znaleziono żadnych nowych plików", mock_dialog.ok.call_args[0][1])

    def test_06b_default_assignids_full_execution_two_phases_single_dialog(self):
        """Action 'assignids' reuses a single DialogProgress across both Phase 1 and Phase 2 and auto-closes."""
        import default
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        mock_dialog = MagicMock()
        mock_progress = MagicMock()
        mock_progress.iscanceled.return_value = False
        updates = []
        mock_progress.update.side_effect = lambda pct, msg="": updates.append((pct, msg))

        # Prepare fake library items needing IDs
        movie_item = {
            "movieid": 1,
            "title": "Test Movie",
            "file": "smb://nas/wideo/filmy/TestMovie.mkv",
            "year": 2024,
            "uniqueid": {},
        }
        self.vfs.files["smb://nas/wideo/filmy/TestMovie.nfo"] = b"<movie><title>Test Movie</title></movie>"

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(default.xbmcgui, "Dialog", return_value=mock_dialog), \
                patch.object(default.xbmcgui, "DialogProgress", return_value=mock_progress), \
                patch.object(sudosync_core, "collect_library", return_value=([movie_item], [])), \
                patch.object(sudosync_core, "rpc", side_effect=lambda method, params=None: {}), \
                patch.object(sys, "argv", ["default.py", "assignids"]):
            default.main()

        # DialogProgress created exactly once
        mock_progress.create.assert_called_once()

        # Updates occurred for both Phase 1 and Phase 2 on the same dialog instance
        self.assertTrue(len(updates) >= 2)
        phase1_updates = [u for u in updates if "Odczyt" in u[1] or "Skanowanie" in u[1] or "Scanning" in u[1]]
        phase2_updates = [u for u in updates if "Przypisywanie" in u[1] or "Nadawanie" in u[1] or "Assigning" in u[1]]
        self.assertTrue(len(phase1_updates) >= 1)
        self.assertTrue(len(phase2_updates) >= 1)

        # In all updates, the auto-close notice is present
        for _, msg in updates:
            self.assertTrue("zamknie się automatycznie" in msg or "close automatically" in msg)

        # Dialog closed cleanly
        mock_progress.close.assert_called()

        # Final OK report dialog is displayed
        mock_dialog.ok.assert_called_once()
        self.assertIn("Raport z operacji", mock_dialog.ok.call_args[0][1])

    def test_06c_default_assignids_user_cancellation_phase_1(self):
        """User cancels during Phase 1: Phase 2 is skipped, dialog closes, no files written."""
        import default
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        mock_dialog = MagicMock()
        mock_progress = MagicMock()
        # User clicks Cancel during Phase 1
        mock_progress.iscanceled.return_value = True

        movie_item = {
            "movieid": 1,
            "title": "Movie 1",
            "file": "smb://nas/wideo/filmy/Movie1.mkv",
            "year": 2024,
            "uniqueid": {},
        }
        self.vfs.files["smb://nas/wideo/filmy/Movie1.nfo"] = b"<movie><title>Movie 1</title></movie>"

        assign_mock = MagicMock()

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(default.xbmcgui, "Dialog", return_value=mock_dialog), \
                patch.object(default.xbmcgui, "DialogProgress", return_value=mock_progress), \
                patch.object(sudosync_core, "collect_library", return_value=([movie_item], [])), \
                patch.object(sudosync_core, "assign_sudosync_ids", assign_mock), \
                patch.object(sys, "argv", ["default.py", "assignids"]):
            default.main()

        # Phase 2 (assign_sudosync_ids) was NOT called
        assign_mock.assert_not_called()
        # Dialog closed in finally
        mock_progress.close.assert_called()
        # No completion OK dialog shown
        mock_dialog.ok.assert_not_called()

    def test_06d_default_assignids_user_cancellation_phase_2(self):
        """User cancels during Phase 2: loop finishes current atomic write, safely breaks, releases locks, marks cancelled."""
        import default
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        # Provide 2 movie items
        movie1 = {
            "movieid": 1,
            "title": "Movie 1",
            "file": "smb://nas/wideo/filmy/Movie1.mkv",
            "year": 2024,
            "uniqueid": {},
        }
        movie2 = {
            "movieid": 2,
            "title": "Movie 2",
            "file": "smb://nas/wideo/filmy/Movie2.mkv",
            "year": 2024,
            "uniqueid": {},
        }
        self.vfs.files["smb://nas/wideo/filmy/Movie1.nfo"] = b"<movie><title>Movie 1</title></movie>"
        self.vfs.files["smb://nas/wideo/filmy/Movie2.nfo"] = b"<movie><title>Movie 2</title></movie>"

        mock_dialog = MagicMock()
        mock_progress = MagicMock()

        calls = {"count": 0}
        def fake_iscanceled():
            calls["count"] += 1
            # Return False for Phase 1 and beginning of Phase 2, but True after Movie 1 is processed
            return calls["count"] > 3

        mock_progress.iscanceled.side_effect = fake_iscanceled

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(default.xbmcgui, "Dialog", return_value=mock_dialog), \
                patch.object(default.xbmcgui, "DialogProgress", return_value=mock_progress), \
                patch.object(sudosync_core, "collect_library", return_value=([movie1, movie2], [])), \
                patch.object(sudosync_core, "rpc", side_effect=lambda method, params=None: {}), \
                patch.object(sys, "argv", ["default.py", "assignids"]):
            default.main()

        # Lock is released
        self.assertFalse(sudosync_core._ASSIGN_IDS_LOCK_HELD)
        # Dialog closed in finally
        mock_progress.close.assert_called()
        # Normal completion OK dialog NOT shown when cancelled
        mock_dialog.ok.assert_not_called()

        # Movie 2 was NOT assigned because cancellation safely aborted before it
        nfo_2 = self.vfs.files["smb://nas/wideo/filmy/Movie2.nfo"].decode("utf-8")
        self.assertNotIn("sudosync", nfo_2)


    def test_07_assign_sudosync_ids_crash_mid_loop_preserves_counter_for_other_kodi(self):
        """Failure or crash while writing 2nd film NFO still preserves pre-persisted counter >= 1 on NAS so 2nd Kodi avoids ID collisions."""
        addon_kodi1 = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi 1",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })
        addon_kodi2 = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi 2",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })

        # Kodi 1 media
        self.vfs.files["smb://nas/wideo1/Film1.nfo"] = b"<movie><title>Film 1</title></movie>\n"
        self.vfs.files["smb://nas/wideo1/Film2.nfo"] = b"<movie><title>Film 2</title></movie>\n"
        # Kodi 2 media (separate share)
        self.vfs.files["smb://nas/wideo2/Film3.nfo"] = b"<movie><title>Film 3</title></movie>\n"

        rpc_movies_kodi1 = [
            {"movieid": 1, "title": "Film 1", "file": "smb://nas/wideo1/Film1.mkv", "uniqueid": {}},
            {"movieid": 2, "title": "Film 2", "file": "smb://nas/wideo1/Film2.mkv", "uniqueid": {}},
        ]
        rpc_movies_kodi2 = [
            {"movieid": 3, "title": "Film 3", "file": "smb://nas/wideo2/Film3.mkv", "uniqueid": {}},
        ]

        def fake_rpc_kodi1(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": rpc_movies_kodi1}
            return {}

        def fake_rpc_kodi2(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": rpc_movies_kodi2}
            return {}

        orig_write_bytes = sudosync_core._write_vfs_bytes

        def failing_write_bytes(path, payload):
            if "Film2" in path:
                raise IOError("Simulated crash/disk error on Film 2 NFO write")
            return orig_write_bytes(path, payload)

        # 1. Run Kodi 1 with failure on Film 2
        self.active_profile = "/profile_kodi1"
        p1 = self._patch_env(addon_kodi1)
        with p1[0], p1[1], p1[2], p1[3], p1[4], p1[5], p1[6], p1[7], p1[8], p1[9], p1[10], p1[11], p1[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_rpc_kodi1), \
                patch.object(sudosync_core, "_write_vfs_bytes", side_effect=failing_write_bytes):
            res_kodi1 = sudosync_core.assign_sudosync_ids(show_notification=False)

        # Film 1 was successfully modified, Film 2 failed during write
        self.assertEqual(res_kodi1["movies"], 1)

        # Verify counter was pre-persisted on NAS (SudoSync.cfg and system/config.json >= 1)
        cfg_raw = json.loads(self.vfs.files[self.base_path + "SudoSync.cfg"].decode("utf-8"))
        cfg_num = cfg_raw.get("network_settings", {}).get("sudosync_id_last_number", 0)
        self.assertGreaterEqual(cfg_num, 1)

        sys_raw = json.loads(self.vfs.files[self.base_path + "system/config.json"].decode("utf-8"))
        sys_num = sys_raw.get("sudosync_id_last_number", 0)
        self.assertGreaterEqual(sys_num, 1)

        # Film 1 has DOM-000001
        nfo_1 = self.vfs.files["smb://nas/wideo1/Film1.nfo"].decode("utf-8")
        self.assertIn("DOM-000001", nfo_1)

        # 2. Run Kodi 2 (does not see wideo1 share)
        self.active_profile = "/profile_kodi2"
        p2 = self._patch_env(addon_kodi2)
        with p2[0], p2[1], p2[2], p2[3], p2[4], p2[5], p2[6], p2[7], p2[8], p2[9], p2[10], p2[11], p2[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_rpc_kodi2):
            res_kodi2 = sudosync_core.assign_sudosync_ids(show_notification=False)

        self.assertEqual(res_kodi2["movies"], 1)

        # Film 3 receives candidate number > cfg_num (no collision with DOM-000001 or reserved DOM-000002)
        nfo_3 = self.vfs.files["smb://nas/wideo2/Film3.nfo"].decode("utf-8")
        self.assertNotIn("DOM-000001", nfo_3)
        self.assertTrue("DOM-000002" in nfo_3 or "DOM-000003" in nfo_3)
        # End result on NAS reflects the newly assigned ID
        cfg_final = json.loads(self.vfs.files[self.base_path + "SudoSync.cfg"].decode("utf-8"))
        self.assertGreater(cfg_final.get("network_settings", {}).get("sudosync_id_last_number", 0), cfg_num)

    def test_08_assign_sudosync_ids_concurrency_lock_blocks_parallel_run(self):
        """When concurrency lock is held, parallel assign_sudosync_ids (auto and manual) safely returns 0, and finally block releases lock."""
        addon_kodi = FakeAddon({
            "base_path": self.base_path,
            "client_name": "Kodi Salon",
            "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM",
            "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "0",
        })
        self.vfs.files["smb://nas/wideo/FilmA.nfo"] = b"<movie><title>Film A</title></movie>\n"
        rpc_movies = [{"movieid": 10, "title": "Film A", "file": "smb://nas/wideo/FilmA.mkv", "uniqueid": {}}]

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetMovies":
                return {"movies": rpc_movies}
            return {}

        mock_dialog = MagicMock()
        mock_dialog_inst = MagicMock()
        mock_dialog.return_value = mock_dialog_inst

        self.active_profile = "/profile_kodi1"
        p = self._patch_env(addon_kodi)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9], p[10], p[11], p[12], \
                patch.object(sudosync_core, "rpc", side_effect=fake_rpc), \
                patch.object(sudosync_core.xbmcgui, "Dialog", mock_dialog):

            # Explicitly acquire lock to simulate ongoing assignment in another process/service
            acquired = sudosync_core._acquire_assign_ids_lock(self.base_path)
            self.assertTrue(acquired)
            self.assertFalse(sudosync_core._acquire_assign_ids_lock(self.base_path))

            # auto_mode=True returns 0 without raising or modifying
            auto_res = sudosync_core.assign_sudosync_ids(show_notification=False, auto_mode=True)
            self.assertEqual(auto_res, 0)
            self.assertNotIn("DOM-000001", self.vfs.files["smb://nas/wideo/FilmA.nfo"].decode("utf-8"))

            # auto_mode=False returns 0 and triggers notification
            manual_res = sudosync_core.assign_sudosync_ids(show_notification=True, auto_mode=False)
            self.assertEqual(manual_res, 0)
            mock_dialog_inst.notification.assert_called()

            # Release lock
            sudosync_core._release_assign_ids_lock(self.base_path)
            self.assertFalse(sudosync_core._ASSIGN_IDS_LOCK_HELD)

            # Now assign_sudosync_ids can run normally and releases its lock in finally
            normal_res = sudosync_core.assign_sudosync_ids(show_notification=False, auto_mode=False)
            self.assertIsInstance(normal_res, dict)
            self.assertEqual(normal_res["movies"], 1)
            self.assertFalse(sudosync_core._ASSIGN_IDS_LOCK_HELD)

            # Test that an exception inside assign_sudosync_ids still releases the lock in finally
            with patch.object(sudosync_core, "collect_library", side_effect=RuntimeError("RPC error")):
                with self.assertRaises(RuntimeError):
                    sudosync_core.assign_sudosync_ids(show_notification=False)
            self.assertFalse(sudosync_core._ASSIGN_IDS_LOCK_HELD)


if __name__ == "__main__":
    unittest.main()


