"""Domain unit & contract tests for ODayPlus Site Economics Simulator.

Verifies acceptance criteria for ODP-ECONOMICS-001:
1. Version machine mix, CAPEX, fitout, utilities, maintenance, financing, tax, and residual value.
2. Compute monthly cash flow, NPV, IRR, and censored payback outcomes.
3. Conforms to odayplus.site-economics.v1 contract and integrates with emgi.site-market-context.v1.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from modules.site_economics import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    CensoringType,
    EconomicsDecision,
    FinancingSpec,
    FitoutSpec,
    InMemorySiteEconomicsRepository,
    MachineClass,
    MachineMixItem,
    MachineMixSpec,
    MachineModelSpec,
    MaintenanceSpec,
    RampCurveSpec,
    ResidualValueSpec,
    SeasonalitySpec,
    SimulationInput,
    SimulationOverrides,
    SiteEconomicsDocument,
    SiteEconomicsService,
    SiteEconomicsSimulator,
    SiteOperatingParameters,
    TargetFormatRegistry,
    TargetFormatSpec,
    TaxSpec,
    UtilitiesCostSpec,
    compute_irr,
    compute_npv,
    compute_payback,
    compute_pmt,
    validate_site_economics_document,
)
from modules.site_economics.domain.formats import (
    DRYER_STACK_15KG_V1,
    VENDING_DETERGENT_SMART_V1,
    WASHER_LARGE_20KG_V1,
    build_default_g2_standard_v1,
)
from tests.site_economics_fixtures import (
    legacy_arithmetic_format,
    synthetic_g2_format,
    synthetic_g2_registry,
)


def test_original_g2_mixed_catalog_cannot_generate_new_financial_results():
    original = build_default_g2_standard_v1()
    assert original.machine_mix.machine_counts_by_class == {
        "WASHER": 5, "DRYER": 4, "COMBO": 1, "VENDING": 1,
    }
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsSimulator().simulate(SimulationInput(
            format_spec=original,
            operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25),
        ))


@pytest.mark.parametrize("area", [16.0, 25.0, 50.0])
def test_default_oday_target_never_substitutes_another_store_format(area):
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsService().evaluate_site("synthetic-g2-target", area, 0)


@pytest.mark.parametrize("kind", ["quantity", "washer", "same_capacity", "same_model", "nonfinite", "bool_quantity", "known_model_retyped"])
def test_explicit_g2_must_have_three_distinct_large_and_three_medium_combos(kind):
    spec = synthetic_g2_format()
    large, medium = spec.machine_mix.items
    if kind == "quantity":
        large = replace(large, quantity=2)
    elif kind == "washer":
        large = replace(large, machine_model=replace(large.machine_model, machine_class=MachineClass.WASHER))
    elif kind == "same_capacity":
        large = replace(large, machine_model=replace(large.machine_model, capacity_kg=medium.machine_model.capacity_kg))
    elif kind == "same_model":
        large = replace(large, machine_model=replace(large.machine_model, model_id=medium.machine_model.model_id))
    elif kind == "nonfinite":
        large = replace(large, machine_model=replace(large.machine_model, base_cycle_price=float("nan")))
    elif kind == "bool_quantity":
        large = replace(large, quantity=True)
    else:
        large = replace(large, machine_model=replace(large.machine_model, model_id=WASHER_LARGE_20KG_V1.model_id))
    bad = replace(spec, machine_mix=replace(spec.machine_mix, items=(large, medium)))
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsSimulator().simulate(SimulationInput(
            format_spec=bad,
            operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25),
        ))


def test_registered_format_version_cannot_be_replaced_with_different_financial_facts():
    registry = synthetic_g2_registry()
    spec = synthetic_g2_format()
    registry.register(spec)
    with pytest.raises(ValueError, match="TARGET_FORMAT_IMMUTABLE_VERSION"):
        registry.register(replace(spec, machine_mix=replace(spec.machine_mix,
            installation_and_delivery_fee=0)))
    assert registry.get("ODAY_G2") == spec


@pytest.mark.parametrize("field,value", [("quantity", "3"), ("quantity", 3.5),
    ("capacity_kg", True), ("unit_capex", "100"), ("base_cycle_price", False)])
def test_g2_deserialization_does_not_repair_nonmeasurements_into_valid_configuration(field, value):
    raw = synthetic_g2_format().to_dict()
    item = raw["machine_mix"]["items"][0]
    if field == "quantity":
        item[field] = value
    else:
        item["machine_model"][field] = value
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsSimulator().simulate(SimulationInput(
            format_spec=TargetFormatSpec.from_dict(raw),
            operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25),
        ))


@pytest.mark.parametrize("area", [16.0, 25.0, 50.0])
@pytest.mark.parametrize("entry", ["direct", "market_context"])
def test_explicit_six_combo_g2_runs_all_scenarios_without_area_based_format_substitution(area, entry):
    from modules.site_economics.application.context_inputs import content_sha256

    registry = synthetic_g2_registry()
    expected = registry.get("ODAY_G2")
    inputs = []

    class ObserveActualSimulator(SiteEconomicsSimulator):
        def simulate(self, value):
            inputs.append(value)
            return super().simulate(value)

    service = SiteEconomicsService(registry=registry, simulator=ObserveActualSimulator())
    if entry == "direct":
        document = service.evaluate_site("synthetic-g2", area, 0)
    else:
        context = {"context_id": "synthetic-g2-context", "identity": {"site_id": "synthetic-g2"},
            "listing": {"status": "available", "average_area_ping": area, "median_asking_rent_per_ping": 0},
            "demand": {"status": "available", "total_population": 15000, "density_per_sq_km": 8000},
            "competitor": {"status": "available", "active_competitors": 0, "competitor_density_per_sq_km": 0}}
        document = service.evaluate_site_market_context(context)
    assert document.format_code == "ODAY_G2"
    assert document.format_version == expected.format_version == "2.0.0-engineering"
    assert document.assumptions.area_ping == area
    assert document.metadata["target_format_snapshot"] == expected.to_dict()
    assert document.metadata["target_format_sha256"] == content_sha256(expected.to_dict())
    assert document.metadata["format_configuration_policy"] == "oday-g2-six-combo-v1"
    assert document.metadata["production_admission"] == "not_established_by_simulation"
    assert inputs and all(value.format_spec == expected for value in inputs)
    assert expected.machine_mix.machine_counts_by_class == {"COMBO": 6}
    assert sorted(item.quantity for item in expected.machine_mix.items) == [3, 3]
    assert len({item.machine_model.capacity_kg for item in expected.machine_mix.items}) == 2
    assert len(document.monthly_schedule) == 60
    assert set(document.scenarios) == {"base", "optimistic", "pessimistic", "stress_test"}
    validate_site_economics_document(document)
    assert SiteEconomicsDocument.from_dict(document.to_dict()).metadata == document.metadata


def test_retained_mixed_v1_roundtrip_remains_readable_without_new_calculation_authority():
    retained = build_default_g2_standard_v1()
    restored = TargetFormatSpec.from_dict(retained.to_dict())
    assert restored == retained
    assert TargetFormatRegistry().get("ODAY_G2", "1.0.0") == retained
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsService().evaluate_site("synthetic-retained", 25, 0,
            format_code="ODAY_G2", format_version="1.0.0")


def test_new_six_combo_payload_cannot_reuse_the_retained_mixed_g2_version():
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsSimulator().simulate(SimulationInput(
            format_spec=replace(synthetic_g2_format(), format_version="1.0.0"),
            operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25)))


def test_machine_model_and_mix_versioning() -> None:
    washer = WASHER_LARGE_20KG_V1
    assert washer.model_id == "W-20KG-V1"
    assert washer.machine_class == MachineClass.WASHER
    assert washer.unit_capex == 280_000.0
    assert washer.residual_value_per_unit == 280_000.0 * 0.12

    mix_item = MachineMixItem(machine_model=washer, quantity=3)
    assert mix_item.total_capex == 840_000.0
    assert mix_item.total_residual_value == 840_000.0 * 0.12

    mix = MachineMixSpec(
        spec_id="MIX-TEST-V1",
        version="1.0.0",
        items=(
            MachineMixItem(machine_model=washer, quantity=3),
            MachineMixItem(machine_model=DRYER_STACK_15KG_V1, quantity=4),
        ),
        installation_and_delivery_fee=50_000.0,
    )
    assert mix.total_machines == 7
    assert mix.total_equipment_capex == (3 * 280_000.0 + 4 * 310_000.0) + 50_000.0
    assert mix.machine_counts_by_class == {"WASHER": 3, "DRYER": 4}

    # Serialization roundtrip
    d = mix.to_dict()
    mix_restored = MachineMixSpec.from_dict(d)
    assert mix_restored.spec_id == mix.spec_id
    assert mix_restored.total_machines == 7
    assert mix_restored.total_equipment_capex == mix.total_equipment_capex


def test_fitout_and_opex_specs_versioning() -> None:
    fitout = FitoutSpec(
        spec_id="FIT-TEST-V1",
        version="1.0.0",
        base_fitout_cost=600_000.0,
        cost_per_ping=30_000.0,
        plumbing_upgrade_cost=200_000.0,
        electrical_upgrade_cost=200_000.0,
        gas_piping_upgrade_cost=150_000.0,
        facade_signage_cost=100_000.0,
        telemetry_smart_hub_cost=50_000.0,
    )
    total_fitout_25_ping = fitout.compute_total_fitout(25.0)
    expected_fitout = (
        600_000.0 + (25.0 * 30_000.0) + 200_000.0 + 200_000.0 + 150_000.0 + 100_000.0 + 50_000.0
    )
    assert total_fitout_25_ping == expected_fitout

    util = UtilitiesCostSpec(spec_id="UTIL-TEST", version="1.0.0")
    assert util.electricity_rate_per_kwh > 0
    assert util.water_rate_per_liter > 0

    maint = MaintenanceSpec(spec_id="MAINT-TEST", version="1.0.0")
    assert maint.preventative_contract_monthly_base > 0

    fin = FinancingSpec(
        spec_id="FIN-TEST", version="1.0.0", debt_ratio=0.60, annual_interest_rate=0.035
    )
    assert fin.monthly_interest_rate == pytest.approx(0.035 / 12.0)

    tax = TaxSpec(spec_id="TAX-TEST", version="1.0.0", corporate_tax_rate=0.20)
    assert tax.corporate_tax_rate == 0.20

    res = ResidualValueSpec(spec_id="RES-TEST", version="1.0.0", equipment_salvage_ratio=0.12)
    assert res.recover_lease_deposit is True


def test_target_format_registry_and_resolution() -> None:
    reg = TargetFormatRegistry()
    codes = reg.list_codes()
    assert "ODAY_G2" in codes
    assert "ODAY_G3_COMPACT" in codes
    assert "ODAY_FLAGSHIP" in codes

    g2 = reg.get("ODAY_G2")
    assert g2.format_code == "ODAY_G2"
    assert g2.format_version == "1.0.0"
    assert g2.recommended_area_ping == 25.0

    # Test area-based recommendation
    best_small = reg.find_best_format_for_area(16.0)
    assert best_small.format_code == "ODAY_G3_COMPACT"

    best_medium = reg.find_best_format_for_area(26.0)
    assert best_medium.format_code == "ODAY_G2"

    best_large = reg.find_best_format_for_area(50.0)
    assert best_large.format_code == "ODAY_FLAGSHIP"

    # Custom format registration
    custom_spec = build_default_g2_standard_v1()
    custom_v2 = TargetFormatSpec(
        format_code="ODAY_G2",
        format_name="ODay G2 Revised",
        format_version="2.0.0",
        description="Upgraded 2.0 version",
        target_area_ping_min=20.0,
        target_area_ping_max=40.0,
        recommended_area_ping=28.0,
        machine_mix=custom_spec.machine_mix,
        fitout_spec=custom_spec.fitout_spec,
        utilities_spec=custom_spec.utilities_spec,
        maintenance_spec=custom_spec.maintenance_spec,
        financing_spec=custom_spec.financing_spec,
        tax_spec=custom_spec.tax_spec,
        residual_spec=custom_spec.residual_spec,
        ramp_spec=custom_spec.ramp_spec,
        seasonality_spec=custom_spec.seasonality_spec,
    )
    reg.register(custom_v2)
    latest_g2 = reg.get("ODAY_G2")
    assert latest_g2.format_version == "2.0.0"
    v1_g2 = reg.get("ODAY_G2", "1.0.0")
    assert v1_g2.format_version == "1.0.0"


def test_pmt_npv_irr_calculations() -> None:
    # 1. PMT check: ,000,000 at 3.6% annual interest for 60 months
    # Monthly rate = 0.003
    # PMT = 1,000,000 * 0.003 * (1.003^60) / (1.003^60 - 1) ~ 18,237.49
    pmt = compute_pmt(1_000_000.0, 0.036, 60)
    assert pmt == pytest.approx(18_237.49, rel=1e-3)

    # 2. NPV check
    # Outlay -100, then +55 at t=1, +55 at t=2 at 0% discount rate = +10
    assert compute_npv([-100.0, 55.0, 55.0], 0.0) == pytest.approx(10.0)

    # 3. IRR check: -100, +60, +60 -> monthly rate is ~13.066%, annualized is ~326%
    # With 12 periods of equal cash flows: -1200, then +110 each month for 12 months
    cfs = [-1200.0] + [110.0] * 12
    irr = compute_irr(cfs)
    assert irr is not None
    assert irr > 0.0

    # Non-convergent / all negative cash flows
    assert compute_irr([-100.0, -10.0, -10.0]) is None
    assert compute_irr([100.0, 10.0, 10.0]) is None


def test_censored_payback_outcomes() -> None:
    # 1. Normal Payback: -100 at t=0, +40 at t=1, +40 at t=2, +40 at t=3
    # Payback is at t = 2.5 months
    res_normal = compute_payback([-100.0, 40.0, 40.0, 40.0], horizon_months=12)
    assert res_normal.is_censored is False
    assert res_normal.censoring_type == CensoringType.NOT_CENSORED
    assert res_normal.payback_months == pytest.approx(2.5, abs=0.05)

    # 2. Right-Censored: -100 at t=0, +10 at t=1..t=5 (sum = 50 < 100)
    res_censored = compute_payback([-100.0, 10.0, 10.0, 10.0, 10.0, 10.0], horizon_months=5)
    assert res_censored.is_censored is True
    assert res_censored.censoring_type == CensoringType.RIGHT_CENSORED
    assert res_censored.payback_months is None
    assert "does not reach breakeven" in str(res_censored.censored_reason)

    # 3. Negative cash flow: -100 at t=0, -10 at t=1..t=5
    res_neg = compute_payback([-100.0, -10.0, -5.0, -2.0], horizon_months=3)
    assert res_neg.is_censored is True
    assert res_neg.censoring_type == CensoringType.NEGATIVE_CASH_FLOW
    assert res_neg.payback_months is None
    assert "negative or zero" in str(res_neg.censored_reason)


def test_full_site_economics_simulation_schedule() -> None:
    g2_format = legacy_arithmetic_format()
    operating = SiteOperatingParameters(
        monthly_base_rent=60_000.0,
        area_ping=25.0,
        lease_term_months=60,
        lease_deposit_months=2,
        rent_free_months=1,
    )

    sim_input = SimulationInput(
        format_spec=g2_format,
        operating_params=operating,
        horizon_months=60,
        annual_discount_rate=0.08,
    )

    simulator = SiteEconomicsSimulator()
    result = simulator.simulate(sim_input)

    # Verify Schedule Length
    assert len(result.monthly_schedule) == 60
    assert len(result.unlevered_cash_flows) == 61  # Month 0 + 60 months
    assert len(result.levered_cash_flows) == 61

    # Month 0 outlays
    total_outlay = result.metrics.total_initial_cash_outlay
    equity = result.metrics.equity_investment
    debt = result.metrics.debt_financed
    assert equity + debt == pytest.approx(total_outlay)
    assert result.unlevered_cash_flows[0] == -total_outlay
    assert result.levered_cash_flows[0] == -equity

    # Month 1 checks (Rent free period)
    m1 = result.monthly_schedule[0]
    assert m1.month == 1
    assert m1.rent_expense == 0.0
    assert m1.ramp_multiplier == 0.40
    assert m1.gross_revenue > 0

    # Month 2 checks (Rent starts)
    m2 = result.monthly_schedule[1]
    assert m2.rent_expense == 60_000.0

    # Terminal Month 60 checks
    m60 = result.monthly_schedule[59]
    assert m60.is_terminal is True
    assert m60.terminal_salvage_inflow > 0
    assert m60.terminal_deposit_return == 120_000.0  # 2 months deposit of 60k
    assert m60.terminal_decommissioning_outflow > 0

    # Returns metrics
    assert result.metrics.average_monthly_revenue > 100_000.0
    assert result.metrics.average_ebitda_margin > 0.30
    assert result.metrics.breakeven_monthly_revenue > 0
    assert result.metrics.breakeven_turns_per_day > 0
    assert result.metrics.levered_npv > 0
    assert result.metrics.levered_irr is not None
    assert result.metrics.levered_irr > 0.05
    assert result.metrics.simple_payback.is_censored is False
    assert result.metrics.simple_payback.payback_months is not None
    assert result.metrics.simple_payback.payback_months < 45.0

    # Decision Recommendation
    assert result.decision.recommendation in (
        EconomicsDecision.GO,
        EconomicsDecision.CONDITIONAL_GO,
    )
    assert result.decision.confidence_score >= 0.70


def test_service_evaluate_site_and_scenarios() -> None:
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    doc = service.evaluate_site(
        site_id="SITE-TAIPEI-001",
        area_ping=25.0,
        monthly_rent=65_000.0,
        tenant_id="tenant-tw-01",
        format_code="ODAY_G2",
    )

    assert doc.contract_id == CONTRACT_ID
    assert doc.contract_version == CONTRACT_VERSION
    assert doc.site_id == "SITE-TAIPEI-001"
    assert doc.format_code == "ODAY_G2"
    assert doc.metrics.horizon_months == 60

    # Check 4 scenario sensitivity branches
    assert "base" in doc.scenarios
    assert "optimistic" in doc.scenarios
    assert "pessimistic" in doc.scenarios
    assert "stress_test" in doc.scenarios

    base_scen = doc.scenarios["base"]
    opt_scen = doc.scenarios["optimistic"]
    pess_scen = doc.scenarios["pessimistic"]

    assert opt_scen.levered_npv >= base_scen.levered_npv
    assert pess_scen.levered_npv <= base_scen.levered_npv

    # Verify document validation
    validate_site_economics_document(doc)

    # Verify JSON roundtrip
    json_str = doc.to_json()
    doc_reloaded = SiteEconomicsDocument.from_json(json_str)
    assert doc_reloaded.site_id == doc.site_id
    assert doc_reloaded.digest == doc.digest
    assert doc_reloaded.metrics.levered_npv == doc.metrics.levered_npv


def test_service_evaluate_with_site_market_context() -> None:
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    # Mock / real emgi.site-market-context.v1 payload
    mock_market_context = {
        "context_id": "ctx-tw-tp-001",
        "identity": {
            "site_id": "SITE-TP-XINYI-101",
            "site_name": "Xinyi Anhe ODay Candidate",
            "primary_h3_index": "8928308280fffff",
            "latitude": 25.033,
            "longitude": 121.555,
            "h3_resolution": 9,
        },
        "listing": {
            "average_area_ping": 28.0,
            "median_asking_rent_per_ping": 2_400.0,
            "status": "available",
        },
        "demand": {
            "total_population": 22_000.0,
            "density_per_sq_km": 14_000.0,
            "status": "available",
        },
        "competitor": {
            "active_competitors": 2,
            "competitor_density_per_sq_km": 1.5,
            "status": "available",
        },
        "rent": {
            "median_rent_per_ping": 2_400.0,
            "status": "available",
        },
        "catchment": {
            "catchment_id": "cat-001",
            "status": "available",
        },
        "coverage": {
            "overall_readiness": "ready",
            "has_gaps": False,
        },
    }

    doc = service.evaluate_site_market_context(
        market_context=mock_market_context,
        tenant_id="tenant-tw-01",
        # This existing positive-NPV arithmetic control retains every original
        # price and mixed-machine number without calling that legacy mix G2.
        overrides=SimulationOverrides(format_code="ENGINEERING_MIXED"),
    )

    assert doc.site_id == "SITE-TP-XINYI-101"
    assert doc.source_market_context_id == "ctx-tw-tp-001"
    assert doc.assumptions.area_ping == 28.0
    assert doc.assumptions.monthly_base_rent == 28.0 * 2_400.0
    assert doc.assumptions.demand_multiplier > 1.0  # Population & density uplift
    assert doc.assumptions.competitor_discount > 0.0  # 2 competitors present

    assert doc.metrics.levered_npv > 0
    assert doc.decision.recommendation in (
        EconomicsDecision.GO,
        EconomicsDecision.CONDITIONAL_GO,
        EconomicsDecision.INVESTIGATE,
    )


def test_infeasible_extreme_rent_site_rejection() -> None:
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    # Extremely exorbitant rent (300,000 TWD / month for small 15 ping space)
    doc = service.evaluate_site(
        site_id="SITE-OVERPRICED-001",
        area_ping=15.0,
        monthly_rent=300_000.0,
        tenant_id="tenant-tw-01",
    )

    # Should have negative NPV, censored or very long payback, and REJECT recommendation
    assert doc.metrics.levered_npv < 0
    assert doc.decision.recommendation == EconomicsDecision.REJECT
    assert len(doc.decision.risk_flags) > 0


def test_repository_save_and_retrieve() -> None:
    repo = InMemorySiteEconomicsRepository()
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    doc1 = service.evaluate_site(
        site_id="SITE-REPO-001",
        area_ping=24.0,
        monthly_rent=50_000.0,
    )
    doc2 = service.evaluate_site(
        site_id="SITE-REPO-001",
        area_ping=24.0,
        monthly_rent=55_000.0,
    )

    repo.save(doc1)
    repo.save(doc2)

    by_id = repo.get_by_document_id(doc1.document_id)
    assert by_id is not None
    assert by_id.document_id == doc1.document_id

    latest = repo.get_latest_by_site_id("SITE-REPO-001")
    assert latest is not None
    assert latest.document_id == doc2.document_id

    history = repo.list_by_site_id("SITE-REPO-001")
    assert len(history) == 2


def test_zero_debt_and_full_debt_financing_edge_cases() -> None:
    simulator = SiteEconomicsSimulator()
    g2 = legacy_arithmetic_format()
    operating = SiteOperatingParameters(monthly_base_rent=50_000.0, area_ping=25.0)

    # 1. 100% Equity / 0% Debt
    sim_input_equity = SimulationInput(
        format_spec=g2,
        operating_params=operating,
        custom_debt_ratio=0.0,
    )
    res_equity = simulator.simulate(sim_input_equity)
    assert res_equity.metrics.debt_financed == 0.0
    assert res_equity.metrics.equity_investment == res_equity.metrics.total_initial_cash_outlay
    assert res_equity.metrics.min_dscr is None  # No debt service
    for item in res_equity.monthly_schedule:
        assert item.total_debt_service == 0.0
        assert item.loan_principal_payment == 0.0
        assert item.interest_expense == 0.0

    # 2. 0% Interest Rate Financing
    sim_input_zero_interest = SimulationInput(
        format_spec=g2,
        operating_params=operating,
        custom_debt_ratio=0.60,
        custom_interest_rate=0.0,
    )
    res_zero_int = simulator.simulate(sim_input_zero_interest)
    assert res_zero_int.metrics.debt_financed > 0
    for item in res_zero_int.monthly_schedule:
        assert item.interest_expense == 0.0
        if item.month <= 60:
            assert item.loan_principal_payment > 0


def test_tax_loss_carryforward_behavior() -> None:
    simulator = SiteEconomicsSimulator()
    g2 = legacy_arithmetic_format()
    # High rent causing negative EBIT in year 1
    operating = SiteOperatingParameters(
        monthly_base_rent=150_000.0,
        area_ping=25.0,
        rent_free_months=0,
    )
    sim_input = SimulationInput(
        format_spec=g2,
        operating_params=operating,
    )
    res = simulator.simulate(sim_input)
    # Check that in months with negative taxable income, tax expense is strictly 0.0
    for item in res.monthly_schedule:
        if item.taxable_income <= 0.0:
            assert item.tax_expense == 0.0


def test_custom_format_composition_and_overrides() -> None:
    # Build a dedicated Pet Wash & Vending micro-store
    pet_washer = MachineModelSpec(
        model_id="PET-PRO-V1",
        machine_class=MachineClass.OTHER,
        model_name="Pet Pro Disinfection Washer",
        capacity_kg=12.0,
        unit_capex=200_000.0,
        baseline_turns_per_day=5.0,
        max_turns_per_day=16.0,
        base_cycle_price=160.0,
        useful_life_months=84,
        residual_value_ratio=0.10,
    )
    custom_mix = MachineMixSpec(
        spec_id="MIX-PET-ONLY-V1",
        version="1.0.0",
        items=(
            MachineMixItem(machine_model=pet_washer, quantity=4),
            MachineMixItem(machine_model=VENDING_DETERGENT_SMART_V1, quantity=2),
        ),
        installation_and_delivery_fee=30_000.0,
    )
    custom_format = TargetFormatSpec(
        format_code="ODAY_PET_EXPRESS",
        format_name="ODay Pet Care Express",
        format_version="1.0.0",
        description="Dedicated self-service pet grooming and washing hub",
        target_area_ping_min=10.0,
        target_area_ping_max=20.0,
        recommended_area_ping=15.0,
        machine_mix=custom_mix,
        fitout_spec=FitoutSpec(
            spec_id="FIT-PET-V1",
            version="1.0.0",
            base_fitout_cost=250_000.0,
            cost_per_ping=18_000.0,
        ),
        utilities_spec=UtilitiesCostSpec(spec_id="UTIL-PET-V1", version="1.0.0"),
        maintenance_spec=MaintenanceSpec(spec_id="MAINT-PET-V1", version="1.0.0"),
        financing_spec=FinancingSpec(spec_id="FIN-PET-V1", version="1.0.0", debt_ratio=0.50),
        tax_spec=TaxSpec(spec_id="TAX-PET-V1", version="1.0.0"),
        residual_spec=ResidualValueSpec(spec_id="RES-PET-V1", version="1.0.0"),
        ramp_spec=RampCurveSpec(spec_id="RAMP-PET-V1", version="1.0.0"),
        seasonality_spec=SeasonalitySpec(spec_id="SEAS-PET-V1", version="1.0.0"),
    )

    reg = TargetFormatRegistry()
    reg.register(custom_format)
    retrieved = reg.get("ODAY_PET_EXPRESS")
    assert retrieved.format_name == "ODay Pet Care Express"
    assert retrieved.machine_mix.total_machines == 6

    service = SiteEconomicsService(registry=reg)
    doc = service.evaluate_site(
        site_id="SITE-PET-001",
        area_ping=15.0,
        monthly_rent=35_000.0,
        format_code="ODAY_PET_EXPRESS",
    )
    assert doc.format_code == "ODAY_PET_EXPRESS"
    assert (
        doc.metrics.total_initial_capex
        == custom_format.machine_mix.total_equipment_capex
        + custom_format.fitout_spec.compute_total_fitout(15.0)
    )
    assert doc.metrics.levered_npv > 0
    assert doc.decision.recommendation in (
        EconomicsDecision.GO,
        EconomicsDecision.CONDITIONAL_GO,
        EconomicsDecision.INVESTIGATE,
    )


def test_document_validation_errors() -> None:
    with pytest.raises(ValueError, match="Invalid contract_id"):
        validate_site_economics_document({"contract_id": "invalid.contract.id"})

    with pytest.raises(ValueError, match="site_id is required"):
        validate_site_economics_document({"contract_id": CONTRACT_ID, "site_id": ""})

    with pytest.raises(ValueError, match="format_code is required"):
        validate_site_economics_document(
            {"contract_id": CONTRACT_ID, "site_id": "S1", "format_code": ""}
        )


def test_payback_search_bounded_by_horizon() -> None:
    # 84-month series: initial outlay -720, then +10 each month
    # Breakeven happens exactly at month 72
    cfs = [-720.0] + [10.0] * 84

    # Horizon 60 months: must be RIGHT_CENSORED and not search beyond month 60
    res_60 = compute_payback(cfs, horizon_months=60)
    assert res_60.is_censored is True
    assert res_60.censoring_type == CensoringType.RIGHT_CENSORED
    assert res_60.payback_months is None
    assert res_60.horizon_months == 60

    # Horizon 84 months: must find payback at month 72.0
    res_84 = compute_payback(cfs, horizon_months=84)
    assert res_84.is_censored is False
    assert res_84.censoring_type == CensoringType.NOT_CENSORED
    assert res_84.payback_months == 72.0
    assert res_84.horizon_months == 84


def test_zero_month_payback_decision_evaluation() -> None:
    simulator = SiteEconomicsSimulator()
    g2 = legacy_arithmetic_format()
    operating = SiteOperatingParameters(monthly_base_rent=50_000.0, area_ping=25.0)

    # 100% debt financing -> zero equity outlay -> 0.0 payback months
    sim_input = SimulationInput(
        format_spec=g2,
        operating_params=operating,
        custom_debt_ratio=1.0,
    )
    res = simulator.simulate(sim_input)
    assert res.metrics.simple_payback.payback_months == 0.0
    assert res.metrics.simple_payback.is_censored is False
    # Decision must NOT be REJECT due to falsy 0.0 evaluating to 999.0
    assert res.decision.recommendation in (
        EconomicsDecision.GO,
        EconomicsDecision.CONDITIONAL_GO,
    )


def test_untruncated_loan_term_when_horizon_less_than_loan_term() -> None:
    simulator = SiteEconomicsSimulator()
    g2 = legacy_arithmetic_format()
    operating = SiteOperatingParameters(monthly_base_rent=50_000.0, area_ping=25.0)

    # Horizon = 36 months, Loan term = 60 months
    sim_input = SimulationInput(
        format_spec=g2,
        operating_params=operating,
        horizon_months=36,
    )
    res = simulator.simulate(sim_input)
    # Monthly debt service should use the full 60 months loan term
    expected_loan_term = g2.financing_spec.loan_term_months
    assert expected_loan_term == 60
    debt_financed = res.metrics.debt_financed
    expected_pmt = compute_pmt(
        debt_financed, g2.financing_spec.annual_interest_rate, expected_loan_term
    )
    m1 = res.monthly_schedule[0]
    assert m1.total_debt_service == pytest.approx(expected_pmt, rel=1e-3)

    # F1 check: Terminal month 36 pays off outstanding debt
    m36 = res.monthly_schedule[35]
    assert m36.is_terminal is True
    assert m36.terminal_loan_payoff > 0.0
    # Remaining loan principal is 0 at the end of terminal exit
    assert m36.remaining_loan_principal == 0.0
    # Levered NPV must reflect the unamortized debt payoff at exit (~ -746,454 vs +181,410 uncorrected)
    assert res.metrics.levered_npv < 0
    assert res.metrics.levered_npv == pytest.approx(-746_454.34, abs=500.0)
    assert res.decision.recommendation == EconomicsDecision.REJECT


def test_per_model_depreciation_and_salvage_rates() -> None:
    # F2: Test heterogeneous machine models with different useful_life_months and residual_value_ratio
    model_a = MachineModelSpec(
        model_id="M-A",
        machine_class=MachineClass.WASHER,
        model_name="Model A",
        capacity_kg=15.0,
        unit_capex=200_000.0,
        baseline_turns_per_day=5.0,
        max_turns_per_day=15.0,
        base_cycle_price=100.0,
        useful_life_months=36,
        residual_value_ratio=0.20,  # 40,000 salvage, 160,000 depreciable over 36mo = 4,444.44/mo
    )
    model_b = MachineModelSpec(
        model_id="M-B",
        machine_class=MachineClass.DRYER,
        model_name="Model B",
        capacity_kg=15.0,
        unit_capex=300_000.0,
        baseline_turns_per_day=5.0,
        max_turns_per_day=15.0,
        base_cycle_price=100.0,
        useful_life_months=60,
        residual_value_ratio=0.10,  # 30,000 salvage, 270,000 depreciable over 60mo = 4,500.00/mo
    )
    mix = MachineMixSpec(
        spec_id="MIX-HETERO-V1",
        version="1.0.0",
        items=(
            MachineMixItem(machine_model=model_a, quantity=1),
            MachineMixItem(machine_model=model_b, quantity=1),
        ),
        installation_and_delivery_fee=0.0,
    )
    format_spec = TargetFormatSpec(
        format_code="ODAY_HETERO",
        format_name="Heterogeneous Format",
        format_version="1.0.0",
        description="Format with distinct machine lives",
        target_area_ping_min=10.0,
        target_area_ping_max=30.0,
        recommended_area_ping=20.0,
        machine_mix=mix,
        fitout_spec=FitoutSpec(
            spec_id="F1", version="1.0", base_fitout_cost=0.0, cost_per_ping=0.0
        ),
        utilities_spec=UtilitiesCostSpec(spec_id="U1", version="1.0"),
        maintenance_spec=MaintenanceSpec(spec_id="M1", version="1.0"),
        financing_spec=FinancingSpec(spec_id="FIN1", version="1.0", debt_ratio=0.0),
        tax_spec=TaxSpec(spec_id="T1", version="1.0", corporate_tax_rate=0.20),
        residual_spec=ResidualValueSpec(spec_id="R1", version="1.0"),
        ramp_spec=RampCurveSpec(spec_id="RC1", version="1.0"),
        seasonality_spec=SeasonalitySpec(spec_id="S1", version="1.0"),
    )
    simulator = SiteEconomicsSimulator()
    sim_input = SimulationInput(
        format_spec=format_spec,
        operating_params=SiteOperatingParameters(monthly_base_rent=10_000.0, area_ping=20.0),
        horizon_months=60,
    )
    res = simulator.simulate(sim_input)

    # In month 1..36, depreciation includes Model A (4,444.44) + Model B (4,500.00) = 8,944.44
    m10 = res.monthly_schedule[9]
    assert m10.equipment_depreciation == pytest.approx(4444.44 + 4500.00, rel=1e-2)

    # In month 37..60, Model A is fully depreciated, only Model B depreciates (4,500.00)
    m45 = res.monthly_schedule[44]
    assert m45.equipment_depreciation == pytest.approx(4500.00, rel=1e-2)

    # Total salvage at terminal is sum of per-model residuals (40,000 + 30,000 = 70,000)
    m60 = res.monthly_schedule[59]
    assert m60.terminal_salvage_inflow == pytest.approx(70_000.0, rel=1e-3)


def test_monthly_capacity_capping_with_ramp_and_seasonality() -> None:
    # F3: Verify that under high demand multipliers and peak seasonality, monthly cycles never exceed capacity
    g2 = legacy_arithmetic_format()
    simulator = SiteEconomicsSimulator()
    sim_input = SimulationInput(
        format_spec=g2,
        operating_params=SiteOperatingParameters(monthly_base_rent=50_000.0, area_ping=25.0),
        demand_multiplier=3.5,  # Extreme demand
        horizon_months=24,
    )
    res = simulator.simulate(sim_input)

    days_in_month = 365.25 / 12.0
    max_total_cycles_month = sum(
        item.quantity * item.machine_model.max_turns_per_day * days_in_month
        for item in g2.machine_mix.items
    )

    for item in res.monthly_schedule:
        # No month should exceed physical equipment capacity
        assert item.total_cycles_count <= max_total_cycles_month + 1e-4


def test_unlevered_tax_loss_carryforward_behavior() -> None:
    # F4: Verify unlevered tax NOL carryforward
    g2 = legacy_arithmetic_format()
    simulator = SiteEconomicsSimulator()
    # High rent in early months causing negative EBIT, then recovering
    sim_input = SimulationInput(
        format_spec=g2,
        operating_params=SiteOperatingParameters(
            monthly_base_rent=120_000.0,
            area_ping=25.0,
            rent_free_months=0,
        ),
        horizon_months=36,
    )
    res = simulator.simulate(sim_input)

    # Find first month where EBIT turns positive after initial negative months
    found_negative = False
    for item in res.monthly_schedule:
        if item.ebit < 0:
            found_negative = True
            assert item.unlevered_operating_cash_flow == item.ebitda  # tax is 0

    assert found_negative


def test_rent_override_updates_lease_deposit_and_scenarios() -> None:
    # F6: Verify rent overrides correctly update lease deposit at initial outlay and terminal return
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    doc = service.evaluate_site(
        site_id="SITE-RENT-DEP-001",
        area_ping=25.0,
        monthly_rent=80_000.0,
    )
    # Default 2 months deposit for 80k rent = 160,000 TWD
    assert doc.monthly_schedule[-1].terminal_deposit_return == 160_000.0

    # Scenarios should have corresponding deposits
    opt_scen = doc.scenarios["optimistic"]
    assert opt_scen.monthly_rent == pytest.approx(80_000.0 * 0.90)


def test_falsy_zero_rent_support() -> None:
    # F7: Verify custom_rent_amount=0.0 is preserved and not replaced with fallback
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    doc = service.evaluate_site(
        site_id="SITE-ZERO-RENT",
        area_ping=25.0,
        monthly_rent=0.0,
    )
    assert doc.assumptions.monthly_base_rent == 0.0
    for item in doc.monthly_schedule:
        assert item.rent_expense == 0.0


def test_invalid_horizon_raises_value_error() -> None:
    # F8: Verify horizon <= 0 error handling
    simulator = SiteEconomicsSimulator()
    g2 = legacy_arithmetic_format()
    with pytest.raises(ValueError, match="horizon_months must be greater than 0"):
        simulator.simulate(
            SimulationInput(
                format_spec=g2,
                operating_params=SiteOperatingParameters(
                    monthly_base_rent=50_000.0, area_ping=25.0
                ),
                horizon_months=0,
            )
        )

    payback_out = compute_payback([-100.0, 50.0, 50.0], horizon_months=0)
    assert payback_out.is_censored is True
    assert payback_out.censoring_type == CensoringType.INSUFFICIENT_DATA


def test_sensitivity_scenarios_preserve_capex_and_cannibalization_overrides() -> None:
    service = SiteEconomicsService(registry=synthetic_g2_registry())
    overrides = SimulationOverrides(
        cannibalization_discount=0.15,
        custom_equipment_capex=1_500_000.0,
        custom_fitout_capex=800_000.0,
    )
    doc = service.evaluate_site(
        site_id="SITE-OVERRIDE-001",
        area_ping=25.0,
        monthly_rent=60_000.0,
        overrides=overrides,
    )

    assert doc.assumptions.cannibalization_discount == 0.15
    assert doc.assumptions.total_equipment_capex == 1_500_000.0
    assert doc.assumptions.total_fitout_capex == 800_000.0

    base_scen = doc.scenarios["base"]
    stress_scen = doc.scenarios["stress_test"]
    pess_scen = doc.scenarios["pessimistic"]
    opt_scen = doc.scenarios["optimistic"]

    # Stress test must have worse NPV than base case when capex/cannibalization are preserved
    assert stress_scen.levered_npv < base_scen.levered_npv
    assert pess_scen.levered_npv < base_scen.levered_npv
    assert opt_scen.levered_npv > base_scen.levered_npv


# Nonproductive fixtures below are arbitrary engineering assumptions, not any
# supplier quote, invoice basis, actual hardware measurement or financial grant.
def _synthetic_capital_asset_payload():
    return {"asset_id": "SYNTHETIC-PAYMENT-CONTROLLER", "asset_kind": "PAYMENT_SYSTEM", "version": "1.0.0-engineering",
            "quantity": 2, "unit_capex": 1234.0, "useful_life_months": 2,
            "residual_value_ratio": 0.25, "monthly_fixed_operating_cost": 37.0}


def _capital_asset_format(payload=None):
    raw = synthetic_g2_format().to_dict()
    raw["capital_assets"] = [_synthetic_capital_asset_payload() if payload is None else payload]
    return TargetFormatSpec.from_dict(raw)


def test_ancillary_asset_costs_change_cash_flow_without_creating_laundry_demand():
    base = synthetic_g2_format()
    enriched = _capital_asset_format()
    params = SiteOperatingParameters(monthly_base_rent=0, area_ping=25)
    def run(spec):
        return SiteEconomicsSimulator().simulate(SimulationInput(
            format_spec=replace(spec, tax_spec=replace(spec.tax_spec, corporate_tax_rate=0)),
            operating_params=params, horizon_months=3, custom_debt_ratio=0))
    original, actual = run(base), run(enriched)
    assert actual.metrics.total_initial_capex - original.metrics.total_initial_capex == 2468
    assert actual.unlevered_cash_flows[0] - original.unlevered_cash_flows[0] == -2468
    for a, b in zip(actual.monthly_schedule, original.monthly_schedule, strict=True):
        assert (a.total_cycles_count, a.gross_revenue, a.utilities_expense, a.maintenance_expense) == (
            b.total_cycles_count, b.gross_revenue, b.utilities_expense, b.maintenance_expense)
        assert a.store_operations_expense - b.store_operations_expense == 74
    assert [round(a.equipment_depreciation-b.equipment_depreciation, 2) for a, b in zip(
        actual.monthly_schedule, original.monthly_schedule, strict=True)] == [925.5, 925.5, 0]
    assert actual.monthly_schedule[-1].terminal_salvage_inflow - original.monthly_schedule[-1].terminal_salvage_inflow == 617
    assert [round(a-b,2) for a,b in zip(actual.unlevered_cash_flows,original.unlevered_cash_flows,strict=True)] == [-2468,-74,-74,543]


@pytest.mark.parametrize("field,value", [("quantity", True), ("quantity", 1.5),
    ("unit_capex", "1234"), ("unit_capex", float("inf")),
    ("useful_life_months", 2.5), ("residual_value_ratio", 1.1),
    ("monthly_fixed_operating_cost", -1), ("baseline_turns_per_day", 10),
    ("asset_kind", "WASHER"), ("asset_kind", "OTHER"), ("asset_kind", False)])
def test_ancillary_asset_payload_does_not_repair_or_hide_invalid_financial_inputs(field, value):
    raw = _synthetic_capital_asset_payload()
    raw[field] = value
    with pytest.raises(ValueError, match="CAPITAL_ASSET_INVALID"):
        _capital_asset_format(raw)


def test_ancillary_asset_must_survive_snapshot_roundtrip_and_version_guard():
    spec = _capital_asset_format()
    payload = spec.to_dict()
    assert payload["capital_assets"] == [_synthetic_capital_asset_payload()]
    assert TargetFormatSpec.from_dict(payload) == spec
    registry = TargetFormatRegistry()
    registry.register(spec)
    changed = _synthetic_capital_asset_payload()
    changed["unit_capex"] = 1235
    with pytest.raises(ValueError, match="TARGET_FORMAT_IMMUTABLE_VERSION"):
        registry.register(_capital_asset_format(changed))


def test_explicit_total_equipment_override_preserves_ancillary_cost_and_lifetime():
    spec = _capital_asset_format()
    params = SiteOperatingParameters(monthly_base_rent=0, area_ping=25)
    core = spec.machine_mix.total_equipment_capex
    overridden = SiteEconomicsSimulator().simulate(SimulationInput(format_spec=spec,
        operating_params=params, horizon_months=3, custom_equipment_capex=core+2468))
    ordinary = SiteEconomicsSimulator().simulate(SimulationInput(format_spec=spec,
        operating_params=params, horizon_months=3))
    assert overridden.monthly_schedule == ordinary.monthly_schedule
    assert overridden.metrics == ordinary.metrics
    with pytest.raises(ValueError, match="CAPITAL_ASSET_INVALID"):
        SiteEconomicsSimulator().simulate(SimulationInput(format_spec=spec,
            operating_params=params, custom_equipment_capex=2467))


def test_ancillary_assets_do_not_waive_the_six_combo_machine_requirement():
    spec = _capital_asset_format()
    large, medium = spec.machine_mix.items
    wrong = replace(spec, machine_mix=replace(spec.machine_mix, items=(replace(large, quantity=2), medium)))
    with pytest.raises(ValueError, match="ODAY_G2_CONFIGURATION_REQUIRED"):
        SiteEconomicsSimulator().simulate(SimulationInput(format_spec=wrong,
            operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25)))


def test_ancillary_only_generic_scenario_does_not_depreciate_or_salvage_assets_twice():
    spec = _capital_asset_format()
    spec = replace(spec, format_code="ENGINEERING_ASSETS_ONLY",
        machine_mix=replace(spec.machine_mix, items=(), installation_and_delivery_fee=0))
    result = SiteEconomicsSimulator().simulate(SimulationInput(format_spec=spec,
        operating_params=SiteOperatingParameters(monthly_base_rent=0, area_ping=25),
        horizon_months=3))
    assert all(row.total_cycles_count == 0 and row.gross_revenue == 0 for row in result.monthly_schedule)
    assert [row.equipment_depreciation for row in result.monthly_schedule] == [925.5,925.5,0]
    assert result.monthly_schedule[-1].terminal_salvage_inflow == 617


def test_ancillary_asset_cost_and_snapshot_reach_all_service_scenarios():
    from modules.site_economics.application.context_inputs import content_sha256

    spec = _capital_asset_format()
    registry = TargetFormatRegistry()
    registry.register(spec)
    service = SiteEconomicsService(registry=registry)
    doc = service.evaluate_site("synthetic-ancillary", 25, 0)
    assert doc.assumptions.total_equipment_capex == spec.machine_mix.total_equipment_capex + 2468
    assert doc.metadata["target_format_snapshot"] == spec.to_dict()
    assert doc.metadata["target_format_sha256"] == content_sha256(spec.to_dict())
    assert doc.metadata["production_admission"] == "not_established_by_simulation"
    assert doc.metadata["capital_assets_cost_policy"] == "explicit-nonlaundry-capital-v1"
    assert doc.metadata["ancillary_sales_admission"] == "not_established_by_capital_costs"
    assert doc.engine_version == "site-economics-simulator-v1.2.0"
    validate_site_economics_document(doc)
    restored = SiteEconomicsDocument.from_dict(doc.to_dict())
    assert restored.metadata == doc.metadata and restored.assumptions == doc.assumptions
    assert set(doc.scenarios) == {"base","optimistic","pessimistic","stress_test"}
    old_reader_shape = doc.to_dict()
    old_reader_shape.pop("engine_version")
    assert SiteEconomicsDocument.from_dict(old_reader_shape).engine_version == "site-economics-simulator-v1.1.0"


@pytest.mark.parametrize("kind", ["duplicate", "productive-id", "known-laundry-id", "non-collection", "aggregate-overflow"])
def test_capital_asset_identity_and_aggregate_conflicts_are_refused(kind):
    raw = synthetic_g2_format().to_dict()
    asset = _synthetic_capital_asset_payload()
    if kind == "duplicate":
        raw["capital_assets"] = [asset,dict(asset,version="2.0.0-engineering")]
    elif kind == "productive-id":
        raw["capital_assets"] = [dict(asset,asset_id=raw["machine_mix"]["items"][0]["machine_model"]["model_id"])]
    elif kind == "known-laundry-id":
        raw["capital_assets"] = [dict(asset,asset_id=WASHER_LARGE_20KG_V1.model_id)]
    elif kind == "non-collection":
        raw["capital_assets"] = None
    else:
        first = dict(asset,quantity=1,unit_capex=1e308)
        raw["capital_assets"] = [first,dict(first,asset_id="SYNTHETIC-SECOND-CONTROL")]
    with pytest.raises(ValueError, match="CAPITAL_ASSET_INVALID"):
        TargetFormatSpec.from_dict(raw)


def test_ancillary_fixed_costs_increase_all_mature_breakeven_metrics():
    base = synthetic_g2_format()
    payload = dict(_synthetic_capital_asset_payload(), quantity=1,
                   unit_capex=0, monthly_fixed_operating_cost=10_000)
    with_cost = _capital_asset_format(payload)
    params = SiteOperatingParameters(monthly_base_rent=0, area_ping=25)
    original = SiteEconomicsSimulator().simulate(SimulationInput(
        format_spec=base, operating_params=params, horizon_months=3))
    actual = SiteEconomicsSimulator().simulate(SimulationInput(
        format_spec=with_cost, operating_params=params, horizon_months=3))
    assert actual.metrics.total_initial_cash_outlay == original.metrics.total_initial_cash_outlay
    for a,b in zip(actual.monthly_schedule,original.monthly_schedule,strict=True):
        assert a.total_cycles_count == b.total_cycles_count and a.gross_revenue == b.gross_revenue
        assert a.store_operations_expense-b.store_operations_expense == 10_000
    assert actual.metrics.breakeven_monthly_revenue > original.metrics.breakeven_monthly_revenue
    assert actual.metrics.breakeven_turns_per_day > original.metrics.breakeven_turns_per_day
    assert actual.metrics.breakeven_capacity_utilization > original.metrics.breakeven_capacity_utilization
