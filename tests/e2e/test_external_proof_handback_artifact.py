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

TASK_NOTE_CONTEXT = {
    "ODP-EXT-PROD-001": (
        "Redacted production credential evidence lists credential names only, secret owner, "
        "rotation policy, startup validation, and fail closed behavior for missing, placeholder, "
        "expired, and revoked credentials."
    ),
    "ODP-EXT-PROD-002": (
        "Redacted allowed-use license attestation covers production listing snapshot lineage, "
        "canonical snapshot id, freshness SLA, and export restriction watermark behavior."
    ),
    "ODP-EXT-PROD-003": (
        "Redacted production geocoder proof includes observed timestamp, confidence mapping, "
        "low-confidence handling, timeout, unauthorized, rate-limit, and fail closed behavior."
    ),
    "ODP-MAP-STAGE-001": (
        "Redacted remote staging evidence for staging tile endpoint, attribution, terms, "
        "tile outage fallback, and list/ranking/detail workflow proof."
    ),
    "ODP-MAP-STAGE-002": (
        "Redacted remote staging evidence for staging geocoder endpoint, attribution, terms, "
        "geocoder outage fallback, and list workflow proof."
    ),
    "ODP-PV-STAGE-001": (
        "Redacted remote staging evidence covers ODP_STAGING_DEPLOY_URL, ODP_STAGING_API_URL, "
        "ODP_STAGING_SECRET_OWNER, ODAY_RELEASE_SHA, /platform/health, /platform/version, "
        "and PR #82 headRefOid match."
    ),
    "ODP-PV-STAGE-002": (
        "Redacted remote staging drill evidence proves the same staging target, product smoke, "
        "API smoke, backup artifact, restore target, rollback result, post-drill health/version, "
        "and correlation id."
    ),
}


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

    artifact_note = TASK_NOTE_CONTEXT[task_id]
    evidence_note = f"{TASK_NOTE_CONTEXT[task_id]} Evidence item satisfied for {task_id}."

    for artifact in artifacts:
        artifact["notes"] = f"{artifact_note} Artifact type: {artifact['artifact_type']}."

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
                "command": command,
                "exit_code": 0,
                "observed_at": "2026-06-30T02:32:00Z",
                "notes": f"Release head was fetched at execution time. {evidence_note}",
            }
            for command in queue_entry["allowed_commands"]
        ],
        "required_evidence_results": [
            {
                "evidence": evidence,
                "status": "proven",
                "artifact_ids": artifact_ids,
                "notes": evidence_note,
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


def run_checker(
    path: Path, *, expected_sha: str = EXPECTED_SHA
) -> subprocess.CompletedProcess[str]:
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


def test_external_proof_handback_artifact_checker_accepts_valid_map_geocoder_handback(
    tmp_path,
) -> None:
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(valid_handback("ODP-MAP-STAGE-002"), indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback artifact checks passed." in result.stdout


def test_external_proof_handback_artifact_checker_accepts_valid_handback_for_every_task(
    tmp_path,
) -> None:
    queue_entries = load_json(QUEUE)["queue"]

    for entry in queue_entries:
        handback = tmp_path / f"{entry['task_id']}.json"
        handback.write_text(
            json.dumps(valid_handback(entry["task_id"]), indent=2), encoding="utf-8"
        )

        result = run_checker(handback)

        assert result.returncode == 0, result.stdout + result.stderr


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


def test_external_proof_handback_artifact_checker_rejects_missing_required_evidence(
    tmp_path,
) -> None:
    payload = valid_handback()
    payload["required_evidence_results"] = payload["required_evidence_results"][:-1]
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "missing required evidence results" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_missing_queue_command_fragment(
    tmp_path,
) -> None:
    payload = valid_handback("ODP-PV-STAGE-002")
    payload["commands_run"] = payload["commands_run"][:1]
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "commands_run missing required queue command fragment" in result.stdout
    assert "check_remote_staging_proof.py" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_map_handback_without_live_boundary_notes(
    tmp_path,
) -> None:
    payload = valid_handback("ODP-MAP-STAGE-001")
    for artifact in payload["artifacts"]:
        artifact["notes"] = "Redacted screenshot and report were reviewed."
    for command in payload["commands_run"]:
        command["notes"] = "Command completed."
    for result_item in payload["required_evidence_results"]:
        result_item["notes"] = "Evidence accepted."
    payload["redaction_summary"] = (
        "Secret values and provider tokens were redacted before attachment."
    )
    payload["completion_attestation"]["notes"] = "Accepted after redacted artifact review."

    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "handback evidence notes must mention 'staging tile endpoint'" in result.stdout
    assert "handback evidence notes must mention 'tile outage'" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_provider_handback_without_task_notes(
    tmp_path,
) -> None:
    payload = valid_handback("ODP-EXT-PROD-001")
    for artifact in payload["artifacts"]:
        artifact["notes"] = "Redacted provider evidence was reviewed."
    for command in payload["commands_run"]:
        command["notes"] = "Command completed."
    for result_item in payload["required_evidence_results"]:
        result_item["notes"] = "Evidence accepted."
    payload["redaction_summary"] = (
        "Secret values and provider tokens were redacted before attachment."
    )
    payload["completion_attestation"]["notes"] = "Accepted after redacted artifact review."

    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "handback evidence notes must mention 'production credential'" in result.stdout
    assert "handback evidence notes must mention 'secret owner'" in result.stdout
    assert "handback evidence notes must mention 'fail closed'" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_map_handback_that_uses_mock_endpoint(
    tmp_path,
) -> None:
    payload = valid_handback("ODP-MAP-STAGE-001")
    payload["artifacts"][0]["notes"] += " Captured endpoint mock://tiles/{z}/{x}/{y}.png."

    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "must not rely on local/mock endpoint token 'mock://'" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_staging_handback_that_uses_fixture_proof(
    tmp_path,
) -> None:
    payload = valid_handback("ODP-PV-STAGE-002")
    payload["required_evidence_results"][0]["notes"] += (
        " This was verified from deterministic fixture output."
    )

    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "must not rely on local/mock endpoint token 'fixture'" in result.stdout


def test_external_proof_handback_artifact_checker_rejects_unaccepted_attestation(tmp_path) -> None:
    payload = valid_handback()
    payload["completion_attestation"]["decision"] = "needs_revision"
    handback = tmp_path / "handback.json"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_checker(handback)

    assert result.returncode == 1
    assert "completion_attestation.decision must be accepted before closeout" in result.stdout
