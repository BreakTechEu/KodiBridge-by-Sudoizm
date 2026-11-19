"""Activity feedback and cooperative exit at a service-loop boundary."""
import time
import uuid
from contextlib import contextmanager

import xbmc
import xbmcgui

from resources.lib.sudosync_i18n import tr

RUNNING = "KodiBridge.ServiceRunning"
REQUEST = "KodiBridge.QuitRequest"
READY = "KodiBridge.QuitReady"
ERROR = "KodiBridge.WorkError"


# Stage names written to the log (the log is not translated).
STAGE_NAMES = {
    32800: "post-scan library check",
    32801: "library synchronization",
    32802: "library snapshot",
    32806: "post-scan library update",
}
# Minimum time between two window updates with the same percentage.
UPDATE_INTERVAL = 0.25
# Item counts seen by the last stage of this process: the next stage uses them
# to show one percentage for movies and episodes together.
_LAST_TOTALS = {}


def _log(message):
    try:
        xbmc.log("[SudoSync] {}".format(message), xbmc.LOGINFO)
    except Exception:
        pass


class Activity(object):
    """Abort check handed to the work; it also shows how far the work is.

    Calling the object returns True when the work must stop. The library
    readers call report_progress(done, total, media_type) for every item, so
    the background window shows "done/total" and a percentage.
    """

    def __init__(self, label, abort, delay=0):
        self.label = label
        self._abort = abort
        self._delay = delay
        self._start = time.monotonic()
        self._dialog = None
        self._attempted = False
        self._pending = None
        self._shown = None
        self._shown_at = 0.0
        self._totals = {}
        self._done = {}
        self.aborted = False

    def __call__(self):
        self._open_if_due()
        self._show()
        stop = bool(self._abort()) if callable(self._abort) else False
        if stop:
            self.aborted = True
        return stop

    def _open_if_due(self):
        if self._attempted or time.monotonic() - self._start < self._delay:
            return
        self._attempted = True
        try:
            self._dialog = xbmcgui.DialogProgressBG()
            self._dialog.create("KodiBridge", tr(self.label))
        except Exception:
            self._dialog = None

    def report_progress(self, done, total, media_type=None, item=None):
        try:
            done = max(0, int(done))
            total = max(0, int(total))
            kind = media_type if media_type in ("movie", "episode") else None
            self._totals[kind] = total
            self._done[kind] = done
            if kind == "movie":
                all_done = done
                all_total = total + self._totals.get("episode", _LAST_TOTALS.get("episode", 0))
                message = tr(32807, tr(self.label), done, total)
            elif kind == "episode":
                movies = self._totals.get("movie", 0)
                all_done = movies + done
                all_total = movies + total
                message = tr(32808, tr(self.label), done, total)
            else:
                all_done, all_total = done, total
                message = tr(32809, tr(self.label), done, total)
            percent = int(min(100, (all_done * 100) // all_total)) if all_total > 0 else 0
            self._pending = (percent, message)
            self._open_if_due()
            self._show()
        except Exception:
            pass

    def _show(self):
        if self._dialog is None or self._pending is None or self._pending == self._shown:
            return
        now = time.monotonic()
        if self._shown is not None and self._pending[0] == self._shown[0] and now - self._shown_at < UPDATE_INTERVAL:
            return
        try:
            self._dialog.update(self._pending[0], "KodiBridge", self._pending[1])
            self._shown = self._pending
            self._shown_at = now
        except Exception:
            pass

    def counts(self):
        names = (("movie", "movies"), ("episode", "episodes"), (None, "items"))
        return ", ".join(
            "{} {}/{}".format(name, self._done.get(kind, 0), self._totals[kind])
            for kind, name in names if kind in self._totals
        )

    def close(self, failed=False):
        elapsed = time.monotonic() - self._start
        for kind in ("movie", "episode"):
            if kind in self._totals:
                _LAST_TOTALS[kind] = self._totals[kind]
        stage = STAGE_NAMES.get(self.label, str(self.label))
        counts = self.counts()
        _log("Stage '{}' {} after {:.1f} s{}".format(
            stage, "stopped" if failed or self.aborted else "finished", elapsed, " ({})".format(counts) if counts else ""))
        if self._dialog is not None:
            try:
                self._dialog.close()
            except Exception:
                pass
            self._dialog = None


@contextmanager
def progress(label, abort, delay=0):
    activity = Activity(label, abort, delay)
    failed = True
    try:
        activity()
        yield activity
        failed = False
    finally:
        activity.close(failed=failed)


def record_error(failed):
    window = xbmcgui.Window(10000)
    if failed:
        window.setProperty(ERROR, "1")
    elif not window.getProperty(REQUEST):
        window.clearProperty(ERROR)


def quit_boundary(monitor):
    window = xbmcgui.Window(10000)
    token = window.getProperty(REQUEST)
    if not isinstance(token, str) or not token:
        return
    window.setProperty(READY, token)
    try:
        while window.getProperty(REQUEST) == token:
            if monitor.waitForAbort(0.1):
                return True
    finally:
        window.clearProperty(READY)


def safe_quit():
    window = xbmcgui.Window(10000)
    if window.getProperty(RUNNING) != "1":
        xbmc.executebuiltin("Quit()")
        return
    if window.getProperty(REQUEST):
        return
    token = uuid.uuid4().hex
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    window.setProperty(REQUEST, token)
    try:
        dialog.create("KodiBridge", tr(32803))
        while window.getProperty(READY) != token and window.getProperty(RUNNING) == "1":
            if dialog.iscanceled() or monitor.waitForAbort(0.1):
                return
        if dialog.iscanceled() or monitor.abortRequested():
            return
        dialog.close()
        if window.getProperty(ERROR) == "1" and not xbmcgui.Dialog().yesno("KodiBridge", tr(32804)):
            return
        xbmc.executebuiltin("Quit()")
        monitor.waitForAbort(3)
    finally:
        dialog.close()
        window.clearProperty(REQUEST)
