#!/usr/bin/env python3
"""Reconcile the product-grade evidence and fleet-closure gates into one truth.

The release ships with several independently maintained evidence surfaces:

- ``PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`` enumerates the live external
  blockers (#132-#138);
- ``EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`` tracks handback pickup ACKs;
- ``EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md`` is the human pickup surface;
- ``PRODUCT_RELEASE_CLOSEOUT_QUEUE.json`` enumerates the owner/reviewer/Human-Ops
  closure packets;
- live ``ai-status.json`` is the runtime source of truth for fleet completion.

Each surface has its own checker, but nothing verifies they agree on the four
numbers operators actually quote: **blocker count**, **pending pickup ACKs**,
**closure packets**, and **fleet completion**. When the queues are edited on one
date and ``ai-status.json`` moves on another, the surfaces drift: closure packets
point at tasks that have left the board, blockers name tasks the fleet is already
implementing in-repo, and the quoted counts disagree.

This checker computes those four numbers from their named authoritative sources
and validates two invariant families:

- **static** invariants hold on the committed repo alone (CI-safe, no
  ``ai-status.json`` dependency): queue/board task-id parity, ``blocker_count ==
  pending_pickup_acks``, tracking-issue and pickup-board coverage, and
  bundle-status honesty;
- **runtime** invariants cross-check the closure packets and blockers against
  live ``ai-status.json``: orphaned closure packets (queue task absent from the
  board), stale closure packets (queue still lists a task the board marks
  ``done``), status contradictions, and blockers that already have an active
  in-repo implementation task.

Static invariants gate the exit code by default. Runtime findings are reported
but only fail the run under ``--strict-runtime`` so a snapshot can be rendered
against a drifting board without the tool refusing to run.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence"
EXTERNAL_QUEUE_PATH = EVIDENCE / "PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
HANDBACK_BOARD_PATH = EVIDENCE / "EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"
PICKUP_BOARD_PATH = EVIDENCE / "EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md"
RELEASE_CLOSEOUT_QUEUE_PATH = EVIDENCE / "PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"

DEFAULT_STATUS_ROOT = (
    Path(os.path.expanduser(os.environ["PANTHEON_STATUS_ROOT"])).resolve()
    if os.environ.get("PANTHEON_STATUS_ROOT")
    else ROOT
)

ACCEPTED_HANDBACK_STATUS = "accepted"
# External-queue statuses that mean the blocker is no longer open.
RESOLVED_QUEUE_STATUSES = {"resolved", "accepted", "closed", "done"}
# Live statuses that mean the underlying work is still moving through the fleet.
ACTIVE_LIVE_STATUSES = {"todo", "in_progress", "review", "review_approved"}
DONE_LIVE_STATUS = "done"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# reconciliation model
# ---------------------------------------------------------------------------
def reconcile(
    external_queue: dict[str, Any],
    handback_board: dict[str, Any],
    pickup_board_text: str,
    release_queue: dict[str, Any],
    status_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute the single reconciled truth from every source.

    Returns a plain dict so it can be serialized to the reconciliation doc and
    consumed by the pytest gate.
    """
    external_ids = [str(e["task_id"]) for e in external_queue.get("queue", [])]
    board_entries = {str(e["task_id"]): e for e in handback_board.get("tasks", [])}

    blockers: list[dict[str, Any]] = []
    for entry in external_queue.get("queue", []):
        task_id = str(entry["task_id"])
        board_entry = board_entries.get(task_id, {})
        handback_status = str(board_entry.get("status", "missing_status_board_entry"))
        queue_status = str(entry.get("status", ""))
        blockers.append(
            {
                "task_id": task_id,
                "tracking_issue": str(entry.get("tracking_issue", "")),
                "blocking_type": str(entry.get("blocking_type", "")),
                "queue_status": queue_status,
                "queue_open": queue_status not in RESOLVED_QUEUE_STATUSES,
                "handback_status": handback_status,
                "accepted": handback_status == ACCEPTED_HANDBACK_STATUS,
                "on_pickup_board": task_id in pickup_board_text,
            }
        )

    # Two independent views of "how many blockers are still open":
    #  - the external queue's own status field;
    #  - the handback board's acceptance state.
    queue_open_blockers = [b for b in blockers if b["queue_open"]]
    pending_acks = [b for b in blockers if not b["accepted"]]

    # Closure packets: each release-closeout queue entry is one lifecycle action;
    # a task may carry more than one (owner_handoff then reviewer_approve).
    closeout_actions = [
        {
            "task_id": str(e.get("task_id", "")),
            "actor": str(e.get("actor", "")),
            "action_type": str(e.get("action_type", "")),
            "status": str(e.get("status", "")),
        }
        for e in release_queue.get("queue", [])
    ]
    closeout_task_ids = sorted({a["task_id"] for a in closeout_actions})

    fleet: dict[str, Any] = {"available": status_payload is not None}
    if status_payload is not None:
        tasks = status_payload.get("tasks", [])
        counts: dict[str, int] = {}
        for task in tasks:
            counts[str(task.get("status", ""))] = counts.get(str(task.get("status", "")), 0) + 1
        total = len(tasks)
        done = counts.get(DONE_LIVE_STATUS, 0)
        fleet.update(
            {
                "total_tasks": total,
                "done_tasks": done,
                "status_counts": counts,
                "completion_pct": round(100.0 * done / total, 1) if total else 0.0,
                "updated_at": str(status_payload.get("updated_at", "")),
            }
        )

    return {
        "blocker_count": len(blockers),
        "queue_open_blocker_count": len(queue_open_blockers),
        "open_blocker_count": len(queue_open_blockers),
        "pending_pickup_acks": len(pending_acks),
        "closure_packets": len(closeout_actions),
        "closure_packet_tasks": closeout_task_ids,
        "external_ids": external_ids,
        "handback_board_ids": sorted(board_entries),
        "bundle_status": str(handback_board.get("bundle_status", {}).get("status", "")),
        "blockers": blockers,
        "closeout_actions": closeout_actions,
        "fleet_completion": fleet,
    }


# ---------------------------------------------------------------------------
# static invariants (CI-safe; no ai-status.json)
# ---------------------------------------------------------------------------
def validate_static(reconciliation: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    external_ids = set(reconciliation["external_ids"])
    board_ids = set(reconciliation["handback_board_ids"])
    if external_ids != board_ids:
        errors.append(
            "external proof queue and handback status board task ids must match: "
            f"missing_from_board={sorted(external_ids - board_ids)}, "
            f"extra_on_board={sorted(board_ids - external_ids)}"
        )

    # The external queue's open-blocker count and the board's pending-ACK count
    # are independent views of the same fact and must agree.
    if reconciliation["queue_open_blocker_count"] != reconciliation["pending_pickup_acks"]:
        errors.append(
            "external queue open-blocker count must equal handback pending pickup ACKs: "
            f"queue_open={reconciliation['queue_open_blocker_count']}, "
            f"pending_acks={reconciliation['pending_pickup_acks']}"
        )

    for blocker in reconciliation["blockers"]:
        task_id = blocker["task_id"]
        if not blocker["tracking_issue"]:
            errors.append(f"{task_id} blocker is missing a tracking_issue")
        if blocker["handback_status"] == "missing_status_board_entry":
            errors.append(f"{task_id} blocker has no handback status board entry")
        if not blocker["on_pickup_board"]:
            errors.append(f"{task_id} blocker is not listed on the external proof pickup board")

    # Bundle can only claim accepted when every handback is accepted.
    bundle_status = reconciliation["bundle_status"]
    all_accepted = reconciliation["pending_pickup_acks"] == 0
    if bundle_status == ACCEPTED_HANDBACK_STATUS and not all_accepted:
        errors.append(
            "handback bundle_status is 'accepted' while pending pickup ACKs remain: "
            f"pending={reconciliation['pending_pickup_acks']}"
        )

    return errors


# ---------------------------------------------------------------------------
# runtime invariants (require live ai-status.json)
# ---------------------------------------------------------------------------
def evaluate_runtime(reconciliation: dict[str, Any], status_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify each reconciliation drift finding against live ai-status.json."""
    live = {str(task.get("id")): task for task in status_payload.get("tasks", [])}
    findings: list[dict[str, Any]] = []

    # Closure packets vs live runtime state.
    queue_statuses: dict[str, set[str]] = {}
    for action in reconciliation["closeout_actions"]:
        queue_statuses.setdefault(action["task_id"], set()).add(action["status"])
    for task_id in reconciliation["closure_packet_tasks"]:
        live_task = live.get(task_id)
        if live_task is None:
            findings.append(
                {
                    "kind": "orphaned_closure_packet",
                    "task_id": task_id,
                    "detail": "closure packet points at a task absent from ai-status.json",
                }
            )
            continue
        live_status = str(live_task.get("status", ""))
        if live_status == DONE_LIVE_STATUS:
            findings.append(
                {
                    "kind": "stale_closure_packet",
                    "task_id": task_id,
                    "detail": "closure packet still open while ai-status.json marks the task done",
                }
            )
        elif live_status not in queue_statuses.get(task_id, set()):
            findings.append(
                {
                    "kind": "closure_status_drift",
                    "task_id": task_id,
                    "detail": (
                        f"closure packet status {sorted(queue_statuses.get(task_id, set()))} "
                        f"contradicts live status {live_status!r}"
                    ),
                }
            )

    # Blockers vs live runtime state.
    for blocker in reconciliation["blockers"]:
        task_id = blocker["task_id"]
        live_task = live.get(task_id)
        if live_task is None:
            continue  # pure external blocker with no in-repo task is expected
        live_status = str(live_task.get("status", ""))
        if live_status == DONE_LIVE_STATUS and not blocker["accepted"]:
            findings.append(
                {
                    "kind": "blocker_done_but_unaccepted",
                    "task_id": task_id,
                    "detail": "ai-status.json marks the blocker done but its handback is not accepted",
                }
            )
        elif live_status in ACTIVE_LIVE_STATUSES and not blocker["accepted"]:
            findings.append(
                {
                    "kind": "blocker_has_active_implementation",
                    "task_id": task_id,
                    "detail": f"blocker now has an active in-repo task (live status {live_status!r})",
                }
            )

    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the reconciliation JSON")
    parser.add_argument("--report", action="store_true", help="emit the Markdown reconciliation truth")
    parser.add_argument(
        "--status-path",
        type=Path,
        default=DEFAULT_STATUS_ROOT / "ai-status.json",
        help="ai-status.json path; defaults to PANTHEON_STATUS_ROOT/ai-status.json or repo ai-status.json",
    )
    parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="do not read ai-status.json even if present (static reconciliation only)",
    )
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="fail when runtime drift findings are present, not only static invariants",
    )
    return parser.parse_args()


def load_status(status_path: Path, *, skip_runtime: bool) -> dict[str, Any] | None:
    if skip_runtime or not status_path.exists():
        return None
    return load_json(status_path)


def render_markdown(reconciliation: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    fleet = reconciliation["fleet_completion"]
    lines = [
        "# Product-Grade Gate Reconciliation",
        "",
        "Single reconciled truth for the product-grade evidence and fleet-closure",
        "gates. Regenerate with:",
        "",
        "```bash",
        "python3 scripts/e2e/check_product_grade_gate_reconciliation.py --report \\",
        '  --status-path "$PANTHEON_STATUS_ROOT/ai-status.json"',
        "```",
        "",
        "Static invariants are enforced by",
        "`tests/e2e/test_product_grade_gate_reconciliation.py`. Runtime drift below is a",
        "dated snapshot of live `ai-status.json`, not a committed gate; re-run the",
        "command above to refresh it.",
        "",
        "## Reconciled Counts",
        "",
        "| Metric | Value | Authoritative source |",
        "|---|---:|---|",
        f"| Blocker count (external) | {reconciliation['blocker_count']} | "
        "`PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` |",
        f"| Open blockers / pending pickup ACKs | {reconciliation['pending_pickup_acks']} | "
        "`EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` |",
        f"| Closure packets (lifecycle actions) | {reconciliation['closure_packets']} | "
        "`PRODUCT_RELEASE_CLOSEOUT_QUEUE.json` |",
        f"| Handback bundle status | `{reconciliation['bundle_status']}` | "
        "`EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` |",
    ]
    if fleet.get("available"):
        lines.append(
            f"| Fleet completion | {fleet['completion_pct']}% "
            f"({fleet['done_tasks']}/{fleet['total_tasks']} done) | "
            f"`ai-status.json` @ {fleet.get('updated_at', '')} |"
        )
    else:
        lines.append("| Fleet completion | n/a | `ai-status.json` not resolved (static run) |")

    lines.extend(["", "## Open Blockers (pending pickup ACK)", ""])
    pending = [b for b in reconciliation["blockers"] if not b["accepted"]]
    if pending:
        lines.extend(["| Task | Issue | Blocking type | Handback status |", "|---|---|---|---|"])
        for blocker in pending:
            issue = blocker["tracking_issue"].rstrip("/").rsplit("/", 1)[-1]
            lines.append(
                f"| `{blocker['task_id']}` | #{issue} | {blocker['blocking_type']} | "
                f"`{blocker['handback_status']}` |"
            )
    else:
        lines.append("None. Every external blocker handback is accepted.")

    lines.extend(["", "## Fleet Completion", ""])
    if fleet.get("available"):
        for status, count in sorted(fleet["status_counts"].items()):
            lines.append(f"- `{status}`: {count}")
    else:
        lines.append("Static run: `ai-status.json` was not resolved, so fleet completion is not reported.")

    lines.extend(["", "## Runtime Drift Findings", ""])
    if not fleet.get("available"):
        lines.append("Static run: runtime cross-check against `ai-status.json` skipped.")
    elif findings:
        lines.extend(["| Kind | Task | Detail |", "|---|---|---|"])
        for finding in findings:
            lines.append(f"| `{finding['kind']}` | `{finding['task_id']}` | {finding['detail']} |")
    else:
        lines.append("None. Every closure packet and blocker agrees with live `ai-status.json`.")

    lines.extend(
        [
            "",
            "## Drift Kinds",
            "",
            "- `orphaned_closure_packet`: closure queue names a task absent from `ai-status.json`.",
            "- `stale_closure_packet`: closure packet still open while `ai-status.json` marks it `done`.",
            "- `closure_status_drift`: closure packet status contradicts live status.",
            "- `blocker_done_but_unaccepted`: `ai-status.json` marks a blocker `done` but its handback is not accepted.",
            "- `blocker_has_active_implementation`: a live in-repo task is already implementing the blocker.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()

    external_queue = load_json(EXTERNAL_QUEUE_PATH)
    handback_board = load_json(HANDBACK_BOARD_PATH)
    pickup_board_text = PICKUP_BOARD_PATH.read_text(encoding="utf-8")
    release_queue = load_json(RELEASE_CLOSEOUT_QUEUE_PATH)
    status_payload = load_status(args.status_path, skip_runtime=args.skip_runtime)

    reconciliation = reconcile(
        external_queue,
        handback_board,
        pickup_board_text,
        release_queue,
        status_payload,
    )
    static_errors = validate_static(reconciliation)
    findings = evaluate_runtime(reconciliation, status_payload) if status_payload is not None else []
    reconciliation["runtime_findings"] = findings

    if args.json:
        print(json.dumps(reconciliation, ensure_ascii=False, indent=2))
    elif args.report:
        print(render_markdown(reconciliation, findings), end="")
    else:
        if static_errors:
            print("Product-grade gate reconciliation static invariants failed:")
            for error in static_errors:
                print(f"- {error}")
        else:
            print(
                "Product-grade gate reconciliation static invariants passed: "
                f"blockers={reconciliation['blocker_count']}, "
                f"pending_acks={reconciliation['pending_pickup_acks']}, "
                f"closure_packets={reconciliation['closure_packets']}."
            )
        if status_payload is None:
            print("Runtime cross-check skipped: ai-status.json not resolved.")
        elif findings:
            print(f"Runtime drift findings: {len(findings)}")
            for finding in findings:
                print(f"- {finding['kind']}: {finding['task_id']} — {finding['detail']}")
        else:
            print("Runtime cross-check passed: closure packets and blockers agree with ai-status.json.")

    exit_errors = list(static_errors)
    if args.strict_runtime:
        exit_errors.extend(f"{f['kind']}: {f['task_id']}" for f in findings)
    return 1 if exit_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
