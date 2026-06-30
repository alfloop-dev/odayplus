from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"
CHECKER = ROOT / "scripts/e2e/check_external_proof_handback_template.py"


def test_external_proof_handback_template_checker_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback template checks passed." in result.stdout


def test_external_proof_handback_template_matches_closeout_queue() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    queue_entries = {entry["task_id"]: entry for entry in queue["queue"]}
    template_entries = {entry["task_id"]: entry for entry in template["tasks"]}

    assert set(queue_entries) == set(template_entries)
    assert template["release_target"]["pr"] == 82
    assert "headRefOid" in template["release_target"]["authority"]
    assert template["artifact_contract"]["redacted"] is True
    assert template["artifact_contract"]["contains_secret_values"] is False

    for task_id, queue_entry in queue_entries.items():
        template_entry = template_entries[task_id]
        assert template_entry["tracking_issue"] == queue_entry["tracking_issue"]
        assert template_entry["owner"] == queue_entry["owner"]
        assert template_entry["required_evidence_results"] == queue_entry["required_evidence"]
        assert template_entry["minimum_artifact_types"]
        assert template_entry["forbidden_artifact_content"]


def test_external_proof_handback_template_requires_redaction_and_attestation() -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    common_fields = set(template["required_common_fields"])
    artifact_fields = set(template["artifact_contract"])
    attestation_fields = set(template["completion_attestation_contract"])
    global_rules = "\n".join(template["global_rules"])

    assert {"release_head_ref_oid", "correlation_ids", "redaction_summary", "completion_attestation"} <= common_fields
    assert {"artifact_id", "artifact_type", "redacted", "contains_secret_values", "observed_at"} <= artifact_fields
    assert {"accepted_by", "accepted_at", "decision", "notes"} <= attestation_fields
    assert "Do not include secret values" in global_rules
    assert "deterministic/mock-live proof separate" in global_rules
