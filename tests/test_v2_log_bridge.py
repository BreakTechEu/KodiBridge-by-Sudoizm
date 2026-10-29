# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
from __future__ import absolute_import

import logging
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib import sudosync_core  # noqa: E402
from resources.lib.sudosync_v2 import snapshot as snapshot_module  # noqa: E402
from resources.lib.sudosync_v2.snapshot import build_snapshot  # noqa: E402

CLIENT = "12345678-1234-4234-8234-123456789012"
OTHER_CLIENT = "87654321-4321-4321-8321-210987654321"
STAMP = "2026-10-29T12:00:00Z"


def _two_part_finale():
    # One TVDB episode aired in two parts: same external IDs, distinct SudoSync IDs.
    return [
        {"episodeid": 201, "title": "Finale part 1", "uniqueid": {"tvdb": "777", "sudosync": "MAZURY-000901"}},
        {"episodeid": 202, "title": "Finale part 2", "uniqueid": {"tvdb": "777", "sudosync": "MAZURY-000902"}},
    ]


class SharedExternalIdentityLoggingTests(unittest.TestCase):
    def setUp(self):
        snapshot_module._LAST_SHARED_EXTERNAL_REPORT.clear()

    def test_shared_external_ids_are_summarized_once_without_warnings(self):
        with self.assertLogs("resources.lib.sudosync_v2.snapshot", level="DEBUG") as cm:
            snap = build_snapshot([], _two_part_finale(), CLIENT, "Living Room", generated_at=STAMP)
        self.assertEqual(len(snap["episodes"]), 2)
        self.assertFalse([r for r in cm.records if r.levelno >= logging.WARNING])
        summaries = [r for r in cm.records if r.levelno == logging.INFO]
        self.assertEqual(len(summaries), 1)
        self.assertIn("0 movie pair(s), 1 episode pair(s)", summaries[0].getMessage())

    def test_unchanged_summary_is_not_repeated_at_info(self):
        build_snapshot([], _two_part_finale(), CLIENT, "Living Room", generated_at=STAMP)
        with self.assertLogs("resources.lib.sudosync_v2.snapshot", level="DEBUG") as cm:
            build_snapshot([], _two_part_finale(), CLIENT, "Living Room", generated_at=STAMP)
        self.assertFalse([r for r in cm.records if r.levelno >= logging.INFO])

    def test_summary_is_tracked_per_client(self):
        build_snapshot([], _two_part_finale(), CLIENT, "Living Room", generated_at=STAMP)
        with self.assertLogs("resources.lib.sudosync_v2.snapshot", level="INFO") as cm:
            build_snapshot([], _two_part_finale(), OTHER_CLIENT, "Bedroom", generated_at=STAMP)
        self.assertEqual(len(cm.records), 1)

    def test_no_summary_without_shared_external_ids(self):
        episodes = [{"episodeid": 1, "title": "A", "uniqueid": {"tvdb": "1", "sudosync": "MAZURY-000001"}}]
        logger = logging.getLogger("resources.lib.sudosync_v2.snapshot")
        with patch.object(logger, "info") as info:
            build_snapshot([], episodes, CLIENT, "Living Room", generated_at=STAMP)
        info.assert_not_called()


class KodiLogBridgeTests(unittest.TestCase):
    def setUp(self):
        self.target = logging.getLogger(sudosync_core.V2_LOGGER_NAME)
        self.saved = (list(self.target.handlers), self.target.level, self.target.propagate)

    def tearDown(self):
        self.target.handlers[:] = self.saved[0]
        self.target.setLevel(self.saved[1])
        self.target.propagate = self.saved[2]

    def test_install_is_idempotent(self):
        first = sudosync_core.install_v2_log_bridge()
        second = sudosync_core.install_v2_log_bridge()
        self.assertIs(first, second)
        self.assertEqual(
            sum(isinstance(h, sudosync_core.KodiLogHandler) for h in self.target.handlers), 1
        )
        self.assertFalse(self.target.propagate)

    def test_levels_are_mapped_to_kodi_levels(self):
        sudosync_core.install_v2_log_bridge()
        child = logging.getLogger(sudosync_core.V2_LOGGER_NAME + ".snapshot")
        fake_xbmc = MagicMock()
        fake_xbmc.LOGDEBUG, fake_xbmc.LOGINFO, fake_xbmc.LOGWARNING, fake_xbmc.LOGERROR = 0, 1, 2, 4
        with patch.object(sudosync_core, "xbmc", fake_xbmc):
            child.debug("d")
            child.info("i")
            child.warning("w smb://user:secret@nas/share")
            child.error("e")
        levels = [call.args[1] for call in fake_xbmc.log.call_args_list]
        self.assertEqual(levels, [0, 1, 2, 4])
        messages = [call.args[0] for call in fake_xbmc.log.call_args_list]
        self.assertTrue(all(m.startswith("[SudoSync] v2.") for m in messages))
        self.assertNotIn("secret", messages[2])


if __name__ == "__main__":
    unittest.main()
