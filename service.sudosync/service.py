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
import os
import time

import xbmc
import xbmcgui

from resources.lib.sudosync_v2.local_service_guard import acquire_kodi_service_guard, ServiceGuardError
from resources.lib.sudosync_conflicts import (
    ConflictCheckProgress,
    prompt_and_resolve,
    resolvable_conflicts,
    take_resolution_request,
)

from resources.lib.sudosync_core import (
    addon_settings,
    assign_sudosync_ids,
    get_or_create_client_id,
    install_v2_log_bridge,
    log,
    maybe_check_github_update_daily,
    read_shared_config,
    record_kodibridge_id_coverage,
    renumbered_sudosync_ids,
    take_nfo_cache_refresh_request,
    take_nfo_id_epoch_change,
    update_local_status_from_v2,
)


# Last system/config.json read by the service loop; the sync engine reads the
# KodiBridge IDs renumbered by the duplicate repair from it.
_SHARED_VIEW = {"data": None}


def _drop_nfo_identifier_cache(v2_runtime, reason):
    """Forget cached .nfo identifiers so the next cycle reads them again."""
    try:
        from resources.lib.sudosync_core import _NFO_ID_CACHE
        _NFO_ID_CACHE.clear()
        kodi_adapter = getattr(getattr(v2_runtime, "sync_engine", None), "_kodi", None)
        if hasattr(kodi_adapter, "clear_nfo_cache"):
            kodi_adapter.clear_nfo_cache()
        log("NFO identifier cache cleared: {}.".format(reason), xbmc.LOGINFO)
    except Exception as exc:
        log("V2 NFO cache invalidation failed: {}".format(exc), xbmc.LOGWARNING)


_MonitorBase = getattr(xbmc, "Monitor", object)
if not isinstance(_MonitorBase, type) or getattr(_MonitorBase, "__module__", "").startswith("unittest.mock"):
    class _MonitorBase(object):
        def __init__(self):
            pass
        def abortRequested(self):
            return False
        def waitForAbort(self, timeout=0):
            return False
        def onScanFinished(self, library):
            pass
        def onNotification(self, sender, method, data):
            pass
        def onSettingsChanged(self):
            pass


class SudoSyncMonitor(_MonitorBase):
    def __init__(self):
        super(SudoSyncMonitor, self).__init__()
        self.dirty = False
        self.dirty_since = 0.0
        self.dirty_seq = 0
        self.scanning = False
        self.scan_refresh_pending = False
        self.playback_dirty = False
        self.user_update_dirty = False
        self.scan_assign_pending = False
        self.scan_finished_at = 0.0

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
        self.dirty_seq = int(getattr(self, "dirty_seq", 0)) + 1

    def onScanFinished(self, library):
        try:
            super(SudoSyncMonitor, self).onScanFinished(library)
        except (AttributeError, TypeError):
            pass
        if str(library).lower() == "video":
            self.scanning = False
            self.scan_refresh_pending = True
            self._mark_dirty()
            try:
                settings = addon_settings()
                id_enabled = bool(settings.get("sudosync_id_enabled") or settings.get("enable_sudosync_id"))
            except Exception:
                id_enabled = False
            if id_enabled:
                self.scan_assign_pending = True
                self.scan_finished_at = time.time()
                log("SudoSyncMonitor: onScanFinished('video') — zarejestrowano zakończenie skanowania wideo (debounce 3s).", xbmc.LOGINFO)

    def onNotification(self, sender, method, data):
        if method == "VideoLibrary.OnScanStarted":
            self.scanning = True
            return
        if method == "VideoLibrary.OnScanFinished":
            self.onScanFinished("video")
            return
        if method == "VideoLibrary.OnCleanFinished":
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


def _initial_snapshot_and_analyse(v2_runtime=None, abort_requested=None, base_client_id=None, init_completed=False):
    """Run the v2 startup snapshot step when LIVE sync is not active (v2 engine only)."""
    if v2_runtime is None:
        log("V2 runtime unavailable; local snapshot not published.", xbmc.LOGERROR)
        return {"status": "error", "error": "v2_unavailable"}
    try:
        rec = v2_runtime.recover(abort_requested=abort_requested)
    except Exception as exc:
        log("V2 recovery check before initial snapshot failed (fail-closed): {}".format(exc), xbmc.LOGERROR)
        return {"status": "error", "error": str(exc)}

    if not isinstance(rec, dict):
        log("V2 recovery check returned non-dict result (fail-closed)", xbmc.LOGERROR)
        return {"status": "error", "error": "invalid_recovery_result"}

    rec_status = rec.get("status")
    if rec_status != "ready":
        reason = (rec.get("recovery") or {}).get("reason", rec_status) if isinstance(rec.get("recovery"), dict) else rec_status
        log("Initial snapshot skipped: v2 session recovery status is '{}' (reason={}, fail-closed).".format(
            rec_status, reason
        ), xbmc.LOGWARNING)
        return {"status": str(rec_status or "error"), "recovery": (rec.get("recovery") if isinstance(rec, dict) else None)}

    if not init_completed:
        # Before initialization is completed the cycle stays read-only: no local
        # snapshot is published and Kodi is never modified.
        if base_client_id:
            try:
                if v2_runtime.catalog.load(base_client_id, abort_requested=abort_requested) is not None:
                    v2_init = v2_runtime.initialization_preview(base_client_id, abort_requested=abort_requested)
                    log("V2 initialization preview status={}".format(v2_init.get("status")), xbmc.LOGINFO)
                    return v2_init
            except Exception as exc:
                log("V2 initialization preview skipped: {}".format(exc), xbmc.LOGWARNING)
        return {"status": "not_initialized"}

    try:
        v2_pub = v2_runtime.publish_local_snapshot(
            abort_requested=abort_requested,
            suppress_local_changes=False,
        )
        log("V2 initial snapshot status={}".format(v2_pub.get("status")), xbmc.LOGINFO)
        update_local_status_from_v2(
            published_snapshot=(v2_pub.get("snapshot") if isinstance(v2_pub, dict) else None),
            v2_result=v2_pub,
        )
        return v2_pub
    except Exception as exc:
        log("V2 initial snapshot publish failed: {}".format(exc), xbmc.LOGWARNING)
        return {"status": "error", "error": str(exc)}


# Short retry delay when a sync cycle is busy without an error or block.
LIVE_BUSY_RETRY_SECONDS = 10
# How often the loop re-reads system/config.json from the NAS. Reading it every
# 2 s kept a handle open often enough to block replacing the file (e.g. while
# KodiBridge IDs were being assigned from settings).
SHARED_CONFIG_REFRESH_SECONDS = 30


def _live_retry_delay_seconds(result, live_poll):
    """Return backoff delay in seconds if live sync failed, is blocked, or is busy."""
    if not isinstance(result, dict):
        return 0
    if bool(result.get("error")) or bool(result.get("blocked")):
        try:
            poll_val = int(live_poll)
        except (ValueError, TypeError):
            poll_val = 60
        return max(poll_val, 30)
    if bool(result.get("busy")):
        return LIVE_BUSY_RETRY_SECONDS
    return 0


# Problem summary already shown in a notification during this service run.
_COVERAGE_NOTIFIED = {"key": None}


def coverage_problem_summary(coverage):
    """Return (key, message) for coverage problems, or (None, None) when complete."""
    if not isinstance(coverage, dict):
        return None, None
    totals = {}
    for key in ("missing", "duplicates", "ambiguous", "repeated"):
        totals[key] = sum(int((coverage.get(t) or {}).get(key) or 0) for t in ("movie", "episode"))
    unreadable = int(coverage.get("unreadable_nfo") or 0)
    parts = []
    if totals["missing"]:
        parts.append("bez KodiBridge ID: {}".format(totals["missing"]))
    if totals["duplicates"]:
        parts.append("zdublowane ID: {}".format(totals["duplicates"]))
    if totals["ambiguous"]:
        parts.append("blokujące synchronizację: {}".format(totals["ambiguous"]))
    if totals["repeated"]:
        parts.append("ten sam plik dwa razy w bibliotece: {}".format(totals["repeated"]))
    if unreadable:
        parts.append("nieczytelne .nfo: {}".format(unreadable))
    if not parts:
        return None, None
    key = (totals["missing"], totals["duplicates"], totals["ambiguous"], unreadable, totals["repeated"])
    return key, "Pozycje biblioteki: " + ", ".join(parts) + ". Szczegóły w oknie statusu KodiBridge."


def _record_coverage(v2_runtime):
    """Store the KodiBridge ID coverage of the last library read and tell the user about problems.

    A notification is shown at the first check after the service starts and
    again whenever the problem counts change; never during video playback
    (it is shown after the next check once playback has stopped).
    """
    coverage = getattr(getattr(v2_runtime, "sync_engine", None), "last_coverage", None)
    if not isinstance(coverage, dict):
        return
    record_kodibridge_id_coverage(coverage)
    key, message = coverage_problem_summary(coverage)
    if key is None:
        _COVERAGE_NOTIFIED["key"] = None
        return
    if key == _COVERAGE_NOTIFIED["key"] or _is_playing_video():
        return
    _COVERAGE_NOTIFIED["key"] = key
    log("KodiBridge ID coverage problems: {}".format(message), xbmc.LOGWARNING)
    try:
        xbmcgui.Dialog().notification("KodiBridge — KodiBridge ID", message, xbmcgui.NOTIFICATION_WARNING, 10000)
    except Exception:
        pass


def _execute_integrated_live_cycle(
    v2_runtime,
    monitor,
    show_notification,
    suppress_local_changes,
    collect_local,
    receiver_notification_delay_ms=0,
):
    """Run one LIVE sync cycle with the v2 engine (the only synchronization engine).

    The KodiBridge ID coverage is recorded after every cycle, also a failed
    one: the library is read before the snapshot is built.
    """
    try:
        return _run_live_cycle(
            v2_runtime, monitor, show_notification, suppress_local_changes,
            collect_local, receiver_notification_delay_ms,
        )
    finally:
        _record_coverage(v2_runtime)


def _run_live_cycle(
    v2_runtime,
    monitor,
    show_notification,
    suppress_local_changes,
    collect_local,
    receiver_notification_delay_ms=0,
):
    if v2_runtime is None:
        log("V2 runtime unavailable; LIVE sync cycle skipped.", xbmc.LOGERROR)
        update_local_status_from_v2(error="v2_unavailable")
        return {"busy": True, "error": "v2_unavailable", "v2": None, "applied_items": 0}

    # NFO identifiers are cached between cycles; a finished library scan/clean
    # may have changed sidecar files, so drop the cache before re-reading.
    if getattr(monitor, "scan_refresh_pending", False) is True:
        try:
            kodi_adapter = getattr(getattr(v2_runtime, "sync_engine", None), "_kodi", None)
            if hasattr(kodi_adapter, "clear_nfo_cache"):
                kodi_adapter.clear_nfo_cache()
        except Exception as exc:
            log("V2 NFO cache invalidation failed: {}".format(exc), xbmc.LOGWARNING)
    try:
        v2_result = v2_runtime.sync_cycle(
            abort_requested=monitor.abortRequested,
            suppress_local_changes=suppress_local_changes,
            publish_local=collect_local,
        )
    except Exception as exc:
        log("V2 live sync cycle error: {}".format(exc), xbmc.LOGERROR)
        update_local_status_from_v2(error=exc)
        return {"busy": True, "error": str(exc), "v2": None, "applied_items": 0}

    if not isinstance(v2_result, dict):
        log("V2 live sync returned invalid result: {}".format(v2_result), xbmc.LOGERROR)
        update_local_status_from_v2(error="invalid_v2_result")
        return {"busy": True, "error": "invalid_v2_result", "v2": v2_result, "applied_items": 0}

    v2_status = v2_result.get("status")
    v2_applied = int(v2_result.get("applied_count") or 0)
    if v2_status == "blocked":
        log("V2 live sync blocked by unrecovered session; skipping cycle until resolved.", xbmc.LOGERROR)
        update_local_status_from_v2(error="blocked: unrecovered v2 session")
        return {"busy": True, "blocked": True, "v2": v2_result, "applied_items": 0}

    if v2_status not in ("committed", "no_changes", "deferred"):
        log("V2 live sync returned non-ok status '{}'.".format(v2_status), xbmc.LOGERROR)
        update_local_status_from_v2(error="unexpected v2 status: {}".format(v2_status))
        return {"busy": True, "error": "unexpected_v2_status", "v2": v2_result, "applied_items": 0}

    if v2_status == "deferred":
        log("V2 live sync deferred (reason={}).".format(v2_result.get("reason", "unknown")), xbmc.LOGINFO)
    elif v2_status == "committed":
        log("V2 live sync committed {} item(s).".format(v2_applied), xbmc.LOGINFO)
    update_local_status_from_v2(v2_result=v2_result)

    if v2_status == "committed" and show_notification and v2_applied > 0:
        try:
            if receiver_notification_delay_ms and receiver_notification_delay_ms > 0:
                xbmc.sleep(int(receiver_notification_delay_ms))
            xbmcgui.Dialog().notification(
                "KodiBridge LIVE",
                "Zsynchronizowano zmian z sieci: {}.".format(v2_applied),
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
        except Exception:
            pass

    return {"busy": False, "v2": v2_result, "applied_items": v2_applied}


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
    
    from resources.lib.sudosync_bootstrap import (
        enforce_network_version_guard,
        is_network_addon_version_newer,
        refresh_network_active_kodi_lease,
        release_network_active_kodi_lease,
        run_bootstrap,
    )

    if not run_bootstrap():
        log("Bootstrap konfiguracji zatrzymany. Zatrzymuje serwis.")
        return

    settings = addon_settings()
    base_for_lease = settings.get("base_path", "")
    conflict_check = None
    try:
        if settings.get("check_updates", True):
            try:
                gh_upd = maybe_check_github_update_daily(ask_and_install=True)
                if isinstance(gh_upd, dict) and gh_upd.get("installed"):
                    log("Zainstalowano aktualizację z GitHub Releases. Zatrzymuję bieżącą instancję usługi.")
                    return
            except Exception as exc:
                log("Daily GitHub update check skipped: {}".format(exc), xbmc.LOGWARNING)

        # Initialize v2 runtime and run startup recovery/preview after bootstrap.
        v2_runtime = None
        v2_blocked = False
        v2_startup = None
        try:
            from resources.lib.sudosync_v2.kodi_vfs import KodiVFSBackend
            from resources.lib.sudosync_v2.runtime import (
                ProcessLocalLockFactory,
                build_kodi_v2_runtime,
            )
            from resources.lib.sudosync_core import _addon_profile_path, _NFO_ID_CACHE

            cache_file = os.path.join(_addon_profile_path(), "nfo_id_cache.json")
            def _seed_core_cache(p, t, ids):
                if p and ids:
                    _NFO_ID_CACHE[p] = dict(ids)

            v2_runtime = build_kodi_v2_runtime(
                xbmc.executeJSONRPC,
                KodiVFSBackend(),
                settings["base_path"],
                ProcessLocalLockFactory(),
                get_or_create_client_id(),
                settings["client_name"],
                abort_requested=monitor.abortRequested,
                cache_path=cache_file,
                on_nfo_cached=_seed_core_cache,
                # With the network KodiBridge ID option enabled, media are matched
                # only by KodiBridge ID; external IDs are a fallback otherwise.
                sudosync_only=lambda: bool(addon_settings().get("sudosync_id_enabled")),
                renumbered_ids=lambda: renumbered_sudosync_ids(_SHARED_VIEW["data"]),
            )
            # Recovery only: the library scan is done once, by the first sync cycle.
            # A second full NFO scan here delayed the first snapshot by 1-2 minutes.
            v2_startup = v2_runtime.recover(abort_requested=monitor.abortRequested)
            v2_startup_status = v2_startup.get("status")
            if v2_startup_status == "blocked":
                v2_blocked = True
                log("V2 startup blocked by unrecovered session marker.", xbmc.LOGERROR)
            elif v2_startup_status == "deferred":
                log("V2 startup recovery deferred (reason={}).".format(
                    (v2_startup.get("recovery") or {}).get("reason", "unknown")
                ), xbmc.LOGINFO)
            log("V2 startup recovery {}: {}".format(
                v2_startup.get("status"),
                (v2_startup.get("recovery") or {}).get("status", "n/a"),
            ), xbmc.LOGINFO)
            for skipped in (v2_startup.get("recovery") or {}).get("superseded") or []:
                log("V2 recovery skipped {} {} without writing ({}): current Kodi state is newer than the interrupted plan.".format(
                    skipped.get("type"), skipped.get("kodi_id"), skipped.get("reason")
                ), xbmc.LOGWARNING)
        except Exception as exc:
            # A preview failure must not disable the currently working service.
            log("V2 startup preview unavailable: {}".format(exc), xbmc.LOGWARNING)

        snapshot_interval = settings["interval_seconds"]
        live_poll = settings.get("live_poll_seconds", 60)
        last_snapshot = 0.0
        last_live_poll = 0.0
        last_lease_refresh = 0.0
        live_retry_not_before = 0.0
        # dirty_seq already covered by a snapshot published during playback.
        playback_published_seq = -1
        # Concurrent-edit conflicts from the last cycle, waiting for the user;
        # conflicts postponed by the user are not asked again until Kodi restarts.
        pending_conflicts = []
        conflict_dismissed = set()
        conflict_failures = {}
        conflict_check = ConflictCheckProgress()
        # Earliest retry of a failed publish during playback.
        playback_retry_not_before = 0.0
        shared_cache = None
        shared_read_at = 0.0
        report_no_conflicts = False
        local_client_id = getattr(v2_runtime, "client_id", None)

        try:
            shared = read_shared_config(settings["base_path"])
            _SHARED_VIEW["data"] = shared
            if v2_runtime is not None and take_nfo_id_epoch_change(shared):
                _drop_nfo_identifier_cache(v2_runtime, "KodiBridge IDs were changed on another Kodi")
            init = shared.get("initialization") or {}
            if v2_blocked:
                log("Skipping startup synchronization because v2 session recovery is blocked.", xbmc.LOGERROR)
            elif v2_runtime is not None and isinstance(v2_startup, dict) and v2_startup.get("status") == "deferred":
                log("Startup synchronization deferred: v2 session recovery is deferred.", xbmc.LOGINFO)
                live_retry_not_before = time.time() + LIVE_BUSY_RETRY_SECONDS
            elif init.get("completed", False) and settings.get("live_sync_enabled", True):
                result = _execute_integrated_live_cycle(
                    v2_runtime=v2_runtime,
                    monitor=monitor,
                    show_notification=False,
                    suppress_local_changes=False,
                    collect_local=True,
                    receiver_notification_delay_ms=12000,
                )
                if result.get("waiting"):
                    log("LIVE waiting for clients: {}".format(", ".join(result.get("waiting_for_clients") or [])))
                pending_conflicts = resolvable_conflicts(result.get("v2"))
                last_live_poll = time.time()
                startup_delay = _live_retry_delay_seconds(result, live_poll)
                if startup_delay > 0:
                    live_retry_not_before = time.time() + startup_delay
            else:
                _initial_snapshot_and_analyse(
                    v2_runtime=v2_runtime,
                    abort_requested=monitor.abortRequested,
                    base_client_id=init.get("base_client_id"),
                    init_completed=bool(init.get("completed", False)),
                )
                _record_coverage(v2_runtime)
                last_snapshot = time.time()
        except Exception as exc:
            log("Startup synchronization failed: {}".format(exc), xbmc.LOGERROR)

        while not monitor.abortRequested():
            now = time.time()
            try:
                settings = addon_settings()
                base_for_lease = settings.get("base_path") or base_for_lease
                if (now - last_lease_refresh) >= 60:
                    lease_ok = refresh_network_active_kodi_lease(base_for_lease, now_epoch=now)
                    last_lease_refresh = now
                    if not lease_ok:
                        log(
                            "Wykryto inne aktywne Kodi w sieci lub nowszą wymaganą wersję wtyczki — utrata dzierżawy. Zatrzymuję usługę.",
                            xbmc.LOGWARNING,
                        )
                        try:
                            outdated, _, _, net_cfg = is_network_addon_version_newer(base_for_lease)
                        except Exception:
                            outdated, net_cfg = False, {}
                        if outdated:
                            # Never interrupt playback with the update prompt: wait
                            # until the video stops, then ask immediately.
                            while _is_playing_video() and not monitor.waitForAbort(5):
                                pass
                        if outdated and not monitor.abortRequested():
                            try:
                                release_network_active_kodi_lease(base_for_lease)
                            except Exception:
                                pass
                            try:
                                enforce_network_version_guard(base_for_lease, net_cfg)
                            except Exception as exc:
                                log("Version guard dialog failed: {}".format(exc), xbmc.LOGWARNING)
                        else:
                            try:
                                xbmcgui.Dialog().notification(
                                    "KodiBridge",
                                    "Inne Kodi przejęło sesję lub wymaga nowszej wersji. Usługa wstrzymana.",
                                    xbmcgui.NOTIFICATION_WARNING,
                                    5000,
                                )
                            except Exception:
                                pass
                        break
                snapshot_interval = settings["interval_seconds"]
                live_poll = settings.get("live_poll_seconds", 60)
                if shared_cache is None or (now - shared_read_at) >= SHARED_CONFIG_REFRESH_SECONDS:
                    shared_cache = read_shared_config(settings["base_path"])
                    shared_read_at = now
                    _SHARED_VIEW["data"] = shared_cache
                    if v2_runtime is not None and take_nfo_id_epoch_change(shared_cache):
                        _drop_nfo_identifier_cache(v2_runtime, "KodiBridge IDs were changed on another Kodi")
                        last_live_poll = 0.0
                shared = shared_cache
                init = shared.get("initialization") or {}
                live_active = bool(init.get("completed", False) and settings.get("live_sync_enabled", True))
                if take_nfo_cache_refresh_request() and v2_runtime is not None:
                    # KodiBridge IDs were assigned, repaired or rolled back from the
                    # settings script: drop cached NFO identifiers and re-read them now.
                    _drop_nfo_identifier_cache(v2_runtime, "KodiBridge IDs were changed in settings")
                    last_live_poll = 0.0
                if take_resolution_request():
                    # Settings action: ask again about postponed conflicts and
                    # run a sync cycle right away to find current ones.
                    conflict_dismissed.clear()
                    last_live_poll = 0.0
                    report_no_conflicts = True
                    conflict_check.start(now)
                if report_no_conflicts and conflict_check.timed_out(now):
                    report_no_conflicts = False
                    conflict_check.finish(
                        "Sprawdzanie konfliktów przerwane po 15 minutach. Spróbuj ponownie później.", warning=True)

                # Check for pending post-scan SudoSync ID assignment (debounce 3-5s)
                scan_pending = getattr(monitor, "scan_assign_pending", False) is True
                scan_at = getattr(monitor, "scan_finished_at", 0.0)
                if (
                    scan_pending
                    and isinstance(scan_at, (int, float))
                    and scan_at > 0.0
                    and (now - scan_at) >= 3.0
                    and not monitor.scanning
                    and not _is_playing_video()
                ):
                    id_enabled = bool(settings.get("sudosync_id_enabled") or settings.get("enable_sudosync_id"))
                    if not id_enabled:
                        monitor.scan_assign_pending = False
                    else:
                        monitor.scan_assign_pending = False
                        try:
                            assign_res = assign_sudosync_ids(
                                show_notification=False,
                                auto_mode=True,
                                silent=True,
                                base_path=settings.get("base_path"),
                                is_canceled=monitor.abortRequested,
                            )
                            if assign_res == 0:
                                # Lock busy (another process/device is assigning IDs).
                                # Re-arm debounce to retry shortly.
                                monitor.scan_assign_pending = True
                                monitor.scan_finished_at = now + 10.0
                            else:
                                new_ids = 0
                                if isinstance(assign_res, dict):
                                    new_ids = int(assign_res.get("movies", 0)) + int(assign_res.get("episodes", 0))
                                elif isinstance(assign_res, int):
                                    new_ids = assign_res

                                if new_ids > 0:
                                    if v2_runtime is not None:
                                        try:
                                            kodi_adapter = getattr(getattr(v2_runtime, "sync_engine", None), "_kodi", None)
                                            if hasattr(kodi_adapter, "clear_nfo_cache"):
                                                kodi_adapter.clear_nfo_cache()
                                            pub_res = v2_runtime.publish_local_snapshot(
                                                abort_requested=monitor.abortRequested,
                                                suppress_local_changes=False,
                                            )
                                            log("Przebudowano i opublikowano snapshot v2 po nadaniu nowych SudoSync ID (status={}).".format(pub_res.get("status")), xbmc.LOGINFO)
                                        except Exception as exc:
                                            log("Błąd publikacji snapshotu v2 po nadaniu ID: {}".format(exc), xbmc.LOGWARNING)
                        except Exception as exc:
                            log("Błąd automatycznego nadawania SudoSync ID w usłudze: {}".format(exc), xbmc.LOGERROR)

                if live_active:
                    # Never alter library state while a video is actively playing.
                    # Player.OnStop will trigger a prompt cycle a few seconds later.
                    playing = _is_playing_video()
                    event_due = monitor.dirty and monitor.dirty_since and (now - monitor.dirty_since) >= 2
                    poll_due = (now - last_live_poll) >= live_poll
                    if playing:
                        # Never APPLY remote changes during playback, but publish
                        # local state so an abrupt Android kill cannot strand it
                        # only in this Kodi (written only when changed). Playback
                        # progress is published every live_poll seconds; an explicit
                        # library update (watched status, rating) is published as
                        # soon as its notification settles, without waiting.
                        current_seq = getattr(monitor, "dirty_seq", 0)
                        user_event_due = bool(
                            getattr(monitor, "user_update_dirty", False) is True
                            and event_due
                            and current_seq != playback_published_seq
                        )
                        if (poll_due or user_event_due) and not monitor.scanning and now >= playback_retry_not_before:
                            published_ok = False
                            if v2_runtime is not None:
                                try:
                                    v2_pub = v2_runtime.publish_local_snapshot(
                                        abort_requested=monitor.abortRequested,
                                        suppress_local_changes=False,
                                    )
                                    published_ok = isinstance(v2_pub, dict) and v2_pub.get("status") in ("published", "unchanged")
                                    if published_ok and v2_pub.get("published_snapshot_path"):
                                        update_local_status_from_v2(
                                            published_snapshot=v2_pub.get("snapshot"),
                                            v2_result=v2_pub,
                                        )
                                        if user_event_due:
                                            log("V2 published local library change during playback.", xbmc.LOGINFO)
                                except Exception as exc:
                                    log("V2 playback snapshot publish failed: {}".format(exc), xbmc.LOGWARNING)
                                _record_coverage(v2_runtime)
                            # Dirty flags stay set: remote changes are still applied
                            # by the regular cycle after playback stops.
                            if published_ok:
                                playback_published_seq = current_seq
                                last_live_poll = time.time()
                            else:
                                # Not published: keep the event pending and retry
                                # shortly instead of waiting for the next poll.
                                playback_retry_not_before = time.time() + LIVE_BUSY_RETRY_SECONDS
                    elif (event_due or poll_due) and not monitor.scanning and time.time() >= live_retry_not_before:
                        cycle_dirty_seq = getattr(monitor, "dirty_seq", 0)
                        cycle_dirty_since = monitor.dirty_since
                        suppress = bool(
                            monitor.scan_refresh_pending
                            and not monitor.playback_dirty
                            and not monitor.user_update_dirty
                        )
                        # v2 publishes the local snapshot only when it differs from the last one,
                        # so every cycle may collect: this is also the safety net for missed notifications.
                        collect_local = True
                        result = _execute_integrated_live_cycle(
                            v2_runtime=v2_runtime,
                            monitor=monitor,
                            # Background LIVE notifications are wanted only when this
                            # Kodi actually receives/applies remote changes. The core
                            # emits no source-side toast, so forcing this flag here is safe.
                            show_notification=True,
                            suppress_local_changes=suppress,
                            collect_local=collect_local,
                            receiver_notification_delay_ms=0,
                        )
                        last_live_poll = time.time()
                        retry_delay = _live_retry_delay_seconds(result, live_poll)
                        if retry_delay > 0:
                            live_retry_not_before = time.time() + retry_delay
                        v2_status = (result.get("v2") or {}).get("status") if isinstance(result.get("v2"), dict) else None
                        if not result.get("busy"):
                            pending_conflicts = resolvable_conflicts(result.get("v2"))
                        if report_no_conflicts:
                            if result.get("busy"):
                                # Error or busy session: keep the request and retry.
                                conflict_check.update(
                                    "Synchronizacja chwilowo niemożliwa ({}), ponawiam…".format(
                                        result.get("error") or "sesja zajęta"), now=time.time())
                            elif v2_status == "deferred":
                                conflict_check.update("Czekam, aż będzie można odczytać bibliotekę…", now=time.time())
                            else:
                                report_no_conflicts = False
                                if pending_conflicts:
                                    conflict_check.finish()
                                else:
                                    conflict_check.finish("Brak konfliktów do rozwiązania.")
                        if not result.get("busy"):
                            if (
                                getattr(monitor, "dirty_seq", 0) == cycle_dirty_seq
                                and monitor.dirty_since == cycle_dirty_since
                            ):
                                monitor.dirty = False
                                monitor.dirty_since = 0.0
                                monitor.scan_refresh_pending = False
                                monitor.playback_dirty = False
                                monitor.user_update_dirty = False
                    if pending_conflicts and v2_runtime is not None and not monitor.scanning and not _is_playing_video():
                        to_ask, pending_conflicts = pending_conflicts, []
                        lease_path = base_for_lease
                        if prompt_and_resolve(
                            v2_runtime, to_ask, conflict_dismissed, local_client_id,
                            is_busy=lambda: _is_playing_video() or bool(monitor.scanning),
                            abort_requested=monitor.abortRequested,
                            keep_alive=lambda: refresh_network_active_kodi_lease(lease_path),
                            failures=conflict_failures,
                        ):
                            # Apply/publish the rest of the state right away.
                            last_live_poll = 0.0
                else:
                    periodic_due = (now - last_snapshot) >= snapshot_interval
                    dirty_due = monitor.dirty and monitor.dirty_since and (now - monitor.dirty_since) >= 20 and (now - last_snapshot) >= 60
                    if periodic_due or dirty_due:
                        cycle_dirty_seq = getattr(monitor, "dirty_seq", 0)
                        cycle_dirty_since = monitor.dirty_since
                        _initial_snapshot_and_analyse(
                            v2_runtime=v2_runtime,
                            abort_requested=monitor.abortRequested,
                            base_client_id=init.get("base_client_id"),
                            init_completed=bool(init.get("completed", False)),
                        )
                        _record_coverage(v2_runtime)
                        last_snapshot = time.time()
                        if (
                            getattr(monitor, "dirty_seq", 0) == cycle_dirty_seq
                            and monitor.dirty_since == cycle_dirty_since
                        ):
                            monitor.dirty = False
                            monitor.dirty_since = 0.0
                            monitor.scan_refresh_pending = False
                            monitor.playback_dirty = False
                            monitor.user_update_dirty = False

                if settings.get("check_updates", True) and not _is_playing_video():
                    try:
                        gh_upd = maybe_check_github_update_daily(now_epoch=now, ask_and_install=True)
                        if isinstance(gh_upd, dict) and gh_upd.get("installed"):
                            log("Zainstalowano aktualizację z GitHub Releases. Zatrzymuję bieżącą instancję usługi.")
                            break
                    except Exception as exc:
                        log("Daily GitHub update check in loop skipped: {}".format(exc), xbmc.LOGWARNING)

            except Exception as exc:
                log("Service cycle failed: {}".format(exc), xbmc.LOGERROR)
                # Avoid a hot failure loop on broken NAS/network.
                err_now = time.time()
                last_live_poll = err_now
                last_snapshot = err_now
                live_retry_not_before = err_now + _live_retry_delay_seconds({"error": True}, live_poll)

            if monitor.waitForAbort(2):
                break
    finally:
        # The background progress bar of a pending conflict check belongs to
        # this process; close it when the service stops for any reason.
        try:
            if conflict_check is not None and conflict_check.active:
                conflict_check.finish()
        except Exception:
            pass
        try:
            release_network_active_kodi_lease(base_for_lease)
        except Exception:
            pass

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

    install_v2_log_bridge()
    target_runner = runner if runner is not None else _run_service
    try:
        return target_runner()
    finally:
        service_guard.release()


if __name__ == "__main__":
    main()
