# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for journaled plan application and fail-closed recovery."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.transaction import TransactionRunner, TransactionError
from resources.lib.sudosync_v2.snapshot import build_snapshot

CLIENT = "12345678-1234-4234-8234-123456789012"
STAMP = "2026-10-11T10:00:00Z"


def state(playcount=0):
    return {
        "playcount": playcount,
        "lastplayed": "",
        "resume": {"position": 0.0, "total": 0.0},
        "userrating": None,
    }


def current_snapshot():
    return build_snapshot([], [], CLIENT, "Test Kodi", generated_at=STAMP)


def plan(changes=None, media_type="movie", kodi_id=5):
    before = state()
    changes = changes or {"playcount": 2}
    return {
        "local_client_id": CLIENT,
        "changes": [{
            "type": media_type,
            "kodi_id": kodi_id,
            "identity": ["imdb:tt1234567"],
            "before": before,
            "changes": changes,
        }],
    }


class FakeCoordinator(object):
    def __init__(self):
        self.acquired = []
        self.released = []
        self.marker_retained = False

    def acquire(self, client_id, prepare=None):
        self.acquired.append(client_id)
        session_id = "12345678-1234-4234-8234-123456789012"
        if prepare is not None:
            prepare(session_id)
        self.marker_retained = True
        return session_id

    def release(self, session_id):
        self.released.append(session_id)
        self.marker_retained = False


class FakeKodi(object):
    def __init__(self):
        self.playing = False
        self.items = {("movie", 5): state()}
        self.write_count = 0
        self.fail_verify = False
        self.start_playing_after_write = False
        self.replace_identity_after_write = False
        self.movie_imdbnumber = "tt1234567"

    def is_video_playing(self):
        return self.playing

    def get_movie_details(self, kodi_id):
        return {"movieid": kodi_id, "imdbnumber": self.movie_imdbnumber, **deepcopy(self.items[("movie", kodi_id)])}

    def get_episode_details(self, kodi_id):
        return {"episodeid": kodi_id, **deepcopy(self.items[("episode", kodi_id)])}

    def set_movie_details(self, kodi_id, changes):
        self.write_count += 1
        self.items[("movie", kodi_id)].update(deepcopy(changes))
        if self.start_playing_after_write:
            self.playing = True
        if self.replace_identity_after_write:
            self.movie_imdbnumber = "tt7654321"
        if self.fail_verify:
            self.items[("movie", kodi_id)]["playcount"] = 0
        return "OK"

    def set_episode_details(self, kodi_id, changes):
        self.write_count += 1
        self.items[("episode", kodi_id)].update(deepcopy(changes))
        return "OK"


class TestTransactionRunner(unittest.TestCase):
    def setUp(self):
        self.kodi = FakeKodi()
        self.coordinator = FakeCoordinator()
        self.backups = []
        self.journals = []
        self.runner = TransactionRunner(
            self.kodi,
            self.coordinator,
            lambda snapshot, session_id: (self.backups.append((snapshot, session_id)) or "backup.json"),
            lambda journal: self.journals.append(deepcopy(journal)),
            clock=lambda: STAMP,
        )

    def test_successful_write_is_verified_journaled_and_released(self):
        result = self.runner.run(plan(), CLIENT, current_snapshot())
        self.assertEqual(result["status"], "committed")
        self.assertEqual(self.kodi.items[("movie", 5)]["playcount"], 2)
        self.assertEqual(self.journals[-1]["status"], "committed")
        self.assertEqual(self.journals[0]["status"], "prepared")
        self.assertEqual(self.journals[0]["items"][0]["status"], "pending")
        self.assertEqual(self.journals[-1]["items"][0]["status"], "verified")
        self.assertEqual(len(self.backups), 1)
        self.assertEqual(len(self.coordinator.released), 1)
        self.assertFalse(self.coordinator.marker_retained)

    def test_noop_does_not_acquire_session(self):
        result = self.runner.run({"local_client_id": CLIENT, "changes": []}, CLIENT, {})
        self.assertEqual(result["status"], "no_changes")
        self.assertFalse(self.coordinator.acquired)

    def test_stale_plan_aborts_before_any_library_write_and_releases_marker(self):
        # The target changed after planning but before the transaction acquired
        # its session. The old plan must not overwrite that newer local state.
        self.kodi.items[("movie", 5)]["playcount"] = 1
        with self.assertRaisesRegex(TransactionError, "Plan precondition failed"):
            self.runner.run(plan({"playcount": 2}), CLIENT, current_snapshot())
        self.assertEqual(self.kodi.write_count, 0)
        self.assertFalse(self.coordinator.marker_retained)
        self.assertEqual(self.coordinator.released, ["12345678-1234-4234-8234-123456789012"])
        self.assertEqual(self.journals[-1]["status"], "aborted")

    def test_reused_kodi_id_for_different_title_aborts_before_write(self):
        candidate = plan()
        candidate["changes"][0]["identity"].append("tmdb:55")
        self.kodi.get_movie_details = lambda kodi_id: {
            "movieid": kodi_id, "imdbnumber": "tt7654321", "uniqueid": {"tmdb": "55"}, **state()
        }
        with self.assertRaisesRegex(TransactionError, "Plan identity precondition failed"):
            self.runner.run(candidate, CLIENT, current_snapshot())
        self.assertEqual(self.kodi.write_count, 0)
        self.assertFalse(self.coordinator.marker_retained)
        self.assertEqual(self.journals[-1]["status"], "aborted")

    def test_playback_before_acquire_defers(self):
        self.kodi.playing = True
        result = self.runner.run(plan(), CLIENT, current_snapshot())
        self.assertEqual(result["status"], "deferred")
        self.assertFalse(self.coordinator.acquired)

    def test_verification_failure_retains_marker_and_journal(self):
        self.kodi.fail_verify = True
        with self.assertRaisesRegex(TransactionError, "verification failed"):
            self.runner.run(plan(), CLIENT, current_snapshot())
        self.assertTrue(self.coordinator.marker_retained)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")
        self.assertFalse(self.coordinator.released)

    def test_identity_change_during_write_retains_marker_for_recovery(self):
        self.kodi.replace_identity_after_write = True
        with self.assertRaisesRegex(TransactionError, "identity changed during transaction"):
            self.runner.run(plan(), CLIENT, current_snapshot())
        self.assertEqual(self.kodi.write_count, 1)
        self.assertTrue(self.coordinator.marker_retained)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")
        self.assertFalse(self.coordinator.released)

    def test_backup_failure_releases_marker_before_any_write(self):
        runner = TransactionRunner(
            self.kodi, self.coordinator,
            lambda snapshot, session_id: (_ for _ in ()).throw(IOError("backup offline")),
            lambda journal: self.journals.append(journal),
            clock=lambda: STAMP,
        )
        with self.assertRaisesRegex(TransactionError, "prepare or acquire"):
            runner.run(plan(), CLIENT, current_snapshot())
        self.assertFalse(self.coordinator.marker_retained)
        self.assertEqual(self.kodi.write_count, 0)

    def test_playback_starting_mid_transaction_retains_marker(self):
        self.kodi.start_playing_after_write = True
        multi = {
            "local_client_id": CLIENT,
            "changes": [
                plan({"playcount": 2})["changes"][0],
                {**plan({"playcount": 3}, kodi_id=6)["changes"][0], "kodi_id": 6},
            ],
        }
        self.kodi.items[("movie", 6)] = state()
        with self.assertRaisesRegex(TransactionError, "playback started"):
            self.runner.run(multi, CLIENT, current_snapshot())
        self.assertTrue(self.coordinator.marker_retained)
        self.assertEqual(self.kodi.write_count, 1)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")


if __name__ == "__main__":
    unittest.main()
