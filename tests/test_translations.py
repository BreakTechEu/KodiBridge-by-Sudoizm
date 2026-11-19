# -*- coding: utf-8 -*-
"""User interface texts come from strings.po in every supported language."""
import ast
import io
import os
import re
import sys
import tokenize
import unittest
from unittest.mock import MagicMock, patch

for _name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcvfs"):
    sys.modules.setdefault(_name, MagicMock())
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

from resources.lib import sudosync_i18n  # noqa: E402

LANGUAGES = ("resource.language.en_gb", "resource.language.pl_pl")
UI_SOURCES = (
    "default.py",
    "service.py",
    "resources/lib/sudosync_core.py",
    "resources/lib/sudosync_bootstrap.py",
    "resources/lib/sudosync_conflicts.py",
    "resources/lib/sudosync_v2/foreign_session.py",
    "resources/lib/kodibridge_activity.py",
)
POLISH_LETTERS = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _source(name):
    with io.open(os.path.join(SERVICE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _text(entry):
    msgid, msgstr = entry
    return msgstr or msgid


class StringsFileTests(unittest.TestCase):
    def setUp(self):
        self.po = {language: sudosync_i18n.load_po(language) for language in LANGUAGES}

    def test_every_language_has_the_same_strings(self):
        english = set(self.po["resource.language.en_gb"])
        self.assertTrue(english)
        for language in LANGUAGES:
            self.assertEqual(set(self.po[language]), english, language)

    def test_placeholders_match_between_languages(self):
        for string_id, entry in self.po["resource.language.en_gb"].items():
            expected = sorted(PLACEHOLDER.findall(_text(entry)))
            for language in LANGUAGES:
                actual = sorted(PLACEHOLDER.findall(_text(self.po[language][string_id])))
                self.assertEqual(actual, expected, "{} #{}".format(language, string_id))

    def test_msgid_is_the_same_in_every_language(self):
        for string_id, entry in self.po["resource.language.en_gb"].items():
            for language in LANGUAGES:
                self.assertEqual(self.po[language][string_id][0], entry[0], "{} #{}".format(language, string_id))

    def test_every_string_used_in_code_exists(self):
        used = set()
        for name in UI_SOURCES:
            source = _source(name)
            used.update(int(n) for n in re.findall(r"\b_?tr\((\d{5})\b", source))
            used.update(int(n) for n in re.findall(r"_ID = (32\d{3})\b", source))
            used.update(int(n) for n in re.findall(r'"\w+": (32\d{3}),', source))
            used.update(int(n) for n in re.findall(r"tr\((\d{5}) if ", source))
        self.assertTrue(used)
        missing = sorted(used - set(self.po["resource.language.en_gb"]))
        self.assertEqual(missing, [])


class NoHardCodedPolishTests(unittest.TestCase):
    def test_polish_text_only_in_log_calls(self):
        offenders = []
        for name in UI_SOURCES:
            source = _source(name)
            offsets = [0]
            for line in source.splitlines(True):
                offsets.append(offsets[-1] + len(line))
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type != tokenize.STRING or token.string.startswith(('"""', "'''")):
                    continue
                try:
                    value = ast.literal_eval(token.string)
                except Exception:
                    continue
                if not isinstance(value, str) or not POLISH_LETTERS.search(value):
                    continue
                before = source[:offsets[token.start[0] - 1] + token.start[1]]
                if before.rfind("log(") > max(before.rfind("tr("), before.rfind("Error("), before.rfind("Dialog()")):
                    continue
                offenders.append("{}:{}".format(name, token.start[0]))
        self.assertEqual(offenders, [])


class TranslateTests(unittest.TestCase):
    def test_outside_kodi_the_polish_text_is_used(self):
        with patch.object(sudosync_i18n, "_from_kodi", return_value=None):
            self.assertEqual(sudosync_i18n.tr(32118), "Zainstaluj")
            self.assertEqual(sudosync_i18n.tr(32160, 3, 0), "Przywrócono: 3 plików. Błędy: 0")

    def test_text_from_kodi_wins_and_is_formatted(self):
        with patch.object(sudosync_i18n, "_from_kodi", return_value="Restored: {0} files. Errors: {1}"):
            self.assertEqual(sudosync_i18n.tr(32160, 3, 0), "Restored: 3 files. Errors: 0")

    def test_kodi_addon_api_is_used(self):
        addon = MagicMock()
        addon.getLocalizedString.return_value = "Install"
        with patch.object(sys.modules["xbmcaddon"], "Addon", return_value=addon):
            self.assertEqual(sudosync_i18n.tr(32118), "Install")
        addon.getLocalizedString.assert_called_with(32118)

    def test_newlines_are_unescaped(self):
        with patch.object(sudosync_i18n, "_from_kodi", return_value=None):
            self.assertIn("\n", sudosync_i18n.tr(32115, "2.3.6"))

    def test_bad_arguments_do_not_raise(self):
        with patch.object(sudosync_i18n, "_from_kodi", return_value="{0} {1}"):
            self.assertEqual(sudosync_i18n.tr(1, "only one"), "{0} {1}")


if __name__ == "__main__":
    unittest.main()
