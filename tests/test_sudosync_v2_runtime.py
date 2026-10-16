# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
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


class InMemorySharedBackend(object):
    def __init__(self):
        self.files = {}
        self.dirs = set()

    def exists(self, path):
        return path in self.files or path.rstrip("/") in self.dirs

    def read_text(self, path):
        if path not in self.files:
            raise IOError("missing " + path)
        return self.files[path]

    def write_text(self, path, text):
        self.files[path] = text
        return True

    def rename(self, source, destination):
        if destination in self.files or source not in self.files:
            return False
        self.files[destination] = self.files.pop(source)
        return True

    def delete(self, path):
        if path not in self.files:
            return False
        del self.files[path]
        return True

    def mkdirs(self, path):
        self.dirs.add(path.rstrip("/"))
        return True

    def listdir(self, path):
        prefix = path.rstrip("/") + "/"
        files = []
        for key in self.files:
            if key.startswith(prefix):
                rest = key[len(prefix):]
                if "/" not in rest:
                    files.append(rest)
        return [], sorted(files)


class MutableFakeKodi(object):
    def __init__(self, movie_id, playcount=0):
        self.movie_id = movie_id
        self.playcount = playcount
        self.lastplayed = ""
        self.resume = {"position": 0.0, "total": 0.0}
        self.userrating = None
        self.playing = False
        self.writes = 0

    def is_video_playing(self):
        return self.playing

    def _item(self):
        return {
            "movieid": self.movie_id,
            "title": "Movie",
            "imdbnumber": "tt1234567",
            "uniqueid": {"imdb": "tt1234567"},
            "playcount": self.playcount,
            "lastplayed": self.lastplayed,
            "resume": dict(self.resume),
            "userrating": self.userrating or 0,
        }

    def get_movies(self):
        return [self._item()]

    def get_episodes(self):
        return []

    def get_movie_details(self, movie_id):
        assert movie_id == self.movie_id
        return self._item()

    def set_movie_details(self, movie_id, changes):
        assert movie_id == self.movie_id
        self.writes += 1
        if "playcount" in changes:
            self.playcount = changes["playcount"]
        if "lastplayed" in changes:
            self.lastplayed = changes["lastplayed"]
        if "resume" in changes:
            self.resume = dict(changes["resume"])
        if "userrating" in changes:
            self.userrating = changes["userrating"]
        return "OK"


class TestV2EndToEndSyncCycle(unittest.TestCase):
    def test_sequential_two_client_sync_and_feedback_loop_prevention(self):
        from resources.lib.sudosync_v2.runtime import ProcessLocalLockFactory

        client_a = "12345678-1234-4234-8234-123456789012"
        client_b = "22345678-1234-4234-8234-123456789012"
        backend = InMemorySharedBackend()
        lock_factory = ProcessLocalLockFactory()

        kodi_a = MutableFakeKodi(movie_id=10, playcount=2)
        kodi_b = MutableFakeKodi(movie_id=99, playcount=0)

        runtime_a = build_v2_runtime(kodi_a, backend, "smb://nas/.SudoSync", lock_factory, client_a, "Living Room")
        runtime_b = build_v2_runtime(kodi_b, backend, "smb://nas/.SudoSync", lock_factory, client_b, "Bedroom")

        # 1. Device A publishes its initial state
        res_a1 = runtime_a.sync_cycle()
        self.assertEqual(res_a1["status"], "no_changes")
        self.assertIsNotNone(res_a1["published_snapshot_path"])

        # 2. Device B initializes from Device A
        init_b = runtime_b.apply_initialization(client_a)
        self.assertEqual(init_b["status"], "committed")
        self.assertEqual(init_b["applied_count"], 1)
        self.assertEqual(kodi_b.playcount, 2)
        self.assertEqual(kodi_b.writes, 1)

        # 3. Device B watches the movie again (playcount 2 -> 3) and runs sync_cycle
        kodi_b.playcount = 3
        res_b2 = runtime_b.sync_cycle()
        self.assertEqual(res_b2["status"], "no_changes")
        self.assertIsNotNone(res_b2["published_snapshot_path"])

        # 4. Device A runs sync_cycle and receives playcount=3 via journaled transaction
        res_a2 = runtime_a.sync_cycle()
        self.assertEqual(res_a2["status"], "committed")
        self.assertEqual(res_a2["applied_count"], 1)
        self.assertEqual(kodi_a.playcount, 3)
        self.assertEqual(kodi_a.writes, 1)

        # 5. Feedback-loop check: subsequent cycles on both A and B make zero writes and no re-publications
        res_b3 = runtime_b.sync_cycle()
        self.assertEqual(res_b3["status"], "no_changes")
        self.assertEqual(res_b3["applied_count"], 0)
        self.assertIsNone(res_b3["published_snapshot_path"])
        self.assertEqual(kodi_b.writes, 1)

        res_a3 = runtime_a.sync_cycle()
        self.assertEqual(res_a3["status"], "no_changes")
        self.assertEqual(res_a3["applied_count"], 0)
        self.assertIsNone(res_a3["published_snapshot_path"])
        self.assertEqual(kodi_a.writes, 1)


if __name__ == "__main__":
    unittest.main()
