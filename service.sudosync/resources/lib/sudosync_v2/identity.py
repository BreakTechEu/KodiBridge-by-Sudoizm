# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Conservative media identity helpers for SudoSync v2."""
from __future__ import absolute_import
import re
import unicodedata
from . import IDENTIFIER_TYPES

def normalize_identifier(kind, value):
    """Return a canonical strong identifier, or None for empty/invalid input."""
    kind = str(kind or "").strip().lower()
    if kind not in IDENTIFIER_TYPES or value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    if not text or len(text) > 256 or any(ord(ch) < 32 for ch in text):
        return None
    if kind == "imdb" and not re.fullmatch(r"tt[0-9]+", text):
        return None
    if kind in ("tmdb", "tvdb", "trakt") and not re.fullmatch(r"[0-9]+", text):
        return None
    if kind == "sudosync" and not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{2,127}", text):
        return None
    return text

def identifiers_from_kodi_item(item):
    """Return normalized strong identifiers from Kodi library details."""
    if not isinstance(item, dict):
        return {}
    identifiers = {}
    unique = item.get("uniqueid")
    if isinstance(unique, dict):
        for kind in ("imdb", "tmdb", "tvdb", "trakt", "sudosync"):
            value = normalize_identifier(kind, unique.get(kind))
            if value is not None:
                identifiers[kind] = value
    imdbnumber = normalize_identifier("imdb", item.get("imdbnumber"))
    if imdbnumber is not None:
        if "imdb" in identifiers and identifiers["imdb"] != imdbnumber:
            raise ValueError("Kodi returned conflicting IMDb identifiers for one library item")
        identifiers["imdb"] = imdbnumber
    return identifiers

def identity_matches_kodi_item(expected_identity, item):
    """True when Kodi details still contain at least one planned strong ID."""
    actual = identifiers_from_kodi_item(item)
    expected = dict(value.split(":", 1) for value in expected_identity)
    shared_kinds = set(actual).intersection(expected)
    return bool(shared_kinds) and all(actual[kind] == expected[kind] for kind in shared_kinds)

def is_valid_serialized_identity(identity):
    """Validate the canonical ``kind:value`` identity list used in plans."""
    if not isinstance(identity, list) or not identity:
        return False
    seen_kinds = set()
    for value in identity:
        if not isinstance(value, str) or ":" not in value:
            return False
        kind, identifier = value.split(":", 1)
        normalized = normalize_identifier(kind, identifier)
        if (kind != kind.strip().lower() or kind in seen_kinds
                or normalized is None or normalized != identifier):
            return False
        seen_kinds.add(kind)
    return True

def strong_aliases(media_type, identifiers):
    """Build typed aliases; media type is part of identity by design."""
    media_type = str(media_type or "").strip().lower()
    if media_type not in ("movie", "episode") or not isinstance(identifiers, dict):
        return frozenset()
    aliases = set()
    for kind in IDENTIFIER_TYPES:
        value = normalize_identifier(kind, identifiers.get(kind))
        if value is not None:
            aliases.add((media_type, kind, value))
    return frozenset(aliases)

def match_by_strong_identity(left_type, left_ids, right_type, right_ids):
    """True only when records share at least one well-formed typed strong ID."""
    left_type = str(left_type or "").strip().lower()
    right_type = str(right_type or "").strip().lower()
    if left_type != right_type or left_type not in ("movie", "episode"):
        return False
    return bool(strong_aliases(left_type, left_ids).intersection(strong_aliases(right_type, right_ids)))
