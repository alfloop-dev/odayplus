"""Executable acceptance registry and real-result receipt validation."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from delivery_toolchain.e2e.generate_product_e2e_receipt import generate_receipt
from delivery_toolchain.e2e.product_e2e_receipt import (
    CANONICAL_SPEC_INVENTORY,
    DELETED_SPEC_REFERENCES,
    E2E_SCENARIOS,
    EVIDENCE_COMMIT_ALLOWLIST,
    EXPECTED_PLAYWRIGHT_TEST_COUNT,
    PYTEST_NODE_IDS,
    RAW_PLAYWRIGHT_PATH,
    RAW_PYTEST_PATH,
    RECEIPT_PATH,
    SCHEMA_VERSION,
    bind_scenarios,
    canonical_json_bytes,
    git_value,
    parse_playwright_payload,
    parse_pytest_payload,
    seal_normalized,
    sha256_bytes,
    source_identity,
    validate_acceptance_scenarios_and_inventory,
    validate_evidence_proof_at_generation,
    validate_raw_artifact,
    validate_receipt_packet,
    verify_evidence_relationship,
)
from delivery_toolchain.e2e.run_python_e2e_tests import run_python_tests

ROOT = Path(__file__).resolve().parents[2]
_RENAME_BODY = "".join(f"harmless synthetic line {index}\n" for index in range(40))


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
    payload: dict[str, Any] | None = None,
    exit_code: int = 0,
    integrity_errors: list[str] | None = None,
) -> dict[str, Any]:
    raw_payload = {"raw": runner} if payload is None else payload
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
        "payload": raw_payload,
        "payload_sha256": sha256_bytes(canonical_json_bytes(raw_payload)),
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


def _clone_packet_repo(
    path: Path,
    *,
    source: Path | None = None,
    filtered_shared: bool = False,
) -> Path:
    if source is not None:
        # Local clones share immutable Git objects; each test still owns its refs,
        # index and mutable packet files without copying the full object database.
        subprocess.run(
            ["git", "clone", "-q", "--shared", str(source), str(path)],
            check=True,
        )
        return path

    origin = path / "origin" if filtered_shared else path
    origin.mkdir(parents=True, exist_ok=True)
    _init_repo(origin)
    _commit(origin, "product.py", _RENAME_BODY + "prior-only\n", "prior product revision")
    _commit(origin, "product.py", _RENAME_BODY, "tested source")
    repo = _filtered_shared_clone(origin, path) if filtered_shared else origin
    if filtered_shared:
        subprocess.run(
            ["git", "config", "user.email", "acceptance@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Acceptance Test"],
            cwd=repo,
            check=True,
        )

    tested_source = source_identity(repo)
    specs_by_file: dict[str, list[str]] = {
        spec_file: [] for spec_file in CANONICAL_SPEC_INVENTORY
    }
    for scenario in E2E_SCENARIOS:
        if scenario.is_manual:
            continue
        for ref in scenario.automation_refs:
            if ".spec.ts::" not in ref:
                continue
            spec_file, title = ref.split("::", 1)
            if title not in specs_by_file[spec_file]:
                specs_by_file[spec_file].append(title)
    for spec_file, titles in specs_by_file.items():
        if not titles:
            titles.append(f"canonical pass for {spec_file}")
    seeded = sum(len(titles) for titles in specs_by_file.values())
    first_file = CANONICAL_SPEC_INVENTORY[0]
    for index in range(EXPECTED_PLAYWRIGHT_TEST_COUNT - seeded):
        specs_by_file[first_file].append(f"synthetic pass {index}")
    pw_payload: dict[str, Any] = {
        "suites": [
            {
                "file": spec_file,
                "specs": [
                    {
                        "file": spec_file,
                        "title": title,
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
                    }
                    for title in titles
                ],
                "suites": [],
            }
            for spec_file, titles in specs_by_file.items()
        ],
        "stats": {
            "expected": EXPECTED_PLAYWRIGHT_TEST_COUNT,
            "skipped": 0,
            "unexpected": 0,
            "flaky": 0,
        },
    }
    pw_results, _pw_counts, pw_errors = parse_playwright_payload(pw_payload)
    _write_packet_artifact(
        repo,
        "playwright",
        _artifact(
            "playwright",
            pw_results,
            source=tested_source,
            payload=pw_payload,
            integrity_errors=pw_errors,
        ),
    )
    py_payload: dict[str, Any] = {
        "requested_node_ids": list(PYTEST_NODE_IDS),
        "phase_reports": {
            nodeid: [
                {"phase": phase, "outcome": "passed", "duration_seconds": 0.001}
                for phase in ("setup", "call", "teardown")
            ]
            for nodeid in PYTEST_NODE_IDS
        },
        "collection_errors": [],
        "runner_start_source": tested_source,
    }
    py_results, _py_counts, py_errors = parse_pytest_payload(
        py_payload, expected_source=tested_source
    )
    _write_packet_artifact(
        repo,
        "pytest",
        _artifact(
            "pytest",
            py_results,
            source=tested_source,
            payload=py_payload,
            integrity_errors=py_errors,
        ),
    )
    generate_receipt(repo)
    return repo


def _filtered_shared_clone(origin: Path, base: Path) -> Path:
    """Reach ``origin`` the way product CI does: blob:none partial, then --shared."""
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=origin, check=True)
    partial = base / "partial"
    subprocess.run(
        ["git", "clone", "-q", "--filter=blob:none", f"file://{origin}", str(partial)],
        check=True,
    )
    shared = base / "shared"
    _clone_packet_repo(shared, source=partial)
    return shared


def _generation_proof(proof: dict[str, Any]) -> dict[str, Any]:
    """Extend a current-source proof with the raw paths a generation proof records."""
    return {**proof, "working_tree_paths": sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH})}


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
    "output,exit_code,expected_error",
    [
        ("Total: 130 tests in 18 files", 0, None),
        ("Total: 122 tests in 18 files", 0, "must be exactly 130 tests in 18 files"),
        ("Total: 125 tests in 18 files", 0, "must be exactly 130 tests in 18 files"),
        ("Total: 126 tests in 18 files", 0, "must be exactly 130 tests in 18 files"),
        ("Total: 128 tests in 18 files", 0, "must be exactly 130 tests in 18 files"),
        ("Total: 131 tests in 18 files", 0, "must be exactly 130 tests in 18 files"),
        ("Total: 129 tests in 17 files", 0, "must be exactly 130 tests in 18 files"),
        ("", 0, "no parseable total"),
        ("Total: 129 tests in 18 files", 1, "exited 1"),
    ],
)
def test_playwright_inventory_remains_exact_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    exit_code: int,
    expected_error: str | None,
) -> None:
    def list_tests(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command == ["npx", "playwright", "test", "--list", "--project=chromium"]
        assert kwargs["cwd"] == ROOT
        return subprocess.CompletedProcess(command, exit_code, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", list_tests)
    errors = validate_acceptance_scenarios_and_inventory(ROOT)
    if expected_error is None:
        assert errors == []
    else:
        assert len(errors) == 1
        assert expected_error in errors[0]


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
def test_raw_runner_rejects_every_non_passing_terminal_status(status: str, exit_code: int) -> None:
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


def test_raw_playwright_reparses_self_consistently_resealed_payload() -> None:
    artifact = json.loads((ROOT / RAW_PLAYWRIGHT_PATH).read_text(encoding="utf-8"))
    payload = artifact["payload"]

    def fail_all_results(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                for attempt in test.get("results", []):
                    attempt["status"] = "failed"
        for child in suite.get("suites", []):
            fail_all_results(child)

    for suite in payload["suites"]:
        fail_all_results(suite)
    payload["stats"].update({"expected": 0, "skipped": 0, "unexpected": 107, "flaky": 0})
    artifact["payload_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    seal_normalized(artifact, "normalized_artifact_sha256")

    errors = validate_raw_artifact(artifact, "playwright")
    assert not any("hash mismatch" in error for error in errors)
    assert any("results do not exactly match parsed payload" in error for error in errors)
    assert any("counts do not exactly match parsed payload" in error for error in errors)


def test_raw_pytest_rejects_resealed_phase_outcome_contradiction() -> None:
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    nodeid = PYTEST_NODE_IDS[0]
    call_report = next(
        report
        for report in artifact["payload"]["phase_reports"][nodeid]
        if report["phase"] == "call"
    )
    call_report["outcome"] = "failed"
    artifact["payload_sha256"] = sha256_bytes(
        canonical_json_bytes(artifact["payload"])
    )
    seal_normalized(artifact, "normalized_artifact_sha256")

    errors = validate_raw_artifact(artifact, "pytest")
    assert not any("hash mismatch" in error for error in errors)
    assert any("results do not exactly match parsed payload" in error for error in errors)
    assert any("counts do not exactly match parsed payload" in error for error in errors)


def test_raw_pytest_rejects_resealed_collection_error_contradiction() -> None:
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    artifact["payload"]["collection_errors"].append("forged collection failure")
    artifact["payload_sha256"] = sha256_bytes(
        canonical_json_bytes(artifact["payload"])
    )
    seal_normalized(artifact, "normalized_artifact_sha256")

    errors = validate_raw_artifact(artifact, "pytest")
    assert not any("hash mismatch" in error for error in errors)
    assert any(
        "integrity_errors do not exactly match parsed payload" in error
        for error in errors
    )


def test_raw_pytest_rejects_resealed_requested_and_collected_id_drift() -> None:
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    removed = next(
        nodeid
        for nodeid in reversed(artifact["payload"]["requested_node_ids"])
        if nodeid in PYTEST_NODE_IDS
    )
    artifact["payload"]["requested_node_ids"].remove(removed)
    artifact["payload"]["requested_node_ids"].append("tests/security/forged.py::test_forged")
    artifact["payload_sha256"] = sha256_bytes(
        canonical_json_bytes(artifact["payload"])
    )
    seal_normalized(artifact, "normalized_artifact_sha256")

    _results, _counts, payload_errors = parse_pytest_payload(
        artifact["payload"],
        expected_source=artifact["source"],
    )
    errors = validate_raw_artifact(artifact, "pytest")
    assert any("missing exact test ids: tests/security/forged.py::test_forged" in error for error in payload_errors)
    assert any(f"unexpected collected test ids: {removed}" in error for error in payload_errors)
    assert any(
        "integrity_errors do not exactly match parsed payload" in error
        for error in errors
    )


def test_raw_pytest_reproduces_and_rejects_three_inconsistencies_fixture() -> None:
    """Reproduce the three inconsistencies when stored summary contradicts raw payload."""
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    # Tamper the stored results, counts, and integrity_errors relative to payload
    forged_results = [r for r in artifact["results"] if r["test_id"] != PYTEST_NODE_IDS[0]]
    artifact["results"] = forged_results
    artifact["counts"] = _counts(forged_results, "pytest")
    artifact["integrity_errors"] = ["forged discrepancy"]
    seal_normalized(artifact, "normalized_artifact_sha256")

    errors = validate_raw_artifact(artifact, "pytest", require_success=False)
    assert "raw pytest results do not exactly match parsed payload" in errors
    assert "raw pytest counts do not exactly match parsed payload" in errors
    assert "raw pytest integrity_errors do not exactly match parsed payload" in errors


def test_raw_pytest_positive_projection_and_validation() -> None:
    """Positive test: authentic pytest payload parses cleanly without errors."""
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    results, counts, integrity_errors = parse_pytest_payload(
        artifact["payload"],
        expected_source=artifact["source"],
    )
    assert integrity_errors == []
    assert counts["total_tests"] == len(artifact["payload"]["requested_node_ids"])
    assert counts["passed"] == len(artifact["payload"]["requested_node_ids"])
    assert len(results) == len(artifact["payload"]["requested_node_ids"])
    assert all(r["status"] == "passed" for r in results)
    errors = validate_raw_artifact(artifact, "pytest", require_success=True)
    assert errors == []


@pytest.mark.parametrize(
    "started_at,ended_at,expected_error",
    [
        ("not-a-timestamp", "also-not-a-timestamp", "strict UTC timestamp"),
        (
            "2026-07-31T00:02:00Z",
            "2026-07-31T00:01:00Z",
            "precedes run.started_at",
        ),
        (
            "2026-07-31T00:00:00+00:00",
            "2026-07-31T00:01:00+00:00",
            "strict UTC timestamp",
        ),
    ],
)
def test_raw_runner_rejects_non_utc_or_reversed_run_times(
    started_at: str, ended_at: str, expected_error: str
) -> None:
    artifact = json.loads((ROOT / RAW_PYTEST_PATH).read_text(encoding="utf-8"))
    artifact["run"]["started_at"] = started_at
    artifact["run"]["ended_at"] = ended_at
    seal_normalized(artifact, "normalized_artifact_sha256")
    errors = validate_raw_artifact(artifact, "pytest")
    assert any(expected_error in error for error in errors)


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
    assert any("E2E-EXP-001 missing exact playwright result" in error for error in errors)


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


def test_receipt_recomputes_aggregate_counts_and_generation_proof(
    tmp_path: Path,
) -> None:
    _clone_packet_repo(tmp_path)
    assert validate_receipt_packet(tmp_path, allow_worktree_evidence=True) == []
    receipt_path = tmp_path / RECEIPT_PATH
    baseline = json.loads(receipt_path.read_text(encoding="utf-8"))

    forged_counts = copy.deepcopy(baseline)
    forged_counts["aggregate_counts"] = {
        **forged_counts["aggregate_counts"],
        "total_tests": 999,
        "passed": 999,
    }
    seal_normalized(forged_counts, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(forged_counts), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("aggregate_counts do not match" in error for error in errors)

    forged_proof = copy.deepcopy(baseline)
    forged_proof["evidence_proof_at_generation"]["relation"] = "forged"
    forged_proof["evidence_proof_at_generation"]["allowlist"] = []
    seal_normalized(forged_proof, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(forged_proof), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("does not exactly match recomputed" in error for error in errors)

    missing_raw_proof = copy.deepcopy(baseline)
    missing_raw_proof["evidence_proof_at_generation"]["working_tree_paths"] = []
    seal_normalized(missing_raw_proof, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(missing_raw_proof), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("both generated raw artifact paths" in error for error in errors)


def test_receipt_rejects_duplicate_extra_and_mispathed_reconciliations(
    tmp_path: Path,
) -> None:
    _clone_packet_repo(tmp_path)
    assert validate_receipt_packet(tmp_path, allow_worktree_evidence=True) == []
    receipt_path = tmp_path / RECEIPT_PATH
    baseline = json.loads(receipt_path.read_text(encoding="utf-8"))

    duplicate = copy.deepcopy(baseline)
    duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
    seal_normalized(duplicate, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(duplicate), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("each runner exactly once" in error for error in errors)

    extra = copy.deepcopy(baseline)
    extra["artifacts"].append({"runner": "extra", "path": "forged.json"})
    seal_normalized(extra, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(extra), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("each runner exactly once" in error for error in errors)

    mispathed = copy.deepcopy(baseline)
    mispathed["artifacts"][0]["path"] = RAW_PYTEST_PATH
    seal_normalized(mispathed, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(mispathed), encoding="utf-8")
    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("playwright artifact path mismatch" in error for error in errors)


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
    proof, errors = verify_evidence_relationship(tmp_path, source, allow_worktree_evidence=False)
    assert errors == []
    assert proof["relation"] == "evidence_only_descendant"

    _commit(tmp_path, "source.py", "v2\n", "source changed after test")
    _proof, errors = verify_evidence_relationship(tmp_path, source, allow_worktree_evidence=False)
    assert any("non-evidence paths" in error for error in errors)


def test_intervening_source_change_is_rejected_even_if_reverted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", "v1\n", "source")
    source = source_identity(tmp_path)
    _commit(tmp_path, "source.py", "v2\n", "intervening source change")
    _commit(tmp_path, "source.py", "v1\n", "revert source")
    _proof, errors = verify_evidence_relationship(tmp_path, source, allow_worktree_evidence=False)
    assert any("non-evidence paths" in error for error in errors)


def test_stale_or_mismatched_source_tree_is_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", "v1\n", "source")
    source = source_identity(tmp_path)
    source["tree_sha"] = "0" * 40
    _proof, errors = verify_evidence_relationship(tmp_path, source, allow_worktree_evidence=False)
    assert any("tested tree mismatch" in error for error in errors)


def _commit_rename(repo: Path, old: str, new: str, message: str) -> None:
    """Commit a similar-bodied move so default rename detection must read both blobs."""
    (repo / new).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "mv", old, new], cwd=repo, check=True)
    (repo / new).write_text(_RENAME_BODY + "evidence revision\n", encoding="utf-8")
    subprocess.run(["git", "add", new], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)


def test_evidence_git_reads_are_metadata_only_in_filtered_shared_clone(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(origin, RAW_PLAYWRIGHT_PATH, _RENAME_BODY, "prior evidence")
    _commit(origin, "old_source.py", _RENAME_BODY, "prior source")
    old_blob = git_value(origin, "rev-parse", f"HEAD:{RAW_PLAYWRIGHT_PATH}")
    # The tested commit itself also moves a file, so reading it must stay metadata-only.
    _commit_rename(origin, "old_source.py", "source.py", "tested source")
    source = source_identity(origin)
    _commit_rename(origin, RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH, "evidence-only rename")

    clone = _filtered_shared_clone(origin, tmp_path)

    # The fixture must really be the failing context, not a complete clone.
    missing_blob = subprocess.run(
        ["git", "cat-file", "-e", old_blob], cwd=clone, capture_output=True, check=False
    )
    assert missing_blob.returncode != 0
    rename_detecting_log = subprocess.run(
        ["git", "log", "--format=", "--name-only", f"{source['commit_sha']}..HEAD"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rename_detecting_log.returncode == 128

    # Both literal names are read, and both readers agree on the same metadata.
    proof, errors = verify_evidence_relationship(clone, source, allow_worktree_evidence=False)
    assert errors == []
    assert proof["relation"] == "evidence_only_descendant"
    assert proof["intervening_touched_paths"] == sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH})
    generation_proof = _generation_proof(proof)
    assert validate_evidence_proof_at_generation(clone, source, generation_proof) == []

    # Metadata-only reads keep the literal proof comparison exact.
    forged = copy.deepcopy(generation_proof)
    forged["intervening_touched_paths"] = [RAW_PYTEST_PATH]
    assert any(
        "does not exactly match recomputed" in error
        for error in validate_evidence_proof_at_generation(clone, source, forged)
    )

    # The separate tree and ancestry boundaries still hold in the same clone.
    wrong_tree = {**source, "tree_sha": "0" * 40}
    _proof, errors = verify_evidence_relationship(clone, wrong_tree, allow_worktree_evidence=False)
    assert any("tested tree mismatch" in error for error in errors)
    assert any(
        "tested source tree does not match" in error
        for error in validate_evidence_proof_at_generation(clone, wrong_tree, generation_proof)
    )
    head_before_source = {**generation_proof, "evidence_head_sha": source["commit_sha"]}
    assert any(
        "not an ancestor of evidence head" in error
        for error in validate_evidence_proof_at_generation(
            clone, source_identity(clone), head_before_source
        )
    )


def test_intervening_non_evidence_change_is_rejected_in_filtered_shared_clone(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(origin, "source.py", _RENAME_BODY, "source")
    source = source_identity(origin)
    _commit(origin, "source.py", "changed after test\n", "intervening source change")
    _commit(origin, "source.py", _RENAME_BODY, "revert source")

    clone = _filtered_shared_clone(origin, tmp_path)

    proof, errors = verify_evidence_relationship(clone, source, allow_worktree_evidence=False)
    assert any("non-evidence paths: source.py" in error for error in errors)
    generation_proof = _generation_proof(proof)
    assert any(
        "intervening commits touch non-evidence paths: source.py" in error
        for error in validate_evidence_proof_at_generation(clone, source, generation_proof)
    )


@pytest.mark.parametrize("filtered_shared", [False, True], ids=["complete", "filtered-shared"])
def test_source_renamed_into_evidence_path_is_rejected_by_both_readers(
    tmp_path: Path,
    filtered_shared: bool,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(origin, "source.py", _RENAME_BODY, "source")
    source = source_identity(origin)
    _commit_rename(origin, "source.py", RAW_PYTEST_PATH, "hide source as evidence")

    repo = _filtered_shared_clone(origin, tmp_path) if filtered_shared else origin

    proof, errors = verify_evidence_relationship(repo, source, allow_worktree_evidence=False)
    assert proof["intervening_touched_paths"] == sorted({RAW_PYTEST_PATH, "source.py"})
    assert any("non-evidence paths: source.py" in error for error in errors)
    assert any(
        "intervening commits touch non-evidence paths: source.py" in error
        for error in validate_evidence_proof_at_generation(repo, source, _generation_proof(proof))
    )


def test_staged_source_rename_into_evidence_path_is_rejected(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "source.py", _RENAME_BODY, "source")
    source = source_identity(tmp_path)
    (tmp_path / RAW_PYTEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "mv", "source.py", RAW_PYTEST_PATH], cwd=tmp_path, check=True)

    proof, errors = verify_evidence_relationship(tmp_path, source, allow_worktree_evidence=True)

    assert proof["working_tree_paths"] == sorted({RAW_PYTEST_PATH, "source.py"})
    assert any("working tree contains non-evidence changes: source.py" in e for e in errors)


def _create_two_evidence_branches(
    repo: Path, *, source: dict[str, str] | None = None
) -> dict[str, str]:
    if source is None:
        _commit(repo, "product.py", _RENAME_BODY, "tested source")
        source = source_identity(repo)
    subprocess.run(["git", "checkout", "-qb", "playwright-evidence", source["commit_sha"]], cwd=repo, check=True)
    _commit(repo, RAW_PLAYWRIGHT_PATH, _RENAME_BODY + "pw\n", "playwright evidence")
    subprocess.run(["git", "checkout", "-qb", "pytest-evidence", source["commit_sha"]], cwd=repo, check=True)
    _commit(repo, RAW_PYTEST_PATH, _RENAME_BODY + "py\n", "pytest evidence")
    return source


@pytest.mark.parametrize("filtered_shared", [False, True], ids=["complete", "filtered-shared"])
@pytest.mark.parametrize(
    "first_branch,second_branch",
    [
        ("playwright-evidence", "pytest-evidence"),
        ("pytest-evidence", "playwright-evidence"),
    ],
)
def test_ordinary_permitted_evidence_only_merge_is_accepted_by_both_validators(
    tmp_path: Path,
    filtered_shared: bool,
    first_branch: str,
    second_branch: str,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(
        origin,
        RAW_PLAYWRIGHT_PATH,
        _RENAME_BODY + "prior-only\n",
        "prior playwright evidence",
    )
    old_blob = git_value(origin, "rev-parse", f"HEAD:{RAW_PLAYWRIGHT_PATH}")
    source = _create_two_evidence_branches(origin)
    subprocess.run(["git", "checkout", "-q", first_branch], cwd=origin, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "-qm", "clean evidence merge", second_branch],
        cwd=origin,
        check=True,
    )

    repo = _filtered_shared_clone(origin, tmp_path) if filtered_shared else origin
    if filtered_shared:
        missing_blob = subprocess.run(
            ["git", "cat-file", "-e", old_blob], cwd=repo, capture_output=True, check=False
        )
        assert missing_blob.returncode != 0

    proof, errors = verify_evidence_relationship(repo, source, allow_worktree_evidence=False)
    assert errors == []
    assert proof["relation"] == "evidence_only_descendant"
    assert proof["intervening_touched_paths"] == sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH})
    assert validate_evidence_proof_at_generation(repo, source, _generation_proof(proof)) == []


@pytest.mark.parametrize("filtered_shared", [False, True], ids=["complete", "filtered-shared"])
@pytest.mark.parametrize(
    "source_is_second_parent",
    [False, True],
    ids=["source-first-parent", "source-second-parent"],
)
def test_tested_source_merged_as_either_parent_with_common_base_evidence_branch_is_accepted(
    tmp_path: Path,
    filtered_shared: bool,
    source_is_second_parent: bool,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(origin, "product.py", _RENAME_BODY + "v0\n", "common source")
    common = source_identity(origin)
    old_blob = git_value(origin, "rev-parse", "HEAD:product.py")

    subprocess.run(["git", "checkout", "-qb", "tested", common["commit_sha"]], cwd=origin, check=True)
    _commit(origin, "product.py", _RENAME_BODY + "v1\n", "tested product revision")
    source = source_identity(origin)

    subprocess.run(
        ["git", "checkout", "-qb", "evidence", common["commit_sha"]], cwd=origin, check=True
    )
    _commit(origin, RAW_PLAYWRIGHT_PATH, _RENAME_BODY + "pw\n", "allowed evidence")

    if source_is_second_parent:
        subprocess.run(
            ["git", "merge", "--no-ff", "-qm", "import tested source as second parent", "tested"],
            cwd=origin,
            check=True,
        )
    else:
        subprocess.run(["git", "checkout", "-q", "tested"], cwd=origin, check=True)
        subprocess.run(
            ["git", "merge", "--no-ff", "-qm", "merge allowed evidence as second parent", "evidence"],
            cwd=origin,
            check=True,
        )

    net_diff = git_value(origin, "diff", "--name-only", source["commit_sha"], "HEAD").splitlines()
    assert net_diff == [RAW_PLAYWRIGHT_PATH]

    repo = _filtered_shared_clone(origin, tmp_path) if filtered_shared else origin
    if filtered_shared:
        missing_blob = subprocess.run(
            ["git", "cat-file", "-e", old_blob], cwd=repo, capture_output=True, check=False
        )
        assert missing_blob.returncode != 0

    proof, errors = verify_evidence_relationship(repo, source, allow_worktree_evidence=False)
    assert errors == []
    assert proof["relation"] == "evidence_only_descendant"
    assert proof["intervening_touched_paths"] == [RAW_PLAYWRIGHT_PATH]
    assert validate_evidence_proof_at_generation(repo, source, _generation_proof(proof)) == []


@pytest.mark.parametrize(
    "source_is_second_parent",
    [False, True],
    ids=["source-first-parent", "source-second-parent"],
)
@pytest.mark.parametrize(
    "mutation_kind",
    [
        "non-source-side-edit",
        "merge-only-edit",
        "merge-reverts-to-common-base",
    ],
)
def test_common_base_merge_rejects_side_branch_and_merge_product_mutations(
    tmp_path: Path,
    source_is_second_parent: bool,
    mutation_kind: str,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    _commit(origin, "product.py", _RENAME_BODY + "v0\n", "common source")
    common = source_identity(origin)

    subprocess.run(["git", "checkout", "-qb", "tested", common["commit_sha"]], cwd=origin, check=True)
    _commit(origin, "product.py", _RENAME_BODY + "v1\n", "tested product revision")
    source = source_identity(origin)

    subprocess.run(
        ["git", "checkout", "-qb", "evidence", common["commit_sha"]], cwd=origin, check=True
    )
    _commit(origin, RAW_PLAYWRIGHT_PATH, _RENAME_BODY + "pw\n", "allowed evidence")
    if mutation_kind == "non-source-side-edit":
        _commit(origin, "product.py", "side branch product edit\n", "side branch product edit")

    first_branch = "evidence" if source_is_second_parent else "tested"
    second_branch = "tested" if source_is_second_parent else "evidence"
    subprocess.run(["git", "checkout", "-q", first_branch], cwd=origin, check=True)
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", second_branch],
        cwd=origin,
        check=mutation_kind != "non-source-side-edit",
    )
    if mutation_kind == "non-source-side-edit":
        # Even if the merge resolves product.py to the exact tested v1 tree,
        # the non-source side-branch product edit must still be rejected.
        (origin / "product.py").write_text(_RENAME_BODY + "v1\n", encoding="utf-8")
    elif mutation_kind == "merge-only-edit":
        (origin / "product.py").write_text("merge-only product edit\n", encoding="utf-8")
    else:
        (origin / "product.py").write_text(_RENAME_BODY + "v0\n", encoding="utf-8")
    subprocess.run(["git", "add", "product.py"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", f"merge ({mutation_kind})"], cwd=origin, check=True)

    if mutation_kind == "non-source-side-edit":
        net_diff = git_value(
            origin, "diff", "--name-only", source["commit_sha"], "HEAD"
        ).splitlines()
        assert net_diff == [RAW_PLAYWRIGHT_PATH]

    repo = _filtered_shared_clone(origin, tmp_path)
    proof, errors = verify_evidence_relationship(repo, source, allow_worktree_evidence=False)
    assert proof["intervening_touched_paths"] == sorted({RAW_PLAYWRIGHT_PATH, "product.py"})
    assert any(
        "intervening commits touch non-evidence paths: product.py" in error for error in errors
    )
    generation_errors = validate_evidence_proof_at_generation(
        repo, source, _generation_proof(proof)
    )
    assert any(
        "intervening commits touch non-evidence paths: product.py" in error
        for error in generation_errors
    )


@pytest.mark.parametrize("filtered_shared", [False, True], ids=["complete", "filtered-shared"])
@pytest.mark.parametrize(
    "first_branch,second_branch",
    [
        ("playwright-evidence", "pytest-evidence"),
        ("pytest-evidence", "playwright-evidence"),
    ],
)
def test_merge_only_product_change_is_rejected_by_both_validators(
    tmp_path: Path,
    filtered_shared: bool,
    first_branch: str,
    second_branch: str,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    source = _create_two_evidence_branches(origin)
    subprocess.run(["git", "checkout", "-q", first_branch], cwd=origin, check=True)
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", second_branch],
        cwd=origin,
        check=True,
    )
    (origin / "product.py").write_text("mutated only in merge commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "product.py"], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-qm", "merge with product mutation"], cwd=origin, check=True)

    repo = _filtered_shared_clone(origin, tmp_path) if filtered_shared else origin

    proof, errors = verify_evidence_relationship(repo, source, allow_worktree_evidence=False)
    assert proof["intervening_touched_paths"] == sorted(
        {RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH, "product.py"}
    )
    assert any(
        "intervening commits touch non-evidence paths: product.py" in error for error in errors
    )

    generation_proof = _generation_proof(proof)
    generation_errors = validate_evidence_proof_at_generation(repo, source, generation_proof)
    assert any(
        "intervening commits touch non-evidence paths: product.py" in error
        for error in generation_errors
    )

    # A forged generation proof that omits the merge-only product path is rejected on both counts.
    forged_generation_proof = {
        **generation_proof,
        "intervening_touched_paths": sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH}),
    }
    forged_errors = validate_evidence_proof_at_generation(repo, source, forged_generation_proof)
    assert any(
        "intervening commits touch non-evidence paths: product.py" in error
        for error in forged_errors
    )
    assert any(
        "does not exactly match recomputed Git relationship" in error for error in forged_errors
    )


@pytest.mark.parametrize("filtered_shared", [False, True], ids=["complete", "filtered-shared"])
def test_receipt_packet_rejects_merge_only_product_mutation(
    tmp_path: Path,
    filtered_shared: bool,
) -> None:
    repo = _clone_packet_repo(tmp_path, filtered_shared=filtered_shared)
    if filtered_shared:
        old_blob = git_value(tmp_path / "origin", "rev-parse", "HEAD~1:product.py")
        missing_blob = subprocess.run(
            ["git", "cat-file", "-e", old_blob], cwd=repo, capture_output=True, check=False
        )
        assert missing_blob.returncode != 0
    assert validate_receipt_packet(repo, allow_worktree_evidence=True) == []

    receipt_path = repo / RECEIPT_PATH
    pw_bytes = (repo / RAW_PLAYWRIGHT_PATH).read_bytes()
    py_bytes = (repo / RAW_PYTEST_PATH).read_bytes()
    baseline = json.loads(receipt_path.read_text(encoding="utf-8"))

    _create_two_evidence_branches(repo, source=baseline["tested_source"])

    subprocess.run(["git", "checkout", "-q", "playwright-evidence"], cwd=repo, check=True)
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "pytest-evidence"],
        cwd=repo,
        check=True,
    )
    (repo / RAW_PLAYWRIGHT_PATH).write_bytes(pw_bytes)
    (repo / RAW_PYTEST_PATH).write_bytes(py_bytes)
    (repo / "product.py").write_text("merge-only product change\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH, "product.py"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "merge adds product.py"], cwd=repo, check=True)

    forged = copy.deepcopy(baseline)
    forged["evidence_proof_at_generation"] = {
        **forged["evidence_proof_at_generation"],
        "evidence_head_sha": git_value(repo, "rev-parse", "HEAD"),
        "relation": "evidence_only_descendant",
        "intervening_touched_paths": sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH}),
    }
    seal_normalized(forged, "normalized_receipt_sha256")
    receipt_path.write_text(json.dumps(forged, indent=2) + "\n", encoding="utf-8")

    errors = validate_receipt_packet(repo, allow_worktree_evidence=True)
    assert any("intervening commits touch non-evidence paths: product.py" in e for e in errors)
    assert any(
        "receipt evidence_proof_at_generation intervening commits touch non-evidence paths: product.py"
        in e
        for e in errors
    )
    assert any(
        "receipt evidence_proof_at_generation does not exactly match recomputed Git relationship"
        in e
        for e in errors
    )


@pytest.mark.parametrize("revert_in_merge", [False, True], ids=["revert-after-merge", "revert-in-merge"])
def test_merge_product_mutation_reverted_later_remains_rejected(
    tmp_path: Path,
    revert_in_merge: bool,
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_repo(origin)
    source = _create_two_evidence_branches(origin)
    if revert_in_merge:
        _commit(origin, "product.py", "branch product mutation\n", "mutate product on branch")
        subprocess.run(["git", "checkout", "-q", "playwright-evidence"], cwd=origin, check=True)
        subprocess.run(
            ["git", "merge", "--no-commit", "--no-ff", "pytest-evidence"],
            cwd=origin,
            check=True,
        )
        (origin / "product.py").write_text(_RENAME_BODY, encoding="utf-8")
        subprocess.run(["git", "add", "product.py"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "-qm", "merge reverts product.py"], cwd=origin, check=True)
    else:
        subprocess.run(["git", "checkout", "-q", "playwright-evidence"], cwd=origin, check=True)
        subprocess.run(
            ["git", "merge", "--no-commit", "--no-ff", "pytest-evidence"],
            cwd=origin,
            check=True,
        )
        (origin / "product.py").write_text("merge product mutation\n", encoding="utf-8")
        subprocess.run(["git", "add", "product.py"], cwd=origin, check=True)
        subprocess.run(["git", "commit", "-qm", "merge mutates product.py"], cwd=origin, check=True)
        _commit(origin, "product.py", _RENAME_BODY, "revert merge product mutation")

    # Net tree difference between tested source and HEAD has only the two evidence files.
    net_diff = git_value(origin, "diff", "--name-only", source["commit_sha"], "HEAD").splitlines()
    assert sorted(net_diff) == sorted({RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH})

    clone = _filtered_shared_clone(origin, tmp_path)
    proof, errors = verify_evidence_relationship(clone, source, allow_worktree_evidence=False)
    assert "product.py" in proof["intervening_touched_paths"]
    assert any("intervening commits touch non-evidence paths: product.py" in e for e in errors)
    generation_errors = validate_evidence_proof_at_generation(
        clone, source, _generation_proof(proof)
    )
    assert any(
        "intervening commits touch non-evidence paths: product.py" in e
        for e in generation_errors
    )


def test_non_ancestor_and_invalid_sources_fail_closed_in_both_validators(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "product.py", "v1\n", "base")
    base = source_identity(tmp_path)

    subprocess.run(["git", "checkout", "-qb", "sibling", base["commit_sha"]], cwd=tmp_path, check=True)
    _commit(tmp_path, RAW_PLAYWRIGHT_PATH, "{}\n", "sibling commit")
    sibling = source_identity(tmp_path)

    subprocess.run(["git", "checkout", "-qb", "mainline", base["commit_sha"]], cwd=tmp_path, check=True)
    _commit(tmp_path, RAW_PYTEST_PATH, "{}\n", "mainline commit")
    head = source_identity(tmp_path)

    # Non-ancestor sibling source
    sibling_proof, sibling_errors = verify_evidence_relationship(
        tmp_path, sibling, allow_worktree_evidence=False
    )
    assert any("tested source is not an ancestor of evidence HEAD" in e for e in sibling_errors)
    assert any(
        "tested source is not an ancestor of evidence head" in e
        for e in validate_evidence_proof_at_generation(
            tmp_path, sibling, _generation_proof(sibling_proof)
        )
    )

    valid_proof, valid_errors = verify_evidence_relationship(
        tmp_path, base, allow_worktree_evidence=False
    )
    assert valid_errors == []
    valid_gen_proof = _generation_proof(valid_proof)

    # Malformed source identities (including ref name "HEAD" instead of a 40-hex SHA)
    for malformed_source in (
        {"commit_sha": "HEAD", "tree_sha": head["tree_sha"]},
        {"commit_sha": "not-a-sha", "tree_sha": head["tree_sha"]},
        {"commit_sha": head["commit_sha"], "tree_sha": "not-a-tree"},
    ):
        _p, rel_errors = verify_evidence_relationship(
            tmp_path, malformed_source, allow_worktree_evidence=False
        )
        assert rel_errors == ["tested source SHA/tree is malformed"]
        gen_errors = validate_evidence_proof_at_generation(
            tmp_path, malformed_source, valid_gen_proof
        )
        assert gen_errors == [
            "receipt evidence_proof_at_generation tested source SHA/tree is malformed"
        ]

    # Missing commit SHA and non-commit (tree) SHA passed as commit SHA
    for unresolvable_commit in ("f" * 40, head["tree_sha"]):
        unresolvable_source = {
            "commit_sha": unresolvable_commit,
            "tree_sha": head["tree_sha"],
        }
        _p, rel_errors = verify_evidence_relationship(
            tmp_path, unresolvable_source, allow_worktree_evidence=False
        )
        assert any("tested source commit cannot be resolved" in e for e in rel_errors)
        gen_errors = validate_evidence_proof_at_generation(
            tmp_path, unresolvable_source, valid_gen_proof
        )
        assert any("commit cannot be resolved" in e for e in gen_errors)


def test_dangerous_path_names_preserve_exact_literal_contract_in_merge_and_worktree(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    source = _create_two_evidence_branches(tmp_path)
    trailing_space_spoof = f"{RAW_PLAYWRIGHT_PATH} "
    quoted_spoof = f'"{RAW_PYTEST_PATH}"'
    tab_and_quote_path = 'spaced\tand "quoted" product.py'

    subprocess.run(["git", "checkout", "-q", "playwright-evidence"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "pytest-evidence"],
        cwd=tmp_path,
        check=True,
    )
    for dangerous in (trailing_space_spoof, quoted_spoof, tab_and_quote_path):
        target = tmp_path / dangerous
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("spoof\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", dangerous], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "merge with dangerous literal paths"],
        cwd=tmp_path,
        check=True,
    )

    proof, errors = verify_evidence_relationship(
        tmp_path, source, allow_worktree_evidence=False
    )
    expected_touched = sorted(
        {
            RAW_PLAYWRIGHT_PATH,
            RAW_PYTEST_PATH,
            trailing_space_spoof,
            quoted_spoof,
            tab_and_quote_path,
        }
    )
    assert proof["intervening_touched_paths"] == expected_touched
    for dangerous in (trailing_space_spoof, quoted_spoof, tab_and_quote_path):
        assert any(dangerous in e for e in errors)

    generation_errors = validate_evidence_proof_at_generation(
        tmp_path, source, _generation_proof(proof)
    )
    for dangerous in (trailing_space_spoof, quoted_spoof, tab_and_quote_path):
        assert any(dangerous in e for e in generation_errors)

    # Also verify working-tree status preserves trailing-space and quoted paths literally.
    worktree_spoof = f"{RECEIPT_PATH} "
    worktree_target = tmp_path / worktree_spoof
    worktree_target.parent.mkdir(parents=True, exist_ok=True)
    worktree_target.write_text("untracked spoof\n", encoding="utf-8")
    wt_proof, wt_errors = verify_evidence_relationship(
        tmp_path, source_identity(tmp_path), allow_worktree_evidence=True
    )
    assert wt_proof["working_tree_paths"] == [worktree_spoof]
    assert any(
        f"working tree contains non-evidence changes: {worktree_spoof}" in e
        for e in wt_errors
    )


def test_python_runner_propagates_pytest_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pytest, "main", lambda *args, **kwargs: 1)
    status = run_python_tests(output_path=tmp_path / "raw_pytest_results.json")
    assert status != 0
    artifact = json.loads((tmp_path / "raw_pytest_results.json").read_text(encoding="utf-8"))
    assert artifact["run"]["exit_code"] == 1


def test_receipt_rejects_insufficient_or_mismatched_pytest_counts(
    tmp_path: Path,
) -> None:
    _clone_packet_repo(tmp_path)
    assert validate_receipt_packet(tmp_path, allow_worktree_evidence=True) == []
    py_path = tmp_path / RAW_PYTEST_PATH
    py_artifact = json.loads(py_path.read_text(encoding="utf-8"))
    # Forge pytest counts to not prove exact passes
    py_artifact["counts"]["total_tests"] = len(PYTEST_NODE_IDS) - 1
    py_artifact["counts"]["passed"] = len(PYTEST_NODE_IDS) - 1
    # Adjust results to match counts so validate_raw_artifact passes
    py_artifact["results"] = py_artifact["results"][:-1]
    py_artifact["payload"]["requested_node_ids"] = py_artifact["payload"]["requested_node_ids"][:-1]
    dropped_id = list(py_artifact["payload"]["phase_reports"].keys())[-1]
    del py_artifact["payload"]["phase_reports"][dropped_id]
    py_artifact["payload_sha256"] = sha256_bytes(canonical_json_bytes(py_artifact["payload"]))
    seal_normalized(py_artifact, "normalized_artifact_sha256")
    py_path.write_text(json.dumps(py_artifact, indent=2), encoding="utf-8")

    errors = validate_receipt_packet(tmp_path, allow_worktree_evidence=True)
    assert any("Pytest receipt counts do not prove" in error for error in errors)


def test_evidence_allowlist_is_explicit_and_narrow() -> None:
    assert EVIDENCE_COMMIT_ALLOWLIST == {
        RAW_PLAYWRIGHT_PATH,
        RAW_PYTEST_PATH,
        RECEIPT_PATH,
    }
