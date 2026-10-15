# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for fail-closed shared operation coordination."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.session import (
    SharedOperationCoordinator, SessionBusyError, SessionError, SessionOwnershipError,
)
from resources.lib.sudosync_v2.storage import decode_json, encode_json


class Backend(object):
    def __init__(self):
        self.files = {}
        self.fail_write = False
        self.fail_delete = False

    def exists(self, path):
        return path in self.files

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, text):
        if self.fail_write:
            return False
        self.files[path] = text
        return True

    def delete(self, path):
        if self.fail_delete or path not in self.files:
            return False
        del self.files[path]
        return True


class LockFactory(object):
    """Thread lock for unit tests only; production must inject cross-process locking."""
    def __init__(self):
        self.guard = threading.Lock()
        self.locks = {}

    def __call__(self, path):
        with self.guard:
            return self.locks.setdefault(path, threading.RLock())


class TestSharedOperationCoordinator(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.lock = LockFactory()
        self.path = "share/.sudosync-active-operation.json"
        self.ids = iter([
            "12345678-1234-4234-8234-123456789012",
            "22345678-1234-4234-8234-123456789012",
        ])
        self.coordinator = SharedOperationCoordinator(
            self.backend, self.lock, self.path,
            clock=lambda: "2026-10-11T12:00:00Z",
            id_factory=lambda: next(self.ids),
        )

    def test_prepare_callback_runs_before_marker_is_created(self):
        observed = []

        def prepare(session_id):
            observed.append((session_id, self.backend.exists(self.path)))

        token = self.coordinator.acquire("living-room", prepare=prepare)
        self.assertEqual(observed, [(token, False)])
        self.assertTrue(self.backend.exists(self.path))

    def test_prepare_failure_does_not_create_marker(self):
        def prepare(session_id):
            raise IOError("journal unavailable")

        with self.assertRaisesRegex(SessionError, "Cannot prepare transaction"):
            self.coordinator.acquire("living-room", prepare=prepare)
        self.assertFalse(self.backend.exists(self.path))

    def test_only_one_operation_can_own_marker(self):
        token = self.coordinator.acquire("living-room")
        marker = decode_json(self.backend.files[self.path])
        self.assertEqual(marker["session_id"], token)
        with self.assertRaises(SessionBusyError):
            self.coordinator.acquire("bedroom")
        self.assertEqual(decode_json(self.backend.files[self.path])["session_id"], token)

    def test_clean_release_allows_next_operation(self):
        first = self.coordinator.acquire("living-room")
        self.coordinator.release(first)
        self.assertFalse(self.backend.exists(self.path))
        second = self.coordinator.acquire("bedroom")
        self.assertNotEqual(first, second)

    def test_wrong_owner_cannot_release_marker(self):
        token = self.coordinator.acquire("living-room")
        with self.assertRaises(SessionOwnershipError):
            self.coordinator.release("22345678-1234-4234-8234-123456789012")
        self.assertEqual(decode_json(self.backend.files[self.path])["session_id"], token)

    def test_marker_from_crashed_session_is_not_reclaimed_by_age(self):
        self.backend.files[self.path] = encode_json({
            "format": "SudoSync v2 active operation",
            "schema_version": 1,
            "session_id": "12345678-1234-4234-8234-123456789012",
            "client_id": "offline-kodi",
            "started_at": "2020-01-01T00:00:00Z",
        })
        with self.assertRaisesRegex(SessionBusyError, "No age-based takeover"):
            self.coordinator.acquire("living-room")
        self.assertTrue(self.backend.exists(self.path))

    def test_corrupt_marker_fails_closed(self):
        self.backend.files[self.path] = "{not-json"
        with self.assertRaisesRegex(SessionError, "cannot be trusted"):
            self.coordinator.acquire("living-room")
        self.assertEqual(self.backend.files[self.path], "{not-json")

    def test_marker_write_failure_does_not_return_token(self):
        self.backend.fail_write = True
        with self.assertRaises(SessionError):
            self.coordinator.acquire("living-room")
        self.assertFalse(self.backend.exists(self.path))

    def test_inspect_reports_clear_state(self):
        result = self.coordinator.inspect("living-room")
        self.assertEqual(result["state"], "clear")
        self.assertIsNone(result["marker"])

    def test_inspect_recognizes_original_kodi_as_recovery_owner(self):
        token = self.coordinator.acquire("living-room")
        result = self.coordinator.inspect("living-room")
        self.assertEqual(result["state"], "owned_recovery")
        self.assertEqual(result["marker"]["session_id"], token)
        self.assertEqual(self.coordinator.resume_owned("living-room", token), token)
        self.assertTrue(self.backend.exists(self.path))

    def test_inspect_blocks_other_kodi_and_does_not_release_marker(self):
        token = self.coordinator.acquire("living-room")
        result = self.coordinator.inspect("bedroom")
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["marker"]["session_id"], token)
        with self.assertRaises(SessionBusyError):
            self.coordinator.resume_owned("bedroom", token)
        self.assertTrue(self.backend.exists(self.path))

    def test_owner_cannot_resume_a_different_session_id(self):
        self.coordinator.acquire("living-room")
        with self.assertRaises(SessionOwnershipError):
            self.coordinator.resume_owned("living-room", "22345678-1234-4234-8234-123456789012")
        self.assertTrue(self.backend.exists(self.path))

    def test_recovery_requires_explicit_confirmation(self):
        token = self.coordinator.acquire("living-room")
        with self.assertRaisesRegex(SessionError, "Explicit confirmation"):
            self.coordinator.recover_abandoned(token)
        self.assertTrue(self.backend.exists(self.path))

    def test_explicit_recovery_requires_matching_session_id(self):
        token = self.coordinator.acquire("living-room")
        with self.assertRaises(SessionOwnershipError):
            self.coordinator.recover_abandoned("22345678-1234-4234-8234-123456789012", True)
        self.assertTrue(self.backend.exists(self.path))
        result = self.coordinator.recover_abandoned(token, True)
        self.assertEqual(result["recovered_session_id"], token)
        self.assertTrue(result["operator_confirmed_no_process_running"])
        self.assertFalse(self.backend.exists(self.path))

    def test_failed_release_leaves_marker(self):
        token = self.coordinator.acquire("living-room")
        self.backend.fail_delete = True
        with self.assertRaises(SessionError):
            self.coordinator.release(token)
        self.assertTrue(self.backend.exists(self.path))


if __name__ == "__main__":
    unittest.main()
