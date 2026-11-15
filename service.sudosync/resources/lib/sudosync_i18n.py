# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------
# KodiBridge by Sudoizm
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""User interface texts in the language chosen in Kodi.

Every text shown to the user lives in resources/language/*/strings.po and is
read with ``tr(string_id, ...)``. Kodi picks the language and falls back to
English (en_gb) itself. Outside Kodi (tests, tools) Kodi returns nothing and the
text is read from the Polish strings.po, the add-on's primary language.
"""
from __future__ import absolute_import

import io
import os
import re

ADDON_ID = "service.sudosync"
FALLBACK_LANGUAGE = "resource.language.pl_pl"
LANGUAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "language")

_PO_CACHE = {}
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
_LINE = re.compile(r'^(msgctxt|msgid|msgstr)\s+"(.*)"\s*$')
_CONTINUATION = re.compile(r'^"(.*)"\s*$')


def _unescape(text):
    return re.sub(r"\\(.)", lambda match: _ESCAPES.get(match.group(1), match.group(0)), text)


def load_po(language=FALLBACK_LANGUAGE):
    """Return {string_id: (msgid, msgstr)} of one strings.po file (cached)."""
    if language in _PO_CACHE:
        return _PO_CACHE[language]
    entries = {}
    path = os.path.join(LANGUAGE_DIR, language, "strings.po")
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (IOError, OSError):
        lines = []
    current = {}
    field = None

    def flush():
        context = current.get("msgctxt", "")
        if context.startswith("#") and context[1:].isdigit():
            entries[int(context[1:])] = (current.get("msgid", ""), current.get("msgstr", ""))

    for line in lines:
        match = _LINE.match(line)
        if match:
            if match.group(1) == "msgctxt":
                flush()
                current = {}
            field = match.group(1)
            current[field] = _unescape(match.group(2))
            continue
        match = _CONTINUATION.match(line)
        if match and field:
            current[field] = current.get(field, "") + _unescape(match.group(1))
    flush()
    _PO_CACHE[language] = entries
    return entries


def _from_kodi(string_id):
    try:
        import xbmcaddon
        value = xbmcaddon.Addon(ADDON_ID).getLocalizedString(string_id)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _from_po(string_id):
    msgid, msgstr = load_po().get(string_id, ("", ""))
    return msgstr or msgid or None


def tr(string_id, *args, **kwargs):
    """Text ``string_id`` in the Kodi language, formatted with str.format arguments."""
    text = _from_kodi(string_id) or _from_po(string_id) or ""
    if args or kwargs:
        try:
            text = text.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            pass
    return text
