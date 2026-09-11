"""Tests for product CI parallel jobs refactoring and aggregator verification.

Validates that:
1. .github/workflows/ci.yml splits product checks into isolated parallel runners.
2. All original commands are preserved and uniquely assigned to exactly one owning lane.
3. Required runner dependencies (PostgreSQL service/env, Node 20, npm ci, fetch-depth 0)
   are configured for each owning lane.
4. The product job aggregates results fail-closed under all circumstances.
5. delivery_toolchain/governance/verify_ci_product_jobs.py enforces all required
   acceptance criteria (missing lane, malformed lane, missing result, unknown result,
   failure, cancelled, unexpected skip, tooling skip) for both scopes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from delivery_toolchain.governance.classify_change_review_scope import (
    classify_paths,
    load_manifest,
)
from delivery_toolchain.governance.verify_ci_product_jobs import (
    REQUIRED_PRODUCT_LANES,
    parse_needs,
    verify_product_lanes,
)

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
VERIFY_SCRIPT_PATH = ROOT / "delivery_toolchain" / "governance" / "verify_ci_product_jobs.py"


@pytest.fixture(scope="module")
def ci_workflow() -> dict[str, Any]:
    assert CI_WORKFLOW_PATH.exists(), f"{CI_WORKFLOW_PATH} does not exist."
    data = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ci.yml must parse to a dictionary."
    return data


def test_ci_workflow_triggers(ci_workflow: dict[str, Any]) -> None:
    triggers = ci_workflow.get("on") or ci_workflow.get(True)
    assert isinstance(triggers, dict), "Workflow must have trigger definitions."
    assert "push" in triggers
    assert "pull_request" in triggers
    assert "merge_group" in triggers
    assert set(triggers["push"].get("branches", [])) == {"main", "dev"}
    assert set(triggers["pull_request"].get("branches", [])) == {"main", "dev"}
    assert triggers["merge_group"].get("types") == ["checks_requested"]


def test_ci_workflow_jobs_structure(ci_workflow: dict[str, Any]) -> None:
    jobs = ci_workflow.get("jobs", {})
    assert "change-scope" in jobs
    assert "orchestrator" in jobs
    assert "product" in jobs
    assert "performance-gate" in jobs
    assert "product-e2e-gate" in jobs

    for lane in REQUIRED_PRODUCT_LANES:
        assert lane in jobs, f"Expected product lane {lane!r} in ci.yml jobs."


def test_ci_workflow_product_lanes_isolated(ci_workflow: dict[str, Any]) -> None:
    jobs = ci_workflow["jobs"]

    for lane in REQUIRED_PRODUCT_LANES:
        job = jobs[lane]
        assert job.get("runs-on") == "ubuntu-latest"
        assert job.get("needs") == "change-scope"
        if_cond = str(job.get("if", ""))
        assert "needs.change-scope.outputs.scope != 'development_tooling'" in if_cond


def test_ci_workflow_product_aggregate_job(ci_workflow: dict[str, Any]) -> None:
    jobs = ci_workflow["jobs"]
    product_job = jobs["product"]

    assert product_job.get("runs-on") == "ubuntu-latest"
    assert product_job.get("if") == "always()" or "always()" in str(product_job.get("if"))

    needs = product_job.get("needs", [])
    assert isinstance(needs, list)
    assert "change-scope" in needs
    for lane in REQUIRED_PRODUCT_LANES:
        assert lane in needs, f"Aggregate product job must list {lane!r} in needs."

    steps = product_job.get("steps", [])
    verify_step = next(
        (s for s in steps if "verify_ci_product_jobs.py" in str(s.get("run", ""))),
        None,
    )
    assert verify_step is not None, "product job must execute verify_ci_product_jobs.py"
    env = verify_step.get("env", {})
    assert "NEEDS_JSON" in env or "--needs" in verify_step.get("run", "")


def test_ci_workflow_command_exact_single_ownership(ci_workflow: dict[str, Any]) -> None:
    """Assert each complete original acceptance command is present in exactly one lane."""
    jobs = ci_workflow["jobs"]

    # Canonical acceptance commands from the baseline monolithic product job
    expected_command_lanes = {
        "uv run ruff check tests modules apps shared models solver pipelines infra": "product-lint-unit",
        'uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto': "product-lint-unit",
        "uv run pytest tests/integration/test_official_real_estate_postgresql.py": "product-db",
        'uv run pytest -m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration': "product-db",
        "make api-contract": "product-api-contract",
        "make security": "product-security",
        "make node-check": "product-node",
    }

    def normalize_cmd(text: str) -> str:
        return " ".join(line.strip().rstrip("\\").strip() for line in text.strip().splitlines() if line.strip())

    for expected_cmd, expected_lane in expected_command_lanes.items():
        norm_expected = normalize_cmd(expected_cmd)
        owning_lanes: list[str] = []

        for lane in REQUIRED_PRODUCT_LANES:
            job_steps = jobs[lane].get("steps", [])
            for step in job_steps:
                run_text = step.get("run", "")
                if not run_text:
                    continue
                norm_run = normalize_cmd(run_text)
                if norm_expected in norm_run or norm_run == norm_expected:
                    owning_lanes.append(lane)

        assert len(owning_lanes) == 1, (
            f"Command {expected_cmd!r} must have exactly one owning lane, found: {owning_lanes}"
        )
        assert owning_lanes[0] == expected_lane, (
            f"Command {expected_cmd!r} expected to belong to {expected_lane!r}, but found in {owning_lanes[0]!r}"
        )


def test_ci_workflow_required_runner_prerequisites(ci_workflow: dict[str, Any]) -> None:
    """Assert required runner dependencies and environment across all parallel lanes."""
    jobs = ci_workflow["jobs"]

    # 1. product-lint-unit: PostgreSQL 16 service, INTAKE_TEST_DATABASE_URL, Node 20, npm ci
    unit_job = jobs["product-lint-unit"]
    assert "postgres" in unit_job.get("services", {}), "product-lint-unit must have postgres service container"
    assert unit_job["services"]["postgres"].get("image") == "postgis/postgis:16-3.5"
    assert "INTAKE_TEST_DATABASE_URL" in unit_job.get("env", {}), "product-lint-unit must define INTAKE_TEST_DATABASE_URL"

    unit_steps = unit_job.get("steps", [])
    unit_node_step = next((s for s in unit_steps if "actions/setup-node" in str(s.get("uses", ""))), None)
    assert unit_node_step is not None, "product-lint-unit must set up Node"
    assert str(unit_node_step.get("with", {}).get("node-version")) == "20"
    unit_install_step = next((s for s in unit_steps if "npm ci" in str(s.get("run", ""))), None)
    assert unit_install_step is not None, "product-lint-unit must install npm dependencies via npm ci"

    # 2. product-db: PostgreSQL 16 service, INTAKE_TEST_DATABASE_URL
    db_job = jobs["product-db"]
    assert "postgres" in db_job.get("services", {}), "product-db must have postgres service container"
    assert db_job["services"]["postgres"].get("image") == "postgis/postgis:16-3.5"
    assert "INTAKE_TEST_DATABASE_URL" in db_job.get("env", {}), "product-db must define INTAKE_TEST_DATABASE_URL"

    # 3. product-api-contract: fetch-depth 0 and ODP_API_BASE_REF
    api_job = jobs["product-api-contract"]
    api_checkout = next((s for s in api_job.get("steps", []) if "actions/checkout" in str(s.get("uses", ""))), None)
    assert api_checkout is not None
    assert api_checkout.get("with", {}).get("fetch-depth") == 0
    api_step = next((s for s in api_job.get("steps", []) if "make api-contract" in str(s.get("run", ""))), None)
    assert api_step is not None
    assert "ODP_API_BASE_REF" in api_step.get("env", {})

    # 4. product-security: Node 20, npm ci
    sec_job = jobs["product-security"]
    sec_steps = sec_job.get("steps", [])
    sec_node_step = next((s for s in sec_steps if "actions/setup-node" in str(s.get("uses", ""))), None)
    assert sec_node_step is not None, "product-security must set up Node"
    assert str(sec_node_step.get("with", {}).get("node-version")) == "20"
    sec_install_step = next((s for s in sec_steps if "npm ci" in str(s.get("run", ""))), None)
    assert sec_install_step is not None, "product-security must run npm ci"

    # 5. product-node: Node 20
    node_job = jobs["product-node"]
    node_steps = node_job.get("steps", [])
    node_step = next((s for s in node_steps if "actions/setup-node" in str(s.get("uses", ""))), None)
    assert node_step is not None, "product-node must set up Node"
    assert str(node_step.get("with", {}).get("node-version")) == "20"


# ---------------------------------------------------------------------------
# verify_ci_product_jobs.py unit and behavioral tests
# ---------------------------------------------------------------------------


def make_sample_needs(
    scope: str = "product_or_mixed",
    change_scope_result: str = "success",
    lane_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lane_overrides = lane_overrides or {}
    default_lane_result = "skipped" if scope == "development_tooling" else "success"

    needs: dict[str, Any] = {
        "change-scope": {
            "result": change_scope_result,
            "outputs": {"scope": scope},
        }
    }
    for lane in REQUIRED_PRODUCT_LANES:
        if lane in lane_overrides:
            needs[lane] = lane_overrides[lane]
        else:
            needs[lane] = {"result": default_lane_result, "outputs": {}}
    return needs


def test_parse_needs_valid_and_invalid() -> None:
    data = parse_needs('{"change-scope": {"result": "success"}}')
    assert data == {"change-scope": {"result": "success"}}

    with pytest.raises(ValueError, match="Needs payload is empty"):
        parse_needs("")

    with pytest.raises(ValueError, match="Failed to parse needs payload as JSON"):
        parse_needs("{invalid json")

    with pytest.raises(ValueError, match="Needs payload must be a JSON object"):
        parse_needs("[1, 2, 3]")

    with pytest.raises(ValueError, match="Needs payload must be a JSON object"):
        parse_needs('"string payload"')


def test_verify_product_lanes_success_product_scope() -> None:
    needs = make_sample_needs(scope="product_or_mixed")
    ok, errors = verify_product_lanes(needs)
    assert ok is True
    assert errors == []


def test_verify_product_lanes_success_tooling_scope() -> None:
    needs = make_sample_needs(scope="development_tooling")
    ok, errors = verify_product_lanes(needs)
    assert ok is True
    assert errors == []


def test_verify_product_lanes_tooling_scope_with_success_results() -> None:
    needs = make_sample_needs(
        scope="development_tooling",
        lane_overrides={lane: {"result": "success", "outputs": {}} for lane in REQUIRED_PRODUCT_LANES},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is True
    assert errors == []


def test_verify_product_lanes_fails_when_change_scope_fails() -> None:
    needs = make_sample_needs(change_scope_result="failure")
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("did not succeed" in e for e in errors)


def test_verify_product_lanes_fails_when_change_scope_missing() -> None:
    needs = make_sample_needs()
    del needs["change-scope"]
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("missing from CI needs context" in e for e in errors)


def test_verify_product_lanes_fails_when_change_scope_outputs_invalid() -> None:
    needs = make_sample_needs()
    needs["change-scope"]["outputs"] = None
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("outputs" in e for e in errors)


def test_verify_product_lanes_fails_when_lane_fails() -> None:
    needs = make_sample_needs(
        scope="product_or_mixed",
        lane_overrides={"product-db": {"result": "failure", "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-db' failed" in e for e in errors)


def test_verify_product_lanes_fails_when_lane_cancelled() -> None:
    needs = make_sample_needs(
        scope="product_or_mixed",
        lane_overrides={"product-security": {"result": "cancelled", "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-security' was cancelled" in e for e in errors)


def test_verify_product_lanes_fails_when_lane_unexpectedly_skipped() -> None:
    needs = make_sample_needs(
        scope="product_or_mixed",
        lane_overrides={"product-lint-unit": {"result": "skipped", "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("unexpectedly skipped" in e for e in errors)


@pytest.mark.parametrize("scope", ["product_or_mixed", "development_tooling"])
def test_verify_product_lanes_fails_when_lane_missing(scope: str) -> None:
    """Missing lanes must fail closed in BOTH product and tooling scopes."""
    needs = make_sample_needs(scope=scope)
    del needs["product-node"]
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-node' is missing" in e for e in errors)


@pytest.mark.parametrize("scope", ["product_or_mixed", "development_tooling"])
def test_verify_product_lanes_fails_when_all_lanes_missing(scope: str) -> None:
    """If all 5 lanes are absent from needs context, must fail closed in both scopes."""
    needs = {"change-scope": {"result": "success", "outputs": {"scope": scope}}}
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert len(errors) == len(REQUIRED_PRODUCT_LANES)
    for lane in REQUIRED_PRODUCT_LANES:
        assert any(lane in e and "is missing" in e for e in errors)


@pytest.mark.parametrize("scope", ["product_or_mixed", "development_tooling"])
def test_verify_product_lanes_fails_on_malformed_lane_object(scope: str) -> None:
    """Malformed lane data (not dict) must fail closed in both scopes."""
    needs = make_sample_needs(
        scope=scope,
        lane_overrides={"product-api-contract": "not a dict"},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-api-contract' data is invalid" in e for e in errors)


@pytest.mark.parametrize("scope", ["product_or_mixed", "development_tooling"])
def test_verify_product_lanes_fails_on_missing_result_key(scope: str) -> None:
    """Lane object missing 'result' key must fail closed in both scopes."""
    needs = make_sample_needs(
        scope=scope,
        lane_overrides={"product-security": {"outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-security' is missing 'result' key" in e for e in errors)


@pytest.mark.parametrize("scope", ["product_or_mixed", "development_tooling"])
@pytest.mark.parametrize("bad_result", ["unknown", "in_progress", "neutral", 123, ""])
def test_verify_product_lanes_fails_on_unknown_or_invalid_result(scope: str, bad_result: Any) -> None:
    """Lane with unknown/invalid result must fail closed in both scopes."""
    needs = make_sample_needs(
        scope=scope,
        lane_overrides={"product-lint-unit": {"result": bad_result, "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("product-lint-unit" in e and ("unknown" in e or "non-string" in e) for e in errors)


def test_verify_product_lanes_tooling_scope_fails_on_lane_failure() -> None:
    needs = make_sample_needs(
        scope="development_tooling",
        lane_overrides={"product-lint-unit": {"result": "failure", "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("reported 'failure' during development_tooling" in e for e in errors)


def test_verify_product_lanes_tooling_scope_fails_on_lane_cancelled() -> None:
    needs = make_sample_needs(
        scope="development_tooling",
        lane_overrides={"product-lint-unit": {"result": "cancelled", "outputs": {}}},
    )
    ok, errors = verify_product_lanes(needs)
    assert ok is False
    assert any("reported 'cancelled' during development_tooling" in e for e in errors)


def test_verify_cli_invocation_with_needs_arg() -> None:
    needs = make_sample_needs(scope="product_or_mixed")
    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT_PATH), "--needs", json.dumps(needs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "All 5 product lanes succeeded" in res.stdout


def test_verify_cli_invocation_with_needs_file(tmp_path: Path) -> None:
    needs = make_sample_needs(
        scope="product_or_mixed",
        lane_overrides={"product-api-contract": {"result": "failure", "outputs": {}}},
    )
    file_path = tmp_path / "needs.json"
    file_path.write_text(json.dumps(needs), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT_PATH), "--needs-file", str(file_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1
    assert "product-api-contract' failed" in res.stderr


def test_verify_cli_invocation_via_env() -> None:
    needs = make_sample_needs(scope="development_tooling")
    env = dict(os.environ)
    env["NEEDS_JSON"] = json.dumps(needs)
    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Development tooling change scope verified" in res.stdout


def test_verify_cli_invocation_via_stdin() -> None:
    needs = make_sample_needs(scope="development_tooling")
    res = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT_PATH)],
        input=json.dumps(needs),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Development tooling change scope verified" in res.stdout


def test_verify_change_review_scope_for_task_files() -> None:
    manifest = load_manifest(ROOT / "config" / "change-review-scopes.json")
    touched_paths = [
        ".github/workflows/ci.yml",
        "delivery_toolchain/governance/verify_ci_product_jobs.py",
        "tests/tooling/test_ci_product_parallel.py",
        "docs/evidence/execution-control/ODP-CI-PRODUCT-PARALLEL-JOBS-001/README.md",
    ]
    result = classify_paths(touched_paths, manifest)
    assert result["scope"] == "development_tooling"
    assert result["non_tooling_paths"] == []
