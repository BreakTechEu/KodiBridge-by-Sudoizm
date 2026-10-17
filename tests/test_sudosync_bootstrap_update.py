# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
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


class TestSettingsAndIconIntegrity(unittest.TestCase):
    @staticmethod
    def _parse_po(path):
        entries = {}
        current_ctx = None
        current_id = None
        current_str = None
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line.startswith('msgctxt "#') and line.endswith('"'):
                    current_ctx = line[len('msgctxt "#'):-1]
                elif line.startswith('msgid "') and line.endswith('"'):
                    current_id = line[len('msgid "'):-1]
                elif line.startswith('msgstr "') and line.endswith('"'):
                    current_str = line[len('msgstr "'):-1]
                    if current_ctx is not None:
                        entries[current_ctx] = (current_id, current_str)
                        current_ctx = None
        return entries

    def test_settings_xml_uses_valid_numeric_string_ids_in_both_languages(self):
        import xml.etree.ElementTree as ET
        addon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
        settings_path = os.path.join(addon_dir, "resources", "settings.xml")
        en_po = self._parse_po(os.path.join(addon_dir, "resources", "language", "resource.language.en_gb", "strings.po"))
        pl_po = self._parse_po(os.path.join(addon_dir, "resources", "language", "resource.language.pl_pl", "strings.po"))

        # Every string ID in en_gb must exist in pl_pl with the exact same msgid
        for ctx, (en_msgid, en_msgstr) in en_po.items():
            self.assertIn(ctx, pl_po, "Missing msgctxt #{} in pl_pl/strings.po".format(ctx))
            pl_msgid, pl_msgstr = pl_po[ctx]
            self.assertEqual(
                en_msgid,
                pl_msgid,
                "Mismatched msgid for #{}: en='{}' vs pl='{}'".format(ctx, en_msgid, pl_msgid),
            )
            self.assertTrue(en_msgstr.strip(), "Empty msgstr for #{} in en_gb".format(ctx))
            self.assertTrue(pl_msgstr.strip(), "Empty msgstr for #{} in pl_pl".format(ctx))

        root = ET.parse(settings_path).getroot()
        for elem in root.iter():
            if elem.tag not in ("category", "group", "setting"):
                continue
            if elem.attrib.get("visible", "").lower() == "false":
                continue
            label = elem.attrib.get("label", "")
            self.assertTrue(
                label.isdigit(),
                "Non-numeric label '{}' on <{} id='{}'>".format(label, elem.tag, elem.attrib.get("id")),
            )
            self.assertIn(label, pl_po, "Label ID #{} not found in pl_pl/strings.po".format(label))
            help_id = elem.attrib.get("help", "")
            if help_id:
                self.assertTrue(
                    help_id.isdigit(),
                    "Non-numeric help '{}' on <{} id='{}'>".format(help_id, elem.tag, elem.attrib.get("id")),
                )
                self.assertIn(help_id, pl_po, "Help ID #{} not found in pl_pl/strings.po".format(help_id))

    def test_addon_xml_declares_existing_icon(self):
        import xml.etree.ElementTree as ET
        addon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
        root = ET.parse(os.path.join(addon_dir, "addon.xml")).getroot()
        icon_el = root.find("./extension[@point='xbmc.addon.metadata']/assets/icon")
        self.assertIsNotNone(icon_el)
        self.assertEqual(icon_el.text, "resources/icon.png")
        icon_path = os.path.join(addon_dir, "resources", "icon.png")
        self.assertTrue(os.path.isfile(icon_path))
        self.assertGreater(os.path.getsize(icon_path), 1024)


class TestGitHubUpdateAndNetworkVersionGuard(unittest.TestCase):
    def test_network_version_guard_promotes_newer_local_version(self):
        cfg = {
            "schema_version": 1,
            "configuration": {"completed": True},
            "network_settings": {
                "sudosync_id_enabled": False,
                "sudosync_id_prefix": "",
                "min_addon_version": "2.0.0",
            },
        }
        with patch.object(bootstrap, "_get_local_addon_version", return_value="2.1.0"), \
                patch.object(bootstrap, "write_sudosync_cfg", return_value=True) as mock_write:
            allowed = bootstrap.enforce_network_version_guard("smb://nas/video/.SudoSync/", cfg)
            self.assertTrue(allowed)
            self.assertEqual(cfg["network_settings"]["min_addon_version"], "2.1.0")
            mock_write.assert_called_once()

    def test_network_version_guard_blocks_older_local_version_and_offers_update(self):
        cfg = {
            "schema_version": 1,
            "configuration": {"completed": True},
            "network_settings": {
                "sudosync_id_enabled": False,
                "sudosync_id_prefix": "",
                "min_addon_version": "2.1.0",
            },
        }
        dialog = MagicMock()
        dialog.yesno.side_effect = [False, False]  # Użytkownik wybiera wyłączenie do czasu aktualizacji
        with patch.object(bootstrap, "_get_local_addon_version", return_value="2.0.0"), \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            allowed = bootstrap.enforce_network_version_guard("smb://nas/video/.SudoSync/", cfg)
            self.assertFalse(allowed)
            dialog.ok.assert_called_once()
            self.assertIn("2.1.0", dialog.ok.call_args[0][1])

    def test_find_github_update_parses_latest_release(self):
        import io
        import json
        fake_payload = json.dumps({
            "tag_name": "v2.1.0",
            "html_url": "https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases/tag/v2.1.0",
            "assets": [
                {
                    "name": "service.sudosync-2.1.0.zip",
                    "browser_download_url": "https://github.com/BreakTechEu/SudoSync-Kodi-addon/releases/download/v2.1.0/service.sudosync-2.1.0.zip",
                }
            ],
        }).encode("utf-8")

        resp_mock = MagicMock()
        resp_mock.read.return_value = fake_payload
        resp_mock.__enter__.return_value = resp_mock
        resp_mock.__exit__.return_value = False

        addon_mock = MagicMock()
        addon_mock.getAddonInfo.return_value = "2.0.0"

        with patch.object(core.xbmcaddon, "Addon", return_value=addon_mock), \
                patch("urllib.request.urlopen", return_value=resp_mock):
            res = core.find_github_update()
            self.assertTrue(res["available"])
            self.assertEqual(res["version"], "2.1.0")
            self.assertEqual(res["filename"], "service.sudosync-2.1.0.zip")

    def test_maybe_check_github_update_daily_respects_24h_interval_and_consent(self):
        status_store = {"last_github_update_check_epoch": 1000.0}
        dialog = MagicMock()
        dialog.yesno.return_value = False  # Użytkownik klika "Później"

        with patch.object(core, "addon_settings", return_value={"check_updates": True}), \
                patch.object(core, "read_local_status", side_effect=lambda: dict(status_store)), \
                patch.object(core, "_write_local_json", side_effect=lambda _p, data: status_store.update(data)), \
                patch.object(core, "find_github_update", return_value={
                    "available": True,
                    "version": "2.1.0",
                    "current_version": "2.0.0",
                }) as mock_find, \
                patch.object(core, "install_github_update") as mock_install, \
                patch.object(core.xbmcgui, "Dialog", return_value=dialog):
            # Przed upływem 24h (np. 1h później) -> nie odpytuje GitHuba
            early = core.maybe_check_github_update_daily(now_epoch=4600.0, ask_and_install=True)
            self.assertFalse(early.get("checked", True))
            self.assertEqual(early.get("reason"), "not_due")
            mock_find.assert_not_called()

            # Po upływie >24h (86400s) -> odpytuje GitHuba i pyta o zgodę
            due = core.maybe_check_github_update_daily(now_epoch=1000.0 + 86401.0, ask_and_install=True)
            self.assertTrue(due.get("available"))
            mock_find.assert_called_once()
            dialog.yesno.assert_called_once()
            mock_install.assert_not_called()

    def test_mask_vfs_credentials_strips_login_and_password(self):
        raw_url = "smb://user:Secret%24Pass@192.168.69.100/Wideo/.SudoSync/"
        self.assertEqual(
            core.mask_vfs_credentials(raw_url),
            "smb://192.168.69.100/Wideo/.SudoSync/",
        )
        self.assertEqual(
            bootstrap.mask_vfs_credentials(raw_url),
            "smb://192.168.69.100/Wideo/.SudoSync/",
        )

    def test_addon_settings_prefix_fallback_strips_credentials(self):
        raw_url = "smb://admin:TajneHaslo@192.168.69.100/Wideo/.SudoSync/"
        addon_mock = MagicMock()
        addon_mock.getSetting.side_effect = lambda key: raw_url if key == "base_path" else ""
        with patch.object(core.xbmcaddon, "Addon", return_value=addon_mock):
            settings = core.addon_settings()
            self.assertEqual(settings["sudosync_id_prefix"], "192.168.69.100")
            self.assertNotIn("TajneHaslo", settings["sudosync_id_prefix"])

    def test_network_active_kodi_lease_blocks_second_active_client_and_allows_expired_takeover(self):
        lock_store = {
            "client_id": "client-A",
            "client_name": "Salon",
            "updated_at_epoch": 1000.0,
        }
        dialog = MagicMock()
        with patch.object(bootstrap, "_local_client_identity", return_value=("client-B", "Sypialnia")), \
                patch.object(bootstrap, "_read_json_file", return_value=("VALID", dict(lock_store))), \
                patch.object(bootstrap, "_write_json_file", return_value=True) as mock_write, \
                patch.object(bootstrap.xbmcgui, "Dialog", return_value=dialog):
            # Lease held by client-A is only 50s old (< 180s TTL) -> blocks client-B
            allowed = bootstrap.acquire_network_active_kodi_lease("smb://nas/video/.SudoSync/", now_epoch=1050.0)
            self.assertFalse(allowed)
            dialog.ok.assert_called_once()
            self.assertIn("Salon", dialog.ok.call_args[0][1])
            mock_write.assert_not_called()

        # After TTL expires (e.g. 1300.0 - 1000.0 = 300s > 180s), client-B takes over the lease
        written = {}
        with patch.object(bootstrap, "_local_client_identity", return_value=("client-B", "Sypialnia")), \
                patch.object(bootstrap, "_read_json_file", return_value=("VALID", dict(lock_store))), \
                patch.object(bootstrap, "_write_json_file", side_effect=lambda _p, obj: written.update(obj) or True) as mock_write:
            allowed = bootstrap.acquire_network_active_kodi_lease("smb://nas/video/.SudoSync/", now_epoch=1300.0)
            self.assertTrue(allowed)
            self.assertEqual(written["client_id"], "client-B")
            self.assertEqual(written["client_name"], "Sypialnia")
            self.assertEqual(written["updated_at_epoch"], 1300.0)
            mock_write.assert_called_once()

    def test_refresh_and_release_network_active_kodi_lease(self):
        lock_store = {
            "client_id": "client-A",
            "client_name": "Salon",
            "updated_at_epoch": 1000.0,
        }
        written = {}
        with patch.object(bootstrap, "_read_json_file", return_value=("VALID", dict(lock_store))), \
                patch.object(bootstrap, "_local_client_identity", return_value=("client-A", "Salon")), \
                patch.object(bootstrap, "_write_json_file", side_effect=lambda _p, obj: written.update(obj) or True), \
                patch.object(bootstrap.xbmcvfs, "exists", return_value=True), \
                patch.object(bootstrap.xbmcvfs, "delete") as mock_delete:
            self.assertTrue(bootstrap.refresh_network_active_kodi_lease("smb://nas/video/.SudoSync/", now_epoch=1060.0))
            self.assertEqual(written["updated_at_epoch"], 1060.0)
            self.assertTrue(bootstrap.release_network_active_kodi_lease("smb://nas/video/.SudoSync/"))
            mock_delete.assert_called_once()

    def test_refresh_network_active_kodi_lease_returns_false_on_peer_conflict(self):
        lock_store = {
            "client_id": "client-Peer",
            "client_name": "Sypialnia",
            "updated_at_epoch": 1050.0,
        }
        with patch.object(bootstrap, "_read_json_file", return_value=("VALID", dict(lock_store))), \
                patch.object(bootstrap, "_local_client_identity", return_value=("client-Local", "Salon")):
            # Peer updated at 1050, now is 1080 (30s ago, well within 180s TTL) -> conflict
            self.assertFalse(bootstrap.refresh_network_active_kodi_lease("smb://nas/video/.SudoSync/", now_epoch=1080.0))

    def test_refresh_network_active_kodi_lease_returns_false_when_network_version_upgraded(self):
        cfg = {
            "schema_version": 1,
            "configuration": {"completed": True},
            "network_settings": {"min_addon_version": "2.1.0"},
        }
        with patch.object(bootstrap, "_get_local_addon_version", return_value="2.0.2"), \
                patch.object(bootstrap, "read_sudosync_cfg", return_value=("VALID", cfg)), \
                patch.object(bootstrap, "_local_client_identity", return_value=("client-Local", "Salon")):
            self.assertFalse(bootstrap.refresh_network_active_kodi_lease("smb://nas/video/.SudoSync/", now_epoch=1080.0))

    def test_service_loop_exits_when_lease_lost_on_refresh(self):
        import service as sudosync_service
        mock_monitor = MagicMock()
        mock_monitor.aborted = False
        mock_monitor.dirty = False
        mock_monitor.scanning = False
        mock_monitor.abortRequested.return_value = False

        dialog_mock = MagicMock()
        with patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=MagicMock()), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=mock_monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": "smb://nas/video/.SudoSync/",
                    "client_name": "Salon",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": False,
                }), \
                patch.object(sudosync_service, "read_shared_config", return_value={"initialization": {"completed": False}}), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "_initial_snapshot_and_analyse"), \
                patch.object(sudosync_service.time, "time", side_effect=[1000.0, 1070.0]), \
                patch("resources.lib.sudosync_bootstrap.refresh_network_active_kodi_lease", return_value=False) as mock_refresh, \
                patch.object(sudosync_service.xbmcgui, "Dialog", return_value=dialog_mock), \
                patch("resources.lib.sudosync_bootstrap.release_network_active_kodi_lease") as mock_release:
            sudosync_service._run_service()

        mock_refresh.assert_called_once()
        dialog_mock.notification.assert_called_once()
        mock_release.assert_called_once()


    def test_ensure_client_enrolled_and_initialized_populates_shared_cfg(self):
        shared_cfg = {}
        local_reg = {}

        def fake_update_shared(mutator, _base_path):
            mutator(shared_cfg)
            return shared_cfg

        with patch.object(bootstrap, "_local_client_identity", return_value=("client-1", "Salon")), \
                patch.object(core, "read_shared_config", return_value=shared_cfg), \
                patch.object(core, "update_shared_config", side_effect=fake_update_shared), \
                patch.object(core, "_live_registry_file", return_value="/tmp/live_reg.json"), \
                patch.object(core, "_read_local_json", return_value=local_reg), \
                patch.object(core, "_write_local_json", side_effect=lambda _p, data: local_reg.update(data)):
            bootstrap._ensure_client_enrolled_and_initialized("smb://nas/video/.SudoSync/")
            init = shared_cfg["initialization"]
            self.assertEqual(init["base_client_id"], "client-1")
            self.assertEqual(init["base_client_name"], "Salon")
            self.assertTrue(init["completed"])
            self.assertTrue(local_reg["bootstrapped"])
            self.assertEqual(local_reg["client_id"], "client-1")


if __name__ == "__main__":
    unittest.main()
