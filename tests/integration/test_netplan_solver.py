"""Integration tests for NetPlan (ODP-R5-002).

Acceptance focus:

* scenario builder produces discrete OPEN/KEEP/IMPROVE/MOVE/EXIT action domains
* solver enforces hard constraints and returns alternatives
* infeasible scenarios include structured diagnosis without auto-relaxing limits
* lifecycle records approval, execution, outcome, and status history
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.netplan import (
    BUSINESS_UAT_UNVERIFIED,
    BUSINESS_UAT_VERIFIED,
    GOVERNED_DISABLED,
    GOVERNED_ENABLED,
    ApprovalRecord,
    CandidateSiteInput,
    ExistingStoreInput,
    FixedManagementApprovalReceiptVerifier,
    InMemoryNetPlanRepository,
    InvalidNetPlanTransitionError,
    ManagementApprovalExpectation,
    ManagementApprovalReceipt,
    ManagementApprovalVerification,
    ManagementBaselineInput,
    NetPlanApprovalError,
    NetPlanScenarioStatus,
    NetPlanService,
    ScenarioBuildRequest,
    build_scenario_options,
    compare_solver_against_management_baseline,
    compute_solver_problem_hash,
    run_netplan_solver_batch,
)
from shared.auth import Role
from solver.netplan import (
    NETPLAN_POLICY_VERSION,
    STATUS_FEASIBLE,
    STATUS_INFEASIBLE,
    STATUS_OPTIMAL,
    ActionOption,
    NetPlanConstraints,
    NetworkAction,
    NetworkPlanSolveResult,
    solve_network_plan,
)
from tests.integration._authz import auth_headers

MOMENT = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
APPROVAL_SOURCE = "management-approval-system"
APPROVAL_PRINCIPAL = "principal://network-strategy-director"
APPROVAL_ROLE = "network-strategy-director"


def _stores() -> tuple[ExistingStoreInput, ...]:
    return (
        ExistingStoreInput(
            store_id="store-001",
            baseline_gross_margin=500_000,
            improve_gross_margin_uplift=90_000,
            improve_cost=140_000,
            move_gross_margin_uplift=120_000,
            move_cost=260_000,
            keep_risk=0.10,
            improve_risk=0.18,
            move_risk=0.34,
            source_snapshot_ids=("network-store-001",),
        ),
        ExistingStoreInput(
            store_id="store-002",
            baseline_gross_margin=350_000,
            improve_gross_margin_uplift=40_000,
            improve_cost=80_000,
            move_gross_margin_uplift=55_000,
            move_cost=180_000,
            keep_risk=0.12,
            improve_risk=0.16,
            move_risk=0.30,
            source_snapshot_ids=("network-store-002",),
        ),
    )


def _sites() -> tuple[CandidateSiteInput, ...]:
    return (
        CandidateSiteInput(
            candidate_site_id="candidate-a",
            expected_gross_margin=260_000,
            open_cost=190_000,
            risk_score=0.22,
            source_snapshot_ids=("sitescore-candidate-a",),
        ),
        CandidateSiteInput(
            candidate_site_id="candidate-b",
            expected_gross_margin=210_000,
            open_cost=150_000,
            risk_score=0.19,
            source_snapshot_ids=("sitescore-candidate-b",),
        ),
    )


def _constraints(**overrides: object) -> NetPlanConstraints:
    values = {
        "max_budget": 420_000,
        "min_expected_gross_margin": 1_100_000,
        "min_capacity_delta": 1,
        "max_average_risk": 0.22,
        "min_action_counts": {NetworkAction.OPEN: 1},
        "max_action_counts": {NetworkAction.MOVE: 1, NetworkAction.EXIT: 0},
    }
    values.update(overrides)
    return NetPlanConstraints(**values)


def _management_baseline(
    *,
    actions_by_entity: dict[str, NetworkAction] | None = None,
    receipt_id: str = "receipt-netplan-2026q3-001",
    baseline_id: str = "baseline-netplan-2026q3",
    baseline_name: str = "2026 Q3 management baseline",
    scenario_id: str = "scenario-netplan-2026q3",
    source_snapshot_ids: tuple[str, ...] = (
        "network-store-001",
        "network-store-002",
        "sitescore-candidate-a",
        "sitescore-candidate-b",
    ),
    scope: str = "tenant:tenant-1",
    release_id: str = "2026Q3",
) -> ManagementBaselineInput:
    return ManagementBaselineInput(
        baseline_id=baseline_id,
        baseline_name=baseline_name,
        scenario_id=scenario_id,
        actions_by_entity=actions_by_entity
        or {
            "store-001": NetworkAction.IMPROVE,
            "store-002": NetworkAction.KEEP,
            "candidate-a": NetworkAction.OPEN,
            "candidate-b": NetworkAction.KEEP,
        },
        approval_receipt_id=receipt_id,
        source_snapshot_ids=source_snapshot_ids,
        scope=scope,
        release_id=release_id,
    )


def _approval_receipt(
    baseline: ManagementBaselineInput,
    options: dict[str, tuple],
    constraints: NetPlanConstraints,
    **changes: object,
) -> ManagementApprovalReceipt:
    receipt = ManagementApprovalReceipt(
        receipt_id=baseline.approval_receipt_id,
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=APPROVAL_ROLE,
        decision="APPROVED",
        approval_reference_id="APR-NETPLAN-2026Q3-001",
        issued_at="2026-06-01T00:00:00Z",
        expires_at="2026-08-01T00:00:00Z",
        scenario_id=baseline.scenario_id,
        baseline_id=baseline.baseline_id,
        baseline_name=baseline.baseline_name,
        scope=baseline.scope,
        release_id=baseline.release_id,
        policy_version=constraints.policy_version,
        actions_by_entity=baseline.actions_by_entity,
        source_snapshot_ids=baseline.source_snapshot_ids,
        baseline_content_hash=baseline.compute_canonical_hash(constraints=constraints),
        solver_problem_hash=compute_solver_problem_hash(options, constraints, 100_000.0),
        receipt_hash="",
    )
    receipt = replace(receipt, **changes)
    if "receipt_hash" not in changes:
        receipt = replace(receipt, receipt_hash=receipt.compute_receipt_hash())
    return receipt


def _approval_verifier(
    receipt: ManagementApprovalReceipt,
    *,
    source_system: str = APPROVAL_SOURCE,
    principal_id: str = APPROVAL_PRINCIPAL,
    principal_role: str = APPROVAL_ROLE,
    clock: datetime = MOMENT,
) -> FixedManagementApprovalReceiptVerifier:
    return FixedManagementApprovalReceiptVerifier(
        receipts={receipt.receipt_id: receipt},
        source_system=source_system,
        principal_id=principal_id,
        principal_role=principal_role,
        clock=lambda: clock,
    )


def _compare(
    *,
    options: dict[str, tuple],
    constraints: NetPlanConstraints,
    solve_result: NetworkPlanSolveResult,
    baseline: ManagementBaselineInput,
    receipt: ManagementApprovalReceipt | None = None,
):
    return compare_solver_against_management_baseline(
        options_by_entity=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        approval_verifier=(
            _approval_verifier(receipt)
            if receipt is not None
            else None
        ),
    )


def test_scenario_builder_and_solver_return_optimal_plan_with_alternatives() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    result = solve_network_plan(options_by_entity=options, constraints=_constraints())

    assert result.solver_status == STATUS_OPTIMAL
    assert result.infeasible is False
    assert result.budget_usage <= 420_000
    assert result.expected_gross_margin >= 1_100_000
    assert result.action_counts[NetworkAction.OPEN] >= 1
    assert result.action_counts.get(NetworkAction.EXIT, 0) == 0
    assert result.alternative_plan_available is True
    assert result.alternatives[0].objective_value <= result.objective_value
    assert all(action.action in set(NetworkAction) for action in result.selected_actions)
    summary = result.to_dict()
    assert summary["alternative_plan_available"] is True
    assert {
        "objective_value",
        "budget_usage",
        "binding_constraints",
        "solver_status",
    } <= summary.keys()


def test_infeasible_scenario_reports_structured_diagnosis_without_relaxing() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    result = solve_network_plan(
        options_by_entity=options,
        constraints=_constraints(max_budget=100_000, min_expected_gross_margin=2_000_000),
    )

    assert result.solver_status == STATUS_INFEASIBLE
    assert result.infeasible is True
    assert result.selected_actions == ()
    diagnosis = [item.to_dict() for item in result.diagnostics]
    assert {item["violated_constraint"] for item in diagnosis} >= {
        "max_budget",
        "min_expected_gross_margin",
    }
    for item in diagnosis:
        assert {
            "violated_constraint",
            "affected_stores",
            "required_relaxation",
            "business_impact",
            "suggested_action",
        } <= item.keys()


def test_service_lifecycle_tracks_approval_execution_and_outcome() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solved = solve_network_plan(options_by_entity=options, constraints=constraints)
    approved_plan = _management_baseline(
        actions_by_entity={
            action.entity_id: action.action for action in solved.selected_actions
        },
        baseline_id="netplan-scenario-001",
        baseline_name="2026 Q3 expansion",
        scenario_id="netplan-scenario-001",
        source_snapshot_ids=tuple(
            sorted(
                {
                    snapshot_id
                    for action in solved.selected_actions
                    for snapshot_id in action.source_snapshot_ids
                }
            )
        ),
    )
    authority_receipt = _approval_receipt(approved_plan, options, constraints)
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(
        repository=repository,
        approval_verifier=_approval_verifier(authority_receipt),
    )
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name="2026 Q3 expansion",
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=constraints,
        scenario_id="netplan-scenario-001",
        correlation_id="corr-netplan-1",
        created_at=MOMENT,
    )

    solve = service.solve(scenario.scenario_id, solved_at=MOMENT)
    assert solve.result.solver_status == STATUS_OPTIMAL
    service.submit_for_approval(scenario.scenario_id, actor="network-planner", occurred_at=MOMENT)
    approval = service.decide(
        scenario.scenario_id,
        actor_id=APPROVAL_PRINCIPAL,
        reason="budget and risk within quarterly policy",
        approval_receipt_id=authority_receipt.receipt_id,
        decided_at=MOMENT,
    )
    assert approval.is_approved is True
    assert approval.authentic_approval_verified is True
    assert approval.policy_version == NETPLAN_POLICY_VERSION
    approval_payload = approval.to_dict()
    assert approval_payload["authority_attestation_id"]
    assert approval_payload["authority_binding_hash"]
    assert approval_payload["authority_verified_at"]
    assert approval_payload["business_uat_status"] == BUSINESS_UAT_VERIFIED
    assert approval_payload["governance_status"] == GOVERNED_ENABLED

    execution = service.execute(scenario.scenario_id, executed_by="ops-runner", executed_at=MOMENT)
    assert len(execution.actions) == len(solve.result.selected_actions)
    outcome = service.record_outcome(
        scenario.scenario_id,
        actual_gross_margin=solve.result.expected_gross_margin + 25_000,
        observed_at=MOMENT,
        source_snapshot_ids=("actuals-2026q3",),
        actor="network-analyst",
    )
    assert outcome.variance == 25_000
    assert outcome.label_registry_payload["label_type"] == "netplan_realized_gross_margin"

    closed = service.close(scenario.scenario_id, actor="network-analyst", occurred_at=MOMENT)
    assert closed.status is NetPlanScenarioStatus.CLOSED
    assert [transition.to_status for transition in closed.status_history] == [
        NetPlanScenarioStatus.SOLVED,
        NetPlanScenarioStatus.PENDING_APPROVAL,
        NetPlanScenarioStatus.APPROVED,
        NetPlanScenarioStatus.EXECUTED,
        NetPlanScenarioStatus.OUTCOME_OBSERVED,
        NetPlanScenarioStatus.CLOSED,
    ]
    assert all(transition.actor and transition.reason for transition in closed.status_history)


def test_backdated_decision_cannot_revive_an_expired_authority_receipt() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solved = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline(
        actions_by_entity={action.entity_id: action.action for action in solved.selected_actions},
        baseline_id="netplan-expiry-001",
        baseline_name="expiry authority clock",
        scenario_id="netplan-expiry-001",
        source_snapshot_ids=tuple(
            sorted(
                {
                    snapshot_id
                    for action in solved.selected_actions
                    for snapshot_id in action.source_snapshot_ids
                }
            )
        ),
    )
    expired_receipt = _approval_receipt(
        baseline,
        options,
        constraints,
        expires_at="2026-06-20T00:00:00Z",
    )
    service = NetPlanService(approval_verifier=_approval_verifier(expired_receipt))
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name=baseline.baseline_name,
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=constraints,
        scenario_id=baseline.scenario_id,
        correlation_id="corr-authority-clock",
        created_at=MOMENT,
    )
    service.solve(scenario.scenario_id, solved_at=MOMENT)
    service.submit_for_approval(scenario.scenario_id, occurred_at=MOMENT)

    with pytest.raises(NetPlanApprovalError, match="approval_expired"):
        service.decide(
            scenario.scenario_id,
            actor_id=APPROVAL_PRINCIPAL,
            reason="caller attempts to backdate evaluation",
            approval_receipt_id=expired_receipt.receipt_id,
            decided_at=datetime(2026, 6, 10, tzinfo=UTC),
        )

    persisted = service.repository.get_scenario(scenario.scenario_id)
    assert persisted is not None
    assert persisted.status is NetPlanScenarioStatus.PENDING_APPROVAL


def test_infeasible_scenario_cannot_skip_to_approval() -> None:
    service = NetPlanService()
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name="impossible",
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=_constraints(max_budget=1),
        scenario_id="netplan-scenario-infeasible",
        correlation_id="corr-netplan-bad",
    )
    solve = service.solve(scenario.scenario_id)
    assert solve.result.infeasible is True

    with pytest.raises(InvalidNetPlanTransitionError):
        service.submit_for_approval(scenario.scenario_id)


def test_batch_worker_solves_multiple_scenarios_and_persists_results() -> None:
    repository = InMemoryNetPlanRepository()
    result = run_netplan_solver_batch(
        requests=[
            ScenarioBuildRequest(
                tenant_id="tenant-1",
                scenario_name="batch solve",
                planning_horizon="2026Q3",
                existing_stores=_stores(),
                candidate_sites=_sites(),
                constraints=_constraints(),
                scenario_id="netplan-batch-001",
                correlation_id="corr-batch-1",
            )
        ],
        job_id="netplan-job-1",
        solved_at=MOMENT,
        repository=repository,
    )

    assert result.status == "succeeded"
    assert result.to_dict()["scenarios"][0]["solve"]["result"]["solver_status"] == STATUS_OPTIMAL
    assert repository.get_scenario("netplan-batch-001").status is NetPlanScenarioStatus.SOLVED


def test_management_baseline_comparison_deterministic_proof() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.baseline_id == "baseline-netplan-2026q3"
    assert receipt.baseline_feasible is True
    assert receipt.superior_or_equal is True
    assert receipt.approval_verified is True
    assert receipt.baseline_objective_value is not None
    assert receipt.solver_objective_value >= receipt.baseline_objective_value
    assert receipt.objective_gain_over_baseline is not None
    assert receipt.objective_gain_over_baseline >= 0.0
    assert receipt.baseline_canonical_hash != ""
    assert receipt.solver_problem_hash != ""
    assert receipt.solver_result_hash != ""
    assert receipt.scenario_hash != ""
    assert receipt.source_snapshot_hash != ""
    assert receipt.actions_domain_hash != ""
    assert receipt.approval_receipt_hash == authority_receipt.receipt_hash
    assert receipt.comparison_output_hash != ""
    assert receipt.baseline_canonical_hash == baseline.compute_canonical_hash(constraints=constraints)


def test_infeasible_management_baseline_identified_with_violations() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints(
        max_budget=600_000,
        min_capacity_delta=1,
        min_action_counts={NetworkAction.OPEN: 1, NetworkAction.MOVE: 1},
    )
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline(
        actions_by_entity={
            "store-001": NetworkAction.KEEP,
            "store-002": NetworkAction.KEEP,
            "candidate-a": NetworkAction.KEEP,
            "candidate-b": NetworkAction.KEEP,
        },
    )
    authority_receipt = _approval_receipt(baseline, options, constraints)
    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.baseline_feasible is False
    assert receipt.superior_or_equal is False
    assert "min_capacity_delta" in receipt.baseline_constraint_violations
    assert {
        "min_action_counts.OPEN",
        "min_action_counts.MOVE",
    } & set(receipt.baseline_constraint_violations)


def test_missing_authoritative_readback_stays_governed_disabled() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
    )

    assert receipt.baseline_feasible is False
    assert receipt.superior_or_equal is False
    assert receipt.approval_verified is False
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED
    assert "authoritative_approval_verifier_missing" in receipt.baseline_constraint_violations


def test_baseline_extra_entities_domain_mismatch_fails_comparison() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline(
        actions_by_entity={
            "store-001": NetworkAction.KEEP,
            "store-002": NetworkAction.KEEP,
            "candidate-a": NetworkAction.OPEN,
            "candidate-b": NetworkAction.KEEP,
            "extra-store-999": NetworkAction.OPEN,
        },
    )
    authority_receipt = _approval_receipt(baseline, options, constraints)
    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.baseline_feasible is False
    assert receipt.superior_or_equal is False
    assert "extra_baseline_entities" in receipt.baseline_constraint_violations


def test_unbound_solve_result_fails_comparison() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    other_options = build_scenario_options(existing_stores=_stores()[:1])
    other_constraints = NetPlanConstraints(max_budget=500_000)
    other_solve_result = solve_network_plan(options_by_entity=other_options, constraints=other_constraints)
    assert other_solve_result.solver_status == STATUS_OPTIMAL
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=other_solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.baseline_feasible is False
    assert receipt.superior_or_equal is False
    assert "unbound_solve_result_domain" in receipt.baseline_constraint_violations


def test_actor_string_cannot_approve_without_authoritative_readback() -> None:
    service = NetPlanService()
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name="test approval auth",
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=_constraints(),
        correlation_id="corr-auth",
    )
    service.solve(scenario.scenario_id)
    service.submit_for_approval(scenario.scenario_id)

    from modules.netplan import NetPlanApprovalError

    with pytest.raises(NetPlanApprovalError) as exc_info:
        service.decide(
            scenario.scenario_id,
            actor_id="Human/Ops:strategy-director:spoofed",
            reason="actor strings are not authentication",
            approval_receipt_id="ANY",
        )
    assert "verifier is not configured" in str(exc_info.value)


def test_self_consistent_caller_receipt_cannot_self_attest_public_approval() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    baseline = _management_baseline()
    caller_receipt = replace(
        _approval_receipt(baseline, options, constraints),
        source_system="caller-source",
        principal_id="caller-principal",
        principal_role="caller-role",
        receipt_hash="",
    )
    caller_receipt = replace(
        caller_receipt,
        receipt_hash=caller_receipt.compute_receipt_hash(),
    )
    caller_verification = ManagementApprovalVerification(
        verified=True,
        receipt=caller_receipt,
        violations=(),
    )
    approval = ApprovalRecord(
        approval_id="caller-approval",
        scenario_id=baseline.scenario_id,
        actor_id=caller_receipt.principal_id,
        decision="approved",
        reason="caller asserts its own authority",
        decided_at=MOMENT,
        policy_version=constraints.policy_version,
        authority_receipt=caller_receipt,
        authority_verification=caller_verification,
        verification_violations=(),
    )

    assert caller_receipt.receipt_hash == caller_receipt.compute_receipt_hash()
    assert approval.authentic_approval_verified is False
    assert approval.to_dict()["business_uat_status"] == BUSINESS_UAT_UNVERIFIED
    assert approval.to_dict()["governance_status"] == GOVERNED_DISABLED


def test_repository_and_api_serialize_forged_public_approval_as_disabled() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    baseline = _management_baseline(
        baseline_id="netplan-forged-public-001",
        baseline_name="forged public approval boundary",
        scenario_id="netplan-forged-public-001",
    )
    caller_receipt = replace(
        _approval_receipt(baseline, options, constraints),
        source_system="caller-source",
        principal_id="caller-principal",
        principal_role="caller-role",
        receipt_hash="",
    )
    caller_receipt = replace(
        caller_receipt,
        receipt_hash=caller_receipt.compute_receipt_hash(),
    )
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(repository=repository)
    scenario = service.create_scenario(
        tenant_id="tenant-1",
        scenario_name=baseline.baseline_name,
        planning_horizon="2026Q3",
        existing_stores=_stores(),
        candidate_sites=_sites(),
        constraints=constraints,
        scenario_id=baseline.scenario_id,
        correlation_id="corr-forged-public",
        created_at=MOMENT,
    )
    repository.save_approval(
        ApprovalRecord(
            approval_id="caller-repository-approval",
            scenario_id=scenario.scenario_id,
            actor_id=caller_receipt.principal_id,
            decision="approved",
            reason="direct repository mutation",
            decided_at=MOMENT,
            policy_version=constraints.policy_version,
            authority_receipt=caller_receipt,
            verification_violations=(),
        )
    )

    persisted = repository.list_approvals(scenario.scenario_id)[0].to_dict()
    assert persisted["authentic_approval_verified"] is False
    assert persisted["business_uat_status"] == BUSINESS_UAT_UNVERIFIED
    assert persisted["governance_status"] == GOVERNED_DISABLED

    client = TestClient(
        create_app(netplan_repository=repository),
        headers=auth_headers(Role.EXECUTIVE),
    )
    detail = client.get(f"/api/v1/netplan/scenarios/{scenario.scenario_id}")
    assert detail.status_code == 200
    public_approval = detail.json()["approvals"][0]
    assert public_approval["authentic_approval_verified"] is False
    assert public_approval["business_uat_status"] == BUSINESS_UAT_UNVERIFIED
    assert public_approval["governance_status"] == GOVERNED_DISABLED

    mutation = client.post(
        f"/api/v1/netplan/scenarios/{scenario.scenario_id}/decide",
        json={
            "actor_id": "caller-principal",
            "reason": "caller tries to inject an approval receipt",
            "decision": "approved",
            "approval_receipt_id": caller_receipt.receipt_id,
            "source_system": caller_receipt.source_system,
            "principal_role": caller_receipt.principal_role,
            "receipt_hash": caller_receipt.receipt_hash,
        },
    )
    assert mutation.status_code == 422
    assert len(repository.list_approvals(scenario.scenario_id)) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_id", "caller-scenario"),
        ("actor_id", "caller-principal"),
        ("policy_version", "caller-policy"),
    ],
)
def test_authority_attestation_cannot_be_replayed_across_approval_record_fields(
    field: str,
    value: str,
) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    expectation = ManagementApprovalExpectation(
        receipt_id=baseline.approval_receipt_id,
        scenario_id=baseline.scenario_id,
        baseline_id=baseline.baseline_id,
        baseline_name=baseline.baseline_name,
        scope=baseline.scope,
        release_id=baseline.release_id,
        policy_version=constraints.policy_version,
        actions_by_entity=baseline.actions_by_entity,
        source_snapshot_ids=baseline.source_snapshot_ids,
        baseline_content_hash=baseline.compute_canonical_hash(constraints=constraints),
        solver_problem_hash=compute_solver_problem_hash(options, constraints, 100_000.0),
    )
    verification = _approval_verifier(authority_receipt).verify(expectation)
    approval = ApprovalRecord(
        approval_id="authority-replay",
        scenario_id=authority_receipt.scenario_id,
        actor_id=authority_receipt.principal_id,
        decision="approved",
        reason="valid authority verification before record mutation",
        decided_at=MOMENT,
        policy_version=authority_receipt.policy_version,
        authority_receipt=authority_receipt,
        authority_verification=verification,
    )

    assert approval.authentic_approval_verified is True
    assert replace(approval, **{field: value}).authentic_approval_verified is False


@pytest.mark.parametrize(
    ("receipt_id", "expected_violation"),
    [
        ("ANY", "approval_receipt_id_invalid"),
        ("UNVERIFIED", "approval_receipt_id_invalid"),
        ("missing-receipt", "authoritative_approval_unresolved"),
    ],
)
def test_arbitrary_receipt_ids_fail_closed(
    receipt_id: str,
    expected_violation: str,
) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline(receipt_id=receipt_id)
    authority_receipt = _approval_receipt(baseline, options, constraints)
    verifier = _approval_verifier(authority_receipt)
    if receipt_id == "missing-receipt":
        verifier = FixedManagementApprovalReceiptVerifier(
            receipts={},
            source_system=APPROVAL_SOURCE,
            principal_id=APPROVAL_PRINCIPAL,
            principal_role=APPROVAL_ROLE,
            clock=lambda: MOMENT,
        )

    receipt = compare_solver_against_management_baseline(
        options_by_entity=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        approval_verifier=verifier,
    )

    assert receipt.superior_or_equal is False
    assert expected_violation in receipt.baseline_constraint_violations
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize("field", ["source_system", "principal_id", "principal_role"])
def test_authority_configuration_rejects_wildcards(field: str) -> None:
    values = {
        "source_system": APPROVAL_SOURCE,
        "principal_id": APPROVAL_PRINCIPAL,
        "principal_role": APPROVAL_ROLE,
    }
    values[field] = "ANY"

    with pytest.raises(ValueError, match="fixed non-wildcard"):
        FixedManagementApprovalReceiptVerifier(receipts={}, **values)


@pytest.mark.parametrize(
    ("changes", "expected_violation"),
    [
        ({"source_system": "caller-supplied-source"}, "approval_source_system_mismatch"),
        ({"principal_id": "Human/Ops:spoofed"}, "approval_principal_mismatch"),
        ({"principal_role": "untrusted-role"}, "approval_principal_role_mismatch"),
        ({"decision": "PENDING"}, "approval_decision_not_active"),
        ({"approval_reference_id": " "}, "approval_reference_missing"),
        ({"issued_at": ""}, "approval_issued_at_invalid"),
        ({"issued_at": "2026-06-01T00:00:00+08:00"}, "approval_issued_at_invalid"),
        ({"issued_at": "2026-07-01T00:00:00Z"}, "approval_issued_in_future"),
        ({"expires_at": "2026-06-28T08:59:59Z"}, "approval_expired"),
        ({"expires_at": "2026-05-01T00:00:00Z"}, "approval_time_window_invalid"),
        ({"expires_at": "2026-08-01T00:00:00+00:00"}, "approval_expires_at_invalid"),
        ({"scope": "tenant:other"}, "approval_scope_mismatch"),
        ({"release_id": "2026Q4"}, "approval_release_mismatch"),
        ({"policy_version": "caller-policy"}, "approval_policy_version_mismatch"),
        ({"baseline_content_hash": "0" * 64}, "approval_baseline_hash_mismatch"),
        ({"solver_problem_hash": "1" * 64}, "approval_solver_problem_hash_mismatch"),
        (
            {"actions_by_entity": {"store-001": NetworkAction.EXIT}},
            "approval_actions_domain_mismatch",
        ),
        (
            {"source_snapshot_ids": ("caller-snapshot",)},
            "approval_source_snapshots_mismatch",
        ),
        ({"receipt_hash": "2" * 64}, "approval_receipt_integrity_mismatch"),
    ],
)
def test_authoritative_receipt_mutations_fail_closed(
    changes: dict[str, object],
    expected_violation: str,
) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints, **changes)

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.superior_or_equal is False
    assert expected_violation in receipt.baseline_constraint_violations
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


def test_caller_baseline_mutation_cannot_redefine_expected_hashes() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    approved_baseline = _management_baseline()
    authority_receipt = _approval_receipt(approved_baseline, options, constraints)
    caller_mutation = replace(
        approved_baseline,
        actions_by_entity={
            **approved_baseline.actions_by_entity,
            "store-001": NetworkAction.EXIT,
        },
    )

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=caller_mutation,
        receipt=authority_receipt,
    )

    assert receipt.superior_or_equal is False
    assert {
        "approval_actions_domain_mismatch",
        "approval_baseline_hash_mismatch",
    } <= set(receipt.baseline_constraint_violations)
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize(
    ("mutation", "expected_violation"),
    [
        ("objective", "solve_objective_mismatch"),
        ("action", "selected_option_not_in_problem"),
        ("status", "solve_status_mismatch"),
        ("feasibility", "solve_feasibility_flag_mismatch"),
        ("counts", "solve_action_counts_mismatch"),
    ],
)
def test_forged_solve_result_mutations_fail_closed(
    mutation: str,
    expected_violation: str,
) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)

    if mutation == "objective":
        forged = replace(solve_result, objective_value=solve_result.objective_value + 1)
    elif mutation == "action":
        forged_action = replace(
            solve_result.selected_actions[0],
            expected_gross_margin=solve_result.selected_actions[0].expected_gross_margin + 1,
        )
        forged = replace(
            solve_result,
            selected_actions=(forged_action, *solve_result.selected_actions[1:]),
        )
    elif mutation == "status":
        forged = replace(solve_result, solver_status=STATUS_INFEASIBLE)
    elif mutation == "feasibility":
        forged = replace(solve_result, infeasible=True)
    else:
        forged = replace(solve_result, action_counts={NetworkAction.EXIT: 99})

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert receipt.superior_or_equal is False
    assert expected_violation in receipt.baseline_constraint_violations
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize(
    ("budget_cost", "risk_score"),
    [
        (100.00004, 0.22),
        (100.0, 0.22004),
    ],
)
def test_raw_hard_constraint_overflow_never_reaches_governed_enablement(
    budget_cost: float,
    risk_score: float,
) -> None:
    options = {
        "store-raw-boundary": (
            ActionOption(
                entity_id="store-raw-boundary",
                action=NetworkAction.KEEP,
                expected_gross_margin=100.0,
                budget_cost=budget_cost,
                risk_score=risk_score,
            ),
        )
    }
    constraints = NetPlanConstraints(max_budget=100.0, max_average_risk=0.22)
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline(
        actions_by_entity={"store-raw-boundary": NetworkAction.KEEP},
        source_snapshot_ids=(),
    )
    authority_receipt = _approval_receipt(baseline, options, constraints)

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=solve_result,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert solve_result.solver_status == STATUS_INFEASIBLE
    assert solve_result.selected_actions == ()
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize("target", ["primary", "alternative"])
def test_sub_tolerance_scalar_forgery_fails_exact_recomputation(target: str) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)

    if target == "primary":
        forged = replace(
            solve_result,
            objective_value=solve_result.objective_value + 5e-7,
        )
        expected_violation = "solve_objective_mismatch"
    else:
        forged_alternative = replace(
            solve_result.alternatives[0],
            objective_value=solve_result.alternatives[0].objective_value + 5e-7,
        )
        forged = replace(
            solve_result,
            alternatives=(forged_alternative, *solve_result.alternatives[1:]),
        )
        expected_violation = "alternative_metrics_mismatch"

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert expected_violation in receipt.baseline_constraint_violations
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize("public_path", ["solve", "problem_hash"])
def test_duplicate_entity_action_option_identity_is_rejected(public_path: str) -> None:
    options = {
        "store-duplicate": (
            ActionOption(
                entity_id="store-duplicate",
                action=NetworkAction.KEEP,
                expected_gross_margin=10.0,
                budget_cost=0.0,
                risk_score=0.0,
            ),
            ActionOption(
                entity_id="store-duplicate",
                action=NetworkAction.KEEP,
                expected_gross_margin=100.0,
                budget_cost=0.0,
                risk_score=0.0,
            ),
        )
    }
    constraints = NetPlanConstraints(max_budget=100.0)

    with pytest.raises(ValueError, match=r"duplicate \(entity_id, action\)"):
        if public_path == "solve":
            solve_network_plan(options_by_entity=options, constraints=constraints)
        else:
            compute_solver_problem_hash(options, constraints, 100_000.0)


def test_second_best_feasible_result_cannot_forge_solver_status_or_omit_alternatives() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    second_best = solve_result.alternatives[0]
    forged = replace(
        solve_result,
        solver_status=STATUS_FEASIBLE,
        objective_value=second_best.objective_value,
        selected_actions=second_best.actions,
        expected_gross_margin=second_best.expected_gross_margin,
        budget_usage=second_best.budget_usage,
        average_risk=second_best.average_risk,
        capacity_delta=second_best.capacity_delta,
        action_counts=second_best.action_counts,
        binding_constraints=second_best.binding_constraints,
        alternatives=(),
    )

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert {
        "solve_status_mismatch",
        "optimality_claim_mismatch",
        "alternative_count_mismatch",
    } <= set(receipt.baseline_constraint_violations)
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


@pytest.mark.parametrize("mutation", ["omission", "substitution"])
def test_alternative_set_mutations_fail_closed(mutation: str) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    assert len(solve_result.alternatives) >= 2

    if mutation == "omission":
        forged_alternatives = ()
        expected_violation = "alternative_count_mismatch"
    else:
        forged_alternatives = (
            solve_result.alternatives[1],
            solve_result.alternatives[0],
            *solve_result.alternatives[2:],
        )
        expected_violation = "alternative_content_mismatch"
    forged = replace(solve_result, alternatives=forged_alternatives)

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert expected_violation in receipt.baseline_constraint_violations
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


def test_authoritative_problem_hash_prevents_lowering_the_alternative_limit() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    forged = replace(solve_result, alternatives=())

    receipt = compare_solver_against_management_baseline(
        options_by_entity=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        alternative_limit=0,
        approval_verifier=_approval_verifier(authority_receipt),
    )

    assert "approval_solver_problem_hash_mismatch" in receipt.baseline_constraint_violations
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


def test_negative_alternative_limit_is_rejected() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints()

    with pytest.raises(ValueError, match="alternative_limit must be non-negative"):
        compute_solver_problem_hash(options, constraints, 100_000.0, -1)


@pytest.mark.parametrize("mutation", ["content", "multiplicity"])
def test_infeasibility_diagnosis_mutations_fail_closed(mutation: str) -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    constraints = _constraints(max_budget=100_000, min_expected_gross_margin=2_000_000)
    solve_result = solve_network_plan(options_by_entity=options, constraints=constraints)
    baseline = _management_baseline()
    authority_receipt = _approval_receipt(baseline, options, constraints)
    assert solve_result.infeasible is True
    assert solve_result.diagnostics

    if mutation == "content":
        forged_first = replace(
            solve_result.diagnostics[0],
            affected_stores=("forged-store",),
            required_relaxation="forged relaxation",
            business_impact="forged impact",
            suggested_action="forged action",
        )
        forged_diagnostics = (forged_first, *solve_result.diagnostics[1:])
    else:
        forged_diagnostics = (*solve_result.diagnostics, solve_result.diagnostics[-1])
    forged = replace(solve_result, diagnostics=forged_diagnostics)

    receipt = _compare(
        options=options,
        constraints=constraints,
        solve_result=forged,
        baseline=baseline,
        receipt=authority_receipt,
    )

    assert "infeasibility_diagnosis_mismatch" in receipt.baseline_constraint_violations
    assert receipt.business_uat_status == BUSINESS_UAT_UNVERIFIED
    assert receipt.governance_status == GOVERNED_DISABLED


def test_infeasible_max_average_risk_has_dedicated_diagnosis() -> None:
    options = build_scenario_options(existing_stores=_stores(), candidate_sites=_sites())
    result = solve_network_plan(
        options_by_entity=options,
        constraints=_constraints(max_average_risk=0.03),
    )

    assert result.solver_status == STATUS_INFEASIBLE
    assert result.infeasible is True
    viol_constraints = [d.violated_constraint for d in result.diagnostics]
    assert "max_average_risk" in viol_constraints


def test_infeasible_max_action_counts_has_dedicated_diagnosis() -> None:
    store_move_only = ExistingStoreInput(
        store_id="store-forced-move",
        baseline_gross_margin=500_000,
        move_gross_margin_uplift=120_000,
        move_cost=260_000,
        move_risk=0.34,
    )
    forced_options = {
        "store-forced-move": (
            build_scenario_options(existing_stores=(store_move_only,))["store-forced-move"][2],  # MOVE option
        )
    }
    result = solve_network_plan(
        options_by_entity=forced_options,
        constraints=_constraints(max_action_counts={NetworkAction.MOVE: 0}),
    )

    assert result.solver_status == STATUS_INFEASIBLE
    assert result.infeasible is True
    viol_constraints = [d.violated_constraint for d in result.diagnostics]
    assert "max_action_counts.MOVE" in viol_constraints
