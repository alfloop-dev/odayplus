"""Scoped MongoDB change-stream adapter, checkpoint recovery and delete propagation.

Phase 34B of ``ODP-FR-INT-001``. Phase 34A
(``ODP-CDC-SOURCE-CONTRACT-PREP-001``) produced the per-source applicability
matrix and the exchange contract draft; the H07 ruling of 2026-09-13 then fixed
the five open decisions. This module implements that ruling. The decisions it
encodes, and where each one is load-bearing:

1. **Scoped CDC.** Only ``orders`` and ``device_log`` (the source of
   ``core.machine_status_events``) get a change stream, with a sub-10s target.
   The other thirteen collections keep the 15-minute sensor / daily snapshot
   batch path. See :data:`SCOPED_CDC_POLICIES` and :data:`BATCH_ONLY_SOURCE_KINDS`.
2. **Replica set, 24-48h oplog window.** A resume token that falls outside the
   window is permanently dead, and the approved recovery is an automatic full
   snapshot re-read. See :class:`CdcCheckpoint` and :func:`plan_snapshot_recovery`.
3. **Dedicated ``odp_cdc_reader`` account, in-adapter projection and masking.**
   See :func:`redact_change_document`.
4. **Soft delete and audit tombstone in parallel.** A business row is marked
   (``transaction_status = 'voided'``) rather than removed, and a tombstone is
   recorded alongside it. A GDPR erasure additionally writes a SHA-256 hash
   tombstone, blanks the identifying fields, and hands physical removal to a
   90-day Retention Purge Job. See :func:`plan_change_application` and
   :func:`gdpr_erasure`.
5. **Dagster-resident sensor writing straight to PostgreSQL.** No Kafka,
   Redpanda, Pub/Sub or RabbitMQ is introduced. The staging table is
   ``data_plane.cdc_staging_events``; the wiring lives in
   :mod:`apps.data_platform.definitions`.

Two hard constraints from the ruling shape the code more than anything else:

* **(a) CDC layers on top of the batch path; it never replaces it.** Because an
  expired resume token can only be recovered by a full re-read, the snapshot
  batch path stays the fallback for every source kind, including the two scoped
  ones. :func:`batch_path_retained` returns ``True`` for all fifteen kinds and
  :func:`plan_snapshot_recovery` produces the batch partitions to re-run. Any
  future change that makes CDC the sole path for a collection contradicts the
  ruling (evidence: ``docs/evidence/ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md``
  line 257).
* **(b) A change stream returns the whole document.** That boundary is wider
  than the batch reader's server-side ``find`` projection, so the same
  allowlist has to be re-applied in memory the moment the packet is
  deserialised, before the event reaches any buffer or table.
  :func:`redact_change_document` is that gate, and it reuses
  ``apps.data_platform.source.SOURCE_PROJECTIONS`` rather than restating it, so
  the two paths cannot drift apart.

Scope limit, stated so results here are not over-read. This module opens no
change stream by itself: :class:`MongoChangeStreamFactory` requires a live,
already-authenticated database handle and raises rather than substituting
sample data, per the 34A entry condition that forbids a stub connector. Nothing
here proves the upstream cluster is a replica set, that the oplog window is
what the ruling recorded, or that the sub-10s target is met in production;
those are live measurements that need the real credential and are deliberately
out of this module's reach.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from apps.data_platform.contracts import SourceEnvelope, SourceKind
from apps.data_platform.deletion import (
    DeleteEvent,
    DeletePropagationMode,
    DeleteScope,
    version_from_timestamp,
)
from apps.data_platform.identifiers import snapshot_id_for_content, tenant_id_for_merchant
from apps.data_platform.serialization import canonical_json, json_safe, parse_datetime
from apps.data_platform.source import (
    SOURCE_PROJECTIONS,
    envelope_for_document,
    source_id_for_document,
)


#: Contract draft this adapter implements, carried onto every staged row so a
#: later envelope revision is distinguishable in the landing table.
CDC_CONTRACT_VERSION = "1.0.0-draft.34a"
#: ``source_system`` enum value from the 34A exchange contract.
CDC_SOURCE_SYSTEM = "fongniao_mongo"
#: The only database this data plane is approved to read.
CDC_SOURCE_DATABASE = "fongniao_prod"
#: Least-privilege account H07 decision 3 approved for ``changeStream`` + ``find``.
CDC_READER_ROLE = "odp_cdc_reader"

#: Oplog retention window recorded by H07 decision 2. It is an operator's verbal
#: range, not a measured value: no ``rs.status()`` or ``db.getReplicationInfo()``
#: output has been read back. The floor is what recovery planning assumes, so the
#: assumption fails safe (a wider real window only means the plan re-reads more
#: than strictly necessary).
OPLOG_RETENTION_FLOOR = timedelta(hours=24)
OPLOG_RETENTION_CEILING = timedelta(hours=48)

#: H07 decision 4: physical removal is deferred to a periodic Retention Purge
#: Job this far after the erasure is recorded.
GDPR_PURGE_RETENTION = timedelta(days=90)


class CdcScopeError(RuntimeError):
    """Raised when CDC is requested for a collection H07 kept on the batch path."""


class SchemaValidationError(ValueError):
    """Raised when a change packet cannot be read as a contract-shaped event."""


class OplogCursorExpiredError(RuntimeError):
    """Raised when a stored resume token is no longer inside the oplog window.

    Fail-closed on purpose. The stream must not silently restart from "now",
    because every change between the dead token and now would be lost without
    any record that it happened. The caller's only approved move is the full
    snapshot re-read :func:`plan_snapshot_recovery` describes.
    """


class CdcOperation(StrEnum):
    """Mutation verbs from the 34A exchange contract."""

    INSERT = "insert"
    UPDATE = "update"
    REPLACE = "replace"
    DELETE = "delete"
    VOID = "void"
    REFUND = "refund"
    WITHDRAW = "withdraw"
    TOMBSTONE = "tombstone"
    SNAPSHOT_BACKFILL = "snapshot_backfill"


#: Verbs that remove or retire an entity downstream rather than upserting it.
RETIRING_OPERATIONS = frozenset(
    {
        CdcOperation.DELETE,
        CdcOperation.VOID,
        CdcOperation.REFUND,
        CdcOperation.WITHDRAW,
        CdcOperation.TOMBSTONE,
    }
)


class CheckpointStatus(StrEnum):
    """Lifecycle of one ``(source, partition)`` resume token."""

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    PAUSED = "PAUSED"


class CdcRejectReason(StrEnum):
    """Quarantine taxonomy from the 34A exchange contract."""

    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    RESUME_TOKEN_EXPIRED = "RESUME_TOKEN_EXPIRED"
    TENANT_BOUNDARY_VIOLATION = "TENANT_BOUNDARY_VIOLATION"
    CORRUPTED_PAYLOAD_CHECKSUM = "CORRUPTED_PAYLOAD_CHECKSUM"
    SOURCE_SUPERSEDED = "SOURCE_SUPERSEDED"


class ChangeDisposition(StrEnum):
    """What the ordering / idempotency guard decided about one change."""

    #: Newer than what landed; project it.
    APPLY = "APPLY"
    #: Exactly what already landed; converge without rewriting.
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    #: Older than what landed; never regress the row.
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class ScopedCdcPolicy:
    """Per-collection CDC policy as ruled by H07 decision 1."""

    source_kind: SourceKind
    collection: str
    #: Sub-10s end-to-end target from the ruling. The 34A acceptance matrix
    #: additionally asks for a P95 under 5s measured against live telemetry;
    #: neither number is verified by this module.
    latency_sla_seconds: int
    #: Which fields form the ordering partition, per the contract's
    #: ``partition_key_rules``.
    partition_key_fields: tuple[str, ...]
    #: Canonical table the projected event lands in.
    canonical_table: str
    #: Redaction profile applied in memory before buffering (constraint b).
    redaction_profile: str
    #: Primary key of :attr:`canonical_table`, used to join lineage to the row.
    canonical_id_column: str
    #: Column the soft delete marks, or ``None`` when the canonical table has no
    #: approved record-lifecycle column.
    lifecycle_column: str | None
    #: Why the lifecycle column is absent, when it is.
    lifecycle_gap: str | None = None


SCOPED_CDC_POLICIES: dict[SourceKind, ScopedCdcPolicy] = {
    SourceKind.ORDERS: ScopedCdcPolicy(
        source_kind=SourceKind.ORDERS,
        collection="orders",
        latency_sla_seconds=10,
        partition_key_fields=("tenant_id", "store_id"),
        canonical_table="core.transactions",
        canonical_id_column="transaction_id",
        redaction_profile="orders_projected_v1",
        lifecycle_column="transaction_status",
    ),
    SourceKind.DEVICE_LOG: ScopedCdcPolicy(
        source_kind=SourceKind.DEVICE_LOG,
        collection="device_log",
        latency_sla_seconds=10,
        partition_key_fields=("machine_id",),
        canonical_table="core.machine_status_events",
        canonical_id_column="status_event_id",
        redaction_profile="device_log_minimized_v1",
        lifecycle_column=None,
        lifecycle_gap=(
            "core.machine_status_events has no approved record-lifecycle column: "
            "status_type carries the device's state (online/offline/error/...), not "
            "the row's. Adding one is a canonical migration under infra/db/migrations/, "
            "outside this task's owned paths, so a device_log retirement records the "
            "audit tombstone and retains the row instead of claiming a soft delete "
            "that no column can express."
        ),
    ),
}

#: Every kind H07 decision 1 left on the 15-minute sensor / daily snapshot path.
BATCH_ONLY_SOURCE_KINDS = frozenset(set(SourceKind) - set(SCOPED_CDC_POLICIES))

#: Canonical statuses a retirement verb maps onto. Constrained to the governed
#: enum in ``apps.data_platform.status_mapping``; ``withdraw`` has no separate
#: canonical status there, so it converges on ``voided`` like the other
#: non-refund retirements rather than inventing an unapproved value.
RETIREMENT_STATUS: dict[CdcOperation, str] = {
    CdcOperation.DELETE: "voided",
    CdcOperation.VOID: "voided",
    CdcOperation.WITHDRAW: "voided",
    CdcOperation.TOMBSTONE: "voided",
    CdcOperation.REFUND: "refunded",
}

#: Upstream state tokens that carry a business retirement verb, per source kind.
#: Only tokens the 34A inventory actually observed are listed: ``orders.state``
#: is documented in ``apps.data_platform.mapping`` as the four-value set
#: TRADE_SUCCESS / TRADE_FAIL / TRADE_NOT_PAY / TRADE_REFUND, so ``TRADE_REFUND``
#: is the one derivable verb. ``void`` and ``withdraw`` have no observed upstream
#: token; they are honoured when an event carries them explicitly and are never
#: inferred from an unrecognised state, which would be fabricating a source enum.
SOURCE_VERB_TOKENS: dict[SourceKind, dict[str, CdcOperation]] = {
    SourceKind.ORDERS: {"TRADE_REFUND": CdcOperation.REFUND},
    SourceKind.DEVICE_LOG: {},
}

#: MongoDB ``operationType`` values this adapter understands. ``invalidate``,
#: ``drop`` and ``dropDatabase`` are deliberately absent: they mean the stream
#: itself died, which is an expiry-class event, not a document change.
_MONGO_OPERATION_TYPES: dict[str, CdcOperation] = {
    "insert": CdcOperation.INSERT,
    "update": CdcOperation.UPDATE,
    "replace": CdcOperation.REPLACE,
    "delete": CdcOperation.DELETE,
}

#: ``operationType`` values that mean the cursor can no longer be resumed.
_STREAM_INVALIDATING_TYPES = frozenset({"invalidate", "drop", "dropDatabase", "rename"})


def cdc_policy(source_kind: SourceKind) -> ScopedCdcPolicy:
    """Return the CDC policy for a scoped kind, or refuse an out-of-scope one."""
    try:
        return SCOPED_CDC_POLICIES[source_kind]
    except KeyError as exc:
        raise CdcScopeError(
            f"{source_kind.value} is not in the H07 CDC scope; it stays on the "
            f"15-minute sensor / daily snapshot batch path"
        ) from exc


def batch_path_retained(source_kind: SourceKind) -> bool:
    """Return whether the snapshot batch path still owns this kind.

    Always ``True``. Enabling a change stream adds a low-latency path; it never
    retires the batch one, because an expired resume token's only recovery is a
    full re-read. This function exists so the invariant is assertable rather
    than merely documented.
    """
    _ = source_kind
    return True


@dataclass(frozen=True)
class RedactionReport:
    """Which fields survived the in-memory allowlist and which were dropped."""

    profile: str
    kept: tuple[str, ...]
    dropped: tuple[str, ...]

    @property
    def redacted(self) -> bool:
        return bool(self.dropped)


def redact_change_document(
    source_kind: SourceKind,
    document: dict[str, Any] | None,
) -> tuple[dict[str, Any], RedactionReport]:
    """Apply the batch reader's field allowlist to a full change-stream document.

    This is hard constraint (b). ``collection.find(query, SOURCE_PROJECTIONS[kind])``
    lets the server drop unapproved fields before they ever reach this process;
    ``collection.watch()`` has no equivalent, so the identical allowlist is
    enforced here, on the deserialised packet, before the payload is staged,
    buffered or logged. The allowlist is read from ``SOURCE_PROJECTIONS`` rather
    than copied, so the CDC boundary cannot silently widen when the batch
    projection changes.

    Nested minimisation (``device_log.logData``) is *not* repeated here: it is
    applied by ``envelope_for_document``, which both paths share, so the two
    stay byte-identical including the content hash.
    """
    policy = cdc_policy(source_kind)
    allowed = SOURCE_PROJECTIONS[source_kind]
    payload = json_safe(document or {})
    if not isinstance(payload, dict):
        raise SchemaValidationError(
            f"{source_kind.value} change payload is {type(payload).__name__}, not an object"
        )
    kept = {key: value for key, value in payload.items() if allowed.get(key)}
    dropped = tuple(sorted(set(payload) - set(kept)))
    return kept, RedactionReport(policy.redaction_profile, tuple(sorted(kept)), dropped)


def idempotency_key(
    *,
    source_collection: str,
    document_id: str,
    sequence_or_updated_at: str,
) -> str:
    """Return the contract's deterministic dedup hash.

    ``sha256(source_system + ':' + collection + ':' + document_id + ':' + sequence_or_updated_at)``,
    exactly as the 34A contract specifies, so an event replayed through any
    producer collapses onto the same staged row.
    """
    material = ":".join(
        (CDC_SOURCE_SYSTEM, source_collection, str(document_id), str(sequence_or_updated_at))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def partition_key_for(source_kind: SourceKind, payload: dict[str, Any]) -> str:
    """Return the ordering partition for one projected payload.

    Ordering is only ever guaranteed within a partition, so the key has to be
    derivable from the projected payload alone: a key that needed a dropped
    field could not be computed after redaction.
    """
    policy = cdc_policy(source_kind)
    if source_kind is SourceKind.ORDERS:
        merchant = _reference_id(payload.get("merchant"))
        place = _reference_id(payload.get("place"))
        if not merchant:
            raise SchemaValidationError("orders change carries no merchant reference")
        tenant = tenant_id_for_merchant(merchant)
        return f"{tenant}:{place or 'unassigned'}"
    device = _reference_id(payload.get("device"))
    if not device:
        raise SchemaValidationError("device_log change carries no device reference")
    _ = policy
    return device


def _reference_id(value: Any) -> str:
    """Return the source id of a Mongo reference, embedded document or scalar."""
    if isinstance(value, dict):
        for key in ("id", "_id"):
            nested = value.get(key)
            if nested is not None:
                return str(nested).strip()
        return ""
    return "" if value is None else str(value).strip()


def classify_operation(
    source_kind: SourceKind,
    operation_type: str,
    after_payload: dict[str, Any] | None,
    *,
    declared_operation: str | None = None,
) -> CdcOperation:
    """Map one change packet onto a contract verb.

    Precedence is deliberate. An explicitly declared verb wins, because verbs
    like ``withdraw`` exist in the contract but have no upstream state token to
    derive them from, and a producer that knows better must be able to say so.
    Otherwise a physical upstream delete is a ``delete``, and a business
    retirement is read from the observed state token map. An unrecognised state
    stays an ``update``: the downstream projection already quarantines it as
    ``UNSUPPORTED_STATUS``, which keeps CDC and batch reaching the same verdict
    on the same document.
    """
    if declared_operation:
        try:
            return CdcOperation(str(declared_operation).strip().lower())
        except ValueError as exc:
            raise SchemaValidationError(
                f"{declared_operation!r} is not a contract mutation verb"
            ) from exc
    normalized = str(operation_type or "").strip()
    if normalized in _STREAM_INVALIDATING_TYPES:
        raise OplogCursorExpiredError(
            f"change stream reported {normalized!r}; the cursor cannot be resumed"
        )
    try:
        base = _MONGO_OPERATION_TYPES[normalized]
    except KeyError as exc:
        raise SchemaValidationError(
            f"{operation_type!r} is not a supported MongoDB operationType"
        ) from exc
    if base is CdcOperation.DELETE:
        return base
    token = str((after_payload or {}).get("state") or "").strip().upper()
    return SOURCE_VERB_TOKENS.get(source_kind, {}).get(token, base)


@dataclass(frozen=True)
class CdcChangeEnvelope:
    """One redacted change event, shaped by the 34A ``cdc_change_envelope``."""

    change_id: str
    source_kind: SourceKind
    source_collection: str
    source_id: str
    operation: CdcOperation
    resume_token: str
    partition_key: str
    sequence_number: int
    server_timestamp: datetime
    source_timestamp: datetime | None
    ingested_at: datetime
    idempotency_key: str
    tenant_id: UUID | None
    after_payload: dict[str, Any]
    redaction: RedactionReport
    updated_fields: tuple[str, ...] = ()
    removed_fields: tuple[str, ...] = ()
    content_sha256: str = ""
    source_system: str = CDC_SOURCE_SYSTEM
    source_database: str = CDC_SOURCE_DATABASE
    contract_version: str = CDC_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("change_id", "resume_token", "partition_key", "idempotency_key"):
            if not str(getattr(self, name)).strip():
                raise SchemaValidationError(f"{name} is required on a change envelope")
        if self.sequence_number < 0:
            raise SchemaValidationError("sequence_number must be non-negative")
        if self.server_timestamp.tzinfo is None or self.ingested_at.tzinfo is None:
            raise SchemaValidationError("change envelope timestamps must be timezone aware")

    @property
    def retires_entity(self) -> bool:
        return self.operation in RETIRING_OPERATIONS

    @property
    def latency_seconds(self) -> float:
        """Capture latency: server commit to this process reading the packet.

        This is the producer-side half of the end-to-end number the acceptance
        matrix asks for. It stops at ingestion, not at the PostgreSQL commit, and
        on synthetic events it measures nothing but the fixture's own clock.
        """
        return (self.ingested_at - self.server_timestamp).total_seconds()

    @property
    def source_version(self) -> int | None:
        """Monotonic version used to order this change against recorded state."""
        moment = self.source_timestamp or self.server_timestamp
        try:
            return version_from_timestamp(moment)
        except ValueError:
            return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "source_system": self.source_system,
            "source_database": self.source_database,
            "source_collection_or_table": self.source_collection,
            "operation": self.operation.value,
            "resume_token": self.resume_token,
            "partition_key": self.partition_key,
            "sequence_number": self.sequence_number,
            "server_timestamp": self.server_timestamp.isoformat(),
            "source_timestamp": (
                None if self.source_timestamp is None else self.source_timestamp.isoformat()
            ),
            "ingested_at": self.ingested_at.isoformat(),
            "idempotency_key": self.idempotency_key,
            "tenant_id": None if self.tenant_id is None else str(self.tenant_id),
            "document_key": {"_id": self.source_id},
            "after_payload": dict(self.after_payload),
            "updated_fields": list(self.updated_fields),
            "removed_fields": list(self.removed_fields),
            "redaction_profile": self.redaction.profile,
            "redacted_fields": list(self.redaction.dropped),
            "content_sha256": self.content_sha256,
            "contract_version": self.contract_version,
        }


def change_envelope(
    source_kind: SourceKind,
    change: dict[str, Any],
    *,
    ingested_at: datetime,
    sequence_number: int,
) -> CdcChangeEnvelope:
    """Deserialise one raw change-stream packet into a redacted envelope.

    Redaction happens on the first line that touches the payload, before the
    envelope exists, so no code path downstream of here can observe an
    unprojected field.
    """
    policy = cdc_policy(source_kind)
    if not isinstance(change, dict):
        raise SchemaValidationError("change packet must be an object")
    resume_token = _resume_token_value(change.get("_id"))
    if not resume_token:
        raise SchemaValidationError("change packet carries no resume token")
    full_document = change.get("fullDocument")
    if full_document is None and change.get("operationType") != "delete":
        full_document = change.get("fullDocumentBeforeChange")
    after_payload, redaction = redact_change_document(source_kind, full_document)
    document_key = change.get("documentKey") or {}
    source_id = _reference_id(document_key.get("_id")) if isinstance(document_key, dict) else ""
    if not source_id:
        try:
            source_id = source_id_for_document(source_kind, after_payload)
        except ValueError as exc:
            raise SchemaValidationError(
                f"{source_kind.value} change carries no stable source id"
            ) from exc
    operation = classify_operation(
        source_kind,
        str(change.get("operationType") or ""),
        after_payload,
        declared_operation=change.get("odpOperation"),
    )
    server_timestamp = _cluster_time(change, ingested_at)
    source_timestamp = _optional_time(after_payload.get("updatedAt")) or _optional_time(
        after_payload.get("createdAt")
    )
    description = change.get("updateDescription") or {}
    updated_fields = tuple(sorted(dict(description.get("updatedFields") or {})))
    removed_fields = tuple(sorted(str(name) for name in description.get("removedFields") or ()))
    tenant_id = _tenant_for_payload(source_kind, after_payload)
    partition_key = (
        partition_key_for(source_kind, after_payload)
        if after_payload
        else f"{source_kind.value}:{source_id}"
    )
    return CdcChangeEnvelope(
        change_id=str(change.get("odpChangeId") or uuid4()),
        source_kind=source_kind,
        source_collection=policy.collection,
        source_id=source_id,
        operation=operation,
        resume_token=resume_token,
        partition_key=partition_key,
        sequence_number=sequence_number,
        server_timestamp=server_timestamp,
        source_timestamp=source_timestamp,
        ingested_at=ingested_at.astimezone(UTC),
        idempotency_key=idempotency_key(
            source_collection=policy.collection,
            document_id=source_id,
            sequence_or_updated_at=(
                source_timestamp.isoformat()
                if source_timestamp is not None
                else server_timestamp.isoformat()
            ),
        ),
        tenant_id=tenant_id,
        after_payload=after_payload,
        redaction=redaction,
        updated_fields=updated_fields,
        removed_fields=removed_fields,
        content_sha256=hashlib.sha256(
            canonical_json(after_payload).encode("utf-8")
        ).hexdigest(),
    )


def _resume_token_value(token: Any) -> str:
    if isinstance(token, dict):
        return str(token.get("_data") or "").strip()
    return "" if token is None else str(token).strip()


def _cluster_time(change: dict[str, Any], fallback: datetime) -> datetime:
    for key in ("wallTime", "clusterTime"):
        moment = _optional_time(change.get(key))
        if moment is not None:
            return moment
    return fallback.astimezone(UTC)


def _optional_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return parse_datetime(value, field_name="cdc.timestamp")
    except ValueError:
        return None


def _tenant_for_payload(source_kind: SourceKind, payload: dict[str, Any]) -> UUID | None:
    merchant = _reference_id(payload.get("merchant"))
    if not merchant:
        return None
    _ = source_kind
    return tenant_id_for_merchant(merchant)


@dataclass(frozen=True)
class ChangeDecision:
    """Ordering / idempotency verdict for one change against recorded state."""

    disposition: ChangeDisposition
    detail: str
    reason: CdcRejectReason | None = None

    @property
    def applies(self) -> bool:
        return self.disposition is ChangeDisposition.APPLY


def decide_change(
    envelope: CdcChangeEnvelope,
    *,
    recorded_server_timestamp: datetime | None,
    recorded_idempotency_key: str | None = None,
) -> ChangeDecision:
    """Decide one change against what already landed for the same entity.

    The contract's conditional upsert is
    ``... DO UPDATE ... WHERE EXCLUDED.server_timestamp >= target.server_timestamp``,
    so an equal timestamp is allowed through and a strictly older one is not.
    An exact idempotency-key match short-circuits first: a replayed event must
    converge on the same terminal state without being reported as a conflict.
    """
    if recorded_idempotency_key and recorded_idempotency_key == envelope.idempotency_key:
        return ChangeDecision(
            ChangeDisposition.DUPLICATE_IGNORED,
            f"idempotency key {envelope.idempotency_key[:12]}… already landed",
        )
    if recorded_server_timestamp is None:
        return ChangeDecision(ChangeDisposition.APPLY, "nothing recorded for this entity yet")
    if envelope.server_timestamp < recorded_server_timestamp:
        return ChangeDecision(
            ChangeDisposition.SUPERSEDED,
            (
                f"server timestamp {envelope.server_timestamp.isoformat()} is older than "
                f"the recorded {recorded_server_timestamp.isoformat()}"
            ),
            CdcRejectReason.SOURCE_SUPERSEDED,
        )
    return ChangeDecision(
        ChangeDisposition.APPLY,
        f"server timestamp {envelope.server_timestamp.isoformat()} is at least as new",
    )


def check_tenant_binding(
    envelope: CdcChangeEnvelope,
    *,
    authorized_tenants: Iterable[UUID] | None,
) -> ChangeDecision | None:
    """Refuse a change whose tenant is outside the partition's authorisation.

    ``authorized_tenants`` of ``None`` means "not constrained at this layer" and
    is not the same as an empty set, which means "no tenant is authorised here"
    and refuses everything.
    """
    if authorized_tenants is None:
        return None
    allowed = set(authorized_tenants)
    if envelope.tenant_id is not None and envelope.tenant_id in allowed:
        return None
    return ChangeDecision(
        ChangeDisposition.SUPERSEDED,
        (
            f"tenant {envelope.tenant_id} is not authorised for partition "
            f"{envelope.partition_key}"
        ),
        CdcRejectReason.TENANT_BOUNDARY_VIOLATION,
    )


@dataclass(frozen=True)
class CdcCheckpoint:
    """Durable ``(source, partition)`` cursor backing resume and recovery."""

    source_kind: SourceKind
    partition_id: str
    resume_token: str
    last_sequence_no: int
    last_server_timestamp: datetime
    processed_count: int = 0
    status: CheckpointStatus = CheckpointStatus.ACTIVE
    updated_at: datetime | None = None
    expired_at: datetime | None = None
    expiry_detail: str = ""
    #: Ingestion run that completed the snapshot re-read after an expiry. Until
    #: it is set, a new baseline token must not be minted: doing so would
    #: declare the gap closed without anything having re-read it.
    recovery_run_id: str | None = None

    def __post_init__(self) -> None:
        if not str(self.resume_token).strip():
            raise ValueError("a checkpoint without a resume token cannot resume anything")
        if self.last_server_timestamp.tzinfo is None:
            raise ValueError("checkpoint timestamps must be timezone aware")
        if self.last_sequence_no < 0 or self.processed_count < 0:
            raise ValueError("checkpoint counters must be non-negative")

    @property
    def resumable(self) -> bool:
        return self.status in {CheckpointStatus.ACTIVE, CheckpointStatus.STALE}


def resume_token_for(checkpoint: CdcCheckpoint | None) -> str | None:
    """Return the token to resume from, or refuse to resume at all.

    Steps 1-2 of the contract's resume sequence when the token is live; step 4
    when it is not. There is no third branch that starts from "now", because
    that is precisely the silent-loss behaviour the fail-closed rule forbids.
    """
    if checkpoint is None:
        return None
    if checkpoint.status is CheckpointStatus.EXPIRED:
        raise OplogCursorExpiredError(
            f"resume token for {checkpoint.source_kind.value}/{checkpoint.partition_id} "
            f"expired at {checkpoint.expired_at}: {checkpoint.expiry_detail}"
        )
    if checkpoint.status is CheckpointStatus.PAUSED:
        raise CdcScopeError(
            f"{checkpoint.source_kind.value}/{checkpoint.partition_id} is paused; "
            f"resume requires an explicit operator action"
        )
    return checkpoint.resume_token


def advance_checkpoint(
    checkpoint: CdcCheckpoint | None,
    envelope: CdcChangeEnvelope,
    *,
    processed_delta: int = 1,
    source_kind: SourceKind | None = None,
    partition_id: str | None = None,
) -> CdcCheckpoint:
    """Move the cursor forward onto one processed change.

    The cursor never moves backwards. A replayed older event still counts as
    processed but leaves the token where the newest event put it, so a crash
    during a replay cannot rewind the stream.
    """
    if checkpoint is None:
        kind = source_kind or envelope.source_kind
        return CdcCheckpoint(
            source_kind=kind,
            partition_id=partition_id or envelope.partition_key,
            resume_token=envelope.resume_token,
            last_sequence_no=envelope.sequence_number,
            last_server_timestamp=envelope.server_timestamp,
            processed_count=max(processed_delta, 0),
            status=CheckpointStatus.ACTIVE,
            updated_at=envelope.ingested_at,
        )
    if checkpoint.status is CheckpointStatus.EXPIRED and checkpoint.recovery_run_id is None:
        raise OplogCursorExpiredError(
            f"{checkpoint.source_kind.value}/{checkpoint.partition_id} is expired; "
            f"a snapshot recovery run must complete before a new baseline token"
        )
    moves_forward = envelope.sequence_number >= checkpoint.last_sequence_no
    return replace(
        checkpoint,
        resume_token=envelope.resume_token if moves_forward else checkpoint.resume_token,
        last_sequence_no=max(envelope.sequence_number, checkpoint.last_sequence_no),
        last_server_timestamp=max(
            envelope.server_timestamp, checkpoint.last_server_timestamp
        ),
        processed_count=checkpoint.processed_count + max(processed_delta, 0),
        status=CheckpointStatus.ACTIVE,
        updated_at=envelope.ingested_at,
        expired_at=None,
        expiry_detail="",
    )


def expire_checkpoint(
    checkpoint: CdcCheckpoint,
    *,
    at: datetime,
    detail: str,
) -> CdcCheckpoint:
    """Mark a cursor dead without discarding it.

    The dead token is kept on the row on purpose: it is the only evidence of
    where the stream actually stopped, and the recovery window is measured from
    the timestamp beside it.
    """
    return replace(
        checkpoint,
        status=CheckpointStatus.EXPIRED,
        expired_at=at.astimezone(UTC),
        expiry_detail=detail,
        recovery_run_id=None,
        updated_at=at.astimezone(UTC),
    )


@dataclass(frozen=True)
class SnapshotRecoveryPlan:
    """The full re-read that an expired resume token forces.

    H07 decision 2 pre-authorises this transition, so no human is in the loop
    for the switch itself. What is *not* authorised is skipping it: the CDC
    stream stays expired until a run listed here has actually completed.
    """

    source_kind: SourceKind
    partition_id: str
    #: Daily batch partitions to re-read, oldest first.
    partition_keys: tuple[str, ...]
    reason: CdcRejectReason
    detail: str
    triggered_at: datetime
    decision_ref: str = "H07-2026-09-13-decision-2"

    @property
    def batch_path(self) -> str:
        """The existing batch entry point this plan re-uses, never a new one."""
        return "apps.data_platform.pipeline.DataPlaneRunner.run_partition"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "partition_id": self.partition_id,
            "partition_keys": list(self.partition_keys),
            "reason": self.reason.value,
            "detail": self.detail,
            "triggered_at": self.triggered_at.isoformat(),
            "decision_ref": self.decision_ref,
            "batch_path": self.batch_path,
        }


def plan_snapshot_recovery(
    checkpoint: CdcCheckpoint,
    *,
    now: datetime,
    oplog_retention: timedelta = OPLOG_RETENTION_FLOOR,
) -> SnapshotRecoveryPlan:
    """Enumerate the batch partitions a re-read has to cover.

    The window starts at the earlier of the last committed change and
    ``now - oplog_retention``. Using the retention floor rather than the ceiling
    is the safe direction: if the real window is wider than the floor, the plan
    simply re-reads more days than strictly necessary, whereas assuming the
    ceiling could leave a hole.
    """
    if checkpoint.status is not CheckpointStatus.EXPIRED:
        raise ValueError("snapshot recovery is only planned for an expired checkpoint")
    horizon = now.astimezone(UTC) - oplog_retention
    start = min(checkpoint.last_server_timestamp.astimezone(UTC), horizon)
    partitions = _daily_partitions(start.date(), now.astimezone(UTC).date())
    return SnapshotRecoveryPlan(
        source_kind=checkpoint.source_kind,
        partition_id=checkpoint.partition_id,
        partition_keys=partitions,
        reason=CdcRejectReason.RESUME_TOKEN_EXPIRED,
        detail=(
            f"resume token dead since {checkpoint.last_server_timestamp.isoformat()}; "
            f"re-reading {len(partitions)} daily partition(s) through the batch path"
        ),
        triggered_at=now.astimezone(UTC),
    )


def _daily_partitions(start: date, end: date) -> tuple[str, ...]:
    if end < start:
        start, end = end, start
    span = (end - start).days
    return tuple((start + timedelta(days=offset)).isoformat() for offset in range(span + 1))


def record_recovery(checkpoint: CdcCheckpoint, *, run_id: str) -> CdcCheckpoint:
    """Attach the completed snapshot run that closes an expiry gap."""
    if not str(run_id).strip():
        raise ValueError("a recovery run id is required to clear an expired checkpoint")
    return replace(checkpoint, recovery_run_id=str(run_id))


@dataclass(frozen=True)
class SoftDeleteDirective:
    """H07 decision 4's soft delete: mark the business row, never remove it."""

    canonical_table: str
    canonical_id_column: str
    status_column: str
    status_value: str
    source_kind: SourceKind
    source_id: str
    tenant_id: UUID
    server_timestamp: datetime
    source_version: int | None

    def statement(self, control_schema: str) -> tuple[str, tuple[Any, ...]]:
        """Return the tenant-scoped, version-guarded status update.

        Three guards, each load-bearing. The ``canonical_lineage`` join is what
        binds a source identity to the canonical row, so the update cannot act
        on a row this source never landed. The ``core.stores`` join re-asserts
        the tenant at the row itself rather than trusting the lineage row alone.
        The version predicate makes a late-arriving older retirement a no-op
        instead of a regression.

        The join column comes from the source kind's policy rather than being
        written into this string. An earlier revision hardcoded
        ``target.transaction_id``, which was correct only because
        ``core.transactions`` is the one canonical table that currently has an
        approved lifecycle column; the moment a second one gained one, that
        literal would have produced silently wrong SQL against it.
        """
        statement = (
            f"UPDATE {self.canonical_table} AS target "  # nosec B608
            f"SET {self.status_column} = %s, updated_at = %s "
            f"FROM {control_schema}.canonical_lineage AS lineage, core.stores AS scope "
            f"WHERE lineage.canonical_table = %s "
            f"AND lineage.canonical_id = target.{self.canonical_id_column} "
            f"AND lineage.source_kind = %s AND lineage.source_id = %s "
            f"AND lineage.tenant_id = %s AND target.store_id = scope.store_id "
            f"AND scope.tenant_id = %s "
            f"AND (%s::bigint IS NULL OR lineage.source_version IS NULL "
            f"OR lineage.source_version <= %s::bigint) "
            f"AND target.{self.status_column} IS DISTINCT FROM %s"
        )
        params = (
            self.status_value,
            self.server_timestamp,
            self.canonical_table,
            self.source_kind.value,
            self.source_id,
            self.tenant_id,
            self.tenant_id,
            self.source_version,
            self.source_version,
            self.status_value,
        )
        return statement, params


@dataclass(frozen=True)
class GdprErasure:
    """SHA-256 hash tombstone plus blanked identifiers, per H07 decision 4."""

    source_kind: SourceKind
    source_id: str
    tombstone_hash: str
    blanked_fields: tuple[str, ...]
    blanked_payload: dict[str, Any]
    purged_at: datetime
    retained_until: datetime
    reason: str
    #: Set when the erasure had no identifying field to blank, so a reader does
    #: not mistake an empty ``blanked_fields`` for an erasure that was skipped.
    no_identifier_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "source_id": self.source_id,
            "tombstone_hash": self.tombstone_hash,
            "blanked_fields": list(self.blanked_fields),
            "purged_at": self.purged_at.isoformat(),
            "retained_until": self.retained_until.isoformat(),
            "reason": self.reason,
            "no_identifier_detail": self.no_identifier_detail,
        }


def gdpr_erasure(
    source_kind: SourceKind,
    raw_document: dict[str, Any],
    *,
    purged_at: datetime,
    reason: str,
    retention: timedelta = GDPR_PURGE_RETENTION,
) -> GdprErasure:
    """Turn a full upstream document into an erasure record.

    The identifying material is exactly what the projection allowlist drops:
    the same boundary that keeps those fields out of the landing tables defines
    what an erasure has to account for. Each dropped field is overwritten with
    an empty string and its value is folded into a SHA-256 tombstone, so an
    auditor can later prove *which* subject was erased without the erased data
    being retained anywhere.

    Physical removal is not performed here. H07 decision 4 keeps the row for 90
    days and hands deletion to a periodic Retention Purge Job; ``retained_until``
    is that job's input.
    """
    projected, report = redact_change_document(source_kind, raw_document)
    payload = json_safe(raw_document or {})
    material = {name: payload.get(name) for name in report.dropped}
    tombstone_hash = hashlib.sha256(
        canonical_json(
            {
                "source_kind": source_kind.value,
                "source_id": source_id_for_document(source_kind, projected),
                "erased_fields": material,
            }
        ).encode("utf-8")
    ).hexdigest()
    blanked = dict(projected)
    blanked.update({name: "" for name in report.dropped})
    moment = purged_at.astimezone(UTC)
    return GdprErasure(
        source_kind=source_kind,
        source_id=source_id_for_document(source_kind, projected),
        tombstone_hash=tombstone_hash,
        blanked_fields=report.dropped,
        blanked_payload=blanked,
        purged_at=moment,
        retained_until=moment + retention,
        reason=reason,
        no_identifier_detail=(
            ""
            if report.dropped
            else (
                f"the {source_kind.value} projection allowlist already excludes every "
                f"direct identifier, so this document carried none to blank; the hash "
                f"tombstone and the 90-day retention handoff are still recorded"
            )
        ),
    )


@dataclass(frozen=True)
class CdcApplyPlan:
    """What one decided change does downstream."""

    envelope: CdcChangeEnvelope
    #: Canonical projection input, for upsert-shaped verbs.
    source_envelope: SourceEnvelope | None
    #: Status mark for a retirement verb, when the table can express one.
    soft_delete: SoftDeleteDirective | None
    #: Audit tombstone recorded in parallel with the soft delete.
    tombstone: DeleteEvent | None
    #: Why no soft delete was planned for a retirement, when that is the case.
    lifecycle_gap: str = ""

    @property
    def retires_entity(self) -> bool:
        return self.tombstone is not None


def plan_change_application(
    envelope: CdcChangeEnvelope,
    *,
    run_id: str,
    now: datetime,
    control_schema: str = "data_plane",
) -> CdcApplyPlan:
    """Turn a decided change into the downstream writes it implies.

    Upsert-shaped verbs reuse ``envelope_for_document`` so a CDC-sourced record
    is byte-identical to the batch-sourced one, down to ``content_sha256`` and
    ``source_snapshot_id``; that is what lets the two paths replay over each
    other without either resurrecting or duplicating anything.

    Retirement verbs follow H07 decision 4's "soft delete and audit tombstone in
    parallel": both are planned, not one or the other. Where the canonical table
    has no lifecycle column, the tombstone is still recorded and the gap is
    named rather than being quietly upgraded to a physical delete.
    """
    _ = control_schema
    policy = cdc_policy(envelope.source_kind)
    if not envelope.retires_entity:
        return CdcApplyPlan(
            envelope=envelope,
            source_envelope=envelope_for_document(
                envelope.source_kind,
                envelope.after_payload,
                run_id=run_id,
                observed_at=envelope.ingested_at,
            ),
            soft_delete=None,
            tombstone=None,
        )
    status = RETIREMENT_STATUS[envelope.operation]
    soft_delete: SoftDeleteDirective | None = None
    if policy.lifecycle_column is not None and envelope.tenant_id is not None:
        soft_delete = SoftDeleteDirective(
            canonical_table=policy.canonical_table,
            canonical_id_column=policy.canonical_id_column,
            status_column=policy.lifecycle_column,
            status_value=status,
            source_kind=envelope.source_kind,
            source_id=envelope.source_id,
            tenant_id=envelope.tenant_id,
            server_timestamp=envelope.server_timestamp,
            source_version=envelope.source_version,
        )
    tombstone = DeleteEvent(
        scope=DeleteScope(
            source_kind=envelope.source_kind,
            source_id=envelope.source_id,
            tenant_id=envelope.tenant_id,
        ),
        source_version=envelope.source_version,
        purged_at=now.astimezone(UTC),
        source_snapshot_id=str(
            envelope_for_document(
                envelope.source_kind,
                envelope.after_payload or {"_id": envelope.source_id},
                run_id=run_id,
                observed_at=envelope.ingested_at,
            ).source_snapshot_id
        ),
        tombstone_hash=envelope.content_sha256,
        run_id=run_id,
        # Soft delete keeps the row, so the tombstone records purge evidence and
        # guards against resurrection rather than removing anything.
        mode=DeletePropagationMode.TOMBSTONE_PURGE,
        context={
            "cdc_operation": envelope.operation.value,
            "cdc_change_id": envelope.change_id,
            "cdc_resume_token": envelope.resume_token,
            "soft_delete_status": status if soft_delete is not None else None,
            "contract_version": envelope.contract_version,
        },
    )
    return CdcApplyPlan(
        envelope=envelope,
        source_envelope=None,
        soft_delete=soft_delete,
        tombstone=tombstone,
        lifecycle_gap="" if soft_delete is not None else (policy.lifecycle_gap or ""),
    )


def stage_statement(
    envelope: CdcChangeEnvelope,
    *,
    control_schema: str,
) -> tuple[str, tuple[Any, ...]]:
    """Return the idempotent insert into the PostgreSQL staging table.

    ``ON CONFLICT (idempotency_key) DO NOTHING`` is what turns the stream's
    at-least-once delivery into an exactly-once landing: a redelivered change
    collapses onto the row already there instead of being re-applied.
    """
    statement = (
        f"INSERT INTO {control_schema}.cdc_staging_events ("  # nosec B608
        f"idempotency_key, change_id, source_kind, source_collection, source_id, "
        f"tenant_id, operation, partition_key, sequence_number, resume_token, "
        f"server_timestamp, source_timestamp, ingested_at, after_payload, "
        f"updated_fields, removed_fields, redaction_profile, redacted_fields, "
        f"content_sha256, contract_version"
        f") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
        f"%s, %s, %s, %s, %s, %s) "
        f"ON CONFLICT (idempotency_key) DO NOTHING"
    )
    params = (
        envelope.idempotency_key,
        envelope.change_id,
        envelope.source_kind.value,
        envelope.source_collection,
        envelope.source_id,
        envelope.tenant_id,
        envelope.operation.value,
        envelope.partition_key,
        envelope.sequence_number,
        envelope.resume_token,
        envelope.server_timestamp,
        envelope.source_timestamp,
        envelope.ingested_at,
        canonical_json(envelope.after_payload),
        list(envelope.updated_fields),
        list(envelope.removed_fields),
        envelope.redaction.profile,
        list(envelope.redaction.dropped),
        envelope.content_sha256,
        envelope.contract_version,
    )
    return statement, params


def checkpoint_upsert_statement(
    checkpoint: CdcCheckpoint,
    *,
    control_schema: str,
) -> tuple[str, tuple[Any, ...]]:
    """Return the checkpoint upsert, guarded so the cursor cannot rewind."""
    statement = (
        f"INSERT INTO {control_schema}.cdc_checkpoints ("  # nosec B608
        f"source_kind, partition_id, resume_token, last_sequence_no, "
        f"last_server_timestamp, processed_count, status, expired_at, "
        f"expiry_detail, recovery_run_id, updated_at"
        f") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) "
        f"ON CONFLICT (source_kind, partition_id) DO UPDATE SET "
        f"resume_token = EXCLUDED.resume_token, "
        f"last_sequence_no = EXCLUDED.last_sequence_no, "
        f"last_server_timestamp = EXCLUDED.last_server_timestamp, "
        f"processed_count = EXCLUDED.processed_count, "
        f"status = EXCLUDED.status, "
        f"expired_at = EXCLUDED.expired_at, "
        f"expiry_detail = EXCLUDED.expiry_detail, "
        f"recovery_run_id = EXCLUDED.recovery_run_id, "
        f"updated_at = CURRENT_TIMESTAMP "
        f"WHERE EXCLUDED.last_sequence_no >= {control_schema}.cdc_checkpoints.last_sequence_no "
        f"OR EXCLUDED.status <> 'ACTIVE'"
    )
    params = (
        checkpoint.source_kind.value,
        checkpoint.partition_id,
        checkpoint.resume_token,
        checkpoint.last_sequence_no,
        checkpoint.last_server_timestamp,
        checkpoint.processed_count,
        checkpoint.status.value,
        checkpoint.expired_at,
        checkpoint.expiry_detail,
        checkpoint.recovery_run_id,
    )
    return statement, params


def checkpoint_select_statement(
    source_kind: SourceKind,
    partition_id: str,
    *,
    control_schema: str,
) -> tuple[str, tuple[Any, ...]]:
    statement = (
        f"SELECT resume_token, last_sequence_no, last_server_timestamp, "  # nosec B608
        f"processed_count, status, expired_at, expiry_detail, recovery_run_id, updated_at "
        f"FROM {control_schema}.cdc_checkpoints "
        f"WHERE source_kind = %s AND partition_id = %s"
    )
    return statement, (source_kind.value, partition_id)


def quarantine_statement(
    envelope: CdcChangeEnvelope | None,
    *,
    reason: CdcRejectReason,
    detail: str,
    run_id: str,
    partition_key: str,
    source_kind: SourceKind,
    source_id: str,
    content_sha256: str,
    control_schema: str,
) -> tuple[str, tuple[Any, ...]]:
    """Return the dead-letter insert for a change the pipeline refuses.

    Poison packets land in the same ``quarantined_records`` table the batch path
    uses, keyed by a content-addressed snapshot id, so one operator query covers
    both paths and a redelivery of the same poison does not pile up rows.
    """
    snapshot_id = str(snapshot_id_for_content(source_kind.value, source_id, content_sha256))
    statement = (
        f"INSERT INTO {control_schema}.quarantined_records ("  # nosec B608
        f"source_snapshot_id, source_kind, source_id, content_sha256, run_id, "
        f"partition_key, reason_code, reason_detail, retryable"
        f") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        f"ON CONFLICT (source_snapshot_id) DO NOTHING"
    )
    params = (
        snapshot_id,
        source_kind.value,
        source_id,
        content_sha256,
        run_id,
        partition_key,
        reason.value,
        detail,
        # A superseded or malformed change is never retryable: replaying it
        # would produce the same verdict against newer state.
        False,
    )
    _ = envelope
    return statement, params


class ChangeStreamFactory(Protocol):
    """Opens a resumable change stream for one scoped collection."""

    def __call__(
        self,
        source_kind: SourceKind,
        *,
        resume_after: str | None,
    ) -> Iterator[dict[str, Any]]: ...


class MongoChangeStreamFactory:
    """Opens a real ``collection.watch()`` against the approved database.

    There is deliberately no offline branch. The 34A entry conditions forbid a
    stub connector that returns sample data to make the pipeline look ready, so
    an absent driver or database handle raises here instead of degrading into
    something that produces events nobody wrote.
    """

    def __init__(self, database: Any, *, batch_size: int = 100) -> None:
        if database is None:
            raise RuntimeError(
                "MongoChangeStreamFactory requires a live authenticated database handle; "
                "it will not fabricate change events"
            )
        self._database = database
        self._batch_size = batch_size

    def __call__(
        self,
        source_kind: SourceKind,
        *,
        resume_after: str | None,
    ) -> Iterator[dict[str, Any]]:
        policy = cdc_policy(source_kind)
        collection = self._database[policy.collection]
        options: dict[str, Any] = {
            # The full document is what the change stream returns regardless;
            # asking for it explicitly makes the widened boundary visible at the
            # call site that redact_change_document then narrows again.
            "full_document": "updateLookup",
            "batch_size": self._batch_size,
        }
        if resume_after:
            options["resume_after"] = {"_data": resume_after}
        try:
            return iter(collection.watch(**options))
        except Exception as exc:  # pragma: no cover - needs a live cluster
            if _is_resume_token_rejection(exc):
                raise OplogCursorExpiredError(
                    f"{policy.collection} rejected the stored resume token: {exc}"
                ) from exc
            raise


#: Server error codes that mean the resume point is gone for good.
_EXPIRED_CURSOR_CODES = frozenset({136, 280, 286})
_EXPIRED_CURSOR_NAMES = ("ChangeStreamHistoryLost", "CappedPositionLost", "CursorNotFound")


def _is_resume_token_rejection(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code in _EXPIRED_CURSOR_CODES:
        return True
    text = str(exc)
    return any(name in text for name in _EXPIRED_CURSOR_NAMES)


@dataclass
class CdcDrainResult:
    """Readback record of one drain tick."""

    source_kind: SourceKind
    partition_id: str
    run_id: str
    staged: int = 0
    applied: int = 0
    duplicates: int = 0
    superseded: int = 0
    quarantined: int = 0
    checkpoint: CdcCheckpoint | None = None
    recovery_plan: SnapshotRecoveryPlan | None = None
    plans: list[CdcApplyPlan] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    quarantine_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def observed(self) -> int:
        return self.applied + self.duplicates + self.superseded + self.quarantined

    @property
    def max_latency_seconds(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    def sla_breaches(self) -> int:
        """Count ticks over the ruling's target, on whatever clock produced them.

        On synthetic events this counts fixture timestamps and says nothing
        about production latency.
        """
        budget = cdc_policy(self.source_kind).latency_sla_seconds
        return sum(1 for value in self.latencies if value > budget)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "partition_id": self.partition_id,
            "run_id": self.run_id,
            "staged": self.staged,
            "applied": self.applied,
            "duplicates": self.duplicates,
            "superseded": self.superseded,
            "quarantined": self.quarantined,
            "observed": self.observed,
            "max_latency_seconds": self.max_latency_seconds,
            "sla_breaches": self.sla_breaches(),
            "quarantine_reasons": dict(self.quarantine_reasons),
            "checkpoint_status": (
                None if self.checkpoint is None else self.checkpoint.status.value
            ),
            "recovery_plan": (
                None if self.recovery_plan is None else self.recovery_plan.as_dict()
            ),
        }


class CdcLandingStore(Protocol):
    """Durable side of the adapter: staging, checkpoints and quarantine."""

    def load_checkpoint(
        self, source_kind: SourceKind, partition_id: str
    ) -> CdcCheckpoint | None: ...

    def commit_tick(
        self,
        *,
        envelopes: Sequence[CdcChangeEnvelope],
        checkpoint: CdcCheckpoint | None,
        quarantines: Sequence[tuple[str, tuple[Any, ...]]],
    ) -> int: ...

    def recorded_state(
        self, source_kind: SourceKind, source_id: str
    ) -> tuple[datetime | None, str | None]: ...


class ScopedCdcAdapter:
    """Drains a scoped change stream into the PostgreSQL staging table.

    The durability boundary is the staging table, not the canonical projection.
    One tick stages the changes it read and moves the checkpoint in the *same*
    transaction, so the cursor can never advance past a change that was not
    landed. Canonical projection then replays from staging under the same
    content-addressed snapshot ids the batch path uses, which makes a crash
    between the two harmless: the change is already durable and re-applying it
    converges.
    """

    def __init__(
        self,
        *,
        store: CdcLandingStore,
        stream_factory: ChangeStreamFactory,
        clock: Any = None,
    ) -> None:
        self._store = store
        self._stream_factory = stream_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def drain(
        self,
        source_kind: SourceKind,
        partition_id: str,
        *,
        limit: int,
        run_id: str,
        authorized_tenants: Iterable[UUID] | None = None,
    ) -> CdcDrainResult:
        """Read up to ``limit`` changes, stage them, and advance the cursor."""
        policy = cdc_policy(source_kind)
        if limit <= 0:
            raise ValueError("drain limit must be positive")
        result = CdcDrainResult(
            source_kind=source_kind, partition_id=partition_id, run_id=run_id
        )
        checkpoint = self._store.load_checkpoint(source_kind, partition_id)
        try:
            resume_after = resume_token_for(checkpoint)
        except OplogCursorExpiredError as exc:
            return self._recover(result, checkpoint, exc)
        sequence = 0 if checkpoint is None else checkpoint.last_sequence_no
        staged: list[CdcChangeEnvelope] = []
        quarantines: list[tuple[str, tuple[Any, ...]]] = []
        # Changes staged earlier in this same tick are not visible to
        # `recorded_state` yet, because the tick commits once at the end. Without
        # this, two copies of one change arriving in a single batch would both be
        # counted as applied and only collapse later on the staging table's
        # conflict clause, making the tick's own readback disagree with the row
        # count it produced.
        in_tick: dict[str, tuple[datetime, str]] = {}
        try:
            stream = self._stream_factory(source_kind, resume_after=resume_after)
            for raw in _bounded(stream, limit):
                sequence += 1
                try:
                    envelope = change_envelope(
                        source_kind,
                        raw,
                        ingested_at=self._clock(),
                        sequence_number=sequence,
                    )
                except SchemaValidationError as exc:
                    self._quarantine(
                        result,
                        quarantines,
                        reason=CdcRejectReason.SCHEMA_VALIDATION_FAILED,
                        detail=str(exc),
                        run_id=run_id,
                        partition_key=partition_id,
                        source_kind=source_kind,
                        raw=raw,
                    )
                    continue
                result.latencies.append(envelope.latency_seconds)
                boundary = check_tenant_binding(
                    envelope, authorized_tenants=authorized_tenants
                )
                if boundary is not None:
                    self._reject(result, quarantines, envelope, boundary, run_id)
                    continue
                recorded_ts, recorded_key = in_tick.get(
                    envelope.source_id
                ) or self._store.recorded_state(source_kind, envelope.source_id)
                decision = decide_change(
                    envelope,
                    recorded_server_timestamp=recorded_ts,
                    recorded_idempotency_key=recorded_key,
                )
                if decision.disposition is ChangeDisposition.DUPLICATE_IGNORED:
                    result.duplicates += 1
                    checkpoint = advance_checkpoint(
                        checkpoint,
                        envelope,
                        source_kind=source_kind,
                        partition_id=partition_id,
                    )
                    continue
                if decision.disposition is ChangeDisposition.SUPERSEDED:
                    self._reject(result, quarantines, envelope, decision, run_id)
                    checkpoint = advance_checkpoint(
                        checkpoint,
                        envelope,
                        source_kind=source_kind,
                        partition_id=partition_id,
                    )
                    continue
                staged.append(envelope)
                in_tick[envelope.source_id] = (
                    envelope.server_timestamp,
                    envelope.idempotency_key,
                )
                result.applied += 1
                result.plans.append(
                    plan_change_application(
                        envelope, run_id=run_id, now=self._clock()
                    )
                )
                checkpoint = advance_checkpoint(
                    checkpoint,
                    envelope,
                    source_kind=source_kind,
                    partition_id=partition_id,
                )
        except OplogCursorExpiredError as exc:
            # Whatever was already read stays durable; the cursor then dies at
            # exactly that point so recovery re-reads from there and no further.
            if staged or quarantines:
                self._store.commit_tick(
                    envelopes=staged, checkpoint=checkpoint, quarantines=quarantines
                )
                result.staged = len(staged)
            return self._recover(result, checkpoint, exc)
        result.staged = self._store.commit_tick(
            envelopes=staged, checkpoint=checkpoint, quarantines=quarantines
        )
        result.checkpoint = checkpoint
        _ = policy
        return result

    def _recover(
        self,
        result: CdcDrainResult,
        checkpoint: CdcCheckpoint | None,
        exc: OplogCursorExpiredError,
    ) -> CdcDrainResult:
        """Expire the cursor and hand the gap to the snapshot batch path."""
        now = self._clock()
        if checkpoint is None:
            # No durable cursor and a dead stream: there is nothing to expire and
            # nothing to bound a recovery window with, so this is not silently
            # converted into a fresh baseline.
            raise exc
        expired = (
            checkpoint
            if checkpoint.status is CheckpointStatus.EXPIRED
            else expire_checkpoint(checkpoint, at=now, detail=str(exc))
        )
        self._store.commit_tick(envelopes=(), checkpoint=expired, quarantines=())
        result.checkpoint = expired
        result.recovery_plan = plan_snapshot_recovery(expired, now=now)
        result.quarantine_reasons[CdcRejectReason.RESUME_TOKEN_EXPIRED.value] = (
            result.quarantine_reasons.get(CdcRejectReason.RESUME_TOKEN_EXPIRED.value, 0) + 1
        )
        return result

    def _reject(
        self,
        result: CdcDrainResult,
        quarantines: list[tuple[str, tuple[Any, ...]]],
        envelope: CdcChangeEnvelope,
        decision: ChangeDecision,
        run_id: str,
    ) -> None:
        reason = decision.reason or CdcRejectReason.SCHEMA_VALIDATION_FAILED
        if reason is CdcRejectReason.SOURCE_SUPERSEDED:
            result.superseded += 1
        else:
            result.quarantined += 1
        result.quarantine_reasons[reason.value] = (
            result.quarantine_reasons.get(reason.value, 0) + 1
        )
        quarantines.append(
            quarantine_statement(
                envelope,
                reason=reason,
                detail=decision.detail,
                run_id=run_id,
                partition_key=envelope.partition_key,
                source_kind=envelope.source_kind,
                source_id=envelope.source_id,
                content_sha256=envelope.content_sha256,
                control_schema=getattr(self._store, "control_schema", "data_plane"),
            )
        )

    def _quarantine(
        self,
        result: CdcDrainResult,
        quarantines: list[tuple[str, tuple[Any, ...]]],
        *,
        reason: CdcRejectReason,
        detail: str,
        run_id: str,
        partition_key: str,
        source_kind: SourceKind,
        raw: Any,
    ) -> None:
        """Isolate a poison packet without stopping the stream."""
        result.quarantined += 1
        result.quarantine_reasons[reason.value] = (
            result.quarantine_reasons.get(reason.value, 0) + 1
        )
        digest = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
        quarantines.append(
            quarantine_statement(
                None,
                reason=reason,
                detail=detail,
                run_id=run_id,
                partition_key=partition_key,
                source_kind=source_kind,
                # A packet this malformed may have no usable id, so the content
                # digest doubles as the identity: two copies of the same poison
                # collapse onto one quarantine row.
                source_id=f"unparsed:{digest[:32]}",
                content_sha256=digest,
                control_schema=getattr(self._store, "control_schema", "data_plane"),
            )
        )


def _bounded(stream: Iterable[dict[str, Any]], limit: int) -> Iterator[dict[str, Any]]:
    """Yield at most ``limit`` packets from a stream that may never end.

    A resident change stream is unbounded by nature, so every tick has to stop
    on its own terms rather than waiting for the producer to finish.
    """
    for index, packet in enumerate(stream):
        if index >= limit:
            return
        yield packet


class PsycopgCdcStore:
    """PostgreSQL landing store for the scoped change streams.

    Deliberately separate from :class:`~apps.data_platform.store.PsycopgCanonicalStore`.
    That class is the canonical projection authority and its transactions are
    scoped to a batch of already-validated envelopes; this one owns the stream's
    own durability boundary, whose unit of atomicity is "everything this tick
    read, plus where the tick stopped". Merging them would either widen the
    canonical transaction to cover an unbounded stream or narrow the stream's
    commit to something that can leave the cursor ahead of the data.
    """

    def __init__(self, config: Any, *, connection_factory: Any | None = None) -> None:
        config.validate()
        self._config = config
        self._connect = connection_factory or self._default_connection_factory()

    def _default_connection_factory(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for CDC landing persistence") from exc
        return lambda: psycopg.connect(self._config.postgres_dsn)

    @property
    def control_schema(self) -> str:
        return self._config.control_schema

    def load_checkpoint(
        self, source_kind: SourceKind, partition_id: str
    ) -> CdcCheckpoint | None:
        statement, params = checkpoint_select_statement(
            source_kind, partition_id, control_schema=self.control_schema
        )
        with self._connect() as connection:
            row = connection.execute(statement, params).fetchone()
        if row is None:
            return None
        return CdcCheckpoint(
            source_kind=source_kind,
            partition_id=partition_id,
            resume_token=str(row[0]),
            last_sequence_no=int(row[1]),
            last_server_timestamp=row[2],
            processed_count=int(row[3]),
            status=CheckpointStatus(str(row[4])),
            expired_at=row[5],
            expiry_detail=str(row[6] or ""),
            recovery_run_id=None if row[7] is None else str(row[7]),
            updated_at=row[8],
        )

    def commit_tick(
        self,
        *,
        envelopes: Sequence[CdcChangeEnvelope],
        checkpoint: CdcCheckpoint | None,
        quarantines: Sequence[tuple[str, tuple[Any, ...]]],
    ) -> int:
        """Stage, quarantine and checkpoint one tick atomically.

        Ordering inside the transaction matters: the checkpoint moves last, so
        a failure anywhere before it leaves the cursor pointing at changes that
        will simply be re-read and collapse on their idempotency keys.
        """
        staged = 0
        with self._connect() as connection:
            for envelope in envelopes:
                statement, params = stage_statement(
                    envelope, control_schema=self.control_schema
                )
                connection.execute(statement, params)
                staged += 1
            for statement, params in quarantines:
                connection.execute(statement, params)
            if checkpoint is not None:
                statement, params = checkpoint_upsert_statement(
                    checkpoint, control_schema=self.control_schema
                )
                connection.execute(statement, params)
        return staged

    def recorded_state(
        self, source_kind: SourceKind, source_id: str
    ) -> tuple[datetime | None, str | None]:
        """Return the newest staged ``(server_timestamp, idempotency_key)``.

        Read from staging rather than from the canonical row because staging is
        where at-least-once redelivery is collapsed; a canonical row that has
        not been replayed yet would make a duplicate look new.
        """
        statement = (
            f"SELECT server_timestamp, idempotency_key "  # nosec B608
            f"FROM {self.control_schema}.cdc_staging_events "
            f"WHERE source_kind = %s AND source_id = %s "
            f"ORDER BY server_timestamp DESC, sequence_number DESC LIMIT 1"
        )
        with self._connect() as connection:
            row = connection.execute(statement, (source_kind.value, source_id)).fetchone()
        if row is None:
            return None, None
        return row[0], str(row[1])

    def open_tick(
        self, run_id: str, source_kind: SourceKind, partition_id: str, *, started_at: datetime
    ) -> None:
        """Record the tick's run row before anything can need to reference it."""
        statement, params = tick_run_statement(
            run_id,
            source_kind,
            partition_id,
            status="RUNNING",
            started_at=started_at,
            control_schema=self.control_schema,
        )
        with self._connect() as connection:
            connection.execute(statement, params)

    def close_tick(
        self,
        run_id: str,
        result: CdcDrainResult,
        *,
        started_at: datetime,
        finished_at: datetime,
        error: BaseException | None = None,
    ) -> None:
        """Close the tick's run row with what the drain actually did."""
        statement, params = tick_run_statement(
            run_id,
            result.source_kind,
            result.partition_id,
            status="FAILED" if error is not None else "SUCCEEDED",
            started_at=started_at,
            processed_count=result.applied,
            final_cursor=None if result.checkpoint is None else result.checkpoint.resume_token,
            finished_at=finished_at,
            error_type=None if error is None else type(error).__name__,
            error_message=None if error is None else str(error),
            control_schema=self.control_schema,
        )
        with self._connect() as connection:
            connection.execute(statement, params)

    def pending_statement(
        self, source_kind: SourceKind, partition_key: str, *, limit: int
    ) -> tuple[str, tuple[Any, ...]]:
        """Return the replay query for rows staged but not yet projected."""
        statement = (
            f"SELECT idempotency_key, source_id, operation, tenant_id, "  # nosec B608
            f"server_timestamp, source_timestamp, ingested_at, after_payload, "
            f"content_sha256, change_id, resume_token, sequence_number "
            f"FROM {self.control_schema}.cdc_staging_events "
            f"WHERE source_kind = %s AND partition_key = %s AND applied_at IS NULL "
            f"ORDER BY sequence_number ASC LIMIT %s"
        )
        return statement, (source_kind.value, partition_key, limit)

    def mark_applied_statement(
        self, idempotency_keys: Sequence[str]
    ) -> tuple[str, tuple[Any, ...]]:
        """Return the statement that closes the replay window for these rows."""
        statement = (
            f"UPDATE {self.control_schema}.cdc_staging_events "  # nosec B608
            f"SET applied_at = CURRENT_TIMESTAMP "
            f"WHERE idempotency_key = ANY(%s) AND applied_at IS NULL"
        )
        return statement, (list(idempotency_keys),)


@dataclass
class CdcProjectionResult:
    """Readback record of one replay from staging into the canonical tables."""

    source_kind: SourceKind
    partition_key: str
    upserted: int = 0
    quarantined: int = 0
    soft_deleted: int = 0
    tombstoned: int = 0
    #: Retirements that could not be expressed as a status mark, with the reason.
    lifecycle_gaps: list[str] = field(default_factory=list)
    delete_outcomes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "partition_key": self.partition_key,
            "upserted": self.upserted,
            "quarantined": self.quarantined,
            "soft_deleted": self.soft_deleted,
            "tombstoned": self.tombstoned,
            "lifecycle_gaps": sorted(set(self.lifecycle_gaps)),
            "delete_outcomes": dict(self.delete_outcomes),
        }


class ScopedCdcProjector:
    """Replays staged changes into the canonical tables.

    Split from :class:`ScopedCdcAdapter` because the two have different failure
    semantics. Draining must never block on a downstream projection error, or a
    single poison entity would stall the cursor and burn oplog window; replaying
    must be re-runnable from scratch, which it is, because every staged row is
    content-addressed and every write it performs is idempotent.
    """

    def __init__(
        self,
        *,
        canonical_store: Any,
        landing_store: Any,
        control_schema: str = "data_plane",
    ) -> None:
        self._canonical = canonical_store
        self._landing = landing_store
        self._control_schema = getattr(landing_store, "control_schema", control_schema)

    def apply(
        self,
        source_kind: SourceKind,
        partition_key: str,
        plans: Sequence[CdcApplyPlan],
    ) -> CdcProjectionResult:
        """Apply one batch of plans, then close their replay window."""
        result = CdcProjectionResult(source_kind=source_kind, partition_key=partition_key)
        upserts = [plan.source_envelope for plan in plans if plan.source_envelope is not None]
        if upserts:
            projection = self._canonical.apply_batch(
                source_kind, tuple(upserts), partition_key=partition_key
            )
            result.upserted = projection.valid_loaded
            result.quarantined = projection.quarantined_count
        for plan in plans:
            if plan.tombstone is None:
                continue
            if plan.soft_delete is not None:
                result.soft_deleted += self._mark(plan.soft_delete)
            elif plan.lifecycle_gap:
                result.lifecycle_gaps.append(plan.lifecycle_gap)
            outcome = self._canonical.tombstone_record(plan.tombstone)
            result.tombstoned += 1
            key = outcome.outcome.value
            result.delete_outcomes[key] = result.delete_outcomes.get(key, 0) + 1
        applied = [plan.envelope.idempotency_key for plan in plans]
        if applied and hasattr(self._landing, "mark_applied_statement"):
            statement, params = self._landing.mark_applied_statement(applied)
            with self._landing._connect() as connection:  # noqa: SLF001
                connection.execute(statement, params)
        return result

    def _mark(self, directive: SoftDeleteDirective) -> int:
        """Run one soft delete and report whether it actually marked a row."""
        statement, params = directive.statement(self._control_schema)
        with self._landing._connect() as connection:  # noqa: SLF001
            cursor = connection.execute(statement, params)
        return int(getattr(cursor, "rowcount", 0) or 0)


def tick_run_statement(
    run_id: str,
    source_kind: SourceKind,
    partition_id: str,
    *,
    status: str,
    started_at: datetime,
    processed_count: int = 0,
    final_cursor: str | None = None,
    finished_at: datetime | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    control_schema: str,
) -> tuple[str, tuple[Any, ...]]:
    """Return the ``ingestion_runs`` upsert for one resident CDC tick.

    A drain tick *is* an ingestion run: it reads ``fongniao_prod`` and lands
    records, and ``quarantined_records.run_id`` references this table, so a
    poison packet has nothing to attach to without it. Two flags stay FALSE on
    purpose and should be read literally rather than as a degraded batch run:
    ``reconciled`` is FALSE because a tick performs no reconciliation, and
    ``partition_complete`` is FALSE because a change stream has no end to reach.
    """
    statement = (
        f"INSERT INTO {control_schema}.ingestion_runs ("  # nosec B608
        f"run_id, source_database, source_kind, partition_key, status, "
        f"processed_count, final_cursor, started_at, finished_at, "
        f"error_type, error_message"
        f") VALUES (%s, 'fongniao_prod', %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        f"ON CONFLICT (run_id) DO UPDATE SET "
        f"status = EXCLUDED.status, "
        f"processed_count = EXCLUDED.processed_count, "
        f"final_cursor = EXCLUDED.final_cursor, "
        f"finished_at = EXCLUDED.finished_at, "
        f"error_type = EXCLUDED.error_type, "
        f"error_message = EXCLUDED.error_message"
    )
    params = (
        run_id,
        source_kind.value,
        partition_id,
        status,
        processed_count,
        final_cursor,
        started_at,
        finished_at,
        error_type,
        error_message,
    )
    return statement, params
