# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
import sys
import rsa
import hashlib

def sign_release(zip_path, priv_key_path):
    with open(priv_key_path, "r") as f:
        priv_pem = f.read()
        
    privkey = rsa.PrivateKey.load_pkcs1(priv_pem)
    
    with open(zip_path, "rb") as f:
        data = f.read()
        
    signature = rsa.sign(data, privkey, 'SHA-256')
    
    with open(zip_path, "ab") as f:
        f.write(signature)
        
    print(f"Signed {zip_path} successfully. Added {len(signature)} bytes.")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: sign_release.py <zip_path> <priv_key_path>")
    else:
        sign_release(sys.argv[1], sys.argv[2])
