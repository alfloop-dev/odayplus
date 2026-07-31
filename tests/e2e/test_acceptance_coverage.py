"""Executable acceptance registry and real-result receipt validation."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.e2e.product_e2e_receipt import (
    CANONICAL_SPEC_INVENTORY,
    DELETED_SPEC_REFERENCES,
    E2E_SCENARIOS,
    EVIDENCE_COMMIT_ALLOWLIST,
    EXPECTED_CANONICAL_SPEC_COUNT,
    EXPECTED_PLAYWRIGHT_TEST_COUNT,
    RAW_PLAYWRIGHT_PATH,
    RAW_PYTEST_PATH,
    RECEIPT_PATH,
    SCHEMA_VERSION,
    bind_scenarios,
    canonical_json_bytes,
    parse_playwright_payload,
    seal_normalized,
    sha256_bytes,
    source_identity,
    validate_raw_artifact,
    validate_receipt_packet,
    verify_evidence_relationship,
)
from scripts.e2e.run_python_e2e_tests import run_python_tests

ROOT = Path(__file__).resolve().parents[2]


def validate_acceptance_scenarios_and_inventory(root_path: Path) -> list[str]:
    """Validate registry, canonical inventory, collection count, and real packet."""
    errors: list[str] = []
    scenario_ids = [scenario.scenario_id for scenario in E2E_SCENARIOS]
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("acceptance registry contains duplicate scenario ids")

    for scenario in E2E_SCENARIOS:
        for ref in scenario.automation_refs:
            if any(deleted in ref for deleted in DELETED_SPEC_REFERENCES):
                errors.append(
                    f"{scenario.scenario_id} cites a deleted spec reference: {ref}"
                )
            if scenario.is_manual:
                continue
            if ref.count("::") != 1:
                errors.append(
                    f"{scenario.scenario_id} must use one exact normalized test id: {ref}"
                )
                continue
            file_name, exact_title = ref.split("::", 1)
            target = root_path / file_name
            if not target.is_file():
                errors.append(
                    f"{scenario.scenario_id} exact test file is missing: {file_name}"
                )
            elif exact_title not in target.read_text(encoding="utf-8"):
                errors.append(
                    f"{scenario.scenario_id} exact test title is missing: {ref}"
                )

    actual_inventory = tuple(
        sorted(
            str(path.relative_to(root_path)).replace(os.sep, "/")
            for path in (root_path / "tests/e2e").glob("*.spec.ts")
            if path.is_file()
        )
    )
    if actual_inventory != tuple(sorted(CANONICAL_SPEC_INVENTORY)):
        errors.append(
            "canonical Playwright spec inventory differs from the explicit 16-file registry"
        )

    proc = subprocess.run(
        ["npx", "playwright", "test", "--list", "--project=chromium"],
        cwd=root_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        errors.append(f"Playwright --list exited {proc.returncode}: {proc.stderr.strip()}")
    else:
        match = re.search(
            r"Total:\s*(\d+)\s*tests\s*in\s*(\d+)\s*files", proc.stdout
        )
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

    errors.extend(validate_receipt_packet(root_path))
    return errors


def _counts(results: list[dict[str, Any]], runner: str) -> dict[str, int]:
    counts = {
        "total_specs": (
            len({item["test_id"].split("::", 1)[0] for item in results})
            if runner == "playwright"
            else 0
        ),
        "total_tests": len(results),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "interrupted": 0,
        "flaky": 0,
        "malformed": 0,
    }
    fields = {
        "passed": "passed",
        "failed": "failed",
        "skipped": "skipped",
        "timedOut": "timed_out",
        "interrupted": "interrupted",
        "flaky": "flaky",
        "malformed": "malformed",
    }
    for result in results:
        counts[fields[result["status"]]] += 1
    return counts


def _artifact(
    runner: str,
    results: list[dict[str, Any]],
    *,
    source: dict[str, str] | None = None,
    exit_code: int = 0,
    integrity_errors: list[str] | None = None,
) -> dict[str, Any]:
    payload = {"raw": runner}
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner": runner,
        "source": source or source_identity(ROOT),
        "run": {
            "command": f"run {runner}",
            "version": "test-version",
            "started_at": "2026-07-31T00:00:00Z",
            "ended_at": "2026-07-31T00:01:00Z",
            "exit_code": exit_code,
            "environment": {"name": "mutation"},
        },
        "payload": payload,
        "payload_sha256": sha256_bytes(canonical_json_bytes(payload)),
        "counts": _counts(results, runner),
        "results": results,
        "integrity_errors": integrity_errors or [],
    }
    return seal_normalized(artifact, "normalized_artifact_sha256")


def _write_packet_artifact(root: Path, runner: str, artifact: dict[str, Any]) -> str:
    relative = RAW_PLAYWRIGHT_PATH if runner == "playwright" else RAW_PYTEST_PATH
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(artifact, indent=2).encode()
    path.write_bytes(raw)
    return sha256_bytes(raw)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "acceptance@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Acceptance Test"],
        cwd=path,
        check=True,
    )


def _commit(path: Path, relative: str, content: str, message: str) -> None:
    target = path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", relative], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)


def test_all_qa03_scenarios_are_registered_once_with_exact_refs() -> None:
    ids = [scenario.scenario_id for scenario in E2E_SCENARIOS]
    assert len(ids) == 16
    assert len(ids) == len(set(ids))
    for scenario in E2E_SCENARIOS:
        if scenario.priority == "P0":
            assert not scenario.is_manual
        for ref in scenario.automation_refs:
            assert not any(deleted in ref for deleted in DELETED_SPEC_REFERENCES)
            if not scenario.is_manual:
                assert ref.count("::") == 1
                assert ref.startswith("tests/")


def test_no_deleted_specs_referenced_and_inventory_consistent() -> None:
    assert validate_acceptance_scenarios_and_inventory(ROOT) == []


@pytest.mark.parametrize(
    "status,exit_code",
    [
        ("failed", 1),
        ("skipped", 0),
        ("timedOut", 1),
        ("interrupted", 1),
        ("flaky", 0),
        ("malformed", 0),
    ],
)
def test_raw_runner_rejects_every_non_passing_terminal_status(
    status: str, exit_code: int
) -> None:
    artifact = _artifact(
        "playwright",
        [
            {
                "test_id": "tests/e2e/example.spec.ts::exact title",
                "status": status,
            }
        ],
        exit_code=exit_code,
    )
    errors = validate_raw_artifact(artifact, "playwright")
    assert any("non-passing" in error for error in errors)


def test_raw_runner_rejects_zero_tests_and_contradictory_counts() -> None:
    artifact = _artifact("playwright", [])
    artifact["counts"]["passed"] = 107
    seal_normalized(artifact, "normalized_artifact_sha256")
    errors = validate_raw_artifact(artifact, "playwright")
    assert any("counts contradict" in error for error in errors)
    assert any("zero tests" in error for error in errors)


def test_playwright_payload_counts_unique_spec_files_and_exact_results() -> None:
    payload = {
        "suites": [
            {
                "file": "example.spec.ts",
                "specs": [
                    {
                        "file": "example.spec.ts",
                        "title": "first exact title",
                        "tests": [
                            {
                                "status": "expected",
                                "expectedStatus": "passed",
                                "projectName": "chromium",
                                "results": [
                                    {
                                        "status": "passed",
                                        "duration": 1,
                                        "retry": 0,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "file": "example.spec.ts",
                        "title": "second exact title",
                        "tests": [
                            {
                                "status": "expected",
                                "expectedStatus": "passed",
                                "projectName": "chromium",
                                "results": [
                                    {
                                        "status": "passed",
                                        "duration": 1,
                                        "retry": 0,
                                    }
                                ],
                            }
                        ],
                    },
                ],
                "suites": [],
            }
        ],
        "stats": {"expected": 2, "skipped": 0, "unexpected": 0, "flaky": 0},
    }
    results, counts, errors = parse_playwright_payload(payload)
    assert errors == []
    assert counts["total_specs"] == 1
    assert counts["total_tests"] == 2
    assert results[0]["test_id"] == "tests/e2e/example.spec.ts::first exact title"


def test_playwright_payload_rejects_zero_suite_and_malformed_stats() -> None:
    _results, _counts, errors = parse_playwright_payload(
        {"suites": [], "stats": {"expected": "107"}}
    )
    assert any("zero suites" in error for error in errors)

    payload = {
        "suites": [
            {
                "file": "example.spec.ts",
                "specs": [
                    {
                        "title": "exact title",
                        "tests": [
                            {
                                "status": "expected",
                                "expectedStatus": "passed",
                                "projectName": "chromium",
                                "results": [{"status": "passed", "retry": 0}],
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        ],
        "stats": {"expected": 107, "skipped": -1, "unexpected": 0, "flaky": 0},
    }
    _results, _counts, errors = parse_playwright_payload(payload)
    assert any("contradicts parsed count" in error for error in errors)
    assert any("must be a non-negative integer" in error for error in errors)


def test_raw_runner_rejects_duplicate_exact_id_and_tampered_hashes() -> None:
    result = {
        "test_id": "tests/e2e/example.spec.ts::exact title",
        "status": "passed",
    }
    artifact = _artifact("playwright", [result, result.copy()])
    artifact["payload"]["tampered"] = True
    errors = validate_raw_artifact(artifact, "playwright")
    assert any("normalized artifact hash mismatch" in error for error in errors)
    assert any("payload hash mismatch" in error for error in errors)
    assert any("duplicate normalized test ids" in error for error in errors)


def test_scenario_binding_requires_exact_id_not_substring() -> None:
    playwright = _artifact(
        "playwright",
        [
            {
                "test_id": (
                    "tests/e2e/operator-network-scoring.spec.ts::"
                    "SiteScore Lab renders GO/WAIT/REJECT scorecards with conditions and reasons EXTRA"
                ),
                "status": "passed",
            }
        ],
    )
    pytest_artifact = _artifact("pytest", [])
    _results, errors = bind_scenarios(
        {"playwright": playwright, "pytest": pytest_artifact},
        {"playwright": "a" * 64, "pytest": "b" * 64},
    )
    assert any(
        "E2E-EXP-001 missing exact playwright result" in error for error in errors
    )


def test_receipt_rejects_missing_or_duplicate_scenario_results(tmp_path: Path) -> None:
    playwright = _artifact(
        "playwright",
        [
            {
                "test_id": "tests/e2e/example.spec.ts::exact title",
                "status": "passed",
            }
        ],
    )
    pytest_artifact = _artifact(
        "pytest",
        [
            {
                "test_id": "tests/security/example.py::test_exact",
                "status": "passed",
            }
        ],
    )
    pw_hash = _write_packet_artifact(tmp_path, "playwright", playwright)
    py_hash = _write_packet_artifact(tmp_path, "pytest", pytest_artifact)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tested_source": source_identity(ROOT),
        "artifacts": [
            {"runner": "playwright", "sha256": pw_hash},
            {"runner": "pytest", "sha256": py_hash},
        ],
        "runner_counts": {
            "playwright": playwright["counts"],
            "pytest": pytest_artifact["counts"],
        },
        "scenario_results": [
            {"scenario_id": "E2E-EXP-001"},
            {"scenario_id": "E2E-EXP-001"},
        ],
        "validation_errors": [],
        "exit_code": 0,
        "status": "passed",
    }
    seal_normalized(receipt, "normalized_receipt_sha256")
    receipt_path = tmp_path / RECEIPT_PATH
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path)
    assert any("missing or duplicate scenario results" in error for error in errors)


def test_evidence_only_child_is_allowed_but_source_change_is_rejected(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", "v1\n", "source")
    source = source_identity(tmp_path)
    _commit(
        tmp_path,
        RAW_PLAYWRIGHT_PATH,
        "{}\n",
        "evidence-only child",
    )
    proof, errors = verify_evidence_relationship(
        tmp_path, source, allow_worktree_evidence=False
    )
    assert errors == []
    assert proof["relation"] == "evidence_only_descendant"

    _commit(tmp_path, "source.py", "v2\n", "source changed after test")
    _proof, errors = verify_evidence_relationship(
        tmp_path, source, allow_worktree_evidence=False
    )
    assert any("non-evidence paths" in error for error in errors)


def test_intervening_source_change_is_rejected_even_if_reverted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", "v1\n", "source")
    source = source_identity(tmp_path)
    _commit(tmp_path, "source.py", "v2\n", "intervening source change")
    _commit(tmp_path, "source.py", "v1\n", "revert source")
    _proof, errors = verify_evidence_relationship(
        tmp_path, source, allow_worktree_evidence=False
    )
    assert any("non-evidence paths" in error for error in errors)


def test_stale_or_mismatched_source_tree_is_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", "v1\n", "source")
    source = source_identity(tmp_path)
    source["tree_sha"] = "0" * 40
    _proof, errors = verify_evidence_relationship(
        tmp_path, source, allow_worktree_evidence=False
    )
    assert any("tested tree mismatch" in error for error in errors)


def test_python_runner_propagates_pytest_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pytest, "main", lambda *args, **kwargs: 1)
    status = run_python_tests(output_path=tmp_path / "raw_pytest_results.json")
    assert status != 0
    artifact = json.loads(
        (tmp_path / "raw_pytest_results.json").read_text(encoding="utf-8")
    )
    assert artifact["run"]["exit_code"] == 1


def test_evidence_allowlist_is_explicit_and_narrow() -> None:
    assert EVIDENCE_COMMIT_ALLOWLIST == {
        RAW_PLAYWRIGHT_PATH,
        RAW_PYTEST_PATH,
        RECEIPT_PATH,
    }
