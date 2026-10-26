"""Signed HTTP/real SQLite; original declared synthetic management authority.

These controls execute the scoped native approval and tracking integration.
They do not establish human approval, physical execution, POS measurements or
model production execution. Original receipt/disclosure policies remain real.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.netplan_builder import create_netplan_builder_router
from apps.api.oday_api.security.dependencies import build_engine, require_operator_permission
from modules.netplan.application.planning import NetPlanService
from modules.opsboard.application.netplan_builder import CanonicalNetPlanBuilder
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.auth import Action
from shared.governance.decision_policy import InMemoryDecisionPolicyRepository
from shared.governance.netplan_disclosure import default_netplan_disclosure_policy
from shared.infrastructure.persistence.netplan_outcomes import (
    LABEL_COLLECTION,
    RECEIPT_COLLECTION,
    TASK_COLLECTION,
)
from tests.integration.test_netplan_solver import (
    _approval_receipt,
    _approval_verifier,
    _management_baseline,
)
from tests.integration.test_operator_netplan_scenario_builder import (
    BASE,
    STORE,
    TENANT,
    _business,
    _case,
)
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers


@contextmanager
def signed_pending(path, monkeypatch):
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    with _case(path) as case:
        original = case.repository.get_scenario(case.scenario_id)
        constraints = replace(original.constraints, max_equipment_units=1000, max_labour_headcount=1000,
                              min_coverage_delta=0, max_open_per_dilution_zone=2)
        options = {identity: tuple(replace(option, equipment_units=0, labour_headcount=0,
                                         coverage_delta=0) for option in values)
                   for identity, values in original.options_by_entity.items()}
        case.repository.save_scenario(replace(original, constraints=constraints, options_by_entity=options))
        policy = InMemoryDecisionPolicyRepository([replace(default_netplan_disclosure_policy(TENANT),
            effective_from=datetime(2020, 1, 1, tzinfo=UTC))])
        native = NetPlanService(repository=case.repository, policy_repository=policy, runtime_mode="poc",
            feature_flags=case.bundle.feature_flag_registry, job_queue=case.bundle.job_queue)
        solve = native.solve(case.scenario_id)
        baseline = _management_baseline(scenario_id=case.scenario_id, baseline_id=case.scenario_id,
            baseline_name=original.scenario_name, scope="tenant:" + TENANT, release_id=original.planning_horizon,
            actions_by_entity={row.entity_id: row.action for row in solve.result.selected_actions},
            source_snapshot_ids=tuple(sorted({ref for row in solve.result.selected_actions for ref in row.source_snapshot_ids})))
        receipt = _approval_receipt(baseline, options, constraints,
            principal_id="planner", issued_at="2026-10-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z")
        verifier = _approval_verifier(receipt, principal_id="planner", clock=datetime.now(UTC))
        native.approval_verifier = verifier
        native.acknowledge_unmodelled_constraints(case.scenario_id, actor_id="planner",
            reason="Declared synthetic lease and sequencing authority", acknowledged_classes=("LEASE", "SEQUENCING"),
            approval_receipt_id=receipt.receipt_id)
        native.submit_for_approval(case.scenario_id, actor="planner", reason="Declared native scenario submission")
        case.rebalance_factory.approval_verifier = verifier
        case.rebalance_factory.policy_repository = policy
        case.mappings["other-reviewer"] = {"roles": ["executive"], "scope": {"tenant_id": TENANT}}
        boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer=ISSUER,
            audiences=frozenset({"unit-feature-api"}), signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings=case.mappings), audit_log=case.bundle.audit_log)
        app = FastAPI()
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", "tracking-server-cid")
            return await call_next(request)

        def factory(request):
            principal = request.state.operator_principal
            repository = (case.repository if principal.tenant_id == TENANT else
                          case.bundle._scoped_repository("netplan_repository", principal.tenant_id))
            return CanonicalNetPlanBuilder(principal=principal, scenario_repository=repository,
                state_repository=case.state, rebalance_factory=case.rebalance_factory,
                store_repository=case.bundle.store_repository, brand_repository=case.bundle.brand_repository,
                listing_snapshot_reader=lambda: {"listings": [], "candidates": []},
                audit_log=case.bundle.audit_log, correlation_id=request.state.correlation_id,
                role_id=request.state.operator_role_id, job_queue=case.bundle.job_queue,
                policy_repository=policy, feature_flags=case.bundle.feature_flag_registry)

        engine = build_engine(audit_log=case.bundle.audit_log)
        def guard(action, **kwargs):
            return require_operator_permission("netplan", action, engine=engine, boundary=boundary, **kwargs)
        app.include_router(create_netplan_builder_router(factory=factory,
            require_view=guard(Action.VIEW, scoped_read_resource="operator_network"),
            require_edit=guard(Action.CREATE), require_solve=guard(Action.EXECUTE),
            require_decide=guard(Action.APPROVE)), prefix="/operator")
        with TestClient(app, raise_server_exceptions=False) as client:
            case.client = client
            yield case, receipt


def current(case):
    response = case.client.get(BASE, headers=_headers("planner"))
    assert response.status_code == 200, response.text
    return response.json()


def decide(case, value, receipt, *, actor="planner", key="verified-native-approval", body_extra=None, **changes):
    return case.client.post(BASE + "/decide", headers={**_headers(actor), "Idempotency-Key": key},
        json={"scenarioId": value["scenarioId"], "expectedProblemHash": value["problemHash"],
              "reason": "Original receipt verified by current signed principal",
              "nativeDecision": {"decision": "approved", "approvalReceiptId": receipt.receipt_id}, **changes, **(body_extra or {})})


def test_signed_native_approval_creates_three_pending_then_actual_execution_anchors_without_rebuild(tmp_path, monkeypatch):
    with signed_pending(tmp_path / "approval.sqlite", monkeypatch) as (case, receipt):
        value = current(case)
        response = decide(case, value, receipt)
        assert response.status_code == 200, response.text
        result = response.json()
        approval = case.repository.list_approvals(case.scenario_id)[0]
        assert approval.authentic_approval_verified and approval.actor_id == "planner"
        tracking = result["builder"]["outcomeTracking"]
        assert len(tracking["tasks"]) == 3
        assert {row["horizon_days"] for row in tracking["tasks"]} == {90, 180, 365}
        assert all(row["status"] == "PENDING_EXECUTION" and row["due_at"] is None
                   and row["approval_id"] == approval.approval_id for row in tracking["tasks"])
        after = _business(case)
        replay = decide(case, value, receipt)
        assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
        assert replay.json()["audit"] == result["audit"] and _business(case) == after
        assert replay.json()["builder"]["outcomeTracking"] == tracking
        executed = case.client.post(BASE + "/execute", headers={**_headers("planner"), "Idempotency-Key": "actual-execution"},
            json={"scenarioId": case.scenario_id, "expectedProblemHash": value["problemHash"],
                  "reason": "Record original native execution"})
        assert executed.status_code == 200, executed.text
        actual = case.repository.get_execution(case.scenario_id)
        anchored = executed.json()["builder"]["outcomeTracking"]
        assert {row["task_id"] for row in anchored["tasks"]} == {row["task_id"] for row in tracking["tasks"]}
        assert all(row["executed_at"] == actual.executed_at.isoformat()
                   and row["due_at"] == (actual.executed_at + timedelta(days=row["horizon_days"])).isoformat()
                   and row["measured_value"] is None and not row["mature"] for row in anchored["tasks"])
        event = case.bundle.audit_log.get_event(anchored["receipt"]["audit_event_id"], tenant_id=TENANT)
        assert event.actor == "planner" and event.metadata["execution_id"] == actual.execution_id


@pytest.mark.parametrize("extra", [{"actor_id": "forged"}, {"actor": "planner"}, {"decided_at": "2000-01-01"}])
def test_body_actor_and_clock_never_authorize_scoped_native_approval(tmp_path, monkeypatch, extra):
    with signed_pending(tmp_path / "body.sqlite", monkeypatch) as (case, receipt):
        value = current(case)
        before = _business(case)
        response = decide(case, value, receipt, body_extra=extra)
        assert response.status_code == 422 and _business(case) == before
        assert case.repository.list_approvals(case.scenario_id) == []


@pytest.mark.parametrize("actor", ["viewer", "foreign"])
def test_current_role_and_tenant_denials_cannot_create_tracking_or_native_approval(tmp_path, monkeypatch, actor):
    with signed_pending(tmp_path / "denied.sqlite", monkeypatch) as (case, receipt):
        value = current(case)
        before = _business(case)
        response = decide(case, value, receipt, actor=actor)
        assert response.status_code in {403, 404} and _business(case) == before
        assert case.repository.list_approvals(case.scenario_id) == []


def test_wrong_receipt_stale_hash_and_replay_scope_revocation_leave_business_unchanged(tmp_path, monkeypatch):
    with signed_pending(tmp_path / "pins.sqlite", monkeypatch) as (case, receipt):
        value = current(case)
        before = _business(case)
        assert decide(case, value, replace(receipt, receipt_id="unknown-authority")).status_code == 409
        assert decide(case, value, receipt, actor="other-reviewer").status_code == 409
        assert decide(case, {**value, "problemHash": "0" * 64}, receipt).status_code == 409
        assert _business(case) == before
        response = decide(case, value, receipt)
        assert response.status_code == 200, response.text
        conflicting = decide(case, value, receipt, reason="Another command body with the same receipt key")
        assert conflicting.status_code == 409
        old = case.bundle.store_repository.get_store(STORE)
        case.bundle.store_repository.save_store(replace(old, region_code="south"))
        case.mappings["planner"]["scope"]["region_ids"] = ["north"]
        before = _business(case)
        assert decide(case, value, receipt).status_code == 403 and _business(case) == before


def test_tracking_audit_failure_rolls_back_real_native_approval_and_same_SQL_receipt(tmp_path, monkeypatch):
    with signed_pending(tmp_path / "rollback.sqlite", monkeypatch) as (case, receipt):
        value = current(case)
        before = _business(case)
        original = case.bundle.audit_log.record
        def failing(event):
            if event.event_type == "netplan.outcome_tracking_recorded.v1":
                raise RuntimeError("declared tracking audit failure")
            return original(event)
        monkeypatch.setattr(case.bundle.audit_log, "record", failing)
        response = decide(case, value, receipt)
        assert response.status_code == 503 and _business(case) == before
        assert case.repository.list_approvals(case.scenario_id) == []
        documents = case.repository._store
        assert documents.list_all(TASK_COLLECTION) == []
        assert documents.list_all(LABEL_COLLECTION) == []
        assert documents.list_all(RECEIPT_COLLECTION) == []
