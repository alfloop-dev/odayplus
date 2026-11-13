#!/usr/bin/env python3
"""Atomically roll the supervisor onto a clean, named, exact ``origin/dev`` worktree.

This is deliberately a deployment primitive rather than another best-effort
runbook.  It never fetches into or edits the runtime the service is currently
using: a fresh worktree is prepared, inspected, then selected by replacing the
stable runtime symlink.  If restarting the service fails, the symlink is put
back and the previous service is restarted.

On a watchdog-managed installation the watchdog's own policy decides whether a
restart may happen at all, so it is consulted before the healthy service is
touched, and its exit status alone never counts as recovery: only a live
supervisor that loaded the selected code and config does.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import common  # noqa: E402
import supervisor_watchdog  # noqa: E402
import worktree_cleanliness  # noqa: E402

BWRAP_PROFILE_ARTIFACT = ".orchestrator/profiles/bwrap"
INSTALLED_BWRAP_PROFILE = Path("/etc/apparmor.d/bwrap")
SUPERVISOR_STOP_GRACE_SECONDS = 20.0
# Proof needs one completed loop of the new process, which took 31s on the live
# fleet on 2026-11-06 and grows with the amount of work queued.
LOADED_RUNTIME_PROOF_TIMEOUT_SECONDS = 300.0
LOADED_RUNTIME_PROOF_POLL_SECONDS = 1.0


def run(args: list[str], *, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(f"{' '.join(args)}: {detail}")
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], cwd=repo)


def clean(repo: Path) -> bool:
    """No tracked change, judged only once no protected body can be read doing so.

    The shared domain proof runs first: status may hash a materialized tracked
    body to compare it, so a checkout holding one is never clean.
    """
    refusal = worktree_cleanliness.protected_body_checkout_refusal(repo)
    if refusal:
        print(f"protected_body_refused={repo}: {refusal}", file=sys.stderr)
        return False
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


def status_launcher(runtime_link: Path) -> bytes:
    runtime_writer = runtime_link / "scripts" / "ai_status.py"
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'status_root="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"\n'
        f'exec python3 {shlex.quote(str(runtime_writer))} "$@"\n'
    ).encode()


def recorded_protected_paths(status_root: Path) -> tuple[str, ...]:
    """Every protection the canonical board records (restrictive union)."""
    status_file = status_root / "ai-status.json"
    if not status_file.exists():
        return ()
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"canonical status is unreadable; protected-path preflight cannot run: {exc}") from exc
    paths: set[str] = set()
    for task in status.get("tasks", []) or []:
        try:
            paths.update(worktree_cleanliness.task_protected_paths(task))
        except worktree_cleanliness.ProtectedPathPolicyError as exc:
            task_id = task.get("id") if isinstance(task, dict) else None
            raise SystemExit(f"task {task_id} has unusable protected_paths: {exc}") from exc
    return tuple(sorted(paths))


def protected_preflight(source: Path, target_sha: str, protected: tuple[str, ...], installed_profile: Path) -> None:
    """Judge the target from Git metadata before a single file is checked out.

    The reviewed Bubblewrap profile in the target tree is compared with the
    operator-installed one; this never installs or edits host policy.
    """
    if not protected:
        return
    verdict = worktree_cleanliness.inspect_protected_git_metadata(source, target_sha, protected)
    if not verdict.ok:
        raise SystemExit(f"refusing rollout: protected_metadata_refused: {verdict.reason}: {verdict.detail}")
    artifact = subprocess.run(
        ["git", "cat-file", "blob", f"{target_sha}:{BWRAP_PROFILE_ARTIFACT}"],
        cwd=source,
        capture_output=True,
        check=False,
    )
    if artifact.returncode != 0:
        raise SystemExit(f"refusing rollout: target has no reviewed {BWRAP_PROFILE_ARTIFACT} for protected tasks")
    try:
        installed = installed_profile.read_bytes()
    except OSError as exc:
        raise SystemExit(
            f"refusing rollout: protected tasks need the reviewed sandbox profile installed at {installed_profile} ({exc})"
        ) from exc
    if installed != artifact.stdout:
        raise SystemExit(
            f"refusing rollout: installed {installed_profile} differs from reviewed {BWRAP_PROFILE_ARTIFACT}; "
            "the operator must install the reviewed profile first"
        )


def add_runtime_worktree(source: Path, target: Path, branch: str, target_sha: str) -> None:
    """Register the runtime without a checkout, prove HEAD, exclude the protected
    body domain, then materialize it and prove the domain stayed out."""
    git(source, "worktree", "add", "--no-checkout", "-b", branch, str(target), target_sha)
    if git(target, "symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{branch}" or git(target, "rev-parse", "HEAD") != target_sha:
        raise RuntimeError(f"runtime worktree HEAD is not {branch} at {target_sha}")
    excluded = worktree_cleanliness.install_protected_body_sparse(target)
    if excluded:
        raise RuntimeError(f"runtime worktree protected body exclusion failed: {excluded}")
    git(target, "read-tree", "--reset", "-u", target_sha)
    refusal = worktree_cleanliness.protected_body_checkout_refusal(target, require_sparse=True)
    if refusal:
        raise RuntimeError(f"runtime worktree materialized protected body domain: {refusal}")


def restore_link(link: Path, previous: Path | None) -> None:
    if previous is None:
        link.unlink(missing_ok=True)
    else:
        point_link(link, previous)


def watchdog_env(status_root: Path, config_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    env[common.STATUS_ROOT_ENV_VAR] = str(status_root)
    env[common.LEGACY_STATUS_ROOT_ENV_VAR] = str(status_root)
    env[common.CONFIG_PATH_ENV_VAR] = str(config_file)
    env[common.LEGACY_CONFIG_PATH_ENV_VAR] = str(config_file)
    return env


@dataclass(frozen=True)
class WatchdogContext:
    """The config and policy the watchdog process of this rollout will act on."""

    runtime_link: Path
    target: Path
    config_file: Path
    pid_file: Path
    env: dict[str, str]
    config: dict[str, Any]
    settings: dict[str, Any]


def load_watchdog_context(runtime_link: Path, target: Path, status_root: Path, config_file: Path, pid_file: Path) -> WatchdogContext:
    """Select config the way ``supervisor_watchdog.main`` will under the same env."""
    env = watchdog_env(status_root, config_file)
    saved = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        authoritative_root = common.authoritative_status_root()
        if authoritative_root is None:
            raise common.ConfigError(f"{status_root} has no .orchestrator/config.json")
        config = common.load_config_for_status_root(authoritative_root)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    settings = supervisor_watchdog.watchdog_settings(config)
    return WatchdogContext(runtime_link, target, config_file, pid_file, env, config, settings)


@contextlib.contextmanager
def watchdog_judging_from(root: Path) -> Iterator[None]:
    """Have the original helpers answer as a watchdog running from ``root`` would.

    They anchor a relative state file and measure the disk under their module's
    ``ROOT``, which here is this operator's checkout rather than any runtime.
    """
    operator_root = supervisor_watchdog.ROOT
    supervisor_watchdog.ROOT = root
    try:
        yield
    finally:
        supervisor_watchdog.ROOT = operator_root


def supervisor_identity_refusal(context: WatchdogContext, runtime: Path | None) -> str | None:
    """Accept only no service at all, or one verified supervisor of ``runtime``.

    The code root, not the status root, is the identity root: a runtime split
    from its canonical state is the normal deployment.  A pid file naming an
    unrelated process while nothing holds the lock is merely stale, and the stop
    path only ever signals verified supervisors.
    """
    pid = supervisor_watchdog.read_pid_file(context.pid_file)
    lock_held = supervisor_watchdog.supervisor_lock_held(context.config)
    running = common.iter_supervisor_pids(runtime) if runtime is not None else []
    if not lock_held and not running:
        return None
    if lock_held and running == [pid]:
        return None
    return f"unverified_supervisor_identity:pid={pid},lock_held={lock_held},running={running}"


def budget_refusal(context: WatchdogContext, now: datetime) -> str | None:
    """What the watchdog state under the current ``ROOT`` would suppress a restart with."""
    config, settings = context.config, context.settings
    # The original loader reads a non-object document as empty, which would pass
    # as an unspent budget.
    path = supervisor_watchdog.watchdog_state_path(config, settings)
    if not isinstance(common.load_json(path, default={}), dict):
        return f"unusable_state:{path}"
    watchdog_state = supervisor_watchdog.load_watchdog_state(config, settings)
    watchdog_state["restart_attempts"] = supervisor_watchdog.trim_restart_attempts(
        watchdog_state["restart_attempts"], now
    )
    return supervisor_watchdog.budget_suppression_reason(watchdog_state, now, settings)


def restart_refusal(context: WatchdogContext, previous_runtime: Path | None) -> str | None:
    """Why the watchdog could not replace the supervisor now, or None.

    ``run_watchdog`` cannot answer this: it reports a healthy supervisor before
    it looks at the budget, and it persists what it decides.  Its policy helpers
    are read here on private copies, so no counter or circuit changes.
    """
    config, settings, target = context.config, context.settings, context.target
    if not settings.get("enabled", True):
        return "watchdog_disabled"
    try:
        runtime_state, state_error = supervisor_watchdog.load_runtime_state_file(config)
        # The target watchdog measures its own root's disk.  Until the target is
        # prepared, its filesystem is the runtime parent's.
        with watchdog_judging_from(target if target.exists() else target.parent):
            resource = supervisor_watchdog.resource_snapshot(config, runtime_state, settings)
        pressure = supervisor_watchdog.resource_pressure_reasons(resource, settings, state_error)
        if pressure:
            return "resource_pressure:" + ",".join(pressure)
        # The same non-object reading applies to the runtime state.
        state_path = common.config_path(config, "state_file")
        if not isinstance(common.load_json(state_path, default={}), dict):
            return f"unusable_state:{state_path}"
        now = datetime.now(UTC).replace(microsecond=0)
        # A relative state file belongs to whichever runtime runs the watchdog.
        # The previous one holds what this service has spent, and the target one
        # is what the replacing watchdog will read; either may suppress.
        for root in (target,) if previous_runtime is None else (previous_runtime, target):
            with watchdog_judging_from(root):
                budget = budget_refusal(context, now)
            if budget:
                return budget
        return supervisor_identity_refusal(context, previous_runtime)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return f"unusable_state:{type(exc).__name__}: {exc}"


def require_restart_admissible(context: WatchdogContext, previous_runtime: Path | None) -> None:
    refusal = restart_refusal(context, previous_runtime)
    if refusal:
        raise SystemExit(
            f"refusing rollout: restart_not_admissible: {refusal}; "
            "supervisor, selected runtime and status launcher left unchanged"
        )


def config_digest(config_file: Path) -> str:
    """The digest ``supervisor.runtime_provenance`` stamps for this document."""
    return hashlib.sha256(config_file.read_bytes()).hexdigest()[:16]


def watchdog_decision_failure(result: Any) -> str | None:
    """Only a started replacement, or one the watchdog already finds healthy, can be proven."""
    if not isinstance(result, dict) or "new_pid" not in result:
        return f"malformed watchdog result: {result!r}"
    decision, reason, new_pid = result.get("decision"), result.get("reason"), result.get("new_pid")
    if decision == "restart_supervisor" and type(new_pid) is int and new_pid > 0:
        return None
    if decision == "observe_only" and reason == "supervisor_healthy" and new_pid is None:
        return None
    return f"watchdog did not recover the supervisor: decision={decision} reason={reason} new_pid={new_pid}"


def loaded_runtime_failure(context: WatchdogContext, runtime: Path, code_sha: str, digest: str, pid: int) -> str | None:
    """Why ``pid`` is not yet proven to be the one supervisor serving ``runtime``, or None."""
    running = common.iter_supervisor_pids(runtime)
    lock_held = supervisor_watchdog.supervisor_lock_held(context.config)
    if running != [pid] or not lock_held:
        return f"expected only supervisor {pid} of {runtime}: running={running} lock_held={lock_held}"
    runtime_state, state_error = supervisor_watchdog.load_runtime_state_file(context.config)
    if state_error:
        return f"runtime state unreadable: {state_error}"
    supervisor = runtime_state.get("supervisor")
    if not isinstance(supervisor, dict) or supervisor.get("pid") != pid:
        return f"supervisor {pid} has not recorded its heartbeat"
    if supervisor.get("loaded_code_sha") != code_sha:
        return f"supervisor {pid} loaded code {supervisor.get('loaded_code_sha')}, expected {code_sha}"
    if supervisor.get("loaded_config_digest") != digest:
        return f"supervisor {pid} loaded config {supervisor.get('loaded_config_digest')}, expected {digest}"
    now = datetime.now(UTC).replace(microsecond=0)
    health = supervisor_watchdog.evaluate_supervisor_health(runtime_state, pid, True, now, context.settings)
    if not health.get("healthy"):
        return f"supervisor {pid} is unhealthy: {health.get('reason')}"
    if supervisor.get("lifecycle") != "running" or not supervisor.get("last_successful_loop_at"):
        return f"supervisor {pid} has not completed a loop: lifecycle={supervisor.get('lifecycle')}"
    return None


def wait_for_loaded_runtime(context: WatchdogContext, runtime: Path, code_sha: str, digest: str, pid: int | None) -> str | None:
    if pid is None:
        # The watchdog started nothing, so whoever holds the lock must already be here.
        running = common.iter_supervisor_pids(runtime)
        if len(running) != 1:
            return f"no single supervisor of {runtime} behind the watchdog's healthy report: running={running}"
        pid = running[0]
    deadline = time.monotonic() + LOADED_RUNTIME_PROOF_TIMEOUT_SECONDS
    while True:
        failure = loaded_runtime_failure(context, runtime, code_sha, digest, pid)
        if failure is None:
            print(f"supervisor_proven pid={pid} runtime={runtime} code={code_sha} config={digest}")
            return None
        if not common.pid_is_alive(pid):
            return f"supervisor {pid} exited: {failure}"
        if time.monotonic() >= deadline:
            return f"timed out: {failure}"
        time.sleep(LOADED_RUNTIME_PROOF_POLL_SECONDS)


def run_watchdog_and_prove(context: WatchdogContext, runtime: Path, code_sha: str) -> str | None:
    """Run the one process manager from the stable link; None once ``runtime`` is proven."""
    result = subprocess.run(
        [
            "bash",
            str(context.runtime_link / "scripts" / "run-supervisor-watchdog.sh"),
            "--config",
            str(context.config_file),
            "--restart",
            "--json",
        ],
        cwd=context.runtime_link,
        env=context.env,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    digest = config_digest(context.config_file)
    print(f"watchdog_result={result.stdout.strip()}")
    if result.returncode:
        return f"watchdog exited {result.returncode}"
    try:
        decision = json.loads(result.stdout)
    except ValueError:
        return f"malformed watchdog result: {result.stdout.strip()!r}"
    failure = watchdog_decision_failure(decision)
    if failure:
        return failure
    return wait_for_loaded_runtime(context, runtime, code_sha, digest, decision["new_pid"])


def stop_supervisors(runtime: Path) -> str | None:
    """Stop through the one identity-checked stop path; None once none of ``runtime``'s remain."""
    stopped = common.terminate_supervisor_processes(runtime, term_grace_seconds=SUPERVISOR_STOP_GRACE_SECONDS)
    remaining = sorted(pid for pid, was_stopped in stopped.items() if not was_stopped)
    return f"supervisors of {runtime} did not stop: {remaining}" if remaining else None


def restart_with_watchdog(context: WatchdogContext, previous_runtime: Path | None, target: Path, target_sha: str) -> str | None:
    """Replace only the supervisor; worker processes remain outside its lifetime.

    The watchdog is the sole process manager on cron-based installations.  It
    starts from the stable runtime link and reads/writes state in the canonical
    status root, so a rollout does not introduce a systemd service alongside it.
    Returns why the selected runtime is not proven to be serving, or None; by now
    the link and launcher are switched, so an exception is one such reason too.
    """
    try:
        if previous_runtime is not None:
            failure = stop_supervisors(previous_runtime)
            if failure:
                return failure
        return run_watchdog_and_prove(context, target, target_sha)
    except Exception as exc:
        return f"restart raised {type(exc).__name__}: {exc}"


def recover_previous_runtime(context: WatchdogContext, previous_runtime: Path | None, target: Path) -> str:
    """Report what actually serves after the link and launcher were restored."""
    try:
        if previous_runtime != target:
            failure = stop_supervisors(target)
            if failure:
                return f"previous supervisor NOT verified: {failure}"
        if previous_runtime is None:
            return "no previous runtime to restart"
        previous_sha = git(previous_runtime, "rev-parse", "HEAD")
        failure = run_watchdog_and_prove(context, previous_runtime, previous_sha)
    except Exception as exc:
        return f"previous supervisor NOT verified: {type(exc).__name__}: {exc}"
    return f"previous supervisor NOT verified: {failure}" if failure else "previous supervisor verified"


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
    parser.add_argument(
        "--installed-bwrap-profile",
        type=Path,
        default=INSTALLED_BWRAP_PROFILE,
        help="operator-installed AppArmor profile compared with the reviewed artifact (read only)",
    )
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
    print(f"target={target} sha={target_sha} branch={branch}")
    print(f"previous={previous if previous else 'none'}")
    print(f"status_launcher={launcher} writer={link / 'scripts' / 'ai_status.py'}")
    protected = recorded_protected_paths(status_root)
    protected_preflight(source, target_sha, protected, args.installed_bwrap_profile)
    if protected:
        print(f"protected_paths={','.join(protected)} sandbox_profile=matches")
    watchdog_context: WatchdogContext | None = None
    if args.watchdog_pid_file:
        try:
            watchdog_context = load_watchdog_context(
                link, target, status_root, live_config, args.watchdog_pid_file.expanduser().resolve()
            )
        except (common.ConfigError, OSError, ValueError) as exc:
            raise SystemExit(f"refusing rollout: restart_not_admissible: unusable_config: {exc}") from exc
        require_restart_admissible(watchdog_context, previous)
        print("restart_admission=admissible")
    if args.dry_run:
        return 0

    if target.exists():
        # A separate clone can hold the same HEAD on the same named branch; only
        # the Git common directory proves the target belongs to this repository.
        # Judged before anything reads the target's status or switches to it.
        if not worktree_cleanliness.shares_git_common_dir(source, target):
            raise SystemExit(f"existing target is not a worktree of the source repository: {target}")
        if not clean(target) or git(target, "rev-parse", "HEAD") != target_sha:
            raise SystemExit(f"existing target is not the expected clean runtime: {target}")
    else:
        try:
            add_runtime_worktree(source, target, branch, target_sha)
        except RuntimeError as exc:
            raise SystemExit(f"could not prepare clean runtime worktree: {exc}") from exc

    if not clean(target) or git(target, "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise SystemExit("fresh runtime did not pass clean named-branch integrity checks")
    if git(target, "rev-list", "--count", f"HEAD..{args.tracking_ref}") != "0":
        raise SystemExit("fresh runtime is behind tracking ref after preparation")

    if watchdog_context is not None:
        # Preparing the worktree takes a while, and the cron watchdog keeps acting.
        require_restart_admissible(watchdog_context, previous)

    launcher_before = snapshot_file(launcher)
    try:
        # Install the stable entry point before changing the runtime symlink so
        # every status writer immediately follows the same code plane.
        replace_file(launcher, status_launcher(link), 0o755)
        point_link(link, target)
    except Exception:
        restore_file(launcher, launcher_before)
        restore_link(link, previous)
        raise

    if watchdog_context is not None:
        failure = restart_with_watchdog(watchdog_context, previous, target, target_sha)
    else:
        restarted = subprocess.run(
            ["systemctl", "--user", "restart", args.service], text=True, check=False
        ).returncode == 0
        failure = None if restarted else f"systemctl restart {args.service} failed"
    if failure is None:
        print(f"rollout complete: {link} -> {target}")
        return 0

    restore_link(link, previous)
    restore_file(launcher, launcher_before)
    restored = f"supervisor restart failed: {failure}; restored previous runtime and status launcher"
    if watchdog_context is not None:
        raise SystemExit(f"{restored}; {recover_previous_runtime(watchdog_context, previous, target)}")
    subprocess.run(["systemctl", "--user", "restart", args.service], text=True, check=False)
    raise SystemExit(restored)


if __name__ == "__main__":
    raise SystemExit(main())
