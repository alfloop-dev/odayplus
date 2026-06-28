"""Formal E2E acceptance coverage registry for ODP-R7-003.

The Playwright specs in this directory exercise the UI surfaces. This registry
keeps the QA-03 acceptance IDs explicit so release reviewers can see which
business closure, data fixture, role, and audit evidence each scenario owns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class E2EScenario:
    scenario_id: str
    priority: str
    name: str
    owner_role: str
    deterministic_dataset: str
    automation_ref: str
    route_or_surface: str
    audit_evidence: tuple[str, ...]
    closes_loop: bool


E2E_SCENARIOS: tuple[E2EScenario, ...] = (
    E2EScenario(
        "E2E-EXP-001",
        "P0",
        "HeatZone to SiteScore opening decision",
        "expansion_user + site_reviewer",
        "golden_sitescore_dataset:v1",
        "tests/e2e/e2e-exp.spec.ts::SiteScore list and detail",
        "/w/expansion/sitescore/ssr-7001",
        ("decision_id", "model_version", "feature_snapshot_time", "correlation_id"),
        True,
    ),
    E2EScenario(
        "E2E-EXP-002",
        "P0",
        "Listing import, geocode, dedup, and candidate creation",
        "expansion_user",
        "golden_listing_dataset:v1",
        "tests/e2e/e2e-exp.spec.ts::Listing and Candidate screens",
        "/w/expansion/listings?selected=lst-9003&drawer=listing",
        ("field lineage", "hard_rule", "correlation_id"),
        True,
    ),
    E2EScenario(
        "E2E-EXP-003",
        "P1",
        "SiteScore return for supplement and rescore",
        "site_reviewer",
        "golden_sitescore_dataset:v1",
        "manual-uat: UAT-SITE-003 plus versioned report export",
        "docs/uat/UAT_ACCEPTANCE_PLAN.md#sitescore-review",
        ("report_version", "return_reason", "decision_log"),
        True,
    ),
    E2EScenario(
        "E2E-OPS-001",
        "P0",
        "Post-opening SiteScore realization",
        "ops_manager",
        "golden_forecastops_dataset:v1",
        "tests/e2e/e2e-ops.spec.ts::Store detail",
        "/w/operations/forecast/store-001",
        ("prediction_run_id", "outcome_status", "label_registry"),
        True,
    ),
    E2EScenario(
        "E2E-OPS-002",
        "P0",
        "ForecastOps four-light alert to root cause",
        "ops_manager",
        "golden_forecastops_dataset:v1",
        "tests/e2e/e2e-ops.spec.ts::Forecast overview",
        "/w/operations/forecast?selected=store-002",
        ("forecast_run_id", "four-light-policy-v1", "correlation_id"),
        True,
    ),
    E2EScenario(
        "E2E-INT-001",
        "P0",
        "Red alert to intervention and observation maturity",
        "field_supervisor",
        "golden_intervention_dataset:v1",
        "tests/e2e/e2e-intervention-price-ad.spec.ts::E2E-INT-001",
        "/interventions?selected=int-3002&drawer=case",
        ("decision_id", "conflict_check", "observation_window"),
        True,
    ),
    E2EScenario(
        "E2E-PRICE-001",
        "P0",
        "PriceOps plan, approval, execution, and rollback",
        "pricing_user",
        "golden_priceops_dataset:v1",
        "tests/e2e/e2e-intervention-price-ad.spec.ts::E2E-PRICE-001",
        "/pricing?selected=price-5102&drawer=plan",
        ("hard_constraint", "rollback_plan", "decision_id"),
        True,
    ),
    E2EScenario(
        "E2E-AD-001",
        "P0",
        "AdLift campaign, controls, and incrementality",
        "marketing_user",
        "golden_adlift_dataset:v1",
        "tests/e2e/e2e-intervention-price-ad.spec.ts::E2E-AD-001",
        "/adlift?selected=adlift-8801&drawer=report",
        ("control_match", "pre_trend", "contamination"),
        True,
    ),
    E2EScenario(
        "E2E-AVM-001",
        "P0",
        "Long-term red store to AVM valuation and Data Room",
        "finance_user + legal_user",
        "golden_avm_dataset:v1",
        "tests/e2e/e2e-avm-netplan.spec.ts::DealRoomAVM case detail",
        "/w/dealroom/cases/vc-5101",
        ("decision_id", "finance_approval", "avm.dataroom_exported.v1"),
        True,
    ),
    E2EScenario(
        "E2E-NET-001",
        "P0",
        "NetPlan scenario, solver alternatives, and approval",
        "executive_user",
        "golden_netplan_dataset:v1",
        "tests/e2e/e2e-avm-netplan.spec.ts::NetPlan feasible detail",
        "/w/network/scenarios/np-6201",
        ("solver_status", "binding_constraints", "approval_id"),
        True,
    ),
    E2EScenario(
        "E2E-LEARN-001",
        "P0",
        "Model training, validation, shadow, canary, production",
        "mlops_user",
        "golden_learninghub_dataset:v1",
        "tests/e2e/e2e-learning-audit.spec.ts::Learning Hub model detail",
        "/w/ai/models/sitescore-propensity/2.4.0",
        ("model_card", "release_approval", "rollback_target"),
        True,
    ),
    E2EScenario(
        "E2E-LEARN-002",
        "P0",
        "Model release rollback",
        "mlops_user",
        "golden_learninghub_dataset:v1",
        "tests/e2e/e2e-learning-audit.spec.ts::Learning Hub model detail",
        "/w/ai/models/sitescore-propensity/2.4.0",
        ("rollback_reason", "previous_champion", "audit_event_id"),
        True,
    ),
    E2EScenario(
        "E2E-DATA-001",
        "P0",
        "Data quality failure blocks model scoring",
        "data_scientist",
        "data_quality_fixtures:v1",
        "tests/integration/test_learninghub_release.py + tests/data/test_pit_snapshot.py",
        "Data Quality Center / Learning Hub release gates",
        ("data_quality_status", "blocked_model_list", "failure_history"),
        True,
    ),
    E2EScenario(
        "E2E-AUDIT-001",
        "P0",
        "Decision audit evidence export",
        "audit_user",
        "audit_snapshot:v1",
        "tests/e2e/e2e-learning-audit.spec.ts::Audit decision detail",
        "/w/audit/decisions/decision-netplan-404",
        ("decision_id", "approval_chain", "bundle_checksum"),
        True,
    ),
    E2EScenario(
        "E2E-SEC-001",
        "P0",
        "Role permissions and data isolation",
        "security_owner",
        "uat_accounts:v1",
        "tests/security/test_rbac_abac.py",
        "AuthorizationEngine",
        ("403_audit", "scope.store", "rbac"),
        True,
    ),
    E2EScenario(
        "E2E-FRAN-001",
        "P1",
        "Franchisee self-store status and intervention feedback",
        "franchisee_user",
        "uat_accounts:v1",
        "manual-uat: UAT-FRAN-001..005",
        "docs/uat/UAT_ACCEPTANCE_PLAN.md#franchisee",
        ("store_scope", "masked_model_details", "supervisor_notification"),
        True,
    ),
)


def test_all_qa03_scenarios_are_registered_once() -> None:
    expected = {
        "E2E-EXP-001",
        "E2E-EXP-002",
        "E2E-EXP-003",
        "E2E-OPS-001",
        "E2E-OPS-002",
        "E2E-INT-001",
        "E2E-PRICE-001",
        "E2E-AD-001",
        "E2E-AVM-001",
        "E2E-NET-001",
        "E2E-LEARN-001",
        "E2E-LEARN-002",
        "E2E-DATA-001",
        "E2E-AUDIT-001",
        "E2E-SEC-001",
        "E2E-FRAN-001",
    }
    actual = {scenario.scenario_id for scenario in E2E_SCENARIOS}
    assert actual == expected
    assert len(actual) == len(E2E_SCENARIOS)


def test_p0_scenarios_have_automation_data_and_audit_evidence() -> None:
    for scenario in E2E_SCENARIOS:
        if scenario.priority != "P0":
            continue
        assert not scenario.automation_ref.startswith("manual-uat:"), scenario.scenario_id
        assert scenario.deterministic_dataset
        assert scenario.owner_role
        assert scenario.closes_loop
        assert len(scenario.audit_evidence) >= 3


def test_acceptance_registry_links_release_review_surfaces() -> None:
    surfaces = {scenario.route_or_surface for scenario in E2E_SCENARIOS}
    assert "/w/audit/decisions/decision-netplan-404" in surfaces
    assert "AuthorizationEngine" in surfaces
    assert any("Data Quality Center" in surface for surface in surfaces)
