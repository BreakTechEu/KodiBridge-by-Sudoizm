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


@contextmanager
def progress(label, abort, delay=0):
    start = time.monotonic()
    dialog = None
    attempted = False

    def check():
        nonlocal dialog, attempted
        if not attempted and time.monotonic() - start >= delay:
            attempted = True
            try:
                dialog = xbmcgui.DialogProgressBG()
                dialog.create("KodiBridge", tr(label))
            except Exception:
                dialog = None
        return abort() if callable(abort) else False

    try:
        check()
        yield check
    finally:
        if dialog is not None:
            dialog.close()


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
