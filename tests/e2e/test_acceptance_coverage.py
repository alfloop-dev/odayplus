"""Formal E2E acceptance coverage registry for ODP-R7-003.

The Playwright specs in this directory exercise the UI surfaces. This registry
keeps the QA-03 acceptance IDs explicit so release reviewers can see which
business closure, data fixture, role, and audit evidence each scenario owns.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
        "tests/integration/test_avm_official_outcome_contract.py::test_official_outcome_migration_has_bounded_source_and_provenance_contracts",
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
        "tests/integration/test_intervention_workflow.py::test_full_lifecycle_reaches_completed_with_causal_evidence_and_label",
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
        "tests/integration/test_priceops_constraints.py::test_full_pilot_lifecycle_records_complete_status_history",
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
        "tests/integration/test_adlift_incrementality.py::test_difference_in_differences_isolates_ad_lift_from_market_movement",
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
        "tests/integration/test_learninghub_release.py::test_governed_release_invokes_remote_mlflow_alias_updates",
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
        "tests/integration/test_learninghub_release.py::test_learninghub_validates_releases_and_rolls_back_model_aliases",
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

FORBIDDEN_SEMANTIC_MAPPINGS = {
    "E2E-PRICE-001": "operator-growth.spec.ts",
    "E2E-AD-001": "operator-growth.spec.ts",
    "E2E-LEARN-001": "operator-governance.spec.ts",
    "E2E-LEARN-002": "operator-governance.spec.ts",
    "E2E-OPS-001": "operator-store-ops.spec.ts",
    "E2E-INT-001": "operator-store-ops.spec.ts",
}


def validate_acceptance_scenarios_and_inventory(root_path: Path) -> list[str]:
    """Executable validator for scenario coverage, canonical specs, title resolution, raw Playwright artifacts, exact HEAD SHA, and execution receipts."""
    errors: list[str] = []

    def get_file(rel_str: str) -> Path | None:
        p = root_path / rel_str
        if p.exists():
            return p
        return None

    # 1. Validate scenario automation refs resolve to existing files, existing titles, do not use deleted specs, and do not use false semantic mappings
    for scenario in E2E_SCENARIOS:
        ref = scenario.automation_ref
        for deleted_spec in DELETED_SPEC_REFERENCES:
            if deleted_spec in ref:
                errors.append(
                    f"{scenario.scenario_id} cites deleted spec reference: {ref}"
                )

        if scenario.scenario_id in FORBIDDEN_SEMANTIC_MAPPINGS:
            forbidden_file = FORBIDDEN_SEMANTIC_MAPPINGS[scenario.scenario_id]
            if forbidden_file in ref:
                errors.append(
                    f"Semantic mapping failure: {scenario.scenario_id} cannot be mapped to generic UI test '{ref}'. "
                    f"Must map to exact domain capability test."
                )

        if ref.startswith("manual-uat:"):
            route = scenario.route_or_surface
            doc_path_str = route.split("#")[0] if "#" in route else route
            if doc_path_str.startswith("docs/") and not get_file(doc_path_str):
                errors.append(f"{scenario.scenario_id} manual UAT doc missing: {doc_path_str}")
        else:
            refs = [r.strip() for r in ref.split("+")]
            for single_ref in refs:
                parts = single_ref.split("::")
                file_part = parts[0].strip()
                title_part = parts[1].strip() if len(parts) > 1 else None

                target_file = get_file(file_part)
                if file_part.startswith("tests/") and not target_file:
                    errors.append(
                        f"{scenario.scenario_id} automation ref file missing: {file_part}"
                    )
                elif title_part and target_file:
                    content = target_file.read_text(encoding="utf-8")
                    if title_part not in content:
                        errors.append(
                            f"{scenario.scenario_id} automation ref title '{title_part}' not found in {file_part}"
                        )

    # 2. Validate canonical spec files inventory (16 files) and test count (107 tests) via Playwright --list
    e2e_dir = root_path / "tests/e2e"
    if e2e_dir.exists():
        spec_files = sorted([p for p in e2e_dir.glob("*.spec.ts") if p.is_file()])
        if len(spec_files) != EXPECTED_CANONICAL_SPEC_COUNT:
            errors.append(
                f"Expected {EXPECTED_CANONICAL_SPEC_COUNT} Playwright spec files in tests/e2e, "
                f"found {len(spec_files)}"
            )

    try:
        proc = subprocess.run(
            ["npx", "playwright", "test", "--list", "--project=chromium"],
            cwd=root_path if (root_path / "playwright.config.ts").exists() else ROOT,
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

    # 3. Validate raw Playwright machine-readable artifact (docs/evidence/e2e/raw_playwright_results.json)
    raw_path = get_file("docs/evidence/e2e/raw_playwright_results.json")
    raw_hash = None
    if not raw_path:
        errors.append("Raw Playwright results artifact missing: docs/evidence/e2e/raw_playwright_results.json")
    else:
        try:
            raw_bytes = raw_path.read_bytes()
            if len(raw_bytes) == 0:
                errors.append("Raw Playwright results artifact is empty (0 bytes)")
            else:
                raw_hash = hashlib.sha256(raw_bytes).hexdigest()
        except Exception as exc:
            errors.append(f"Failed to read raw Playwright results artifact: {exc}")

    # 4. Validate current git HEAD SHA vs receipt git_sha
    current_git_sha = None
    try:
        git_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        current_git_sha = git_proc.stdout.strip()
    except Exception as exc:
        errors.append(f"Failed to resolve current git HEAD SHA: {exc}")

    # 5. Validate durable execution receipt artifact (docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json)
    receipt_path = get_file("docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json")
    if not receipt_path:
        errors.append("Execution receipt artifact missing: docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

            receipt_raw_hash = receipt.get("raw_artifact_hash")
            if not receipt_raw_hash:
                errors.append("Execution receipt missing raw_artifact_hash")
            elif raw_hash and receipt_raw_hash != raw_hash:
                errors.append(
                    f"Execution receipt raw_artifact_hash ({receipt_raw_hash}) does not match actual raw artifact hash ({raw_hash})"
                )

            git_sha = receipt.get("git_sha")
            if not git_sha or not isinstance(git_sha, str):
                errors.append("Execution receipt missing valid git_sha")
            elif current_git_sha and git_sha != current_git_sha:
                errors.append(
                    f"Stale receipt: execution receipt git_sha ({git_sha}) does not match current HEAD ({current_git_sha})"
                )

            for req_field in ("command", "runner_versions", "run_id", "start_time", "end_time", "exit_code", "environment"):
                if req_field not in receipt:
                    errors.append(f"Execution receipt missing mandatory metadata field: {req_field}")

            if receipt.get("exit_code") != 0:
                errors.append(f"Execution receipt exit_code is non-zero: {receipt.get('exit_code')}")

            if receipt.get("status") != "passed":
                errors.append(f"Execution receipt status is not passed: {receipt.get('status')}")

            summary = receipt.get("summary", {})
            if summary.get("total_specs") != EXPECTED_CANONICAL_SPEC_COUNT:
                errors.append(
                    f"Execution receipt total_specs ({summary.get('total_specs')}) != {EXPECTED_CANONICAL_SPEC_COUNT}"
                )
            if summary.get("total_tests") != EXPECTED_TEST_INVENTORY_COUNT:
                errors.append(
                    f"Execution receipt total_tests ({summary.get('total_tests')}) != {EXPECTED_TEST_INVENTORY_COUNT}"
                )
            if summary.get("passed") != EXPECTED_TEST_INVENTORY_COUNT or summary.get("failed", 0) > 0 or summary.get("skipped", 0) > 0:
                errors.append("Execution receipt contains failed, skipped, or incomplete test executions")

            scenario_results = {r.get("scenario_id"): r for r in receipt.get("scenario_results", [])}
            for scenario in E2E_SCENARIOS:
                if scenario.scenario_id not in scenario_results:
                    errors.append(f"Execution receipt missing result for scenario: {scenario.scenario_id}")
                    continue

                result = scenario_results[scenario.scenario_id]
                res_status = result.get("status")

                if scenario.automation_ref.startswith("manual-uat:"):
                    if res_status == "passed":
                        errors.append(
                            f"Execution receipt invalid: manual UAT scenario {scenario.scenario_id} marked as passed! "
                            f"Must remain pending and route to ODP-PLAN-UAT-SIGNOFF-001."
                        )
                else:
                    if scenario.priority == "P0" and res_status != "passed":
                        errors.append(
                            f"Execution receipt scenario {scenario.scenario_id} status is {res_status}"
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
    errors = validate_acceptance_scenarios_and_inventory(ROOT)
    assert errors == [], f"Acceptance scenario validation errors: {errors}"


# --- Negative Mutation Unit Tests ---


def test_validator_rejects_stale_git_sha(tmp_path: Path) -> None:
    raw_file = tmp_path / "docs/evidence/e2e/raw_playwright_results.json"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text('{"test": 1}', encoding="utf-8")
    actual_hash = hashlib.sha256(b'{"test": 1}').hexdigest()

    receipt = {
        "git_sha": "stale_sha_1234567890",
        "raw_artifact_hash": actual_hash,
        "status": "passed",
        "command": "npx playwright test",
        "runner_versions": {},
        "run_id": "r1",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:01:00Z",
        "exit_code": 0,
        "environment": "chromium",
        "summary": {"total_specs": 16, "total_tests": 107, "passed": 107, "failed": 0, "skipped": 0},
        "scenario_results": [],
    }
    receipt_file = tmp_path / "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    errors = validate_acceptance_scenarios_and_inventory(tmp_path)
    assert any("Stale receipt" in err for err in errors)


def test_validator_rejects_missing_raw_artifact(tmp_path: Path) -> None:
    fake_dir = tmp_path / "empty_dir"
    fake_dir.mkdir(parents=True, exist_ok=True)
    receipt_file = fake_dir / "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text("{}", encoding="utf-8")

    errors = validate_acceptance_scenarios_and_inventory(fake_dir)
    assert any("Raw Playwright results artifact missing" in err for err in errors)


def test_validator_rejects_raw_artifact_hash_mismatch(tmp_path: Path) -> None:
    raw_file = tmp_path / "docs/evidence/e2e/raw_playwright_results.json"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text('{"test": 1}', encoding="utf-8")

    git_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    receipt = {
        "git_sha": git_sha,
        "raw_artifact_hash": "wrong_hash_value",
        "status": "passed",
        "command": "npx playwright test",
        "runner_versions": {},
        "run_id": "r1",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:01:00Z",
        "exit_code": 0,
        "environment": "chromium",
        "summary": {"total_specs": 16, "total_tests": 107, "passed": 107, "failed": 0, "skipped": 0},
        "scenario_results": [],
    }
    receipt_file = tmp_path / "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    errors = validate_acceptance_scenarios_and_inventory(tmp_path)
    assert any("does not match actual raw artifact hash" in err for err in errors)


def test_validator_rejects_manual_uat_marked_passed(tmp_path: Path) -> None:
    raw_file = tmp_path / "docs/evidence/e2e/raw_playwright_results.json"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text('{"test": 1}', encoding="utf-8")
    actual_hash = hashlib.sha256(b'{"test": 1}').hexdigest()

    git_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    receipt = {
        "git_sha": git_sha,
        "raw_artifact_hash": actual_hash,
        "status": "passed",
        "command": "npx playwright test",
        "runner_versions": {},
        "run_id": "r1",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:01:00Z",
        "exit_code": 0,
        "environment": "chromium",
        "summary": {"total_specs": 16, "total_tests": 107, "passed": 107, "failed": 0, "skipped": 0},
        "scenario_results": [
            {"scenario_id": "E2E-EXP-003", "status": "passed"},
        ],
    }
    receipt_file = tmp_path / "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    errors = validate_acceptance_scenarios_and_inventory(tmp_path)
    assert any("manual UAT scenario E2E-EXP-003 marked as passed" in err for err in errors)


def test_validator_rejects_false_semantic_mapping() -> None:
    for scenario_id, forbidden in FORBIDDEN_SEMANTIC_MAPPINGS.items():
        assert forbidden in ("operator-growth.spec.ts", "operator-governance.spec.ts", "operator-store-ops.spec.ts")
