"""Unit tests for the Kodi-independent SudoSync v2 domain."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.identity import match_by_strong_identity, normalize_identifier
from resources.lib.sudosync_v2.merge import plan_field_merge
from resources.lib.sudosync_v2.schema import SnapshotValidationError, validate_snapshot

CLIENT = "12345678-1234-4234-8234-123456789012"

def record(state=None, versions=None, media_type="movie", ids=None):
    base_state = {
        "playcount": 0,
        "lastplayed": "",
        "resume": {"position": 0.0, "total": 0.0},
        "userrating": None,
    }
    base_versions = {
        field: {"lc": 0, "seq": 0, "client_id": CLIENT}
        for field in ("playcount", "lastplayed", "resume", "userrating")
    }
    if state:
        base_state.update(state)
    if versions:
        base_versions.update(versions)
    return {"type": media_type, "ids": ids or {"imdb": "tt1234567"}, "state": base_state, "versions": base_versions}

def snapshot():
    return {
        "format": "SudoSync v2 snapshot",
        "schema_version": 1,
        "client": {"id": CLIENT, "name": "Living Room"},
        "generated_at": "2026-10-10T12:30:45Z",
        "movies": [record()],
        "episodes": [],
    }

class TestIdentity(unittest.TestCase):
    def test_identifiers_are_normalized_and_typed(self):
        self.assertEqual(normalize_identifier("imdb", " TT1234567 "), "tt1234567")
        self.assertIsNone(normalize_identifier("imdb", "1234567"))
        self.assertIsNone(normalize_identifier("tmdb", "../123"))
        self.assertTrue(match_by_strong_identity("movie", {"imdb": "tt1234567"}, "movie", {"imdb": "TT1234567"}))
        self.assertFalse(match_by_strong_identity("movie", {"imdb": "tt1234567"}, "episode", {"imdb": "tt1234567"}))
        self.assertTrue(match_by_strong_identity(" Movie ", {"imdb": "tt1234567"}, "movie", {"imdb": "TT1234567"}))
        self.assertTrue(match_by_strong_identity("episode", {"tvdb": "12345"}, " EPISODE ", {"tvdb": "12345"}))
        self.assertFalse(match_by_strong_identity("series", {"imdb": "tt1234567"}, "series", {"imdb": "tt1234567"}))

    def test_missing_or_invalid_ids_do_not_match(self):
        self.assertFalse(match_by_strong_identity("movie", {"title": "Same"}, "movie", {"title": "Same"}))
        self.assertFalse(match_by_strong_identity("movie", {"tmdb": "../"}, "movie", {"tmdb": "../"}))

class TestMerge(unittest.TestCase):
    def test_newer_field_version_wins_independently(self):
        a = record(
            state={"playcount": 1, "userrating": 7},
            versions={
                "playcount": {"lc": 2, "seq": 1, "client_id": "A"},
                "userrating": {"lc": 1, "seq": 1, "client_id": "A"},
            },
        )
        b = record(
            state={"playcount": 3, "userrating": 8},
            versions={
                "playcount": {"lc": 3, "seq": 1, "client_id": "B"},
                "userrating": {"lc": 1, "seq": 1, "client_id": "A"},
            },
        )
        result = plan_field_merge([a, b])
        self.assertEqual(result["target_state"]["playcount"], 3)
        self.assertNotIn("userrating", result["target_state"])
        self.assertEqual([item["field"] for item in result["conflicts"]], ["userrating"])

    def test_concurrent_writes_from_different_clients_conflict(self):
        a = record(
            state={"playcount": 1},
            versions={"playcount": {"lc": 5, "seq": 2, "client_id": "A"}},
        )
        b = record(
            state={"playcount": 2},
            versions={"playcount": {"lc": 5, "seq": 2, "client_id": "B"}},
        )
        result = plan_field_merge([a, b])
        self.assertNotIn("playcount", result["target_state"])
        self.assertEqual(result["conflicts"][0]["field"], "playcount")
        self.assertEqual(len(result["conflicts"][0]["values"]), 2)

    def test_merge_does_not_mutate_inputs(self):
        a = record(state={"resume": {"position": 12.5, "total": 90.0}})
        before = deepcopy(a)
        plan_field_merge([a])
        self.assertEqual(a, before)

    def test_equal_newest_version_with_equal_value_is_not_conflict(self):
        version = {"lc": 4, "seq": 7, "client_id": "client"}
        a = record(state={"playcount": 2}, versions={"playcount": version})
        b = record(state={"playcount": 2}, versions={"playcount": version})
        result = plan_field_merge([a, b])
        self.assertEqual(result["target_state"]["playcount"], 2)
        self.assertFalse(result["conflicts"])

class TestSnapshotSchema(unittest.TestCase):
    def test_valid_snapshot(self):
        self.assertIsNone(validate_snapshot(snapshot()))

    def test_rejects_wrong_type_and_oversized_snapshot(self):
        bad = snapshot()
        bad["movies"][0]["type"] = "episode"
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(bad)
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(snapshot(), max_records=0)

    def test_rejects_non_finite_resume(self):
        bad = snapshot()
        bad["movies"][0]["state"]["resume"]["position"] = float("nan")
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(bad)

    def test_rejects_invalid_client_uuid(self):
        bad = snapshot()
        bad["client"]["id"] = "client-one"
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(bad)

if __name__ == "__main__":
    unittest.main()
