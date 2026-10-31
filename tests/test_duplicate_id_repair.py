# -*- coding: utf-8 -*-
"""Repair of duplicate KodiBridge IDs: renumbering, rollback and network notice."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
LIB_DIR = os.path.join(SERVICE_DIR, "resources", "lib")
for _path in (SERVICE_DIR, LIB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import resources.lib.sudosync_bootstrap as sudosync_bootstrap  # noqa: E402
import resources.lib.sudosync_core as sudosync_core  # noqa: E402
from resources.lib.sudosync_v2.snapshot import SnapshotBuildError, build_snapshot  # noqa: E402
from tests.test_multi_kodi_partial_access_and_sudosync_ids import FakeAddon, RichVirtualVfs  # noqa: E402

BASE = "smb://nas/wideo/.SudoSync/"
PROFILE = "/profile_kodi3"
CLIENT = "33333333-3333-4333-8333-333333333333"


def _episode_nfo(entries):
    body = b"".join(
        b"<episodedetails>\n  <title>" + title + b"</title>\n  <season>" + season + b"</season>\n"
        b"  <episode>" + episode + b"</episode>\n  <uniqueid type=\"tvdb\">" + tvdb + b"</uniqueid>\n"
        b"  <uniqueid type=\"sudosync\" default=\"false\">" + sid + b"</uniqueid>\n</episodedetails>\n"
        for title, season, episode, tvdb, sid in entries
    )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body


class DuplicateRepairTests(unittest.TestCase):
    def setUp(self):
        sudosync_core._NFO_ID_CACHE.clear()
        sudosync_core._SEEN_NFO_ID_EPOCH["value"] = None
        self.vfs = RichVirtualVfs()
        for d in ("", "system", "clients"):
            self.vfs.mkdirs(BASE + d)
        self.vfs.files[BASE + "SudoSync.cfg"] = json.dumps({
            "format": "SudoSync configuration", "schema_version": 1,
            "network_settings": {"sudosync_id_enabled": True, "sudosync_id_prefix": "DOM",
                                 "sudosync_id_last_number": 9},
        }).encode("utf-8")
        self.vfs.files[BASE + "system/config.json"] = json.dumps({
            "format": "SudoSync shared config", "schema_version": 1,
            "sudosync_network_id": "DOM", "sudosync_network_alias": "DOM", "sudosync_id_last_number": 9,
            "network_settings": {"sudosync_id_enabled": True, "sudosync_id_prefix": "DOM",
                                 "sudosync_id_last_number": 9},
            "initialization": {"completed": True, "base_client_id": CLIENT, "base_client_name": "Kodi3"},
        }).encode("utf-8")
        nas = "smb://nas/wideo/seriale/"
        self.vfs.files[nas + "A/a1.nfo"] = _episode_nfo([(b"A1", b"1", b"1", b"111", b"DOM-000005")])
        self.vfs.files[nas + "B/b3.nfo"] = _episode_nfo([(b"B3", b"2", b"3", b"222", b"DOM-000005")])
        self.vfs.files[nas + "M/m12.nfo"] = _episode_nfo([
            (b"M1", b"1", b"1", b"301", b"DOM-000007"), (b"M2", b"1", b"2", b"302", b"DOM-000007")])
        self.episodes = [
            {"episodeid": 1, "title": "A1", "showtitle": "Show A", "season": 1, "episode": 1,
             "file": nas + "A/a1.mkv", "uniqueid": {"tvdb": "111"}},
            {"episodeid": 2, "title": "B3", "showtitle": "Show B", "season": 2, "episode": 3,
             "file": nas + "B/b3.mkv", "uniqueid": {"tvdb": "222"}},
            {"episodeid": 3, "title": "M1", "showtitle": "Show M", "season": 1, "episode": 1,
             "file": nas + "M/m12.mkv", "uniqueid": {"tvdb": "301"}},
            {"episodeid": 4, "title": "M2", "showtitle": "Show M", "season": 1, "episode": 2,
             "file": nas + "M/m12.mkv", "uniqueid": {"tvdb": "302"}},
        ]
        self.addon = FakeAddon({
            "base_path": BASE, "client_name": "Kodi3", "sudosync_id_enabled": "true",
            "sudosync_id_prefix": "DOM", "sudosync_id_prefix_last_synced": "DOM",
            "sudosync_id_last_number": "9",
        })
        self.tmp = tempfile.mkdtemp()
        self.profile = PROFILE

    def tearDown(self):
        sudosync_core._NFO_ID_CACHE.clear()
        sudosync_core._SEEN_NFO_ID_EPOCH["value"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, stack):
        vfs = self.vfs

        def write_local(path, data):
            vfs.files[vfs._norm(path)] = json.dumps(data, ensure_ascii=False).encode("utf-8")

        def read_local(path, default=None):
            norm = vfs._norm(path)
            return json.loads(vfs.files[norm].decode("utf-8")) if norm in vfs.files else default

        def fake_rpc(method, params=None):
            if method == "VideoLibrary.GetEpisodes":
                return {"episodes": [dict(e) for e in self.episodes]}
            if method == "VideoLibrary.GetMovies":
                return {"movies": []}
            return {}

        x = sudosync_core.xbmcvfs
        for name in ("exists", "delete", "rename", "copy", "mkdirs", "rmdir", "listdir"):
            stack.enter_context(patch.object(x, name, side_effect=getattr(vfs, name)))
        stack.enter_context(patch.object(x, "File", side_effect=vfs.make_file))
        stack.enter_context(patch.object(sudosync_core.xbmcaddon, "Addon", return_value=self.addon))
        stack.enter_context(patch.object(sudosync_bootstrap.xbmcaddon, "Addon", return_value=self.addon))
        stack.enter_context(patch.object(sudosync_core, "_write_local_json", side_effect=write_local))
        stack.enter_context(patch.object(sudosync_core, "_read_local_json", side_effect=read_local))
        stack.enter_context(patch.object(sudosync_core, "_addon_profile_path", side_effect=lambda: self.profile))
        stack.enter_context(patch.object(sudosync_core, "get_or_create_client_id", return_value=CLIENT))
        return stack.enter_context(patch.object(sudosync_core, "rpc", side_effect=fake_rpc))

    def _snapshot_knows(self, tvdb):
        self.vfs.files[BASE + "clients/client-11111111-1111-4111-8111-111111111111.json"] = json.dumps({
            "movies": [], "episodes": [{"type": "episode", "ids": {"sudosync": "dom-000005", "tvdb": tvdb}}],
        }).encode("utf-8")

    def _repair(self):
        with ExitStack() as stack:
            rpc = self._env(stack)
            result = sudosync_core.repair_duplicate_sudosync_ids()
        return result, rpc

    def _shared(self):
        return json.loads(self.vfs.files[BASE + "system/config.json"].decode("utf-8"))

    def test_groups_skip_multi_episode_file(self):
        with ExitStack() as stack:
            self._env(stack)
            movies, episodes = sudosync_core.collect_library(first_import=True)
            groups = sudosync_core.find_duplicate_sudosync_id_groups(movies, episodes)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["sudosync_id"], "DOM-000005")
        self.assertEqual([e["file"].rsplit("/", 1)[-1] for e in groups[0]["files"]], ["a1.mkv", "b3.mkv"])

    def test_item_known_on_network_keeps_number(self):
        self._snapshot_knows("222")  # Show B is synchronized under DOM-000005
        result, rpc = self._repair()
        a1 = self.vfs.files["smb://nas/wideo/seriale/A/a1.nfo"]
        self.assertEqual(result["renumbered"], 1)
        self.assertIn(b">DOM-000010</uniqueid>", a1)
        self.assertNotIn(b"DOM-000005", a1)
        self.assertIn(b">DOM-000005</uniqueid>", self.vfs.files["smb://nas/wideo/seriale/B/b3.nfo"])
        self.assertIn(b"DOM-000005", self.vfs.files["smb://nas/wideo/seriale/A/a1.nfo.repair.bak"])
        rpc.assert_any_call("VideoLibrary.RefreshEpisode", {"episodeid": 1})
        shared = self._shared()
        self.assertEqual(shared["sudosync_id_last_number"], 10)
        self.assertEqual(shared["kodibridge_id_renumbered"], {"dom-000005": ["dom-000010"]})
        self.assertTrue(shared["nfo_id_epoch"])
        manifest = json.loads(self.vfs.files[PROFILE + "/repair_ids_manifest.json"].decode("utf-8"))
        self.assertEqual(manifest["modified"][0]["old_sudosync_id"], "DOM-000005")
        self.assertIn("Show B S02E03", result["kept"][0])

    def test_without_network_knowledge_first_path_keeps_number(self):
        self._snapshot_knows("999")
        result, _ = self._repair()
        self.assertIn(b">DOM-000005</uniqueid>", self.vfs.files["smb://nas/wideo/seriale/A/a1.nfo"])
        self.assertIn(b">DOM-000010</uniqueid>", self.vfs.files["smb://nas/wideo/seriale/B/b3.nfo"])
        self.assertEqual(result["changes"], ["DOM-000005 → DOM-000010 — Show B S02E03"])

    def test_rollback_restores_original_nfo(self):
        original = self.vfs.files["smb://nas/wideo/seriale/B/b3.nfo"]
        self._repair()
        with ExitStack() as stack:
            self._env(stack)
            manifests = sudosync_core.latest_rollback_manifests()
            self.assertEqual(manifests[0][0], "repair")
            res = sudosync_core.rollback_sudosync_ids(manifest_path=manifests[0][1])
            pairs = sudosync_core.renumbered_pairs_from_manifest(manifests[0][1])
        self.assertEqual(res["restored"], 1)
        self.assertEqual(self.vfs.files["smb://nas/wideo/seriale/B/b3.nfo"], original)
        self.assertEqual(pairs, [("DOM-000005", "DOM-000010")])

    def test_no_duplicates_changes_nothing(self):
        self.vfs.files["smb://nas/wideo/seriale/B/b3.nfo"] = _episode_nfo(
            [(b"B3", b"2", b"3", b"222", b"DOM-000006")])
        before = dict(self.vfs.files)
        result, _ = self._repair()
        self.assertEqual((result["groups"], result["renumbered"]), (0, 0))
        self.assertNotIn("nfo_id_epoch", self._shared())
        self.assertEqual(
            {k: v for k, v in self.vfs.files.items() if ".lock" not in k and "config.json" not in k
             and not k.endswith("SudoSync.cfg")},
            {k: v for k, v in before.items() if ".lock" not in k and "config.json" not in k
             and not k.endswith("SudoSync.cfg")},
        )

    def test_replace_only_matching_value(self):
        raw = (b'<movie><uniqueid type="sudosync">DOM-000001</uniqueid>'
               b'<uniqueid type="tmdb">DOM-000001</uniqueid></movie>')
        new, count = sudosync_core._replace_sudosync_id_in_nfo(raw, "dom-000001", "DOM-000002")
        self.assertEqual(count, 1)
        self.assertEqual(new, raw.replace(b">DOM-000001</uniqueid><uniqueid type=\"tmdb\"",
                                          b">DOM-000002</uniqueid><uniqueid type=\"tmdb\""))

    def test_epoch_change_reported_once(self):
        self.profile = self.tmp
        with patch.object(sudosync_core, "_addon_profile_path", side_effect=lambda: self.profile):
            self.assertFalse(sudosync_core.take_nfo_id_epoch_change({}))
            self.assertTrue(sudosync_core.take_nfo_id_epoch_change({"nfo_id_epoch": "e1"}))
            self.assertFalse(sudosync_core.take_nfo_id_epoch_change({"nfo_id_epoch": "e1"}))
            sudosync_core._SEEN_NFO_ID_EPOCH["value"] = None  # service restart
            self.assertFalse(sudosync_core.take_nfo_id_epoch_change({"nfo_id_epoch": "e1"}))
            self.assertTrue(sudosync_core.take_nfo_id_epoch_change({"nfo_id_epoch": "e2"}))


class RenumberedSnapshotTests(unittest.TestCase):
    def _build(self, renumbered):
        self.previous = previous = build_snapshot(
            [], [{"episodeid": 2, "season": 2, "episode": 3, "playcount": 1,
                  "uniqueid": {"sudosync": "DOM-000005", "tvdb": "222"}}],
            CLIENT, "Kodi3", logical_clock=1)
        return build_snapshot(
            [], [{"episodeid": 2, "season": 2, "episode": 3, "playcount": 1,
                  "uniqueid": {"sudosync": "DOM-000010", "tvdb": "222"}}],
            CLIENT, "Kodi3", previous_snapshot=previous, logical_clock=2, renumbered_ids=renumbered)

    def test_new_id_without_repair_record_is_rejected(self):
        with self.assertRaises(SnapshotBuildError):
            self._build(None)

    def test_new_id_from_repair_keeps_history(self):
        snap = self._build({"dom-000005": ["dom-000010"]})
        self.assertEqual(snap["episodes"][0]["ids"]["sudosync"], "dom-000010")
        self.assertEqual(snap["episodes"][0]["versions"], self.previous["episodes"][0]["versions"])


if __name__ == "__main__":
    unittest.main()
