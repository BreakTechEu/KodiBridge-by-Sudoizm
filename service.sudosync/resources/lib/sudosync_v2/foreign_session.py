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

TITLE_ID = 32230


def _tr(string_id, *args, **kwargs):
    # This package is imported both as resources.lib.sudosync_v2 and as sudosync_v2.
    try:
        from resources.lib.sudosync_i18n import tr
    except ImportError:
        from sudosync_i18n import tr
    return tr(string_id, *args, **kwargs)


def title():
    return _tr(TITLE_ID)


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
        device = _tr(32231, owner_name)
    elif owner_id:
        device = _tr(32232, owner_id[:8])
    else:
        device = _tr(32233)
    when = _tr(32234, started) if started else ""
    message = _tr(32235, device=device, when=when)
    log_line = (
        "Unfinished v2 operation of another Kodi blocks synchronization "
        "(owner client {}, name {}, session {}, started {}).".format(
            owner_id or "?", owner_name or "?", session_id or "?",
            recovery.get("started_at") or "?")
    )
    return title(), message, log_line
