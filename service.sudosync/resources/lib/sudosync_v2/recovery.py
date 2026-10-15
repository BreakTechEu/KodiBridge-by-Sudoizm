# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Automatic recovery of an unfinished transaction by its owning Kodi client.

This module deliberately requires the original shared marker and its durable
journal. It never steals another client's session and never uses marker age.
"""
from __future__ import absolute_import

from copy import deepcopy

from .transaction import TransactionError, _field_matches
from .identity import identity_matches_kodi_item, is_valid_serialized_identity


JOURNAL_FORMAT = "SudoSync v2 transaction journal"
JOURNAL_SCHEMA_VERSION = 1
SYNC_FIELDS = ("playcount", "lastplayed", "resume", "userrating")


class RecoveryRunner(object):
    """Resume an interrupted transaction only on the Kodi client that owns it.

    load_journal(session_id) must load the durable journal for that exact ID.
    persist_journal(journal) must durably persist the whole journal or raise.
    """

    def __init__(self, kodi, coordinator, load_journal, persist_journal, clock=None):
        if kodi is None or coordinator is None:
            raise ValueError("kodi adapter and coordinator are required")
        if not callable(load_journal) or not callable(persist_journal):
            raise ValueError("journal callbacks are required")
        self._kodi = kodi
        self._coordinator = coordinator
        self._load_journal = load_journal
        self._persist_journal = persist_journal
        if clock is None:
            from .transaction import _utc_now
            clock = _utc_now
        self._clock = clock

    def _details(self, item):
        if item["type"] == "movie":
            return self._kodi.get_movie_details(item["kodi_id"])
        return self._kodi.get_episode_details(item["kodi_id"])

    def _set_details(self, item):
        if item["type"] == "movie":
            return self._kodi.set_movie_details(item["kodi_id"], item["changes"])
        return self._kodi.set_episode_details(item["kodi_id"], item["changes"])

    def _matches_after(self, item, actual):
        return all(_field_matches(actual, expected, field)
                   for field, expected in item["changes"].items())

    def _matches_before_or_after(self, item, actual):
        before = item.get("before")
        if not isinstance(before, dict):
            return False
        for field, expected_after in item["changes"].items():
            if field not in before:
                return False
            if not (_field_matches(actual, expected_after, field)
                    or _field_matches(actual, before[field], field)):
                return False
        return True

    def _validate_journal(self, journal, client_id, session_id):
        if not isinstance(journal, dict):
            raise TransactionError("recovery journal is missing or malformed")
        if journal.get("format") != JOURNAL_FORMAT or journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
            raise TransactionError("recovery journal format/schema is unsupported")
        if journal.get("client_id") != client_id or journal.get("session_id") != session_id:
            raise TransactionError("recovery journal owner/session does not match the active marker")
        if journal.get("status") not in ("prepared", "applying", "needs_recovery", "committed", "aborted"):
            raise TransactionError("recovery journal is not in a recoverable state")
        items = journal.get("items")
        if not isinstance(items, list) or not items:
            raise TransactionError("recovery journal has no transaction items")
        seen = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("type") not in ("movie", "episode"):
                raise TransactionError("recovery item {} has invalid media type".format(index))
            kodi_id = item.get("kodi_id")
            if type(kodi_id) is not int or kodi_id < 1:
                raise TransactionError("recovery item {} has invalid Kodi ID".format(index))
            if (item["type"], kodi_id) in seen:
                raise TransactionError("recovery journal contains duplicate Kodi targets")
            seen.add((item["type"], kodi_id))
            if not isinstance(item.get("before"), dict) or not isinstance(item.get("changes"), dict):
                raise TransactionError("recovery item {} has invalid before/changes data".format(index))
            identity = item.get("identity")
            if not isinstance(identity, list) or not identity:
                raise TransactionError("recovery item {} has no strong identity".format(index))
            if not is_valid_serialized_identity(identity):
                raise TransactionError("recovery item {} has an invalid strong identity".format(index))
            if not item["changes"] or not set(item["changes"]).issubset(SYNC_FIELDS):
                raise TransactionError("recovery item {} has unsupported changes".format(index))
            if item.get("status") not in ("pending", "applying", "verified"):
                raise TransactionError("recovery item {} has unsupported status".format(index))
        return journal

    def _verify_after(self, item):
        actual = self._details(item)
        if not identity_matches_kodi_item(item["identity"], actual):
            raise TransactionError("Recovery identity check failed for {} {}; refusing to access a different library item".format(item["type"], item["kodi_id"]))
        if not isinstance(actual, dict) or not self._matches_after(item, actual):
            raise TransactionError("Recovery verification failed for {} {}".format(item["type"], item["kodi_id"]))

    def recover_on_start(self, client_id):
        """Inspect ownership, then resume this client's interrupted work."""
        ownership = self._coordinator.inspect(client_id)
        if ownership["state"] == "clear":
            return {"status": "clear"}
        if ownership["state"] == "blocked":
            marker = ownership["marker"]
            return {"status": "blocked", "owner_client_id": marker["client_id"],
                    "session_id": marker["session_id"], "started_at": marker["started_at"]}
        if ownership["state"] != "owned_recovery":
            raise TransactionError("session coordinator returned an unknown ownership state")

        marker = ownership["marker"]
        session_id = marker["session_id"]
        self._coordinator.resume_owned(client_id, session_id)
        try:
            journal = self._load_journal(session_id)
        except Exception as exc:
            raise TransactionError("Cannot load the owner's recovery journal: {}".format(exc))
        journal = self._validate_journal(journal, client_id, session_id)

        # An aborted journal is written only when the transaction runner knows
        # no Kodi mutation was attempted. If the process crashed before marker
        # release, clear that marker without applying any pending item.
        if journal["status"] == "aborted":
            try:
                self._coordinator.release(session_id)
            except Exception as exc:
                raise TransactionError(
                    "Pre-write abort is durable but its session marker could not be released: {}".format(exc)
                )
            return {"status": "recovered", "session_id": session_id, "aborted": True}

        try:
            if self._kodi.is_video_playing():
                return {"status": "deferred", "reason": "video_playing", "session_id": session_id}

            # A committed journal may coexist with a marker if Kodi stopped
            # between durable commit and marker release. Verify before release.
            if journal["status"] == "committed":
                for item in journal["items"]:
                    self._verify_after(item)
                self._coordinator.release(session_id)
                return {"status": "recovered", "session_id": session_id, "already_committed": True}

            for item in journal["items"]:
                if self._kodi.is_video_playing():
                    raise TransactionError("video playback started during recovery")
                actual = self._details(item)
                if not isinstance(actual, dict):
                    raise TransactionError("Kodi returned invalid details during recovery")
                if not identity_matches_kodi_item(item["identity"], actual):
                    raise TransactionError(
                        "Recovery identity check failed for {} {}; refusing to access a different library item"
                        .format(item["type"], item["kodi_id"])
                    )

                if item["status"] == "verified":
                    if not self._matches_after(item, actual):
                        raise TransactionError(
                            "A previously verified field changed after interruption; refusing to overwrite it"
                        )
                    continue

                # The RPC may have completed just before Kodi stopped, or it
                # may not have started. Resume only from recorded before/after values.
                if not self._matches_after(item, actual):
                    if not self._matches_before_or_after(item, actual):
                        raise TransactionError(
                            "Current Kodi state matches neither before nor intended after values; "
                            "manual inspection is required"
                        )
                    item["status"] = "applying"
                    item["started_at"] = self._clock()
                    journal["status"] = "applying"
                    journal["error"] = ""
                    self._persist_journal(deepcopy(journal))
                    self._set_details(item)
                    self._verify_after(item)

                item["status"] = "verified"
                item["verified_at"] = self._clock()
                self._persist_journal(deepcopy(journal))

            journal["status"] = "committed"
            journal["finished_at"] = self._clock()
            journal["error"] = ""
            self._persist_journal(deepcopy(journal))
            self._coordinator.release(session_id)
            return {"status": "recovered", "session_id": session_id, "already_committed": False}
        except Exception as exc:
            journal["status"] = "needs_recovery"
            journal["error"] = str(exc)
            journal["finished_at"] = self._clock()
            try:
                self._persist_journal(deepcopy(journal))
            except Exception:
                pass
            if isinstance(exc, TransactionError):
                raise
            raise TransactionError("Recovery stopped safely; the session remains blocked: {}".format(exc))
