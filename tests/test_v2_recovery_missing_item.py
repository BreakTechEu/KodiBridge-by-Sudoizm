# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Recovery of a session whose library item was removed from Kodi after the interruption."""
import json
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.kodi_rpc import (
    JSONRPC_INVALID_PARAMS, KodiJsonRpcAdapter, KodiRpcError, is_missing_item_error,
)
from resources.lib.sudosync_v2.recovery import RecoveryRunner
from resources.lib.sudosync_v2.transaction import TransactionError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sudosync_v2_recovery import CLIENT_A, SESSION, Coordinator, Kodi, journal


def two_item_journal():
    data = journal()
    second = deepcopy(data["items"][0])
    second["kodi_id"] = 6
    second["status"] = "pending"
    data["items"].append(second)
    return data


class KodiWithoutMovie5(Kodi):
    def __init__(self, error):
        super(KodiWithoutMovie5, self).__init__()
        self.error = error

    def get_movie_details(self, kodi_id):
        if kodi_id == 5:
            raise self.error
        return super(KodiWithoutMovie5, self).get_movie_details(kodi_id)


class TestMissingItemError(unittest.TestCase):
    def test_error_code_is_kept(self):
        def execute(raw):
            request = json.loads(raw)
            return json.dumps({"jsonrpc": "2.0", "id": request["id"],
                               "error": {"code": -32602, "message": "Invalid params."}})
        adapter = KodiJsonRpcAdapter(execute)
        with self.assertRaises(KodiRpcError) as ctx:
            adapter.get_movie_details(31)
        self.assertEqual(ctx.exception.code, JSONRPC_INVALID_PARAMS)
        self.assertIn("failed (-32602): Invalid params.", str(ctx.exception))
        self.assertTrue(is_missing_item_error(ctx.exception))

    def test_other_errors_are_not_missing_items(self):
        self.assertFalse(is_missing_item_error(KodiRpcError("x", code=-32603)))
        self.assertFalse(is_missing_item_error(KodiRpcError("x")))
        self.assertFalse(is_missing_item_error(IOError("smb")))


class TestRecoveryWithRemovedItem(unittest.TestCase):
    def _runner(self, kodi):
        self.coordinator = Coordinator()
        self.journals = [two_item_journal()]
        return RecoveryRunner(
            kodi, self.coordinator,
            lambda session_id: deepcopy(self.journals[-1]),
            lambda value: self.journals.append(deepcopy(value)),
            clock=lambda: "2026-10-11T12:00:00Z",
        )

    def test_removed_item_is_superseded_and_session_finishes(self):
        kodi = KodiWithoutMovie5(KodiRpcError("Invalid params.", code=JSONRPC_INVALID_PARAMS))
        result = self._runner(kodi).recover_on_start(CLIENT_A)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["superseded"], [{"type": "movie", "kodi_id": 5, "reason": "item_missing"}])
        self.assertEqual(kodi.writes, 1)  # only movie 6
        self.assertEqual(self.coordinator.released, [SESSION])
        final = self.journals[-1]
        self.assertEqual(final["status"], "committed")
        self.assertEqual([i["status"] for i in final["items"]], ["superseded", "verified"])

    def test_other_rpc_error_keeps_session_blocked(self):
        kodi = KodiWithoutMovie5(KodiRpcError("Internal error", code=-32603))
        with self.assertRaises(TransactionError):
            self._runner(kodi).recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)
        self.assertEqual(kodi.writes, 0)
        self.assertEqual(self.journals[-1]["status"], "needs_recovery")

    def test_transport_error_keeps_session_blocked(self):
        kodi = KodiWithoutMovie5(IOError("Kodi stopped"))
        with self.assertRaises(TransactionError):
            self._runner(kodi).recover_on_start(CLIENT_A)
        self.assertTrue(self.coordinator.exists)


if __name__ == "__main__":
    unittest.main()
