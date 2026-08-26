#!/usr/bin/env python3
"""Schema and digest helpers for the immutable ODay Plus release manifest.

The release manifest is the artifact identity shared by dev, ephemeral
staging, and production.  A deployment may add environment metadata around a
manifest, but it must never rebuild or rewrite the manifest itself.  The
``manifest_digest`` field is the SHA-256 of the canonical JSON payload with
that field removed; this makes the self-described file independently
verifiable without creating a hash cycle.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = (1,)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")

REQUIRED_FIELDS = (
    "schema_version",
    "release_id",
    "candidate_sha",
    "components",
    "migration_digest",
    "data_contract_digest",
    "source_policy_digest",
    "external_sources_expected_enabled",
    "sbom_refs",
    "signature_refs",
    "created_at",
    "created_by_workflow",
    "manifest_digest",
)


def is_exact_sha(value: Any) -> bool:
    """Return whether *value* is a lowercase 40-character git SHA."""

    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}", value))


def is_sha256_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def canonical_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable payload used for manifest identity hashing."""

    payload = copy.deepcopy(manifest)
    payload.pop("manifest_digest", None)
    return payload


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(
        canonical_payload(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_manifest_digest(manifest: dict[str, Any]) -> str:
    """Compute the self-describing SHA-256 manifest identity."""

    return "sha256:" + hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def is_valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_manifest(
    manifest: Any,
    *,
    expected_candidate_sha: str | None = None,
    expected_digest: str | None = None,
) -> list[str]:
    """Return all manifest integrity errors; an empty list means valid.

    The validator deliberately requires every deployment identity field.  It
    does not accept mutable tags, abbreviated SHAs, or an omitted digest as a
    substitute for an immutable artifact reference.
    """

    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"manifest missing required field: {field}")

    version = manifest.get("schema_version")
    if isinstance(version, bool) or version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"manifest.schema_version must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, "
            f"got: {version!r}"
        )

    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append("manifest.release_id must be a stable release identifier")

    candidate_sha = manifest.get("candidate_sha")
    if not is_exact_sha(candidate_sha):
        errors.append(
            "manifest.candidate_sha must be an exact 40-character lowercase git SHA"
        )
    if expected_candidate_sha and candidate_sha != expected_candidate_sha:
        errors.append(
            "manifest.candidate_sha does not match release.candidate_sha; "
            "the manifest is for a different candidate"
        )

    components = manifest.get("components")
    if not isinstance(components, dict) or not components:
        errors.append("manifest.components must be a non-empty object")
    elif not all(isinstance(name, str) and name.strip() for name in components):
        errors.append("manifest.components names must be non-empty strings")
    else:
        for name, component in components.items():
            label = f"manifest.components[{name!r}]"
            if not isinstance(component, dict):
                errors.append(f"{label} must be an object")
                continue
            image = component.get("image")
            if not isinstance(image, str) or not IMAGE_DIGEST_PATTERN.fullmatch(image):
                errors.append(
                    f"{label}.image must be an immutable image reference with @sha256 digest"
                )

    for field in ("migration_digest", "data_contract_digest", "source_policy_digest"):
        if not is_sha256_digest(manifest.get(field)):
            errors.append(f"manifest.{field} must be a sha256:<64 lowercase hex> digest")

    sources = manifest.get("external_sources_expected_enabled")
    if not isinstance(sources, list) or not all(
        isinstance(source, str) and source.strip() for source in sources
    ):
        errors.append(
            "manifest.external_sources_expected_enabled must be a list of non-empty strings"
        )

    for field in ("sbom_refs", "signature_refs"):
        refs = manifest.get(field)
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref.strip() for ref in refs
        ):
            errors.append(f"manifest.{field} must be a list of non-empty strings")

    if not is_valid_timestamp(manifest.get("created_at")):
        errors.append("manifest.created_at must be an RFC3339 timestamp with timezone")
    if not isinstance(manifest.get("created_by_workflow"), str) or not manifest.get(
        "created_by_workflow"
    ).strip():
        errors.append("manifest.created_by_workflow must be a non-empty workflow reference")

    recorded_digest = manifest.get("manifest_digest")
    if not is_sha256_digest(recorded_digest):
        errors.append("manifest.manifest_digest must be a sha256:<64 lowercase hex> digest")
    else:
        actual_digest = compute_manifest_digest(manifest)
        if recorded_digest != actual_digest:
            errors.append(
                "manifest.manifest_digest does not match its canonical immutable payload"
            )
        if expected_digest and recorded_digest != expected_digest:
            errors.append(
                "manifest.manifest_digest does not match the digest recorded by the registry"
            )

    return errors


def load_manifest(
    path: Path,
    *,
    expected_candidate_sha: str | None = None,
    expected_digest: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate one manifest, returning errors instead of guessing."""

    if not path.exists():
        return None, [f"manifest file does not exist: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"manifest cannot be read as JSON: {exc}"]
    errors = validate_manifest(
        payload,
        expected_candidate_sha=expected_candidate_sha,
        expected_digest=expected_digest,
    )
    return (payload if isinstance(payload, dict) else None), errors


ROOT = Path(__file__).resolve().parents[2]


def compute_file_set_digest(paths: Any, *, root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over a sequence of file paths."""
    h = hashlib.sha256()
    for p in sorted(paths):
        path_obj = Path(p)
        if path_obj.is_file():
            rel_path = path_obj.relative_to(root).as_posix().encode("utf-8")
            h.update(rel_path)
            h.update(b"\x00")
            h.update(path_obj.read_bytes())
            h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def compute_migration_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over infra/db/migrations SQL files."""
    migrations_dir = root / "infra/db/migrations"
    return compute_file_set_digest(migrations_dir.glob("*.sql"), root=root)


def compute_data_contract_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over docs/data contract files."""
    data_dir = root / "docs/data"
    return compute_file_set_digest(data_dir.glob("*"), root=root)


def compute_source_policy_digest(root: Path = ROOT) -> str:
    """Compute deterministic SHA-256 digest over security/license policies."""
    policy_files = [
        root / "docs/security/license_policy.json",
        root / "docs/security/license_exemptions.json",
        root / "docs/security/release_bindings.json",
    ]
    return compute_file_set_digest(policy_files, root=root)


def build_release_manifest(
    *,
    release_id: str,
    candidate_sha: str,
    components: dict[str, dict[str, str]],
    sbom_refs: list[str],
    signature_refs: list[str],
    created_at: str,
    created_by_workflow: str,
    external_sources_expected_enabled: list[str] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Build and self-seal a canonical release manifest dictionary."""
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": release_id,
        "candidate_sha": candidate_sha,
        "components": components,
        "migration_digest": compute_migration_digest(root=root),
        "data_contract_digest": compute_data_contract_digest(root=root),
        "source_policy_digest": compute_source_policy_digest(root=root),
        "external_sources_expected_enabled": external_sources_expected_enabled or [],
        "sbom_refs": sbom_refs,
        "signature_refs": signature_refs,
        "created_at": created_at,
        "created_by_workflow": created_by_workflow,
    }
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


__all__ = [
    "ROOT",
    "SUPPORTED_SCHEMA_VERSIONS",
    "build_release_manifest",
    "compute_data_contract_digest",
    "compute_file_set_digest",
    "compute_manifest_digest",
    "compute_migration_digest",
    "compute_source_policy_digest",
    "is_exact_sha",
    "is_sha256_digest",
    "load_manifest",
    "validate_manifest",
]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate or inspect an ODay Plus release manifest.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json",
        help="Path to release manifest JSON",
    )
    parser.add_argument("--expected-sha", type=str, default=None, help="Expected candidate SHA")
    parser.add_argument("--expected-digest", type=str, default=None, help="Expected manifest digest")
    args = parser.parse_args()

    manifest, errors = load_manifest(
        args.manifest,
        expected_candidate_sha=args.expected_sha,
        expected_digest=args.expected_digest,
    )
    if errors:
        print(f"FAIL: {len(errors)} error(s) in release manifest {args.manifest}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"PASS: Release manifest {args.manifest} is valid.")
    print(f"  Release ID:     {manifest['release_id']}")
    print(f"  Candidate SHA:  {manifest['candidate_sha']}")
    print(f"  Manifest Digest:{manifest['manifest_digest']}")
    print(f"  Components:     {len(manifest['components'])}")
    for name, comp in manifest["components"].items():
        print(f"    - {name}: {comp['image']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

