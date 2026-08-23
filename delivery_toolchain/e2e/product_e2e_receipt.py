#!/usr/bin/env python3
"""Canonical product E2E registry and receipt validation primitives."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _support import load_json_object as read_json

SCHEMA_VERSION = "2.0.0"
EXPECTED_CANONICAL_SPEC_COUNT = 17
EXPECTED_PLAYWRIGHT_TEST_COUNT = 108
RAW_PLAYWRIGHT_PATH = "docs/evidence/e2e/raw_playwright_results.json"
RAW_PYTEST_PATH = "docs/evidence/e2e/raw_pytest_results.json"
RECEIPT_PATH = "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json"
EVIDENCE_COMMIT_ALLOWLIST = frozenset(
    {
        RAW_PLAYWRIGHT_PATH,
        RAW_PYTEST_PATH,
        RECEIPT_PATH,
    }
)
WORKER_CONTEXT_PATHS = frozenset({"AI_COLLABORATION_GUIDE.md", "ai-status.json"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TERMINAL_STATUSES = frozenset(
    {"passed", "failed", "skipped", "timedOut", "interrupted", "flaky", "malformed"}
)
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


@dataclass(frozen=True)
class E2EScenario:
    scenario_id: str
    priority: str
    name: str
    owner_role: str
    deterministic_dataset: str
    automation_refs: tuple[str, ...]
    route_or_surface: str
    audit_evidence: tuple[str, ...]
    closes_loop: bool

    @property
    def is_manual(self) -> bool:
        return len(self.automation_refs) == 1 and self.automation_refs[0].startswith("manual-uat:")


E2E_SCENARIOS: tuple[E2EScenario, ...] = (
    E2EScenario(
        "E2E-EXP-001",
        "P0",
        "HeatZone to SiteScore opening decision",
        "expansion_user + site_reviewer",
        "golden_sitescore_dataset:v1",
        (
            "tests/e2e/operator-network-scoring.spec.ts::SiteScore Lab renders GO/WAIT/REJECT scorecards with conditions and reasons",
        ),
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
        (
            "tests/e2e/operator-network-listings.spec.ts::HZ-01 to L-2024 to CS-1001 completes through UI and API",
        ),
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
        ("manual-uat:UAT-SITE-003",),
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
        (
            "tests/integration/test_avm_official_outcome_contract.py::test_official_outcome_migration_has_bounded_source_and_provenance_contracts",
        ),
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
        (
            "tests/e2e/operator-store-ops.spec.ts::Package 10 issue detail exposes the four-light evidence without legacy filter chips",
        ),
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
        (
            "tests/integration/test_intervention_workflow.py::test_full_lifecycle_reaches_completed_with_causal_evidence_and_label",
        ),
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
        (
            "tests/integration/test_priceops_constraints.py::test_full_pilot_lifecycle_records_complete_status_history",
        ),
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
        (
            "tests/integration/test_adlift_incrementality.py::test_difference_in_differences_isolates_ad_lift_from_market_movement",
        ),
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
        (
            "tests/e2e/operator-network-rebalance.spec.ts::AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution",
        ),
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
        (
            "tests/e2e/operator-network-rebalance.spec.ts::AVM + NetPlan workflow persists selected scenario and creates Govern approval without execution",
        ),
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
        (
            "tests/integration/test_learninghub_release.py::test_governed_release_invokes_remote_mlflow_alias_updates",
        ),
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
        (
            "tests/integration/test_learninghub_release.py::test_learninghub_validates_releases_and_rolls_back_model_aliases",
        ),
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
        (
            "tests/integration/test_learninghub_release.py::test_learninghub_blocks_release_without_passed_validation_or_model_card",
            "tests/data/test_pit_snapshot.py::test_point_in_time_validation_rejects_future_feature_snapshot",
        ),
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
        (
            "tests/e2e/operator-governance.spec.ts::Evidence Package export produces a record and an audit event",
        ),
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
        (
            "tests/security/test_rbac_abac.py::test_rbac_denies_action_outside_role",
            "tests/security/test_rbac_abac.py::test_tenant_isolation_blocks_other_tenant",
        ),
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
        ("manual-uat:UAT-FRAN-001..005",),
        "docs/uat/UAT_ACCEPTANCE_PLAN.md#franchisee",
        ("store_scope", "masked_model_details", "supervisor_notification"),
        True,
    ),
)

CANONICAL_SPEC_INVENTORY = (
    "tests/e2e/e2e-network-find-areas-api-binding.spec.ts",
    "tests/e2e/e2e-operator-console.spec.ts",
    "tests/e2e/market_intelligence.spec.ts",
    "tests/e2e/operator-assisted-listing-intake-a11y.spec.ts",
    "tests/e2e/operator-assisted-listing-intake-mobile.spec.ts",
    "tests/e2e/operator-assisted-listing-intake.spec.ts",
    "tests/e2e/operator-governance.spec.ts",
    "tests/e2e/operator-growth.spec.ts",
    "tests/e2e/operator-network-assisted-intake.spec.ts",
    "tests/e2e/operator-network-listings.spec.ts",
    "tests/e2e/operator-network-rebalance.spec.ts",
    "tests/e2e/operator-network-review.spec.ts",
    "tests/e2e/operator-network-scoring.spec.ts",
    "tests/e2e/operator-shell-today.spec.ts",
    "tests/e2e/operator-store-ops.spec.ts",
    "tests/e2e/product-e2e-env.spec.ts",
    "tests/e2e/shell-resource-binding.spec.ts",
)

DELETED_SPEC_REFERENCES = (
    "e2e-exp.spec.ts",
    "e2e-ops.spec.ts",
    "e2e-intervention-price-ad.spec.ts",
    "e2e-avm-netplan.spec.ts",
    "e2e-learning-audit.spec.ts",
)

PYTEST_NODE_IDS = tuple(
    dict.fromkeys(
        ref
        for scenario in E2E_SCENARIOS
        for ref in scenario.automation_refs
        if not scenario.is_manual and ".spec.ts::" not in ref
    )
)


def iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_hash(value: dict[str, Any], hash_field: str) -> str:
    payload = {key: item for key, item in value.items() if key != hash_field}
    return sha256_bytes(canonical_json_bytes(payload))


def seal_normalized(value: dict[str, Any], hash_field: str) -> dict[str, Any]:
    value[hash_field] = normalized_hash(value, hash_field)
    return value


def git_value(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def source_identity(root: Path) -> dict[str, str]:
    return {
        "commit_sha": git_value(root, "rev-parse", "HEAD"),
        "tree_sha": git_value(root, "rev-parse", "HEAD^{tree}"),
    }


def normalize_repo_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    for marker in ("tests/e2e/", "tests/integration/", "tests/data/", "tests/security/"):
        position = normalized.find(marker)
        if position >= 0:
            return normalized[position:]
    normalized = normalized.removeprefix("./")
    if normalized.endswith(".spec.ts") and "/" not in normalized:
        return f"tests/e2e/{normalized}"
    return normalized


def normalize_test_id(file_name: str, title: str) -> str:
    file_part = normalize_repo_path(file_name)
    clean_title = " ".join(str(title).split())
    if not file_part.startswith("tests/") or not clean_title:
        raise ValueError(f"cannot normalize test id from {file_name!r} and {title!r}")
    return f"{file_part}::{clean_title}"


def _playwright_result_status(test: dict[str, Any]) -> tuple[str, list[str]]:
    errors: list[str] = []
    results = test.get("results")
    if not isinstance(results, list) or not results:
        return "malformed", ["test has no runner result"]
    result_statuses = [item.get("status") for item in results if isinstance(item, dict)]
    if len(result_statuses) != len(results):
        return "malformed", ["test has a non-object runner result"]
    if len(results) > 1 or test.get("status") == "flaky":
        return "flaky", errors
    result_status = result_statuses[0]
    declared = test.get("status")
    expected = test.get("expectedStatus")
    if declared == "expected" and expected == "passed" and result_status == "passed":
        return "passed", errors
    if declared == "skipped" or result_status == "skipped":
        return "skipped", errors
    if result_status in {"failed", "timedOut", "interrupted"}:
        return str(result_status), errors
    if declared == "unexpected":
        return "failed", errors
    return "malformed", [
        f"contradictory status declared={declared!r} expected={expected!r} result={result_status!r}"
    ]


def parse_playwright_payload(
    payload: Any,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    errors: list[str] = []
    results: list[dict[str, Any]] = []
    spec_files: set[str] = set()
    seen_ids: set[str] = set()
    if not isinstance(payload, dict):
        return [], _empty_counts(), ["Playwright payload must be an object"]
    suites = payload.get("suites")
    if not isinstance(suites, list) or not suites:
        return [], _empty_counts(), ["Playwright payload has zero suites"]

    def walk(suite: Any, inherited_file: str = "") -> None:
        if not isinstance(suite, dict):
            errors.append("Playwright suite entry must be an object")
            return
        suite_file = str(suite.get("file") or inherited_file)
        specs = suite.get("specs", [])
        if not isinstance(specs, list):
            errors.append("Playwright suite specs must be a list")
            specs = []
        for spec in specs:
            if not isinstance(spec, dict):
                errors.append("Playwright spec entry must be an object")
                continue
            file_name = str(spec.get("file") or suite_file)
            normalized_file = normalize_repo_path(file_name)
            title = str(spec.get("title") or "")
            tests = spec.get("tests")
            if not isinstance(tests, list) or not tests:
                errors.append(f"{normalized_file}::{title} has zero project results")
                continue
            spec_files.add(normalized_file)
            for test in tests:
                if not isinstance(test, dict):
                    errors.append(f"{normalized_file}::{title} has malformed test result")
                    continue
                try:
                    test_id = normalize_test_id(normalized_file, title)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if test_id in seen_ids:
                    errors.append(f"duplicate normalized Playwright test id: {test_id}")
                    continue
                seen_ids.add(test_id)
                status, status_errors = _playwright_result_status(test)
                errors.extend(f"{test_id}: {item}" for item in status_errors)
                attempts = test.get("results") if isinstance(test.get("results"), list) else []
                results.append(
                    {
                        "test_id": test_id,
                        "status": status,
                        "project": test.get("projectName"),
                        "expected_status": test.get("expectedStatus"),
                        "attempts": [
                            {
                                "status": attempt.get("status"),
                                "duration_ms": attempt.get("duration"),
                                "retry": attempt.get("retry"),
                                "started_at": attempt.get("startTime"),
                            }
                            for attempt in attempts
                            if isinstance(attempt, dict)
                        ],
                    }
                )
        children = suite.get("suites", [])
        if not isinstance(children, list):
            errors.append("Playwright child suites must be a list")
            return
        for child in children:
            walk(child, suite_file)

    for suite in suites:
        walk(suite)

    counts = _counts_from_results(results)
    counts["total_specs"] = len(spec_files)
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        errors.append("Playwright payload missing stats object")
    else:
        expected_stats = {
            "expected": counts["passed"],
            "skipped": counts["skipped"],
            "unexpected": (
                counts["failed"] + counts["timed_out"] + counts["interrupted"] + counts["malformed"]
            ),
            "flaky": counts["flaky"],
        }
        for field, expected_value in expected_stats.items():
            actual = stats.get(field)
            if not isinstance(actual, int) or actual < 0:
                errors.append(f"Playwright stats.{field} must be a non-negative integer")
            elif actual != expected_value:
                errors.append(
                    f"Playwright stats.{field}={actual} contradicts parsed count {expected_value}"
                )
    if counts["total_tests"] == 0:
        errors.append("Playwright payload has zero tests")
    return results, counts, errors


def _empty_counts() -> dict[str, int]:
    return {
        "total_specs": 0,
        "total_tests": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "interrupted": 0,
        "flaky": 0,
        "malformed": 0,
    }


def _counts_from_results(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = _empty_counts()
    counts["total_tests"] = len(results)
    status_fields = {
        "passed": "passed",
        "failed": "failed",
        "skipped": "skipped",
        "timedOut": "timed_out",
        "interrupted": "interrupted",
        "flaky": "flaky",
        "malformed": "malformed",
    }
    for result in results:
        field = status_fields.get(str(result.get("status")))
        if field:
            counts[field] += 1
        else:
            counts["malformed"] += 1
    return counts


def _pytest_result_for_node(
    nodeid: str, phase_reports: Any
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(phase_reports, list) or not phase_reports:
        return (
            {
                "test_id": nodeid,
                "status": "malformed",
                "duration_ms": 0.0,
                "phases": phase_reports if isinstance(phase_reports, list) else [],
            },
            [f"{nodeid}: missing or malformed phase reports"],
        )

    phases: list[str] = []
    outcomes: list[str] = []
    duration_seconds = 0.0
    malformed = False
    phase_order = {"setup": 0, "call": 1, "teardown": 2}
    for index, report in enumerate(phase_reports):
        if not isinstance(report, dict):
            errors.append(f"{nodeid}: phase report {index} must be an object")
            malformed = True
            continue
        phase = report.get("phase")
        outcome = report.get("outcome")
        duration = report.get("duration_seconds")
        if phase not in phase_order:
            errors.append(f"{nodeid}: phase report {index} has invalid phase {phase!r}")
            malformed = True
        else:
            phases.append(str(phase))
        if outcome not in {"passed", "failed", "skipped"}:
            errors.append(f"{nodeid}: phase report {index} has invalid outcome {outcome!r}")
            malformed = True
        else:
            outcomes.append(str(outcome))
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or float(duration) < 0
        ):
            errors.append(f"{nodeid}: phase report {index} has invalid duration_seconds")
            malformed = True
        else:
            duration_seconds += float(duration)

    if len(phases) != len(set(phases)):
        errors.append(f"{nodeid}: contains duplicate pytest phases")
        malformed = True
    if phases != sorted(phases, key=phase_order.__getitem__):
        errors.append(f"{nodeid}: pytest phases are out of order")
        malformed = True

    if malformed:
        status = "malformed"
    elif "failed" in outcomes:
        status = "failed"
    elif "skipped" in outcomes:
        status = "skipped"
    elif phases == ["setup", "call", "teardown"] and outcomes == [
        "passed",
        "passed",
        "passed",
    ]:
        status = "passed"
    else:
        status = "malformed"
        errors.append(f"{nodeid}: passing pytest result requires setup/call/teardown")

    return (
        {
            "test_id": nodeid,
            "status": status,
            "duration_ms": round(duration_seconds * 1000, 3),
            "phases": phase_reports,
        },
        errors,
    )


def parse_pytest_payload(
    payload: Any,
    *,
    expected_source: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    """Recompute the complete pytest artifact projection from its raw payload."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [], _empty_counts(), ["Pytest payload must be an object"]

    canonical_ids = list(PYTEST_NODE_IDS)
    canonical_id_set = set(canonical_ids)
    requested_ids = payload.get("requested_node_ids")
    if not isinstance(requested_ids, list) or any(
        not isinstance(nodeid, str) for nodeid in requested_ids
    ):
        errors.append("Pytest requested_node_ids must be a list of strings")
        requested_ids = []
    else:
        if len(requested_ids) != len(set(requested_ids)):
            errors.append("Pytest requested_node_ids contains duplicates")
        if requested_ids != canonical_ids:
            errors.append("Pytest requested_node_ids do not exactly match canonical node ids")
        missing_requested = sorted(canonical_id_set - set(requested_ids))
        unexpected_requested = sorted(set(requested_ids) - canonical_id_set)
        if missing_requested:
            errors.append("missing requested test ids: " + ", ".join(missing_requested))
        if unexpected_requested:
            errors.append("unexpected requested test ids: " + ", ".join(unexpected_requested))

    phase_reports = payload.get("phase_reports")
    if not isinstance(phase_reports, dict) or any(
        not isinstance(nodeid, str) for nodeid in phase_reports
    ):
        errors.append("Pytest phase_reports must be an object keyed by test id")
        phase_reports = {}
    collected_ids = set(phase_reports)
    unexpected_ids = sorted(collected_ids - canonical_id_set)
    missing_ids = sorted(canonical_id_set - collected_ids)
    if unexpected_ids:
        errors.append("unexpected collected test ids: " + ", ".join(unexpected_ids))
    if missing_ids:
        errors.append("missing exact test ids: " + ", ".join(missing_ids))

    collection_errors = payload.get("collection_errors")
    if not isinstance(collection_errors, list) or any(
        not isinstance(item, str) for item in collection_errors
    ):
        errors.append("Pytest collection_errors must be a list of strings")
    else:
        errors.extend(collection_errors)

    runner_start_source = payload.get("runner_start_source")
    if not isinstance(runner_start_source, dict):
        errors.append("Pytest payload missing runner_start_source")
    else:
        observed_commit = runner_start_source.get("commit_sha")
        observed_tree = runner_start_source.get("tree_sha")
        if not SHA_RE.fullmatch(str(observed_commit or "")):
            errors.append("Pytest runner_start_source commit_sha is invalid")
        if not SHA_RE.fullmatch(str(observed_tree or "")):
            errors.append("Pytest runner_start_source tree_sha is invalid")
        if isinstance(expected_source, dict):
            if observed_commit != expected_source.get("commit_sha"):
                errors.append(
                    "runner-start source SHA does not match declared tested source"
                )
            if observed_tree != expected_source.get("tree_sha"):
                errors.append(
                    "runner-start tree SHA does not match declared tested tree"
                )

    results: list[dict[str, Any]] = []
    for nodeid in canonical_ids:
        result, result_errors = _pytest_result_for_node(
            nodeid, phase_reports.get(nodeid, [])
        )
        results.append(result)
        errors.extend(result_errors)
    return results, _counts_from_results(results), errors


def _strict_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == UTC else None


def validate_raw_artifact(
    artifact: Any,
    expected_runner: str,
    *,
    require_success: bool = True,
) -> list[str]:
    errors: list[str] = []
    label = f"raw {expected_runner}"
    if not isinstance(artifact, dict):
        return [f"{label} artifact must be an object"]
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label} schema_version must be {SCHEMA_VERSION}")
    if artifact.get("runner") != expected_runner:
        errors.append(f"{label} runner identity mismatch")
    stored_hash = artifact.get("normalized_artifact_sha256")
    computed_hash = normalized_hash(artifact, "normalized_artifact_sha256")
    if stored_hash != computed_hash:
        errors.append(f"{label} normalized artifact hash mismatch")

    source = artifact.get("source")
    if not isinstance(source, dict):
        errors.append(f"{label} missing source identity")
    else:
        if not SHA_RE.fullmatch(str(source.get("commit_sha") or "")):
            errors.append(f"{label} source commit_sha is invalid")
        if not SHA_RE.fullmatch(str(source.get("tree_sha") or "")):
            errors.append(f"{label} source tree_sha is invalid")

    run = artifact.get("run")
    if not isinstance(run, dict):
        errors.append(f"{label} missing run metadata")
        run = {}
    for field in ("command", "version", "started_at", "ended_at", "environment"):
        if not run.get(field):
            errors.append(f"{label} missing run.{field}")
    started_at = _strict_utc_timestamp(run.get("started_at"))
    ended_at = _strict_utc_timestamp(run.get("ended_at"))
    if started_at is None:
        errors.append(f"{label} run.started_at must be a strict UTC timestamp ending in Z")
    if ended_at is None:
        errors.append(f"{label} run.ended_at must be a strict UTC timestamp ending in Z")
    if started_at is not None and ended_at is not None and ended_at < started_at:
        errors.append(f"{label} run.ended_at precedes run.started_at")
    exit_code = run.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        errors.append(f"{label} run.exit_code must be an integer")

    payload = artifact.get("payload")
    if artifact.get("payload_sha256") != sha256_bytes(canonical_json_bytes(payload)):
        errors.append(f"{label} payload hash mismatch")

    results = artifact.get("results")
    if not isinstance(results, list):
        errors.append(f"{label} results must be a list")
        results = []
    ids = [item.get("test_id") for item in results if isinstance(item, dict)]
    if len(ids) != len(results):
        errors.append(f"{label} contains malformed result entries")
    if len(ids) != len(set(ids)):
        errors.append(f"{label} contains duplicate normalized test ids")
    if any(
        not isinstance(item, dict)
        or item.get("status") not in TERMINAL_STATUSES
        or not isinstance(item.get("test_id"), str)
        or "::" not in item.get("test_id", "")
        for item in results
    ):
        errors.append(f"{label} contains invalid normalized result")

    counts = artifact.get("counts")
    parsed_counts = _counts_from_results([item for item in results if isinstance(item, dict)])
    if expected_runner == "playwright":
        parsed_counts["total_specs"] = len(
            {
                str(item.get("test_id")).split("::", 1)[0]
                for item in results
                if isinstance(item, dict) and "::" in str(item.get("test_id"))
            }
        )
    if counts != parsed_counts:
        errors.append(f"{label} counts contradict exact result list")
    integrity_errors = artifact.get("integrity_errors")
    if not isinstance(integrity_errors, list):
        errors.append(f"{label} integrity_errors must be a list")
        integrity_errors = ["malformed integrity_errors"]
    if expected_runner == "playwright":
        payload_results, payload_counts, payload_errors = parse_playwright_payload(payload)
        if results != payload_results:
            errors.append(f"{label} results do not exactly match parsed payload")
        if counts != payload_counts:
            errors.append(f"{label} counts do not exactly match parsed payload")
        if integrity_errors != payload_errors:
            errors.append(f"{label} integrity_errors do not exactly match parsed payload")
    elif expected_runner == "pytest":
        payload_results, payload_counts, payload_errors = parse_pytest_payload(
            payload,
            expected_source=source,
        )
        if results != payload_results:
            errors.append(f"{label} results do not exactly match parsed payload")
        if counts != payload_counts:
            errors.append(f"{label} counts do not exactly match parsed payload")
        if integrity_errors != payload_errors:
            errors.append(f"{label} integrity_errors do not exactly match parsed payload")
    if require_success:
        if exit_code != 0:
            errors.append(f"{label} runner exited {exit_code}")
        if integrity_errors:
            errors.append(f"{label} carries integrity errors")
        if not results:
            errors.append(f"{label} has zero tests")
        if any(item.get("status") != "passed" for item in results if isinstance(item, dict)):
            errors.append(f"{label} contains non-passing, skipped, flaky, or malformed results")
    return errors


def _status_paths(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value in WORKER_CONTEXT_PATHS or value.startswith(".orchestrator/task-briefs/"):
            continue
        if value:
            paths.append(value)
    return sorted(set(paths))


def verify_evidence_relationship(
    root: Path,
    source: dict[str, Any],
    *,
    allow_worktree_evidence: bool,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    source_sha = str(source.get("commit_sha") or "")
    source_tree = str(source.get("tree_sha") or "")
    if not SHA_RE.fullmatch(source_sha) or not SHA_RE.fullmatch(source_tree):
        return {}, ["tested source SHA/tree is malformed"]
    try:
        recorded_tree = git_value(root, "show", "-s", "--format=%T", source_sha)
        head_sha = git_value(root, "rev-parse", "HEAD")
    except subprocess.CalledProcessError as exc:
        return {}, [f"tested source commit cannot be resolved: {exc}"]
    if recorded_tree != source_tree:
        errors.append(
            f"tested tree mismatch: source commit has {recorded_tree}, artifact records {source_tree}"
        )

    relation = "exact_source_head"
    touched_paths: list[str] = []
    if head_sha != source_sha:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, head_sha],
            cwd=root,
            check=False,
        )
        if ancestor.returncode != 0:
            errors.append("tested source is not an ancestor of evidence HEAD")
        else:
            relation = "evidence_only_descendant"
            log_output = git_value(
                root,
                "log",
                "--format=",
                "--name-only",
                f"{source_sha}..{head_sha}",
            )
            touched_paths = sorted(
                {line.strip() for line in log_output.splitlines() if line.strip()}
            )
            disallowed = sorted(set(touched_paths) - EVIDENCE_COMMIT_ALLOWLIST)
            if disallowed:
                errors.append(
                    "intervening commits touch non-evidence paths: " + ", ".join(disallowed)
                )
    worktree_paths = _status_paths(root)
    if worktree_paths:
        disallowed_worktree = sorted(set(worktree_paths) - EVIDENCE_COMMIT_ALLOWLIST)
        if disallowed_worktree:
            errors.append(
                "working tree contains non-evidence changes: " + ", ".join(disallowed_worktree)
            )
        elif not allow_worktree_evidence:
            errors.append("evidence validation requires a clean working tree")
    return (
        {
            "tested_source_sha": source_sha,
            "tested_tree_sha": source_tree,
            "evidence_head_sha": head_sha,
            "relation": relation,
            "intervening_touched_paths": touched_paths,
            "working_tree_paths": worktree_paths,
            "allowlist": sorted(EVIDENCE_COMMIT_ALLOWLIST),
        },
        errors,
    )


def validate_evidence_proof_at_generation(
    root: Path,
    source: dict[str, Any],
    proof: Any,
) -> list[str]:
    """Recompute every durable part of the generation-time Git proof."""
    label = "receipt evidence_proof_at_generation"
    if not isinstance(proof, dict):
        return [f"{label} must be an object"]

    source_sha = str(source.get("commit_sha") or "")
    source_tree = str(source.get("tree_sha") or "")
    evidence_head_sha = str(proof.get("evidence_head_sha") or "")
    working_tree_paths = proof.get("working_tree_paths")
    errors: list[str] = []
    if not SHA_RE.fullmatch(evidence_head_sha):
        return [f"{label} evidence_head_sha is invalid"]
    if not isinstance(working_tree_paths, list) or any(
        not isinstance(path, str) or not path for path in working_tree_paths
    ):
        errors.append(f"{label} working_tree_paths must be a list of paths")
        working_tree_paths = []
    if working_tree_paths != sorted(set(working_tree_paths)):
        errors.append(f"{label} working_tree_paths must be sorted and unique")
    disallowed_worktree = sorted(set(working_tree_paths) - EVIDENCE_COMMIT_ALLOWLIST)
    if disallowed_worktree:
        errors.append(
            f"{label} records non-evidence working tree paths: " + ", ".join(disallowed_worktree)
        )
    required_raw_paths = {RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH}
    if not required_raw_paths.issubset(working_tree_paths):
        errors.append(f"{label} does not record both generated raw artifact paths")

    try:
        recorded_tree = git_value(root, "show", "-s", "--format=%T", source_sha)
        git_value(root, "show", "-s", "--format=%H", evidence_head_sha)
    except subprocess.CalledProcessError as exc:
        return errors + [f"{label} commit cannot be resolved: {exc}"]
    if recorded_tree != source_tree:
        errors.append(f"{label} tested source tree does not match its commit")

    relation = "exact_source_head"
    touched_paths: list[str] = []
    if evidence_head_sha != source_sha:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, evidence_head_sha],
            cwd=root,
            check=False,
        )
        if ancestor.returncode != 0:
            errors.append(f"{label} tested source is not an ancestor of evidence head")
        else:
            relation = "evidence_only_descendant"
            log_output = git_value(
                root,
                "log",
                "--format=",
                "--name-only",
                f"{source_sha}..{evidence_head_sha}",
            )
            touched_paths = sorted(
                {line.strip() for line in log_output.splitlines() if line.strip()}
            )
            disallowed = sorted(set(touched_paths) - EVIDENCE_COMMIT_ALLOWLIST)
            if disallowed:
                errors.append(
                    f"{label} intervening commits touch non-evidence paths: "
                    + ", ".join(disallowed)
                )

    expected = {
        "tested_source_sha": source_sha,
        "tested_tree_sha": source_tree,
        "evidence_head_sha": evidence_head_sha,
        "relation": relation,
        "intervening_touched_paths": touched_paths,
        "working_tree_paths": working_tree_paths,
        "allowlist": sorted(EVIDENCE_COMMIT_ALLOWLIST),
    }
    if proof != expected:
        errors.append(f"{label} does not exactly match recomputed Git relationship")
    return errors


def expected_aggregate_counts(
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, int]:
    runner_counts = [
        artifact.get("counts")
        for artifact in artifacts.values()
        if isinstance(artifact.get("counts"), dict)
    ]

    def count_value(counts: dict[str, Any], field: str) -> int:
        value = counts.get(field)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    total_tests = sum(count_value(counts, "total_tests") for counts in runner_counts)
    passed = sum(count_value(counts, "passed") for counts in runner_counts)
    return {
        "total_tests": total_tests,
        "passed": passed,
        "non_passing": total_tests - passed,
        "total_scenarios": len(E2E_SCENARIOS),
        "automated_scenarios": sum(1 for scenario in E2E_SCENARIOS if not scenario.is_manual),
        "manual_pending_scenarios": sum(1 for scenario in E2E_SCENARIOS if scenario.is_manual),
    }


def bind_scenarios(
    artifacts: dict[str, dict[str, Any]],
    artifact_hashes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for runner, artifact in artifacts.items():
        index: dict[str, dict[str, Any]] = {}
        for result in artifact.get("results", []):
            if not isinstance(result, dict) or not isinstance(result.get("test_id"), str):
                continue
            test_id = result["test_id"]
            if test_id in index:
                errors.append(f"duplicate {runner} result for exact test id {test_id}")
            index[test_id] = result
        indexes[runner] = index

    scenario_results: list[dict[str, Any]] = []
    seen_scenarios: set[str] = set()
    for scenario in E2E_SCENARIOS:
        if scenario.scenario_id in seen_scenarios:
            errors.append(f"duplicate scenario id {scenario.scenario_id}")
            continue
        seen_scenarios.add(scenario.scenario_id)
        if scenario.is_manual:
            scenario_results.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "priority": scenario.priority,
                    "name": scenario.name,
                    "automation_refs": list(scenario.automation_refs),
                    "status": "pending",
                    "route": "ODP-PLAN-UAT-SIGNOFF-001",
                }
            )
            continue

        bindings: list[dict[str, Any]] = []
        scenario_ok = True
        for test_id in scenario.automation_refs:
            runner = "playwright" if ".spec.ts::" in test_id else "pytest"
            result = indexes.get(runner, {}).get(test_id)
            if result is None:
                errors.append(f"{scenario.scenario_id} missing exact {runner} result for {test_id}")
                scenario_ok = False
                continue
            if result.get("status") != "passed":
                errors.append(
                    f"{scenario.scenario_id} exact result {test_id} is {result.get('status')}"
                )
                scenario_ok = False
            bindings.append(
                {
                    "runner": runner,
                    "test_id": test_id,
                    "raw_result": result,
                    "artifact_path": (
                        RAW_PLAYWRIGHT_PATH if runner == "playwright" else RAW_PYTEST_PATH
                    ),
                    "artifact_sha256": artifact_hashes[runner],
                }
            )
        scenario_results.append(
            {
                "scenario_id": scenario.scenario_id,
                "priority": scenario.priority,
                "name": scenario.name,
                "automation_refs": list(scenario.automation_refs),
                "status": "passed" if scenario_ok else "failed",
                "bindings": bindings,
            }
        )
    return scenario_results, errors


def validate_receipt_packet(
    root: Path,
    *,
    allow_worktree_evidence: bool = False,
) -> list[str]:
    errors: list[str] = []
    paths = {
        "playwright": root / RAW_PLAYWRIGHT_PATH,
        "pytest": root / RAW_PYTEST_PATH,
    }
    artifacts: dict[str, dict[str, Any]] = {}
    artifact_hashes: dict[str, str] = {}
    for runner, path in paths.items():
        if not path.is_file():
            errors.append(f"missing raw {runner} artifact: {path.relative_to(root)}")
            continue
        try:
            raw_bytes = path.read_bytes()
            artifact_hashes[runner] = sha256_bytes(raw_bytes)
            artifact = json.loads(raw_bytes)
            artifacts[runner] = artifact
            errors.extend(validate_raw_artifact(artifact, runner))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"raw {runner} artifact is malformed: {exc}")
    receipt_path = root / RECEIPT_PATH
    if not receipt_path.is_file():
        errors.append(f"missing execution receipt: {RECEIPT_PATH}")
        return errors
    try:
        receipt = read_json(receipt_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"execution receipt is malformed: {exc}")
        return errors

    if receipt.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"receipt schema_version must be {SCHEMA_VERSION}")
    if receipt.get("normalized_receipt_sha256") != normalized_hash(
        receipt, "normalized_receipt_sha256"
    ):
        errors.append("receipt normalized hash mismatch")
    if receipt.get("status") != "passed" or receipt.get("exit_code") != 0:
        errors.append("receipt does not record a passing aggregate execution")
    if receipt.get("validation_errors"):
        errors.append("receipt carries validation errors")

    if len(artifacts) == 2:
        sources = [artifact.get("source") for artifact in artifacts.values()]
        if sources[0] != sources[1]:
            errors.append("raw runner artifacts do not share the exact tested source/tree")
        source = receipt.get("tested_source")
        if source != sources[0]:
            errors.append("receipt tested_source does not match both raw runner artifacts")
        if isinstance(source, dict):
            _proof, proof_errors = verify_evidence_relationship(
                root, source, allow_worktree_evidence=allow_worktree_evidence
            )
            errors.extend(proof_errors)
            errors.extend(
                validate_evidence_proof_at_generation(
                    root, source, receipt.get("evidence_proof_at_generation")
                )
            )

        receipt_artifacts = receipt.get("artifacts")
        if not isinstance(receipt_artifacts, list):
            errors.append("receipt artifacts must be a list")
        else:
            expected_runners = set(paths)
            artifact_runners = [
                item.get("runner") for item in receipt_artifacts if isinstance(item, dict)
            ]
            if len(artifact_runners) != len(receipt_artifacts):
                errors.append("receipt artifact reconciliation entries must be objects")
            if (
                len(artifact_runners) != len(expected_runners)
                or len(artifact_runners) != len(set(artifact_runners))
                or set(artifact_runners) != expected_runners
            ):
                errors.append(
                    "receipt artifact reconciliations must contain each runner exactly once"
                )
            receipt_index = {
                item["runner"]: item
                for item in receipt_artifacts
                if isinstance(item, dict) and item.get("runner") in expected_runners
            }
            for runner, artifact in artifacts.items():
                entry = receipt_index.get(runner)
                if not isinstance(entry, dict):
                    errors.append(f"receipt missing {runner} artifact reconciliation")
                    continue
                expected_path = RAW_PLAYWRIGHT_PATH if runner == "playwright" else RAW_PYTEST_PATH
                if entry.get("path") != expected_path:
                    errors.append(f"receipt {runner} artifact path mismatch")
                if entry.get("sha256") != artifact_hashes[runner]:
                    errors.append(f"receipt {runner} artifact hash mismatch")
                if entry.get("normalized_artifact_sha256") != artifact.get(
                    "normalized_artifact_sha256"
                ):
                    errors.append(f"receipt {runner} normalized artifact hash mismatch")
                run = artifact.get("run", {})
                for field in (
                    "command",
                    "version",
                    "started_at",
                    "ended_at",
                    "exit_code",
                    "environment",
                ):
                    if entry.get(field) != run.get(field):
                        errors.append(f"receipt {runner} metadata mismatch for {field}")

        expected_results, binding_errors = bind_scenarios(artifacts, artifact_hashes)
        errors.extend(binding_errors)
        actual_results = receipt.get("scenario_results")
        if actual_results != expected_results:
            errors.append("receipt scenario bindings do not exactly match raw results")
        ids = (
            [
                item.get("scenario_id")
                for item in actual_results
                if isinstance(actual_results, list) and isinstance(item, dict)
            ]
            if isinstance(actual_results, list)
            else []
        )
        if len(ids) != len(E2E_SCENARIOS) or len(ids) != len(set(ids)):
            errors.append("receipt has missing or duplicate scenario results")

        playwright_counts = artifacts["playwright"].get("counts", {})
        if (
            playwright_counts.get("total_specs") != EXPECTED_CANONICAL_SPEC_COUNT
            or playwright_counts.get("total_tests") != EXPECTED_PLAYWRIGHT_TEST_COUNT
            or playwright_counts.get("passed") != EXPECTED_PLAYWRIGHT_TEST_COUNT
        ):
            errors.append(
                "Playwright receipt counts do not prove "
                f"{EXPECTED_CANONICAL_SPEC_COUNT} specs / "
                f"{EXPECTED_PLAYWRIGHT_TEST_COUNT} passes"
            )
        if receipt.get("runner_counts") != {
            runner: artifact.get("counts") for runner, artifact in artifacts.items()
        }:
            errors.append("receipt runner_counts do not match raw artifacts")
        if receipt.get("aggregate_counts") != expected_aggregate_counts(artifacts):
            errors.append("receipt aggregate_counts do not match raw artifacts")
    return errors


def validate_acceptance_scenarios_and_inventory(root: Path) -> list[str]:
    """Validate the executable registry and canonical test inventory.

    The checked-in execution packet is intentionally validated separately by
    :func:`validate_receipt_packet`.  A receipt proves one exact tested source;
    ordinary commits after that source are expected to make it stale until the
    product E2E runner emits a fresh packet.  Treating that expected staleness as
    a generic product-unit-test failure deadlocks every reviewed dev merge.
    """
    errors: list[str] = []
    scenario_ids = [scenario.scenario_id for scenario in E2E_SCENARIOS]
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("acceptance registry contains duplicate scenario ids")

    for scenario in E2E_SCENARIOS:
        for ref in scenario.automation_refs:
            if any(deleted in ref for deleted in DELETED_SPEC_REFERENCES):
                errors.append(f"{scenario.scenario_id} cites a deleted spec reference: {ref}")
            if scenario.is_manual:
                continue
            if ref.count("::") != 1:
                errors.append(
                    f"{scenario.scenario_id} must use one exact normalized test id: {ref}"
                )
                continue
            file_name, exact_title = ref.split("::", 1)
            target = root / file_name
            if not target.is_file():
                errors.append(f"{scenario.scenario_id} exact test file is missing: {file_name}")
            elif exact_title not in target.read_text(encoding="utf-8"):
                errors.append(f"{scenario.scenario_id} exact test title is missing: {ref}")

    actual_inventory = tuple(
        sorted(
            str(path.relative_to(root)).replace(os.sep, "/")
            for path in (root / "tests/e2e").glob("*.spec.ts")
            if path.is_file()
        )
    )
    if actual_inventory != tuple(sorted(CANONICAL_SPEC_INVENTORY)):
        errors.append(
            "canonical Playwright spec inventory differs from the explicit "
            f"{EXPECTED_CANONICAL_SPEC_COUNT}-file registry"
        )

    proc = subprocess.run(
        ["npx", "playwright", "test", "--list", "--project=chromium"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        errors.append(f"Playwright --list exited {proc.returncode}: {proc.stderr.strip()}")
    else:
        match = re.search(r"Total:\s*(\d+)\s*tests\s*in\s*(\d+)\s*files", proc.stdout)
        if not match:
            errors.append("Playwright --list output has no parseable total")
        elif (
            int(match.group(1)) != EXPECTED_PLAYWRIGHT_TEST_COUNT
            or int(match.group(2)) != EXPECTED_CANONICAL_SPEC_COUNT
        ):
            errors.append(
                "Playwright inventory must be exactly "
                f"{EXPECTED_PLAYWRIGHT_TEST_COUNT} tests in "
                f"{EXPECTED_CANONICAL_SPEC_COUNT} files"
            )

    return errors
