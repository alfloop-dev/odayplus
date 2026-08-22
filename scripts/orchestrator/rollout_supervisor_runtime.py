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
import shlex
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    content: bytes = b""
    mode: int = 0o755


def snapshot_file(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(exists=False)
    return FileSnapshot(
        exists=True,
        content=path.read_bytes(),
        mode=stat.S_IMODE(path.stat().st_mode),
    )


def replace_file(path: Path, content: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.next-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.write_bytes(content)
    temporary.chmod(mode)
    os.replace(temporary, path)


def restore_file(path: Path, snapshot: FileSnapshot) -> None:
    if snapshot.exists:
        replace_file(path, snapshot.content, snapshot.mode)
    else:
        path.unlink(missing_ok=True)


def status_launcher(runtime_link: Path, config_path: Path) -> bytes:
    runtime_writer = runtime_link / "scripts" / "ai_status.py"
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'status_root="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"\n'
        f'export ORCH_CONFIG_PATH={shlex.quote(str(config_path))}\n'
        f'export PANTHEON_CONFIG_PATH={shlex.quote(str(config_path))}\n'
        f'exec python3 {shlex.quote(str(runtime_writer))} "$@"\n'
    ).encode()


def restore_link(link: Path, previous: Path | None) -> None:
    if previous is None:
        link.unlink(missing_ok=True)
    else:
        point_link(link, previous)


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def restart_with_watchdog(
    previous_pid: int | None,
    runtime_link: Path,
    status_root: Path,
    config_path: Path,
) -> bool:
    """Replace only the supervisor; worker processes remain outside its lifetime.

    The watchdog is the sole process manager on cron-based installations.  It
    starts from the stable runtime link and reads/writes state in the canonical
    status root, so a rollout does not introduce a systemd service alongside it.
    """
    if previous_pid and pid_alive(previous_pid):
        os.kill(previous_pid, signal.SIGTERM)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and pid_alive(previous_pid):
            time.sleep(0.1)
        if pid_alive(previous_pid):
            return False
    env = os.environ.copy()
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["ORCH_CONFIG_PATH"] = str(config_path)
    env["PANTHEON_CONFIG_PATH"] = str(config_path)
    result = subprocess.run(
        [
            "bash",
            str(runtime_link / "scripts" / "run-supervisor-watchdog.sh"),
            "--config",
            str(config_path),
            "--restart",
        ],
        cwd=runtime_link,
        env=env,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime-link", type=Path, required=True)
    parser.add_argument("--runtime-parent", type=Path, required=True)
    parser.add_argument(
        "--status-root",
        type=Path,
        required=True,
        help="canonical state root whose scripts/ai-status.sh must use this runtime",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        help="authoritative live config (default: STATUS_ROOT/.orchestrator/config.json)",
    )
    restart_group = parser.add_mutually_exclusive_group(required=True)
    restart_group.add_argument("--service", help="systemd user service name")
    restart_group.add_argument(
        "--watchdog-pid-file",
        type=Path,
        help="cron/watchdog installation: canonical supervisor.pid to replace",
    )
    parser.add_argument("--tracking-ref", default="origin/dev")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source = args.source_root.resolve()
    # Do not resolve the link itself: resolving follows the current runtime and
    # would replace its directory path rather than the stable symlink.
    link = args.runtime_link.expanduser()
    parent = args.runtime_parent.resolve()
    status_root = args.status_root.expanduser().resolve()
    live_config = (
        args.config_path.expanduser().resolve()
        if args.config_path
        else status_root / ".orchestrator" / "config.json"
    )
    launcher = status_root / "scripts" / "ai-status.sh"
    if not source.is_dir() or not (source / ".git").exists():
        raise SystemExit(f"source root is not a git checkout: {source}")
    if not parent.is_dir():
        raise SystemExit(f"runtime parent does not exist: {parent}")
    if not launcher.parent.is_dir():
        raise SystemExit(f"status root scripts directory does not exist: {launcher.parent}")
    if not live_config.is_file():
        raise SystemExit(f"live config does not exist: {live_config}")
    if link.exists() and not link.is_symlink():
        raise SystemExit(f"runtime link exists but is not a symlink: {link}")
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
    previous_pid = read_pid(args.watchdog_pid_file.expanduser().resolve()) if args.watchdog_pid_file else None
    print(f"target={target} sha={target_sha} branch={branch}")
    print(f"previous={previous if previous else 'none'}")
    print(f"status_launcher={launcher} writer={link / 'scripts' / 'ai_status.py'}")
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

    launcher_before = snapshot_file(launcher)
    try:
        # Install the stable entry point before changing the runtime symlink so
        # every status writer immediately follows the same code plane.
        replace_file(launcher, status_launcher(link, live_config), 0o755)
        point_link(link, target)
    except Exception:
        restore_file(launcher, launcher_before)
        restore_link(link, previous)
        raise

    if args.service:
        restarted = subprocess.run(
            ["systemctl", "--user", "restart", args.service], text=True, check=False
        ).returncode == 0
    else:
        restarted = restart_with_watchdog(previous_pid, link, status_root, live_config)
    if restarted:
        print(f"rollout complete: {link} -> {target}")
        return 0

    restore_link(link, previous)
    restore_file(launcher, launcher_before)
    if args.service:
        subprocess.run(["systemctl", "--user", "restart", args.service], text=True, check=False)
    else:
        restart_with_watchdog(None, link, status_root, live_config)
    raise SystemExit("supervisor restart failed; restored previous runtime and status launcher")


if __name__ == "__main__":
    raise SystemExit(main())
