# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
from __future__ import absolute_import

import unittest
import sys
import os

from resources.lib.sudosync_v2.snapshot import SnapshotBuildError, build_snapshot
from resources.lib.sudosync_v2.identity import match_by_strong_identity
from resources.lib.sudosync_v2.kodi_rpc import KodiJsonRpcAdapter
from resources.lib.sudosync_core import (
    _safe_parse_nfo_xml,
    _nfo_raw_has_strong_id,
    _nfo_raw_has_sudosync_id,
)

CLIENT = "12345678-1234-4234-8234-123456789012"
STAMP = "2026-10-21T12:00:00Z"

MULTI_EPISODE_NFO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<episodedetails>
  <title>Episode 1</title>
  <season>1</season>
  <episode>1</episode>
  <uniqueid default="true" type="tmdb">1001</uniqueid>
  <uniqueid default="false" type="imdb">tt0001001</uniqueid>
</episodedetails>
<episodedetails>
  <title>Episode 2</title>
  <season>1</season>
  <episode>2</episode>
  <uniqueid default="true" type="tmdb">1002</uniqueid>
  <uniqueid default="false" type="imdb">tt0001002</uniqueid>
  <uniqueid default="false" type="sudosync">MAZURY-000500</uniqueid>
</episodedetails>
"""


class TestMultiEpisodeNfoAndSudoSyncIdentity(unittest.TestCase):

    def test_safe_parse_nfo_xml_multi_episode(self):
        root = _safe_parse_nfo_xml(MULTI_EPISODE_NFO)
        self.assertIsNotNone(root)
        self.assertEqual(root.tag, "root")
        uids = root.findall(".//uniqueid")
        self.assertEqual(len(uids), 5)
        types = [u.attrib.get("type") for u in uids]
        self.assertIn("sudosync", types)
        self.assertIn("tmdb", types)

    def test_nfo_raw_helpers_with_multi_episode(self):
        has_strong, ids = _nfo_raw_has_strong_id(MULTI_EPISODE_NFO, "episode")
        self.assertTrue(has_strong)
        self.assertIn("tmdb", ids)

        has_sudo, sudo_ids = _nfo_raw_has_sudosync_id(MULTI_EPISODE_NFO, "episode")
        self.assertTrue(has_sudo)
        self.assertEqual(sudo_ids.get("sudosync"), "MAZURY-000500")

    def test_kodi_rpc_read_single_nfo_multi_episode(self):
        def fake_reader(path, max_bytes):
            return MULTI_EPISODE_NFO

        adapter = KodiJsonRpcAdapter(lambda m, p: {}, nfo_reader=fake_reader)
        root, ids = adapter._read_single_nfo("smb://nas/Show/s01e01-e02.nfo")
        self.assertEqual(root.tag, "root")
        self.assertEqual(ids.get("sudosync"), "mazury-000500")
        self.assertIn(ids.get("tmdb"), ("1001", "1002"))

    def test_snapshot_build_allows_duplicate_external_ids_with_unique_sudosync(self):
        ep1 = {
            "episodeid": 101,
            "title": "Copy A",
            "uniqueid": {"tmdb": "2250557", "sudosync": "MAZURY-000256"},
            "playcount": 0,
        }
        ep2 = {
            "episodeid": 102,
            "title": "Copy B",
            "uniqueid": {"tmdb": "2250557", "sudosync": "MAZURY-000258"},
            "playcount": 1,
        }
        # Does NOT raise SnapshotBuildError, despite sharing tmdb 2250557
        snap = build_snapshot([], [ep1, ep2], CLIENT, "Living Room", generated_at=STAMP)
        self.assertEqual(len(snap["episodes"]), 2)
        self.assertEqual(snap["episodes"][0]["ids"]["sudosync"], "mazury-000256")
        self.assertEqual(snap["episodes"][1]["ids"]["sudosync"], "mazury-000258")

    def test_snapshot_build_rejects_duplicate_sudosync_id(self):
        ep1 = {
            "episodeid": 101,
            "title": "Copy A",
            "uniqueid": {"sudosync": "MAZURY-000256"},
            "playcount": 0,
        }
        ep2 = {
            "episodeid": 102,
            "title": "Copy B",
            "uniqueid": {"sudosync": "MAZURY-000256"},
            "playcount": 1,
        }
        with self.assertRaises(SnapshotBuildError) as ctx:
            build_snapshot([], [ep1, ep2], CLIENT, "Living Room", generated_at=STAMP)
        self.assertIn("duplicate/overlapping strong identity", str(ctx.exception))

    def test_snapshot_build_rejects_duplicate_external_ids_when_not_both_have_sudosync(self):
        ep1 = {
            "episodeid": 101,
            "title": "Copy A",
            "uniqueid": {"tmdb": "2250557", "sudosync": "MAZURY-000256"},
            "playcount": 0,
        }
        ep2 = {
            "episodeid": 102,
            "title": "Copy B",
            "uniqueid": {"tmdb": "2250557"},  # missing sudosync ID
            "playcount": 1,
        }
        with self.assertRaises(SnapshotBuildError) as ctx:
            build_snapshot([], [ep1, ep2], CLIENT, "Living Room", generated_at=STAMP)
        self.assertIn("duplicate/overlapping strong identity", str(ctx.exception))

    def test_snapshot_build_prior_disambiguation_with_sudosync(self):
        ep1 = {
            "episodeid": 101,
            "title": "Copy A",
            "uniqueid": {"tmdb": "2250557", "sudosync": "MAZURY-000256"},
            "playcount": 0,
        }
        ep2 = {
            "episodeid": 102,
            "title": "Copy B",
            "uniqueid": {"tmdb": "2250557", "sudosync": "MAZURY-000258"},
            "playcount": 1,
        }
        snap1 = build_snapshot([], [ep1, ep2], CLIENT, "Living Room", generated_at=STAMP)

        # Update ep2 only
        ep2_updated = dict(ep2)
        ep2_updated["playcount"] = 2
        snap2 = build_snapshot([], [ep1, ep2_updated], CLIENT, "Living Room", previous_snapshot=snap1, generated_at=STAMP)

        self.assertEqual(snap2["episodes"][0]["versions"]["playcount"]["seq"], 0)
        self.assertEqual(snap2["episodes"][1]["versions"]["playcount"]["seq"], 1)

    def test_match_by_strong_identity_sudosync_precedence(self):
        # Same TMDB, but different SudoSync IDs -> MUST NOT match
        ids_a = {"tmdb": "2250557", "sudosync": "MAZURY-000256"}
        ids_b = {"tmdb": "2250557", "sudosync": "MAZURY-000258"}
        self.assertFalse(match_by_strong_identity("episode", ids_a, "episode", ids_b))

        # Same SudoSync ID -> MUST match
        ids_c = {"tmdb": "2250557", "sudosync": "MAZURY-000256"}
        self.assertTrue(match_by_strong_identity("episode", ids_a, "episode", ids_c))

        # No SudoSync IDs, shared TMDB -> matches by TMDB
        ids_d = {"tmdb": "2250557"}
        ids_e = {"tmdb": "2250557"}
        self.assertTrue(match_by_strong_identity("episode", ids_d, "episode", ids_e))


if __name__ == "__main__":
    unittest.main()
