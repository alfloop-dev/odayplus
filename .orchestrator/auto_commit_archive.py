#!/usr/bin/env python3
"""Auto-commit archive metadata and task briefs that the supervisor generates
in the main worktree but no per-task worker PR includes.

Background:
  When a worker calls ``scripts/ai-status.sh done`` it writes
  ``ai-task-archive/tasks/<TASK-ID>.json`` and updates
  ``ai-task-archive/index.json`` in ``PANTHEON_STATUS_ROOT`` (the main
  worktree), not in the worker's per-task worktree.  Worker PRs therefore
  do not include these files, so they accumulate untracked on disk
  forever (the OPS-ARCHIVE-BACKFILL-001 incident).

What this script does:
  1. Detects ``ai-task-archive/tasks/*.json`` + ``.orchestrator/task-briefs/*.md``
     that are untracked in the main worktree, plus a modified
     ``ai-task-archive/index.json``.
  2. If trigger conditions are met (>= MIN_FILES pending OR oldest file
     >= MIN_AGE_SECONDS old), commits in a worktree already leased by the
     supervisor's Worker Manager, then opens a PR with task_finalize.sh.
  3. Skips if an OPS-ARCHIVE-AUTO-COMMIT-* PR is already open (avoid
     duplicate PRs while one is mid-merge).
  4. Holds .orchestrator/auto_commit_archive.lock during execution;
     stale lock (>1h) is broken automatically.

Invocation:
  * Supervisor: the only mutating mode; it passes --workspace + --task-id
  * Manual:     python3 .orchestrator/auto_commit_archive.py --dry-run
  * Supervisor periodic hook (see auto_commit_archive_settings in
    supervisor.py).

Exit codes:
  0  success or nothing to do
  1  precondition / lock failure (also non-fatal for callers)
  2  hard error during PR flow (lock released; caller should retry later)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def _root() -> Path:
    raw = os.environ.get("PANTHEON_STATUS_ROOT")
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    return Path(__file__).resolve().parents[1]


ROOT = _root()
LOCK_FILE = ROOT / ".orchestrator" / "auto_commit_archive.lock"
TASK_ID_PREFIX = "OPS-ARCHIVE-AUTO-COMMIT"
MIN_FILES = 5
MIN_AGE_SECONDS = 4 * 3600
STALE_LOCK_SECONDS = 3600


def iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check)


def detect_pending() -> dict[str, object]:
    """Return {'briefs': [...], 'archives': [...], 'index_modified': bool}."""
    briefs: list[str] = []
    archives: list[str] = []
    out = _run(["git", "-C", str(ROOT), "status", "--short"], check=False).stdout
    for raw in out.split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("?? .orchestrator/task-briefs/"):
            briefs.append(line[3:].strip())
        elif line.startswith("?? ai-task-archive/tasks/"):
            archives.append(line[3:].strip())
    index_modified = (
        _run(
            ["git", "-C", str(ROOT), "diff", "--quiet", "--", "ai-task-archive/index.json"],
            check=False,
        ).returncode
        != 0
    )
    return {"briefs": briefs, "archives": archives, "index_modified": index_modified}


def should_trigger(pending: dict[str, object]) -> tuple[bool, str]:
    briefs = pending["briefs"]  # type: ignore[index]
    archives = pending["archives"]  # type: ignore[index]
    total = len(briefs) + len(archives)
    if total == 0 and not pending["index_modified"]:
        return False, "no pending files"
    if total >= MIN_FILES:
        return True, f"pending file count {total} >= {MIN_FILES}"
    oldest = float("inf")
    for f in [*briefs, *archives]:
        p = ROOT / str(f)
        try:
            oldest = min(oldest, p.stat().st_mtime)
        except OSError:
            pass
    if oldest == float("inf"):
        return False, "no readable pending files (only index modified)"
    age = time.time() - oldest
    if age >= MIN_AGE_SECONDS:
        return True, f"oldest pending file {age/3600:.1f}h old >= {MIN_AGE_SECONDS/3600}h"
    return False, f"only {total} pending file(s), oldest {age/3600:.1f}h old"


def open_pr_exists() -> bool:
    """True if any OPS-ARCHIVE-AUTO-COMMIT-* PR is currently open."""
    try:
        proc = _run(
            ["gh", "pr", "list", "--state", "open", "--search", TASK_ID_PREFIX, "--json", "number,headRefName"],
            cwd=ROOT,
            check=False,
        )
    except FileNotFoundError:
        return False
    if proc.returncode != 0:
        return False
    out = (proc.stdout or "").strip()
    return out not in {"", "[]"}


def acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            mtime = LOCK_FILE.stat().st_mtime
        except OSError:
            return False
        if time.time() - mtime > STALE_LOCK_SECONDS:
            try:
                LOCK_FILE.unlink()
            except OSError:
                return False
            return acquire_lock()
        return False
    try:
        os.write(fd, f"{os.getpid()} {iso_now()}\n".encode())
    finally:
        os.close(fd)
    return True


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _build_commit_message(task_id: str, pending: dict[str, object]) -> str:
    briefs = pending["briefs"]  # type: ignore[index]
    archives = pending["archives"]  # type: ignore[index]
    idx = 1 if pending["index_modified"] else 0
    total = len(briefs) + len(archives) + idx
    # Subject capped at 72 chars by check_commit_trailers.py.
    # task_id alone is ~40 chars; keep description very tight.
    lines = [
        f"{task_id}: backfill {total} files",
        "",
        "Supervisor periodic housekeeping run via",
        ".orchestrator/auto_commit_archive.py. Backfills files generated",
        "by the supervisor in the main worktree that no per-task worker PR",
        "includes (archive metadata + dispatch briefs).",
        "",
        "Backfilled:",
        f"- {len(archives)} ai-task-archive/tasks/*.json terminal task records",
        f"- {len(briefs)} .orchestrator/task-briefs/*.md generated briefs",
        f"- {idx} ai-task-archive/index.json (recent_terminal_ids + counts bump)",
        "",
        "Triggered when pending count >= 5 OR oldest pending file >= 4h old.",
        "Per-cycle cap enforced by skipping while another",
        "OPS-ARCHIVE-AUTO-COMMIT-* PR is open.",
        "",
        "Verification: jq . on the index.json delta; git status on main",
        "worktree after merge should drop the listed untracked entries.",
        "",
        "LLM-Agent: Orchestrator",
        f"Task-ID: {task_id}",
        "Reviewer: operator",
        "",
    ]
    return "\n".join(lines)


def run_backfill_pr(
    pending: dict[str, object],
    *,
    workspace_path: Path | None,
    task_id: str | None,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Open the per-task PR. Returns (success, message)."""
    if open_pr_exists():
        return True, "skip: existing OPS-ARCHIVE-AUTO-COMMIT-* PR is open"

    files: list[str] = []
    files.extend(str(b) for b in pending["briefs"])  # type: ignore[arg-type]
    files.extend(str(a) for a in pending["archives"])  # type: ignore[arg-type]
    if pending["index_modified"]:
        files.append("ai-task-archive/index.json")
    if not files:
        return True, "skip: no files to commit"

    if dry_run:
        return True, f"dry-run: would open {task_id or TASK_ID_PREFIX + '-DRY-RUN'} with {len(files)} files"
    if not task_id or not workspace_path:
        return False, "mutating archive runs require a supervisor-leased --workspace and --task-id"
    worktree_path = workspace_path.resolve()
    msg_path = Path(f"/tmp/{task_id}-msg.txt")
    index_file = Path(f"/tmp/git-index-task-{task_id}")

    cleanup_paths: list[Path] = []
    try:
        for f in files:
            src = ROOT / f
            dst = worktree_path / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        msg_path.write_text(_build_commit_message(task_id, pending), encoding="utf-8")
        cleanup_paths.append(msg_path)

        cmd = [
            "python3",
            "delivery_toolchain/git/worker_commit.py",
            "--task-id",
            task_id,
            "--message-file",
            str(msg_path),
            "--scope",
            *files,
            "--index-file",
            str(index_file),
        ]
        _run(cmd, cwd=worktree_path)
        cleanup_paths.append(index_file)

        # This is supervisor housekeeping, not a canonical board task.  It has
        # no owner/reviewer row to transition, so opt out explicitly; every
        # normal worker task must use task_finalize's atomic review submission.
        _run(["bash", "delivery_toolchain/git/task_finalize.sh", task_id, "--no-status-submit"], cwd=worktree_path)
        return True, f"opened PR for {task_id} ({len(files)} files)"
    except subprocess.CalledProcessError as exc:
        return False, f"command failed: {' '.join(exc.cmd)}\nstderr: {(exc.stderr or '')[:400]}"
    finally:
        # Always clean tmp message + index.
        for p in cleanup_paths:
            if p == worktree_path:
                continue
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        # The Worker Manager owns branch and worktree lifecycle.  On failure it
        # intentionally leaves the registered lease in place for inspection or
        # a normal retry; this archive script never deletes or recreates it.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Detect but do not open PR.")
    parser.add_argument("--quiet", action="store_true", help="Suppress 'nothing to do' output.")
    parser.add_argument("--workspace", help="Worker-Manager leased worktree (supervisor only).")
    parser.add_argument("--task-id", help="Task id bound to --workspace (supervisor only).")
    args = parser.parse_args(argv)

    if not acquire_lock():
        if not args.quiet:
            print(f"auto_commit_archive: another instance holds {LOCK_FILE.name}; skip", file=sys.stderr)
        return 1
    try:
        pending = detect_pending()
        trigger, reason = should_trigger(pending)
        if not trigger:
            if not args.quiet:
                print(f"auto_commit_archive: no trigger ({reason})")
            return 0
        print(f"auto_commit_archive: trigger ({reason})", flush=True)
        workspace = Path(args.workspace).resolve() if args.workspace else None
        ok, message = run_backfill_pr(
            pending,
            workspace_path=workspace,
            task_id=args.task_id,
            dry_run=args.dry_run,
        )
        print(f"auto_commit_archive: {message}", flush=True)
        return 0 if ok else 2
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
