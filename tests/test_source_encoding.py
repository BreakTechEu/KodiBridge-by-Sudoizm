# -*- coding: utf-8 -*-
"""Test weryfikujacy brak podwojnie zakodowanych ciagow znakow w repozytorium."""

import os
import re
import unittest

UNDEFINED_CP1250 = {0x81, 0x83, 0x88, 0x90, 0x98}


def decode_cp1250_to_utf8(s: str) -> str:
    raw_bytes = bytearray()
    for ch in s:
        code = ord(ch)
        if code in UNDEFINED_CP1250:
            raw_bytes.append(code)
        else:
            try:
                b = ch.encode("cp1250")
                raw_bytes.extend(b)
            except UnicodeEncodeError:
                if code <= 255:
                    raw_bytes.append(code)
                else:
                    return s
    try:
        decoded = raw_bytes.decode("utf-8")
        if decoded != s:
            return decoded
        return s
    except UnicodeDecodeError:
        return s


class TestSourceEncoding(unittest.TestCase):
    def test_no_double_encoded_source_files(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_extensions = {".py", ".xml", ".po", ".md", ".yml", ".txt"}
        own_path = os.path.abspath(__file__)

        found_violations = []

        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "_audit_v2", "node_modules", "venv")
            ]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in target_extensions:
                    file_path = os.path.join(root, file)
                    if os.path.abspath(file_path) == own_path:
                        continue
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except Exception:
                        continue

                    for m in re.finditer(r"[^\x00-\x7f]+", content):
                        run = m.group(0)
                        fixed = decode_cp1250_to_utf8(run)
                        if fixed != run:
                            rel_path = os.path.relpath(file_path, repo_root)
                            found_violations.append((rel_path, run, fixed))
                            break

        if found_violations:
            msg_lines = [
                f"Znaleziono {len(found_violations)} plikow z podwojnie zakodowanymi znakami:"
            ]
            for rel, bad, good in found_violations:
                msg_lines.append(f"  {rel}: {bad!r} -> {good!r}")
            self.fail("\n".join(msg_lines))


if __name__ == "__main__":
    unittest.main()
