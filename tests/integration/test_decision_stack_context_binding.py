"""Pure decision-stack contract checks, with explicitly synthetic component values.

Hash agreement proves content consistency, never current model or source
admission, authenticated HTTP ownership or a materialized planning draft.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from modules.netplan.integrations.emgi import (
    INTEGRATION_POLICY_VERSION,
    CandidateAdmission,
    NetPlanEmgiIntegrationService,
    netplan_emgi_document_digest,
    validate_netplan_emgi_document,
)
from modules.site_economics import SimulationOverrides, SiteEconomicsService
from modules.site_economics.application.context_inputs import content_sha256
from modules.site_feasibility import SiteFeasibilityService
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3 import SiteScoreV3Service
from modules.sitescore.v3.domain.contracts import (
    SiteScoreV3Document,
    validate_sitescore_v3_document,
    validate_sitescore_v3_payload,
)
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreAssessment,
    SiteScoreDecision,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    SiteMarketContextDocument,
)
from tests.domain.test_site_economics_context_inputs import envelope
from tests.integration.test_netplan_emgi import (
    MANIFEST_ID,
    SITE_ID,
    TENANT_ID,
    _build,
    _economics_wire,
    _feasibility_wire,
    _market_context,
    _real_decision_stack,
    _sitescore_wire,
    _survey,
)


def score(context, feasibility=None, economics=None, **kwargs):
    return SiteScoreV3Service().evaluate(
        site_id=SITE_ID, manifest_id=MANIFEST_ID, market_context=context,
        feasibility_doc=_feasibility_wire() if feasibility is None else feasibility,
        economics_doc=_economics_wire() if economics is None else economics, **kwargs,
    )


def stack(document):
    feasibility = SiteFeasibilityService().evaluate_feasibility(
        SITE_ID, document, [_survey()], tenant_id=TENANT_ID,
    )
    economics = SiteEconomicsService().evaluate_site_market_context(
        document, tenant_id=TENANT_ID, site_id=SITE_ID,
        overrides=SimulationOverrides(demand_multiplier=1.8, monthly_rent=40_000),
    )
    result = score(document, feasibility, economics, tenant_id=TENANT_ID)
    return result, feasibility, economics


@pytest.mark.parametrize("recommendation,readiness,decision", [
    ("FEASIBLE", DecisionReadiness.READY, SiteScoreDecision.GO),
    ("CONDITIONAL", DecisionReadiness.INCOMPLETE_FEASIBILITY, SiteScoreDecision.INCOMPLETE),
    ("UNKNOWN_REQUIRES_SURVEY", DecisionReadiness.INCOMPLETE_FEASIBILITY, SiteScoreDecision.INCOMPLETE),
    ("UNRECOGNIZED", DecisionReadiness.INCOMPLETE_FEASIBILITY, SiteScoreDecision.INCOMPLETE),
    (None, DecisionReadiness.INCOMPLETE_FEASIBILITY, SiteScoreDecision.INCOMPLETE),
    ("INFEASIBLE", DecisionReadiness.READY, SiteScoreDecision.NO_GO),
])
def test_model_components_remain_available_while_physical_readiness_is_separate(recommendation, readiness, decision):
    result = score(_market_context(), _feasibility_wire(recommendation=recommendation))
    assert result.assessment.availability is ScoreAvailability.AVAILABLE
    assert result.assessment.components.demand_score == .8
    assert result.assessment.readiness is readiness
    assert result.assessment.decision is decision
    assert validate_sitescore_v3_document(result)
    if readiness is not DecisionReadiness.READY:
        assert _build(sitescore_doc=result,
                      feasibility_doc=_feasibility_wire(recommendation=recommendation)).candidate_input is None


@pytest.mark.parametrize("recommendation", ["CONDITIONAL_GO", "INVESTIGATE", "UNKNOWN", None, "UNRECOGNIZED"])
def test_model_components_remain_available_without_an_economic_binding_go(recommendation):
    result = score(_market_context(), economics=_economics_wire(recommendation=recommendation))
    assert result.assessment.availability is ScoreAvailability.AVAILABLE
    assert result.assessment.components is not None
    assert result.assessment.readiness is DecisionReadiness.INCOMPLETE_ECONOMICS
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE
    assert validate_sitescore_v3_document(result)


def test_full_envelope_selects_child_scores_and_all_three_producer_hashes_agree():
    document = envelope(_market_context(site_id="other-site"), _market_context())
    result, feasibility, economics = stack(document)
    expected = content_sha256(document["contexts"][1])
    assert result.assessment.decision is SiteScoreDecision.GO
    assert result.assessment.components.demand_score == .8
    assert result.source_market_context_sha256 == feasibility.metadata["source_market_context_sha256"] == economics.source_market_context_sha256 == expected
    admitted = _build(market_context=document, sitescore_doc=result,
                      feasibility_doc=feasibility, economics_doc=economics)
    assert admitted.admission is CandidateAdmission.ADMITTED
    assert admitted.evidence.market_context_sha256 == expected
    assert admitted.evidence.policy_version("netplan_emgi_integration") == INTEGRATION_POLICY_VERSION == "netplan-emgi-admission-v3"
    assert admitted.evidence.policy_version("feasibility_gate") == "physical-feasibility-gate-v3"
    assert admitted.requires_human_approval


def test_unknown_drainage_withholds_native_candidate_even_with_six_available_model_components():
    context, _, _, economics = _real_decision_stack()
    survey = _survey()
    survey["attributes"].pop("utilities_drainage_available")
    feasibility = SiteFeasibilityService().evaluate_feasibility(SITE_ID, context, [survey])
    assert feasibility.decision.recommendation is FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    result = score(context, feasibility, economics)
    assert result.assessment.availability is ScoreAvailability.AVAILABLE
    assert result.assessment.components is not None
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE
    decision = _build(market_context=context, sitescore_doc=result,
        feasibility_doc=feasibility, economics_doc=economics)
    assert decision.admission is CandidateAdmission.WITHHELD_INCOMPLETE
    assert decision.candidate_input is None


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_site_cannot_choose_first_zoning_or_first_score(reverse):
    one, two = _market_context(), _market_context()
    two["identity"]["metadata"]["zoning"] = "residential_only"
    two["demand_score"] = .1
    rows = [one, two][::-1] if reverse else [one, two]
    document = envelope(*rows)
    feasibility = SiteFeasibilityService().evaluate_feasibility(
        SITE_ID, document, [_survey()], tenant_id=TENANT_ID,
    )
    result = score(document, tenant_id=TENANT_ID)
    assert feasibility.decision.recommendation is FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    assert feasibility.metadata["binding_recommendation_allowed"] is False
    assert feasibility.metadata["source_market_context_sha256"] is None
    assert result.assessment.availability is ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE
    assert result.source_market_context_sha256 is None


@pytest.mark.parametrize("corruption", ["sibling_manifest", "foreign_site", "foreign_tenant", "period", "malformed_contexts"])
def test_invalid_market_provenance_withholds_scores_as_well_as_decision(corruption):
    selected = _market_context(manifest_id="other-manifest") if corruption == "sibling_manifest" else _market_context()
    document = envelope(selected, _market_context(site_id="sibling"))
    if corruption == "foreign_site":
        document["contexts"] = [document["contexts"][1]]
    elif corruption == "foreign_tenant":
        document["tenant_id"] = "foreign"
    elif corruption == "period":
        document["contexts"][0]["period_key"] = "2026-09"
    elif corruption == "malformed_contexts":
        document["contexts"] = "not-an-array"
    result = score(document, tenant_id=TENANT_ID)
    assert result.assessment.availability is ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.components is None
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE


def test_root_manifest_can_vouch_but_root_component_values_cannot_replace_child():
    selected = _market_context()
    selected.pop("component_manifest_refs")
    document = envelope(selected)
    document["component_manifest_refs"] = _market_context()["component_manifest_refs"]
    document["demand_score"] = .999
    result, feasibility, economics = stack(document)
    assert result.assessment.decision is SiteScoreDecision.GO
    assert result.assessment.components.demand_score == .8
    assert _build(market_context=document, sitescore_doc=result,
                  feasibility_doc=feasibility, economics_doc=economics).is_admitted


@pytest.mark.parametrize("label", ["feasibility", "economics"])
@pytest.mark.parametrize("corruption", ["foreign_id", "false_hash", "blank_hash", "conflicting_hash", "missing_hash", "foreign_site"])
def test_legacy_manifest_cannot_hide_declared_or_missing_actual_context_references(label, corruption):
    context, old_score, feasibility, economics = _real_decision_stack()
    wire = (feasibility if label == "feasibility" else economics).to_dict()
    wire.setdefault("metadata", {})["manifest_id"] = MANIFEST_ID
    if corruption == "foreign_id":
        wire["source_market_context_id"] = "foreign-context"
    elif corruption == "false_hash":
        wire["source_market_context_sha256"] = "f" * 64
    elif corruption == "blank_hash":
        wire["source_market_context_sha256"] = ""
    elif corruption == "conflicting_hash":
        wire["source_market_context_sha256"] = content_sha256(context)
        wire["metadata"]["source_market_context_sha256"] = "f" * 64
    elif corruption == "missing_hash":
        wire.pop("source_market_context_sha256", None)
        wire["metadata"].pop("source_market_context_sha256", None)
    else:
        wire["site_id"] = "foreign-site"
    result = score(context, wire if label == "feasibility" else feasibility,
                   wire if label == "economics" else economics)
    assert old_score.assessment.decision is SiteScoreDecision.GO
    assert result.assessment.availability is ScoreAvailability.AVAILABLE
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE
    assert result.assessment.readiness is not DecisionReadiness.READY


def test_identical_declared_digest_does_not_hide_changed_actual_context():
    context = _market_context()
    context["sha256"] = "a" * 64
    result, feasibility, economics = stack(context)
    context["listing"]["average_area_ping"] += 1
    changed = score(context, feasibility, economics)
    assert changed.assessment.decision is SiteScoreDecision.INCOMPLETE
    assert changed.source_market_context_sha256 == content_sha256(context) != result.source_market_context_sha256


@pytest.mark.parametrize("field", ["demand_score", "format_score", "ramp_score", "cannibalization_score", "economics_score", "policy_score"])
@pytest.mark.parametrize("value", [True, "0.8", None, float("nan"), float("inf"), 10 ** 1000],
                         ids=["bool", "numeric-string", "missing", "nan", "inf", "huge-int"])
def test_each_invalid_model_component_is_unavailable_without_coercion_or_crash(field, value):
    context = _market_context()
    context[field] = value
    result = score(context)
    assert result.assessment.availability is ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.components is None
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE


def test_all_real_zero_components_stay_present_without_inventing_a_ranking_rule():
    context = _market_context()
    for name in ("demand_score", "format_score", "ramp_score", "cannibalization_score", "economics_score", "policy_score"):
        context[name] = 0
    result, _, _ = stack(context)
    assert result.assessment.availability is ScoreAvailability.AVAILABLE
    assert result.assessment.components.demand_score == 0
    assert result.assessment.components.policy_score == 0
    assert validate_sitescore_v3_document(result)


@pytest.mark.parametrize("label", ["feasibility", "sitescore"])
@pytest.mark.parametrize("corruption", ["missing", "false", "conflict"])
def test_netplan_requires_both_additional_producers_to_bind_actual_context(label, corruption):
    context, result, feasibility, economics = _real_decision_stack()
    wire = (feasibility if label == "feasibility" else result).to_dict()
    if corruption == "missing":
        wire.pop("source_market_context_sha256", None)
        wire.get("metadata", {}).pop("source_market_context_sha256", None)
    elif corruption == "false":
        wire["source_market_context_sha256"] = "f" * 64
    else:
        wire["source_market_context_sha256"] = content_sha256(context)
        wire.setdefault("metadata", {})["source_market_context_sha256"] = "f" * 64
    decision = _build(market_context=context, sitescore_doc=wire if label == "sitescore" else result,
                      feasibility_doc=wire if label == "feasibility" else feasibility, economics_doc=economics)
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.candidate_input is None
    assert any(("SiteScore v3" if label == "sitescore" else "Feasibility") + " source market context content hash" in reason for reason in decision.reasons)


@pytest.mark.parametrize("mutation", ["version", "no_components", "bool_component", "huge_component", "readiness", "availability", "bad_hash", "unhashable_state", "time"])
def test_sitescore_validator_and_native_mapper_reject_inconsistent_go_documents(mutation):
    wire = _sitescore_wire()
    if mutation == "version":
        wire["contract_version"] = "unknown-version"
    elif mutation == "no_components":
        wire["assessment"]["components"] = None
    elif mutation == "bool_component":
        wire["assessment"]["components"]["demand_score"] = True
    elif mutation == "huge_component":
        wire["assessment"]["components"]["demand_score"] = 10 ** 1000
    elif mutation == "readiness":
        wire["assessment"]["readiness"] = "INCOMPLETE_FEASIBILITY"
    elif mutation == "availability":
        wire["assessment"]["availability"] = "UNAVAILABLE_MISSING_INPUT"
    elif mutation == "bad_hash":
        wire["source_market_context_sha256"] = "invalid"
    elif mutation == "unhashable_state":
        wire["assessment"]["availability"] = {"not": "an enum"}
    else:
        wire["evaluated_at"] = "not-an-instant"
    assert not validate_sitescore_v3_payload(wire)
    decision = _build(sitescore_doc=wire)
    assert decision.candidate_input is None
    assert not decision.is_admitted


def retained_fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures/decision_stack/retained_policy_v2_approval.json").read_text())


def test_optional_hash_absent_keeps_actual_original_v1_wire_and_digest_shape():
    fixture = retained_fixture()
    payload = fixture["original_sitescore_wire"]
    retained = SiteScoreV3Document(**{
        **payload, "assessment": SiteScoreAssessment.from_dict(payload["assessment"]),
    })
    assert "source_market_context_sha256" not in payload
    assert retained.to_dict() == payload
    # An older slotted pickle has no new slot value, rather than explicit None.
    object.__delattr__(retained, "source_market_context_sha256")
    assert retained.to_dict() == payload
    assert validate_sitescore_v3_document(retained)
    assert content_sha256(retained.to_dict()) == fixture["original_sitescore_wire_sha256"]
    assert _build(sitescore_doc=retained).candidate_input is None


def test_actual_original_plan_policy_digest_and_review_history_are_not_repinned():
    fixture = retained_fixture()
    raw = fixture["original_plan"]
    before = deepcopy(raw)
    validate_netplan_emgi_document(raw)
    assert raw == before
    assert raw["policy"]["policy_version"] == "netplan-emgi-admission-v2"
    assert raw["candidates"][0]["evidence"]["policy_versions"]["netplan_emgi_integration"] == "netplan-emgi-admission-v2"
    assert netplan_emgi_document_digest(raw) == fixture["original_plan_digest"]
    bundle = fixture["original_evidence_bundle"]
    assert bundle["document_digest"] == fixture["original_plan_digest"]
    assert bundle["state"] == "APPROVED"
    assert INTEGRATION_POLICY_VERSION == "netplan-emgi-admission-v3"


def test_generated_contract_missing_real_model_components_stays_unavailable():
    document = envelope(_market_context())
    document.update(generated_at="2026-10-16T00:00:00Z",
                    source_support={"sample_count": 1, "observation_count": 1})
    row = document["contexts"][0]
    row.update(source_support=document["source_support"],
               coverage={"overall_readiness": "ready", "domain_coverage": {}},
               mobility={"status": "unavailable"}, event={"status": "unavailable"},
               poi={"status": "unavailable"}, traffic={"status": "unavailable"})
    for domain in ("listing", "rent", "competitor", "catchment", "demand"):
        row[domain]["status"] = "available"
    row["catchment"].update(catchment_id="engineering-catchment", cutoff_seconds=600,
                            geom={"type": "Polygon", "coordinates": []}, graph_version="engineering",
                            routing_engine="engineering", travel_mode="pedestrian")
    row["identity"].update(latitude=25, longitude=121, h3_resolution=9,
                           primary_h3_index="8928308280fffff")
    typed = SiteMarketContextDocument.from_dict(document)
    assert "demand_score" not in typed.to_dict()["contexts"][0]
    result, feasibility, economics = stack(typed)
    assert result.assessment.readiness is DecisionReadiness.READY
    assert result.assessment.availability is ScoreAvailability.UNAVAILABLE_MISSING_INPUT
    assert result.assessment.decision is SiteScoreDecision.INCOMPLETE
    assert result.assessment.components is None
    decision = NetPlanEmgiIntegrationService().build_candidate(
        site_id=SITE_ID, tenant_id=TENANT_ID, manifest_id=MANIFEST_ID, market_context=typed,
        sitescore_doc=result, feasibility_doc=feasibility, economics_doc=economics,
    )
    assert decision.admission is CandidateAdmission.WITHHELD_INCOMPLETE
    assert decision.candidate_input is None
