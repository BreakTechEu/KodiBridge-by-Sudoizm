# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""SudoSync v2 domain layer.

This package is deliberately independent of Kodi APIs so its rules can be
unit-tested on ordinary Python. Runtime integration is a later milestone.
"""

SCHEMA_VERSION = 1
FORMAT_NAME = "SudoSync v2 snapshot"
SYNC_FIELDS = ("playcount", "lastplayed", "resume", "userrating")
IDENTIFIER_TYPES = ("imdb", "tmdb", "tvdb", "trakt", "sudosync")
