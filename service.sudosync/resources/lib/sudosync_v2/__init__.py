"""SudoSync v2 domain layer.

This package is deliberately independent of Kodi APIs so its rules can be
unit-tested on ordinary Python. Runtime integration is a later milestone.
"""

SCHEMA_VERSION = 1
FORMAT_NAME = "SudoSync v2 snapshot"
SYNC_FIELDS = ("playcount", "lastplayed", "resume", "userrating")
IDENTIFIER_TYPES = ("imdb", "tmdb", "tvdb", "trakt", "sudosync")
