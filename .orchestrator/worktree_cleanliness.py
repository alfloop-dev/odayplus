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
import re
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
            or any(char in _PATTERN_CHARS or ord(char) < 32 for char in value)
            or value != value.rstrip()
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


# --- protected body domain (every checkout) ---------------------------------
#
# Unlike ``protected_paths`` this is not a task record that refuses a commit:
# it names the path domains whose bodies no runtime, worker or CI checkout may
# materialize, wherever a copy sits.  A canonical prefix matches as consecutive
# path components at any depth (``tests/sources`` and an archived
# ``docs/.../untracked/tests/sources/...``); a named file matches any component
# that starts with it (``test_listing_production_asset.py.txt``).  Membership is
# decided from the path name alone, so a denied body is never opened to judge
# it.  Tracked domain entries stay tracked: a sparse exclusion keeps them out
# of the checkout, and Git tree/index/lstat metadata proves that it did.

PROTECTED_BODY_PREFIXES: tuple[str, ...] = ("tests/sources",)
PROTECTED_BODY_NAMES: tuple[str, ...] = ("test_listing_production_asset.py",)
# On POSIX Git keeps a literal backslash inside one tree-name component, while
# the domain reads it as a separator ("archive\tests/sources/x").  No finite
# set of slash patterns follows every such spelling, so any name holding a
# backslash is excluded whole (``\\`` is the escaped backslash in a pattern).
_BACKSLASH_NAME_PATTERNS: tuple[str, ...] = ("!**/*\\\\*", "!**/*\\\\*/**")


def _domain_prefixes(prefixes: Iterable[str] | None) -> tuple[str, ...]:
    return PROTECTED_BODY_PREFIXES if prefixes is None else tuple(prefixes)


def protected_body_sparse_patterns(
    prefixes: Iterable[str] | None = None, *, frozen_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """The non-cone sparse patterns that keep every domain entry out of a checkout.

    They cover every Git name ``in_protected_body_domain`` denies: an entry
    beneath a prefix and an entry named by the prefix itself (a file tracked
    at ``.../tests/sources``), at any depth, and every name holding a
    backslash, which the domain may read as a separator.  A slash-only Git
    name has no empty or ``.`` component, so the domain reads it literally.
    """
    return (
        "/*",
        *(
            pattern
            for prefix in _domain_prefixes(prefixes)
            for pattern in (f"!**/{prefix}", f"!**/{prefix}/**")
        ),
        *(f"!**/{name}*" for name in PROTECTED_BODY_NAMES),
        *_BACKSLASH_NAME_PATTERNS,
        *(pattern for path in normalize_protected_paths(frozen_paths) for pattern in (f"!/{path}", f"!/{path}/**")),
    )


def in_protected_body_domain(
    relative_path: object, prefixes: Iterable[str] | None = None, *, frozen_paths: tuple[str, ...] = (),
) -> bool:
    """Whether a repo-relative path names a domain entry, from its name only."""
    parts = [part for part in str(relative_path or "").replace("\\", "/").split("/") if part not in {"", "."}]
    for prefix in _domain_prefixes(prefixes):
        wanted = prefix.split("/")
        if any(parts[index : index + len(wanted)] == wanted for index in range(len(parts) - len(wanted) + 1)):
            return True
    literal = "/".join(parts)
    return any(literal == path or literal.startswith(path + "/") for path in frozen_paths) or any(
        part.startswith(name) for part in parts for name in PROTECTED_BODY_NAMES
    )


def protected_body_tree_entries(
    cwd: Path | str, commit: str, prefixes: Iterable[str] | None = None, *, frozen_paths: tuple[str, ...] = (),
) -> tuple[tuple[str, str, str], ...] | None:
    """(mode, object id, path) of every domain entry ``commit`` tracks; None when unreadable.

    Tree objects only: no blob is read, so a blob-filtered clone fetches nothing.
    """
    prefixes = _domain_prefixes(prefixes)
    if not commit:
        return None
    proc = _git_metadata(Path(cwd), "ls-tree", "-r", "-z", "--full-tree", str(commit))
    if proc is None or proc.returncode != 0:
        return None
    entries: list[tuple[str, str, str]] = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        header, _, name = record.partition(b"\t")
        fields = header.split()
        if len(fields) != 3:
            return None
        path = os.fsdecode(name)
        if in_protected_body_domain(path, prefixes, frozen_paths=frozen_paths):
            entries.append((fields[0].decode("ascii", "replace"), fields[2].decode("ascii", "replace"), path))
    return tuple(sorted(entries, key=lambda entry: entry[2]))


def _git_flag(cwd: Path, key: str) -> str | None:
    proc = _git_metadata(cwd, "config", "--type=bool", "--default", "false", "--get", key)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.decode("ascii", "replace").strip()


def _sparse_patterns(cwd: Path) -> list[str] | None:
    located = _git_metadata(cwd, "rev-parse", "--git-path", "info/sparse-checkout")
    if located is None or located.returncode != 0:
        return None
    path = Path(os.fsdecode(located.stdout.strip()))
    if not path.is_absolute():
        path = cwd / path
    try:
        # Git ignores empty lines, including the leading newline added by actions/checkout.
        # Keep every character of nonempty patterns, including embedded spaces.
        return [line for line in path.read_text(encoding="utf-8").split("\n") if line]
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError):
        return None


def install_protected_body_sparse(
    worktree_path: Path | str, prefixes: Iterable[str] | None = None, *, frozen_paths: tuple[str, ...] = (),
) -> str | None:
    """Exclude the domain from a registered, not yet materialized worktree.

    Returns why it could not.  An index that already holds entries means files
    may already be on disk; changing the patterns then would delete them, so it
    is refused rather than repaired.
    """
    root = Path(worktree_path)
    listed = _git_metadata(root, "ls-files", "-z", "--cached")
    if listed is None or listed.returncode != 0:
        return "unreadable_metadata: index"
    if listed.stdout:
        return "already_materialized: sparse exclusion must precede the first checkout"
    patterns = "".join(f"{pattern}\n" for pattern in protected_body_sparse_patterns(prefixes, frozen_paths=frozen_paths))
    try:
        proc = subprocess.run(
            ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
            cwd=root,
            input=patterns.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError) as error:
        return f"sparse_exclusion_failed: {error}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        return f"sparse_exclusion_failed: {detail}"
    return None


def protected_body_checkout_refusal(
    worktree_path: Path | str,
    *,
    prefixes: Iterable[str] | None = None,
    require_sparse: bool = False,
    target: str | None = None,
    frozen_paths: tuple[str, ...] = (),
) -> str | None:
    """Why status, hashing, copying, or checking ``target`` out could touch a domain body here.

    Runs before any of them, from configuration, index flags, untracked names
    and ``lstat`` only.  HEAD and the index are read as a union: once either
    tracks a domain entry (or ``require_sparse`` is set), the worktree must
    carry exactly the domain's sparse patterns, HEAD and the index must record
    the same (mode, object) at every domain path, and every such entry must be
    skip-worktree and absent from disk.  Untracked names are listed without any
    exclude file, because status would open a ``.gitignore`` inside the domain;
    any untracked domain entry refuses, whatever its kind.  An unreadable
    answer refuses; it is never read as "nothing there".

    ``target`` is a commit about to be checked out or merged here.  Its domain
    entries, read from tree objects only, need the same exclusion already in
    place, so a checkout without one is refused before Git writes anything,
    even when it holds no domain entry yet.
    """
    prefixes = _domain_prefixes(prefixes)
    root = Path(worktree_path)
    listed = _git_metadata(root, "ls-files", "-z", "-t", "--stage")
    if listed is None or listed.returncode != 0:
        return "unreadable_metadata: index"
    indexed: dict[str, list[tuple[str, str, str, str]]] = {}
    for record in listed.stdout.split(b"\0"):
        if not record:
            continue
        header, _, name = record.partition(b"\t")
        fields = header.decode("ascii", "replace").split()
        if len(fields) != 4:
            return "unreadable_metadata: index"
        path = os.fsdecode(name)
        if in_protected_body_domain(path, prefixes, frozen_paths=frozen_paths):
            tag, mode, object_id, stage = fields
            indexed.setdefault(path, []).append((tag, mode, object_id, stage))
    tracked: dict[str, tuple[str, str]] = {}
    born = _git_metadata(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if born is None:
        return "unreadable_metadata: HEAD"
    if born.returncode == 0:
        listing = protected_body_tree_entries(root, born.stdout.decode("ascii", "replace").strip(), prefixes, frozen_paths=frozen_paths)
        if listing is None:
            return "unreadable_metadata: HEAD tree"
        tracked = {path: (mode, object_id) for mode, object_id, path in listing}
    incoming: tuple[tuple[str, str, str], ...] = ()
    if target is not None:
        incoming = protected_body_tree_entries(root, target, prefixes, frozen_paths=frozen_paths)
        if incoming is None:
            return "unreadable_metadata: target tree"
    if indexed or tracked or incoming or require_sparse:
        if _git_flag(root, "core.sparseCheckout") != "true":
            return "protected_body_not_excluded: sparse checkout is not enabled"
        if _git_flag(root, "core.sparseCheckoutCone") != "false":
            return "protected_body_not_excluded: sparse checkout is in cone mode"
        patterns = _sparse_patterns(root)
        if patterns is None:
            return "unreadable_metadata: sparse patterns"
        if patterns != list(protected_body_sparse_patterns(prefixes, frozen_paths=frozen_paths)):
            return "protected_body_not_excluded: sparse patterns differ from the protected body domain"
    for path in sorted(set(indexed) | set(tracked)):
        stages = indexed.get(path, [])
        if not stages:
            return f"protected_body_drift: {path} is in HEAD but not in the index"
        if len(stages) != 1 or stages[0][3] != "0":
            return f"protected_body_drift: {path} is unmerged in the index"
        tag, mode, object_id, _stage = stages[0]
        if path not in tracked:
            return f"protected_body_drift: {path} is in the index but not in HEAD"
        if (mode, object_id) != tracked[path]:
            return f"protected_body_drift: {path} differs between HEAD and the index"
        if tag != "S":
            return f"protected_body_materialized: {path} is not skip-worktree ({tag})"
        for candidate in [*_ancestors(path), path]:
            try:
                metadata = (root / candidate).lstat()
            except (FileNotFoundError, NotADirectoryError):
                break
            except OSError:
                return f"unreadable_metadata: worktree {candidate}"
            if candidate == path:
                return f"protected_body_present: {path}"
            if stat.S_ISLNK(metadata.st_mode):
                return f"protected_body_present: {candidate} is a symlink above {path}"
            if not stat.S_ISDIR(metadata.st_mode):
                break
    # No --exclude-standard: Git then opens no .gitignore while it lists.
    untracked = _git_metadata(root, "ls-files", "-z", "--others")
    if untracked is None or untracked.returncode != 0:
        return "unreadable_metadata: untracked entries"
    for record in untracked.stdout.split(b"\0"):
        path = os.fsdecode(record)
        if record and in_protected_body_domain(path, prefixes, frozen_paths=frozen_paths):
            return f"protected_body_present: {path} is untracked domain content"
    return None


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


def _git_metadata_path(cwd: Path, *args: str) -> Path | None:
    """One path Git reports for ``cwd``, resolved against ``cwd`` when relative."""
    if not cwd.exists():
        return None
    proc = _git_metadata(cwd, *args)
    if proc is None or proc.returncode != 0:
        return None
    reported = os.fsdecode(proc.stdout).strip()
    if not reported:
        return None
    path = Path(reported)
    return (path if path.is_absolute() else cwd / path).resolve()


def shares_git_common_dir(repo_root: Path | str, path: Path | str) -> bool:
    """``path`` is the top level of a worktree of ``repo_root``'s repository.

    Only the resolved Git common directory binds a checkout to a repository: a
    separate clone can hold the same HEAD on the same named branch.  Metadata
    only; any unanswerable question is a refusal.
    """
    try:
        repo_root = Path(repo_root)
        path = Path(path).resolve()
        expected = _git_metadata_path(repo_root, "rev-parse", "--git-common-dir")
        if expected is None:
            return False
        if _git_metadata_path(path, "rev-parse", "--show-toplevel") != path:
            return False
        return _git_metadata_path(path, "rev-parse", "--git-common-dir") == expected
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _tree_entry(cwd: Path, commit: str, relative_path: str) -> tuple[str, str, str] | None | bool:
    """Exact (mode, type, object ID) of a tree entry; ``False`` when unreadable."""
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
            return (
                fields[0].decode("ascii", "replace"),
                fields[1].decode("ascii", "replace"),
                fields[2].decode("ascii", "replace"),
            )
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


def frozen_task_base(task: object) -> str | None:
    """An existing immutable task base authorizes tracked, excluded frozen files."""
    if not task_protected_paths(task) or "frozen_evidence_base_sha" not in task:
        return None
    base = task["frozen_evidence_base_sha"]
    if not isinstance(base, str) or not re.fullmatch(r"[a-f0-9]{40}", base):
        raise ProtectedPathPolicyError("frozen_evidence_base_sha must be a full lowercase Git object ID")
    return base


def frozen_task_upstream_refusal(cwd: Path | str, frozen_base: str, upstream_ref: str) -> str | None:
    """A task can only pin a freeze to history already on its trusted upstream."""
    if not upstream_ref.startswith("refs/remotes/origin/"):
        return "frozen_base_upstream_required: remote base reference is missing"
    root = Path(cwd)
    upstream = _git_metadata(root, "show-ref", "--verify", "--hash", upstream_ref)
    if upstream is None or upstream.returncode:
        return "frozen_base_upstream_required: remote base reference is unavailable"
    upstream_sha = upstream.stdout.decode("ascii", "replace").strip()
    if not re.fullmatch(r"[a-f0-9]{40}", upstream_sha):
        return "unreadable_metadata: remote base object ID"
    ancestry = _git_metadata(root, "merge-base", "--is-ancestor", frozen_base, upstream_sha)
    if ancestry is None or ancestry.returncode:
        return "frozen_base_not_upstream: canonical task base must be on the remote base branch"
    return None


def frozen_task_commit_refusal(
    cwd: Path | str, commit: str, protected: tuple[str, ...], frozen_base: str,
) -> str | None:
    """Compare tree modes and object IDs from the trusted task base, without reading blobs."""
    root = Path(cwd)
    for revision in (frozen_base, commit):
        verdict = inspect_protected_git_metadata(root, revision, protected)
        if not verdict.ok:
            return f"{verdict.reason}: {verdict.detail}"
    ancestry = _git_metadata(root, "merge-base", "--is-ancestor", frozen_base, commit)
    if ancestry is None or ancestry.returncode:
        return "frozen_base_not_ancestor: candidate is not based on the canonical task base"
    for path in protected:
        original = _tree_entry(root, frozen_base, path)
        proposed = _tree_entry(root, commit, path)
        if original is False or proposed is False:
            return f"unreadable_metadata: frozen path {path}"
        if original != proposed:
            return f"frozen_path_changed: {path}"
        if original is not None and original[0] not in {"100644", "100755"}:
            return f"frozen_path_unsupported_mode: {path} ({original[0]})"
    return None


def protected_task_checkout_refusal(
    cwd: Path,
    commit: str,
    protected: tuple[str, ...],
    *,
    worktree_path: Path | None = None,
    check_index: bool = False,
    checkout_into: Path | None = None,
    frozen_base: str | None = None,
) -> str | None:
    """Why Git may not materialize or read ``commit`` for a protected task, if it may not.

    ``metadata_safe`` only proves the entry cannot escape.  A regular tracked
    protected entry would still be written by read-tree, merge and checkout,
    and read by status, so it is refused here from its tree entry alone.

    Given the checkout itself (``worktree_path`` and ``check_index``), this also
    answers for what status and hashing will meet there rather than what the
    commit names: Git opens a staged body to compare it, and an ignore file under
    the prefix as it walks in.  An indexed entry, or anything on disk under the
    prefix, is refused before they run, as is any error proving neither.  The one
    entry a healthy checkout holds is the empty directory its own protected
    launch mounts the mask on (``plan_protected_execution``).

    The checkout itself is also held to the shared protected body domain, for
    every task, before any caller's status, hash, copy, checkout or merge.
    ``checkout_into`` names the existing checkout ``commit`` is about to be
    checked out or merged into: that checkout is held to the same domain for
    what it holds now and for what ``commit`` would bring, so a target that
    introduces a domain entry needs the exclusion before Git writes a file.
    """
    frozen_paths = protected if frozen_base is not None else ()
    if worktree_path is not None and check_index:
        body_refusal = protected_body_checkout_refusal(worktree_path, frozen_paths=frozen_paths)
        if body_refusal:
            return f"protected_metadata_refused: {body_refusal}"
    if checkout_into is not None:
        body_refusal = protected_body_checkout_refusal(checkout_into, target=commit, frozen_paths=frozen_paths)
        if body_refusal:
            return f"protected_metadata_refused: {body_refusal}"
    if not protected:
        return None
    verdict = inspect_protected_git_metadata(
        cwd, commit, protected, worktree_path=worktree_path, check_index=check_index
    )
    if not verdict.ok:
        return f"protected_metadata_refused: {verdict.reason}: {verdict.detail}"
    if frozen_base is not None:
        refusal = frozen_task_commit_refusal(cwd, verdict.detail, protected, frozen_base)
        return f"protected_metadata_refused: {refusal}" if refusal else None
    for path in protected:
        entry = _tree_entry(cwd, verdict.detail, path)
        if entry is False:
            return f"protected_metadata_refused: unreadable_metadata: {path}"
        if entry is not None:
            return f"protected_metadata_refused: protected_entry_tracked: {path} ({entry[0]}) at {verdict.detail}"
        if worktree_path is None or not check_index:
            continue
        # Modes only: a nested entry is proven present without naming it.
        indexed = _git_metadata(
            Path(worktree_path), "ls-files", "--cached", "-z", "--format=%(objectmode)", "--", f":(top,literal){path}"
        )
        if indexed is None or indexed.returncode != 0:
            return f"protected_metadata_refused: unreadable_metadata: index {path}"
        if indexed.stdout:
            return f"protected_metadata_refused: protected_entry_indexed: {path}"
        target = Path(worktree_path) / path
        try:
            if not stat.S_ISDIR(target.lstat().st_mode):
                return f"protected_metadata_refused: protected_entry_present: {path}"
            with os.scandir(target) as entries:
                if next(entries, None) is not None:
                    return f"protected_metadata_refused: protected_entry_present: {path}"
        except FileNotFoundError:
            continue
        except OSError:
            return f"protected_metadata_refused: unreadable_metadata: worktree {path}"
    return None



def context_path_protection_error(
    root: Path | str,
    relative_path: str,
    protected_paths: Iterable[str],
) -> str | None:
    """Refuse a context source/destination before it is hashed, listed or copied.

    The protected body domain applies to every task; ``protected_paths`` adds
    the task's own entries.
    """
    protected = tuple(protected_paths or ())
    if in_protected_body_domain(relative_path):
        return f"context path {relative_path!r} lies in the protected body domain"
    conflict = protected_path_conflict(relative_path, protected)
    if conflict:
        return f"context path {relative_path!r} overlaps protected path {conflict!r}"
    base = Path(root)
    try:
        resolved_root = base.resolve()
    except (OSError, RuntimeError):
        return f"context root {base} is unreadable"
    current = base
    # The domain/task checks above judge the logical (slash-normalized) name;
    # metadata is read for the exact filesystem spelling the caller will hash
    # or copy. A literal backslash is part of a POSIX entry name, not a separator.
    parts = str(relative_path).strip("/").split("/")
    for index, part in enumerate(parts):
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
            # An ancestor alias leads the rest of the path somewhere else:
            # judge the resolved full path as well as the alias target.
            resolved = "/".join(item for item in (target, *parts[index + 1 :]) if item not in {"", "."})
            for candidate in (target, resolved):
                if in_protected_body_domain(candidate):
                    return f"context path {relative_path!r} is a symlink alias into the protected body domain"
                conflict = protected_path_conflict(candidate, protected)
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
    """Mount operations for one protected launch, outermost first.

    ``("mask", path, "dir"|"file")`` replaces a protected entry with an empty
    read-only view.  ``("shadow", dir, children)`` freezes a directory: a
    private read-only tmpfs with each entry that existed at launch bound back
    (``children`` = ``(name, "rw"|"symlink", link_target)``), so entries
    the host adds later -- a protected file that was missing, a new sibling
    checkout -- never appear inside the sandbox, and nothing is created on the
    host to make that so.  ``("hide", root)`` covers another owner's checkout
    entirely.
    """

    protected_paths: tuple[str, ...]
    cwd: Path
    operations: tuple[tuple, ...]
    bwrap: str = BWRAP_BINARY
    absent_frozen_paths: tuple[Path, ...] = ()

    @property
    def mount_targets(self) -> tuple[Path, ...]:
        return tuple(operation[1] for operation in self.operations)

    def wrap(self, command: list[str], *, extra_masks: Iterable[Path] = ()) -> list[str]:
        argv = [self.bwrap, "--die-with-parent", "--dev-bind", "/", "/"]
        operations = [*self.operations, *(("mask", Path(target), "dir") for target in extra_masks)]
        for operation in operations:
            kind, target = operation[0], str(operation[1])
            if kind == "mask" and operation[2] == "file":
                argv.extend(["--ro-bind", "/dev/null", target])
            elif kind in {"mask", "hide"}:
                argv.extend(["--tmpfs", target, "--remount-ro", target])
            else:
                argv.extend(["--tmpfs", target])
                for name, mode, link_target in operation[2]:
                    child = f"{target}/{name}"
                    if mode == "symlink":
                        argv.extend(["--symlink", link_target, child])
                    else:
                        argv.extend(["--bind" if mode == "rw" else "--ro-bind", child, child])
                argv.extend(["--remount-ro", target])
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


def _shadow_operation(directory: Path) -> tuple:
    """Freeze which entries ``directory`` has; existing entries keep their access."""
    children: list[tuple[str, str, str | None]] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError as error:
        raise ProtectedExecutionUnavailable(f"{directory} cannot be listed: {error}") from None
    for entry in entries:
        child = Path(entry.path)
        if entry.is_symlink():
            children.append((entry.name, "symlink", os.readlink(child)))
        else:
            children.append((entry.name, "rw", None))
    return ("shadow", directory, tuple(children))


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


_UNSHADOWABLE = frozenset({Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/home"), Path.home()})


def plan_protected_execution(
    cwd: Path | str,
    protected_paths: Iterable[str],
    *,
    alias_roots: Iterable[Path | str] = (),
    runner_root: Path | None = None,
    bwrap: str = BWRAP_BINARY,
) -> ProtectedExecutionPlan:
    """Deny each protected path in every checkout of the repository.

    The workspace, named aliases, the runner checkout and roots containing
    the workspace's Git common directory keep the per-path masks.
    Other registered checkouts are hidden as a whole. The checkout that only
    hosts shared Git metadata can be frozen at its root when a protected
    ancestor is absent. Only mount points in the task's workspace are created
    on the host. A symlinked protected ancestor anywhere refuses.
    """
    protected = normalize_protected_paths(list(protected_paths or ()))
    if not protected:
        raise ProtectedExecutionUnavailable("no protected paths to enforce")
    workspace = Path(cwd).resolve()
    aliases: list[Path] = []
    for item in alias_roots:
        try:
            aliases.append(Path(item).resolve())
        except (OSError, RuntimeError):
            raise ProtectedExecutionUnavailable(f"alias root {item} cannot be resolved") from None
    roots: list[Path] = [workspace]
    for root in [*_registered_worktrees(workspace), *aliases]:
        try:
            resolved = Path(root).resolve()
        except (OSError, RuntimeError):
            raise ProtectedExecutionUnavailable(f"registered root {root} cannot be resolved") from None
        if resolved not in roots and resolved.is_dir():
            roots.append(resolved)

    common_dir = _git_metadata_path(workspace, "rev-parse", "--git-common-dir")
    if common_dir is None:
        raise ProtectedExecutionUnavailable("workspace Git common directory cannot be resolved")
    required = (workspace, *aliases)
    if runner_root is not None:
        try:
            runner = runner_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ProtectedExecutionUnavailable("worker runner root cannot be resolved") from None
        if not runner.is_dir():
            raise ProtectedExecutionUnavailable("worker runner root is not a directory")
        # Keep its registered checkout when it has one. The runner path is
        # not a new delivery alias and does not alter the parent freeze.
        required += (runner,)
    # Keep ancestors of required paths. An unrelated checkout nested below
    # a required root does not need to be visible to this worker.
    foreign = {root for root in roots if not any(_is_within(path, root) for path in (*required, common_dir))}

    operations: list[tuple] = []
    absent_frozen_paths: list[Path] = []
    base = workspace.parent
    if base not in _UNSHADOWABLE and not any(_is_within(alias, base) for alias in aliases):
        operations.append(_shadow_operation(base))
    for root in roots:
        violation = _worktree_metadata_violation(root, protected)
        if violation:
            raise ProtectedExecutionUnavailable(f"{root}: {violation}")
        hide_root = root in foreign
        if hide_root and not foreign.intersection(root.parents):
            operations.append(("hide", root))
        for path in protected:
            target = root / path
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                metadata = None
            except OSError as error:
                raise ProtectedExecutionUnavailable(f"{target}: {error}") from None
            if root == workspace:
                listed = _git_metadata(root, "ls-files", "-z", "-t", "--", f":(top,literal){path}")
                if listed is None or listed.returncode:
                    raise ProtectedExecutionUnavailable(f"cannot verify the workspace index for {path}")
                if listed.stdout:
                    if listed.stdout != b"S " + os.fsencode(path) + b"\0" or metadata is not None:
                        raise ProtectedExecutionUnavailable(f"tracked frozen file {path} is not safely excluded")
                    ancestor = target.parent
                    while ancestor != root and not ancestor.exists():
                        ancestor = ancestor.parent
                    operations.append(_shadow_operation(ancestor))
                    absent_frozen_paths.append(target)
                    continue
            if metadata is None and root == workspace:
                # Our own checkout: an empty read-only mount point also keeps
                # the worker from creating new protected content.
                target.mkdir(parents=True, exist_ok=True)
                metadata = target.lstat()
            if metadata is not None:
                if stat.S_ISDIR(metadata.st_mode):
                    kind = "dir"
                elif stat.S_ISREG(metadata.st_mode):
                    kind = "file"
                else:
                    raise ProtectedExecutionUnavailable(f"{target} is neither a directory nor a regular file")
                if not hide_root:
                    operations.append(("mask", target, kind))
                continue
            if hide_root:
                continue
            ancestor = target.parent
            while ancestor != root and not ancestor.exists():
                ancestor = ancestor.parent
            if ancestor == root and (root in _UNSHADOWABLE or any(_is_within(path, root) for path in required)):
                raise ProtectedExecutionUnavailable(
                    f"{root} lacks every ancestor of protected {path}; it cannot be denied without "
                    "modifying that shared checkout"
                )
            operations.append(_shadow_operation(ancestor))
    # Parents before children, so an inner mask lands on top of an outer
    # freeze rather than underneath it.
    unique: dict[Path, tuple] = {}
    for operation in operations:
        unique.setdefault(operation[1], operation)
    ordered = sorted(unique.values(), key=lambda operation: len(operation[1].parts))
    return ProtectedExecutionPlan(
        protected_paths=protected, cwd=workspace, operations=tuple(ordered), bwrap=bwrap,
        absent_frozen_paths=tuple(absent_frozen_paths),
    )


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
mounts = {}
for line in open('/proc/self/mountinfo', encoding='utf-8'):
    parts = line.split()
    mounts[unescape(parts[4])] = parts[5].split(',')
for target in spec['targets']:
    if 'ro' not in mounts.get(target, ()):
        errors.append('unmasked:' + target)
for target in spec['absent_frozen_paths']:
    if os.path.lexists(target):
        errors.append('materialized_frozen_path:' + target)
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
            "targets": [str(target) for target in plan.mount_targets],
            "absent_frozen_paths": [str(target) for target in plan.absent_frozen_paths],
            "token": PROTECTED_EXECUTION_PROBE_TOKEN,
        }
        argv = plan.wrap(
            [sys.executable, "-I", "-c", _PROBE_SOURCE, json.dumps(spec)],
            extra_masks=[canary_dir],
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
    runner_root: Path | None = None,
    bwrap: str | None = None,
) -> list[str]:
    """The one way a protected task's native process is started."""
    binary = bwrap or BWRAP_BINARY
    if not Path(binary).is_absolute():
        binary = shutil.which(binary) or binary
    plan = plan_protected_execution(
        cwd, protected_paths, alias_roots=alias_roots, runner_root=runner_root, bwrap=binary,
    )
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
    if code.strip() != "??" or in_protected_body_domain(path):
        # Domain content is never proven to be a seed: its body is not read.
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
        if protected_path_conflict(path, protected_paths) or in_protected_body_domain(path):
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
    frozen_base: str | None = None,
    enforce_task_paths: bool = False,
) -> WorktreeInspection:
    path = Path(worktree_path)
    protected = normalize_protected_paths(list(protected_paths or ()))
    # The shared guard runs before status can hash anything in the checkout.
    if enforce_task_paths or frozen_base is not None:
        body_refusal = protected_task_checkout_refusal(
            path, "HEAD", protected, worktree_path=path, check_index=True, frozen_base=frozen_base,
        )
    else:
        body_refusal = None
        if protected:
            metadata = inspect_protected_git_metadata(path, "HEAD", protected, worktree_path=path, check_index=True)
            if not metadata.ok:
                body_refusal = f"{metadata.reason}: {metadata.detail}"
        if not body_refusal:
            body_refusal = protected_body_checkout_refusal(path)
    if body_refusal:
        return WorktreeInspection(
            kind="protected_metadata_refused",
            entries=(),
            blocking_entries=(),
            fingerprint="",
            detail=body_refusal.removeprefix("protected_metadata_refused: "),
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


def task_review_freeze(
    repo: Path, task_id: str | None, status_file: Path | None, upstream_ref: str,
) -> tuple[tuple[str, ...], str | None]:
    """Load the canonical task's freeze and verify its upstream anchor."""
    if task_id is None and status_file is None:
        return (), None
    if not task_id or status_file is None:
        raise ProtectedPathPolicyError("task ID and status file must be supplied together")
    try:
        tasks = json.loads(status_file.read_text(encoding="utf-8"))["tasks"]
        if not isinstance(tasks, list):
            raise TypeError("tasks must be a list")
    except FileNotFoundError:
        if os.environ.get("PANTHEON_STATUS_ROOT"):
            raise ProtectedPathPolicyError("canonical task registry is missing") from None
        return (), None
    except (OSError, TypeError, ValueError, KeyError):
        raise ProtectedPathPolicyError("canonical task registry is invalid") from None
    matches = [item for item in tasks if isinstance(item, dict) and item.get("id") == task_id]
    if len(matches) > 1:
        raise ProtectedPathPolicyError("duplicate task records")
    task = matches[0] if matches else None
    protected = task_protected_paths(task)
    frozen_base = frozen_task_base(task)
    if frozen_base is not None:
        refusal = frozen_task_upstream_refusal(repo, frozen_base, upstream_ref)
        if refusal:
            raise ProtectedPathPolicyError(refusal)
    return protected, frozen_base


def frozen_tracked_files(repo: Path, protected: tuple[str, ...]) -> tuple[str, ...]:
    """Select the tracked file names after the caller has checked the frozen workspace."""
    tracked = []
    for path in protected:
        entry = _tree_entry(repo, "HEAD", path)
        if entry is False:
            raise ProtectedPathPolicyError(f"unreadable_metadata: frozen path {path}")
        if entry is not None and entry[0] in {"100644", "100755"}:
            tracked.append(path)
    return tuple(tracked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a task worktree using the shared handoff-cleanliness policy.")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--upstream-ref", default="")
    parser.add_argument("--materialized-path", action="append", default=[])
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--print-frozen-tracked", action="store_true")
    args = parser.parse_args(argv)
    try:
        protected, frozen_base = task_review_freeze(
            args.repo, args.task_id, args.status_file, args.upstream_ref,
        )
    except ProtectedPathPolicyError as error:
        print(f"worktree_cleanliness: {error}", file=os.sys.stderr)
        return 1
    inspection = inspect_worktree(
        args.repo,
        enforce_task_paths=True,
        protected_paths=protected,
        frozen_base=frozen_base,
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
    if inspection.handoff_clean and args.print_frozen_tracked and frozen_base is not None:
        try:
            for path in frozen_tracked_files(args.repo, protected):
                print(path)
        except ProtectedPathPolicyError as error:
            print(f"worktree_cleanliness: {error}", file=os.sys.stderr)
            return 1
    return 0 if inspection.handoff_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
