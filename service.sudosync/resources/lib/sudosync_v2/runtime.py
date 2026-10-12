"""Startup ordering for a read-only SudoSync v2 service cycle.

The runtime resolves any shared recovery marker before asking the sync engine
to read Kodi's library. It deliberately exposes preview only; mutation remains
owned by the separately guarded transaction runner.
"""
from __future__ import absolute_import

import os

from .catalog import SnapshotCatalog
from .engine import SyncEngine
from .native_shared_lock import NativeSharedFileLockFactory
from .recovery import RecoveryRunner
from .session import SharedOperationCoordinator
from .storage import SharedJsonRepository
from .transaction import TransactionRunner
from .transaction_store import TransactionStore


class V2RuntimeError(RuntimeError):
    """The v2 runtime could not safely enter its preview cycle."""


class V2ServiceRuntime(object):
    """Recover first, then build a read-only preview when startup is safe.

    ``recovery_runner`` must implement ``recover_on_start(client_id)`` and
    ``sync_engine`` must implement the read-only ``preview()`` operation.
    A blocked or deferred recovery result prevents all subsequent library reads.
    """

    def __init__(self, client_id, recovery_runner, sync_engine):
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("client_id must be a non-empty string")
        if recovery_runner is None or not callable(getattr(recovery_runner, "recover_on_start", None)):
            raise ValueError("recovery_runner must provide recover_on_start")
        if sync_engine is None or not callable(getattr(sync_engine, "preview", None)):
            raise ValueError("sync_engine must provide preview")
        self._client_id = client_id
        self._recovery_runner = recovery_runner
        self._sync_engine = sync_engine

    def start(self):
        """Recover any owned session before allowing a preview cycle."""
        recovery = self._recovery_runner.recover_on_start(self._client_id)
        if not isinstance(recovery, dict) or not isinstance(recovery.get("status"), str):
            raise V2RuntimeError("Recovery runner returned an invalid startup result")

        status = recovery["status"]
        if status in ("blocked", "deferred"):
            return {"status": status, "recovery": recovery, "preview": None}
        if status not in ("clear", "recovered"):
            raise V2RuntimeError("Recovery runner returned an unsupported startup status: " + status)

        preview = self._sync_engine.preview()
        return {"status": "ready", "recovery": recovery, "preview": preview}


class V2RuntimeComponents(object):
    """Composed v2 repositories and runners for a Kodi service instance."""

    def __init__(self, service_runtime, catalog, transaction_store,
                 coordinator, transaction_runner):
        self.service_runtime = service_runtime
        self.catalog = catalog
        self.transaction_store = transaction_store
        self.coordinator = coordinator
        self.transaction_runner = transaction_runner

    def start(self):
        """Recover the owned session, then create a read-only startup preview."""
        return self.service_runtime.start()


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

    native_root = os.path.abspath(native_shared_path)
    native_path = os.path.abspath(os.path.join(native_root, *segments))
    try:
        if os.path.commonpath((native_root, native_path)) != native_root:
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
                     client_id, client_name, max_records=100000):
    """Wire v2 adapters and runners without enabling any library writes.

    ``lock_factory`` must provide cross-process exclusion verified on the exact
    target filesystem and platform. This builder does not certify that provider.
    """
    if kodi is None or shared_backend is None or lock_factory is None:
        raise ValueError("Kodi adapter, shared backend, and verified lock factory are required")
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
        lambda: catalog.load(client_id),
        lambda: catalog.load_peers(exclude_client_id=client_id),
        max_records=max_records,
    )
    service_runtime = V2ServiceRuntime(client_id, recovery, engine)
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
    )
