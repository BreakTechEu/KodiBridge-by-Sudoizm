# -*- coding: utf-8 -*-
"""An item with a new KodiBridge ID matching several previous records must not stop the sync."""
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
FIRST = "aaaaaaaa-1111-4111-8111-111111111111"
SECOND = "cccccccc-3333-4333-8333-333333333333"
NEW = "bbbbbbbb-2222-4222-8222-222222222222"
NEW_TOO = "dddddddd-4444-4444-8444-444444444444"
ZERO = {"lc": 0, "seq": 0, "client_id": CLIENT}


def _episode(episodeid, ids, playcount=0):
    return {"episodeid": episodeid, "season": 2, "episode": 7,
            "playcount": playcount, "uniqueid": dict(ids)}


def _previous():
    # Two files of one episode, each with its own KodiBridge ID.
    return build_snapshot(
        [], [_episode(1, {"sudosync": FIRST, "imdb": "tt9", "tvdb": "5001"}, playcount=1),
             _episode(2, {"sudosync": SECOND, "imdb": "tt9", "tvdb": "5001"}, playcount=1)],
        CLIENT, "Kodi", logical_clock=1)


def _build(current):
    return build_snapshot([], current, CLIENT, "Kodi", previous_snapshot=_previous(), logical_clock=3)


class NewIdSeveralPreviousRecordsTests(unittest.TestCase):
    def test_new_id_continues_the_only_record_gone_from_the_library(self):
        previous = _previous()
        for order in (0, 1):
            current = [_episode(2, {"sudosync": SECOND, "imdb": "tt9", "tvdb": "5001"}, playcount=1),
                       _episode(3, {"sudosync": NEW, "imdb": "tt9", "tvdb": "5001"}, playcount=1)]
            if order:
                current.reverse()
            snap = _build(current)
            by_id = {r["ids"]["sudosync"]: r for r in snap["episodes"]}
            self.assertEqual(sorted(by_id), sorted([SECOND, NEW]))
            self.assertEqual(by_id[NEW]["versions"], previous["episodes"][0]["versions"])
            self.assertEqual(by_id[SECOND]["versions"], previous["episodes"][1]["versions"])

    def test_third_file_with_a_new_id_is_a_new_item(self):
        for order in (0, 1):
            current = [_episode(1, {"sudosync": FIRST, "imdb": "tt9", "tvdb": "5001"}, playcount=1),
                       _episode(2, {"sudosync": SECOND, "imdb": "tt9", "tvdb": "5001"}, playcount=1),
                       _episode(3, {"sudosync": NEW, "imdb": "tt9", "tvdb": "5001"})]
            if order:
                current.reverse()
            snap = _build(current)
            by_id = {r["ids"]["sudosync"]: r for r in snap["episodes"]}
            self.assertEqual(sorted(by_id), sorted([FIRST, SECOND, NEW]))
            self.assertTrue(all(v == ZERO for v in by_id[NEW]["versions"].values()))

    def test_new_ids_for_both_files_are_new_items(self):
        with self.assertLogs("resources.lib.sudosync_v2.snapshot", level="INFO") as cm:
            snap = _build([_episode(3, {"sudosync": NEW, "imdb": "tt9", "tvdb": "5001"}, playcount=1),
                           _episode(4, {"sudosync": NEW_TOO, "imdb": "tt9", "tvdb": "5001"}, playcount=1)])
        by_id = {r["ids"]["sudosync"]: r for r in snap["episodes"]}
        self.assertEqual(sorted(by_id), sorted([NEW, NEW_TOO]))
        for record in by_id.values():
            self.assertTrue(all(v == ZERO for v in record["versions"].values()))
        self.assertTrue(any("cannot be told apart" in line for line in cm.output))

    def test_remaining_record_with_another_identifier_changed_is_a_conflict(self):
        with self.assertRaises(SnapshotBuildError):
            _build([_episode(2, {"sudosync": SECOND, "imdb": "tt9", "tvdb": "5001"}),
                    _episode(3, {"sudosync": NEW, "imdb": "tt9", "tvdb": "6001"})])


if __name__ == "__main__":
    unittest.main()
