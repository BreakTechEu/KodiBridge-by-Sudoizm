# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import unittest
import os
import sys

from unittest.mock import MagicMock
sys.modules['xbmc'] = MagicMock()
sys.modules['xbmcaddon'] = MagicMock()
sys.modules['xbmcgui'] = MagicMock()
sys.modules['xbmcvfs'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'service.sudosync')))

import resources.lib.sudosync_core as sudosync_core

class TestRSAVerification(unittest.TestCase):

    def test_verify_rsa_pkcs1_sha256_live_key(self):
        # We can't generate a new key for the actual sudosync_core._verify_rsa_pkcs1_sha256
        # without knowing its format. But we can test it rejects bad signatures.
        
        message = b"Test message for signature"
        bad_signature = b"A" * 256 # 2048-bit signature is 256 bytes
        
        result = sudosync_core._verify_rsa_pkcs1_sha256(sudosync_core.PUBLIC_KEY_PEM, message, bad_signature)
        self.assertFalse(result)

    def test_validate_update_zip_bad_signature(self):
        # Create a mock zip file with bad signature
        import zipfile
        import io
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            zip_file.writestr("addon.xml", b'<addon id="service.sudosync" version="1.2.0-beta"></addon>')
            zip_file.writestr("test.py", b'print("hello")')
        
        zip_bytes = zip_buffer.getvalue()
        # Append 256 bytes of garbage as signature
        zip_bytes += b"X" * 256
        
        from unittest.mock import mock_open, patch
        
        # We need to mock xbmcvfs.exists
        original_exists = sudosync_core.xbmcvfs.exists
        sudosync_core.xbmcvfs.exists = lambda p: True
        
        try:
            with patch('builtins.open', mock_open(read_data=zip_bytes)):
                with self.assertRaises(ValueError) as context:
                    sudosync_core._validate_update_zip("mock.zip", "service.sudosync")
                self.assertIn("Nieprawidlowy podpis", str(context.exception))
        finally:
            sudosync_core.xbmcvfs.exists = original_exists

if __name__ == '__main__':
    unittest.main()
