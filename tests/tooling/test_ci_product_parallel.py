"""Tests for product CI parallel jobs refactoring and aggregator verification.

Validates that:
1. .github/workflows/ci.yml splits product checks into isolated parallel runners.
2. Acceptance commands have one owning lane; redundant file executions stay retired.
3. Required runner dependencies (PostgreSQL service/env, Node 20, npm ci, fetch-depth 0)
   are configured for each owning lane.
4. The product job aggregates results fail-closed under all circumstances.
5. delivery_toolchain/governance/verify_ci_product_jobs.py enforces all required
   acceptance criteria (missing lane, malformed lane, missing result, unknown result,
   failure, cancelled, unexpected skip, tooling skip) for both scopes.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from copy import deepcopy
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


def pytest_addopts_reporting_errors(workflow: dict[str, Any]) -> list[str]:
    """Keep inherited pytest options from narrowing the required acceptance commands."""
    scopes = [("workflow", workflow)]
    for name, job in workflow.get("jobs", {}).items():
        scopes.append((name, job))
        scopes.extend((f"{name}/step{index}", step) for index, step in enumerate(job.get("steps", [])))
    errors = []
    for name, scope in scopes:
        env = scope.get("env", {})
        if "PYTEST_ADDOPTS" not in env:
            continue
        value = env["PYTEST_ADDOPTS"]
        if value == "":
            continue
        try:
            options = shlex.split(value) if isinstance(value, str) else []
        except ValueError:
            options = []
        if len(options) != 1 or not re.fullmatch(
            r"--junitxml=\.odp_data/ci/[A-Za-z0-9][A-Za-z0-9_.-]*\.xml", options[0]
        ):
            errors.append(f"{name}: PYTEST_ADDOPTS must contain only a literal CI JUnit report path")
    return errors


TOOLING_COMMAND = (
    'uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling infra'
    " tests/e2e/test_acceptance_coverage.py"
)
PRODUCT_UNIT_COMMAND = (
    'uv run pytest -m "not requires_live_env and not performance" --ignore=tests/tooling'
    " --ignore=tests/e2e/test_acceptance_coverage.py tests modules apps shared models -n auto"
)
RECEIPT_UNIT_FILE = "tests/e2e/test_acceptance_coverage.py"
# A synthetic stand-in for every selection root either lane names. Each file
# holds one trivial case; no repository test or source body is collected.
SYNTHETIC_SELECTION_TREE = (
    ".orchestrator/test_orchestrator_unit.py",
    "delivery_toolchain/test_toolchain_unit.py",
    "scripts/test_script_unit.py",
    "tests/tooling/test_tooling_unit.py",
    "infra/test_infra_unit.py",
    RECEIPT_UNIT_FILE,
    "tests/e2e/test_other_e2e_unit.py",
    "tests/unit/test_product_unit.py",
    "modules/test_module_unit.py",
    "apps/test_app_unit.py",
    "shared/test_shared_unit.py",
    "models/test_model_unit.py",
)


def lane_pytest_argv(jobs: dict[str, Any], lane: str, step_name: str) -> list[str]:
    """Return the one `uv run pytest` argv a lane step really executes."""
    step, = [step for step in jobs[lane]["steps"] if step.get("name") == step_name]
    commands = [command for command in parse_executable_commands_from_script(step["run"])
                if command[:3] == ["uv", "run", "pytest"]]
    assert len(commands) == 1, f"{lane}/{step_name}: expected one pytest invocation, found {commands}"
    return commands[0][3:]


def synthetic_collected_files(tree: Path, pytest_args: list[str]) -> list[str]:
    """Collect the actual lane argv against the synthetic tree and report selected files."""
    if not (tree / "pytest.ini").exists():
        (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for relative in SYNTHETIC_SELECTION_TREE:
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def test_selected():\n    pass\n", encoding="utf-8")
    args = list(pytest_args)
    if "-n" in args:  # worker fan-out does not change which files are selected
        index = args.index("-n")
        del args[index:index + 2]
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=tree, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sorted({line.split("::", 1)[0] for line in result.stdout.splitlines() if "::" in line})


def receipt_unit_lane_owners(jobs: dict[str, Any], tree: Path) -> dict[str, list[str]]:
    lanes = {
        "orchestrator": lane_pytest_argv(jobs, "orchestrator", "Test orchestrator and infrastructure code"),
        "product-lint-unit": lane_pytest_argv(jobs, "product-lint-unit", "Test product code"),
    }
    return {lane: synthetic_collected_files(tree, args) for lane, args in lanes.items()}


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
    """Assert each required acceptance command is present in exactly one lane."""
    jobs = ci_workflow["jobs"]

    # Required acceptance selections, each with one owning lane. Official
    # real-estate PostgreSQL cases belong to the complete database selection.
    expected_command_lanes = {
        "uv run ruff check tests modules apps shared models solver pipelines infra": "product-lint-unit",
        PRODUCT_UNIT_COMMAND: "product-lint-unit",
        'uv run pytest -m "requires_live_env" tests/contract tests/ops tests/integration -x --tb=short -vv -n2 --dist=loadfile --max-worker-restart=0': "product-db",
        "make api-contract": "product-api-contract",
        "make security": "product-security",
        "make node-check": "product-node",
    }

    official_file = "tests/integration/test_official_real_estate_postgresql.py"
    for job in jobs.values():
        for step in job.get("steps", []):
            for command in parse_executable_commands_from_script(step.get("run", "")):
                if command[:3] == ["uv", "run", "pytest"]:
                    assert official_file not in [arg.removeprefix("./") for arg in command[3:]], (
                        "Official PostgreSQL cases are covered by the complete SQL selection; "
                        "a dedicated file invocation runs the same cases twice."
                    )

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


@pytest.mark.parametrize("duplicate_command", [
    "uv run pytest tests/integration/test_official_real_estate_postgresql.py",
    "uv run pytest -vv ./tests/integration/test_official_real_estate_postgresql.py",
])
def test_official_postgresql_cases_reject_duplicate_ci_runs(
    ci_workflow: dict[str, Any], duplicate_command: str,
) -> None:
    workflow = deepcopy(ci_workflow)
    workflow["jobs"]["product-db"]["steps"].append({"run": duplicate_command})
    with pytest.raises(AssertionError, match="same cases twice"):
        test_ci_workflow_command_exact_single_ownership(workflow)


def test_delivery_tooling_keeps_a_required_unconditional_owner(ci_workflow: dict[str, Any]) -> None:
    """Moving tooling out of product must never make its only owner optional."""
    jobs = ci_workflow["jobs"]
    owner = jobs["orchestrator"]
    assert owner.get("if") is None
    assert owner.get("continue-on-error", False) is False
    assert count_exact_command_occurrences(jobs, TOOLING_COMMAND) == {"orchestrator": 1}
    step, = [step for step in owner["steps"] if step.get("name") == "Test orchestrator and infrastructure code"]
    assert step.get("if") is None
    assert step.get("continue-on-error", False) is False
    policy = json.loads((ROOT / ".github/branch-protection/policy.json").read_text())
    assert "orchestrator" in policy["required_status_checks"]


def test_receipt_unit_file_has_one_owner_in_tooling_and_mixed_scope(
    ci_workflow: dict[str, Any], tmp_path: Path,
) -> None:
    """The unconditional tooling lane owns the receipt unit file; product never repeats it."""
    selected = receipt_unit_lane_owners(ci_workflow["jobs"], tmp_path)
    # Tooling-only scope runs just the orchestrator lane, which must select it.
    assert RECEIPT_UNIT_FILE in selected["orchestrator"]
    assert "tests/e2e/test_other_e2e_unit.py" not in selected["orchestrator"]
    # Mixed scope runs both lanes; every selected file still has one owner.
    assert RECEIPT_UNIT_FILE not in selected["product-lint-unit"]
    assert "tests/e2e/test_other_e2e_unit.py" in selected["product-lint-unit"]
    assert not any(path.startswith("tests/tooling/") for path in selected["product-lint-unit"])
    assert not set(selected["orchestrator"]) & set(selected["product-lint-unit"])
    assert set(selected["orchestrator"]) | set(selected["product-lint-unit"]) == set(SYNTHETIC_SELECTION_TREE)


@pytest.mark.parametrize("damage", ["tooling-omits", "product-repeats"])
def test_receipt_unit_selection_rejects_omission_or_duplicate_run(
    ci_workflow: dict[str, Any], tmp_path: Path, damage: str,
) -> None:
    workflow = deepcopy(ci_workflow)
    jobs = workflow["jobs"]
    if damage == "tooling-omits":
        step, = [step for step in jobs["orchestrator"]["steps"]
                 if step.get("name") == "Test orchestrator and infrastructure code"]
        step["run"] = step["run"].replace(" " + RECEIPT_UNIT_FILE, "")
    else:
        step, = [step for step in jobs["product-lint-unit"]["steps"] if step.get("name") == "Test product code"]
        step["run"] = step["run"].replace(" --ignore=" + RECEIPT_UNIT_FILE, "")
    with pytest.raises(AssertionError):
        test_receipt_unit_file_has_one_owner_in_tooling_and_mixed_scope(workflow, tmp_path)


def test_ci_workflow_pytest_addopts_are_reporting_only(ci_workflow: dict[str, Any]) -> None:
    assert pytest_addopts_reporting_errors(ci_workflow) == []


def test_heatzone_latency_keeps_one_required_isolated_owner(ci_workflow: dict[str, Any]) -> None:
    node = "tests/integration/test_heatzone_flow.py::test_heatzone_api_scores_batch_and_returns_map_results_within_fixture_target"
    command = (f'uv run pytest -m performance {node} '
               '--junitxml="performance-reports/heatzone_fixture_attempt_${attempt}.xml"')
    jobs = ci_workflow["jobs"]
    assert count_exact_command_occurrences(jobs, command) == {"performance-gate": 1}
    assert count_exact_command_occurrences(jobs, "uv run pytest -m performance tests/performance") == {"performance-gate": 1}
    gate = jobs["performance-gate"]
    assert gate["needs"] == "change-scope"
    assert "needs.change-scope.outputs.scope != 'development_tooling'" in gate["if"]
    assert gate.get("continue-on-error", False) is False
    step, = [step for step in gate["steps"] if node in step.get("run", "")]
    assert step.get("if") is None and step.get("continue-on-error", False) is False
    assert "for attempt in 1 2 3; do" in step["run"]
    assert command + " || status=$?" in step["run"]
    assert 'exit "${status}"' in step["run"]
    artifact, = [step for step in gate["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert artifact["if"] == "always()" and artifact["with"]["path"] == "performance-reports/"
    assert "performance-gate" in jobs["automatic-dev-release"]["needs"]
    tree = ast.parse((ROOT / node.split("::")[0]).read_text())
    function, = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == node.split("::")[1]]
    assert [ast.unparse(item) for item in function.decorator_list] == ["pytest.mark.performance"]


@pytest.mark.parametrize("damage", ["missing-selector", "duplicate-owner", "optional-step"])
def test_heatzone_latency_owner_rejects_omitted_duplicated_or_optional_gate(
    ci_workflow: dict[str, Any], damage: str,
) -> None:
    workflow = deepcopy(ci_workflow)
    step = next(step for step in workflow["jobs"]["performance-gate"]["steps"]
                if "heatzone_fixture_attempt_" in step.get("run", ""))
    if damage == "missing-selector":
        step["run"] = "echo omitted"
    elif damage == "duplicate-owner":
        workflow["jobs"]["product-lint-unit"]["steps"].append(deepcopy(step))
    else:
        step["continue-on-error"] = True
    with pytest.raises(AssertionError):
        test_heatzone_latency_keeps_one_required_isolated_owner(workflow)


def test_ci_workflow_retains_main_database_report_after_failure(ci_workflow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps = ci_workflow["jobs"]["product-db"]["steps"]
    database = next(step for step in steps if step.get("name") == "Test database contracts, migrations and schema gates")
    assert database["env"]["PYTEST_ADDOPTS"] == "--junitxml=.odp_data/ci/database-contracts.xml"
    assert database.get("if") is None and database.get("continue-on-error", False) is False
    artifact = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert artifact.get("if") == "always()"
    assert artifact["with"]["if-no-files-found"] == "error"
    assert ".odp_data/ci/database-contracts.xml" in artifact["with"]["path"].splitlines()
    verification = next(step for step in steps if step.get("name") == "Test model admission and delete propagation against PostgreSQL")
    code = verification["run"].split("uv run python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    reports = [
        ".odp_data/ci/database-contracts.xml",
        ".odp_data/ci/current-model-admission.xml",
        ".odp_data/ci/delete-propagation.xml",
    ]
    monkeypatch.chdir(tmp_path)
    passed = "<testsuite><testcase /></testsuite>"
    for report in reports:
        target = Path(report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(passed)
    # Exercise the existing common guard with synthetic reporting controls.
    # This validates the CI gate, not execution of any SQL business scenario.
    exec(compile(code, "ci-native-sql-evidence", "exec"), {})
    for report in reports:
        for outcome in ("skipped", "failure", "error"):
            Path(report).write_text(f"<testsuite><testcase><{outcome} /></testcase></testsuite>")
            with pytest.raises(SystemExit, match=outcome):
                exec(compile(code, "ci-native-sql-evidence", "exec"), {})
        Path(report).write_text("<testsuite />")
        with pytest.raises(SystemExit, match="did not execute"):
            exec(compile(code, "ci-native-sql-evidence", "exec"), {})
        Path(report).write_text(passed)


def test_required_ci_keeps_native_sql_diagnostics_opt_in(ci_workflow: dict[str, Any]) -> None:
    key = "ODP_NATIVE_SQL_PROFILE"
    assert key not in ci_workflow.get("env", {})
    for job in ci_workflow["jobs"].values():
        assert key not in job.get("env", {})
        for step in job.get("steps", []):
            assert key not in step.get("env", {})
    artifact = next(step for step in ci_workflow["jobs"]["product-db"]["steps"]
                    if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert artifact["if"] == "always()"
    assert ".odp_data/ci/native-intake-command-profile.jsonl" in artifact["with"]["path"].splitlines()


@pytest.mark.parametrize("level", ["workflow", "job", "step"])
@pytest.mark.parametrize("options", [
    "-k native_intake", "--deselect=tests/integration/test_native_intake_command_closure_postgresql.py",
    "--maxfail=1", "-p no:some_plugin", "${{ inputs.pytest_options }}", "@pytest-options.txt",
    "--junitxml=.odp_data/ci/database-contracts.xml -k narrowed", None, ["--junitxml=report.xml"],
])
def test_ci_pytest_addopts_reject_selection_plugins_and_dynamic_values(level: str, options: Any) -> None:
    step: dict[str, Any] = {"run": "uv run pytest tests/integration"}
    job: dict[str, Any] = {"steps": [step]}
    workflow = {"jobs": {"product-db": job}}
    scope = {"workflow": workflow, "job": job, "step": step}[level]
    scope["env"] = {"PYTEST_ADDOPTS": options}
    assert len(pytest_addopts_reporting_errors(workflow)) == 1


@pytest.mark.parametrize("options", ["", "--junitxml=.odp_data/ci/database-contracts.xml"])
def test_ci_pytest_addopts_accept_empty_or_literal_reporting(options: str) -> None:
    workflow = {"env": {"PYTEST_ADDOPTS": options}, "jobs": {"product-db": {
        "env": {"PYTEST_ADDOPTS": options}, "steps": [{"env": {"PYTEST_ADDOPTS": options}}],
    }}}
    assert pytest_addopts_reporting_errors(workflow) == []


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
    expected = 'uv run pytest -m "requires_live_env" tests/contract tests/ops tests/integration -x --tb=short -vv -n2 --dist=loadfile --max-worker-restart=0'
    # Mutated with an additional ignore flag
    mutated_script = (
        'uv run pytest -m "requires_live_env" \\\n'
        '  tests/contract tests/ops tests/integration -x --tb=short -vv -n2 --dist=loadfile --max-worker-restart=0 --ignore=tests/contract'
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


# --- protected checkout denial on every existing workflow checkout -------------

# Every existing workflow that checks out this repository, and how many times.
PROTECTED_CHECKOUT_WORKFLOWS = {
    "assisted-intake-design-validation.yml": 1,
    "ci.yml": 11,
    "deploy-dev.yml": 6,
    "emgi-consumer-boundary.yml": 1,
    "merge-queue-review-gate.yml": 1,
    "promote-dev-to-main.yml": 1,
    "tooling-scope-review-gate.yml": 1,
}
PROTECTED_CHECKOUT_TOTAL = 22
# Jobs whose gates diff, walk or prove ancestry; a shallow clone would silently weaken them.
FULL_HISTORY_JOBS = {
    "assisted-intake-design-validation.yml": {"validate-contract-package"},
    "ci.yml": {"change-scope", "product-lint-unit", "product-db", "product-api-contract", "product-e2e-gate"},
    "deploy-dev.yml": {"release_phase", "build", "admission", "automatic_admission", "deploy", "staging_closeout"},
    "emgi-consumer-boundary.yml": {"boundary"},
    "merge-queue-review-gate.yml": {"review-gate"},
    "promote-dev-to-main.yml": {"promote"},
    "tooling-scope-review-gate.yml": {"classify"},
}
PR_COMPARED_COMMITS = "${{ github.event.pull_request.base.sha }} ${{ github.event.pull_request.head.sha }}"
# The jobs whose steps hand these exact commits to a diff. Every other job proves HEAD only.
COMPARED_COMMITS = {
    ("assisted-intake-design-validation.yml", "validate-contract-package"): (
        "${{ github.event.pull_request.base.sha || github.sha }} ${{ github.event.pull_request.head.sha || github.sha }}"
    ),
    ("ci.yml", "change-scope"): (
        "${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha || github.event.before }} "
        "${{ github.event.pull_request.head.sha || github.event.merge_group.head_sha || github.event.after }}"
    ),
    ("deploy-dev.yml", "admission"): "${{ inputs.release_sha }} ${{ github.sha }}",
    ("emgi-consumer-boundary.yml", "boundary"): PR_COMPARED_COMMITS,
    ("tooling-scope-review-gate.yml", "classify"): PR_COMPARED_COMMITS,
}
# The exact trusted or dispatched ref each job checks out; every other job takes the event default.
CHECKOUT_REFS = {
    ("deploy-dev.yml", "release_phase"): "${{ inputs.phase == 'auto' && github.sha || inputs.release_sha }}",
    ("deploy-dev.yml", "build"): "${{ inputs.release_sha }}",
    ("deploy-dev.yml", "admission"): "${{ github.sha }}",
    ("deploy-dev.yml", "automatic_admission"): "${{ inputs.release_sha }}",
    ("deploy-dev.yml", "deploy"): "${{ inputs.release_sha }}",
    ("deploy-dev.yml", "staging_closeout"): "${{ inputs.release_sha }}",
    ("merge-queue-review-gate.yml", "review-gate"): "${{ github.event.merge_group.head_sha }}",
    ("promote-dev-to-main.yml", "promote"): "${{ github.event.workflow_run.head_sha }}",
}
def _head_equality_script(variable: str, label: str) -> str:
    """The approved exact-SHA assertion form: HEAD must equal one named, pinned commit."""
    return (
        'head_sha="$(git rev-parse HEAD)"\n'
        f'if [ "${{head_sha}}" != "${{{variable}}}" ]; then\n'
        f'  echo "Error: checked out HEAD (${{head_sha}}) does not match {label} (${{{variable}}})." >&2\n'
        "  exit 1\n"
        "fi\n"
    )


RELEASE_SHA_INPUT = "${{ inputs.release_sha }}"
# The one HEAD identity assertion allowed between a ref checkout and its proof:
# (step name, exact approved script, compared variable, the pinned commit it must hold,
#  whether the step itself binds that variable or inherits it from the job).
# Nothing but this exact equality may run before the proof, so no extra read or
# command can touch a denied body and no weakened comparison can pass.
IDENTITY_ASSERTIONS = {
    ("deploy-dev.yml", "release_phase"): (
        "Assert exact release SHA is checked out",
        "set -euo pipefail\n" + _head_equality_script("RELEASE_SHA", "inputs.release_sha"),
        "RELEASE_SHA", RELEASE_SHA_INPUT, "step",
    ),
    ("deploy-dev.yml", "build"): (
        "Assert exact release SHA is checked out",
        "set -euo pipefail\n" + _head_equality_script("ODAY_RELEASE_SHA", "ODAY_RELEASE_SHA"),
        "ODAY_RELEASE_SHA", RELEASE_SHA_INPUT, "job",
    ),
    ("deploy-dev.yml", "admission"): (
        "Assert the dispatch event SHA is checked out",
        "set -euo pipefail\n"
        'if [[ ! "${EVENT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then\n'
        '  echo "Error: dispatch event SHA (${EVENT_SHA}) is not an exact 40-character commit SHA." >&2\n'
        "  exit 1\n"
        "fi\n" + _head_equality_script("EVENT_SHA", "the dispatch event SHA"),
        "EVENT_SHA", "${{ github.sha }}", "step",
    ),
    ("deploy-dev.yml", "automatic_admission"): (
        "Assert automatic admission checkout identity",
        'test "$(git rev-parse HEAD)" = "${RELEASE_SHA}"',
        "RELEASE_SHA", RELEASE_SHA_INPUT, "step",
    ),
    ("deploy-dev.yml", "deploy"): (
        "Assert exact release SHA is checked out",
        "set -euo pipefail\n" + _head_equality_script("ODAY_RELEASE_SHA", "ODAY_RELEASE_SHA"),
        "ODAY_RELEASE_SHA", RELEASE_SHA_INPUT, "job",
    ),
    ("deploy-dev.yml", "staging_closeout"): (
        "Assert exact release SHA is checked out",
        "set -euo pipefail\n" + _head_equality_script("ODAY_RELEASE_SHA", "ODAY_RELEASE_SHA"),
        "ODAY_RELEASE_SHA", RELEASE_SHA_INPUT, "job",
    ),
}
# Keys a guarded script step may carry; anything else (shell, working-directory,
# id-driven outputs, ...) could change what the exact script runs or judges.
GUARDED_SCRIPT_KEYS = {"name", "run", "env"}
# Guarded steps also inherit workflow/job `env` and `defaults.run`. Plain data
# variables (release SHA, service names, PG*) are needed downstream; these names
# instead decide what runs before or around the exact scripts: shell startup
# files and functions, command lookup, loaders, interpreter hooks and Git's own
# repository/config/object selection.
INHERITED_EXECUTION_ENV = re.compile(
    r"(?:BASH_ENV|ENV|SHELLOPTS|BASHOPTS|BASH_FUNC_\S*|BASH_XTRACEFD|PS4|IFS|CDPATH|GLOBIGNORE|EXECIGNORE"
    r"|PATH|HOME|XDG_CONFIG_HOME|LD_\S*|DYLD_\S*|PYTHON\S*|GIT_\S*|PERL5?LIB|PERL5OPT|RUBYOPT|NODE_OPTIONS)"
)
# Inherited env may only carry literal data names; anything else (an
# expression, a non-string YAML key) cannot be judged against the list above.
PLAIN_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
GIT_PREFLIGHT_NAME = "Require sparse-capable Git before checkout"
DENIAL_PROOF_NAME = "Prove protected path stayed out of checkout"
# The shared protected body domain of .orchestrator/worktree_cleanliness.py:
# the root prefix and every embedded copy of it, plus the named asset.
DENY_PATTERNS = ["/*", "!**/tests/sources/**", "!**/test_listing_production_asset.py*"]
# `sparse-checkout` is what denies and `ref` is pinned per job above; every
# other input can widen, redirect or replace the deny.
ALLOWED_CHECKOUT_INPUTS = {"fetch-depth", "ref", "sparse-checkout", "sparse-checkout-cone-mode"}
REMATERIALIZING_COMMAND = re.compile(
    r"\bgit(?:\s+-[cC]\s+\S+|\s+-\S+)*\s+(?:checkout|switch|restore|read-tree|stash|clone|archive|pull|fetch|merge|rebase"
    r"|sparse-checkout|submodule|lfs|worktree\s+add|reset\s+--hard)\b"
    r"|\bgh\s+repo\s+clone\b|/(?:tarball|zipball)/|\bgit\s+config\b[^\n]*core\.sparse"
)


def load_pr_workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def reference_step_scripts() -> tuple[str, str]:
    steps = load_pr_workflow("tooling-scope-review-gate.yml")["jobs"]["classify"]["steps"]
    return steps[0]["run"], steps[2]["run"]


def _identity_assertion_violations(
    here: str, job: dict[str, Any], step: dict[str, Any], expected: tuple[str, str, str, str, str] | None
) -> list[str]:
    if expected is None:
        return [f"{here}: exact denial proof must directly follow checkout"]
    name, script, variable, pinned, binder = expected
    violations = []
    if step.get("name") != name or step.get("run") != script:
        violations.append(f"{here}: only the exact HEAD identity assertion may precede the denial proof")
    if set(step) - GUARDED_SCRIPT_KEYS - {"if", "continue-on-error"}:
        violations.append(f"{here}: identity assertion may not set {sorted(set(step) - GUARDED_SCRIPT_KEYS)}")
    # The compared value is the exact trusted/dispatched commit, bound where the approved form binds it.
    step_env = step.get("env")
    if binder == "step":
        if step_env != {variable: pinned}:
            violations.append(f"{here}: identity assertion must compare HEAD with {pinned}")
    elif step_env is not None or not isinstance(job.get("env"), dict) or job["env"].get(variable) != pinned:
        violations.append(f"{here}: identity assertion must compare HEAD with {pinned}")
    return violations


def _inherited_execution_violations(here: str, workflow: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """Workflow/job settings every guarded step inherits must not change how its exact script runs."""
    violations = []
    for scope, source in (("workflow", workflow), ("job", job)):
        # Any defaults value (mapping, expression string or other shape) is a
        # run default the guarded steps would inherit; none is inspected away.
        defaults = source.get("defaults")
        if defaults is not None and defaults != {}:
            violations.append(f"{here}: guarded steps inherit {scope} defaults {defaults!r}")
        env = source.get("env")
        if env is None:
            continue
        if not isinstance(env, dict):
            # A whole-env expression decides its names only at run time.
            violations.append(f"{here}: guarded steps inherit an opaque {scope} env {env!r}")
            continue
        unsafe = sorted(
            repr(key)
            for key in env
            if not isinstance(key, str) or not PLAIN_ENV_NAME.fullmatch(key) or INHERITED_EXECUTION_ENV.fullmatch(key)
        )
        if unsafe:
            violations.append(f"{here}: guarded steps inherit {scope} execution env {unsafe}")
    if "container" in job:
        violations.append(f"{here}: guarded steps inherit a job container")
    return violations


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
        violations.extend(_inherited_execution_violations(here, workflow, job))
        if checkout["uses"] != "actions/checkout@v4":
            violations.append(f"{here}: checkout version changed: {checkout['uses']}")
        if at != 1 or steps[0].get("name") != GIT_PREFLIGHT_NAME or steps[0].get("run") != preflight_run:
            violations.append(f"{here}: exact Git preflight must be the only step before checkout")
        guarded = [steps[0], checkout]
        following = steps[at + 1] if at + 1 < len(steps) else {}
        if following.get("name") != DENIAL_PROOF_NAME:
            violations.extend(
                _identity_assertion_violations(here, job, following, IDENTITY_ASSERTIONS.get((name, job_name)))
            )
            guarded.append(following)
            following = steps[at + 2] if at + 2 < len(steps) else {}
        elif (name, job_name) in IDENTITY_ASSERTIONS:
            violations.append(f"{here}: exact HEAD identity assertion was dropped")
        if following.get("name") != DENIAL_PROOF_NAME or following.get("run") != proof_run:
            violations.append(f"{here}: exact denial proof must directly follow checkout")
        guarded.append(following)
        expected_env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        if (name, job_name) in COMPARED_COMMITS:
            expected_env["PROTECTED_COMPARE_SHAS"] = COMPARED_COMMITS[(name, job_name)]
        if following.get("env") != expected_env:
            violations.append(f"{here}: denial proof must judge repository configuration and every compared commit")
        for step in guarded:
            for softener in ("if", "continue-on-error"):
                if softener in step:
                    violations.append(f"{here}: {step.get('name') or step.get('uses')} may not set {softener}")
        for step, keys in ((steps[0], {"name", "run"}), (following, GUARDED_SCRIPT_KEYS)):
            if set(step) - keys - {"if", "continue-on-error"}:
                violations.append(f"{here}: {step.get('name')} may not set {sorted(set(step) - keys)}")
        inputs = checkout.get("with") or {}
        if set(inputs) - ALLOWED_CHECKOUT_INPUTS:
            violations.append(f"{here}: unsupported checkout inputs {sorted(set(inputs) - ALLOWED_CHECKOUT_INPUTS)}")
        if inputs.get("ref") != CHECKOUT_REFS.get((name, job_name)):
            violations.append(f"{here}: checkout ref changed: {inputs.get('ref')!r}")
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
    if checkouts != PROTECTED_CHECKOUT_WORKFLOWS[name]:
        violations.append(f"{name}: expected {PROTECTED_CHECKOUT_WORKFLOWS[name]} checkouts, found {checkouts}")
    return violations


@pytest.mark.parametrize("name", sorted(PROTECTED_CHECKOUT_WORKFLOWS))
def test_workflow_denies_protected_prefix_before_every_checkout(name: str) -> None:
    assert protected_checkout_violations(name, load_pr_workflow(name)) == []


def test_every_existing_checkout_is_covered() -> None:
    """No workflow may check this repository out outside the validated set."""
    found = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        jobs = (yaml.safe_load(path.read_text()) or {}).get("jobs") or {}
        count = sum(
            "actions/checkout" in str(step.get("uses", ""))
            for job in jobs.values()
            for step in (job.get("steps") or [])
        )
        if count:
            found[path.name] = count
    assert found == PROTECTED_CHECKOUT_WORKFLOWS
    assert sum(found.values()) == PROTECTED_CHECKOUT_TOTAL


def test_deny_patterns_are_the_shared_domain_policy() -> None:
    """The workflows deny exactly what the shared cleanliness policy names."""
    sys.path.insert(0, str(ROOT / ".orchestrator"))
    try:
        import worktree_cleanliness as wc
    finally:
        sys.path.remove(str(ROOT / ".orchestrator"))
    assert DENY_PATTERNS == list(wc.protected_body_sparse_patterns())
    _preflight, proof = reference_step_scripts()
    assert proof.count('denied = "tests/sources"') == 1
    assert "wc.protected_body_checkout_refusal(root, prefixes=(denied,), require_sparse=True)" in proof
    assert "wc.protected_body_sparse_patterns((denied,))" in proof


def _proof_step(name: str, job: str) -> tuple[list[dict[str, Any]], int]:
    steps = load_pr_workflow(name)["jobs"][job]["steps"]
    return steps, next(i for i, step in enumerate(steps) if step.get("name") == DENIAL_PROOF_NAME)


def test_compared_commits_are_the_ones_the_downstream_consumers_read() -> None:
    """The proof must vouch for the exact values handed to the later diff, not lookalikes."""
    expression = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
    for name in ("emgi-consumer-boundary.yml", "tooling-scope-review-gate.yml"):
        job = next(iter(load_pr_workflow(name)["jobs"]))
        steps, proof = _proof_step(name, job)
        consumer = next(step for step in steps if "BASE_SHA" in (step.get("env") or {}))
        assert steps.index(consumer) > proof, "the consumer must run after the proof"
        assert steps[proof]["env"]["PROTECTED_COMPARE_SHAS"] == f"{consumer['env']['BASE_SHA']} {consumer['env']['HEAD_SHA']}"

    steps, proof = _proof_step("ci.yml", "change-scope")
    consumer = next(step for step in steps if step.get("name") == "Classify change scope")
    assert steps.index(consumer) > proof
    base, head = expression.findall(steps[proof]["env"]["PROTECTED_COMPARE_SHAS"])
    env = consumer["env"]
    # One alternative per case the classifier selects from, in each endpoint.
    assert [expression.fullmatch(env[key]).group(1) for key in ("PR_BASE_SHA", "MERGE_GROUP_BASE_SHA", "PUSH_BEFORE_SHA")] == base.split(" || ")
    assert [expression.fullmatch(env[key]).group(1) for key in ("PR_HEAD_SHA", "MERGE_GROUP_HEAD_SHA", "PUSH_AFTER_SHA")] == head.split(" || ")

    # Admission compares the dispatched candidate with the trusted event commit it checked out.
    steps, proof = _proof_step("deploy-dev.yml", "admission")
    consumer = next(step for step in steps if step.get("name") == "Validate candidate ancestry against the dispatch event SHA")
    assert steps.index(consumer) > proof
    assert steps[proof]["env"]["PROTECTED_COMPARE_SHAS"] == f"{consumer['env']['RELEASE_SHA']} {consumer['env']['EVENT_SHA']}"

    # The intake gate reviews the PR head against the PR base; a dispatch reviews its own commit.
    steps, proof = _proof_step("assisted-intake-design-validation.yml", "validate-contract-package")
    consumers = [step for step in steps if "REVIEWED_COMMIT" in (step.get("env") or {})]
    assert consumers and all(steps.index(step) > proof for step in consumers)
    base, head = expression.findall(steps[proof]["env"]["PROTECTED_COMPARE_SHAS"])
    for step in consumers:
        env = step["env"]
        assert env["REVIEWED_COMMIT"] == env["CURRENT_PR_HEAD"] == f"${{{{ {head} }}}}"
        assert expression.fullmatch(env["BASE_COMMIT"]).group(1).split(" || ")[0] == base.split(" || ")[0]


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


def test_pinned_refs_and_identity_assertions_are_all_the_jobs_that_declared_them() -> None:
    """Exact dispatch/trusted refs and their HEAD assertions are not a hand-picked subset."""
    declared_refs = {}
    for name in PROTECTED_CHECKOUT_WORKFLOWS:
        for job_name, job in load_pr_workflow(name)["jobs"].items():
            for step in job.get("steps", []):
                if "actions/checkout" in str(step.get("uses", "")) and "ref" in (step.get("with") or {}):
                    declared_refs[(name, job_name)] = step["with"]["ref"]
    assert declared_refs == CHECKOUT_REFS
    assert set(IDENTITY_ASSERTIONS) <= set(CHECKOUT_REFS)


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
        ("body fetch", lambda s: s.append({"name": "x", "run": "git -c a=b fetch -q --filter=blob:none --stdin origin < oids"})),
        ("worktree", lambda s: s.append({"name": "x", "run": "git worktree add ../w HEAD"})),
        ("config reset", lambda s: s.append({"name": "x", "run": "git config core.sparseCheckout false"})),
        ("rename fetch", lambda s: s.append({"name": "x", "run": "git diff --name-only a b"})),
    ],
)
def test_checkout_contract_rejects_each_weakening(label: str, mutate: Any) -> None:
    assert _mutated(mutate), label


def _mutated_job(name: str, job: str, mutate: Any) -> list[str]:
    workflow = load_pr_workflow(name)
    mutate(workflow["jobs"][job]["steps"])
    return protected_checkout_violations(name, workflow)


@pytest.mark.parametrize(
    ("label", "name", "job", "mutate"),
    [
        ("admission ref dropped", "deploy-dev.yml", "admission", lambda s: s[1]["with"].pop("ref")),
        ("admission ref redirected", "deploy-dev.yml", "admission",
         lambda s: s[1]["with"].update({"ref": "${{ inputs.release_sha }}"})),
        ("auto ref untrusted", "deploy-dev.yml", "release_phase",
         lambda s: s[1]["with"].update({"ref": "${{ inputs.release_sha }}"})),
        ("admission history lost", "deploy-dev.yml", "admission", lambda s: s[1]["with"].pop("fetch-depth")),
        ("admission compare dropped", "deploy-dev.yml", "admission", lambda s: s[3]["env"].pop("PROTECTED_COMPARE_SHAS")),
        ("admission compare head only", "deploy-dev.yml", "admission",
         lambda s: s[3]["env"].update({"PROTECTED_COMPARE_SHAS": "${{ github.sha }}"})),
        ("identity assertion dropped", "deploy-dev.yml", "build", lambda s: s.pop(2)),
        ("identity assertion runs python", "deploy-dev.yml", "deploy",
         lambda s: s[2].update({"run": "python3 -c 'print(1)'\ngit rev-parse HEAD"})),
        ("identity assertion renamed", "deploy-dev.yml", "staging_closeout", lambda s: s[2].update({"name": "Anything"})),
        ("identity assertion skipped", "deploy-dev.yml", "automatic_admission", lambda s: s[2].update({"if": "false"})),
        ("proof after identity removed", "deploy-dev.yml", "deploy", lambda s: s.pop(3)),
        ("deploy proof skipped", "deploy-dev.yml", "deploy", lambda s: s[3].update({"continue-on-error": True})),
        ("deploy deny weakened", "deploy-dev.yml", "deploy", lambda s: s[1]["with"].update({"sparse-checkout": "/*\n"})),
        ("deploy rest tarball", "deploy-dev.yml", "deploy",
         lambda s: s.append({"name": "x", "run": "gh api repos/o/r/tarball/main > a.tgz"})),
        ("queue ref dropped", "merge-queue-review-gate.yml", "review-gate", lambda s: s[1]["with"].pop("ref")),
        ("queue proof removed", "merge-queue-review-gate.yml", "review-gate", lambda s: s.pop(2)),
        ("promote ref redirected", "promote-dev-to-main.yml", "promote",
         lambda s: s[1]["with"].update({"ref": "${{ github.sha }}"})),
        ("promote body fetch", "promote-dev-to-main.yml", "promote",
         lambda s: s.append({"name": "x", "run": "git fetch origin main"})),
        ("intake compare dropped", "assisted-intake-design-validation.yml", "validate-contract-package",
         lambda s: s[2]["env"].pop("PROTECTED_COMPARE_SHAS")),
        ("intake history lost", "assisted-intake-design-validation.yml", "validate-contract-package",
         lambda s: s[1]["with"].pop("fetch-depth")),
        ("intake ref injected", "assisted-intake-design-validation.yml", "validate-contract-package",
         lambda s: s[1]["with"].update({"ref": "main"})),
        ("ci lane deny omitted", "ci.yml", "product-db", lambda s: s[1]["with"].pop("sparse-checkout")),
        ("ci lane history lost", "ci.yml", "product-e2e-gate", lambda s: s[1]["with"].pop("fetch-depth")),
    ],
)
def test_every_workflow_rejects_each_weakening(label: str, name: str, job: str, mutate: Any) -> None:
    assert _mutated_job(name, job, mutate), label


def _approved_assertion(job: str) -> str:
    return IDENTITY_ASSERTIONS[("deploy-dev.yml", job)][1]


@pytest.mark.parametrize(
    ("label", "job", "mutate"),
    [
        # The exact-SHA equality must survive, not just its name or a substring of it.
        ("equality missing", "build", lambda j: j["steps"][2].update({"run": "git rev-parse HEAD"})),
        ("no assertion", "build", lambda j: j["steps"][2].update({"run": "echo git rev-parse HEAD"})),
        ("body read before proof", "build",
         lambda j: j["steps"][2].update({"run": "git rev-parse HEAD\ngit show HEAD:guarded/zone/body.txt"})),
        ("body read appended", "build",
         lambda j: j["steps"][2].update({"run": _approved_assertion("build") + "cat guarded/zone/body.txt\n"})),
        ("extra command prepended", "deploy",
         lambda j: j["steps"][2].update({"run": "git ls-tree -r HEAD guarded\n" + _approved_assertion("deploy")})),
        ("equality inverted", "staging_closeout",
         lambda j: j["steps"][2].update({"run": _approved_assertion("staging_closeout").replace("!=", "==")})),
        ("failure tolerated", "build",
         lambda j: j["steps"][2].update({"run": _approved_assertion("build").replace("exit 1", "exit 0")})),
        ("self comparison", "automatic_admission",
         lambda j: j["steps"][2].update({"run": 'test "$(git rev-parse HEAD)" = "$(git rev-parse HEAD)"\n'})),
        ("exact sha shape dropped", "admission",
         lambda j: j["steps"][2].update({"run": "set -euo pipefail\n" + _head_equality_script("EVENT_SHA", "the dispatch event SHA")})),
        # The compared value must be the exact trusted/dispatched commit.
        ("job value redirected", "build", lambda j: j["env"].update({"ODAY_RELEASE_SHA": "${{ github.sha }}"})),
        ("job value overridden by step", "deploy",
         lambda j: j["steps"][2].update({"env": {"ODAY_RELEASE_SHA": "${{ github.event.after }}"}})),
        ("step value redirected", "release_phase",
         lambda j: j["steps"][2]["env"].update({"RELEASE_SHA": "${{ github.sha }}"})),
        ("step value dropped", "automatic_admission", lambda j: j["steps"][2].pop("env")),
        ("dispatch value redirected", "admission",
         lambda j: j["steps"][2]["env"].update({"EVENT_SHA": "${{ inputs.release_sha }}"})),
        ("startup script injected", "release_phase",
         lambda j: j["steps"][2]["env"].update({"BASH_ENV": "guarded/zone/body.txt"})),
        # Anything that changes how the exact script runs.
        ("shell replaced", "build", lambda j: j["steps"][2].update({"shell": "cat guarded/zone/body.txt; bash {0}"})),
        ("directory moved", "deploy", lambda j: j["steps"][2].update({"working-directory": "guarded/zone"})),
        ("action replaces script", "build", lambda j: j["steps"][2].update({"uses": "actions/github-script@v7"})),
    ],
)
def test_identity_assertion_rejects_each_mutation(label: str, job: str, mutate: Any) -> None:
    """In-memory mutations only: nothing here is executed and no body is read."""
    workflow = load_pr_workflow("deploy-dev.yml")
    assert protected_checkout_violations("deploy-dev.yml", workflow) == []
    mutate(workflow["jobs"][job])
    violations = protected_checkout_violations("deploy-dev.yml", workflow)
    assert any(f"deploy-dev.yml:{job}: identity assertion" in v or "exact HEAD identity assertion" in v
               for v in violations), (label, violations)


def test_guarded_script_steps_take_no_execution_overrides() -> None:
    for index, extra in ((0, {"shell": "sh -c 'cat guarded/zone/body.txt; sh {0}'"}),
                         (0, {"env": {"BASH_ENV": "guarded/zone/body.txt"}}),
                         (2, {"working-directory": "guarded"})):
        workflow = load_pr_workflow("tooling-scope-review-gate.yml")
        workflow["jobs"]["classify"]["steps"][index].update(extra)
        assert protected_checkout_violations("tooling-scope-review-gate.yml", workflow), extra


_STARTUP_READ = "$(git show HEAD:guarded/zone/body.txt; echo /dev/null)"
_SHELL_READ = 'bash -c "git show HEAD:guarded/zone/body.txt; bash {0}"'


def _set(container: dict[str, Any], key: str, value: dict[str, Any]) -> None:
    container.setdefault(key, {}).update(value)


@pytest.mark.parametrize(
    ("label", "name", "job", "mutate"),
    [
        # The guarded preflight, identity assertion and proof all inherit these.
        ("workflow startup env", "deploy-dev.yml", "build", lambda w: _set(w, "env", {"BASH_ENV": _STARTUP_READ})),
        ("job startup env", "deploy-dev.yml", "build", lambda w: _set(w["jobs"]["build"], "env", {"BASH_ENV": _STARTUP_READ})),
        ("workflow default shell", "deploy-dev.yml", "build",
         lambda w: _set(w, "defaults", {"run": {"shell": _SHELL_READ}})),
        ("job default shell", "deploy-dev.yml", "build",
         lambda w: _set(w["jobs"]["build"], "defaults", {"run": {"shell": _SHELL_READ}})),
        ("workflow default directory", "tooling-scope-review-gate.yml", "classify",
         lambda w: _set(w, "defaults", {"run": {"working-directory": "guarded/zone"}})),
        ("job default directory", "ci.yml", "product-db",
         lambda w: _set(w["jobs"]["product-db"], "defaults", {"run": {"working-directory": "guarded"}})),
        ("workflow sh startup", "promote-dev-to-main.yml", "promote", lambda w: _set(w, "env", {"ENV": _STARTUP_READ})),
        ("job command lookup", "deploy-dev.yml", "deploy",
         lambda w: _set(w["jobs"]["deploy"], "env", {"PATH": "guarded/zone:/usr/bin"})),
        ("job exported function", "merge-queue-review-gate.yml", "review-gate",
         lambda w: _set(w["jobs"]["review-gate"], "env", {"BASH_FUNC_git%%": "() { cat guarded/zone/body.txt; }"})),
        ("workflow git redirect", "emgi-consumer-boundary.yml", "boundary",
         lambda w: _set(w, "env", {"GIT_DIR": "/elsewhere/.git"})),
        ("job loader", "assisted-intake-design-validation.yml", "validate-contract-package",
         lambda w: _set(w["jobs"]["validate-contract-package"], "env", {"LD_PRELOAD": "guarded/zone/x.so"})),
        ("job interpreter hook", "ci.yml", "orchestrator",
         lambda w: _set(w["jobs"]["orchestrator"], "env", {"PYTHONPATH": "guarded/zone"})),
        ("job container", "ci.yml", "change-scope",
         lambda w: w["jobs"]["change-scope"].update({"container": {"image": "x", "env": {"BASH_ENV": _STARTUP_READ}}})),
    ],
)
def test_guarded_steps_reject_inherited_execution_overrides(label: str, name: str, job: str, mutate: Any) -> None:
    """In-memory mutations only: nothing here is executed and no body is read."""
    workflow = load_pr_workflow(name)
    assert protected_checkout_violations(name, workflow) == []
    mutate(workflow)
    violations = protected_checkout_violations(name, workflow)
    assert any(v.startswith(f"{name}:{job}: guarded steps inherit") for v in violations), (label, violations)


_WHOLE_ENV = "${{ fromJSON(vars.GUARDED_ENV) }}"


@pytest.mark.parametrize(
    ("label", "scope", "key", "value"),
    [
        # Shapes the name list cannot judge are refused, not skipped or crashed on.
        ("workflow whole-env expression", "workflow", "env", _WHOLE_ENV),
        ("job whole-env expression", "job", "env", _WHOLE_ENV),
        ("job env list", "job", "env", ["BASH_ENV=/dev/null"]),
        ("workflow expression env key", "workflow", "env", {"${{ vars.NAME }}": _STARTUP_READ}),
        ("job non-string env key", "job", "env", {True: "x"}),
        ("job spaced env key", "job", "env", {"BASH_ENV ": _STARTUP_READ}),
        ("workflow defaults expression", "workflow", "defaults", "${{ fromJSON(vars.GUARDED_DEFAULTS) }}"),
        ("job falsey non-mapping defaults", "job", "defaults", []),
        ("job defaults run expression", "job", "defaults", {"run": "${{ fromJSON(vars.RUN) }}"}),
        ("workflow empty defaults run", "workflow", "defaults", {"run": {}}),
    ],
)
def test_guarded_steps_reject_opaque_inherited_context(label: str, scope: str, key: str, value: Any) -> None:
    """In-memory mutations only: nothing here is executed and no body is read."""
    name, job = "deploy-dev.yml", "build"
    workflow = load_pr_workflow(name)
    assert protected_checkout_violations(name, workflow) == []
    (workflow if scope == "workflow" else workflow["jobs"][job])[key] = value
    violations = protected_checkout_violations(name, workflow)
    assert any(v.startswith(f"{name}:{job}: guarded steps inherit") for v in violations), (label, violations)


def test_inherited_data_env_and_pinned_commit_stay_accepted() -> None:
    """The necessary workflow/job data env (including the pinned release SHA) is not an override."""
    workflow = load_pr_workflow("deploy-dev.yml")
    assert workflow["env"] and workflow["jobs"]["build"]["env"]["ODAY_RELEASE_SHA"]
    _set(workflow["jobs"]["build"], "env", {"ODP_EXTRA_LABEL": "x"})
    _set(workflow, "env", {"PGHOST": "localhost"})
    assert protected_checkout_violations("deploy-dev.yml", workflow) == []


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
    `withdrawn` continues `removed` with a history that leaves HEAD again: one blob
    tracked under both the prefix and an allowed path, and one permitted body.
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
        self.git(self.seed, "checkout", "-q", "-b", "withdrawn")
        (self.seed / self.PREFIX).mkdir(parents=True, exist_ok=True)
        (self.seed / "open").mkdir()
        self.history_paths = {
            f"{self.PREFIX}/body.txt",
            f"{self.PREFIX}/shared.txt",
            "open/shared-copy.txt",
            "open/permitted.txt",
        }
        for path, text in (
            (f"{self.PREFIX}/shared.txt", "shared synthetic body\n"),
            ("open/shared-copy.txt", "shared synthetic body\n"),
            ("open/permitted.txt", "permitted history\n"),
        ):
            (self.seed / path).write_text(text, encoding="utf-8")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-q", "-m", "history")
        self.published = self.rev("HEAD")
        self.shared_oid = self.rev(f"HEAD:{self.PREFIX}/shared.txt")
        assert self.shared_oid == self.rev("HEAD:open/shared-copy.txt")
        self.permitted_oid = self.rev("HEAD:open/permitted.txt")
        self.git(self.seed, "rm", "-q", "-r", self.PREFIX, "open")
        self.git(self.seed, "commit", "-q", "-m", "withdrawn")
        self.withdrawn = self.rev("HEAD")
        self.git(root, "init", "-q", "--bare", str(self.origin))
        self.git(self.origin, "config", "uploadpack.allowFilter", "true")
        self.git(self.origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.git(self.seed, "push", "-q", str(self.origin), "dev", "bodied", "removed", "withdrawn")

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

    def local_objects(self, repo: Path | None = None) -> str:
        return self.git(repo or self.checkout, "cat-file", "--batch-check", "--batch-all-objects").stdout

    def lose_guarded_tree(self) -> None:
        """Leave the protected ancestor's tree unreadable, here and from any promisor."""
        tree = self.git(self.checkout, "rev-parse", "HEAD:guarded").stdout.strip()
        self.git(self.checkout, "remote", "set-url", "origin", str(self.root / "gone.git"))
        store = self.checkout / ".git" / "objects"
        for pack in sorted((store / "pack").glob("*.pack")):
            kept = self.root / pack.name
            shutil.move(pack, kept)
            with kept.open("rb") as stream:
                subprocess.run(["git", "unpack-objects", "-q"], cwd=self.checkout, stdin=stream, check=True)
        for leftover in (store / "pack").iterdir():
            leftover.unlink()
        (store / tree[:2] / tree[2:]).unlink()


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


def test_local_clone_consumers_read_history_metadata_without_any_body_fetch(
    checkout_fixture: _CheckoutFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both clone forms the product suites use work from the partial checkout as it stands."""
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="withdrawn")
    proof = fixture.run_proof()
    assert proof.returncode == 0, proof.stdout + proof.stderr
    before = fixture.local_objects()
    unfetched = (fixture.body_oid, fixture.shared_oid, fixture.permitted_oid)
    assert not [oid for oid in unfetched if oid in before]

    shared = fixture.root / "shared"
    fixture.git(fixture.root, "clone", "-q", "--shared", str(fixture.checkout), str(shared))
    for repo in (fixture.checkout, shared):
        changed = fixture.git(repo, "log", "--no-renames", "--format=", "--name-only", f"{fixture.clean}..HEAD")
        assert fixture.history_paths <= set(changed.stdout.split())
        fixture.git(repo, "show", "-s", "--no-renames", "--format=%H", "HEAD")
    assert fixture.git(shared, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames").stdout == ""
    with monkeypatch.context() as served_without_lazy_fetch:
        served_without_lazy_fetch.setenv("GIT_NO_LAZY_FETCH", "1")
        shallow = fixture.root / "shallow"
        fixture.git(fixture.root, "clone", "-q", "--depth", "1", f"file://{fixture.checkout}", str(shallow))
    assert fixture.git(shallow, "rev-parse", "HEAD").stdout.strip() == fixture.withdrawn

    assert fixture.local_objects() == before
    for repo in (shared, shallow):
        assert not (repo / fixture.PREFIX).exists()
        objects = fixture.local_objects(repo)
        assert not [oid for oid in unfetched if oid in objects]


def test_permitted_history_body_is_on_demand_and_never_brings_a_protected_one(
    checkout_fixture: _CheckoutFixture,
) -> None:
    fixture = checkout_fixture
    fixture.action_checkout(fixture.deny_patterns(), ref="withdrawn")
    shared = fixture.root / "shared"
    fixture.git(fixture.root, "clone", "-q", "--shared", str(fixture.checkout), str(shared))
    refused = subprocess.run(
        ["git", "cat-file", "-e", fixture.permitted_oid], cwd=shared, capture_output=True, check=False
    )
    assert refused.returncode != 0, "a local clone has no promisor to serve history from"

    read = fixture.git(fixture.checkout, "cat-file", "blob", f"{fixture.published}:open/permitted.txt")
    assert read.stdout == "permitted history\n"
    objects = fixture.local_objects()
    assert fixture.permitted_oid in objects
    assert fixture.body_oid not in objects
    assert fixture.shared_oid not in objects, "a blob also tracked under the deny stays out"
    assert not (fixture.checkout / fixture.PREFIX).exists()


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
        pytest.param(lambda f: f.lose_guarded_tree(), id="unreadable-protected-ancestor-tree"),
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
