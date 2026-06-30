#!/usr/bin/env python3
"""Validate external runtime proof handback template.

This checker keeps the fleet-facing handback template synchronized with the
external proof closeout queue. It does not validate completed proof artifacts;
it validates the schema fleets must use when they attach real provider, live
map, and remote staging evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"

REQUIRED_COMMON_FIELDS = {
    "task_id",
    "tracking_issue",
    "release_head_ref_oid",
    "executed_at",
    "executed_by",
    "environment",
    "correlation_ids",
    "redaction_summary",
    "artifacts",
    "commands_run",
    "required_evidence_results",
    "completion_attestation",
}

REQUIRED_ARTIFACT_FIELDS = {
    "artifact_id",
    "artifact_type",
    "location",
    "redacted",
    "contains_secret_values",
    "observed_at",
    "notes",
}

FORBIDDEN_TOKENS = {
    "secret values",
    "access tokens",
    "private keys",
    "connection strings",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def validate(queue: dict[str, Any], template: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if template.get("schema_version") != 1:
        errors.append("template schema_version must be 1")

    release_target = template.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("template release_target.pr must be 82")
    if "headRefOid" not in str(release_target.get("authority", "")):
        errors.append("template release_target.authority must mention headRefOid")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("template release_target.must_not_hardcode_dev_hash must be true")

    global_text = "\n".join(str(rule) for rule in template.get("global_rules", []))
    for token in ("Do not include secret values", "PR #82 headRefOid", "correlation_id", "redacted"):
        if token not in global_text:
            errors.append(f"template global_rules missing token: {token}")

    common_fields = set(template.get("required_common_fields", []))
    missing_common = REQUIRED_COMMON_FIELDS - common_fields
    if missing_common:
        errors.append(f"template required_common_fields missing: {sorted(missing_common)}")

    artifact_fields = set((template.get("artifact_contract") or {}).keys())
    missing_artifact = REQUIRED_ARTIFACT_FIELDS - artifact_fields
    if missing_artifact:
        errors.append(f"template artifact_contract missing: {sorted(missing_artifact)}")
    if (template.get("artifact_contract") or {}).get("redacted") is not True:
        errors.append("template artifact_contract.redacted must be true")
    if (template.get("artifact_contract") or {}).get("contains_secret_values") is not False:
        errors.append("template artifact_contract.contains_secret_values must be false")

    attestation = template.get("completion_attestation_contract") or {}
    for field in ("accepted_by", "accepted_at", "decision", "notes"):
        if field not in attestation:
            errors.append(f"template completion_attestation_contract missing {field}")
    if "accepted | rejected | needs_revision" not in str(attestation.get("decision", "")):
        errors.append("template completion_attestation_contract.decision must enumerate accepted/rejected/needs_revision")

    queue_entries = {entry["task_id"]: entry for entry in queue.get("queue", [])}
    template_entries = {entry.get("task_id"): entry for entry in template.get("tasks", [])}
    if set(queue_entries) != set(template_entries):
        errors.append(
            f"template task ids must match external proof queue: "
            f"queue={sorted(queue_entries)}, template={sorted(template_entries)}"
        )

    for task_id, queue_entry in queue_entries.items():
        entry = template_entries.get(task_id)
        if not isinstance(entry, dict):
            continue
        prefix = f"{task_id}"
        if entry.get("tracking_issue") != queue_entry.get("tracking_issue"):
            errors.append(f"{prefix} tracking_issue must match external proof queue")
        if entry.get("owner") != queue_entry.get("owner"):
            errors.append(f"{prefix} owner must match external proof queue")
        if entry.get("handoff_environment") not in {"production", "remote_staging", "staging_equivalent"}:
            errors.append(f"{prefix} handoff_environment is invalid")
        if not non_empty_string_list(entry.get("minimum_artifact_types")):
            errors.append(f"{prefix} minimum_artifact_types must be non-empty")
        if not non_empty_string_list(entry.get("forbidden_artifact_content")):
            errors.append(f"{prefix} forbidden_artifact_content must be non-empty")
        forbidden_text = "\n".join(entry.get("forbidden_artifact_content", [])).lower()
        if not any(token in forbidden_text for token in FORBIDDEN_TOKENS):
            errors.append(f"{prefix} forbidden_artifact_content must mention secret/token/key/connection-string risk")
        required_results = entry.get("required_evidence_results", [])
        if required_results != queue_entry.get("required_evidence"):
            errors.append(f"{prefix} required_evidence_results must exactly mirror external proof queue")

    return errors


def main() -> int:
    errors = validate(load_json(QUEUE_PATH), load_json(TEMPLATE_PATH))
    if errors:
        print("External proof handback template check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof handback template checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
