# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import time

import xbmc
import xbmcgui

from resources.lib.sudosync_core import (
    addon_settings,
    check_for_update,
    collect_and_write,
    collect_live_snapshot,
    install_latest_update,
    log,
    read_shared_config,
    run_dry_run,
    run_live_sync_cycle,
    live_local_changes_detected,
    sync_initial_base_selection,
    sync_network_settings,
)


class SudoSyncMonitor(xbmc.Monitor):
    def __init__(self):
        super(SudoSyncMonitor, self).__init__()
        self.dirty = False
        self.dirty_since = 0.0
        self.scanning = False
        self.scan_refresh_pending = False
        self.playback_dirty = False
        self.user_update_dirty = False

    def _mark_dirty(self):
        self.dirty = True
        self.dirty_since = time.time()

    def onNotification(self, sender, method, data):
        if method == "VideoLibrary.OnScanStarted":
            self.scanning = True
            return
        if method in ("VideoLibrary.OnScanFinished", "VideoLibrary.OnCleanFinished"):
            self.scanning = False
            self.scan_refresh_pending = True
            self._mark_dirty()
            return
        if method == "Player.OnStop":
            # Explicit playback activity always outranks a pending post-scan
            # quarantine. Otherwise a scan finishing just before playback stops
            # could consume the real resume/lastplayed change as scan metadata.
            self.playback_dirty = True
            self._mark_dirty()
            return
        if method == "VideoLibrary.OnUpdate" and not self.scanning:
            # An explicit library update after scanning has finished must not be
            # swallowed by the post-scan quarantine. This is especially important
            # for editing an already existing user rating.
            self.user_update_dirty = True
            self._mark_dirty()


def _initial_snapshot_and_analyse():
    collect_and_write(show_notification=False)
    try:
        run_dry_run(show_notification=False)
    except Exception as exc:
        log("DRY RUN skipped/failed: {}".format(exc), xbmc.LOGWARNING)


def _is_playing_video():
    try:
        return bool(xbmc.Player().isPlayingVideo())
    except Exception:
        return False


def main():
    monitor = SudoSyncMonitor()
    settings = addon_settings()
    snapshot_interval = settings["interval_seconds"]
    live_poll = settings.get("live_poll_seconds", 60)
    last_snapshot = 0.0
    last_live_poll = 0.0

    if settings.get("check_updates", True):
        try:
            update_result = check_for_update(show_notification=not settings.get("auto_install_updates", False), manual=False)
            if update_result.get("available") and settings.get("auto_install_updates", False):
                installed = install_latest_update(show_dialogs=False)
                if installed.get("installed"):
                    xbmcgui.Dialog().notification(
                        "SudoSync — zaktualizowano",
                        "Zainstalowano {}. Nowa wersja ruszy po ponownym uruchomieniu Kodi.".format(installed.get("version")),
                        xbmcgui.NOTIFICATION_INFO,
                        10000,
                    )
        except Exception as exc:
            log("Update check/install failed: {}".format(exc), xbmc.LOGWARNING)

    try:
        sync_initial_base_selection(show_notification=False)
        sync_network_settings()
        shared = read_shared_config(settings["base_path"])
        init = shared.get("initialization") or {}
        if init.get("completed", False) and settings.get("live_sync_enabled", True):
            result = run_live_sync_cycle(show_notification=False, suppress_local_changes=False, collect_local=True, skip_if_no_remote_change=False, notify_local_changes=False, receiver_notification_delay_ms=12000)
            if result.get("waiting"):
                log("LIVE waiting for clients: {}".format(", ".join(result.get("waiting_for_clients") or [])))
            last_live_poll = time.time()
        else:
            _initial_snapshot_and_analyse()
            last_snapshot = time.time()
    except Exception as exc:
        log("Startup synchronization failed: {}".format(exc), xbmc.LOGERROR)

    while not monitor.abortRequested():
        now = time.time()
        try:
            settings = addon_settings()
            snapshot_interval = settings["interval_seconds"]
            live_poll = settings.get("live_poll_seconds", 60)
            shared = read_shared_config(settings["base_path"])
            init = shared.get("initialization") or {}
            live_active = bool(init.get("completed", False) and settings.get("live_sync_enabled", True))

            if live_active:
                # Never alter library state while a video is actively playing.
                # Player.OnStop will trigger a prompt cycle a few seconds later.
                playing = _is_playing_video()
                event_due = monitor.dirty and monitor.dirty_since and (now - monitor.dirty_since) >= 2
                poll_due = (now - last_live_poll) >= live_poll
                local_probe = {"changed": False}
                if poll_due and not monitor.scanning:
                    # Notifications are the fast path; this lightweight probe is
                    # the periodic safety net required for Android/Google TV.
                    local_probe = live_local_changes_detected()

                if playing:
                    # Never APPLY remote changes during playback. If the safety
                    # probe sees a local change, however, publish our snapshot so
                    # an abrupt Android kill cannot strand it only in this Kodi.
                    if poll_due and local_probe.get("changed") and not monitor.scanning:
                        collect_live_snapshot(
                            show_notification=False,
                            # While video is actively playing this state is a
                            # genuine playback change, not an NFO/scan import.
                            suppress_local_changes=False,
                            notify_local_changes=False,
                        )
                    if poll_due:
                        last_live_poll = time.time()
                elif (event_due or poll_due) and not monitor.scanning:
                    suppress = bool(
                        monitor.scan_refresh_pending
                        and not monitor.playback_dirty
                        and not monitor.user_update_dirty
                    )
                    collect_local = bool(event_due or suppress or local_probe.get("changed"))
                    result = run_live_sync_cycle(
                        # Background LIVE notifications are wanted only when this
                        # Kodi actually receives/applies remote changes. The core
                        # emits no source-side toast, so forcing this flag here is safe.
                        show_notification=True,
                        suppress_local_changes=suppress,
                        collect_local=collect_local,
                        skip_if_no_remote_change=bool(poll_due and not collect_local),
                        notify_local_changes=False,
                        receiver_notification_delay_ms=0,
                    )
                    last_live_poll = time.time()
                    if not result.get("busy"):
                        monitor.dirty = False
                        monitor.dirty_since = 0.0
                        monitor.scan_refresh_pending = False
                        monitor.playback_dirty = False
                        monitor.user_update_dirty = False
            else:
                periodic_due = (now - last_snapshot) >= snapshot_interval
                dirty_due = monitor.dirty and monitor.dirty_since and (now - monitor.dirty_since) >= 20 and (now - last_snapshot) >= 60
                if periodic_due or dirty_due:
                    _initial_snapshot_and_analyse()
                    last_snapshot = time.time()
                    monitor.dirty = False
                    monitor.dirty_since = 0.0
                    monitor.scan_refresh_pending = False
                    monitor.playback_dirty = False
                    monitor.user_update_dirty = False

            sync_initial_base_selection(show_notification=False)
            sync_network_settings()
        except Exception as exc:
            log("Service cycle failed: {}".format(exc), xbmc.LOGERROR)
            # Avoid a hot failure loop on broken NAS/network.
            last_live_poll = time.time()
            last_snapshot = time.time()

        if monitor.waitForAbort(2):
            break

    log("Service stopped")


if __name__ == "__main__":
    main()
