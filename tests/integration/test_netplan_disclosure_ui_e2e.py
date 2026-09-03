from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.netplan import (
    ActionOption,
    ConstraintClass,
    FixedManagementApprovalReceiptVerifier,
    InMemoryNetPlanRepository,
    ManagementApprovalReceipt,
    ManagementBaselineInput,
    NetPlanConstraintDisclosureError,
    NetPlanProductionExecutor,
    NetPlanScenario,
    NetPlanService,
    NetworkAction,
    compute_solver_problem_hash,
)
from modules.opsboard.application.network_rebalance import (
    NetworkRebalancePolicyError,
    NetworkRebalanceService,
)
from shared.governance import (
    InMemoryDecisionPolicyRepository,
    default_netplan_disclosure_policy,
)
from solver.netplan import NetPlanConstraints

TENANT_ID = "tenant-e2e-disclosure"
MOMENT = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
AUTHORISED_ROLE = "network-planning-authority"
APPROVAL_PRINCIPAL = "principal://network-planning-authority"
APPROVAL_SOURCE = "test://management-approval"
RECEIPT_ID = "receipt-e2e-001"


class _FakeAvmCase:
    def __init__(self, store_id: str, case_id: str = "CASE-101") -> None:
        self.store_id = store_id
        self.case_id = case_id


class _FakeAvmRepo:
    def __init__(self, cases: list[_FakeAvmCase] | None = None) -> None:
        self._cases = cases or [_FakeAvmCase("STORE-101")]

    def list_cases(self) -> list[Any]:
        return self._cases


def _options() -> dict[str, tuple[ActionOption, ...]]:
    return {
        "STORE-101": (
            ActionOption(
                entity_id="STORE-101",
                action=NetworkAction.KEEP,
                expected_gross_margin=500_000.0,
                budget_cost=0.0,
                risk_score=0.10,
                capacity_delta=0,
                source_snapshot_ids=("network-store-101",),
                construction_days=0.0,
                equipment_units=0.0,
                labour_headcount=0.0,
                coverage_delta=0.0,
            ),
            ActionOption(
                entity_id="STORE-101",
                action=NetworkAction.IMPROVE,
                expected_gross_margin=590_000.0,
                budget_cost=140_000.0,
                risk_score=0.18,
                capacity_delta=0,
                source_snapshot_ids=("network-store-101",),
                construction_days=12.0,
                equipment_units=3.0,
                labour_headcount=2.0,
                coverage_delta=0.0,
            ),
        ),
        "CANDIDATE-A": (
            ActionOption(
                entity_id="CANDIDATE-A",
                action=NetworkAction.OPEN,
                expected_gross_margin=260_000.0,
                budget_cost=190_000.0,
                risk_score=0.22,
                capacity_delta=1,
                source_snapshot_ids=("sitescore-candidate-a",),
                construction_days=20.0,
                equipment_units=4.0,
                labour_headcount=6.0,
                coverage_delta=3.0,
                dilution_zone_id="zone-north",
            ),
            ActionOption(
                entity_id="CANDIDATE-A",
                action=NetworkAction.KEEP,
                expected_gross_margin=0.0,
                budget_cost=0.0,
                risk_score=0.0,
                capacity_delta=0,
                source_snapshot_ids=("sitescore-candidate-a",),
                notes=("defer_candidate_site",),
                construction_days=0.0,
                equipment_units=0.0,
                labour_headcount=0.0,
                coverage_delta=0.0,
            ),
        ),
    }


def _fully_modelled_constraints(**overrides: object) -> NetPlanConstraints:
    values: dict[str, object] = {
        "max_budget": 420_000,
        "max_construction_days": 60.0,
        "max_equipment_units": 12.0,
        "max_labour_headcount": 20.0,
        "min_coverage_delta": 1.0,
        "max_open_per_dilution_zone": 2,
    }
    values.update(overrides)
    return NetPlanConstraints(**values)  # type: ignore[arg-type]


def _build_scenario(
    *,
    scenario_id: str,
    constraints: NetPlanConstraints,
) -> NetPlanScenario:
    return NetPlanScenario.create(
        tenant_id=TENANT_ID,
        scenario_name=f"NetPlan E2E Scenario {scenario_id}",
        planning_horizon="2026Q3",
        options_by_entity=_options(),
        constraints=constraints,
        scenario_id=scenario_id,
        correlation_id="corr-disclosure-e2e",
        created_at=MOMENT,
    )


def _build_verifier(
    scenario: NetPlanScenario,
    solve: Any,
    *,
    receipt_id: str = RECEIPT_ID,
    principal_role: str = AUTHORISED_ROLE,
) -> FixedManagementApprovalReceiptVerifier:
    actions_by_entity = {
        action.entity_id: action.action for action in solve.result.selected_actions
    }
    source_snapshot_ids = tuple(
        sorted(
            {
                snapshot_id
                for action in solve.result.selected_actions
                for snapshot_id in action.source_snapshot_ids
            }
        )
    )
    baseline = ManagementBaselineInput(
        baseline_id=scenario.scenario_id,
        baseline_name=scenario.scenario_name,
        scenario_id=scenario.scenario_id,
        actions_by_entity=actions_by_entity,
        approval_receipt_id=receipt_id,
        source_snapshot_ids=source_snapshot_ids,
        scope=f"tenant:{TENANT_ID}",
        release_id="2026Q3",
    )
    receipt = ManagementApprovalReceipt(
        receipt_id=receipt_id,
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=principal_role,
        decision="APPROVED",
        approval_reference_id="APR-NETPLAN-DISCLOSURE-001",
        issued_at="2026-06-01T00:00:00Z",
        expires_at="2026-12-31T00:00:00Z",
        scenario_id=scenario.scenario_id,
        baseline_id=scenario.scenario_id,
        baseline_name=scenario.scenario_name,
        scope=baseline.scope,
        release_id=baseline.release_id,
        policy_version=scenario.constraints.policy_version,
        actions_by_entity=actions_by_entity,
        source_snapshot_ids=source_snapshot_ids,
        baseline_content_hash=baseline.compute_canonical_hash(constraints=scenario.constraints),
        solver_problem_hash=compute_solver_problem_hash(
            scenario.options_by_entity,
            scenario.constraints,
            100_000.0,
            solve.alternative_limit,
            scenario.model_version,
        ),
        receipt_hash="",
    )
    receipt = replace(receipt, receipt_hash=receipt.compute_receipt_hash())
    return FixedManagementApprovalReceiptVerifier(
        receipts={receipt.receipt_id: receipt},
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=principal_role,
        clock=lambda: MOMENT,
    )


def _attach_verifier(
    service: NetPlanService,
    scenario: NetPlanScenario,
    solve: Any,
    *,
    receipt_id: str = RECEIPT_ID,
    principal_role: str = AUTHORISED_ROLE,
) -> FixedManagementApprovalReceiptVerifier:
    """Install the management approval authority the way composition does.

    Returned as well as attached so a test can give the same authority to the
    Operator service: the Operator submit path does not mint its own signature,
    it asks `NetPlanService` for one, and both must be looking at the same
    receipt for that to mean anything.
    """
    verifier = _build_verifier(
        scenario, solve, receipt_id=receipt_id, principal_role=principal_role
    )
    service.approval_verifier = verifier
    return verifier


def test_e2e_production_solve_to_operator_projection_and_durable_approval_receipt() -> None:
    """E2E test verifying:
    1. Production-required CP-SAT solve with all 6 caps models 6 classes, leaving LEASE & SEQUENCING unmodelled.
    2. OpsBoard NetworkRebalanceService projects the exact 8-class partition.
    3. Operator UI API responds with full modelled/unmodelled disclosure contracts.
    4. Operator acknowledgement is recorded with cryptographic receipt hash.
    5. Final decision produces durable ApprovalRecord with disclosure bindings and integrity verification.
    """
    repo = InMemoryNetPlanRepository()
    policy_repo = InMemoryDecisionPolicyRepository(
        [default_netplan_disclosure_policy(tenant_id=TENANT_ID)]
    )

    scenario = _build_scenario(
        scenario_id="SCENARIO-E2E-PROD-001",
        constraints=_fully_modelled_constraints(),
    )
    repo.save_scenario(scenario)

    executor = NetPlanProductionExecutor()
    service = NetPlanService(
        repository=repo,
        policy_repository=policy_repo,
        production_executor=executor,
    )

    # Step 1: OpsBoard service projection performs canonical solve
    rebalance_service = NetworkRebalanceService(
        netplan_repository=repo,
        netplan_production_executor=executor,
        netplan_policy_repository=policy_repo,
        avm_repository=_FakeAvmRepo(),
        tenant_id=TENANT_ID,
        require_canonical=True,
    )

    # Initialize store
    rebalance_service._store("STORE-101")["status"] = "avmready"

    solve_res = rebalance_service.solve_netplan(
        store_id="STORE-101",
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-e2e-solve",
        correlation_id="corr-e2e-solve",
    )

    # Step 2: Verify solver result partition in repo
    solve = repo.get_solve(scenario.scenario_id)
    assert solve is not None
    assert solve.problem_hash is not None
    assert len(solve.problem_hash) == 64

    assert set(solve.result.modelled_constraint_classes) == {
        ConstraintClass.CAPITAL,
        ConstraintClass.CONSTRUCTION,
        ConstraintClass.EQUIPMENT,
        ConstraintClass.LABOUR,
        ConstraintClass.COVERAGE,
        ConstraintClass.DILUTION,
    }
    assert set(solve.result.unmodelled_constraint_classes) == {
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    }

    scenarios = solve_res["store"]["netPlanScenarios"]
    assert len(scenarios) >= 1
    primary_scenario = scenarios[0]

    # Verify partition contract in OpsBoard projection
    assert set(primary_scenario["modelledConstraintClasses"]) == {
        "CAPITAL",
        "CONSTRUCTION",
        "EQUIPMENT",
        "LABOUR",
        "COVERAGE",
        "DILUTION",
    }
    assert set(primary_scenario["unmodelledConstraintClasses"]) == {
        "LEASE",
        "SEQUENCING",
    }
    assert (
        primary_scenario["modelled_constraint_classes"]
        == primary_scenario["modelledConstraintClasses"]
    )
    assert (
        primary_scenario["unmodelled_constraint_classes"]
        == primary_scenario["unmodelledConstraintClasses"]
    )

    # Step 3: the projection also carries the submit gate's own classification,
    # resolved from the registered policy. The console renders this rather than
    # re-deriving the split, so it cannot offer a signature the server refuses.
    assert primary_scenario["blockedConstraintClasses"] == []
    assert set(primary_scenario["acknowledgeableConstraintClasses"]) == {
        "LEASE",
        "SEQUENCING",
    }
    assert primary_scenario["disclosurePolicyVersionId"] == (
        default_netplan_disclosure_policy(tenant_id=TENANT_ID).policy_version_id
    )

    # Step 4: Operator scenario selection
    select_res = rebalance_service.select_scenario(
        store_id="STORE-101",
        scenario_id=primary_scenario["id"],
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-e2e-select",
        correlation_id="corr-e2e-select",
    )
    assert select_res["store"]["selectedScenarioId"] == primary_scenario["id"]

    # Step 5: the same management approval authority is given to both services.
    # The Operator submit path signs through NetPlanService rather than minting
    # its own acknowledgement, so this is the single source of authority for the
    # whole flow.
    verifier = _attach_verifier(service, scenario, solve, receipt_id=RECEIPT_ID)
    rebalance_service.netplan_approval_verifier = verifier
    service.submit_for_approval(
        scenario.scenario_id,
        actor=APPROVAL_PRINCIPAL,
        reason="submitted for network planning approval",
        occurred_at=MOMENT,
    )

    # Step 6: Operator submit review. The acknowledgement is produced by this one
    # call -- a submission that reached Govern without a durable receipt behind
    # it is the gap this task closes.
    ack_reason = "租約條件已由商務處完成線下簽核；Q1-Q2 時序排程已與工程團隊確認。"
    submit_res = rebalance_service.submit_review(
        store_id="STORE-101",
        reason="Move scenario verified with CP-SAT and submitted for Govern review",
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-e2e-submit",
        correlation_id="corr-e2e-submit",
        acknowledged_classes=["LEASE", "SEQUENCING"],
        acknowledgement_reason=ack_reason,
        acknowledgement_actor_id=APPROVAL_PRINCIPAL,
        approval_receipt_id=RECEIPT_ID,
    )

    assert submit_res["store"]["status"] == "pendingapproval"
    approval = submit_res["governApproval"]
    assert approval["id"].startswith("APR-NET-")
    assert approval["modelledConstraintClasses"] == primary_scenario["modelledConstraintClasses"]
    assert (
        approval["unmodelledConstraintClasses"] == primary_scenario["unmodelledConstraintClasses"]
    )
    assert approval["blockedConstraintClasses"] == []
    assert set(approval["acknowledgedConstraintClasses"]) == {"LEASE", "SEQUENCING"}
    assert approval["acknowledgementReason"] == ack_reason
    assert approval["disclosurePolicyVersionId"] == (
        default_netplan_disclosure_policy(tenant_id=TENANT_ID).policy_version_id
    )
    # Authority on the approval is the role the verified receipt carried, not the
    # requester's own `actorRoleId`.
    assert approval["disclosureAcknowledgedByRole"] == AUTHORISED_ROLE
    assert approval["disclosureAcknowledgedBy"] == APPROVAL_PRINCIPAL
    assert approval["disclosureApprovalReceiptId"] == RECEIPT_ID
    assert approval["disclosureSolverProblemHash"] == solve.problem_hash

    # The Operator submission left a durable, sealed receipt in the NetPlan
    # repository -- not a field on an in-memory approval row.
    stored = repo.list_disclosure_acknowledgements(scenario.scenario_id)
    assert len(stored) == 1
    ack = stored[0]
    assert ack.acknowledgement_id == approval["disclosureAcknowledgementId"]
    assert ack.scenario_id == scenario.scenario_id
    assert ack.solver_problem_hash == solve.problem_hash
    assert ack.actor_role == AUTHORISED_ROLE
    assert ack.reason == ack_reason
    assert set(ack.acknowledged_classes) == {ConstraintClass.LEASE, ConstraintClass.SEQUENCING}

    # Verify cryptographic receipt hash
    assert ack.integrity_verified is True
    assert ack.receipt_hash == ack.compute_receipt_hash()

    # Step 7: Final decision produces durable ApprovalRecord with disclosure bindings
    decide_result = service.decide(
        scenario_id=scenario.scenario_id,
        decision="approved",
        reason="治理中心核准：所有 6 項硬限制經 CP-SAT 求解驗證，未建模之 LEASE 與 SEQUENCING 經授權主管具名簽核收據存查。",
        actor_id=APPROVAL_PRINCIPAL,
        approval_receipt_id="receipt-e2e-001",
        decided_at=MOMENT,
    )

    approval_record = decide_result
    assert approval_record is not None
    assert approval_record.decision == "approved"
    assert approval_record.disclosure_acknowledgement_id == ack.acknowledgement_id
    assert approval_record.disclosure_policy_version_id == ack.policy_version_id
    assert approval_record.acknowledged_constraint_classes == (
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    )
    assert approval_record.authentic_approval_verified is True


def test_e2e_blocked_unmodelled_classes_fail_closed_at_operator_submission_boundary() -> None:
    """Verify that a scenario with blocked unmodelled classes (e.g. CAPITAL only, missing CONSTRUCTION cap)
    fails closed:
    - NetworkRebalanceService.submit_review blocks submission.
    - NetPlanService.acknowledge_unmodelled_constraints refuses to sign blocked classes.
    """
    repo = InMemoryNetPlanRepository()
    policy_repo = InMemoryDecisionPolicyRepository(
        [default_netplan_disclosure_policy(tenant_id=TENANT_ID)]
    )

    # Only budget cap provided -> CONSTRUCTION, EQUIPMENT, etc. are unmodelled and BLOCKED
    capital_only_constraints = NetPlanConstraints(max_budget=420_000)
    scenario = _build_scenario(
        scenario_id="SCENARIO-E2E-BLOCKED-001",
        constraints=capital_only_constraints,
    )
    repo.save_scenario(scenario)

    executor = NetPlanProductionExecutor()
    service = NetPlanService(
        repository=repo,
        policy_repository=policy_repo,
        production_executor=executor,
    )

    rebalance_service = NetworkRebalanceService(
        netplan_repository=repo,
        netplan_production_executor=executor,
        netplan_policy_repository=policy_repo,
        avm_repository=_FakeAvmRepo(),
        tenant_id=TENANT_ID,
        require_canonical=True,
    )

    rebalance_service._store("STORE-101")["status"] = "avmready"

    solve_res = rebalance_service.solve_netplan(
        store_id="STORE-101",
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-blocked-solve",
        correlation_id="corr-blocked-solve",
    )

    solve = repo.get_solve(scenario.scenario_id)
    assert solve is not None
    assert solve.result.modelled_constraint_classes == (ConstraintClass.CAPITAL,)
    assert ConstraintClass.CONSTRUCTION in solve.result.unmodelled_constraint_classes

    # The console is told these classes block before anyone tries to submit, so
    # the acknowledgement form is never offered for them.
    projected = solve_res["store"]["netPlanScenarios"][0]
    assert set(projected["blockedConstraintClasses"]) == {
        "CONSTRUCTION",
        "EQUIPMENT",
        "LABOUR",
        "COVERAGE",
        "DILUTION",
    }
    assert set(projected["acknowledgeableConstraintClasses"]) == {"LEASE", "SEQUENCING"}

    selected_id = solve_res["store"]["netPlanScenarios"][0]["id"]
    rebalance_service.select_scenario(
        store_id="STORE-101",
        scenario_id=selected_id,
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-blocked-select",
        correlation_id="corr-blocked-select",
    )

    # 1. OpsBoard submit_review must raise NetworkRebalancePolicyError
    with pytest.raises(NetworkRebalancePolicyError) as exc_info:
        rebalance_service.submit_review(
            store_id="STORE-101",
            reason="Trying to submit blocked scenario",
            actor_role_id="expansionManager",
            actor_name="王若寧",
            idempotency_key="idem-blocked-submit",
            correlation_id="corr-blocked-submit",
        )
    message = str(exc_info.value)
    assert "CONSTRUCTION" in message
    assert "does not permit acknowledging" in message
    assert default_netplan_disclosure_policy(tenant_id=TENANT_ID).policy_version_id in message
    assert rebalance_service._store("STORE-101")["status"] == "netplanreview"
    assert repo.list_disclosure_acknowledgements(scenario.scenario_id) == []

    # Naming the blocked classes explicitly does not open a second door: the
    # refusal is on the disclosure, not on whether the submitter asked nicely.
    with pytest.raises(NetworkRebalancePolicyError):
        rebalance_service.submit_review(
            store_id="STORE-101",
            reason="Trying to submit blocked scenario with an acknowledgement",
            actor_role_id="expansionManager",
            actor_name="王若寧",
            idempotency_key="idem-blocked-submit-ack",
            correlation_id="corr-blocked-submit-ack",
            acknowledged_classes=[
                "CONSTRUCTION",
                "EQUIPMENT",
                "LABOUR",
                "COVERAGE",
                "DILUTION",
                "LEASE",
                "SEQUENCING",
            ],
            acknowledgement_reason="線下已確認，請放行",
            acknowledgement_actor_id=APPROVAL_PRINCIPAL,
            approval_receipt_id="receipt-e2e-002",
        )

    # 2. NetPlanService acknowledge_unmodelled_constraints must refuse acknowledging blocked classes
    _attach_verifier(service, scenario, solve, receipt_id="receipt-e2e-002")
    service.submit_for_approval(scenario.scenario_id, occurred_at=MOMENT)
    with pytest.raises(NetPlanConstraintDisclosureError) as ack_exc:
        service.acknowledge_unmodelled_constraints(
            scenario_id=scenario.scenario_id,
            actor_id=APPROVAL_PRINCIPAL,
            reason="Trying to waive construction cap",
            acknowledged_classes=["CONSTRUCTION"],
            approval_receipt_id="receipt-e2e-002",
            acknowledged_at=MOMENT,
        )
    assert "cannot acknowledge CONSTRUCTION under policy" in str(ack_exc.value)


def test_e2e_operator_submission_refuses_unsigned_or_unauthorised_acknowledgement() -> None:
    """The Operator boundary itself enforces the acknowledgement contract.

    Each refusal below was reachable while the submit path carried its own copy
    of the rule: it recorded `actorRoleId` without checking it, reused the
    submission reason as the acknowledgement reason, and filled in the class
    list on the caller's behalf. Any one of those produces a Govern approval
    that looks signed and is not.
    """
    repo = InMemoryNetPlanRepository()
    policy_repo = InMemoryDecisionPolicyRepository(
        [default_netplan_disclosure_policy(tenant_id=TENANT_ID)]
    )
    scenario = _build_scenario(
        scenario_id="SCENARIO-E2E-OPERATOR-ACK-001",
        constraints=_fully_modelled_constraints(),
    )
    repo.save_scenario(scenario)

    executor = NetPlanProductionExecutor()
    rebalance_service = NetworkRebalanceService(
        netplan_repository=repo,
        netplan_production_executor=executor,
        netplan_policy_repository=policy_repo,
        avm_repository=_FakeAvmRepo(),
        tenant_id=TENANT_ID,
        require_canonical=True,
    )
    rebalance_service._store("STORE-101")["status"] = "avmready"
    solve_res = rebalance_service.solve_netplan(
        store_id="STORE-101",
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-ack-solve",
        correlation_id="corr-ack-solve",
    )
    solve = repo.get_solve(scenario.scenario_id)
    assert solve is not None
    rebalance_service.select_scenario(
        store_id="STORE-101",
        scenario_id=solve_res["store"]["netPlanScenarios"][0]["id"],
        actor_role_id="expansionManager",
        actor_name="王若寧",
        idempotency_key="idem-ack-select",
        correlation_id="corr-ack-select",
    )
    rebalance_service.netplan_approval_verifier = _build_verifier(
        scenario, solve, receipt_id="receipt-e2e-operator"
    )

    def _submit(**overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "store_id": "STORE-101",
            "reason": "Submitted for Govern review",
            "actor_role_id": "expansionManager",
            "actor_name": "王若寧",
            "idempotency_key": None,
            "correlation_id": "corr-ack-submit",
            "acknowledged_classes": ["LEASE", "SEQUENCING"],
            "acknowledgement_reason": "租約與排程風險已於線下確認。",
            "acknowledgement_actor_id": APPROVAL_PRINCIPAL,
            "approval_receipt_id": "receipt-e2e-operator",
        }
        payload.update(overrides)
        return rebalance_service.submit_review(**payload)

    # 1. No named classes: an "acknowledge whatever is outstanding" submission.
    with pytest.raises(NetworkRebalancePolicyError) as exc_unnamed:
        _submit(acknowledged_classes=None)
    assert "naming each class" in str(exc_unnamed.value)

    # 2. Naming only part of the disclosure leaves the rest silently unsigned.
    with pytest.raises(NetworkRebalancePolicyError) as exc_partial:
        _submit(acknowledged_classes=["LEASE"])
    assert "SEQUENCING" in str(exc_partial.value)

    # 3. The acknowledgement reason does not fall back to the submission reason.
    with pytest.raises(NetworkRebalancePolicyError) as exc_reason:
        _submit(acknowledgement_reason="   ")
    assert "requires its own reason" in str(exc_reason.value)

    # 4. No receipt means no established authority.
    with pytest.raises(NetworkRebalancePolicyError) as exc_receipt:
        _submit(approval_receipt_id=None)
    assert "management approval receipt" in str(exc_receipt.value)

    # 5. A principal who is not the one the receipt names cannot sign for it,
    #    however authoritative the role they put on their own request.
    with pytest.raises(NetworkRebalancePolicyError) as exc_actor:
        _submit(acknowledgement_actor_id="principal://someone-else")
    assert "does not match the verified approval principal" in str(exc_actor.value)

    # 6. A receipt whose principal holds no acknowledging authority is refused
    #    even though every field of the submission is well formed.
    rebalance_service.netplan_approval_verifier = _build_verifier(
        scenario,
        solve,
        receipt_id="receipt-e2e-operator",
        principal_role="expansion-manager",
    )
    with pytest.raises(NetworkRebalancePolicyError) as exc_role:
        _submit()
    assert "not authorised to acknowledge" in str(exc_role.value)

    # 7. A solve with nothing outstanding cannot absorb an acknowledgement
    #    either: silently dropping the named classes would record a signature
    #    that no later reader could locate.
    projected_scenario = rebalance_service._store("STORE-101")["netPlanScenarios"][0]
    projected_scenario["unmodelledConstraintClasses"] = []
    projected_scenario["unmodelled_constraint_classes"] = []
    with pytest.raises(NetworkRebalancePolicyError) as exc_nothing:
        _submit(acknowledged_classes=["LEASE"])
    assert "no acknowledgeable constraint class" in str(exc_nothing.value)

    # None of the refusals advanced the store or left a partial receipt behind.
    assert rebalance_service._store("STORE-101")["status"] == "netplanreview"
    assert repo.list_disclosure_acknowledgements(scenario.scenario_id) == []


def test_e2e_acknowledgement_requires_authorized_role_and_non_empty_reason() -> None:
    """Verify acknowledgement safeguards:
    - Empty reason is rejected.
    - Unauthorized role is rejected.
    - Cannot sign for classes that were actually modelled.
    """
    repo = InMemoryNetPlanRepository()
    policy_repo = InMemoryDecisionPolicyRepository(
        [default_netplan_disclosure_policy(tenant_id=TENANT_ID)]
    )

    scenario = _build_scenario(
        scenario_id="SCENARIO-E2E-SAFEGUARD-001",
        constraints=_fully_modelled_constraints(),
    )
    repo.save_scenario(scenario)

    executor = NetPlanProductionExecutor()
    service = NetPlanService(
        repository=repo,
        policy_repository=policy_repo,
        production_executor=executor,
    )

    solve = service.solve(scenario.scenario_id, solved_at=MOMENT)
    _attach_verifier(service, scenario, solve, receipt_id="receipt-e2e-003")
    service.submit_for_approval(scenario.scenario_id, occurred_at=MOMENT)

    # 1. Empty / whitespace reason rejected
    with pytest.raises(NetPlanConstraintDisclosureError) as exc_reason:
        service.acknowledge_unmodelled_constraints(
            scenario_id=scenario.scenario_id,
            actor_id=APPROVAL_PRINCIPAL,
            reason="   ",
            acknowledged_classes=["LEASE", "SEQUENCING"],
            approval_receipt_id="receipt-e2e-003",
            acknowledged_at=MOMENT,
        )
    assert "requires a reason" in str(exc_reason.value)

    # 2. Signing for already modelled class (CAPITAL) rejected
    with pytest.raises(NetPlanConstraintDisclosureError) as exc_modelled:
        service.acknowledge_unmodelled_constraints(
            scenario_id=scenario.scenario_id,
            actor_id=APPROVAL_PRINCIPAL,
            reason="Valid reason",
            acknowledged_classes=["CAPITAL"],
            approval_receipt_id="receipt-e2e-003",
            acknowledged_at=MOMENT,
        )
    assert "cannot acknowledge CAPITAL under policy" in str(exc_modelled.value)
