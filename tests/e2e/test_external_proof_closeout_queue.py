from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
CHECKER = ROOT / "scripts/e2e/check_external_proof_closeout_queue.py"


def test_external_proof_closeout_queue_is_validated_by_checker() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof closeout queue checks passed." in result.stdout


def test_external_proof_closeout_queue_covers_live_provider_map_and_staging_tasks() -> None:
    payload = json.loads(QUEUE.read_text(encoding="utf-8"))
    task_ids = {entry["task_id"] for entry in payload["queue"]}

    assert {
        "ODP-EXT-PROD-001",
        "ODP-EXT-PROD-002",
        "ODP-EXT-PROD-003",
        "ODP-MAP-STAGE-001",
        "ODP-MAP-STAGE-002",
        "ODP-PV-STAGE-001",
        "ODP-PV-STAGE-002",
    } <= task_ids
    assert {boundary["topic"] for boundary in payload["proof_boundaries"]} == {
        "external_data_sources",
        "maps",
        "remote_staging",
    }
    assert payload["release_target"]["must_not_hardcode_dev_hash"] is True


def test_external_proof_tasks_do_not_allow_local_or_mock_proof_to_close_live_claims() -> None:
    payload = json.loads(QUEUE.read_text(encoding="utf-8"))

    for entry in payload["queue"]:
        assert entry["status"] == "external_blocked"
        assert "Do not close" in entry["completion_rule"]
        assert "gh pr view 82" in "\n".join(entry["allowed_commands"])
        assert entry["required_evidence"]
