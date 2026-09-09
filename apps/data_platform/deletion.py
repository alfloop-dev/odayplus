"""Downstream delete / tombstone propagation semantics for the data plane.

Phase 34A (``ODP-CDC-SOURCE-CONTRACT-PREP-001``) recorded that
``apps/data_platform/store.py`` and ``pipeline.py`` had no physical delete, soft
delete, or tombstone path at all, so an upstream entity deletion left the
downstream PostgreSQL projection populated forever. This module holds the
ordering, idempotency and tenant-isolation rules for closing that gap; the SQL
that executes them lives in :mod:`apps.data_platform.store`.

Scope limit, stated so the result is not over-read: everything here is an
*offline* landing-layer capability driven by envelopes the caller already
produced. It opens no change stream, reads no upstream credential, and performs
no live deletion by itself. The ``soft_delete`` mode of the 34A draft contract
(mapping ``void`` / ``refund`` / ``withdraw`` onto status columns) is a CDC verb
mapping question that stays with Phase 34B and H07, and is deliberately not
implemented here.

Two invariants drive every decision:

* A tombstone is a *version-guarded* record. A delete only wins over what is
  already recorded when its source version is at least as new, so a replayed or
  late-arriving older delete converges instead of regressing.
* A projection upsert may never resurrect an entity that a newer delete already
  removed. An envelope whose source version is unknown is treated as older than
  every tombstone, so the unknown case fails closed rather than resurrecting.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from apps.data_platform.contracts import SourceEnvelope, SourceKind


class DeletePropagationMode(StrEnum):
    """Delete propagation modes carried over from the 34A draft contract."""

    #: Record purge evidence and suppress resurrection; leave rows in place.
    TOMBSTONE_PURGE = "TOMBSTONE_PURGE"
    #: Additionally remove the tenant-scoped projection rows this source landed.
    SINK_DELETE = "SINK_DELETE"


class DeleteOutcome(StrEnum):
    """Terminal outcome of one delete event, recorded for readback audit."""

    #: The delete won and downstream projection rows were in scope.
    APPLIED = "APPLIED"
    #: Nothing had landed downstream; the tombstone is still recorded so a
    #: late upsert of the deleted entity cannot land later.
    ABSENT_TOMBSTONED = "ABSENT_TOMBSTONED"
    #: Same source version as the recorded tombstone: idempotent replay.
    REPLAYED = "REPLAYED"
    #: Older than the recorded tombstone: ignored without regressing state.
    STALE_IGNORED = "STALE_IGNORED"
    #: No tenant could be resolved, or the named tenant does not exist.
    REJECTED_UNRESOLVED_TENANT = "REJECTED_UNRESOLVED_TENANT"
    #: The event named a tenant that does not own this source identity.
    REJECTED_TENANT_BOUNDARY = "REJECTED_TENANT_BOUNDARY"
    #: The source identity is owned by more than one tenant and the event did
    #: not say which; deleting either one would cross a tenant boundary.
    REJECTED_AMBIGUOUS_TENANT = "REJECTED_AMBIGUOUS_TENANT"
    #: The event carried no usable monotonic source version.
    REJECTED_UNKNOWN_VERSION = "REJECTED_UNKNOWN_VERSION"


#: Outcomes that leave every downstream row and every tombstone untouched.
REJECTED_OUTCOMES = frozenset(
    {
        DeleteOutcome.REJECTED_UNRESOLVED_TENANT,
        DeleteOutcome.REJECTED_TENANT_BOUNDARY,
        DeleteOutcome.REJECTED_AMBIGUOUS_TENANT,
        DeleteOutcome.REJECTED_UNKNOWN_VERSION,
        DeleteOutcome.STALE_IGNORED,
    }
)


def version_from_timestamp(moment: datetime) -> int:
    """Return the monotonic source version encoded by an aware timestamp."""
    if moment.tzinfo is None:
        raise ValueError("Source versions require timezone-aware timestamps")
    version = int(moment.timestamp() * 1_000_000)
    if version < 0:
        raise ValueError("Source versions predating the epoch are not orderable")
    return version


def envelope_version(envelope: SourceEnvelope) -> int | None:
    """Return an envelope's source version, or ``None`` when unknown.

    Only ``source_updated_at`` counts. ``observed_at`` is when *this pipeline*
    read the record, so a replay of an ancient document would otherwise look
    newer than the delete that removed it.
    """
    if envelope.source_updated_at is None:
        return None
    try:
        return version_from_timestamp(envelope.source_updated_at)
    except ValueError:
        return None


def suppresses_upsert(recorded_version: int | None, candidate_version: int | None) -> bool:
    """Return whether a recorded tombstone must block a projection upsert."""
    if recorded_version is None:
        return False
    if candidate_version is None:
        return True
    return candidate_version <= recorded_version


@dataclass(frozen=True)
class DeleteScope:
    """The tenant / source-kind / source-id triple a delete may act on."""

    source_kind: SourceKind
    source_id: str
    tenant_id: UUID | None = None

    def __post_init__(self) -> None:
        if not str(self.source_id).strip():
            raise ValueError("source_id is required for a delete scope")


@dataclass(frozen=True)
class DeleteEvent:
    """One upstream delete or tombstone, expressed in landing-layer terms."""

    scope: DeleteScope
    source_version: int | None
    purged_at: datetime
    source_snapshot_id: str
    tombstone_hash: str
    run_id: str
    mode: DeletePropagationMode = DeletePropagationMode.TOMBSTONE_PURGE
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_snapshot_id", "tombstone_hash", "run_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.purged_at.tzinfo is None:
            raise ValueError("purged_at must be timezone aware")


@dataclass(frozen=True)
class TombstoneState:
    """The durable tombstone row, as read back for audit."""

    tenant_id: UUID
    source_kind: SourceKind
    source_id: str
    source_version: int
    purged_at: datetime
    propagation_mode: DeletePropagationMode
    tombstone_hash: str
    source_snapshot_id: str
    run_id: str
    purged_row_count: int
    retained_targets: tuple[str, ...]
    replay_count: int


@dataclass(frozen=True)
class TenantResolution:
    """Outcome of binding a delete event to exactly one owning tenant."""

    tenant_id: UUID | None
    outcome: DeleteOutcome | None
    detail: str

    @property
    def resolved(self) -> bool:
        return self.outcome is None and self.tenant_id is not None


@dataclass(frozen=True)
class DeleteDecision:
    """What a delete event is allowed to do against the recorded tombstone."""

    outcome: DeleteOutcome
    detail: str

    @property
    def records_tombstone(self) -> bool:
        return self.outcome not in REJECTED_OUTCOMES

    @property
    def purges_rows(self) -> bool:
        return self.outcome in {DeleteOutcome.APPLIED, DeleteOutcome.REPLAYED}


@dataclass(frozen=True)
class DeleteResult:
    """Readback-friendly record of one applied or refused delete event."""

    outcome: DeleteOutcome
    scope: DeleteScope
    mode: DeletePropagationMode
    detail: str
    tenant_id: UUID | None = None
    source_version: int | None = None
    purged_row_count: int = 0
    retained_targets: tuple[str, ...] = ()
    replay_count: int = 0

    @property
    def rejected(self) -> bool:
        return self.outcome in REJECTED_OUTCOMES

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "source_kind": self.scope.source_kind.value,
            "source_id": self.scope.source_id,
            "tenant_id": None if self.tenant_id is None else str(self.tenant_id),
            "propagation_mode": self.mode.value,
            "source_version": self.source_version,
            "purged_row_count": self.purged_row_count,
            "retained_targets": list(self.retained_targets),
            "replay_count": self.replay_count,
            "detail": self.detail,
        }


def resolve_delete_tenant(
    declared: UUID | None,
    owning_tenants: Iterable[UUID],
) -> TenantResolution:
    """Bind a delete to the single tenant that owns the source identity.

    ``owning_tenants`` comes from ``canonical_lineage``, which is the only
    downstream record of which tenant a source identity actually landed under.
    A declared tenant is never trusted past that record: an event naming a
    tenant that does not own the identity is refused rather than widened.
    """
    owners = set(owning_tenants)
    if declared is not None:
        if owners and declared not in owners:
            return TenantResolution(
                None,
                DeleteOutcome.REJECTED_TENANT_BOUNDARY,
                f"tenant {declared} does not own this source identity",
            )
        return TenantResolution(declared, None, "tenant declared by the delete event")
    if len(owners) == 1:
        resolved = owners.pop()
        return TenantResolution(resolved, None, "tenant resolved from canonical lineage")
    if not owners:
        return TenantResolution(
            None,
            DeleteOutcome.REJECTED_UNRESOLVED_TENANT,
            "no tenant declared and nothing landed downstream for this identity",
        )
    return TenantResolution(
        None,
        DeleteOutcome.REJECTED_AMBIGUOUS_TENANT,
        f"no tenant declared and {len(owners)} tenants own this source identity",
    )


def decide_delete(
    *,
    requested_version: int | None,
    recorded_version: int | None,
    downstream_target_count: int,
) -> DeleteDecision:
    """Decide one delete event against the tombstone already recorded."""
    if requested_version is None or requested_version < 0:
        return DeleteDecision(
            DeleteOutcome.REJECTED_UNKNOWN_VERSION,
            "delete events without a monotonic source version cannot be ordered",
        )
    if recorded_version is None:
        if downstream_target_count:
            return DeleteDecision(
                DeleteOutcome.APPLIED,
                f"first delete for this identity over {downstream_target_count} target(s)",
            )
        return DeleteDecision(
            DeleteOutcome.ABSENT_TOMBSTONED,
            "nothing landed downstream; tombstone recorded to block later upserts",
        )
    if recorded_version > requested_version:
        return DeleteDecision(
            DeleteOutcome.STALE_IGNORED,
            f"recorded version {recorded_version} is newer than {requested_version}",
        )
    if recorded_version == requested_version:
        return DeleteDecision(
            DeleteOutcome.REPLAYED,
            f"version {requested_version} already recorded; replay is idempotent",
        )
    return DeleteDecision(
        DeleteOutcome.APPLIED,
        f"version {requested_version} supersedes recorded version {recorded_version}",
    )


# Tenant-scoped purge statements, keyed by the ``canonical_lineage`` table name.
# Every statement binds ``(canonical_id, tenant_id)`` in that order, and every
# statement carries the tenant predicate itself so a wrong lineage row can never
# reach across a tenant boundary. Tables absent from this map are never purged;
# they are reported as retained targets instead of being deleted through SQL
# built from a table name that came out of the database.
_LEAF_PURGE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "core.transactions": (
        # The data-plane authority row references the transaction, so it goes first.
        "DELETE FROM {schema}.transaction_authority AS auth "
        "USING core.transactions AS target, core.stores AS scope "
        "WHERE auth.transaction_id = target.transaction_id "
        "AND target.transaction_id = %s AND target.store_id = scope.store_id "
        "AND scope.tenant_id = %s",
        "DELETE FROM core.transactions AS target USING core.stores AS scope "
        "WHERE target.transaction_id = %s AND target.store_id = scope.store_id "
        "AND scope.tenant_id = %s",
    ),
    "core.machine_status_events": (
        # The data-plane evidence row references the event, so it goes first.
        "DELETE FROM {schema}.machine_status_event_evidence "
        "WHERE status_event_id = %s AND tenant_id = %s",
        "DELETE FROM core.machine_status_events AS target USING core.stores AS scope "
        "WHERE target.status_event_id = %s AND target.store_id = scope.store_id "
        "AND scope.tenant_id = %s",
    ),
    "{schema}.store_daily_facts": (
        "DELETE FROM {schema}.store_daily_facts "
        "WHERE source_snapshot_id = %s AND tenant_id = %s",
    ),
    "{schema}.forecast_inputs": (
        "DELETE FROM {schema}.forecast_inputs "
        "WHERE source_snapshot_id = %s AND tenant_id = %s",
    ),
    "{schema}.learning_import_lineage": (
        "DELETE FROM {schema}.learning_import_lineage "
        "WHERE source_snapshot_id = %s AND tenant_id = %s",
    ),
    "{schema}.domain_inputs": (
        "DELETE FROM {schema}.domain_inputs "
        "WHERE source_snapshot_id = %s AND tenant_id = %s",
    ),
}

#: Identity and hierarchy tables a source delete must not physically remove.
#: They are shared parents (a merchant delete would cascade into every store,
#: machine and transaction beneath it) and their retirement policy is a human
#: decision that H07 has not answered. They are tombstoned and reported, never
#: purged, so the audit never claims an erasure that did not happen.
RETAINED_CANONICAL_TABLES = frozenset(
    {
        "core.tenants",
        "core.brands",
        "core.stores",
        "core.machines",
        "core.address_locations",
    }
)


def purgeable_tables(control_schema: str) -> frozenset[str]:
    """Return the canonical tables a sink delete is allowed to purge."""
    return frozenset(
        table.format(schema=control_schema) for table in _LEAF_PURGE_TEMPLATES
    )


@dataclass(frozen=True)
class PurgePlan:
    """Ordered, fully bound purge statements plus the targets left in place."""

    statements: tuple[tuple[str, tuple[Any, ...]], ...]
    retained_targets: tuple[str, ...]


def plan_purge(
    targets: Sequence[tuple[str, UUID]],
    *,
    tenant_id: UUID,
    control_schema: str,
) -> PurgePlan:
    """Build the tenant-scoped purge plan for one identity's lineage targets."""
    templates = {
        table.format(schema=control_schema): tuple(
            statement.format(schema=control_schema) for statement in statements
        )
        for table, statements in _LEAF_PURGE_TEMPLATES.items()
    }
    statements: list[tuple[str, tuple[Any, ...]]] = []
    retained: set[str] = set()
    for canonical_table, canonical_id in targets:
        bound = templates.get(canonical_table)
        if bound is None:
            retained.add(canonical_table)
            continue
        statements.extend((statement, (canonical_id, tenant_id)) for statement in bound)
    return PurgePlan(tuple(statements), tuple(sorted(retained)))
