# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for safe one-time initialization planning."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.initialization import build_initialization_plan
from resources.lib.sudosync_v2.snapshot import build_snapshot
from resources.lib.sudosync_v2.planning import PlanningError

BASE = "12345678-1234-4234-8234-123456789012"
TARGET = "22345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


def item(kodi_id, playcount=0, lastplayed="", position=0, total=0, userrating=0, imdb="tt1234567", uniqueid=None):
    return {
        "movieid": kodi_id,
        "imdbnumber": imdb,
        "uniqueid": uniqueid if uniqueid is not None else ({"imdb": imdb} if imdb else {}),
        "playcount": playcount,
        "lastplayed": lastplayed,
        "resume": {"position": position, "total": total},
        "userrating": userrating,
    }


def snapshot(client_id, name, records):
    return build_snapshot(records, [], client_id, name, generated_at=STAMP)


class TestInitializationPlan(unittest.TestCase):
    def test_imports_base_values_into_empty_target(self):
        base = snapshot(BASE, "Base", [item(1, playcount=3, lastplayed=STAMP, position=120, total=600, userrating=8)])
        target = snapshot(TARGET, "Target", [item(9)])
        plan = build_initialization_plan(base, target, {"movie": [99], "episode": []})
        self.assertEqual(plan["change_count"], 1)
        self.assertEqual(plan["changes"][0]["kodi_id"], 99)
        self.assertEqual(plan["changes"][0]["changes"], {
            "playcount": 3, "lastplayed": STAMP,
            "resume": {"position": 120.0, "total": 600.0}, "userrating": 8,
        })

    def test_preserves_higher_target_playcount(self):
        base = snapshot(BASE, "Base", [item(1, playcount=2)])
        target = snapshot(TARGET, "Target", [item(9, playcount=5)])
        plan = build_initialization_plan(base, target, {"movie": [99], "episode": []})
        self.assertNotIn("playcount", plan["changes"][0]["changes"] if plan["changes"] else {})

    def test_reports_nonempty_resume_conflict_and_does_not_overwrite(self):
        base = snapshot(BASE, "Base", [item(1, position=100, total=600)])
        target = snapshot(TARGET, "Target", [item(9, position=250, total=600)])
        plan = build_initialization_plan(base, target, {"movie": [99], "episode": []})
        self.assertEqual(plan["change_count"], 0)
        self.assertEqual(plan["conflicts"][0]["field"], "resume")

    def test_reports_identifier_bridge_as_ambiguous(self):
        base = snapshot(BASE, "Base", [item(1, imdb="tt1234567", uniqueid={"imdb": "tt1234567", "tmdb": 10})])
        target = snapshot(TARGET, "Target", [item(9, imdb="tt1234567", uniqueid={"imdb": "tt1234567", "tmdb": 11})])
        plan = build_initialization_plan(base, target, {"movie": [99], "episode": []})
        self.assertEqual(plan["ambiguous_count"], 1)
        self.assertEqual(plan["change_count"], 0)

    def test_rejects_same_client_as_base_and_target(self):
        base = snapshot(BASE, "Base", [item(1)])
        with self.assertRaises(PlanningError):
            build_initialization_plan(base, base, {"movie": [1], "episode": []})


if __name__ == "__main__":
    unittest.main()
