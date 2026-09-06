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

A second mode plans a *controlled task-history recovery batch* from a
read-only recovery inventory. It grades evidence instead of trusting it, and
it writes only the batch file: applying a batch is
``ai_status.py archive_recovery_apply``, which does it inside the canonical
status lock. See the "Controlled task-history recovery" section below.

Usage::

    python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
        --archive-dir /path/to/ai-task-archive/tasks --repo . --dry-run

    python3 scripts/orchestrator/backfill_task_archive_snapshots.py \
        --archive-dir /path/to/ai-task-archive/tasks --repo . \
        --board /path/to/ai-status.json \
        --recovery-inventory inventory.json --batch-out batch.json \
        --recovery-owner Claude --recovery-reviewer Codex
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
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
    parser.add_argument(
        "--recovery-inventory",
        help="read-only recovery inventory; switches the tool to recovery-batch planning",
    )
    parser.add_argument("--batch-out", help="where to write the recovery batch plan")
    parser.add_argument("--board", help="ai-status.json the batch is planned against")
    parser.add_argument("--recovery-owner", help="present-day recovery owner")
    parser.add_argument("--recovery-reviewer", help="present-day recovery reviewer")
    parser.add_argument("--attestations", help="JSON file of per-task acceptance/CI/runtime/approval attestations")
    parser.add_argument("--authorization", help="recovery authorization document to pin by hash")
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

    if args.recovery_inventory:
        return run_recovery_plan(args, repo=repo, archive_dir=archive_dir)

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



# ---------------------------------------------------------------------------
# Controlled task-history recovery
# ---------------------------------------------------------------------------
#
# The 2026-09-06 archive incident left 38 task ids that resolve in neither the
# live board nor ``ai-task-archive/``. A read-only inventory found a merged
# candidate PR for every one of them, but a merged PR is *delivery* evidence,
# not acceptance: it says nothing about the CI, runtime rollout or review
# approval the original ``done`` transition stood on.
#
# So this half of the tool never decides "close enough". It grades each id and
# emits an offline *batch* for a second reviewer:
#
# * ``reconstructable_done`` -- an own-branch merge proven in local git on the
#   pinned ref *and* an explicit attestation for all four of acceptance, CI,
#   runtime and approval. Only this tier may enter the terminal archive.
# * ``merge_verified`` / ``merge_claimed`` / ``no_evidence`` -- everything else.
#   These become active ``blocked`` + ``non_dispatchable`` recovery
#   placeholders carrying their gaps, never a terminal record.
#
# Nothing here writes to the canonical board or archive: the batch is a plan,
# and ``ai_status.py archive_recovery_apply`` is the only writer.

RECOVERY_SOURCE = "ORCH-ARCHIVE-HISTORY-RECOVERY-001"
RECOVERY_BATCH_SCHEMA_VERSION = 1
RECOVERY_BATCH_TYPE = "task_history_recovery_batch"
RECOVERY_INVENTORY_TYPE = "read_only_recovery_inventory"

TIER_RECONSTRUCTABLE_DONE = "reconstructable_done"
TIER_MERGE_VERIFIED = "merge_verified"
TIER_MERGE_CLAIMED = "merge_claimed"
TIER_NO_EVIDENCE = "no_evidence"

ACTION_ARCHIVE_DONE = "archive_reconstructed_done"
ACTION_BLOCKED_PLACEHOLDER = "active_blocked_placeholder"

# A merged PR alone is never enough for a reconstructed `done`; all four of
# these must be attested, each with a traceable source and a verification time.
REQUIRED_DONE_ATTESTATIONS = ("acceptance", "ci", "runtime", "approval")
ATTESTATION_REQUIRED_FIELDS = ("source", "verified_at", "verifier")

# Fields an attestation may never carry. The recovery authorization excludes
# inventing a historical owner, reviewer, approver or Human GO, so an input
# that supplies one is refused rather than quietly ignored -- ignoring it would
# let the same fabrication back in through a later, less careful reader.
FABRICATION_FIELDS = (
    "historical_owner",
    "historical_reviewer",
    "human_go",
    "approver",
    "review_approval",
)

CANDIDATE_REQUIRED_FIELDS = ("number", "url", "headRefName", "mergedAt")

REFUSAL_ACTIVE_CONFLICT = "active_task_id_conflict"
REFUSAL_ARCHIVE_CONFLICT = "archive_snapshot_exists"
REFUSAL_DUPLICATE_ID = "duplicate_inventory_id"
REFUSAL_MISSING_PROVENANCE = "missing_candidate_provenance"
REFUSAL_FABRICATED_ACTOR = "fabricated_historical_actor"

RECOVERY_WAITING_FOR = "Human/Ops"
RECOVERY_PHASE = "History Recovery"


class RecoveryInputError(Exception):
    """An input that cannot be trusted to plan a recovery batch from."""


def iso_now() -> str:
    return (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_recovery_inventory(path: Path) -> dict[str, Any]:
    """Load the read-only recovery inventory, refusing anything unrecognised."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryInputError(f"recovery inventory unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecoveryInputError("recovery inventory must be a JSON object")
    if str(payload.get("type") or "") != RECOVERY_INVENTORY_TYPE:
        raise RecoveryInputError(
            f"recovery inventory type must be {RECOVERY_INVENTORY_TYPE!r}"
        )
    if not str(payload.get("observed_at") or "").strip():
        raise RecoveryInputError("recovery inventory is missing observed_at")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RecoveryInputError("recovery inventory has no entries")
    return payload


def normalize_candidate(candidate: Any) -> tuple[dict[str, Any], list[str]]:
    """Return (normalized candidate, missing provenance fields)."""

    if not isinstance(candidate, dict):
        return {}, list(CANDIDATE_REQUIRED_FIELDS) + ["mergeCommit.oid"]
    merge_commit = candidate.get("mergeCommit")
    merge_oid = ""
    if isinstance(merge_commit, dict):
        merge_oid = str(merge_commit.get("oid") or "").strip()
    normalized = {
        "pr_number": candidate.get("number"),
        "url": str(candidate.get("url") or "").strip(),
        "title": str(candidate.get("title") or "").strip(),
        "head_ref": str(candidate.get("headRefName") or "").strip(),
        "head_oid": str(candidate.get("headRefOid") or "").strip(),
        "merge_commit": merge_oid,
        "merged_at": str(candidate.get("mergedAt") or "").strip(),
        "state": str(candidate.get("state") or "").strip(),
    }
    missing = [field for field in CANDIDATE_REQUIRED_FIELDS if not candidate.get(field)]
    if not merge_oid:
        missing.append("mergeCommit.oid")
    return normalized, missing


def _attestation_gaps(attestation: dict[str, Any] | None) -> list[str]:
    """Return the required attestations this input does not actually carry."""

    gaps: list[str] = []
    attestation = attestation if isinstance(attestation, dict) else {}
    for name in REQUIRED_DONE_ATTESTATIONS:
        item = attestation.get(name)
        if not isinstance(item, dict):
            gaps.append(name)
            continue
        if any(not str(item.get(field) or "").strip() for field in ATTESTATION_REQUIRED_FIELDS):
            gaps.append(name)
    return gaps


def grade_recovery_entry(
    repo: Path,
    entry: dict[str, Any],
    ref: str,
    *,
    attestation: dict[str, Any] | None = None,
    scan_limit: int = SCAN_LIMIT,
) -> dict[str, Any]:
    """Grade one missing task id into an evidence tier plus its gaps.

    The grading never upgrades itself on similarity: local git is the only
    thing that can produce ``merge_verified``, and only an explicit attestation
    for every one of :data:`REQUIRED_DONE_ATTESTATIONS` can lift that to
    ``reconstructable_done``.
    """

    task_id = str(entry.get("missing_id") or "").strip()
    refusals: list[str] = []
    if not task_id:
        return {
            "task_id": "",
            "evidence_tier": TIER_NO_EVIDENCE,
            "action": None,
            "gaps": ["task_id"],
            "refusals": [REFUSAL_MISSING_PROVENANCE],
            "candidates": [],
            "local_merge": None,
            "dependents": [],
        }

    candidates: list[dict[str, Any]] = []
    raw_candidates = entry.get("candidates")
    raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
    for raw in raw_candidates:
        normalized, missing = normalize_candidate(raw)
        if missing:
            refusals.append(REFUSAL_MISSING_PROVENANCE)
            normalized["missing_provenance"] = missing
        candidates.append(normalized)

    if isinstance(attestation, dict):
        fabricated = [field for field in FABRICATION_FIELDS if attestation.get(field)]
        if fabricated:
            refusals.append(REFUSAL_FABRICATED_ACTOR)

    local_merge = find_merge_evidence(repo, task_id, ref, scan_limit=scan_limit)
    gaps: list[str] = []
    if local_merge is not None:
        tier = TIER_MERGE_VERIFIED
        declared = {c["merge_commit"] for c in candidates if c.get("merge_commit")}
        if declared and local_merge["merge_commit"] not in declared:
            # Not a refusal -- a cross-repo or rebased candidate legitimately
            # names a different commit -- but it is a gap, so it also keeps the
            # id out of `reconstructable_done`. Two sources disagree about what
            # delivered this task; promoting on one of them would be picking a
            # winner silently, which is the whole failure mode this tool exists
            # to avoid.
            gaps.append("candidate_merge_commit_mismatch")
    elif candidates:
        tier = TIER_MERGE_CLAIMED
        gaps.append("merge_not_verifiable_on_ref")
    else:
        tier = TIER_NO_EVIDENCE
        gaps.append("no_candidate_delivery_evidence")

    gaps.extend(_attestation_gaps(attestation))
    # Every gap counts, not only the missing attestations: a verified merge
    # promotes to `reconstructable_done` only when nothing at all is open.
    if tier == TIER_MERGE_VERIFIED and not gaps:
        tier = TIER_RECONSTRUCTABLE_DONE

    dependents = [
        {
            "id": str((item or {}).get("id") or "").strip(),
            "status": str((item or {}).get("status") or "").strip(),
        }
        for item in (entry.get("dependents") or [])
        if isinstance(item, dict) and str((item or {}).get("id") or "").strip()
    ]

    action = (
        ACTION_ARCHIVE_DONE
        if tier == TIER_RECONSTRUCTABLE_DONE
        else ACTION_BLOCKED_PLACEHOLDER
    )
    return {
        "task_id": task_id,
        "evidence_tier": tier,
        "action": action,
        "gaps": gaps,
        "refusals": sorted(set(refusals)),
        "candidates": candidates,
        "local_merge": dict(local_merge) if local_merge else None,
        "dependents": dependents,
    }


def _history_recovery_block(
    grading: dict[str, Any],
    *,
    record_kind: str,
    recovery_owner: str,
    recovery_reviewer: str,
    generated_at: str,
    baseline: dict[str, Any],
    attestation: dict[str, Any] | None,
) -> dict[str, Any]:
    """The provenance block every reconstructed record carries.

    ``historical_actors`` and ``recovery_actors`` are deliberately separate
    keys. Collapsing them would put a present-day name where an unrecoverable
    one belongs, which is the single change that would make a reconstructed
    record indistinguishable from an original one.
    """

    return {
        "reconstructed": True,
        "record_kind": record_kind,
        "created_by": RECOVERY_SOURCE,
        "generated_at": generated_at,
        "evidence_tier": grading["evidence_tier"],
        "gaps": list(grading["gaps"]),
        "historical_actors": {
            "owner": UNKNOWN_ACTOR,
            "reviewer": UNKNOWN_ACTOR,
            "human_go": UNKNOWN_ACTOR,
        },
        "recovery_actors": {"owner": recovery_owner, "reviewer": recovery_reviewer},
        "evidence": {
            "local_merge": grading["local_merge"],
            "candidates": grading["candidates"],
            "attestations": deepcopy(attestation) if isinstance(attestation, dict) else None,
            "verified_at": generated_at,
            "verifier": recovery_owner,
        },
        "known_dependents": list(grading["dependents"]),
        "baseline": {
            "ref": baseline.get("ref"),
            "ref_commit": baseline.get("ref_commit"),
            "inventory_sha256": baseline.get("inventory_sha256"),
            "authorization_sha256": baseline.get("authorization_sha256"),
        },
        "note": (
            "重建記錄，來源為 PR/commit 等可驗證證據，不是原始 archive bytes，"
            "也不代表原始驗收、CI、部署或核准已完成。"
        ),
    }


def build_recovery_placeholder(
    grading: dict[str, Any],
    *,
    recovery_owner: str,
    recovery_reviewer: str,
    generated_at: str,
    baseline: dict[str, Any],
    attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the active ``blocked`` + ``non_dispatchable`` recovery record."""

    task_id = grading["task_id"]
    return {
        "id": task_id,
        "title": f"歷史復原佔位：{task_id}",
        "summary_zh": (
            "2026-09-06 archive 事故遺失的任務歷史。證據不足以重建 done，"
            "維持 blocked 並保留已知依賴與候選來源，等待人工裁決。"
        ),
        "phase": RECOVERY_PHASE,
        "owner": recovery_owner,
        "reviewer": recovery_reviewer,
        "status": "blocked",
        "non_dispatchable": True,
        "waiting_for": RECOVERY_WAITING_FOR,
        "priority": "P2",
        "depends_on": [],
        "artifacts": [],
        "acceptance": [
            "補齊 acceptance/CI/runtime/approval 可驗證證據後才可改為 done；"
            "否則由人類裁決以 blocked 收尾。",
        ],
        "next": (
            "等待人工裁決：補齊 "
            + "/".join(REQUIRED_DONE_ATTESTATIONS)
            + " 證據，或確認此 ID 以 blocked 結案。"
        ),
        "last_update": generated_at,
        "history_recovery": _history_recovery_block(
            grading,
            record_kind=RECORD_KIND_PLACEHOLDER,
            recovery_owner=recovery_owner,
            recovery_reviewer=recovery_reviewer,
            generated_at=generated_at,
            baseline=baseline,
            attestation=attestation,
        ),
    }


def build_recovery_done_task(
    grading: dict[str, Any],
    *,
    recovery_owner: str,
    recovery_reviewer: str,
    generated_at: str,
    baseline: dict[str, Any],
    artifacts: list[str],
    attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the task record a ``reconstructable_done`` id would be archived as.

    ``owner``/``reviewer`` stay :data:`UNKNOWN_ACTOR`: the people who ran the
    original task are not recoverable, and the present-day recovery pair is
    recorded separately under ``history_recovery.recovery_actors``.
    """

    local_merge = grading["local_merge"] or {}
    return {
        "id": grading["task_id"],
        "title": local_merge.get("subject") or grading["task_id"],
        "phase": RECOVERY_PHASE,
        "owner": UNKNOWN_ACTOR,
        "reviewer": UNKNOWN_ACTOR,
        "status": TERMINAL_STATUS_DONE,
        "terminal_outcome": TERMINAL_OUTCOME_COMPLETED,
        "depends_on": [],
        "artifacts": artifacts,
        "next": "Reconstructed from verifiable delivery and acceptance evidence.",
        "last_update": local_merge.get("merged_at") or generated_at,
        "history_recovery": _history_recovery_block(
            grading,
            record_kind=RECORD_KIND_DONE,
            recovery_owner=recovery_owner,
            recovery_reviewer=recovery_reviewer,
            generated_at=generated_at,
            baseline=baseline,
            attestation=attestation,
        ),
    }


def capture_recovery_baseline(
    *,
    repo: Path,
    ref: str,
    board_path: Path,
    archive_dir: Path,
    inventory_path: Path,
    authorization_path: Path | None,
) -> dict[str, Any]:
    """Pin every input the batch is only valid against.

    ``archive_snapshot_digests`` is the guard that keeps the six snapshots that
    survived the incident intact: the apply step recomputes it and refuses the
    whole batch if any existing byte moved.
    """

    try:
        board = json.loads(board_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryInputError(f"board unreadable: {exc}") from exc
    if not isinstance(board, dict):
        raise RecoveryInputError("board must be a JSON object")
    board_revision = str(board.get("_status_write_revision") or "").strip()
    if not board_revision:
        raise RecoveryInputError(
            "board has no _status_write_revision; refusing to plan against an "
            "unpinnable baseline"
        )

    ref_commit = _git(repo, "rev-parse", ref)
    if not ref_commit:
        raise RecoveryInputError(f"cannot resolve ref {ref!r} in {repo}")

    index_path = archive_dir.parent / "index.json"
    digests = {
        path.stem: sha256_file(path) for path in sorted(archive_dir.glob("*.json"))
    }
    return {
        "repo": str(repo),
        "ref": ref,
        "ref_commit": ref_commit,
        "board_path": str(board_path),
        "board_sha256": sha256_file(board_path),
        "board_revision": board_revision,
        "board_active_task_ids": sorted(
            str((task or {}).get("id") or "").strip()
            for task in (board.get("tasks") or [])
            if isinstance(task, dict) and str((task or {}).get("id") or "").strip()
        ),
        "archive_tasks_dir": str(archive_dir),
        "archive_index_path": str(index_path),
        "archive_index_sha256": sha256_file(index_path) if index_path.exists() else None,
        "archive_snapshot_digests": digests,
        "inventory_path": str(inventory_path),
        "inventory_sha256": sha256_file(inventory_path),
        "authorization_path": str(authorization_path) if authorization_path else None,
        "authorization_sha256": (
            sha256_file(authorization_path)
            if authorization_path and authorization_path.exists()
            else None
        ),
    }


# --- the shared batch validator --------------------------------------------
#
# Between planning and applying, the batch is a plain JSON file that a reviewer
# -- or anyone else -- can edit. So the apply step must not assume the grading
# above produced what it is reading: it has to re-derive every property that
# grading was supposed to guarantee. That check lives here, once, and both
# halves call it. A second copy inside the writer is precisely how an apply
# path ends up admitting a shape the planner would never emit.

RECOVERY_EVIDENCE_TIERS = (
    TIER_RECONSTRUCTABLE_DONE,
    TIER_MERGE_VERIFIED,
    TIER_MERGE_CLAIMED,
    TIER_NO_EVIDENCE,
)

RECORD_KIND_DONE = "reconstructed_done"
RECORD_KIND_PLACEHOLDER = "recovery_placeholder"
RECOVERY_RECORD_KINDS = {
    ACTION_ARCHIVE_DONE: RECORD_KIND_DONE,
    ACTION_BLOCKED_PLACEHOLDER: RECORD_KIND_PLACEHOLDER,
}

PLACEHOLDER_STATUS = "blocked"

# Baseline keys the apply step compares against live state before it writes.
# An absent key is refused rather than skipped: a batch that simply omitted
# ``board_sha256`` would otherwise buy itself an unchecked baseline.
REQUIRED_BASELINE_FIELDS = (
    "repo",
    "ref",
    "ref_commit",
    "board_revision",
    "board_sha256",
    "inventory_path",
    "inventory_sha256",
    "authorization_path",
    "authorization_sha256",
)

# The provenance fields every record copies from the batch baseline. They are
# re-compared per record so a hand-edited entry cannot carry a different origin
# from the batch it travels in.
RECORD_BASELINE_FIELDS = (
    "ref",
    "ref_commit",
    "inventory_sha256",
    "authorization_sha256",
)


NORMALIZED_CANDIDATE_REQUIRED_FIELDS = (
    "pr_number",
    "url",
    "head_ref",
    "merged_at",
    "merge_commit",
)


def validate_candidate_provenance(task_id: str, candidate: Any) -> list[str]:
    """Validate one candidate PR record within a recovery entry.

    Re-derives provenance completeness from the raw fields instead of trusting
    any `missing_provenance` flag.
    """
    if not isinstance(candidate, dict):
        return [f"{task_id}: candidate is not a JSON object (got {type(candidate).__name__})"]

    problems: list[str] = []
    missing_fields: list[str] = []

    pr_number = candidate.get("pr_number")
    if pr_number is None or not isinstance(pr_number, int) or pr_number <= 0:
        missing_fields.append("pr_number")

    for field in ("url", "head_ref", "merged_at", "merge_commit"):
        val = candidate.get(field)
        if not isinstance(val, str) or not val.strip():
            missing_fields.append(field)

    if missing_fields:
        pr_label = repr(pr_number) if pr_number is not None else "<no pr_number>"
        problems.append(
            f"{task_id}: candidate PR {pr_label} is missing provenance {missing_fields}"
        )

    if candidate.get("missing_provenance"):
        flagged = candidate.get("missing_provenance")
        pr_label = repr(pr_number) if pr_number is not None else "<no pr_number>"
        msg = f"{task_id}: candidate PR {pr_label} is missing provenance {flagged}"
        if msg not in problems:
            problems.append(msg)

    return problems


def _record_history_problems(
    task_id: str,
    record: dict[str, Any],
    *,
    action: str,
    tier: str,
    gaps: list[Any],
    recovery_owner: str,
    recovery_reviewer: str,
    baseline: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Check one record's provenance block; returns (problems, that block)."""

    history = record.get("history_recovery")
    if not isinstance(history, dict):
        return [f"{task_id}: record carries no history_recovery provenance"], {}

    problems: list[str] = []
    if history.get("reconstructed") is not True:
        problems.append(f"{task_id}: history_recovery.reconstructed must be true")
    if str(history.get("created_by") or "") != RECOVERY_SOURCE:
        problems.append(
            f"{task_id}: history_recovery.created_by must be {RECOVERY_SOURCE!r}"
        )
    expected_kind = RECOVERY_RECORD_KINDS[action]
    if str(history.get("record_kind") or "") != expected_kind:
        problems.append(
            f"{task_id}: record_kind {history.get('record_kind')!r} does not match "
            f"action {action!r} (expected {expected_kind!r})"
        )
    if str(history.get("evidence_tier") or "") != tier:
        problems.append(
            f"{task_id}: record evidence_tier {history.get('evidence_tier')!r} "
            f"disagrees with the entry's {tier!r}"
        )
    if list(history.get("gaps") or []) != list(gaps):
        problems.append(
            f"{task_id}: record gaps {history.get('gaps')!r} disagree with the "
            f"entry's {list(gaps)!r}"
        )

    historical = history.get("historical_actors")
    historical = historical if isinstance(historical, dict) else {}
    if any(
        str(historical.get(field) or "") != UNKNOWN_ACTOR
        for field in ("owner", "reviewer", "human_go")
    ):
        problems.append(
            f"{task_id}: historical_actors must stay {UNKNOWN_ACTOR} for "
            "owner/reviewer/human_go; the original actors are not recoverable"
        )
    if history.get("recovery_actors") != {
        "owner": recovery_owner,
        "reviewer": recovery_reviewer,
    }:
        problems.append(
            f"{task_id}: record recovery_actors {history.get('recovery_actors')!r} do "
            f"not match the batch pair ({recovery_owner!r}, {recovery_reviewer!r})"
        )

    evidence = history.get("evidence")
    if not isinstance(evidence, dict):
        problems.append(f"{task_id}: record carries no evidence block")
        evidence = {}
    raw_candidates = evidence.get("candidates")
    if raw_candidates is None:
        problems.append(f"{task_id}: record evidence carries no candidates list")
    elif not isinstance(raw_candidates, list):
        problems.append(f"{task_id}: record evidence candidates must be a list")
    else:
        for candidate in raw_candidates:
            problems.extend(validate_candidate_provenance(task_id, candidate))
    attestations = evidence.get("attestations")
    if isinstance(attestations, dict):
        fabricated = sorted(
            field for field in FABRICATION_FIELDS if attestations.get(field)
        )
        if fabricated:
            problems.append(
                f"{task_id}: attestations may not supply {fabricated}; a historical "
                "owner, reviewer, approver or Human GO is never reconstructed"
            )

    record_baseline = history.get("baseline")
    record_baseline = record_baseline if isinstance(record_baseline, dict) else {}
    drifted = [
        field
        for field in RECORD_BASELINE_FIELDS
        if record_baseline.get(field) != baseline.get(field)
    ]
    if drifted:
        problems.append(
            f"{task_id}: record baseline {drifted} does not match the batch baseline"
        )
    return problems, history


def validate_recovery_entry(
    entry: Any,
    *,
    recovery_owner: str,
    recovery_reviewer: str,
    baseline: dict[str, Any],
) -> list[str]:
    """Every reason this entry may not be applied. Empty means admissible.

    The two actions are held to deliberately different shapes. A reconstructed
    ``done`` is a terminal claim, so it must carry the top evidence tier with no
    open gaps, a complete attestation set and a verified merge, and it may not
    name anyone alive today. A placeholder is the conservative outcome, so it
    must be ``blocked`` and ``non_dispatchable``: an entry that flipped either
    field would land on the board and be dispatched as ordinary work.
    """

    if not isinstance(entry, dict):
        return ["an entry is not a JSON object"]
    task_id = str(entry.get("task_id") or "").strip()
    if not task_id:
        return ["an entry carries no task_id"]
    action = entry.get("action")
    if action not in RECOVERY_RECORD_KINDS:
        return [f"{task_id}: unknown action {action!r}"]
    record = entry.get("record")
    if not isinstance(record, dict):
        return [f"{task_id}: entry carries no record"]

    problems: list[str] = []
    if str(record.get("id") or "").strip() != task_id:
        problems.append(
            f"{task_id}: record id {str(record.get('id') or '')!r} does not match the "
            "entry task_id"
        )
    tier = str(entry.get("evidence_tier") or "").strip()
    if tier not in RECOVERY_EVIDENCE_TIERS:
        problems.append(f"{task_id}: unknown evidence_tier {tier!r}")
    gaps = entry.get("gaps")
    if not isinstance(gaps, list):
        problems.append(f"{task_id}: gaps must be a list, got {type(gaps).__name__}")
        gaps = []

    history_problems, history = _record_history_problems(
        task_id,
        record,
        action=action,
        tier=tier,
        gaps=gaps,
        recovery_owner=recovery_owner,
        recovery_reviewer=recovery_reviewer,
        baseline=baseline,
    )
    problems.extend(history_problems)
    evidence = history.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    record_actors = (str(record.get("owner") or ""), str(record.get("reviewer") or ""))

    if action == ACTION_ARCHIVE_DONE:
        if tier != TIER_RECONSTRUCTABLE_DONE:
            problems.append(
                f"{task_id}: only {TIER_RECONSTRUCTABLE_DONE} may enter the terminal "
                f"archive, not {tier!r}"
            )
        if gaps:
            problems.append(
                f"{task_id}: a reconstructed done record may not carry open gaps "
                f"{sorted(str(gap) for gap in gaps)}"
            )
        if str(record.get("status") or "") != TERMINAL_STATUS_DONE:
            problems.append(
                f"{task_id}: reconstructed archive record status must be "
                f"{TERMINAL_STATUS_DONE!r}, got {record.get('status')!r}"
            )
        if str(record.get("terminal_outcome") or "") != TERMINAL_OUTCOME_COMPLETED:
            problems.append(
                f"{task_id}: reconstructed archive record terminal_outcome must be "
                f"{TERMINAL_OUTCOME_COMPLETED!r}, got {record.get('terminal_outcome')!r}"
            )
        if record_actors != (UNKNOWN_ACTOR, UNKNOWN_ACTOR):
            problems.append(
                f"{task_id}: reconstructed archive record must keep owner/reviewer as "
                f"{UNKNOWN_ACTOR}, got {record_actors}"
            )
        missing_attestations = _attestation_gaps(evidence.get("attestations"))
        if missing_attestations:
            problems.append(
                f"{task_id}: reconstructed done has no complete attestation for "
                f"{sorted(missing_attestations)}"
            )
        local_merge = evidence.get("local_merge")
        if not isinstance(local_merge, dict) or not str(
            local_merge.get("merge_commit") or ""
        ).strip():
            problems.append(
                f"{task_id}: reconstructed done carries no verified local merge commit"
            )
        candidates = evidence.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            problems.append(
                f"{task_id}: reconstructed done carries no candidate PR records"
            )
        if not str(entry.get("archived_at") or "").strip():
            problems.append(f"{task_id}: reconstructed done carries no archived_at")
    else:
        if str(record.get("status") or "") != PLACEHOLDER_STATUS:
            problems.append(
                f"{task_id}: recovery placeholder status must be "
                f"{PLACEHOLDER_STATUS!r}, got {record.get('status')!r}"
            )
        if record.get("non_dispatchable") is not True:
            problems.append(
                f"{task_id}: recovery placeholder must be non_dispatchable; a "
                "dispatchable placeholder is ordinary work on the board"
            )
        if record.get("terminal_outcome"):
            problems.append(
                f"{task_id}: recovery placeholder may not carry a terminal_outcome"
            )
        if not str(record.get("waiting_for") or "").strip():
            problems.append(f"{task_id}: recovery placeholder must name what it waits for")
        if record_actors != (recovery_owner, recovery_reviewer):
            problems.append(
                f"{task_id}: placeholder actors {record_actors} do not match the batch "
                f"recovery pair ({recovery_owner!r}, {recovery_reviewer!r})"
            )
        if entry.get("archived_at"):
            problems.append(
                f"{task_id}: a placeholder stays on the active board and is never archived"
            )
    return problems


def validate_recovery_batch(batch: Any, *, for_apply: bool = True) -> list[str]:
    """Every reason this batch document may not be applied as a whole.

    ``for_apply`` is the difference between a plan and a write. A plan may
    legitimately report refusals -- that report is the point of reviewing it --
    but an apply is all-or-nothing, so a single refusal disqualifies the entire
    document rather than the one id it names.
    """

    if not isinstance(batch, dict):
        return ["recovery batch must be a JSON object"]
    if str(batch.get("type") or "") != RECOVERY_BATCH_TYPE:
        return [f"recovery batch type must be {RECOVERY_BATCH_TYPE!r}"]
    if int(batch.get("schema_version") or 0) != RECOVERY_BATCH_SCHEMA_VERSION:
        return [
            f"recovery batch schema_version must be {RECOVERY_BATCH_SCHEMA_VERSION}"
        ]

    problems: list[str] = []
    if batch.get("canonical_apply_performed"):
        problems.append("batch is already marked canonical_apply_performed")

    actors = batch.get("recovery_actors")
    actors = actors if isinstance(actors, dict) else {}
    owner = str(actors.get("owner") or "").strip()
    reviewer = str(actors.get("reviewer") or "").strip()
    if not owner or not reviewer:
        problems.append("batch names no recovery owner/reviewer pair")
    elif owner == reviewer:
        problems.append("recovery reviewer cannot equal the recovery owner")
    if UNKNOWN_ACTOR in {owner, reviewer}:
        problems.append(
            f"{UNKNOWN_ACTOR} names the unrecoverable historical actor and can never "
            "be a present-day recovery actor"
        )

    baseline = batch.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    missing_baseline = [
        field
        for field in REQUIRED_BASELINE_FIELDS
        if not str(baseline.get(field) or "").strip()
    ]
    if missing_baseline:
        problems.append(
            f"batch baseline is missing required provenance {missing_baseline}"
        )
    if not isinstance(baseline.get("archive_snapshot_digests"), dict):
        problems.append("batch baseline carries no archive_snapshot_digests")

    entries = batch.get("entries")
    if not isinstance(entries, list):
        problems.append("batch entries must be a list")
        return problems

    seen: dict[str, int] = {}
    for entry in entries:
        problems.extend(
            validate_recovery_entry(
                entry,
                recovery_owner=owner,
                recovery_reviewer=reviewer,
                baseline=baseline,
            )
        )
        if isinstance(entry, dict):
            task_id = str(entry.get("task_id") or "").strip()
            if task_id:
                seen[task_id] = seen.get(task_id, 0) + 1
    duplicates = sorted(task_id for task_id, count in seen.items() if count > 1)
    if duplicates:
        problems.append(f"batch repeats task id(s) {duplicates}")

    if for_apply:
        refusals = [item for item in (batch.get("refusals") or []) if item]
        if refusals:
            named = sorted(
                str((item or {}).get("task_id") or "<no id>")
                for item in refusals
                if isinstance(item, dict)
            )
            problems.append(
                f"batch carries {len(refusals)} planning refusal(s) for {named}; a "
                "batch is applied all-or-nothing, so re-plan without them rather than "
                "applying the remaining entries"
            )
        if not entries:
            problems.append("batch has no entries to apply")
    return problems


def build_recovery_batch(
    *,
    repo: Path,
    archive_dir: Path,
    board_path: Path,
    inventory_path: Path,
    ref: str,
    recovery_owner: str,
    recovery_reviewer: str,
    attestations: dict[str, Any] | None = None,
    authorization_path: Path | None = None,
    generated_at: str | None = None,
    scan_limit: int = SCAN_LIMIT,
) -> dict[str, Any]:
    """Grade the whole inventory into one reviewable, apply-ready batch."""

    owner = str(recovery_owner or "").strip()
    reviewer = str(recovery_reviewer or "").strip()
    if not owner or not reviewer:
        raise RecoveryInputError("recovery owner and reviewer are both required")
    if owner == reviewer:
        raise RecoveryInputError("recovery reviewer cannot equal recovery owner")
    if UNKNOWN_ACTOR in {owner, reviewer}:
        raise RecoveryInputError(
            f"{UNKNOWN_ACTOR} names the unrecoverable historical actor and can "
            "never be the present-day recovery owner or reviewer"
        )
    if authorization_path is None:
        raise RecoveryInputError(
            "a recovery batch must cite the authorization document it was planned "
            "under; the apply step re-hashes it and refuses a batch whose "
            "authorization has drifted"
        )
    if not authorization_path.exists():
        raise RecoveryInputError(f"authorization document not found: {authorization_path}")

    inventory = load_recovery_inventory(inventory_path)
    baseline = capture_recovery_baseline(
        repo=repo,
        ref=ref,
        board_path=board_path,
        archive_dir=archive_dir,
        inventory_path=inventory_path,
        authorization_path=authorization_path,
    )
    generated_at = generated_at or iso_now()
    attestations = attestations if isinstance(attestations, dict) else {}
    active_ids = set(baseline["board_active_task_ids"])
    archived_ids = set(baseline["archive_snapshot_digests"])

    entries: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_entry in inventory["entries"]:
        if not isinstance(raw_entry, dict):
            refusals.append(
                {"task_id": "", "reasons": [REFUSAL_MISSING_PROVENANCE], "detail": "entry is not an object"}
            )
            continue
        grading = grade_recovery_entry(
            repo,
            raw_entry,
            ref,
            attestation=attestations.get(str(raw_entry.get("missing_id") or "").strip()),
            scan_limit=scan_limit,
        )
        task_id = grading["task_id"]
        reasons = list(grading["refusals"])
        if not task_id:
            refusals.append({"task_id": "", "reasons": reasons or [REFUSAL_MISSING_PROVENANCE], "detail": "missing_id"})
            continue
        if task_id in seen:
            reasons.append(REFUSAL_DUPLICATE_ID)
        seen.add(task_id)
        if task_id in active_ids:
            reasons.append(REFUSAL_ACTIVE_CONFLICT)
        if task_id in archived_ids:
            reasons.append(REFUSAL_ARCHIVE_CONFLICT)
        if reasons:
            refusals.append(
                {
                    "task_id": task_id,
                    "reasons": sorted(set(reasons)),
                    "evidence_tier": grading["evidence_tier"],
                }
            )
            continue

        attestation = attestations.get(task_id)
        if grading["action"] == ACTION_ARCHIVE_DONE:
            record = build_recovery_done_task(
                grading,
                recovery_owner=owner,
                recovery_reviewer=reviewer,
                generated_at=generated_at,
                baseline=baseline,
                artifacts=find_repo_artifacts(repo, task_id),
                attestation=attestation,
            )
            archived_at = (grading["local_merge"] or {}).get("merged_at") or generated_at
        else:
            record = build_recovery_placeholder(
                grading,
                recovery_owner=owner,
                recovery_reviewer=reviewer,
                generated_at=generated_at,
                baseline=baseline,
                attestation=attestation,
            )
            archived_at = None

        entries.append(
            {
                "task_id": task_id,
                "action": grading["action"],
                "evidence_tier": grading["evidence_tier"],
                "gaps": grading["gaps"],
                "archived_at": archived_at,
                "record": record,
            }
        )

    counts = {
        "inventory_entries": len(inventory["entries"]),
        "planned": len(entries),
        ACTION_ARCHIVE_DONE: sum(1 for item in entries if item["action"] == ACTION_ARCHIVE_DONE),
        ACTION_BLOCKED_PLACEHOLDER: sum(
            1 for item in entries if item["action"] == ACTION_BLOCKED_PLACEHOLDER
        ),
        "refused": len(refusals),
    }
    batch = {
        "schema_version": RECOVERY_BATCH_SCHEMA_VERSION,
        "type": RECOVERY_BATCH_TYPE,
        "generated_at": generated_at,
        "generated_by": RECOVERY_SOURCE,
        "requires_maintenance_hold": True,
        "canonical_apply_performed": False,
        "recovery_actors": {"owner": owner, "reviewer": reviewer},
        "authorization": {
            "path": str(authorization_path) if authorization_path else None,
            "sha256": baseline["authorization_sha256"],
            "scope": (
                "依可驗證證據受控重建歷史；缺證據維持 blocked。不含補造 Human GO、"
                "核准者身分或 production 授權。"
            ),
        },
        "inventory": {
            "observed_at": inventory.get("observed_at"),
            "authoritative": bool(inventory.get("authoritative")),
            "limitations": list(inventory.get("limitations") or []),
        },
        "baseline": baseline,
        "counts": counts,
        "applicable": not refusals,
        "entries": entries,
        "refusals": refusals,
    }
    # The planner holds itself to the validator the writer will re-run. If these
    # two ever disagree, the disagreement surfaces here at plan time rather than
    # as a refusal in the middle of a canonical apply.
    self_check = validate_recovery_batch(batch, for_apply=False)
    if self_check:
        raise RecoveryInputError(
            "planner produced a batch its own validator refuses: "
            + "; ".join(self_check)
        )
    return batch


def run_recovery_plan(args: argparse.Namespace, *, repo: Path, archive_dir: Path) -> int:
    """Plan a recovery batch. Writes the plan file and nothing else.

    ``--apply`` is refused here on purpose. The legacy backfill mode writes
    archive snapshots directly because it only ever adds a file per verified
    merge; a recovery batch also creates *board* rows, so its only writer is
    ``ai_status.py archive_recovery_apply``, which does that inside the
    canonical status lock.
    """

    if args.apply:
        print(
            "FAIL: --apply is not available in recovery mode. Plan the batch here, "
            "then apply it with `ai_status.py archive_recovery_apply`.",
            file=sys.stderr,
        )
        return 1
    missing = [
        name
        for name, value in (
            ("--batch-out", args.batch_out),
            ("--board", args.board),
            ("--recovery-owner", args.recovery_owner),
            ("--recovery-reviewer", args.recovery_reviewer),
            ("--authorization", args.authorization),
        )
        if not value
    ]
    if missing:
        print(f"FAIL: recovery mode requires {', '.join(missing)}", file=sys.stderr)
        return 1

    attestations: dict[str, Any] = {}
    if args.attestations:
        try:
            loaded = json.loads(Path(args.attestations).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL: attestations unreadable: {exc}", file=sys.stderr)
            return 1
        if not isinstance(loaded, dict):
            print("FAIL: attestations must be a JSON object keyed by task id", file=sys.stderr)
            return 1
        attestations = loaded

    try:
        batch = build_recovery_batch(
            repo=repo,
            archive_dir=archive_dir,
            board_path=Path(args.board).resolve(),
            inventory_path=Path(args.recovery_inventory).resolve(),
            ref=args.ref,
            recovery_owner=args.recovery_owner,
            recovery_reviewer=args.recovery_reviewer,
            attestations=attestations,
            authorization_path=Path(args.authorization).resolve() if args.authorization else None,
        )
    except RecoveryInputError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.batch_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    counts = batch["counts"]
    for entry in batch["entries"]:
        print(
            f"  plan {entry['action']}: {entry['task_id']} "
            f"tier={entry['evidence_tier']} gaps={','.join(entry['gaps']) or 'none'}"
        )
    for refusal in batch["refusals"]:
        print(f"  REFUSE {refusal['task_id'] or '<no id>'}: {','.join(refusal['reasons'])}")
    print(
        f"\nPLAN ONLY: {counts['planned']} entr(ies) planned "
        f"({counts[ACTION_ARCHIVE_DONE]} reconstructed done, "
        f"{counts[ACTION_BLOCKED_PLACEHOLDER]} blocked placeholders), "
        f"{counts['refused']} refused. Nothing was written to the canonical "
        f"board or archive. Batch: {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
