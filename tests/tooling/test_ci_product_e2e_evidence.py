"""CI must retain fresh evidence, including failures, without relabeling old runs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from delivery_toolchain.e2e import collect_ci_product_e2e_evidence as collector
from delivery_toolchain.e2e.product_e2e_receipt import (
    RAW_PLAYWRIGHT_PATH,
    RAW_PYTEST_PATH,
    RECEIPT_PATH,
    canonical_json_bytes,
    seal_normalized,
    sha256_bytes,
)
from delivery_toolchain.e2e.record_playwright_results import build_artifact

ROOT = Path(__file__).resolve().parents[2]
SOURCE = {"commit_sha": "a" * 40, "tree_sha": "b" * 40}


def write_raw(root: Path, *, failed: bool = False) -> tuple[Path, dict]:
    payload = {
        "suites": [
            {
                "file": "example.spec.ts",
                "specs": [
                    {
                        "title": "retained failure" if failed else "passing result",
                        "file": "example.spec.ts",
                        "tests": [
                            {
                                "projectName": "chromium",
                                "expectedStatus": "passed",
                                "status": "unexpected" if failed else "expected",
                                "results": [
                                    {
                                        "status": "failed" if failed else "passed",
                                        "retry": 0,
                                        "duration": 12,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
        "stats": {
            "expected": 0 if failed else 1,
            "unexpected": 1 if failed else 0,
            "skipped": 0,
            "flaky": 0,
        },
    }
    artifact = build_artifact(
        payload,
        source_sha=SOURCE["commit_sha"],
        tree_sha=SOURCE["tree_sha"],
        command="playwright test",
        version="test",
        started_at="2026-10-11T00:00:00Z",
        ended_at="2026-10-11T00:00:01Z",
        exit_code=1 if failed else 0,
        environment={"project": "chromium", "workers": "1", "retries": "0"},
    )
    path = root / RAW_PLAYWRIGHT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact))
    return path, artifact


@pytest.mark.parametrize("mismatched", ["commit_sha", "tree_sha"])
def test_other_source_or_tree_is_not_exported(tmp_path, monkeypatch, mismatched):
    path, artifact = write_raw(tmp_path)
    artifact["source"][mismatched] = "c" * 40
    seal_normalized(artifact, "normalized_artifact_sha256")
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    output = tmp_path / "archive"
    packet = collector.collect_evidence(tmp_path, output, expect_complete=True)
    assert not (output / path.name).exists()
    assert packet["files"] == []
    assert packet["validation_errors"]
    assert any(x["reason"] == "not this checkout's source/tree" for x in packet["omitted"])


def test_failed_current_run_is_retained_byte_for_byte_as_diagnostics(tmp_path, monkeypatch):
    path, _ = write_raw(tmp_path, failed=True)
    before = path.read_bytes()
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    output = tmp_path / "archive"
    packet = collector.collect_evidence(tmp_path, output)
    assert (output / path.name).read_bytes() == before
    assert packet["files"][0]["sha256"] == sha256_bytes(before)
    assert packet["files"][0]["validation_errors"] == []
    assert packet["expected_complete_passing_packet"] is False
    assert packet["full_product_acceptance_claimed"] is False
    assert json.loads((output / path.name).read_text())["run"]["exit_code"] == 1


def test_resealed_payload_tampering_cannot_make_successful_collection(tmp_path, monkeypatch):
    path, artifact = write_raw(tmp_path)
    artifact["payload"]["stats"]["expected"] = 100
    seal_normalized(artifact, "normalized_artifact_sha256")
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    packet = collector.collect_evidence(tmp_path, tmp_path / "archive", expect_complete=True)
    assert any("payload hash mismatch" in x for x in packet["validation_errors"])
    assert packet["files"][0]["validation_errors"]
    assert packet["production_admission_claimed"] is False


def test_malformed_current_receipt_seal_is_retained_and_rejected(tmp_path, monkeypatch):
    path = tmp_path / RECEIPT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": collector.SCHEMA_VERSION,
                "tested_source": SOURCE,
                "status": "passed",
                "exit_code": 0,
                "validation_errors": [],
                "normalized_receipt_sha256": "wrong",
            }
        )
    )
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    packet = collector.collect_evidence(tmp_path, tmp_path / "archive", expect_complete=True)
    assert any("receipt normalized hash mismatch" in x for x in packet["validation_errors"])


def test_complete_collection_still_requires_canonical_packet_validation(tmp_path, monkeypatch):
    # The checked-in packet is a historical fixture, not live acceptance proof.
    source = json.loads((ROOT / RECEIPT_PATH).read_text())["tested_source"]
    for relative in [RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH, RECEIPT_PATH]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(collector, "source_identity", lambda _: source)

    def refused(root, *, allow_worktree_evidence):
        assert root == tmp_path and allow_worktree_evidence
        return ["canonical generation proof refused"]

    monkeypatch.setattr(collector, "validate_receipt_packet", refused)
    packet = collector.collect_evidence(tmp_path, tmp_path / "archive", expect_complete=True)
    assert len(packet["files"]) == 3
    assert "canonical generation proof refused" in packet["validation_errors"]


def test_existing_output_is_not_reused(tmp_path):
    output = tmp_path / "archive"
    output.mkdir()
    old = output / "stale-receipt.json"
    old.write_text("old")
    with pytest.raises(ValueError, match="must be empty"):
        collector.collect_evidence(tmp_path, output)
    assert old.read_text() == "old"


def _with_attachments(path, artifact, attachments):
    result = artifact["payload"]["suites"][0]["specs"][0]["tests"][0]["results"][0]
    result["attachments"] = attachments
    artifact["payload_sha256"] = sha256_bytes(canonical_json_bytes(artifact["payload"]))
    seal_normalized(artifact, "normalized_artifact_sha256")
    path.write_text(json.dumps(artifact))


def test_first_failure_trace_is_copied_but_unreferenced_old_trace_is_not(tmp_path, monkeypatch):
    path, artifact = write_raw(tmp_path, failed=True)
    trace = tmp_path / "test-results" / "first-failure" / "trace.zip"
    trace.parent.mkdir(parents=True)
    trace.write_bytes(b"explicit synthetic trace bytes")
    stale = tmp_path / "test-results" / "stale.zip"
    stale.write_bytes(b"old run")
    _with_attachments(path, artifact, [{"name": "trace", "contentType": "application/zip", "path": str(trace)}])
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    output = tmp_path / "archive"
    packet = collector.collect_evidence(tmp_path, output)
    assert len(packet["diagnostics"]) == 1
    evidence = packet["diagnostics"][0]
    assert (output / evidence["archive_path"]).read_bytes() == trace.read_bytes()
    assert evidence["sha256"] == sha256_bytes(trace.read_bytes())
    assert evidence["referencing_artifact_sha256"] == sha256_bytes(path.read_bytes())
    assert evidence["diagnostic_only"] is True
    assert not (output / "test-results" / "stale.zip").exists()
    assert packet["full_product_acceptance_claimed"] is False


@pytest.mark.parametrize("kind", ["outside", "symlink", "missing", "parent_symlink"])
def test_attachment_paths_cannot_export_unrelated_files(tmp_path, monkeypatch, kind):
    path, artifact = write_raw(tmp_path, failed=True)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("must never be exported")
    results = tmp_path / "test-results"
    results.mkdir()
    attachment = unrelated
    if kind == "symlink":
        attachment = results / "trace.zip"
        attachment.symlink_to(unrelated)
    elif kind == "missing":
        attachment = results / "missing.zip"
    elif kind == "parent_symlink":
        (results / "linked").symlink_to(tmp_path, target_is_directory=True)
        attachment = results / "linked" / "unrelated.txt"
    _with_attachments(path, artifact, [{"name": "trace", "path": str(attachment)}])
    monkeypatch.setattr(collector, "source_identity", lambda _: SOURCE)
    output = tmp_path / "archive"
    packet = collector.collect_evidence(tmp_path, output)
    assert not packet["diagnostics"]
    assert len(packet["diagnostics_omitted"]) == 1
    assert not (output / "test-results").exists()


def test_stale_source_cannot_export_even_a_real_attachment(tmp_path, monkeypatch):
    path, artifact = write_raw(tmp_path, failed=True)
    trace = tmp_path / "test-results" / "trace.zip"
    trace.parent.mkdir()
    trace.write_bytes(b"old evidence")
    _with_attachments(path, artifact, [{"name": "trace", "path": str(trace)}])
    monkeypatch.setattr(collector, "source_identity", lambda _: {**SOURCE, "commit_sha": "c" * 40})
    output = tmp_path / "archive"
    packet = collector.collect_evidence(tmp_path, output)
    assert not packet["files"] and not packet["diagnostics"]
    assert not (output / "test-results").exists()


def test_real_checkout_does_not_relabel_committed_historical_packet(tmp_path):
    # Isolate the checkout being tested: ROOT may legitimately contain fresh
    # outputs after the real producer has run, rather than historical inputs.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Evidence test",
            "-c",
            "user.email=evidence@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "isolated evidence checkout",
        ],
        cwd=checkout,
        check=True,
    )
    for relative in [RAW_PLAYWRIGHT_PATH, RAW_PYTEST_PATH, RECEIPT_PATH]:
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    packet = collector.collect_evidence(checkout, tmp_path / "archive")
    assert packet["files"] == []
    assert len(packet["omitted"]) == 3
    assert all(x["reason"] == "not this checkout's source/tree" for x in packet["omitted"])
