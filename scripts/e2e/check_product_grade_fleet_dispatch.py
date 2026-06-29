#!/usr/bin/env python3
"""Validate product-grade E2E follow-up dispatch is fleet-executable.

The markdown dispatch is for humans. The JSON packet is for gates, operators,
and future fleet automation. This checker prevents the live external source,
live map, and remote staging follow-up work from becoming a loose note with
missing ownership, evidence, or boundary fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.json"
MARKDOWN = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.md"
GAP_TASKS = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md"

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


def main() -> int:
    errors: list[str] = []

    if not PACKET.exists():
        print(f"missing product-grade fleet dispatch packet: {PACKET.relative_to(ROOT)}")
        return 1

    packet = json.loads(PACKET.read_text(encoding="utf-8"))
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
        if task_id not in markdown_text:
            errors.append(f"{task_id} missing from markdown dispatch")
        if task_id not in gap_text:
            errors.append(f"{task_id} missing from gap execution tasks")

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

    if errors:
        print("Product-grade fleet dispatch validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product-grade fleet dispatch checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
