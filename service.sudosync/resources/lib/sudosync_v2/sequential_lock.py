"""Process-local locks for deployments that guarantee a single active Kodi.

This provider does not coordinate separate Kodi devices or processes. It is
safe only when the operator ensures that every other SudoSync service using
the same shared directory is fully stopped for the entire active session.
"""
from __future__ import absolute_import

import threading


class SequentialAccessLockFactory(object):
    """Return one process-wide lock; never claim cross-host safety."""

    def __init__(self):
        # One reentrant lock avoids retaining an unbounded mapping of unique
        # transaction/journal paths over a long-running Kodi service.
        self._lock = threading.RLock()

    def __call__(self, resource_path):
        if not isinstance(resource_path, str) or not resource_path.strip():
            raise ValueError("resource_path must be a non-empty string")
        return self._lock
