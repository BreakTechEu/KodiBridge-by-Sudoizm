"""Tests for startup ordering in the SudoSync v2 runtime."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.runtime import (
    V2RuntimeError, V2ServiceRuntime, build_mapped_native_lock_factory,
    build_v2_runtime, map_shared_resource_to_native,
)


class Recovery(object):
    def __init__(self, events, result=None, error=None):
        self.events = events
        self.result = result or {"status": "clear"}
        self.error = error
        self.calls = []

    def recover_on_start(self, client_id):
        self.calls.append(client_id)
        self.events.append("recovery")
        if self.error:
            raise self.error
        return self.result


class Engine(object):
    def __init__(self, events):
        self.events = events
        self.calls = 0

    def preview(self):
        self.calls += 1
        self.events.append("preview")
        return {"active": True, "plan": {"changes": []}}


class TestV2ServiceRuntime(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.recovery = Recovery(self.events)
        self.engine = Engine(self.events)
        self.runtime = V2ServiceRuntime("kodi-client", self.recovery, self.engine)

    def test_clear_marker_allows_preview_only_after_recovery_check(self):
        result = self.runtime.start()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.recovery.calls, ["kodi-client"])
        self.assertEqual(self.engine.calls, 1)
        self.assertEqual(self.events, ["recovery", "preview"])
        self.assertEqual(result["preview"]["active"], True)

    def test_successful_recovery_precedes_preview(self):
        self.recovery.result = {"status": "recovered", "session_id": "session"}
        result = self.runtime.start()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.engine.calls, 1)

    def test_blocked_marker_prevents_preview(self):
        self.recovery.result = {"status": "blocked", "owner_client_id": "other"}
        result = self.runtime.start()
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["preview"])
        self.assertEqual(self.engine.calls, 0)

    def test_active_playback_during_recovery_defers_and_prevents_preview(self):
        self.recovery.result = {"status": "deferred", "reason": "video_playing"}
        result = self.runtime.start()
        self.assertEqual(result["status"], "deferred")
        self.assertIsNone(result["preview"])
        self.assertEqual(self.engine.calls, 0)

    def test_recovery_failure_prevents_preview(self):
        self.recovery.error = IOError("shared journal unavailable")
        with self.assertRaisesRegex(IOError, "shared journal unavailable"):
            self.runtime.start()
        self.assertEqual(self.engine.calls, 0)

    def test_unknown_recovery_status_fails_closed(self):
        self.recovery.result = {"status": "unknown"}
        with self.assertRaisesRegex(V2RuntimeError, "unsupported startup status"):
            self.runtime.start()
        self.assertEqual(self.engine.calls, 0)


class Lock(object):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class SharedBackend(object):
    def exists(self, path):
        return False

    def read_text(self, path):
        raise AssertionError("unexpected read")

    def write_text(self, path, text):
        raise AssertionError("startup preview must not write shared files")

    def rename(self, source, destination):
        raise AssertionError("startup preview must not rename shared files")

    def delete(self, path):
        raise AssertionError("startup preview must not delete shared files")

    def mkdirs(self, path):
        raise AssertionError("startup preview must not create shared directories")

    def listdir(self, path):
        return [], []


class KodiReadOnly(object):
    def is_video_playing(self):
        return False

    def get_movies(self):
        return []

    def get_episodes(self):
        return []


class TestV2RuntimeComposition(unittest.TestCase):
    def test_factory_composes_recovery_and_read_only_preview(self):
        lock_paths = []

        def lock_factory(path):
            lock_paths.append(path)
            return Lock()

        components = build_v2_runtime(
            KodiReadOnly(), SharedBackend(), "smb://nas/SudoSync",
            lock_factory, "12345678-1234-4234-8234-123456789012", "Kodi A",
        )
        result = components.start()

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["recovery"]["status"], "clear")
        self.assertTrue(result["preview"]["active"])
        self.assertEqual(result["preview"]["plan"]["change_count"], 0)
        self.assertIn("smb://nas/SudoSync/transactions/active-operation.json", lock_paths)

    def test_factory_requires_a_lock_provider(self):
        with self.assertRaisesRegex(ValueError, "lock factory are required"):
            build_v2_runtime(
                KodiReadOnly(), SharedBackend(), "smb://nas/SudoSync", None,
                "12345678-1234-4234-8234-123456789012", "Kodi A",
            )

    def test_vfs_resource_maps_under_the_configured_native_mount(self):
        native_root = os.path.abspath(os.path.join(os.getcwd(), "mount"))
        result = map_shared_resource_to_native(
            "smb://nas/SudoSync/",
            native_root,
            "smb://nas/SudoSync/transactions/journal.json",
        )
        self.assertEqual(result, os.path.join(native_root, "transactions", "journal.json"))

    def test_vfs_mapping_rejects_traversal_and_resources_outside_root(self):
        native_root = os.path.abspath(os.getcwd())
        with self.assertRaisesRegex(ValueError, "unsafe path segment"):
            map_shared_resource_to_native("smb://nas/SudoSync", native_root, "smb://nas/SudoSync/../outside")
        with self.assertRaisesRegex(ValueError, "unsafe path segment"):
            map_shared_resource_to_native("smb://nas/SudoSync", native_root, "smb://nas/SudoSync/..\\\\outside")
        with self.assertRaisesRegex(ValueError, "unsafe path segment"):
            map_shared_resource_to_native("smb://nas/SudoSync", native_root, "smb://nas/SudoSync/drive:C")
        with self.assertRaisesRegex(ValueError, "outside the configured VFS root"):
            map_shared_resource_to_native("smb://nas/SudoSync", native_root, "smb://nas/Other/file")

    def test_vfs_mapping_rejects_symlink_escape_from_native_mount(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as temp_root:
            mount_root = os.path.join(temp_root, "mount")
            outside_root = os.path.join(temp_root, "outside")
            os.makedirs(mount_root)
            os.makedirs(outside_root)
            link_path = os.path.join(mount_root, "transactions")
            try:
                os.symlink(outside_root, link_path, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest("directory symlinks are unavailable: {}".format(exc))

            with self.assertRaisesRegex(ValueError, "outside the configured mount root"):
                map_shared_resource_to_native(
                    "smb://nas/SudoSync", mount_root,
                    "smb://nas/SudoSync/transactions/active-operation.json",
                )

    def test_native_lock_provider_cannot_be_enabled_without_physical_verification(self):
        with self.assertRaisesRegex(V2RuntimeError, "physically verified"):
            build_mapped_native_lock_factory(
                "smb://nas/SudoSync", os.path.abspath(os.getcwd()), filesystem_verified=False,
            )

if __name__ == "__main__":
    unittest.main()
