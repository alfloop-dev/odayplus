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
* **Evidence is matched on delivery *form*, not on mention.** ``git log --grep``
  searches the whole commit message, so the newest commit naming a task id is
  very often some *other* task's commit that merely referenced it in its body.
  ``--grep`` is therefore used only as a cheap prefilter; every candidate is
  then re-checked with :func:`subject_delivers`, which accepts a subject only
  when it is a merge of that task's own ``task/<id>`` branch or a squash
  subject introduced by ``<id>:``. Candidates are scanned until one qualifies
  instead of stopping at the first, which is what previously turned real
  merges into "no merge evidence".
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

# "Merge pull request #678 from alfloop-dev/task/ODP-CI-FLAKE-REMEDIATION-001"
MERGE_SUBJECT_PATTERN = re.compile(
    r"^Merge pull request #(?P<pr>\d+) from (?P<source>\S+)\s*$"
)

# "[ReviewBus] ODP-PLAN-AVM-OUTCOME-001 <summary> (#587)" -- ReviewBus PRs land
# as squashes whose subject carries this prefix instead of "<id>: ".
REVIEWBUS_PREFIX_PATTERN = re.compile(r"^\[ReviewBus\]\s+", re.IGNORECASE)

# Upper bound on candidates examined per task id. --grep already narrows the
# history to commits that mention the id at all, so this only bounds the
# pathological case; it is not the "stop at the first hit" behaviour that
# caused the original false negatives.
SCAN_LIMIT = 500

DELIVERY_FORM_MERGE = "merge-commit"
DELIVERY_FORM_SQUASH = "squash-subject"
DELIVERY_FORM_REVIEWBUS = "reviewbus-subject"


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


def _task_branch(source: str) -> str | None:
    """Return the task id from a merge source ref, or None if it is not a task branch.

    Both ``alfloop-dev/task/<id>`` and the bare ``task/<id>`` occur in history.
    """

    marker = "/task/"
    index = source.find(marker)
    if index >= 0:
        return source[index + len(marker) :]
    if source.startswith("task/"):
        return source[len("task/") :]
    return None


def _introduces(text: str, task_id: str, *, allow_space: bool) -> bool:
    """True when ``text`` starts with ``task_id`` at a real identifier boundary.

    The boundary check is the whole point: ``ODP-X-001`` must not be considered
    the opener of ``ODP-X-001-SIDECAR-ACCEPTANCE: ...``.
    """

    if text[: len(task_id)].casefold() != task_id.casefold():
        return False
    tail = text[len(task_id) :]
    if tail.startswith(":"):
        return True
    return allow_space and (tail == "" or tail[:1].isspace())


def subject_delivers(subject: str, task_id: str) -> str | None:
    """Return the delivery form when ``subject`` *delivers* ``task_id``, else None.

    Only three subject shapes count as a delivery, and each must name the task
    id exactly:

    ``merge-commit``
        ``Merge pull request #N from <owner>/task/<task_id>`` -- the branch tail
        must equal the task id.
    ``squash-subject``
        ``<task_id>: <summary>``.
    ``reviewbus-subject``
        ``[ReviewBus] <task_id> <summary> (#N)``.

    Merely *mentioning* the id is not delivery. This matters because task ids
    are routinely prefixes of one another, and a sidecar's own commit normally
    names its parent in the summary. ``[ReviewBus] ODP-X-001-SIDECAR-ACCEPTANCE
    Prepare ODP-X-001 acceptance packet`` delivers the sidecar, not ``ODP-X-001``
    -- attributing it to the parent is precisely the false positive that put a
    parent task in the archive on the strength of its sidecar's merge.
    """

    subject = subject.strip()

    merge_match = MERGE_SUBJECT_PATTERN.match(subject)
    if merge_match:
        branch = _task_branch(merge_match.group("source"))
        if branch is not None and branch.casefold() == task_id.casefold():
            return DELIVERY_FORM_MERGE
        return None

    reviewbus = REVIEWBUS_PREFIX_PATTERN.match(subject)
    if reviewbus:
        rest = subject[reviewbus.end() :]
        if _introduces(rest, task_id, allow_space=True):
            return DELIVERY_FORM_REVIEWBUS
        return None

    if _introduces(subject, task_id, allow_space=False):
        return DELIVERY_FORM_SQUASH
    return None


def find_merge_evidence(
    repo: Path, task_id: str, ref: str, scan_limit: int = SCAN_LIMIT
) -> MergeEvidence | None:
    """Return merge evidence for ``task_id`` on ``ref``, or None when absent.

    ``--grep`` is a prefilter only: it matches the whole commit message, so most
    hits are other tasks' commits that merely referenced this id. Every
    candidate is re-checked with :func:`subject_delivers`, and the scan
    continues past non-delivering commits rather than giving up on the first.

    A ``Merge pull request ... /task/<id>`` commit is the strongest signal and
    is preferred; a squash subject introduced by ``<id>:`` is accepted as a
    fallback, since some tasks land that way.
    """

    output = _git(
        repo,
        "log",
        ref,
        "--format=%H|%cI|%s",
        f"--grep={task_id}",
        "--fixed-strings",
        "--regexp-ignore-case",
        f"--max-count={scan_limit}",
    )
    if not output:
        return None

    fallback: MergeEvidence | None = None
    for line in output.splitlines():
        sha, _, rest = line.partition("|")
        iso_date, _, subject = rest.partition("|")
        form = subject_delivers(subject, task_id)
        if form is None:
            continue
        pr_match = PR_PATTERN.search(subject)
        evidence = MergeEvidence(
            merge_commit=sha,
            merged_at=iso_date,
            merge_pr=f"#{pr_match.group(1)}" if pr_match else None,
            subject=subject,
            delivery_form=form,
        )
        if form == DELIVERY_FORM_MERGE:
            return evidence
        if fallback is None:
            fallback = evidence
    return fallback


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
            "delivery_form": evidence.get("delivery_form"),
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
