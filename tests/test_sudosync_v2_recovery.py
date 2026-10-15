# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for automatic owner-only recovery of interrupted v2 transactions."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.recovery import RecoveryRunner
from resources.lib.sudosync_v2.session import SessionBusyError
from resources.lib.sudosync_v2.transaction import TransactionError

CLIENT_A = "12345678-1234-4234-8234-123456789012"
CLIENT_B = "22345678-1234-4234-8234-123456789012"
SESSION = "32345678-1234-4234-8234-123456789012"


def state(playcount=0):
    return {"playcount": playcount, "lastplayed": "", "resume": {"position": 0.0, "total": 0.0}, "userrating": None}


def journal(status="needs_recovery", item_status="applying"):
    return {
        "format": "SudoSync v2 transaction journal", "schema_version": 1,
        "session_id": SESSION, "client_id": CLIENT_A, "status": status,
        "started_at": "2026-10-11T10:00:00Z", "finished_at": "",
        "backup_ref": "backup.json", "error": "Kodi stopped",
        "items": [{
            "type": "movie", "kodi_id": 5, "identity": ["imdb:tt1234567"],
            "before": state(), "after": dict(state(), playcount=2),
            "changes": {"playcount": 2}, "status": item_status,
        }],
    }


class Coordinator(object):
    def __init__(self, owner=CLIENT_A, exists=True):
        self.owner = owner
        self.exists = exists
        self.released = []
        self.resumed = []

    def inspect(self, client_id):
        if not self.exists:
            return {"state": "clear", "marker": None}
        marker = {"client_id": self.owner, "session_id": SESSION, "started_at": "2026-10-11T10:00:00Z"}
        return {"state": "owned_recovery" if self.owner == client_id else "blocked", "marker": marker}

    def resume_owned(self, client_id, session_id):
        if self.owner != client_id or session_id != SESSION:
            raise SessionBusyError("not owner")
        self.resumed.append(session_id)
        return session_id

    def release(self, session_id):
        self.released.append(session_id)
        self.exists = False


class Kodi(object):
    def __init__(self, playcount=0):
        self.playing = False
        self.value = playcount
        self.writes = 0
        self.fail_write = False

    def is_video_playing(self):
        return self.playing

    def get_movie_details(self, kodi_id):
        return dict(state(self.value), movieid=kodi_id, imdbnumber="tt1234567")

    def get_episode_details(self, kodi_id):
        raise AssertionError("unexpected episode read")

    def set_movie_details(self, kodi_id, changes):
        self.writes += 1
        if self.fail_write:
            raise IOError("simulated Kodi stop")
        self.value = changes["playcount"]
        return "OK"

    def set_episode_details(self, kodi_id, changes):
        raise AssertionError("unexpected episode write")


class TestRecoveryRunner(unittest.TestCase):
    def setUp(self):
        self.coordinator = Coordinator()
        self.kodi = Kodi()
        self.journals = [journal()]
        self.runner = RecoveryRunner(
            self.kodi, self.coordinator,
            lambda session_id: deepcopy(self.journals[-1]),
            lambda value: self.journals.append(deepcopy(value)),
            clock=lambda: "2026-10-11T12:00:00Z",
        )

    def test_prepared_journal_is_recovered_after_marker_was_created_before_applying_transition(self):
        self.journals = [journal(status="prepared", item_status="pending")]
        result = self.runner.recover_on_start(CLIENT_A)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.kodi.value, 2)
        self.assertEqual(self.journals[-1]["status"], "committed")
        self.assertEqual(self.coordinator.released, [SESSION])

    def test_aborted_prewrite_journal_releases_marker_without_mutation(self):
        self.journals = [journal(status="aborted", item_status="pending")]
        result = self.runner.recover_on_start(CLIENT_A)
        self.assertEqual(result["status"], "recovered")
        self.assertTrue(result["aborted"])
        self.assertEqual(self.kodi.writes, 0)
        self.assertEqual(self.kodi.value, 0)
        self.assertEqual(self.coordinator.released, [SESSION])

    def test_same_kodi_resumes_partial_transaction_and_releases(self):
        result = self.runner.recover_on_start(CLIENT_A)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.kodi.value, 2)
        self.assertEqual(self.journals[-1]["status"], "committed")
        self.assertEqual(self.coordinator.released, [SESSION])

    def test_other_kodi_is_blocked_without_writes(self):
        result = self.runner.recover_on_start(CLIENT_B)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.kodi.writes, 0)
        self.assertFalse(self.coordinator.released)

    def test_no_marker_is_clear(self):
        self.coordinator.exists = False
        self.assertEqual(self.runner.recover_on_start(CLIENT_A), {"status": "clear"})

    def test_already_applied_rpc_is_verified_without_duplicate_write(self):
        self.kodi.value = 2
        result = self.runner.recover_on_start(CLIENT_A)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.kodi.writes, 0)
        self.assertEqual(self.journals[-1]["status"], "committed")

    def test_unexpected_library_state_keeps_session_blocked(self):
        self.kodi.value = 9
        with self.assertRaisesRegex(TransactionError, "matches neither"):
            self.runner.recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)
        self.assertEqual(self.kodi.writes, 0)

    def test_reused_kodi_id_for_different_title_keeps_session_blocked(self):
        self.kodi.get_movie_details = lambda kodi_id: dict(state(self.kodi.value), movieid=kodi_id, imdbnumber="tt7654321")
        with self.assertRaisesRegex(TransactionError, "Recovery identity check failed"):
            self.runner.recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)
        self.assertEqual(self.kodi.writes, 0)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")

    def test_failed_recovery_keeps_session_blocked(self):
        self.kodi.fail_write = True
        with self.assertRaises(TransactionError):
            self.runner.recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")

    def test_committed_journal_is_verified_before_marker_release(self):
        self.journals = [journal(status="committed", item_status="verified")]
        self.kodi.value = 2
        result = self.runner.recover_on_start(CLIENT_A)
        self.assertTrue(result["already_committed"])
        self.assertEqual(self.coordinator.released, [SESSION])

    def test_verified_item_that_changed_is_not_overwritten(self):
        self.journals = [journal(item_status="verified")]
        self.kodi.value = 9
        with self.assertRaisesRegex(TransactionError, "previously verified"):
            self.runner.recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)


if __name__ == "__main__":
    unittest.main()
