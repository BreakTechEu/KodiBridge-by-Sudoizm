# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Durable shared backup and transaction-journal storage for SudoSync v2."""
from __future__ import absolute_import

import uuid

from .schema import validate_snapshot
from .storage import RepositoryError


JOURNAL_FORMAT = "SudoSync v2 transaction journal"
JOURNAL_SCHEMA_VERSION = 1


class TransactionStoreError(RepositoryError):
    """A transaction backup or journal cannot be trusted."""


def _session_id(value):
    try:
        parsed = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise TransactionStoreError("session ID must be a UUID")
    if parsed != str(value).lower():
        raise TransactionStoreError("session ID must be canonical")
    return parsed


class TransactionStore(object):
    """Store immutable pre-write backups and the journal for each session.

    The injected SharedJsonRepository must already use the production-approved
    shared locking mechanism. A missing or invalid journal is never treated as
    an empty transaction when the active-session marker points to it.
    """

    def __init__(self, repository, base_path, max_records=100000):
        if repository is None:
            raise ValueError("repository is required")
        if not isinstance(base_path, str) or not base_path.strip():
            raise ValueError("base_path must be a non-empty path")
        if type(max_records) is not int or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self._repository = repository
        self._base = base_path.rstrip("/")
        self._backup_dir = self._base + "/transactions/backups"
        self._journal_dir = self._base + "/transactions/journals"
        self._max_records = max_records

    def backup_path(self, session_id):
        return self._backup_dir + "/backup-" + _session_id(session_id) + ".json"

    def journal_path(self, session_id):
        return self._journal_dir + "/journal-" + _session_id(session_id) + ".json"

    def save_backup(self, snapshot, session_id):
        """Persist and read-back validate the pre-mutation snapshot."""
        path = self.backup_path(session_id)
        try:
            validate_snapshot(snapshot, max_records=self._max_records)
            if self._repository.load(path) is not None:
                raise TransactionStoreError("backup already exists for this session")
            if not self._repository.save(path, snapshot):
                raise TransactionStoreError("backup save did not report success")
            stored = self._repository.load(path)
            if stored != snapshot:
                raise TransactionStoreError("backup read-back does not match the snapshot")
        except TransactionStoreError:
            raise
        except Exception as exc:
            raise TransactionStoreError("Cannot persist transaction backup: {}".format(exc))
        return path

    def persist_journal(self, journal):
        """Persist a complete, correctly keyed journal."""
        if not isinstance(journal, dict):
            raise TransactionStoreError("journal must be an object")
        if journal.get("format") != JOURNAL_FORMAT:
            raise TransactionStoreError("journal format is unsupported")
        if journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise TransactionStoreError("journal schema is unsupported")
        session_id = _session_id(journal.get("session_id"))
        if not isinstance(journal.get("client_id"), str) or not journal["client_id"].strip():
            raise TransactionStoreError("journal client ID is invalid")
        if journal.get("status") not in ("prepared", "applying", "needs_recovery", "committed", "aborted"):
            raise TransactionStoreError("journal status is invalid")
        items = journal.get("items")
        if not isinstance(items, list) or not items:
            raise TransactionStoreError("journal must contain at least one transaction item")
        path = self.journal_path(session_id)
        try:
            if not self._repository.save(path, journal):
                raise TransactionStoreError("journal save did not report success")
            stored = self._repository.load(path)
            if stored != journal:
                raise TransactionStoreError("journal read-back does not match the saved journal")
        except TransactionStoreError:
            raise
        except Exception as exc:
            raise TransactionStoreError("Cannot persist transaction journal: {}".format(exc))
        return True

    def load_journal(self, session_id):
        """Load a journal required by an active marker; never invent an empty one."""
        canonical = _session_id(session_id)
        path = self.journal_path(canonical)
        try:
            journal = self._repository.load(path)
        except Exception as exc:
            raise TransactionStoreError("Cannot load transaction journal: {}".format(exc))
        if journal is None:
            raise TransactionStoreError("Active session has no durable transaction journal")
        if not isinstance(journal, dict) or journal.get("session_id") != canonical:
            raise TransactionStoreError("Journal session ID does not match its canonical filename")
        if journal.get("format") != JOURNAL_FORMAT or journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise TransactionStoreError("Journal format/schema is unsupported")
        return journal
