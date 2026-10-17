"""Actual PG financial persistence to native scenario input, synthetic sources.

This proves no authenticated HTTP, approved report, draft materialization or
production source ownership. The native domain API is exercised directly.
"""
import pytest

from modules.netplan.application.planning import NetPlanService
from modules.netplan.application.production import NetPlanProductionExecutor
from modules.netplan.domain.planning import NetPlanScenarioStatus
from modules.netplan.integrations.emgi import CandidateAdmission, NetPlanEmgiIntegrationService
from modules.site_economics.application.service import SimulationOverrides, SiteEconomicsService
from modules.site_feasibility import SiteFeasibilityService
from modules.sitescore.v3 import SiteScoreV3Service
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository
from solver.netplan import NetworkAction
from tests.integration.test_netplan_emgi import MANIFEST_ID, SITE_ID, _market_context, _survey
from tests.integration.test_site_economics_repository_postgresql import owners as owners

pytestmark = pytest.mark.requires_live_env


def stack(tenant):
    context = _market_context()
    context["tenant_id"] = tenant
    economics = SiteEconomicsService().evaluate_site_market_context(
        context, tenant_id=tenant,
        overrides=SimulationOverrides(demand_multiplier=1.8, monthly_rent=40_000),
    )
    feasibility = SiteFeasibilityService().evaluate_feasibility(
        site_id=SITE_ID, market_context=context, surveys=[_survey()], tenant_id=tenant,
    )
    score = SiteScoreV3Service().evaluate(
        site_id=SITE_ID, manifest_id=MANIFEST_ID, market_context=context,
        feasibility_doc=feasibility, economics_doc=economics, tenant_id=tenant,
    )
    assert context["tenant_id"] == tenant
    assert (score.source_market_context_sha256
            == feasibility.metadata["source_market_context_sha256"]
            == economics.source_market_context_sha256)
    return context, economics, feasibility, score


def admit(context, economics, feasibility, score, tenant):
    return NetPlanEmgiIntegrationService().build_candidate(
        site_id=SITE_ID, tenant_id=tenant, manifest_id=MANIFEST_ID,
        market_context=context, sitescore_doc=score,
        feasibility_doc=feasibility, economics_doc=economics,
    )


def create_native(documents, candidate):
    # The actual PostgreSQL fixture requires live execution. An explicit
    # engineering mode must not override that guard; supply the real executor.
    service = NetPlanService(
        repository=DurableNetPlanRepository(documents), runtime_mode="production",
        production_executor=NetPlanProductionExecutor(),
    )
    assert service.production_required and service.strict_production_composition
    assert type(service.production_executor) is NetPlanProductionExecutor
    return service.create_scenario(
        tenant_id=documents.tenant_id, scenario_name="synthetic financial provenance scenario",
        planning_horizon="2026Q4", constraints={"max_budget": candidate.open_cost},
        candidate_sites=[candidate], correlation_id="engineering-content-binding",
    )


def test_financial_hash_and_native_cost_survive_independent_postgresql_owner(owners):
    documents, (writer, reader) = owners
    context, economics, feasibility, score = stack(documents[0].tenant_id)
    writer.save(economics)
    committed = reader.get_by_document_id(economics.document_id)
    decision = admit(context, committed, feasibility, score, documents[0].tenant_id)
    assert decision.admission is CandidateAdmission.ADMITTED
    scenario = create_native(documents[0], decision.candidate_input)
    persisted = DurableNetPlanRepository(documents[1]).get_scenario(scenario.scenario_id)
    assert persisted.status is NetPlanScenarioStatus.DRAFT
    option = next(row for row in persisted.options_by_entity[SITE_ID] if row.action is NetworkAction.OPEN)
    assert option.budget_cost == committed.metrics.total_initial_cash_outlay
    assert option.expected_gross_margin == committed.metrics.average_monthly_ebitda * 12
    assert option.source_snapshot_ids == decision.evidence.snapshot_ids
    assert decision.evidence.market_context_sha256 == committed.source_market_context_sha256


def test_same_id_changed_context_is_withheld_after_real_financial_storage(owners):
    documents, (writer, reader) = owners
    context, economics, feasibility, score = stack(documents[0].tenant_id)
    writer.save(economics)
    context["listing"]["average_area_ping"] += 1
    decision = admit(context, reader.get_by_document_id(economics.document_id),
                     feasibility, score, documents[0].tenant_id)
    assert decision.admission is CandidateAdmission.WITHHELD_PROVENANCE
    assert decision.candidate_input is None
    assert DurableNetPlanRepository(documents[1]).list_scenarios() == []
    assert reader.get_by_document_id(economics.document_id) == economics


def test_outer_pg_transaction_rolls_back_financial_and_native_scenario_together(owners):
    documents, (writer, reader) = owners
    context, economics, feasibility, score = stack(documents[0].tenant_id)
    scenario = None
    with pytest.raises(RuntimeError, match="engineering outer rollback"):
        with documents[0].engine.transaction():
            writer.save(economics)
            decision = admit(context, economics, feasibility, score, documents[0].tenant_id)
            assert decision.admission is CandidateAdmission.ADMITTED
            scenario = create_native(documents[0], decision.candidate_input)
            raise RuntimeError("engineering outer rollback")
    assert reader.get_by_document_id(economics.document_id) is None
    assert DurableNetPlanRepository(documents[1]).get_scenario(scenario.scenario_id) is None
    assert DurableNetPlanRepository(documents[1]).list_scenarios() == []
