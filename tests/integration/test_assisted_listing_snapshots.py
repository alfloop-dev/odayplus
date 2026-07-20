from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from modules.external_data.application.source_snapshots import (
    SourcePolicyViolation,
    SourceSnapshotService,
)
from modules.listing.application.intake_workflow import (
    InMemoryIntakeRepository,
    IntakeWorkflowService,
)
from modules.listing.domain.intake_states import (
    Actor,
    IntakeStage,
    PrincipalRole,
    TransitionContext,
)
from shared.infrastructure.object_store.client import InMemoryObjectStore


@pytest.fixture
def memory_store():
    return InMemoryObjectStore()


@pytest.fixture
def workflow_service():
    repo = InMemoryIntakeRepository()
    return IntakeWorkflowService(repo)


@pytest.fixture
def base_context():
    actor = Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="00000000-0000-0000-0000-000000000001")
    return TransitionContext(actor=actor, correlation_id="corr-123", idempotency_key="idem-123")


def test_source_policy_gates(memory_store, workflow_service) -> None:
    service = SourceSnapshotService(db_conn=None, object_store=memory_store, intake_workflow_service=workflow_service)

    tenant_id = "00000000-0000-0000-0000-000000000001"

    # 1. Unknown source resolves to POLICY_UNKNOWN
    policy = service.check_source_policy(tenant_id, "unknown-source")
    assert policy == "POLICY_UNKNOWN"

    # 2. Blocked source resolves to SOURCE_BLOCKED
    service.register_source(
        source_id="blocked-src",
        display_name="Blocked Source",
        allowed_hosts=["blocked.com"],
        retrieval_mode="APPROVED_RETRIEVAL",
        kill_switch=True,  # Blocked!
    )
    policy = service.check_source_policy(tenant_id, "blocked-src")
    assert policy == "SOURCE_BLOCKED"

    # 3. Approved source
    service.register_source(
        source_id="ok-src",
        display_name="OK Source",
        allowed_hosts=["ok.com"],
        retrieval_mode="APPROVED_RETRIEVAL",
        kill_switch=False,
    )
    policy = service.check_source_policy(tenant_id, "ok-src")
    assert policy == "APPROVED_RETRIEVAL"


def test_create_snapshot_success(memory_store, workflow_service, base_context) -> None:
    service = SourceSnapshotService(db_conn=None, object_store=memory_store, intake_workflow_service=workflow_service)
    
    tenant_id = "00000000-0000-0000-0000-000000000001"
    intake_id = "IN-123"
    source_id = "src-591"

    service.register_source(
        source_id=source_id,
        display_name="591",
        allowed_hosts=["591.com.tw"],
        retrieval_mode="APPROVED_RETRIEVAL",
    )

    # Pre-populate workflow repository and advance to CHECKING_SOURCE_POLICY
    workflow_service.submit_intake(
        intake_id=intake_id,
        tenant_id=tenant_id,
        source_id=source_id,
        canonical_url="https://591.com.tw/listing-123",
        context=base_context,
    )
    workflow_service.start_identity_check(intake_id, base_context)
    workflow_service.start_source_policy_evaluation(intake_id, base_context)

    raw_data = b"raw html listing data"
    redacted_data = b"redacted html listing data"

    snapshot_id = service.create_snapshot(
        tenant_id=tenant_id,
        intake_id=intake_id,
        source_id=source_id,
        raw_data=raw_data,
        original_url="https://591.com.tw/listing-123?utm=123",
        canonical_url="https://591.com.tw/listing-123",
        media_type="text/html",
        capture_method="SERVER_RETRIEVAL",
        retention_class="STANDARD",
        encryption_key_ref="kms://key-1",
        observed_at=datetime.now(UTC),
        captured_at=datetime.now(UTC),
        bucket="taiwan-snapshots",
        redacted_data=redacted_data,
        context=base_context,
    )

    assert snapshot_id is not None

    # Check GCS contents
    raw_content = memory_store.download_object(tenant_id, f"gs://taiwan-snapshots/snapshots/{snapshot_id}/raw")
    assert raw_content == raw_data

    redacted_content = memory_store.download_object(tenant_id, f"gs://taiwan-snapshots/snapshots/{snapshot_id}/redacted")
    assert redacted_content == redacted_data


def test_create_snapshot_policy_fail(memory_store, workflow_service, base_context) -> None:
    service = SourceSnapshotService(db_conn=None, object_store=memory_store, intake_workflow_service=workflow_service)
    
    tenant_id = "00000000-0000-0000-0000-000000000001"
    intake_id = "IN-123"
    source_id = "blocked-src"

    service.register_source(
        source_id=source_id,
        display_name="Blocked",
        allowed_hosts=["blocked.com"],
        retrieval_mode="APPROVED_RETRIEVAL",
        kill_switch=True,
    )

    # Pre-populate workflow repository and advance to CHECKING_SOURCE_POLICY
    workflow_service.submit_intake(
        intake_id=intake_id,
        tenant_id=tenant_id,
        source_id=source_id,
        canonical_url="https://blocked.com/listing",
        context=base_context,
    )
    workflow_service.start_identity_check(intake_id, base_context)
    workflow_service.start_source_policy_evaluation(intake_id, base_context)

    # Evaluate policy violation -> raises exception and quarantines intake
    with pytest.raises(SourcePolicyViolation) as exc:
        service.create_snapshot(
            tenant_id=tenant_id,
            intake_id=intake_id,
            source_id=source_id,
            raw_data=b"html",
            original_url="https://blocked.com/listing",
            canonical_url="https://blocked.com/listing",
            media_type="text/html",
            capture_method="SERVER_RETRIEVAL",
            retention_class="STANDARD",
            encryption_key_ref="kms://key-1",
            observed_at=datetime.now(UTC),
            captured_at=datetime.now(UTC),
            bucket="taiwan-snapshots",
            context=base_context,
        )
    assert exc.value.policy == "SOURCE_BLOCKED"

    # Intake should be quarantined
    intake = workflow_service.repository.get_by_id(intake_id)
    assert intake.stage == IntakeStage.QUARANTINED
    assert intake.evidence.get("quarantine_reason") == "SOURCE_BLOCKED"


def test_reconciliation_missing_object(memory_store, workflow_service, base_context) -> None:
    service = SourceSnapshotService(db_conn=None, object_store=memory_store, intake_workflow_service=workflow_service)
    
    tenant_id = "00000000-0000-0000-0000-000000000001"
    intake_id = "IN-456"
    source_id = "src-591"

    service.register_source(
        source_id=source_id,
        display_name="591",
        allowed_hosts=["591.com.tw"],
        retrieval_mode="APPROVED_RETRIEVAL",
    )

    # Pre-populate workflow repository and advance to RETRIEVING
    workflow_service.submit_intake(
        intake_id=intake_id,
        tenant_id=tenant_id,
        source_id=source_id,
        canonical_url="https://591.com.tw/listing-456",
        context=base_context,
    )
    workflow_service.start_identity_check(intake_id, base_context)
    workflow_service.start_source_policy_evaluation(intake_id, base_context)
    workflow_service.approve_retrieval(intake_id, "APPROVED_RETRIEVAL", base_context)

    snapshot_id = service.create_snapshot(
        tenant_id=tenant_id,
        intake_id=intake_id,
        source_id=source_id,
        raw_data=b"ok html",
        original_url="https://591.com.tw/listing-456",
        canonical_url="https://591.com.tw/listing-456",
        media_type="text/html",
        capture_method="SERVER_RETRIEVAL",
        retention_class="STANDARD",
        encryption_key_ref="kms://key-1",
        observed_at=datetime.now(UTC),
        captured_at=datetime.now(UTC),
        bucket="taiwan-snapshots",
        context=base_context,
    )

    # Delete raw object directly from GCS to simulate discrepancy
    uri = f"gs://taiwan-snapshots/snapshots/{snapshot_id}/raw"
    memory_store.delete_object(tenant_id, uri)

    # Run integrity check -> fails closed and quarantines intake
    retrieval_actor = Actor(actor_id="sys", role=PrincipalRole.SVC_RETRIEVAL, tenant_id=tenant_id)
    retrieval_context = TransitionContext(actor=retrieval_actor, correlation_id="corr-123", idempotency_key="idem-456")
    ok = service.verify_snapshot_integrity(tenant_id, snapshot_id, retrieval_context)
    assert not ok

    intake = workflow_service.repository.get_by_id(intake_id)
    assert intake.stage == IntakeStage.QUARANTINED
    assert intake.evidence.get("quarantine_reason") == "INTEGRITY_FAILED"

    # Verify reconciliation findings recorded
    assert len(service._in_memory_findings) == 1
    finding = list(service._in_memory_findings.values())[0]
    assert finding["finding_type"] == "MISSING_EVIDENCE"
    assert finding["source_id"] == snapshot_id


def test_reconciliation_orphan_object(memory_store, workflow_service) -> None:
    service = SourceSnapshotService(db_conn=None, object_store=memory_store, intake_workflow_service=workflow_service)
    
    tenant_id = "00000000-0000-0000-0000-000000000001"
    bucket = "taiwan-snapshots"

    # Directly upload to GCS (without SQL metadata entry) to simulate orphan
    memory_store.upload_object(
        tenant_id=tenant_id,
        bucket=bucket,
        key="snapshots/orphan-snap-999/raw",
        data=b"orphan html",
        content_type="text/html",
        if_generation_match=0,
    )

    # Run reconciler
    res = service.reconcile_snapshots(tenant_id, bucket)
    assert res["orphans"] == 1

    # Finding should be created
    assert len(service._in_memory_findings) == 1
    finding = list(service._in_memory_findings.values())[0]
    assert finding["finding_type"] == "ORPHAN_REFERENCE"
    assert finding["source_id"] == f"gs://{bucket}/snapshots/orphan-snap-999/raw"


@pytest.mark.requires_live_env
def test_postgres_snapshot_integration(intake_db) -> None:
    """Exercise snapshot storage and query paths on real PostgreSQL 16."""
    from shared.infrastructure.object_store.client import InMemoryObjectStore

    store = InMemoryObjectStore()
    with intake_db.connect(autocommit=True) as conn:
        service = SourceSnapshotService(db_conn=conn, object_store=store)

        tenant_id = "00000000-0000-0000-0000-000000000001"
        intake_id = "00000000-0000-0000-0000-000000000002"
        source_id = "src-pg-1"

        # Pre-populate registry and intake table to satisfy foreign keys
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute(
            """
            INSERT INTO intake.source_registry (
                source_id, display_name, allowed_hosts, canonicalization_rule_version,
                retrieval_mode, policy_owner_subject_id, kill_switch, production_enabled
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (source_id, "PG Test Source", ["pg.com"], "v1.0", "APPROVED_RETRIEVAL", "00000000-0000-0000-0000-000000000000", False, True)
        )
        cur.execute(
            """
            INSERT INTO intake.intakes (
                intake_id, tenant_id, source_id, canonical_url, original_url, processing_state, version,
                submitter_subject_id, intake_method, correlation_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                intake_id, tenant_id, source_id, "https://pg.com/list", "https://pg.com/list", "SUBMITTED", 1,
                "00000000-0000-0000-0000-000000000000", "URL", "00000000-0000-0000-0000-000000000000"
            )
        )

        # 1. Create snapshot successfully
        snapshot_id = service.create_snapshot(
            tenant_id=tenant_id,
            intake_id=intake_id,
            source_id=source_id,
            raw_data=b"pg raw html data",
            original_url="https://pg.com/list?utm=123",
            canonical_url="https://pg.com/list",
            media_type="text/html",
            capture_method="SERVER_RETRIEVAL",
            retention_class="STANDARD",
            encryption_key_ref="kms://key-pg",
            observed_at=datetime.now(UTC),
            captured_at=datetime.now(UTC),
            bucket="taiwan-snapshots",
        )

        assert snapshot_id is not None

        # Verify query works and metadata is saved correctly in Postgres
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute(
            "SELECT byte_length, content_sha256 FROM intake.source_snapshots WHERE source_snapshot_id = %s",
            (snapshot_id,)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == len(b"pg raw html data")
        assert row[1].strip() == hashlib.sha256(b"pg raw html data").hexdigest()
