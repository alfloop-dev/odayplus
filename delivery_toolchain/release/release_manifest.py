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
RELEASE_STATUSES = frozenset({"ready", "blocked"})

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

    release_status = manifest.get("release_status")
    if release_status is not None and release_status not in RELEASE_STATUSES:
        errors.append(
            f"manifest.release_status must be one of {sorted(RELEASE_STATUSES)}, "
            f"got: {release_status!r}"
        )
    if release_status == "blocked":
        blockers = manifest.get("blockers")
        if not isinstance(blockers, list) or not blockers:
            errors.append("blocked manifest must include a non-empty blockers list")

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


def validate_release_admission(manifest: Any) -> list[str]:
    """Return why a structurally valid manifest cannot be deployed.

    A blocked manifest is intentionally still hashable and reviewable: it is
    the immutable record of what was observed and why release stopped.  It is
    not, however, a deployable artifact.  Keeping this predicate separate from
    ``validate_manifest`` lets auditors inspect a blocked candidate without
    accidentally treating it as a successful release.
    """

    errors = validate_manifest(manifest)
    if not isinstance(manifest, dict):
        return errors
    # Manifests created before the status field was introduced remain
    # admissible when they contain the required immutable references.  New
    # manifests must explicitly move to ``ready`` before deployment; a
    # recorded ``blocked`` state can never be promoted implicitly.
    release_status = manifest.get("release_status", "ready")
    if release_status != "ready":
        errors.append(
            "release admission requires manifest.release_status='ready'; "
            f"got {release_status!r}"
        )
    for field in ("sbom_refs", "signature_refs"):
        refs = manifest.get(field)
        if not isinstance(refs, list) or not refs:
            errors.append(f"release admission requires non-empty manifest.{field}")
    return errors


def component_binding_errors(manifest: Any, images: dict[str, str]) -> list[str]:
    """Return why *images* are not the artifacts this manifest identifies.

    A lease authorises deploying *a release*, not *any image*.  The Runtime
    Release deploy phase receives its image references as workflow inputs, so
    without this check a valid lease would admit an arbitrary digest that the
    build phase never produced, signed, or recorded an SBOM for.  Binding the
    handoff back to ``manifest.components`` is what makes "build once, deploy
    that exact artifact" an enforced property rather than a convention.
    """

    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    components = manifest.get("components")
    if not isinstance(components, dict) or not components:
        return ["manifest.components must be a non-empty object"]

    errors: list[str] = []
    for name in sorted(images):
        image = images[name]
        if not isinstance(image, str) or not IMAGE_DIGEST_PATTERN.fullmatch(image):
            errors.append(
                f"handoff image for {name!r} must be an immutable @sha256 reference"
            )
            continue
        component = components.get(name)
        if not isinstance(component, dict):
            errors.append(
                f"manifest has no component {name!r}; this release never built that artifact"
            )
            continue
        if component.get("image") != image:
            errors.append(
                f"handoff image for {name!r} is not the image recorded by the manifest; "
                "the deploy would run an artifact this release did not build"
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
    release_status: str | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Build and self-seal a canonical release manifest dictionary.

    ``release_status`` is omitted by default so an existing manifest digest is
    unchanged by this parameter's introduction.  A build phase that has already
    published signed images and an SBOM passes ``"ready"`` explicitly; nothing
    may promote a manifest to ``ready`` implicitly.
    """
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
    if release_status is not None:
        manifest["release_status"] = release_status
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
    "component_binding_errors",
    "is_exact_sha",
    "is_sha256_digest",
    "load_manifest",
    "RELEASE_STATUSES",
    "validate_release_admission",
    "validate_manifest",
]


def _print_manifest_summary(manifest: dict[str, Any]) -> None:
    print(f"  Release ID:      {manifest['release_id']}")
    print(f"  Candidate SHA:   {manifest['candidate_sha']}")
    print(f"  Manifest digest: {manifest['manifest_digest']}")
    print(f"  Release status:  {manifest.get('release_status', 'ready')}")
    print(f"  Components:      {len(manifest['components'])}")
    for name, comp in manifest["components"].items():
        print(f"    - {name}: {comp['image']}")


def main(argv: list[str] | None = None) -> int:
    """Validate a release manifest and refuse to bless a non-admissible one.

    The default mode answers the question an auditor actually has -- "may this
    manifest be deployed?" -- not merely "is this file well formed?".  A
    manifest that parses cleanly but records ``release_status='blocked'`` is a
    NO-GO, so it must exit non-zero and must never print a success verdict;
    otherwise this command becomes the same kind of fake green light the
    release gates exist to eliminate.  Pure structural checking is still
    available, but only when it is asked for explicitly.
    """

    import argparse

    parser = argparse.ArgumentParser(
        description="Validate an ODay Plus release manifest and its admission status.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json",
        help="Path to release manifest JSON",
    )
    parser.add_argument("--expected-sha", type=str, default=None, help="Expected candidate SHA")
    parser.add_argument("--expected-digest", type=str, default=None, help="Expected manifest digest")
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help=(
            "Check schema and digest self-consistency only, without deciding "
            "release admission. Never reports a deployable verdict."
        ),
    )
    args = parser.parse_args(argv)

    manifest, errors = load_manifest(
        args.manifest,
        expected_candidate_sha=args.expected_sha,
        expected_digest=args.expected_digest,
    )
    if errors:
        print(f"INVALID: {len(errors)} structural error(s) in release manifest {args.manifest}:")
        for err in errors:
            print(f"  - {err}")
        return 1

    assert manifest is not None  # load_manifest reports an error when it is None

    if args.structure_only:
        print(f"STRUCTURE-OK: {args.manifest} is schema valid and digest self-consistent.")
        print("  Release admission was NOT evaluated (--structure-only).")
        _print_manifest_summary(manifest)
        return 0

    admission_errors = validate_release_admission(manifest)
    if admission_errors:
        print(f"BLOCKED: release manifest {args.manifest} is NOT admissible for deployment.")
        _print_manifest_summary(manifest)
        print(f"  Admission refused for {len(admission_errors)} reason(s):")
        for err in admission_errors:
            print(f"    - {err}")
        blockers = manifest.get("blockers")
        if isinstance(blockers, list) and blockers:
            print(f"  Recorded blockers ({len(blockers)}):")
            for blocker in blockers:
                if isinstance(blocker, dict):
                    print(
                        f"    - [{blocker.get('severity', '?')}] "
                        f"{blocker.get('id', '?')}: {blocker.get('reason', '')}"
                    )
                else:
                    print(f"    - {blocker}")
        return 1

    print(f"ADMISSIBLE: release manifest {args.manifest} may be deployed.")
    _print_manifest_summary(manifest)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
