# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Journaled, fail-closed application of an approved SudoSync plan.

The runner requires an injected verified shared-operation coordinator and
durable backup/journal callbacks. It never guesses that a partial operation is
safe to retry and intentionally leaves the active marker in place on uncertain
failure for explicit recovery.
"""
from __future__ import absolute_import

from copy import deepcopy
from datetime import datetime, timezone

from .schema import validate_snapshot
from .identity import identity_matches_kodi_item, is_valid_serialized_identity


class TransactionError(RuntimeError):
    """A plan could not be applied and verified safely."""


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _actual_field(details, field):
    if field == "userrating":
        value = details.get(field)
        return None if value in (None, 0) else value
    if field == "lastplayed":
        return details.get(field) or ""
    if field == "playcount":
        return details.get(field, 0)
    if field == "resume":
        value = details.get("resume")
        if not isinstance(value, dict):
            value = {}
        return {
            "position": float(value.get("position", 0) or 0),
            "total": float(value.get("total", 0) or 0),
        }
    raise ValueError("unsupported synchronized field: " + field)


def _field_matches(actual, expected, field):
    observed = _actual_field(actual, field)
    if field == "resume":
        return (
            abs(observed["position"] - float(expected["position"])) <= 1.0
            and abs(observed["total"] - float(expected["total"])) <= 1.0
        )
    return observed == expected


class TransactionRunner(object):
    """Apply a precomputed plan only under an injected exclusive session lock.

    Required callbacks:
      save_backup(snapshot, session_id) -> durable backup reference
      persist_journal(journal) -> durable write or raises
    """

    def __init__(self, kodi, coordinator, save_backup, persist_journal, clock=None):
        if kodi is None or coordinator is None:
            raise ValueError("kodi adapter and coordinator are required")
        if not callable(save_backup) or not callable(persist_journal):
            raise ValueError("backup and journal callbacks are required")
        self._kodi = kodi
        self._coordinator = coordinator
        self._save_backup = save_backup
        self._persist_journal = persist_journal
        self._clock = clock or _utc_now

    @staticmethod
    def _validate_plan(plan, client_id):
        if not isinstance(plan, dict) or not isinstance(plan.get("changes"), list):
            raise TransactionError("plan is malformed")
        plan_client = plan.get("local_client_id") or plan.get("target_client_id")
        if plan_client != client_id:
            raise TransactionError("plan belongs to a different target client")
        normalized = []
        seen = set()
        for index, item in enumerate(plan["changes"]):
            if not isinstance(item, dict) or item.get("type") not in ("movie", "episode"):
                raise TransactionError("plan item {} has an invalid media type".format(index))
            kodi_id = item.get("kodi_id")
            if type(kodi_id) is not int or kodi_id < 1:
                raise TransactionError("plan item {} has an invalid local Kodi ID".format(index))
            key = (item["type"], kodi_id)
            if key in seen:
                raise TransactionError("plan contains duplicate local Kodi target: {}".format(key))
            seen.add(key)
            before, changes = item.get("before"), item.get("changes")
            if not isinstance(before, dict) or not isinstance(changes, dict) or not changes:
                raise TransactionError("plan item {} must include before-state and non-empty changes".format(index))
            identity = item.get("identity")
            if not isinstance(identity, list) or not identity:
                raise TransactionError("plan item {} must include a strong identity".format(index))
            if not is_valid_serialized_identity(identity):
                raise TransactionError("plan item {} has an invalid strong identity".format(index))
            if not set(changes).issubset({"playcount", "lastplayed", "resume", "userrating"}):
                raise TransactionError("plan item {} contains unsupported fields".format(index))
            if not set(changes).issubset(before):
                raise TransactionError("plan item {} lacks before-values for changed fields".format(index))
            after = deepcopy(before)
            after.update(deepcopy(changes))
            normalized.append({
                "type": item["type"],
                "kodi_id": kodi_id,
                "identity": deepcopy(item.get("identity") or []),
                "before": deepcopy(before),
                "after": after,
                "changes": deepcopy(changes),
                "status": "pending",
            })
        return normalized

    def _get_details(self, item):
        if item["type"] == "movie":
            return self._kodi.get_movie_details(item["kodi_id"])
        return self._kodi.get_episode_details(item["kodi_id"])

    def _set_details(self, item, changes):
        if item["type"] == "movie":
            return self._kodi.set_movie_details(item["kodi_id"], changes)
        return self._kodi.set_episode_details(item["kodi_id"], changes)

    def _verify(self, item):
        details = self._get_details(item)
        if not isinstance(details, dict):
            raise TransactionError("Kodi returned invalid details during verification")
        if not identity_matches_kodi_item(item["identity"], details):
            raise TransactionError(
                "Kodi item identity changed during transaction for {} {}; recovery is required"
                .format(item["type"], item["kodi_id"])
            )
        for field, expected in item["changes"].items():
            if not _field_matches(details, expected, field):
                raise TransactionError(
                    "Kodi verification failed for {} {} field {}".format(item["type"], item["kodi_id"], field)
                )

    def run(self, plan, client_id, current_snapshot):
        """Apply and verify every change; unresolved failures retain the marker."""
        items = self._validate_plan(plan, client_id)
        if not items:
            return {"status": "no_changes", "items": [], "backup_ref": None}
        try:
            validate_snapshot(current_snapshot)
        except (ValueError, TypeError) as exc:
            raise TransactionError("current snapshot is invalid; refusing to mutate Kodi: {}".format(exc))
        if current_snapshot.get("client", {}).get("id") != client_id:
            raise TransactionError("backup snapshot belongs to a different client")
        if self._kodi.is_video_playing():
            return {"status": "deferred", "reason": "video_playing", "items": [], "backup_ref": None}

        prepared = {}

        def prepare_session(session_id):
            backup_ref = self._save_backup(current_snapshot, session_id)
            if not isinstance(backup_ref, str) or not backup_ref:
                raise TransactionError("backup callback did not return a durable reference")
            journal = {
                "format": "SudoSync v2 transaction journal",
                "schema_version": 1,
                "session_id": session_id,
                "client_id": client_id,
                "status": "prepared",
                "started_at": self._clock(),
                "finished_at": "",
                "backup_ref": backup_ref,
                "items": items,
                "error": "",
            }
            # The backup and prepared journal must be durable before the active
            # marker becomes visible. This closes the marker-without-journal
            # crash window. No Kodi library writes are allowed in this callback.
            self._persist_journal(deepcopy(journal))
            prepared["journal"] = journal

        try:
            session_id = self._coordinator.acquire(client_id, prepare=prepare_session)
        except Exception as exc:
            raise TransactionError("Cannot prepare or acquire exclusive synchronization session: {}".format(exc))

        journal = prepared.get("journal")
        if journal is None:
            # A coordinator that ignores the preparation callback cannot be
            # used safely by this transaction runner.
            raise TransactionError("Coordinator acquired a session without preparing its journal")

        # No Kodi library mutation has started. If this transition cannot be
        # persisted, release the marker only because the transaction is still
        # guaranteed to be pre-write.
        try:
            journal["status"] = "applying"
            self._persist_journal(deepcopy(journal))
        except Exception as exc:
            try:
                self._coordinator.release(session_id)
            except Exception as release_exc:
                raise TransactionError(
                    "Could not persist applying state and could not release pre-write session: {}".format(release_exc)
                )
            raise TransactionError("Could not persist applying state before any library write: {}".format(exc))

        mutation_started = False
        try:
            for item in journal["items"]:
                if self._kodi.is_video_playing():
                    raise TransactionError("video playback started during the transaction")

                # Plans may become stale while they wait for the shared session.
                # Re-read the target under the session marker and compare every
                # changed field with its recorded before-value before any write.
                actual_before = self._get_details(item)
                if not isinstance(actual_before, dict):
                    raise TransactionError("Kodi returned invalid details before mutation")
                if not identity_matches_kodi_item(item["identity"], actual_before):
                    raise TransactionError(
                        "Plan identity precondition failed for {} {}; refusing to write to a different library item"
                        .format(item["type"], item["kodi_id"])
                    )
                for field in item["changes"]:
                    expected_before = item["before"][field]
                    if not _field_matches(actual_before, expected_before, field):
                        raise TransactionError(
                            "Plan precondition failed for {} {} field {}; refusing stale write"
                            .format(item["type"], item["kodi_id"], field)
                        )

                # Persist intent before each mutation so recovery can distinguish
                # untouched work from a write whose outcome may be uncertain.
                item["status"] = "applying"
                item["started_at"] = self._clock()
                self._persist_journal(deepcopy(journal))
                # From this point a failed RPC may have partially changed Kodi;
                # never clear the marker automatically on error.
                mutation_started = True
                self._set_details(item, item["changes"])
                self._verify(item)
                item["status"] = "verified"
                item["verified_at"] = self._clock()
                self._persist_journal(deepcopy(journal))
        except Exception as exc:
            journal["error"] = str(exc)
            journal["finished_at"] = self._clock()
            if not mutation_started:
                # No library mutation has been attempted in this session. It is
                # safe to abort and release the marker, even if the plan was stale.
                journal["status"] = "aborted"
                try:
                    self._persist_journal(deepcopy(journal))
                except Exception:
                    pass
                try:
                    self._coordinator.release(session_id)
                except Exception as release_exc:
                    raise TransactionError(
                        "Transaction failed before any library write and the session marker remains: {}"
                        .format(release_exc)
                    )
                if isinstance(exc, TransactionError):
                    raise
                raise TransactionError("Transaction aborted before any library write: {}".format(exc))

            journal["status"] = "needs_recovery"
            try:
                self._persist_journal(deepcopy(journal))
            except Exception:
                # Preserve the lock marker even if the journal store is down.
                pass
            if isinstance(exc, TransactionError):
                raise
            raise TransactionError("Transaction stopped after a possible partial write: {}".format(exc))

        journal["status"] = "committed"
        journal["finished_at"] = self._clock()
        try:
            self._persist_journal(deepcopy(journal))
        except Exception as exc:
            # Writes were verified, but without a durable commit record the
            # next run must not assume the operation is complete.
            raise TransactionError("Writes verified but commit journal failed; session remains locked: {}".format(exc))
        try:
            self._coordinator.release(session_id)
        except Exception as exc:
            raise TransactionError("Transaction committed but session marker remains: {}".format(exc))
        return deepcopy(journal)
