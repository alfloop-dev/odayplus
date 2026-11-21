#!/usr/bin/env python3
"""Validate code ownership, lifecycle, artifact, and import boundaries."""

from __future__ import annotations

import argparse
import ast
import csv
import io
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/code-boundaries.yaml"


@dataclass(frozen=True)
class ClassifiedFile:
    path: str
    boundary: str
    retention_class: str
    is_product_runtime: bool
    verified_scope: str = ""


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: manifest must be a mapping")
    return payload


@cache
def glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile an anchored repo-relative glob with zero-or-more ``**/``."""
    index = 0
    chunks = ["^"]
    while index < len(pattern):
        if pattern.startswith("**/", index):
            chunks.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            chunks.append(".*")
            index += 2
        elif pattern[index] == "*":
            chunks.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            chunks.append("[^/]")
            index += 1
        else:
            chunks.append(re.escape(pattern[index]))
            index += 1
    chunks.append("$")
    return re.compile("".join(chunks))


def matches(path: str, pattern: str) -> bool:
    return glob_regex(pattern).fullmatch(path) is not None


def boundary_matches(path: str, settings: dict[str, Any]) -> bool:
    includes = [str(pattern) for pattern in settings.get("include", [])]
    excludes = [str(pattern) for pattern in settings.get("exclude", [])]
    return any(matches(path, pattern) for pattern in includes) and not any(
        matches(path, pattern) for pattern in excludes
    )


def discover_code_files(root: Path, extensions: set[str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        path
        for path in result.stdout.splitlines()
        if Path(path).suffix in extensions and (root / path).is_file()
    )


def classify_files(paths: list[str], manifest: dict[str, Any]) -> tuple[list[ClassifiedFile], list[str]]:
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, dict) or not boundaries:
        return [], ["manifest boundaries must be a non-empty mapping"]

    errors: list[str] = []
    classified: list[ClassifiedFile] = []
    retention_classes = manifest.get("retention_classes", {})
    for path in paths:
        matches_for_path = [
            name
            for name, settings in boundaries.items()
            if isinstance(settings, dict) and boundary_matches(path, settings)
        ]
        if not matches_for_path:
            errors.append(f"unclassified code file: {path}")
            continue
        if len(matches_for_path) > 1:
            errors.append(
                f"code file matches multiple boundaries: {path}: {', '.join(matches_for_path)}"
            )
            continue
        name = matches_for_path[0]
        settings = boundaries[name]
        retention_class = str(settings.get("retention_class") or "")
        if retention_class not in retention_classes:
            errors.append(f"{name}: unknown retention_class {retention_class!r}")
        verified_scope = ""
        if name == "verification":
            verification = manifest.get("verification_ownership", {})
            verified_scope = str(verification.get("default_scope") or "")
            for rule in verification.get("rules", []):
                if not isinstance(rule, dict):
                    continue
                if any(matches(path, str(pattern)) for pattern in rule.get("include", [])):
                    verified_scope = str(rule.get("scope") or "")
                    break
            if verified_scope not in boundaries or verified_scope == "verification":
                errors.append(
                    f"verification file has invalid verified scope {verified_scope!r}: {path}"
                )
        classified.append(
            ClassifiedFile(
                path=path,
                boundary=name,
                retention_class=retention_class,
                is_product_runtime=settings.get("is_product_runtime") is True,
                verified_scope=verified_scope,
            )
        )
    return classified, errors


def validate_artifact_profiles(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    boundaries = manifest.get("boundaries", {})
    profiles = manifest.get("artifact_profiles", {})
    for profile_name, profile in profiles.items():
        scopes = profile.get("include_scopes", []) if isinstance(profile, dict) else []
        unknown = [scope for scope in scopes if scope not in boundaries]
        if unknown:
            errors.append(f"artifact profile {profile_name} has unknown scopes: {unknown}")

    production = profiles.get("production", {})
    production_scopes = set(production.get("include_scopes", []))
    non_product = [
        scope
        for scope in production_scopes
        if not bool(boundaries.get(scope, {}).get("is_product_runtime"))
    ]
    if non_product:
        errors.append(f"production artifact contains non-product scopes: {sorted(non_product)}")
    if not production_scopes:
        errors.append("production artifact profile must contain at least one scope")
    return errors


def validate_removal_bundles(
    classified: list[ClassifiedFile],
    manifest: dict[str, Any],
) -> list[str]:
    """Ensure each removable scope is physically contained by its declared roots."""

    errors: list[str] = []
    boundaries = set(manifest.get("boundaries", {}))
    for bundle_name, bundle in manifest.get("removal_bundles", {}).items():
        if not isinstance(bundle, dict):
            errors.append(f"removal bundle {bundle_name} must be a mapping")
            continue
        code_scopes = {str(scope) for scope in bundle.get("code_scopes", [])}
        verification_scopes = {
            str(scope) for scope in bundle.get("verification_scopes", [])
        }
        roots = tuple(str(root) for root in bundle.get("roots", []))
        unknown = (code_scopes | verification_scopes) - boundaries
        if unknown:
            errors.append(f"removal bundle {bundle_name} has unknown scopes: {sorted(unknown)}")
        if not roots:
            errors.append(f"removal bundle {bundle_name} must declare roots")
            continue

        for entry in classified:
            in_bundle = entry.boundary in code_scopes or (
                entry.boundary == "verification"
                and entry.verified_scope in verification_scopes
            )
            inside_root = entry.path.startswith(roots)
            if entry.boundary in code_scopes and not inside_root:
                errors.append(
                    f"removal bundle {bundle_name} misses {entry.path} ({entry.boundary})"
                )
            if inside_root and not in_bundle:
                errors.append(
                    f"removal bundle {bundle_name} root contains foreign scope: "
                    f"{entry.path} ({entry.boundary})"
                )
    return errors


def module_aliases(path: str) -> set[str]:
    source_path = PurePosixPath(path)
    if source_path.suffix != ".py":
        return set()
    without_suffix = source_path.with_suffix("")
    parts = list(without_suffix.parts)
    aliases: set[str] = set()
    if all(part.isidentifier() for part in parts):
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            aliases.add(".".join(parts))
    # The legacy orchestrator prepends its directory to sys.path and imports
    # sibling modules by stem. Index those aliases until the code is moved.
    if source_path.parts and source_path.parts[0] == ".orchestrator":
        aliases.add(source_path.stem)
    return aliases


def imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"cannot parse {path}: {exc}") from exc
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def resolve_local_scope(
    module: str,
    alias_to_scopes: dict[str, set[str]],
) -> set[str]:
    candidate = module
    while candidate:
        if candidate in alias_to_scopes:
            return alias_to_scopes[candidate]
        candidate = candidate.rpartition(".")[0]
    return set()


def validate_import_boundaries(
    root: Path,
    classified: list[ClassifiedFile],
    manifest: dict[str, Any],
    *,
    frozen_sources: frozenset[str] = frozenset(),
) -> list[str]:
    boundaries = manifest["boundaries"]
    scope_by_path = {entry.path: entry.boundary for entry in classified}
    alias_to_scopes: dict[str, set[str]] = defaultdict(set)
    for entry in classified:
        for alias in module_aliases(entry.path):
            alias_to_scopes[alias].add(entry.boundary)

    errors: list[str] = []
    ignored_scopes = {"archived", "evidence_artifact"}
    for path, source_scope in scope_by_path.items():
        # A frozen source is the exact upstream blob, certified by the
        # worktree policy. Its name still contributes to import resolution.
        if source_scope in ignored_scopes or path in frozen_sources:
            continue
        allowed = set(boundaries[source_scope].get("allowed_import_scopes", []))
        for module in imported_modules(root / path):
            target_scopes = resolve_local_scope(module, alias_to_scopes)
            disallowed = target_scopes - allowed
            if disallowed:
                errors.append(
                    f"forbidden import: {path} ({source_scope}) imports {module} "
                    f"from {', '.join(sorted(disallowed))}"
                )
    return errors


def inventory_text(classified: list[ClassifiedFile], manifest: dict[str, Any]) -> str:
    profiles = manifest.get("artifact_profiles", {})
    profile_scopes = {
        name: set(settings.get("include_scopes", []))
        for name, settings in profiles.items()
        if isinstance(settings, dict)
    }
    retention = manifest.get("retention_classes", {})
    output = io.StringIO(newline="")
    fields = [
        "path",
        "boundary",
        "retention_class",
        "is_product_runtime",
        "verified_scope",
        "artifact_profiles",
        "removable",
        "removal_condition",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for entry in sorted(classified, key=lambda item: item.path):
        policy = retention.get(entry.retention_class, {})
        included_profiles = sorted(
            name for name, scopes in profile_scopes.items() if entry.boundary in scopes
        )
        writer.writerow(
            {
                "path": entry.path,
                "boundary": entry.boundary,
                "retention_class": entry.retention_class,
                "is_product_runtime": str(entry.is_product_runtime).lower(),
                "verified_scope": entry.verified_scope,
                "artifact_profiles": ";".join(included_profiles),
                "removable": str(policy.get("removable", "")).lower(),
                "removal_condition": str(policy.get("removal_condition", "")),
            }
        )
    return output.getvalue()


def validate_repository(
    root: Path,
    manifest: dict[str, Any],
    *,
    check_inventory: bool = True,
    frozen_sources: frozenset[str] = frozenset(),
) -> tuple[list[ClassifiedFile], list[str]]:
    extensions = {str(value) for value in manifest.get("tracked_extensions", [])}
    paths = sorted(
        set(discover_code_files(root, extensions))
        | {path for path in frozen_sources if Path(path).suffix in extensions}
    )
    classified, errors = classify_files(paths, manifest)
    errors.extend(validate_artifact_profiles(manifest))
    errors.extend(validate_removal_bundles(classified, manifest))
    if not errors:
        errors.extend(validate_import_boundaries(root, classified, manifest, frozen_sources=frozen_sources))
    if check_inventory and not errors:
        inventory_path = root / str(manifest.get("inventory_path") or "")
        expected = inventory_text(classified, manifest)
        actual = inventory_path.read_text(encoding="utf-8") if inventory_path.exists() else ""
        if actual != expected:
            errors.append(
                "boundary inventory is stale: run delivery_toolchain/governance/check_code_boundaries.py --write-inventory"
            )
    return classified, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--upstream-ref", default="")
    parser.add_argument("--write-inventory", action="store_true")
    parser.add_argument("--skip-inventory-check", action="store_true")
    return parser.parse_args()


def verified_frozen_sources(args: argparse.Namespace, repo: Path, manifest_path: Path) -> frozenset[str]:
    if args.task_id is None and args.status_file is None:
        return frozenset()
    # Use the installed toolchain policy, including when preflighting a release
    # branch that predates this version of the checker.
    policy_root = str(ROOT / ".orchestrator")
    if policy_root not in sys.path:
        sys.path.insert(0, policy_root)
    from worktree_cleanliness import (
        frozen_tracked_files,
        protected_task_checkout_refusal,
        task_review_freeze,
    )

    protected, base = task_review_freeze(repo, args.task_id, args.status_file, args.upstream_ref)
    if base is None:
        return frozenset()
    refusal = protected_task_checkout_refusal(
        repo, "HEAD", protected, worktree_path=repo, check_index=True, frozen_base=base,
    )
    if refusal:
        raise ValueError(refusal)
    # The accepted upstream classification can only justify the missing
    # source when the candidate uses the same governing manifest.
    manifest_rel = DEFAULT_MANIFEST.relative_to(ROOT)
    if manifest_path != repo / manifest_rel or str(manifest_rel) in protected:
        raise ValueError("protected checkout requires the canonical code boundary manifest")
    expected = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}:{manifest_rel}"],
        cwd=repo, capture_output=True, check=False,
    )
    actual = subprocess.run(
        ["git", "hash-object", "--no-filters", "--", str(manifest_path)],
        cwd=repo, capture_output=True, check=False,
    )
    if expected.returncode or actual.returncode or not expected.stdout or expected.stdout != actual.stdout:
        raise ValueError("protected checkout requires the unchanged upstream code boundary manifest")
    return frozenset(frozen_tracked_files(repo, protected))


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    manifest_path = (args.manifest or repo / DEFAULT_MANIFEST.relative_to(ROOT)).resolve()
    try:
        frozen_sources = verified_frozen_sources(args, repo, manifest_path)
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as error:
        print(f"Code boundary checks failed: {error}", file=sys.stderr)
        return 1
    classified, errors = validate_repository(
        repo,
        manifest,
        check_inventory=not args.skip_inventory_check and not args.write_inventory,
        frozen_sources=frozen_sources,
    )
    if errors:
        print("Code boundary checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    if args.write_inventory:
        inventory_path = repo / str(manifest["inventory_path"])
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_text(inventory_text(classified, manifest), encoding="utf-8")
        print(f"Wrote {inventory_path.relative_to(repo)}")

    counts = Counter(entry.boundary for entry in classified)
    print(f"Code boundary checks passed for {len(classified)} files.")
    for boundary, count in sorted(counts.items()):
        print(f"- {boundary}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
