#!/usr/bin/env python3
"""Synchronize granular execution-pack criteria into live Supervisor tasks.

This script deliberately invokes the repository's official ai-status CLI.  It
does not edit ai-status.json or derived task briefs directly.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.ops.validate_plan_execution_pack import (
        EXPECTED_HUMAN_OWNERS,
        EXPECTED_HUMAN_REVIEWERS,
        _archive_snapshot_path,
        validate_archived_packet_state,
    )
except ModuleNotFoundError:  # Direct script execution puts scripts/ops on sys.path.
    from validate_plan_execution_pack import (
        EXPECTED_HUMAN_OWNERS,
        EXPECTED_HUMAN_REVIEWERS,
        _archive_snapshot_path,
        validate_archived_packet_state,
    )

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"
PACKET_MD = "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md"
PACKET_JSON = "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"

DEFAULT_HUMAN_ARTIFACTS = {
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001": ["docs/evidence/models/heatzone/human-data-gate/"],
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001": ["docs/evidence/models/sitescore/human-data-gate/"],
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001": ["docs/evidence/models/avm/human-data-gate/"],
    "ODP-PLAN-OSS-LEGAL-POLICY-001": ["docs/evidence/oss-legal-policy/"],
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001": ["docs/evidence/netplan/baseline-approval/"],
    "ODP-PLAN-UAT-SIGNOFF-001": [
        "docs/uat/",
        "docs/evidence/uat/",
    ],
}


def _unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def build_acceptance(packet: dict[str, Any]) -> list[str]:
    criteria = [
        *(f"Deliverable: {item}" for item in packet["batch_deliverables"]),
        *(f"Fail-closed: {item}" for item in packet["must_reject"]),
        f"Evidence set: {'; '.join(packet['evidence'])}",
        f"Handoff gate: {packet['handoff_gate']}",
        (
            "Batch rule: re-audit every criterion after reopen; do not hand off, "
            "open/refresh PR, or deploy after fixing only the latest reviewer example."
        ),
    ]
    return _unique_strings(criteria)


def build_task_metadata(task: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    task_id = task["id"]
    source_docs = _unique_strings(
        [
            *(task.get("source_docs") or []),
            PACKET_MD,
            PACKET_JSON,
        ]
    )
    artifacts = _unique_strings(
        [
            *(task.get("artifacts") or []),
            *DEFAULT_HUMAN_ARTIFACTS.get(task_id, []),
        ]
    )
    verification = _unique_strings(
        [
            *(task.get("verification") or []),
            *packet["verification"],
        ]
    )
    return {
        "acceptance": build_acceptance(packet),
        "source_docs": source_docs,
        "artifacts": artifacts,
        "verification": verification,
        "task_class": packet["class"],
        "gap_ids": _unique_strings([*(task.get("gap_ids") or []), *packet["gap_ids"]]),
        "execution_packet_id": "ODP-PLAN-EXECUTION-CONTROL-PACK-001",
        "execution_packet_path": PACKET_JSON,
        "execution_mode": "complete-batch-before-handoff-pr-or-deploy",
    }


def sync(packet_path: Path, status_root: Path, actor: str, dry_run: bool) -> None:
    packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
    status_path = status_root / "ai-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    tasks = {
        task["id"]: task
        for task in status.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    cli = status_root / "scripts/ai-status.sh"
    if not cli.is_file():
        raise FileNotFoundError(f"official status CLI missing: {cli}")

    for packet in packet_data["task_packets"]:
        task_id = packet["task_id"]
        task = tasks.get(task_id)
        if task is None:
            archived_task, archive_errors = validate_archived_packet_state(
                packet=packet,
                active_tasks=tasks,
                archive_root=status_root / "ai-task-archive",
            )
            if archive_errors:
                raise ValueError(
                    f"official archive invalid for {task_id}: {'; '.join(archive_errors)}"
                )
            if archived_task is None:
                raise ValueError(f"live task missing: {task_id}")
            if dry_run:
                print(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "state": "official_archive_validated",
                            "action": "skip",
                        },
                        ensure_ascii=False,
                    )
                )
            continue
        archive_path = _archive_snapshot_path(status_root / "ai-task-archive", task_id)
        if archive_path.is_file():
            raise ValueError(f"task exists in both active and official archive state: {task_id}")
        owner = task.get("owner")
        reviewer = task.get("reviewer")
        if task_id in EXPECTED_HUMAN_OWNERS:
            if owner != "Human/Ops":
                if reviewer == "Human/Ops":
                    reviewer = str(owner)
                owner = "Human/Ops"
        if task_id in EXPECTED_HUMAN_REVIEWERS:
            if reviewer != "Human/Ops":
                if owner == "Human/Ops":
                    owner = str(reviewer)
                reviewer = "Human/Ops"
        if not owner or not reviewer or owner == reviewer:
            raise ValueError(f"invalid owner/reviewer for {task_id}: {owner}/{reviewer}")

        metadata = build_task_metadata(task, packet)
        preserved_next = str(task.get("next") or "Execution packet synchronized.")
        command = [
            str(cli),
            "assign",
            task_id,
            str(owner),
            str(reviewer),
            str(task.get("title") or task_id),
        ]
        if dry_run:
            print(
                json.dumps(
                    {
                        "task_id": task_id,
                        "owner": owner,
                        "reviewer": reviewer,
                        "acceptance_count": len(metadata["acceptance"]),
                        "source_docs": metadata["source_docs"],
                        "artifacts": metadata["artifacts"],
                        "verification_count": len(metadata["verification"]),
                    },
                    ensure_ascii=False,
                )
            )
            continue

        env = os.environ.copy()
        env["AI_NAME"] = actor
        env["TASK_METADATA_JSON"] = json.dumps(metadata, ensure_ascii=False)
        subprocess.run(command, cwd=status_root, env=env, check=True)
        subprocess.run(
            [str(cli), "note", task_id, preserved_next],
            cwd=status_root,
            env={**os.environ, "AI_NAME": actor},
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--status-root", type=Path, required=True)
    parser.add_argument("--actor", default="CodexCoordinator")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sync(
        packet_path=args.packet.resolve(),
        status_root=args.status_root.resolve(),
        actor=args.actor,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
