#!/usr/bin/env python3
"""Validate the development-plan execution control pack.

The JSON packet is the machine-readable expansion of the 19 unresolved
ODP-PLAN task contracts.  An optional live ai-status path also verifies that
Supervisor task briefs have been enriched from this packet.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"
DEFAULT_MARKDOWN = ROOT / "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md"

EXPECTED_COMPLETED = {
    "ODP-PLAN-CANONICAL-SHELL-LIVE-001",
    "ODP-PLAN-DEFERRED-OSS-ADR-001",
    "ODP-PLAN-GAP-ARCHIVE-001",
    "ODP-PLAN-GATE-REGISTRY-001",
    "ODP-PLAN-HEATZONE-OUTCOME-001",
    "ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001",
    "ODP-PLAN-SOLVER-RUNTIME-COMPAT-001",
}

EXPECTED_UNRESOLVED = {
    "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001",
    "ODP-PLAN-AVM-OUTCOME-001",
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001",
    "ODP-PLAN-ENGINEERING-HARDENING-001",
    "ODP-PLAN-FINAL-GATE-AUDIT-001",
    "ODP-PLAN-FORECAST-BUSINESS-001",
    "ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001",
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001",
    "ODP-PLAN-LIVE-STAGING-PROOF-001",
    "ODP-PLAN-NETPLAN-ACCEPTANCE-001",
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001",
    "ODP-PLAN-OBSERVABILITY-LIVE-001",
    "ODP-PLAN-OSS-LEGAL-POLICY-001",
    "ODP-PLAN-OSS-LICENSE-GATE-001",
    "ODP-PLAN-PRICE-ADLIFT-PILOT-001",
    "ODP-PLAN-SITESCORE-OUTCOME-001",
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
    "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001",
    "ODP-PLAN-UAT-SIGNOFF-001",
}

EXPECTED_HUMAN_GATES = {
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001",
    "ODP-PLAN-FINAL-GATE-AUDIT-001",
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001",
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001",
    "ODP-PLAN-OSS-LEGAL-POLICY-001",
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
    "ODP-PLAN-UAT-SIGNOFF-001",
}

REQUIRED_PACKET_LIST_FIELDS = (
    "gap_ids",
    "batch_deliverables",
    "must_reject",
    "evidence",
    "verification",
)

REQUIRED_GLOBAL_LIST_FIELDS = (
    "scope_freeze",
    "owner_pre_handoff",
    "review",
    "pr_merge_closeout",
    "deployment",
    "human_gate",
)


def _load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def validate_packet(
    packet_path: Path = DEFAULT_PACKET,
    markdown_path: Path = DEFAULT_MARKDOWN,
    live_status_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []

    if not packet_path.is_file():
        return [f"execution packet missing: {packet_path}"]
    if not markdown_path.is_file():
        return [f"execution packet markdown missing: {markdown_path}"]

    try:
        packet = _load_object(packet_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"execution packet is unreadable: {exc}"]

    if packet.get("schema_version") != "1.0.0":
        errors.append("schema_version must be 1.0.0")
    if packet.get("packet_id") != "ODP-PLAN-EXECUTION-CONTROL-PACK-001":
        errors.append("unexpected packet_id")
    if packet.get("program_id") != "ODP-PLAN-GAP-CLOSEOUT-2026-07-30":
        errors.append("unexpected program_id")

    coverage = packet.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("coverage must be an object")
        coverage = {}
    expected_coverage = {
        "rtm_rows": 84,
        "rtm_unique_rows": 84,
        "governance_tasks": 26,
        "completed_tasks": 7,
        "unresolved_tasks": 19,
        "stage_distribution": [12, 12, 10, 9, 11, 12, 11, 7],
    }
    for key, expected in expected_coverage.items():
        if coverage.get(key) != expected:
            errors.append(f"coverage.{key} must equal {expected!r}")

    completed = packet.get("completed_task_ids")
    if not isinstance(completed, list) or set(completed) != EXPECTED_COMPLETED:
        errors.append("completed_task_ids must contain the exact seven archived tasks")
        completed = completed if isinstance(completed, list) else []
    if len(completed) != len(set(completed)):
        errors.append("completed_task_ids contains duplicates")

    global_contract = packet.get("global_execution_contract")
    if not isinstance(global_contract, dict):
        errors.append("global_execution_contract must be an object")
        global_contract = {}
    for field in REQUIRED_GLOBAL_LIST_FIELDS:
        value = global_contract.get(field)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            errors.append(f"global_execution_contract.{field} must be a non-empty string list")

    task_packets = packet.get("task_packets")
    if not isinstance(task_packets, list):
        errors.append("task_packets must be an array")
        task_packets = []

    ids = [item.get("task_id") for item in task_packets if isinstance(item, dict)]
    if len(task_packets) != 19:
        errors.append(f"task_packets must contain 19 entries, got {len(task_packets)}")
    if len(ids) != len(set(ids)):
        errors.append("task_packets contains duplicate task ids")
    if set(ids) != EXPECTED_UNRESOLVED:
        missing = sorted(EXPECTED_UNRESOLVED - set(ids))
        extra = sorted(set(ids) - EXPECTED_UNRESOLVED)
        errors.append(f"task packet id mismatch; missing={missing}, extra={extra}")
    if set(completed) & set(ids):
        errors.append("completed and unresolved task ids overlap")
    if len(set(completed) | set(ids)) != 26:
        errors.append("completed plus unresolved task ids must total 26")

    markdown = markdown_path.read_text(encoding="utf-8")
    for index, item in enumerate(task_packets):
        if not isinstance(item, dict):
            errors.append(f"task_packets[{index}] must be an object")
            continue
        task_id = item.get("task_id")
        task_class = item.get("class")
        expected_class = "human_gate" if task_id in EXPECTED_HUMAN_GATES else "implementation"
        if task_class != expected_class:
            errors.append(f"{task_id}: class must be {expected_class}")
        for field in REQUIRED_PACKET_LIST_FIELDS:
            value = item.get(field)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(entry, str) and entry.strip() for entry in value)
            ):
                errors.append(f"{task_id}: {field} must be a non-empty string list")
        if not isinstance(item.get("handoff_gate"), str) or not item["handoff_gate"].strip():
            errors.append(f"{task_id}: handoff_gate must be a non-empty string")
        if task_id not in markdown:
            errors.append(f"{task_id}: missing from markdown index")

    implementation_count = sum(
        isinstance(item, dict) and item.get("class") == "implementation" for item in task_packets
    )
    human_count = sum(
        isinstance(item, dict) and item.get("class") == "human_gate" for item in task_packets
    )
    if implementation_count != 12 or human_count != 7:
        errors.append(
            f"task class distribution must be implementation=12, human_gate=7; "
            f"got {implementation_count}/{human_count}"
        )

    if "每修一個 finding 就開 PR 或部署" not in markdown:
        errors.append("markdown must state the no-one-finding-per-PR/deploy rule")
    if "ODP-PLAN-LIVE-STAGING-PROOF-001" not in markdown:
        errors.append("markdown must state the single planned deployment task")
    if "`NO-GO`" not in markdown:
        errors.append("markdown must preserve the NO-GO release claim")

    if live_status_path is not None:
        errors.extend(_validate_live_status(task_packets, live_status_path))

    return errors


def _validate_live_status(task_packets: list[dict[str, Any]], live_status_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        status = _load_object(live_status_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"live status is unreadable: {exc}"]

    tasks = {
        task.get("id"): task
        for task in status.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    packet_source = "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"

    for packet in task_packets:
        task_id = packet["task_id"]
        task = tasks.get(task_id)
        if task is None:
            errors.append(f"{task_id}: absent from live task state")
            continue
        if task.get("owner") == task.get("reviewer"):
            errors.append(f"{task_id}: owner must not equal reviewer")
        if task.get("task_class") != packet.get("class"):
            errors.append(
                f"{task_id}: live task_class {task.get('task_class')!r} "
                f"does not match packet {packet.get('class')!r}"
            )
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or len(acceptance) < 5:
            errors.append(
                f"{task_id}: live acceptance must contain at least five granular criteria"
            )
        source_docs = task.get("source_docs")
        if not isinstance(source_docs, list) or packet_source not in source_docs:
            errors.append(f"{task_id}: live source_docs must reference the control-pack JSON")
        artifacts = task.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            errors.append(f"{task_id}: live artifacts must be non-empty")
        verification = task.get("verification")
        if not isinstance(verification, list) or not verification:
            errors.append(f"{task_id}: live verification must be non-empty")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    errors = validate_packet(args.packet, args.markdown, args.status)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "Execution control pack valid: 84 RTM rows, 26 governance tasks, 19 granular open-task packets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
