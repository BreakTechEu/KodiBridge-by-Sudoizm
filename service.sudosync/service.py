# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------

import atexit
import time

import xbmc
import xbmcgui

from resources.lib.sudosync_v2.local_service_guard import acquire_kodi_service_guard, ServiceGuardError

from resources.lib.sudosync_core import (
    addon_settings,
    collect_and_write,
    collect_live_snapshot,
    get_or_create_client_id,
    log,
    read_shared_config,
    run_dry_run,
    run_live_sync_cycle,
    live_local_changes_detected,
    sync_initial_base_selection,
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

    def onSettingsChanged(self):
        try:
            from resources.lib.sudosync_core import addon_settings
            from resources.lib.sudosync_bootstrap import read_sudosync_cfg, write_sudosync_cfg
            
            settings = addon_settings()
            base = settings["base_path"]
            status, cfg = read_sudosync_cfg(base)
            if status == "VALID":
                net = cfg.get("network_settings", {})
                if net.get("sudosync_id_enabled") != settings["sudosync_id_enabled"] or net.get("sudosync_id_prefix") != settings["sudosync_id_prefix"]:
                    net["sudosync_id_enabled"] = settings["sudosync_id_enabled"]
                    net["sudosync_id_prefix"] = settings["sudosync_id_prefix"]
                    cfg["network_settings"] = net
                    write_sudosync_cfg(base, cfg)
        except Exception:
            pass

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


def _initial_snapshot_and_analyse(v2_runtime=None, abort_requested=None, base_client_id=None):
    if v2_runtime is not None:
        try:
            v2_pub = v2_runtime.publish_local_snapshot(
                abort_requested=abort_requested,
                suppress_local_changes=False,
            )
            log("V2 initial snapshot status={}".format(v2_pub.get("status")), xbmc.LOGINFO)
            if base_client_id:
                try:
                    if v2_runtime.catalog.load(base_client_id, abort_requested=abort_requested) is not None:
                        v2_init = v2_runtime.initialization_preview(
                            base_client_id,
                            abort_requested=abort_requested,
                        )
                        log("V2 initialization preview status={}".format(v2_init.get("status")), xbmc.LOGINFO)
                except Exception as exc:
                    log("V2 initialization preview skipped: {}".format(exc), xbmc.LOGWARNING)
        except Exception as exc:
            log("V2 initial snapshot publish failed: {}".format(exc), xbmc.LOGWARNING)

    collect_and_write(show_notification=False)
    try:
        run_dry_run(show_notification=False)
    except Exception as exc:
        log("DRY RUN skipped/failed: {}".format(exc), xbmc.LOGWARNING)


def _execute_integrated_live_cycle(
    v2_runtime,
    monitor,
    show_notification,
    suppress_local_changes,
    collect_local,
    skip_if_no_remote_change,
    notify_local_changes,
    receiver_notification_delay_ms,
):
    """Run the v2 live sync cycle first, coordinating safely with the legacy v1 live cycle."""
    v2_result = None
    v2_committed = False
    v2_applied = 0

    if v2_runtime is not None:
        try:
            v2_result = v2_runtime.sync_cycle(
                abort_requested=monitor.abortRequested,
                suppress_local_changes=suppress_local_changes,
                publish_local=collect_local,
            )
            v2_status = v2_result.get("status")
            v2_applied = int(v2_result.get("applied_count") or 0)
            if v2_status == "blocked":
                log(
                    "V2 live sync blocked by unrecovered session; skipping cycle until resolved.",
                    xbmc.LOGERROR,
                )
                return {
                    "busy": True,
                    "blocked": True,
                    "v2": v2_result,
                    "applied_items": 0,
                }
            if v2_status == "deferred":
                log(
                    "V2 live sync deferred (reason={}).".format(v2_result.get("reason", "unknown")),
                    xbmc.LOGINFO,
                )
            elif v2_status == "committed":
                v2_committed = True
                log(
                    "V2 live sync committed {} item(s).".format(v2_applied),
                    xbmc.LOGINFO,
                )
        except Exception as exc:
            log("V2 live sync cycle error: {}".format(exc), xbmc.LOGWARNING)

    # If v2 committed remote changes to Kodi, update the v1 live snapshot with
    # suppress_local_changes=True so v1 records the newly applied state with
    # ZERO_VERSION instead of re-broadcasting v2's writes as new local edits.
    v1_suppress = True if v2_committed else suppress_local_changes
    v1_collect = True if v2_committed else collect_local
    v1_skip = False if v2_committed else skip_if_no_remote_change

    result = run_live_sync_cycle(
        show_notification=show_notification and not v2_committed,
        suppress_local_changes=v1_suppress,
        collect_local=v1_collect,
        skip_if_no_remote_change=v1_skip,
        notify_local_changes=notify_local_changes,
        receiver_notification_delay_ms=receiver_notification_delay_ms,
    )
    if not isinstance(result, dict):
        result = {}

    # If a legacy v1-only peer applied changes through v1, publish the resulting
    # Kodi library state into the v2 catalog with suppress_local_changes=True.
    if v2_runtime is not None and not v2_committed and int(result.get("applied_items") or 0) > 0:
        try:
            v2_runtime.publish_local_snapshot(
                abort_requested=monitor.abortRequested,
                suppress_local_changes=True,
            )
        except Exception as exc:
            log("V2 post-v1 snapshot sync failed: {}".format(exc), xbmc.LOGWARNING)

    if v2_committed and show_notification and v2_applied > 0:
        try:
            if receiver_notification_delay_ms and receiver_notification_delay_ms > 0:
                xbmc.sleep(int(receiver_notification_delay_ms))
            xbmcgui.Dialog().notification(
                "SudoSync LIVE",
                "Zsynchronizowano zmian z sieci: {}.".format(v2_applied),
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
        except Exception:
            pass

    result["v2"] = v2_result
    result["applied_items"] = int(result.get("applied_items") or 0) + v2_applied
    return result


def _is_playing_video():
    try:
        return bool(xbmc.Player().isPlayingVideo())
    except Exception:
        return False


def run_service_guarded(service_guard, runner):
    """Execute runner while holding service_guard, guaranteeing release in finally."""
    try:
        return runner()
    finally:
        if service_guard is not None:
            service_guard.release()


def _run_service():
    monitor = SudoSyncMonitor()
    settings = addon_settings()
    
    from resources.lib.sudosync_bootstrap import check_and_enforce_updates_before_bootstrap, run_bootstrap

    update_status = check_and_enforce_updates_before_bootstrap()
    if update_status == "restart":
        log("Aktualizacja zainstalowana. SudoSync przerywa działanie obecnej instancji i czeka na restart.")
        return
    if update_status == "blocked":
        log("SudoSync nie uruchamia usługi, ponieważ wykryta paczka aktualizacji nie przeszła weryfikacji.", xbmc.LOGERROR)
        return

    if not run_bootstrap():
        log("Bootstrap konfiguracji zatrzymany. Zatrzymuje serwis.")
        return

    settings = addon_settings()

    # Initialize v2 runtime and run startup recovery/preview after bootstrap.
    v2_runtime = None
    v2_blocked = False
    try:
        from resources.lib.sudosync_v2.kodi_vfs import KodiVFSBackend
        from resources.lib.sudosync_v2.runtime import (
            ProcessLocalLockFactory,
            build_kodi_v2_runtime,
        )

        v2_runtime = build_kodi_v2_runtime(
            xbmc.executeJSONRPC,
            KodiVFSBackend(),
            settings["base_path"],
            ProcessLocalLockFactory(),
            get_or_create_client_id(),
            settings["client_name"],
            abort_requested=monitor.abortRequested,
        )
        v2_startup = v2_runtime.start(abort_requested=monitor.abortRequested)
        if v2_startup.get("status") == "blocked":
            v2_blocked = True
            log("V2 startup blocked by unrecovered session marker.", xbmc.LOGERROR)
        v2_preview = v2_startup.get("preview")
        if v2_preview is None:
            log("V2 startup preview {}: {}".format(
                v2_startup.get("status"),
                (v2_startup.get("recovery") or {}).get("status", "no preview"),
            ), xbmc.LOGINFO)
        else:
            log("V2 startup preview active={} reason={}".format(
                v2_preview.get("active"), v2_preview.get("reason", ""),
            ), xbmc.LOGINFO)
            if v2_preview.get("skipped_nfo_count"):
                log("V2 startup preview skipped {} corrupted NFO file(s)".format(
                    v2_preview.get("skipped_nfo_count")
                ), xbmc.LOGWARNING)
    except Exception as exc:
        # A preview failure must not disable the currently working service.
        log("V2 startup preview unavailable: {}".format(exc), xbmc.LOGWARNING)

    snapshot_interval = settings["interval_seconds"]
    live_poll = settings.get("live_poll_seconds", 60)
    last_snapshot = 0.0
    last_live_poll = 0.0

    try:
        sync_initial_base_selection(show_notification=False)
        shared = read_shared_config(settings["base_path"])
        init = shared.get("initialization") or {}
        if v2_blocked:
            log("Skipping startup synchronization because v2 session recovery is blocked.", xbmc.LOGERROR)
        elif init.get("completed", False) and settings.get("live_sync_enabled", True):
            result = _execute_integrated_live_cycle(
                v2_runtime=v2_runtime,
                monitor=monitor,
                show_notification=False,
                suppress_local_changes=False,
                collect_local=True,
                skip_if_no_remote_change=False,
                notify_local_changes=False,
                receiver_notification_delay_ms=12000,
            )
            if result.get("waiting"):
                log("LIVE waiting for clients: {}".format(", ".join(result.get("waiting_for_clients") or [])))
            last_live_poll = time.time()
        else:
            _initial_snapshot_and_analyse(
                v2_runtime=v2_runtime,
                abort_requested=monitor.abortRequested,
                base_client_id=init.get("base_client_id"),
            )
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
                        if v2_runtime is not None:
                            try:
                                v2_runtime.publish_local_snapshot(
                                    abort_requested=monitor.abortRequested,
                                    suppress_local_changes=False,
                                )
                            except Exception as exc:
                                log("V2 playback snapshot publish failed: {}".format(exc), xbmc.LOGWARNING)
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
                    result = _execute_integrated_live_cycle(
                        v2_runtime=v2_runtime,
                        monitor=monitor,
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
                    _initial_snapshot_and_analyse(
                        v2_runtime=v2_runtime,
                        abort_requested=monitor.abortRequested,
                        base_client_id=init.get("base_client_id"),
                    )
                    last_snapshot = time.time()
                    monitor.dirty = False
                    monitor.dirty_since = 0.0
                    monitor.scan_refresh_pending = False
                    monitor.playback_dirty = False
                    monitor.user_update_dirty = False

            sync_initial_base_selection(show_notification=False)
        except Exception as exc:
            log("Service cycle failed: {}".format(exc), xbmc.LOGERROR)
            # Avoid a hot failure loop on broken NAS/network.
            last_live_poll = time.time()
            last_snapshot = time.time()

        if monitor.waitForAbort(2):
            break

    log("Service stopped")


def main(guard_factory=acquire_kodi_service_guard, runner=None):
    # Prevent duplicate service execution on this Kodi installation before any
    # bootstrap, update check, snapshot, or synchronization work begins.
    try:
        service_guard = guard_factory()
    except ServiceGuardError as exc:
        log("Local service guard unavailable; refusing to start: {}".format(exc), xbmc.LOGERROR)
        return
    if service_guard is None:
        log("Another SudoSync service instance already owns this Kodi profile; refusing duplicate start.", xbmc.LOGWARNING)
        return
    atexit.register(service_guard.release)

    target_runner = runner if runner is not None else _run_service
    try:
        return target_runner()
    finally:
        service_guard.release()


if __name__ == "__main__":
    main()
