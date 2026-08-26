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


def test_manifest_digest_matches_canonical_payload() -> None:
    manifest = load_manifest()
    assert validate_manifest(manifest) == []
    assert manifest["manifest_digest"] == compute_manifest_digest(manifest)


def test_manifest_component_tag_mutation_fails_closed() -> None:
    manifest = load_manifest()
    image = manifest["components"]["api"]["image"]
    manifest["components"]["api"]["image"] = image.replace("@sha256:", ":mutable@sha256:")
    # The image is still syntactically digest-pinned, but the recorded
    # manifest identity is now stale and must not be accepted.
    errors = validate_manifest(manifest)
    assert any("manifest_digest" in error for error in errors)


def test_manifest_candidate_sha_mutation_fails_closed() -> None:
    manifest = load_manifest()
    manifest["candidate_sha"] = "0" * 40
    errors = validate_manifest(
        manifest,
        expected_candidate_sha="ace4265b5190c00c72846b637fc04850bacec77e",
    )
    assert any("candidate_sha" in error for error in errors)


def test_blocked_manifest_is_reviewable_but_not_admissible() -> None:
    manifest = load_manifest()
    assert manifest["release_status"] == "blocked"
    assert manifest["blockers"]
    assert validate_manifest(manifest) == []

    errors = validate_release_admission(manifest)
    assert any("release_status='ready'" in error for error in errors)
    assert any("non-empty manifest.sbom_refs" in error for error in errors)
    assert any("non-empty manifest.signature_refs" in error for error in errors)


def test_blocked_manifest_requires_blocker_record() -> None:
    manifest = load_manifest()
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
