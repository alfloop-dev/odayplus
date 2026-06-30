#!/usr/bin/env python3
"""Validate completed external proof handback artifacts.

The template checker validates the contract fleets must use. This checker
validates a concrete handback JSON before Product Validation accepts or closes
external-proof issues #132-#138.

It intentionally does not contact providers or staging targets. The live proof
must already be captured in redacted artifacts; this script verifies that the
handback cites the current release head, maps to the closeout queue, contains
all required evidence results, and does not claim completion with unredacted or
missing artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
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

ALLOWED_EVIDENCE_STATUSES = {"passed", "proven", "accepted"}
ALLOWED_ATTESTATION_DECISIONS = {"accepted", "rejected", "needs_revision"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:password|token|secret|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,*]{8,}"),
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(is_non_empty_string(item) for item in value)


def collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(collect_strings(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for item in value.values():
            strings.extend(collect_strings(item))
        return strings
    return []


def has_secret_like_value(payload: dict[str, Any]) -> bool:
    text_values = "\n".join(collect_strings(payload))
    return any(pattern.search(text_values) for pattern in SECRET_VALUE_PATTERNS)


def normalize_evidence_results(value: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    normalized: dict[str, dict[str, Any]] = {}

    if not isinstance(value, list) or not value:
        return {}, ["required_evidence_results must be a non-empty list of objects"]

    for index, item in enumerate(value):
        prefix = f"required_evidence_results[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        evidence = item.get("evidence")
        status = item.get("status")
        artifact_ids = item.get("artifact_ids")
        if not is_non_empty_string(evidence):
            errors.append(f"{prefix}.evidence must be non-empty")
            continue
        if status not in ALLOWED_EVIDENCE_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ALLOWED_EVIDENCE_STATUSES)}")
        if not is_string_list(artifact_ids):
            errors.append(f"{prefix}.artifact_ids must be a non-empty string list")
        if not is_non_empty_string(item.get("notes")):
            errors.append(f"{prefix}.notes must be non-empty")
        normalized[str(evidence)] = item

    return normalized, errors


def validate_handback(
    handback: dict[str, Any],
    queue_entries: dict[str, dict[str, Any]],
    template_entries: dict[str, dict[str, Any]],
    *,
    expected_sha: str | None,
) -> list[str]:
    errors: list[str] = []

    missing = REQUIRED_COMMON_FIELDS - set(handback)
    if missing:
        errors.append(f"missing common fields: {sorted(missing)}")

    if has_secret_like_value(handback):
        errors.append("handback contains a string that looks like an unredacted secret value")

    task_id = handback.get("task_id")
    if task_id not in queue_entries or task_id not in template_entries:
        errors.append(f"task_id must match external proof queue/template, got {task_id!r}")
        return errors

    queue_entry = queue_entries[str(task_id)]
    template_entry = template_entries[str(task_id)]
    prefix = str(task_id)

    if handback.get("tracking_issue") != queue_entry.get("tracking_issue"):
        errors.append(f"{prefix} tracking_issue must match queue")

    release_sha = handback.get("release_head_ref_oid")
    if not isinstance(release_sha, str) or not SHA_RE.match(release_sha):
        errors.append(f"{prefix} release_head_ref_oid must be a 40-character lowercase git SHA")
    if expected_sha and release_sha != expected_sha:
        errors.append(f"{prefix} release_head_ref_oid must match --expected-sha")

    if not is_non_empty_string(handback.get("executed_at")):
        errors.append(f"{prefix} executed_at must be non-empty")
    if not is_non_empty_string(handback.get("executed_by")):
        errors.append(f"{prefix} executed_by must be non-empty")

    if handback.get("environment") != template_entry.get("handoff_environment"):
        errors.append(
            f"{prefix} environment must equal template handoff_environment "
            f"{template_entry.get('handoff_environment')!r}"
        )

    if not is_string_list(handback.get("correlation_ids")):
        errors.append(f"{prefix} correlation_ids must be a non-empty string list")
    if not is_non_empty_string(handback.get("redaction_summary")):
        errors.append(f"{prefix} redaction_summary must be non-empty")

    commands = handback.get("commands_run")
    if not isinstance(commands, list) or not commands:
        errors.append(f"{prefix} commands_run must be a non-empty list")
    else:
        for index, command in enumerate(commands):
            command_prefix = f"{prefix} commands_run[{index}]"
            if not isinstance(command, dict):
                errors.append(f"{command_prefix} must be an object")
                continue
            if not is_non_empty_string(command.get("command")):
                errors.append(f"{command_prefix}.command must be non-empty")
            if command.get("exit_code") != 0:
                errors.append(f"{command_prefix}.exit_code must be 0")
            if not is_non_empty_string(command.get("observed_at")):
                errors.append(f"{command_prefix}.observed_at must be non-empty")

    artifacts = handback.get("artifacts")
    artifact_ids: set[str] = set()
    artifact_types: set[str] = set()
    if not isinstance(artifacts, list) or not artifacts:
        errors.append(f"{prefix} artifacts must be a non-empty list")
    else:
        for index, artifact in enumerate(artifacts):
            artifact_prefix = f"{prefix} artifacts[{index}]"
            if not isinstance(artifact, dict):
                errors.append(f"{artifact_prefix} must be an object")
                continue
            missing_artifact = REQUIRED_ARTIFACT_FIELDS - set(artifact)
            if missing_artifact:
                errors.append(f"{artifact_prefix} missing fields: {sorted(missing_artifact)}")
            artifact_id = artifact.get("artifact_id")
            artifact_type = artifact.get("artifact_type")
            if not is_non_empty_string(artifact_id):
                errors.append(f"{artifact_prefix}.artifact_id must be non-empty")
            else:
                artifact_ids.add(str(artifact_id))
            if not is_non_empty_string(artifact_type):
                errors.append(f"{artifact_prefix}.artifact_type must be non-empty")
            else:
                artifact_types.add(str(artifact_type))
            if not is_non_empty_string(artifact.get("location")):
                errors.append(f"{artifact_prefix}.location must be non-empty")
            if artifact.get("redacted") is not True:
                errors.append(f"{artifact_prefix}.redacted must be true")
            if artifact.get("contains_secret_values") is not False:
                errors.append(f"{artifact_prefix}.contains_secret_values must be false")
            if not is_non_empty_string(artifact.get("observed_at")):
                errors.append(f"{artifact_prefix}.observed_at must be non-empty")
            if not is_non_empty_string(artifact.get("notes")):
                errors.append(f"{artifact_prefix}.notes must be non-empty")

    minimum_types = set(template_entry.get("minimum_artifact_types", []))
    missing_types = minimum_types - artifact_types
    if missing_types:
        errors.append(f"{prefix} artifacts missing minimum artifact types: {sorted(missing_types)}")

    evidence_results, evidence_errors = normalize_evidence_results(handback.get("required_evidence_results"))
    errors.extend(f"{prefix} {error}" for error in evidence_errors)
    required_evidence = set(queue_entry.get("required_evidence", []))
    missing_evidence = required_evidence - set(evidence_results)
    extra_evidence = set(evidence_results) - required_evidence
    if missing_evidence:
        errors.append(f"{prefix} missing required evidence results: {sorted(missing_evidence)}")
    if extra_evidence:
        errors.append(f"{prefix} has evidence results not present in queue: {sorted(extra_evidence)}")
    for evidence, result in evidence_results.items():
        result_artifact_ids = set(result.get("artifact_ids", []))
        missing_artifact_refs = result_artifact_ids - artifact_ids
        if missing_artifact_refs:
            errors.append(f"{prefix} evidence {evidence!r} references unknown artifact ids: {sorted(missing_artifact_refs)}")

    attestation = handback.get("completion_attestation")
    if not isinstance(attestation, dict):
        errors.append(f"{prefix} completion_attestation must be an object")
    else:
        if not is_non_empty_string(attestation.get("accepted_by")):
            errors.append(f"{prefix} completion_attestation.accepted_by must be non-empty")
        if not is_non_empty_string(attestation.get("accepted_at")):
            errors.append(f"{prefix} completion_attestation.accepted_at must be non-empty")
        if attestation.get("decision") not in ALLOWED_ATTESTATION_DECISIONS:
            errors.append(
                f"{prefix} completion_attestation.decision must be one of "
                f"{sorted(ALLOWED_ATTESTATION_DECISIONS)}"
            )
        if attestation.get("decision") != "accepted":
            errors.append(f"{prefix} completion_attestation.decision must be accepted before closeout")
        if not is_non_empty_string(attestation.get("notes")):
            errors.append(f"{prefix} completion_attestation.notes must be non-empty")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handback", nargs="+", type=Path, help="Completed external proof handback JSON file(s).")
    parser.add_argument("--expected-sha", help="Require each handback to cite this PR #82 headRefOid.")
    args = parser.parse_args()

    queue = load_json(QUEUE_PATH)
    template = load_json(TEMPLATE_PATH)
    queue_entries = {entry["task_id"]: entry for entry in queue.get("queue", [])}
    template_entries = {entry["task_id"]: entry for entry in template.get("tasks", [])}

    errors: list[str] = []
    for path in args.handback:
        try:
            handback = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: unable to load JSON: {exc}")
            continue
        for error in validate_handback(
            handback,
            queue_entries,
            template_entries,
            expected_sha=args.expected_sha,
        ):
            errors.append(f"{path}: {error}")

    if errors:
        print("External proof handback artifact check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof handback artifact checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
