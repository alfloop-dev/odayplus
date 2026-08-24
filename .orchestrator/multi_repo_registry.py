#!/usr/bin/env python3
"""Multi-repository coordination registry.

Artifact routing is prefix based. For example,
``execute-plans/e2e/dummy.spec.ts`` resolves to the ``execute_plans``
repository root with repository-relative path ``e2e/dummy.spec.ts``.
"""
from __future__ import annotations

import os
import re
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import repo_root_for_config, to_bool

# `pantheon` is the orchestrator's original name, from the project this code was
# ported from. It survives as the repo id of the checkout the supervisor runs
# from, because it is written into ai-status.json records, the task archive and
# runtime state going back months; renaming the key would strand all of that.
# Everything human-facing now says ODay Plus, `aliases` lets the new name
# resolve, and `LEGACY_SELF_REPO_ID` gives call sites one name to refer to
# instead of a bare string repeated across the codebase.
LEGACY_SELF_REPO_ID = "pantheon"

DEFAULT_REPOSITORIES: dict[str, dict[str, Any]] = {
    LEGACY_SELF_REPO_ID: {
        "display_name": "ODay Plus",
        # Deliberately no alias for "odayplus": that is a separate repo id in
        # this registry, carrying the GitHub slug this one lacks. Aliasing it
        # here would route tasks that declare `alfloop-dev/odayplus` to a
        # repository with no slug, and status-check emission needs the slug.

        "repo": None,
        "local_path": ".",
        "default_branch": "dev",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
        "screen_docs_dir": "docs/screens",
        "bff_docs_dir": "docs/bff",
        "examples_dir": "docs/examples",
    },
    "front_ai_trading_system": {
        "display_name": "front-ai-trading-system",
        "repo": "ajoe734/front-ai-trading-system",
        "local_path": "../front-ai-trading-system",
        "default_branch": "main",
        "artifact_prefixes": ["front-ai-trading-system/"],
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
        "screen_docs_dir": "docs/screens",
    },
    "execute_plans": {
        "display_name": "execute-plans",
        "repo": "ajoe734/execute-plans",
        "local_path": "../execute-plans",
        "default_branch": "main",
        "artifact_prefixes": ["execute-plans/"],
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
    "runtime_platform": {
        "display_name": "lean-platform",
        "repo": None,
        "local_path": "../lean-platform",
        "default_branch": "main",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
    "lean_engine": {
        "display_name": "Lean",
        "repo": "ajoe734/pantheon-lean",
        "local_path": "../Lean",
        "default_branch": "master",
        "coordination_dir": ".coordination",
        "requests_dir": ".coordination/requests",
        "responses_dir": ".coordination/responses",
    },
}

_ENV_PATH_OVERRIDE_SUFFIX = "_LOCAL_PATHS"


def _path_override_env_key(repo_id: str) -> str:
    return repo_id.upper().replace("-", "_").replace("/", "_") + _ENV_PATH_OVERRIDE_SUFFIX


def _coalesce_repo_local_path_candidates(repo_id: str, local_path: str | None) -> list[str]:
    candidates: list[str] = []
    if local_path:
        candidates.append(local_path)

    # Deployment-specific checkout locations belong in configuration or in the
    # documented per-repo env override below. A hard-coded sibling guess here
    # silently outranks both: it keeps resolving to some other checkout on the
    # host when the configured one is wrong, which hides the misconfiguration
    # instead of surfacing it.
    override = os.getenv(_path_override_env_key(repo_id), "")
    for raw in override.split(os.pathsep):
        value = str(raw).strip()
        if value:
            candidates.append(value)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


DEFAULT_WORKER_ROUTES: dict[str, dict[str, Any]] = {
    "pantheon-bff-worker": {
        "target_agent": "Codex",
        "description": "ODay Plus BFF and contract work",
    },
    "front-sync-worker": {
        "target_agent": "Codex",
        "description": "Front-end type, SDK, and hook sync work",
    },
    "front-ui-worker": {
        "target_agent": "Copilot",
        "description": "Front-end UI implementation work",
    },
    "runtime-worker": {
        "target_agent": "Gemini",
        "description": "Runtime and platform integration work",
    },
    "engine-worker": {
        "target_agent": "Claude",
        "description": "LEAN engine capability work",
        "requires_human_approval": True,
    },
    "qa-worker": {
        "target_agent": "Claude",
        "description": "QA verification and acceptance work",
    },
}


WORKER_ALIASES = {
    "pantheon-bff": "pantheon-bff-worker",
    "front-sync": "front-sync-worker",
    "front-ui": "front-ui-worker",
    "runtime": "runtime-worker",
    "engine": "engine-worker",
    "qa": "qa-worker",
}


# A task's own ``repository`` and ``pr_number`` describe the delivery being
# reviewed.  They do not describe any consumer (or other repository) that must
# land alongside it.  Those additional deliveries therefore have to be
# declared under one of these explicit fields.  Keeping the accepted aliases
# here makes the record portable across the catalog, status writer and older
# task imports without ever falling back to the producer PR as evidence.
REQUIRED_DELIVERY_FIELDS = (
    "required_cross_repo_deliveries",
    "cross_repo_delivery_requirements",
    "cross_repo_deliveries",
    "cross_repo_delivery",
    "delivery_requirements",
    "required_deliveries",
    "required_delivery_prs",
    "required_cross_repo_prs",
)


def _raw_required_deliveries(task: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return explicitly declared delivery records, preserving bad records.

    A malformed declaration is evidence that the gate cannot answer, not a
    reason to silently drop the requirement.  The caller therefore receives a
    small record with an ``error`` field and can fail closed while retaining the
    original declaration for operator diagnosis.
    """
    task = task or {}
    for field in REQUIRED_DELIVERY_FIELDS:
        if field not in task:
            continue
        raw = task.get(field)
        if raw is None:
            return []
        if isinstance(raw, list):
            return [item if isinstance(item, dict) else {"declaration": item} for item in raw]
        if isinstance(raw, dict):
            # A single record is the canonical shape.  Also accept a wrapper
            # used by early catalog imports without treating its keys as PRs.
            for wrapper in ("requirements", "deliveries", "items"):
                wrapped = raw.get(wrapper)
                if isinstance(wrapped, list):
                    return [item if isinstance(item, dict) else {"declaration": item} for item in wrapped]
            if not any(
                key in raw
                for key in (
                    "repository",
                    "repository_slug",
                    "repo",
                    "pr_number",
                    "pull_request",
                    "task_id",
                )
            ):
                mapped: list[dict[str, Any]] = []
                for repository, declaration in raw.items():
                    if isinstance(declaration, dict):
                        item = deepcopy(declaration)
                        item.setdefault("repository", repository)
                    else:
                        item = {"repository": repository, "pr_number": declaration}
                    mapped.append(item)
                return mapped
            return [raw]
        return [{"declaration": raw}]
    return []


def _first_delivery_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def task_delivery_requirements(
    config: dict[str, Any],
    task: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Normalize a task's explicitly required delivery PR declarations.

    The returned shape is deliberately JSON-compatible because it is stored in
    status evidence and blockers.  A record is *not* inferred from the task's
    own PR, repository, artifacts or dependencies.  A required cross-repo
    delivery without an explicit repository is invalid and stays in the list
    with ``error`` so a terminal gate can reject it visibly.
    """
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(_raw_required_deliveries(task)):
        record = deepcopy(raw)
        repository_value = _first_delivery_value(
            record, "repository", "repository_slug", "repo", "repo_slug"
        )
        url_value = _first_delivery_value(
            record, "pr_url", "pull_request_url", "url", "source_doc"
        )
        if not repository_value and isinstance(url_value, str):
            url_match = re.search(r"github\.com/([^/]+/[^/#?]+)", url_value)
            if url_match:
                repository_value = url_match.group(1).removesuffix(".git")
        repository = str(repository_value or "").strip()
        repo_id = matching_repo_id(config, repository) if repository else None
        slug = repository_slug(config, repo_id) if repo_id else None

        pr_value = _first_delivery_value(
            record, "pr_number", "pull_request", "pr", "pr_url", "pull_request_url", "url", "source_doc"
        )
        pr_number: int | None = None
        if isinstance(pr_value, dict):
            pr_value = _first_delivery_value(pr_value, "number", "pr_number")
        if isinstance(pr_value, str) and not pr_value.strip().lstrip("#").isdigit():
            url_match = re.search(r"/pull/(\d+)(?:$|[/#?])", pr_value)
            pr_value = url_match.group(1) if url_match else pr_value
        try:
            if pr_value is not None and str(pr_value).strip():
                pr_number = int(str(pr_value).strip().lstrip("#"))
                if pr_number <= 0:
                    raise ValueError
        except (TypeError, ValueError):
            pr_number = None

        task_id = str(
            _first_delivery_value(record, "task_id", "child_task_id", "delivery_task_id")
            or ""
        ).strip()
        approved_head = str(
            _first_delivery_value(
                record,
                "approved_head",
                "approved_head_sha",
                "head_sha",
                "expected_head",
                "expected_head_sha",
            )
            or ""
        ).strip()
        head_branch = str(
            _first_delivery_value(record, "head_branch", "branch", "headRefName") or ""
        ).strip()
        base_branch = str(
            _first_delivery_value(
                record, "base_branch", "target_branch", "baseRefName"
            )
            or (resolve_repository(config, repo_id).get("default_branch") if repo_id else "")
            or "dev"
        ).strip()
        required_value = record.get("required", True)
        required = not (
            isinstance(required_value, str)
            and required_value.strip().lower() in {"0", "false", "no", "off"}
        ) and bool(required_value)

        errors: list[str] = []
        if not repository:
            errors.append("repository is required")
        elif not repo_id or not slug:
            errors.append(f"repository is not registered: {repository}")
        if pr_number is None and not task_id:
            errors.append("pr_number or child task_id is required")
        requirement_id = str(
            _first_delivery_value(record, "id", "delivery_id", "requirement_id") or ""
        ).strip()
        if not requirement_id:
            requirement_id = (
                f"{slug}#{pr_number}" if slug and pr_number is not None
                else task_id or f"delivery-{index + 1}"
            )

        item: dict[str, Any] = {
            "id": requirement_id,
            "required": required,
            "repository": slug or repository or None,
            "repository_id": repo_id,
            "pr_number": pr_number,
            "task_id": task_id or None,
            "head_branch": head_branch or None,
            "base_branch": base_branch or None,
            "approved_head": approved_head or None,
            "declaration": record,
        }
        if errors:
            item["error"] = "; ".join(errors)
        normalized.append(item)
    return normalized


# This spelling reads naturally at call sites that only deal with the
# cross-repository subset and is kept as a public alias for task/test tooling.
cross_repo_delivery_requirements = task_delivery_requirements


def coordination_enabled(config: dict[str, Any]) -> bool:
    coord_cfg = config.get("coordination")
    if coord_cfg is None:
        return False
    return to_bool(coord_cfg.get("enabled", True))


def coordination_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("coordination", {}) or {})


def repositories(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_REPOSITORIES)
    # ODay Plus is the supervisor/status repository. EMGI producer tasks live
    # in the sibling data-platform checkout and must be routable without
    # pretending their authority documents belong to the status repository.
    merged.setdefault(
        "odayplus",
        {
            "display_name": "odayplus",
            "repo": "alfloop-dev/odayplus",
            "local_path": ".",
            "default_branch": "dev",
            "artifact_prefixes": ["odayplus/"],
            "coordination_dir": ".coordination",
            "requests_dir": ".coordination/requests",
            "responses_dir": ".coordination/responses",
        },
    )
    merged.setdefault(
        "oday_data_platform",
        {
            "display_name": "oday-data-platform",
            "repo": "alfloop-dev/oday-data-platform",
            "local_path": "../oday-data-platform",
            "default_branch": "dev",
            "artifact_prefixes": ["oday-data-platform/"],
            "coordination_dir": ".coordination",
            "requests_dir": ".coordination/requests",
            "responses_dir": ".coordination/responses",
        },
    )
    for repo_id, override in (coordination_config(config).get("repositories", {}) or {}).items():
        current = merged.setdefault(repo_id, {})
        current.update(deepcopy(override or {}))

    pantheon_repo = merged.setdefault("pantheon", {})
    if not pantheon_repo.get("repo"):
        pantheon_repo["repo"] = ((config.get("github_bus") or {}).get("repo")) or None
    return merged


def repository_path_anchor(config: dict[str, Any]) -> Path:
    """Directory that relative repository ``local_path`` values hang off.

    Module-level ``ROOT`` answers "which copy of the code is executing". For a
    fleet whose supervisor runs from a per-rollout checkout while its state and
    sibling repositories live somewhere else, that is the wrong question: the
    anchor has to be the fleet's own repository root, or every relative path in
    the registry silently re-points whenever a rollout swaps the code
    directory. ``repo_root_for_config`` falls back to ``ROOT`` when a config
    carries no ``status_file``, which keeps callers that pass ``{}`` working.
    """
    return repo_root_for_config(config)


def _resolve_repo_candidate(config: dict[str, Any], candidate: str) -> Path | None:
    path = Path(os.path.expanduser(candidate))
    if path.is_absolute():
        return path
    return (repository_path_anchor(config) / path).resolve()


def resolve_repository(config: dict[str, Any], repo_id: str) -> dict[str, Any]:
    repo = deepcopy(repositories(config).get(repo_id, {}))
    repo["id"] = repo_id
    repo["display_name"] = repo.get("display_name") or repo_id
    local_path = repo.get("local_path")
    resolved_local_path = None
    default_path: Path | None = None
    for candidate in _coalesce_repo_local_path_candidates(repo_id, local_path):
        candidate_path = _resolve_repo_candidate(config, candidate) if candidate else None
        if candidate_path is None:
            continue
        if default_path is None:
            default_path = candidate_path
        if candidate_path.exists():
            resolved_local_path = candidate_path
            break
    if resolved_local_path is None:
        resolved_local_path = default_path
    repo["resolved_local_path"] = resolved_local_path
    return repo


def normalized_repository_slug(value: str | None) -> str | None:
    candidate = re.sub(r"\.git$", "", str(value or "").strip())
    return candidate.casefold() or None


def checkout_origin_slug(root: Path) -> str | None:
    """The ``owner/name`` a checkout actually pushes to, if it has an origin."""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"[:/]([^/:]+/[^/]+)$", re.sub(r"\.git$", "", (proc.stdout or "").strip()))
    return match.group(1).casefold() if match else None


@dataclass(frozen=True)
class RepositoryBinding:
    """The single answer to "which repo is this, and where is its checkout".

    Every subsystem that touches a task's repository -- worktree leasing,
    source-document routing, review status checks -- used to re-derive this
    independently, from the status-file path, from artifact prefixes, or from a
    fleet-wide default. Those derivations disagreed, and the disagreements only
    surfaced as damage far downstream: a task branch created in the wrong
    origin, a status check posted against a repository that never had the SHA.
    """

    repo_id: str
    slug: str | None
    root: Path | None
    source: str
    error: str | None = None
    # This belongs to the registry, rather than worker_worktrees.  A worktree
    # is an implementation detail of a repository binding; letting it choose a
    # second base ref made cross-repository tasks silently use the wrong branch.
    default_branch: str = ""

    @property
    def resolved(self) -> bool:
        return self.root is not None


def resolve_repository_binding(
    config: dict[str, Any],
    repo_id: str | None,
    *,
    expected_slug: str | None = None,
    verify_origin: bool = True,
) -> RepositoryBinding:
    """Bind a registry id to a verified local checkout, or explain why not."""
    if not repo_id:
        return RepositoryBinding("", None, None, "unresolved", "no repository id")

    repo = resolve_repository(config, repo_id)
    slug = str(repo.get("repo") or "").strip() or None
    wanted = normalized_repository_slug(expected_slug or slug)
    root = repo.get("resolved_local_path")

    if not isinstance(root, Path) or not root.exists():
        return RepositoryBinding(
            repo_id,
            slug,
            None,
            "unresolved",
            f"repository_checkout_unavailable: no local checkout for {slug or repo_id}",
            str(repo.get("default_branch") or "").strip(),
        )

    resolved_root = root.resolve()
    if verify_origin and wanted:
        actual = checkout_origin_slug(resolved_root)
        if actual is not None and actual != wanted:
            return RepositoryBinding(
                repo_id,
                slug,
                None,
                "unresolved",
                (
                    f"repository_checkout_mismatch: {resolved_root} points at "
                    f"{actual}, expected {wanted}"
                ),
                str(repo.get("default_branch") or "").strip(),
            )
    return RepositoryBinding(
        repo_id,
        slug,
        resolved_root,
        f"repository:{repo_id}",
        default_branch=str(repo.get("default_branch") or "").strip(),
    )


def resolve_task_repository(config: dict[str, Any], task: dict[str, Any] | None) -> RepositoryBinding:
    """Authoritative task -> repository -> checkout resolution.

    An explicit ``repository`` slug on the task is the strongest statement of
    intent and wins; artifact-prefix inference is the documented fallback for
    older task records that never carried one.
    """
    task = task or {}
    declared = str(task.get("repository") or "").strip()
    if declared:
        repo_id = matching_repo_id(config, declared)
        if not repo_id:
            return RepositoryBinding(
                "",
                declared,
                None,
                "unresolved",
                f"unknown_repository: {declared} is not in the repository registry",
            )
        # Canonicalize to the configured slug so origin verification compares
        # the right value.  A task may declare a registry ID ("oday_data_platform"),
        # an alias, a display name, or the full GitHub slug; all resolve to the
        # same repo_id but only the configured slug matches what `git remote
        # get-url origin` returns.  Passing the raw declared value caused false
        # repository_checkout_mismatch when it was not already the slug.
        configured_slug = repository_slug(config, repo_id)
        return resolve_repository_binding(config, repo_id, expected_slug=configured_slug)

    inferred = _infer_repository_from_artifacts(config, task)
    if inferred is None:
        # Inference returns None only for genuine ambiguity: artifacts spanning
        # more than one non-ODay-Plus repository. `or "pantheon"` used to turn
        # that into an answer, which is the same silent wrong-repository choice
        # the declared-but-unknown branch above already refuses. Treat both
        # "cannot answer" cases the same way and let the caller decide.
        prefixes = ", ".join(task_artifact_repository_ids(config, task)) or "none"
        return RepositoryBinding(
            "",
            None,
            None,
            "unresolved",
            f"ambiguous_repository: task artifacts span several repositories ({prefixes})",
        )
    return resolve_repository_binding(config, inferred)


def matching_repo_id(config: dict[str, Any], value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    lowered = candidate.casefold()
    for repo_id, repo in repositories(config).items():
        options = {
            repo_id,
            str(repo.get("display_name") or ""),
            str(repo.get("repo") or ""),
            # A repository may answer to more than one name while a rename is in
            # flight: `pantheon` is the id on disk, `odayplus` is what people
            # write. Both must resolve to the same repository or a task declaring
            # either one routes somewhere else.
            *(str(alias) for alias in (repo.get("aliases") or [])),
        }
        normalized = {item.strip().casefold() for item in options if item and item.strip()}
        if lowered in normalized:
            return repo_id
    return None


def repository_slug(config: dict[str, Any], repo_id: str | None) -> str | None:
    if not repo_id:
        return None
    repo = resolve_repository(config, repo_id)
    slug = str(repo.get("repo") or "").strip()
    return slug or None


def repository_local_path(config: dict[str, Any], repo_id: str | None) -> Path | None:
    if not repo_id:
        return None
    repo = resolve_repository(config, repo_id)
    path = repo.get("resolved_local_path")
    return path if isinstance(path, Path) else None


def canonical_repository_id_for_root(
    config: dict[str, Any],
    root: Path,
    *,
    fallback: str,
) -> str:
    """Return the registry's stable id for a checkout shared by several ids.

    ``pantheon`` and ``odayplus`` intentionally resolve to the same checkout in
    a live fleet.  Worktree paths must not depend on which alias happened to be
    written into an individual task record, otherwise a single repository
    acquires two parallel directory namespaces and lease discovery becomes
    accidental.  ``iter_local_repositories`` already defines the canonical
    first-registered-id rule used everywhere else that walks local checkouts.
    """
    try:
        target = root.resolve()
    except OSError:
        target = root
    for repo in iter_local_repositories(config):
        candidate = repo.get("resolved_local_path")
        if not isinstance(candidate, Path):
            continue
        try:
            if candidate.resolve() == target:
                return str(repo.get("id") or fallback)
        except OSError:
            if candidate == target:
                return str(repo.get("id") or fallback)
    return fallback


def _normalized_artifact_path(value: str | Path | None) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def _safe_artifact_prefix(value: str | None) -> str | None:
    candidate = _normalized_artifact_path(value).strip("/")
    if not candidate or candidate in {".", ".."}:
        return None
    parts = [part for part in candidate.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) + "/"


def repository_artifact_prefixes(config: dict[str, Any], repo_id: str) -> list[str]:
    repo = resolve_repository(config, repo_id)
    raw_prefixes = repo.get("artifact_prefixes")
    candidates: list[str] = []
    if isinstance(raw_prefixes, str):
        candidates.append(raw_prefixes)
    elif isinstance(raw_prefixes, list):
        candidates.extend(str(item) for item in raw_prefixes if str(item).strip())

    for raw in (
        repo_id,
        repo.get("display_name"),
        str(repo.get("repo") or "").rsplit("/", 1)[-1],
        Path(str(repo.get("local_path") or "")).name,
    ):
        value = str(raw or "").strip()
        if value:
            candidates.append(value)

    prefixes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        prefix = _safe_artifact_prefix(candidate)
        if prefix is None or prefix in seen:
            continue
        seen.add(prefix)
        prefixes.append(prefix)
    return sorted(prefixes, key=len, reverse=True)


def _path_repository_id(config: dict[str, Any], value: Path) -> str | None:
    try:
        resolved = value.resolve(strict=False)
    except OSError:
        resolved = value.absolute()

    matches: list[tuple[int, str]] = []
    for repo in iter_local_repositories(config):
        repo_id = str(repo.get("id") or "").strip()
        root = repo.get("resolved_local_path")
        if not repo_id or not isinstance(root, Path):
            continue
        try:
            resolved_root = root.resolve(strict=False)
        except OSError:
            resolved_root = root.absolute()
        if resolved == resolved_root or resolved_root in resolved.parents:
            matches.append((len(str(resolved_root)), repo_id))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def artifact_repository_id(config: dict[str, Any], artifact_path: str | Path | None) -> str:
    candidate = _normalized_artifact_path(artifact_path)
    if not candidate:
        return "pantheon"

    path = Path(candidate)
    if path.is_absolute():
        return _path_repository_id(config, path) or "pantheon"

    for repo_id in repositories(config):
        if repo_id == "pantheon":
            continue
        for prefix in repository_artifact_prefixes(config, repo_id):
            if candidate == prefix[:-1] or candidate.startswith(prefix):
                return repo_id
    return "pantheon"


def repository_relative_artifact_path(
    config: dict[str, Any],
    artifact_path: str | Path | None,
    repo_id: str | None = None,
) -> Path:
    candidate = _normalized_artifact_path(artifact_path)
    if not candidate:
        return Path()

    path = Path(candidate)
    target_repo_id = repo_id or artifact_repository_id(config, candidate)
    if path.is_absolute():
        repo_root = repository_local_path(config, target_repo_id)
        if repo_root is not None:
            try:
                return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
            except (OSError, ValueError):
                pass
        return path

    for prefix in repository_artifact_prefixes(config, target_repo_id):
        if candidate == prefix[:-1]:
            return Path()
        if candidate.startswith(prefix):
            return Path(candidate[len(prefix) :])
    return Path(candidate)


def artifact_local_path(config: dict[str, Any], artifact_path: str | Path | None) -> Path | None:
    repo_id = artifact_repository_id(config, artifact_path)
    repo_root = repository_local_path(config, repo_id)
    if repo_root is None:
        return None
    return repo_root / repository_relative_artifact_path(config, artifact_path, repo_id)


def task_artifact_repository_ids(config: dict[str, Any], task: dict[str, Any]) -> list[str]:
    repo_ids: list[str] = []
    seen: set[str] = set()
    for artifact in task.get("artifacts") or []:
        repo_id = artifact_repository_id(config, artifact)
        if repo_id in seen:
            continue
        seen.add(repo_id)
        repo_ids.append(repo_id)
    return repo_ids or ["pantheon"]


def task_repository_id(config: dict[str, Any], task: dict[str, Any] | None) -> str | None:
    """The repository a task belongs to. The only public answer to that question.

    Every subsystem used to derive this for itself -- from artifact prefixes,
    from the status file's directory, from a fleet-wide default -- and the
    derivations disagreed. Five separate call sites were fixed on 2026-08-20
    for the same defect, one at a time, because being wrong was always
    reachable. This exists so there is one place to be right.

    Returns None when the task names a repository the registry does not know,
    or when artifacts span several: both mean "cannot answer", and a caller
    that treats them as an answer picks a repository the task never named.
    """
    binding = resolve_task_repository(config, task or {})
    return binding.repo_id or None


def _infer_repository_from_artifacts(config: dict[str, Any], task: dict[str, Any]) -> str | None:
    """Artifact-prefix inference, for tasks that declare no repository.

    Private on purpose. This is the *fallback* half of
    ``resolve_task_repository``, not an alternative to it: it cannot see a
    declared ``task.repository`` and will happily answer ``pantheon`` for a
    task that named something else. Callers outside this module must use
    ``task_repository_id`` or ``resolve_task_repository``, and
    ``test_repository_inference_is_not_reachable_outside_the_registry`` keeps
    that true.
    """
    repo_ids = task_artifact_repository_ids(config, task)
    non_pantheon = [repo_id for repo_id in repo_ids if repo_id != "pantheon"]
    if len(non_pantheon) == 1:
        return non_pantheon[0]
    if len(non_pantheon) > 1:
        return None
    return "pantheon"


def coordination_requests_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("requests_dir") or ".coordination/requests")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def coordination_responses_dir(config: dict[str, Any], repo_id: str | None) -> Path | None:
    base = repository_local_path(config, repo_id)
    if base is None:
        return None
    repo = resolve_repository(config, repo_id or "")
    rel = str(repo.get("responses_dir") or ".coordination/responses")
    return base / rel if not Path(rel).is_absolute() else Path(rel)


def iter_local_repositories(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per distinct local checkout, in registry order.

    Several registry ids can legitimately name the same checkout -- a fleet's
    own repository is commonly registered both as ``pantheon`` and under its
    real slug. Callers use this to walk the repositories present on disk, so
    yielding that checkout twice makes them process every file in it twice:
    the coordination watcher raises two dispatch events for one request file.
    The first registered id wins, which keeps path-to-repository attribution
    stable.
    """
    items: list[dict[str, Any]] = []
    seen_roots: set[Path] = set()
    for repo_id in repositories(config):
        resolved = resolve_repository(config, repo_id)
        local_path = resolved.get("resolved_local_path")
        if not isinstance(local_path, Path):
            continue
        try:
            key = local_path.resolve()
        except OSError:
            key = local_path
        if key in seen_roots:
            continue
        seen_roots.add(key)
        items.append(resolved)
    return items


def worker_routes(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(DEFAULT_WORKER_ROUTES)
    for worker_kind, override in (coordination_config(config).get("worker_routes", {}) or {}).items():
        current = merged.setdefault(worker_kind, {})
        current.update(deepcopy(override or {}))
    return merged


def worker_route(config: dict[str, Any], worker_kind: str | None) -> dict[str, Any] | None:
    if not worker_kind:
        return None
    return worker_routes(config).get(str(worker_kind).strip())


def resolve_worker_kind(alias: str | None) -> str | None:
    value = str(alias or "").strip().lower()
    if not value:
        return None
    if value in DEFAULT_WORKER_ROUTES:
        return value
    return WORKER_ALIASES.get(value)
