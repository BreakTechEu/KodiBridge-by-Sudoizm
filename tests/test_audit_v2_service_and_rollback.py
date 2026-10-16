# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure xbmc stubs exist before importing addon modules
if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = MagicMock()
if "xbmcaddon" not in sys.modules:
    sys.modules["xbmcaddon"] = MagicMock()
if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = MagicMock()
if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = MagicMock()

SERVICE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "service.sudosync")
)
LIB_DIR = os.path.abspath(
    os.path.join(SERVICE_DIR, "resources", "lib")
)
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import service as sudosync_service
import sudosync_core
from resources.lib.sudosync_v2.runtime import (
    ProcessLocalLockFactory,
    build_v2_runtime,
)
from tests.test_sudosync_v2_runtime import (
    InMemorySharedBackend,
    MutableFakeKodi,
)


class VirtualXbmcVfs(object):
    """In-memory file system adapter for xbmcvfs in SudoSync ID tests."""

    def __init__(self):
        self.files = {}

    def _norm(self, path):
        return str(path).replace("\\", "/")

    def exists(self, path):
        if not path:
            return False
        return self._norm(path) in self.files

    def delete(self, path):
        norm = self._norm(path)
        if norm in self.files:
            del self.files[norm]
            return True
        return False

    def rename(self, src, dst):
        s_norm = self._norm(src)
        d_norm = self._norm(dst)
        if s_norm not in self.files:
            return False
        self.files[d_norm] = self.files.pop(s_norm)
        return True

    def make_file(self, path, mode="r"):
        vfs = self
        norm = self._norm(path)

        class _VfsFile(object):
            def __init__(self_inner):
                self_inner.offset = 0

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc_val, exc_tb):
                self_inner.close()

            def readBytes(self_inner, size=None):
                data = vfs.files.get(norm, b"")
                if size is None or size < 0:
                    chunk = data[self_inner.offset:]
                    self_inner.offset = len(data)
                else:
                    chunk = data[self_inner.offset : self_inner.offset + size]
                    self_inner.offset += len(chunk)
                return bytearray(chunk)

            def read(self_inner, size=None):
                raw = bytes(self_inner.readBytes(size))
                return raw.decode("utf-8", errors="replace")

            def write(self_inner, payload):
                if isinstance(payload, str):
                    data = payload.encode("utf-8")
                else:
                    data = bytes(payload)
                vfs.files[norm] = data
                return True

            def close(self_inner):
                pass

        return _VfsFile()


class TestSudoSyncIdRollback(unittest.TestCase):
    def setUp(self):
        sudosync_core._NFO_ID_CACHE.clear()
        self.vfs = VirtualXbmcVfs()
        self.local_json = {}

    def tearDown(self):
        sudosync_core._NFO_ID_CACHE.clear()

    def _patch_vfs_and_json(self):
        def fake_write_local_json(path, data):
            norm = self.vfs._norm(path)
            raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.vfs.files[norm] = raw
            self.local_json[norm] = json.loads(raw.decode("utf-8"))

        def fake_read_local_json(path, default=None):
            norm = self.vfs._norm(path)
            if norm in self.local_json:
                return self.local_json[norm]
            if norm in self.vfs.files:
                return json.loads(self.vfs.files[norm].decode("utf-8"))
            return default

        return (
            patch.object(sudosync_core.xbmcvfs, "exists", side_effect=self.vfs.exists),
            patch.object(sudosync_core.xbmcvfs, "delete", side_effect=self.vfs.delete),
            patch.object(sudosync_core.xbmcvfs, "rename", side_effect=self.vfs.rename),
            patch.object(sudosync_core.xbmcvfs, "File", side_effect=self.vfs.make_file),
            patch.object(sudosync_core, "_write_local_json", side_effect=fake_write_local_json),
            patch.object(sudosync_core, "_read_local_json", side_effect=fake_read_local_json),
            patch.object(sudosync_core, "_addon_profile_path", return_value="/profile"),
        )

    def test_01_restore_preexisting_nfo_from_backup(self):
        original_nfo = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b"<movie>\n"
            b"  <title>Domowe Wideo</title>\n"
            b"  <plot>Wesele 2020</plot>\n"
            b"</movie>\n"
        )
        nfo_path = "smb://nas/movies/DomoweWideo.nfo"
        self.vfs.files[nfo_path] = original_nfo

        movies = [
            {
                "title": "Domowe Wideo",
                "file": "smb://nas/movies/DomoweWideo.mkv",
                "ids": {},
                "local": {"movieid": 101},
            }
        ]

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(sudosync_core, "addon_settings", return_value={"sudosync_id_enabled": True, "base_path": "smb://nas/.sudosync/"}), \
                patch.object(sudosync_core, "collect_library", return_value=(movies, [])), \
                patch.object(sudosync_core, "get_or_sync_network_id_and_alias", return_value="DOM"), \
                patch.object(sudosync_core, "_find_best_nfo", return_value=(nfo_path, False, False)), \
                patch.object(sudosync_core, "rpc", return_value={}):
            assign_res = sudosync_core.assign_sudosync_ids(show_notification=False)
            self.assertEqual(assign_res["movies"], 1)
            self.assertTrue(self.vfs.exists(nfo_path + ".bak"))
            self.assertIn(b'type="sudosync"', self.vfs.files[nfo_path])

            rb_res = sudosync_core.rollback_sudosync_ids(assign_res["manifest_path"])
            self.assertEqual(rb_res["rolled_back"], 1)
            self.assertEqual(rb_res["restored"], 1)
            self.assertEqual(rb_res["deleted"], 0)
            self.assertEqual(rb_res["errors"], 0)
            self.assertEqual(self.vfs.files[nfo_path], original_nfo)
            self.assertFalse(self.vfs.exists(nfo_path + ".bak"))

    def test_02_safely_delete_unmodified_created_nfo(self):
        nfo_path = "smb://nas/movies/NoweNagranie.nfo"
        movies = [
            {
                "title": "Nowe Nagranie",
                "file": "smb://nas/movies/NoweNagranie.mkv",
                "ids": {},
                "local": {"movieid": 102},
            }
        ]

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(sudosync_core, "addon_settings", return_value={"sudosync_id_enabled": True, "base_path": "smb://nas/.sudosync/"}), \
                patch.object(sudosync_core, "collect_library", return_value=(movies, [])), \
                patch.object(sudosync_core, "get_or_sync_network_id_and_alias", return_value="DOM"), \
                patch.object(sudosync_core, "_find_best_nfo", return_value=(nfo_path, False, False)), \
                patch.object(sudosync_core, "rpc", return_value={}):
            assign_res = sudosync_core.assign_sudosync_ids(show_notification=False)
            self.assertEqual(assign_res["movies"], 1)
            self.assertTrue(self.vfs.exists(nfo_path))
            self.assertFalse(self.vfs.exists(nfo_path + ".bak"))

            manifest = self.local_json["/profile/assign_ids_manifest.json"]
            entry = manifest["modified"][0]
            self.assertIsNone(entry["backup"])
            self.assertEqual(entry["action"], "created")
            self.assertTrue(entry["created"])
            self.assertTrue(entry["sudosync_id"].startswith("DOM-"))
            self.assertEqual(len(entry["content_sha256"]), 64)

            rb_res = sudosync_core.rollback_sudosync_ids(assign_res["manifest_path"])
            self.assertEqual(rb_res["rolled_back"], 1)
            self.assertEqual(rb_res["deleted"], 1)
            self.assertEqual(rb_res["restored"], 0)
            self.assertEqual(rb_res["errors"], 0)
            self.assertFalse(self.vfs.exists(nfo_path))

    def test_03_protect_created_nfo_modified_later_by_user_or_scraper(self):
        nfo_path = "smb://nas/movies/NagranieScraper.nfo"
        movies = [
            {
                "title": "Nagranie Scraper",
                "file": "smb://nas/movies/NagranieScraper.mkv",
                "ids": {},
                "local": {"movieid": 103},
            }
        ]

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(sudosync_core, "addon_settings", return_value={"sudosync_id_enabled": True, "base_path": "smb://nas/.sudosync/"}), \
                patch.object(sudosync_core, "collect_library", return_value=(movies, [])), \
                patch.object(sudosync_core, "get_or_sync_network_id_and_alias", return_value="DOM"), \
                patch.object(sudosync_core, "_find_best_nfo", return_value=(nfo_path, False, False)), \
                patch.object(sudosync_core, "rpc", return_value={}):
            assign_res = sudosync_core.assign_sudosync_ids(show_notification=False)
            self.assertTrue(self.vfs.exists(nfo_path))

            # Simulate user/scraper adding metadata after SudoSync created the file
            modified_content = self.vfs.files[nfo_path].replace(
                b"</movie>",
                b"  <plot>Dodany opis przez uzytkownika</plot>\n</movie>",
            )
            self.vfs.files[nfo_path] = modified_content

            rb_res = sudosync_core.rollback_sudosync_ids(assign_res["manifest_path"])
            self.assertEqual(rb_res["rolled_back"], 0)
            self.assertEqual(rb_res["deleted"], 0)
            self.assertEqual(rb_res["skipped_modified"], 1)
            self.assertEqual(rb_res["errors"], 1)
            self.assertEqual(rb_res["error_details"][0]["reason"], "file_modified")
            # File must be preserved intact
            self.assertTrue(self.vfs.exists(nfo_path))
            self.assertEqual(self.vfs.files[nfo_path], modified_content)

    def test_04_protect_unverifiable_legacy_manifest_entry_with_backup_none(self):
        nfo_path = "smb://nas/movies/StaryManifest.nfo"
        self.vfs.files[nfo_path] = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            b"<movie>\n"
            b"  <title>Stary Manifest</title>\n"
            b'  <uniqueid type="sudosync" default="true">DOM-112233445566</uniqueid>\n'
            b"</movie>\n"
        )
        manifest_path = "/profile/assign_ids_manifest.json"
        legacy_manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {"file": nfo_path, "backup": None, "title": "Stary Manifest"}
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            sudosync_core._write_local_json(manifest_path, legacy_manifest)
            rb_res = sudosync_core.rollback_sudosync_ids(manifest_path)

            # Without provenance hash or sudosync_id in manifest, rollback must NOT blindly delete
            self.assertEqual(rb_res["rolled_back"], 0)
            self.assertEqual(rb_res["deleted"], 0)
            self.assertEqual(rb_res["errors"], 1)
            self.assertEqual(rb_res["error_details"][0]["reason"], "unverifiable_created_file")
            self.assertTrue(self.vfs.exists(nfo_path))

    def test_05_missing_backup_and_partial_rollback_failure_with_error_reporting(self):
        # 4 entries in one manifest:
        # 1) Pre-existing file with valid .bak -> restored
        # 2) Pre-existing file with missing .bak -> preserved, error reported (missing_backup)
        # 3) Created file unmodified -> deleted
        # 4) Created file modified later -> preserved, error reported (file_modified)
        file_ok_mod = "smb://nas/movies/ModOk.nfo"
        file_missing_bak = "smb://nas/movies/ModMissingBak.nfo"
        file_ok_created = "smb://nas/movies/CreatedOk.nfo"
        file_edited_created = "smb://nas/movies/CreatedEdited.nfo"

        self.vfs.files[file_ok_mod] = b"<movie><title>ModOk</title><uniqueid type=\"sudosync\">DOM-1</uniqueid></movie>"
        self.vfs.files[file_ok_mod + ".bak"] = b"<movie><title>ModOk</title></movie>"

        self.vfs.files[file_missing_bak] = b"<movie><title>ModMissingBak</title><uniqueid type=\"sudosync\">DOM-2</uniqueid></movie>"

        created_ok_bytes = sudosync_core._build_created_nfo_xml("movie", "CreatedOk", "DOM-333333333333").encode("utf-8")
        self.vfs.files[file_ok_created] = created_ok_bytes

        created_edited_orig = sudosync_core._build_created_nfo_xml("movie", "CreatedEdited", "DOM-444444444444").encode("utf-8")
        self.vfs.files[file_edited_created] = created_edited_orig.replace(b"CreatedEdited", b"Zmieniony Tytul")

        manifest_path = "/profile/assign_ids_manifest.json"
        manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {
                    "file": file_ok_mod,
                    "backup": file_ok_mod + ".bak",
                    "action": "modified",
                    "created": False,
                    "sudosync_id": "DOM-1",
                    "content_sha256": hashlib.sha256(self.vfs.files[file_ok_mod]).hexdigest(),
                },
                {
                    "file": file_missing_bak,
                    "backup": file_missing_bak + ".bak",
                    "action": "modified",
                    "created": False,
                    "sudosync_id": "DOM-2",
                    "content_sha256": hashlib.sha256(self.vfs.files[file_missing_bak]).hexdigest(),
                },
                {
                    "file": file_ok_created,
                    "backup": None,
                    "action": "created",
                    "created": True,
                    "type": "movie",
                    "sudosync_id": "DOM-333333333333",
                    "content_sha256": hashlib.sha256(created_ok_bytes).hexdigest(),
                    "title": "CreatedOk",
                },
                {
                    "file": file_edited_created,
                    "backup": None,
                    "action": "created",
                    "created": True,
                    "type": "movie",
                    "sudosync_id": "DOM-444444444444",
                    "content_sha256": hashlib.sha256(created_edited_orig).hexdigest(),
                    "title": "CreatedEdited",
                },
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            sudosync_core._write_local_json(manifest_path, manifest)
            rb_res = sudosync_core.rollback_sudosync_ids(manifest_path)

            self.assertEqual(rb_res["rolled_back"], 2)
            self.assertEqual(rb_res["restored"], 1)
            self.assertEqual(rb_res["deleted"], 1)
            self.assertEqual(rb_res["skipped_modified"], 1)
            self.assertEqual(rb_res["errors"], 2)
            self.assertEqual(len(rb_res["error_details"]), 2)

            reasons = {item["file"]: item["reason"] for item in rb_res["error_details"]}
            self.assertEqual(reasons[file_missing_bak], "missing_backup")
            self.assertEqual(reasons[file_edited_created], "file_modified")

            # Verify file states after partial failure
            self.assertEqual(self.vfs.files[file_ok_mod], b"<movie><title>ModOk</title></movie>")
            self.assertTrue(self.vfs.exists(file_missing_bak))
            self.assertFalse(self.vfs.exists(file_ok_created))
            self.assertTrue(self.vfs.exists(file_edited_created))

            with self.assertRaises(RuntimeError):
                sudosync_core.rollback_sudosync_ids("/profile/nonexistent_manifest.json")

    def test_06_modified_nfo_edited_later_by_user_or_scraper_is_protected(self):
        """Modified .nfo whose content was changed after SudoSync ID assignment must not be overwritten."""
        nfo_path = "smb://nas/movies/Film.nfo"
        bak_path = nfo_path + ".bak"
        original_nfo = b"<movie><title>Film</title><plot>Kopia zapasowa</plot></movie>"
        sudosync_written_nfo = b"<movie><title>Film</title><uniqueid type=\"sudosync\">DOM-123</uniqueid></movie>"
        scraper_modified_nfo = b"<movie><title>Film</title><plot>Nowy opis pobrany przez scraper</plot></movie>"

        self.vfs.files[nfo_path] = scraper_modified_nfo
        self.vfs.files[bak_path] = original_nfo

        manifest_path = "/profile/assign_ids_manifest.json"
        manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {
                    "file": nfo_path,
                    "backup": bak_path,
                    "action": "modified",
                    "created": False,
                    "sudosync_id": "DOM-123",
                    "content_sha256": hashlib.sha256(sudosync_written_nfo).hexdigest(),
                    "title": "Film",
                }
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            sudosync_core._write_local_json(manifest_path, manifest)
            res = sudosync_core.rollback_sudosync_ids(manifest_path)

            self.assertEqual(res["rolled_back"], 0)
            self.assertEqual(res["restored"], 0)
            self.assertEqual(res["skipped_modified"], 1)
            self.assertEqual(res["errors"], 1)
            self.assertEqual(res["error_details"][0]["reason"], "file_modified")
            # Both files must remain untouched
            self.assertEqual(self.vfs.files[nfo_path], scraper_modified_nfo)
            self.assertEqual(self.vfs.files[bak_path], original_nfo)

    def test_07_legacy_manifest_without_content_sha256_for_modified_is_protected(self):
        """Modified .nfo in a legacy manifest without content_sha256 must not be overwritten."""
        nfo_path = "smb://nas/movies/LegacyModified.nfo"
        bak_path = nfo_path + ".bak"
        self.vfs.files[nfo_path] = b"<movie><title>Current</title></movie>"
        self.vfs.files[bak_path] = b"<movie><title>Backup</title></movie>"

        manifest_path = "/profile/assign_ids_manifest.json"
        manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {
                    "file": nfo_path,
                    "backup": bak_path,
                    "action": "modified",
                    "created": False,
                    "title": "LegacyModified",
                }
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            sudosync_core._write_local_json(manifest_path, manifest)
            res = sudosync_core.rollback_sudosync_ids(manifest_path)

            self.assertEqual(res["rolled_back"], 0)
            self.assertEqual(res["restored"], 0)
            self.assertEqual(res["errors"], 1)
            self.assertEqual(res["error_details"][0]["reason"], "unverifiable_modified_file")
            self.assertEqual(self.vfs.files[nfo_path], b"<movie><title>Current</title></movie>")
            self.assertEqual(self.vfs.files[bak_path], b"<movie><title>Backup</title></movie>")

    def test_08_failed_restore_keeps_target_nfo_intact(self):
        """If restore operation fails (e.g. exception during rename), the target .nfo file is never lost."""
        nfo_path = "smb://nas/movies/FailSwap.nfo"
        bak_path = nfo_path + ".bak"
        target_content = b"<movie><title>Target Content</title></movie>"
        backup_content = b"<movie><title>Backup Content</title></movie>"

        self.vfs.files[nfo_path] = target_content
        self.vfs.files[bak_path] = backup_content

        manifest_path = "/profile/assign_ids_manifest.json"
        manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {
                    "file": nfo_path,
                    "backup": bak_path,
                    "action": "modified",
                    "created": False,
                    "sudosync_id": "DOM-999",
                    "content_sha256": hashlib.sha256(target_content).hexdigest(),
                }
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                patch.object(sudosync_core, "_safe_restore_modified_nfo", side_effect=OSError("Disk write error")):
            sudosync_core._write_local_json(manifest_path, manifest)
            res = sudosync_core.rollback_sudosync_ids(manifest_path)

            self.assertEqual(res["rolled_back"], 0)
            self.assertEqual(res["restored"], 0)
            self.assertEqual(res["errors"], 1)
            self.assertEqual(res["error_details"][0]["reason"], "restore_failed")
            # Target content must remain intact on disk
            self.assertEqual(self.vfs.files[nfo_path], target_content)

    def test_09_partial_failure_with_modified_sha256_or_replace_error_processes_remaining(self):
        """A failure on one entry must not prevent processing the remaining valid entries."""
        file_a = "smb://nas/movies/A.nfo"
        file_b = "smb://nas/movies/B.nfo"
        file_c = "smb://nas/movies/C.nfo"

        content_a_sudosync = b"<movie><title>A</title><uniqueid type=\"sudosync\">DOM-A</uniqueid></movie>"
        content_a_edited = b"<movie><title>A</title><plot>Scraped plot</plot></movie>"
        content_b = b"<movie><title>B</title><uniqueid type=\"sudosync\">DOM-B</uniqueid></movie>"
        content_c = b"<movie><title>C</title><uniqueid type=\"sudosync\">DOM-C</uniqueid></movie>"

        self.vfs.files[file_a] = content_a_edited
        self.vfs.files[file_a + ".bak"] = b"<movie><title>A-Backup</title></movie>"

        self.vfs.files[file_b] = content_b
        self.vfs.files[file_b + ".bak"] = b"<movie><title>B-Backup</title></movie>"

        self.vfs.files[file_c] = content_c
        self.vfs.files[file_c + ".bak"] = b"<movie><title>C-Backup</title></movie>"

        manifest_path = "/profile/assign_ids_manifest.json"
        manifest = {
            "generated_at": "2026-10-16T12:00:00.000Z",
            "modified": [
                {
                    "file": file_a,
                    "backup": file_a + ".bak",
                    "action": "modified",
                    "created": False,
                    "content_sha256": hashlib.sha256(content_a_sudosync).hexdigest(),
                },
                {
                    "file": file_b,
                    "backup": file_b + ".bak",
                    "action": "modified",
                    "created": False,
                    "content_sha256": hashlib.sha256(content_b).hexdigest(),
                },
                {
                    "file": file_c,
                    "backup": file_c + ".bak",
                    "action": "modified",
                    "created": False,
                    # Missing content_sha256 -> fails closed
                },
            ],
            "errors": [],
        }

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            sudosync_core._write_local_json(manifest_path, manifest)
            res = sudosync_core.rollback_sudosync_ids(manifest_path)

            self.assertEqual(res["rolled_back"], 1)
            self.assertEqual(res["restored"], 1)
            self.assertEqual(res["skipped_modified"], 1)
            self.assertEqual(res["errors"], 2)

            # File B was restored
            self.assertEqual(self.vfs.files[file_b], b"<movie><title>B-Backup</title></movie>")
            # Files A and C were protected
            self.assertEqual(self.vfs.files[file_a], content_a_edited)
            self.assertEqual(self.vfs.files[file_c], content_c)

    def test_10_safe_restore_helper_recovers_target_if_rename_fails(self):
        """_safe_restore_modified_nfo recovers target file from safety copy if backup swap fails."""
        nfo_path = "smb://nas/movies/SwapFail.nfo"
        bak_path = nfo_path + ".bak"
        target_content = b"<movie><title>Target Content</title></movie>"
        backup_content = b"<movie><title>Backup Content</title></movie>"

        self.vfs.files[nfo_path] = target_content
        self.vfs.files[bak_path] = backup_content

        patches = self._patch_vfs_and_json()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            orig_rename = self.vfs.rename

            def failing_rename(src, dst):
                if src == bak_path:
                    return False
                return orig_rename(src, dst)

            with patch.object(sudosync_core.xbmcvfs, "rename", side_effect=failing_rename):
                with self.assertRaises(IOError):
                    sudosync_core._safe_restore_modified_nfo(bak_path, nfo_path)

            # Target content must remain restored from the safety copy and not lost
            self.assertEqual(self.vfs.files[nfo_path], target_content)
            self.assertEqual(self.vfs.files[bak_path], backup_content)


class TestServiceV2Integration(unittest.TestCase):
    def setUp(self):
        self.backend = InMemorySharedBackend()
        self.base_path = "smb://nas/sudosync"
        self.client_id = "11111111-1111-4111-8111-111111111111"
        self.peer_id = "22222222-2222-4222-8222-222222222222"

    def test_01_service_loop_executes_v2_sync_cycle_and_commits_peer_changes(self):
        """Verify normal _run_service loop executes v2 sync_cycle, applies remote changes via transaction runner with backup/journal/verification, and coordinates with v1."""
        lock_factory = ProcessLocalLockFactory()
        kodi_local = MutableFakeKodi(movie_id=10, playcount=0)
        kodi_peer = MutableFakeKodi(movie_id=99, playcount=0)

        v2_local = build_v2_runtime(
            kodi_local,
            self.backend,
            self.base_path,
            lock_factory,
            self.client_id,
            "Salon",
        )
        v2_peer = build_v2_runtime(
            kodi_peer,
            self.backend,
            self.base_path,
            lock_factory,
            self.peer_id,
            "Sypialnia",
        )

        # 1. Device A publishes initial baseline
        v2_local.publish_local_snapshot()
        # 2. Device B initializes from Device A
        v2_peer.apply_initialization(self.client_id)
        # 3. Device B updates playcount to 4 and publishes to catalog
        kodi_peer.playcount = 4
        v2_peer.sync_cycle()

        v1_calls = []

        def fake_v1_live_cycle(**kwargs):
            v1_calls.append(kwargs)
            return {"applied_items": 0, "busy": False}

        mock_monitor = MagicMock()
        mock_monitor.aborted = False

        def fake_wait_for_abort(timeout=None):
            mock_monitor.aborted = True
            return True

        mock_monitor.abortRequested.side_effect = lambda: mock_monitor.aborted
        mock_monitor.waitForAbort.side_effect = fake_wait_for_abort
        mock_monitor.dirty = False
        mock_monitor.dirty_since = 0.0
        mock_monitor.scanning = False
        mock_monitor.scan_refresh_pending = False
        mock_monitor.playback_dirty = False
        mock_monitor.user_update_dirty = False

        with patch("resources.lib.sudosync_bootstrap.check_and_enforce_updates_before_bootstrap", return_value="ok"), \
                patch("resources.lib.sudosync_bootstrap.run_bootstrap", return_value=True), \
                patch("resources.lib.sudosync_v2.runtime.build_kodi_v2_runtime", return_value=v2_local), \
                patch.object(sudosync_service, "SudoSyncMonitor", return_value=mock_monitor), \
                patch.object(sudosync_service, "addon_settings", return_value={
                    "base_path": self.base_path,
                    "client_name": "Salon",
                    "interval_seconds": 300,
                    "live_poll_seconds": 60,
                    "live_sync_enabled": True,
                    "sudosync_id_enabled": True,
                    "sudosync_id_prefix": "DOM",
                }), \
                patch.object(sudosync_service, "get_or_create_client_id", return_value=self.client_id), \
                patch.object(sudosync_service, "sync_initial_base_selection"), \
                patch.object(sudosync_service, "read_shared_config", return_value={
                    "initialization": {"completed": True, "base_client_id": self.client_id}
                }), \
                patch.object(sudosync_service, "run_live_sync_cycle", side_effect=fake_v1_live_cycle), \
                patch.object(sudosync_service, "_is_playing_video", return_value=False):
            sudosync_service._run_service()

        # Verify v2 transaction runner applied the peer changes to Kodi
        self.assertEqual(kodi_local.writes, 1)
        self.assertEqual(kodi_local.playcount, 4)

        # Verify updated local v2 snapshot was published into the catalog
        updated_snap = v2_local.catalog.load(self.client_id)
        self.assertEqual(updated_snap["movies"][0]["state"]["playcount"], 4)

        # Verify v1 live cycle was invoked with suppress_local_changes=True after v2 commit
        # so v1 does not treat v2's writes as new local user edits.
        self.assertGreaterEqual(len(v1_calls), 1)
        self.assertTrue(v1_calls[0]["suppress_local_changes"])
        self.assertTrue(v1_calls[0]["collect_local"])

    def test_02_service_defers_remote_writes_during_playback_and_publishes_local_snapshot(self):
        """During active playback, remote changes are deferred while local progress is published to v2 catalog."""
        lock_factory = ProcessLocalLockFactory()
        kodi_local = MutableFakeKodi(movie_id=10, playcount=0)
        kodi_local.resume = {"position": 600.0, "total": 3600.0}
        kodi_local.playing = True

        kodi_peer = MutableFakeKodi(movie_id=99, playcount=2)
        v2_local = build_v2_runtime(
            kodi_local,
            self.backend,
            self.base_path,
            lock_factory,
            self.client_id,
            "Salon",
        )
        v2_peer = build_v2_runtime(
            kodi_peer,
            self.backend,
            self.base_path,
            lock_factory,
            self.peer_id,
            "Sypialnia",
        )
        v2_peer.sync_cycle()

        monitor = MagicMock()
        monitor.abortRequested.return_value = False
        monitor.dirty = False
        monitor.dirty_since = 0.0
        monitor.scanning = False
        monitor.scan_refresh_pending = False
        monitor.playback_dirty = False
        monitor.user_update_dirty = False

        with patch.object(sudosync_service, "run_live_sync_cycle", return_value={"applied_items": 0}):
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=v2_local,
                monitor=monitor,
                show_notification=False,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )

        self.assertEqual(res["v2"]["status"], "deferred")
        self.assertEqual(kodi_local.writes, 0)
        self.assertEqual(kodi_local.playcount, 0)

        # Meanwhile, local snapshot publishing during playback succeeds without writing to Kodi
        pub_res = v2_local.publish_local_snapshot(suppress_local_changes=False)
        self.assertEqual(pub_res["status"], "published")
        loaded = v2_local.catalog.load(self.client_id)
        self.assertEqual(loaded["movies"][0]["state"]["resume"]["position"], 600)
        self.assertEqual(kodi_local.writes, 0)

    def test_03_blocked_v2_session_prevents_live_cycle_writes(self):
        """If v2 recovery reports a blocked session marker, _execute_integrated_live_cycle halts before v1 writes."""
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.return_value = {
            "status": "blocked",
            "recovery": {"status": "blocked", "reason": "unrecovered_journal"},
            "applied_count": 0,
        }
        monitor = MagicMock()
        monitor.abortRequested.return_value = False

        with patch.object(sudosync_service, "run_live_sync_cycle") as mock_v1:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )
            self.assertTrue(res["blocked"])
            self.assertTrue(res["busy"])
            self.assertEqual(res["applied_items"], 0)
            mock_v1.assert_not_called()

    def test_04_pre_initialization_publishes_v2_snapshot_and_runs_readonly_preview(self):
        """Before initial sync is completed, _initial_snapshot_and_analyse publishes v2 snapshot and runs read-only initialization_preview without modifying Kodi."""
        lock_factory = ProcessLocalLockFactory()
        kodi_local = MutableFakeKodi(movie_id=10, playcount=0)
        kodi_base = MutableFakeKodi(movie_id=1, playcount=3)

        v2_local = build_v2_runtime(
            kodi_local,
            self.backend,
            self.base_path,
            lock_factory,
            self.client_id,
            "Salon",
        )
        v2_base = build_v2_runtime(
            kodi_base,
            self.backend,
            self.base_path,
            lock_factory,
            self.peer_id,
            "Baza",
        )
        v2_base.publish_local_snapshot()

        with patch.object(sudosync_service, "collect_and_write"), \
                patch.object(sudosync_service, "run_dry_run"):
            sudosync_service._initial_snapshot_and_analyse(
                v2_runtime=v2_local,
                abort_requested=lambda: False,
                base_client_id=self.peer_id,
            )

        # Local v2 snapshot was published, and 0 Kodi writes were executed (user approval required first)
        self.assertIsNotNone(v2_local.catalog.load(self.client_id))
        self.assertEqual(kodi_local.writes, 0)
        self.assertEqual(kodi_local.playcount, 0)

    def test_05_v2_exception_prevents_v1_call(self):
        """If v2_runtime.sync_cycle() raises any exception, v1 sync MUST NOT be called."""
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.side_effect = RuntimeError("V2 storage corrupted")
        monitor = MagicMock()
        monitor.abortRequested.return_value = False

        with patch.object(sudosync_service, "run_live_sync_cycle") as mock_v1:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )

            self.assertTrue(res["busy"])
            self.assertEqual(res["error"], "V2 storage corrupted")
            self.assertEqual(res["applied_items"], 0)
            mock_v1.assert_not_called()

    def test_06_invalid_or_unknown_v2_result_prevents_v1_call(self):
        """If v2_runtime.sync_cycle() returns None, non-dict, or unknown status, v1 sync MUST NOT be called."""
        monitor = MagicMock()
        monitor.abortRequested.return_value = False

        # Case A: returns None
        mock_v2 = MagicMock()
        mock_v2.sync_cycle.return_value = None
        with patch.object(sudosync_service, "run_live_sync_cycle") as mock_v1:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )
            self.assertTrue(res["busy"])
            self.assertEqual(res["error"], "invalid_v2_result")
            mock_v1.assert_not_called()

        # Case B: returns non-dict
        mock_v2.sync_cycle.return_value = "invalid_string_result"
        with patch.object(sudosync_service, "run_live_sync_cycle") as mock_v1:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )
            self.assertTrue(res["busy"])
            self.assertEqual(res["error"], "invalid_v2_result")
            mock_v1.assert_not_called()

        # Case C: returns unexpected status
        mock_v2.sync_cycle.return_value = {"status": "corrupt_state", "applied_count": 0}
        with patch.object(sudosync_service, "run_live_sync_cycle") as mock_v1:
            res = sudosync_service._execute_integrated_live_cycle(
                v2_runtime=mock_v2,
                monitor=monitor,
                show_notification=True,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=0,
            )
            self.assertTrue(res["busy"])
            self.assertEqual(res["error"], "unexpected_v2_status")
            mock_v1.assert_not_called()


if __name__ == "__main__":
    unittest.main()
