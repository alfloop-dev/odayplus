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
    compute_data_contract_digest,
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


def valid_data_snapshot(contract_digest: str | None = None) -> dict:
    return {
        "id": "snap-test-001",
        "uri": "gs://odayplus-snapshots/masked/snap-test-001.tar.gz",
        "content_sha256": "sha256:" + "d" * 64,
        "data_contract_digest": contract_digest or compute_data_contract_digest(root=ROOT),
        "masked": True,
    }


def valid_rollback_release(current_sha: str = "1" * 40) -> dict:
    prev_sha = "0" * 40 if current_sha != "0" * 40 else "9" * 40
    return {
        "release_id": "odp-test-prev",
        "candidate_sha": prev_sha,
        "manifest_digest": "sha256:" + "e" * 64,
        "components": {
            "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "f" * 64},
            "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "a" * 64},
        },
        "data_snapshot": {
            "id": "snap-prev-001",
            "uri": "gs://odayplus-snapshots/masked/snap-prev-001.tar.gz",
            "content_sha256": "sha256:" + "b" * 64,
            "data_contract_digest": "sha256:" + "c" * 64,
            "masked": True,
        },
    }


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
    signature references. Legacy v1 remains readable for historical audit, but
    admission fails closed without snapshot and rollback bindings.
    """

    manifest = load_manifest()

    if manifest.get("release_status", "ready") == "ready":
        assert manifest["components"], "a ready manifest must have something to deploy"
        assert manifest["sbom_refs"]
        assert manifest["signature_refs"]
        assert validate_manifest(manifest) == []
        # Committed v1 manifest lacks snapshot and rollback bindings, so admission fails closed
        assert validate_release_admission(manifest)
    else:
        assert manifest["blockers"], "a blocked manifest must record why it is blocked"
        assert validate_release_admission(manifest)


def built_manifest(**overrides) -> dict:
    """Build a self-sealed manifest that carries immutable components and bindings.

    Component-level mutation coverage builds its own subject instead of
    borrowing the release of the day.
    """

    candidate_sha = overrides.pop("candidate_sha", "1" * 40)
    data_snapshot = overrides.pop("data_snapshot", valid_data_snapshot())
    rollback_release = overrides.pop("rollback_release", valid_rollback_release(candidate_sha))
    manifest = build_release_manifest(
        release_id=overrides.pop("release_id", "odp-test-0001"),
        candidate_sha=candidate_sha,
        components=overrides.pop(
            "components",
            {
                "api": {
                    "image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64
                },
                "web": {
                    "image": "registry.example.invalid/odayplus/web@sha256:" + "9" * 64
                },
            },
        ),
        sbom_refs=overrides.pop("sbom_refs", ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64]),
        signature_refs=overrides.pop("signature_refs", ["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64]),
        created_at=overrides.pop("created_at", "2026-08-26T00:00:00Z"),
        created_by_workflow=overrides.pop("created_by_workflow", "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml"),
        data_snapshot=data_snapshot,
        rollback_release=rollback_release,
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


def test_v1_manifest_is_structurally_valid_for_audit_but_admission_fails_closed() -> None:
    manifest = built_manifest(schema_version=1)
    manifest.pop("data_snapshot", None)
    manifest.pop("rollback_release", None)
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    # v1 manifest is structurally valid for audit
    assert validate_manifest(manifest) == []

    # but staging/production admission fails closed without snapshot and rollback
    errors = validate_release_admission(manifest)
    assert any("manifest.data_snapshot" in err for err in errors)
    assert any("manifest.rollback_release" in err for err in errors)


def test_v2_manifest_requires_data_snapshot_and_rollback_release() -> None:
    manifest_no_snap = built_manifest(schema_version=2)
    manifest_no_snap.pop("data_snapshot", None)
    manifest_no_snap["manifest_digest"] = compute_manifest_digest(manifest_no_snap)
    assert any("data_snapshot" in err for err in validate_manifest(manifest_no_snap))

    manifest_no_rb = built_manifest(schema_version=2)
    manifest_no_rb.pop("rollback_release", None)
    manifest_no_rb["manifest_digest"] = compute_manifest_digest(manifest_no_rb)
    assert any("rollback_release" in err for err in validate_manifest(manifest_no_rb))


def test_data_snapshot_unmasked_fails_closed() -> None:
    snap = valid_data_snapshot()
    snap["masked"] = False
    manifest = built_manifest(data_snapshot=snap)
    errors = validate_manifest(manifest)
    assert any("masked must be True" in err for err in errors)


def test_data_snapshot_invalid_sha256_fails_closed() -> None:
    snap = valid_data_snapshot()
    snap["content_sha256"] = "invalid_hash"
    manifest = built_manifest(data_snapshot=snap)
    errors = validate_manifest(manifest)
    assert any("content_sha256 must be a sha256:" in err for err in errors)


def test_data_snapshot_contract_digest_mismatch_fails_closed() -> None:
    snap = valid_data_snapshot()
    snap["data_contract_digest"] = "sha256:" + "0" * 64
    manifest = built_manifest(data_snapshot=snap)
    errors = validate_manifest(manifest)
    assert any("data_contract_digest does not match" in err for err in errors)


def test_rollback_release_same_candidate_sha_fails_closed() -> None:
    rb = valid_rollback_release("1" * 40)
    rb["candidate_sha"] = "1" * 40
    manifest = built_manifest(candidate_sha="1" * 40, rollback_release=rb)
    errors = validate_manifest(manifest)
    assert any("must not match current candidate_sha" in err for err in errors)


def test_rollback_release_mutable_tag_fails_closed() -> None:
    rb = valid_rollback_release("1" * 40)
    rb["components"]["api"]["image"] = "registry.example.invalid/odayplus/api:latest"
    manifest = built_manifest(candidate_sha="1" * 40, rollback_release=rb)
    errors = validate_manifest(manifest)
    assert any("immutable image reference with @sha256 digest" in err for err in errors)


def test_rollback_release_missing_web_or_api_fails_closed() -> None:
    rb = valid_rollback_release("1" * 40)
    rb["components"].pop("web", None)
    manifest = built_manifest(candidate_sha="1" * 40, rollback_release=rb)
    errors = validate_manifest(manifest)
    assert any("missing required component: 'web'" in err for err in errors)


def test_rollback_release_missing_snapshot_pointer_fails_closed() -> None:
    rb = valid_rollback_release("1" * 40)
    rb.pop("data_snapshot", None)
    manifest = built_manifest(candidate_sha="1" * 40, rollback_release=rb)
    errors = validate_manifest(manifest)
    assert any("missing required data_snapshot pointer" in err for err in errors)


def test_byte_determinism_and_tampering_detection() -> None:
    m1 = built_manifest()
    m2 = built_manifest()
    assert m1 == m2
    assert m1["manifest_digest"] == m2["manifest_digest"]
    assert validate_manifest(m1) == []
    assert validate_release_admission(m1) == []

    # Tampering with snapshot id breaks digest
    tampered_snap = copy.deepcopy(m1)
    tampered_snap["data_snapshot"]["id"] = "tampered-id"
    errors = validate_manifest(tampered_snap)
    assert any("manifest_digest" in err for err in errors)

    # Tampering with rollback digest breaks digest
    tampered_rb = copy.deepcopy(m1)
    tampered_rb["rollback_release"]["manifest_digest"] = "sha256:" + "0" * 64
    errors = validate_manifest(tampered_rb)
    assert any("manifest_digest" in err for err in errors)

    # Tampering with component image breaks digest
    tampered_comp = copy.deepcopy(m1)
    tampered_comp["components"]["api"]["image"] = "registry.example.invalid/odayplus/api@sha256:" + "0" * 64
    errors = validate_manifest(tampered_comp)
    assert any("manifest_digest" in err for err in errors)

