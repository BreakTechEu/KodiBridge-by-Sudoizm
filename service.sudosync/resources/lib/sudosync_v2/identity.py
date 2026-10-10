""""Conservative media identity helpers for SudoSync v2."""
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
"