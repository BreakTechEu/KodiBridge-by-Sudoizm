import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ActivityTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "service.sudosync/resources/lib/kodibridge_activity.py"
        spec = importlib.util.spec_from_file_location("activity_under_test", path)
        self.activity = importlib.util.module_from_spec(spec)
        self.gui = Mock()
        self.kodi = Mock()
        with patch.dict(sys.modules, {"xbmc": self.kodi, "xbmcgui": self.gui,
                                      "resources.lib.sudosync_i18n": Mock()}):
            spec.loader.exec_module(self.activity)
        self.props = {self.activity.RUNNING: "1"}
        window = self.gui.Window.return_value
        window.getProperty.side_effect = lambda key: self.props.get(key, "")
        window.setProperty.side_effect = self.props.__setitem__
        window.clearProperty.side_effect = lambda key: self.props.pop(key, None)
        self.monitor = self.kodi.Monitor.return_value
        self.monitor.abortRequested.return_value = False
        self.gui.DialogProgress.return_value.iscanceled.return_value = False

    def test_short_cycle_stays_quiet_and_long_cycle_closes_progress(self):
        dialog = self.gui.DialogProgressBG
        with patch.object(self.activity.time, "monotonic", side_effect=[0, 0, 1, 4]):
            with self.activity.progress(32801, lambda: False, delay=2) as check:
                self.assertFalse(check())
                dialog.assert_not_called()
                self.assertFalse(check())
                dialog.return_value.create.assert_called_once()
            dialog.return_value.close.assert_called_once()

    def test_cancel_wait_clears_request_and_keeps_kodi_open(self):
        self.gui.DialogProgress.return_value.iscanceled.return_value = True
        self.activity.safe_quit()
        self.kodi.executebuiltin.assert_not_called()
        self.assertNotIn(self.activity.REQUEST, self.props)

    def test_quit_waits_for_service_acknowledgement(self):
        def acknowledge(timeout):
            if timeout < 1:
                self.kodi.executebuiltin.assert_not_called()
                self.props[self.activity.READY] = self.props[self.activity.REQUEST]
            return False

        self.monitor.waitForAbort.side_effect = acknowledge
        self.activity.safe_quit()
        self.kodi.executebuiltin.assert_called_once_with("Quit()")
        self.assertNotIn(self.activity.REQUEST, self.props)

    def test_error_requires_confirmation_before_exit(self):
        def acknowledge(timeout):
            self.props[self.activity.READY] = self.props[self.activity.REQUEST]
            return False

        self.props[self.activity.ERROR] = "1"
        self.monitor.waitForAbort.side_effect = acknowledge
        self.gui.Dialog.return_value.yesno.return_value = False
        self.activity.safe_quit()
        self.gui.Dialog.return_value.yesno.assert_called_once()
        self.kodi.executebuiltin.assert_not_called()

    def test_boundary_releases_acknowledgement_after_abort(self):
        self.props[self.activity.REQUEST] = "pending"
        self.monitor.waitForAbort.return_value = True
        self.assertTrue(self.activity.quit_boundary(self.monitor))
        self.assertNotIn(self.activity.READY, self.props)
