# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""User resolution of concurrent-edit conflicts in the v2 engine."""
from __future__ import absolute_import

import os
import sys
import unittest
from unittest.mock import MagicMock

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib.sudosync_v2.runtime import (  # noqa: E402
    ProcessLocalLockFactory,
    V2RuntimeError,
    build_v2_runtime,
)
from tests.test_sudosync_v2_runtime import InMemorySharedBackend, MutableFakeKodi  # noqa: E402

CLIENT_A = "12345678-1234-4234-8234-123456789012"
CLIENT_B = "22345678-1234-4234-8234-123456789012"


def _playcount_conflicts(result):
    plan = (result.get("preview") or {}).get("plan") or {}
    return [c for c in plan.get("conflicts") or [] if c["field"] == "playcount"]


class ConflictResolutionTests(unittest.TestCase):
    def setUp(self):
        backend = InMemorySharedBackend()
        lock_factory = ProcessLocalLockFactory()
        # Both devices first publish with initial (zero) versions and different
        # non-empty play counts: a concurrent conflict nobody can win.
        self.kodi_a = MutableFakeKodi(movie_id=10, playcount=1)
        self.kodi_b = MutableFakeKodi(movie_id=99, playcount=2)
        self.runtime_a = build_v2_runtime(self.kodi_a, backend, "smb://nas/.SudoSync", lock_factory, CLIENT_A, "Salon")
        self.runtime_b = build_v2_runtime(self.kodi_b, backend, "smb://nas/.SudoSync", lock_factory, CLIENT_B, "Sypialnia")
        self.runtime_a.sync_cycle()
        self.first_b = self.runtime_b.sync_cycle()

    def test_plan_reports_conflict_with_local_item_and_device_names(self):
        conflicts = _playcount_conflicts(self.first_b)
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["kodi_id"], 99)
        self.assertEqual(conflict["local_value"], 2)
        self.assertEqual(conflict["identity"], ["imdb:tt1234567"])
        self.assertEqual(
            sorted((v["client_name"], v["value"]) for v in conflict["values"]),
            [("Salon", 1), ("Sypialnia", 2)],
        )
        # The conflicted field is not applied either way.
        self.assertEqual(self.kodi_b.playcount, 2)
        self.assertEqual(self.kodi_b.writes, 0)

    def test_choosing_peer_value_writes_kodi_and_wins_everywhere(self):
        result = self.runtime_b.resolve_conflict("movie", 99, "playcount", 1)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(self.kodi_b.playcount, 1)
        self.assertEqual(self.kodi_b.writes, 1)

        after_a = self.runtime_a.sync_cycle()
        self.assertEqual(_playcount_conflicts(after_a), [])
        self.assertEqual(self.kodi_a.playcount, 1)
        after_b = self.runtime_b.sync_cycle()
        self.assertEqual(_playcount_conflicts(after_b), [])
        self.assertEqual(after_b["status"], "no_changes")

    def test_choosing_local_value_publishes_without_kodi_write(self):
        result = self.runtime_b.resolve_conflict("movie", 99, "playcount", 2)
        self.assertEqual(result["status"], "resolved")
        self.assertIsNone(result["transaction"])
        self.assertEqual(self.kodi_b.writes, 0)

        after_a = self.runtime_a.sync_cycle()
        self.assertEqual(after_a["status"], "committed")
        self.assertEqual(self.kodi_a.playcount, 2)
        # The applied value does not bounce back as a new local edit.
        self.assertEqual(self.runtime_b.sync_cycle()["status"], "no_changes")
        self.assertEqual(self.runtime_a.sync_cycle()["status"], "no_changes")

    def test_resolution_is_skipped_when_conflict_no_longer_exists(self):
        self.runtime_b.resolve_conflict("movie", 99, "playcount", 1)
        again = self.runtime_b.resolve_conflict("movie", 99, "playcount", 2)
        self.assertEqual(again["status"], "not_found")
        self.assertEqual(self.kodi_b.playcount, 1)

    def test_value_outside_conflict_is_rejected(self):
        with self.assertRaises(V2RuntimeError):
            self.runtime_b.resolve_conflict("movie", 99, "playcount", 7)
        self.assertEqual(self.kodi_b.writes, 0)

    def test_resolution_waits_while_video_is_playing(self):
        self.kodi_b.playing = True
        result = self.runtime_b.resolve_conflict("movie", 99, "playcount", 1)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(self.kodi_b.playcount, 2)
        self.kodi_b.playing = False
        self.assertEqual(_playcount_conflicts(self.runtime_b.sync_cycle())[0]["local_value"], 2)


class ConflictResolutionGuardTests(unittest.TestCase):
    setUp = ConflictResolutionTests.setUp

    def test_changed_version_is_not_applied(self):
        result = self.runtime_b.resolve_conflict(
            "movie", 99, "playcount", 1, expected_identity=["imdb:tt1234567"], expected_version={"lc": 3, "seq": 1},
        )
        self.assertEqual(result["status"], "changed")
        self.assertEqual(self.kodi_b.writes, 0)

    def test_other_item_under_same_kodi_id_is_not_applied(self):
        result = self.runtime_b.resolve_conflict(
            "movie", 99, "playcount", 1, expected_identity=["imdb:tt7654321"], expected_version={"lc": 0, "seq": 0},
        )
        self.assertEqual(result["status"], "changed")
        self.assertEqual(self.kodi_b.writes, 0)

    def test_matching_expectation_is_applied(self):
        result = self.runtime_b.resolve_conflict(
            "movie", 99, "playcount", 1, expected_identity=["imdb:tt1234567"], expected_version={"lc": 0, "seq": 0},
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(self.kodi_b.playcount, 1)


class TwoPartEpisodeKodi(object):
    """Two parts of one TV episode: same TVDB ID, different KodiBridge IDs."""

    def __init__(self, playcounts):
        self.playcounts = dict(playcounts)
        self.playing = False
        self.writes = 0

    def is_video_playing(self):
        return self.playing

    def _item(self, episode_id):
        return {
            "episodeid": episode_id,
            "title": "Finale part {}".format(episode_id - 200),
            "uniqueid": {"tvdb": "777", "sudosync": "MAZURY-00090{}".format(episode_id - 200)},
            "playcount": self.playcounts[episode_id],
            "lastplayed": "",
            "resume": {"position": 0.0, "total": 0.0},
            "userrating": 0,
        }

    def get_movies(self):
        return []

    def get_episodes(self):
        return [self._item(i) for i in sorted(self.playcounts)]

    def get_episode_details(self, episode_id):
        return self._item(episode_id)

    def set_episode_details(self, episode_id, changes):
        self.writes += 1
        if "playcount" in changes:
            self.playcounts[episode_id] = changes["playcount"]
        return "OK"


class MultiPartConflictTests(unittest.TestCase):
    def test_resolution_touches_only_the_chosen_part(self):
        backend = InMemorySharedBackend()
        lock_factory = ProcessLocalLockFactory()
        kodi_a = TwoPartEpisodeKodi({201: 1, 202: 5})
        kodi_b = TwoPartEpisodeKodi({201: 2, 202: 5})
        runtime_a = build_v2_runtime(kodi_a, backend, "smb://nas/.SudoSync", lock_factory, CLIENT_A, "Salon",
                                     sudosync_only=True)
        runtime_b = build_v2_runtime(kodi_b, backend, "smb://nas/.SudoSync", lock_factory, CLIENT_B, "Sypialnia",
                                     sudosync_only=True)
        runtime_a.sync_cycle()
        conflicts = _playcount_conflicts(runtime_b.sync_cycle())
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["kodi_id"], 201)

        result = runtime_b.resolve_conflict("episode", 201, "playcount", 1,
                                            expected_identity=conflicts[0]["identity"],
                                            expected_version=conflicts[0]["version"])
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(kodi_b.playcounts, {201: 1, 202: 5})
        parts = {r["ids"]["sudosync"]: r for r in result["snapshot"]["episodes"]}
        self.assertEqual(parts["mazury-000901"]["state"]["playcount"], 1)
        self.assertEqual(parts["mazury-000901"]["versions"]["playcount"]["lc"], 1)
        self.assertEqual(parts["mazury-000902"]["versions"]["playcount"]["lc"], 0)

        runtime_a.sync_cycle()
        self.assertEqual(kodi_a.playcounts, {201: 1, 202: 5})
        self.assertEqual(_playcount_conflicts(runtime_b.sync_cycle()), [])


if __name__ == "__main__":
    unittest.main()
