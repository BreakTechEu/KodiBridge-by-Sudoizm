# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import os
import sys
import unittest
from unittest.mock import MagicMock

# Mock xbmc modules
sys.modules['xbmc'] = MagicMock()
sys.modules['xbmcaddon'] = MagicMock()
sys.modules['xbmcgui'] = MagicMock()
sys.modules['xbmcvfs'] = MagicMock()

# Append the resources/lib directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'service.sudosync')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'service.sudosync', 'resources', 'lib')))

import sudosync_core

class TestSudoSync(unittest.TestCase):

    def test_numeric_version(self):
        self.assertEqual(sudosync_core._numeric_version("1.1.0-alpha"), (1, 1, 0, 0, 0))
        self.assertEqual(sudosync_core._numeric_version("1.1.0-beta"), (1, 1, 0, 1, 0))
        self.assertEqual(sudosync_core._numeric_version("1.1.0-rc"), (1, 1, 0, 2, 0))
        self.assertEqual(sudosync_core._numeric_version("1.1.0"), (1, 1, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("1.2.0"), (1, 2, 0, 3, 0))
        
        self.assertGreater(sudosync_core._numeric_version("1.1.0-beta"), sudosync_core._numeric_version("1.1.0-alpha"))
        self.assertGreater(sudosync_core._numeric_version("1.1.0"), sudosync_core._numeric_version("1.1.0-beta"))

        # Prerelease stage numbers (beta2 > beta)
        self.assertEqual(sudosync_core._numeric_version("1.2.0-beta2"), (1, 2, 0, 1, 2))
        self.assertEqual(sudosync_core._numeric_version("1.2.0-rc1"), (1, 2, 0, 2, 1))
        self.assertEqual(sudosync_core._numeric_version("1.2.1-alpha"), (1, 2, 1, 0, 0))
        self.assertGreater(
            sudosync_core._numeric_version("1.2.0-beta2"),
            sudosync_core._numeric_version("1.2.0-beta"),
        )
        self.assertGreater(
            sudosync_core._numeric_version("1.2.0-beta2"),
            sudosync_core._numeric_version("1.2.0-beta1"),
        )

        # Full ordering chain: alpha < beta < rc < final
        self.assertLess(
            sudosync_core._numeric_version("1.2.0-beta"),
            sudosync_core._numeric_version("1.2.0-beta2"),
        )
        self.assertLess(
            sudosync_core._numeric_version("1.2.0-beta2"),
            sudosync_core._numeric_version("1.2.0-rc1"),
        )
        self.assertLess(
            sudosync_core._numeric_version("1.2.0-rc1"),
            sudosync_core._numeric_version("1.2.0"),
        )
        self.assertLess(
            sudosync_core._numeric_version("1.2.0"),
            sudosync_core._numeric_version("1.2.1-alpha"),
        )

        # Format variants (. and - separators, compact rc1)
        self.assertEqual(sudosync_core._numeric_version("1.2.0-beta.2"), (1, 2, 0, 1, 2))
        self.assertEqual(sudosync_core._numeric_version("1.2.0-rc.3"), (1, 2, 0, 2, 3))
        self.assertEqual(sudosync_core._numeric_version("1.2.0rc1"), (1, 2, 0, 2, 1))

        # CHANGELOG compatibility
        self.assertEqual(sudosync_core._numeric_version("SudoSync 1.1.0 (v2 Beta)"), (1, 1, 0, 1, 0))
        self.assertEqual(sudosync_core._numeric_version("SudoSync 1.0.0"), (1, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("0.4.2-alpha: powiadomienia LIVE"), (0, 4, 2, 0, 0))
        self.assertEqual(sudosync_core._numeric_version("SudoSync 0.4.x — historia wersji testowej"), (0, 4, 0, 3, 0))

        # Edge cases: none should raise
        self.assertEqual(sudosync_core._numeric_version(None), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version(""), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("   "), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version(123), (123, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version([]), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version({}), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("not_a_version"), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("search"), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("source"), (0, 0, 0, 3, 0))
        self.assertEqual(sudosync_core._numeric_version("1.2.0-final"), (1, 2, 0, 3, 0))


    def test_nfo_cache_and_clear(self):
        sudosync_core._NFO_ID_CACHE.clear()
        sudosync_core._NFO_ID_CACHE["/path/test.mkv"] = {"sudosync": "test-123"}
        self.assertEqual(len(sudosync_core._NFO_ID_CACHE), 1)
        sudosync_core._NFO_ID_CACHE.clear()
        self.assertEqual(len(sudosync_core._NFO_ID_CACHE), 0)


    def test_record_merges_kodi_and_nfo_ids(self):
        sudosync_core._NFO_ID_CACHE.clear()
        sudosync_core._NFO_ID_CACHE["smb://nas/movies/Matrix.mkv"] = {
            "sudosync": "ss-999",
            "tmdb": "603",
        }
        raw_movie = {
            "movieid": 10,
            "title": "The Matrix",
            "year": 1999,
            "file": "smb://nas/movies/Matrix.mkv",
            "uniqueid": {"imdb": "tt0133093"},
            "playcount": 1,
            "lastplayed": "2026-10-10 20:00:00",
            "resume": {"position": 0, "total": 8160},
            "userrating": 9,
        }
        rec = sudosync_core._record("movie", raw_movie)
        self.assertEqual(rec["ids"].get("imdb"), "tt0133093")
        self.assertEqual(rec["ids"].get("sudosync"), "ss-999")
        self.assertEqual(rec["ids"].get("tmdb"), "603")
        sudosync_core._NFO_ID_CACHE.clear()


if __name__ == '__main__':
    unittest.main()

