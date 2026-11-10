# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock()

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
LIB_DIR = os.path.join(SERVICE_DIR, "resources", "lib")
for _path in (SERVICE_DIR, LIB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import service as sudosync_service
from resources.lib.sudosync_v2.foreign_session import (
    TITLE, describe_foreign_session, format_started_at, owner_client_name,
)

OWNER = "1a2b3c4d-1111-4222-8333-444455556666"
BLOCKED = {
    "status": "blocked",
    "owner_client_id": OWNER,
    "session_id": "7e8f9a0b-5555-4666-8777-888899990000",
    "started_at": "2026-11-08T12:48:47Z",
}


class TestForeignSessionMessage(unittest.TestCase):
    def test_format_started_at_uses_given_local_time(self):
        self.assertEqual(format_started_at("2026-11-08T12:48:47Z", localtime=time.gmtime), "08.11.2026 12:48")
        self.assertEqual(format_started_at("2026-11-08T12:48:47.123Z", localtime=time.gmtime), "08.11.2026 12:48")

    def test_format_started_at_rejects_invalid(self):
        for value in (None, "", "2026-11-08T12:48:47", "garbage Z", 5):
            self.assertIsNone(format_started_at(value))

    def test_owner_name_from_snapshot(self):
        load = MagicMock(return_value={"client": {"id": OWNER, "name": " AndroidBox "}})
        self.assertEqual(owner_client_name(load, OWNER), "AndroidBox")
        load.assert_called_once_with(OWNER)

    def test_owner_name_missing_or_failing(self):
        self.assertIsNone(owner_client_name(MagicMock(return_value=None), OWNER))
        self.assertIsNone(owner_client_name(MagicMock(side_effect=RuntimeError("smb")), OWNER))
        self.assertIsNone(owner_client_name(None, OWNER))
        self.assertIsNone(owner_client_name(MagicMock(), ""))

    def test_message_names_device_time_and_steps(self):
        title, message, log_line = describe_foreign_session(BLOCKED, "AndroidBox", localtime=time.gmtime)
        self.assertEqual(title, TITLE)
        self.assertIn("„AndroidBox”", message)
        self.assertIn("08.11.2026 12:48", message)
        self.assertIn("Wyłącz → Zakończ", message)
        self.assertIn(OWNER, log_line)
        self.assertIn(BLOCKED["session_id"], log_line)

    def test_message_without_name_uses_short_id(self):
        _title, message, _log = describe_foreign_session(BLOCKED, None, localtime=time.gmtime)
        self.assertIn("o identyfikatorze 1a2b3c4d", message)

    def test_message_tolerates_empty_recovery(self):
        _title, message, log_line = describe_foreign_session(None)
        self.assertIn("nieznanym", message)
        self.assertIn("?", log_line)


class TestServiceReportsForeignSession(unittest.TestCase):
    def setUp(self):
        sudosync_service._FOREIGN_SESSION_NOTIFIED["session_id"] = None
        self.runtime = MagicMock()
        self.runtime.catalog.load.return_value = {"client": {"id": OWNER, "name": "AndroidBox"}}

    def _report(self, recovery, playing=False):
        dialog = MagicMock()
        with patch.object(sudosync_service, "_is_playing_video", return_value=playing), \
                patch.object(sudosync_service.xbmcgui, "Dialog", return_value=dialog), \
                patch.object(sudosync_service, "log"):
            sudosync_service._report_foreign_session(self.runtime, recovery)
        return dialog

    def test_shown_once_per_session(self):
        first = self._report(dict(BLOCKED))
        first.ok.assert_called_once()
        title, message = first.ok.call_args[0]
        self.assertEqual(title, TITLE)
        self.assertIn("AndroidBox", message)
        self.assertFalse(self._report(dict(BLOCKED)).ok.called)

    def test_new_session_is_shown_again(self):
        self._report(dict(BLOCKED))
        other = dict(BLOCKED, session_id="00000000-0000-4000-8000-000000000000")
        self.assertTrue(self._report(other).ok.called)

    def test_not_shown_during_playback_then_shown(self):
        self.assertFalse(self._report(dict(BLOCKED), playing=True).ok.called)
        self.assertTrue(self._report(dict(BLOCKED)).ok.called)

    def test_ignored_when_not_blocked(self):
        for recovery in (None, {"status": "clear"}, {"status": "deferred"}):
            self.assertFalse(self._report(recovery).ok.called)

    def test_live_cycle_blocked_reports(self):
        runtime = MagicMock()
        runtime.sync_cycle.return_value = {"status": "blocked", "recovery": dict(BLOCKED)}
        monitor = MagicMock()
        monitor.scan_refresh_pending = False
        with patch.object(sudosync_service, "_report_foreign_session") as report, \
                patch.object(sudosync_service, "update_local_status_from_v2"), \
                patch.object(sudosync_service, "log"):
            result = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=runtime, monitor=monitor, show_notification=False,
                suppress_local_changes=False, collect_local=True)
        self.assertTrue(result.get("blocked"))
        report.assert_called_once_with(runtime, BLOCKED)


if __name__ == "__main__":
    unittest.main()
