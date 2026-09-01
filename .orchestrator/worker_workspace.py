from __future__ import annotations

"""Workspace lifecycle helpers extracted from legacy supervisor."""
# ruff: noqa: F401,F821,I001

import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

from common import normalize_agent_id, utc_now
from dispatch_policy import REASON_OWNED_IN_PROGRESS, REASON_OWNED_READY, REASON_REVIEW_READY, worker_logical_dispatch_agent_id
import verification_evidence
from runtime_state import ACTIVE_WORKER_STATUSES

# Compatibility aliases remain exports while Supervisor callers migrate to the
# shared policy module. The runtime's scope injector resolves these names.
from worktree_cleanliness import (
    WorktreeInspection,
    blocking_dirt_entries as _blocking_dirt_entries,
    describe_dirt_entries as _describe_dirt_entries,
    inspect_porcelain as _inspect_porcelain,
    inspect_worktree,
    is_reusable_dirt_entry as _is_reusable_dirt_entry,
    normalize_materialized_paths as _normalize_materialized_paths,
    parse_porcelain_entries as _parse_porcelain_entries,
)


def _supervisor_module():
    import supervisor
    return supervisor


def _sync_supervisor_scope() -> None:
    sv = _supervisor_module()
    # Keep module-local definitions authoritative and avoid replacing them on sync.
    excluded = {
        "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__",
        "Any", "_supervisor_module", "_sync_supervisor_scope", "_entrypoint", "_sync_scope_guard",
    }
    module_exports = {
        "__all__",
    }
    # Skip only dunders. The four copies of this function used to disagree --
    # two skipped every `_`-prefixed name, two skipped only `__` -- so whether a
    # single-underscore helper resolved depended on which file asked. That is how
    # `_reset_queue_record_for_redispatch` came to be called in `process_queue`
    # while never being present in this module's globals: supervisor defines it,
    # and this module's rule filtered it out. Module-local names that must NOT be
    # replaced are listed in `excluded` by name rather than inferred from a prefix.
    g = globals()
    for key, value in sv.__dict__.items():
        if key in excluded or key in module_exports or key.startswith("__"):
            continue
        g[key] = value


def _entrypoint(func):
    def _sync_scope_guard(*args, **kwargs):
        _sync_supervisor_scope()
        return func(*args, **kwargs)
    return _sync_scope_guard





@_entrypoint
def worker_worktree_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktrees")
    settings = raw if isinstance(raw, dict) else {}
    try:
        git_network_timeout_seconds = max(
            1.0,
            float(
                settings.get(
                    "git_network_timeout_seconds",
                    config.get("supervisor", {}).get("external_command_timeout_seconds", 30),
                )
            ),
        )
    except (TypeError, ValueError):
        git_network_timeout_seconds = 30.0
    return {
        # The default is only diagnostic; leasing and pruning fail closed until
        # a fleet names its root explicitly. A live fleet must keep that path:
        # is baked into every `git worktree` registration on disk, so changing
        # it would orphan them all at once -- 127 of them on this host.
        "root": str(settings.get("root") or "/tmp/pantheon-worker-worktrees"),
        # Housekeeping is destructive (it removes already-merged worktrees),
        # so unlike allocation it may never act on an inherited /tmp default.
        # A deployed fleet must name the root that contains its own leases.
        "root_configured": bool(str(settings.get("root") or "").strip()),
        "git_network_timeout_seconds": git_network_timeout_seconds,
    }


class WorkerBaseResolution(NamedTuple):
    """The immutable remote base one supervisor cycle leases to one repo."""

    repository_id: str
    default_branch: str
    sha: str
    remote_ref: str


@_entrypoint
def _worker_base_cache_key(repo_root: Path, repository_id: str, default_branch: str) -> str:
    common_rc, common_dir = _git_output(repo_root, "rev-parse", "--git-common-dir")
    if common_rc == 0 and common_dir.strip():
        try:
            common = (repo_root / common_dir.strip()).resolve()
        except OSError:
            common = repo_root.resolve()
    else:
        common = repo_root.resolve()
    return f"{common}|{repository_id}|{default_branch}"


@_entrypoint
def resolve_worker_base(
    repo_root: Path,
    *,
    repository_id: str,
    default_branch: str,
    base_cache: dict[str, WorkerBaseResolution | str] | None,
    network_timeout_seconds: float | None,
) -> tuple[WorkerBaseResolution | None, str | None]:
    """Fetch a registry default branch once, then return its immutable SHA.

    The cache is deliberately supplied by ``run_once`` rather than persisted in
    runtime state.  It is a scheduling optimisation and provenance record for a
    single dispatch cycle, not another source of truth or a second state
    machine.  A failed first fetch is cached too, so a burst of tasks cannot
    turn one unavailable remote into an unbounded retry storm.
    """
    branch = str(default_branch or "").strip()
    if not branch_name_is_usable(branch):
        return None, f"invalid_registry_default_branch:{branch or 'missing'}"
    key = _worker_base_cache_key(repo_root, repository_id, branch)
    cache = base_cache if base_cache is not None else {}
    cached = cache.get(key)
    if isinstance(cached, WorkerBaseResolution):
        return cached, None
    if isinstance(cached, str):
        return None, cached

    has_origin = "origin" in _git_output(repo_root, "remote")[1].splitlines()
    if not has_origin:
        error = "missing_origin_remote"
        cache[key] = error
        return None, error
    fetch_proc, network_error = _run_git_network_command(
        repo_root,
        ["fetch", "origin", "--quiet", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        timeout_seconds=network_timeout_seconds,
    )
    if network_error or fetch_proc is None:
        error = f"base_fetch_timed_out:{network_error or 'unknown network failure'}"
        cache[key] = error
        return None, error
    if fetch_proc.returncode != 0:
        details = (fetch_proc.stderr or fetch_proc.stdout or "").strip()
        error = f"base_fetch_failed:{details or branch}"
        cache[key] = error
        return None, error
    remote_ref = f"refs/remotes/origin/{branch}"
    sha = _git_commit_oid(repo_root, remote_ref)
    if not sha:
        error = f"base_sha_missing:{remote_ref}"
        cache[key] = error
        return None, error
    resolved = WorkerBaseResolution(repository_id, branch, sha, f"origin/{branch}")
    cache[key] = resolved
    return resolved, None

@_entrypoint
def _task_id_slug(task_id: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(task_id or "").lower()).strip("-")
    return slug or "unknown-task"

@_entrypoint
def canonical_task_record(
    config: dict[str, Any],
    task_id: str | None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The task as the status file states it, not as a dispatch event echoed it."""
    try:
        record = task_index_from_status(config, load_status(config)).get(str(task_id or ""))
    except Exception:
        record = None
    return record if isinstance(record, dict) else fallback


# Conservative subset of git's own ref-name rules. A task record is data, and a
# malformed branch name in it must not reach `git worktree add`.
_INVALID_BRANCH_CHARS = re.compile(r"[\s~^:?*\[\\]")


@_entrypoint
def branch_name_is_usable(name: str) -> bool:
    if not name or name.startswith("-") or name.startswith("/") or name.endswith("/"):
        return False
    if name.endswith(".lock") or ".." in name or "@{" in name:
        return False
    return _INVALID_BRANCH_CHARS.search(name) is None


@_entrypoint
def worker_task_branch(config: dict[str, Any], task_id: str | None, task: dict[str, Any] | None = None) -> str:
    """The branch a worker must check out for a task.

    The task record's own `branch` wins. Deriving `task/<id>` unconditionally
    invented a name for every task whose branch does not follow that
    convention -- which is every task reimported from an existing GitHub PR.
    The fail-closed refresh policy then reported the invented branch as missing
    from a remote that never had it, and no retry could clear that.
    SINGLE-RUNTIME-RELEASE-0D1603CF sat there: its work is on
    `single-runtime-release-0d1603cf` (PR #822), while the leased worktree held
    an empty `task/SINGLE-RUNTIME-RELEASE-0D1603CF` carrying no task commit at
    all. Derivation stays as the fallback for records that name no branch.
    """
    if isinstance(task, dict):
        recorded = str(task.get("branch") or "").strip()
        if recorded and branch_name_is_usable(recorded):
            return recorded
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    normalized_task_id = str(task_id or "").strip()
    return f"{prefix}{normalized_task_id}" if normalized_task_id else f"{prefix}unknown-task"

@_entrypoint
def _worker_worktree_base_root(config: dict[str, Any], settings: dict[str, Any]) -> Path:
    repo_root = config_path(config, "status_file").parents[0]
    configured = Path(os.path.expanduser(str(settings.get("root") or "")))
    if not configured.is_absolute():
        configured = repo_root / configured
    return configured.resolve()

@_entrypoint
def worker_task_repo_root(config: dict[str, Any], task: dict[str, Any] | None) -> tuple[Path | None, str]:
    """Resolve the checkout that owns a task's worktree and task branch.

    Delegates to the registry so worktree leasing cannot drift from the
    repository every other subsystem resolved for the same task. Falling back
    to the supervisor root would create the task branch in the wrong origin,
    and the fail-closed refresh policy then reports that branch as missing from
    a remote that never had it -- a dispatch deadlock no retry can clear.
    """
    binding = worker_task_repository_binding(config, task)
    if binding.resolved:
        return binding.root, binding.source
    return None, binding.error or "unresolved repository"


@_entrypoint
def worker_task_repository_binding(config: dict[str, Any], task: dict[str, Any] | None):
    """Resolve the registry binding used for both worktree and base authority."""
    # Lazy import mirrors source_document_router: avoids a common.py cycle.
    from multi_repo_registry import resolve_task_repository

    return resolve_task_repository(config, task)


@_entrypoint
def worker_task_worktree_path(
    config: dict[str, Any],
    task_id: str | None,
    settings: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    repository_id: str | None = None,
) -> Path:
    active_settings = settings or worker_worktree_settings(config)
    root = repo_root or config_path(config, "status_file").parents[0]
    repo_slug = re.sub(r"[^a-z0-9]+", "-", str(repository_id or root.name).lower()).strip("-") or "repo"
    return _worker_worktree_base_root(config, active_settings) / repo_slug / _task_id_slug(task_id)

@_entrypoint
def _git_worktree_records(repo_root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    if current:
        records.append(current)
    return records

@_entrypoint
def _worktree_record_branch(record: dict[str, str]) -> str:
    branch = str(record.get("branch") or "").strip()
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


@_entrypoint
def _detached_head_is_merged(repo_root: Path, worktree_path: Path, base_refs: list[str]) -> bool:
    """Return True when a branchless worktree's HEAD is already contained in a base.

    This is the detached-HEAD spelling of the `branch in merged_branches` test that
    guards worktree removal: both answer "is this content already in a base branch,
    so that deleting the checkout loses nothing?". Fails closed -- an unreadable
    HEAD or an unanswerable ancestry query keeps the worktree.
    """
    if not base_refs:
        return False
    head = _git_commit_oid(worktree_path, "HEAD")
    if not head:
        return False
    for base_ref in base_refs:
        rc, _ = _git_output(repo_root, "merge-base", "--is-ancestor", head, base_ref)
        if rc == 0:
            return True
    return False


@_entrypoint
def _worktree_matches_repo_common_dir(repo_root: Path, path: Path) -> bool:
    try:
        path = path.resolve()
        repo_common_rc, repo_common = _git_output(repo_root, "rev-parse", "--git-common-dir")
        if repo_common_rc != 0:
            return False
        expected_common = Path(repo_common)
        if not expected_common.is_absolute():
            expected_common = (repo_root / expected_common).resolve()
        else:
            expected_common = expected_common.resolve()

        top_level_rc, top_level = _git_output(path, "rev-parse", "--show-toplevel")
        if top_level_rc != 0 or Path(top_level).resolve() != path:
            return False

        worktree_common_rc, worktree_common = _git_output(path, "rev-parse", "--git-common-dir")
        if worktree_common_rc != 0:
            return False
        resolved_worktree_common = Path(worktree_common)
        if not resolved_worktree_common.is_absolute():
            resolved_worktree_common = (path / resolved_worktree_common).resolve()
        else:
            resolved_worktree_common = resolved_worktree_common.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_worktree_common == expected_common


@_entrypoint
def _existing_worktree_for_branch(repo_root: Path, branch: str, *, exclude_root: bool) -> Path | None:
    resolved_repo_root = repo_root.resolve()

    for record in _git_worktree_records(repo_root):
        if _worktree_record_branch(record) != branch:
            continue
        path_value = record.get("worktree")
        if not path_value:
            continue
        path = Path(path_value).resolve()
        if not path.exists() or not path.is_dir():
            continue
        if exclude_root and path == resolved_repo_root:
            continue
        if not (path / ".git").exists():
            continue
        if _git_output(path, "rev-parse", "--is-inside-work-tree")[0] != 0:
            continue
        if not _worktree_matches_repo_common_dir(repo_root, path):
            continue
        return path
    return None

@_entrypoint
def _create_worker_worktree(repo_root: Path, path: Path, branch: str, base_sha: str) -> tuple[bool, str | None]:
    """Create a registered task worktree from an immutable base commit.

    This is the only creation primitive.  In particular, it must not fall back
    to ``git clone``: a clone has its own common-dir and turns one registry
    repository into a second, competing source authority.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_dir():
            return False, f"Worker worktree path already exists and is not empty: {path}"
        if any(path.iterdir()) and (
            not (path / ".git").exists() or not _worktree_matches_repo_common_dir(repo_root, path)
        ):
            return False, f"Worker worktree path is not a registered worktree: {path}"
        elif any(path.iterdir()) and (path / ".git").exists():
            return False, f"Worker worktree path already exists and is not empty: {path}"

    remote_ref = f"refs/remotes/origin/{branch}"
    if _git_ref_exists(repo_root, f"refs/heads/{branch}"):
        command = ["git", "worktree", "add", str(path), branch]
    elif _git_ref_exists(repo_root, remote_ref):
        command = ["git", "worktree", "add", "-b", branch, str(path), f"origin/{branch}"]
    else:
        if not _git_commit_oid(repo_root, base_sha):
            return False, f"Worker base SHA is unavailable in repository: {base_sha}"
        command = ["git", "worktree", "add", "-b", branch, str(path), base_sha]

    proc = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        return False, f"Failed to create worker worktree {path} for {branch}: {details}"
    return True, None

def _classify_worktree_dirt(
    porcelain_status: str | bytes,
    worktree_path: Path | None = None,
    materialized_paths=None,
) -> tuple[str, list[str]]:
    """Compatibility view over the shared handoff-cleanliness policy.

    The legacy labels remain only for call sites/tests while the policy itself
    lives in ``worktree_cleanliness`` and is also used by task_finalize.sh.
    """
    inspection = _inspect_porcelain(
        porcelain_status,
        worktree_path=worktree_path,
        materialized_paths=materialized_paths,
    )
    if inspection.kind == "clean":
        return "clean", []
    if inspection.kind == "orchestrator_seed_only":
        return "scratch_only", [path for _code, path in inspection.entries]
    return "real", []

@_entrypoint
def _git_output(cwd: Path, *args: str) -> tuple[int, str]:
    if not cwd or not Path(cwd).exists():
        return 1, ""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, ValueError):
        return 1, ""

@_entrypoint
def _run_git_network_command(
    cwd: Path,
    args: list[str],
    *,
    timeout_seconds: float | None,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    """Run a remote git operation with a bounded wait.

    Worktree preflight runs in the supervisor's critical path.  A wedged
    HTTPS remote must fail this one lease closed, rather than consuming every
    scheduler tick and hiding useful capacity behind a stuck subprocess.
    ``None`` keeps direct unit-test callers backward compatible.
    """
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    try:
        return subprocess.run(["git", *args], **kwargs), None
    except subprocess.TimeoutExpired:
        limit = float(timeout_seconds or 0)
        return None, f"git network command timed out after {limit:g}s"
    except (OSError, ValueError) as exc:
        return None, f"git network command could not start: {type(exc).__name__}: {exc}"

@_entrypoint
def _git_commit_oid(cwd: Path, ref: str) -> str | None:
    returncode, output = _git_output(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    oid = output.splitlines()[0].strip() if output else ""
    return oid if returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40,64}", oid) else None

@_entrypoint
def _git_operation_in_progress(worktree_path: Path) -> bool:
    # REBASE_HEAD records the commit currently being replayed, but Git may
    # retain it after a successful rebase has finished.  The authoritative
    # in-progress signals are rebase-merge/rebase-apply below; treating a stale
    # REBASE_HEAD as active permanently jams an otherwise reusable worktree.
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        returncode, _ = _git_output(worktree_path, "rev-parse", "--verify", "-q", marker)
        if returncode == 0:
            return True
    for marker in ("rebase-merge", "rebase-apply"):
        returncode, raw_path = _git_output(worktree_path, "rev-parse", "--git-path", marker)
        if returncode != 0 or not raw_path:
            return True
        marker_path = Path(raw_path)
        if not marker_path.is_absolute():
            marker_path = worktree_path / marker_path
        if marker_path.exists():
            return True
    return False

@_entrypoint
def _prune_worktree_lease_blocks(bucket: dict[str, Any]) -> None:
    """Forget streaks that nothing has touched for days.

    The counter is durable state, and `_clear_worktree_lease_block` only runs
    when a task actually leases a worktree. A task that is blocked and then
    abandoned -- finished, cancelled, renamed -- never reaches that path, so
    without an expiry its entry would sit in `state.json` forever. Supervisor
    ticks are minutes apart, so anything untouched for days is also no longer a
    *consecutive* streak; dropping it restarts the count, which is the honest
    reading.
    """

    cutoff = datetime.now(UTC) - timedelta(hours=WORKTREE_LEASE_BLOCK_RETENTION_HOURS)
    for key, entry in list(bucket.items()):
        if not isinstance(entry, dict):
            bucket.pop(key, None)
            continue
        # An unparseable timestamp is kept: expiring a streak we cannot date is
        # the failure mode this whole task exists to remove. A hand-edited entry
        # can also be naive; read it as UTC rather than raising inside dispatch.
        last_at = _parse_iso_utc(entry.get("last_at") or entry.get("first_at"))
        if last_at is None:
            continue
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=UTC)
        if last_at < cutoff:
            bucket.pop(key, None)

@_entrypoint
def compute_worktree_state_identity(
    worktree_path: Path | str,
    *,
    materialized_paths: Iterable[object] | None = None,
) -> str:
    """Compute an auditable, secret-free identity of the worktree state."""
    try:
        path = Path(worktree_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return "unresolvable_path"
    if not path.exists() or not path.is_dir() or not (path / ".git").exists():
        return "missing_worktree"
    head_sha = _git_commit_oid(path, "HEAD") or "none"
    if _git_operation_in_progress(path):
        return f"unresolved_git_operation:{head_sha}"
    inspection = inspect_worktree(path, materialized_paths=materialized_paths)
    if inspection.kind == "clean":
        return f"clean:{head_sha}"
    if inspection.kind == "orchestrator_seed_only":
        return f"orchestrator_seed_only:{inspection.fingerprint}:{head_sha}"
    return f"{inspection.kind}:{inspection.fingerprint}:{head_sha}"


@_entrypoint
def is_worktree_lease_block_repaired(entry: dict[str, Any]) -> bool:
    """Return True when a recorded lease block's worktree is verified clean and idle."""
    if not isinstance(entry, dict):
        return False
    raw_path = entry.get("worktree_path")
    if not raw_path:
        return False
    identity = compute_worktree_state_identity(
        raw_path,
        materialized_paths=entry.get("materialized_paths"),
    )
    return identity.startswith("clean:") or identity.startswith("orchestrator_seed_only:")


@_entrypoint
def _record_worktree_lease_block(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    task_id: str,
    refresh_status: str,
    message: str,
    worktree_path: Path | str | None = None,
    materialized_paths: Any = None,
    worktree_state_identity: str | None = None,
) -> int:
    """Count consecutive lease blocks and escalate once they stop being noise.

    A single blocked lease is ordinary: the next tick usually clears it. What is
    not ordinary is the same block repeating unchanged forever. On 2026-08-05 ten
    tasks were blocked 1713 times over ~8h without one escalation, because each
    attempt only appended an activity record and returned. `active_workers=0`
    alongside a non-empty queue is not itself an alarm condition, so the fleet
    read as healthy the entire time.

    Returns the current consecutive count.
    """

    bucket = state.setdefault("worker_worktree_lease_blocks", {})
    _prune_worktree_lease_blocks(bucket)
    key = normalize_agent_id(task_id) or task_id
    entry = bucket.get(key)
    if not isinstance(entry, dict) or entry.get("refresh_status") != refresh_status:
        entry = {"count": 0, "first_at": utc_now(), "refresh_status": refresh_status, "escalated": False}
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_at"] = utc_now()
    entry["message"] = message

    if worktree_path is not None:
        try:
            wp = Path(worktree_path).resolve()
            entry["worktree_path"] = str(wp)
            if worktree_state_identity is None:
                worktree_state_identity = compute_worktree_state_identity(wp, materialized_paths=materialized_paths)
            entry["worktree_state_identity"] = worktree_state_identity
        except (OSError, RuntimeError, ValueError):
            entry["worktree_path"] = str(worktree_path)
            if worktree_state_identity is not None:
                entry["worktree_state_identity"] = worktree_state_identity
    elif worktree_state_identity is not None:
        entry["worktree_state_identity"] = worktree_state_identity

    if materialized_paths is not None:
        entry["materialized_paths"] = list(materialized_paths)

    bucket[key] = entry

    threshold = max(2, int(worker_runtime_settings(config).get("lease_block_escalate_after", 5)))
    if entry["count"] >= threshold and not entry.get("escalated"):
        entry["escalated"] = True
        console_log(
            f"worktree lease blocked repeatedly: task={task_id} count={entry['count']} "
            f"status={refresh_status} -- dispatch for this task is stuck and needs an owner decision",
            quiet=SUPERVISOR_LOG_QUIET,
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease_escalated",
                "task_id": task_id,
                "message": (
                    f"Worktree lease has been blocked {entry['count']} consecutive times with "
                    f"`{refresh_status}`. This will not clear on its own: {message}"
                ),
                "refresh_status": refresh_status,
                "consecutive_blocks": entry["count"],
                "first_blocked_at": entry.get("first_at"),
            },
        )
    return int(entry["count"])

@_entrypoint
def _clear_worktree_lease_block(state: dict[str, Any], task_id: str) -> None:
    bucket = state.get("worker_worktree_lease_blocks")
    if isinstance(bucket, dict):
        bucket.pop(normalize_agent_id(task_id) or task_id, None)

# Refresh status token for a reused worktree denied by real dirt. The refresh returns
# "<token>: <what git actually reported>" so an operator reads the offending paths
# instead of a fixed guess; callers must therefore compare on the token, not the
# whole string.
_SKIPPED_DIRTY_WORKTREE = "skipped_dirty_worktree"


@_entrypoint
def _lease_status_kind(refresh_status: str | None) -> str:
    """The stable token of a refresh status, dropping any ': <detail>' suffix."""
    return str(refresh_status or "").split(":", 1)[0].strip()


@_entrypoint
def _is_skipped_dirty_worktree(refresh_status: str | None) -> bool:
    return _lease_status_kind(refresh_status) == _SKIPPED_DIRTY_WORKTREE


@_entrypoint
def _dirty_worktree_detail(refresh_status: str | None) -> str:
    status = str(refresh_status or "")
    prefix = f"{_SKIPPED_DIRTY_WORKTREE}:"
    if status.startswith(prefix):
        detail = status[len(prefix) :].strip()
        if detail:
            return detail
    return "dirty changes"


@_entrypoint
def _refresh_reused_worker_worktree(
    repo_root: Path,
    worktree_path: Path,
    base_sha: str,
    expected_branch: str,
    *,
    network_timeout_seconds: float | None = None,
    materialized_paths=None,
    required_head: str | None = None,
) -> tuple[bool, str]:
    """Lease a clean reused worktree against one immutable cycle base.

    This is intentionally local after ``resolve_worker_base`` fetched the
    registry's default branch.  Fetching or fast-forwarding a task branch here
    would create a second source authority and make one cycle observe several
    moving branch heads.  A clean branch behind the base is fast-forwarded only
    to the supplied SHA; a branch containing or diverging from that base stays
    untouched and the relation is returned for the worker/review workflow.
    """
    worktree_path = worktree_path.resolve()
    repo_root = repo_root.resolve()

    top_rc, top_level = _git_output(worktree_path, "rev-parse", "--show-toplevel")
    worktree_common_rc, worktree_common = _git_output(worktree_path, "rev-parse", "--git-common-dir")
    repo_common_rc, repo_common = _git_output(repo_root, "rev-parse", "--git-common-dir")
    try:
        resolved_top = Path(top_level).resolve()
        resolved_worktree_common = (worktree_path / worktree_common).resolve()
        resolved_repo_common = (repo_root / repo_common).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, "wrong_worktree: unable to resolve repository identity"
    if (
        top_rc != 0
        or worktree_common_rc != 0
        or repo_common_rc != 0
        or resolved_top != worktree_path
        or resolved_worktree_common != resolved_repo_common
    ):
        return False, "wrong_worktree: path is not the expected repository worktree"

    branch_rc, branch = _git_output(worktree_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_rc == 0:
        if branch != expected_branch:
            return False, f"wrong_branch: expected {expected_branch}, found {branch}"
    else:
        current_head = _git_commit_oid(worktree_path, "HEAD")
        expected_head = (
            _git_commit_oid(repo_root, f"refs/heads/{expected_branch}")
            or _git_commit_oid(repo_root, f"origin/{expected_branch}")
            or _git_commit_oid(repo_root, expected_branch)
        )
        if not current_head or not expected_head or current_head != expected_head:
            return False, f"wrong_branch: expected {expected_branch} ({expected_head or 'none'}), found detached HEAD at {current_head or 'none'}"
    if _git_operation_in_progress(worktree_path):
        return False, "unresolved_git_operation"

    status_proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
    )
    if status_proc.returncode != 0:
        return False, "status_failed"
    if status_proc.stdout:
        inspection = _inspect_porcelain(
            status_proc.stdout,
            worktree_path=worktree_path,
            materialized_paths=materialized_paths,
        )
        if not inspection.handoff_clean:
            return False, f"{_SKIPPED_DIRTY_WORKTREE}: {inspection.detail}"

    local_head = _git_commit_oid(worktree_path, "HEAD")
    if required_head:
        required_head = str(required_head).strip()
        expected_head = _git_commit_oid(repo_root, required_head)
        if not expected_head or expected_head != required_head:
            return False, "review_head_unavailable"
        if not local_head:
            return False, "unverifiable_refs: missing local HEAD"
        if local_head != expected_head:
            review_contains_rc, _ = _git_output(
                worktree_path, "merge-base", "--is-ancestor", local_head, expected_head
            )
            if review_contains_rc != 0:
                return False, f"review_head_mismatch: local={local_head}, expected={expected_head}"
            merge_proc = subprocess.run(
                ["git", "merge", "--ff-only", expected_head],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if merge_proc.returncode != 0:
                details = (merge_proc.stderr or merge_proc.stdout or "").strip().splitlines()
                return False, f"review_head_fast_forward_failed: {details[0] if details else 'unknown'}"
            local_head = _git_commit_oid(worktree_path, "HEAD")
            if local_head != expected_head:
                return False, "review_head_fast_forward_incomplete"
        # A reviewer must see the exact submitted commit. Once that commit has
        # been established, the ordinary base refresh below must not advance
        # this checkout when dev already contains it.
        return True, f"review_head_pinned_at_{expected_head[:12]}"
    # Production passes the SHA resolved once at the beginning of this cycle.
    # Symbolic refs remain accepted only for direct diagnostic/test callers.
    base_head = _git_commit_oid(worktree_path, base_sha)
    if not local_head or not base_head:
        return False, "unverifiable_refs: missing local HEAD or immutable base"

    base_contains_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", local_head, base_head
    )
    if base_contains_rc not in {0, 1}:
        return False, "unverifiable_refs: cannot compare local HEAD with immutable base"
    if base_contains_rc == 0:
        merge_proc = subprocess.run(
            ["git", "merge", "--ff-only", base_head],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if merge_proc.returncode != 0:
            details = (merge_proc.stderr or merge_proc.stdout or "").strip().splitlines()
            return False, f"fast_forward_failed: {details[0] if details else 'unknown'}"
        return True, f"ff_to_{base_head[:12]}"

    task_contains_rc, _ = _git_output(
        worktree_path, "merge-base", "--is-ancestor", base_head, local_head
    )
    if task_contains_rc not in {0, 1}:
        return False, "unverifiable_refs: cannot compare immutable base with local HEAD"
    if task_contains_rc == 0:
        return True, f"base_present_at_{local_head[:12]}"
    return True, f"base_advance_rebase_required:local={local_head},base={base_head}"


class WorkerHandoffSeal(NamedTuple):
    """Result of the one closeout gate between an owner and a reviewer."""

    accepted: bool
    reason: str
    detail: str
    head_sha: str | None
    dirt_fingerprint: str | None


@_entrypoint
def _worker_materialized_context_paths(state: dict[str, Any], worker: dict[str, Any]) -> list[str]:
    """Recover the Supervisor-owned context allowlist for a settled worker."""
    paths: list[str] = []
    request = worker.get("request_snapshot")
    metadata = request.get("metadata") if isinstance(request, dict) else None
    if isinstance(metadata, dict):
        for raw in metadata.get("materialized_context_files", []) or []:
            paths.append(str(raw))
        workspace_task_id = str(metadata.get("workspace_task_id") or worker.get("task_id") or "")
    else:
        workspace_task_id = str(worker.get("task_id") or "")
    lease = ((state.get("worker_worktrees") or {}).get("leases") or {}).get(workspace_task_id)
    if isinstance(lease, dict):
        for raw in lease.get("materialized_context_files", []) or []:
            paths.append(str(raw))
    return paths

@_entrypoint
def _dead_owner_continuation_eligible(
    config: dict[str, Any],
    worker: dict[str, Any],
    task: dict[str, Any] | None,
) -> bool:
    """Allow recovery only for the dead task owner's execution worker."""
    request = worker.get("request_snapshot")
    raw_reason = (
        request.get("reason")
        if isinstance(request, dict)
        else worker.get("reason")
    )
    if str(raw_reason or "").strip() not in {
        REASON_OWNED_READY,
        REASON_OWNED_IN_PROGRESS,
    }:
        return False
    if not isinstance(task, dict):
        return False
    schema = config.get("schema") if isinstance(config.get("schema"), dict) else {}
    owner = str(task.get(schema.get("assignee_field", "owner")) or "")
    worker_agent = worker_logical_dispatch_agent_id(config, worker)
    return bool(
        owner
        and worker_agent
        and normalize_agent_id(owner) == normalize_agent_id(worker_agent)
    )

@_entrypoint
def seal_worker_handoff(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    task: dict[str, Any] | None,
) -> WorkerHandoffSeal:
    """Verify that an owner can safely release its workspace to another role.

    A review status is not enough: task_finalize can submit it while the owner
    CLI is still flushing output.  This gate runs only after that process exits,
    so no reviewer or helper inherits a path that the owner changed afterwards.
    """
    if str(worker.get("workspace_mode") or "") != "isolated_worktree":
        # Legacy/manual workers have no Supervisor-owned task checkout to seal.
        return WorkerHandoffSeal(True, "not_isolated_worktree", "no isolated worker worktree", None, None)
    raw_path = str(worker.get("workspace_path") or "").strip()
    if not raw_path:
        return WorkerHandoffSeal(False, "workspace_missing", "isolated worker has no workspace path", None, None)
    try:
        workspace_path = Path(raw_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return WorkerHandoffSeal(False, "workspace_unreadable", raw_path, None, None)

    expected_branch = str(worker.get("workspace_branch") or "").strip()
    if not expected_branch and isinstance(task, dict):
        expected_branch = worker_task_branch(config, str(worker.get("task_id") or ""), task)
    branch_rc, current_branch = _git_output(workspace_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_rc != 0 or not current_branch or (expected_branch and current_branch != expected_branch):
        return WorkerHandoffSeal(
            False,
            "branch_mismatch",
            f"expected {expected_branch or 'a task branch'}, found {current_branch or 'detached HEAD'}",
            None,
            None,
        )
    if _git_operation_in_progress(workspace_path):
        return WorkerHandoffSeal(False, "git_operation_in_progress", "Git operation remains in progress", None, None)

    inspection: WorktreeInspection = inspect_worktree(
        workspace_path,
        materialized_paths=_worker_materialized_context_paths(state, worker),
    )
    if not inspection.handoff_clean:
        return WorkerHandoffSeal(
            False,
            inspection.kind,
            inspection.detail,
            _git_commit_oid(workspace_path, "HEAD"),
            inspection.fingerprint or None,
        )

    head_sha = _git_commit_oid(workspace_path, "HEAD")
    if not head_sha:
        return WorkerHandoffSeal(False, "head_unreadable", "cannot resolve workspace HEAD", None, inspection.fingerprint)
    submission = task.get("review_submission") if isinstance(task, dict) else None
    expected_head = str(submission.get("remote_sha") or "") if isinstance(submission, dict) else ""
    if str((task or {}).get("status") or "").lower() == "review" and expected_head and head_sha != expected_head:
        return WorkerHandoffSeal(
            False,
            "review_head_mismatch",
            f"workspace HEAD {head_sha[:12]} differs from submitted review head {expected_head[:12]}",
            head_sha,
            inspection.fingerprint,
        )
    return WorkerHandoffSeal(True, inspection.kind, inspection.detail, head_sha, inspection.fingerprint)


@_entrypoint
def record_unsealed_worker_handoff(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    task: dict[str, Any] | None,
    seal: WorkerHandoffSeal,
) -> None:
    """Record immutable enough provenance for a same-owner cleanup continuation."""
    task_id = str(worker.get("task_id") or "")
    if not task_id or seal.accepted:
        return
    schema = config.get("schema") if isinstance(config.get("schema"), dict) else {}
    owner = str((task or {}).get(schema.get("assignee_field", "owner")) or "")
    bucket = state.setdefault("worker_worktrees", {}).setdefault("handoff_blocks", {})
    bucket[task_id] = {
        "task_id": task_id,
        "owner": owner,
        "workspace_path": str(worker.get("workspace_path") or ""),
        "workspace_branch": str(worker.get("workspace_branch") or ""),
        "head_sha": seal.head_sha,
        "dirt_fingerprint": seal.dirt_fingerprint,
        "reason": seal.reason,
        "detail": seal.detail,
        "source_run_id": worker.get("run_id"),
        "sealed_at": utc_now(),
    }


@_entrypoint
def clear_unsealed_worker_handoff(state: dict[str, Any], task_id: str | None) -> None:
    bucket = (state.get("worker_worktrees") or {}).get("handoff_blocks")
    if isinstance(bucket, dict):
        bucket.pop(str(task_id or ""), None)


@_entrypoint
def sealed_owner_continuation_allowed(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    task: dict[str, Any] | None,
    *,
    target_agent: str | None,
    worktree_path: Path,
    branch: str,
    materialized_paths=None,
) -> tuple[bool, str]:
    """Permit only the owner to finish an exactly sealed dirty workspace.

    This is not the retired fresh-worktree recovery path.  It resumes the same
    task, branch and checkout only after the prior owner exited and the exact
    dirt fingerprint was recorded.  Reviewers and helper claims always remain
    fail-closed.
    """
    if str(request.reason or "") not in {
        REASON_OWNED_READY,
        REASON_OWNED_IN_PROGRESS,
    }:
        return False, "not_owner_execution"
    task_id = str(request.task_id or "")
    record = ((state.get("worker_worktrees") or {}).get("handoff_blocks") or {}).get(task_id)
    if not isinstance(record, dict):
        return False, "no_handoff_block"
    schema = config.get("schema") if isinstance(config.get("schema"), dict) else {}
    owner = str((task or {}).get(schema.get("assignee_field", "owner")) or "")
    if not owner or normalize_agent_id(owner) != normalize_agent_id(str(target_agent or "")):
        return False, "not_same_owner"
    if str(record.get("owner") or "") != owner:
        return False, "owner_changed"
    try:
        recorded_path = Path(str(record.get("workspace_path") or "")).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, "recorded_path_invalid"
    if recorded_path != worktree_path.resolve() or str(record.get("workspace_branch") or "") != branch:
        return False, "workspace_changed"
    inspection = inspect_worktree(worktree_path, materialized_paths=materialized_paths)
    if inspection.kind != "owner_dirty" or inspection.fingerprint != record.get("dirt_fingerprint"):
        return False, "dirt_changed"
    head_sha = _git_commit_oid(worktree_path, "HEAD")
    if not head_sha or head_sha != record.get("head_sha"):
        return False, "head_changed"
    return True, str(record.get("detail") or "owner worktree requires closeout")

@_entrypoint
def _task_brief_context_candidates(task_id: str | None, rel_context_path: str) -> list[str]:
    normalized = rel_context_path.replace("\\", "/").strip()
    candidates = [normalized]
    if ".orchestrator/task-briefs/" in normalized and task_id:
        hyphen_slug = _task_id_slug(task_id)
        underscore_slug = hyphen_slug.replace("-", "_")
        for slug in (underscore_slug, hyphen_slug, normalize_agent_id(task_id)):
            if slug:
                candidates.append(f".orchestrator/task-briefs/{slug}.md")
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered

@_entrypoint
def _atomic_replace_context_bytes(
    destination: Path,
    payload: bytes,
    *,
    source_stat: os.stat_result | None = None,
) -> None:
    """Write context bytes without following an existing destination inode.

    In particular, replacing the directory entry prevents an untracked hard link
    at the destination from mutating a tracked file that shares the same inode.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.context-",
        dir=str(destination.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if source_stat is not None:
            os.chmod(temp_path, source_stat.st_mode & 0o777)
        os.replace(temp_path, destination)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

@_entrypoint
def _atomic_copy_context_file(source: Path, destination: Path) -> None:
    _atomic_replace_context_bytes(
        destination,
        source.read_bytes(),
        source_stat=source.stat(follow_symlinks=False),
    )

@_entrypoint
def _atomic_write_context_text(destination: Path, text: str) -> None:
    _atomic_replace_context_bytes(destination, text.encode("utf-8"))

@_entrypoint
def _is_tracked_in_worktree(worktree_path: Path, rel_path: str) -> bool:
    try:
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel_path],
            cwd=str(worktree_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False

@_entrypoint
def _file_or_dir_hash(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_dir():
            hasher = hashlib.sha256()
            for subfile in sorted(path.rglob("*")):
                if subfile.is_file():
                    rel = str(subfile.relative_to(path))
                    hasher.update(rel.encode("utf-8"))
                    hasher.update(subfile.read_bytes())
            return hasher.hexdigest()
    except Exception:
        return None
    return None

@_entrypoint
def _is_valid_sha256(h: str | None) -> bool:
    return bool(h and isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h))

@_entrypoint
def _generated_worker_task_brief(config: dict[str, Any], task_id: str | None) -> str:
    try:
        text, _, _ = generate_task_brief_content(config, str(task_id or ""))
        return text
    except ValueError as err:
        if "Archived-task ambiguity" in str(err):
            raise
        task = task_index_from_status(config, load_status(config)).get(str(task_id or ""))
        if not task:
            return "\n".join(
                [
                    f"# Task Brief: {task_id or 'unknown-task'}",
                    "",
                    "Generated in the worker workspace because the supervisor root did not have a task brief file.",
                    "",
                ]
            )
        source_docs = [str(item).strip() for item in (task.get("source_docs") or []) if str(item).strip()]
        acceptance = [str(item).strip() for item in (task.get("acceptance") or []) if str(item).strip()]
        verification = [str(item).strip() for item in (task.get("verification") or []) if str(item).strip()]
        body = [
            f"# Task Brief: {task.get('id') or task_id}",
            "",
            "Generated in the worker workspace because the supervisor root did not have a task brief file.",
            "",
            "## Task",
            f"- Title: {task.get('title') or '-'}",
            f"- Status: {task.get('status') or '-'}",
            f"- Owner: {task.get('owner') or '-'}",
            f"- Reviewer: {task.get('reviewer') or '-'}",
            f"- Next: {task.get('next') or '-'}",
            "",
            "## Summary",
            str(task.get("summary_zh") or "-"),
            "",
            "## Source Documents",
        ]
        body.extend([f"- {item}" for item in source_docs] or ["- none"])
        body.extend(["", "## Acceptance"])
        body.extend([f"- {item}" for item in acceptance] or ["- none"])
        body.extend(["", "## Verification"])
        if verification:
            audits = verification_evidence.audit_commands(verification)
            for audit in audits:
                if audit.ok:
                    body.append(f"- `{audit.command}`")
                else:
                    body.append(
                        f"- `{audit.command}` — REJECTED ({', '.join(audit.violations)}): "
                        + "; ".join(audit.details)
                    )
            body.extend(
                [
                    "",
                    "### Verification Evidence Policy",
                    "- Run each command so its own exit code survives: no pipe without `set -o pipefail`,",
                    "  no `|| true`, no `; echo ...` tail, no `set +e`, no backgrounding.",
                    "- Record a receipt binding head SHA, exact command, real exit code, duration, and test selection.",
                    "- A run killed by a signal or timeout is `interrupted`, never a pass, and is repeated with the",
                    "  same selection rather than escalated to a wider suite.",
                    "- Re-running an already-measured head SHA and selection needs an explicit retry reason.",
                ]
            )
            rejected = [audit for audit in audits if not audit.ok]
            if rejected:
                body.append(
                    f"- {len(rejected)} declared command(s) above are rejected by the policy and must be fixed before use."
                )
        else:
            body.append("- none")
        body.append("")
        return "\n".join(body)

@_entrypoint
def _generated_collaboration_guide(config: dict[str, Any]) -> str:
    """Materialize AI_COLLABORATION_GUIDE.md into a worktree when the repo has none.

    The wakeup prompt tells every worker to read AI_COLLABORATION_GUIDE.md first, but
    the file is not tracked anywhere in the repo, so a worker (notably the
    Antigravity/`agy` CLI) burns its whole session hunting for it and never reaches
    the commit/closeout step. Seeding a concise, accurate guide stops the hunt.
    """
    return "\n".join(
        [
            "# AI Collaboration Guide (worker-seeded)",
            "",
            "Generated into this worktree because the supervisor root has no tracked",
            "AI_COLLABORATION_GUIDE.md. It restates the rules already in your wakeup prompt.",
            "",
            "## Workspace",
            "- You run inside an isolated per-task git worktree. It is NOT a staging area.",
            "- Confirm you are on the expected `task/<TASK-ID>` branch; use",
            "  `./delivery_toolchain/git/task_start.sh \"<TASK-ID>\"` if not.",
            "- ai-status.json / current-work.md / ai-activity-log.jsonl are seeded here",
            "  (gitignored); do not edit them by hand — use the status commands.",
            "",
            "## Commit discipline (critical — uncommitted work jams the fleet)",
            "- Commit AND push your work before you finish. A worktree left dirty blocks",
            "  the next dispatch and can deadlock the whole fleet.",
            "- Put one-shot patchers, probes and temporary test scripts in $ORCH_SCRATCH_DIR,",
            "  never at the repository root. The handoff gate rejects unknown untracked files.",
            "- Anchor-commit intermediate states per .orchestrator/skills/worker-anchor-commit.md.",
            "- Commit subject must include the Task ID; body needs LLM-Agent / Task-ID / Reviewer.",
            "- No interactive git (`git add -p/-i`, `git commit --interactive`, `git rebase -i`).",
            "",
            "## Status & closeout",
            "- Update status only via `scripts/ai-status.sh` or `python3 scripts/ai_status.py`",
            "  with your own `AI_NAME`.",
            "- For `owned_finalize_dispatch` / `review_approved`, follow",
            "  .orchestrator/skills/task-closeout-finalization.md before `... done`.",
            "",
        ]
    )

_GITIGNORE_MAGIC = re.compile(r"([\[\]*?])")


def _local_exclude_pattern(rel_path: str) -> str:
    """Render one materialized path as a literal, root-anchored exclude line.

    The leading ``/`` pins the pattern to the repository root and the escapes
    keep glob metacharacters in a filename from widening it, so the entry can
    only ever hide the exact file the supervisor just wrote and hash-verified.
    Excluding the enclosing directory instead would also hide anything the
    worker created there, which is dirt that must still be reported.
    """
    normalized = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    return "/" + _GITIGNORE_MAGIC.sub(r"\\\1", normalized)


@_entrypoint
def materialize_worker_context_files(
    config: dict[str, Any],
    request: DeliveryRequest,
    workspace_path: Path,
) -> list[str]:
    """Seed the context files a worker is told to read into its isolated worktree.

    Task briefs are copied/generated as before. The other canonical references
    (ai-status.json, current-work.md, AI_COLLABORATION_GUIDE.md, ...) live in the
    supervisor root but are gitignored or untracked, so a fresh/reused worktree does
    NOT contain them. Without them the worker — notably the Antigravity/`agy` CLI —
    burns its whole session hunting for files it was instructed to read and never
    reaches the commit/closeout step, leaving uncommitted dirt that then jams the
    reuse lease. Seeding them as untracked copies is safe because the one shared
    cleanliness policy recognizes only the Supervisor-proven seed allowlist; we
    still never overwrite a file the branch already tracks.
    """
    if not request.context_files:
        return []
    status_root = config_path(config, "status_file").parents[0].resolve()
    materialized: list[str] = []
    manifest_entries: list[dict[str, Any]] = []

    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    resolver = TaskResolver(tasks)
    task = resolver.get(request.task_id)
    is_mutating_or_p0 = False
    if task:
        is_mutating_or_p0 = (
            str(task.get("priority") or "").upper() == "P0"
            or bool(task.get("mutates_canonical"))
            or str(task.get("phase") or "").strip() != "Unassigned"
        )

    for rel_context_path in request.context_files:
        rel_value = str(rel_context_path or "").strip().replace("\\", "/")
        if not rel_value or Path(rel_value).is_absolute():
            continue
        valid_dest, destination, dest_err = validate_destination_context_path(rel_value, workspace_path)
        if not valid_dest:
            if is_mutating_or_p0:
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: {dest_err}"
                )
            continue
        is_tracked_rc, _ = _git_output(workspace_path, "ls-files", "--error-unmatch", rel_value)
        if is_tracked_rc == 0:
            # Never clobber any destination tracked by Git; doing so when live source bytes
            # differ from the tracked baseline mutates tracked content and makes the fresh worktree dirty.
            continue
        if ".orchestrator/task-briefs/" in rel_value:
            try:
                validate_task_archive_ambiguity(config, request.task_id)
            except ValueError as err:
                if is_mutating_or_p0:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: {err}"
                    ) from err
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            copied = False
            found_src = None
            for candidate in _task_brief_context_candidates(request.task_id, rel_value):
                source = status_root / candidate
                if not source.exists() or not source.is_file():
                    continue
                existing_text = source.read_text(encoding="utf-8")
                if task and is_task_brief_stale(existing_text, task):
                    break
                _atomic_copy_context_file(source, destination)
                copied = True
                found_src = source
                break
            if not copied:
                try:
                    text = _generated_worker_task_brief(config, request.task_id)
                except ValueError as err:
                    if is_mutating_or_p0:
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: {err}"
                        ) from err
                    continue
                _atomic_write_context_text(destination, text)
                for candidate in _task_brief_context_candidates(request.task_id, rel_value):
                    canon_brief = status_root / candidate
                    try:
                        canon_brief.parent.mkdir(parents=True, exist_ok=True)
                        canon_brief.write_text(text, encoding="utf-8")
                        found_src = canon_brief
                    except OSError:
                        pass
                    break
            brief_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0 and not _is_valid_sha256(brief_hash):
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for task brief '{rel_value}'"
                )
            if not _is_valid_sha256(brief_hash):
                continue
            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str(found_src.resolve()) if found_src else str((status_root / rel_value).resolve()),
                "sha256": brief_hash,
            })
            continue

        source = status_root / rel_value
        valid, norm_path, err_reason = validate_source_doc_path(rel_value, status_root, task=task)
        if not valid and rel_value not in _SEEDABLE_UNTRACKED_CONTEXT and rel_value != "AI_COLLABORATION_GUIDE.md":
            if is_mutating_or_p0:
                raise ValueError(f"Fail-closed on workspace materialization for task {request.task_id}: {err_reason} for '{rel_value}'")
            continue

        always_refresh = rel_value in _SEEDABLE_UNTRACKED_CONTEXT
        if source.exists():
            if destination.exists() and not always_refresh:
                source_hash = _file_or_dir_hash(source)
                dest_hash = _file_or_dir_hash(destination)
                if is_mutating_or_p0:
                    if not _is_valid_sha256(source_hash) or not _is_valid_sha256(dest_hash):
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for '{rel_value}' (canonical sha {source_hash} vs destination sha {dest_hash})"
                        )
                if source_hash and dest_hash and source_hash == dest_hash:
                    materialized.append(rel_value)
                    manifest_entries.append({
                        "relative_path": rel_value,
                        "canonical_source_path": str(source.resolve()),
                        "sha256": source_hash,
                    })
                    continue
                if _is_tracked_in_worktree(workspace_path, rel_value):
                    if is_mutating_or_p0:
                        raise ValueError(
                            f"Fail-closed on workspace materialization for task {request.task_id}: tracked document '{rel_value}' hash mismatch between worktree and canonical source"
                        )
                    continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if source.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    dir_failed = False
                    resolved_status_root = status_root.resolve()
                    for src_item in source.rglob("*"):
                        try:
                            src_item.resolve().relative_to(resolved_status_root)
                        except Exception as err:
                            if is_mutating_or_p0:
                                raise ValueError(
                                    f"Fail-closed on workspace materialization for task {request.task_id}: directory child symlink '{src_item}' points outside status root"
                                ) from err
                            dir_failed = True
                            break

                        rel_child = src_item.relative_to(source)
                        child_rel_value = (Path(rel_value) / rel_child).as_posix()
                        valid_child, child_dest, child_err = validate_destination_context_path(child_rel_value, workspace_path)
                        if not valid_child:
                            if is_mutating_or_p0:
                                raise ValueError(
                                    f"Fail-closed on workspace materialization for task {request.task_id}: {child_err}"
                                )
                            dir_failed = True
                            break
                        if src_item.is_dir():
                            child_dest.mkdir(parents=True, exist_ok=True)
                        elif src_item.is_file():
                            if child_dest.exists() and not always_refresh:
                                src_h = _file_or_dir_hash(src_item)
                                dst_h = _file_or_dir_hash(child_dest)
                                if is_mutating_or_p0 and (not _is_valid_sha256(src_h) or not _is_valid_sha256(dst_h)):
                                    raise ValueError(
                                        f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for directory item '{child_rel_value}'"
                                    )
                                if src_h and dst_h and src_h == dst_h:
                                    continue
                                if _is_tracked_in_worktree(workspace_path, child_rel_value):
                                    if is_mutating_or_p0:
                                        raise ValueError(
                                            f"Fail-closed on workspace materialization for task {request.task_id}: tracked document item '{child_rel_value}' hash mismatch between worktree and canonical source"
                                        )
                                    dir_failed = True
                                    break

                            child_dest.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                _atomic_copy_context_file(src_item, child_dest)
                            except OSError as err:
                                if is_mutating_or_p0:
                                    raise ValueError(
                                        f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy directory source item '{child_rel_value}': {err}"
                                    ) from err
                                dir_failed = True
                                break
                    if dir_failed:
                        continue
                else:
                    try:
                        _atomic_copy_context_file(source, destination)
                    except OSError as err:
                        if is_mutating_or_p0:
                            raise ValueError(
                                f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy source document '{rel_value}': {err}"
                            ) from err
                        continue
            except OSError as err:
                if is_mutating_or_p0:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: failed to copy source document '{rel_value}': {err}"
                    ) from err
                continue

            source_hash = _file_or_dir_hash(source)
            final_dest_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0:
                if not _is_valid_sha256(source_hash) or not _is_valid_sha256(final_dest_hash):
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for '{rel_value}' (canonical sha {source_hash} vs destination sha {final_dest_hash})"
                    )
                if source_hash != final_dest_hash:
                    raise ValueError(
                        f"Fail-closed on workspace materialization for task {request.task_id}: final source and destination tree mismatch for '{rel_value}' (canonical sha {source_hash} vs destination sha {final_dest_hash})"
                    )
            else:
                if not _is_valid_sha256(source_hash) or not _is_valid_sha256(final_dest_hash) or source_hash != final_dest_hash:
                    continue

            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str(source.resolve()),
                "sha256": source_hash,
            })
        elif rel_value == "AI_COLLABORATION_GUIDE.md" and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_context_text(destination, _generated_collaboration_guide(config))
            guide_hash = _file_or_dir_hash(destination)
            if is_mutating_or_p0 and not _is_valid_sha256(guide_hash):
                raise ValueError(
                    f"Fail-closed on workspace materialization for task {request.task_id}: unable to establish valid 64-hex SHA256 integrity hash for generated '{rel_value}'"
                )
            if not _is_valid_sha256(guide_hash):
                continue
            materialized.append(rel_value)
            manifest_entries.append({
                "relative_path": rel_value,
                "canonical_source_path": str((status_root / rel_value).resolve()),
                "sha256": guide_hash,
            })

    if materialized:
        request.metadata["materialized_context_files"] = materialized
        request.metadata["materialized_source_manifest"] = manifest_entries
        request.metadata["source_manifest"] = manifest_entries

    rc, out = _git_output(workspace_path, "rev-parse", "--git-path", "info/exclude")
    if rc == 0 and out.strip():
        try:
            exclude_path = Path(out.strip())
            if not exclude_path.is_absolute():
                exclude_path = (workspace_path / exclude_path).resolve()
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            existing_exclude = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            lines_to_add = [
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "current-work.md",
                "ai-activity-log.jsonl",
                ".orchestrator/task-briefs/",
                ".orchestrator/reviews/",
            ]
            # The fixed list above only covers the canonical references that
            # every worker gets. A task's own `source_docs` land wherever the
            # board points them -- ai-task-archive/tasks/<id>.json, for one --
            # and the supervisor writing them is what makes the worktree
            # untracked-dirty, so task_finalize fails closed on the
            # orchestrator's own copy and the task can never be submitted.
            # Only manifest entries whose paths are NOT already covered by
            # the fixed prefix list are added. Files under .orchestrator/
            # are covered by the existing prefixes or gitignored state and
            # must remain visible to workspace regression tests.
            _COVERED_PREFIXES = (
                "AI_COLLABORATION_GUIDE.md",
                "ai-status.json",
                "current-work.md",
                "ai-activity-log.jsonl",
                ".orchestrator/",
            )
            for entry in manifest_entries:
                rel = str(entry.get("relative_path") or "").strip().replace("\\", "/").lstrip("/")
                if not rel:
                    continue
                if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in _COVERED_PREFIXES):
                    continue
                pattern = _local_exclude_pattern(rel)
                if pattern:
                    lines_to_add.append(pattern)
            new_lines = [line for line in lines_to_add if line not in existing_exclude.splitlines()]
            if new_lines:
                with open(exclude_path, "a", encoding="utf-8") as ef:
                    if existing_exclude and not existing_exclude.endswith("\n"):
                        ef.write("\n")
                    for line in new_lines:
                        ef.write(f"{line}\n")
        except OSError:
            pass
    return materialized

@_entrypoint
def _orchestrator_materialized_paths(
    state: dict[str, Any],
    request,
    workspace_task_id: str,
) -> list[str]:
    """Paths the orchestrator seeds into the worker workspace itself.

    These are the context files a worker is told to read, written by the supervisor
    rather than by the worker. A repository that version-controls them (ODay Plus's own)
    never sees them as dirt; a repository that does not (any cross-repository workspace)
    sees them as untracked, so without this allowlist the orchestrator's own writes deny
    the next lease and the task can never be dispatched into that workspace again.
    """
    paths: list[str] = []
    leases = (state.get("worker_worktrees") or {}).get("leases", {}) or {}
    lease = leases.get(workspace_task_id)
    if isinstance(lease, dict):
        previous = lease.get("materialized_context_files")
        if isinstance(previous, list):
            paths.extend(str(value) for value in previous)
    return paths


@_entrypoint
def prepare_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    target_agent: str | None,
    base_cache: dict[str, WorkerBaseResolution | str] | None = None,
) -> tuple[bool, str | None]:
    settings = worker_worktree_settings(config)
    if not settings.get("root_configured", False):
        return False, "Cannot lease isolated worker worktree: worker_worktrees.root is not configured."
    workspace_task_id = worker_workspace_task_id(request)
    if not workspace_task_id:
        return False, "Cannot lease isolated worker worktree: execution request has no task id."

    # The dispatch event carries a progress snapshot, not the task record: it
    # has no `branch` and no `repository`, so resolving either from it silently
    # fell back to a derived name and the default repository. Read the canonical
    # record instead, and keep the snapshot only as the fallback.
    task_metadata = request.metadata.get("task")
    task_record = canonical_task_record(
        config,
        workspace_task_id,
        task_metadata if isinstance(task_metadata, dict) else None,
    )
    binding = worker_task_repository_binding(config, task_record)
    repo_root = binding.root
    repo_root_source = binding.source if binding.resolved else (binding.error or binding.source)
    if repo_root is None:
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: "
            f"{repo_root_source}. Refusing to create the task branch in the "
            "supervisor repository, which would publish it to the wrong origin."
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease",
                "task_id": request.task_id,
                "workspace_task_id": workspace_task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "message": message,
                "repo_root_source": repo_root_source,
            },
        )
        return False, message

    # Use the registry's first id for this checkout.  Alias ids such as
    # `pantheon` and `odayplus` may name the same root, but must share a single
    # worktree namespace and a single base snapshot.
    from multi_repo_registry import canonical_repository_id_for_root

    repository_id = canonical_repository_id_for_root(
        config,
        repo_root,
        fallback=binding.repo_id or "pantheon",
    )
    base, base_error = resolve_worker_base(
        repo_root,
        repository_id=repository_id,
        default_branch=binding.default_branch,
        base_cache=base_cache,
        network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
    )
    if base is None:
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: "
            f"failed to resolve fresh registry base ({base_error or 'unknown error'})."
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_base",
                "task_id": request.task_id,
                "workspace_task_id": workspace_task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "repository_id": repository_id,
                "default_branch": binding.default_branch,
                "message": message,
            },
        )
        return False, message

    branch = worker_task_branch(config, workspace_task_id, task_record)
    worktree_path = worker_task_worktree_path(
        config,
        workspace_task_id,
        settings,
        repo_root,
        repository_id=repository_id,
    )
    reused = False
    base_relation = "created_from_exact_base"
    materialized_paths = _orchestrator_materialized_paths(state, request, workspace_task_id)
    review_submission = task_record.get("review_submission") if isinstance(task_record, dict) else None
    required_review_head = (
        str(review_submission.get("remote_sha") or "").strip()
        if str(request.reason or "") == REASON_REVIEW_READY and isinstance(review_submission, dict)
        else None
    )

    # A task branch has exactly one registered lease.  `reuse_existing=false`
    # never had a safe meaning for a single branch (Git cannot check it out in
    # two worktrees), so always discover and validate the existing binding.
    existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
    if existing:
        worktree_path = existing
        reused = True
        owner_continuation = False
        refresh_ok, refresh_status = _refresh_reused_worker_worktree(
            repo_root,
            worktree_path,
            base.sha,
            branch,
            network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
            materialized_paths=materialized_paths,
            required_head=required_review_head,
        )
        if not refresh_ok and _is_skipped_dirty_worktree(refresh_status):
            allowed, continuation_detail = sealed_owner_continuation_allowed(
                config,
                state,
                request,
                task_record,
                target_agent=target_agent,
                worktree_path=worktree_path,
                branch=branch,
                materialized_paths=materialized_paths,
            )
            if allowed:
                owner_continuation = True
                refresh_ok = True
                refresh_status = "sealed_owner_continuation"
                base_relation = "sealed_owner_continuation"
                request.metadata["worktree_continuation"] = "sealed_owner_dirt"
                request.message = (
                    "CLOSEOUT CONTINUATION: your prior worker exited after leaving the exact "
                    f"task worktree dirty ({continuation_detail}). You alone may finish this "
                    "same checkout. Commit deliverables, delete scratch, or move temporary "
                    "files to $ORCH_SCRATCH_DIR; do not hand this worktree to a reviewer.\n\n"
                    + request.message
                )
        write_activity_log(
            config,
            {
                "type": "worker_worktree_refreshed",
                "task_id": request.task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "workspace_branch": branch,
                "workspace_path": str(worktree_path),
                "base_sha": base.sha,
                "base_ref": base.remote_ref,
                "refresh_ok": refresh_ok,
                "refresh_status": refresh_status,
            },
        )
        if not refresh_ok:
            if _is_skipped_dirty_worktree(refresh_status):
                reason = (
                    f"has {_dirty_worktree_detail(refresh_status)}. Preserve and commit "
                    "the task-owned work before dispatch."
                )
            else:
                reason = f"failed the fail-closed refresh policy ({refresh_status})."
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"reused worktree {worktree_path} {reason}"
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "refresh_status": refresh_status,
                },
            )
            if _is_skipped_dirty_worktree(refresh_status):
                # Preserve here too, not only at worker death.
                #
                # `preserve_dead_worker_worktree` runs from the four paths that
                # declare a worker dead. This refusal fires for dirt none of them
                # ever saw: a worker that exited cleanly without committing, or
                # dirt that predates the current lease entirely. Measured on
                # 2026-08-23, four worktrees sat blocked between four and seven
                # hours holding 3608, 336, 44 and 22 uncommitted files, and
                # `.orchestrator/worktree-dirt-backups/` had an entry for none of
                # them -- every one had to be copied out by hand before anything
                # could touch the worktree.
                #
                # This does not change the refusal, and the helper leaves the
                # worktree wholly untouched, so the dirt stays visible exactly as
                # 2026-08-21 intended. It only means the copy exists before
                # someone decides to clear it.
                #
                # Once per distinct dirt-state rather than once per retry: the
                # block entry resets precisely when `refresh_status` changes, so
                # reuse that as the "this is new dirt" signal.
                lease_task_id = str(request.task_id or workspace_task_id)
                bucket = state.get("worker_worktree_lease_blocks") or {}
                prior = bucket.get(normalize_agent_id(lease_task_id) or lease_task_id)
                if not (isinstance(prior, dict) and prior.get("refresh_status") == refresh_status):
                    try:
                        _quarantine_and_preserve_dirty_worktree(
                            config,
                            state,
                            worktree_path,
                            lease_task_id,
                            expected_branch=branch,
                            trigger="lease_refused_dirty_worktree",
                            owning_repo_root=repo_root,
                        )
                    except Exception as error:  # never let preservation break the refusal
                        # The reporting must not be able to raise either: this
                        # runs on a path whose whole job is to refuse cleanly,
                        # and a config without an activity-log path would
                        # otherwise turn a best-effort backup into a crash.
                        try:
                            write_activity_log(
                                config,
                                {
                                    "type": "worker_worktree_lease_preserve_failed",
                                    "task_id": lease_task_id,
                                    "workspace_path": str(worktree_path),
                                    "refresh_status": refresh_status,
                                    "message": f"Could not preserve dirty worktree at lease refusal: {error}",
                                },
                            )
                        except Exception:
                            pass
            _record_worktree_lease_block(
                config,
                state,
                task_id=str(request.task_id or workspace_task_id),
                refresh_status=refresh_status,
                message=message,
                worktree_path=worktree_path,
                materialized_paths=materialized_paths,
            )
            return False, message
        _clear_worktree_lease_block(state, str(request.task_id or workspace_task_id))
        if not owner_continuation:
            clear_unsealed_worker_handoff(state, str(request.task_id or workspace_task_id))
        if refresh_status.startswith("review_head_pinned_at_"):
            base_relation = "review_head_pinned"
            request.metadata.update(
                {
                    "review_head_immutable": True,
                    "review_head": required_review_head,
                    "worktree_refresh_status": refresh_status,
                }
            )
        elif refresh_status.startswith("ff_to_"):
            base_relation = "fast_forwarded"
        elif refresh_status.startswith("base_present_at_"):
            base_relation = "contains_base"
        elif refresh_status.startswith("base_advance_rebase_required:"):
            base_relation = "diverged"
            if str(request.reason or "").strip() == "owned_finalize_dispatch":
                request.metadata.update(
                    {
                        "approved_head_immutable": True,
                        "base_advance_deferred_to_merge_queue": True,
                        "worktree_refresh_status": refresh_status,
                    }
                )
            else:
                base_advance_prompt = (
                    "BASE ADVANCE REQUIRED BEFORE REVIEW OR MERGE: this task branch "
                    f"diverges from {base.remote_ref} ({base.sha[:12]}). Compose the current "
                    "base through the normal task workflow, resolve and verify it, then push "
                    "normally. Do not reset, discard, or overwrite task history.\n\n"
                )
                request.message = base_advance_prompt + request.message
                request.metadata.update(
                    {
                        "base_advance_required": True,
                        "worktree_refresh_status": refresh_status,
                    }
                )

    if not reused:
        if _branch_checked_out_in_root(repo_root, branch):
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"branch {branch} is currently checked out in repository root {repo_root}. "
                "Move that root back to dev or finish that root task branch first."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message
        branch_preexisted = (
            _git_ref_exists(repo_root, f"refs/heads/{branch}")
            or _git_ref_exists(repo_root, f"refs/remotes/origin/{branch}")
        )
        creation_sha = base.sha
        if required_review_head:
            resolved_review_head = _git_commit_oid(repo_root, required_review_head)
            if not resolved_review_head or resolved_review_head != required_review_head:
                refresh_status = "review_head_unavailable"
                message = (
                    f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                    "submitted review head is unavailable locally; refusing to create a "
                    "review checkout at the cycle base."
                )
                _record_worktree_lease_block(
                    config,
                    state,
                    task_id=str(request.task_id or workspace_task_id),
                    refresh_status=refresh_status,
                    message=message,
                    worktree_path=worktree_path,
                    materialized_paths=materialized_paths,
                )
                return False, message
            creation_sha = resolved_review_head
        created, error = _create_worker_worktree(repo_root, worktree_path, branch, creation_sha)
        if not created:
            message = error or f"Failed to create worker worktree for {workspace_task_id}."
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                },
            )
            return False, message
        if branch_preexisted or required_review_head:
            # A branch without a currently registered checkout is still an
            # existing task branch, not a new task.  Apply the same exact-SHA
            # relation check before handing it to a worker.
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                repo_root,
                worktree_path,
                base.sha,
                branch,
                network_timeout_seconds=float(settings["git_network_timeout_seconds"]),
                materialized_paths=materialized_paths,
                required_head=required_review_head,
            )
            if not refresh_ok:
                message = (
                    f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                    f"attached task branch failed the fail-closed refresh policy ({refresh_status})."
                )
                _record_worktree_lease_block(
                    config,
                    state,
                    task_id=str(request.task_id or workspace_task_id),
                    refresh_status=refresh_status,
                    message=message,
                    worktree_path=worktree_path,
                    materialized_paths=materialized_paths,
                )
                return False, message
            if refresh_status.startswith("review_head_pinned_at_"):
                base_relation = "review_head_pinned"
                request.metadata.update(
                    {
                        "review_head_immutable": True,
                        "review_head": required_review_head,
                        "worktree_refresh_status": refresh_status,
                    }
                )
            elif refresh_status.startswith("ff_to_"):
                base_relation = "fast_forwarded"
            elif refresh_status.startswith("base_present_at_"):
                base_relation = "contains_base"
            elif refresh_status.startswith("base_advance_rebase_required:"):
                base_relation = "diverged"
                request.metadata.update(
                    {
                        "base_advance_required": True,
                        "worktree_refresh_status": refresh_status,
                    }
                )

    # The workspace is the task's own repository; the status root is not. It
    # names the fleet that owns ai-status.json, the approval queue and the
    # permission rules -- always the pantheon checkout. Setting it to the task's
    # repository was invisible while every task lived here, because the two
    # coincided. For a cross-repository task they diverge, and the wake prompt
    # then tells the worker to run `$PANTHEON_STATUS_ROOT/scripts/ai-status.sh`
    # inside a repository that has no `scripts/` at all.
    fleet_root = config_path(config, "status_file").parents[0]
    request.metadata.update(
        {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree_path),
            "workspace_branch": branch,
            "status_root": str(fleet_root),
            "repository_id": repository_id,
            "base_ref": base.remote_ref,
            "base_sha": base.sha,
            "base_relation": base_relation,
        }
    )
    materialized_context_files = materialize_worker_context_files(config, request, worktree_path)
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    leases[workspace_task_id] = {
        "task_id": request.task_id,
        "workspace_task_id": workspace_task_id,
        "branch": branch,
        "path": str(worktree_path),
        "status_root": str(fleet_root),
        "repo_root_source": repo_root_source,
        "repository_id": repository_id,
        "base_ref": base.remote_ref,
        "base_sha": base.sha,
        "base_relation": base_relation,
        "last_queue_event_id": queue_event_id,
        "last_target_agent": target_agent,
        "last_used_at": utc_now(),
        "materialized_context_files": materialized_context_files,
        "materialized_source_manifest": request.metadata.get("materialized_source_manifest", []),
        "source_manifest": request.metadata.get("source_manifest", []),
    }
    write_activity_log(
        config,
        {
            "type": "worker_worktree_reused" if reused else "worker_worktree_allocated",
            "task_id": request.task_id,
            "workspace_task_id": workspace_task_id,
            "target_agent": target_agent,
            "queue_event_id": queue_event_id,
            "workspace_branch": branch,
            "workspace_path": str(worktree_path),
            "status_root": str(fleet_root),
            "repository_id": repository_id,
            "base_ref": base.remote_ref,
            "base_sha": base.sha,
            "base_relation": base_relation,
        },
    )
    return True, None

@_entrypoint
def worker_worktree_housekeeping_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktree_housekeeping")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "tick_interval_seconds": int(settings.get("tick_interval_seconds", 600) or 0),
        "max_removals_per_tick": int(settings.get("max_removals_per_tick", 5)),
        # How long a fully-refused reclaim may stay quiet before it is logged
        # again. Only rate-limits the repeat: a change in the refusal mix still
        # logs immediately.
        "stall_log_interval_seconds": int(settings.get("stall_log_interval_seconds", 3600) or 0),
    }

@_entrypoint
def _scan_process_paths_in_root(base_root: Path) -> set[Path]:
    """Return resolved paths under base_root mentioned in any live process cmdline."""
    base_str = str(base_root)
    referenced: set[Path] = set()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return referenced
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        if int(name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if base_str not in cmdline:
            continue
        for tok in cmdline.split(" "):
            if tok.startswith(base_str):
                try:
                    referenced.add(Path(tok).resolve())
                except OSError:
                    pass
    return referenced

# How many unmanaged worktree paths a single scan reports. The count is exact;
# this only bounds the sample, so one pathological host cannot turn a routine
# status record into an unbounded blob.
_UNMANAGED_SAMPLE_LIMIT = 20


@_entrypoint
def _classify_processes_in_worktree(worktree_path: Path) -> tuple[list[int], int]:
    """Split processes referencing a worktree into (orphan pids, still-parented count).

    "Orphan" here means PPid == 1: the process that started it is gone and init
    adopted it. That is the judgment that makes reaping safe, and it is the one
    that survives a child which called `setsid()`.

    Process groups cannot answer this. The pgserver instances that kept
    worktrees pinned on this host each ran as their own session leader
    (`pid == pgid == sid`), so they had already escaped any group the worker
    was killed through -- twelve of them outlived their workers by up to three
    days. Reparenting to init is the one signal they could not escape.

    A non-zero second element means something still has a living parent, so the
    worktree is genuinely in use and must not be touched.
    """
    orphans: list[int] = []
    still_parented = 0
    wt_str = str(worktree_path)

    def references_worktree(cmdline: str) -> bool:
        """True when the path appears as a whole path, not as a prefix.

        A bare `in` test reads a process running in `<path>-old` as a holder of
        `<path>` -- the same prefix collision that made a string-prefix
        managed-root check unsound. Requiring a boundary on both sides keeps
        `--dir=<path>` style arguments matching while rejecting a longer
        sibling name. This only labels a refusal that has already been decided,
        so a miss degrades the diagnosis, never the safety of the skip.
        """
        index = cmdline.find(wt_str)
        while index != -1:
            end = index + len(wt_str)
            before_ok = index == 0 or cmdline[index - 1] in " =:,\"'"
            after_ok = end >= len(cmdline) or cmdline[end] in (" ", os.sep)
            if before_ok and after_ok:
                return True
            index = cmdline.find(wt_str, index + 1)
        return False

    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return ([], 1)  # Fail closed: unreadable /proc means "assume in use".
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit() or int(name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if not references_worktree(cmdline):
            continue
        ppid = None
        try:
            for line in (entry / "status").read_text(errors="ignore").splitlines():
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):
            still_parented += 1  # Unreadable state is not evidence of an orphan.
            continue
        if ppid == 1:
            orphans.append(int(name))
        else:
            still_parented += 1
    return (orphans, still_parented)


@_entrypoint
def prune_orphan_worktrees(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    base_cache: dict[str, WorkerBaseResolution | str] | None = None,
) -> bool:
    """Remove finished worker worktrees whose branches are merged and tree is clean."""
    settings = worker_worktree_housekeeping_settings(config)
    if not settings["enabled"]:
        return False

    interval = settings["tick_interval_seconds"]
    bucket = state.setdefault("worker_worktree_housekeeping", {})
    if interval > 0:
        last_at = bucket.get("last_run_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(UTC)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_run_at"] = utc_now()

    worktree_settings = worker_worktree_settings(config)
    if not worktree_settings.get("root_configured", False):
        return False
    base_root = _worker_worktree_base_root(config, worktree_settings)
    if not base_root.exists():
        return False

    # Only a worker that can still USE its workspace holds a claim on it.
    #
    # This used to claim for every record in state["workers"] regardless of
    # status, so a `completed` or `failed` worker kept its worktree reserved for
    # as long as its record survived -- which is until `max_worker_history`
    # (200) evicts it. Observed on a live host: 200 worker records, 142
    # completed / 45 failed / 12 superseded, and every single reclaimable
    # worktree held by one of them. The pruner ran on schedule and removed
    # nothing.
    #
    # `test_skips_worktree_claimed_by_active_worker` already names the intended
    # rule; its fixture just never set a status, so it could not tell the two
    # readings apart. Dropping a finished worker's claim is safe because the
    # guards that follow are the ones carrying the real risk: an unmerged branch
    # or detached HEAD, a dirty tree, and a live process are all still refused.
    claimed_paths: set[Path] = set()
    for worker in state.get("workers", {}).values():
        if str(worker.get("status") or "").strip().lower() not in ACTIVE_WORKER_STATUSES:
            continue
        wp = worker.get("workspace_path")
        if not wp:
            continue
        try:
            claimed_paths.add(Path(str(wp)).resolve())
        except OSError:
            continue

    live_paths = _scan_process_paths_in_root(base_root)

    max_removals = max(0, settings["max_removals_per_tick"])
    removed: list[str] = []
    # Every guard below ends in a bare `continue`, and the only log this
    # function ever wrote was the success one. "Examined 69 worktrees and
    # refused all 69" and "there was nothing to examine" were therefore the
    # same silence, which is how a pruner that had never reclaimed a single
    # worktree kept looking healthy while the host filled to 99%. Counting the
    # refusals costs nothing and is the difference between a stalled reclaim
    # being visible and being invisible.
    examined = 0
    skipped: dict[str, int] = {}
    repos_skipped: dict[str, int] = {}
    # A configured cap of 0 means "reclaim nothing this tick", which is the
    # capped state before the scan even starts.
    capped = max_removals <= 0
    orphan_reports: list[tuple[str, list[int]]] = []
    unmanaged = 0
    unmanaged_paths: list[str] = []
    # Pruning has the same authority boundary as leasing: each distinct local
    # checkout contributes its registry default branch and freshly fetched
    # immutable remote SHA.  Never use a developer's local dev/master/main as
    # evidence that task work is merged.
    from multi_repo_registry import iter_local_repositories

    active_base_cache = base_cache if base_cache is not None else {}
    for repo in iter_local_repositories(config):
        repo_root = repo.get("resolved_local_path")
        repo_id = str(repo.get("id") or "").strip()
        default_branch = str(repo.get("default_branch") or "").strip()
        if not isinstance(repo_root, Path) or not repo_id:
            repos_skipped["unusable"] = repos_skipped.get("unusable", 0) + 1
            continue
        base_refs: list[str] = []
        merged_branches: set[str] = set()
        # `max_removals_per_tick` bounds destruction, not accounting. Reaching
        # it used to `break` out of both loops, which stopped the counters
        # dead: a capped tick would report "3 unmanaged" on a host with 68 and
        # call the number exact. That is the same under-reporting this function
        # exists to remove, so the scan now runs to the end and only the
        # expensive work stops -- no base resolution (a network fetch), no
        # per-worktree git status, and above all no further removal.
        if not capped:
            base, _base_error = resolve_worker_base(
                repo_root,
                repository_id=repo_id,
                default_branch=default_branch,
                base_cache=active_base_cache,
                network_timeout_seconds=float(worktree_settings["git_network_timeout_seconds"]),
            )
            if base is None:
                # Counted separately from the per-worktree refusals. A repo
                # whose base will not resolve is skipped whole, and folding
                # that into the worktree tally reads as "4 of the 68 examined
                # worktrees" when it actually means "4 repos, contributing an
                # unknown number of worktrees, were never examined at all".
                repos_skipped["base_unresolved"] = repos_skipped.get("base_unresolved", 0) + 1
                continue
            base_refs = [base.sha]
            merged_proc = subprocess.run(
                ["git", "branch", "--merged", base.sha, "--list", "task/*"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if merged_proc.returncode == 0:
                for line in merged_proc.stdout.splitlines():
                    name = line.strip().lstrip("*").strip()
                    if name:
                        merged_branches.add(name)

        for record in _git_worktree_records(repo_root):
            wt_value = record.get("worktree")
            if not wt_value:
                continue
            try:
                wt_path = Path(wt_value).resolve()
            except (OSError, ValueError):
                # Unresolvable, so it can be placed neither inside nor outside
                # the managed root. Counted as an examined refusal rather than
                # as foreign: attributing an unreadable record to the side this
                # function does not own would quietly shrink the denominator in
                # "reclaimed 0 of N".
                examined += 1
                skipped["path_unresolvable"] = skipped.get("path_unresolvable", 0) + 1
                continue
            if not wt_path.is_relative_to(base_root):
                # Registered in this repository, but outside the root this
                # function manages -- someone ran `git worktree add` somewhere
                # else. Reclaiming it is not this function's call: it did not
                # create it and cannot know who is using it.
                #
                # Staying silent about it was still wrong. On the host that
                # prompted this work, 68 of 178 registered worktrees sat
                # outside the managed root and held 8.3G, and because this
                # branch skipped before any counter, they were invisible to
                # every reclaim report -- not even counted as refused. Naming
                # them is what lets an operator find space the pruner is not
                # allowed to take.
                #
                # Membership is decided on the RESOLVED path, by path
                # components. A `str.startswith` test on the raw record got
                # this wrong in both directions: it claimed a sibling root
                # named `<root>-archive` as managed, and it accepted a symlink
                # under the root whose target lives somewhere else entirely.
                # Both end with the pruner deciding removal for a directory it
                # does not own.
                unmanaged += 1
                if len(unmanaged_paths) < _UNMANAGED_SAMPLE_LIMIT:
                    unmanaged_paths.append(wt_value)
                continue
            # Only worktrees under the managed root are this function's
            # business, so `examined` counts from here: anything earlier is a
            # foreign checkout, not a reclaim candidate we declined.
            examined += 1
            if capped:
                # Still counted, deliberately not evaluated: this tick has
                # spent its removal budget, and saying so is more useful than
                # a truncated scan that looks like a complete one.
                skipped["deferred_removal_cap"] = skipped.get("deferred_removal_cap", 0) + 1
                continue
            if wt_path in claimed_paths:
                skipped["claimed_by_active_worker"] = skipped.get("claimed_by_active_worker", 0) + 1
                continue
            if any(live.is_relative_to(wt_path) or wt_path.is_relative_to(live) for live in live_paths):
                # No ACTIVE worker claims this worktree, yet something inside is
                # still running. Two very different situations look identical
                # here, and conflating them is why twelve test databases
                # outlived their workers by up to three days while the pruner
                # reported a uniform "busy": either a real tenant is at work, or
                # a settled worker leaked a child that now pins the worktree
                # forever. Reparenting to init (PPid == 1) tells them apart.
                orphan_pids, still_parented = _classify_processes_in_worktree(wt_path)
                if orphan_pids and not still_parented:
                    skipped["live_process_orphaned"] = skipped.get("live_process_orphaned", 0) + 1
                    orphan_reports.append((str(wt_path), sorted(orphan_pids)))
                else:
                    skipped["live_process"] = skipped.get("live_process", 0) + 1
                continue
            branch = _worktree_record_branch(record)
            if branch:
                if branch not in merged_branches:
                    skipped["branch_not_merged"] = skipped.get("branch_not_merged", 0) + 1
                    continue
            elif not _detached_head_is_merged(repo_root, wt_path, base_refs):
                skipped["detached_head_not_merged"] = skipped.get("detached_head_not_merged", 0) + 1
                continue
            status_proc = subprocess.run(
                ["git", "-C", str(wt_path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
            )
            if status_proc.returncode != 0:
                skipped["status_query_failed"] = skipped.get("status_query_failed", 0) + 1
                continue
            if status_proc.stdout.strip():
                skipped["dirty"] = skipped.get("dirty", 0) + 1
                continue
            remove_proc = subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "remove", str(wt_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if remove_proc.returncode == 0:
                removed.append(str(wt_path))
                if len(removed) >= max_removals:
                    capped = True
            else:
                skipped["remove_failed"] = skipped.get("remove_failed", 0) + 1

    # Durable per-tick reclaim accounting. This is cheap, it overwrites rather
    # than accumulates, and it is what makes "the pruner is running but
    # reclaiming nothing" a readable state instead of an inferred one.
    bucket["last_scan"] = {
        "at": bucket["last_run_at"],
        "examined": examined,
        "removed": len(removed),
        "skipped": dict(sorted(skipped.items())),
        # Repository-level refusals, and only for repositories this tick
        # actually evaluated for removal. Once the removal budget is spent no
        # base is resolved, so a repo scanned after that point contributes its
        # worktrees to the counts above without ever reaching a verdict here.
        "repos_skipped": dict(sorted(repos_skipped.items())),
        # The tick spent its `max_removals_per_tick` budget. Counting continues
        # past this point, so `examined` and `unmanaged` stay exact; what a
        # capped tick does not carry is a removal verdict for every worktree it
        # counted -- those sit in `skipped.deferred_removal_cap`.
        "capped_at_max_removals": capped,
        # Registered worktrees living outside the managed root, which this
        # function may not reclaim. NOT a garbage count: the main checkout and
        # every live supervisor runtime legitimately appear here. It answers
        # "what else does this repository have on the same disk that reclaim
        # will never touch", which on the live host was 8.3G of abandoned
        # scratch checkouts sitting alongside those legitimate ones. The sample
        # is capped; `unmanaged` is the true count.
        "unmanaged": unmanaged,
        "unmanaged_sample": list(unmanaged_paths),
        # Worktrees held only by init-reparented leftovers. Unlike the other
        # refusals this one is actionable without waiting for anybody: the
        # owning worker is already gone, so these pids and paths are what an
        # operator needs to reclaim the space.
        "orphaned_worktrees": [
            {"path": path, "pids": pids} for path, pids in orphan_reports
        ],
    }

    if removed:
        bucket.pop("stall_since", None)
        bucket.pop("last_stall_log_at", None)
        write_activity_log(
            config,
            {
                "type": "worktree_pruned",
                "message": f"Pruned {len(removed)} orphan worker worktree(s): {', '.join(removed)}",
            },
        )
        return True

    if examined or repos_skipped:
        # Reclaimed nothing. One such tick is ordinary -- every worktree may
        # legitimately be busy. A run of them means reclaim has stopped
        # working, and that is the condition that silently filled this host's
        # disk. Log it on a slow cadence, and again whenever the refusal mix
        # changes, so the signal survives without flooding.
        #
        # `repos_skipped` alone is enough to report: if every repository fails
        # to resolve, `examined` is 0 and this is the only thing that would
        # distinguish a broken pruner from an empty one.
        stall_interval = max(0, settings["stall_log_interval_seconds"])
        signature = ",".join(
            [f"{k}={v}" for k, v in sorted(skipped.items())]
            + [f"repo:{k}={v}" for k, v in sorted(repos_skipped.items())]
        )
        bucket.setdefault("stall_since", bucket["last_run_at"])
        last_logged_at = _parse_iso_utc(str(bucket.get("last_stall_log_at") or ""))
        elapsed_ok = True
        if last_logged_at is not None and stall_interval > 0:
            elapsed_ok = (datetime.now(UTC) - last_logged_at).total_seconds() >= stall_interval
        if elapsed_ok or bucket.get("last_stall_signature") != signature:
            bucket["last_stall_log_at"] = bucket["last_run_at"]
            bucket["last_stall_signature"] = signature
            try:
                write_activity_log(
                    config,
                    {
                        "type": "worktree_prune_stalled",
                        "message": (
                            f"Reclaimed 0 of {examined} candidate worktree(s) since "
                            f"{bucket.get('stall_since')}; refusals: {signature or 'none'}"
                        ),
                        "examined": examined,
                        "skipped": dict(sorted(skipped.items())),
                        "repos_skipped": dict(sorted(repos_skipped.items())),
                        "orphaned_worktrees": [
                            {"path": path, "pids": pids} for path, pids in orphan_reports
                        ],
                        "unmanaged": unmanaged,
                    },
                )
            except (KeyError, OSError):
                # This log is diagnostic; `bucket["last_scan"]` above already
                # carries the same counts durably. A caller with no activity-log
                # path configured must still get its reclaim decision, so a
                # missing sink degrades the diagnosis rather than the function.
                pass
        return False

    bucket.pop("stall_since", None)
    return False

class QuarantineOutcome(NamedTuple):
    """Whether dirt was preserved, and -- when it was not -- why not.

    This helper has thirteen distinct ways to decline and used to report all of
    them as a bare ``False``. Every caller could see that preservation had not
    happened and none could see whether that was routine ("the worktree was
    already clean") or a defect ("this worktree is checked out of a repository
    the caller did not expect"). Both of those were the same value, so an
    operator reading the activity log after DPF-SRC-RIS-NLSC-001 blocked five
    times could not tell that quarantine had been misrouted rather than simply
    having nothing to do.

    It stays falsy when it refuses, so ``bool(...)`` callers and the existing
    ``return_value=False`` test doubles keep working unchanged; the reason is
    additive.
    """

    preserved: bool
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.preserved


def _quarantine_refused(reason: str, detail: str = "") -> QuarantineOutcome:
    return QuarantineOutcome(False, reason, detail)


@_entrypoint
def preserve_dead_worker_worktree(
    config: dict[str, Any],
    state: dict[str, Any],
    worker: dict[str, Any],
    *,
    task: dict[str, Any] | None = None,
    trigger: str = "worker_death",
) -> QuarantineOutcome:
    """Back up a dead worker's uncommitted work when it is declared dead.

    Preservation used to happen only on the *next* lease attempt for the task,
    which is the one moment it is least likely to run. A worker that dies before
    committing leaves no remote branch; the fail-closed refresh policy then
    refuses the lease because the branch is missing from the remote -- so the
    only worker that could have committed the work is the one refused entry, and
    the quarantine that would have saved it sits behind that same refusal. Four
    to five occurrences cost 4459+ lines that had to be recovered by hand.

    Death is the correct moment: the process is gone, nothing is writing to the
    worktree, and the dirt is still on disk. Running here makes the later lease
    attempt a redundancy rather than the only chance.

    This deliberately does NOT clean the worktree. The backup is a copy, and the
    worktree's dirtiness is what keeps the worktree pruner from reclaiming it
    before anyone has looked at what was lost. Preserve, then leave it alone.

    Returns the `QuarantineOutcome` so the caller can log why nothing was saved;
    it never raises, because a worker being settled must not be blocked by a
    failure to back up its scratch space.
    """
    workspace_path = str(worker.get("workspace_path") or "")
    task_id = str(worker.get("task_id") or "")
    if not workspace_path:
        return _quarantine_refused("worker_had_no_workspace")
    try:
        record = task if task is not None else canonical_task_record(config, task_id)
        repo_root, _repo_source = worker_task_repo_root(config, record)
        outcome = _quarantine_and_preserve_dirty_worktree(
            config,
            state,
            workspace_path,
            task_id,
            expected_branch=worker_task_branch(config, task_id, record),
            # The dying worker's own record is still in `state` with an active
            # status. Naming its run id is what stops the helper mistaking it
            # for a live owner and refusing to touch its own worktree.
            run_id=str(worker.get("run_id") or "") or None,
            trigger=trigger,
            owning_repo_root=repo_root,
        )
    except Exception as error:  # pragma: no cover - settlement must not be blocked
        return _quarantine_refused("preserve_raised", str(error))

    write_activity_log(
        config,
        {
            "type": "worker_death_worktree_preserved" if outcome else "worker_death_worktree_not_preserved",
            "task_id": task_id,
            "worker_run_id": worker.get("run_id"),
            "provider": worker.get("provider"),
            "trigger": trigger,
            "workspace_path": workspace_path,
            "reason": getattr(outcome, "reason", ""),
            "detail": getattr(outcome, "detail", ""),
            "message": (
                f"Preserved uncommitted work for {task_id or 'unknown task'} when its worker was declared dead."
                if outcome
                else (
                    f"No uncommitted work preserved for {task_id or 'unknown task'} at worker death: "
                    f"{getattr(outcome, 'reason', '') or 'unspecified'}."
                )
            ),
        },
    )
    if outcome and _dead_owner_continuation_eligible(config, worker, record):
        handoff_seal = seal_worker_handoff(config, state, worker, record)
        if (
            not handoff_seal.accepted
            and handoff_seal.reason == "owner_dirty"
            and handoff_seal.head_sha
            and handoff_seal.dirt_fingerprint
        ):
            record_unsealed_worker_handoff(
                config,
                state,
                worker,
                record,
                handoff_seal,
            )
    return outcome


@_entrypoint
def _quarantine_and_preserve_dirty_worktree(
    config: dict[str, Any],
    state: dict[str, Any],
    worktree_path: Path | str | None,
    task_id: str | None,
    *,
    expected_branch: str | None = None,
    run_id: str | None = None,
    trigger: str = "",
    owning_repo_root: Path | str | None = None,
) -> QuarantineOutcome:
    """Quarantine and preserve dirty worktree state as an immutable backup without destructive reset/clean/stash or modifying the worktree.

    Returns a falsy `QuarantineOutcome` carrying the reason it declined, or a
    truthy one iff it inventoried tracked/staged/untracked dirt, wrote verified immutable
    backup to `.orchestrator/worktree-dirt-backups/`, leaving the original worktree wholly untouched.
    """
    if worktree_path is None:
        return _quarantine_refused("no_worktree_path")
    # Two different roots doing two different jobs, conflated until 2026-08-20.
    #
    # `fleet_root` owns the backup directory: the orchestrator's state lives in
    # the Pantheon checkout no matter whose code is being preserved.
    #
    # `repo_root` is the repository this worktree is supposed to belong to, and
    # it is what the git-identity check below compares against. Deriving it from
    # the status file made it Pantheon for every task, so a worktree in a
    # sibling repository compared two different repositories, failed the check
    # and returned False -- silently, every time. That disabled quarantine for
    # every oday-data-platform task, which is where the DPF work lives:
    # DPF-SRC-RIS-NLSC-001 and DPF-SRC-OSM-TDX-001 each blocked five times with
    # `skipped_dirty_worktree` and escalated, with no backup ever written,
    # because the recovery that exists for exactly that status could not run.
    fleet_root = config_path(config, "status_file").parents[0].resolve()
    try:
        repo_root = Path(owning_repo_root).expanduser().resolve() if owning_repo_root else fleet_root
    except (OSError, TypeError, ValueError):
        repo_root = fleet_root
    try:
        worktree_path = Path(worktree_path).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return _quarantine_refused("unreadable_worktree_path")
    if worktree_path == repo_root or not (worktree_path / ".git").exists():
        return _quarantine_refused("not_a_worktree")

    wt_git_rc, wt_git_dir = _git_output(worktree_path, "rev-parse", "--git-dir")
    repo_git_rc, _repo_git_dir = _git_output(repo_root, "rev-parse", "--git-dir")
    top_rc, top_level = _git_output(worktree_path, "rev-parse", "--show-toplevel")
    worktree_common_rc, worktree_common = _git_output(worktree_path, "rev-parse", "--git-common-dir")
    repo_common_rc, repo_common = _git_output(repo_root, "rev-parse", "--git-common-dir")
    try:
        resolved_top = Path(top_level).resolve()
        wt_gd = Path(wt_git_dir) if Path(wt_git_dir).is_absolute() else (worktree_path / wt_git_dir)
        wt_cd = Path(worktree_common) if Path(worktree_common).is_absolute() else (wt_gd / worktree_common)
        repo_cd = Path(repo_common) if Path(repo_common).is_absolute() else (repo_root / repo_common)

        resolved_worktree_common = wt_cd.resolve()
        resolved_repo_common = repo_cd.resolve()
    except (OSError, RuntimeError, ValueError):
        return _quarantine_refused("unreadable_git_metadata")
    if (
        top_rc != 0
        or worktree_common_rc != 0
        or repo_common_rc != 0
        or wt_git_rc != 0
        or repo_git_rc != 0
        or resolved_top != worktree_path
        or resolved_worktree_common != resolved_repo_common
    ):
        return _quarantine_refused("worktree_belongs_to_another_repository")

    branch_rc, current_branch = _git_output(worktree_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_rc != 0 or not current_branch:
        return _quarantine_refused("detached_head")
    if expected_branch and current_branch != expected_branch:
        return _quarantine_refused("branch_mismatch")
    if _git_operation_in_progress(worktree_path):
        return _quarantine_refused("git_operation_in_progress")

    local_head = _git_commit_oid(worktree_path, "HEAD")
    if not local_head:
        return _quarantine_refused("no_local_head")

    active_statuses = active_worker_statuses(config)
    for other in state.get("workers", {}).values():
        if not isinstance(other, dict):
            continue
        other_status = str(other.get("status") or "")
        if other_status in active_statuses:
            other_run_id = str(other.get("run_id") or "")
            if run_id and other_run_id == run_id:
                continue
            other_task_id = str(other.get("task_id") or "")
            other_path = str(other.get("workspace_path") or "")
            if (task_id and other_task_id == task_id) or (other_path and Path(other_path).resolve() == worktree_path):
                return _quarantine_refused("worktree_in_use_by_active_worker")

    status_proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
        check=False,
    )
    if status_proc.returncode != 0:
        return _quarantine_refused("status_unreadable")
    if not status_proc.stdout:
        # A clean worktree is the ordinary case at worker death, not a failure
        # to read it. Folding both into "status_unreadable" made the routine
        # outcome indistinguishable from a real one, and the single time it
        # fired it was read as lost work.
        return _quarantine_refused("worktree_clean")

    raw_entries = [e for e in status_proc.stdout.split(b"\0") if e]
    if not raw_entries:
        return _quarantine_refused("nothing_to_preserve")

    inventory_files: list[dict[str, Any]] = []
    idx = 0
    while idx < len(raw_entries):
        item = raw_entries[idx]
        code = item[:2].decode("utf-8", errors="replace")
        path_bytes = item[3:] if len(item) > 3 else b""
        idx += 1
        orig_path_bytes = None
        if len(code) >= 2 and (code[0] in ("R", "C") or code[1] in ("R", "C")):
            if idx < len(raw_entries):
                orig_path_bytes = raw_entries[idx]
                idx += 1

        rel_path = os.fsdecode(path_bytes)
        orig_path = os.fsdecode(orig_path_bytes) if orig_path_bytes is not None else None
        if not rel_path:
            continue
        full_p = worktree_path / rel_path

        is_symlink = os.path.islink(full_p) or (hasattr(full_p, "is_symlink") and full_p.is_symlink())
        symlink_target: str | None = None
        sha256_val: str | None = None
        is_file = False
        is_dir = False

        if is_symlink:
            try:
                symlink_target = os.readlink(full_p)
            except OSError:
                symlink_target = None
        elif full_p.exists():
            if full_p.is_file():
                is_file = True
                try:
                    h = hashlib.sha256()
                    with open(full_p, "rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
                    sha256_val = h.hexdigest()
                except OSError:
                    sha256_val = None
            elif full_p.is_dir():
                is_dir = True

        inventory_files.append({
            "path": rel_path,
            "orig_path": orig_path,
            "status_code": code,
            "sha256": sha256_val,
            "is_symlink": is_symlink,
            "symlink_target": symlink_target,
            "is_file": is_file,
            "is_dir": is_dir,
        })

    try:
        backup_dir = fleet_root / ".orchestrator" / "worktree-dirt-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stamp = f"{now_str}_{uuid.uuid4().hex[:8]}"
        task_backup_dir = backup_dir / f"{_task_id_slug(task_id)}-{stamp}"
        task_backup_dir.mkdir(parents=True, exist_ok=False)

        manifest = {
            "task_id": task_id,
            "branch": current_branch,
            "head_sha": local_head,
            "trigger": trigger,
            "run_id": run_id,
            "timestamp": now_str,
            "files": inventory_files,
        }
        manifest_path = task_backup_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        staged_proc = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=worktree_path, capture_output=True, check=False)
        if staged_proc.returncode != 0:
            raise RuntimeError("failed to capture staged diff")
        (task_backup_dir / "staged.patch").write_bytes(staged_proc.stdout)

        unstaged_proc = subprocess.run(["git", "diff", "--binary"], cwd=worktree_path, capture_output=True, check=False)
        if unstaged_proc.returncode != 0:
            raise RuntimeError("failed to capture unstaged diff")
        (task_backup_dir / "unstaged.patch").write_bytes(unstaged_proc.stdout)

        untracked_base = task_backup_dir / "untracked"
        for file_entry in inventory_files:
            rel_p = file_entry["path"]
            src_path = worktree_path / rel_p
            if file_entry.get("status_code", "").startswith("?") or not src_path.exists():
                if file_entry.get("is_symlink") and file_entry.get("symlink_target"):
                    dst_path = untracked_base / rel_p
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    if dst_path.exists() or os.path.islink(dst_path):
                        dst_path.unlink()
                    os.symlink(file_entry["symlink_target"], dst_path)
                elif file_entry.get("is_file") and file_entry.get("sha256"):
                    if src_path.exists() and src_path.is_file():
                        dst_path = untracked_base / rel_p
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        h_check = hashlib.sha256()
                        with open(dst_path, "rb") as f_chk:
                            while ch := f_chk.read(65536):
                                h_check.update(ch)
                        if h_check.hexdigest() != file_entry["sha256"]:
                            raise RuntimeError(f"backup checksum mismatch for {rel_p}")

        checksums: dict[str, str] = {}
        for b_root, _, b_files in os.walk(task_backup_dir):
            for bf in b_files:
                if bf == "backup_checksums.sha256":
                    continue
                fp = Path(b_root) / bf
                rel_bp = fp.relative_to(task_backup_dir).as_posix()
                if fp.is_symlink() or os.path.islink(fp):
                    target = os.readlink(fp)
                    checksums[rel_bp] = "symlink:" + hashlib.sha256(target.encode("utf-8")).hexdigest()
                else:
                    h_b = hashlib.sha256()
                    with open(fp, "rb") as f_b:
                        while ch := f_b.read(65536):
                            h_b.update(ch)
                    checksums[rel_bp] = h_b.hexdigest()
        (task_backup_dir / "backup_checksums.sha256").write_text(json.dumps(checksums, indent=2), encoding="utf-8")

    except Exception as exc:
        return _quarantine_refused("backup_write_failed", str(exc))

    write_activity_log(
        config,
        {
            "type": "worker_worktree_preserved",
            "task_id": task_id,
            "run_id": run_id,
            "trigger": trigger,
            "workspace_path": str(worktree_path),
            "backup_dir": str(task_backup_dir),
            "head_sha": local_head,
            "message": (
                f"Quarantined dirty worktree for {task_id} ({trigger or 'cleanup'}); "
                f"backup saved to {task_backup_dir}."
            ),
        },
    )
    return QuarantineOutcome(True, "preserved")
