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
import stat
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


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
            path = os.fsdecode(path_bytes).strip()
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


def is_safe_context_destination(worktree_path: Path, relative_path: str) -> bool:
    """Do not allow a symlinked, non-regular, or escaping seed destination."""
    value = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not value or Path(value).is_absolute() or ".." in value.split("/"):
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
) -> WorktreeInspection:
    path = Path(worktree_path)
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
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
        fingerprint=_worktree_fingerprint(path, inspection, proc.stdout),
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
