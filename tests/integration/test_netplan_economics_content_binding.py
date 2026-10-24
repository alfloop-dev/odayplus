"""Real financial outputs must remain tied to this candidate's actual content.

These tests exercise the native input mapping, not authenticated HTTP ownership.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.netplan.integrations.emgi import (
    CandidateAdmission,
    CandidateDecisionRequest,
    NetPlanEmgiIntegrationService,
)
from modules.site_economics.application.context_inputs import content_sha256
from tests.integration.test_netplan_emgi import (
    MANIFEST_ID,
    SITE_ID,
    TENANT_ID,
    _build,
    _economics_wire,
    _market_context,
    _real_decision_stack,
)


def test_real_producer_hash_reaches_exact_native_open_cost_and_evidence():
    context, score, feasibility, economics = _real_decision_stack()
    decision = _build(market_context=context, sitescore_doc=score,
                      feasibility_doc=feasibility, economics_doc=economics)
    assert decision.admission is CandidateAdmission.ADMITTED
    assert decision.evidence.market_context_sha256 == economics.source_market_context_sha256 == content_sha256(context)
    assert decision.candidate_input.open_cost == economics.metrics.total_initial_cash_outlay
    assert decision.candidate_input.expected_gross_margin == economics.metrics.average_monthly_ebitda * 12


@pytest.mark.parametrize("corruption", ["missing", "false_hash", "changed_content", "same_id_other_site", "foreign_feasibility"])
def test_same_context_id_cannot_hide_changed_financial_provenance(corruption):
    context, score, feasibility, economics = _real_decision_stack()
    if corruption == "missing":
        economics = replace(economics, source_market_context_sha256=None)
    elif corruption == "false_hash":
        economics = replace(economics, source_market_context_sha256="f" * 64)
    elif corruption == "changed_content":
        context["listing"]["average_area_ping"] = 29
    elif corruption == "same_id_other_site":
        economics = replace(economics, site_id="foreign")
    else:
        feasibility = replace(feasibility, site_id="foreign")
    decision = _build(market_context=context, sitescore_doc=score,
                      feasibility_doc=feasibility, economics_doc=economics)
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.candidate_input is None


def test_declared_market_hash_cannot_replace_actual_content_hash():
    context = _market_context()
    context["sha256"] = "f" * 64
    economics = _economics_wire()
    economics["source_market_context_sha256"] = context["sha256"]
    decision = _build(market_context=context, economics_doc=economics)
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.evidence.market_context_sha256 == content_sha256(context)


def test_duplicate_matching_contexts_are_refused_instead_of_selecting_first():
    context = _market_context()
    decision = _build(market_context={"document_id": "engineering-doc",
                      "contexts": [context, deepcopy(context)]})
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.candidate_input is None


def test_plan_tenant_cannot_relabel_another_tenants_financial_document():
    context, score, feasibility, economics = _real_decision_stack()
    document = NetPlanEmgiIntegrationService().build_plan_document(
        tenant_id="foreign-tenant", scenario_key="engineering-plan", manifest_id=MANIFEST_ID,
        market_context=context, requests=[CandidateDecisionRequest(
            SITE_ID, score, feasibility, economics,
        )],
    )
    assert economics.tenant_id == TENANT_ID
    assert document.candidates[0].admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert document.candidate_site_inputs() == ()


def test_a_sibling_contract_declaration_does_not_admit_the_selected_context():
    context = _market_context()
    del context["component_manifest_refs"]
    economics = _economics_wire()
    economics["source_market_context_sha256"] = content_sha256(context)
    decision = _build(
        market_context={"document_id": "engineering-doc", "contexts": [
            context, _market_context(site_id="sibling"),
        ]}, economics_doc=economics,
    )
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.candidate_input is None
