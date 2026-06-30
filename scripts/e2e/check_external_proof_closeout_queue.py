#!/usr/bin/env python3
"""Validate external proof closeout tasks for product-grade E2E release.

The product release can prove deterministic and mock-live behavior in CI. This
queue tracks the remaining external proof that must be supplied by Platform/Ops,
Data Partnerships, Legal, or Product Validation before anyone can claim live
provider, live map, or remote staging completion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"

REQUIRED_TASK_IDS = {
    "ODP-EXT-PROD-001",
    "ODP-EXT-PROD-002",
    "ODP-EXT-PROD-003",
    "ODP-MAP-STAGE-001",
    "ODP-MAP-STAGE-002",
    "ODP-PV-STAGE-001",
    "ODP-PV-STAGE-002",
}

REQUIRED_TOPICS = {"external_data_sources", "maps", "remote_staging"}

REQUIRED_BOUNDARY_TOKENS = (
    "provider credential/OAuth provisioning",
    "provider-specific production license approval",
    "provider production listing snapshot",
    "provider production geocoder response",
    "remote staging live tile endpoint smoke",
    "remote staging live geocoder endpoint smoke",
    "staging host/API URL/secret owner configuration",
    "remote /platform/version release_sha match",
    "staging backup/restore/rollback drill",
)

REQUIRED_COMMAND_TOKENS = (
    "gh pr view 82",
    "headRefOid",
    "check_remote_staging_proof.py",
    "PLAYWRIGHT_BASE_URL",
)

FORBIDDEN_COMPLETION_PHRASES = (
    "close from deterministic fixture",
    "close from local MapLibre",
    "close from replay fixture alone",
    "mock-live provider proof is sufficient",
)


def load_payload() -> dict[str, Any]:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    release_target = payload.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("release_target.pr must be 82")
    if "headRefOid" not in str(release_target.get("authority", "")):
        errors.append("release_target.authority must use PR #82 headRefOid")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("release_target.must_not_hardcode_dev_hash must be true")

    preflight = "\n".join(str(command) for command in payload.get("global_preflight", []))
    for required in ("gh pr view 82", "check_product_release_gate.py", "check_external_proof_closeout_queue.py"):
        if required not in preflight:
            errors.append(f"global_preflight missing {required}")

    text = json.dumps(payload, ensure_ascii=False)
    for token in REQUIRED_BOUNDARY_TOKENS:
        if token not in text:
            errors.append(f"queue missing boundary token: {token}")

    topics = {str(boundary.get("topic")) for boundary in payload.get("proof_boundaries", [])}
    missing_topics = REQUIRED_TOPICS - topics
    if missing_topics:
        errors.append(f"missing proof boundary topics: {sorted(missing_topics)}")

    entries = payload.get("queue", [])
    if not isinstance(entries, list) or not entries:
        errors.append("queue must be a non-empty list")
        return errors

    task_ids = {str(entry.get("task_id")) for entry in entries}
    missing_tasks = REQUIRED_TASK_IDS - task_ids
    if missing_tasks:
        errors.append(f"missing external proof tasks: {sorted(missing_tasks)}")

    for index, entry in enumerate(entries):
        prefix = f"queue[{index}] {entry.get('task_id')}"
        for field in (
            "task_id",
            "title",
            "owner",
            "reviewer",
            "status",
            "blocking_type",
            "tracking_issue",
            "required_evidence",
            "allowed_commands",
            "evidence_refs",
            "completion_rule",
        ):
            if field not in entry:
                errors.append(f"{prefix} missing {field}")

        if entry.get("status") != "external_blocked":
            errors.append(f"{prefix} status must be external_blocked")

        tracking_issue = str(entry.get("tracking_issue", ""))
        if not tracking_issue.startswith("https://github.com/alfloop-dev/odayplus/issues/"):
            errors.append(f"{prefix} tracking_issue must link to a GitHub issue")

        if not entry.get("required_evidence"):
            errors.append(f"{prefix} required_evidence must be non-empty")
        if not entry.get("allowed_commands"):
            errors.append(f"{prefix} allowed_commands must be non-empty")
        if not entry.get("evidence_refs"):
            errors.append(f"{prefix} evidence_refs must be non-empty")

        command_text = "\n".join(str(command) for command in entry.get("allowed_commands", []))
        if "gh pr view 82" not in command_text or "headRefOid" not in command_text:
            errors.append(f"{prefix} allowed_commands must verify PR #82 headRefOid")

        for evidence_ref in entry.get("evidence_refs", []):
            ref_path = ROOT / str(evidence_ref)
            if not ref_path.exists():
                errors.append(f"{prefix} evidence ref does not exist: {evidence_ref}")

        completion_rule = str(entry.get("completion_rule", ""))
        if "Do not close" not in completion_rule:
            errors.append(f"{prefix} completion_rule must explicitly say Do not close")

    for token in REQUIRED_COMMAND_TOKENS:
        if token not in text:
            errors.append(f"queue command coverage missing token: {token}")

    for phrase in FORBIDDEN_COMPLETION_PHRASES:
        if phrase in text and "Do not close" not in text[text.find(phrase) - 80 : text.find(phrase) + 120]:
            errors.append(f"forbidden completion phrase lacks Do not close boundary: {phrase}")

    return errors


def main() -> int:
    errors = validate(load_payload())
    if errors:
        print("External proof closeout queue failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("External proof closeout queue checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
