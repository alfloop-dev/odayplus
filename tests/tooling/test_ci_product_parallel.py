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
import re
import shlex
import shutil
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


def tokenize_command_line(line: str) -> list[str]:
    """Tokenize a shell command line while preserving quotes and separating punctuation operators."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = False
    lexer.commenters = "#"
    return list(lexer)


def parse_executable_commands_from_script(script: str) -> list[list[str]]:
    """Extract individual executable command argv lists from a GitHub Actions run block.

    Correctly handles:
    - Multi-line run blocks
    - Backslash line continuations
    - Shell comments (# ...)
    - Sequential and chained commands (;, &&, ||, |, &)
    """
    if not script or not script.strip():
        return []

    lines = script.splitlines()
    logical_lines: list[str] = []
    buf: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if buf:
                logical_lines.append(" ".join(buf))
                buf = []
            continue
        if stripped.endswith("\\"):
            buf.append(stripped[:-1].strip())
        else:
            buf.append(stripped)
            logical_lines.append(" ".join(buf))
            buf = []
    if buf:
        logical_lines.append(" ".join(buf))

    commands: list[list[str]] = []
    for log_line in logical_lines:
        try:
            tokens = tokenize_command_line(log_line)
        except ValueError:
            tokens = log_line.split()

        current_cmd: list[str] = []
        for token in tokens:
            if token in (";", "&&", "||", "|", "&"):
                if current_cmd:
                    commands.append(current_cmd)
                    current_cmd = []
            else:
                current_cmd.append(token)
        if current_cmd:
            commands.append(current_cmd)

    return commands


def count_exact_command_occurrences(
    jobs: dict[str, Any],
    expected_command_str: str,
) -> dict[str, int]:
    """Find exact occurrences of expected_command_str across all jobs.

    Returns a dict mapping job_name -> count of exact matches.
    """
    expected_argv = tokenize_command_line(expected_command_str)
    occurrences: dict[str, int] = {}

    for job_name, job_data in jobs.items():
        if not isinstance(job_data, dict):
            continue
        count = 0
        for step in job_data.get("steps", []):
            run_script = step.get("run", "")
            if not run_script:
                continue
            step_commands = parse_executable_commands_from_script(run_script)
            for cmd_argv in step_commands:
                if cmd_argv == expected_argv:
                    count += 1
        if count > 0:
            occurrences[job_name] = count

    return occurrences


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

    # Canonical acceptance commands; database execution also preserves JUnit evidence.
    expected_command_lanes = {
        "uv run ruff check tests modules apps shared models solver pipelines infra": "product-lint-unit",
        'uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto': "product-lint-unit",
        "uv run pytest tests/integration/test_official_real_estate_postgresql.py": "product-db",
        'uv run pytest -ra --junitxml="$RUNNER_TEMP/product-db-results.xml" -m "requires_live_env" tests/contract tests/ops tests/integration': "product-db",
        "make api-contract": "product-api-contract",
        "make security": "product-security",
        "make node-check": "product-node",
    }

    for expected_cmd, expected_lane in expected_command_lanes.items():
        occurrences = count_exact_command_occurrences(jobs, expected_cmd)
        total_count = sum(occurrences.values())

        assert total_count == 1, (
            f"Command {expected_cmd!r} must have exactly 1 occurrence across the entire workflow, "
            f"found total {total_count} in lanes: {occurrences}"
        )
        assert list(occurrences.keys()) == [expected_lane], (
            f"Command {expected_cmd!r} expected uniquely in {expected_lane!r}, "
            f"found in: {list(occurrences.keys())}"
        )
        assert occurrences[expected_lane] == 1


# ---------------------------------------------------------------------------
# Command parser and exact argv coverage regression tests
# ---------------------------------------------------------------------------


def test_parse_executable_commands_handles_continuation() -> None:
    script = (
        'uv run pytest -m "requires_live_env and not requires_postgis" \\\n'
        '  tests/contract tests/ops tests/integration\n'
    )
    cmds = parse_executable_commands_from_script(script)
    assert len(cmds) == 1
    assert cmds[0] == [
        "uv",
        "run",
        "pytest",
        "-m",
        "requires_live_env and not requires_postgis",
        "tests/contract",
        "tests/ops",
        "tests/integration",
    ]


def test_parse_executable_commands_handles_chaining_and_comments() -> None:
    script = (
        "# Setup step\n"
        "uv sync && npm ci\n"
        "make bootstrap; make security\n"
    )
    cmds = parse_executable_commands_from_script(script)
    assert len(cmds) == 4
    assert cmds[0] == ["uv", "sync"]
    assert cmds[1] == ["npm", "ci"]
    assert cmds[2] == ["make", "bootstrap"]
    assert cmds[3] == ["make", "security"]


def test_command_exact_matching_rejects_narrowed_selector() -> None:
    expected = 'uv run pytest -m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration'
    # Mutated with an additional ignore flag
    mutated_script = (
        'uv run pytest -m "requires_live_env and not requires_postgis" \\\n'
        '  tests/contract tests/ops tests/integration --ignore=tests/contract'
    )
    mock_jobs = {
        "product-db": {"steps": [{"run": mutated_script}]},
    }
    occurrences = count_exact_command_occurrences(mock_jobs, expected)
    assert occurrences == {}, "Narrowed command selector must not match original required command"


def test_command_exact_matching_rejects_substring_echo() -> None:
    expected = "make security"
    mock_jobs = {
        "product-security": {"steps": [{"run": 'echo "make security"'}]},
    }
    occurrences = count_exact_command_occurrences(mock_jobs, expected)
    assert occurrences == {}, "echo 'make security' must not match executable make security"


def test_command_exact_matching_rejects_same_step_duplication() -> None:
    expected = "make security"
    mock_jobs = {
        "product-security": {
            "steps": [
                {
                    "run": "make security\nmake security\n",
                }
            ]
        }
    }
    occurrences = count_exact_command_occurrences(mock_jobs, expected)
    assert occurrences == {"product-security": 2}
    assert sum(occurrences.values()) == 2, "Duplication in single step must yield count 2"


def test_command_exact_matching_rejects_cross_lane_duplication() -> None:
    expected = "make security"
    mock_jobs = {
        "product-security": {"steps": [{"run": "make security"}]},
        "product-lint-unit": {"steps": [{"run": "make security"}]},
    }
    occurrences = count_exact_command_occurrences(mock_jobs, expected)
    assert occurrences == {"product-security": 1, "product-lint-unit": 1}
    assert len(occurrences) == 2, "Cross-lane occurrence must be detected as multiple owning lanes"


def test_command_exact_matching_rejects_missing_command() -> None:
    expected = "make api-contract"
    mock_jobs = {
        "product-api-contract": {"steps": [{"run": "uv sync\nmake bootstrap\n"}]},
    }
    occurrences = count_exact_command_occurrences(mock_jobs, expected)
    assert occurrences == {}, "Missing command must yield empty occurrences"



def test_ci_workflow_required_runner_prerequisites(ci_workflow: dict[str, Any]) -> None:
    """Assert required runner dependencies and environment across all parallel lanes."""
    jobs = ci_workflow["jobs"]

    # 1. product-lint-unit: PostgreSQL 16 service, INTAKE_TEST_DATABASE_URL, Node 20, npm ci, fetch-depth 0
    unit_job = jobs["product-lint-unit"]
    assert "postgres" in unit_job.get("services", {}), "product-lint-unit must have postgres service container"
    assert unit_job["services"]["postgres"].get("image") == "postgis/postgis:16-3.5"
    assert "INTAKE_TEST_DATABASE_URL" in unit_job.get("env", {}), "product-lint-unit must define INTAKE_TEST_DATABASE_URL"

    unit_steps = unit_job.get("steps", [])
    unit_checkout = next((s for s in unit_steps if "actions/checkout" in str(s.get("uses", ""))), None)
    assert unit_checkout is not None, "product-lint-unit must check out repository"
    assert unit_checkout.get("with", {}).get("fetch-depth") == 0, "product-lint-unit requires fetch-depth 0 for git ancestry tests"
    unit_node_step = next((s for s in unit_steps if "actions/setup-node" in str(s.get("uses", ""))), None)
    assert unit_node_step is not None, "product-lint-unit must set up Node"
    assert str(unit_node_step.get("with", {}).get("node-version")) == "20"
    unit_install_step = next((s for s in unit_steps if "npm ci" in str(s.get("run", ""))), None)
    assert unit_install_step is not None, "product-lint-unit must install npm dependencies via npm ci"

    # 2. product-db: PostgreSQL 16 service, INTAKE_TEST_DATABASE_URL, fetch-depth 0
    db_job = jobs["product-db"]
    assert "postgres" in db_job.get("services", {}), "product-db must have postgres service container"
    assert db_job["services"]["postgres"].get("image") == "postgis/postgis:16-3.5"
    assert "INTAKE_TEST_DATABASE_URL" in db_job.get("env", {}), "product-db must define INTAKE_TEST_DATABASE_URL"
    db_checkout = next((s for s in db_job.get("steps", []) if "actions/checkout" in str(s.get("uses", ""))), None)
    assert db_checkout is not None, "product-db must check out repository"
    assert db_checkout.get("with", {}).get("fetch-depth") == 0, "product-db requires fetch-depth 0"

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


def test_verify_change_review_scope_for_contract_test_deliverable() -> None:
    manifest = load_manifest(ROOT / "config" / "change-review-scopes.json")
    touched_paths = [
        ".github/workflows/ci.yml",
        "delivery_toolchain/governance/verify_ci_product_jobs.py",
        "tests/tooling/test_ci_product_parallel.py",
        "docs/evidence/execution-control/ODP-CI-PRODUCT-PARALLEL-JOBS-001/README.md",
        "tests/contract/test_merge_queue_batch_policy.py",
    ]
    result = classify_paths(touched_paths, manifest)
    assert result["scope"] == "product_or_mixed"
    assert result["non_tooling_paths"] == ["tests/contract/test_merge_queue_batch_policy.py"]


def test_quarterly_password_browser_is_a_required_isolated_supplement() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text())
    steps = workflow["jobs"]["product-e2e-gate"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    name = "Verify area planning drafts with password session and PostgreSQL"
    quarter = by_name[name]
    personal = by_name["Verify personal listing search with canonical API and PostgreSQL"]
    assert quarter.get("continue-on-error", False) is False
    assert quarter.get("if") is None
    assert quarter["timeout-minutes"] == personal["timeout-minutes"] == 10
    assert quarter["env"]["ODP_SCORE_BROWSER_MODE"] == "isolated-synthetic"
    assert quarter["env"]["ODP_SCORE_BROWSER_DATABASE_URL"].endswith("/odp_score_browser_quarterly_ci")
    assert quarter["env"]["ODP_SCORE_BROWSER_DATABASE_URL"] != personal["env"]["ODP_SCORE_BROWSER_DATABASE_URL"]
    assert "CREATE DATABASE odp_score_browser_quarterly_ci" in quarter["run"]
    assert "--config tests/visual/operator-area-planning-draft.config.ts" in quarter["run"]
    assert "--reporter=list,json,html" in quarter["run"]
    order = [step.get("name") for step in steps]
    assert order.index("Build personal listing search supplemental fixture") < order.index(name)
    assert order.index("Verify personal listing search with canonical API and PostgreSQL") < order.index(name)
    assert order.index(name) < order.index("Run product E2E dev merge gate")
    diagnostics = by_name["Preserve supplemental area planning3 diagnostics"]
    assert diagnostics["if"] == "always()"
    assert diagnostics["with"]["retention-days"] == 90
    assert diagnostics["with"]["path"] == "${{ runner.temp }}/product-e2e-supplemental/area-planning3/"


# --- protected checkout denial on the three unconditional PR workflows -------

PROTECTED_PR_WORKFLOWS = {
    "ci.yml": 11,
    "emgi-consumer-boundary.yml": 1,
    "tooling-scope-review-gate.yml": 1,
}
# Jobs whose gates diff, walk or prove ancestry; a shallow clone would silently weaken them.
FULL_HISTORY_JOBS = {
    "ci.yml": {"change-scope", "product-lint-unit", "product-db", "product-api-contract", "product-e2e-gate"},
    "emgi-consumer-boundary.yml": {"boundary"},
    "tooling-scope-review-gate.yml": {"classify"},
}
PR_COMPARED_COMMITS = "${{ github.event.pull_request.base.sha }} ${{ github.event.pull_request.head.sha }}"
# The jobs whose steps hand these exact commits to a diff. Every other job proves HEAD only.
COMPARED_COMMITS = {
    ("ci.yml", "change-scope"): (
        "${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha || github.event.before }} "
        "${{ github.event.pull_request.head.sha || github.event.merge_group.head_sha || github.event.after }}"
    ),
    ("emgi-consumer-boundary.yml", "boundary"): PR_COMPARED_COMMITS,
    ("tooling-scope-review-gate.yml", "classify"): PR_COMPARED_COMMITS,
}
GIT_PREFLIGHT_NAME = "Require sparse-capable Git before checkout"
DENIAL_PROOF_NAME = "Prove protected path stayed out of checkout"
DENY_PATTERNS = ["/*", "!/tests/sources"]
# `sparse-checkout` is what denies; every other input can widen, redirect or replace it.
ALLOWED_CHECKOUT_INPUTS = {"fetch-depth", "sparse-checkout", "sparse-checkout-cone-mode"}
REMATERIALIZING_COMMAND = re.compile(
    r"\bgit(?:\s+-[cC]\s+\S+|\s+-\S+)*\s+(?:checkout|switch|restore|read-tree|stash|clone|archive|pull|merge|rebase"
    r"|sparse-checkout|submodule|lfs|worktree\s+add|reset\s+--hard)\b"
    r"|\bgh\s+repo\s+clone\b|/(?:tarball|zipball)/|\bgit\s+config\b[^\n]*core\.sparse"
)


def load_pr_workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def reference_step_scripts() -> tuple[str, str]:
    steps = load_pr_workflow("tooling-scope-review-gate.yml")["jobs"]["classify"]["steps"]
    return steps[0]["run"], steps[2]["run"]


def protected_checkout_violations(name: str, workflow: dict[str, Any]) -> list[str]:
    """Every way a job could materialize the denied prefix, stated as a violation."""
    preflight_run, proof_run = reference_step_scripts()
    violations: list[str] = []
    checkouts = 0
    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        here = f"{name}:{job_name}"
        positions = [i for i, step in enumerate(steps) if "actions/checkout" in str(step.get("uses", ""))]
        checkouts += len(positions)
        if len(positions) != 1:
            violations.append(f"{here}: expected exactly one checkout, found {len(positions)}")
            continue
        at = positions[0]
        checkout = steps[at]
        if checkout["uses"] != "actions/checkout@v4":
            violations.append(f"{here}: checkout version changed: {checkout['uses']}")
        if at != 1 or steps[0].get("name") != GIT_PREFLIGHT_NAME or steps[0].get("run") != preflight_run:
            violations.append(f"{here}: exact Git preflight must be the only step before checkout")
        following = steps[at + 1] if at + 1 < len(steps) else {}
        if following.get("name") != DENIAL_PROOF_NAME or following.get("run") != proof_run:
            violations.append(f"{here}: exact denial proof must directly follow checkout")
        expected_env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        if (name, job_name) in COMPARED_COMMITS:
            expected_env["PROTECTED_COMPARE_SHAS"] = COMPARED_COMMITS[(name, job_name)]
        if following.get("env") != expected_env:
            violations.append(f"{here}: denial proof must judge repository configuration and every compared commit")
        for step in (steps[0], checkout, following):
            for softener in ("if", "continue-on-error"):
                if softener in step:
                    violations.append(f"{here}: {step.get('name') or step.get('uses')} may not set {softener}")
        inputs = checkout.get("with") or {}
        if set(inputs) - ALLOWED_CHECKOUT_INPUTS:
            violations.append(f"{here}: unsupported checkout inputs {sorted(set(inputs) - ALLOWED_CHECKOUT_INPUTS)}")
        if inputs.get("sparse-checkout-cone-mode") is not False:
            violations.append(f"{here}: sparse checkout must stay non-cone")
        if str(inputs.get("sparse-checkout") or "").split() != DENY_PATTERNS:
            violations.append(f"{here}: deny patterns changed")
        if job_name in FULL_HISTORY_JOBS[name] and inputs.get("fetch-depth") != 0:
            violations.append(f"{here}: required full history was lost")
        for step in steps:
            script = str(step.get("run") or "")
            if REMATERIALIZING_COMMAND.search(script):
                violations.append(f"{here}: {step.get('name')} can re-materialize the denied prefix")
            if re.search(r"git diff (?!--no-renames)[^\n]*--name-only", script):
                violations.append(f"{here}: {step.get('name')} rename detection would fetch bodies")
    if checkouts != PROTECTED_PR_WORKFLOWS[name]:
        violations.append(f"{name}: expected {PROTECTED_PR_WORKFLOWS[name]} checkouts, found {checkouts}")
    return violations


@pytest.mark.parametrize("name", sorted(PROTECTED_PR_WORKFLOWS))
def test_pr_workflow_denies_protected_prefix_before_every_checkout(name: str) -> None:
    assert protected_checkout_violations(name, load_pr_workflow(name)) == []


def test_compared_commits_are_the_ones_the_downstream_consumers_read() -> None:
    """The proof must vouch for the exact values handed to the later diff, not lookalikes."""
    expression = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
    for name in ("emgi-consumer-boundary.yml", "tooling-scope-review-gate.yml"):
        steps = next(iter(load_pr_workflow(name)["jobs"].values()))["steps"]
        consumer = next(step for step in steps if "BASE_SHA" in (step.get("env") or {}))
        assert steps.index(consumer) > 2, "the consumer must run after the proof"
        assert steps[2]["env"]["PROTECTED_COMPARE_SHAS"] == f"{consumer['env']['BASE_SHA']} {consumer['env']['HEAD_SHA']}"

    steps = load_pr_workflow("ci.yml")["jobs"]["change-scope"]["steps"]
    consumer = next(step for step in steps if step.get("name") == "Classify change scope")
    assert steps.index(consumer) > 2
    base, head = expression.findall(steps[2]["env"]["PROTECTED_COMPARE_SHAS"])
    env = consumer["env"]
    # One alternative per case the classifier selects from, in each endpoint.
    assert [expression.fullmatch(env[key]).group(1) for key in ("PR_BASE_SHA", "MERGE_GROUP_BASE_SHA", "PUSH_BEFORE_SHA")] == base.split(" || ")
    assert [expression.fullmatch(env[key]).group(1) for key in ("PR_HEAD_SHA", "MERGE_GROUP_HEAD_SHA", "PUSH_AFTER_SHA")] == head.split(" || ")


def test_full_history_jobs_are_all_the_jobs_that_declared_it() -> None:
    """The list above is not a hand-picked subset of what the workflows ask for."""
    for name, expected in FULL_HISTORY_JOBS.items():
        jobs = load_pr_workflow(name)["jobs"]
        declared = {
            job_name
            for job_name, job in jobs.items()
            for step in job["steps"]
            if (step.get("with") or {}).get("fetch-depth") == 0
        }
        assert declared == expected


def _mutated(mutate: Any) -> list[str]:
    workflow = load_pr_workflow("tooling-scope-review-gate.yml")
    mutate(workflow["jobs"]["classify"]["steps"])
    return protected_checkout_violations("tooling-scope-review-gate.yml", workflow)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("deny omitted", lambda s: s[1]["with"].pop("sparse-checkout")),
        ("cone mode", lambda s: s[1]["with"].update({"sparse-checkout-cone-mode": True})),
        ("cone default", lambda s: s[1]["with"].pop("sparse-checkout-cone-mode")),
        ("deny weakened", lambda s: s[1]["with"].update({"sparse-checkout": "/*\n"})),
        ("deny re-allowed", lambda s: s[1]["with"].update({"sparse-checkout": "/*\n!/tests/sources\n/tests/sources/\n"})),
        ("filter override", lambda s: s[1]["with"].update({"filter": "blob:limit=1g"})),
        ("lfs", lambda s: s[1]["with"].update({"lfs": True})),
        ("submodules", lambda s: s[1]["with"].update({"submodules": True})),
        ("other repository", lambda s: s[1]["with"].update({"repository": "someone/else"})),
        ("history lost", lambda s: s[1]["with"].pop("fetch-depth")),
        ("floating action", lambda s: s[1].update({"uses": "actions/checkout@main"})),
        ("wrong order", lambda s: s.insert(0, s.pop(1))),
        ("preflight removed", lambda s: s.pop(0)),
        ("preflight weakened", lambda s: s[0].update({"run": "git version || true"})),
        ("preflight tolerated", lambda s: s[0].update({"continue-on-error": True})),
        ("proof removed", lambda s: s.pop(2)),
        ("proof skipped", lambda s: s[2].update({"if": "false"})),
        ("proof weakened", lambda s: s[2].update({"run": "true"})),
        ("proof judged by host config", lambda s: s[2].pop("env")),
        ("compare endpoints dropped", lambda s: s[2]["env"].pop("PROTECTED_COMPARE_SHAS")),
        ("compare head only", lambda s: s[2]["env"].update({"PROTECTED_COMPARE_SHAS": "${{ github.sha }}"})),
        ("second checkout", lambda s: s.append({"uses": "actions/checkout@v4"})),
        ("sparse disabled later", lambda s: s.append({"name": "x", "run": "git sparse-checkout disable"})),
        ("later checkout", lambda s: s.append({"name": "x", "run": "git -c a=b checkout HEAD -- ."})),
        ("archive fallback", lambda s: s.append({"name": "x", "run": "git archive HEAD | tar -x"})),
        ("rest tarball", lambda s: s.append({"name": "x", "run": "gh api repos/o/r/tarball/main > a.tgz"})),
        ("clone", lambda s: s.append({"name": "x", "run": "git clone https://example.invalid/r.git"})),
        ("worktree", lambda s: s.append({"name": "x", "run": "git worktree add ../w HEAD"})),
        ("config reset", lambda s: s.append({"name": "x", "run": "git config core.sparseCheckout false"})),
        ("rename fetch", lambda s: s.append({"name": "x", "run": "git diff --name-only a b"})),
    ],
)
def test_checkout_contract_rejects_each_weakening(label: str, mutate: Any) -> None:
    assert _mutated(mutate), label


def _run_preflight(path_dir: Path) -> subprocess.CompletedProcess[str]:
    preflight, _proof = reference_step_scripts()
    for tool in ("sed", "sort", "head"):
        (path_dir / tool).symlink_to(shutil.which(tool))
    return subprocess.run(
        [shutil.which("bash"), "-e", "-c", preflight],
        env={"PATH": str(path_dir)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("reported", "accepted"),
    [
        ("git version 2.28.0", True),
        ("git version 2.43.0", True),
        ("git version 3.0.1 (Apple)", True),
        ("git version 2.43.0\nextra stdout banner", False),
        ("extra stdout banner\ngit version 2.43.0", False),
        ("git version 2.27.1", False),
        ("git version 2.9.5", False),
        ("not a version", False),
        ("", False),
    ],
)
def test_git_preflight_refuses_what_would_fall_back_to_rest(tmp_path: Path, reported: str, accepted: bool) -> None:
    fake = tmp_path / "git"
    fake.write_text(f"#!{shutil.which('bash')}\necho '{reported}'\n", encoding="utf-8")
    fake.chmod(0o755)
    assert (_run_preflight(tmp_path).returncode == 0) is accepted


def test_git_preflight_refuses_a_runner_without_git(tmp_path: Path) -> None:
    assert _run_preflight(tmp_path).returncode != 0


class _CheckoutFixture:
    """A synthetic repository checked out the way actions/checkout@v4 does.

    `dev` tracks nothing under the deny prefix, as a passing commit must;
    `bodied` is that commit plus a body under it, and `removed` then deletes it.
    """

    PREFIX = "guarded/zone"

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        (root / "gitconfig").touch()
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(root / "gitconfig"))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        self.seed = root / "seed"
        self.origin = root / "origin.git"
        self.checkout = root / "checkout"
        self.git(root, "init", "-q", "-b", "dev", str(self.seed))
        self.git(self.seed, "config", "user.name", "Test")
        self.git(self.seed, "config", "user.email", "test@example.invalid")
        (self.seed / "guarded").mkdir()
        (self.seed / "guarded" / "sibling.txt").write_text("sibling\n", encoding="utf-8")
        (self.seed / ".orchestrator").mkdir()
        shutil.copy(ROOT / ".orchestrator" / "worktree_cleanliness.py", self.seed / ".orchestrator")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-q", "-m", "clean")
        self.clean = self.rev("HEAD")
        self.git(self.seed, "checkout", "-q", "-b", "bodied")
        (self.seed / self.PREFIX).mkdir(parents=True)
        (self.seed / self.PREFIX / "body.txt").write_text("synthetic body\n", encoding="utf-8")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-q", "-m", "body")
        self.bodied = self.rev("HEAD")
        self.body_oid = self.rev(f"HEAD:{self.PREFIX}/body.txt")
        self.git(self.seed, "checkout", "-q", "-b", "removed")
        self.git(self.seed, "mv", f"{self.PREFIX}/body.txt", "moved.txt")
        with (self.seed / "moved.txt").open("a", encoding="utf-8") as stream:
            stream.write("edited after the move\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-q", "-m", "moved")
        self.removed = self.rev("HEAD")
        self.git(root, "init", "-q", "--bare", str(self.origin))
        self.git(self.origin, "config", "uploadpack.allowFilter", "true")
        self.git(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.git(self.seed, "push", "-q", str(self.origin), "dev", "bodied", "removed")

    @staticmethod
    def git(cwd: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, text=True, input=stdin, capture_output=True, check=True)

    def rev(self, ref: str) -> str:
        return self.git(self.seed, "rev-parse", ref).stdout.strip()

    def action_checkout(
        self, patterns: list[str], *, ref: str = "dev", depth: int | None = None, blob_filter: str = "blob:none"
    ) -> None:
        """Mirror git-source-provider.ts: fetch with the filter, sparse setup, then checkout."""
        self.git(self.root, "init", "-q", str(self.checkout))
        self.git(self.checkout, "remote", "add", "origin", f"file://{self.origin}")
        fetch = ["-c", "protocol.version=2", "fetch", "-q", "--no-tags", "--prune", f"--filter={blob_filter}"]
        if depth:
            fetch.append(f"--depth={depth}")
        self.git(self.checkout, *fetch, "origin", "+refs/heads/*:refs/remotes/origin/*")
        self.git(self.checkout, "config", "core.sparseCheckout", "true")
        sparse = self.checkout / ".git" / "info" / "sparse-checkout"
        sparse.parent.mkdir(exist_ok=True)
        with sparse.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(patterns) + "\n")
        self.git(self.checkout, "checkout", "-q", "--force", "-B", "current", f"refs/remotes/origin/{ref}")

    def deny_patterns(self) -> list[str]:
        return [line.replace("tests/sources", self.PREFIX) for line in DENY_PATTERNS]

    def run_proof(self, compare: str | None = None) -> subprocess.CompletedProcess[str]:
        _preflight, proof = reference_step_scripts()
        assert proof.count('denied = "tests/sources"') == 1
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        env.pop("PROTECTED_COMPARE_SHAS", None)
        if compare is not None:
            env["PROTECTED_COMPARE_SHAS"] = compare
        return subprocess.run(
            ["bash", "-e", "-c", proof.replace('denied = "tests/sources"', f'denied = "{self.PREFIX}"')],
            cwd=self.checkout,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def local_objects(self) -> str:
        return self.git(self.checkout, "cat-file", "--batch-check", "--batch-all-objects").stdout


@pytest.fixture
def checkout_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _CheckoutFixture:
    return _CheckoutFixture(tmp_path, monkeypatch)


def test_clean_commits_pass_the_proof_with_and_without_compared_endpoints(checkout_fixture: _CheckoutFixture) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns())
    assert (fixture.checkout / "guarded" / "sibling.txt").is_file(), "only the exact prefix is denied"
    assert fixture.git(fixture.checkout, "status", "--porcelain").stdout == ""
    for compare in (None, f"{fixture.clean} {fixture.clean}"):
        proof = fixture.run_proof(compare)
        assert proof.returncode == 0, proof.stdout + proof.stderr


@pytest.mark.parametrize("depth", [None, 1])
def test_tracked_body_is_never_materialized_or_downloaded_and_then_refused(
    checkout_fixture: _CheckoutFixture, depth: int | None
) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="bodied", depth=depth)
    assert not (fixture.checkout / fixture.PREFIX).exists()
    assert fixture.body_oid not in fixture.local_objects()
    proof = fixture.run_proof()
    assert proof.returncode != 0
    assert "denied path tracked" in proof.stderr
    assert fixture.body_oid not in fixture.local_objects(), "the refusal itself reads metadata only"


def test_full_history_survives_the_deny(checkout_fixture: _CheckoutFixture) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="removed")
    assert fixture.git(fixture.checkout, "rev-list", "--count", "HEAD").stdout.strip() == "3"
    assert fixture.git(fixture.checkout, "merge-base", "HEAD", "origin/dev").stdout.strip() == fixture.clean
    assert fixture.body_oid not in fixture.local_objects()


def test_base_only_body_deleted_at_head_is_refused_before_a_rename_diff_can_fetch_it(
    checkout_fixture: _CheckoutFixture,
) -> None:
    """HEAD is clean here; only the compared base tracks the body."""
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="removed")
    assert fixture.run_proof().returncode == 0, "a HEAD-only proof sees nothing wrong"

    proof = fixture.run_proof(f"{fixture.bodied} {fixture.removed}")
    assert proof.returncode != 0
    assert f"denied path tracked at {fixture.bodied}" in proof.stderr
    assert fixture.body_oid not in fixture.local_objects()

    # Why it must be refused first: the validator's own diff would download it.
    fixture.git(fixture.checkout, "diff", "--name-status", "-M", fixture.bodied, fixture.removed)
    assert fixture.body_oid in fixture.local_objects()


def test_pr_head_body_absent_from_the_merge_checkout_is_refused(checkout_fixture: _CheckoutFixture) -> None:
    """A pull_request checkout is the merge commit, which is not the PR head the job compares."""
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns())
    proof = fixture.run_proof(f"{fixture.clean} {fixture.bodied}")
    assert proof.returncode != 0
    assert f"denied path tracked at {fixture.bodied}" in proof.stderr
    assert fixture.body_oid not in fixture.local_objects()


def test_classifier_diff_without_renames_fetches_no_body(checkout_fixture: _CheckoutFixture) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="removed")
    changed = fixture.git(fixture.checkout, "diff", "--no-renames", "--name-only", fixture.bodied, fixture.removed)
    assert changed.stdout.split() == [f"{fixture.PREFIX}/body.txt", "moved.txt"]
    assert fixture.body_oid not in fixture.local_objects()


@pytest.mark.parametrize(
    "compare",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("{clean}", id="one-endpoint"),
        pytest.param("{clean} {clean} {clean}", id="three-endpoints"),
        pytest.param("HEAD HEAD", id="symbolic"),
        pytest.param("{short} {clean}", id="abbreviated"),
        pytest.param("{clean} " + "0" * 40, id="all-zero"),
        pytest.param("{clean} " + "a" * 40, id="unknown-commit"),
    ],
)
def test_unresolved_compared_commits_fail_closed(checkout_fixture: _CheckoutFixture, compare: str) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns())
    proof = fixture.run_proof(compare.format(clean=fixture.clean, short=fixture.clean[:12]))
    assert proof.returncode != 0, proof.stdout


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda f: f.git(f.checkout, "config", "core.sparseCheckout", "false"), id="sparse-config-off"),
        pytest.param(lambda f: f.git(f.checkout, "config", "core.sparseCheckoutCone", "true"), id="cone-on"),
        pytest.param(
            lambda f: f.git(
                f.checkout, "update-index", "--add", "--cacheinfo", f"100644,{f.rev('HEAD:guarded/sibling.txt')},{f.PREFIX}/x"
            ),
            id="index-entry-added",
        ),
        pytest.param(lambda f: (f.checkout / f.PREFIX).mkdir(parents=True), id="prefix-path-created"),
        pytest.param(
            lambda f: (f.checkout / ".git" / "info" / "sparse-checkout").write_text("/*\n"), id="patterns-replaced"
        ),
        pytest.param(lambda f: f.git(f.checkout, "config", "remote.origin.partialclonefilter", "blob:limit=1g"), id="filter-changed"),
        pytest.param(lambda f: f.git(f.checkout, "config", "filter.x.clean", "cat"), id="unsafe-content-filter"),
        pytest.param(
            lambda f: f.git(
                f.checkout, "update-index", "--add", "--replace", "--cacheinfo", f"120000,{f.rev('HEAD:guarded/sibling.txt')},guarded"
            ),
            id="unsafe-index-ancestor",
        ),
    ],
)
def test_denial_proof_fails_closed(checkout_fixture: _CheckoutFixture, damage: Any) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns())
    assert fixture.run_proof().returncode == 0
    damage(fixture)
    proof = fixture.run_proof()
    assert proof.returncode != 0, proof.stdout


def test_disabling_sparse_rematerializes_and_is_refused(checkout_fixture: _CheckoutFixture) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="bodied")
    fixture.git(fixture.checkout, "sparse-checkout", "disable")
    assert (fixture.checkout / fixture.PREFIX / "body.txt").is_file(), "the synthetic hazard the deny exists for"
    assert fixture.run_proof().returncode != 0


def test_denial_proof_fails_without_a_git_repository(checkout_fixture: _CheckoutFixture) -> None:
    """What a REST archive fallback would have left behind."""
    fixture = checkout_fixture
    fixture.checkout.mkdir()
    shutil.copytree(fixture.seed / ".orchestrator", fixture.checkout / ".orchestrator")
    assert fixture.run_proof().returncode != 0


def _commit_with_entry(fixture: _CheckoutFixture, entry: str) -> str:
    tree = fixture.git(fixture.seed, "mktree", stdin=entry + "\n").stdout.strip()
    commit = fixture.git(fixture.seed, "commit-tree", tree, "-p", fixture.clean, "-m", "unsafe").stdout.strip()
    fixture.git(fixture.seed, "push", "-q", str(fixture.origin), f"+{commit}:refs/heads/dev")
    return commit


def test_denial_proof_refuses_unsafe_ancestor_and_gitlink_trees(checkout_fixture: _CheckoutFixture) -> None:
    fixture = checkout_fixture
    tooling = fixture.git(fixture.seed, "ls-tree", fixture.clean, ".orchestrator").stdout.strip()
    blob = fixture.git(fixture.seed, "hash-object", "-w", "--stdin", stdin="elsewhere").stdout.strip()
    _commit_with_entry(fixture, f"{tooling}\n120000 blob {blob}\tguarded")
    fixture.action_checkout(fixture.deny_patterns())
    assert "ancestor_escape" in fixture.run_proof().stderr

    shutil.rmtree(fixture.checkout)
    inner = fixture.git(fixture.seed, "mktree", stdin=f"160000 commit {fixture.clean}\tzone\n").stdout.strip()
    _commit_with_entry(fixture, f"{tooling}\n040000 tree {inner}\tguarded")
    fixture.action_checkout(fixture.deny_patterns())
    assert "protected_entry_escape" in fixture.run_proof().stderr
