# -*- coding: utf-8 -*-
"""A KodiBridge ID replaced outside the duplicate repair must not stop the sync."""
import os
import sys
import unittest
from unittest.mock import MagicMock

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
LIB_DIR = os.path.join(SERVICE_DIR, "resources", "lib")
for _path in (SERVICE_DIR, LIB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from resources.lib.sudosync_v2.snapshot import SnapshotBuildError, build_snapshot  # noqa: E402

CLIENT = "44444444-4444-4444-8444-444444444444"
OLD = "aaaaaaaa-1111-4111-8111-111111111111"
NEW = "bbbbbbbb-2222-4222-8222-222222222222"
OTHER = "cccccccc-3333-4333-8333-333333333333"


def _episode(episodeid, season, episode, ids, playcount=0):
    return {"episodeid": episodeid, "season": season, "episode": episode,
            "playcount": playcount, "uniqueid": dict(ids)}


def _previous(*episodes):
    return build_snapshot([], list(episodes), CLIENT, "Kodi", logical_clock=1)


class ReplacedIdTests(unittest.TestCase):
    def test_replaced_id_keeps_field_history(self):
        previous = _previous(
            _episode(1, 2, 5, {"sudosync": OLD, "tvdb": "5001", "tmdb": "7001"}, playcount=1),
            _episode(2, 2, 4, {"sudosync": OTHER, "tvdb": "5000"}, playcount=1),
        )
        snap = build_snapshot(
            [], [_episode(1, 2, 5, {"sudosync": NEW, "tvdb": "5001", "tmdb": "7001"}, playcount=1),
                 _episode(2, 2, 4, {"sudosync": OTHER, "tvdb": "5000"}, playcount=1)],
            CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)
        by_id = {r["ids"]["sudosync"]: r for r in snap["episodes"]}
        self.assertNotIn(OLD, by_id)
        self.assertEqual(by_id[NEW]["versions"], previous["episodes"][0]["versions"])

    def test_change_after_replacement_advances_version(self):
        previous = _previous(_episode(1, 2, 5, {"sudosync": OLD, "tvdb": "5001"}))
        snap = build_snapshot(
            [], [_episode(1, 2, 5, {"sudosync": NEW, "tvdb": "5001"}, playcount=1)],
            CLIENT, "Kodi", previous_snapshot=previous, logical_clock=5)
        self.assertNotEqual(snap["episodes"][0]["versions"]["playcount"],
                            previous["episodes"][0]["versions"]["playcount"])

    def test_old_id_still_in_library_is_a_conflict(self):
        previous = _previous(_episode(1, 2, 5, {"sudosync": OLD, "tvdb": "5001"}))
        with self.assertRaises(SnapshotBuildError):
            build_snapshot(
                [], [_episode(1, 2, 5, {"sudosync": NEW, "tvdb": "5001"}),
                     _episode(3, 2, 6, {"sudosync": OLD, "tvdb": "5002"})],
                CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)

    def test_other_identifier_changed_too_is_a_conflict(self):
        previous = _previous(_episode(1, 2, 5, {"sudosync": OLD, "tvdb": "5001", "tmdb": "1"}))
        with self.assertRaises(SnapshotBuildError):
            build_snapshot(
                [], [_episode(1, 2, 5, {"sudosync": NEW, "tvdb": "5001", "tmdb": "2"})],
                CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)

    def test_two_items_cannot_take_over_one_previous_record(self):
        previous = _previous(_episode(1, 2, 5, {"sudosync": OLD, "tvdb": "5001"}))
        with self.assertRaises(SnapshotBuildError):
            build_snapshot(
                [], [_episode(1, 2, 5, {"sudosync": NEW, "tvdb": "5001"}),
                     _episode(2, 2, 5, {"sudosync": OTHER, "tvdb": "5001"})],
                CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)

    def test_movie_replaced_id_keeps_field_history(self):
        movie_old = {"movieid": 7, "playcount": 1, "uniqueid": {"sudosync": OLD, "tmdb": "9001"}}
        movie_new = {"movieid": 7, "playcount": 1, "uniqueid": {"sudosync": NEW, "tmdb": "9001"}}
        previous = build_snapshot([movie_old], [], CLIENT, "Kodi", logical_clock=1)
        snap = build_snapshot([movie_new], [], CLIENT, "Kodi", previous_snapshot=previous, logical_clock=2)
        self.assertEqual(snap["movies"][0]["ids"]["sudosync"], NEW)
        self.assertEqual(snap["movies"][0]["versions"], previous["movies"][0]["versions"])


if __name__ == "__main__":
    unittest.main()
