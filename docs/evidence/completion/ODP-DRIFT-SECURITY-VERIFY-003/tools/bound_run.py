#!/usr/bin/env python3
"""Run a verification command and bind its receipts to an exact commit SHA.

Reviewer finding (reopen #3, item 2): the round-3 baseline-equivalence and
native-core receipts were plain pytest transcripts. A transcript proves a run
happened; it does not prove *which* tree it ran against, so it cannot be
audited against the head being reviewed. The historical runs recorded no SHA,
and a SHA cannot honestly be written back into them after the fact, so the runs
are repeated here through this wrapper instead.

What this records, per run:

* the exact ``git rev-parse HEAD`` and its subject, read before the command
  starts and again after it finishes, so a mid-run branch move is visible
  rather than silently averaged away;
* ``git status --porcelain`` before and after, so "the tree was clean at this
  SHA" is a measurement rather than an assumption -- an uncommitted edit would
  make the run describe something other than the recorded commit;
* the SHA-256 of every declared input (files and whole trees), so the run is
  bound to the fixture and source bytes it consumed, not only to a commit;
* the command, cwd, wall clock and exit code, taken from the child process;
* the SHA-256 of every receipt the run produced, computed after the files are
  closed.

The exit code written to the receipt is the child's own. This wrapper never
substitutes a verdict: it fails loudly if the child cannot be started, and
otherwise reports exactly what the child returned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


#: Receipts this task writes land here. This directory is deliberately narrow:
#: it covers outputs only, not the sibling ``tools/`` directory, because a
#: modified tool changes what a run does and must never be waved through as
#: "just this session's own files".
TASK_RECEIPTS_DIR = "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/receipts/"


def _repo_state() -> dict[str, object]:
    """Snapshot HEAD and classify what, if anything, the tree carries beyond it.

    A bare "is the worktree clean" boolean is too blunt to be useful here:
    every run in this session necessarily leaves receipts behind, so the next
    run would report "dirty" and the flag would stop meaning anything.

    What actually has to hold is narrower and stronger -- *the code, tools and
    fixtures under test are exactly this commit*. That is false if any tracked
    file is modified, or if an untracked file sits anywhere the run could
    import or read as input. It stays true only when every deviation is a
    receipt file, which nothing under test reads.
    """
    porcelain = _git("status", "--porcelain")
    tracked_modifications: list[str] = []
    untracked: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        status, path = line[:2], line[3:].strip()
        (untracked if status == "??" else tracked_modifications).append(path)
    deviations = [
        path
        for path in tracked_modifications + untracked
        if not path.startswith(TASK_RECEIPTS_DIR)
    ]
    return {
        "head_sha": _git("rev-parse", "HEAD").strip(),
        "head_subject": _git("log", "-1", "--format=%s").strip(),
        "porcelain": porcelain,
        "worktree_clean": porcelain.strip() == "",
        "tracked_files_modified": tracked_modifications,
        "untracked_files": untracked,
        "deviations_outside_receipts": deviations,
        "code_tree_matches_commit": not deviations,
    }


def _describe_file(path: Path) -> dict[str, object]:
    try:
        rel = str(path.relative_to(ROOT)) if path.is_absolute() else str(path)
    except ValueError:
        rel = str(path)
    if not path.is_file():
        return {"path": rel, "exists": False}
    return {
        "path": rel,
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _describe_tree(root: Path) -> dict[str, object]:
    """Hash a whole directory as one value, ignoring bytecode caches.

    The per-file digests are folded into a single ordered digest so the tree
    can be compared with one number, while the file list stays available for
    locating which member changed.
    """
    members: list[dict[str, object]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        members.append(_describe_file(path))
    folded = hashlib.sha256()
    for member in members:
        folded.update(f"{member['path']}\x00{member['sha256']}\x00".encode())
    return {
        "path": str(root.relative_to(ROOT)),
        "file_count": len(members),
        "tree_sha256": folded.hexdigest(),
        "files": members,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="receipt basename")
    parser.add_argument("--receipt-dir", required=True, help="directory for receipts")
    parser.add_argument(
        "--purpose", default="", help="one line describing what the run establishes"
    )
    parser.add_argument(
        "--input", action="append", default=[], help="input file to hash (repeatable)"
    )
    parser.add_argument(
        "--input-tree", action="append", default=[], help="input directory to hash"
    )
    parser.add_argument(
        "--extra-output",
        action="append",
        default=[],
        help="receipt the command writes itself (e.g. a JUnit XML), hashed afterwards",
    )
    parser.add_argument(
        "--collect-output",
        action="append",
        default=[],
        help=(
            "file the command wrote outside the repo; moved into --receipt-dir after "
            "the post-run tree snapshot, so writing it cannot dirty the tree the run "
            "is bound to. Hashed at its final committed location."
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command")
    args = parser.parse_args()

    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        print("bound_run: no command given", file=sys.stderr)
        return 2

    receipt_dir = Path(args.receipt_dir)
    if not receipt_dir.is_absolute():
        receipt_dir = ROOT / receipt_dir
    receipt_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = receipt_dir / f"{args.label}_stdout.txt"
    stderr_path = receipt_dir / f"{args.label}_stderr.txt"
    exit_path = receipt_dir / f"{args.label}_exit_code.txt"
    binding_path = receipt_dir / f"{args.label}_run_binding.json"

    inputs = [_describe_file((ROOT / p).resolve()) for p in args.input]
    trees = [_describe_tree((ROOT / p).resolve()) for p in args.input_tree]

    before = _repo_state()
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    res = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, check=False)
    duration = time.monotonic() - started_monotonic
    finished_at = _utc_now()
    after = _repo_state()

    stdout_path.write_text(res.stdout, encoding="utf-8")
    stderr_path.write_text(res.stderr, encoding="utf-8")
    exit_path.write_text(f"{res.returncode}\n", encoding="utf-8")

    collected: list[dict[str, str]] = []
    for source in args.collect_output:
        src = Path(source)
        if not src.is_absolute():
            src = (ROOT / src).resolve()
        dest = receipt_dir / src.name
        if not src.is_file():
            raise SystemExit(f"bound_run: --collect-output {src} was never written")
        dest.write_bytes(src.read_bytes())
        src.unlink()
        collected.append({"staged_at": str(src), "committed_at": str(dest.relative_to(ROOT))})

    outputs = [_describe_file(p) for p in (stdout_path, stderr_path, exit_path)]
    for entry in collected:
        outputs.append(_describe_file(ROOT / entry["committed_at"]))
    for extra in args.extra_output:
        path = Path(extra)
        outputs.append(_describe_file((ROOT / path).resolve() if not path.is_absolute() else path))

    binding = {
        "label": args.label,
        "purpose": args.purpose,
        "recorded_by": "docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/tools/bound_run.py",
        "command": command,
        "cwd": str(ROOT),
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "duration_seconds": round(duration, 3),
        "exit_code": res.returncode,
        "exit_code_source": "child process returncode",
        "repo_state_before": before,
        "repo_state_after": after,
        "run_sha": before["head_sha"],
        "run_sha_stable": before["head_sha"] == after["head_sha"],
        "worktree_clean_throughout": bool(before["worktree_clean"] and after["worktree_clean"]),
        "code_tree_matches_commit_throughout": bool(
            before["code_tree_matches_commit"] and after["code_tree_matches_commit"]
        ),
        "inputs": inputs,
        "input_trees": trees,
        "collected_outputs": collected,
        "outputs": outputs,
        "stdout_sha256": _sha256_bytes(res.stdout.encode("utf-8")),
        "stderr_sha256": _sha256_bytes(res.stderr.encode("utf-8")),
    }
    binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"bound_run: {args.label} exit={res.returncode} run_sha={before['head_sha']} "
        f"code_tree_matches_commit={binding['code_tree_matches_commit_throughout']} "
        f"worktree_pristine={binding['worktree_clean_throughout']}",
        file=sys.stderr,
    )
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
