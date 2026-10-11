"""Tests for the mockable Kodi VFS repository backend."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.kodi_vfs import KodiVFSBackend, KodiVFSBackendError


class FakeFile(object):
    def __init__(self, vfs, path, mode):
        self.vfs, self.path, self.mode = vfs, path, mode
        self.position = 0
        self.buffer = ""

    def read(self, size=-1):
        data = self.vfs.files[self.path]
        if size < 0:
            return data
        return data[:size]

    def write(self, data):
        self.vfs.files[self.path] = data
        return True

    def flush(self):
        pass

    def close(self):
        pass


class FakeVFS(object):
    def __init__(self):
        self.files = {}
        self.directories = {}
        self.fail_rename = False

    def exists(self, path):
        return path in self.files or path in self.directories

    def File(self, path, mode):
        if mode == "r" and path not in self.files:
            raise IOError("not found")
        if mode == "w":
            self.files[path] = ""
        return FakeFile(self, path, mode)

    def rename(self, source, destination):
        if self.fail_rename or source not in self.files or destination in self.files:
            return False
        self.files[destination] = self.files.pop(source)
        return True

    def delete(self, path):
        if path not in self.files:
            return False
        del self.files[path]
        return True

    def mkdirs(self, path):
        self.directories[path] = True
        return True

    def listdir(self, path):
        return [], [name.split("/")[-1] for name in self.files if name.startswith(path.rstrip("/") + "/")]


class TestKodiVFSBackend(unittest.TestCase):
    def setUp(self):
        self.vfs = FakeVFS()
        self.backend = KodiVFSBackend(self.vfs, max_bytes=16)

    def test_read_write_and_rename(self):
        self.assertTrue(self.backend.write_text("share/a.json", '{"ok": true}'))
        self.assertEqual(self.backend.read_text("share/a.json"), '{"ok": true}')
        self.assertTrue(self.backend.rename("share/a.json", "share/b.json"))
        self.assertFalse(self.backend.exists("share/a.json"))
        self.assertEqual(self.backend.read_text("share/b.json"), '{"ok": true}')

    def test_rename_refuses_existing_destination(self):
        self.vfs.files["share/a"] = "a"
        self.vfs.files["share/b"] = "b"
        self.assertFalse(self.backend.rename("share/a", "share/b"))
        self.assertEqual(self.vfs.files["share/b"], "b")

    def test_oversized_read_fails_closed(self):
        self.vfs.files["share/a"] = "x" * 17
        with self.assertRaises(KodiVFSBackendError):
            self.backend.read_text("share/a")

    def test_oversized_write_is_rejected(self):
        with self.assertRaises(KodiVFSBackendError):
            self.backend.write_text("share/a", "x" * 17)

    def test_listdir_returns_separate_lists(self):
        self.vfs.files["share/a.json"] = "{}"
        self.assertEqual(self.backend.listdir("share"), ([], ["a.json"]))


if __name__ == "__main__":
    unittest.main()
