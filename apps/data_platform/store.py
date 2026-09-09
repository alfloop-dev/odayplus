from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import (
    ProjectionBatchResult,
    QuarantineReason,
    ReconciliationResult,
    SourceEnvelope,
    SourceKind,
)
from apps.data_platform.deletion import (
    DeleteEvent,
    DeleteOutcome,
    DeletePropagationMode,
    DeleteResult,
    DeleteScope,
    TenantResolution,
    TombstoneState,
    canonical_lock_key,
    decide_delete,
    envelope_version,
    plan_purge,
    resolve_delete_tenant,
    scope_lock_key,
    suppresses_upsert,
)
from apps.data_platform.identifiers import (
    brand_id_for_merchant,
    machine_id_for_device,
    store_id_for_place,
    tenant_id_for_merchant,
)
from apps.data_platform.mapping import (
    MappingLookup,
    MachineIdentity,
    MerchantIdentity,
    MissingMappingError,
    SourceContractError,
    StoreIdentity,
    project_merchant,
    project_device,
    project_daily_statistic,
    project_domain_input,
    project_forecast_input,
    project_learning_import,
    project_machine_status_event,
    project_place,
    project_transaction,
)
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.status_mapping import optional_status_contract


class CanonicalStore(Protocol):
    def install(self) -> None: ...

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
    ) -> None: ...

    def apply_batch(
        self,
        source_kind: SourceKind,
        envelopes: Sequence[SourceEnvelope],
        *,
        partition_key: str,
    ) -> ProjectionBatchResult: ...

    def delete_record(self, event: DeleteEvent) -> DeleteResult: ...

    def tombstone_record(self, event: DeleteEvent) -> DeleteResult: ...

    def get_tombstone(
        self,
        tenant_id: UUID,
        source_kind: SourceKind,
        source_id: str,
    ) -> TombstoneState | None: ...

    def get_checkpoint(self, source_kind: SourceKind, partition_key: str) -> str | None: ...

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None: ...

    def reconcile(
        self,
        run_id: str,
        source_kind: SourceKind,
        source_count: int,
        source_checksum: str,
        valid_checksum: str,
    ) -> ReconciliationResult: ...

    def complete_run(
        self,
        run_id: str,
        *,
        final_cursor: str | None,
        processed_count: int,
        reconciliation: ReconciliationResult,
        partition_complete: bool,
        finished_at: datetime,
    ) -> None: ...

    def fail_run(
        self,
        run_id: str,
        *,
        source_kind: SourceKind,
        partition_key: str,
        source_snapshot_ids: Sequence[str],
        error: BaseException,
        retryable: bool,
    ) -> None: ...


class _PostgresLookup(MappingLookup):
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._merchants: dict[str, MerchantIdentity] = {}
        self._places: dict[str, StoreIdentity] = {}
        self._devices: dict[str, MachineIdentity] = {}

    def require_merchant(self, source_merchant_id: str) -> MerchantIdentity:
        cached = self._merchants.get(source_merchant_id)
        if cached is not None:
            return cached
        tenant_id = tenant_id_for_merchant(source_merchant_id)
        brand_id = brand_id_for_merchant(source_merchant_id)
        row = self._connection.execute(
            """
            SELECT t.tenant_id, b.brand_id
            FROM core.tenants AS t
            JOIN core.brands AS b
              ON b.tenant_id = t.tenant_id
            WHERE t.tenant_id = %s AND b.brand_id = %s
            """,
            (tenant_id, brand_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_MERCHANT_MAPPING,
                f"Missing merchant mapping for source merchant {source_merchant_id}",
            )
        identity = MerchantIdentity(
            source_merchant_id,
            UUID(str(row[0])),
            UUID(str(row[1])),
        )
        self._merchants[source_merchant_id] = identity
        return identity

    def require_place(self, source_place_id: str) -> StoreIdentity:
        cached = self._places.get(source_place_id)
        if cached is not None:
            return cached
        store_id = store_id_for_place(source_place_id)
        row = self._connection.execute(
            """
            SELECT s.tenant_id, s.brand_id, b.brand_code
            FROM core.stores AS s
            JOIN core.brands AS b
              ON b.brand_id = s.brand_id AND b.tenant_id = s.tenant_id
            WHERE s.store_id = %s AND s.source_store_id = %s
            """,
            (store_id, source_place_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_PLACE_MAPPING,
                f"Missing place mapping for source place {source_place_id}",
            )
        brand_code = str(row[2])
        prefix = "fongniao_"
        if not brand_code.startswith(prefix):
            raise MissingMappingError(
                QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
                f"Place {source_place_id} is not owned by a fongniao merchant tenant",
            )
        identity = StoreIdentity(
            source_place_id=source_place_id,
            source_merchant_id=brand_code.removeprefix(prefix),
            tenant_id=UUID(str(row[0])),
            brand_id=UUID(str(row[1])),
            store_id=store_id,
        )
        self._places[source_place_id] = identity
        return identity

    def require_device(self, source_device_id: str) -> MachineIdentity:
        cached = self._devices.get(source_device_id)
        if cached is not None:
            return cached
        machine_id = machine_id_for_device(source_device_id)
        row = self._connection.execute(
            """
            SELECT s.tenant_id, m.store_id
            FROM core.machines AS m
            JOIN core.stores AS s ON s.store_id = m.store_id
            WHERE m.machine_id = %s AND m.source_machine_id = %s
            """,
            (machine_id, source_device_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_DEVICE_MAPPING,
                f"Missing device mapping for source device {source_device_id}",
            )
        identity = MachineIdentity(
            source_device_id=source_device_id,
            tenant_id=UUID(str(row[0])),
            store_id=UUID(str(row[1])),
            machine_id=machine_id,
        )
        self._devices[source_device_id] = identity
        return identity


#: How many times a delete may re-key its scope lock while binding an owning
#: tenant. One pass covers an event that declares its tenant; two cover one that
#: has to learn the owner from lineage first. The bound exists so a pathological
#: churn of owners cannot spin here, and the last read still decides.
_DELETE_SCOPE_LOCK_ATTEMPTS = 3


class PsycopgCanonicalStore:
    """Transactional canonical writer and lineage/checkpoint authority."""

    def __init__(
        self,
        config: DataPlaneConfig,
        *,
        connection_factory: Any | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._connect = connection_factory or self._default_connection_factory()
        self._status_contract = optional_status_contract(config.status_mapping_path)

    def _default_connection_factory(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for canonical persistence") from exc
        return lambda: psycopg.connect(self._config.postgres_dsn)

    @property
    def _schema(self) -> str:
        return self._config.control_schema

    def install(self) -> None:
        path = Path(__file__).with_name("sql") / "control_schema.sql"
        sql = path.read_text(encoding="utf-8").replace("{{control_schema}}", self._schema)
        with self._connect() as connection:
            connection.execute(sql)

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.ingestion_runs (
                    run_id, source_database, source_kind, partition_key, status,
                    resumed_from, started_at
                ) VALUES (%s, 'fongniao_prod', %s, %s, 'RUNNING', %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = 'RUNNING',
                    reconciled = FALSE,
                    partition_complete = FALSE,
                    error_type = NULL,
                    error_message = NULL
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (run_id, source_kind.value, partition_key, resumed_from, started_at),
            )

    def apply_batch(
        self,
        source_kind: SourceKind,
        envelopes: Sequence[SourceEnvelope],
        *,
        partition_key: str,
    ) -> ProjectionBatchResult:
        if not envelopes:
            return ProjectionBatchResult((), {})
        valid: list[str] = []
        reason_counts: dict[str, int] = {}
        with self._connect() as connection:
            lookup = _PostgresLookup(connection)
            for envelope in envelopes:
                try:
                    with connection.transaction():
                        self._project_one(connection, lookup, source_kind, envelope)
                        self._resolve_quarantine(connection, envelope)
                    valid.append(f"{envelope.source_snapshot_id}:{envelope.content_sha256}")
                except SourceContractError as exc:
                    reason = exc.reason_code.value
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                    with connection.transaction():
                        self._quarantine(
                            connection,
                            envelope,
                            partition_key=partition_key,
                            reason_code=reason,
                            reason_detail=str(exc),
                        )
        return ProjectionBatchResult(tuple(valid), reason_counts)

    def _lock_keys(self, connection: Any, keys: Iterable[int]) -> None:
        """Acquire transaction-scoped advisory locks in sorted order to prevent deadlocks."""
        for key in sorted(set(keys)):
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    def _lock_delete_scope(
        self,
        connection: Any,
        tenant_id: UUID,
        source_kind: SourceKind,
        source_id: str,
        canonical_targets: Sequence[tuple[str, UUID | str]] = (),
    ) -> None:
        """Enter the database-level coordination for one delete scope and its canonical targets.

        Both the delete path and the projection guard take these locks before they
        read, so neither can decide on a state the other commits away a moment
        later. It is transaction scoped, so it is held for the rest of the
        caller's transaction and released by its commit or rollback -- a caller
        cannot leak it, and cannot drop it while its own writes are still
        pending.

        Lock hierarchy invariant:
        Level 1: Scope advisory lock (scope_lock_key)
        Level 2: Canonical target advisory locks (canonical_lock_key, sorted)
        All paths strictly acquire Level 1 locks before Level 2 locks.
        """
        self._lock_keys(connection, [scope_lock_key(tenant_id, source_kind, source_id)])
        if canonical_targets:
            target_keys = [
                canonical_lock_key(tenant_id, table, target_id)
                for table, target_id in canonical_targets
            ]
            self._lock_keys(connection, target_keys)

    def _guard_deleted(
        self,
        connection: Any,
        tenant_id: UUID,
        source_kind: SourceKind,
        envelope: SourceEnvelope,
        canonical_targets: Sequence[tuple[str, UUID | str]] = (),
    ) -> None:
        """Refuse an upsert that would resurrect an already deleted entity for this tenant.

        An envelope with no ``source_updated_at`` has no orderable version, so
        it is treated as older than the tombstone rather than allowed through.

        The tombstone read alone cannot decide this: a delete committing on
        another connection just after the read would leave this upsert free to
        resurrect the entity. Taking the scope lock first makes the read and the
        upsert that follows it one indivisible step against that delete -- and
        because the lock outlives this method, a delete that loses the race
        still sees this upsert's rows and its lineage version when it runs.
        """
        self._lock_delete_scope(
            connection,
            tenant_id,
            source_kind,
            envelope.source_id,
            canonical_targets=canonical_targets,
        )
        row = connection.execute(
            f"""
            SELECT source_version
            FROM {self._schema}.tombstones
            WHERE tenant_id = %s AND entity_type = %s AND entity_id = %s
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (tenant_id, source_kind.value, envelope.source_id),
        ).fetchone()
        if row is None:
            return
        recorded = int(row[-1])
        if not suppresses_upsert(recorded, envelope_version(envelope)):
            return
        candidate = envelope_version(envelope)
        raise SourceContractError(
            QuarantineReason.SOURCE_DELETED,
            f"{envelope.source_kind.value}:{envelope.source_id} was deleted upstream at "
            f"version {recorded}; this record's version "
            f"({'unknown' if candidate is None else candidate}) cannot resurrect it",
        )

    def delete_record(self, event: DeleteEvent) -> DeleteResult:
        """Propagate an upstream delete into the tenant-scoped sink rows."""
        return self._propagate_delete(event, DeletePropagationMode.SINK_DELETE)

    def tombstone_record(self, event: DeleteEvent) -> DeleteResult:
        """Record purge evidence and block resurrection without removing rows."""
        return self._propagate_delete(event, DeletePropagationMode.TOMBSTONE_PURGE)

    def get_tombstone(
        self,
        tenant_id: UUID,
        source_kind: SourceKind,
        source_id: str,
    ) -> TombstoneState | None:
        """Read one tombstone back for audit, after a restart or otherwise."""
        with self._connect() as connection:
            return self._read_tombstone(
                connection,
                tenant_id,
                DeleteScope(source_kind, source_id, tenant_id),
            )

    def _read_lineage(self, connection: Any, scope: DeleteScope) -> list[tuple[Any, ...]]:
        """Read every tenant, purge target and applied version for one identity."""
        return connection.execute(
            f"""
            SELECT DISTINCT tenant_id, canonical_table, canonical_id, source_version
            FROM {self._schema}.canonical_lineage
            WHERE source_kind = %s AND source_id = %s
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (scope.source_kind.value, scope.source_id),
        ).fetchall()

    def _read_tombstone_tenants(self, connection: Any, scope: DeleteScope) -> list[UUID]:
        """Read any tenant that has already recorded a tombstone for this identity."""
        rows = connection.execute(
            f"""
            SELECT DISTINCT tenant_id
            FROM {self._schema}.tombstones
            WHERE entity_type = %s AND entity_id = %s
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (scope.source_kind.value, scope.source_id),
        ).fetchall()
        return [UUID(str(r[0])) for r in rows]

    def _enter_delete_scope(
        self,
        connection: Any,
        scope: DeleteScope,
    ) -> tuple[list[tuple[Any, ...]], TenantResolution]:
        """Bind the delete to one tenant and return its lineage read under the lock.

        Which tenant owns a source identity is itself a lineage fact, so when the
        event does not declare one the first read has to happen before the scope
        lock can be keyed. That read only chooses the lock; it never feeds the
        decision. Once a tenant is known its lock is taken and the lineage is
        read again, so the owner, the purge targets and the currently applied
        version the caller decides on all come from inside the coordination.

        Lock hierarchy invariant:
        Level 1: Scope advisory lock (scope_lock_key)
        Level 2: Canonical target advisory locks (canonical_lock_key, sorted)
        All paths (upsert and delete, declared and inferred tenant) strictly
        acquire Level 1 locks before Level 2 locks to prevent deadlocks.
        """
        locked_tenants: set[UUID] = set()
        locked_targets: set[tuple[str, str]] = set()
        lineage: list[tuple[Any, ...]] = []
        resolution = TenantResolution(
            None,
            DeleteOutcome.REJECTED_UNRESOLVED_TENANT,
            "no tenant declared and nothing landed downstream for this identity",
        )
        for _ in range(_DELETE_SCOPE_LOCK_ATTEMPTS):
            # Level 1: Scope lock for declared tenant
            if scope.tenant_id is not None and scope.tenant_id not in locked_tenants:
                self._lock_keys(
                    connection,
                    [scope_lock_key(scope.tenant_id, scope.source_kind, scope.source_id)],
                )
                locked_tenants.add(scope.tenant_id)

            # Read lineage and tombstones under held scope lock (or initial discovery)
            lineage = self._read_lineage(connection, scope)
            tombstone_tenants = self._read_tombstone_tenants(connection, scope)
            all_owners = [UUID(str(row[0])) for row in lineage] + tombstone_tenants
            resolution = resolve_delete_tenant(scope.tenant_id, all_owners)
            candidate = resolution.tenant_id if resolution.resolved else None

            # Level 1: Scope lock for inferred candidate tenant
            if candidate is not None and candidate not in locked_tenants:
                self._lock_keys(
                    connection,
                    [scope_lock_key(candidate, scope.source_kind, scope.source_id)],
                )
                locked_tenants.add(candidate)
                # Re-read lineage under candidate's scope lock
                lineage = self._read_lineage(connection, scope)
                tombstone_tenants = self._read_tombstone_tenants(connection, scope)
                all_owners = [UUID(str(row[0])) for row in lineage] + tombstone_tenants
                resolution = resolve_delete_tenant(scope.tenant_id, all_owners)
                candidate = resolution.tenant_id if resolution.resolved else None

            # Level 2: Canonical target locks for the candidate tenant
            new_target_keys: list[int] = []
            if candidate is not None:
                for row in lineage:
                    if UUID(str(row[0])) == candidate:
                        canonical_table = str(row[1])
                        canonical_id = str(row[2])
                        pair = (canonical_table, canonical_id)
                        if pair not in locked_targets:
                            new_target_keys.append(
                                canonical_lock_key(candidate, canonical_table, canonical_id)
                            )
                            locked_targets.add(pair)

            if new_target_keys:
                self._lock_keys(connection, new_target_keys)
                # Re-read lineage under full lock coverage
                lineage = self._read_lineage(connection, scope)
            else:
                break

        return lineage, resolution

    def _propagate_delete(
        self,
        event: DeleteEvent,
        mode: DeletePropagationMode,
    ) -> DeleteResult:
        scope = event.scope
        with self._connect() as connection:
            with connection.transaction():
                lineage, resolution = self._enter_delete_scope(connection, scope)
                if resolution.resolved and not self._tenant_exists(
                    connection, resolution.tenant_id
                ):
                    resolution = TenantResolution(
                        None,
                        DeleteOutcome.REJECTED_UNRESOLVED_TENANT,
                        f"tenant {scope.tenant_id} is not a known tenant",
                    )
                if not resolution.resolved:
                    return DeleteResult(
                        resolution.outcome or DeleteOutcome.REJECTED_UNRESOLVED_TENANT,
                        scope,
                        mode,
                        resolution.detail,
                    )
                tenant_id = resolution.tenant_id
                assert tenant_id is not None  # nosec B101 -- narrowed by resolved
                targets = tuple(
                    (str(row[1]), row[2])
                    for row in lineage
                    if UUID(str(row[0])) == tenant_id
                )
                applied_versions = [
                    int(row[3])
                    for row in lineage
                    if UUID(str(row[0])) == tenant_id and len(row) > 3 and row[3] is not None
                ]
                latest_applied_version = max(applied_versions) if applied_versions else None
                recorded = self._read_tombstone(
                    connection, tenant_id, scope, for_update=True
                )
                recorded_version = None if recorded is None else recorded.source_version

                effective_recorded_version = recorded_version
                if latest_applied_version is not None:
                    if effective_recorded_version is None or latest_applied_version > effective_recorded_version:
                        if event.source_version is not None and event.source_version < latest_applied_version:
                            effective_recorded_version = latest_applied_version

                decision = decide_delete(
                    requested_version=event.source_version,
                    recorded_version=effective_recorded_version,
                    downstream_target_count=len(targets),
                )
                if not decision.records_tombstone:
                    return self._unchanged_result(
                        decision.outcome, scope, mode, decision.detail, tenant_id, recorded
                    )
                purged = 0
                if mode is DeletePropagationMode.SINK_DELETE and decision.purges_rows:
                    plan = plan_purge(
                        targets,
                        tenant_id=tenant_id,
                        control_schema=self._schema,
                        source_kind=scope.source_kind,
                        source_id=scope.source_id,
                        source_version=event.source_version,
                    )
                    for statement, params in plan.statements:
                        cursor = connection.execute(statement, params)
                        purged += max(int(getattr(cursor, "rowcount", 0) or 0), 0)

                    retained_set = set(plan.retained_targets)
                    for canonical_table, canonical_id in targets:
                        if canonical_table not in retained_set:
                            if canonical_table == "core.transactions":
                                exists = connection.execute(
                                    "SELECT 1 FROM core.transactions WHERE transaction_id = %s",
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                            elif canonical_table == "core.machine_status_events":
                                exists = connection.execute(
                                    "SELECT 1 FROM core.machine_status_events WHERE status_event_id = %s",
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                            elif canonical_table == f"{self._schema}.store_daily_facts":
                                exists = connection.execute(
                                    f"SELECT 1 FROM {self._schema}.store_daily_facts WHERE source_snapshot_id = %s",  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                            elif canonical_table == f"{self._schema}.forecast_inputs":
                                exists = connection.execute(
                                    f"SELECT 1 FROM {self._schema}.forecast_inputs WHERE source_snapshot_id = %s",  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                            elif canonical_table == f"{self._schema}.learning_import_lineage":
                                exists = connection.execute(
                                    f"SELECT 1 FROM {self._schema}.learning_import_lineage WHERE source_snapshot_id = %s",  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                            elif canonical_table == f"{self._schema}.domain_inputs":
                                exists = connection.execute(
                                    f"SELECT 1 FROM {self._schema}.domain_inputs WHERE source_snapshot_id = %s",  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                                    (canonical_id,),
                                ).fetchone()
                                if exists is not None:
                                    retained_set.add(canonical_table)
                    retained = tuple(sorted(retained_set))

                    connection.execute(
                        f"""
                        DELETE FROM {self._schema}.canonical_lineage
                        WHERE tenant_id = %s AND source_kind = %s AND source_id = %s
                          AND (source_version IS NULL OR source_version <= %s)
                        """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                        (tenant_id, scope.source_kind.value, scope.source_id, event.source_version),
                    )
                else:
                    retained = tuple(sorted({table for table, _ in targets}))

                if recorded is not None and recorded.retained_targets:
                    retained = tuple(sorted(set(retained) | set(recorded.retained_targets)))

                row = self._upsert_tombstone(
                    connection, event, tenant_id, mode, purged, retained
                )
                if row is None:
                    # The database-level version guard refused the write, which
                    # only happens when a concurrent writer recorded a newer
                    # delete between the read and the upsert.
                    return self._unchanged_result(
                        DeleteOutcome.STALE_IGNORED,
                        scope,
                        mode,
                        "a concurrent delete recorded a newer version first",
                        tenant_id,
                        self._read_tombstone(connection, tenant_id, scope),
                    )
                return DeleteResult(
                    decision.outcome,
                    scope,
                    mode,
                    decision.detail,
                    tenant_id=tenant_id,
                    source_version=int(row[0]),
                    purged_row_count=int(row[2]),
                    retained_targets=tuple(row[3] or ()) if len(row) > 3 else retained,
                    replay_count=int(row[1]),
                )

    @staticmethod
    def _unchanged_result(
        outcome: DeleteOutcome,
        scope: DeleteScope,
        mode: DeletePropagationMode,
        detail: str,
        tenant_id: UUID | None,
        recorded: TombstoneState | None,
    ) -> DeleteResult:
        """Report a refused delete using the tombstone that stayed in place."""
        return DeleteResult(
            outcome,
            scope,
            mode,
            detail,
            tenant_id=tenant_id,
            source_version=None if recorded is None else recorded.source_version,
            purged_row_count=0 if recorded is None else recorded.purged_row_count,
            retained_targets=() if recorded is None else recorded.retained_targets,
            replay_count=0 if recorded is None else recorded.replay_count,
        )

    @staticmethod
    def _tenant_exists(connection: Any, tenant_id: UUID | None) -> bool:
        row = connection.execute(
            "SELECT 1 FROM core.tenants WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
        return row is not None

    def _read_tombstone(
        self,
        connection: Any,
        tenant_id: UUID,
        scope: DeleteScope,
        *,
        for_update: bool = False,
    ) -> TombstoneState | None:
        row = connection.execute(
            f"""
            SELECT source_version, purged_at, propagation_mode, tombstone_hash,
                   source_snapshot_id, run_id, purged_row_count, retained_targets,
                   replay_count
            FROM {self._schema}.tombstones
            WHERE tenant_id = %s AND entity_type = %s AND entity_id = %s
            {'FOR UPDATE' if for_update else ''}
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (tenant_id, scope.source_kind.value, scope.source_id),
        ).fetchone()
        if row is None:
            return None
        return TombstoneState(
            tenant_id=tenant_id,
            source_kind=scope.source_kind,
            source_id=scope.source_id,
            source_version=int(row[0]),
            purged_at=row[1],
            propagation_mode=DeletePropagationMode(str(row[2])),
            tombstone_hash=str(row[3]),
            source_snapshot_id=str(row[4]),
            run_id=str(row[5]),
            purged_row_count=int(row[6]),
            retained_targets=tuple(row[7] or ()),
            replay_count=int(row[8]),
        )

    def _upsert_tombstone(
        self,
        connection: Any,
        event: DeleteEvent,
        tenant_id: UUID,
        mode: DeletePropagationMode,
        purged_row_count: int,
        retained_targets: Sequence[str],
    ) -> tuple[Any, ...] | None:
        """Write the tombstone behind a database-level version guard.

        The ``WHERE EXCLUDED.source_version >= ...`` clause is what makes a late
        older delete a no-op even under concurrency: the guard lives in the same
        statement as the write, so no read-then-write window can regress it.
        """
        return connection.execute(
            f"""
            INSERT INTO {self._schema}.tombstones (
                tenant_id, entity_type, entity_id, source_version, purged_at,
                propagation_mode, tombstone_hash, source_snapshot_id, run_id,
                purged_row_count, retained_targets, context
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (tenant_id, entity_type, entity_id) DO UPDATE SET
                source_version = EXCLUDED.source_version,
                purged_at = EXCLUDED.purged_at,
                propagation_mode = EXCLUDED.propagation_mode,
                tombstone_hash = EXCLUDED.tombstone_hash,
                source_snapshot_id = EXCLUDED.source_snapshot_id,
                run_id = EXCLUDED.run_id,
                purged_row_count = {self._schema}.tombstones.purged_row_count
                    + EXCLUDED.purged_row_count,
                retained_targets = CASE
                    WHEN cardinality(EXCLUDED.retained_targets) > 0 THEN EXCLUDED.retained_targets
                    ELSE {self._schema}.tombstones.retained_targets
                END,
                context = EXCLUDED.context,
                replay_count = {self._schema}.tombstones.replay_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE EXCLUDED.source_version >= {self._schema}.tombstones.source_version
            RETURNING source_version, replay_count, purged_row_count, retained_targets
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                tenant_id,
                event.scope.source_kind.value,
                event.scope.source_id,
                event.source_version,
                event.purged_at,
                mode.value,
                event.tombstone_hash,
                event.source_snapshot_id,
                event.run_id,
                purged_row_count,
                list(retained_targets),
                json.dumps(event.context, sort_keys=True, default=str),
            ),
        ).fetchone()

    def _project_one(
        self,
        connection: Any,
        lookup: _PostgresLookup,
        source_kind: SourceKind,
        envelope: SourceEnvelope,
    ) -> None:
        if source_kind is SourceKind.MERCHANT:
            projection = project_merchant(envelope, self._status_contract)
            self._guard_deleted(
                connection,
                projection.tenant_id,
                source_kind,
                envelope,
                canonical_targets=[
                    ("core.tenants", projection.tenant_id),
                    ("core.brands", projection.brand_id),
                ],
            )
            self._upsert_merchant(connection, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.tenants",
                projection.tenant_id,
            )
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.brands",
                projection.brand_id,
            )
        elif source_kind is SourceKind.PLACE:
            projection = project_place(envelope, lookup, self._status_contract)
            self._guard_deleted(
                connection,
                projection.tenant_id,
                source_kind,
                envelope,
                canonical_targets=[("core.stores", projection.store_id)],
            )
            self._upsert_place(connection, projection)
            self._upsert_place_geography(connection, envelope, projection)
            if projection.address_id is not None:
                self._lineage(
                    connection,
                    envelope,
                    projection.tenant_id,
                    "core.address_locations",
                    projection.address_id,
                )
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.stores",
                projection.store_id,
            )
        elif source_kind is SourceKind.DEVICE:
            projection = project_device(envelope, lookup)
            self._guard_deleted(
                connection,
                projection.tenant_id,
                source_kind,
                envelope,
                canonical_targets=[("core.machines", projection.machine_id)],
            )
            self._upsert_device(connection, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.machines",
                projection.machine_id,
            )
        elif source_kind in {
            SourceKind.ORDERS,
            SourceKind.TRANSACTION,
            SourceKind.TRADE,
        }:
            projection = project_transaction(envelope, lookup, self._status_contract)
            self._guard_deleted(
                connection,
                projection.tenant_id,
                source_kind,
                envelope,
                canonical_targets=[("core.transactions", projection.transaction_id)],
            )
            self._upsert_transaction(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.transactions",
                projection.transaction_id,
            )
        elif source_kind is SourceKind.DEVICE_DAILY_STATISTICS:
            projection = project_daily_statistic(envelope, lookup)
            self._guard_deleted(connection, projection.tenant_id, source_kind, envelope)
            self._upsert_daily_statistic(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.store_daily_facts",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.AI_REVENUE_STATS:
            projection = project_forecast_input(envelope, lookup)
            self._guard_deleted(connection, projection.tenant_id, source_kind, envelope)
            self._upsert_forecast_input(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.forecast_inputs",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.AI_CONSUMER_KMEANS_V1:
            projection = project_learning_import(envelope, lookup)
            self._guard_deleted(connection, projection.tenant_id, source_kind, envelope)
            self._upsert_learning_import(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.learning_import_lineage",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.DEVICE_LOG:
            projection = project_machine_status_event(envelope, lookup, self._status_contract)
            self._guard_deleted(
                connection,
                projection.tenant_id,
                source_kind,
                envelope,
                canonical_targets=[("core.machine_status_events", projection.status_event_id)],
            )
            self._upsert_machine_status_event(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.machine_status_events",
                projection.status_event_id,
            )
        elif source_kind in {
            SourceKind.CAMPAIGN,
            SourceKind.PRODUCT,
            SourceKind.PRODUCTS,
            SourceKind.PROMOTIONS,
        }:
            projection = project_domain_input(envelope, lookup)
            self._guard_deleted(connection, projection.tenant_id, source_kind, envelope)
            self._upsert_domain_input(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.domain_inputs",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.MEMBER:
            raise SourceContractError(
                QuarantineReason.SENSITIVE_MEMBER_EXCLUDED,
                "Member records are raw-minimized and excluded from canonical projection",
            )
        else:  # pragma: no cover - exhaustive StrEnum guard
            raise ValueError(f"Unsupported source kind: {source_kind}")

    def _quarantine(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        *,
        partition_key: str,
        reason_code: str,
        reason_detail: str,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.quarantined_records (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, partition_key, reason_code, reason_detail, retryable
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                source_kind = EXCLUDED.source_kind,
                source_id = EXCLUDED.source_id,
                content_sha256 = EXCLUDED.content_sha256,
                partition_key = EXCLUDED.partition_key,
                reason_code = EXCLUDED.reason_code,
                reason_detail = EXCLUDED.reason_detail,
                quarantined_at = CURRENT_TIMESTAMP,
                resolved_at = NULL
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                envelope.source_kind.value,
                envelope.source_id,
                envelope.content_sha256,
                envelope.run_id,
                partition_key,
                reason_code,
                reason_detail,
            ),
        )

    def _resolve_quarantine(self, connection: Any, envelope: SourceEnvelope) -> None:
        connection.execute(
            f"""
            UPDATE {self._schema}.quarantined_records
            SET resolved_at = CURRENT_TIMESTAMP
            WHERE source_snapshot_id = %s AND resolved_at IS NULL
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (envelope.source_snapshot_id,),
        )

    @staticmethod
    def _upsert_merchant(connection: Any, projection: Any) -> None:
        connection.execute(
            """
            INSERT INTO core.tenants (
                tenant_id, tenant_name, status, created_at, updated_at
            ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO UPDATE SET
                tenant_name = EXCLUDED.tenant_name,
                status = EXCLUDED.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (projection.tenant_id, projection.tenant_name, projection.tenant_status),
        )
        connection.execute(
            """
            INSERT INTO core.brands (
                brand_id, tenant_id, brand_code, brand_name, brand_type,
                brand_capture_group, status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, 'owned', 'fongniao_prod',
                %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (brand_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                brand_code = EXCLUDED.brand_code,
                brand_name = EXCLUDED.brand_name,
                brand_type = 'owned',
                brand_capture_group = 'fongniao_prod',
                status = EXCLUDED.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.brand_id,
                projection.tenant_id,
                projection.brand_code,
                projection.brand_name,
                projection.brand_status,
            ),
        )

    @staticmethod
    def _upsert_place(connection: Any, projection: Any) -> None:
        if projection.address_id is not None:
            connection.execute(
                """
                INSERT INTO core.address_locations (
                    address_id, raw_address, normalized_address, city, district,
                    latitude, longitude, geom, geocode_precision,
                    geocode_confidence, h3_res_8, h3_res_9, h3_res_10,
                    manual_override_flag, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    CASE
                        WHEN %s::double precision IS NULL
                          OR %s::double precision IS NULL THEN NULL
                        ELSE ST_SetSRID(
                            ST_MakePoint(
                                %s::double precision,
                                %s::double precision
                            ),
                            4326
                        )
                    END,
                    'source',
                    CASE
                        WHEN %s::double precision IS NULL
                          OR %s::double precision IS NULL THEN NULL
                        ELSE 1.00
                    END,
                    %s, %s, %s, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (address_id) DO UPDATE SET
                    raw_address = EXCLUDED.raw_address,
                    normalized_address = EXCLUDED.normalized_address,
                    city = EXCLUDED.city,
                    district = EXCLUDED.district,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    geom = EXCLUDED.geom,
                    geocode_precision = 'source',
                    geocode_confidence = EXCLUDED.geocode_confidence,
                    h3_res_8 = EXCLUDED.h3_res_8,
                    h3_res_9 = EXCLUDED.h3_res_9,
                    h3_res_10 = EXCLUDED.h3_res_10,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    projection.address_id,
                    projection.raw_address,
                    projection.normalized_address,
                    projection.city,
                    projection.district,
                    projection.latitude,
                    projection.longitude,
                    projection.longitude,
                    projection.latitude,
                    projection.longitude,
                    projection.latitude,
                    projection.longitude,
                    projection.latitude,
                    projection.h3_res_8,
                    projection.h3_res_9,
                    projection.h3_res_10,
                ),
            )
        connection.execute(
            """
            INSERT INTO core.stores (
                store_id, tenant_id, brand_id, source_store_id, store_name,
                store_status, ownership_type, store_format_code, address_id,
                effective_from, effective_to, is_current, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'owned', %s, %s, %s,
                '9999-12-31 23:59:59+00', TRUE,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (store_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                brand_id = EXCLUDED.brand_id,
                source_store_id = EXCLUDED.source_store_id,
                store_name = EXCLUDED.store_name,
                store_status = EXCLUDED.store_status,
                ownership_type = 'owned',
                store_format_code = EXCLUDED.store_format_code,
                address_id = EXCLUDED.address_id,
                is_current = TRUE,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.store_id,
                projection.tenant_id,
                projection.brand_id,
                projection.source_id,
                projection.store_name,
                projection.store_status,
                projection.store_format_code,
                projection.address_id,
                projection.effective_from,
            ),
        )

    def _upsert_place_geography(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.place_geography (
                source_snapshot_id, source_id, tenant_id, store_id,
                raw_address, normalized_address, city, district,
                latitude, longitude, geocode_confidence,
                h3_res_8, h3_res_9, h3_res_10, h3_derivation_version,
                run_id, valid_from, observed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (source_snapshot_id) DO NOTHING
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.raw_address,
                projection.normalized_address,
                getattr(projection, "city", None),
                getattr(projection, "district", None),
                projection.latitude,
                projection.longitude,
                1.0 if projection.latitude is not None else None,
                projection.h3_res_8,
                projection.h3_res_9,
                projection.h3_res_10,
                ("stable_h3_index-v1" if projection.h3_res_9 is not None else None),
                envelope.run_id,
                getattr(projection, "effective_from", envelope.observed_at),
                envelope.observed_at,
            ),
        )

    def _upsert_transaction(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        projection: Any,
    ) -> None:
        authority_rank = {
            SourceKind.ORDERS: 1,
            SourceKind.TRANSACTION: 2,
            SourceKind.TRADE: 3,
        }[envelope.source_kind]
        authority = connection.execute(
            f"""
            WITH ensured_transaction AS (
                INSERT INTO core.transactions (
                    transaction_id, source_transaction_id, store_id, machine_id,
                    member_id, event_time, observation_time, payment_time,
                    gross_amount, discount_amount, net_amount, currency,
                    payment_method, transaction_status, refund_of_transaction_id,
                    price_schedule_id, promotion_id, source_system, ingested_at
                ) VALUES (
                    %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, NULL, NULL, NULL, 'fongniao_prod', %s
                )
                ON CONFLICT (transaction_id) DO NOTHING
                RETURNING transaction_id
            )
            INSERT INTO {self._schema}.transaction_authority (
                transaction_id, source_kind, authority_rank, source_snapshot_id
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (transaction_id) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                authority_rank = EXCLUDED.authority_rank,
                source_snapshot_id = EXCLUDED.source_snapshot_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE EXCLUDED.authority_rank <=
                  {self._schema}.transaction_authority.authority_rank
            RETURNING source_kind, authority_rank
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                projection.transaction_id,
                projection.source_id,
                projection.store_id,
                projection.event_time,
                projection.observation_time,
                projection.payment_time,
                projection.gross_amount,
                projection.discount_amount,
                projection.net_amount,
                projection.currency,
                projection.payment_method,
                projection.transaction_status,
                projection.ingested_at,
                projection.transaction_id,
                envelope.source_kind.value,
                authority_rank,
                envelope.source_snapshot_id,
            ),
        ).fetchone()
        if authority is None:
            authority = connection.execute(
                f"""
                SELECT source_kind, authority_rank
                FROM {self._schema}.transaction_authority
                WHERE transaction_id = %s
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (projection.transaction_id,),
            ).fetchone()
            raise SourceContractError(
                QuarantineReason.SOURCE_SUPERSEDED,
                (
                    f"{envelope.source_kind.value} transaction is superseded by "
                    f"authoritative {authority[0] if authority else 'unknown'}"
                ),
            )
        connection.execute(
            """
            INSERT INTO core.transactions (
                transaction_id, source_transaction_id, store_id, machine_id,
                member_id, event_time, observation_time, payment_time,
                gross_amount, discount_amount, net_amount, currency,
                payment_method, transaction_status, refund_of_transaction_id,
                price_schedule_id, promotion_id, source_system, ingested_at
            ) VALUES (
                %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, NULL, NULL, NULL, 'fongniao_prod', %s
            )
            ON CONFLICT (transaction_id) DO UPDATE SET
                source_transaction_id = EXCLUDED.source_transaction_id,
                store_id = EXCLUDED.store_id,
                event_time = EXCLUDED.event_time,
                observation_time = EXCLUDED.observation_time,
                payment_time = EXCLUDED.payment_time,
                gross_amount = EXCLUDED.gross_amount,
                discount_amount = EXCLUDED.discount_amount,
                net_amount = EXCLUDED.net_amount,
                currency = EXCLUDED.currency,
                payment_method = EXCLUDED.payment_method,
                transaction_status = EXCLUDED.transaction_status,
                source_system = 'fongniao_prod',
                ingested_at = EXCLUDED.ingested_at
            """,
            (
                projection.transaction_id,
                projection.source_id,
                projection.store_id,
                projection.event_time,
                projection.observation_time,
                projection.payment_time,
                projection.gross_amount,
                projection.discount_amount,
                projection.net_amount,
                projection.currency,
                projection.payment_method,
                projection.transaction_status,
                projection.ingested_at,
            ),
        )

    @staticmethod
    def _upsert_device(connection: Any, projection: Any) -> None:
        connection.execute(
            """
            INSERT INTO core.machines (
                machine_id, store_id, source_machine_id, machine_serial_no,
                equipment_brand_id, machine_family, machine_type, capacity_kg,
                capacity_band, installed_on, removed_on, machine_status,
                effective_from, effective_to, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'other', %s, NULL, 'medium',
                NULL, NULL, %s, %s, '9999-12-31 23:59:59+00',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (machine_id) DO UPDATE SET
                store_id = EXCLUDED.store_id,
                source_machine_id = EXCLUDED.source_machine_id,
                machine_serial_no = EXCLUDED.machine_serial_no,
                equipment_brand_id = EXCLUDED.equipment_brand_id,
                machine_type = EXCLUDED.machine_type,
                machine_status = EXCLUDED.machine_status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.machine_id,
                projection.store_id,
                projection.source_id,
                projection.serial_number,
                projection.product_id,
                projection.machine_type,
                projection.machine_status,
                projection.effective_from,
            ),
        )

    def _upsert_daily_statistic(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.store_daily_facts (
                source_snapshot_id, source_id, tenant_id, store_id, machine_id,
                period_start, period_end, gross_amount, transaction_count,
                gateway, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                gross_amount = EXCLUDED.gross_amount,
                transaction_count = EXCLUDED.transaction_count,
                gateway = EXCLUDED.gateway
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.machine_id,
                projection.period_start,
                projection.period_end,
                projection.gross_amount,
                projection.transaction_count,
                projection.gateway,
                envelope.run_id,
            ),
        )

    def _upsert_forecast_input(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.forecast_inputs (
                source_snapshot_id, source_id, tenant_id, store_id,
                forecast_date, predicted_value, output_class,
                source_model_version, source_model_run_id,
                source_freshness_at, observed_at, run_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                'legacy_external_model_output', NULL, NULL, %s, %s, %s
            )
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                predicted_value = EXCLUDED.predicted_value,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.forecast_date,
                projection.predicted_value,
                envelope.source_updated_at,
                projection.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_domain_input(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.domain_inputs (
                source_snapshot_id, source_id, input_kind, tenant_id,
                store_id, effective_at, input_payload, source_freshness_at,
                observed_at, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                tenant_id = EXCLUDED.tenant_id,
                store_id = EXCLUDED.store_id,
                effective_at = EXCLUDED.effective_at,
                input_payload = EXCLUDED.input_payload,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.input_kind,
                projection.tenant_id,
                projection.store_id,
                projection.effective_at,
                json.dumps(projection.input_payload, sort_keys=True),
                envelope.source_updated_at,
                envelope.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_learning_import(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.learning_import_lineage (
                source_snapshot_id, source_id, tenant_id, run_date,
                source_account_ref_hash, feature_snapshot, segment_id,
                segment_labels, output_class, source_model_version,
                source_model_run_id, source_freshness_at, observed_at, run_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                'legacy_external_model_output', NULL, NULL, %s, %s, %s
            )
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                feature_snapshot = EXCLUDED.feature_snapshot,
                segment_id = EXCLUDED.segment_id,
                segment_labels = EXCLUDED.segment_labels,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.run_date,
                projection.source_account_ref_hash,
                json.dumps(
                    {key: str(value) for key, value in projection.feature_snapshot.items()},
                    sort_keys=True,
                ),
                projection.segment_id,
                json.dumps(list(projection.segment_labels)),
                envelope.source_updated_at,
                projection.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_machine_status_event(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            """
            INSERT INTO core.machine_status_events (
                status_event_id, store_id, machine_id, event_time,
                status_type, severity, error_code, resolved_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (status_event_id) DO UPDATE SET
                store_id = EXCLUDED.store_id,
                machine_id = EXCLUDED.machine_id,
                event_time = EXCLUDED.event_time,
                status_type = EXCLUDED.status_type,
                severity = EXCLUDED.severity,
                error_code = EXCLUDED.error_code
            """,
            (
                projection.status_event_id,
                projection.store_id,
                projection.machine_id,
                projection.event_time,
                projection.status_type,
                projection.severity,
                projection.error_code,
            ),
        )
        connection.execute(
            f"""
            INSERT INTO {self._schema}.machine_status_event_evidence (
                source_snapshot_id, source_id, tenant_id, store_id,
                machine_id, status_event_id, content_sha256,
                observation_time, source_freshness_at, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                content_sha256 = EXCLUDED.content_sha256,
                observation_time = EXCLUDED.observation_time,
                source_freshness_at = EXCLUDED.source_freshness_at,
                run_id = EXCLUDED.run_id
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.machine_id,
                projection.status_event_id,
                envelope.content_sha256,
                projection.observation_time,
                envelope.source_updated_at,
                envelope.run_id,
            ),
        )

    def _lineage(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        tenant_id: UUID,
        canonical_table: str,
        canonical_id: UUID,
    ) -> None:
        version = envelope_version(envelope)
        connection.execute(
            f"""
            INSERT INTO {self._schema}.canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id, source_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id, canonical_table, canonical_id)
            DO NOTHING
            """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
            (
                envelope.source_snapshot_id,
                envelope.source_kind.value,
                envelope.source_id,
                envelope.content_sha256,
                envelope.run_id,
                tenant_id,
                canonical_table,
                canonical_id,
                version,
            ),
        )

    def get_checkpoint(self, source_kind: SourceKind, partition_key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT source_cursor
                FROM {self._schema}.checkpoints
                WHERE source_kind = %s AND partition_key = %s
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (source_kind.value, partition_key),
            ).fetchone()
        return None if row is None else str(row[0])

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None:
        source_cursor = str(envelope.source_document.get("_id") or envelope.source_id)
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.checkpoints (
                    source_kind, partition_key, source_cursor, source_updated_at,
                    run_id, processed_count
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_kind, partition_key) DO UPDATE SET
                    source_cursor = EXCLUDED.source_cursor,
                    source_updated_at = EXCLUDED.source_updated_at,
                    run_id = EXCLUDED.run_id,
                    processed_count = EXCLUDED.processed_count,
                    updated_at = CURRENT_TIMESTAMP
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (
                    source_kind.value,
                    partition_key,
                    source_cursor,
                    envelope.source_updated_at,
                    envelope.run_id,
                    processed_count,
                ),
            )

    def reconcile(
        self,
        run_id: str,
        source_kind: SourceKind,
        source_count: int,
        source_checksum: str,
        valid_checksum: str,
    ) -> ReconciliationResult:
        raw_table = f"raw_{source_kind.value}"
        with self._connect() as connection:
            raw_relation = f"{self._config.raw_schema}.{raw_table}"
            raw_relation_exists = connection.execute(
                "SELECT to_regclass(%s)",
                (raw_relation,),
            ).fetchone()
            if raw_relation_exists is None or raw_relation_exists[0] is None:
                if source_count:
                    raise RuntimeError(
                        f"Raw landing table {raw_relation} is missing for a non-empty run"
                    )
                raw_rows = []
            else:
                raw_rows = connection.execute(
                    f"""
                    SELECT source_snapshot_id::text, content_sha256
                    FROM {raw_relation}
                    WHERE run_id = %s
                    """,  # nosec B608 -- SourceKind and schema identifiers are validated.
                    (run_id,),
                ).fetchall()
            canonical_rows = connection.execute(
                f"""
                SELECT DISTINCT source_snapshot_id::text, content_sha256
                FROM {self._schema}.canonical_lineage
                WHERE run_id = %s AND source_kind = %s
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (run_id, source_kind.value),
            ).fetchall()
            quarantine_rows = connection.execute(
                f"""
                SELECT reason_code, COUNT(*)
                FROM {self._schema}.quarantined_records
                WHERE run_id = %s AND source_kind = %s AND resolved_at IS NULL
                GROUP BY reason_code
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (run_id, source_kind.value),
            ).fetchall()
            drift_row = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {self._schema}.tombstones AS tomb
                JOIN {self._schema}.canonical_lineage AS lineage
                  ON lineage.tenant_id = tomb.tenant_id
                 AND lineage.source_kind = tomb.entity_type
                 AND lineage.source_id = tomb.entity_id
                WHERE tomb.entity_type = %s
                  AND (lineage.source_version IS NULL OR lineage.source_version <= tomb.source_version)
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (source_kind.value,),
            ).fetchone()
        raw_checksum = aggregate_checksum([f"{row[0]}:{row[1]}" for row in raw_rows])
        canonical_checksum = aggregate_checksum([f"{row[0]}:{row[1]}" for row in canonical_rows])
        return ReconciliationResult(
            source_total=source_count,
            valid_loaded=len(canonical_rows),
            quarantined_count=sum(int(row[1]) for row in quarantine_rows),
            raw_count=len(raw_rows),
            canonical_count=len(canonical_rows),
            source_checksum=source_checksum,
            raw_checksum=raw_checksum,
            valid_checksum=valid_checksum,
            canonical_checksum=canonical_checksum,
            quarantine_reason_counts={str(row[0]): int(row[1]) for row in quarantine_rows},
            sink_delete_drift=0 if drift_row is None else int(drift_row[0]),
        )

    def complete_run(
        self,
        run_id: str,
        *,
        final_cursor: str | None,
        processed_count: int,
        reconciliation: ReconciliationResult,
        partition_complete: bool,
        finished_at: datetime,
    ) -> None:
        status = "SUCCEEDED" if reconciliation.reconciled else "RECONCILIATION_FAILED"
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE {self._schema}.ingestion_runs
                SET status = %s,
                    final_cursor = %s,
                    processed_count = %s,
                    valid_loaded = %s,
                    quarantined_count = %s,
                    reconciled = %s,
                    partition_complete = %s,
                    source_checksum = %s,
                    raw_checksum = %s,
                    canonical_checksum = %s,
                    finished_at = %s
                WHERE run_id = %s
                """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                (
                    status,
                    final_cursor,
                    processed_count,
                    reconciliation.valid_loaded,
                    reconciliation.quarantined_count,
                    reconciliation.reconciled,
                    partition_complete,
                    reconciliation.source_checksum,
                    reconciliation.raw_checksum,
                    reconciliation.canonical_checksum,
                    finished_at,
                    run_id,
                ),
            )

    def fail_run(
        self,
        run_id: str,
        *,
        source_kind: SourceKind,
        partition_key: str,
        source_snapshot_ids: Sequence[str],
        error: BaseException,
        retryable: bool,
    ) -> None:
        with self._connect() as connection:
            with connection.transaction():
                connection.execute(
                    f"""
                    UPDATE {self._schema}.ingestion_runs
                    SET status = 'FAILED',
                        error_type = %s,
                        error_message = %s,
                        finished_at = %s
                    WHERE run_id = %s
                    """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                    (type(error).__name__, str(error), datetime.now(UTC), run_id),
                )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.projection_failures (
                        failure_id, run_id, source_kind, partition_key,
                        source_snapshot_ids, error_type, error_message, retryable
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,  # nosec B608 -- DataPlaneConfig validates the schema identifier.
                    (
                        uuid4(),
                        run_id,
                        source_kind.value,
                        partition_key,
                        [UUID(value) for value in source_snapshot_ids],
                        type(error).__name__,
                        str(error),
                        retryable,
                    ),
                )
