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



class TestSentinelPathRemoval(unittest.TestCase):
    def test_empty_base_path_runs_folder_wizard(self):
        dialog = MagicMock()
        dialog.browse.return_value = ""  # Użytkownik anulował
        with patch.object(bootstrap, "get_base_path", return_value=""), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            result = bootstrap.run_bootstrap()
            self.assertFalse(result)
            dialog.browse.assert_called_once_with(3, "Wybierz katalog współdzielony (np. Wideo)", "network")

    def test_custom_sentinel_path_treated_normally_and_does_not_run_folder_wizard(self):
        dialog = MagicMock()
        sentinel_path = "smb://192.168.69.100/Wideo/.SudoSync/"
        with patch.object(bootstrap, "get_base_path", return_value=sentinel_path), \
                patch.object(bootstrap, "test_vfs_rw", return_value=(True, "OK")), \
                patch.object(bootstrap, "read_sudosync_cfg", return_value=("VALID", {
                    "schema_version": 1,
                    "configuration": {"completed": True},
                    "network_settings": {"sudosync_id_enabled": False, "sudosync_id_prefix": ""},
                })), \
                patch.object(bootstrap, "apply_network_settings"), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            result = bootstrap.run_bootstrap()
            self.assertTrue(result)
            dialog.browse.assert_not_called()

    def test_sentinel_path_does_not_skip_update_check(self):
        sentinel_path = "smb://192.168.69.100/Wideo/.SudoSync/"
        with patch.object(bootstrap, "get_base_path", return_value=sentinel_path), \
                patch.object(core, "check_for_update", return_value={"available": False}) as mock_check:
            result = bootstrap.check_and_enforce_updates_before_bootstrap()
            self.assertFalse(result)
            mock_check.assert_called_once()

    def test_empty_base_path_skips_update_check(self):
        with patch.object(bootstrap, "get_base_path", return_value=""), \
                patch.object(core, "check_for_update") as mock_check:
            result = bootstrap.check_and_enforce_updates_before_bootstrap()
            self.assertFalse(result)
            mock_check.assert_not_called()

    def test_core_addon_settings_empty_by_default_without_sentinel(self):
        addon_mock = MagicMock()
        addon_mock.getSetting.side_effect = lambda key: "" if key == "base_path" else ""
        with patch.object(core.xbmcaddon, "Addon", return_value=addon_mock):
            settings = core.addon_settings()
            self.assertEqual(settings["base_path"], "")

    def test_core_addon_settings_accepts_sentinel_path(self):
        sentinel_path = "smb://192.168.69.100/Wideo/.SudoSync/"
        addon_mock = MagicMock()
        addon_mock.getSetting.side_effect = lambda key: sentinel_path if key == "base_path" else ""
        with patch.object(core.xbmcaddon, "Addon", return_value=addon_mock):
            settings = core.addon_settings()
            self.assertEqual(settings["base_path"], sentinel_path)


if __name__ == "__main__":
    unittest.main()
