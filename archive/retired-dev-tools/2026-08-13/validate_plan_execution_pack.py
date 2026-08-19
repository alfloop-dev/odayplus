#!/usr/bin/env python3
"""Archived validator for the 2026-07-31 execution control packet.

The JSON packet is the machine-readable expansion of the 19 unresolved
ODP-PLAN task contracts.  An optional live ai-status path also verifies that
Supervisor task briefs have been enriched from this packet.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"
DEFAULT_MARKDOWN = ROOT / "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md"
DEFAULT_MATRIX = ROOT / "docs/evidence/DEVELOPMENT_PLAN_IMPLEMENTATION_GAP_MATRIX_2026-07-30.md"
DEFAULT_LEDGER = ROOT / "docs/evidence/DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md"

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

EXPECTED_HUMAN_OWNERS = {
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001",
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001",
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001",
    "ODP-PLAN-OSS-LEGAL-POLICY-001",
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001",
}

EXPECTED_HUMAN_REVIEWERS = {
    "ODP-PLAN-FINAL-GATE-AUDIT-001",
    "ODP-PLAN-UAT-SIGNOFF-001",
}

EXPECTED_GAP_IDS = {
    "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001": {"GAP-P0-002"},
    "ODP-PLAN-AVM-OUTCOME-001": {"GAP-P1-003"},
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001": {"GAP-P1-003-DATA"},
    "ODP-PLAN-ENGINEERING-HARDENING-001": {"GAP-P2-ENGINEERING"},
    "ODP-PLAN-FINAL-GATE-AUDIT-001": {"FINAL-GATE"},
    "ODP-PLAN-FORECAST-BUSINESS-001": {"GAP-P1-004"},
    "ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001": {"GAP-P0-004"},
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001": {"GAP-P1-001-DATA"},
    "ODP-PLAN-LIVE-STAGING-PROOF-001": {"GAP-P0-006"},
    "ODP-PLAN-NETPLAN-ACCEPTANCE-001": {"GAP-P1-006"},
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001": {"GAP-P1-006-BUSINESS"},
    "ODP-PLAN-OBSERVABILITY-LIVE-001": {"GAP-P1-008"},
    "ODP-PLAN-OSS-LEGAL-POLICY-001": {"GAP-P1-007-LEGAL"},
    "ODP-PLAN-OSS-LICENSE-GATE-001": {"GAP-P1-007"},
    "ODP-PLAN-PRICE-ADLIFT-PILOT-001": {"GAP-P1-005"},
    "ODP-PLAN-SITESCORE-OUTCOME-001": {"GAP-P1-002"},
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001": {"GAP-P1-002-DATA"},
    "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001": {"GAP-P1-002"},
    "ODP-PLAN-UAT-SIGNOFF-001": {"GAP-P0-005"},
}

EXPECTED_RTM_IDS = {
    *(
        f"PLAN-S{stage}-{item:03d}"
        for stage, count in enumerate((11, 11, 9, 8, 10, 11, 10, 6))
        for item in range(1, count + 1)
    ),
    *(f"PLAN-S{stage}-GATE" for stage in range(8)),
}

EXPECTED_LEDGER_SCOPES = {
    "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001": "P0-002",
    "ODP-PLAN-AVM-OUTCOME-001": "P1-003",
    "ODP-PLAN-AVM-OUTCOME-BACKFILL-001": "P1-003 data gate",
    "ODP-PLAN-ENGINEERING-HARDENING-001": "P2",
    "ODP-PLAN-FINAL-GATE-AUDIT-001": "final",
    "ODP-PLAN-FORECAST-BUSINESS-001": "P1-004",
    "ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001": "P0-004",
    "ODP-PLAN-HEATZONE-LABEL-BACKFILL-001": "P1-001 data gate",
    "ODP-PLAN-LIVE-STAGING-PROOF-001": "P0-006",
    "ODP-PLAN-NETPLAN-ACCEPTANCE-001": "P1-006 technical gate",
    "ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001": "P1-006 business gate",
    "ODP-PLAN-OBSERVABILITY-LIVE-001": "P1-008",
    "ODP-PLAN-OSS-LEGAL-POLICY-001": "P1-007 legal gate",
    "ODP-PLAN-OSS-LICENSE-GATE-001": "P1-007",
    "ODP-PLAN-PRICE-ADLIFT-PILOT-001": "P1-005",
    "ODP-PLAN-SITESCORE-OUTCOME-001": "P1-002",
    "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001": "P1-002 data gate",
    "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001": "P1-002",
    "ODP-PLAN-UAT-SIGNOFF-001": "P0-005",
}

DEPLOYMENT_TASK_ID = "ODP-PLAN-LIVE-STAGING-PROOF-001"
DEPLOYMENT_MODE_ALLOWED = "staging_live_allowed"
DEPLOYMENT_MODE_FORBIDDEN = "forbidden"
DEPLOYMENT_MODE_NOT_APPLICABLE = "not_applicable"
PACKET_MD = "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.md"
PACKET_JSON = "docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json"
LEGACY_ARCHIVE_ARTIFACT_ANCHORS = {
    "ODP-PLAN-OSS-LICENSE-GATE-001": "scripts/security/",
    "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001": "tests/e2e/",
    "ODP-PLAN-NETPLAN-ACCEPTANCE-001": "solver/netplan/",
}

REQUIRED_ACCEPTANCE_PREFIXES = (
    "Deliverable:",
    "Fail-closed:",
    "Evidence set:",
    "Handoff gate:",
    "Batch rule:",
    "Deployment:",
)

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


def expected_deployment_mode(packet: dict[str, Any]) -> str:
    if packet.get("task_id") == DEPLOYMENT_TASK_ID:
        return DEPLOYMENT_MODE_ALLOWED
    if packet.get("class") == "human_gate":
        return DEPLOYMENT_MODE_NOT_APPLICABLE
    return DEPLOYMENT_MODE_FORBIDDEN


def deployment_acceptance(mode: str) -> str:
    if mode == DEPLOYMENT_MODE_ALLOWED:
        return (
            "Deployment: staging/live allowed only for exact merged dev after all declared "
            "dependencies are done; production GO remains forbidden before final Human/Ops gate."
        )
    if mode == DEPLOYMENT_MODE_NOT_APPLICABLE:
        return "Deployment: not applicable; this Human/Ops gate grants no deployment authority."
    return (
        "Deployment: forbidden; this task may run only local/CI/read-only verification and "
        "grants no staging/live/production authority."
    )


def build_expected_acceptance(packet: dict[str, Any]) -> list[str]:
    criteria = [
        *(f"Deliverable: {item}" for item in packet["batch_deliverables"]),
        *(f"Fail-closed: {item}" for item in packet["must_reject"]),
        f"Evidence set: {'; '.join(packet['evidence'])}",
        f"Handoff gate: {packet['handoff_gate']}",
        (
            "Batch rule: re-audit every criterion after reopen; do not hand off, "
            "open/refresh PR, or deploy after fixing only the latest reviewer example."
        ),
        deployment_acceptance(packet["deployment_contract"]),
    ]
    return list(dict.fromkeys(item.strip() for item in criteria if item.strip()))


def build_legacy_acceptance(packet: dict[str, Any]) -> list[str]:
    """Return the immutable schema-1.0 contract archived before deployment metadata."""

    return build_expected_acceptance(packet)[:-1]


def validate_packet(
    packet_path: Path = DEFAULT_PACKET,
    markdown_path: Path = DEFAULT_MARKDOWN,
    live_status_path: Path | None = None,
    live_archive_root: Path | None = None,
    matrix_path: Path = DEFAULT_MATRIX,
    ledger_path: Path = DEFAULT_LEDGER,
) -> list[str]:
    errors: list[str] = []

    if not packet_path.is_file():
        return [f"execution packet missing: {packet_path}"]
    if not markdown_path.is_file():
        return [f"execution packet markdown missing: {markdown_path}"]
    if not matrix_path.is_file():
        return [f"source RTM matrix missing: {matrix_path}"]
    if not ledger_path.is_file():
        return [f"source execution ledger missing: {ledger_path}"]

    try:
        packet = _load_object(packet_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"execution packet is unreadable: {exc}"]

    if packet.get("schema_version") != "1.1.0":
        errors.append("schema_version must be 1.1.0")
    if packet.get("packet_id") != "ODP-PLAN-EXECUTION-CONTROL-PACK-001":
        errors.append("unexpected packet_id")
    if packet.get("program_id") != "ODP-PLAN-GAP-CLOSEOUT-2026-07-30":
        errors.append("unexpected program_id")

    matrix = matrix_path.read_text(encoding="utf-8")
    rtm_matches = re.findall(
        r"^\| (PLAN-S([0-7])-(?:\d{3}|GATE)) \|",
        matrix,
        flags=re.MULTILINE,
    )
    rtm_ids = [item[0] for item in rtm_matches]
    stage_distribution = [
        sum(stage == str(index) for _, stage in rtm_matches) for index in range(8)
    ]
    if len(rtm_ids) != 84 or set(rtm_ids) != EXPECTED_RTM_IDS:
        errors.append(
            "source RTM matrix must contain the exact 84 row ids; "
            f"rows={len(rtm_ids)} missing={sorted(EXPECTED_RTM_IDS - set(rtm_ids))} "
            f"extra={sorted(set(rtm_ids) - EXPECTED_RTM_IDS)}"
        )
    if stage_distribution != [12, 12, 10, 9, 11, 12, 11, 7]:
        errors.append(f"source RTM matrix stage distribution drifted: {stage_distribution}")

    ledger = ledger_path.read_text(encoding="utf-8")
    ledger_task_ids = re.findall(
        r"^\| [A-E] \| `([^`]+)` \|",
        ledger,
        flags=re.MULTILINE,
    )
    expected_ledger_ids = EXPECTED_COMPLETED | EXPECTED_UNRESOLVED
    if len(ledger_task_ids) != 26 or set(ledger_task_ids) != expected_ledger_ids:
        errors.append(
            f"source execution ledger must contain the exact 26 governance tasks; "
            f"rows={len(ledger_task_ids)} missing={sorted(expected_ledger_ids - set(ledger_task_ids))} "
            f"extra={sorted(set(ledger_task_ids) - expected_ledger_ids)}"
        )
    missing_contracts = sorted(
        task_id for task_id in expected_ledger_ids if ledger.count(f"### {task_id}") != 1
    )
    if missing_contracts:
        errors.append(f"source execution ledger task contracts must be unique: {missing_contracts}")
    ledger_scope_rows = {
        task_id: scope.strip()
        for task_id, scope in re.findall(
            r"^\| [A-E] \| `([^`]+)` \| ([^|]+) \|",
            ledger,
            flags=re.MULTILINE,
        )
    }
    for task_id, expected_scope in EXPECTED_LEDGER_SCOPES.items():
        if ledger_scope_rows.get(task_id) != expected_scope:
            errors.append(
                f"source execution ledger scope for {task_id} must equal "
                f"{expected_scope!r}, got {ledger_scope_rows.get(task_id)!r}"
            )

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
    if set(EXPECTED_GAP_IDS) != EXPECTED_UNRESOLVED:
        errors.append("authoritative gap mapping must cover the exact 19 unresolved tasks")

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
        deployment_mode = item.get("deployment_contract")
        expected_mode = expected_deployment_mode(item)
        if deployment_mode != expected_mode:
            errors.append(
                f"{task_id}: deployment_contract must equal {expected_mode!r}, "
                f"got {deployment_mode!r}"
            )
        gap_ids = item.get("gap_ids")
        expected_gap_ids = EXPECTED_GAP_IDS.get(task_id)
        if (
            not isinstance(gap_ids, list)
            or len(gap_ids) != len(set(gap_ids))
            or set(gap_ids) != expected_gap_ids
        ):
            errors.append(
                f"{task_id}: gap_ids must equal authoritative scope "
                f"{sorted(expected_gap_ids or [])}, got {gap_ids!r}"
            )
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
    deployment_allowed = [
        item.get("task_id")
        for item in task_packets
        if isinstance(item, dict) and item.get("deployment_contract") == DEPLOYMENT_MODE_ALLOWED
    ]
    if deployment_allowed != [DEPLOYMENT_TASK_ID]:
        errors.append(
            f"exactly {DEPLOYMENT_TASK_ID} may allow staging/live deployment; "
            f"got {deployment_allowed}"
        )

    if "每修一個 finding 就開 PR 或部署" not in markdown:
        errors.append("markdown must state the no-one-finding-per-PR/deploy rule")
    if "ODP-PLAN-LIVE-STAGING-PROOF-001" not in markdown:
        errors.append("markdown must state the single planned deployment task")
    if "`NO-GO`" not in markdown:
        errors.append("markdown must preserve the NO-GO release claim")

    if live_status_path is not None:
        archive_root = live_archive_root or live_status_path.parent / "ai-task-archive"
        errors.extend(_validate_live_status(task_packets, live_status_path, archive_root))

    return errors


def _validate_task_contract(
    task: dict[str, Any],
    packet: dict[str, Any],
    *,
    label: str,
    allow_legacy_archive: bool = False,
) -> list[str]:
    errors: list[str] = []
    task_id = packet["task_id"]

    owner = task.get("owner")
    reviewer = task.get("reviewer")
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or not isinstance(reviewer, str)
        or not reviewer.strip()
    ):
        errors.append(f"{label}: owner and reviewer must be non-empty strings")
    elif owner == reviewer:
        errors.append(f"{label}: owner must not equal reviewer")
    if task_id in EXPECTED_HUMAN_OWNERS and owner != "Human/Ops":
        errors.append(f"{label}: human gate owner must be Human/Ops")
    if task_id in EXPECTED_HUMAN_REVIEWERS and reviewer != "Human/Ops":
        errors.append(f"{label}: human approval reviewer must be Human/Ops")
    if task.get("task_class") != packet.get("class"):
        errors.append(
            f"{label}: task_class {task.get('task_class')!r} "
            f"does not match packet {packet.get('class')!r}"
        )
    acceptance = task.get("acceptance")
    normalized_acceptance: list[str] = []
    legacy_contract = False
    if (
        not isinstance(acceptance, list)
        or len(acceptance) < 5
        or not all(isinstance(item, str) and item.strip() for item in acceptance)
    ):
        errors.append(f"{label}: acceptance must contain at least five non-empty granular criteria")
    else:
        normalized_acceptance = [item.strip() for item in acceptance]
        expected_acceptance = build_expected_acceptance(packet)
        legacy_contract = allow_legacy_archive and normalized_acceptance == build_legacy_acceptance(
            packet
        )
        if len(normalized_acceptance) != len(set(normalized_acceptance)):
            errors.append(f"{label}: acceptance criteria must be unique, not repeated generic text")
        missing_prefixes = [
            prefix
            for prefix in REQUIRED_ACCEPTANCE_PREFIXES
            if not any(item.startswith(prefix) for item in normalized_acceptance)
            and not (legacy_contract and prefix == "Deployment:")
        ]
        if missing_prefixes:
            errors.append(
                f"{label}: acceptance is not granular; missing criterion classes {missing_prefixes}"
            )
        if normalized_acceptance != expected_acceptance and not legacy_contract:
            errors.append(f"{label}: acceptance must exactly match the task execution packet")
    source_docs = task.get("source_docs")
    if not isinstance(source_docs, list) or PACKET_JSON not in source_docs:
        errors.append(f"{label}: source_docs must reference the control-pack JSON")
    artifacts = task.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or not all(isinstance(item, str) and item.strip() for item in artifacts)
    ):
        errors.append(f"{label}: artifacts must be a non-empty string list")
    elif legacy_contract:
        artifact_anchor = LEGACY_ARCHIVE_ARTIFACT_ANCHORS.get(task_id)
        if artifact_anchor is None or artifact_anchor not in artifacts:
            errors.append(f"{label}: legacy archive artifacts do not match the frozen task anchor")
    elif not {PACKET_MD, PACKET_JSON}.issubset(artifacts):
        errors.append(f"{label}: artifacts must preserve both control-pack authorities")
    verification = task.get("verification")
    if (
        not isinstance(verification, list)
        or not verification
        or not all(isinstance(item, str) and item.strip() for item in verification)
    ):
        errors.append(f"{label}: verification must be a non-empty string list")
    elif not set(packet.get("verification") or []).issubset(verification):
        errors.append(f"{label}: verification must contain the complete packet verification")
    if task.get("execution_packet_id") != "ODP-PLAN-EXECUTION-CONTROL-PACK-001":
        errors.append(f"{label}: execution_packet_id must reference the control pack")
    exact_fields = {
        "execution_packet_deliverables": packet.get("batch_deliverables"),
        "execution_packet_must_reject": packet.get("must_reject"),
        "execution_packet_evidence": packet.get("evidence"),
        "execution_packet_handoff_gate": packet.get("handoff_gate"),
        "deployment_contract": packet.get("deployment_contract"),
    }
    for field, expected in exact_fields.items():
        if legacy_contract and field not in task:
            continue
        if task.get(field) != expected:
            errors.append(f"{label}: {field} must exactly match the task execution packet")
    gap_ids = task.get("gap_ids")
    if gap_ids != packet.get("gap_ids"):
        errors.append(f"{label}: gap_ids must exactly equal the packet scope for {task_id}")

    return errors


def _archive_snapshot_path(archive_root: Path, task_id: str) -> Path:
    return archive_root / "tasks" / f"{quote(task_id, safe='-_.')}.json"


def _load_archive_snapshot(path: Path, task_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        snapshot = _load_object(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, [f"{task_id}: archived snapshot is unreadable: {exc}"]

    errors: list[str] = []
    if snapshot.get("task_id") != task_id:
        errors.append(f"{task_id}: archived snapshot task_id mismatch: {snapshot.get('task_id')!r}")
    if snapshot.get("terminal_status") != "done":
        errors.append(f"{task_id}: archived snapshot terminal_status must be 'done'")
    if snapshot.get("terminal_outcome") not in {"completed", "superseded"}:
        errors.append(
            f"{task_id}: archived snapshot terminal_outcome must be completed or superseded"
        )
    task = snapshot.get("task")
    if not isinstance(task, dict):
        errors.append(f"{task_id}: archived snapshot task must be an object")
        return None, errors
    if task.get("id") != task_id:
        errors.append(f"{task_id}: archived task id mismatch: {task.get('id')!r}")
    if task.get("status") != "done":
        errors.append(f"{task_id}: archived task status must be 'done'")
    task_outcome = task.get("terminal_outcome") or "completed"
    if task_outcome != snapshot.get("terminal_outcome"):
        errors.append(f"{task_id}: archived task and snapshot terminal outcomes disagree")
    return task, errors


def _validate_replacement_chain(
    *,
    packet: dict[str, Any],
    first_task: dict[str, Any],
    active_tasks: dict[str, dict[str, Any]],
    archive_root: Path,
) -> list[str]:
    errors: list[str] = []
    original_id = packet["task_id"]
    current = first_task
    seen = {original_id}

    while (current.get("terminal_outcome") or "completed") == "superseded":
        replacement_id = str(current.get("superseded_by") or "").strip()
        if not replacement_id:
            errors.append(f"{original_id}: superseded packet has no replacement task")
            break
        if replacement_id in seen:
            errors.append(
                f"{original_id}: superseded replacement chain contains a cycle at {replacement_id}"
            )
            break
        seen.add(replacement_id)

        replacement_active = active_tasks.get(replacement_id)
        replacement_path = _archive_snapshot_path(archive_root, replacement_id)
        replacement_archived = replacement_path.is_file()
        if replacement_active is not None and replacement_archived:
            errors.append(
                f"{original_id}: replacement {replacement_id} exists in both active and archive state"
            )
            break
        if replacement_active is None and not replacement_archived:
            errors.append(
                f"{original_id}: superseded replacement {replacement_id} is absent from canonical state"
            )
            break

        if replacement_active is not None:
            if replacement_active.get("status") == "done":
                errors.append(f"{original_id}: active replacement {replacement_id} cannot be done")
            current = replacement_active
        else:
            current, archive_errors = _load_archive_snapshot(replacement_path, replacement_id)
            errors.extend(f"{original_id}: replacement chain: {error}" for error in archive_errors)
            if current is None:
                break

        errors.extend(
            _validate_task_contract(
                current,
                packet,
                label=f"{original_id}: replacement {replacement_id}",
            )
        )

    return errors


def validate_archived_packet_state(
    *,
    packet: dict[str, Any],
    active_tasks: dict[str, dict[str, Any]],
    archive_root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate one packet's official archive snapshot and any replacement chain."""

    task_id = packet["task_id"]
    archive_path = _archive_snapshot_path(archive_root, task_id)
    if not archive_path.is_file():
        return None, [f"{task_id}: absent from active and archive state"]

    archived_task, errors = _load_archive_snapshot(archive_path, task_id)
    if archived_task is None:
        return None, errors
    errors.extend(
        _validate_task_contract(
            archived_task,
            packet,
            label=f"{task_id}: archive",
            allow_legacy_archive=True,
        )
    )
    if (archived_task.get("terminal_outcome") or "completed") == "superseded":
        errors.extend(
            _validate_replacement_chain(
                packet=packet,
                first_task=archived_task,
                active_tasks=active_tasks,
                archive_root=archive_root,
            )
        )
    return archived_task, errors


def _validate_live_status(
    task_packets: list[dict[str, Any]],
    live_status_path: Path,
    archive_root: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        status = _load_object(live_status_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"live status is unreadable: {exc}"]

    task_rows = [task for task in status.get("tasks", []) if isinstance(task, dict)]
    task_ids = [task.get("id") for task in task_rows if isinstance(task.get("id"), str)]
    if len(task_ids) != len(set(task_ids)):
        errors.append("live task state contains duplicate task ids")
    tasks = {task["id"]: task for task in task_rows if isinstance(task.get("id"), str)}

    for packet in task_packets:
        task_id = packet["task_id"]
        active_task = tasks.get(task_id)
        archive_path = _archive_snapshot_path(archive_root, task_id)
        archived = archive_path.is_file()

        if active_task is not None and archived:
            errors.append(f"{task_id}: exists in both active and archive state")
            continue
        if active_task is None and not archived:
            errors.append(f"{task_id}: absent from active and archive state")
            continue

        if active_task is not None:
            if active_task.get("status") == "done":
                errors.append(f"{task_id}: active task cannot have terminal status done")
            errors.extend(_validate_task_contract(active_task, packet, label=f"{task_id}: active"))
            continue

        archived_task, archive_errors = validate_archived_packet_state(
            packet=packet,
            active_tasks=tasks,
            archive_root=archive_root,
        )
        errors.extend(archive_errors)
        if archived_task is None:
            continue

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    errors = validate_packet(
        args.packet,
        args.markdown,
        args.status,
        args.archive_root,
        args.matrix,
        args.ledger,
    )
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
