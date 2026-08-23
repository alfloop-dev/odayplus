"""Integration tests for ODP-NETPLAN-001.

Cover the two acceptance criteria of the NetPlan/OpsBoard EMGI seam:

1. NetPlan consumes a *versioned* market context plus the sitescore-v3,
   physical-feasibility and site-economics decision products, persists the
   evidence and policy versions behind every verdict, and fails closed when
   the provenance chain does not line up.
2. The final approval and its audit trail stay with a human operator in
   OpsBoard; the machine only ever marks a candidate admissible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from modules.netplan.domain.planning import build_scenario_options
from modules.netplan.integrations.emgi import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    INTEGRATION_POLICY_VERSION,
    MARKET_CONTEXT_CONTRACT_ID,
    CandidateAdmission,
    CandidateDecisionRequest,
    NetPlanEmgiContractError,
    NetPlanEmgiIntegrationService,
    NetPlanEmgiPolicy,
    netplan_emgi_document_digest,
    validate_netplan_emgi_document,
)
from modules.opsboard.integrations.emgi import (
    APPROVAL_POLICY_VERSION,
    ApprovalAction,
    ApprovalActor,
    ApprovalState,
    OpsBoardEmgiApprovalConflict,
    OpsBoardEmgiApprovalNotFound,
    OpsBoardEmgiApprovalPolicyError,
    OpsBoardEmgiApprovalService,
    admitted_candidates,
)
from modules.site_economics import SimulationOverrides, SiteEconomicsService
from modules.site_economics.domain.contracts import (
    CONTRACT_ID as ECONOMICS_CONTRACT_ID,
)
from modules.site_economics.domain.contracts import (
    CONTRACT_VERSION as ECONOMICS_CONTRACT_VERSION,
)
from modules.site_economics.domain.contracts import (
    ENGINE_VERSION as ECONOMICS_ENGINE_VERSION,
)
from modules.site_economics.domain.models import EconomicsDecision
from modules.site_feasibility import SiteFeasibilityService
from modules.site_feasibility.domain.contracts import (
    CONTRACT_ID as FEASIBILITY_CONTRACT_ID,
)
from modules.site_feasibility.domain.contracts import (
    CONTRACT_VERSION as FEASIBILITY_CONTRACT_VERSION,
)
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3 import SiteScoreV3Service
from modules.sitescore.v3.domain.contracts import (
    CONTRACT_ID as SITESCORE_CONTRACT_ID,
)
from modules.sitescore.v3.domain.contracts import (
    CONTRACT_VERSION as SITESCORE_CONTRACT_VERSION,
)
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreDecision,
)
from shared.auth import Principal, Role, Scope
from solver.netplan import NetworkAction

SITE_ID = "SITE-REAL-001"
MANIFEST_ID = "mf-real-001"
CONTEXT_ID = "ctx-real-001"
TENANT_ID = "tenant-real-001"


def _market_context(
    *,
    site_id: str = SITE_ID,
    manifest_id: str = MANIFEST_ID,
    context_id: str = CONTEXT_ID,
) -> dict[str, Any]:
    """A released-shape emgi.site-market-context.v1 payload for one site."""

    return {
        "context_id": context_id,
        "component_manifest_refs": [
            {
                "component_id": "component-real-001",
                "component_kind": "CUSTOM",
                "contract_id": MARKET_CONTEXT_CONTRACT_ID,
                "sha256": "a" * 64,
                "feature_manifest_id": manifest_id,
            }
        ],
        "identity": {"site_id": site_id, "metadata": {"zoning": "commercial"}},
        "listing": {
            "average_area_ping": 28.0,
            "median_asking_rent_per_ping": 2_400.0,
        },
        "demand": {"total_population": 22_000.0, "density_per_sq_km": 14_000.0},
        "competitor": {"active_competitors": 2, "competitor_density_per_sq_km": 1.5},
        "rent": {"median_rent_per_ping": 2_400.0},
        "catchment": {"catchment_id": "catchment-real-001"},
        "demand_score": 0.8,
        "format_score": 0.9,
        "ramp_score": 0.85,
        "cannibalization_score": 0.1,
        "economics_score": 0.95,
        "policy_score": 1.0,
    }


def _survey(site_id: str = SITE_ID) -> dict[str, Any]:
    return {
        "survey_id": "survey-real-001",
        "target_entity_id": site_id,
        "survey_type": "PHYSICAL_FEASIBILITY",
        "review_status": "APPROVED",
        "attributes": {
            "legal_use_restrictions": "NONE",
            "frontage_meters": 5.0,
            "utilities_power_capacity_amp": 100,
            "utilities_water_pressure_psi": 40,
            "flood_risk_level": "LOW",
            "loading_zone_available": True,
            "temporary_stop_allowed": True,
        },
    }


def _real_decision_stack(
    market_context: dict[str, Any] | None = None,
    *,
    site_id: str = SITE_ID,
    manifest_id: str = MANIFEST_ID,
) -> tuple[dict[str, Any], Any, Any, Any]:
    """Run the three real producers over one market context."""

    context = market_context if market_context is not None else _market_context()
    feasibility_doc = SiteFeasibilityService().evaluate_feasibility(
        site_id=site_id,
        market_context=context,
        surveys=[_survey(site_id)],
    )
    economics_doc = SiteEconomicsService().evaluate_site_market_context(
        market_context=context,
        tenant_id=TENANT_ID,
        overrides=SimulationOverrides(demand_multiplier=1.8, monthly_rent=40_000.0),
    )
    sitescore_doc = SiteScoreV3Service().evaluate(
        site_id=site_id,
        manifest_id=manifest_id,
        market_context=context,
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )
    return context, sitescore_doc, feasibility_doc, economics_doc


def _feasibility_wire(
    *,
    recommendation: str = FeasibilityDecision.FEASIBLE.value,
    context_id: str | None = CONTEXT_ID,
    contract_version: str = FEASIBILITY_CONTRACT_VERSION,
    policy_version: str | None = "physical-feasibility-gate-v1",
    site_id: str = SITE_ID,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if context_id is not None:
        metadata["source_market_context_id"] = context_id
    if policy_version is not None:
        metadata["policy_version"] = policy_version
    return {
        "contract_id": FEASIBILITY_CONTRACT_ID,
        "contract_version": contract_version,
        "document_id": "feas-doc-001",
        "site_id": site_id,
        "evaluated_at": "2026-08-23T00:00:00+00:00",
        "decision": {"recommendation": recommendation, "reasons": []},
        "metadata": metadata,
    }


def _economics_wire(
    *,
    recommendation: str = EconomicsDecision.GO.value,
    context_id: str | None = CONTEXT_ID,
    contract_version: str = ECONOMICS_CONTRACT_VERSION,
    confidence_score: float | None = 0.8,
    average_monthly_ebitda: float | None = 90_000.0,
    total_initial_cash_outlay: float | None = 3_600_000.0,
    risk_flags: list[str] | None = None,
    site_id: str = SITE_ID,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if average_monthly_ebitda is not None:
        metrics["average_monthly_ebitda"] = average_monthly_ebitda
    if total_initial_cash_outlay is not None:
        metrics["total_initial_cash_outlay"] = total_initial_cash_outlay
    decision: dict[str, Any] = {
        "recommendation": recommendation,
        "reasons": [],
        "risk_flags": list(risk_flags or []),
    }
    if confidence_score is not None:
        decision["confidence_score"] = confidence_score
    return {
        "contract_id": ECONOMICS_CONTRACT_ID,
        "contract_version": contract_version,
        "document_id": "econ-doc-001",
        "site_id": site_id,
        "tenant_id": TENANT_ID,
        "engine_version": ECONOMICS_ENGINE_VERSION,
        "source_market_context_id": context_id,
        "metrics": metrics,
        "decision": decision,
    }


def _sitescore_wire(
    *,
    site_id: str = SITE_ID,
    manifest_id: str | None = MANIFEST_ID,
    readiness: str = DecisionReadiness.READY.value,
    availability: str = ScoreAvailability.AVAILABLE.value,
    decision: str = SiteScoreDecision.GO.value,
    contract_version: str = SITESCORE_CONTRACT_VERSION,
) -> dict[str, Any]:
    return {
        "contract_id": SITESCORE_CONTRACT_ID,
        "contract_version": contract_version,
        "document_id": "sitescore-doc-001",
        "site_id": site_id,
        "manifest_id": manifest_id,
        "evaluated_at": "2026-08-23T00:00:00+00:00",
        "assessment": {
            "availability": availability,
            "readiness": readiness,
            "decision": decision,
            "components": None,
            "reasons": [],
        },
    }


def _build(
    *,
    market_context: Any = None,
    sitescore_doc: Any = None,
    feasibility_doc: Any = None,
    economics_doc: Any = None,
    manifest_id: str = MANIFEST_ID,
    site_id: str = SITE_ID,
    service: NetPlanEmgiIntegrationService | None = None,
):
    return (service or NetPlanEmgiIntegrationService()).build_candidate(
        site_id=site_id,
        manifest_id=manifest_id,
        market_context=market_context if market_context is not None else _market_context(),
        sitescore_doc=sitescore_doc if sitescore_doc is not None else _sitescore_wire(),
        feasibility_doc=feasibility_doc if feasibility_doc is not None else _feasibility_wire(),
        economics_doc=economics_doc if economics_doc is not None else _economics_wire(),
    )


def _human(subject_id: str = "user-ops-001", *roles: Role) -> Principal:
    return Principal(
        subject_id=subject_id,
        roles=frozenset(roles or (Role.OPERATIONS_MANAGER,)),
        scope=Scope(tenant_id=TENANT_ID),
        attributes={"token_type": "oidc"},
        authenticated=True,
    )


def _service_principal(service_id: str = "netplan-worker") -> Principal:
    return Principal(
        subject_id=f"service:{service_id}",
        roles=frozenset({Role.OPERATIONS_MANAGER}),
        scope=Scope(tenant_id=TENANT_ID),
        attributes={"token_type": "service", "service_id": service_id},
        authenticated=True,
    )


def _plan_document(
    *,
    service: NetPlanEmgiIntegrationService | None = None,
    requests=None,
    market_context: Any = None,
):
    integration = service or NetPlanEmgiIntegrationService()
    return integration.build_plan_document(
        tenant_id=TENANT_ID,
        scenario_key="fy27-h1-expansion",
        manifest_id=MANIFEST_ID,
        market_context=market_context if market_context is not None else _market_context(),
        requests=requests
        if requests is not None
        else [
            CandidateDecisionRequest(
                site_id=SITE_ID,
                sitescore_doc=_sitescore_wire(),
                feasibility_doc=_feasibility_wire(),
                economics_doc=_economics_wire(),
            )
        ],
    )


# --------------------------------------------------------------------------
# 1. Versioned market context and decision inputs, with persisted evidence
# --------------------------------------------------------------------------


def test_real_producers_admit_a_candidate_and_persist_evidence_and_policy_versions():
    context, sitescore_doc, feasibility_doc, economics_doc = _real_decision_stack()
    assert feasibility_doc.decision.recommendation == FeasibilityDecision.FEASIBLE
    assert economics_doc.decision.recommendation == EconomicsDecision.GO
    assert sitescore_doc.assessment.decision == SiteScoreDecision.GO

    candidate = _build(
        market_context=context,
        sitescore_doc=sitescore_doc,
        feasibility_doc=feasibility_doc,
        economics_doc=economics_doc,
    )

    assert candidate.admission == CandidateAdmission.ADMITTED
    assert candidate.reasons == ()
    assert candidate.candidate_input is not None
    # Admission is a machine gate; the binding decision still belongs to a human.
    assert candidate.requires_human_approval is True

    evidence = candidate.evidence
    assert evidence.manifest_id == MANIFEST_ID
    assert evidence.market_context_id == CONTEXT_ID
    assert evidence.market_context_sha256
    assert evidence.policy_version("netplan_emgi_integration") == INTEGRATION_POLICY_VERSION
    assert evidence.policy_version("feasibility_gate") == "physical-feasibility-gate-v1"
    assert evidence.policy_version("economics_engine") == ECONOMICS_ENGINE_VERSION
    assert evidence.policy_version("sitescore_contract") == SITESCORE_CONTRACT_VERSION

    by_label = {ref.label: ref for ref in evidence.refs}
    assert set(by_label) == {"market_context", "sitescore_v3", "feasibility", "economics"}
    assert by_label["market_context"].contract_id == MARKET_CONTEXT_CONTRACT_ID
    assert by_label["sitescore_v3"].contract_id == SITESCORE_CONTRACT_ID
    assert by_label["sitescore_v3"].document_id == sitescore_doc.document_id
    assert by_label["feasibility"].sha256 == feasibility_doc.digest
    assert by_label["economics"].sha256 == economics_doc.digest


def test_admitted_candidate_maps_economics_onto_versioned_solver_inputs():
    policy = NetPlanEmgiPolicy(gross_margin_horizon_months=12, risk_per_flag=0.05)
    candidate = _build(
        service=NetPlanEmgiIntegrationService(policy),
        economics_doc=_economics_wire(
            confidence_score=0.8,
            average_monthly_ebitda=90_000.0,
            total_initial_cash_outlay=3_600_000.0,
            risk_flags=["HIGH_RENT_SENSITIVITY", "THIN_DSCR"],
        ),
    )

    solver_input = candidate.candidate_input
    assert solver_input is not None
    assert solver_input.candidate_site_id == SITE_ID
    assert solver_input.expected_gross_margin == pytest.approx(90_000.0 * 12)
    assert solver_input.open_cost == pytest.approx(3_600_000.0)
    # 1 - confidence, plus one increment per economics risk flag.
    assert solver_input.risk_score == pytest.approx(0.2 + 2 * 0.05)
    assert solver_input.capacity_delta == 1
    assert solver_input.source_snapshot_ids == candidate.evidence.snapshot_ids
    assert any(
        snapshot.startswith(f"{MARKET_CONTEXT_CONTRACT_ID}:")
        for snapshot in solver_input.source_snapshot_ids
    )


def test_admitted_candidate_feeds_netplan_scenario_options_with_its_evidence():
    document = _plan_document()
    options = build_scenario_options(candidate_sites=document.candidate_site_inputs())

    assert set(options) == {SITE_ID}
    open_option = next(option for option in options[SITE_ID] if option.action == NetworkAction.OPEN)
    assert open_option.expected_gross_margin > 0
    assert open_option.source_snapshot_ids == document.candidates[0].evidence.snapshot_ids


def test_market_context_manifest_mismatch_withholds_the_candidate():
    candidate = _build(market_context=_market_context(manifest_id="mf-stale-000"))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert candidate.candidate_input is None
    assert "Market context manifest does not match the evaluation manifest." in candidate.reasons


def test_market_context_for_another_site_withholds_the_candidate():
    candidate = _build(market_context=_market_context(site_id="SITE-OTHER-999"))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert candidate.candidate_input is None
    assert "Market context does not cover this candidate site." in candidate.reasons


def test_missing_evaluation_manifest_withholds_the_candidate():
    candidate = _build(manifest_id="   ")

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "Evaluation manifest is missing." in candidate.reasons


def test_decision_document_from_another_market_context_is_withheld():
    candidate = _build(economics_doc=_economics_wire(context_id="ctx-other-002"))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "Economics source market context mismatch." in candidate.reasons


def test_decision_document_contract_version_drift_is_withheld():
    candidate = _build(feasibility_doc=_feasibility_wire(contract_version="2.0.0"))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert any("declares version 2.0.0" in reason for reason in candidate.reasons)


def test_missing_feasibility_policy_version_is_withheld():
    candidate = _build(feasibility_doc=_feasibility_wire(policy_version=None))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "Feasibility policy version is missing from the evidence chain." in candidate.reasons


def test_a_sibling_context_manifest_does_not_vouch_for_this_site():
    """Only this site's context (or the document root) may pin the manifest."""

    candidate = _build(
        market_context={
            "contexts": [
                _market_context(site_id="SITE-OTHER-999", manifest_id=MANIFEST_ID),
                _market_context(manifest_id="mf-stale-000"),
            ],
            "document_id": "smc-doc-001",
            "contract_id": MARKET_CONTEXT_CONTRACT_ID,
        }
    )

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "Market context manifest does not match the evaluation manifest." in candidate.reasons


def test_document_root_manifest_still_vouches_for_a_context_document():
    context = _market_context()
    context.pop("component_manifest_refs")
    candidate = _build(
        market_context={
            "contexts": [context],
            "document_id": "smc-doc-001",
            "contract_id": MARKET_CONTEXT_CONTRACT_ID,
            "component_manifest_refs": [
                {
                    "component_id": "component-real-001",
                    "component_kind": "CUSTOM",
                    "contract_id": MARKET_CONTEXT_CONTRACT_ID,
                    "feature_manifest_id": MANIFEST_ID,
                }
            ],
        }
    )

    assert candidate.admission == CandidateAdmission.ADMITTED


def test_sitescore_document_for_another_site_is_withheld():
    candidate = _build(sitescore_doc=_sitescore_wire(site_id="SITE-OTHER-999"))

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "SiteScore v3 document belongs to another site." in candidate.reasons


@pytest.mark.parametrize(
    ("recommendation", "expected"),
    [
        (FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY.value, CandidateAdmission.WITHHELD_INCOMPLETE),
        (FeasibilityDecision.CONDITIONAL.value, CandidateAdmission.WITHHELD_INCOMPLETE),
        (FeasibilityDecision.INFEASIBLE.value, CandidateAdmission.REJECTED),
        # A decision this policy version has never been reviewed against must
        # never fall through to ADMITTED.
        ("PROBABLY_FINE", CandidateAdmission.WITHHELD_INCOMPLETE),
    ],
)
def test_feasibility_decisions_gate_admission(recommendation: str, expected: CandidateAdmission):
    candidate = _build(feasibility_doc=_feasibility_wire(recommendation=recommendation))

    assert candidate.admission == expected
    assert candidate.candidate_input is None


@pytest.mark.parametrize(
    ("recommendation", "expected"),
    [
        (EconomicsDecision.CONDITIONAL_GO.value, CandidateAdmission.WITHHELD_INCOMPLETE),
        (EconomicsDecision.INVESTIGATE.value, CandidateAdmission.WITHHELD_INCOMPLETE),
        (EconomicsDecision.REJECT.value, CandidateAdmission.REJECTED),
        ("LOOKS_OK", CandidateAdmission.WITHHELD_INCOMPLETE),
    ],
)
def test_economics_decisions_gate_admission(recommendation: str, expected: CandidateAdmission):
    candidate = _build(economics_doc=_economics_wire(recommendation=recommendation))

    assert candidate.admission == expected
    assert candidate.candidate_input is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"readiness": DecisionReadiness.INCOMPLETE_FEASIBILITY.value},
            CandidateAdmission.WITHHELD_INCOMPLETE,
        ),
        (
            {"availability": ScoreAvailability.UNAVAILABLE_MISSING_INPUT.value},
            CandidateAdmission.WITHHELD_INCOMPLETE,
        ),
        ({"decision": SiteScoreDecision.INCOMPLETE.value}, CandidateAdmission.WITHHELD_INCOMPLETE),
        ({"decision": SiteScoreDecision.NO_GO.value}, CandidateAdmission.REJECTED),
    ],
)
def test_sitescore_assessment_gates_admission(overrides: dict, expected: CandidateAdmission):
    candidate = _build(sitescore_doc=_sitescore_wire(**overrides))

    assert candidate.admission == expected
    assert candidate.candidate_input is None


def test_broken_provenance_outranks_a_business_rejection():
    """A document we cannot trust must not be reported as a merits-based no."""

    candidate = _build(
        economics_doc=_economics_wire(
            recommendation=EconomicsDecision.REJECT.value, context_id="ctx-other-002"
        )
    )

    assert candidate.admission == CandidateAdmission.WITHHELD_PROVENANCE
    assert "Economics source market context mismatch." in candidate.reasons
    assert "Economics rejected the site." in candidate.reasons


@pytest.mark.parametrize(
    "overrides",
    [
        {"average_monthly_ebitda": None},
        {"total_initial_cash_outlay": None},
        {"confidence_score": None},
        {"confidence_score": 1.4},
        {"confidence_score": -0.2},
        {"average_monthly_ebitda": float("inf")},
    ],
)
def test_unusable_economics_figures_withhold_instead_of_defaulting(overrides: dict):
    candidate = _build(economics_doc=_economics_wire(**overrides))

    assert candidate.admission == CandidateAdmission.WITHHELD_INCOMPLETE
    assert candidate.candidate_input is None


def test_plan_document_is_contract_valid_and_carries_every_verdict():
    document = _plan_document(
        requests=[
            CandidateDecisionRequest(
                site_id=SITE_ID,
                sitescore_doc=_sitescore_wire(),
                feasibility_doc=_feasibility_wire(),
                economics_doc=_economics_wire(),
            ),
            CandidateDecisionRequest(
                site_id="SITE-REAL-002",
                sitescore_doc=_sitescore_wire(site_id="SITE-REAL-002"),
                feasibility_doc=_feasibility_wire(
                    recommendation=FeasibilityDecision.INFEASIBLE.value,
                    site_id="SITE-REAL-002",
                ),
                economics_doc=_economics_wire(site_id="SITE-REAL-002"),
            ),
        ],
        market_context={
            "contexts": [_market_context(), _market_context(site_id="SITE-REAL-002")],
            "document_id": "smc-doc-001",
            "contract_id": MARKET_CONTEXT_CONTRACT_ID,
        },
    )

    validate_netplan_emgi_document(document)
    assert document.contract_id == CONTRACT_ID
    assert document.contract_version == CONTRACT_VERSION
    assert document.requires_human_approval is True
    assert document.policy.policy_version == INTEGRATION_POLICY_VERSION
    assert [candidate.admission for candidate in document.candidates] == [
        CandidateAdmission.ADMITTED,
        CandidateAdmission.REJECTED,
    ]
    # Only the admitted site reaches the solver; the rejected one is still
    # published so the audit record explains why it was left out.
    assert len(document.candidate_site_inputs()) == 1
    assert document.digest == document.digest


def test_validate_rejects_a_withheld_candidate_that_smuggles_solver_inputs():
    document = _plan_document()
    payload = document.to_dict()
    payload["candidates"][0]["admission"] = CandidateAdmission.WITHHELD_INCOMPLETE.value

    with pytest.raises(NetPlanEmgiContractError, match="must not carry solver inputs"):
        validate_netplan_emgi_document(payload)


def test_validate_rejects_a_document_that_waives_human_approval():
    payload = _plan_document().to_dict()
    payload["requires_human_approval"] = False

    with pytest.raises(NetPlanEmgiContractError, match="requires_human_approval"):
        validate_netplan_emgi_document(payload)


def test_validate_rejects_evidence_without_policy_versions():
    payload = _plan_document().to_dict()
    payload["candidates"][0]["evidence"]["policy_versions"] = {}

    with pytest.raises(NetPlanEmgiContractError, match="policy versions"):
        validate_netplan_emgi_document(payload)


# --------------------------------------------------------------------------
# 2. Final human approval and audit stay in OpsBoard
# --------------------------------------------------------------------------


def test_opsboard_review_packet_carries_the_evidence_and_policy_versions():
    document = _plan_document()
    service = OpsBoardEmgiApprovalService()

    packet = service.open_review(document, requested_by=_service_principal())

    assert packet.state == ApprovalState.PENDING_HUMAN_APPROVAL
    assert packet.decision is None
    assert packet.document_id == document.document_id
    assert packet.document_digest == document.digest
    assert packet.manifest_id == MANIFEST_ID
    assert packet.admitted_candidate_site_ids == (SITE_ID,)
    assert packet.policy_versions["opsboard_approval"] == APPROVAL_POLICY_VERSION
    assert (
        packet.policy_versions["netplan_emgi_document"]["policy_version"]
        == INTEGRATION_POLICY_VERSION
    )
    assert (
        packet.policy_versions["candidate_evidence"][SITE_ID]["economics_engine"]
        == ECONOMICS_ENGINE_VERSION
    )
    assert [event.event_type for event in packet.audit_trail] == ["netplan_emgi.review_opened"]


def test_opsboard_refuses_to_open_a_review_on_an_invalid_document():
    payload = _plan_document().to_dict()
    payload["contract_id"] = "odayplus.netplan-emgi.v2"

    with pytest.raises(NetPlanEmgiContractError):
        OpsBoardEmgiApprovalService().open_review(payload, requested_by=_human())


def test_service_identity_cannot_take_the_final_decision():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_service_principal())

    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="service identity"):
        service.record_decision(
            packet.packet_id, actor=_service_principal(), action=ApprovalAction.APPROVE
        )

    assert service.get(packet.packet_id).state == ApprovalState.PENDING_HUMAN_APPROVAL


def test_human_without_an_approver_role_cannot_approve():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_human())

    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="approver role"):
        service.record_decision(
            packet.packet_id,
            actor=_human("user-franchisee-001", Role.FRANCHISEE),
            action=ApprovalAction.APPROVE,
        )


def test_human_approval_releases_the_admitted_sites_and_writes_the_audit_trail():
    document = _plan_document()
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(document, requested_by=_service_principal())

    approver = _human("user-ops-001", Role.OPERATIONS_MANAGER)
    decided = service.record_decision(
        packet.packet_id,
        actor=approver,
        action=ApprovalAction.APPROVE,
        reason="Reviewed the FY27 H1 expansion pack.",
    )

    assert decided.state == ApprovalState.APPROVED
    assert decided.decision is not None
    assert decided.decision.actor.subject_id == "user-ops-001"
    assert decided.decision.actor.is_human is True
    assert decided.decision.approved_candidate_site_ids == (SITE_ID,)
    assert service.approved_candidate_site_ids(decided.packet_id) == (SITE_ID,)
    assert [event.event_type for event in decided.audit_trail] == [
        "netplan_emgi.review_opened",
        "netplan_emgi.approved",
    ]

    bundle = service.evidence_bundle(decided.packet_id)
    assert bundle["document_contract_id"] == CONTRACT_ID
    assert bundle["document_digest"] == document.digest
    assert bundle["document_digest"] == netplan_emgi_document_digest(document.to_dict())
    assert bundle["state"] == ApprovalState.APPROVED.value
    assert bundle["decision"]["actor"]["subject_id"] == "user-ops-001"
    assert bundle["candidate_evidence"][SITE_ID]["manifest_id"] == MANIFEST_ID
    assert (
        bundle["candidate_evidence"][SITE_ID]["policy_versions"]["feasibility_gate"]
        == "physical-feasibility-gate-v1"
    )
    assert len(bundle["audit_trail"]) == 2


def test_an_unauthenticated_principal_cannot_take_the_final_decision():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_human())
    anonymous = Principal(
        subject_id="anonymous",
        roles=frozenset({Role.OPERATIONS_MANAGER}),
        scope=Scope(tenant_id=TENANT_ID),
        attributes={"token_type": "oidc"},
        authenticated=False,
    )

    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="not authenticated"):
        service.record_decision(packet.packet_id, actor=anonymous, action=ApprovalAction.APPROVE)


def test_rejecting_or_returning_a_plan_requires_a_reason():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_human())

    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="reason is required"):
        service.record_decision(packet.packet_id, actor=_human(), action=ApprovalAction.REJECT)

    returned = service.record_decision(
        packet.packet_id,
        actor=_human(),
        action=ApprovalAction.RETURN,
        reason="Refresh the market context before committing capital.",
    )
    assert returned.state == ApprovalState.RETURNED
    assert returned.decision is not None
    assert returned.decision.approved_candidate_site_ids == ()


def test_a_plan_with_no_admitted_candidate_cannot_be_approved():
    document = _plan_document(
        requests=[
            CandidateDecisionRequest(
                site_id=SITE_ID,
                sitescore_doc=_sitescore_wire(),
                feasibility_doc=_feasibility_wire(
                    recommendation=FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY.value
                ),
                economics_doc=_economics_wire(),
            )
        ]
    )
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(document, requested_by=_human())

    assert packet.admitted_candidate_site_ids == ()
    assert admitted_candidates(document) == ()
    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="no admitted candidate"):
        service.record_decision(packet.packet_id, actor=_human(), action=ApprovalAction.APPROVE)


def test_a_decided_packet_cannot_be_decided_again():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_human())
    service.record_decision(packet.packet_id, actor=_human(), action=ApprovalAction.APPROVE)

    with pytest.raises(OpsBoardEmgiApprovalConflict):
        service.record_decision(
            packet.packet_id,
            actor=_human(),
            action=ApprovalAction.REJECT,
            reason="Changed my mind.",
        )


def test_nothing_is_released_before_a_human_approves():
    service = OpsBoardEmgiApprovalService()
    packet = service.open_review(_plan_document(), requested_by=_human())

    with pytest.raises(OpsBoardEmgiApprovalPolicyError, match="no site is released"):
        service.approved_candidate_site_ids(packet.packet_id)

    with pytest.raises(OpsBoardEmgiApprovalNotFound):
        service.get("netplan-emgi-approval-missing")


def test_end_to_end_real_producers_reach_a_human_approved_netplan_scenario():
    context, sitescore_doc, feasibility_doc, economics_doc = _real_decision_stack()
    integration = NetPlanEmgiIntegrationService(
        clock=lambda: datetime(2026, 8, 23, tzinfo=UTC),
        id_factory=lambda: "fixed-0001",
    )
    document = integration.build_plan_document(
        tenant_id=TENANT_ID,
        scenario_key="fy27-h1-expansion",
        manifest_id=MANIFEST_ID,
        market_context=context,
        requests=[
            CandidateDecisionRequest(
                site_id=SITE_ID,
                sitescore_doc=sitescore_doc,
                feasibility_doc=feasibility_doc,
                economics_doc=economics_doc,
            )
        ],
    )
    assert document.generated_at == "2026-08-23T00:00:00+00:00"

    approvals = OpsBoardEmgiApprovalService()
    packet = approvals.open_review(document, requested_by=_service_principal())
    decided = approvals.record_decision(
        packet.packet_id,
        actor=ApprovalActor(subject_id="user-exec-001", roles=frozenset({Role.EXECUTIVE})),
        action=ApprovalAction.APPROVE,
        reason="Board-approved FY27 H1 network plan.",
    )

    released = approvals.approved_candidate_site_ids(decided.packet_id)
    assert released == (SITE_ID,)

    options = build_scenario_options(
        candidate_sites=[
            candidate_input
            for candidate_input in document.candidate_site_inputs()
            if candidate_input.candidate_site_id in released
        ]
    )
    assert set(options) == {SITE_ID}
    open_option = next(option for option in options[SITE_ID] if option.action == NetworkAction.OPEN)
    assert open_option.budget_cost == pytest.approx(
        economics_doc.metrics.total_initial_cash_outlay, rel=1e-6
    )
    assert open_option.source_snapshot_ids == document.candidates[0].evidence.snapshot_ids
