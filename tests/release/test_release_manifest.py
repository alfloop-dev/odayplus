"""Mutation and migration coverage for the immutable release contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from delivery_toolchain.e2e.check_release_gate_registry import validate_registry
from delivery_toolchain.release.migrate_gate_registry import (
    RegistryMigrationError,
    migrate_registry,
)
from delivery_toolchain.release.release_manifest import (
    build_release_manifest,
    compute_manifest_digest,
    validate_manifest,
    validate_release_admission,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
REGISTRY_PATH = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def blocked_manifest() -> dict:
    """Return the committed manifest reduced to a blocked candidate.

    The committed manifest tracks whichever candidate was last built, so its
    ``release_status`` flips as releases come and go.  Deriving the blocked
    shape here keeps the blocked-path assertions about the validator instead of
    about today's release state.
    """

    manifest = load_manifest()
    manifest["release_status"] = "blocked"
    manifest["components"] = {}
    manifest["sbom_refs"] = []
    manifest["signature_refs"] = []
    manifest["blockers"] = [
        {
            "id": "TEST-BLOCKER-001",
            "severity": "P0",
            "reason": "Synthetic blocker; this manifest never produced an artifact.",
            "evidence_ref": "docs/evidence/gates/README.md",
        }
    ]
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def test_manifest_digest_matches_canonical_payload() -> None:
    manifest = load_manifest()
    assert validate_manifest(manifest) == []
    assert manifest["manifest_digest"] == compute_manifest_digest(manifest)


def test_committed_manifest_and_registry_describe_one_candidate() -> None:
    """The registry may only quote a manifest that agrees with it.

    A registry that names one candidate while the manifest names another is the
    drift the digest binding exists to catch, and it is exactly what a candidate
    rebind gets wrong when the manifest is not regenerated with it.
    """

    manifest = load_manifest()
    release = load_registry()["release"]

    assert manifest["candidate_sha"] == release["candidate_sha"]
    assert manifest["manifest_digest"] == release["manifest_digest"]


def test_committed_manifest_is_honest_about_whether_it_has_an_artifact() -> None:
    """Whichever state the candidate of the day is in, it must be consistent.

    ``ready`` means the build really published immutable images plus SBOM and
    signature references, so admission must fail for no artifact-shaped reason.
    ``blocked`` means the opposite and must name why.  Nothing in between is a
    representable release identity.
    """

    manifest = load_manifest()

    if manifest.get("release_status", "ready") == "ready":
        assert manifest["components"], "a ready manifest must have something to deploy"
        assert manifest["sbom_refs"]
        assert manifest["signature_refs"]
        assert validate_release_admission(manifest) == []
    else:
        assert manifest["blockers"], "a blocked manifest must record why it is blocked"
        assert validate_release_admission(manifest)


def built_manifest(**overrides) -> dict:
    """Build a self-sealed manifest that carries one immutable component.

    The committed manifest is whatever the current candidate happens to be, and
    a blocked candidate legitimately has no components at all.  Component-level
    mutation coverage therefore builds its own subject instead of borrowing the
    release of the day.
    """

    manifest = build_release_manifest(
        release_id="odp-test-0001",
        candidate_sha="1" * 40,
        components={
            "api": {
                "image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64
            }
        },
        sbom_refs=["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64],
        signature_refs=["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64],
        created_at="2026-08-26T00:00:00Z",
        created_by_workflow="github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml",
    )
    manifest.update(overrides)
    return manifest


def test_manifest_component_tag_mutation_fails_closed() -> None:
    manifest = built_manifest()
    image = manifest["components"]["api"]["image"]
    manifest["components"]["api"]["image"] = image.replace("@sha256:", ":mutable@sha256:")
    # The image is still syntactically digest-pinned, but the recorded
    # manifest identity is now stale and must not be accepted.
    errors = validate_manifest(manifest)
    assert any("manifest_digest" in error for error in errors)


def test_blocked_manifest_may_record_that_no_candidate_image_exists() -> None:
    """A candidate that never built must not have to quote someone else's digests."""

    manifest = built_manifest(components={}, release_status="blocked")
    manifest["blockers"] = [
        {"id": "TEST-NO-IMAGE", "severity": "P0", "reason": "no candidate image"}
    ]
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    assert validate_manifest(manifest) == []


def test_empty_components_are_rejected_unless_the_manifest_is_blocked() -> None:
    for status in ("ready", None):
        manifest = built_manifest(components={})
        if status is None:
            manifest.pop("release_status", None)
        else:
            manifest["release_status"] = status
        manifest["manifest_digest"] = compute_manifest_digest(manifest)

        errors = validate_manifest(manifest)
        assert any("manifest.components" in error for error in errors), (status, errors)


def test_empty_components_are_never_admissible() -> None:
    """Forcing the status to ready must not turn an empty release into a deployable one."""

    manifest = built_manifest(components={}, release_status="ready")
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_release_admission(manifest)
    assert any("at least one immutable component image" in error for error in errors)


def test_manifest_candidate_sha_mutation_fails_closed() -> None:
    manifest = load_manifest()
    manifest["candidate_sha"] = "0" * 40
    errors = validate_manifest(
        manifest,
        expected_candidate_sha=load_registry()["release"]["candidate_sha"],
    )
    assert any("candidate_sha" in error for error in errors)


def test_blocked_manifest_is_reviewable_but_not_admissible() -> None:
    manifest = blocked_manifest()
    assert manifest["blockers"]
    assert validate_manifest(manifest) == []

    errors = validate_release_admission(manifest)
    assert any("release_status='ready'" in error for error in errors)
    assert any("non-empty manifest.sbom_refs" in error for error in errors)
    assert any("non-empty manifest.signature_refs" in error for error in errors)


def test_blocked_manifest_requires_blocker_record() -> None:
    manifest = blocked_manifest()
    manifest["blockers"] = []
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any("non-empty blockers list" in error for error in errors)


def test_registry_stage_contract_breaks_closed() -> None:
    registry = load_registry()
    registry["gates"][0]["stage"] = "dev-verified"
    registry["gates"][0]["environment"] = "staging"
    registry["gates"][0]["admission_target"] = "staging"
    errors = validate_registry(registry, ROOT)
    assert any("environment" in error and "dev-verified" in error for error in errors)


def test_staging_admission_is_dev_verified_not_staging_verified() -> None:
    registry = load_registry()
    gate = registry["gates"][0]
    gate["stage"] = "dev-verified"
    gate["environment"] = "dev"
    gate["admission_target"] = "staging"
    assert validate_registry(registry, ROOT) == []


def test_legacy_migration_adds_identity_and_requires_re_attestation() -> None:
    legacy = copy.deepcopy(load_registry())
    legacy["schema_version"] = "1.0.0"
    legacy.pop("migration")
    for key in ("manifest_ref", "manifest_digest", "stage", "environment", "admission_target"):
        legacy["release"].pop(key, None)
    for gate in legacy["gates"]:
        for key in ("stage", "environment", "admission_target"):
            gate.pop(key, None)

    migrated = migrate_registry(
        legacy,
        load_manifest(),
        migrated_at="2026-08-24T12:00:00Z",
    )
    assert migrated["schema_version"] == "2.0.0"
    assert migrated["migration"]["re_attestation_required"] is True
    assert migrated["release"]["manifest_digest"] == load_manifest()["manifest_digest"]
    assert validate_registry(migrated, ROOT) == []


def test_legacy_migration_rejects_manifest_for_another_candidate() -> None:
    legacy = copy.deepcopy(load_registry())
    legacy["schema_version"] = "1.0.0"
    legacy.pop("migration")
    for key in ("manifest_ref", "manifest_digest", "stage", "environment", "admission_target"):
        legacy["release"].pop(key, None)
    for gate in legacy["gates"]:
        for key in ("stage", "environment", "admission_target"):
            gate.pop(key, None)
    manifest = load_manifest()
    manifest["candidate_sha"] = "0" * 40
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    with pytest.raises(RegistryMigrationError):
        migrate_registry(legacy, manifest)
