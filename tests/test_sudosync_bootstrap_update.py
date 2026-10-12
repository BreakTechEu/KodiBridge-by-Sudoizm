import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.modules.setdefault("xbmc", MagicMock())
sys.modules.setdefault("xbmcaddon", MagicMock())
sys.modules.setdefault("xbmcgui", MagicMock())
sys.modules.setdefault("xbmcvfs", MagicMock())
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

import resources.lib.sudosync_bootstrap as bootstrap
import resources.lib.sudosync_core as core


class TestMandatoryUpdateStartup(unittest.TestCase):
    def setUp(self):
        self.path = patch.object(bootstrap, "get_base_path", return_value="smb://nas/video/.SudoSync/")
        self.path.start()
        self.addCleanup(self.path.stop)

    def test_no_update_allows_normal_start(self):
        with patch.object(core, "check_for_update", return_value={"available": False}), \
                patch.object(core, "install_latest_update") as install:
            self.assertFalse(bootstrap.check_and_enforce_updates_before_bootstrap())
        install.assert_not_called()

    def test_rejected_new_zip_shows_message_and_blocks_start(self):
        dialog = MagicMock()
        with patch.object(core, "check_for_update", return_value={
            "available": True, "filename": "service.sudosync-2.0.0.zip",
        }), patch.object(core, "install_latest_update", side_effect=ValueError("invalid signature")), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            result = bootstrap.check_and_enforce_updates_before_bootstrap()

        self.assertEqual(result, "blocked")
        dialog.ok.assert_called_once()
        message = dialog.ok.call_args[0][1]
        self.assertIn("service.sudosync-2.0.0.zip", message)
        self.assertIn("nie można potwierdzić jego autentyczności", message)

    def test_successful_update_requests_restart(self):
        with patch.object(core, "check_for_update", return_value={"available": True}), \
                patch.object(core, "install_latest_update", return_value={"installed": True, "version": "2.0.0"}), \
                patch.object(bootstrap.xbmc, "sleep"), patch.object(bootstrap.xbmc, "executebuiltin") as builtin:
            result = bootstrap.check_and_enforce_updates_before_bootstrap()

        self.assertEqual(result, "restart")
        builtin.assert_called_once_with("RestartApp")


if __name__ == "__main__":
    unittest.main()
