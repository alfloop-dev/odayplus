"""Engineering simulations prove missing facts stay missing, not source approval."""
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.site_economics.application.context_inputs import (
    SiteEconomicsInputError,
    content_sha256,
)
from modules.site_economics.application.service import SimulationOverrides, SiteEconomicsService
from packages.oday_data_product_contracts_client.models.site_market_context import (
    SiteMarketContextDocument,
)
from tests.integration.test_netplan_emgi import SITE_ID, TENANT_ID, _market_context
from tests.site_economics_fixtures import synthetic_g2_registry

FIELDS = [
    ("listing", "average_area_ping"),
    ("listing", "median_asking_rent_per_ping"),
    ("demand", "total_population"),
    ("demand", "density_per_sq_km"),
    ("competitor", "active_competitors"),
    ("competitor", "competitor_density_per_sq_km"),
]


def evaluate(context=None, **kwargs):
    return SiteEconomicsService(registry=synthetic_g2_registry()).evaluate_site_market_context(
        _market_context() if context is None else context, tenant_id=TENANT_ID, **kwargs,
    )


def envelope(*contexts):
    return {"document_id": "engineering-release", "tenant_id": TENANT_ID,
            "period_grain": "MONTHLY", "period_key": "2026-10",
            "contexts": [{**row, "period_grain": "MONTHLY", "period_key": "2026-10"}
                         for row in contexts]}


@pytest.mark.parametrize("domain,field", FIELDS)
@pytest.mark.parametrize("value", [None, True, "12", -1, float("nan"), float("inf")],
                         ids=["missing", "bool", "string", "negative", "nan", "inf"])
def test_missing_or_invalid_observation_never_invokes_simulator(domain, field, value):
    context = _market_context()
    context[domain][field] = value
    # Remove the other observed rent too: an actual second source may legitimately
    # supply missing listing rent, while an invalid explicit value must be refused.
    if (domain, field) == FIELDS[1]:
        context["rent"].pop("median_rent_per_ping")

    class NoSimulation:
        def simulate(self, _input):
            pytest.fail("invalid observations reached the real simulation boundary")

    with pytest.raises(SiteEconomicsInputError) as refused:
        SiteEconomicsService(simulator=NoSimulation()).evaluate_site_market_context(context)
    assert str(value) not in str(refused.value) or str(value) in {"None", "True"}


def test_actual_zero_population_competitors_and_rent_are_preserved():
    context = _market_context()
    for domain, field in FIELDS[1:]:
        context[domain][field] = 0
    result = evaluate(context)
    assert result.assumptions.monthly_base_rent == 0
    assert result.assumptions.demand_multiplier == .35
    assert result.assumptions.competitor_discount == 0
    assert result.metadata["input_origins"]["monthly_rent"].startswith("listing.")


def test_area_zero_is_refused_instead_of_using_default_twenty_five():
    context = _market_context()
    context["listing"]["average_area_ping"] = 0
    with pytest.raises(SiteEconomicsInputError):
        evaluate(context)


def test_rent_fallback_requires_an_actual_valid_second_domain():
    context = _market_context()
    del context["listing"]["median_asking_rent_per_ping"]
    assert evaluate(context).assumptions.monthly_base_rent == 28 * 2400
    context["rent"]["status"] = "unavailable"
    with pytest.raises(SiteEconomicsInputError, match="DOMAIN_UNAVAILABLE:rent"):
        evaluate(context)


@pytest.mark.parametrize("domain", ["listing", "demand", "competitor"])
def test_unavailable_domain_cannot_supply_numbers_even_when_present(domain):
    context = _market_context()
    context[domain]["status"] = "unavailable"
    with pytest.raises(SiteEconomicsInputError, match="DOMAIN_UNAVAILABLE"):
        evaluate(context)


def test_explicit_assumptions_can_simulate_missing_facts_but_are_labelled():
    context = _market_context()
    for domain in ("listing", "rent", "demand", "competitor"):
        del context[domain]
    result = evaluate(context, overrides=SimulationOverrides(
        area_ping=30, monthly_rent=0, demand_multiplier=1.5, competitor_discount=0,
    ))
    assert result.assumptions.area_ping == 30
    assert result.assumptions.monthly_base_rent == 0
    assert result.assumptions.demand_multiplier == 1.5
    assert result.metadata["input_origins"] == dict.fromkeys(
        ["area_ping", "monthly_rent", "demand_multiplier", "competitor_discount"],
        "explicit_simulation_assumption",
    )
    assert result.metadata["derived_demand_mult"] is None
    assert result.metadata["production_admission"] == "not_established_by_simulation"


def test_explicit_area_is_used_for_observed_rent_calculation():
    result = evaluate(overrides=SimulationOverrides(area_ping=30))
    assert result.assumptions.monthly_base_rent == 30 * 2400


@pytest.mark.parametrize("field,value", [
    ("monthly_rent", True), ("demand_multiplier", "1.2"),
    ("competitor_discount", 1.1), ("debt_ratio", -1),
    ("lease_term_months", 1.5), ("annual_discount_rate", float("inf")),
    ("custom_equipment_capex", float("nan")),
])
def test_invalid_explicit_assumptions_are_refused(field, value):
    with pytest.raises(SiteEconomicsInputError):
        evaluate(overrides=replace(SimulationOverrides(), **{field: value}))


def test_fully_released_document_selects_real_child_and_binds_both_hashes():
    document = envelope(_market_context())
    result = evaluate(document)
    assert result.site_id == SITE_ID
    assert result.source_market_context_id == document["contexts"][0]["context_id"]
    assert result.source_market_context_sha256 == content_sha256(document["contexts"][0])
    assert result.metadata["source_market_context_document_sha256"] == content_sha256(document)
    assert result.metadata["source_market_context_document_id"] == document["document_id"]
    assert result.metadata["context_tenant_declared"] is True


def test_multiple_contexts_need_explicit_unique_site_selection():
    document = envelope(_market_context(site_id="sibling"), _market_context())
    with pytest.raises(SiteEconomicsInputError, match="SELECTION_REQUIRED"):
        evaluate(document)
    result = evaluate(document, site_id=SITE_ID)
    assert result.site_id == SITE_ID
    assert result.source_market_context_sha256 == content_sha256(document["contexts"][1])
    with pytest.raises(SiteEconomicsInputError, match="SELECTION_REQUIRED"):
        evaluate(document, site_id="unknown")
    document["contexts"].append(deepcopy(document["contexts"][1]))
    with pytest.raises(SiteEconomicsInputError, match="SELECTION_REQUIRED"):
        evaluate(document, site_id=SITE_ID)


@pytest.mark.parametrize("field", ["tenant_id", "period_key", "period_grain"])
def test_document_scope_conflicts_are_refused(field):
    document = envelope(_market_context())
    document[field] = "different"
    with pytest.raises(SiteEconomicsInputError, match="SCOPE_MISMATCH"):
        evaluate(document)


@pytest.mark.parametrize("corruption", ["missing_site", "missing_context", "conflicting_site", "nonstring_site"])
def test_missing_or_conflicting_identity_is_not_replaced_with_unknown(corruption):
    context = _market_context()
    if corruption == "missing_site":
        del context["identity"]["site_id"]
    elif corruption == "missing_context":
        del context["context_id"]
    elif corruption == "conflicting_site":
        context["site_id"] = "different"
    else:
        context["identity"]["site_id"] = 123
    with pytest.raises(SiteEconomicsInputError):
        evaluate(context)


def test_generated_released_contract_is_supported_without_editing_generated_code():
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
    result = evaluate(typed)
    assert result.site_id == SITE_ID
    assert result.source_market_context_sha256 == content_sha256(typed.to_dict()["contexts"][0])
    assert result.assumptions.area_ping == 28


def test_hash_is_computed_from_detached_content_and_never_trusts_declared_hash():
    context = _market_context()
    context["sha256"] = "a" * 64
    result = evaluate(context)
    expected = content_sha256(context)
    assert result.source_market_context_sha256 == expected != context["sha256"]
    context["listing"]["average_area_ping"] = 999
    assert result.source_market_context_sha256 == expected
    assert result.assumptions.area_ping == 28
