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

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.intervention import (
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
from tests.integration._authz import INTERVENTION_HEADERS

START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
END = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
EXEC_TIME = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
# PRICE_CHANGE default window is 21 + 7 days, so maturity is 28 days after exec.
MATURE_TIME = EXEC_TIME + timedelta(days=29)
IMMATURE_TIME = EXEC_TIME + timedelta(days=3)


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
            "expected_outcome": "recover GM",
            "planned_start": START.isoformat(),
            "planned_end": END.isoformat(),
            "created_by": "supervisor-a",
        },
        headers={"Idempotency-Key": "iv-idem-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["intervention_id"] == iid

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
