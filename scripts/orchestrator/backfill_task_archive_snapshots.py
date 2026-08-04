#!/usr/bin/env python3
"""Retroactively archive tasks that were merged but never written to the archive.

Nine dependencies of the blocked deployment tasks resolve in neither the live
board nor ``ai-task-archive/``, which makes those tasks permanently
undispatchable under Control Pack 3.1. All nine were in fact completed and
merged into ``origin/dev`` on 2026-07-28; they simply never received an archive
snapshot.

This tool writes the missing snapshots. It is deliberately conservative:

* **Merge evidence is re-derived from git at run time.** No task id is trusted
  from a hard-coded list alone -- a task without a discoverable merge commit on
  the target ref is skipped, not archived. Documentation wording is never used
  as the completion signal, because it is unreliable (one task's evidence file
  still reads "still requires ... before merge" for work that was merged).
* **Idempotent.** An existing snapshot is never overwritten.
* **Dry-run by default.** ``--apply`` is required to write anything.
* **Honestly labelled.** Every snapshot carries ``backfill.retroactive: true``
  plus the merge commit and PR, so an auditor can always tell a reconstructed
  record from a real lifecycle transition.

Writing snapshot files does **not** race with a running supervisor:
``task_archive.load_archived_snapshot()`` resolves by filename, while
``save_state()`` only ever writes ``ai-status.json``. ``index.json`` is left
untouched on purpose -- it is a shared display cache that
``rebuild_archive_index()`` reconstructs by globbing this directory.

Usage::

    python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
        --archive-dir /path/to/ai-task-archive/tasks --repo . --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ARCHIVE_VERSION = 1
TERMINAL_STATUS_DONE = "done"
TERMINAL_OUTCOME_COMPLETED = "completed"
UNKNOWN_ACTOR = "UNKNOWN-HISTORICAL"

BACKFILL_SOURCE = "ODP-RUNBOOK-TASK-DEPENDENCY-GRAPH-REPAIR"

# Candidate task ids. Presence here is *not* sufficient: each is re-verified
# against git before anything is written.
CANDIDATE_TASK_IDS = (
    "ODP-AUTH-RUNTIME-RECONCILE-001",
    "ODP-MODEL-READY-COMPOSE-001",
    "ODP-LEARNINGHUB-PROD-FIX-001",
    "ODP-HEATZONE-PIT-LABEL-AUTHORITY-001",
    "ODP-P10-DEV-LANDING-FIX-001",
    "ODP-OPERATOR-LIVE-PREFLIGHT-001",
    "ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001",
    "ODP-MODEL-CAPABILITY-READINESS-001",
    "ODP-P10-R3CD-DEV-COMPOSE-001",
)

PR_PATTERN = re.compile(r"#(\d+)")


class MergeEvidence(dict):
    """Merge commit, ISO date and PR number backing one task id."""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def find_merge_evidence(repo: Path, task_id: str, ref: str) -> MergeEvidence | None:
    """Return merge evidence for ``task_id`` on ``ref``, or None when absent.

    A ``Merge pull request ... /task/<id>`` commit is the strongest signal and is
    preferred. A squash merge that names the task id in its subject is accepted
    as a fallback, since some tasks land that way.
    """

    for pattern in (f"Merge pull request.*{task_id}", task_id):
        line = _git(
            repo,
            "log",
            ref,
            "--format=%H|%cI|%s",
            f"--grep={pattern}",
            "--max-count=1",
        )
        if not line:
            continue
        sha, _, rest = line.partition("|")
        iso_date, _, subject = rest.partition("|")
        if task_id not in subject and "Merge pull request" not in subject:
            continue
        pr_match = PR_PATTERN.search(subject)
        return MergeEvidence(
            merge_commit=sha,
            merged_at=iso_date,
            merge_pr=f"#{pr_match.group(1)}" if pr_match else None,
            subject=subject,
        )
    return None


def find_repo_artifacts(repo: Path, task_id: str, limit: int = 4) -> list[str]:
    """Return repository evidence paths mentioning ``task_id``, if any."""

    evidence_root = repo / "docs" / "evidence"
    if not evidence_root.exists():
        return []
    hits: list[str] = []
    for path in sorted(evidence_root.rglob(f"*{task_id}*")):
        hits.append(str(path.relative_to(repo)))
        if len(hits) >= limit:
            break
    return hits


def build_snapshot(
    task_id: str, evidence: MergeEvidence, artifacts: list[str]
) -> dict[str, Any]:
    return {
        "version": ARCHIVE_VERSION,
        "task_id": task_id,
        "archived_at": evidence["merged_at"],
        "terminal_status": TERMINAL_STATUS_DONE,
        "terminal_outcome": TERMINAL_OUTCOME_COMPLETED,
        "task": {
            "id": task_id,
            "status": TERMINAL_STATUS_DONE,
            "owner": UNKNOWN_ACTOR,
            "reviewer": UNKNOWN_ACTOR,
            "artifacts": artifacts,
            "last_update": evidence["merged_at"],
        },
        "handoffs": [],
        "blockers": [],
        "backfill": {
            "retroactive": True,
            "created_by": BACKFILL_SOURCE,
            "basis": "merge commit on the target ref",
            "merge_commit": evidence["merge_commit"],
            "merge_pr": evidence["merge_pr"],
            "merge_subject": evidence["subject"],
            "note": (
                "Derived from repository merge evidence, not from a live "
                "lifecycle transition. owner/reviewer were not recoverable."
            ),
        },
    }


def plan(
    repo: Path, archive_dir: Path, task_ids: tuple[str, ...], ref: str
) -> tuple[list[tuple[str, dict[str, Any]]], list[str], list[str]]:
    """Return (to_write, skipped_existing, skipped_unverified)."""

    to_write: list[tuple[str, dict[str, Any]]] = []
    skipped_existing: list[str] = []
    skipped_unverified: list[str] = []

    for task_id in task_ids:
        if (archive_dir / f"{task_id}.json").exists():
            skipped_existing.append(task_id)
            continue
        evidence = find_merge_evidence(repo, task_id, ref)
        if evidence is None:
            skipped_unverified.append(task_id)
            continue
        artifacts = find_repo_artifacts(repo, task_id)
        to_write.append((task_id, build_snapshot(task_id, evidence, artifacts)))

    return to_write, skipped_existing, skipped_unverified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive-dir", required=True, help="ai-task-archive/tasks")
    parser.add_argument("--repo", default=".", help="repository providing merge evidence")
    parser.add_argument("--ref", default="origin/dev", help="ref to search for merges")
    parser.add_argument("--task", action="append", default=[], help="restrict to these ids")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true", help="actually write snapshots")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    archive_dir = Path(args.archive_dir).resolve()

    if not archive_dir.exists():
        print(f"FAIL: archive directory not found: {archive_dir}", file=sys.stderr)
        return 1
    if not (repo / ".git").exists():
        print(f"FAIL: not a git repository: {repo}", file=sys.stderr)
        return 1

    task_ids = tuple(args.task) if args.task else CANDIDATE_TASK_IDS
    to_write, existing, unverified = plan(repo, archive_dir, task_ids, args.ref)

    for task_id in existing:
        print(f"  skip (already archived): {task_id}")
    for task_id in unverified:
        print(f"  SKIP (no merge evidence on {args.ref}): {task_id}")
    for task_id, snapshot in to_write:
        bf = snapshot["backfill"]
        print(
            f"  write: {task_id}  merge={bf['merge_commit'][:12]} "
            f"pr={bf['merge_pr'] or 'n/a'}"
        )

    if not args.apply:
        print(
            f"\nDRY RUN: {len(to_write)} snapshot(s) would be written, "
            f"{len(existing)} already present, {len(unverified)} unverified. "
            "Re-run with --apply to write."
        )
        return 0

    for task_id, snapshot in to_write:
        path = archive_dir / f"{task_id}.json"
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(
        f"\nWrote {len(to_write)} snapshot(s). index.json intentionally untouched; "
        "rebuild_archive_index() reconstructs it by globbing this directory."
    )
    if unverified:
        print(
            f"{len(unverified)} task(s) had no merge evidence and were not archived."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
