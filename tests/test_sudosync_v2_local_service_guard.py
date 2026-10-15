# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Tests for the local OS-managed singleton service guard."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync")))

from resources.lib.sudosync_v2.local_service_guard import LocalServiceGuard


class FakeLockBackend(object):
    def __init__(self):
        self.held = set()

    def acquire(self, handle):
        path = os.path.abspath(handle.name)
        if path in self.held:
            return False
        self.held.add(path)
        return True

    def release(self, handle):
        self.held.remove(os.path.abspath(handle.name))


class TestLocalServiceGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "profile", "sudosync-service.lock")
        self.backend = FakeLockBackend()

    def tearDown(self):
        self.temp.cleanup()

    def test_second_service_instance_is_rejected(self):
        first = LocalServiceGuard(self.path, self.backend)
        second = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_release_does_not_delete_lock_file(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        guard.release()
        self.assertTrue(os.path.isfile(self.path))

    def test_nested_profile_directory_is_created(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        self.assertTrue(os.path.isdir(os.path.dirname(self.path)))
        guard.release()

    def test_empty_path_is_rejected(self):
        with self.assertRaises(ValueError):
            LocalServiceGuard("", self.backend)

    def test_double_release_is_idempotent(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        guard.release()
        # Second release should be a silent no-op
        guard.release()
        # Release on unacquired guard should also be a silent no-op
        unacquired = LocalServiceGuard(self.path, self.backend)
        unacquired.release()

    def test_reacquire_after_release(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        guard.release()
        # Same instance re-acquire
        self.assertTrue(guard.acquire())
        guard.release()
        # New instance re-acquire
        second = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(second.acquire())
        second.release()


class TestGuardedServiceExecution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "profile", "sudosync-service.lock")
        self.backend = FakeLockBackend()

        from unittest.mock import MagicMock
        sys.modules.setdefault("xbmc", MagicMock())
        sys.modules.setdefault("xbmcaddon", MagicMock())
        sys.modules.setdefault("xbmcgui", MagicMock())
        sys.modules.setdefault("xbmcvfs", MagicMock())

        import service
        self.service = service

    def tearDown(self):
        self.temp.cleanup()

    def _make_guard(self):
        guard = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(guard.acquire())
        return guard

    def test_release_on_normal_exit(self):
        guard = self._make_guard()
        state = {"ran": False}

        def runner():
            state["ran"] = True
            # Guard must be held while runner executes
            other = LocalServiceGuard(self.path, self.backend)
            self.assertFalse(other.acquire())

        self.service.run_service_guarded(guard, runner)
        self.assertTrue(state["ran"])
        # Guard must be released after normal exit, allowing re-acquire
        reacquired = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(reacquired.acquire())
        reacquired.release()

    def test_release_on_return(self):
        guard = self._make_guard()

        def runner():
            return "restart"

        result = self.service.run_service_guarded(guard, runner)
        self.assertEqual(result, "restart")
        # Guard must be released after return, allowing re-acquire
        reacquired = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(reacquired.acquire())
        reacquired.release()

    def test_release_on_exception(self):
        guard = self._make_guard()

        def runner():
            raise RuntimeError("bootstrap crash")

        with self.assertRaises(RuntimeError):
            self.service.run_service_guarded(guard, runner)

        # Guard must be released after exception, allowing re-acquire
        reacquired = LocalServiceGuard(self.path, self.backend)
        self.assertTrue(reacquired.acquire())
        reacquired.release()

    def test_reacquire_after_guarded_run(self):
        def make_and_run(val):
            g = self._make_guard()
            return self.service.run_service_guarded(g, lambda: val)

        self.assertEqual(make_and_run("run1"), "run1")
        self.assertEqual(make_and_run("run2"), "run2")

    def test_double_release_with_simulated_atexit(self):
        guard = self._make_guard()
        self.service.run_service_guarded(guard, lambda: "done")
        # Simulate atexit firing at Kodi shutdown after finally has already released
        guard.release()
        self.assertFalse(guard._held)

    def test_main_function_guarded_lifecycle(self):
        def factory():
            g = LocalServiceGuard(self.path, self.backend)
            return g if g.acquire() else None

        # 1. Normal return
        result = self.service.main(guard_factory=factory, runner=lambda: "finished")
        self.assertEqual(result, "finished")

        # 2. Return on restart
        result = self.service.main(guard_factory=factory, runner=lambda: "restart")
        self.assertEqual(result, "restart")

        # 3. Exception
        with self.assertRaises(ValueError):
            self.service.main(guard_factory=factory, runner=lambda: (_ for _ in ()).throw(ValueError("failed")))

        # 4. Ponowne acquire after all the above runs
        check_guard = factory()
        self.assertIsNotNone(check_guard)
        check_guard.release()


if __name__ == "__main__":
    unittest.main()
