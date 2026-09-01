#!/usr/bin/env python3
"""Run a task's declared verification commands and gate finalization on them.

Two subcommands over one receipt store:

  run    execute the commands declared in the task's ``verification`` field,
         recording a receipt per command that binds the head SHA, the exact
         command, the real exit code, the duration and the test selection.

  check  refuse to publish when a declared command has no receipt proving it
         passed at the current head. Called from task_finalize.sh alongside the
         boundary and lint preflights.

The policy itself lives in ``.orchestrator/verification_evidence.py``; this is
the delivery-side entry point onto it. Nothing here schedules work or keeps its
own state: receipts go into the supervisor's existing ``.orchestrator/evidence``
directory, and the task's declaration is read from the status file the board
already owns.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PRODUCED_BY = "delivery_toolchain/git/task_verification.py"


def _load_policy(repo: Path):
    """Import the shared policy, preferring the target repo's copy.

    task_finalize.sh already resolves worktree_cleanliness.py out of the
    toolchain checkout when the target repo has none; the same fallback keeps
    this usable against a repo that does not carry its own .orchestrator.
    """
    toolchain_root = Path(__file__).resolve().parents[2]
    for base in (repo, toolchain_root):
        candidate = base / ".orchestrator"
        if (candidate / "verification_evidence.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            break
    import verification_evidence

    return verification_evidence


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise ValueError(detail)
    return result.stdout.strip()


def resolve_repo(value: str | None) -> Path:
    if value:
        return Path(value).resolve()
    return Path(_git(Path.cwd(), "rev-parse", "--show-toplevel")).resolve()


def evidence_dir(repo: Path) -> Path:
    return repo / ".orchestrator" / "evidence"


def resolve_status_file(repo: Path, value: str | None) -> Path | None:
    """Prefer an explicit path, then the canonical status root, then the repo."""
    candidates: list[Path] = []
    if value:
        candidates.append(Path(value))
    else:
        status_root = os.environ.get("PANTHEON_STATUS_ROOT", "").strip()
        if status_root:
            candidates.append(Path(status_root) / "ai-status.json")
        candidates.append(repo / "ai-status.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_task(status_file: Path, task_id: str) -> dict[str, Any] | None:
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    for task in payload.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("id") or "") == task_id:
            return task
    return None


def declared_commands(task: dict[str, Any] | None) -> list[str]:
    return [str(item).strip() for item in ((task or {}).get("verification") or []) if str(item).strip()]


def cmd_run(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    ve = _load_policy(repo)

    status_file = resolve_status_file(repo, args.status_file)
    if status_file is None:
        print("task_verification: no status file found; pass --status-file.", file=sys.stderr)
        return 1

    task = load_task(status_file, args.task_id)
    commands = declared_commands(task)
    if not commands:
        print(f"task_verification: {args.task_id} declares no verification commands; nothing to run.")
        return 0

    head_sha = _git(repo, "rev-parse", "HEAD")
    store = evidence_dir(repo)
    failed = False

    for command in commands:
        receipts = ve.load_receipts(store, task_id=args.task_id)
        decision, receipt = ve.verify_and_build_receipt(
            command,
            task_id=args.task_id,
            head_sha=head_sha,
            receipts=receipts,
            retry_reason=args.retry_reason,
            cwd=str(repo),
            timeout=args.timeout,
            agent=os.environ.get("AI_NAME") or None,
            run_id=os.environ.get("ORCH_RUN_ID") or None,
            produced_by=PRODUCED_BY,
        )
        if receipt is None:
            print(f"task_verification: refused ({decision.kind}): {decision.reason}", file=sys.stderr)
            print(f"  command: {command}", file=sys.stderr)
            failed = True
            continue

        if not receipt["command_audit"]["ok"]:
            print(
                "task_verification: command was not run -- its exit code would not be its own "
                f"({', '.join(receipt['command_audit']['violations'])}): {command}",
                file=sys.stderr,
            )
            for detail in receipt["command_audit"]["details"]:
                print(f"  {detail}", file=sys.stderr)
            failed = True
            continue

        path = ve.write_receipt(store, receipt)
        print(
            f"task_verification: {receipt['outcome']} "
            f"(exit {receipt['exit_code']}, {receipt['duration_seconds']}s) {command}"
        )
        print(f"  receipt: {path}")
        if not receipt["passed"]:
            failed = True
            if receipt["outcome"] == ve.OUTCOME_INTERRUPTED:
                plan = ve.plan_rerun(receipt, requested_selection=receipt["selection"])
                print(f"  rerun: {plan.reason}", file=sys.stderr)

    return 1 if failed else 0


def cmd_check(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    ve = _load_policy(repo)

    status_file = resolve_status_file(repo, args.status_file)
    if status_file is None:
        print(
            "task_verification: refusing to publish -- no status file found, so whether this "
            "task declares verification commands cannot be determined. Pass --status-file.",
            file=sys.stderr,
        )
        return 1

    task = load_task(status_file, args.task_id)
    if task is None:
        # Housekeeping branches have no board task, so no declaration and no
        # obligation. Say so rather than passing silently.
        print(f"task_verification: {args.task_id} is not on the board; no declared verification to prove.")
        return 0

    # A task marked `verification_required` owes a declaration, so "declares
    # nothing" is not an exemption for it. The gate decides that, so the rule
    # lives in one place with the proof rule it belongs to.
    requirement = ve.declaration_requirement(task)
    commands = declared_commands(task)
    if not commands and not requirement.required:
        print(f"task_verification: {args.task_id} declares no verification commands; nothing to prove.")
        return 0

    head_sha = _git(repo, "rev-parse", "HEAD")
    receipts = ve.load_receipts(evidence_dir(repo), task_id=args.task_id)
    result = ve.evaluate_finalize_gate(
        commands=commands,
        head_sha=head_sha,
        receipts=receipts,
        task_id=args.task_id,
        requirement=requirement,
    )

    for command in result.satisfied:
        print(f"task_verification: proven at {head_sha[:12]}: {command}")

    if result.ok:
        return 0

    print("task_verification: refusing to publish -- declared verification is not proven:", file=sys.stderr)
    for problem in result.problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        f"task_verification: produce receipts with\n"
        f"  python3 {PRODUCED_BY} run --task-id {args.task_id}",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("run", cmd_run), ("check", cmd_check)):
        child = sub.add_parser(name)
        child.add_argument("--task-id", required=True)
        child.add_argument("--repo")
        child.add_argument("--status-file")
        child.set_defaults(handler=handler)
        if name == "run":
            child.add_argument(
                "--retry-reason",
                help="Required to re-measure a head SHA and selection that already has a settled receipt.",
            )
            child.add_argument("--timeout", type=float, default=3600.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ValueError as exc:
        print(f"task_verification: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
