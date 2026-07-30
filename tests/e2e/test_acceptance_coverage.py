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
        "tests/e2e/operator-network-scoring.spec.ts::SiteScore Lab renders GO/WAIT/REJECT scorecards with conditions and reasons",
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
        "tests/e2e/operator-network-listings.spec.ts::HZ-01 to L-2024 to CS-1001 completes through UI and API",
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
        "tests/e2e/operator-store-ops.spec.ts::ISS-1024 lifecycle writes through Store Ops API and reloads updated state",
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
        "tests/e2e/operator-store-ops.spec.ts::Package 10 issue detail exposes the four-light evidence without legacy filter chips",
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
        "tests/e2e/operator-store-ops.spec.ts::Package 10 issue detail exposes the four-light evidence without legacy filter chips",
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
        "tests/e2e/operator-growth.spec.ts::Effective Growth Action can be closed and emits console audit log",
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
        "tests/e2e/operator-growth.spec.ts::Five-step builder navigates all steps and emits a create audit on submit",
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
        "tests/e2e/operator-network-rebalance.spec.ts::AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution",
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
        "tests/e2e/operator-network-rebalance.spec.ts::AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution",
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
        "tests/e2e/operator-governance.spec.ts::Govern workspace exposes all five tabs and the DQ/Model/Connector/SLA/Users board",
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
        "tests/e2e/operator-governance.spec.ts::Govern approval return is blocked without a sufficient reason",
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
        "tests/e2e/operator-governance.spec.ts::Evidence Package export produces a record and an audit event",
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

DELETED_SPEC_REFERENCES = (
    "e2e-exp.spec.ts",
    "e2e-ops.spec.ts",
    "e2e-intervention-price-ad.spec.ts",
    "e2e-avm-netplan.spec.ts",
    "e2e-learning-audit.spec.ts",
)

EXPECTED_CANONICAL_SPEC_COUNT = 16
EXPECTED_TEST_INVENTORY_COUNT = 107


def validate_acceptance_scenarios_and_inventory(root_path: Path) -> list[str]:
    """Executable validator for scenario coverage, canonical specs, title resolution, and execution receipts."""
    import json
    import re
    import subprocess
    errors: list[str] = []

    # 1. Validate scenario automation refs resolve to existing files, existing titles, and do not use deleted specs
    for scenario in E2E_SCENARIOS:
        ref = scenario.automation_ref
        for deleted_spec in DELETED_SPEC_REFERENCES:
            if deleted_spec in ref:
                errors.append(
                    f"{scenario.scenario_id} cites deleted spec reference: {ref}"
                )

        if ref.startswith("manual-uat:"):
            route = scenario.route_or_surface
            if "#" in route:
                doc_path_str = route.split("#")[0]
            else:
                doc_path_str = route
            if doc_path_str.startswith("docs/") and not (root_path / doc_path_str).exists():
                errors.append(f"{scenario.scenario_id} manual UAT doc missing: {doc_path_str}")
        else:
            refs = [r.strip() for r in ref.split("+")]
            for single_ref in refs:
                parts = single_ref.split("::")
                file_part = parts[0].strip()
                title_part = parts[1].strip() if len(parts) > 1 else None

                target_file = root_path / file_part
                if file_part.startswith("tests/") and not target_file.exists():
                    errors.append(
                        f"{scenario.scenario_id} automation ref file missing: {file_part}"
                    )
                elif title_part and target_file.exists():
                    content = target_file.read_text(encoding="utf-8")
                    if title_part not in content:
                        errors.append(
                            f"{scenario.scenario_id} automation ref title '{title_part}' not found in {file_part}"
                        )

    # 2. Validate canonical spec files inventory (16 files) and test count (107 tests) via Playwright --list
    e2e_dir = root_path / "tests/e2e"
    spec_files = sorted([p for p in e2e_dir.glob("*.spec.ts") if p.is_file()])
    if len(spec_files) != EXPECTED_CANONICAL_SPEC_COUNT:
        errors.append(
            f"Expected {EXPECTED_CANONICAL_SPEC_COUNT} Playwright spec files in tests/e2e, "
            f"found {len(spec_files)}"
        )

    try:
        proc = subprocess.run(
            ["npx", "playwright", "test", "--list", "--project=chromium"],
            cwd=root_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            errors.append(
                f"Playwright --list failed with exit code {proc.returncode}: {proc.stderr.strip()}"
            )
        else:
            match = re.search(r"Total:\s*(\d+)\s*tests\s*in\s*(\d+)\s*files", proc.stdout)
            if match:
                pw_tests, pw_files = int(match.group(1)), int(match.group(2))
                if pw_files != EXPECTED_CANONICAL_SPEC_COUNT:
                    errors.append(
                        f"Playwright --list reported {pw_files} spec files, expected {EXPECTED_CANONICAL_SPEC_COUNT}"
                    )
                if pw_tests != EXPECTED_TEST_INVENTORY_COUNT:
                    errors.append(
                        f"Playwright --list reported {pw_tests} total tests, expected {EXPECTED_TEST_INVENTORY_COUNT}"
                    )
            else:
                errors.append("Playwright --list output could not be parsed")
    except Exception as exc:
        errors.append(f"Failed to run Playwright --list: {exc}")

    # 3. Validate durable execution receipt artifact (docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json)
    receipt_path = root_path / "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
    if not receipt_path.exists():
        errors.append("Execution receipt artifact missing: docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("status") != "passed":
                errors.append(f"Execution receipt status is not passed: {receipt.get('status')}")

            git_sha = receipt.get("git_sha")
            if not git_sha or not isinstance(git_sha, str):
                errors.append("Execution receipt missing valid git_sha")

            summary = receipt.get("summary", {})
            if summary.get("total_specs") != EXPECTED_CANONICAL_SPEC_COUNT:
                errors.append(
                    f"Execution receipt total_specs ({summary.get('total_specs')}) != {EXPECTED_CANONICAL_SPEC_COUNT}"
                )
            if summary.get("total_tests") != EXPECTED_TEST_INVENTORY_COUNT:
                errors.append(
                    f"Execution receipt total_tests ({summary.get('total_tests')}) != {EXPECTED_TEST_INVENTORY_COUNT}"
                )
            if summary.get("passed") != EXPECTED_TEST_INVENTORY_COUNT or summary.get("failed", 0) > 0:
                errors.append("Execution receipt contains failed or incomplete test executions")

            scenario_results = {r.get("scenario_id"): r.get("status") for r in receipt.get("scenario_results", [])}
            for scenario in E2E_SCENARIOS:
                if scenario.priority == "P0":
                    if scenario.scenario_id not in scenario_results:
                        errors.append(f"Execution receipt missing result for P0 scenario: {scenario.scenario_id}")
                    elif scenario_results[scenario.scenario_id] != "passed":
                        errors.append(
                            f"Execution receipt scenario {scenario.scenario_id} status is {scenario_results[scenario.scenario_id]}"
                        )
        except Exception as exc:
            errors.append(f"Execution receipt unparsable or invalid: {exc}")

    return errors


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


def test_no_deleted_specs_referenced_and_inventory_consistent() -> None:
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    errors = validate_acceptance_scenarios_and_inventory(root)
    assert errors == [], f"Acceptance scenario validation errors: {errors}"
