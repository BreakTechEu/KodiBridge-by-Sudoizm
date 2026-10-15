#!/usr/bin/env python
# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Manual probe for shared native-file lock semantics on a real SMB/NFS mount.

Run the same command from two Kodi hosts, using the same mounted share path.
This tool does not certify a filesystem automatically; operators must observe
that only one host acquires the lock and repeat after terminating the holder.
Never point it at a Kodi VFS URI such as smb:// or nfs://.
"""
from __future__ import print_function

import argparse
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sudosync"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from resources.lib.sudosync_v2.native_shared_lock import (
    NativeSharedFileLock, SharedFileLockBusy, SharedFileLockError,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="absolute path to a lock file on the shared mounted directory")
    parser.add_argument("--label", required=True, help="unique human-readable host/device label")
    parser.add_argument("--hold-seconds", type=int, default=30, help="how long to hold the lock (default: 30)")
    parser.add_argument("--nonblocking", action="store_true", help="fail immediately if another host holds the lock")
    args = parser.parse_args()

    if args.hold_seconds < 1 or args.hold_seconds > 3600:
        parser.error("--hold-seconds must be between 1 and 3600")

    lock = NativeSharedFileLock(args.path, blocking=not args.nonblocking)
    try:
        with lock:
            print("{} ACQUIRED path={!r} pid={} hold_seconds={}".format(
                args.label, lock.path, os.getpid(), args.hold_seconds
            ), flush=True)
            time.sleep(args.hold_seconds)
            print("{} RELEASED path={!r}".format(args.label, lock.path), flush=True)
        return 0
    except SharedFileLockBusy:
        print("{} BUSY path={!r}".format(args.label, args.path), flush=True)
        return 2
    except (SharedFileLockError, ValueError, OSError) as exc:
        print("{} ERROR: {}".format(args.label, exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
