# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Message for an unfinished operation left by another Kodi.

Another Kodi that stopped abnormally (e.g. killed by Android) during a sync
operation leaves its marker on the NAS. Only that Kodi may finish or roll back
its own operation, so this Kodi never takes the marker over: it tells the user
which device has to be started to finish it.
"""
from __future__ import absolute_import, division, print_function

import calendar
import time

TITLE = "KodiBridge — synchronizacja wstrzymana"


def owner_client_name(load_snapshot, client_id):
    """Return the owner's device name from its published snapshot, or None."""
    if not client_id or not callable(load_snapshot):
        return None
    try:
        snapshot = load_snapshot(client_id)
        name = snapshot["client"]["name"].strip() if isinstance(snapshot, dict) else ""
    except Exception:
        return None
    return name or None


def format_started_at(started_at, localtime=None):
    """Format a marker timestamp (UTC, 'YYYY-MM-DDTHH:MM:SS[.fff]Z') in local time."""
    if not isinstance(started_at, str) or not started_at.endswith("Z"):
        return None
    text = started_at[:-1].split(".", 1)[0]
    try:
        epoch = calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    return time.strftime("%d.%m.%Y %H:%M", (localtime or time.localtime)(epoch))


def describe_foreign_session(recovery, owner_name=None, localtime=None):
    """Return (title, message, log_line) for a 'blocked' recovery result."""
    recovery = recovery if isinstance(recovery, dict) else {}
    owner_id = str(recovery.get("owner_client_id") or "")
    session_id = str(recovery.get("session_id") or "")
    started = format_started_at(recovery.get("started_at"), localtime=localtime)
    if owner_name:
        device = "„{}”".format(owner_name)
    elif owner_id:
        device = "o identyfikatorze {}".format(owner_id[:8])
    else:
        device = "nieznanym"
    when = " rozpoczętej {}".format(started) if started else ""
    message = (
        "Kodi na urządzeniu {device} nie dokończyło operacji synchronizacji{when}, "
        "prawdopodobnie zostało zamknięte nieprawidłowo. Tylko ono może ją dokończyć albo wycofać. "
        "Do tego czasu to Kodi nie synchronizuje stanu obejrzenia.\n"
        "Zamknij to Kodi, uruchom Kodi na urządzeniu {device}, odczekaj około 2 minut bez odtwarzania "
        "i zamknij je z menu Kodi (Wyłącz → Zakończ)."
    ).format(device=device, when=when)
    log_line = (
        "Unfinished v2 operation of another Kodi blocks synchronization "
        "(owner client {}, name {}, session {}, started {}).".format(
            owner_id or "?", owner_name or "?", session_id or "?",
            recovery.get("started_at") or "?")
    )
    return TITLE, message, log_line
