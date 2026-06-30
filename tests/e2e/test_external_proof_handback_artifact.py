from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"
CHECKER = ROOT / "scripts/e2e/check_external_proof_handback_artifact.py"
EXAMPLE = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json"
EXPECTED_SHA = "89d0ccc19c983a3e8f8e908459c65939a62d4dfb"
EXAMPLE_SHA = "1111111111111111111111111111111111111111"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def valid_handback(task_id: str = "ODP-MAP-STAGE-001") -> dict[str, Any]:
    queue_entries = {entry["task_id"]: entry for entry in load_json(QUEUE)["queue"]}
    template_entries = {entry["task_id"]: entry for entry in load_json(TEMPLATE)["tasks"]}
    queue_entry = queue_entries[task_id]
    template_entry = template_entries[task_id]

    artifacts = [
        {
            "artifact_id": f"artifact-{artifact_type}",
            "artifact_type": artifact_type,
            "location": f"https://github.com/alfloop-dev/odayplus/actions/runs/example-{artifact_type}",
            "redacted": True,
            "contains_secret_values": False,
            "observed_at": "2026-06-30T02:30:00Z",
            "notes": f"Redacted {artifact_type} evidence for {task_id}.",
        }
        for artifact_type in template_entry["minimum_artifact_types"]
    ]
    artifact_ids = [artifact["artifact_id"] for artifact in artifacts]

    return {
        "task_id": task_id,
        "tracking_issue": queue_entry["tracking_issue"],
        "release_head_ref_oid": EXPECTED_SHA,
        "executed_at": "2026-06-30T02:31:00Z",
        "executed_by": "Platform/Ops",
        "environment": template_entry["handoff_environment"],
        "correlation_ids": ["corr-odp-map-stage-001"],
        "redaction_summary": "Secret values and provider tokens were redacted before attachment.",
        "artifacts": artifacts,
        "commands_run": [
            {
                "command": queue_entry["allowed_commands"][0],
                "exit_code": 0,
                "observed_at": "2026-06-30T02:32:00Z",
                "notes": "Release head was fetched at execution time.",
            }
        ],
        "required_evidence_results": [
            {
                "evidence": evidence,
                "status": "proven",
                "artifact_ids": artifact_ids,
                "notes": f"Evidence item satisfied for {task_id}.",
            }
            for evidence in queue_entry["required_evidence"]
        ],
        "completion_attestation": {
            "accepted_by": "Product Validation",
            "accepted_at": "2026-06-30T02:40:00Z",
            "decision": "accepted",
            "notes": "Accepted after redacted artifact review.",
        },
    }


def run_checker(path: Path, *, expected_sha: str = EXPECTED_SHA) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            str(path),
            "--expected-sha",
            expected_sha,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_external_proof_handback_artifact_checker_accepts_valid_handback(tmp_path) -> None:
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(valid_handback(), indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback artifact checks passed." in result.stdout


def test_external_proof_handback_example_matches_checker_contract() -> None:
    result = run_checker(EXAMPLE, expected_sha=EXAMPLE_SHA)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback artifact checks passed." in result.stdout


def test_external_proof_handback_example_cannot_close_current_release_without_real_sha() -> None:
    result = run_checker(EXAMPLE, expected_sha=EXPECTED_SHA)

    assert result.returncode == 1
    assert "release_head_ref_oid must match --expected-sha" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_wrong_release_sha(tmp_path) -> None:
    payload = valid_handback()
    payload["release_head_ref_oid"] = "0" * 40
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "release_head_ref_oid must match --expected-sha" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_unredacted_artifact(tmp_path) -> None:
    payload = valid_handback()
    payload["artifacts"][0]["redacted"] = False
    payload["artifacts"][0]["contains_secret_values"] = True
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "artifacts[0].redacted must be true" in result.stdout
    assert "artifacts[0].contains_secret_values must be false" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_missing_required_evidence(tmp_path) -> None:
    payload = valid_handback()
    payload["required_evidence_results"] = payload["required_evidence_results"][:-1]
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "missing required evidence results" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_unaccepted_attestation(tmp_path) -> None:
    payload = valid_handback()
    payload["completion_attestation"]["decision"] = "needs_revision"
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "completion_attestation.decision must be accepted before closeout" in result.stdout
