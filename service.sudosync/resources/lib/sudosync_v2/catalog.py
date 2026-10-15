# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Versioned shared snapshot catalog backed by SharedJsonRepository."""
from __future__ import absolute_import

import re
import uuid

from . import FORMAT_NAME, SCHEMA_VERSION
from .schema import validate_snapshot
from .storage import RepositoryError


_CLIENT_FILE = re.compile(r"^client-([0-9a-fA-F-]{36})\.json$")


class SnapshotCatalogError(RepositoryError):
    """Shared client snapshot catalog is inconsistent or unavailable."""


def _canonical_client_id(value):
    try:
        canonical = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise SnapshotCatalogError("client ID must be a UUID")
    if canonical != str(value).lower():
        raise SnapshotCatalogError("client ID must be canonical")
    return canonical


class SnapshotCatalog(object):
    """Read/publish snapshots in a shared clients directory.

    repository must be a SharedJsonRepository and backend must provide
    listdir/mkdirs. The repository's lock factory must already be a verified
    cross-process lock for this shared filesystem.
    """

    def __init__(self, repository, backend, base_path, max_records=100000):
        if repository is None or backend is None:
            raise ValueError("repository and backend are required")
        if not isinstance(base_path, str) or not base_path.strip():
            raise ValueError("base_path must be a non-empty path")
        self._repository = repository
        self._backend = backend
        self._base_path = base_path.rstrip("/")
        self._clients_path = self._base_path + "/clients"
        self._max_records = max_records

    def _path_for(self, client_id):
        return self._clients_path + "/client-" + _canonical_client_id(client_id) + ".json"

    def publish(self, snapshot):
        try:
            validate_snapshot(snapshot, max_records=self._max_records)
        except (ValueError, TypeError) as exc:
            raise SnapshotCatalogError("Refusing to publish invalid snapshot: {}".format(exc))
        if not self._backend.mkdirs(self._clients_path):
            raise SnapshotCatalogError("Cannot create shared clients directory")
        client_id = snapshot["client"]["id"]
        path = self._path_for(client_id)
        try:
            if not self._repository.save(path, snapshot):
                raise SnapshotCatalogError("Shared snapshot save did not report success")
        except RepositoryError:
            raise
        except Exception as exc:
            raise SnapshotCatalogError("Cannot publish client snapshot: {}".format(exc))
        return path

    def load(self, client_id):
        path = self._path_for(client_id)
        try:
            snapshot = self._repository.load(path)
        except RepositoryError:
            raise
        except Exception as exc:
            raise SnapshotCatalogError("Cannot load client snapshot: {}".format(exc))
        if snapshot is None:
            return None
        try:
            validate_snapshot(snapshot, max_records=self._max_records)
        except (ValueError, TypeError) as exc:
            raise SnapshotCatalogError("Shared client snapshot is invalid: {}".format(exc))
        if snapshot["client"]["id"] != _canonical_client_id(client_id):
            raise SnapshotCatalogError("Snapshot client ID does not match its filename")
        return snapshot

    def load_peers(self, exclude_client_id=None):
        exclude = _canonical_client_id(exclude_client_id) if exclude_client_id else None
        try:
            if not self._backend.exists(self._clients_path):
                return []
            directories, files = self._backend.listdir(self._clients_path)
        except Exception as exc:
            raise SnapshotCatalogError("Cannot enumerate shared client snapshots: {}".format(exc))
        peers = []
        seen = set()
        for filename in sorted(files):
            if not filename.startswith("client-") or not filename.endswith(".json"):
                continue
            match = _CLIENT_FILE.fullmatch(filename)
            if not match:
                raise SnapshotCatalogError("Unexpected client snapshot filename: " + filename)
            client_id = _canonical_client_id(match.group(1))
            if client_id in seen:
                raise SnapshotCatalogError("Duplicate client snapshot file for " + client_id)
            seen.add(client_id)
            if client_id == exclude:
                continue
            snapshot = self.load(client_id)
            if snapshot is None:
                raise SnapshotCatalogError("Listed client snapshot disappeared: " + filename)
            peers.append(snapshot)
        return peers
