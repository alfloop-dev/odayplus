#!/usr/bin/env python3
"""Validate product-grade E2E follow-up dispatch is fleet-executable.

The markdown dispatch is for humans. The JSON packet is for gates, operators,
and future fleet automation. This checker prevents the live external source,
live map, and remote staging follow-up work from becoming a loose note with
missing ownership, evidence, or boundary fields.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.json"
MARKDOWN = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.md"
GAP_TASKS = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md"
BRIEF_DIR = ROOT / "docs/evidence/fleet_dispatch"
BRIEF_INDEX = BRIEF_DIR / "README.md"
DISPATCH_QUEUE = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH_QUEUE.json"
KICKOFF_RUNBOOK = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_KICKOFF_RUNBOOK.md"

EXPECTED_ALIASES = {
    "ODP-EXT-001",
    "ODP-EXT-002",
    "ODP-EXT-003",
    "ODP-EXT-004",
    "ODP-EXT-005",
    "ODP-EXT-006",
    "ODP-EXT-007",
    "ODP-EXT-008",
    "ODP-MAP-E2E-001",
    "ODP-MAP-E2E-002",
    "ODP-MAP-E2E-003",
    "ODP-MAP-E2E-004",
    "ODP-MAP-A11Y-001",
    "ODP-MAP-E2E-005",
    "ODP-MAP-E2E-006",
    "ODP-PV-STAGE-001",
    "ODP-PV-STAGE-002",
}

EXPECTED_BOUNDARIES = {"external_data_sources", "maps", "remote_staging"}


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(non_empty_string(item) for item in value) and bool(value)


def load_packet() -> dict[str, Any]:
    return json.loads(PACKET.read_text(encoding="utf-8"))


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def code_block(items: list[str]) -> str:
    return "\n\n".join(f"```bash\n{item}\n```" for item in items)


def render_task_brief(packet: dict[str, Any], task_id: str) -> str:
    tasks = packet.get("tasks") or []
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        known = ", ".join(sorted(item.get("id", "") for item in tasks))
        raise SystemExit(f"Unknown dispatch task {task_id}. Known tasks: {known}")

    boundary = packet["scope_boundaries"][task["scope_boundary"]]
    return "\n".join(
        [
            f"# Fleet Execution Brief: {task['id']}",
            "",
            f"- Parent: {task['parent']}",
            f"- Status: {task['status']}",
            f"- Scope boundary: {task['scope_boundary']}",
            f"- Owner lane: {task['owner_lane']}",
            f"- Reviewer lane: {task['reviewer_lane']}",
            f"- Suggested branch: `{task['suggested_branch']}`",
            f"- Release authority: PR #{packet['release_target']['pr']} headRefOid and attached checks",
            "",
            "## Objective",
            "",
            task["objective"],
            "",
            "## Current Proof Boundary",
            "",
            f"- Current proof: {boundary['current_proof']}",
            "- Live claim requires:",
            bullet_list(boundary["live_claim_requires"]),
            "",
            "## Implementation Evidence Required",
            "",
            bullet_list(task["implementation_evidence"]),
            "",
            "## Verification Evidence Required",
            "",
            bullet_list(task["verification_evidence"]),
            "",
            *(
                [
                    "## Execution Commands",
                    "",
                    code_block(task["execution_commands"]),
                    "",
                ]
                if non_empty_string_list(task.get("execution_commands"))
                else []
            ),
            *(
                [
                    "## Blocking Dependencies",
                    "",
                    bullet_list(task["blocking_dependencies"]),
                    "",
                ]
                if non_empty_string_list(task.get("blocking_dependencies"))
                else []
            ),
            "## Acceptance Criteria",
            "",
            bullet_list(task["acceptance_criteria"]),
            "",
            "## Handoff Artifacts",
            "",
            bullet_list(task["handoff_artifacts"]),
            "",
            "## Completion Rules",
            "",
            bullet_list(packet["completion_rules"]),
            "",
        ]
    )


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# Product-Grade E2E Fleet Dispatch Report",
        "",
        f"- PR: #{packet['release_target']['pr']}",
        f"- Authority: {packet['release_target']['authority']}",
        f"- Status: {packet['status']}",
        f"- Updated: {packet['updated']}",
        "",
        "## Dispatch Lanes",
        "",
        "| Lane | Owner Lane | Reviewer Lane | Task Count | Aliases |",
        "|---|---|---|---:|---|",
    ]
    for lane in packet["dispatch_lanes"]:
        aliases = lane["aliases"]
        lines.append(
            f"| {lane['lane']} | {lane['owner_lane']} | {lane['reviewer_lane']} | {len(aliases)} | "
            f"{', '.join(aliases)} |"
        )

    lines.extend(["", "## Scope Boundaries", ""])
    for boundary_id, boundary in packet["scope_boundaries"].items():
        lines.extend(
            [
                f"### {boundary_id}",
                "",
                f"- Current proof: {boundary['current_proof']}",
                "- Live claim requires:",
                bullet_list(boundary["live_claim_requires"]),
                "",
            ]
        )

    lines.extend(
        [
            "## Task Brief Commands",
            "",
            "Run `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task <task-id>` for one fleet task.",
            "",
            "| Task | Suggested Branch | Acceptance Count | Handoff Artifact Count |",
            "|---|---|---:|---:|",
        ]
    )
    for task in packet["tasks"]:
        lines.append(
            f"| {task['id']} | `{task['suggested_branch']}` | "
            f"{len(task['acceptance_criteria'])} | {len(task['handoff_artifacts'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def brief_path(task_id: str) -> Path:
    return BRIEF_DIR / f"{task_id}.md"


def write_briefs(packet: dict[str, Any]) -> None:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    BRIEF_INDEX.write_text(render_report(packet), encoding="utf-8")
    for task in packet["tasks"]:
        brief_path(task["id"]).write_text(render_task_brief(packet, task["id"]), encoding="utf-8")


def render_dispatch_queue(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "release_target": packet["release_target"],
        "status": "ready_for_fleet_pickup",
        "queue_role": "historical_initial_dispatch",
        "current_remaining_queue": "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
        "updated": packet["updated"],
        "dispatch_rule": (
            "This is the initial implementation fleet dispatch packet. Repo-side handbacks may already "
            "exist; current live-provider, live-map, and remote-staging blockers are routed through "
            "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json."
        ),
        "queue": [
            {
                "task_id": task["id"],
                "parent": task["parent"],
                "dispatch_status": "ready_for_fleet",
                "scope_boundary": task["scope_boundary"],
                "owner_lane": task["owner_lane"],
                "reviewer_lane": task["reviewer_lane"],
                "brief_path": str(brief_path(task["id"]).relative_to(ROOT)),
                "suggested_branch": task["suggested_branch"],
                "dispatch_command": (
                    f"python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task {task['id']}"
                ),
                "minimum_completion_signal": {
                    "implementation_evidence": task["implementation_evidence"],
                    "verification_evidence": task["verification_evidence"],
                    "execution_commands": task.get("execution_commands", []),
                    "blocking_dependencies": task.get("blocking_dependencies", []),
                    "acceptance_criteria": task["acceptance_criteria"],
                    "handoff_artifacts": task["handoff_artifacts"],
                },
            }
            for task in packet["tasks"]
        ],
    }


def write_dispatch_queue(packet: dict[str, Any]) -> None:
    DISPATCH_QUEUE.write_text(
        json.dumps(render_dispatch_queue(packet), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_kickoff_runbook(packet: dict[str, Any]) -> str:
    queue = render_dispatch_queue(packet)
    entries_by_lane = {
        lane["lane"]: [
            entry
            for entry in queue["queue"]
            if entry["task_id"] in lane["aliases"]
        ]
        for lane in packet["dispatch_lanes"]
    }
    lines = [
        "# Product-Grade E2E Fleet Kickoff Runbook",
        "",
        f"- PR authority: #{packet['release_target']['pr']} headRefOid and attached checks",
        f"- Queue status: {queue['status']}",
        f"- Queue role: {queue['queue_role']}",
        f"- Current remaining queue: `{queue['current_remaining_queue']}`",
        f"- Updated: {queue['updated']}",
        "",
        "## Operator Preflight",
        "",
        "- Confirm PR #82 `headRefOid` and attached checks before starting work.",
        "- Do not claim live-provider, live-map, or remote-staging proof until the relevant task evidence is attached.",
        "- Keep deterministic fixture/source-stub tests as CI defaults.",
            "- Use each task's suggested branch and brief file as the handoff contract.",
            "- Execute any task-specific `execution_commands` before requesting review.",
            "",
            "## Fleet Pickup Sequence",
            "",
    ]
    for lane in packet["dispatch_lanes"]:
        lines.extend(
            [
                f"### {lane['lane']}",
                "",
                f"- Owner lane: {lane['owner_lane']}",
                f"- Reviewer lane: {lane['reviewer_lane']}",
                "",
                "| Task | Suggested Branch | Brief | Dispatch Command |",
                "|---|---|---|---|",
            ]
        )
        for entry in entries_by_lane[lane["lane"]]:
            lines.append(
                f"| {entry['task_id']} | `{entry['suggested_branch']}` | "
                f"`{entry['brief_path']}` | `{entry['dispatch_command']}` |"
            )
        lines.append("")

    lines.extend(
        [
            "## Completion Handback",
            "",
            "For every task, the implementation fleet must attach:",
            "",
            "- implementation evidence",
            "- verification evidence",
            "- acceptance criteria proof",
            "- handoff artifacts",
            "",
            "A document-only PR must not close any `ready_for_fleet` queue entry.",
            "",
        ]
    )
    return "\n".join(lines)


def write_kickoff_runbook(packet: dict[str, Any]) -> None:
    KICKOFF_RUNBOOK.write_text(render_kickoff_runbook(packet), encoding="utf-8")


def validate_packet(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    markdown_text = MARKDOWN.read_text(encoding="utf-8") if MARKDOWN.exists() else ""
    gap_text = GAP_TASKS.read_text(encoding="utf-8") if GAP_TASKS.exists() else ""

    release_target = packet.get("release_target") or {}
    if release_target.get("pr") != 82:
        errors.append("release_target.pr must be 82")
    if "headRefOid" not in str(release_target.get("authority", "")):
        errors.append("release_target.authority must name PR #82 headRefOid")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("release_target.must_not_hardcode_dev_hash must be true")

    boundaries = packet.get("scope_boundaries") or {}
    if set(boundaries) != EXPECTED_BOUNDARIES:
        errors.append(f"scope_boundaries must be {sorted(EXPECTED_BOUNDARIES)}")
    for boundary_id, boundary in boundaries.items():
        if not non_empty_string(boundary.get("current_proof")):
            errors.append(f"{boundary_id} missing current_proof")
        if not non_empty_string_list(boundary.get("live_claim_requires")):
            errors.append(f"{boundary_id} missing live_claim_requires")

    tasks = packet.get("tasks")
    if not isinstance(tasks, list):
        errors.append("tasks must be a list")
        tasks = []
    task_ids = {task.get("id") for task in tasks if isinstance(task, dict)}
    if task_ids != EXPECTED_ALIASES:
        errors.append(f"tasks ids mismatch: expected {sorted(EXPECTED_ALIASES)}, got {sorted(task_ids)}")

    lane_aliases: set[str] = set()
    for lane in packet.get("dispatch_lanes") or []:
        aliases = lane.get("aliases")
        if not non_empty_string(lane.get("lane")):
            errors.append("dispatch lane missing lane name")
        if not non_empty_string(lane.get("owner_lane")):
            errors.append(f"dispatch lane {lane.get('lane')} missing owner_lane")
        if not non_empty_string(lane.get("reviewer_lane")):
            errors.append(f"dispatch lane {lane.get('lane')} missing reviewer_lane")
        if not isinstance(aliases, list) or not aliases:
            errors.append(f"dispatch lane {lane.get('lane')} missing aliases")
            continue
        lane_aliases.update(alias for alias in aliases if isinstance(alias, str))
    if lane_aliases != EXPECTED_ALIASES:
        errors.append(f"dispatch lane aliases mismatch: {sorted(lane_aliases)}")

    for task in tasks:
        if not isinstance(task, dict):
            errors.append("task entry must be an object")
            continue
        task_id = task.get("id")
        if task.get("status") != "open":
            errors.append(f"{task_id} status must remain open until implementation evidence exists")
        for field in ("parent", "scope_boundary", "owner_lane", "reviewer_lane", "objective"):
            if not non_empty_string(task.get(field)):
                errors.append(f"{task_id} missing {field}")
        if task.get("scope_boundary") not in EXPECTED_BOUNDARIES:
            errors.append(f"{task_id} has invalid scope_boundary {task.get('scope_boundary')}")
        if not non_empty_string_list(task.get("implementation_evidence")):
            errors.append(f"{task_id} missing implementation_evidence")
        if not non_empty_string_list(task.get("verification_evidence")):
            errors.append(f"{task_id} missing verification_evidence")
        if not non_empty_string_list(task.get("acceptance_criteria")):
            errors.append(f"{task_id} missing acceptance_criteria")
        if not non_empty_string(task.get("suggested_branch")):
            errors.append(f"{task_id} missing suggested_branch")
        elif not str(task.get("suggested_branch")).startswith(f"task/{task_id}"):
            errors.append(f"{task_id} suggested_branch must start with task/{task_id}")
        if not non_empty_string_list(task.get("handoff_artifacts")):
            errors.append(f"{task_id} missing handoff_artifacts")
        execution_commands = task.get("execution_commands", [])
        if not non_empty_string_list(execution_commands):
            errors.append(f"{task_id} missing execution_commands")
        else:
            joined_commands = "\n".join(execution_commands)
            for required_phrase in ("gh pr view 82", "headRefOid"):
                if required_phrase not in joined_commands:
                    errors.append(f"{task_id} execution_commands missing phrase: {required_phrase}")
        blocking_dependencies = task.get("blocking_dependencies", [])
        if blocking_dependencies and not non_empty_string_list(blocking_dependencies):
            errors.append(f"{task_id} blocking_dependencies must be non-empty strings")
        if task_id in {"ODP-PV-STAGE-001", "ODP-PV-STAGE-002"}:
            joined_commands = "\n".join(execution_commands)
            for required_phrase in (
                "scripts/e2e/check_remote_staging_proof.py",
                "gh pr view 82",
                "headRefOid",
            ):
                if required_phrase not in joined_commands:
                    errors.append(f"{task_id} execution_commands missing phrase: {required_phrase}")
            if task_id == "ODP-PV-STAGE-002" and "npx playwright test" not in joined_commands:
                errors.append(f"{task_id} execution_commands missing staging product E2E smoke command")
        if task_id not in markdown_text:
            errors.append(f"{task_id} missing from markdown dispatch")
        if task_id not in gap_text:
            errors.append(f"{task_id} missing from gap execution tasks")
        path = brief_path(str(task_id))
        expected_brief = render_task_brief(packet, str(task_id))
        if not path.exists():
            errors.append(f"{task_id} missing generated fleet brief: {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected_brief:
            errors.append(f"{task_id} generated fleet brief is stale: {path.relative_to(ROOT)}")

    completion_rules = packet.get("completion_rules")
    if not non_empty_string_list(completion_rules):
        errors.append("completion_rules must be a non-empty string list")
    else:
        joined_rules = " ".join(completion_rules)
        for required_phrase in (
            "document-only PRs must not close",
            "CI defaults",
            "live-provider proof",
            "live-map proof",
            "remote-staging proof",
            "provider secrets",
            "PR #82 headRefOid",
        ):
            if required_phrase not in joined_rules:
                errors.append(f"completion_rules missing phrase: {required_phrase}")

    expected_index = render_report(packet)
    if not BRIEF_INDEX.exists():
        errors.append(f"missing generated fleet dispatch index: {BRIEF_INDEX.relative_to(ROOT)}")
    elif BRIEF_INDEX.read_text(encoding="utf-8") != expected_index:
        errors.append(f"generated fleet dispatch index is stale: {BRIEF_INDEX.relative_to(ROOT)}")

    expected_queue = render_dispatch_queue(packet)
    if not DISPATCH_QUEUE.exists():
        errors.append(f"missing generated fleet dispatch queue: {DISPATCH_QUEUE.relative_to(ROOT)}")
    else:
        queue = json.loads(DISPATCH_QUEUE.read_text(encoding="utf-8"))
        if queue != expected_queue:
            errors.append(f"generated fleet dispatch queue is stale: {DISPATCH_QUEUE.relative_to(ROOT)}")
        if queue.get("queue_role") != "historical_initial_dispatch":
            errors.append("generated fleet dispatch queue must declare historical_initial_dispatch role")
        if queue.get("current_remaining_queue") != "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json":
            errors.append("generated fleet dispatch queue must point to current external proof queue")
        for entry in queue.get("queue", []):
            task_id = entry.get("task_id")
            if entry.get("dispatch_status") != "ready_for_fleet":
                errors.append(f"{task_id} dispatch_status must be ready_for_fleet")
            if not non_empty_string(entry.get("dispatch_command")):
                errors.append(f"{task_id} missing dispatch_command")
            if not non_empty_string(entry.get("brief_path")):
                errors.append(f"{task_id} missing brief_path")
            elif not (ROOT / entry["brief_path"]).exists():
                errors.append(f"{task_id} brief_path does not exist: {entry['brief_path']}")

    expected_runbook = render_kickoff_runbook(packet)
    if not KICKOFF_RUNBOOK.exists():
        errors.append(f"missing generated fleet kickoff runbook: {KICKOFF_RUNBOOK.relative_to(ROOT)}")
    elif KICKOFF_RUNBOOK.read_text(encoding="utf-8") != expected_runbook:
        errors.append(f"generated fleet kickoff runbook is stale: {KICKOFF_RUNBOOK.relative_to(ROOT)}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print a fleet dispatch summary report")
    parser.add_argument("--task", help="print a single fleet execution brief by task id")
    parser.add_argument("--write-briefs", action="store_true", help="write generated fleet brief artifacts")
    parser.add_argument("--write-queue", action="store_true", help="write generated fleet dispatch queue")
    parser.add_argument("--write-runbook", action="store_true", help="write generated fleet kickoff runbook")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not PACKET.exists():
        print(f"missing product-grade fleet dispatch packet: {PACKET.relative_to(ROOT)}")
        return 1

    packet = load_packet()
    if args.write_briefs:
        write_briefs(packet)
        print(f"Wrote fleet dispatch briefs to {BRIEF_DIR.relative_to(ROOT)}.")
        return 0

    if args.write_queue:
        write_dispatch_queue(packet)
        print(f"Wrote fleet dispatch queue to {DISPATCH_QUEUE.relative_to(ROOT)}.")
        return 0

    if args.write_runbook:
        write_kickoff_runbook(packet)
        print(f"Wrote fleet kickoff runbook to {KICKOFF_RUNBOOK.relative_to(ROOT)}.")
        return 0

    errors = validate_packet(packet)
    if errors:
        print("Product-grade fleet dispatch validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    if args.task:
        print(render_task_brief(packet, args.task))
        return 0

    if args.report:
        print(render_report(packet))
        return 0

    print("Product-grade fleet dispatch checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
