from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNCER = ROOT / "scripts/e2e/sync_external_proof_escalation_comments.py"
EXPECTED_SHA = "8629bf521c17b2c473a90dec49b43ba8737d09aa"


def load_syncer_module():
    spec = importlib.util.spec_from_file_location("sync_external_proof_escalation_comments", SYNCER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queue_entry() -> dict:
    return {
        "task_id": "ODP-MAP-STAGE-001",
        "required_evidence": [
            "staging map tile URL configured",
            "provider attribution and terms URL visible",
        ],
        "handback_commands": [
            "python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha-from-pr82 --output <handback.json>",
            "python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha \"$(gh pr view 82 --json headRefOid --jq .headRefOid)\"",
        ],
        "fleet_routing": {
            "escalation": "Platform/Ops owns staging tile endpoint configuration; Product Validation reviews remote smoke and fallback proof before closure."
        },
    }


def scan_row(*, escalation_due: bool = True) -> dict:
    return {
        "task_id": "ODP-MAP-STAGE-001",
        "issue": "#135",
        "status": "no_handback_after_latest_pickup",
        "latest_pickup_created_at": "2026-06-30T12:00:00Z",
        "pickup_age_hours": 25.5,
        "escalation_due": escalation_due,
        "candidate_handback_comments": [],
    }


def test_render_escalation_comment_contains_acceptance_contract() -> None:
    syncer = load_syncer_module()

    comment = syncer.render_escalation_comment(
        queue_entry=queue_entry(),
        scan_row=scan_row(),
        expected_sha=EXPECTED_SHA,
        escalation_hours=24,
    )

    assert "External proof handback escalation" in comment
    assert "ODP-MAP-STAGE-001" in comment
    assert EXPECTED_SHA in comment
    assert "Age since pickup: `25.5h`" in comment
    assert "staging map tile URL configured" in comment
    assert "generate_external_proof_handback_skeleton.py" in comment
    assert "check_external_proof_handback_artifact.py <handback.json>" in comment


def test_rows_to_escalate_selects_due_rows_only_unless_forced() -> None:
    syncer = load_syncer_module()
    queue = {"queue": [queue_entry()]}
    not_due = scan_row(escalation_due=False)

    assert syncer.rows_to_escalate(queue, [not_due], force=False) == []
    assert syncer.rows_to_escalate(queue, [not_due], force=True)[0][1] == not_due


def test_rows_to_escalate_selects_due_rows() -> None:
    syncer = load_syncer_module()
    queue = {"queue": [queue_entry()]}
    due = scan_row(escalation_due=True)

    selected = syncer.rows_to_escalate(queue, [due], force=False)

    assert len(selected) == 1
    assert selected[0][0]["task_id"] == "ODP-MAP-STAGE-001"


def test_escalation_comment_already_posted_is_release_sha_specific() -> None:
    syncer = load_syncer_module()
    issue = {
        "comments": [
            {
                "body": (
                    "## External proof handback escalation - 2026-06-30\n\n"
                    "Task: `ODP-MAP-STAGE-001` (#135)\n"
                    f"Release target: PR #82 headRefOid `{EXPECTED_SHA}`\n"
                )
            }
        ]
    }

    assert syncer.escalation_comment_already_posted(
        issue,
        task_id="ODP-MAP-STAGE-001",
        expected_sha=EXPECTED_SHA,
    )
    assert not syncer.escalation_comment_already_posted(
        issue,
        task_id="ODP-MAP-STAGE-002",
        expected_sha=EXPECTED_SHA,
    )
    assert not syncer.escalation_comment_already_posted(
        issue,
        task_id="ODP-MAP-STAGE-001",
        expected_sha="326bacdd6f43f945609604d0aa2e56c151f840bc",
    )
