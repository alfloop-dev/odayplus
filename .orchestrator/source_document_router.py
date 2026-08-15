#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CACHE_PREFIX = Path(".orchestrator/source-doc-cache")
RECEIPT_DIR = CACHE_PREFIX / "_receipts"
_GITHUB_REF_RE = re.compile(
    r"^github://(?P<repository>[^@\s]+)@(?P<ref>[0-9a-fA-F]{40})/(?P<path>.+)$"
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class SourceDocumentRoutingError(ValueError):
    """A source document cannot be resolved without crossing an authority boundary."""


@dataclass(frozen=True)
class ResolvedSourceDocument:
    original_reference: str
    canonical_reference: str
    context_path: str
    source_path: Path
    repository_id: str
    repository_slug: str
    repository_root: Path
    repository_path: str
    commit_sha: str | None
    sha256: str


def _run_git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=text,
        check=False,
        timeout=30,
    )


def _git_remote_slug(root: Path) -> str | None:
    proc = _run_git(root, "remote", "get-url", "origin")
    if proc.returncode != 0:
        return None
    value = str(proc.stdout or "").strip().replace("\\", "/")
    if value.endswith(".git"):
        value = value[:-4]
    if value.startswith("git@github.com:"):
        return value.split(":", 1)[1]
    marker = "github.com/"
    if marker in value:
        return value.split(marker, 1)[1]
    return None


def _safe_repository_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.lstrip("/")
    if not raw:
        raise SourceDocumentRoutingError("empty source document path")
    path = Path(raw)
    if path.is_absolute():
        raise SourceDocumentRoutingError("raw absolute path rejected")
    if ".." in path.parts:
        raise SourceDocumentRoutingError("traversal path rejected")
    return path.as_posix()


def _source_ref_from_task(task: dict[str, Any] | None) -> str | None:
    task = task or {}
    for key in (
        "source_commit_sha",
        "source_repository_sha",
        "source_document_sha",
        "authority_sha",
    ):
        value = str(task.get(key) or "").strip()
        if value:
            return value
    source_ref = task.get("source_ref")
    if isinstance(source_ref, dict):
        for key in ("commit_sha", "sha", "ref"):
            value = str(source_ref.get(key) or "").strip()
            if value:
                return value
    return None


def _parse_reference(
    reference: str,
    task: dict[str, Any] | None,
) -> tuple[str | None, str | None, str, bool]:
    raw = str(reference or "").strip().replace("\\", "/")
    match = _GITHUB_REF_RE.match(raw)
    if match:
        return (
            match.group("repository"),
            match.group("ref"),
            _safe_repository_path(match.group("path")),
            True,
        )

    # Machine-authored task catalogs may use repo::ref::path where repo contains
    # a slash and path may contain colons. The exact 40-hex ref remains mandatory.
    if "::" in raw:
        parts = raw.split("::", 2)
        if len(parts) == 3 and _SHA_RE.fullmatch(parts[1].strip()):
            return parts[0].strip(), parts[1].strip(), _safe_repository_path(parts[2]), True

    repository = str((task or {}).get("repository") or "").strip() or None
    return repository, _source_ref_from_task(task), _safe_repository_path(raw), False


def _registered_repository(
    config: dict[str, Any],
    repository_slug: str,
) -> tuple[str, Path, dict[str, Any]]:
    # Lazy import avoids common.py <-> multi_repo_registry.py import cycles.
    from multi_repo_registry import matching_repo_id, repository_local_path, resolve_repository

    repo_id = matching_repo_id(config, repository_slug)
    if not repo_id:
        raise SourceDocumentRoutingError(
            f"source repository is not registered: {repository_slug}"
        )
    root = repository_local_path(config, repo_id)
    if root is None:
        raise SourceDocumentRoutingError(
            f"source repository has no configured local path: {repository_slug}"
        )
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise SourceDocumentRoutingError(
            f"source repository checkout is unavailable: {repository_slug}"
        ) from exc
    if not (root / ".git").exists() and _run_git(root, "rev-parse", "--git-dir").returncode != 0:
        raise SourceDocumentRoutingError(
            f"source repository local path is not a git checkout: {root}"
        )
    return repo_id, root, resolve_repository(config, repo_id)


def _resolve_commit(root: Path, requested_ref: str | None, default_branch: str | None) -> str:
    candidates: list[str] = []
    if requested_ref:
        candidates.append(requested_ref)
    if default_branch:
        candidates.extend(
            [
                f"refs/remotes/origin/{default_branch}",
                f"origin/{default_branch}",
                f"refs/heads/{default_branch}",
                default_branch,
            ]
        )
    candidates.append("HEAD")

    for candidate in candidates:
        proc = _run_git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        if proc.returncode != 0:
            continue
        commit = str(proc.stdout or "").strip().lower()
        if not _SHA_RE.fullmatch(commit):
            continue
        if requested_ref and _SHA_RE.fullmatch(requested_ref) and commit != requested_ref.lower():
            continue
        return commit
    requested = requested_ref or default_branch or "HEAD"
    raise SourceDocumentRoutingError(f"unable to resolve source repository ref: {requested}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_tree(path: Path) -> str:
    hasher = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(path).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(item.read_bytes())
    return hasher.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _git_object_type(root: Path, commit: str, repository_path: str) -> str:
    proc = _run_git(root, "cat-file", "-t", f"{commit}:{repository_path}")
    if proc.returncode != 0:
        raise SourceDocumentRoutingError(
            f"missing source document at {commit[:12]}:{repository_path}"
        )
    value = str(proc.stdout or "").strip()
    if value not in {"blob", "tree"}:
        raise SourceDocumentRoutingError(
            f"unsupported source document git object type: {value or 'unknown'}"
        )
    return value


def _materialize_git_object(
    root: Path,
    commit: str,
    repository_path: str,
    destination: Path,
) -> str:
    object_type = _git_object_type(root, commit, repository_path)
    if object_type == "blob":
        proc = _run_git(root, "show", f"{commit}:{repository_path}", text=False)
        if proc.returncode != 0:
            raise SourceDocumentRoutingError(
                f"unable to read source document blob: {repository_path}"
            )
        payload = bytes(proc.stdout or b"")
        expected = hashlib.sha256(payload).hexdigest()
        if not destination.exists() or not destination.is_file() or _sha256_file(destination) != expected:
            _atomic_write(destination, payload)
        return expected

    listing = _run_git(root, "ls-tree", "-r", "--name-only", commit, "--", repository_path)
    if listing.returncode != 0:
        raise SourceDocumentRoutingError(
            f"unable to enumerate source document directory: {repository_path}"
        )
    names = [line.strip() for line in str(listing.stdout or "").splitlines() if line.strip()]
    prefix = repository_path.rstrip("/") + "/"
    if not names or any(not name.startswith(prefix) for name in names):
        raise SourceDocumentRoutingError(
            f"source document directory is empty or escaped its repository path: {repository_path}"
        )
    inventory_names = {"manifest.json", "inventory.json", ".inventory", "LATEST.json"}
    if not any(Path(name).name in inventory_names for name in names):
        raise SourceDocumentRoutingError("directory without inventory manifest")

    destination.mkdir(parents=True, exist_ok=True)
    expected_relatives: set[str] = set()
    for name in names:
        relative = Path(name[len(prefix) :]).as_posix()
        if not relative or ".." in Path(relative).parts:
            raise SourceDocumentRoutingError("invalid source document directory member")
        expected_relatives.add(relative)
        proc = _run_git(root, "show", f"{commit}:{name}", text=False)
        if proc.returncode != 0:
            raise SourceDocumentRoutingError(f"unable to read source directory member: {name}")
        payload = bytes(proc.stdout or b"")
        target = destination / relative
        expected = hashlib.sha256(payload).hexdigest()
        if not target.exists() or not target.is_file() or _sha256_file(target) != expected:
            _atomic_write(target, payload)

    for existing in sorted(destination.rglob("*"), reverse=True):
        if existing.is_file() and existing.relative_to(destination).as_posix() not in expected_relatives:
            existing.unlink()
        elif existing.is_dir() and not any(existing.iterdir()):
            existing.rmdir()
    return _sha256_tree(destination)


def _validate_local_path(status_root: Path, repository_path: str) -> tuple[Path, str]:
    try:
        root = status_root.resolve(strict=True)
        target = (root / repository_path).resolve(strict=True)
        target.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceDocumentRoutingError("missing source document") from exc
    if target.is_dir():
        inventory_names = {"manifest.json", "inventory.json", ".inventory", "LATEST.json"}
        if not any((target / name).exists() for name in inventory_names):
            raise SourceDocumentRoutingError("directory without inventory manifest")
        for item in target.rglob("*"):
            try:
                item.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as exc:
                raise SourceDocumentRoutingError(
                    f"external directory child symlink rejected for '{item}'"
                ) from exc
        return target, _sha256_tree(target)
    if not target.is_file():
        raise SourceDocumentRoutingError("missing source document")
    return target, _sha256_file(target)


def resolve_source_document(
    config: dict[str, Any],
    status_root: Path,
    reference: str,
    *,
    task: dict[str, Any] | None = None,
) -> ResolvedSourceDocument:
    repository_slug, requested_ref, repository_path, explicit = _parse_reference(reference, task)
    current_slug = _git_remote_slug(status_root)

    # Legacy local documents remain local. A task that explicitly declares a
    # different repository must never be silently satisfied by a same-named file
    # under the supervisor status root.
    if not explicit and (not repository_slug or repository_slug == current_slug):
        source_path, digest = _validate_local_path(status_root, repository_path)
        return ResolvedSourceDocument(
            original_reference=str(reference),
            canonical_reference=repository_path,
            context_path=repository_path,
            source_path=source_path,
            repository_id="pantheon",
            repository_slug=current_slug or "local",
            repository_root=status_root.resolve(),
            repository_path=repository_path,
            commit_sha=None,
            sha256=digest,
        )

    if not repository_slug:
        raise SourceDocumentRoutingError("task source repository is not declared")
    repo_id, root, repo = _registered_repository(config, repository_slug)
    default_branch = str((task or {}).get("base_branch") or repo.get("default_branch") or "").strip() or None
    commit = _resolve_commit(root, requested_ref, default_branch)

    safe_repo = re.sub(r"[^A-Za-z0-9_.-]+", "__", repository_slug).strip("_") or repo_id
    cache_rel = CACHE_PREFIX / safe_repo / commit / repository_path
    cache_path = status_root.resolve() / cache_rel
    digest = _materialize_git_object(root, commit, repository_path, cache_path)
    canonical = f"github://{repository_slug}@{commit}/{repository_path}"

    receipt_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    receipt_path = status_root.resolve() / RECEIPT_DIR / f"{receipt_key}.json"
    receipt = {
        "schema": "pantheon.source-document-receipt.v1",
        "canonical_reference": canonical,
        "repository": repository_slug,
        "repository_id": repo_id,
        "commit_sha": commit,
        "repository_path": repository_path,
        "cache_path": cache_rel.as_posix(),
        "sha256": digest,
    }
    _atomic_write(
        receipt_path,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    return ResolvedSourceDocument(
        original_reference=str(reference),
        canonical_reference=canonical,
        context_path=cache_rel.as_posix(),
        source_path=cache_path,
        repository_id=repo_id,
        repository_slug=repository_slug,
        repository_root=root,
        repository_path=repository_path,
        commit_sha=commit,
        sha256=digest,
    )
