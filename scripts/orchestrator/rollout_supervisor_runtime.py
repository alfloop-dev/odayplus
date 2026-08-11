#!/usr/bin/env python3
"""Atomically roll the supervisor onto a clean, named, exact ``origin/dev`` worktree.

This is deliberately a deployment primitive rather than another best-effort
runbook.  It never fetches into or edits the runtime the service is currently
using: a fresh worktree is prepared, inspected, then selected by replacing the
stable runtime symlink.  If restarting the service fails, the symlink is put
back and the previous service is restarted.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(args: list[str], *, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(f"{' '.join(args)}: {detail}")
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], cwd=repo)


def clean(repo: Path) -> bool:
    return not git(repo, "status", "--porcelain", "--untracked-files=no")


def point_link(link: Path, target: Path) -> None:
    temporary = link.with_name(f".{link.name}.next-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime-link", type=Path, required=True)
    parser.add_argument("--runtime-parent", type=Path, required=True)
    parser.add_argument("--service", required=True, help="systemd user service name")
    parser.add_argument("--tracking-ref", default="origin/dev")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source = args.source_root.resolve()
    # Do not resolve the link itself: resolving follows the current runtime and
    # would replace its directory path rather than the stable symlink.
    link = args.runtime_link.expanduser()
    parent = args.runtime_parent.resolve()
    if not source.is_dir() or not (source / ".git").exists():
        raise SystemExit(f"source root is not a git checkout: {source}")
    if not parent.is_dir():
        raise SystemExit(f"runtime parent does not exist: {parent}")
    if not clean(source):
        raise SystemExit("refusing rollout from a dirty source checkout")

    try:
        git(source, "fetch", "--quiet", "origin", "dev")
        target_sha = git(source, "rev-parse", args.tracking_ref)
    except RuntimeError as exc:
        raise SystemExit(f"cannot resolve {args.tracking_ref}: {exc}") from exc

    if git(source, "rev-parse", "HEAD") != target_sha:
        raise SystemExit(
            f"source HEAD is not {args.tracking_ref} ({target_sha[:8]}); merge/push first, then roll out"
        )
    short = target_sha[:12]
    target = parent / f"oday-plus-supervisor-runtime-{short}"
    branch = f"runtime-live-{short}"
    previous = link.resolve() if link.is_symlink() else None
    print(f"target={target} sha={target_sha} branch={branch}")
    print(f"previous={previous if previous else 'none'}")
    if args.dry_run:
        return 0

    if target.exists():
        if not clean(target) or git(target, "rev-parse", "HEAD") != target_sha:
            raise SystemExit(f"existing target is not the expected clean runtime: {target}")
    else:
        try:
            git(source, "worktree", "add", "-b", branch, str(target), target_sha)
        except RuntimeError as exc:
            raise SystemExit(f"could not prepare clean runtime worktree: {exc}") from exc

    if not clean(target) or git(target, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise SystemExit("fresh runtime did not pass clean named-branch integrity checks")
    if git(target, "rev-list", "--count", f"HEAD..{args.tracking_ref}") != "0":
        raise SystemExit("fresh runtime is behind tracking ref after preparation")

    point_link(link, target)
    restart = subprocess.run(["systemctl", "--user", "restart", args.service], text=True, check=False)
    if restart.returncode == 0:
        print(f"rollout complete: {link} -> {target}")
        return 0

    if previous:
        point_link(link, previous)
        subprocess.run(["systemctl", "--user", "restart", args.service], text=True, check=False)
    raise SystemExit("supervisor restart failed; restored previous runtime symlink")


if __name__ == "__main__":
    raise SystemExit(main())
