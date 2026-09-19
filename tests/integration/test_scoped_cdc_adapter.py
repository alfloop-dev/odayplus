"""Acceptance suite for the Phase 34B scoped CDC adapter.

Structured against the six dimensions of the 34A implementation handoff
(``docs/evidence/human-decisions/ODP-CDC-SOURCE-CONTRACT-PREP-001/implementation-handoff.md``
§3) plus the two hard constraints the H07 ruling attached to them.

**Scope limit, stated so nothing here is over-read.** Every test in this file is
offline. The change streams are in-test iterables, the landing store is an
in-memory double, and the documents are synthetic. Nothing opens a connection to
``fongniao_prod``, reads the ``odp_cdc_reader`` credential, or touches a live
PostgreSQL cluster. Concretely, that means this suite **cannot** and does not
establish:

* that the upstream cluster is a replica set, or what its oplog window really is
  (H07 decision 2 records 24-48h from a verbal operator confirmation; no
  ``rs.status()`` / ``db.getReplicationInfo()`` output has been read back);
* that the sub-10s end-to-end target, or the handoff's stricter P95 < 5s, holds
  in production — the latency assertions below measure the fixture's own clock
  arithmetic and nothing else;
* that ``odp_cdc_reader`` exists or has been granted ``changeStream``;
* that a real oplog rejects a real expired token the way the fake stream does.

What it does establish is that the decision logic, the redaction boundary, the
ordering guard, the checkpoint lifecycle and the emitted SQL behave as the
ruling and the contract specify. Dimension 5's live telemetry and the whole of
the live-acceptance half of this task remain unverified work.

The test doubles here are deliberate and confined to this file. The 34A entry
conditions forbid shipping a stub connector that returns sample data, and
``apps.data_platform.cdc.MongoChangeStreamFactory`` has no offline branch for
exactly that reason; a double that lives in a test and is named as one does not
make the production path look ready.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from apps.data_platform.cdc import (
    BATCH_ONLY_SOURCE_KINDS,
    CDC_SOURCE_SYSTEM,
    GDPR_PURGE_RETENTION,
    OPLOG_RETENTION_FLOOR,
    SCOPED_CDC_POLICIES,
    CdcChangeEnvelope,
    CdcCheckpoint,
    CdcOperation,
    CdcRejectReason,
    CdcScopeError,
    ChangeDisposition,
    CheckpointStatus,
    OplogCursorExpiredError,
    SchemaValidationError,
    ScopedCdcAdapter,
    advance_checkpoint,
    batch_path_retained,
    cdc_policy,
    change_envelope,
    check_tenant_binding,
    checkpoint_upsert_statement,
    classify_operation,
    decide_change,
    expire_checkpoint,
    gdpr_erasure,
    idempotency_key,
    partition_key_for,
    plan_change_application,
    plan_snapshot_recovery,
    record_recovery,
    redact_change_document,
    resume_token_for,
    stage_statement,
)
from apps.data_platform.contracts import SourceKind
from apps.data_platform.deletion import DeletePropagationMode
from apps.data_platform.identifiers import tenant_id_for_merchant
from apps.data_platform.source import SOURCE_PROJECTIONS, envelope_for_document

CONTROL_SCHEMA = "data_plane"
INGESTED_AT = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
COMMIT_AT = datetime(2026, 9, 17, 11, 59, 58, tzinfo=UTC)
RUN_ID = "00000000-0000-4000-8000-0000000000aa"
MERCHANT = "merchant-1"
OTHER_MERCHANT = "merchant-2"


# --------------------------------------------------------------------------
# Synthetic upstream documents.
#
# The orders document carries fields that are *not* in ORDERS_PROJECTION on
# purpose. A server-side `find` projection would have dropped them before they
# reached this process; a change stream does not, which is the entire reason
# hard constraint (b) exists. Several of them are direct identifiers, so a
# regression in the redaction gate shows up as personal data in an assertion
# rather than as an abstract field-count mismatch.
# --------------------------------------------------------------------------
def _orders_document(
    source_id: str = "order-1",
    *,
    state: str = "TRADE_SUCCESS",
    merchant: str = MERCHANT,
    updated_at: str = "2026-09-17T11:59:00+00:00",
) -> dict[str, Any]:
    return {
        "_id": source_id,
        "id": source_id,
        "orderId": source_id,
        "merchant": {"id": merchant},
        "place": {"id": "place-1"},
        "device": {"id": "device-1"},
        "amount": 100,
        "amountPaid": 100,
        "currency": "TWD",
        "state": state,
        "payment": "card",
        "createdAt": "2026-09-17T11:00:00+00:00",
        "updatedAt": updated_at,
        # Unprojected. Every one of these must be gone after redaction.
        "memberName": "王小明",
        "memberPhone": "0912345678",
        "memberEmail": "member@example.com",
        "cardNumber": "4111111111111111",
        "deliveryAddress": "台北市信義區信義路五段 7 號",
        "internalNotes": {"csAgent": "agent-7"},
    }


def _device_log_document(source_id: str = "log-1") -> dict[str, Any]:
    return {
        "_id": source_id,
        "id": source_id,
        "merchant": {"id": MERCHANT},
        "place": {"id": "place-1"},
        "device": {"id": "device-1"},
        "logType": "machineStatus",
        "logData": {
            "action": "start",
            "errCode": "E00",
            "state": "occupied",
            # Nested and non-allowlisted: minimized by the shared reader path.
            "operatorPhone": "0987654321",
            "rawFrame": {"bytes": "deadbeef"},
        },
        "createdAt": "2026-09-17T11:00:00+00:00",
        "updatedAt": "2026-09-17T11:59:00+00:00",
    }


def _packet(
    document: dict[str, Any] | None,
    *,
    operation_type: str = "insert",
    token: str = "token-1",
    wall_time: datetime = COMMIT_AT,
    declared: str | None = None,
    document_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape one raw change-stream packet the way pymongo yields it."""
    packet: dict[str, Any] = {
        "_id": {"_data": token},
        "operationType": operation_type,
        "ns": {"db": "fongniao_prod", "coll": "orders"},
        "documentKey": document_key
        or {"_id": (document or {}).get("_id", "unknown")},
        "wallTime": wall_time,
    }
    if document is not None:
        packet["fullDocument"] = document
    if declared is not None:
        packet["odpOperation"] = declared
    return packet


class _FakeLandingStore:
    """In-memory stand-in for ``PsycopgCdcStore``.

    Faithful in the two behaviours the adapter's correctness rests on: the
    checkpoint is only visible after ``commit_tick``, and a redelivered
    ``idempotency_key`` collapses onto the row already staged rather than
    inserting a second one.
    """

    control_schema = CONTROL_SCHEMA

    def __init__(self) -> None:
        self.checkpoints: dict[tuple[str, str], CdcCheckpoint] = {}
        self.staged: dict[str, CdcChangeEnvelope] = {}
        self.quarantined: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def load_checkpoint(
        self, source_kind: SourceKind, partition_id: str
    ) -> CdcCheckpoint | None:
        return self.checkpoints.get((source_kind.value, partition_id))

    def commit_tick(self, *, envelopes, checkpoint, quarantines) -> int:
        self.commits += 1
        staged = 0
        for envelope in envelopes:
            if envelope.idempotency_key in self.staged:
                continue
            self.staged[envelope.idempotency_key] = envelope
            staged += 1
        self.quarantined.extend(quarantines)
        if checkpoint is not None:
            self.checkpoints[
                (checkpoint.source_kind.value, checkpoint.partition_id)
            ] = checkpoint
        return staged

    def recorded_state(
        self, source_kind: SourceKind, source_id: str
    ) -> tuple[datetime | None, str | None]:
        rows = [
            envelope
            for envelope in self.staged.values()
            if envelope.source_kind is source_kind and envelope.source_id == source_id
        ]
        if not rows:
            return None, None
        newest = max(rows, key=lambda item: (item.server_timestamp, item.sequence_number))
        return newest.server_timestamp, newest.idempotency_key

    def seed(self, checkpoint: CdcCheckpoint) -> None:
        self.checkpoints[(checkpoint.source_kind.value, checkpoint.partition_id)] = (
            checkpoint
        )


def _stream_of(*packets: dict[str, Any]):
    """Return a factory yielding fixed packets, ignoring the resume point."""

    def factory(source_kind: SourceKind, *, resume_after: str | None):
        factory.resumed_from = resume_after  # type: ignore[attr-defined]
        return iter(packets)

    factory.resumed_from = None  # type: ignore[attr-defined]
    return factory


def _expiring_stream(*packets: dict[str, Any], after: int = 0):
    """Return a factory that yields ``after`` packets, then loses the cursor."""

    def factory(source_kind: SourceKind, *, resume_after: str | None):
        def generate():
            for index, packet in enumerate(packets):
                if index >= after:
                    raise OplogCursorExpiredError("ChangeStreamHistoryLost")
                yield packet
            raise OplogCursorExpiredError("ChangeStreamHistoryLost")

        return generate()

    return factory


def _clock(*moments: datetime):
    """Return a clock that walks the given moments, holding on the last."""
    values = list(moments)

    def tick() -> datetime:
        return values.pop(0) if len(values) > 1 else values[0]

    return tick


def _envelope(
    document: dict[str, Any] | None = None,
    *,
    source_kind: SourceKind = SourceKind.ORDERS,
    operation_type: str = "insert",
    token: str = "token-1",
    wall_time: datetime = COMMIT_AT,
    sequence_number: int = 1,
    ingested_at: datetime = INGESTED_AT,
    declared: str | None = None,
) -> CdcChangeEnvelope:
    payload = _orders_document() if document is None else document
    return change_envelope(
        source_kind,
        _packet(
            payload,
            operation_type=operation_type,
            token=token,
            wall_time=wall_time,
            declared=declared,
        ),
        ingested_at=ingested_at,
        sequence_number=sequence_number,
    )


# ==========================================================================
# H07 decision 1 and hard constraint (a): scope, and the batch path underneath.
# ==========================================================================
def test_only_the_two_ruled_collections_are_in_cdc_scope() -> None:
    assert set(SCOPED_CDC_POLICIES) == {SourceKind.ORDERS, SourceKind.DEVICE_LOG}
    assert len(BATCH_ONLY_SOURCE_KINDS) == 13
    # Named explicitly rather than by count: `member` is the collection whose
    # accidental inclusion would matter most, and `transaction` / `trade` are the
    # two that map to the same canonical table as `orders`, so a scope widened
    # by copy-paste would land there first.
    for kind in (SourceKind.MEMBER, SourceKind.TRANSACTION, SourceKind.TRADE):
        assert kind in BATCH_ONLY_SOURCE_KINDS
        with pytest.raises(CdcScopeError):
            cdc_policy(kind)


def test_enabling_cdc_never_retires_the_snapshot_batch_path() -> None:
    # Hard constraint (a). An expired resume token can only be recovered by a
    # full re-read, so the batch path stays load-bearing for every kind —
    # including the two that gained a change stream.
    for kind in SourceKind:
        assert batch_path_retained(kind) is True


def test_scoped_latency_targets_match_the_ruling() -> None:
    for policy in SCOPED_CDC_POLICIES.values():
        assert policy.latency_sla_seconds == 10


# ==========================================================================
# Hard constraint (b) and dimension 4: in-memory projection and PII masking.
# ==========================================================================
def test_change_stream_payload_is_narrowed_to_the_batch_projection() -> None:
    document = _orders_document()
    projected, report = redact_change_document(SourceKind.ORDERS, document)

    assert set(projected) <= set(SOURCE_PROJECTIONS[SourceKind.ORDERS])
    assert report.profile == "orders_projected_v1"
    for identifier in (
        "memberName",
        "memberPhone",
        "memberEmail",
        "cardNumber",
        "deliveryAddress",
        "internalNotes",
    ):
        assert identifier not in projected
        assert identifier in report.dropped


def test_redaction_reads_the_same_allowlist_the_batch_reader_uses() -> None:
    # The gate must not hold its own copy of the allowlist: a projection change
    # that widened only the batch side would silently widen the CDC boundary too
    # if these were separate literals.
    document = {key: f"value-{key}" for key in SOURCE_PROJECTIONS[SourceKind.ORDERS]}
    document["notInTheAllowlist"] = "leak"
    projected, report = redact_change_document(SourceKind.ORDERS, document)

    assert set(projected) == set(SOURCE_PROJECTIONS[SourceKind.ORDERS])
    assert report.dropped == ("notInTheAllowlist",)


def test_cdc_and_batch_envelopes_are_identical_for_the_same_document() -> None:
    # Byte-for-byte parity is what lets the two paths replay over each other.
    # The batch reader receives the already-projected document from the server;
    # the CDC path receives everything and projects in memory. Both must reach
    # the same content hash and the same content-addressed snapshot id, or a
    # recovery re-read would look like a different record.
    raw = _orders_document()
    server_projected = {
        key: value
        for key, value in raw.items()
        if SOURCE_PROJECTIONS[SourceKind.ORDERS].get(key)
    }
    batch = envelope_for_document(
        SourceKind.ORDERS, server_projected, run_id=RUN_ID, observed_at=INGESTED_AT
    )
    cdc_projected, _ = redact_change_document(SourceKind.ORDERS, raw)
    cdc = envelope_for_document(
        SourceKind.ORDERS, cdc_projected, run_id=RUN_ID, observed_at=INGESTED_AT
    )

    assert cdc.content_sha256 == batch.content_sha256
    assert cdc.source_snapshot_id == batch.source_snapshot_id
    assert cdc.source_document == batch.source_document


def test_device_log_nested_minimization_survives_the_cdc_path() -> None:
    raw = _device_log_document()
    projected, report = redact_change_document(SourceKind.DEVICE_LOG, raw)
    envelope = envelope_for_document(
        SourceKind.DEVICE_LOG, projected, run_id=RUN_ID, observed_at=INGESTED_AT
    )

    assert report.profile == "device_log_minimized_v1"
    log_data = envelope.source_document["logData"]
    assert set(log_data) == {"action", "errCode", "state", "_redacted_fields"}
    assert log_data["_redacted_fields"] == ["operatorPhone", "rawFrame"]
    assert "0987654321" not in json.dumps(envelope.source_document, ensure_ascii=False)


def test_redacted_fields_never_reach_the_staging_statement() -> None:
    envelope = _envelope()
    statement, params = stage_statement(envelope, control_schema=CONTROL_SCHEMA)
    rendered = json.dumps(params, default=str, ensure_ascii=False)

    assert statement.count("%s") == len(params)
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in statement
    for identifier in ("王小明", "0912345678", "member@example.com", "4111111111111111"):
        assert identifier not in rendered
    # The dropped names are recorded so the boundary stays auditable, but only
    # the names — never the values they carried.
    assert "memberPhone" in rendered


def test_tenant_binding_refuses_a_change_outside_the_authorized_partition() -> None:
    mine = _envelope(_orders_document(merchant=MERCHANT))
    theirs = _envelope(_orders_document("order-2", merchant=OTHER_MERCHANT))
    authorized = {tenant_id_for_merchant(MERCHANT)}

    assert check_tenant_binding(mine, authorized_tenants=authorized) is None
    refusal = check_tenant_binding(theirs, authorized_tenants=authorized)
    assert refusal is not None
    assert refusal.reason is CdcRejectReason.TENANT_BOUNDARY_VIOLATION


def test_an_empty_authorization_set_refuses_everything() -> None:
    # `None` means "not constrained here"; an empty set means "nothing is
    # authorised". Collapsing the two would turn a misconfigured partition into
    # an open one.
    envelope = _envelope()
    assert check_tenant_binding(envelope, authorized_tenants=None) is None
    assert check_tenant_binding(envelope, authorized_tenants=set()) is not None


def test_partition_keys_are_derivable_from_the_projected_payload_alone() -> None:
    projected, _ = redact_change_document(SourceKind.ORDERS, _orders_document())
    key = partition_key_for(SourceKind.ORDERS, projected)
    assert key == f"{tenant_id_for_merchant(MERCHANT)}:place-1"

    log_projected, _ = redact_change_document(
        SourceKind.DEVICE_LOG, _device_log_document()
    )
    assert partition_key_for(SourceKind.DEVICE_LOG, log_projected) == "device-1"


# ==========================================================================
# Dimension 1: the seven mutation verbs.
# ==========================================================================
@pytest.mark.parametrize(
    ("operation_type", "state", "expected"),
    [
        ("insert", "TRADE_SUCCESS", CdcOperation.INSERT),
        ("update", "TRADE_SUCCESS", CdcOperation.UPDATE),
        ("replace", "TRADE_SUCCESS", CdcOperation.REPLACE),
        ("delete", "TRADE_SUCCESS", CdcOperation.DELETE),
        ("update", "TRADE_REFUND", CdcOperation.REFUND),
    ],
)
def test_observed_mutation_verbs_map_off_the_packet(
    operation_type: str, state: str, expected: CdcOperation
) -> None:
    payload, _ = redact_change_document(
        SourceKind.ORDERS, _orders_document(state=state)
    )
    assert classify_operation(SourceKind.ORDERS, operation_type, payload) is expected


@pytest.mark.parametrize("declared", ["void", "withdraw", "tombstone"])
def test_declared_verbs_are_honoured_and_never_invented(declared: str) -> None:
    payload, _ = redact_change_document(SourceKind.ORDERS, _orders_document())
    assert (
        classify_operation(
            SourceKind.ORDERS, "update", payload, declared_operation=declared
        )
        is CdcOperation(declared)
    )
    # There is no upstream `orders.state` token for these verbs in the 34A
    # inventory, so an unrecognised state must not be bent into one: it stays an
    # update and the downstream projection quarantines it as UNSUPPORTED_STATUS,
    # exactly as the batch path does for the same document.
    unknown, _ = redact_change_document(
        SourceKind.ORDERS, _orders_document(state="TRADE_SOMETHING_NEW")
    )
    assert classify_operation(SourceKind.ORDERS, "update", unknown) is CdcOperation.UPDATE


def test_a_stream_invalidating_operation_type_is_an_expiry_not_a_verb() -> None:
    payload, _ = redact_change_document(SourceKind.ORDERS, _orders_document())
    for operation_type in ("invalidate", "drop", "dropDatabase"):
        with pytest.raises(OplogCursorExpiredError):
            classify_operation(SourceKind.ORDERS, operation_type, payload)


def test_an_unknown_operation_type_is_a_schema_failure() -> None:
    payload, _ = redact_change_document(SourceKind.ORDERS, _orders_document())
    with pytest.raises(SchemaValidationError):
        classify_operation(SourceKind.ORDERS, "rekey", payload)


# ==========================================================================
# H07 decision 4: soft delete and audit tombstone, in parallel.
# ==========================================================================
def test_an_upstream_order_delete_marks_the_row_and_records_a_tombstone() -> None:
    envelope = _envelope(operation_type="delete")
    plan = plan_change_application(envelope, run_id=RUN_ID, now=INGESTED_AT)

    assert plan.source_envelope is None
    assert plan.soft_delete is not None
    assert plan.soft_delete.canonical_table == "core.transactions"
    assert plan.soft_delete.status_column == "transaction_status"
    assert plan.soft_delete.status_value == "voided"
    # "In parallel" means both, not one or the other.
    assert plan.tombstone is not None
    assert plan.tombstone.mode is DeletePropagationMode.TOMBSTONE_PURGE
    assert plan.tombstone.context["soft_delete_status"] == "voided"


def test_a_refund_maps_onto_the_governed_refunded_status() -> None:
    envelope = _envelope(_orders_document(state="TRADE_REFUND"), operation_type="update")
    plan = plan_change_application(envelope, run_id=RUN_ID, now=INGESTED_AT)

    assert envelope.operation is CdcOperation.REFUND
    assert plan.soft_delete is not None
    assert plan.soft_delete.status_value == "refunded"


def test_an_ordinary_update_projects_and_does_not_retire_anything() -> None:
    plan = plan_change_application(_envelope(), run_id=RUN_ID, now=INGESTED_AT)

    assert plan.source_envelope is not None
    assert plan.soft_delete is None
    assert plan.tombstone is None
    assert plan.retires_entity is False


def test_device_log_retirement_names_its_gap_instead_of_hard_deleting() -> None:
    # `core.machine_status_events` has no record-lifecycle column and adding one
    # is a canonical migration outside this task's owned paths. The honest
    # outcome is a tombstone plus a named gap, not a silent upgrade to a
    # physical delete that the ruling did not authorise.
    envelope = _envelope(
        _device_log_document(), source_kind=SourceKind.DEVICE_LOG, operation_type="delete"
    )
    plan = plan_change_application(envelope, run_id=RUN_ID, now=INGESTED_AT)

    assert plan.soft_delete is None
    assert plan.tombstone is not None
    assert plan.tombstone.mode is DeletePropagationMode.TOMBSTONE_PURGE
    assert "no approved record-lifecycle column" in plan.lifecycle_gap


def test_the_soft_delete_statement_is_tenant_scoped_and_version_guarded() -> None:
    plan = plan_change_application(
        _envelope(operation_type="delete"), run_id=RUN_ID, now=INGESTED_AT
    )
    assert plan.soft_delete is not None
    statement, params = plan.soft_delete.statement(CONTROL_SCHEMA)

    assert statement.count("%s") == len(params)
    assert statement.startswith("UPDATE core.transactions AS target")
    assert "SET transaction_status = %s" in statement
    # Lineage binds the source identity to the canonical row, so the update
    # cannot reach a row this source never landed.
    assert f"{CONTROL_SCHEMA}.canonical_lineage AS lineage" in statement
    # The tenant is re-asserted at the row itself, not only via lineage.
    assert "core.stores AS scope" in statement
    assert "scope.tenant_id = %s" in statement
    # A late-arriving older retirement must be a no-op, not a regression.
    assert "lineage.source_version <= %s::bigint" in statement
    assert "IS DISTINCT FROM %s" in statement


def test_the_soft_delete_join_column_comes_from_the_policy_not_a_literal() -> None:
    # Every scoped policy declares its canonical primary key, and the statement
    # builder uses it. Hardcoding `target.transaction_id` would work today only
    # because core.transactions is the single table with an approved lifecycle
    # column; the moment a second one gains one — which is exactly the
    # carried-forward gap for core.machine_status_events — that literal would
    # emit silently wrong SQL against it.
    for policy in SCOPED_CDC_POLICIES.values():
        assert policy.canonical_id_column

    plan = plan_change_application(
        _envelope(operation_type="delete"), run_id=RUN_ID, now=INGESTED_AT
    )
    assert plan.soft_delete is not None
    statement, _ = plan.soft_delete.statement(CONTROL_SCHEMA)
    orders_policy = cdc_policy(SourceKind.ORDERS)
    assert plan.soft_delete.canonical_id_column == orders_policy.canonical_id_column
    assert (
        f"lineage.canonical_id = target.{orders_policy.canonical_id_column}" in statement
    )

    # The same builder aimed at the other scoped table joins on that table's own
    # key rather than carrying the orders one over.
    log_policy = cdc_policy(SourceKind.DEVICE_LOG)
    retargeted = replace(
        plan.soft_delete,
        canonical_table=log_policy.canonical_table,
        canonical_id_column=log_policy.canonical_id_column,
        status_column="status_type",
    )
    log_statement, _ = retargeted.statement(CONTROL_SCHEMA)
    assert "lineage.canonical_id = target.status_event_id" in log_statement
    assert "transaction_id" not in log_statement


def test_a_delete_without_a_resolvable_tenant_still_records_a_tombstone() -> None:
    # A physical delete carries no `fullDocument`, so there is no merchant to
    # derive a tenant from. The tombstone must still be planned — with the
    # tenant left unresolved for the store's lineage lookup to bind — or an
    # upstream deletion would leave no downstream trace at all.
    packet = _packet(
        None, operation_type="delete", token="token-del", document_key={"_id": "order-9"}
    )
    envelope = change_envelope(
        SourceKind.ORDERS, packet, ingested_at=INGESTED_AT, sequence_number=1
    )
    plan = plan_change_application(envelope, run_id=RUN_ID, now=INGESTED_AT)

    assert envelope.source_id == "order-9"
    assert envelope.tenant_id is None
    assert plan.tombstone is not None
    assert plan.tombstone.scope.tenant_id is None
    assert plan.soft_delete is None


# ==========================================================================
# H07 decision 4: GDPR erasure — hash tombstone, blanked fields, 90-day hold.
# ==========================================================================
def test_gdpr_erasure_blanks_the_identifiers_and_hashes_what_it_erased() -> None:
    erasure = gdpr_erasure(
        SourceKind.ORDERS,
        _orders_document(),
        purged_at=INGESTED_AT,
        reason="GDPR Art.17 request",
    )

    assert "memberPhone" in erasure.blanked_fields
    assert erasure.blanked_payload["memberPhone"] == ""
    assert erasure.blanked_payload["memberEmail"] == ""
    assert len(erasure.tombstone_hash) == 64
    # Physical removal is deferred, not performed: the ruling keeps the row for
    # 90 days and hands deletion to the Retention Purge Job.
    assert erasure.retained_until == INGESTED_AT + GDPR_PURGE_RETENTION
    assert erasure.retained_until - erasure.purged_at == timedelta(days=90)
    assert erasure.no_identifier_detail == ""


def test_the_erasure_hash_distinguishes_subjects_and_is_stable() -> None:
    first = gdpr_erasure(
        SourceKind.ORDERS, _orders_document(), purged_at=INGESTED_AT, reason="r"
    )
    same = gdpr_erasure(
        SourceKind.ORDERS, _orders_document(), purged_at=INGESTED_AT, reason="r"
    )
    other = dict(_orders_document())
    other["memberPhone"] = "0900000000"
    different = gdpr_erasure(
        SourceKind.ORDERS, other, purged_at=INGESTED_AT, reason="r"
    )

    assert first.tombstone_hash == same.tombstone_hash
    assert first.tombstone_hash != different.tombstone_hash
    # The erased values must not survive inside the record that proves the
    # erasure happened.
    assert "0912345678" not in json.dumps(first.as_dict(), ensure_ascii=False)


def test_an_erasure_with_nothing_to_blank_says_so_rather_than_claiming_one() -> None:
    already_minimal = {
        key: "v" for key in SOURCE_PROJECTIONS[SourceKind.ORDERS] if key != "state"
    }
    erasure = gdpr_erasure(
        SourceKind.ORDERS, already_minimal, purged_at=INGESTED_AT, reason="r"
    )

    assert erasure.blanked_fields == ()
    assert "carried none to blank" in erasure.no_identifier_detail


# ==========================================================================
# Dimension 2: duplicates and out-of-order arrival.
# ==========================================================================
def test_the_idempotency_key_matches_the_contract_formula() -> None:
    envelope = _envelope()
    expected = hashlib.sha256(
        ":".join(
            (
                CDC_SOURCE_SYSTEM,
                "orders",
                "order-1",
                datetime(2026, 9, 17, 11, 59, tzinfo=UTC).isoformat(),
            )
        ).encode("utf-8")
    ).hexdigest()

    assert envelope.idempotency_key == expected
    assert envelope.idempotency_key == idempotency_key(
        source_collection="orders",
        document_id="order-1",
        sequence_or_updated_at=datetime(2026, 9, 17, 11, 59, tzinfo=UTC).isoformat(),
    )


def test_a_replayed_change_converges_instead_of_conflicting() -> None:
    envelope = _envelope()
    decision = decide_change(
        envelope,
        recorded_server_timestamp=envelope.server_timestamp,
        recorded_idempotency_key=envelope.idempotency_key,
    )
    assert decision.disposition is ChangeDisposition.DUPLICATE_IGNORED


def test_an_older_change_never_overwrites_newer_recorded_state() -> None:
    envelope = _envelope(wall_time=COMMIT_AT - timedelta(minutes=5))
    decision = decide_change(envelope, recorded_server_timestamp=COMMIT_AT)

    assert decision.disposition is ChangeDisposition.SUPERSEDED
    assert decision.reason is CdcRejectReason.SOURCE_SUPERSEDED


def test_an_equal_server_timestamp_is_applied_per_the_contract_predicate() -> None:
    # The contract's conditional upsert is `EXCLUDED.server_timestamp >=
    # target.server_timestamp`, so equality is inside the window. Tightening it
    # to `>` would drop the second of two changes committed in the same
    # millisecond.
    envelope = _envelope()
    decision = decide_change(envelope, recorded_server_timestamp=envelope.server_timestamp)
    assert decision.applies


def test_out_of_order_replay_reaches_the_same_terminal_state(
    ) -> None:
    ordered = _FakeLandingStore()
    jittered = _FakeLandingStore()
    old = _packet(
        _orders_document(updated_at="2026-09-17T11:50:00+00:00"),
        operation_type="update",
        token="token-old",
        wall_time=COMMIT_AT - timedelta(minutes=9),
    )
    new = _packet(
        _orders_document(updated_at="2026-09-17T11:59:00+00:00"),
        operation_type="update",
        token="token-new",
        wall_time=COMMIT_AT,
    )

    ScopedCdcAdapter(
        store=ordered, stream_factory=_stream_of(old, new), clock=_clock(INGESTED_AT)
    ).drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)
    ScopedCdcAdapter(
        store=jittered, stream_factory=_stream_of(new, old), clock=_clock(INGESTED_AT)
    ).drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    def terminal(store: _FakeLandingStore) -> Any:
        timestamp, _ = store.recorded_state(SourceKind.ORDERS, "order-1")
        return timestamp

    assert terminal(ordered) == terminal(jittered) == COMMIT_AT
    # Arrival order changes which events are staged, never where the entity ends
    # up: the jittered run recognises the late straggler as superseded.
    assert len(jittered.staged) == 1
    assert jittered.quarantined


def test_a_duplicate_inside_one_tick_is_counted_once() -> None:
    store = _FakeLandingStore()
    packet = _packet(_orders_document(), token="token-1")
    adapter = ScopedCdcAdapter(
        store=store, stream_factory=_stream_of(packet, packet), clock=_clock(INGESTED_AT)
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    assert result.applied == 1
    assert result.duplicates == 1
    assert result.staged == 1


# ==========================================================================
# Dimension 3: checkpoint, resume and the authorised snapshot fallback.
# ==========================================================================
def _checkpoint(**overrides: Any) -> CdcCheckpoint:
    base: dict[str, Any] = {
        "source_kind": SourceKind.ORDERS,
        "partition_id": "orders",
        "resume_token": "token-1",
        "last_sequence_no": 7,
        "last_server_timestamp": COMMIT_AT,
        "processed_count": 7,
    }
    base.update(overrides)
    return CdcCheckpoint(**base)


def test_a_live_checkpoint_resumes_from_its_stored_token() -> None:
    assert resume_token_for(_checkpoint()) == "token-1"
    assert resume_token_for(None) is None


def test_an_expired_token_fails_closed_instead_of_restarting_from_now() -> None:
    # This is the whole point of the fail-closed rule: restarting from "now"
    # would lose every change between the dead token and the restart, with
    # nothing recording that it happened.
    expired = expire_checkpoint(_checkpoint(), at=INGESTED_AT, detail="history lost")

    assert expired.status is CheckpointStatus.EXPIRED
    assert expired.resumable is False
    with pytest.raises(OplogCursorExpiredError):
        resume_token_for(expired)
    # The dead token is kept: it is the only record of where the stream stopped.
    assert expired.resume_token == "token-1"
    assert expired.last_server_timestamp == COMMIT_AT


def test_a_restart_resumes_from_the_committed_cursor_without_gaps() -> None:
    store = _FakeLandingStore()
    first = _packet(_orders_document("order-1"), token="token-1")
    second = _packet(_orders_document("order-2"), token="token-2")

    ScopedCdcAdapter(
        store=store, stream_factory=_stream_of(first), clock=_clock(INGESTED_AT)
    ).drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)
    assert store.checkpoints[("orders", "orders")].resume_token == "token-1"

    # A fresh adapter stands in for the restarted sensor daemon: the cursor lives
    # in the store, not in the process.
    resumed = _stream_of(second)
    ScopedCdcAdapter(store=store, stream_factory=resumed, clock=_clock(INGESTED_AT)).drain(
        SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID
    )

    assert resumed.resumed_from == "token-1"
    assert store.checkpoints[("orders", "orders")].resume_token == "token-2"
    assert len(store.staged) == 2


def test_the_cursor_never_rewinds_on_a_late_arriving_event() -> None:
    checkpoint = _checkpoint()
    stale = _envelope(token="token-stale", sequence_number=3)
    advanced = advance_checkpoint(checkpoint, stale)

    assert advanced.resume_token == "token-1"
    assert advanced.last_sequence_no == 7
    # The replay still counts as work performed.
    assert advanced.processed_count == 8


def test_expiry_mid_drain_keeps_what_was_read_and_hands_over_the_gap() -> None:
    store = _FakeLandingStore()
    store.seed(_checkpoint())
    packets = [
        _packet(_orders_document("order-1"), token="token-a"),
        _packet(_orders_document("order-2"), token="token-b"),
    ]
    adapter = ScopedCdcAdapter(
        store=store,
        stream_factory=_expiring_stream(*packets, after=1),
        clock=_clock(INGESTED_AT),
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    # What was already read stays durable; the cursor then dies exactly there.
    assert len(store.staged) == 1
    assert result.checkpoint is not None
    assert result.checkpoint.status is CheckpointStatus.EXPIRED
    assert result.recovery_plan is not None
    assert result.recovery_plan.reason is CdcRejectReason.RESUME_TOKEN_EXPIRED


def test_recovery_re_reads_through_the_existing_batch_path() -> None:
    # Hard constraint (a) made operational: recovery is not a new mechanism, it
    # is the snapshot partition runner the batch path already owns.
    expired = expire_checkpoint(
        _checkpoint(last_server_timestamp=datetime(2026, 9, 15, 6, tzinfo=UTC)),
        at=INGESTED_AT,
        detail="history lost",
    )
    plan = plan_snapshot_recovery(expired, now=INGESTED_AT)

    assert plan.partition_keys[0] == "2026-09-15"
    assert plan.partition_keys[-1] == "2026-09-17"
    assert plan.batch_path.endswith("DataPlaneRunner.run_partition")
    assert plan.decision_ref.startswith("H07")


def test_recovery_covers_at_least_the_oplog_retention_floor() -> None:
    # If the checkpoint is fresher than the retention floor, the window is still
    # widened to the floor. Assuming the 48h ceiling instead could leave a hole,
    # because the recorded window is a verbal range, not a measurement.
    expired = expire_checkpoint(
        _checkpoint(last_server_timestamp=INGESTED_AT - timedelta(minutes=5)),
        at=INGESTED_AT,
        detail="history lost",
    )
    plan = plan_snapshot_recovery(expired, now=INGESTED_AT)
    earliest = datetime.fromisoformat(plan.partition_keys[0]).replace(tzinfo=UTC)

    assert earliest <= INGESTED_AT - OPLOG_RETENTION_FLOOR


def test_a_new_baseline_token_requires_a_completed_recovery_run() -> None:
    expired = expire_checkpoint(_checkpoint(), at=INGESTED_AT, detail="history lost")
    envelope = _envelope(token="token-fresh", sequence_number=9)

    with pytest.raises(OplogCursorExpiredError):
        advance_checkpoint(expired, envelope)

    recovered = record_recovery(expired, run_id=RUN_ID)
    resumed = advance_checkpoint(recovered, envelope)
    assert resumed.status is CheckpointStatus.ACTIVE
    assert resumed.resume_token == "token-fresh"


def test_recovery_is_only_planned_for_an_expired_checkpoint() -> None:
    with pytest.raises(ValueError):
        plan_snapshot_recovery(_checkpoint(), now=INGESTED_AT)


def test_the_checkpoint_upsert_cannot_move_a_live_cursor_backwards() -> None:
    statement, params = checkpoint_upsert_statement(
        _checkpoint(), control_schema=CONTROL_SCHEMA
    )

    assert statement.count("%s") == len(params)
    assert "ON CONFLICT (source_kind, partition_id) DO UPDATE" in statement
    assert (
        f"WHERE EXCLUDED.last_sequence_no >= {CONTROL_SCHEMA}.cdc_checkpoints.last_sequence_no"
        in statement
    )
    # An expiry must land even though it does not move the sequence forward.
    assert "OR EXCLUDED.status <> 'ACTIVE'" in statement


# ==========================================================================
# Dimension 6: poison pills, quarantine and stream survival.
# ==========================================================================
def test_a_poison_packet_is_isolated_without_stopping_the_stream() -> None:
    store = _FakeLandingStore()
    good_before = _packet(_orders_document("order-1"), token="token-a")
    poison = {"_id": {"_data": "token-bad"}, "operationType": "insert"}
    good_after = _packet(_orders_document("order-2"), token="token-c")
    adapter = ScopedCdcAdapter(
        store=store,
        stream_factory=_stream_of(good_before, poison, good_after),
        clock=_clock(INGESTED_AT),
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    assert result.applied == 2
    assert result.quarantined == 1
    assert result.quarantine_reasons == {
        CdcRejectReason.SCHEMA_VALIDATION_FAILED.value: 1
    }
    # The packet after the poison still landed: quarantine isolates, it does not
    # halt.
    assert {envelope.source_id for envelope in store.staged.values()} == {
        "order-1",
        "order-2",
    }


def test_quarantine_rows_are_content_addressed_so_poison_does_not_pile_up() -> None:
    store = _FakeLandingStore()
    poison = {"_id": {"_data": "token-bad"}, "operationType": "insert"}
    adapter = ScopedCdcAdapter(
        store=store,
        stream_factory=_stream_of(poison, poison),
        clock=_clock(INGESTED_AT),
    )

    adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    statements = {statement for statement, _ in store.quarantined}
    snapshot_ids = {params[0] for _, params in store.quarantined}
    assert len(statements) == 1
    assert "ON CONFLICT (source_snapshot_id) DO NOTHING" in statements.pop()
    # Two copies of the same poison resolve to one quarantine identity.
    assert len(snapshot_ids) == 1


def test_a_superseded_change_is_recorded_as_non_retryable() -> None:
    store = _FakeLandingStore()
    newer = _packet(_orders_document(updated_at="2026-09-17T11:59:00+00:00"), token="t2")
    older = _packet(
        _orders_document(updated_at="2026-09-17T11:40:00+00:00"),
        token="t1",
        wall_time=COMMIT_AT - timedelta(minutes=19),
    )
    adapter = ScopedCdcAdapter(
        store=store, stream_factory=_stream_of(newer, older), clock=_clock(INGESTED_AT)
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    assert result.superseded == 1
    assert result.quarantine_reasons[CdcRejectReason.SOURCE_SUPERSEDED.value] == 1
    _, params = store.quarantined[0]
    assert params[-3] == CdcRejectReason.SOURCE_SUPERSEDED.value
    # Replaying it would reach the same verdict against the same newer state.
    assert params[-1] is False


def test_a_drain_stops_at_its_limit_on_an_unbounded_stream() -> None:
    store = _FakeLandingStore()
    packets = [
        _packet(_orders_document(f"order-{index}"), token=f"token-{index}")
        for index in range(10)
    ]
    adapter = ScopedCdcAdapter(
        store=store, stream_factory=_stream_of(*packets), clock=_clock(INGESTED_AT)
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=3, run_id=RUN_ID)

    assert result.applied == 3
    assert len(store.staged) == 3


# ==========================================================================
# Dimension 5: latency accounting. Synthetic clock only — see the module
# docstring. These assertions are about the arithmetic and the budget, and are
# not evidence about production.
# ==========================================================================
def test_capture_latency_is_measured_from_the_server_commit() -> None:
    envelope = _envelope(wall_time=INGESTED_AT - timedelta(seconds=3))
    assert envelope.latency_seconds == pytest.approx(3.0)


def test_a_drain_reports_breaches_against_the_ruling_budget() -> None:
    store = _FakeLandingStore()
    fast = _packet(
        _orders_document("order-1"), token="t1", wall_time=INGESTED_AT - timedelta(seconds=2)
    )
    slow = _packet(
        _orders_document("order-2"), token="t2", wall_time=INGESTED_AT - timedelta(seconds=45)
    )
    adapter = ScopedCdcAdapter(
        store=store, stream_factory=_stream_of(fast, slow), clock=_clock(INGESTED_AT)
    )

    result = adapter.drain(SourceKind.ORDERS, "orders", limit=10, run_id=RUN_ID)

    assert result.sla_breaches() == 1
    assert result.max_latency_seconds == pytest.approx(45.0)
    assert result.as_dict()["sla_breaches"] == 1


# ==========================================================================
# Persistence contract: the DDL the adapter's statements assume.
# ==========================================================================
def _control_schema_sql() -> str:
    return (
        Path(__file__)
        .parents[2]
        .joinpath("apps", "data_platform", "sql", "control_schema.sql")
        .read_text(encoding="utf-8")
    )


def test_the_cdc_tables_exist_with_their_guard_constraints() -> None:
    schema = _control_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS {{control_schema}}.cdc_checkpoints" in schema
    assert "status IN ('ACTIVE', 'STALE', 'EXPIRED', 'PAUSED')" in schema
    assert "CHECK ((status = 'EXPIRED') = (expired_at IS NOT NULL))" in schema
    assert "CHECK (recovery_run_id IS NULL OR status = 'EXPIRED')" in schema

    assert "CREATE TABLE IF NOT EXISTS {{control_schema}}.cdc_staging_events" in schema
    assert "idempotency_key TEXT PRIMARY KEY" in schema
    # The staging table is scoped to the two ruled collections at the schema
    # level, so widening the CDC scope cannot happen by code change alone.
    assert "source_kind TEXT NOT NULL CHECK (source_kind IN ('orders', 'device_log'))" in schema
    assert "redaction_profile TEXT NOT NULL CHECK (" in schema
    assert "applied_at TIMESTAMPTZ," in schema


def test_the_staging_table_holds_no_foreign_key_that_could_drop_events() -> None:
    schema = _control_schema_sql()
    staging = schema.split("cdc_staging_events", 1)[1].split("CREATE INDEX", 1)[0]
    # A tenant FK would reject a change whose merchant has not been projected
    # yet, turning an ordering artefact into data loss.
    assert "tenant_id UUID," in staging
    assert "REFERENCES core.tenants" not in staging


# ==========================================================================
# H07 decision 5: the Dagster-resident wiring, with no message broker.
# ==========================================================================
def test_the_resident_sensors_are_registered_and_default_to_stopped() -> None:
    from dagster import DefaultSensorStatus

    from apps.data_platform import definitions as module

    names = {sensor.name for sensor in module.defs.sensors}
    assert {"scoped_cdc_orders_sensor", "scoped_cdc_device_log_sensor"} <= names

    for sensor in module.SCOPED_CDC_SENSORS:
        # Starting one reads fongniao_prod through the odp_cdc_reader
        # credential, which is an operator action, not a deployment side effect.
        assert sensor.default_status is DefaultSensorStatus.STOPPED
        assert sensor.minimum_interval_seconds < cdc_policy(
            SourceKind.ORDERS
        ).latency_sla_seconds


def test_no_message_broker_dependency_was_introduced() -> None:
    # H07 decision 5 fixed the transport as a Dagster-resident sensor writing
    # straight to PostgreSQL. Asserted over the import graph rather than over the
    # file's text, so the prose that names the rejected brokers does not satisfy
    # or break its own check.
    brokers = {
        "kafka",
        "confluent_kafka",
        "aiokafka",
        "redpanda",
        "pika",
        "google.cloud.pubsub_v1",
        "pulsar",
        "nats",
    }
    root = Path(__file__).parents[2].joinpath("apps", "data_platform")
    for module_path in (root / "cdc.py", root / "definitions.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in imported:
            root_package = name.split(".")[0]
            assert name not in brokers, f"{module_path.name} imports {name}"
            assert root_package not in brokers, f"{module_path.name} imports {name}"


def test_the_change_stream_factory_refuses_to_fabricate_events() -> None:
    from apps.data_platform.cdc import MongoChangeStreamFactory

    # The 34A entry conditions forbid an empty connector that returns sample
    # data to make the pipeline look production-ready.
    with pytest.raises(RuntimeError):
        MongoChangeStreamFactory(None)


def test_the_envelope_readback_carries_the_contract_fields() -> None:
    payload = _envelope().as_dict()

    assert payload["source_system"] == "fongniao_mongo"
    assert payload["source_database"] == "fongniao_prod"
    assert payload["source_collection_or_table"] == "orders"
    assert payload["document_key"] == {"_id": "order-1"}
    assert payload["redaction_profile"] == "orders_projected_v1"
    assert UUID(payload["tenant_id"]) == tenant_id_for_merchant(MERCHANT)
    assert payload["contract_version"] == "1.0.0-draft.34a"
