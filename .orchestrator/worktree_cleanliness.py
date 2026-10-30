#!/usr/bin/env python3
"""The single authority for deciding whether a worker worktree is handoff-clean.

This module deliberately has no Supervisor dependency.  The delivery shell
script, workspace leasing, and worker settlement therefore ask exactly the
same question of Git instead of maintaining three subtly different allowlists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# --- task-scoped protected paths -------------------------------------------
#
# A task record may carry ``protected_paths``: a literal, repo-relative,
# deny-only list.  It never grants anything.  Every caller that creates,
# refreshes, seeds, launches, resumes or verifies a task workspace asks this
# module, so there is exactly one interpretation of the list.

PROTECTED_PATHS_FIELD = "protected_paths"
BWRAP_BINARY = "/usr/bin/bwrap"
PROTECTED_EXECUTION_PROBE_TOKEN = "ORCH_PROTECTED_EXECUTION_PROBE_OK"
_PATTERN_CHARS = frozenset("*?[]")
# Hooks Git runs by itself while the Supervisor checks out, merges or reads
# status.  Commit-time hooks run inside the worker's own sandbox instead.
_SUPERVISOR_SIDE_HOOKS = (
    "post-checkout",
    "post-merge",
    "post-rewrite",
    "post-index-change",
    "reference-transaction",
    "fsmonitor-watchman",
)
_UNSAFE_TREE_MODES = frozenset({"120000", "160000"})


class ProtectedPathPolicyError(ValueError):
    """A ``protected_paths`` value that cannot be enforced literally."""


class ProtectedExecutionUnavailable(RuntimeError):
    """A protected task cannot be launched under a proven OS boundary."""


def normalize_protected_paths(raw: object) -> tuple[str, ...]:
    """Validate a literal repo-relative list; never guess what prose meant."""
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ProtectedPathPolicyError("protected_paths must be a list of literal repo-relative paths")
    normalized: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise ProtectedPathPolicyError(f"protected_paths entry is not a string: {entry!r}")
        value = entry.strip().rstrip("/")
        parts = value.split("/")
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or "\0" in value
            or any(char in _PATTERN_CHARS for char in value)
            or any(part in {"", ".", ".."} for part in parts)
            or ".git" in parts
        ):
            raise ProtectedPathPolicyError(f"protected_paths entry is not a literal repo-relative path: {entry!r}")
        normalized.add(value)
    return tuple(sorted(normalized))


def task_protected_paths(task: object) -> tuple[str, ...]:
    if not isinstance(task, dict):
        return ()
    return normalize_protected_paths(task.get(PROTECTED_PATHS_FIELD))


def protected_paths_reduction(previous: object, proposed: object) -> tuple[str, ...]:
    """Recorded entries the proposed list would no longer cover."""
    before = normalize_protected_paths(previous)
    after = normalize_protected_paths(proposed)
    return tuple(
        path for path in before
        if not any(path == kept or path.startswith(f"{kept}/") for kept in after)
    )


def protected_path_conflict(relative_path: object, protected_paths: Iterable[str]) -> str | None:
    """The protected entry ``relative_path`` equals, lies under, or contains."""
    protected = tuple(protected_paths or ())
    if not protected:
        return None
    value = str(relative_path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.strip("/")
    if value in {"", "."} or ".." in value.split("/"):
        # The repository root, or a path that may climb anywhere, includes
        # every protected entry.
        return protected[0]
    for path in protected:
        if value == path or value.startswith(f"{path}/") or path.startswith(f"{value}/"):
            return path
    return None


def _ancestors(relative_path: str) -> list[str]:
    parts = relative_path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


@dataclass(frozen=True)
class ProtectedMetadataInspection:
    ok: bool
    reason: str
    detail: str = ""


def _git_metadata(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except (OSError, ValueError):
        return None


def _tree_entry(cwd: Path, commit: str, relative_path: str) -> tuple[str, str] | None | bool:
    """Exact (mode, type) of one tree entry; ``False`` when unreadable."""
    proc = _git_metadata(cwd, "ls-tree", "-z", "--full-tree", commit, "--", relative_path)
    if proc is None or proc.returncode != 0:
        return False
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        header, _, name = record.partition(b"\t")
        fields = header.split()
        if len(fields) != 3:
            return False
        if os.fsdecode(name) == relative_path:
            return fields[0].decode("ascii", "replace"), fields[1].decode("ascii", "replace")
    return None


def _index_entry_modes(cwd: Path, relative_path: str) -> set[str] | None:
    """Stage modes recorded for exactly ``relative_path`` (not its children)."""
    # The exclusion keeps Git from listing any child entry back to us.
    proc = _git_metadata(
        cwd, "ls-files", "--stage", "-z", "--", f":(literal){relative_path}", f":(exclude,glob){relative_path}/**"
    )
    if proc is None or proc.returncode != 0:
        return None
    modes: set[str] = set()
    for record in proc.stdout.split(b"\0"):
        header, _, name = record.partition(b"\t")
        if record and os.fsdecode(name) == relative_path:
            modes.add(header.split()[0].decode("ascii", "replace"))
    return modes


def _unsafe_git_configuration(cwd: Path) -> str | None:
    proc = _git_metadata(
        cwd, "config", "-z", "--get-regexp", r"^(filter\..*\.(smudge|clean|process)|core\.fsmonitor|core\.hookspath)$"
    )
    if proc is None or proc.returncode not in {0, 1}:
        return "git configuration is unreadable"
    hooks_path: str | None = None
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        key, _, value = os.fsdecode(record).partition("\n")
        key = key.lower()
        if key.startswith("filter."):
            return f"content filter {key} would read protected bodies"
        if key == "core.fsmonitor" and value.strip().lower() not in {"", "false", "0", "no", "off"}:
            return "core.fsmonitor would run a hook during status"
        if key == "core.hookspath":
            hooks_path = value.strip()
    if hooks_path:
        hooks_dir = Path(os.path.expanduser(hooks_path))
        if not hooks_dir.is_absolute():
            hooks_dir = cwd / hooks_dir
    else:
        located = _git_metadata(cwd, "rev-parse", "--git-path", "hooks")
        if located is None or located.returncode != 0:
            return "git hooks directory cannot be located"
        hooks_dir = Path(os.fsdecode(located.stdout.strip()))
        if not hooks_dir.is_absolute():
            hooks_dir = cwd / hooks_dir
    for name in _SUPERVISOR_SIDE_HOOKS:
        try:
            metadata = (hooks_dir / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return f"git hook {name} metadata is unreadable"
        if stat.S_ISLNK(metadata.st_mode) or (stat.S_ISREG(metadata.st_mode) and metadata.st_mode & 0o111):
            return f"git hook {name} would run during checkout/merge/status"
    return None


def _worktree_metadata_violation(worktree_path: Path, protected: tuple[str, ...]) -> str | None:
    for path in protected:
        for candidate in [*_ancestors(path), path]:
            try:
                metadata = (worktree_path / candidate).lstat()
            except FileNotFoundError:
                break
            except OSError:
                return f"worktree metadata for {candidate} is unreadable"
            if stat.S_ISLNK(metadata.st_mode):
                return f"worktree path {candidate} is a symlink escape for protected {path}"
            is_target = candidate == path
            if not is_target and not stat.S_ISDIR(metadata.st_mode):
                return f"worktree ancestor {candidate} of protected {path} is not a directory"
    return None


def inspect_protected_git_metadata(
    cwd: Path | str,
    commit: str,
    protected_paths: Iterable[str],
    *,
    worktree_path: Path | str | None = None,
    check_index: bool = False,
) -> ProtectedMetadataInspection:
    """Prove, from immutable metadata only, that Git may touch ``commit``.

    Runs before any status, hash, copy, checkout, merge or restore.  Only the
    exact protected entries and their ancestors are named: there is no
    recursive listing of, and no read from, a protected body.
    """
    protected = normalize_protected_paths(list(protected_paths or ()))
    if not protected:
        return ProtectedMetadataInspection(True, "no_protected_paths")
    root = Path(cwd)
    requested = str(commit or "").strip()
    resolved = _git_metadata(root, "rev-parse", "--verify", "--quiet", f"{requested}^{{commit}}")
    if not requested or resolved is None or resolved.returncode != 0:
        return ProtectedMetadataInspection(False, "unverifiable_commit", requested)
    commit_sha = resolved.stdout.decode("ascii", "replace").strip()
    if len(requested) in {40, 64} and commit_sha != requested:
        return ProtectedMetadataInspection(False, "unverifiable_commit", requested)
    unsafe = _unsafe_git_configuration(root)
    if unsafe:
        return ProtectedMetadataInspection(False, "unsafe_git_execution", unsafe)
    for path in protected:
        for ancestor in _ancestors(path):
            entry = _tree_entry(root, commit_sha, ancestor)
            if entry is False:
                return ProtectedMetadataInspection(False, "unreadable_metadata", ancestor)
            if entry is None:
                break
            if entry[0] != "040000":
                return ProtectedMetadataInspection(
                    False, "ancestor_escape", f"{ancestor} ({entry[0]}) contains protected {path}"
                )
        entry = _tree_entry(root, commit_sha, path)
        if entry is False:
            return ProtectedMetadataInspection(False, "unreadable_metadata", path)
        if entry is not None and entry[0] in _UNSAFE_TREE_MODES:
            return ProtectedMetadataInspection(False, "protected_entry_escape", f"{path} ({entry[0]})")
        if check_index:
            for candidate in [*_ancestors(path), path]:
                modes = _index_entry_modes(root, candidate)
                if modes is None:
                    return ProtectedMetadataInspection(False, "unreadable_metadata", f"index {candidate}")
                if modes & _UNSAFE_TREE_MODES or (candidate != path and modes):
                    return ProtectedMetadataInspection(
                        False, "index_escape", f"index entry {candidate} shadows protected {path}"
                    )
    if worktree_path is not None:
        violation = _worktree_metadata_violation(Path(worktree_path), protected)
        if violation:
            return ProtectedMetadataInspection(False, "worktree_escape", violation)
    return ProtectedMetadataInspection(True, "metadata_safe", commit_sha)


def context_path_protection_error(
    root: Path | str,
    relative_path: str,
    protected_paths: Iterable[str],
) -> str | None:
    """Refuse a context source/destination before it is hashed, listed or copied."""
    protected = tuple(protected_paths or ())
    if not protected:
        return None
    conflict = protected_path_conflict(relative_path, protected)
    if conflict:
        return f"context path {relative_path!r} overlaps protected path {conflict!r}"
    base = Path(root)
    try:
        resolved_root = base.resolve()
    except (OSError, RuntimeError):
        return f"context root {base} is unreadable"
    current = base
    for part in str(relative_path).replace("\\", "/").strip("/").split("/"):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return f"context path metadata for {relative_path!r} is unreadable"
        if stat.S_ISLNK(metadata.st_mode):
            try:
                target = current.resolve().relative_to(resolved_root).as_posix()
            except (OSError, RuntimeError, ValueError):
                return f"context path {relative_path!r} has a symlink leaving its root"
            conflict = protected_path_conflict(target, protected)
            if conflict:
                return f"context path {relative_path!r} is a symlink alias of protected path {conflict!r}"
    return None


def protected_selection_conflict(token: str, protected_paths: Iterable[str], *, cwd: Path | str | None = None) -> str | None:
    """Why a command token would select protected content, if it would."""
    protected = tuple(protected_paths or ())
    if not protected:
        return None
    value = str(token or "").strip().strip("'\"")
    if "=" in value and value.startswith("-"):
        value = value.split("=", 1)[1]
    value = value.split("::", 1)[0]
    if not value or value.startswith("-"):
        return None
    if any(char in _PATTERN_CHARS for char in value):
        prefix = value[: min(value.index(char) for char in _PATTERN_CHARS if char in value)]
        prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        conflict = protected_path_conflict(prefix, protected)
        return f"pattern {value!r} may select protected {conflict!r}" if conflict else None
    if value.startswith("/"):
        if cwd is not None:
            try:
                relative = Path(value).relative_to(Path(cwd).resolve()).as_posix()
            except ValueError:
                relative = None
            if relative is not None:
                conflict = protected_path_conflict(relative, protected)
                return f"path {value!r} selects protected {conflict!r}" if conflict else None
        padded = f"{value.rstrip('/')}/"
        for path in protected:
            if f"/{path}/" in padded:
                return f"absolute path {value!r} names protected {path!r}"
        return None
    if "/" not in value and value not in {".", ".."}:
        # A bare word is a path only if it names a protected entry or one of
        # its ancestors; ordinary arguments (``-q``, ``bootstrap``) are not.
        for path in protected:
            if path == value or path.startswith(f"{value}/"):
                return f"path {value!r} selects protected {path!r}"
        return None
    conflict = protected_path_conflict(value, protected)
    return f"path {value!r} selects protected {conflict!r}" if conflict else None


# --- OS enforcement (Bubblewrap) -------------------------------------------


@dataclass(frozen=True)
class ProtectedExecutionPlan:
    protected_paths: tuple[str, ...]
    cwd: Path
    masks: tuple[tuple[str, Path], ...]
    bwrap: str = BWRAP_BINARY

    def wrap(self, command: list[str], *, extra_masks: Iterable[tuple[str, Path]] = ()) -> list[str]:
        argv = [self.bwrap, "--die-with-parent", "--dev-bind", "/", "/"]
        for kind, target in [*self.masks, *extra_masks]:
            if kind == "file":
                argv.extend(["--ro-bind", "/dev/null", str(target)])
            else:
                argv.extend(["--tmpfs", str(target), "--remount-ro", str(target)])
        argv.extend(["--chdir", str(self.cwd), "--", *command])
        return argv


def _registered_worktrees(cwd: Path) -> list[Path]:
    proc = _git_metadata(cwd, "worktree", "list", "--porcelain", "-z")
    if proc is None or proc.returncode != 0:
        raise ProtectedExecutionUnavailable("registered worktrees cannot be listed")
    roots: list[Path] = []
    for record in proc.stdout.split(b"\0"):
        if record.startswith(b"worktree "):
            roots.append(Path(os.fsdecode(record[len(b"worktree "):])))
    return roots


def plan_protected_execution(
    cwd: Path | str,
    protected_paths: Iterable[str],
    *,
    alias_roots: Iterable[Path | str] = (),
    bwrap: str = BWRAP_BINARY,
) -> ProtectedExecutionPlan:
    """Mask each protected path in every checkout of the same repository.

    Aliases are every registered worktree (the fleet root, the runtime and
    other task checkouts included) plus explicitly named roots.  A symlinked
    ancestor anywhere refuses: masking its target would be guesswork.
    """
    protected = normalize_protected_paths(list(protected_paths or ()))
    if not protected:
        raise ProtectedExecutionUnavailable("no protected paths to enforce")
    workspace = Path(cwd).resolve()
    roots: list[Path] = [workspace]
    for root in [*_registered_worktrees(workspace), *(Path(item) for item in alias_roots)]:
        try:
            resolved = Path(root).resolve()
        except (OSError, RuntimeError):
            raise ProtectedExecutionUnavailable(f"alias root {root} cannot be resolved") from None
        if resolved not in roots and resolved.is_dir():
            roots.append(resolved)
    masks: list[tuple[str, Path]] = []
    for root in roots:
        violation = _worktree_metadata_violation(root, protected)
        if violation:
            raise ProtectedExecutionUnavailable(f"{root}: {violation}")
        for path in protected:
            target = root / path
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                if root != workspace:
                    continue
                # Deny-only also covers creation: an empty read-only mount
                # keeps the worker from writing new protected content.
                target.mkdir(parents=True, exist_ok=True)
                metadata = target.lstat()
            except OSError as error:
                raise ProtectedExecutionUnavailable(f"{target}: {error}") from None
            if stat.S_ISDIR(metadata.st_mode):
                masks.append(("dir", target))
            elif stat.S_ISREG(metadata.st_mode):
                masks.append(("file", target))
            else:
                raise ProtectedExecutionUnavailable(f"{target} is neither a directory nor a regular file")
    return ProtectedExecutionPlan(protected_paths=protected, cwd=workspace, masks=tuple(masks), bwrap=bwrap)


_PROBE_SOURCE = r"""
import json, os, sys
spec = json.loads(sys.argv[1])
errors = []
status = {}
for line in open('/proc/self/status', encoding='utf-8'):
    key, _, value = line.partition(':')
    status[key] = value.strip()
if status.get('NoNewPrivs') != '1':
    errors.append('no_new_privs')
if int(status.get('CapEff', 'f') or 'f', 16) != 0:
    errors.append('effective_capabilities')
if os.getuid() != spec['uid'] or os.getgid() != spec['gid']:
    errors.append('identity')
uid_map = open('/proc/self/uid_map', encoding='utf-8').read().split()
if uid_map[:3] != [str(spec['uid']), str(spec['uid']), '1']:
    errors.append('uid_map')
try:
    open(spec['canary_file'], 'rb').close()
    errors.append('canary_readable')
except OSError:
    pass
try:
    open(os.path.join(spec['canary_dir'], 'write-probe'), 'xb').close()
    errors.append('canary_writable')
except OSError:
    pass
def unescape(value):
    return value.encode().decode('unicode_escape').encode('latin-1').decode('utf-8', 'surrogateescape')
mounts = set()
for line in open('/proc/self/mountinfo', encoding='utf-8'):
    mounts.add(unescape(line.split()[4]))
for target in spec['targets']:
    if target not in mounts:
        errors.append('unmasked:' + target)
if errors:
    print(json.dumps(errors))
    sys.exit(3)
print(spec['token'])
"""


def probe_protected_execution(plan: ProtectedExecutionPlan, *, timeout: float = 20.0) -> None:
    """Prove the real boundary now, with a harmless canary, or refuse.

    The probe runs the exact mount plan plus one synthetic canary mask.  It
    never opens a protected body: masks are proven by the kernel's mount
    table, and readability/writability only against the canary.
    """
    with tempfile.TemporaryDirectory(prefix="orch-protected-probe-") as scratch:
        canary_dir = Path(scratch) / "canary"
        canary_dir.mkdir()
        canary_file = canary_dir / "canary.txt"
        canary_file.write_text("synthetic canary\n", encoding="utf-8")
        spec = {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "canary_dir": str(canary_dir),
            "canary_file": str(canary_file),
            "targets": [str(target) for _kind, target in plan.masks],
            "token": PROTECTED_EXECUTION_PROBE_TOKEN,
        }
        argv = plan.wrap(
            [sys.executable, "-I", "-c", _PROBE_SOURCE, json.dumps(spec)],
            extra_masks=[("dir", canary_dir)],
        )
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError:
            raise ProtectedExecutionUnavailable(f"sandbox binary {plan.bwrap} is not installed") from None
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProtectedExecutionUnavailable(f"sandbox probe did not complete: {error}") from None
        leaked = (canary_dir / "write-probe").exists()
        if proc.returncode != 0 or proc.stdout.strip() != PROTECTED_EXECUTION_PROBE_TOKEN or leaked:
            detail = (proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}")[-400:]
            raise ProtectedExecutionUnavailable(f"sandbox probe failed: {detail}")


def protected_execution_argv(
    command: list[str],
    *,
    cwd: Path | str,
    protected_paths: Iterable[str],
    alias_roots: Iterable[Path | str] = (),
    bwrap: str | None = None,
) -> list[str]:
    """The one way a protected task's native process is started."""
    binary = bwrap or BWRAP_BINARY
    if not Path(binary).is_absolute():
        binary = shutil.which(binary) or binary
    plan = plan_protected_execution(cwd, protected_paths, alias_roots=alias_roots, bwrap=binary)
    probe_protected_execution(plan)
    return plan.wrap(list(command))


@dataclass(frozen=True)
class WorktreeInspection:
    """A deterministic worktree cleanliness result.

    ``owner_dirty`` is intentionally conservative: any tracked/staged change,
    or any untracked path not proven to have been materialized by the
    Supervisor, belongs to the owner rather than a future reviewer.
    """

    kind: str
    entries: tuple[tuple[str, str], ...]
    blocking_entries: tuple[tuple[str, str], ...]
    fingerprint: str
    detail: str

    @property
    def handoff_clean(self) -> bool:
        return self.kind in {"clean", "orchestrator_seed_only"}


def parse_porcelain_entries(porcelain_status: str | bytes) -> list[tuple[str, str]]:
    """Parse porcelain-v1 status, including NUL-safe rename records."""
    entries: list[tuple[str, str]] = []
    if isinstance(porcelain_status, bytes):
        raw_entries = [entry for entry in porcelain_status.split(b"\0") if entry]
        index = 0
        while index < len(raw_entries):
            item = raw_entries[index]
            code = item[:2].decode("utf-8", errors="replace")
            path_bytes = item[3:] if len(item) > 3 else b""
            index += 1
            if len(code) >= 2 and (code[0] in {"R", "C"} or code[1] in {"R", "C"}):
                if index < len(raw_entries):
                    index += 1
            path = os.fsdecode(path_bytes)
            if path:
                entries.append((code, path))
        return entries

    for line in porcelain_status.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        body = line[3:] if len(line) > 3 else line.strip()
        path = body.split(" -> ")[-1].strip().strip('"')
        if path:
            entries.append((code, path))
    return entries


def normalize_materialized_paths(paths: Iterable[object] | None) -> frozenset[str]:
    normalized: set[str] = set()
    for raw in paths or ():
        value = str(raw or "").replace("\\", "/").strip().strip("/")
        if not value or value.startswith("/") or ".." in value.split("/"):
            continue
        normalized.add(value)
    return frozenset(normalized)


def is_safe_context_destination(
    worktree_path: Path,
    relative_path: str,
    protected_paths: Iterable[str] = (),
) -> bool:
    """Do not allow a symlinked, non-regular, escaping or protected seed destination."""
    value = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not value or Path(value).is_absolute() or ".." in value.split("/"):
        return False
    if protected_path_conflict(value, protected_paths):
        return False
    try:
        root = worktree_path.resolve()
        parts = Path(value).parts
        current = root
        for index, part in enumerate(parts):
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                # A missing final destination is safe to seed atomically. A
                # later path component cannot exist without its parent, so we
                # can stop once the first component is absent.
                return index == len(parts) - 1
            if stat.S_ISLNK(metadata.st_mode):
                return False
            resolved = current.resolve()
            if resolved != root and root not in resolved.parents:
                return False
            is_final = index == len(parts) - 1
            if not is_final and not stat.S_ISDIR(metadata.st_mode):
                return False
            if is_final and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                # A seed must not overwrite/read through a hard link into a
                # tracked or otherwise unrelated file.
                return False
            if is_final and not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def is_reusable_dirt_entry(
    code: str,
    path: str,
    worktree_path: Path | None,
    allowlist: frozenset[str],
) -> bool:
    if code.strip() != "??":
        return False
    normalized = path.replace("\\", "/").strip()
    trimmed = normalized.strip("/")
    # A familiar filename or .orchestrator prefix is not proof of origin: an
    # owner could leave arbitrary work there. Only exact recorded materialized
    # paths (or a recorded context directory) are safe to ignore at handoff.
    reusable = trimmed in allowlist or any(trimmed.startswith(f"{entry}/") for entry in allowlist)
    return bool(reusable and (worktree_path is None or is_safe_context_destination(worktree_path, path)))


def blocking_dirt_entries(
    entries: Iterable[tuple[str, str]],
    *,
    worktree_path: Path | None = None,
    materialized_paths: Iterable[object] | None = None,
) -> list[tuple[str, str]]:
    allowlist = normalize_materialized_paths(materialized_paths)
    return [
        (code, path)
        for code, path in entries
        if not is_reusable_dirt_entry(code, path, worktree_path, allowlist)
    ]


def describe_dirt_entries(entries: Iterable[tuple[str, str]], limit: int = 5) -> str:
    staged = 0
    unstaged = 0
    untracked = 0
    names: list[str] = []
    for code, path in entries:
        padded = (code + "  ")[:2]
        if padded.strip() == "??":
            untracked += 1
        elif padded.strip() == "!!":
            continue
        elif padded[0] not in {" ", "?", "!"}:
            staged += 1
        else:
            unstaged += 1
        names.append(path)
    if not names:
        return "no reportable changes"
    parts: list[str] = []
    if staged:
        parts.append(f"{staged} staged")
    if unstaged:
        parts.append(f"{unstaged} unstaged tracked")
    if untracked:
        parts.append(f"{untracked} untracked")
    shown = ", ".join(sorted(names)[:limit])
    if len(names) > limit:
        shown += f", +{len(names) - limit} more"
    noun = "change" if len(names) == 1 else "changes"
    return f"{len(names)} dirty {noun} ({', '.join(parts)}): {shown}"


def inspect_porcelain(
    porcelain_status: str | bytes,
    *,
    worktree_path: Path | None = None,
    materialized_paths: Iterable[object] | None = None,
) -> WorktreeInspection:
    raw = porcelain_status.encode("utf-8") if isinstance(porcelain_status, str) else porcelain_status
    entries = tuple(parse_porcelain_entries(porcelain_status))
    blocking = tuple(
        blocking_dirt_entries(
            entries,
            worktree_path=worktree_path,
            materialized_paths=materialized_paths,
        )
    )
    if not entries:
        kind = "clean"
        detail = "clean"
    elif blocking:
        kind = "owner_dirty"
        detail = describe_dirt_entries(blocking)
    else:
        kind = "orchestrator_seed_only"
        detail = describe_dirt_entries(entries)
    return WorktreeInspection(
        kind=kind,
        entries=entries,
        blocking_entries=blocking,
        fingerprint=hashlib.sha256(raw).hexdigest(),
        detail=detail,
    )


def _worktree_fingerprint(
    worktree_path: Path,
    inspection: WorktreeInspection,
    porcelain_status: bytes,
    protected_paths: Iterable[str] = (),
) -> str:
    """Hash the actual dirty state, not only the porcelain path list.

    Porcelain deliberately does not contain content hashes.  A path-only
    fingerprint would therefore accept a same-path edit made after an owner
    exited, which is precisely when we need to keep the continuation narrow.
    """
    digest = hashlib.sha256()
    digest.update(porcelain_status)
    for code, path in inspection.entries:
        digest.update(code.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if protected_path_conflict(path, protected_paths):
            # Bind protected dirt by lstat metadata only; its body is never read.
            try:
                metadata = (worktree_path / path).lstat()
                digest.update(
                    f"protected:{metadata.st_mode:o}:{metadata.st_size}:{metadata.st_mtime_ns}\0".encode("ascii")
                )
            except OSError:
                digest.update(b"protected-unreadable-or-absent\0")
            continue
        if not is_safe_context_destination(worktree_path, path):
            digest.update(b"unsafe-path\0")
            continue
        candidate = worktree_path / path
        try:
            metadata = candidate.lstat()
        except OSError:
            # Deleted paths still have their Git status hashed above.  Include
            # a distinct marker so an unreadable path cannot masquerade as an
            # empty regular file.
            digest.update(b"unreadable-or-absent\0")
            continue
        if stat.S_ISREG(metadata.st_mode):
            digest.update(b"regular\0")
            try:
                with candidate.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                digest.update(b"read-failed\0")
        elif stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            try:
                digest.update(os.readlink(candidate).encode("utf-8", errors="surrogateescape"))
            except OSError:
                digest.update(b"readlink-failed\0")
        else:
            digest.update(f"mode:{metadata.st_mode:o}:size:{metadata.st_size}".encode("ascii"))
    return digest.hexdigest()


def inspect_worktree(
    worktree_path: Path | str,
    *,
    materialized_paths: Iterable[object] | None = None,
    protected_paths: Iterable[str] = (),
) -> WorktreeInspection:
    path = Path(worktree_path)
    protected = normalize_protected_paths(list(protected_paths or ()))
    if protected:
        metadata = inspect_protected_git_metadata(path, "HEAD", protected, worktree_path=path, check_index=True)
        if not metadata.ok:
            return WorktreeInspection(
                kind="protected_metadata_refused",
                entries=(),
                blocking_entries=(),
                fingerprint="",
                detail=f"{metadata.reason}: {metadata.detail}",
            )
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"],
            cwd=path,
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError):
        proc = None
    if proc is None or proc.returncode != 0:
        return WorktreeInspection(
            kind="status_failed",
            entries=(),
            blocking_entries=(),
            fingerprint="",
            detail="git status could not be read",
        )
    inspection = inspect_porcelain(
        proc.stdout,
        worktree_path=path,
        materialized_paths=materialized_paths,
    )
    return WorktreeInspection(
        kind=inspection.kind,
        entries=inspection.entries,
        blocking_entries=inspection.blocking_entries,
        fingerprint=_worktree_fingerprint(path, inspection, proc.stdout, protected),
        detail=inspection.detail,
    )


def _environment_materialized_paths() -> list[str]:
    raw = os.environ.get("ORCH_MATERIALIZED_CONTEXT_PATHS", "")
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a task worktree using the shared handoff-cleanliness policy.")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--materialized-path", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    inspection = inspect_worktree(
        args.repo,
        materialized_paths=[*_environment_materialized_paths(), *args.materialized_path],
    )
    if args.json:
        print(json.dumps({
            "kind": inspection.kind,
            "detail": inspection.detail,
            "fingerprint": inspection.fingerprint,
            "entries": list(inspection.entries),
            "blocking_entries": list(inspection.blocking_entries),
        }, ensure_ascii=False))
    elif not inspection.handoff_clean:
        print(f"worktree_cleanliness: {inspection.kind}: {inspection.detail}", file=os.sys.stderr)
    return 0 if inspection.handoff_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
