"""Approval must answer for what the solve never checked (ODP-NETPLAN-DISCLOSURE-APPROVAL-001).

`NetPlanConstraints.unmodelled_classes()` has reported the gap since
ODP-FR-NET-002 was reopened, but `decide()` read it and approved anyway, so a
plan validated against capital alone was approvable on identical terms to one
validated against all eight classes. These tests are about the terms no longer
being identical.

Each test states the counterfactual it depends on. "Approval was refused" is
weak evidence on its own -- an approval path that refuses everything passes it
-- so every refusal here is paired with the single change that makes the same
call succeed. That pairing is what shows the disclosure gate is the thing
doing the refusing.

A note on why these scenarios are assembled from `ActionOption` directly rather
than through `build_scenario_options`: `ExistingStoreInput` and
`CandidateSiteInput` carry no `construction_days`, `equipment_units`,
`labour_headcount`, `coverage_delta` or `dilution_zone_id`, so a scenario built
through the public input transport can only ever model CAPITAL. That transport
gap is the sibling "transport/type" item in the remediation plan and is not
fixed here. Building the options directly is what lets these tests exercise the
fully-modelled case the policy is written against -- and the gap is worth
stating plainly, because until it closes, every scenario created through
`NetPlanService.create_scenario` is one this gate blocks.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from modules.netplan import (
    ActionOption,
    ConstraintClass,
    ConstraintDisclosureAcknowledgement,
    FixedManagementApprovalReceiptVerifier,
    ImmutableRecordError,
    InMemoryNetPlanRepository,
    ManagementApprovalReceipt,
    ManagementBaselineInput,
    NetPlanConstraintDisclosureError,
    NetPlanScenario,
    NetPlanScenarioStatus,
    NetPlanService,
    NetworkAction,
    compute_solver_problem_hash,
)
from shared.governance import (
    NETPLAN_DISCLOSURE_POLICY_LABEL,
    DecisionPolicy,
    InMemoryDecisionPolicyRepository,
    PolicyResolutionError,
    default_netplan_disclosure_policy,
)
from solver.netplan import NetPlanConstraints

# After the seeded policy's effective_from (2026-09-01); a decision taken
# before it resolves to no policy at all, which is its own refusal.
MOMENT = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
TENANT_ID = "11111111-1111-1111-1111-111111111111"
SCENARIO_ID = "netplan-disclosure-001"
RECEIPT_ID = "receipt-netplan-disclosure-001"

APPROVAL_SOURCE = "management-approval-system"
APPROVAL_PRINCIPAL = "principal://network-strategy-director"
# The seeded policy authorises this role and no other.
AUTHORISED_ROLE = "network-planning-authority"
UNAUTHORISED_ROLE = "network-strategy-analyst"


def _options() -> dict[str, tuple[ActionOption, ...]]:
    """One store and one candidate site, every resource cost declared.

    Every option states a number for each pool, including the zeroes. That is
    the distinction the solver's validation turns on: `None` means nobody
    measured this, `0.0` means it was measured and consumes nothing, and the
    two must not be written the same way.
    """
    return {
        "store-001": (
            ActionOption(
                entity_id="store-001",
                action=NetworkAction.KEEP,
                expected_gross_margin=500_000.0,
                budget_cost=0.0,
                risk_score=0.10,
                capacity_delta=0,
                source_snapshot_ids=("network-store-001",),
                construction_days=0.0,
                equipment_units=0.0,
                labour_headcount=0.0,
                coverage_delta=0.0,
            ),
            ActionOption(
                entity_id="store-001",
                action=NetworkAction.IMPROVE,
                expected_gross_margin=590_000.0,
                budget_cost=140_000.0,
                risk_score=0.18,
                capacity_delta=0,
                source_snapshot_ids=("network-store-001",),
                construction_days=12.0,
                equipment_units=3.0,
                labour_headcount=2.0,
                coverage_delta=0.0,
            ),
        ),
        "candidate-a": (
            ActionOption(
                entity_id="candidate-a",
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
                entity_id="candidate-a",
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
    """Caps for every class the formulation can bind.

    Only LEASE and SEQUENCING remain unmodelled here, which is the maximum the
    current solver can achieve and therefore the case the policy is designed
    around.
    """
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


def _capital_only_constraints() -> NetPlanConstraints:
    """The shape the solver shipped with: capital bound, seven classes silent."""
    return NetPlanConstraints(max_budget=420_000)


def _policy_repository(
    policy: DecisionPolicy | None = None,
) -> InMemoryDecisionPolicyRepository:
    return InMemoryDecisionPolicyRepository(
        [policy or default_netplan_disclosure_policy(TENANT_ID)]
    )


def _build(
    constraints: NetPlanConstraints,
    *,
    policy_repository: InMemoryDecisionPolicyRepository | None = None,
    principal_role: str = AUTHORISED_ROLE,
) -> tuple[NetPlanService, InMemoryNetPlanRepository]:
    """A scenario solved and submitted, one `decide()` away from approval."""
    options = _options()
    repository = InMemoryNetPlanRepository()
    service = NetPlanService(
        repository=repository,
        policy_repository=(
            _policy_repository() if policy_repository is None else policy_repository
        ),
    )
    scenario = repository.save_scenario(
        NetPlanScenario.create(
            tenant_id=TENANT_ID,
            scenario_name="disclosure approval",
            planning_horizon="2026Q3",
            options_by_entity=options,
            constraints=constraints,
            scenario_id=SCENARIO_ID,
            correlation_id="corr-disclosure",
            created_at=MOMENT,
        )
    )
    solve = service.solve(scenario.scenario_id, solved_at=MOMENT)
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
        baseline_id=SCENARIO_ID,
        baseline_name="disclosure approval",
        scenario_id=SCENARIO_ID,
        actions_by_entity=actions_by_entity,
        approval_receipt_id=RECEIPT_ID,
        source_snapshot_ids=source_snapshot_ids,
        scope=f"tenant:{TENANT_ID}",
        release_id="2026Q3",
    )
    receipt = ManagementApprovalReceipt(
        receipt_id=RECEIPT_ID,
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=principal_role,
        decision="APPROVED",
        approval_reference_id="APR-NETPLAN-DISCLOSURE-001",
        issued_at="2026-06-01T00:00:00Z",
        expires_at="2026-12-31T00:00:00Z",
        scenario_id=SCENARIO_ID,
        baseline_id=SCENARIO_ID,
        baseline_name="disclosure approval",
        scope=baseline.scope,
        release_id=baseline.release_id,
        policy_version=constraints.policy_version,
        actions_by_entity=actions_by_entity,
        source_snapshot_ids=source_snapshot_ids,
        baseline_content_hash=baseline.compute_canonical_hash(constraints=constraints),
        solver_problem_hash=compute_solver_problem_hash(
            options,
            constraints,
            100_000.0,
            solve.alternative_limit,
            scenario.model_version,
        ),
        receipt_hash="",
    )
    receipt = replace(receipt, receipt_hash=receipt.compute_receipt_hash())
    service.approval_verifier = FixedManagementApprovalReceiptVerifier(
        receipts={receipt.receipt_id: receipt},
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=principal_role,
        clock=lambda: MOMENT,
    )
    service.submit_for_approval(scenario.scenario_id, actor="system", occurred_at=MOMENT)
    return service, repository


def _reconstrain(
    repository: InMemoryNetPlanRepository, constraints: NetPlanConstraints
) -> None:
    """Change the scenario's constraints without going through `update_scenario`.

    `update_scenario` rebuilds `options_by_entity` from `ExistingStoreInput` /
    `CandidateSiteInput`, which would strip the resource declarations these
    tests depend on -- the same transport gap noted in the module docstring.
    Replacing the constraints in place moves the solver problem hash, which is
    the state under test, and leaves the options intact.
    """
    scenario = repository.get_scenario(SCENARIO_ID)
    assert scenario is not None
    repository.save_scenario(
        replace(
            scenario,
            constraints=constraints,
            status=NetPlanScenarioStatus.DRAFT,
        )
    )


def _acknowledge(
    service: NetPlanService,
    *,
    classes: tuple[ConstraintClass, ...] = (
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    ),
    reason: str = "lease pipeline confirmed offline; Q3 build order agreed with construction",
    actor_id: str = APPROVAL_PRINCIPAL,
    at: datetime = MOMENT,
) -> ConstraintDisclosureAcknowledgement:
    return service.acknowledge_unmodelled_constraints(
        SCENARIO_ID,
        actor_id=actor_id,
        reason=reason,
        acknowledged_classes=classes,
        approval_receipt_id=RECEIPT_ID,
        acknowledged_at=at,
    )


def _approve(service: NetPlanService, *, at: datetime = MOMENT):
    return service.decide(
        SCENARIO_ID,
        actor_id=APPROVAL_PRINCIPAL,
        reason="approved for 2026Q3 execution",
        decision="approved",
        approval_receipt_id=RECEIPT_ID,
        decided_at=at,
    )


# --------------------------------------------------------------------------
# Acceptance 1: an unmodelled required class fails closed
# --------------------------------------------------------------------------


def test_unmodelled_blocking_class_refuses_approval_and_names_the_classes() -> None:
    """A capital-only plan cannot be approved, and the refusal says what is missing.

    Naming the classes matters as much as refusing: "approval failed" sends an
    operator to the approval system, and the fix is in the scenario.
    """
    service, repository = _build(_capital_only_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service)

    message = str(excinfo.value)
    for blocked in ("CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"):
        assert blocked in message
    assert repository.list_approvals(SCENARIO_ID) == []
    scenario = repository.get_scenario(SCENARIO_ID)
    assert scenario is not None
    assert scenario.status is NetPlanScenarioStatus.PENDING_APPROVAL


def test_supplying_the_missing_caps_lets_the_same_plan_through() -> None:
    """The counterfactual for the test above.

    Same options, same actor, same receipt; the only change is that the caps
    the solver could have bound are now supplied. If this failed too, the test
    above would only be showing that approval is broken.
    """
    service, repository = _build(_fully_modelled_constraints())
    _acknowledge(service)

    approval = _approve(service)

    assert approval.is_approved
    assert approval.authentic_approval_verified
    assert set(approval.modelled_constraint_classes) == {
        ConstraintClass.CAPITAL,
        ConstraintClass.CONSTRUCTION,
        ConstraintClass.EQUIPMENT,
        ConstraintClass.LABOUR,
        ConstraintClass.COVERAGE,
        ConstraintClass.DILUTION,
    }
    assert set(approval.unmodelled_constraint_classes) == {
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    }


def test_acknowledgeable_classes_still_refuse_without_a_signature() -> None:
    """Fully modelled is not enough: LEASE and SEQUENCING still need signing for.

    This is the case the design decision in ODP_NETPLAN_CONSTRAINT_CLASSES
    creates -- two classes the model structurally cannot express. Leaving them
    to pass silently because nothing can be done about them is what would turn
    a documented product decision back into an invisible one.
    """
    service, repository = _build(_fully_modelled_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service)

    assert "LEASE" in str(excinfo.value)
    assert "SEQUENCING" in str(excinfo.value)
    assert repository.list_approvals(SCENARIO_ID) == []


def test_missing_policy_registry_refuses_rather_than_skipping_the_gate() -> None:
    """No policy configured must not mean no policy applied.

    An approval path that waves plans through when its policy source is absent
    is the shape this whole remediation batch is cataloguing: the gate reports
    installed, and passes everything.
    """
    service, repository = _build(_fully_modelled_constraints())
    service.policy_repository = None

    with pytest.raises(NetPlanConstraintDisclosureError):
        _approve(service)

    assert repository.list_approvals(SCENARIO_ID) == []


def test_policy_registry_without_a_covering_version_refuses() -> None:
    """A registry that resolves nothing is the same refusal, from `resolve_policy`.

    Distinct from the test above: there the repository is absent, here it is
    present and empty. Both must refuse, and neither may fall back to a
    built-in default.
    """
    service, repository = _build(
        _fully_modelled_constraints(),
        policy_repository=InMemoryDecisionPolicyRepository([]),
    )

    with pytest.raises(PolicyResolutionError):
        _approve(service)

    assert repository.list_approvals(SCENARIO_ID) == []


def test_a_solve_that_declares_nothing_cannot_be_approved() -> None:
    """Empty class sets mean "undisclosed", not "nothing unmodelled".

    The CP-SAT production path shipped exactly this state -- a plan returned
    with both sets empty (see the 2026-09-02 correction in the constraint-class
    design note). A gate that reads the empty unmodelled set as "no exposure"
    approves precisely the plans that made no claim at all.
    """
    service, repository = _build(_fully_modelled_constraints())
    _acknowledge(service)
    solve = repository.get_solve(SCENARIO_ID)
    assert solve is not None
    repository.save_solve(
        replace(
            solve,
            result=replace(
                solve.result,
                modelled_constraint_classes=(),
                unmodelled_constraint_classes=(),
            ),
        )
    )

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service)

    assert "undisclosed" in str(excinfo.value)


def test_a_solve_that_overstates_what_it_bound_cannot_be_approved() -> None:
    """A result claiming to have bound more than its constraints did is refused.

    Staleness has already established that the scenario has not moved since the
    solve, so the declaration and the constraints must agree. A result claiming
    LEASE was modelled is not a stricter plan; it is a false statement about
    which plan was checked.
    """
    service, repository = _build(_fully_modelled_constraints())
    _acknowledge(service)
    solve = repository.get_solve(SCENARIO_ID)
    assert solve is not None
    repository.save_solve(
        replace(
            solve,
            result=replace(
                solve.result,
                modelled_constraint_classes=tuple(ConstraintClass),
                unmodelled_constraint_classes=(),
            ),
        )
    )

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service)

    assert "does not match the scenario constraints" in str(excinfo.value)


# --------------------------------------------------------------------------
# Acceptance 2: only an authorised actor, with a reason, may acknowledge
# --------------------------------------------------------------------------


def test_unauthorised_role_cannot_acknowledge() -> None:
    """Authority comes from the verified receipt's role, not from the caller.

    The actor here is the same verified principal that would otherwise succeed;
    only the role attested by the approval authority differs.
    """
    service, _ = _build(_fully_modelled_constraints(), principal_role=UNAUTHORISED_ROLE)

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service)

    assert UNAUTHORISED_ROLE in str(excinfo.value)


def test_authorised_role_is_the_only_difference_that_makes_it_succeed() -> None:
    """Counterfactual for the refusal above."""
    service, _ = _build(_fully_modelled_constraints(), principal_role=AUTHORISED_ROLE)

    acknowledgement = _acknowledge(service)

    assert acknowledgement.actor_role == AUTHORISED_ROLE
    assert acknowledgement.actor_id == APPROVAL_PRINCIPAL


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_acknowledgement_requires_a_non_empty_reason(blank: str) -> None:
    """A signature with no stated reason records that someone clicked, nothing more."""
    service, _ = _build(_fully_modelled_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service, reason=blank)

    assert "reason" in str(excinfo.value)


def test_actor_must_match_the_verified_approval_principal() -> None:
    """An acknowledgement cannot be signed on someone else's behalf."""
    service, _ = _build(_fully_modelled_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service, actor_id="principal://someone-else")

    assert "does not match the verified approval principal" in str(excinfo.value)


def test_a_blocking_class_cannot_be_acknowledged_away() -> None:
    """The escape hatch does not open onto the classes the model could have bound.

    If CONSTRUCTION were signable, "we did not supply a construction cap" and
    "we accepted construction risk" would become the same record.
    """
    service, _ = _build(_capital_only_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service, classes=(ConstraintClass.CONSTRUCTION,))

    assert "CONSTRUCTION" in str(excinfo.value)


def test_a_modelled_class_cannot_be_acknowledged() -> None:
    """Signing for exposure this plan does not carry means the signer saw another plan."""
    service, _ = _build(_fully_modelled_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service, classes=(ConstraintClass.CAPITAL,))

    assert "CAPITAL" in str(excinfo.value)


def test_an_unknown_class_name_is_refused_rather_than_ignored() -> None:
    """A typo must not silently produce a receipt that covers nothing."""
    service, _ = _build(_fully_modelled_constraints())

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service, classes=("SEQUENCNG",))  # type: ignore[arg-type]

    assert "SEQUENCNG" in str(excinfo.value)


def test_partial_acknowledgement_does_not_cover_the_rest() -> None:
    """Signing for SEQUENCING is not signing for LEASE."""
    service, repository = _build(_fully_modelled_constraints())
    _acknowledge(service, classes=(ConstraintClass.SEQUENCING,))

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service)

    assert "LEASE" in str(excinfo.value)
    assert repository.list_approvals(SCENARIO_ID) == []


# --------------------------------------------------------------------------
# Acceptance 3: the receipt is immutable and bound to solve/classes/policy/actor
# --------------------------------------------------------------------------


def test_receipt_binds_classes_policy_actor_and_solve_hash() -> None:
    service, repository = _build(_fully_modelled_constraints())
    solve = repository.get_solve(SCENARIO_ID)
    assert solve is not None

    acknowledgement = _acknowledge(service)

    assert set(acknowledgement.acknowledged_classes) == {
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    }
    assert acknowledgement.policy_label == NETPLAN_DISCLOSURE_POLICY_LABEL
    assert acknowledgement.policy_version_id.endswith(f":{TENANT_ID}")
    assert acknowledgement.policy_version == "1.0.0"
    assert acknowledgement.actor_id == APPROVAL_PRINCIPAL
    assert acknowledgement.actor_role == AUTHORISED_ROLE
    assert acknowledgement.solver_problem_hash == solve.problem_hash
    assert acknowledgement.approval_receipt_id == RECEIPT_ID
    assert acknowledgement.reason
    assert acknowledgement.integrity_verified


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("acknowledged_classes", (ConstraintClass.LEASE,)),
        ("actor_id", "principal://someone-else"),
        ("actor_role", "chief-executive"),
        ("reason", "rewritten after the fact"),
        ("policy_version_id", "other-policy:tenant"),
        ("solver_problem_hash", "0" * 64),
        ("scenario_id", "some-other-scenario"),
    ],
)
def test_any_rewrite_of_the_receipt_breaks_its_own_hash(
    field_name: str, value: object
) -> None:
    """Immutability is detectable, not merely asserted.

    `frozen=True` stops an assignment; it does not stop `replace()`, a decoded
    payload, or a row edited in the database. The content hash is what makes
    those visible, and every field that carries meaning is inside it.
    """
    service, _ = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service)

    tampered = replace(acknowledgement, **{field_name: value})

    assert not tampered.integrity_verified
    assert tampered.receipt_hash != tampered.compute_receipt_hash()


def test_a_tampered_receipt_cannot_authorise_an_approval() -> None:
    """Detection is not the point on its own; refusal is.

    The tampered receipt is placed directly into the store, bypassing the
    service, because the threat being modelled is a record altered after it was
    written rather than one the service was talked into issuing.
    """
    service, repository = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service, classes=(ConstraintClass.SEQUENCING,))
    tampered = replace(
        acknowledgement,
        acknowledged_classes=(ConstraintClass.LEASE, ConstraintClass.SEQUENCING),
    )
    repository._disclosure_acknowledgements[tampered.acknowledgement_id] = tampered

    with pytest.raises(NetPlanConstraintDisclosureError):
        _approve(service)

    assert repository.list_approvals(SCENARIO_ID) == []


def test_the_store_refuses_to_overwrite_an_existing_receipt() -> None:
    service, repository = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service)

    with pytest.raises(ImmutableRecordError):
        repository.save_disclosure_acknowledgement(acknowledgement)


def test_the_store_refuses_a_receipt_that_never_verified() -> None:
    service, repository = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service)
    forged = replace(
        acknowledgement,
        acknowledgement_id="netplan-disclosure-ack-forged",
        reason="forged",
    )

    with pytest.raises(ImmutableRecordError):
        repository.save_disclosure_acknowledgement(forged)


def test_the_approval_record_carries_the_disclosure_it_was_granted_under() -> None:
    """The approval must stay answerable after the solve record moves on."""
    service, repository = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service)
    solve = repository.get_solve(SCENARIO_ID)
    assert solve is not None

    approval = _approve(service)

    assert approval.disclosure_acknowledgement_id == acknowledgement.acknowledgement_id
    assert approval.disclosure_policy_version_id == acknowledgement.policy_version_id
    assert approval.disclosure_policy_label == NETPLAN_DISCLOSURE_POLICY_LABEL
    assert approval.disclosure_policy_version == "1.0.0"
    assert set(approval.acknowledged_constraint_classes) == {
        ConstraintClass.LEASE,
        ConstraintClass.SEQUENCING,
    }
    assert approval.solver_problem_hash == solve.problem_hash
    payload = approval.to_dict()
    assert sorted(payload["unmodelled_constraint_classes"]) == ["LEASE", "SEQUENCING"]
    assert payload["disclosure_acknowledgement_id"] == acknowledgement.acknowledgement_id


# --------------------------------------------------------------------------
# Acceptance 4: a signature does not survive a re-solve or a policy change
# --------------------------------------------------------------------------


def test_acknowledgement_does_not_carry_to_a_re_solved_plan() -> None:
    """The signature was for a plan; change the plan and it answers for nothing.

    The budget rises, the scenario returns to draft and is re-solved, and the
    problem hash moves. Nothing deletes the acknowledgement -- it simply stops
    matching, which is why reuse cannot be arranged by keeping a copy of it.
    """
    service, repository = _build(_fully_modelled_constraints())
    acknowledgement = _acknowledge(service)
    original_hash = acknowledgement.solver_problem_hash

    _reconstrain(repository, _fully_modelled_constraints(max_budget=900_000))
    service.solve(SCENARIO_ID, solved_at=MOMENT + timedelta(hours=1))
    service.submit_for_approval(SCENARIO_ID, occurred_at=MOMENT + timedelta(hours=1))

    resolved = repository.get_solve(SCENARIO_ID)
    assert resolved is not None
    assert resolved.problem_hash != original_hash
    # The acknowledgement is still stored and still passes its own integrity
    # check. Nothing revoked it; it simply no longer answers for this plan.
    stored = repository.get_disclosure_acknowledgement(
        acknowledgement.acknowledgement_id
    )
    assert stored is not None
    assert stored.integrity_verified

    # The management receipt is bound to the solve too, so it has to be
    # re-issued for the new plan. Otherwise this test would pass on the receipt
    # mismatch and never reach the acknowledgement check it is about.
    _rebind_receipt(service, repository)

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service, at=MOMENT + timedelta(hours=2))

    assert "no valid acknowledgement" in str(excinfo.value)


def test_re_acknowledging_the_new_solve_restores_approval() -> None:
    """Counterfactual: the plan is approvable again once signed for as it now stands."""
    service, repository = _build(_fully_modelled_constraints())
    _acknowledge(service)
    _reconstrain(repository, _fully_modelled_constraints(max_budget=900_000))
    service.solve(SCENARIO_ID, solved_at=MOMENT + timedelta(hours=1))
    service.submit_for_approval(SCENARIO_ID, occurred_at=MOMENT + timedelta(hours=1))

    # The receipt is bound to the solve, so the management approval has to be
    # re-issued for the new plan before it can be signed for again.
    solve = repository.get_solve(SCENARIO_ID)
    assert solve is not None
    scenario = repository.get_scenario(SCENARIO_ID)
    assert scenario is not None
    _rebind_receipt(service, repository)

    _acknowledge(service, at=MOMENT + timedelta(hours=2))
    approval = _approve(service, at=MOMENT + timedelta(hours=3))

    assert approval.is_approved
    assert approval.solver_problem_hash == solve.problem_hash


def test_acknowledgement_does_not_survive_a_policy_version_change() -> None:
    """A signature taken under v1 does not carry into v2.

    v2 is stricter -- it stops treating LEASE as waivable -- so honouring the
    older signature would let the policy change be undone by the timing of when
    someone happened to sign.
    """
    policy_repository = _policy_repository()
    service, repository = _build(
        _fully_modelled_constraints(), policy_repository=policy_repository
    )
    acknowledgement = _acknowledge(service)
    assert acknowledgement.policy_version_id.startswith(NETPLAN_DISCLOSURE_POLICY_LABEL)

    v1 = policy_repository.versions[0]
    policy_repository.supersede(
        replace(
            v1,
            policy_version_id=f"netplan-constraint-disclosure-policy-v2:{TENANT_ID}",
            policy_label="netplan-constraint-disclosure-policy-v2",
            policy_version="2.0.0",
            effective_from=MOMENT + timedelta(hours=1),
            effective_to=None,
            parameters={
                **v1.parameters,
                "acknowledgeable_classes": ["SEQUENCING"],
            },
            rollback_policy_version=v1.policy_version_id,
        )
    )

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service, at=MOMENT + timedelta(hours=2))

    # LEASE is no longer waivable under v2, so it is a blocking class now.
    assert "LEASE" in str(excinfo.value)
    assert repository.list_approvals(SCENARIO_ID) == []


def test_a_signature_under_the_superseded_policy_is_not_reused_after_a_relabel() -> None:
    """Even a policy change that keeps the same permissions invalidates old signatures.

    v2 here waives exactly what v1 waived. The signature still does not carry,
    because what it recorded was acceptance under a named version -- and
    ODP-AC-BR-004 asks which version let a decision through, not whether some
    version would have.
    """
    policy_repository = _policy_repository()
    service, repository = _build(
        _fully_modelled_constraints(), policy_repository=policy_repository
    )
    _acknowledge(service)

    v1 = policy_repository.versions[0]
    policy_repository.supersede(
        replace(
            v1,
            policy_version_id=f"netplan-constraint-disclosure-policy-v2:{TENANT_ID}",
            policy_label="netplan-constraint-disclosure-policy-v2",
            policy_version="2.0.0",
            effective_from=MOMENT + timedelta(hours=1),
            effective_to=None,
            rollback_policy_version=v1.policy_version_id,
        )
    )

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _approve(service, at=MOMENT + timedelta(hours=2))

    assert "no valid acknowledgement" in str(excinfo.value)


def test_point_in_time_resolution_keeps_the_original_approval_reproducible() -> None:
    """Deciding at an instant under v1 still resolves v1 after v2 is in force."""
    policy_repository = _policy_repository()
    service, _ = _build(
        _fully_modelled_constraints(), policy_repository=policy_repository
    )
    _acknowledge(service)

    v1 = policy_repository.versions[0]
    policy_repository.supersede(
        replace(
            v1,
            policy_version_id=f"netplan-constraint-disclosure-policy-v2:{TENANT_ID}",
            policy_label="netplan-constraint-disclosure-policy-v2",
            policy_version="2.0.0",
            effective_from=MOMENT + timedelta(hours=1),
            effective_to=None,
            parameters={**v1.parameters, "acknowledgeable_classes": ["SEQUENCING"]},
            rollback_policy_version=v1.policy_version_id,
        )
    )

    approval = _approve(service, at=MOMENT + timedelta(minutes=30))

    assert approval.is_approved
    assert approval.disclosure_policy_version == "1.0.0"
    assert approval.disclosure_policy_label == NETPLAN_DISCLOSURE_POLICY_LABEL


def test_a_stale_solve_cannot_be_acknowledged_at_all() -> None:
    """Refusal happens at signing time too, not only at approval time.

    Otherwise the operator's first signal that the plan moved under them would
    be an approval failure attributed to a signature they just gave.
    """
    service, repository = _build(_fully_modelled_constraints())
    _reconstrain(repository, _fully_modelled_constraints(max_budget=900_000))

    with pytest.raises(NetPlanConstraintDisclosureError) as excinfo:
        _acknowledge(service)

    assert "stale solve" in str(excinfo.value)


def test_rejection_does_not_require_disclosure_resolution() -> None:
    """Rejecting an under-disclosed plan is always allowed.

    The gate exists to stop unexamined plans being approved. Making rejection
    depend on the policy registry would leave a plan that cannot be approved
    and cannot be turned down either.
    """
    service, repository = _build(_capital_only_constraints())
    service.policy_repository = None

    approval = service.decide(
        SCENARIO_ID,
        actor_id=APPROVAL_PRINCIPAL,
        reason="capital-only plan sent back for re-scoping",
        decision="rejected",
        decided_at=MOMENT,
    )

    assert not approval.is_approved
    scenario = repository.get_scenario(SCENARIO_ID)
    assert scenario is not None
    assert scenario.status is NetPlanScenarioStatus.REJECTED


def _rebind_receipt(
    service: NetPlanService, repository: InMemoryNetPlanRepository
) -> None:
    """Re-issue the management receipt for the scenario's current solve.

    The authority receipt is bound to the solver problem hash, so a re-solve
    invalidates it alongside the acknowledgement. Rebinding it isolates the
    acknowledgement behaviour under test from the receipt binding that already
    has its own coverage in `test_netplan_solver.py`.
    """
    scenario = repository.get_scenario(SCENARIO_ID)
    solve = repository.get_solve(SCENARIO_ID)
    assert scenario is not None and solve is not None
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
        baseline_id=SCENARIO_ID,
        baseline_name=scenario.scenario_name,
        scenario_id=SCENARIO_ID,
        actions_by_entity=actions_by_entity,
        approval_receipt_id=RECEIPT_ID,
        source_snapshot_ids=source_snapshot_ids,
        scope=f"tenant:{TENANT_ID}",
        release_id=scenario.planning_horizon,
    )
    receipt = ManagementApprovalReceipt(
        receipt_id=RECEIPT_ID,
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=AUTHORISED_ROLE,
        decision="APPROVED",
        approval_reference_id="APR-NETPLAN-DISCLOSURE-001",
        issued_at="2026-06-01T00:00:00Z",
        expires_at="2026-12-31T00:00:00Z",
        scenario_id=SCENARIO_ID,
        baseline_id=SCENARIO_ID,
        baseline_name=scenario.scenario_name,
        scope=baseline.scope,
        release_id=baseline.release_id,
        policy_version=scenario.constraints.policy_version,
        actions_by_entity=actions_by_entity,
        source_snapshot_ids=source_snapshot_ids,
        baseline_content_hash=baseline.compute_canonical_hash(
            constraints=scenario.constraints
        ),
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
    service.approval_verifier = FixedManagementApprovalReceiptVerifier(
        receipts={receipt.receipt_id: receipt},
        source_system=APPROVAL_SOURCE,
        principal_id=APPROVAL_PRINCIPAL,
        principal_role=AUTHORISED_ROLE,
        clock=lambda: MOMENT,
    )
