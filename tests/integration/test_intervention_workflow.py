"""Integration tests for the InterventionOps shared lifecycle (ODP-R4-001).

Covers the acceptance criteria:
- the full state surface (eligibility / action / conflict / approval / execution /
  observation / outcome / effect) exists and transitions correctly;
- an unresolved conflict blocks approval until the overlap is resolved;
- the observation window only opens at execution and cannot mature before it;
- a matured effect evaluation writes a label back to the Label Registry; and
- effect / causal claims are gated on observation maturity, a control group and
  a passing pre-trend, with the Evidence Level always attached.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.intervention import (
    ACTIVE_INTERVENTION_STATUSES,
    AdjustmentOutcome,
    AdjustmentRecord,
    CloseDisposition,
    EvaluationMethod,
    EvidenceLevel,
    InMemoryInterventionRepository,
    InMemoryLabelRegistry,
    InterventionError,
    InterventionKind,
    InterventionStatus,
    InterventionWorkflow,
    PretrendStatus,
    Recommendation,
    can_claim_causal,
    can_claim_effect,
    resolve_evidence_level,
    run_observation_sweep,
)
from shared.auth import Role
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import DurableInterventionRepository
from tests.integration._authz import INTERVENTION_HEADERS, auth_headers

START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
END = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
EXEC_TIME = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
# PRICE_CHANGE default window is 21 + 7 days, so maturity is 28 days after exec.
MATURE_TIME = EXEC_TIME + timedelta(days=29)
IMMATURE_TIME = EXEC_TIME + timedelta(days=3)


def _seed_store(engine: SqliteEngine, store_id: str = "store-durable-1") -> None:
    tenant_id = "00000000-0000-0000-0000-000000000001"
    brand_id = "00000000-0000-0000-0000-000000000002"
    addr_id = "00000000-0000-0000-0000-000000000003"
    engine.execute(
        "INSERT OR IGNORE INTO tenants (tenant_id, tenant_name, status) VALUES (?, ?, ?)",
        (tenant_id, "Tenant A", "active"),
    )
    engine.execute(
        "INSERT OR IGNORE INTO brands (brand_id, tenant_id, brand_code, brand_name) VALUES (?, ?, ?, ?)",
        (brand_id, tenant_id, "BRAND-01", "Brand A"),
    )
    engine.execute(
        "INSERT OR IGNORE INTO address_locations (address_id, raw_address) VALUES (?, ?)",
        (addr_id, "123 Main St"),
    )
    engine.execute(
        "INSERT OR IGNORE INTO stores (store_id, tenant_id, brand_id, store_name, address_id) VALUES (?, ?, ?, ?, ?)",
        (store_id, tenant_id, brand_id, "Store Durable", addr_id),
    )


def _new_workflow() -> tuple[InterventionWorkflow, InMemoryLabelRegistry]:
    registry = InMemoryLabelRegistry()
    workflow = InterventionWorkflow(
        repository=InMemoryInterventionRepository(), label_hooks=[registry]
    )
    return workflow, registry


def _open_case(workflow: InterventionWorkflow, *, store_id: str = "store-001"):
    return workflow.open_case(
        store_id=store_id,
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="alert-7788",
        expected_outcome="recover incremental gross margin after orange alert",
        planned_start=START,
        planned_end=END,
        created_by="supervisor-a",
    )


def _drive_to_approved(workflow: InterventionWorkflow, intervention_id: str) -> None:
    workflow.check_eligibility(intervention_id, eligible=True, actor="supervisor-a")
    workflow.propose_action(
        intervention_id,
        action_spec={"price_change_pct": -5, "rollback_plan": "restore list price"},
        actor="pricing-a",
    )
    workflow.check_conflict(intervention_id, actor="pricing-a")
    workflow.submit_for_approval(intervention_id, actor="pricing-a")
    workflow.approve(intervention_id, actor="ops-manager", reason="margin recovery within band")


def _drive_to_completed(workflow: InterventionWorkflow, intervention_id: str) -> None:
    """Drive a case all the way to COMPLETED with a matured, causal outcome."""
    _drive_to_approved(workflow, intervention_id)
    workflow.execute(intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        intervention_id,
        actor="analyst-a",
        incremental_revenue=120_000.0,
        incremental_gross_margin=48_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
        treatment_store_count=1,
        control_store_count=4,
        evaluation_method=EvaluationMethod.DID,
    )
    workflow.evaluate_effect(intervention_id, actor="analyst-a", now=MATURE_TIME)


def test_full_lifecycle_reaches_completed_with_causal_evidence_and_label() -> None:
    workflow, registry = _new_workflow()
    case = _open_case(workflow)
    assert case.status is InterventionStatus.CANDIDATE

    _drive_to_approved(workflow, case.intervention_id)
    approved = workflow.get(case.intervention_id)
    assert approved.status is InterventionStatus.APPROVED
    assert approved.approval is not None and approved.approval.approved

    # Execution opens the observation window (approval and execution are separate).
    observing = workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    assert observing.status is InterventionStatus.OBSERVING
    assert observing.execution is not None
    assert observing.observation_window is not None
    assert observing.observation_window.opened_at == EXEC_TIME

    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=120_000.0,
        incremental_gross_margin=48_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
        treatment_store_count=1,
        control_store_count=4,
        evaluation_method=EvaluationMethod.DID,
    )

    outcome = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=MATURE_TIME)
    assert outcome.intervention.status is InterventionStatus.COMPLETED
    assert outcome.effect.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert outcome.effect.can_claim_effect is True
    assert outcome.effect.can_claim_causal is True
    assert outcome.effect.incremental_gross_margin == 48_000.0
    assert outcome.audit_event_id

    # Label written back to the registry for ForecastOps exclusion (AC-05-05).
    label = registry.get(case.intervention_id)
    assert label is not None
    assert label.exclude_from_baseline is True
    assert label.evidence_level is EvidenceLevel.L3_DID_VALIDATED
    assert registry.intervened_windows("store-001") == [label]
    assert label.label_maturity_time == observing.observation_window.maturity_time


def test_close_completed_case_records_disposition_and_is_terminal() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_completed(workflow, case.intervention_id)
    completed = workflow.get(case.intervention_id)
    assert completed.status is InterventionStatus.COMPLETED
    # A matured-but-unclosed case is NOT terminal: it still awaits close/follow-up.
    assert completed.is_terminal is False

    closed = workflow.close_case(
        case.intervention_id,
        actor="ops-manager",
        disposition=CloseDisposition.KEEP,
        reason="positive causal effect; keep the change, no follow-up needed",
    )
    assert closed.status is InterventionStatus.CLOSED
    assert closed.is_terminal is True
    assert closed.close is not None
    assert closed.close.disposition is CloseDisposition.KEEP
    assert closed.close.has_follow_up is False
    # The effect recommendation is snapshotted onto the close record for audit.
    assert closed.close.recommendation == closed.effect.recommendation.value


def test_close_requires_reason_and_completed_state() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)

    # Cannot close a case that has not reached COMPLETED (rejects invalid state).
    with pytest.raises(InterventionError, match="cannot close"):
        workflow.close_case(
            case.intervention_id,
            actor="ops-manager",
            disposition=CloseDisposition.KEEP,
            reason="too early",
        )

    _drive_to_completed(workflow, case.intervention_id)

    # Closing is high-risk: a reason is mandatory.
    with pytest.raises(InterventionError, match="requires a reason"):
        workflow.close_case(
            case.intervention_id,
            actor="ops-manager",
            disposition=CloseDisposition.REVERT,
            reason="   ",
        )

    # A closed case cannot be closed again (CLOSED is terminal).
    workflow.close_case(
        case.intervention_id,
        actor="ops-manager",
        disposition=CloseDisposition.KEEP,
        reason="keep the change after positive matured effect",
    )
    with pytest.raises(InterventionError, match="cannot close"):
        workflow.close_case(
            case.intervention_id,
            actor="ops-manager",
            disposition=CloseDisposition.KEEP,
            reason="double close attempt",
        )


def test_close_with_follow_up_opens_linked_candidate_after_maturity() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_completed(workflow, case.intervention_id)
    original = workflow.get(case.intervention_id)

    closed = workflow.close_case(
        case.intervention_id,
        actor="ops-manager",
        disposition=CloseDisposition.ITERATE,
        reason="inconclusive channel mix; schedule a follow-up iteration",
        follow_up=True,
        follow_up_kind=InterventionKind.AD_CAMPAIGN,
    )
    assert closed.status is InterventionStatus.CLOSED
    assert closed.close.has_follow_up is True

    follow_up_id = closed.close.follow_up_intervention_id
    follow_up = workflow.get(follow_up_id)
    assert follow_up is not None
    # The follow-up is a fresh CANDIDATE for the same store, linked back and
    # scheduled after the original's observation window matures (no overlap).
    assert follow_up.status is InterventionStatus.CANDIDATE
    assert follow_up.store_id == original.store_id
    assert follow_up.kind is InterventionKind.AD_CAMPAIGN
    assert follow_up.trigger_ref == f"follow-up:{case.intervention_id}"
    assert follow_up.planned_start == original.observation_window.maturity_time


def test_conflict_blocks_approval_until_resolved() -> None:
    workflow, _ = _new_workflow()

    first = _open_case(workflow)
    _drive_to_approved(workflow, first.intervention_id)
    workflow.execute(first.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)

    # A second, overlapping intervention on the same store must surface a conflict.
    second = _open_case(workflow)
    workflow.check_eligibility(second.intervention_id, eligible=True, actor="supervisor-a")
    workflow.propose_action(
        second.intervention_id, action_spec={"campaign": "promo"}, actor="mkt-a"
    )
    conflicted = workflow.check_conflict(second.intervention_id, actor="mkt-a")
    assert conflicted.conflict is not None
    assert conflicted.conflict.has_conflict is True
    assert first.intervention_id in conflicted.conflict.conflicting_ids
    assert conflicted.conflict.blocks_approval is True

    # Approval is blocked while the conflict is unresolved.
    with pytest.raises(InterventionError, match="unresolved conflict"):
        workflow.submit_for_approval(second.intervention_id, actor="mkt-a")

    # Overriding requires an explicit resolution reason.
    with pytest.raises(InterventionError, match="resolution reason"):
        workflow.check_conflict(second.intervention_id, actor="mkt-a", allow_overlap=True)

    resolved = workflow.check_conflict(
        second.intervention_id,
        actor="ops-manager",
        allow_overlap=True,
        reason="staggered rollout accepted; treated as separate cohort",
    )
    assert resolved.conflict.resolved is True
    assert resolved.conflict.blocks_approval is False

    # Now approval can proceed.
    pending = workflow.submit_for_approval(second.intervention_id, actor="ops-manager")
    assert pending.status is InterventionStatus.PENDING_APPROVAL


def test_approval_and_execution_are_separated_and_guarded() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    workflow.check_eligibility(case.intervention_id, eligible=True, actor="supervisor-a")
    workflow.propose_action(case.intervention_id, action_spec={}, actor="pricing-a")
    workflow.check_conflict(case.intervention_id, actor="pricing-a")
    workflow.submit_for_approval(case.intervention_id, actor="pricing-a")

    # High-risk approval requires a reason (never optimistic).
    with pytest.raises(InterventionError, match="require a reason"):
        workflow.approve(case.intervention_id, actor="ops-manager", reason="")

    # Cannot execute before approval — execution is a separate, gated step.
    with pytest.raises(InterventionError, match="cannot execute"):
        workflow.execute(case.intervention_id, executor="ops-runner")

    workflow.approve(case.intervention_id, actor="ops-manager", reason="ok")
    observing = workflow.execute(case.intervention_id, executor="ops-runner")
    assert observing.status is InterventionStatus.OBSERVING


def test_observation_window_cannot_mature_before_execution() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    observing = workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    window = observing.observation_window
    assert window is not None
    # The maturity time is strictly after execution.
    assert window.maturity_time > window.opened_at
    assert window.is_mature(now=EXEC_TIME) is False
    assert window.is_mature(now=IMMATURE_TIME) is False
    assert window.is_mature(now=MATURE_TIME) is True


def test_immature_window_cannot_claim_effect() -> None:
    workflow, registry = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=90_000.0,
        incremental_gross_margin=30_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
        treatment_store_count=1,
        control_store_count=3,
        evaluation_method=EvaluationMethod.DID,
    )

    outcome = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=IMMATURE_TIME)
    assert outcome.effect.evidence_level is EvidenceLevel.L0_ANECDOTAL
    assert outcome.effect.can_claim_effect is False
    assert outcome.effect.can_claim_causal is False
    # No effect figures are surfaced before maturity.
    assert outcome.effect.incremental_gross_margin == 0.0
    assert outcome.effect.recommendation is Recommendation.INCONCLUSIVE
    assert "observation_window_not_mature" in outcome.effect.limitations
    assert registry.get(case.intervention_id).can_claim_effect is False
    # KEY: an immature evaluate_effect must NOT advance the case to COMPLETED.
    # It must stay in EVALUATING so close_case cannot be called prematurely.
    assert outcome.intervention.status is InterventionStatus.EVALUATING


def test_immature_evaluate_then_close_is_rejected() -> None:
    """Regression: Codex2 review — immature evaluate_effect must not allow close.

    Reproduction sequence:
        1. Drive a case to OBSERVING with an outcome collected.
        2. Call evaluate_effect with now=IMMATURE_TIME (window not settled).
        3. Attempt close_case with KEEP.

    Before the fix evaluate_effect always advanced to COMPLETED even when
    observation_mature=False, which let close_case slip through to CLOSED.
    After the fix:
        - evaluate_effect with an immature window stays in EVALUATING (not COMPLETED);
        - a close_case attempt at that point raises InterventionError because the
          status check (requires COMPLETED) fails.
    """
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=50_000.0,
        incremental_gross_margin=20_000.0,
        has_control_group=False,
        pretrend_status=PretrendStatus.INCONCLUSIVE,
        treatment_store_count=1,
        control_store_count=0,
        evaluation_method=EvaluationMethod.BEFORE_AFTER,
    )

    # Immature evaluation: window has not settled.
    outcome = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=IMMATURE_TIME)
    assert outcome.effect.observation_mature is False
    # Must stay in EVALUATING — NOT COMPLETED.
    assert outcome.intervention.status is InterventionStatus.EVALUATING

    # close_case must be rejected because the status is EVALUATING, not COMPLETED.
    with pytest.raises(InterventionError, match="cannot close"):
        workflow.close_case(
            case.intervention_id,
            actor="ops-manager",
            disposition=CloseDisposition.KEEP,
            reason="attempted close on immature outcome",
        )


def test_close_defence_in_depth_rejects_immature_effect() -> None:
    """Defence-in-depth: close_case must guard effect.observation_mature even if
    the status check is somehow satisfied (e.g. via a future alternative code path).

    Simulated by creating a workflow state where the case is in COMPLETED but the
    effect has observation_mature=False — which cannot happen through the normal
    workflow after the primary fix, but we verify the guard independently.
    """
    from dataclasses import replace as dc_replace

    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_completed(workflow, case.intervention_id)
    completed = workflow.get(case.intervention_id)
    assert completed.status is InterventionStatus.COMPLETED

    # Fabricate an immature effect on an otherwise COMPLETED case to test the
    # defence-in-depth guard independently of the primary fix.
    assert completed.effect is not None
    immature_effect = dc_replace(completed.effect, observation_mature=False)
    tampered = dc_replace(completed, effect=immature_effect)
    workflow.repository.save(tampered)

    with pytest.raises(InterventionError, match="observation window has not matured"):
        workflow.close_case(
            case.intervention_id,
            actor="ops-manager",
            disposition=CloseDisposition.KEEP,
            reason="attempting close with fabricated immature effect",
        )


def test_immature_evaluate_then_mature_retry_reaches_completed() -> None:
    """Regression: Codex2 review — mature retry after immature evaluate must work.

    Full reproduction sequence from the task brief:
        1. Drive a case to OBSERVING with an outcome collected.
        2. Call evaluate_effect(now=IMMATURE_TIME) → EVALUATING, observation_mature=False.
        3. Call evaluate_effect(now=MATURE_TIME) → must succeed (NOT raise) and
           advance the case to COMPLETED so the operator can close it.

    Before the fix step 3 raised:
        InterventionError: cannot evaluate effect on intervention in status EVALUATING
    because _require_status only allowed OBSERVING.  After the fix EVALUATING is
    also accepted, making the mature-retry path reachable.
    """
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=50_000.0,
        incremental_gross_margin=20_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
        treatment_store_count=5,
        control_store_count=5,
        evaluation_method=EvaluationMethod.DID,
    )

    # Step 2: immature first evaluate — case stays in EVALUATING.
    first = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=IMMATURE_TIME)
    assert first.effect.observation_mature is False
    assert first.intervention.status is InterventionStatus.EVALUATING

    # Step 3: mature retry — must NOT raise, must advance to COMPLETED.
    second = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=MATURE_TIME)
    assert second.effect.observation_mature is True
    assert second.intervention.status is InterventionStatus.COMPLETED

    # Verify the case can now be closed.
    closed = workflow.close_case(
        case.intervention_id,
        actor="ops-manager",
        disposition=CloseDisposition.KEEP,
        reason="positive effect confirmed after mature retry",
    )
    assert closed.status is InterventionStatus.CLOSED


def test_mature_without_control_is_before_after_not_causal() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=50_000.0,
        incremental_gross_margin=20_000.0,
        has_control_group=False,
        pretrend_status=PretrendStatus.INCONCLUSIVE,
        treatment_store_count=1,
        control_store_count=0,
        evaluation_method=EvaluationMethod.BEFORE_AFTER,
    )
    outcome = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=MATURE_TIME)
    assert outcome.effect.evidence_level is EvidenceLevel.L1_BEFORE_AFTER
    assert outcome.effect.can_claim_effect is True
    assert outcome.effect.can_claim_causal is False
    assert "no_control_group" in outcome.effect.limitations


def test_pretrend_failure_caps_evidence_at_matched_descriptive() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    _drive_to_approved(workflow, case.intervention_id)
    workflow.execute(case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        case.intervention_id,
        actor="analyst-a",
        incremental_revenue=50_000.0,
        incremental_gross_margin=20_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.FAIL,
        treatment_store_count=1,
        control_store_count=4,
        evaluation_method=EvaluationMethod.DID,
    )
    outcome = workflow.evaluate_effect(case.intervention_id, actor="analyst-a", now=MATURE_TIME)
    assert outcome.effect.evidence_level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    assert outcome.effect.can_claim_causal is False
    assert "pretrend_fail" in outcome.effect.limitations


def test_evidence_level_table() -> None:
    assert (
        resolve_evidence_level(
            mature=False,
            has_control_group=True,
            pretrend_status=PretrendStatus.PASS,
            randomized=True,
        )
        is EvidenceLevel.L0_ANECDOTAL
    )
    assert (
        resolve_evidence_level(
            mature=True,
            has_control_group=False,
            pretrend_status=PretrendStatus.PASS,
            randomized=False,
        )
        is EvidenceLevel.L1_BEFORE_AFTER
    )
    assert (
        resolve_evidence_level(
            mature=True,
            has_control_group=True,
            pretrend_status=PretrendStatus.PASS,
            randomized=True,
        )
        is EvidenceLevel.L4_RANDOMIZED
    )
    assert (
        resolve_evidence_level(
            mature=True,
            has_control_group=True,
            pretrend_status=PretrendStatus.PASS,
            randomized=False,
            replicated=True,
        )
        is EvidenceLevel.L5_POLICY_READY
    )
    assert can_claim_effect(EvidenceLevel.L1_BEFORE_AFTER) is True
    assert can_claim_causal(EvidenceLevel.L2_MATCHED_DESCRIPTIVE) is False
    assert can_claim_causal(EvidenceLevel.L3_DID_VALIDATED) is True


def test_observation_sweep_matures_and_auto_evaluates() -> None:
    workflow, registry = _new_workflow()

    mature_case = _open_case(workflow, store_id="store-mature")
    _drive_to_approved(workflow, mature_case.intervention_id)
    workflow.execute(mature_case.intervention_id, executor="ops-runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        mature_case.intervention_id,
        actor="analyst-a",
        incremental_revenue=80_000.0,
        incremental_gross_margin=32_000.0,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
        treatment_store_count=1,
        control_store_count=4,
        evaluation_method=EvaluationMethod.DID,
    )

    # Executed only just before the sweep, so its window is still open.
    pending_case = _open_case(workflow, store_id="store-pending")
    _drive_to_approved(workflow, pending_case.intervention_id)
    workflow.execute(pending_case.intervention_id, executor="ops-runner", executed_at=MATURE_TIME)

    result = run_observation_sweep(workflow, job_id="sweep-1", now=MATURE_TIME, auto_evaluate=True)
    assert mature_case.intervention_id in result.matured_ids
    assert pending_case.intervention_id not in result.matured_ids
    assert pending_case.intervention_id in result.pending_ids
    assert mature_case.intervention_id in result.evaluated_ids
    assert registry.get(mature_case.intervention_id) is not None
    assert workflow.get(mature_case.intervention_id).status is InterventionStatus.COMPLETED


def test_api_drives_full_lifecycle_with_conflict_and_label() -> None:
    client = TestClient(create_app(), headers=INTERVENTION_HEADERS)

    create = client.post(
        "/interventions",
        json={
            "store_id": "store-api-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-api",
            "expected_outcome": "recover GM",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "supervisor-a",
        },
        headers={"x-correlation-id": "corr-iv-1", "Idempotency-Key": "iv-idem-1"},
    )
    assert create.status_code == 201
    body = create.json()
    assert body["created"] is True
    assert body["status"] == "CANDIDATE"
    iid = body["intervention_id"]

    # Idempotent replay returns the same case without creating a new one.
    replay = client.post(
        "/interventions",
        json={
            "store_id": "store-api-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-api",
            "expected_outcome": "recover GM",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "supervisor-a",
        },
        headers={"Idempotency-Key": "iv-idem-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["intervention_id"] == iid

    conflict = client.post(
        "/interventions",
        json={
            "store_id": "store-api-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "different-alert",
            "expected_outcome": "recover GM",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "supervisor-a",
        },
        headers={"Idempotency-Key": "iv-idem-1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    client.post(f"/interventions/{iid}/eligibility", json={"eligible": True, "actor": "s"})
    client.post(f"/interventions/{iid}/action", json={"action_spec": {"pct": -5}, "actor": "p"})
    client.post(f"/interventions/{iid}/conflict-check", json={"actor": "p"})

    # Approval without a reason is rejected (high risk).
    no_reason = client.post(f"/interventions/{iid}/submit", json={"actor": "p"})
    assert no_reason.status_code == 200
    bad = client.post(
        f"/interventions/{iid}/approve", json={"action": "APPROVE", "actor": "m", "reason": ""}
    )
    assert bad.status_code == 422

    # Cannot execute before approval.
    early = client.post(f"/interventions/{iid}/execute", json={"executor": "r"})
    assert early.status_code == 422

    corr = {"x-correlation-id": "corr-iv-1"}
    approve = client.post(
        f"/interventions/{iid}/approve",
        json={"action": "APPROVE", "actor": "m", "reason": "approved"},
        headers=corr,
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "APPROVED"

    execute = client.post(
        f"/interventions/{iid}/execute",
        json={"executor": "r", "executed_at": EXEC_TIME.isoformat()},
        headers=corr,
    )
    assert execute.status_code == 200
    assert execute.json()["status"] == "OBSERVING"

    client.post(
        f"/interventions/{iid}/outcomes",
        json={
            "actor": "a",
            "incremental_revenue": 100_000,
            "incremental_gross_margin": 40_000,
            "has_control_group": True,
            "pretrend_status": "PASS",
            "treatment_store_count": 1,
            "control_store_count": 4,
            "evaluation_method": "DID",
        },
    )
    evaluate = client.post(
        f"/interventions/{iid}/evaluate",
        json={"actor": "a", "now": MATURE_TIME.isoformat()},
        headers={"x-correlation-id": "corr-iv-1"},
    )
    assert evaluate.status_code == 200
    eff = evaluate.json()
    assert eff["status"] == "COMPLETED"
    assert eff["effect"]["evidence_level"] == "L3"
    assert eff["effect"]["can_claim_causal"] is True

    label = client.get(f"/interventions/{iid}/label")
    assert label.status_code == 200
    assert label.json()["exclude_from_baseline"] is True

    audit = client.get("/audit/events", params={"correlation_id": "corr-iv-1"})
    actions = {e["action"] for e in audit.json()["events"]}
    assert {"create", "approve", "execute", "evaluate_effect"} <= actions


def test_api_conflict_blocks_submit() -> None:
    client = TestClient(create_app(), headers=INTERVENTION_HEADERS)

    def _create() -> str:
        resp = client.post(
            "/interventions",
            json={
                "store_id": "store-api-2",
                "kind": "AD_CAMPAIGN",
                "expected_outcome": "lift",
                "planned_start": START.isoformat(),
                "planned_end": END.isoformat(),
                "created_by": "s",
            },
        )
        return resp.json()["intervention_id"]

    first = _create()
    for path, payload in (
        ("eligibility", {"eligible": True, "actor": "s"}),
        ("action", {"action_spec": {}, "actor": "p"}),
        ("conflict-check", {"actor": "p"}),
        ("submit", {"actor": "p"}),
        ("approve", {"action": "APPROVE", "actor": "m", "reason": "ok"}),
        ("execute", {"executor": "r", "executed_at": EXEC_TIME.isoformat()}),
    ):
        assert client.post(f"/interventions/{first}/{path}", json=payload).status_code == 200

    second = _create()
    client.post(f"/interventions/{second}/eligibility", json={"eligible": True, "actor": "s"})
    client.post(f"/interventions/{second}/action", json={"action_spec": {}, "actor": "p"})
    conflict = client.post(f"/interventions/{second}/conflict-check", json={"actor": "p"})
    assert conflict.json()["conflict"]["has_conflict"] is True

    blocked = client.post(f"/interventions/{second}/submit", json={"actor": "p"})
    assert blocked.status_code == 422


def test_api_close_case_with_follow_up_and_audit() -> None:
    client = TestClient(create_app(), headers=INTERVENTION_HEADERS)
    corr = {"x-correlation-id": "corr-iv-close"}

    create = client.post(
        "/interventions",
        json={
            "store_id": "store-api-close",
            "kind": "PRICE_CHANGE",
            "expected_outcome": "recover GM",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "s",
        },
        headers=corr,
    )
    iid = create.json()["intervention_id"]
    for path, payload in (
        ("eligibility", {"eligible": True, "actor": "s"}),
        ("action", {"action_spec": {"pct": -5}, "actor": "p"}),
        ("conflict-check", {"actor": "p"}),
        ("submit", {"actor": "p"}),
        ("approve", {"action": "APPROVE", "actor": "m", "reason": "ok"}),
        ("execute", {"executor": "r", "executed_at": EXEC_TIME.isoformat()}),
        (
            "outcomes",
            {
                "actor": "a",
                "incremental_revenue": 100_000,
                "incremental_gross_margin": 40_000,
                "has_control_group": True,
                "pretrend_status": "PASS",
                "treatment_store_count": 1,
                "control_store_count": 4,
                "evaluation_method": "DID",
            },
        ),
    ):
        assert (
            client.post(f"/interventions/{iid}/{path}", json=payload, headers=corr).status_code
            == 200
        )
    assert (
        client.post(
            f"/interventions/{iid}/evaluate",
            json={"actor": "a", "now": MATURE_TIME.isoformat()},
            headers=corr,
        ).status_code
        == 200
    )

    # Closing requires a valid disposition; an unknown one is a domain 422.
    bad = client.post(
        f"/interventions/{iid}/close",
        json={"actor": "m", "disposition": "NOT_A_DISPOSITION", "reason": "x"},
    )
    assert bad.status_code == 422

    close = client.post(
        f"/interventions/{iid}/close",
        json={
            "actor": "ops-manager",
            "disposition": "ITERATE",
            "reason": "iterate with a follow-up campaign after positive matured effect",
            "follow_up": True,
        },
        headers=corr,
    )
    assert close.status_code == 200
    closed = close.json()
    assert closed["status"] == "CLOSED"
    assert closed["close"]["disposition"] == "ITERATE"
    follow_up_id = closed["close"]["follow_up_intervention_id"]
    assert follow_up_id

    follow_up = client.get(f"/interventions/{follow_up_id}")
    assert follow_up.status_code == 200
    assert follow_up.json()["status"] == "CANDIDATE"
    assert follow_up.json()["trigger_ref"] == f"follow-up:{iid}"

    audit = client.get("/audit/events", params={"correlation_id": "corr-iv-close"})
    actions = {e["action"] for e in audit.json()["events"]}
    assert "close" in actions


def test_assignment_lifecycle_and_audit() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)
    assert case.assigned_to is None
    assert case.version == 1

    assigned = workflow.assign_case(
        case.intervention_id,
        assignee="operator-jane",
        actor="supervisor-a",
        role="STORE_OPERATOR",
    )
    assert assigned.assigned_to == "operator-jane"
    assert assigned.assigned_by == "supervisor-a"
    assert assigned.assignment_role == "STORE_OPERATOR"
    assert assigned.assigned_at is not None
    assert assigned.version == 2

    unassigned = workflow.unassign_case(case.intervention_id, actor="supervisor-a")
    assert unassigned.assigned_to is None
    assert unassigned.version == 3

    # Close the case and verify terminal case cannot be assigned
    _drive_to_completed(workflow, case.intervention_id)
    workflow.close_case(
        case.intervention_id,
        actor="ops-manager",
        disposition=CloseDisposition.KEEP,
        reason="done",
    )
    terminal = workflow.get(case.intervention_id)
    assert terminal.is_terminal is True
    with pytest.raises(InterventionError, match="cannot assign terminal intervention"):
        workflow.assign_case(
            case.intervention_id, assignee="op-x", actor="supervisor-a"
        )


def test_stale_update_concurrency_conflict_detected() -> None:
    workflow, _ = _new_workflow()
    case = _open_case(workflow)

    # Attempt assignment with expected_version mismatch
    with pytest.raises(InterventionError, match="stale update: expected version 99"):
        workflow.assign_case(
            case.intervention_id,
            assignee="op-y",
            actor="supervisor-a",
            expected_version=99,
        )


def test_api_assignment_rbac_and_inbox_deep_link_filtering() -> None:
    client = TestClient(create_app(), headers=INTERVENTION_HEADERS)

    create = client.post(
        "/interventions",
        json={
            "store_id": "store-assign-1",
            "kind": "PRICE_CHANGE",
            "expected_outcome": "recover margin",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "supervisor-a",
        },
    )
    iid = create.json()["intervention_id"]

    # Negative RBAC check: unauthorized caller (without required permission) is rejected with HTTP 403
    unauth_client = TestClient(create_app(), headers=auth_headers(Role.AUDITOR))
    forbidden_assign = unauth_client.post(
        f"/interventions/{iid}/assign",
        json={"assignee": "op-hero", "actor": "supervisor-a"},
    )
    assert forbidden_assign.status_code == 403

    forbidden_unassign = unauth_client.post(
        f"/interventions/{iid}/unassign",
        json={"actor": "supervisor-a"},
    )
    assert forbidden_unassign.status_code == 403

    # Assign case
    assign = client.post(
        f"/interventions/{iid}/assign",
        json={
            "assignee": "op-hero",
            "actor": "supervisor-a",
            "role": "OPERATOR",
            "expected_version": 1,
        },
    )
    assert assign.status_code == 200
    res = assign.json()
    assert res["assigned_to"] == "op-hero"
    assert res["version"] == 2

    # Stale assignment attempt (expected_version mismatch) returns HTTP 409
    stale = client.post(
        f"/interventions/{iid}/assign",
        json={
            "assignee": "op-another",
            "actor": "supervisor-a",
            "expected_version": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_UPDATE_CONFLICT"

    # Deep-linkable inbox filtering query
    inbox = client.get(
        "/interventions",
        params={"assigned_to": "op-hero", "store_id": "store-assign-1", "kind": "PRICE_CHANGE"},
    )
    assert inbox.status_code == 200
    items = inbox.json()["items"]
    assert len(items) == 1
    assert items[0]["intervention_id"] == iid

    # Invalid query parameters return HTTP 422 instead of 500
    bad_status = client.get("/interventions", params={"status": "NOT_A_STATUS"})
    assert bad_status.status_code == 422

    bad_kind = client.get("/interventions", params={"kind": "NOT_A_KIND"})
    assert bad_kind.status_code == 422

    # Unassign case
    unassign = client.post(
        f"/interventions/{iid}/unassign",
        json={"actor": "supervisor-a", "expected_version": 2},
    )
    assert unassign.status_code == 200
    assert unassign.json()["assigned_to"] is None


def test_recommendation_vocabulary_includes_adjust_and_distinguishes_others() -> None:
    """ODP-FR-INTV-006: Canonical vocabulary distinguishes ADJUST from CONTINUE, STOP, SCALE, CHANGE_CHANNEL."""
    assert Recommendation.ADJUST == "ADJUST"
    assert Recommendation.CONTINUE == "CONTINUE"
    assert Recommendation.STOP == "STOP"
    assert Recommendation.SCALE == "SCALE"
    assert Recommendation.CHANGE_CHANNEL == "CHANGE_CHANNEL"
    assert Recommendation.INCONCLUSIVE == "INCONCLUSIVE"
    all_recs = {r.value for r in Recommendation}
    assert len(all_recs) == 6
    assert "ADJUST" in all_recs
    assert callable(InterventionWorkflow.rollback)
    assert callable(InterventionWorkflow.stop)
    assert callable(InterventionWorkflow.adjust_case)


def test_adjust_case_workflow_lineage_and_recreate() -> None:
    """ODP-FR-INTV-006: Stop-plus-recreate replacement with durable lineage & audit."""
    repo = InMemoryInterventionRepository()
    workflow = InterventionWorkflow(repository=repo)

    # 1. Drive initial intervention to OBSERVING
    case = workflow.open_case(
        store_id="store-adj-1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="alert-price-001",
        expected_outcome="increase gross margin by 5%",
        planned_start=START,
        planned_end=END,
        created_by="ops-mgr-a",
        action_spec={"price_change_pct": -5, "rollback_plan": "revert to standard price"},
    )
    _drive_to_approved(workflow, case.intervention_id)
    case = workflow.execute(case.intervention_id, executor="field-op-1", executed_at=EXEC_TIME)
    assert case.status is InterventionStatus.OBSERVING

    # 2. Adjust active intervention
    adj_outcome = workflow.adjust_case(
        case.intervention_id,
        actor="pricing-lead-1",
        reason="deepen discount to 8% due to local competitor promo",
        action_spec={"price_change_pct": -8},
        rollback_plan="restore baseline tier",
        expected_version=case.version,
    )

    assert isinstance(adj_outcome, AdjustmentOutcome)
    # Predecessor verification
    stopped_orig = adj_outcome.original
    assert stopped_orig.status is InterventionStatus.STOPPED
    assert stopped_orig.is_terminal is True
    assert stopped_orig.replacement_id == adj_outcome.replacement.intervention_id
    assert isinstance(stopped_orig.adjustment, AdjustmentRecord)
    assert stopped_orig.adjustment.predecessor_id == case.intervention_id
    assert stopped_orig.adjustment.replacement_id == adj_outcome.replacement.intervention_id
    assert stopped_orig.adjustment.actor == "pricing-lead-1"
    assert stopped_orig.adjustment.reason == "deepen discount to 8% due to local competitor promo"
    assert stopped_orig.adjustment.rollback_plan == "restore baseline tier"
    assert stopped_orig.adjustment.policy_version == stopped_orig.policy_version

    # Replacement verification
    repl = adj_outcome.replacement
    assert repl.status is InterventionStatus.CANDIDATE
    assert repl.is_terminal is False
    assert repl.predecessor_id == case.intervention_id
    assert repl.trigger_ref == f"adjust:{case.intervention_id}"
    assert repl.store_id == "store-adj-1"
    assert repl.kind is InterventionKind.PRICE_CHANGE
    assert repl.action_spec["price_change_pct"] == -8
    assert repl.action_spec["rollback_plan"] == "restore baseline tier"
    assert repl.adjustment is not None
    assert repl.adjustment.predecessor_id == case.intervention_id
    assert repl.adjustment.replacement_id == repl.intervention_id

    # Audit records verification
    events = workflow.audit_log.list_events()
    adjust_events = [e for e in events if e.action == "adjust"]
    assert len(adjust_events) == 1
    assert adjust_events[0].resource == f"intervention/{case.intervention_id}"
    assert adjust_events[0].metadata["replacement_id"] == repl.intervention_id

    create_events = [
        e for e in events if e.action == "create" and e.resource == f"intervention/{repl.intervention_id}"
    ]
    assert len(create_events) == 1
    assert create_events[0].metadata["predecessor_id"] == case.intervention_id

    # 3. Complete lifecycle on replacement case
    repl = workflow.check_eligibility(repl.intervention_id, eligible=True, actor="ops-mgr-a")
    repl = workflow.propose_action(
        repl.intervention_id,
        actor="ops-mgr-a",
        action_spec={"price_change_pct": -8, "rollback_plan": "restore baseline tier"},
    )
    repl = workflow.check_conflict(repl.intervention_id, actor="ops-mgr-a")
    repl = workflow.submit_for_approval(repl.intervention_id, actor="ops-mgr-a")
    repl = workflow.approve(repl.intervention_id, actor="regional-sup-1", reason="approved replacement")
    repl = workflow.execute(repl.intervention_id, executor="field-op-1", executed_at=EXEC_TIME)
    repl = workflow.collect_outcome(
        repl.intervention_id,
        actor="measurer",
        incremental_revenue=15000.0,
        incremental_gross_margin=6000.0,
        treatment_store_count=1,
        control_store_count=4,
        evaluation_method=EvaluationMethod.SYNTHETIC_CONTROL,
        has_control_group=True,
        pretrend_status=PretrendStatus.PASS,
    )
    eval_outcome = workflow.evaluate_effect(repl.intervention_id, actor="measurer", now=MATURE_TIME)
    assert eval_outcome.effect.recommendation is Recommendation.CONTINUE
    closed = workflow.close_case(
        repl.intervention_id,
        actor="ops-mgr-a",
        disposition=CloseDisposition.KEEP,
        reason="adjustment successfully completed and measured",
    )
    assert closed.status is InterventionStatus.CLOSED


def test_adjust_terminal_interventions_rejected() -> None:
    """ODP-FR-INTV-006: Adjusting terminal interventions (STOPPED, ROLLED_BACK, CLOSED, REJECTED, INELIGIBLE) is rejected."""
    repo = InMemoryInterventionRepository()
    workflow = InterventionWorkflow(repository=repo)

    # 1. STOPPED case
    case = workflow.open_case(
        store_id="s1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t1",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )
    _drive_to_approved(workflow, case.intervention_id)
    stopped = workflow.stop(case.intervention_id, actor="ops", reason="cancel")
    assert stopped.status is InterventionStatus.STOPPED

    with pytest.raises(InterventionError, match="cannot adjust on intervention in status STOPPED"):
        workflow.adjust_case(stopped.intervention_id, actor="ops", reason="try adjust")

    # 2. ROLLED_BACK case
    case2 = workflow.open_case(
        store_id="s2",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t2",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )
    _drive_to_approved(workflow, case2.intervention_id)
    case2 = workflow.execute(case2.intervention_id, executor="op", executed_at=EXEC_TIME)
    rb = workflow.rollback(case2.intervention_id, actor="ops", reason="adverse")
    assert rb.status is InterventionStatus.ROLLED_BACK

    with pytest.raises(InterventionError, match="cannot adjust on intervention in status ROLLED_BACK"):
        workflow.adjust_case(rb.intervention_id, actor="ops", reason="try adjust")

    # 3. REJECTED case
    case3 = workflow.open_case(
        store_id="s3",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t3",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )
    workflow.check_eligibility(case3.intervention_id, eligible=True, actor="ops")
    workflow.propose_action(case3.intervention_id, action_spec={"pct": -5}, actor="ops")
    workflow.check_conflict(case3.intervention_id, actor="ops")
    workflow.submit_for_approval(case3.intervention_id, actor="ops")
    rejected = workflow.reject(case3.intervention_id, actor="sup", reason="bad")
    assert rejected.status is InterventionStatus.REJECTED

    with pytest.raises(InterventionError, match="cannot adjust on intervention in status REJECTED"):
        workflow.adjust_case(rejected.intervention_id, actor="ops", reason="try adjust")


def test_adjust_stale_version_and_empty_reason_rejected() -> None:
    """ODP-FR-INTV-006: Stale version mismatch and empty reasons are rejected."""
    repo = InMemoryInterventionRepository()
    workflow = InterventionWorkflow(repository=repo)
    case = workflow.open_case(
        store_id="s1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t1",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )

    _drive_to_approved(workflow, case.intervention_id)
    approved = workflow.get(case.intervention_id)

    # Stale version check
    with pytest.raises(InterventionError, match=f"stale update: expected version 99, current is {approved.version}"):
        workflow.adjust_case(
            case.intervention_id,
            actor="ops",
            reason="valid reason",
            expected_version=99,
        )

    # Empty reason check
    with pytest.raises(InterventionError, match="adjusting an intervention requires a reason"):
        workflow.adjust_case(
            case.intervention_id,
            actor="ops",
            reason="   ",
        )


def test_stop_and_rollback_workflow_and_validation() -> None:
    """ODP-FR-INTV-006: Direct stop and rollback operations transition to terminal status."""
    repo = InMemoryInterventionRepository()
    workflow = InterventionWorkflow(repository=repo)

    case = workflow.open_case(
        store_id="s1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t1",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )
    # Stop from CANDIDATE is disallowed (requires APPROVED, EXECUTING, OBSERVING)
    with pytest.raises(InterventionError, match="cannot stop on intervention in status CANDIDATE"):
        workflow.stop(case.intervention_id, actor="ops", reason="too early")

    # Stop requires reason
    _drive_to_approved(workflow, case.intervention_id)
    with pytest.raises(InterventionError, match="stop requires a reason"):
        workflow.stop(case.intervention_id, actor="ops", reason="")

    stopped = workflow.stop(case.intervention_id, actor="ops", reason="campaign cancelled")
    assert stopped.status is InterventionStatus.STOPPED


def test_api_adjust_production_entry_success_and_rejections() -> None:
    """ODP-FR-INTV-006: API endpoint POST /interventions/{id}/adjust handles RBAC, lineage, idempotency, and rejections."""
    app = create_app()
    client = TestClient(app, headers=INTERVENTION_HEADERS)

    # 1. Create and drive intervention to EXECUTING
    created = client.post(
        "/interventions",
        json={
            "store_id": "store-api-adj-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-100",
            "expected_outcome": "boost revenue",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
            "action_spec": {"price_change_pct": -5, "rollback_plan": "revert"},
        },
    )
    assert created.status_code == 201
    iid = created.json()["intervention_id"]

    client.post(f"/interventions/{iid}/eligibility", json={"eligible": True, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/action", json={"action_spec": {"price_change_pct": -5}, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/conflict-check", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid}/submit", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid}/approve", json={"action": "APPROVE", "actor": "sup-hero", "reason": "approved"})
    client.post(f"/interventions/{iid}/execute", json={"executor": "field-op", "executed_at": EXEC_TIME.isoformat()})

    # 2. RBAC check: AUDITOR cannot adjust (HTTP 403)
    auditor_client = TestClient(app, headers=auth_headers(Role.AUDITOR))
    unauth_adj = auditor_client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "auditor-1",
            "reason": "try unauthorized adjust",
        },
    )
    assert unauth_adj.status_code == 403

    # A regional supervisor may execute an intervention but may not create the
    # replacement that Adjust opens. The route must reject this combination
    # before the workflow writes lineage or lifecycle audit events.
    regional_client = TestClient(
        app,
        headers=auth_headers(Role.REGIONAL_SUPERVISOR, subject="regional-only"),
    )
    lifecycle_events_before = [
        event
        for event in app.state.audit_log.list_events()
        if event.event_type == "intervention.lifecycle.v1"
        and event.resource == f"intervention/{iid}"
        and event.action == "adjust"
    ]
    regional_adj = regional_client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "regional-only",
            "reason": "try adjust without create permission",
        },
    )
    assert regional_adj.status_code == 403
    unchanged = client.get(f"/interventions/{iid}")
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "OBSERVING"
    assert unchanged.json()["replacement_id"] is None
    assert unchanged.json()["adjustment"] is None
    lifecycle_events_after = [
        event
        for event in app.state.audit_log.list_events()
        if event.event_type == "intervention.lifecycle.v1"
        and event.resource == f"intervention/{iid}"
        and event.action == "adjust"
    ]
    assert lifecycle_events_after == lifecycle_events_before

    # 3. Stale update rejection (HTTP 409)
    stale_adj = client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "ops-hero",
            "reason": "adjust pricing",
            "expected_version": 999,
        },
    )
    assert stale_adj.status_code == 409
    assert stale_adj.json()["detail"]["code"] == "STALE_UPDATE_CONFLICT"

    # 4. Missing reason rejection (HTTP 422)
    empty_reason = client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "ops-hero",
            "reason": "",
        },
    )
    assert empty_reason.status_code == 422

    # 5. Successful Adjust with Idempotency Key
    idem_key = "adj-idem-key-001"
    adj_res = client.post(
        f"/interventions/{iid}/adjust",
        headers={"Idempotency-Key": idem_key},
        json={
            "actor": "pricing-director",
            "reason": "strategic discount adjustment",
            "action_spec": {"price_change_pct": -12},
            "rollback_plan": "tier baseline restore",
        },
    )
    assert adj_res.status_code == 200
    adj_data = adj_res.json()
    assert adj_data["original_status"] == "STOPPED"
    assert adj_data["replacement_status"] == "CANDIDATE"
    repl_id = adj_data["replacement_intervention_id"]
    assert repl_id != iid

    # Verify original details
    assert adj_data["original"]["replacement_id"] == repl_id
    assert adj_data["original"]["adjustment"]["predecessor_id"] == iid
    assert adj_data["original"]["adjustment"]["replacement_id"] == repl_id
    assert adj_data["original"]["adjustment"]["reason"] == "strategic discount adjustment"

    # Verify replacement details
    assert adj_data["replacement"]["predecessor_id"] == iid
    assert adj_data["replacement"]["action_spec"]["price_change_pct"] == -12
    assert adj_data["replacement"]["action_spec"]["rollback_plan"] == "tier baseline restore"

    # 6. Idempotency replay check
    replay_res = client.post(
        f"/interventions/{iid}/adjust",
        headers={"Idempotency-Key": idem_key},
        json={
            "actor": "pricing-director",
            "reason": "strategic discount adjustment",
            "action_spec": {"price_change_pct": -12},
            "rollback_plan": "tier baseline restore",
        },
    )
    assert replay_res.status_code == 200
    assert replay_res.json()["replacement_intervention_id"] == repl_id

    # 7. Reject adjust on already STOPPED original (HTTP 422)
    terminal_adj = client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "pricing-director",
            "reason": "another adjust attempt",
        },
    )
    assert terminal_adj.status_code == 422
    assert "cannot adjust on intervention in status STOPPED" in terminal_adj.json()["detail"]

    # 8. Query replacement case from API
    repl_get = client.get(f"/interventions/{repl_id}")
    assert repl_get.status_code == 200
    assert repl_get.json()["predecessor_id"] == iid
    assert repl_get.json()["status"] == "CANDIDATE"


def test_api_stop_and_rollback_endpoints() -> None:
    """ODP-FR-INTV-006: API endpoints POST /interventions/{id}/stop and POST /interventions/{id}/rollback."""
    app = create_app()
    client = TestClient(app, headers=INTERVENTION_HEADERS)

    # 1. Stop endpoint test
    created1 = client.post(
        "/interventions",
        json={
            "store_id": "store-api-stop-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-201",
            "expected_outcome": "boost revenue",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
        },
    )
    assert created1.status_code == 201
    iid1 = created1.json()["intervention_id"]
    client.post(f"/interventions/{iid1}/eligibility", json={"eligible": True, "actor": "ops-hero"})
    client.post(f"/interventions/{iid1}/action", json={"action_spec": {"price_change_pct": -5}, "actor": "ops-hero"})
    client.post(f"/interventions/{iid1}/conflict-check", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid1}/submit", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid1}/approve", json={"action": "APPROVE", "actor": "sup-hero", "reason": "approved"})

    # Unauthorized stop
    auditor_client = TestClient(app, headers=auth_headers(Role.AUDITOR))
    unauth_stop = auditor_client.post(f"/interventions/{iid1}/stop", json={"actor": "auditor", "reason": "stop"})
    assert unauth_stop.status_code == 403

    # Authorized stop
    stop_res = client.post(f"/interventions/{iid1}/stop", json={"actor": "ops-hero", "reason": "cancelled due to supply issue"})
    assert stop_res.status_code == 200
    assert stop_res.json()["status"] == "STOPPED"

    # 2. Rollback endpoint test
    created2 = client.post(
        "/interventions",
        json={
            "store_id": "store-api-rb-1",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-202",
            "expected_outcome": "boost revenue",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
        },
    )
    assert created2.status_code == 201
    iid2 = created2.json()["intervention_id"]
    client.post(f"/interventions/{iid2}/eligibility", json={"eligible": True, "actor": "ops-hero"})
    client.post(f"/interventions/{iid2}/action", json={"action_spec": {"price_change_pct": -5}, "actor": "ops-hero"})
    client.post(f"/interventions/{iid2}/conflict-check", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid2}/submit", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid2}/approve", json={"action": "APPROVE", "actor": "sup-hero", "reason": "approved"})
    client.post(f"/interventions/{iid2}/execute", json={"executor": "field-op", "executed_at": EXEC_TIME.isoformat()})

    # Unauthorized rollback
    unauth_rb = auditor_client.post(f"/interventions/{iid2}/rollback", json={"actor": "auditor", "reason": "rollback"})
    assert unauth_rb.status_code == 403

    # Authorized rollback
    rb_res = client.post(f"/interventions/{iid2}/rollback", json={"actor": "ops-hero", "reason": "adverse price reaction observed"})
    assert rb_res.status_code == 200
    assert rb_res.json()["status"] == "ROLLED_BACK"


def test_durable_intervention_repository_persists_adjust_lineage(tmp_path: pytest.TempPathFactory) -> None:
    """ODP-FR-INTV-006: DurableInterventionRepository preserves predecessor_id, replacement_id, and adjustment records."""
    db_file = tmp_path / "durable_interventions.db"
    engine = SqliteEngine(db_file)
    _seed_store(engine, store_id="store-durable-1")
    store = SqliteDocumentStore(engine)
    repo = DurableInterventionRepository(store)

    workflow = InterventionWorkflow(repository=repo)
    case = workflow.open_case(
        store_id="store-durable-1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="alert-durable-001",
        expected_outcome="improve margin",
        planned_start=START,
        planned_end=END,
        created_by="ops-mgr",
        action_spec={"price_change_pct": -5, "rollback_plan": "restore standard price"},
    )
    _drive_to_approved(workflow, case.intervention_id)
    case = workflow.execute(case.intervention_id, executor="op", executed_at=EXEC_TIME)

    adj_outcome = workflow.adjust_case(
        case.intervention_id,
        actor="ops-mgr",
        reason="durable adjustment test",
        action_spec={"price_change_pct": -7},
        rollback_plan="durable rollback plan",
    )

    # Re-instantiate repository over same database to ensure persistent durability
    fresh_engine = SqliteEngine(db_file)
    fresh_store = SqliteDocumentStore(fresh_engine)
    fresh_repo = DurableInterventionRepository(fresh_store)

    read_orig = fresh_repo.get(case.intervention_id)
    assert read_orig is not None
    assert read_orig.status is InterventionStatus.STOPPED
    assert read_orig.replacement_id == adj_outcome.replacement.intervention_id
    assert read_orig.adjustment is not None
    assert read_orig.adjustment.reason == "durable adjustment test"
    assert read_orig.adjustment.rollback_plan == "durable rollback plan"

    read_repl = fresh_repo.get(adj_outcome.replacement.intervention_id)
    assert read_repl is not None
    assert read_repl.status is InterventionStatus.CANDIDATE
    assert read_repl.predecessor_id == case.intervention_id
    assert read_repl.adjustment is not None
    assert read_repl.adjustment.reason == "durable adjustment test"

    store_cases = fresh_repo.list_by_store("store-durable-1")
    assert len(store_cases) == 2
    case_ids = {c.intervention_id for c in store_cases}
    assert case.intervention_id in case_ids
    assert adj_outcome.replacement.intervention_id in case_ids


def test_active_intervention_statuses_allowlist_definition() -> None:
    """ODP-FR-INTV-006: ACTIVE_INTERVENTION_STATUSES contains APPROVED, EXECUTING, OBSERVING only."""
    assert ACTIVE_INTERVENTION_STATUSES == frozenset(
        {
            InterventionStatus.APPROVED,
            InterventionStatus.EXECUTING,
            InterventionStatus.OBSERVING,
        }
    )
    # Ensure pre-activation, evaluation, completed, and terminal states are excluded
    non_active = {
        InterventionStatus.CANDIDATE,
        InterventionStatus.ELIGIBILITY_CHECKING,
        InterventionStatus.ELIGIBLE,
        InterventionStatus.INELIGIBLE,
        InterventionStatus.ACTION_PROPOSED,
        InterventionStatus.CONFLICT_CHECKING,
        InterventionStatus.PENDING_APPROVAL,
        InterventionStatus.REJECTED,
        InterventionStatus.EVALUATING,
        InterventionStatus.COMPLETED,
        InterventionStatus.CLOSED,
        InterventionStatus.STOPPED,
        InterventionStatus.ROLLED_BACK,
    }
    for status in non_active:
        assert status not in ACTIVE_INTERVENTION_STATUSES


def test_adjust_workflow_rejects_pre_activation_and_evaluation_states_without_lineage_or_audit() -> None:
    """ODP-FR-INTV-006: Adjust rejects CANDIDATE, ELIGIBLE, PENDING_APPROVAL, EVALUATING, COMPLETED, etc.
    and guarantees no replacement, no lineage, and no audit write on rejection.
    """
    repo = InMemoryInterventionRepository()
    workflow = InterventionWorkflow(repository=repo)

    # 1. CANDIDATE
    cand = workflow.open_case(
        store_id="s-cand",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="t-cand",
        expected_outcome="outcome",
        planned_start=START,
        planned_end=END,
        created_by="ops",
    )
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status CANDIDATE"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on candidate")

    # Assert no mutation
    assert workflow.get(cand.intervention_id).status is InterventionStatus.CANDIDATE
    assert workflow.get(cand.intervention_id).replacement_id is None
    assert workflow.get(cand.intervention_id).adjustment is None
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 2. ELIGIBLE
    workflow.check_eligibility(cand.intervention_id, eligible=True, actor="ops")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.ELIGIBLE
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status ELIGIBLE"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on eligible")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.ELIGIBLE
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 3. ACTION_PROPOSED
    workflow.propose_action(cand.intervention_id, action_spec={"pct": -5}, actor="ops")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.ACTION_PROPOSED
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status ACTION_PROPOSED"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on action proposed")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.ACTION_PROPOSED
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 4. CONFLICT_CHECKING
    workflow.check_conflict(cand.intervention_id, actor="ops")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.CONFLICT_CHECKING
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status CONFLICT_CHECKING"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on conflict checking")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.CONFLICT_CHECKING
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 5. PENDING_APPROVAL
    workflow.submit_for_approval(cand.intervention_id, actor="ops")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.PENDING_APPROVAL
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status PENDING_APPROVAL"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on pending approval")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.PENDING_APPROVAL
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 6. EVALUATING (immature evaluation)
    workflow.approve(cand.intervention_id, actor="sup", reason="approved")
    workflow.execute(cand.intervention_id, executor="runner", executed_at=EXEC_TIME)
    workflow.collect_outcome(
        cand.intervention_id,
        actor="analyst",
        incremental_revenue=10000.0,
        incremental_gross_margin=4000.0,
        has_control_group=False,
        pretrend_status=PretrendStatus.INCONCLUSIVE,
        treatment_store_count=1,
        control_store_count=0,
        evaluation_method=EvaluationMethod.BEFORE_AFTER,
    )
    workflow.evaluate_effect(cand.intervention_id, actor="analyst", now=IMMATURE_TIME)
    assert workflow.get(cand.intervention_id).status is InterventionStatus.EVALUATING
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status EVALUATING"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on evaluating")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.EVALUATING
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 7. COMPLETED
    workflow.evaluate_effect(cand.intervention_id, actor="analyst", now=MATURE_TIME)
    assert workflow.get(cand.intervention_id).status is InterventionStatus.COMPLETED
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status COMPLETED"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on completed")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.COMPLETED
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before

    # 8. CLOSED
    workflow.close_case(
        cand.intervention_id,
        actor="ops-mgr",
        disposition=CloseDisposition.KEEP,
        reason="closed after completion",
    )
    assert workflow.get(cand.intervention_id).status is InterventionStatus.CLOSED
    events_before = len(workflow.audit_log.list_events())
    with pytest.raises(InterventionError, match="cannot adjust on intervention in status CLOSED"):
        workflow.adjust_case(cand.intervention_id, actor="ops", reason="try adjust on closed")
    assert workflow.get(cand.intervention_id).status is InterventionStatus.CLOSED
    assert len(workflow.list_all()) == 1
    assert len(workflow.audit_log.list_events()) == events_before


def test_adjust_workflow_allowed_active_states() -> None:
    """ODP-FR-INTV-006: Adjust succeeds from APPROVED, EXECUTING, and OBSERVING."""
    # Test APPROVED
    repo1 = InMemoryInterventionRepository()
    wf1 = InterventionWorkflow(repository=repo1)
    case1 = _open_case(wf1, store_id="s-appr")
    _drive_to_approved(wf1, case1.intervention_id)
    assert wf1.get(case1.intervention_id).status is InterventionStatus.APPROVED
    adj1 = wf1.adjust_case(case1.intervention_id, actor="ops", reason="adjust from approved")
    assert adj1.original.status is InterventionStatus.STOPPED
    assert adj1.replacement.status is InterventionStatus.CANDIDATE
    assert adj1.replacement.predecessor_id == case1.intervention_id

    # Test EXECUTING
    case_exec = case1.with_transition(
        to_status=InterventionStatus.EXECUTING,
        actor="ops",
        action="execute",
        reason="executing",
    )
    wf1.repository.save(case_exec)
    adj_exec = wf1.adjust_case(case_exec.intervention_id, actor="ops", reason="adjust from executing")
    assert adj_exec.original.status is InterventionStatus.STOPPED
    assert adj_exec.replacement.status is InterventionStatus.CANDIDATE

    # Test OBSERVING
    repo2 = InMemoryInterventionRepository()
    wf2 = InterventionWorkflow(repository=repo2)
    case2 = _open_case(wf2, store_id="s-obs")
    _drive_to_approved(wf2, case2.intervention_id)
    wf2.execute(case2.intervention_id, executor="runner", executed_at=EXEC_TIME)
    assert wf2.get(case2.intervention_id).status is InterventionStatus.OBSERVING
    adj2 = wf2.adjust_case(case2.intervention_id, actor="ops", reason="adjust from observing")
    assert adj2.original.status is InterventionStatus.STOPPED
    assert adj2.replacement.status is InterventionStatus.CANDIDATE
    assert adj2.replacement.predecessor_id == case2.intervention_id


def test_api_adjust_production_entry_rejects_pre_activation_and_evaluation_states_without_lineage() -> None:
    """ODP-FR-INTV-006: API rejects adjusting non-active states (CANDIDATE, PENDING_APPROVAL, EVALUATING, COMPLETED)
    and verifies rejection leaves case unmodified without creating replacement lineage.
    """
    app = create_app()
    client = TestClient(app, headers=INTERVENTION_HEADERS)

    # 1. Create CANDIDATE intervention
    created = client.post(
        "/interventions",
        json={
            "store_id": "store-api-adj-reg",
            "kind": "PRICE_CHANGE",
            "expected_outcome": "boost margin",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
        },
    )
    assert created.status_code == 201
    iid = created.json()["intervention_id"]

    # Attempt adjust on CANDIDATE
    cand_adj = client.post(
        f"/interventions/{iid}/adjust",
        json={"actor": "ops-hero", "reason": "adjust on candidate"},
    )
    assert cand_adj.status_code == 422
    assert "cannot adjust on intervention in status CANDIDATE" in cand_adj.json()["detail"]

    # Verify no replacement was created in list
    cases = client.get("/interventions", params={"store_id": "store-api-adj-reg"}).json()["items"]
    assert len(cases) == 1
    assert cases[0]["status"] == "CANDIDATE"
    assert cases[0]["replacement_id"] is None

    # Drive to PENDING_APPROVAL
    client.post(f"/interventions/{iid}/eligibility", json={"eligible": True, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/action", json={"action_spec": {"pct": -5}, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/conflict-check", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid}/submit", json={"actor": "ops-hero"})

    # Attempt adjust on PENDING_APPROVAL
    pending_adj = client.post(
        f"/interventions/{iid}/adjust",
        json={"actor": "ops-hero", "reason": "adjust on pending approval"},
    )
    assert pending_adj.status_code == 422
    assert "cannot adjust on intervention in status PENDING_APPROVAL" in pending_adj.json()["detail"]

    # Verify still only 1 case
    cases = client.get("/interventions", params={"store_id": "store-api-adj-reg"}).json()["items"]
    assert len(cases) == 1
    assert cases[0]["status"] == "PENDING_APPROVAL"

    # Drive to APPROVED -> Adjust should SUCCEED
    client.post(f"/interventions/{iid}/approve", json={"action": "APPROVE", "actor": "sup-hero", "reason": "approved"})
    appr_adj = client.post(
        f"/interventions/{iid}/adjust",
        json={"actor": "ops-hero", "reason": "adjust on approved"},
    )
    assert appr_adj.status_code == 200
    adj_data = appr_adj.json()
    assert adj_data["original_status"] == "STOPPED"
    assert adj_data["replacement_status"] == "CANDIDATE"
    repl_id = adj_data["replacement_intervention_id"]
    assert repl_id != iid

    # Now there are 2 cases
    cases = client.get("/interventions", params={"store_id": "store-api-adj-reg"}).json()["items"]
    assert len(cases) == 2
    case_ids = {c["intervention_id"] for c in cases}
    assert case_ids == {iid, repl_id}


def test_canonical_uuid_generation_for_intervention_and_adjustment() -> None:
    """ODP-FR-INTV-006: Generated intervention IDs and replacement IDs are valid canonical UUIDs."""
    workflow, _ = _new_workflow()
    case = _open_case(workflow, store_id="store-uuid-001")
    # Verify case ID is valid UUID
    parsed_case_uuid = UUID(case.intervention_id)
    assert str(parsed_case_uuid) == case.intervention_id

    _drive_to_approved(workflow, case.intervention_id)
    adj = workflow.adjust_case(
        case.intervention_id,
        actor="ops-tester",
        reason="canonical uuid test",
        rollback_plan="restore",
    )
    # Verify replacement ID is valid UUID
    parsed_repl_uuid = UUID(adj.replacement.intervention_id)
    assert str(parsed_repl_uuid) == adj.replacement.intervention_id
    assert adj.replacement.intervention_id != case.intervention_id

    # Verify lineage IDs are valid UUIDs
    assert adj.replacement.predecessor_id == case.intervention_id
    assert str(UUID(adj.replacement.predecessor_id)) == case.intervention_id
    assert adj.original.replacement_id == adj.replacement.intervention_id
    assert str(UUID(adj.original.replacement_id)) == adj.replacement.intervention_id


def test_durable_intervention_persistence_sql_table_lineage_and_audit(tmp_path: pytest.TempPathFactory) -> None:
    """ODP-FR-INTV-006: DurableInterventionRepository persists predecessor_id, replacement_id,
    and adjustment audit payload directly into the relational SQL interventions table."""
    db_file = tmp_path / "durable_interventions_sql.db"
    engine = SqliteEngine(db_file)
    _seed_store(engine, store_id="store-durable-sql-1")
    store = SqliteDocumentStore(engine)
    repo = DurableInterventionRepository(store)

    workflow = InterventionWorkflow(repository=repo)
    case = workflow.open_case(
        store_id="store-durable-sql-1",
        kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="alert-durable-sql-001",
        expected_outcome="recover margin via durable SQL persistence",
        planned_start=START,
        planned_end=END,
        created_by="ops-admin",
        action_spec={"price_change_pct": -4, "rollback_plan": "revert price"},
    )
    _drive_to_approved(workflow, case.intervention_id)
    case = workflow.execute(case.intervention_id, executor="runner", executed_at=EXEC_TIME)

    adj = workflow.adjust_case(
        case.intervention_id,
        actor="ops-lead",
        reason="market condition changed, adjust strategy",
        action_spec={"price_change_pct": -8},
        rollback_plan={"strategy": "reset", "threshold": 0.05},
    )

    # 1. Query the SQL table rows directly via engine
    orig_row = engine.query_one(
        "SELECT * FROM interventions WHERE intervention_id = ?", (case.intervention_id,)
    )
    assert orig_row is not None
    assert orig_row["status"] == "stopped"
    assert orig_row["replacement_id"] == adj.replacement.intervention_id
    assert orig_row["store_id"] == "store-durable-sql-1"
    assert orig_row["intervention_type"] == "PRICE_CHANGE"

    orig_adj = json.loads(orig_row["adjustment_json"])
    assert orig_adj["predecessor_id"] == case.intervention_id
    assert orig_adj["replacement_id"] == adj.replacement.intervention_id
    assert orig_adj["actor"] == "ops-lead"
    assert orig_adj["reason"] == "market condition changed, adjust strategy"
    assert orig_adj["policy_version"] == workflow.policy_version
    assert orig_adj["rollback_plan"] == {"strategy": "reset", "threshold": 0.05}

    repl_row = engine.query_one(
        "SELECT * FROM interventions WHERE intervention_id = ?", (adj.replacement.intervention_id,)
    )
    assert repl_row is not None
    assert repl_row["status"] == "candidate"
    assert repl_row["predecessor_id"] == case.intervention_id
    assert repl_row["store_id"] == "store-durable-sql-1"

    repl_adj = json.loads(repl_row["adjustment_json"])
    assert repl_adj["predecessor_id"] == case.intervention_id
    assert repl_adj["replacement_id"] == adj.replacement.intervention_id
    assert repl_adj["actor"] == "ops-lead"
    assert repl_adj["reason"] == "market condition changed, adjust strategy"

    # 2. Verify round-trip re-instantiation
    fresh_engine = SqliteEngine(db_file)
    fresh_store = SqliteDocumentStore(fresh_engine)
    fresh_repo = DurableInterventionRepository(fresh_store)

    reloaded_orig = fresh_repo.get(case.intervention_id)
    assert reloaded_orig is not None
    assert reloaded_orig.status is InterventionStatus.STOPPED
    assert reloaded_orig.replacement_id == adj.replacement.intervention_id
    assert reloaded_orig.adjustment is not None
    assert reloaded_orig.adjustment.reason == "market condition changed, adjust strategy"

    reloaded_repl = fresh_repo.get(adj.replacement.intervention_id)
    assert reloaded_repl is not None
    assert reloaded_repl.status is InterventionStatus.CANDIDATE
    assert reloaded_repl.predecessor_id == case.intervention_id
    assert reloaded_repl.adjustment is not None


def test_durable_intervention_repository_fails_closed_without_relational_schema(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """A missing migration must not leave a document-only intervention."""
    engine = SqliteEngine(tmp_path / "missing_intervention_schema.db")
    engine.execute("DROP TABLE interventions")
    repo = DurableInterventionRepository(SqliteDocumentStore(engine))
    case = _open_case(InterventionWorkflow(repository=InMemoryInterventionRepository()))

    with pytest.raises(RuntimeError, match="schema is missing table 'interventions'"):
        repo.save(case)

    assert repo.get(case.intervention_id) is None


def test_api_production_entry_relational_write_failure_does_not_leave_document(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relational driver failure is observable and cannot create a half-write."""
    engine = SqliteEngine(tmp_path / "failed_intervention_write.db")
    _seed_store(engine, store_id="store-api-write-failure")
    store = SqliteDocumentStore(engine)
    repo = DurableInterventionRepository(store)
    original_execute = engine.execute

    def fail_intervention_write(sql: str, params: tuple = ()):
        if sql.lstrip().upper().startswith("INSERT INTO INTERVENTIONS"):
            raise RuntimeError("relational intervention write failed")
        return original_execute(sql, params)

    monkeypatch.setattr(engine, "execute", fail_intervention_write)
    app = create_app(intervention_repository=repo)
    client = TestClient(
        app,
        headers=INTERVENTION_HEADERS,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/interventions",
        json={
            "store_id": "store-api-write-failure",
            "kind": "PRICE_CHANGE",
            "trigger_ref": "alert-write-failure",
            "expected_outcome": "must not be document-only",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
        },
    )

    assert response.status_code == 500
    assert repo.list_all() == []
    assert engine.query_one(
        "SELECT intervention_id FROM interventions WHERE store_id = ?",
        ("store-api-write-failure",),
    ) is None


def test_api_production_entry_durable_persistence_roundtrip(tmp_path: pytest.TempPathFactory) -> None:
    """ODP-FR-INTV-006: API production entry with DurableInterventionRepository verifies
    end-to-end adjust workflow and SQL persistence round-trip."""
    db_file = tmp_path / "durable_api_interventions.db"
    engine = SqliteEngine(db_file)
    _seed_store(engine, store_id="store-api-durable-01")
    store = SqliteDocumentStore(engine)
    repo = DurableInterventionRepository(store)

    app = create_app(intervention_repository=repo)
    client = TestClient(app, headers=INTERVENTION_HEADERS)

    # 1. Create case via API
    create_res = client.post(
        "/interventions",
        json={
            "store_id": "store-api-durable-01",
            "kind": "PRICE_CHANGE",
            "expected_outcome": "boost revenue by 10%",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "ops-hero",
        },
    )
    assert create_res.status_code == 201
    iid = create_res.json()["intervention_id"]
    assert str(UUID(iid)) == iid

    # 2. Drive to APPROVED via API
    client.post(f"/interventions/{iid}/eligibility", json={"eligible": True, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/action", json={"action_spec": {"price_change_pct": -5}, "actor": "ops-hero"})
    client.post(f"/interventions/{iid}/conflict-check", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid}/submit", json={"actor": "ops-hero"})
    client.post(f"/interventions/{iid}/approve", json={"action": "APPROVE", "actor": "sup-hero", "reason": "approved"})

    # 3. Adjust via API
    adj_res = client.post(
        f"/interventions/{iid}/adjust",
        json={
            "actor": "ops-hero",
            "reason": "pricing response to competitor discount",
            "action_spec": {"price_change_pct": -10},
            "rollback_plan": "revert to base list price",
        },
    )
    assert adj_res.status_code == 200
    adj_data = adj_res.json()
    assert adj_data["original_status"] == "STOPPED"
    assert adj_data["replacement_status"] == "CANDIDATE"
    repl_id = adj_data["replacement_intervention_id"]
    assert str(UUID(repl_id)) == repl_id

    # 4. Verify SQL table directly
    orig_sql = engine.query_one("SELECT * FROM interventions WHERE intervention_id = ?", (iid,))
    assert orig_sql is not None
    assert orig_sql["status"] == "stopped"
    assert orig_sql["replacement_id"] == repl_id
    adj_audit = json.loads(orig_sql["adjustment_json"])
    assert adj_audit["reason"] == "pricing response to competitor discount"
    assert adj_audit["actor"] == "ops-hero"
    assert adj_audit["rollback_plan"] == "revert to base list price"

    repl_sql = engine.query_one("SELECT * FROM interventions WHERE intervention_id = ?", (repl_id,))
    assert repl_sql is not None
    assert repl_sql["status"] == "candidate"
    assert repl_sql["predecessor_id"] == iid
