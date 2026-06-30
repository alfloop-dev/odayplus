#!/usr/bin/env python3
"""Generate fillable external proof handback skeletons for fleet tasks.

The generated JSON is intentionally not accepted closeout evidence. It contains
the correct task id, tracking issue, environment, minimum artifact slots, and
required evidence result slots, but leaves placeholders and a `needs_revision`
attestation so Product Validation must still run the artifact checker against
real, redacted proof before closing #132-#138.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"
EXAMPLE_SHA = "1111111111111111111111111111111111111111"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_skeleton(task_id: str, *, release_sha: str) -> dict[str, Any]:
    queue_entries = {entry["task_id"]: entry for entry in load_json(QUEUE_PATH).get("queue", [])}
    template_entries = {entry["task_id"]: entry for entry in load_json(TEMPLATE_PATH).get("tasks", [])}
    if task_id not in queue_entries or task_id not in template_entries:
        raise KeyError(f"unknown external proof task: {task_id}")

    queue_entry = queue_entries[task_id]
    template_entry = template_entries[task_id]
    artifact_ids: list[str] = []
    artifacts: list[dict[str, Any]] = []
    for artifact_type in template_entry["minimum_artifact_types"]:
        artifact_id = f"replace-with-{task_id.lower()}-{artifact_type}-artifact-id"
        artifact_ids.append(artifact_id)
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "location": "REPLACE_WITH_REDACTED_ARTIFACT_URL_OR_PATH",
                "redacted": True,
                "contains_secret_values": False,
                "observed_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
                "notes": f"Replace with redacted {artifact_type} proof notes for {task_id}.",
            }
        )

    return {
        "task_id": task_id,
        "tracking_issue": queue_entry["tracking_issue"],
        "release_head_ref_oid": release_sha,
        "executed_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
        "executed_by": queue_entry["owner"],
        "environment": template_entry["handoff_environment"],
        "correlation_ids": [f"corr-replace-with-{task_id.lower()}"],
        "redaction_summary": (
            "Replace with a summary confirming secret values, provider tokens, private keys, "
            "connection strings, and unredacted provider payloads are absent."
        ),
        "artifacts": artifacts,
        "commands_run": [
            {
                "command": command,
                "exit_code": "REPLACE_WITH_0_AFTER_SUCCESS",
                "observed_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
                "notes": "Replace with command result summary and artifact reference.",
            }
            for command in queue_entry["allowed_commands"]
        ],
        "required_evidence_results": [
            {
                "evidence": evidence,
                "status": "REPLACE_WITH_passed_OR_proven_OR_accepted",
                "artifact_ids": artifact_ids,
                "notes": "Replace with what the redacted artifact proves for this evidence item.",
            }
            for evidence in queue_entry["required_evidence"]
        ],
        "completion_attestation": {
            "accepted_by": "Product Validation",
            "accepted_at": "REPLACE_WITH_ISO_8601_TIMESTAMP_AFTER_REVIEW",
            "decision": "needs_revision",
            "notes": "Skeleton only. Product Validation changes this to accepted only after artifact checker passes.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="External proof task id, or ALL.")
    parser.add_argument(
        "--release-sha",
        default=EXAMPLE_SHA,
        help="Release SHA to place in the skeleton. Use PR #82 headRefOid for real handbacks.",
    )
    parser.add_argument("--output-dir", type=Path, help="Write skeleton file(s) into this directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_entries = [entry["task_id"] for entry in load_json(QUEUE_PATH).get("queue", [])]
    task_ids = queue_entries if args.task == "ALL" else [args.task]

    skeletons = {task_id: build_skeleton(task_id, release_sha=args.release_sha) for task_id in task_ids}
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for task_id, skeleton in skeletons.items():
            output = args.output_dir / f"{task_id}.handback.skeleton.json"
            output.write_text(json.dumps(skeleton, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(output)
        return 0

    payload: Any = skeletons[task_ids[0]] if len(task_ids) == 1 else skeletons
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
