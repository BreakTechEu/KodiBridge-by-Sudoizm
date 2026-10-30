# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Ask the user which value wins when two Kodi devices changed a field concurrently."""
from __future__ import absolute_import

import json
import threading

import xbmc
import xbmcgui

from resources.lib.sudosync_core import log, rpc

# Window property set by the "Resolve conflicts" settings action and read by
# the running service (the settings script must not run the engine itself).
RESOLVE_REQUEST_PROPERTY = "service.sudosync.resolve_conflicts"
HOME_WINDOW_ID = 10000

FIELD_NAMES = {
    "playcount": "liczba odtworzeń",
    "lastplayed": "data ostatniego odtworzenia",
    "resume": "punkt wznowienia",
    "userrating": "ocena",
}


def conflict_key(conflict):
    """Identify one conflict, including its version, for the dismissed-this-session set."""
    version = conflict.get("version") or {}
    values = sorted(json.dumps(v.get("value"), sort_keys=True) for v in conflict.get("values") or [])
    return (
        conflict.get("type"), conflict.get("kodi_id"), conflict.get("field"),
        version.get("lc"), version.get("seq"), tuple(values),
    )


def resolvable_conflicts(v2_result):
    """Return plan conflicts that this Kodi can resolve (it holds the item)."""
    if not isinstance(v2_result, dict):
        return []
    plan = (v2_result.get("preview") or {}).get("plan") if isinstance(v2_result.get("preview"), dict) else None
    if not isinstance(plan, dict):
        return []
    return [
        c for c in plan.get("conflicts") or []
        if isinstance(c, dict) and type(c.get("kodi_id")) is int and c.get("identity")
    ]


def _clock(seconds):
    seconds = int(round(float(seconds or 0)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{}:{:02d}".format(minutes, secs)


def describe_value(field, value):
    """Return a short Polish description of one synchronized field value."""
    if field == "playcount":
        count = int(value or 0)
        return "nieobejrzane" if count <= 0 else "obejrzane, odtworzeń: {}".format(count)
    if field == "lastplayed":
        return str(value) if value else "brak"
    if field == "resume":
        value = value if isinstance(value, dict) else {}
        position = float(value.get("position") or 0)
        if position <= 0:
            return "od początku"
        total = float(value.get("total") or 0)
        return "{} z {}".format(_clock(position), _clock(total)) if total else _clock(position)
    if field == "userrating":
        return "brak oceny" if not value else "ocena {}/10".format(int(value))
    return str(value)


def item_label(conflict):
    """Return a human-readable title of the conflicting library item."""
    kodi_id = conflict.get("kodi_id")
    try:
        if conflict.get("type") == "movie":
            details = rpc("VideoLibrary.GetMovieDetails", {"movieid": kodi_id, "properties": ["title", "year"]})
            movie = details.get("moviedetails") or {}
            year = movie.get("year")
            return "{} ({})".format(movie.get("title"), year) if year else str(movie.get("title"))
        details = rpc(
            "VideoLibrary.GetEpisodeDetails",
            {"episodeid": kodi_id, "properties": ["title", "showtitle", "season", "episode"]},
        )
        episode = details.get("episodedetails") or {}
        return "{} S{:02d}E{:02d} {}".format(
            episode.get("showtitle") or "", int(episode.get("season") or 0),
            int(episode.get("episode") or 0), episode.get("title") or "",
        ).strip()
    except Exception as exc:
        log("Cannot read title for conflict item {} {}: {}".format(conflict.get("type"), kodi_id, exc), xbmc.LOGDEBUG)
        return "{} #{}".format("film" if conflict.get("type") == "movie" else "odcinek", kodi_id)


def choice_options(conflict, local_client_id):
    """Return (labels, values) for the selection dialog; equal values are merged."""
    grouped = []
    for option in conflict.get("values") or []:
        value = option.get("value")
        name = option.get("client_name") or str(option.get("client_id", ""))[:8]
        if option.get("client_id") == local_client_id:
            name += " (to Kodi)"
        for entry in grouped:
            if entry[0] == value:
                entry[1].append(name)
                break
        else:
            grouped.append((value, [name]))
    labels = ["{}: {}".format(", ".join(names), describe_value(conflict.get("field"), value)) for value, names in grouped]
    values = [value for value, _ in grouped]
    return labels, values


# A failed resolution is retried in later cycles; after this many failures of
# the same conflict it is postponed until the next Kodi start.
MAX_RESOLVE_FAILURES = 3
# How often the active-Kodi lease is refreshed while a dialog is open
# (the lease expires after ACTIVE_KODI_TTL_SECONDS = 180 s).
KEEP_ALIVE_INTERVAL_SECONDS = 30.0


def _select_keeping_lease(heading, options, keep_alive, interval):
    """Show the modal selection dialog while refreshing the lease in the background.

    Returns (selected index, lease_ok). A modal dialog blocks the service loop,
    so without this the active-Kodi lease would expire while it is open.
    """
    if keep_alive is None:
        return xbmcgui.Dialog().select(heading, options), True
    stop = threading.Event()
    state = {"ok": True}

    def worker():
        while not stop.wait(interval):
            try:
                if not keep_alive():
                    state["ok"] = False
                    return
            except Exception as exc:
                log("Lease refresh during conflict dialog failed: {}".format(exc), xbmc.LOGWARNING)
                state["ok"] = False
                return

    thread = threading.Thread(target=worker, name="KodiBridgeConflictLease")
    thread.daemon = True
    thread.start()
    try:
        selected = xbmcgui.Dialog().select(heading, options)
    finally:
        stop.set()
        thread.join(5.0)
    return selected, state["ok"]


def _record_error(message):
    try:
        from resources.lib.sudosync_core import update_local_status_from_v2
        update_local_status_from_v2(error=message)
    except Exception:
        pass


def prompt_and_resolve(runtime, conflicts, dismissed, local_client_id, is_busy=None, abort_requested=None,
                       keep_alive=None, failures=None, keep_alive_interval=KEEP_ALIVE_INTERVAL_SECONDS):
    """Ask about each conflict not yet dismissed this session and apply the choice.

    ``is_busy`` (playback or library scan) is checked before every dialog.
    ``keep_alive`` refreshes the active-Kodi lease while a dialog is open and
    once more before writing; if it fails, nothing is written. "Later" (or
    closing the dialog) postpones the remaining conflicts until the next Kodi
    start. A failed resolution is not treated as "Later": the user is told, the
    error goes to status.json and the conflict is offered again in a later
    cycle, up to MAX_RESOLVE_FAILURES times. Returns the number of resolved
    conflicts.
    """
    if failures is None:
        failures = {}
    counts = {"resolved": 0, "outdated": 0, "failed": 0}
    pending = [c for c in conflicts if conflict_key(c) not in dismissed]
    for index, conflict in enumerate(pending):
        if (is_busy is not None and is_busy()) or (abort_requested is not None and abort_requested()):
            break
        key = conflict_key(conflict)
        labels, values = choice_options(conflict, local_client_id)
        heading = "KodiBridge — konflikt ({}/{}): {}".format(index + 1, len(pending), item_label(conflict))
        field_name = FIELD_NAMES.get(conflict.get("field"), conflict.get("field"))
        selected, lease_ok = _select_keeping_lease(
            "{} — {}".format(heading, field_name),
            labels + ["Później (zapytaj po ponownym uruchomieniu Kodi)"],
            keep_alive, keep_alive_interval,
        )
        if selected < 0 or selected >= len(values):
            for rest in pending[index:]:
                dismissed.add(conflict_key(rest))
            log("Conflict resolution postponed by the user ({} conflict(s)).".format(len(pending) - index))
            break
        if lease_ok and keep_alive is not None:
            try:
                lease_ok = bool(keep_alive())
            except Exception as exc:
                log("Lease refresh before conflict resolution failed: {}".format(exc), xbmc.LOGWARNING)
                lease_ok = False
        if not lease_ok:
            # Another Kodi took over or a newer add-on version is required: the
            # service loop handles it; never write without the lease.
            log("Conflict resolution skipped: active Kodi lease was lost.", xbmc.LOGWARNING)
            break
        try:
            result = runtime.resolve_conflict(
                conflict["type"], conflict["kodi_id"], conflict["field"], values[selected],
                abort_requested=abort_requested,
                expected_identity=conflict.get("identity"),
                expected_version=conflict.get("version"),
            )
        except Exception as exc:
            failures[key] = failures.get(key, 0) + 1
            counts["failed"] += 1
            message = "conflict resolution failed for {} {} {}: {}".format(
                conflict["type"], conflict["kodi_id"], conflict["field"], exc)
            log(message, xbmc.LOGERROR)
            _record_error(message)
            if failures[key] >= MAX_RESOLVE_FAILURES:
                dismissed.add(key)
            continue
        status = result.get("status") if isinstance(result, dict) else None
        log("Conflict {} {} {} -> {} ({}).".format(
            conflict["type"], conflict["kodi_id"], conflict["field"], values[selected], status))
        if status == "resolved":
            counts["resolved"] += 1
            failures.pop(key, None)
        elif status in ("not_found", "changed"):
            # Already resolved elsewhere, or the conflict changed while the
            # dialog was open: a changed one is offered again in the next cycle.
            counts["outdated"] += 1
        else:
            # Playback started or the session is busy: ask again later.
            break
    _notify_summary(counts)
    return counts["resolved"]


def _notify_summary(counts):
    if not any(counts.values()):
        return
    parts = []
    if counts["resolved"]:
        parts.append("zapisano wybór: {}".format(counts["resolved"]))
    if counts["outdated"]:
        parts.append("nieaktualne, bez zapisu: {}".format(counts["outdated"]))
    if counts["failed"]:
        parts.append("błąd zapisu: {} (pytanie wróci później)".format(counts["failed"]))
    try:
        xbmcgui.Dialog().notification(
            "KodiBridge — konflikty", "; ".join(parts).capitalize() + ".",
            xbmcgui.NOTIFICATION_WARNING if counts["failed"] else xbmcgui.NOTIFICATION_INFO,
            6000,
        )
    except Exception:
        pass


def request_resolution():
    """Ask the running service to look for conflicts now (settings action)."""
    xbmcgui.Window(HOME_WINDOW_ID).setProperty(RESOLVE_REQUEST_PROPERTY, "1")


def take_resolution_request():
    """Return True once if the settings action asked for conflict resolution."""
    try:
        window = xbmcgui.Window(HOME_WINDOW_ID)
        if window.getProperty(RESOLVE_REQUEST_PROPERTY) != "1":
            return False
        window.clearProperty(RESOLVE_REQUEST_PROPERTY)
        return True
    except Exception:
        return False
