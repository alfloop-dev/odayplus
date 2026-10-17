"""Actual owned SQLite registry/CAS; engineering authority fixture, not UAT.

Original FixedManagementApprovalReceiptVerifier exercises native verification.
It is explicitly a declared test authority, never a human approval claim.
"""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from modules.netplan.application.planning import NetPlanService
from shared.auth import Principal, Role, Scope
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.netplan_outcomes import (
    LABEL_COLLECTION,
    RECEIPT_COLLECTION,
    TASK_COLLECTION,
    DurableNetPlanOutcomeRegistry,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository
from tests.integration.test_netplan_constraint_disclosure_approval import (
    APPROVAL_PRINCIPAL,
    MOMENT,
    RECEIPT_ID,
    SCENARIO_ID,
    TENANT_ID,
    _acknowledge,
    _build,
    _fully_modelled_constraints,
)


@contextmanager
def owner(path, monkeypatch):
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    original, memory = _build(_fully_modelled_constraints())
    _acknowledge(original)
    engine = SqliteEngine(path)
    try:
        documents = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT_ID)
        repository = DurableNetPlanRepository(documents)
        repository.save_scenario(memory.get_scenario(SCENARIO_ID))
        repository.save_solve(memory.get_solve(SCENARIO_ID))
        for row in memory.list_disclosure_acknowledgements(SCENARIO_ID):
            repository.save_disclosure_acknowledgement(row)
        audit, queue = DurableAuditLog(engine), DurableJobQueue(engine)
        principal = Principal(APPROVAL_PRINCIPAL, frozenset({Role.EXECUTIVE}), Scope(tenant_id=TENANT_ID), authenticated=True)
        authorization = {"allowed": True}

        def current_authorize(scenario):
            # Persistence seam control. HTTP/object ABAC remains covered by the
            # original canonical builder whole suites, not claimed here.
            if not authorization["allowed"] or scenario.tenant_id != principal.tenant_id:
                raise HTTPException(403, "CURRENT_OBJECT_SCOPE_DENIED")

        registry = DurableNetPlanOutcomeRegistry(repository=repository, principal=principal,
            authorize=current_authorize, audit_log=audit, queue=queue, correlation_id="actual-owned-registry-command")
        native = NetPlanService(repository=repository, runtime_mode="poc", approval_verifier=original.approval_verifier,
            policy_repository=original.policy_repository, outcome_tracker=registry)
        yield engine, documents, repository, native, registry, audit, authorization
    finally:
        engine.close()


def approve(native):
    return native.decide(SCENARIO_ID, actor_id=APPROVAL_PRINCIPAL,
        reason="Original verified engineering approval", approval_receipt_id=RECEIPT_ID, decided_at=MOMENT)


def test_verified_approval_creates_exact_three_tasks_and_execution_starts_calendar_days(tmp_path, monkeypatch):
    with owner(tmp_path / "registry.sqlite", monkeypatch) as (_, documents, _, native, registry, audit, _):
        assert documents.list_all(TASK_COLLECTION) == []
        approval = approve(native)
        assert approval.authentic_approval_verified
        before = registry.reconcile(SCENARIO_ID)
        assert len(before["tasks"]) == 3
        assert {row["horizon_days"] for row in before["tasks"]} == {90, 180, 365}
        assert all(row["execution_id"] is None and row["due_at"] is None and row["status"] == "PENDING_EXECUTION" for row in before["tasks"])
        # User-confirmed actual execution calendar days, including leap/year
        # rollover, not approval day and not a business-day/SLA calendar.
        actual = datetime(2026, 12, 30, 23, 40, tzinfo=UTC)
        execution = native.execute(SCENARIO_ID, executed_by=APPROVAL_PRINCIPAL, executed_at=actual)
        after = registry.reconcile(SCENARIO_ID)
        assert {row["task_id"] for row in after["tasks"]} == {row["task_id"] for row in before["tasks"]}
        assert all(row["execution_id"] == execution.execution_id and row["executed_at"] == actual.isoformat()
            and row["due_at"] == (actual + timedelta(days=row["horizon_days"])).isoformat() for row in after["tasks"])
        assert all(row["measured_value"] is None and not row["mature"] and not row["training_eligible"] for row in after["tasks"])
        event = audit.get_event(after["receipt"]["audit_event_id"], tenant_id=TENANT_ID)
        assert event.actor == APPROVAL_PRINCIPAL and event.metadata["execution_id"] == execution.execution_id
        assert event.metadata["anchor_rule"] == "actual_execution_calendar_days"


def test_manual_zero_is_one_actual_observed_label_not_three_horizon_measurements(tmp_path, monkeypatch):
    with owner(tmp_path / "zero.sqlite", monkeypatch) as (_, documents, repository, native, registry, _, _):
        approve(native)
        native.execute(SCENARIO_ID, executed_by=APPROVAL_PRINCIPAL, executed_at=MOMENT)
        record = native.record_outcome(SCENARIO_ID, actual_gross_margin=0, actor=APPROVAL_PRINCIPAL,
            observed_at=MOMENT + timedelta(days=1), source_snapshot_ids=["declared-manual-ledger-source"])
        observed = registry.reconcile(SCENARIO_ID)
        assert repository.get_outcome(SCENARIO_ID) == record
        assert len(observed["labels"]) == len(documents.list_all(LABEL_COLLECTION)) == 1
        label = observed["labels"][0]
        assert label["actual_gross_margin"] == 0 and label["outcome_id"] == record.outcome_id
        assert label["native_label_registry_payload"] == record.label_registry_payload
        assert label["source_snapshot_ids"] == ["declared-manual-ledger-source"]
        assert label["measurement_method"] == "manual_recorded" and label["status"] == "OBSERVED"
        assert label["horizon_days"] is None and label["label_maturity_time"] is None
        assert not label["mature"] and not label["training_eligible"] and label["evidence_level"] is None
        assert len(observed["tasks"]) == 3 and all(row["collected_outcome_id"] is None and row["measured_value"] is None for row in observed["tasks"])
        assert observed["automatic_collection"]["available"] is False


def test_registry_reopen_replay_and_actual_stale_CAS_keep_bytes_and_receipts(tmp_path, monkeypatch):
    path = tmp_path / "reopen.sqlite"
    with owner(path, monkeypatch) as (_, documents, _, native, registry, audit, _):
        approve(native)
        recorded = registry.reconcile(SCENARIO_ID)
        events = list(audit.list_events())
        assert registry.reconcile(SCENARIO_ID) == recorded
        assert audit.list_events() == events
        item, version = documents.get_versioned(TASK_COLLECTION, recorded["tasks"][0]["task_id"])
        assert documents.compare_and_put(TASK_COLLECTION, item["task_id"], {**item, "mature": True}, expected_version=version + 1) is None
        assert documents.get_versioned(TASK_COLLECTION, item["task_id"]) == (item, version)
    engine = SqliteEngine(path)
    try:
        scoped = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT_ID)
        assert sorted(scoped.list_all(TASK_COLLECTION), key=lambda row: row["horizon_days"]) == recorded["tasks"]
        assert scoped.get(RECEIPT_COLLECTION, recorded["receipt"]["receipt_id"]) == recorded["receipt"]
        foreign = TenantScopedDocumentStore(SqliteDocumentStore(engine), "another-tenant")
        assert foreign.list_all(TASK_COLLECTION) == [] and foreign.list_all(LABEL_COLLECTION) == []
    finally:
        engine.close()


def test_failed_audit_rolls_back_approval_and_all_three_tracking_rows(tmp_path, monkeypatch):
    with owner(tmp_path / "audit-fail.sqlite", monkeypatch) as (_, documents, repository, native, _, audit, _):
        before = repository.get_scenario(SCENARIO_ID)
        original = audit.record
        def failing(event):
            if event.event_type == "netplan.outcome_tracking_recorded.v1":
                raise RuntimeError("synthetic registry audit failure")
            return original(event)
        monkeypatch.setattr(audit, "record", failing)
        with pytest.raises(RuntimeError, match="synthetic registry audit failure"):
            approve(native)
        assert repository.get_scenario(SCENARIO_ID) == before
        assert repository.list_approvals(SCENARIO_ID) == []
        assert documents.list_all(TASK_COLLECTION) == []
        assert documents.list_all(RECEIPT_COLLECTION) == []


def test_missing_verified_approval_and_current_scope_denial_do_not_backfill_fake_tasks(tmp_path, monkeypatch):
    with owner(tmp_path / "scope.sqlite", monkeypatch) as (_, documents, _, native, registry, _, authorization):
        missing = registry.reconcile(SCENARIO_ID)
        assert missing["state"] == "unavailable" and missing["tasks"] == []
        assert documents.list_all(TASK_COLLECTION) == []
        approve(native)
        before = documents.list_all(TASK_COLLECTION)
        authorization["allowed"] = False
        with pytest.raises(HTTPException) as error:
            registry.reconcile(SCENARIO_ID)
        assert error.value.status_code == 403
        assert documents.list_all(TASK_COLLECTION) == before
