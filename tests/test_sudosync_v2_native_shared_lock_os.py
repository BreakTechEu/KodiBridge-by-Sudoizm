"""Real OS-lock smoke test (local filesystem only; not SMB/NFS certification)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.native_shared_lock import (
    NativeSharedFileLock, SharedFileLockBusy,
)


class TestNativeSharedFileLockOS(unittest.TestCase):
    def test_second_process_file_handle_cannot_acquire_until_release(self):
        if os.name == "nt":
            self.skipTest("Windows byte-range locking is tested by the physical probe on Windows hosts")
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, ".sudosync-lock-test")
            first = NativeSharedFileLock(path, blocking=True)
            second = NativeSharedFileLock(path, blocking=False)
            with first:
                with self.assertRaises(SharedFileLockBusy):
                    with second:
                        self.fail("second lock unexpectedly acquired the held lock")
            with second:
                self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
