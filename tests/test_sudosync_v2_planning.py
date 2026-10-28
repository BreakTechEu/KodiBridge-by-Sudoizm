# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for conservative cross-client dry-run planning."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.planning import PlanningError, apply_plan_to_snapshot, build_sync_plan
from resources.lib.sudosync_v2.snapshot import build_snapshot

A = "12345678-1234-4234-8234-123456789012"
B = "22345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


def make_snapshot(client_id, name, records, generated=STAMP, previous=None):
    movies = []
    for idx, state in enumerate(records):
        item = {
            "movieid": idx + 1,
            "imdbnumber": state.get("imdb", "tt1234567"),
            "uniqueid": state.get("uniqueid", {"imdb": state.get("imdb", "tt1234567")}),
            "playcount": state.get("playcount", 0),
            "lastplayed": state.get("lastplayed", ""),
            "resume": state.get("resume", {"position": 0, "total": 0}),
            "userrating": state.get("userrating", 0),
        }
        movies.append(item)
    return build_snapshot(movies, [], client_id, name, previous_snapshot=previous, generated_at=generated)


class TestDryRunPlanner(unittest.TestCase):
    def test_plans_only_existing_local_records(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 0}])
        remote_base = make_snapshot(B, "Bedroom", [{"playcount": 0}])
        remote = make_snapshot(B, "Bedroom", [{"playcount": 2}], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [42], "episode": []})
        self.assertEqual(plan["change_count"], 1)
        self.assertEqual(plan["changes"][0]["kodi_id"], 42)
        self.assertEqual(plan["changes"][0]["changes"], {"playcount": 2})
        self.assertEqual(plan["field_counts"]["playcount"], 1)

    def test_remote_only_item_is_not_added_to_local_library(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 0, "imdb": "tt1234567"}])
        remote_base = make_snapshot(B, "Bedroom", [{"playcount": 0, "imdb": "tt1234567"}, {"playcount": 0, "imdb": "tt9999999"}])
        remote = make_snapshot(B, "Bedroom", [{"playcount": 2, "imdb": "tt1234567"}, {"playcount": 3, "imdb": "tt9999999"}], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []})
        self.assertEqual(plan["change_count"], 1)
        self.assertEqual(plan["changes"][0]["kodi_id"], 7)

    def test_equal_version_with_different_values_is_reported_not_written(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 1}])
        remote = make_snapshot(B, "Bedroom", [{"playcount": 2}])
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []})
        self.assertEqual(plan["change_count"], 0)
        self.assertGreaterEqual(plan["conflict_count"], 1)

    def test_identifier_bridge_with_conflicting_ids_is_ambiguous(self):
        local = make_snapshot(A, "Living Room", [{
            "imdb": "tt1234567", "uniqueid": {"imdb": "tt1234567", "tmdb": 10}, "playcount": 1
        }])
        remote = make_snapshot(B, "Bedroom", [{
            "imdb": "tt1234567", "uniqueid": {"imdb": "tt1234567", "tmdb": 11}, "playcount": 2
        }])
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []})
        self.assertEqual(plan["ambiguous_clusters"], 1)
        self.assertEqual(plan["change_count"], 0)

    def test_duplicate_local_ids_mapping_is_rejected(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 0}])
        with self.assertRaises(PlanningError):
            build_sync_plan(local, [], {"movie": [], "episode": []})

    def test_input_snapshots_are_not_mutated(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 0}])
        remote_base = make_snapshot(B, "Bedroom", [{"playcount": 0}])
        remote = make_snapshot(B, "Bedroom", [{"playcount": 2}], previous=remote_base)
        before = deepcopy(local)
        build_sync_plan(local, [remote], {"movie": [7], "episode": []})
        self.assertEqual(local, before)

    def test_apply_plan_to_snapshot_preserves_winning_versions_and_prevents_feedback_loop(self):
        local = make_snapshot(A, "Living Room", [{"playcount": 0}])
        remote_base = make_snapshot(B, "Bedroom", [{"playcount": 0}])
        remote = make_snapshot(B, "Bedroom", [{"playcount": 2}], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [42], "episode": []})
        self.assertIn("target_versions", plan["changes"][0])
        winning_ver = remote["movies"][0]["versions"]["playcount"]
        self.assertEqual(plan["changes"][0]["target_versions"]["playcount"], winning_ver)

        updated_local = apply_plan_to_snapshot(local, plan)
        self.assertEqual(updated_local["movies"][0]["state"]["playcount"], 2)
        self.assertEqual(updated_local["movies"][0]["versions"]["playcount"], winning_ver)

        # Next cycle on A with Kodi now returning playcount=2 must NOT advance lc/seq
        next_local = make_snapshot(A, "Living Room", [{"playcount": 2}], previous=updated_local)
        self.assertEqual(next_local["movies"][0]["versions"]["playcount"], winning_ver)
        next_plan_on_b = build_sync_plan(remote, [next_local], {"movie": [1], "episode": []})
        self.assertEqual(next_plan_on_b["change_count"], 0)


if __name__ == "__main__":
    unittest.main()


class TestKodiBridgeIdOnlyMatching(unittest.TestCase):
    """With the network KodiBridge ID option on, only the KodiBridge ID matches records."""

    KINDS = ("sudosync",)

    def test_same_kodibridge_id_matches_even_when_external_ids_differ(self):
        local = make_snapshot(A, "Living Room", [{
            "imdb": "tt1234567", "uniqueid": {"imdb": "tt1234567", "sudosync": "dom-000001"}, "playcount": 0
        }])
        remote_base = make_snapshot(B, "Bedroom", [{
            "imdb": "tt7654321", "uniqueid": {"imdb": "tt7654321", "sudosync": "dom-000001"}, "playcount": 0
        }])
        remote = make_snapshot(B, "Bedroom", [{
            "imdb": "tt7654321", "uniqueid": {"imdb": "tt7654321", "sudosync": "dom-000001"}, "playcount": 2
        }], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []}, identity_kinds=self.KINDS)
        self.assertEqual(plan["ambiguous_clusters"], 0)
        self.assertEqual(plan["change_count"], 1)
        self.assertEqual(plan["changes"][0]["changes"], {"playcount": 2})

    def test_external_id_alone_does_not_match(self):
        local = make_snapshot(A, "Living Room", [{
            "imdb": "tt1234567", "uniqueid": {"imdb": "tt1234567", "sudosync": "dom-000001"}, "playcount": 0
        }])
        remote_base = make_snapshot(B, "Bedroom", [{"imdb": "tt1234567", "playcount": 0}])
        remote = make_snapshot(B, "Bedroom", [{"imdb": "tt1234567", "playcount": 2}], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []}, identity_kinds=self.KINDS)
        self.assertEqual(plan["change_count"], 0)

    def test_without_restriction_external_id_still_matches(self):
        local = make_snapshot(A, "Living Room", [{
            "imdb": "tt1234567", "uniqueid": {"imdb": "tt1234567", "sudosync": "dom-000001"}, "playcount": 0
        }])
        remote_base = make_snapshot(B, "Bedroom", [{"imdb": "tt1234567", "playcount": 0}])
        remote = make_snapshot(B, "Bedroom", [{"imdb": "tt1234567", "playcount": 2}], previous=remote_base)
        plan = build_sync_plan(local, [remote], {"movie": [7], "episode": []})
        self.assertEqual(plan["change_count"], 1)


class TestApplyPlanWithSharedExternalIds(unittest.TestCase):
    """Two local items share an external ID but differ by KodiBridge ID (e.g. multi-episode files)."""

    def _snapshots(self):
        shared = {"imdb": "tt1234567", "tmdb": 2250557}
        local = make_snapshot(A, "Living Room", [
            {"imdb": "tt1234567", "uniqueid": dict(shared, sudosync="dom-000256"), "playcount": 0},
            {"imdb": "tt1234567", "uniqueid": dict(shared, sudosync="dom-000258"), "playcount": 0},
        ])
        remote_base = make_snapshot(B, "Bedroom", [
            {"imdb": "tt1234567", "uniqueid": dict(shared, sudosync="dom-000256"), "playcount": 0},
        ])
        remote = make_snapshot(B, "Bedroom", [
            {"imdb": "tt1234567", "uniqueid": dict(shared, sudosync="dom-000256"), "playcount": 3},
        ], previous=remote_base)
        return local, remote

    def test_applied_plan_updates_only_the_matching_record(self):
        local, remote = self._snapshots()
        plan = build_sync_plan(local, [remote], {"movie": [7, 8], "episode": []}, identity_kinds=("sudosync",))
        self.assertEqual(plan["change_count"], 1)
        updated = apply_plan_to_snapshot(local, plan)
        by_sid = {r["ids"]["sudosync"]: r["state"]["playcount"] for r in updated["movies"]}
        self.assertEqual(by_sid, {"dom-000256": 3, "dom-000258": 0})
