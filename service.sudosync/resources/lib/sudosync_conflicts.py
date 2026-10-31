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
    """Return (labels, values) for a single-field selection; equal values are merged.

    Used as a fallback when the conflict table cannot be shown.
    """
    grouped = []
    for option in conflict.get("values") or []:
        value = option.get("value")
        name = _device_name(option, local_client_id)
        for entry in grouped:
            if entry[0] == value:
                entry[1].append(name)
                break
        else:
            grouped.append((value, [name]))
    labels = ["{}: {}".format(", ".join(names), describe_value(conflict.get("field"), value)) for value, names in grouped]
    values = [value for value, _ in grouped]
    return labels, values


def _device_name(option, local_client_id):
    name = option.get("client_name") or str(option.get("client_id", ""))[:8]
    if option.get("client_id") == local_client_id:
        name += " (to Kodi)"
    return name


FIELD_ORDER = ("playcount", "lastplayed", "resume", "userrating")
ROW_NAMES = {
    "playcount": "Liczba odtworzeń",
    "lastplayed": "Ostatnio odtworzone",
    "resume": "Punkt wznowienia",
    "userrating": "Ocena",
}


def group_conflicts(conflicts):
    """Group field conflicts by library item: one table per item."""
    groups = {}
    for conflict in conflicts:
        key = (conflict.get("type"), conflict.get("kodi_id"))
        group = groups.setdefault(key, {
            "type": conflict.get("type"),
            "kodi_id": conflict.get("kodi_id"),
            "identity": list(conflict.get("identity") or []),
            "conflicts": {},
        })
        group["conflicts"][conflict.get("field")] = conflict
    return [groups[key] for key in sorted(groups, key=lambda k: (str(k[0]), k[1]))]


def group_key(group):
    return tuple(sorted(conflict_key(c) for c in group["conflicts"].values()))


def _sortable_date(value):
    return str(value or "")


class ConflictMatrix(object):
    """Table model: rows are conflicting fields, columns are devices.

    Each row has exactly one selected cell. Defaults: the newest date for
    "last played", otherwise this Kodi's value when it takes part in the
    conflict, otherwise the first device.
    """

    def __init__(self, group, local_client_id):
        self.local_client_id = local_client_id
        self.fields = [f for f in FIELD_ORDER if f in group["conflicts"]]
        columns = {}
        for conflict in group["conflicts"].values():
            for option in conflict.get("values") or []:
                columns.setdefault(option.get("client_id"), _device_name(option, local_client_id))
        ordered = sorted(columns, key=lambda cid: (cid != local_client_id, columns[cid].lower()))
        self.columns = [(cid, columns[cid]) for cid in ordered]
        self.cells = []
        for field in self.fields:
            by_client = {o.get("client_id"): o.get("value") for o in group["conflicts"][field].get("values") or []}
            self.cells.append([(cid in by_client, by_client.get(cid)) for cid, _ in self.columns])
        self.selected = [self._default(row) for row in range(len(self.fields))]

    def _default(self, row):
        present = [col for col, (ok, _) in enumerate(self.cells[row]) if ok]
        if self.fields[row] == "lastplayed":
            return max(present, key=lambda col: _sortable_date(self.cells[row][col][1]))
        for col in present:
            if self.columns[col][0] == self.local_client_id:
                return col
        return present[0]

    def has_cell(self, row, col):
        return self.cells[row][col][0]

    def select(self, row, col):
        if self.has_cell(row, col):
            self.selected[row] = col
            return True
        return False

    def cell_text(self, row, col):
        present, value = self.cells[row][col]
        if not present:
            return "—"
        return describe_value(self.fields[row], value)

    def choices(self):
        return {field: self.cells[row][self.selected[row]][1] for row, field in enumerate(self.fields)}


_ACTION_CANCEL = (9, 10, 92, 101)  # parent dir, previous menu, back, mouse right click
_ALIGN_CENTER = 0x00000002 | 0x00000004


def _media_path(name):
    try:
        import os
        import xbmcaddon
        import xbmcvfs
        base = xbmcvfs.translatePath(xbmcaddon.Addon("service.sudosync").getAddonInfo("path"))
        return os.path.join(base, "resources", "media", name)
    except Exception:
        return ""


def build_dialog_class(gui):
    """Create the table dialog class on top of ``gui.WindowDialog`` (no skin XML).

    All drawing uses controls created in code and textures shipped with the
    add-on, so it looks the same in every skin. Buttons work with mouse,
    keyboard and remote; right click or Back closes as "Later".
    """
    base = getattr(gui, "WindowDialog", object)
    if not isinstance(base, type):
        base = object

    class ConflictTableDialog(base):
        X, Y, W = 90, 80, 1100
        ROW_H, BTN_H, LABEL_W = 62, 52, 250

        def __init__(self, *args, **kwargs):
            super(ConflictTableDialog, self).__init__()
            self.result = None

        def setup(self, matrix, title, counter):
            self.matrix = matrix
            self.grid = []
            self.by_id = {}
            rows = len(matrix.fields)
            height = 200 + rows * self.ROW_H + 90
            tex_bg, tex_btn, tex_focus, tex_head = (
                _media_path("kb_dialog_bg.png"), _media_path("kb_button.png"),
                _media_path("kb_button_focus.png"), _media_path("kb_header.png"),
            )
            x, y, w = self.X, self.Y, self.W
            self.addControl(gui.ControlImage(x, y, w, height, tex_bg))
            self.addControl(gui.ControlImage(x, y, w, 64, tex_head))
            self.addControl(gui.ControlLabel(x + 24, y + 12, w - 48, 40,
                                             "[B]KodiBridge — konflikt {}: {}[/B]".format(counter, title)))
            self.addControl(gui.ControlLabel(
                x + 24, y + 74, w - 48, 36,
                "Te same pola zmieniono na różnych urządzeniach. Wybierz w każdym wierszu właściwą wartość.",
                textColor="0xFFB0B8C4"))
            cols = len(matrix.columns)
            col_w = int((w - 48 - self.LABEL_W) / max(cols, 1))
            top = y + 124
            for col, (_, name) in enumerate(matrix.columns):
                self.addControl(gui.ControlLabel(x + 24 + self.LABEL_W + col * col_w, top, col_w - 8, 36,
                                                 "[B]{}[/B]".format(name), alignment=_ALIGN_CENTER))
            for row, field in enumerate(matrix.fields):
                row_y = top + 48 + row * self.ROW_H
                self.addControl(gui.ControlLabel(x + 24, row_y + 8, self.LABEL_W - 12, 36, ROW_NAMES[field]))
                buttons = []
                for col in range(cols):
                    if not matrix.has_cell(row, col):
                        self.addControl(gui.ControlLabel(x + 24 + self.LABEL_W + col * col_w, row_y + 8,
                                                         col_w - 8, 36, "—", alignment=_ALIGN_CENTER))
                        buttons.append(None)
                        continue
                    button = gui.ControlButton(x + 24 + self.LABEL_W + col * col_w, row_y, col_w - 8, self.BTN_H,
                                               "", focusTexture=tex_focus, noFocusTexture=tex_btn,
                                               alignment=_ALIGN_CENTER)
                    self.addControl(button)
                    self.by_id[button.getId()] = (row, col)
                    buttons.append(button)
                self.grid.append(buttons)
            bottom = y + height - 76
            self.save_button = gui.ControlButton(x + w - 24 - 2 * 250 - 16, bottom, 250, self.BTN_H, "[B]Zapisz[/B]",
                                                 focusTexture=tex_focus, noFocusTexture=tex_btn,
                                                 alignment=_ALIGN_CENTER)
            self.later_button = gui.ControlButton(x + w - 24 - 250, bottom, 250, self.BTN_H, "Później",
                                                  focusTexture=tex_focus, noFocusTexture=tex_btn,
                                                  alignment=_ALIGN_CENTER)
            self.addControl(self.save_button)
            self.addControl(self.later_button)
            self._navigation()
            self.refresh()
            first = next((b for row in self.grid for b in row if b is not None), self.save_button)
            self.setFocus(first)

        def _navigation(self):
            grid = self.grid
            for r, row in enumerate(grid):
                present = [b for b in row if b is not None]
                for c, button in enumerate(row):
                    if button is None:
                        continue
                    idx = present.index(button)
                    if idx > 0:
                        button.controlLeft(present[idx - 1])
                    if idx < len(present) - 1:
                        button.controlRight(present[idx + 1])
                    up = self._nearest(r - 1, -1, c)
                    down = self._nearest(r + 1, 1, c)
                    if up is not None:
                        button.controlUp(up)
                    button.controlDown(down if down is not None else self.save_button)
            last = self._nearest(len(grid) - 1, -1, 0)
            for button in (self.save_button, self.later_button):
                if last is not None:
                    button.controlUp(last)
            self.save_button.controlRight(self.later_button)
            self.later_button.controlLeft(self.save_button)

        def _nearest(self, start, step, col):
            r = start
            while 0 <= r < len(self.grid):
                row = self.grid[r]
                if row[col] is not None:
                    return row[col]
                present = [b for b in row if b is not None]
                if present:
                    return present[0]
                r += step
            return None

        def refresh(self):
            for r, row in enumerate(self.grid):
                for c, button in enumerate(row):
                    if button is None:
                        continue
                    text = self.matrix.cell_text(r, c)
                    if self.matrix.selected[r] == c:
                        button.setLabel("[B][COLOR FF7CFC9A]» {} «[/COLOR][/B]".format(text))
                    else:
                        button.setLabel("[COLOR FFB0B8C4]{}[/COLOR]".format(text))

        def onControl(self, control):
            control_id = control.getId()
            if control_id == self.save_button.getId():
                self.result = self.matrix.choices()
                self.close()
            elif control_id == self.later_button.getId():
                self.result = None
                self.close()
            elif control_id in self.by_id:
                row, col = self.by_id[control_id]
                self.matrix.select(row, col)
                self.refresh()

        def onAction(self, action):
            try:
                action_id = action.getId()
            except Exception:
                return
            if action_id in _ACTION_CANCEL:
                self.result = None
                self.close()

    return ConflictTableDialog


def show_conflict_table(matrix, title, counter, gui=None):
    """Show the table and return the chosen values (dict) or None for "Later"."""
    gui = gui or xbmcgui
    dialog = build_dialog_class(gui)()
    try:
        dialog.setup(matrix, title, counter)
        dialog.doModal()
        return dialog.result
    finally:
        del dialog


def _ask_by_select(group, local_client_id, title, counter):
    """Fallback: one standard selection dialog per field."""
    choices = {}
    for field in [f for f in FIELD_ORDER if f in group["conflicts"]]:
        labels, values = choice_options(group["conflicts"][field], local_client_id)
        selected = xbmcgui.Dialog().select(
            "KodiBridge ({}): {} — {}".format(counter, title, ROW_NAMES[field].lower()),
            labels + ["Później (zapytaj po ponownym uruchomieniu Kodi)"],
        )
        if selected < 0 or selected >= len(values):
            return None
        choices[field] = values[selected]
    return choices


def ask_item(group, local_client_id, title, counter):
    """Ask about one item: the table, or per-field selections if it cannot be shown."""
    try:
        matrix = ConflictMatrix(group, local_client_id)
    except Exception as exc:
        log("Cannot build conflict table: {}".format(exc), xbmc.LOGWARNING)
        return _ask_by_select(group, local_client_id, title, counter)
    try:
        return show_conflict_table(matrix, title, counter)
    except Exception as exc:
        log("Conflict table unavailable, using selection dialogs: {}".format(exc), xbmc.LOGWARNING)
        return _ask_by_select(group, local_client_id, title, counter)


# A failed resolution is retried in later cycles; after this many failures of
# the same item it is postponed until the next Kodi start.
MAX_RESOLVE_FAILURES = 3
# How often the active-Kodi lease is refreshed while a dialog is open
# (the lease expires after ACTIVE_KODI_TTL_SECONDS = 180 s).
KEEP_ALIVE_INTERVAL_SECONDS = 30.0


def _run_keeping_lease(blocking_call, keep_alive, interval):
    """Run a modal call while refreshing the lease in the background.

    Returns (result, lease_ok). A modal dialog blocks the service loop, so
    without this the active-Kodi lease would expire while it is open.
    """
    if keep_alive is None:
        return blocking_call(), True
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
        result = blocking_call()
    finally:
        stop.set()
        thread.join(5.0)
    return result, state["ok"]


def _record_error(message):
    try:
        from resources.lib.sudosync_core import update_local_status_from_v2
        update_local_status_from_v2(error=message)
    except Exception:
        pass


def _notify(message, warning=False, duration=6000):
    try:
        xbmcgui.Dialog().notification(
            "KodiBridge — konflikty", message,
            xbmcgui.NOTIFICATION_WARNING if warning else xbmcgui.NOTIFICATION_INFO,
            duration,
        )
    except Exception:
        pass


def prompt_and_resolve(runtime, conflicts, dismissed, local_client_id, is_busy=None, abort_requested=None,
                       keep_alive=None, failures=None, keep_alive_interval=KEEP_ALIVE_INTERVAL_SECONDS):
    """Ask about each conflicting item not yet dismissed this session and apply the choices.

    One table per item (all its conflicting fields). ``is_busy`` (playback or
    library scan) is checked before every dialog. ``keep_alive`` refreshes the
    active-Kodi lease while a dialog is open and once more before writing; if
    it fails, nothing is written. "Later" (or closing the dialog) postpones the
    remaining items until the next Kodi start. A failed resolution is not
    treated as "Later": the user is told, the error goes to status.json and
    the item is offered again in a later cycle, up to MAX_RESOLVE_FAILURES
    times. Returns the number of resolved items.
    """
    if failures is None:
        failures = {}
    counts = {"resolved": 0, "outdated": 0, "failed": 0}
    groups = [g for g in group_conflicts(conflicts) if group_key(g) not in dismissed]
    for index, group in enumerate(groups):
        if (is_busy is not None and is_busy()) or (abort_requested is not None and abort_requested()):
            break
        key = group_key(group)
        title = item_label(group)
        counter = "{}/{}".format(index + 1, len(groups))
        choices, lease_ok = _run_keeping_lease(
            lambda: ask_item(group, local_client_id, title, counter), keep_alive, keep_alive_interval,
        )
        if not choices:
            for rest in groups[index:]:
                dismissed.add(group_key(rest))
            log("Conflict resolution postponed by the user ({} item(s)).".format(len(groups) - index))
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
        _notify("Zapisuję wybór: {}…".format(title), duration=4000)
        try:
            result = runtime.resolve_item_conflicts(
                group["type"], group["kodi_id"], choices,
                abort_requested=abort_requested,
                expected_identity=group["identity"],
                expected_versions={f: c.get("version") for f, c in group["conflicts"].items()},
            )
        except Exception as exc:
            failures[key] = failures.get(key, 0) + 1
            counts["failed"] += 1
            message = "conflict resolution failed for {} {}: {}".format(group["type"], group["kodi_id"], exc)
            log(message, xbmc.LOGERROR)
            _record_error(message)
            if failures[key] >= MAX_RESOLVE_FAILURES:
                dismissed.add(key)
            continue
        status = result.get("status") if isinstance(result, dict) else None
        log("Conflict {} {} -> {} ({}).".format(group["type"], group["kodi_id"], choices, status))
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
    _notify("; ".join(parts).capitalize() + ".", warning=bool(counts["failed"]))


CONFLICT_CHECK_TIMEOUT_SECONDS = 15 * 60
CHECK_HEADING = "KodiBridge — sprawdzam konflikty zmian"
CHECK_START_MESSAGE = "Odczyt biblioteki i porównanie z innymi Kodi. Przy dużej bibliotece może to potrwać kilka minut."


class ConflictCheckProgress(object):
    """Non-blocking progress bar shown while a "Resolve conflicts" request is being processed.

    It stays visible until the check ends (no conflicts, conflicts to choose
    from, or failure), so the user always sees that KodiBridge is still working.
    """

    def __init__(self, gui=None):
        self._gui = gui or xbmcgui
        self._dialog = None
        self.started_at = None

    @property
    def active(self):
        return self.started_at is not None

    def start(self, now):
        self.started_at = now
        try:
            if self._dialog is None:
                self._dialog = self._gui.DialogProgressBG()
                self._dialog.create(CHECK_HEADING, CHECK_START_MESSAGE)
            else:
                self._dialog.update(0, CHECK_HEADING, CHECK_START_MESSAGE)
        except Exception as exc:
            log("Cannot show conflict check progress: {}".format(exc), xbmc.LOGDEBUG)
            self._dialog = None

    def update(self, message, now=None):
        if not self.active:
            return
        elapsed = ""
        if now is not None:
            elapsed = " (trwa {} min)".format(int((now - self.started_at) // 60))
        try:
            if self._dialog is not None:
                self._dialog.update(0, CHECK_HEADING, message + elapsed)
        except Exception:
            pass

    def timed_out(self, now):
        return self.active and (now - self.started_at) >= CONFLICT_CHECK_TIMEOUT_SECONDS

    def finish(self, message=None, warning=False):
        self.started_at = None
        try:
            if self._dialog is not None:
                self._dialog.close()
        except Exception:
            pass
        self._dialog = None
        if message:
            try:
                self._gui.Dialog().notification(
                    "KodiBridge", message,
                    self._gui.NOTIFICATION_WARNING if warning else self._gui.NOTIFICATION_INFO,
                    6000 if warning else 5000,
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
