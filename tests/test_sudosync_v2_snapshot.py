# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for building snapshots from Kodi library records."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.snapshot import SnapshotBuildError, build_snapshot
from resources.lib.sudosync_v2.schema import validate_snapshot

CLIENT = "12345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


def movie(**overrides):
    value = {
        "movieid": 10,
        "title": "Example",
        "imdbnumber": "tt1234567",
        "uniqueid": {"imdb": "tt1234567", "tmdb": 1234},
        "playcount": 1,
        "lastplayed": STAMP,
        "resume": {"position": 30.0, "total": 100.0},
        "userrating": 8,
    }
    value.update(overrides)
    return value


class TestSnapshotBuilder(unittest.TestCase):
    def test_builds_valid_snapshot_without_local_kodi_ids(self):
        result = build_snapshot([movie()], [], CLIENT, "Living Room", generated_at=STAMP)
        self.assertIsNone(validate_snapshot(result))
        record = result["movies"][0]
        self.assertEqual(record["ids"], {"imdb": "tt1234567", "tmdb": "1234"})
        self.assertNotIn("movieid", record)
        self.assertEqual(record["state"]["resume"], {"position": 30.0, "total": 100.0})

    def test_preserves_versions_for_unchanged_fields(self):
        first = build_snapshot([movie()], [], CLIENT, "Living Room", generated_at=STAMP)
        second = build_snapshot([movie()], [], CLIENT, "Living Room", previous_snapshot=first, generated_at=STAMP, logical_clock=4)
        self.assertEqual(first["movies"][0]["versions"], second["movies"][0]["versions"])

    def test_rejects_conflicting_identifiers_when_matching_previous_record(self):
        previous = build_snapshot([movie()], [], CLIENT, "Living Room", generated_at=STAMP)
        changed_identity = movie(
            imdbnumber="tt7654321",
            uniqueid={"imdb": "tt7654321", "tmdb": 1234},
        )
        with self.assertRaisesRegex(SnapshotBuildError, "conflicting identifiers with its previous snapshot: imdb"):
            build_snapshot(
                [changed_identity], [], CLIENT, "Living Room",
                previous_snapshot=previous, generated_at=STAMP,
            )

    def test_advances_only_changed_field_version(self):
        first = build_snapshot([movie()], [], CLIENT, "Living Room", generated_at=STAMP, logical_clock=3)
        second = build_snapshot([movie(playcount=2)], [], CLIENT, "Living Room", previous_snapshot=first, generated_at=STAMP, logical_clock=7)
        old = first["movies"][0]["versions"]
        new = second["movies"][0]["versions"]
        self.assertEqual(new["playcount"], {"lc": 8, "seq": 1, "client_id": CLIENT})
        for field in ("lastplayed", "resume", "userrating"):
            self.assertEqual(new[field], old[field])

    def test_accepts_supported_kodi_lastplayed_formats(self):
        for stamp in ("2026-10-11 10:00:00", "2026-10-11T10:00:00+02:00", "2026-10-11T08:00:00.123456Z"):
            result = build_snapshot([movie(lastplayed=stamp)], [], CLIENT, "Living Room", generated_at=STAMP)
            self.assertEqual(result["movies"][0]["state"]["lastplayed"], stamp)

    def test_rejects_conflicting_imdb_identifiers(self):
        with self.assertRaisesRegex(SnapshotBuildError, "conflicting IMDb"):
            build_snapshot([movie(imdbnumber="tt7654321")], [], CLIENT, "Living Room", generated_at=STAMP)

    def test_rejects_duplicate_identity_in_local_library(self):
        with self.assertRaisesRegex(SnapshotBuildError, "duplicate\/overlapping strong identity"):
            build_snapshot([movie(), movie(movieid=11)], [], CLIENT, "Living Room", generated_at=STAMP)

    def test_rejects_invalid_resume_and_rating(self):
        with self.assertRaisesRegex(SnapshotBuildError, "exceeds media duration"):
            build_snapshot([movie(resume={"position": 200, "total": 100})], [], CLIENT, "Living Room", generated_at=STAMP)
        with self.assertRaisesRegex(SnapshotBuildError, "userrating"):
            build_snapshot([movie(userrating=11)], [], CLIENT, "Living Room", generated_at=STAMP)

    def test_records_without_strong_identity_are_kept_but_not_matched(self):
        result = build_snapshot([movie(imdbnumber="", uniqueid={})], [], CLIENT, "Living Room", generated_at=STAMP)
        self.assertEqual(result["movies"][0]["ids"], {})

    def test_rejects_invalid_previous_snapshot_instead_of_resetting_versions(self):
        with self.assertRaisesRegex(SnapshotBuildError, "previous local snapshot is invalid"):
            build_snapshot([movie()], [], CLIENT, "Living Room", previous_snapshot={"movies": []}, generated_at=STAMP)

    def test_previous_snapshot_is_not_mutated(self):
        first = build_snapshot([movie()], [], CLIENT, "Living Room", generated_at=STAMP)
        before = deepcopy(first)
        build_snapshot([movie(playcount=2)], [], CLIENT, "Living Room", previous_snapshot=first, generated_at=STAMP)
        self.assertEqual(first, before)


if __name__ == "__main__":
    unittest.main()
