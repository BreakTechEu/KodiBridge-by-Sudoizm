# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Startup ordering for a read-only SudoSync v2 service cycle.

The runtime resolves any shared recovery marker before asking the sync engine
to read Kodi's library. It deliberately exposes preview only; mutation remains
owned by the separately guarded transaction runner.
"""
from __future__ import absolute_import

import os
import threading

from .catalog import SnapshotCatalog
from .engine import SyncEngine
from .native_shared_lock import NativeSharedFileLockFactory
from .planning import apply_plan_to_snapshot
from .recovery import RecoveryRunner
from .session import SharedOperationCoordinator
from .storage import SharedJsonRepository
from .transaction import TransactionRunner
from .transaction_store import TransactionStore
from .kodi_rpc import KodiJsonRpcAdapter, accepts_abort_requested, invoke_with_abort
from .kodi_vfs import KodiVFSMetadataReader


class V2RuntimeError(RuntimeError):
    """The v2 runtime could not safely enter its preview cycle."""


class ProcessLocalLockFactory(object):
    """Serialize shared-file access inside this Kodi service process.

    This is sufficient only for SudoSync's agreed operating rule: one Kodi
    service uses the shared directory at a time. It does not coordinate other
    processes or devices and must not be used if concurrent Kodi clients are
    introduced.
    """

    def __init__(self):
        self._lock = threading.RLock()

    def __call__(self, resource_path):
        if not isinstance(resource_path, str) or not resource_path.strip():
            raise ValueError("resource_path must be a non-empty string")
        return self._lock


class V2ServiceRuntime(object):
    """Recover first, then build a read-only preview when startup is safe.

    ``recovery_runner`` must implement ``recover_on_start(client_id)`` and
    ``sync_engine`` must implement the read-only ``preview()`` operation.
    A blocked or deferred recovery result prevents all subsequent library reads.
    """

    def __init__(self, client_id, recovery_runner, sync_engine, abort_requested=None):
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("client_id must be a non-empty string")
        if recovery_runner is None or not callable(getattr(recovery_runner, "recover_on_start", None)):
            raise ValueError("recovery_runner must provide recover_on_start")
        if sync_engine is None or not callable(getattr(sync_engine, "preview", None)):
            raise ValueError("sync_engine must provide preview")
        self._client_id = client_id
        self._recovery_runner = recovery_runner
        self._sync_engine = sync_engine
        self._abort_requested = abort_requested

    def start(self, abort_requested=None, suppress_local_changes=False):
        """Recover any owned session before allowing a preview cycle."""
        from .kodi_rpc import is_aborted
        abort = abort_requested if abort_requested is not None else self._abort_requested
        if is_aborted(abort):
            return {
                "status": "aborted",
                "recovery": {"status": "aborted"},
                "preview": {
                    "active": False,
                    "aborted": True,
                    "reason": "aborted",
                    "snapshot": None,
                    "plan": None,
                    "skipped_nfos": [],
                    "skipped_nfo_count": 0,
                },
            }

        recovery = self._recovery_runner.recover_on_start(self._client_id)
        if not isinstance(recovery, dict) or not isinstance(recovery.get("status"), str):
            raise V2RuntimeError("Recovery runner returned an invalid startup result")

        status = recovery["status"]
        if status in ("blocked", "deferred"):
            return {"status": status, "recovery": recovery, "preview": None}
        if status not in ("clear", "recovered"):
            raise V2RuntimeError("Recovery runner returned an unsupported startup status: " + status)

        kwargs = {}
        if suppress_local_changes:
            try:
                import inspect
                sig = inspect.signature(self._sync_engine.preview)
                if "suppress_local_changes" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                ):
                    kwargs["suppress_local_changes"] = True
            except (ValueError, TypeError):
                pass
        preview = invoke_with_abort(self._sync_engine.preview, abort, **kwargs)

        final_status = "aborted" if isinstance(preview, dict) and preview.get("aborted") else "ready"
        return {"status": final_status, "recovery": recovery, "preview": preview}


class V2RuntimeComponents(object):
    """Composed v2 repositories and runners for a Kodi service instance."""

    def __init__(self, service_runtime, catalog, transaction_store,
                 coordinator, transaction_runner, sync_engine=None,
                 client_id=None, max_records=100000):
        self.service_runtime = service_runtime
        self.catalog = catalog
        self.transaction_store = transaction_store
        self.coordinator = coordinator
        self.transaction_runner = transaction_runner
        self.sync_engine = sync_engine
        self.client_id = client_id
        self.max_records = max_records

    def start(self, abort_requested=None, suppress_local_changes=False):
        """Recover the owned session, then create a read-only startup preview."""
        return self.service_runtime.start(
            abort_requested=abort_requested,
            suppress_local_changes=suppress_local_changes,
        )

    def sync_cycle(self, abort_requested=None, suppress_local_changes=False, publish_local=True):
        """Execute one complete v2 sync cycle: recover -> preview -> transact -> publish."""
        startup = self.start(
            abort_requested=abort_requested,
            suppress_local_changes=suppress_local_changes,
        )
        if startup["status"] != "ready":
            return {
                "status": startup["status"],
                "recovery": startup.get("recovery"),
                "preview": startup.get("preview"),
                "transaction": None,
                "published_snapshot_path": None,
                "applied_count": 0,
            }

        preview = startup["preview"]
        if not preview or not preview.get("active"):
            return {
                "status": "deferred",
                "reason": (preview or {}).get("reason", "inactive"),
                "recovery": startup.get("recovery"),
                "preview": preview,
                "transaction": None,
                "published_snapshot_path": None,
                "applied_count": 0,
            }

        candidate = preview["snapshot"]
        plan = preview["plan"]
        changes = plan.get("changes") or []
        client_id = self.client_id or candidate["client"]["id"]

        if not changes:
            published_path = None
            if publish_local:
                previous = self.catalog.load(client_id, abort_requested=abort_requested)
                if (previous is None
                        or previous.get("movies") != candidate.get("movies")
                        or previous.get("episodes") != candidate.get("episodes")
                        or previous.get("client") != candidate.get("client")):
                    published_path = self.catalog.publish(candidate)
            return {
                "status": "no_changes",
                "recovery": startup.get("recovery"),
                "preview": preview,
                "transaction": {"status": "no_changes", "items": [], "backup_ref": None},
                "published_snapshot_path": published_path,
                "applied_count": 0,
            }

        tx_result = self.transaction_runner.run(plan, client_id, candidate)
        if tx_result.get("status") == "deferred":
            return {
                "status": "deferred",
                "reason": tx_result.get("reason", "deferred"),
                "recovery": startup.get("recovery"),
                "preview": preview,
                "transaction": tx_result,
                "published_snapshot_path": None,
                "applied_count": 0,
            }

        post_snapshot = apply_plan_to_snapshot(candidate, plan, max_records=self.max_records)
        published_path = self.catalog.publish(post_snapshot)
        return {
            "status": "committed",
            "recovery": startup.get("recovery"),
            "preview": preview,
            "transaction": tx_result,
            "published_snapshot_path": published_path,
            "applied_count": len(changes),
        }

    def publish_local_snapshot(self, abort_requested=None, suppress_local_changes=False):
        """Publish this Kodi client's snapshot without applying any remote changes."""
        if self.sync_engine is None:
            raise V2RuntimeError("sync_engine is required for publish_local_snapshot")
        client_id = self.client_id or self.service_runtime._client_id
        res = self.sync_engine.publish_local_snapshot(
            abort_requested=abort_requested,
            suppress_local_changes=suppress_local_changes,
        )
        if not res.get("active") or res.get("aborted") or res.get("snapshot") is None:
            return {
                "status": "aborted" if res.get("aborted") else "deferred",
                "snapshot": None,
                "published_snapshot_path": None,
                "skipped_nfos": res.get("skipped_nfos", []),
                "skipped_nfo_count": res.get("skipped_nfo_count", 0),
            }
        candidate = res["snapshot"]
        previous = self.catalog.load(client_id, abort_requested=abort_requested)
        published_path = None
        if (previous is None
                or previous.get("movies") != candidate.get("movies")
                or previous.get("episodes") != candidate.get("episodes")
                or previous.get("client") != candidate.get("client")):
            published_path = self.catalog.publish(candidate)
        return {
            "status": "published" if published_path else "unchanged",
            "snapshot": candidate,
            "published_snapshot_path": published_path,
            "skipped_nfos": res.get("skipped_nfos", []),
            "skipped_nfo_count": res.get("skipped_nfo_count", 0),
        }

    def initialization_preview(self, base_client_id, abort_requested=None):
        """Recover owned session, load base snapshot, and build a read-only initialization preview."""
        if self.sync_engine is None:
            raise V2RuntimeError("sync_engine is required for initialization_preview")
        client_id = self.client_id or self.service_runtime._client_id
        recovery = self.service_runtime._recovery_runner.recover_on_start(client_id)
        status = recovery.get("status") if isinstance(recovery, dict) else None
        if status in ("blocked", "deferred"):
            return {"status": status, "recovery": recovery, "preview": None}
        if status not in ("clear", "recovered"):
            raise V2RuntimeError("Recovery runner returned an unsupported startup status: " + str(status))

        base_snapshot = self.catalog.load(base_client_id, abort_requested=abort_requested)
        if base_snapshot is None:
            raise V2RuntimeError("Base client snapshot does not exist in catalog: " + str(base_client_id))

        preview = self.sync_engine.initialization_preview(base_snapshot, abort_requested=abort_requested)
        final_status = "aborted" if isinstance(preview, dict) and preview.get("aborted") else "ready"
        return {"status": final_status, "recovery": recovery, "preview": preview}

    def apply_initialization(self, base_client_id, abort_requested=None):
        """Apply an explicitly approved one-time initialization plan and publish resulting snapshot."""
        init_res = self.initialization_preview(base_client_id, abort_requested=abort_requested)
        if init_res["status"] != "ready":
            return {
                "status": init_res["status"],
                "recovery": init_res.get("recovery"),
                "preview": init_res.get("preview"),
                "transaction": None,
                "published_snapshot_path": None,
                "applied_count": 0,
            }
        preview = init_res["preview"]
        if not preview or not preview.get("active"):
            return {
                "status": "deferred",
                "reason": (preview or {}).get("reason", "inactive"),
                "recovery": init_res.get("recovery"),
                "preview": preview,
                "transaction": None,
                "published_snapshot_path": None,
                "applied_count": 0,
            }
        candidate = preview["snapshot"]
        plan = preview["plan"]
        if plan.get("conflict_count", 0) > 0:
            raise V2RuntimeError("Refusing initialization apply while unresolved conflicts exist")
        changes = plan.get("changes") or []
        client_id = self.client_id or candidate["client"]["id"]
        if not changes:
            published_path = self.catalog.publish(candidate)
            return {
                "status": "no_changes",
                "recovery": init_res.get("recovery"),
                "preview": preview,
                "transaction": {"status": "no_changes", "items": [], "backup_ref": None},
                "published_snapshot_path": published_path,
                "applied_count": 0,
            }
        tx_result = self.transaction_runner.run(plan, client_id, candidate)
        if tx_result.get("status") == "deferred":
            return {
                "status": "deferred",
                "reason": tx_result.get("reason", "deferred"),
                "recovery": init_res.get("recovery"),
                "preview": preview,
                "transaction": tx_result,
                "published_snapshot_path": None,
                "applied_count": 0,
            }
        post_snapshot = apply_plan_to_snapshot(candidate, plan, max_records=self.max_records)
        published_path = self.catalog.publish(post_snapshot)
        return {
            "status": "committed",
            "recovery": init_res.get("recovery"),
            "preview": preview,
            "transaction": tx_result,
            "published_snapshot_path": published_path,
            "applied_count": len(changes),
        }


def map_shared_resource_to_native(shared_base_path, native_shared_path, resource_path):
    """Map one Kodi VFS resource under the shared root to its native mount path."""
    if not isinstance(shared_base_path, str) or not shared_base_path.strip():
        raise ValueError("shared_base_path must be a non-empty VFS path")
    if not isinstance(native_shared_path, str) or not os.path.isabs(native_shared_path):
        raise ValueError("native_shared_path must be an absolute mounted path")
    if not isinstance(resource_path, str):
        raise ValueError("resource_path must be a string")

    shared_root = shared_base_path.rstrip("/")
    prefix = shared_root + "/"
    if not resource_path.startswith(prefix):
        raise ValueError("shared resource is outside the configured VFS root")
    relative = resource_path[len(prefix):]
    segments = relative.split("/")
    if not relative or any(
        segment in ("", ".", "..") or "\\" in segment or ":" in segment
        for segment in segments
    ):
        raise ValueError("shared resource contains an unsafe path segment")

    native_root = os.path.realpath(os.path.abspath(native_shared_path))
    native_path = os.path.realpath(os.path.abspath(os.path.join(native_root, *segments)))
    try:
        resolved_root = os.path.normcase(native_root)
        resolved_path = os.path.normcase(native_path)
        if os.path.commonpath((resolved_root, resolved_path)) != resolved_root:
            raise ValueError("mapped native resource is outside the configured mount root")
    except ValueError:
        raise ValueError("mapped native resource is outside the configured mount root")
    return native_path


def build_mapped_native_lock_factory(shared_base_path, native_shared_path,
                                     filesystem_verified=False, blocking=True,
                                     backend=None):
    """Create the shared lock provider only after explicit physical verification."""
    if filesystem_verified is not True:
        raise V2RuntimeError(
            "Shared filesystem locking must be physically verified before enabling v2"
        )
    native_factory = NativeSharedFileLockFactory(
        filesystem_verified=True,
        backend=backend,
        blocking=blocking,
    )

    def lock_factory(resource_path):
        native_path = map_shared_resource_to_native(
            shared_base_path, native_shared_path, resource_path
        )
        return native_factory(native_path)

    return lock_factory


def build_v2_runtime(kodi, shared_backend, shared_base_path, lock_factory,
                     client_id, client_name, max_records=100000,
                     abort_requested=None):
    """Wire v2 adapters and runners without enabling any library writes.

    ``lock_factory`` must serialize access according to the configured operating
    model. For the current single-active-Kodi model, ProcessLocalLockFactory is
    sufficient; concurrent Kodi clients require a verified shared lock.
    """
    if kodi is None or shared_backend is None or lock_factory is None:
        raise ValueError("Kodi adapter, shared backend, and lock factory are required")
    if not isinstance(shared_base_path, str) or not shared_base_path.strip():
        raise ValueError("shared_base_path must be a non-empty path")
    base_path = shared_base_path.rstrip("/")
    repository = SharedJsonRepository(shared_backend, lock_factory)
    catalog = SnapshotCatalog(repository, shared_backend, base_path, max_records=max_records)
    transaction_store = TransactionStore(repository, base_path, max_records=max_records)
    coordinator = SharedOperationCoordinator(
        shared_backend,
        lock_factory,
        base_path + "/transactions/active-operation.json",
    )
    recovery = RecoveryRunner(
        kodi,
        coordinator,
        transaction_store.load_journal,
        transaction_store.persist_journal,
    )
    engine = SyncEngine(
        kodi,
        client_id,
        client_name,
        lambda: catalog.load(client_id, abort_requested=abort_requested),
        lambda: catalog.load_peers(exclude_client_id=client_id, abort_requested=abort_requested),
        max_records=max_records,
        abort_requested=abort_requested,
    )
    service_runtime = V2ServiceRuntime(client_id, recovery, engine, abort_requested=abort_requested)
    transaction_runner = TransactionRunner(
        kodi,
        coordinator,
        transaction_store.save_backup,
        transaction_store.persist_journal,
    )
    return V2RuntimeComponents(
        service_runtime,
        catalog,
        transaction_store,
        coordinator,
        transaction_runner,
        sync_engine=engine,
        client_id=client_id,
        max_records=max_records,
    )


def build_kodi_v2_runtime(execute_jsonrpc, shared_backend, shared_base_path,
                          lock_factory, client_id, client_name, vfs_module=None,
                          max_records=100000, abort_requested=None):
    """Compose a Kodi runtime with JSON-RPC and NFO reads provided by Kodi VFS.

    A process-local lock follows the project's single-active-Kodi operating
    rule. Supply a verified shared lock only if concurrent Kodi clients are
    explicitly added to the supported operating model.
    """
    kodi = KodiJsonRpcAdapter(
        execute_jsonrpc,
        max_records=max_records,
        nfo_reader=KodiVFSMetadataReader(vfs_module),
        abort_requested=abort_requested,
    )
    return build_v2_runtime(
        kodi,
        shared_backend,
        shared_base_path,
        lock_factory,
        client_id,
        client_name,
        max_records=max_records,
        abort_requested=abort_requested,
    )
