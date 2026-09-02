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
    EXPECTED_EXTERNAL_SOURCE_COUNT,
    EXTERNAL_SOURCE_INVENTORY,
    SOURCES_OFF_CLOUD_RUN_EGRESS,
    SOURCES_OFF_EGRESS_POSTURE,
    SOURCES_OFF_PROVIDER_MODE,
    SOURCES_OFF_RUNTIME_PROBE_REASON,
    SOURCES_OFF_RUNTIME_PROBE_RESULT,
    build_release_manifest,
    build_sources_off_attestation,
    classify_source_env_var,
    compute_data_contract_digest,
    compute_manifest_digest,
    compute_source_policy_digest,
    compute_sources_off_binding_digest,
    compute_sources_off_probe_receipt_content_digest,
    env_var_belongs_to_source,
    extract_rollback_release_binding,
    sources_off_posture_payload,
    validate_manifest,
    validate_release_admission,
    validate_rollback_manifest,
    validate_sources_off_probe_receipt,
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
        "object_generation": 123,
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
            "object_generation": 122,
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


def test_data_snapshot_missing_object_generation_fails_closed() -> None:
    snap = valid_data_snapshot()
    snap.pop("object_generation")
    manifest = built_manifest(data_snapshot=snap)

    errors = validate_manifest(manifest)
    assert any(
        "data_snapshot missing required field: object_generation" in err
        for err in errors
    )


@pytest.mark.parametrize("generation", [-1, True, 1.5, "generation-1"])
def test_data_snapshot_invalid_object_generation_fails_closed(generation) -> None:
    snap = valid_data_snapshot()
    snap["object_generation"] = generation
    manifest = built_manifest(data_snapshot=snap)

    errors = validate_manifest(manifest)
    assert any("object_generation must be a non-negative integer" in err for err in errors)


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


def test_validate_rollback_manifest_valid_manifest() -> None:
    prev = built_manifest(candidate_sha="0" * 40, release_id="odp-prev-001")
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert errors == []


def test_validate_rollback_manifest_forged_digest_fails_closed() -> None:
    prev = built_manifest(candidate_sha="0" * 40)
    prev["manifest_digest"] = "sha256:" + "f" * 64
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert any("manifest_digest" in err for err in errors)


def test_validate_rollback_manifest_tampered_component_fails_closed() -> None:
    prev = built_manifest(candidate_sha="0" * 40)
    prev["components"]["api"]["image"] = "registry.example.invalid/odayplus/api@sha256:" + "f" * 64
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert any("manifest_digest" in err for err in errors)


def test_validate_rollback_manifest_legacy_v1_fails_closed() -> None:
    prev = built_manifest(schema_version=1)
    prev.pop("data_snapshot", None)
    prev.pop("rollback_release", None)
    prev["manifest_digest"] = compute_manifest_digest(prev)
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert any("data_snapshot" in err for err in errors)


def test_validate_rollback_manifest_blocked_fails_closed() -> None:
    prev = blocked_manifest()
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert any("release_status='ready'" in err for err in errors)


def test_validate_rollback_manifest_same_candidate_fails_closed() -> None:
    prev = built_manifest(candidate_sha="1" * 40)
    errors = validate_rollback_manifest(prev, current_candidate_sha="1" * 40)
    assert any("must not match current candidate_sha" in err for err in errors)


def test_validate_rollback_manifest_same_release_id_fails_closed() -> None:
    prev = built_manifest(candidate_sha="0" * 40, release_id="odp-current-001")
    errors = validate_rollback_manifest(
        prev,
        current_candidate_sha="1" * 40,
        current_release_id="odp-current-001",
    )
    assert any("must not match current release_id" in error for error in errors)


def test_extract_rollback_release_binding_retains_exact_identity() -> None:
    prev = built_manifest(candidate_sha="0" * 40, release_id="odp-prev-001")
    binding = extract_rollback_release_binding(prev)
    assert binding["release_id"] == "odp-prev-001"
    assert binding["candidate_sha"] == "0" * 40
    assert binding["manifest_digest"] == prev["manifest_digest"]
    assert binding["components"]["api"]["image"] == prev["components"]["api"]["image"]
    assert binding["data_snapshot"] == prev["data_snapshot"]


# ---------------------------------------------------------------------------
# Sources-off release admission (ODP-SOURCES-OFF-RELEASE-ADMISSION-REMEDIATION-001)
# ---------------------------------------------------------------------------
#
# "All external sources stay closed" is the standing posture of this product,
# not a missing artifact. These tests pin both halves of that: a sources-off
# release is admissible on bound posture evidence, and every way of weakening
# that evidence -- an enabled source, a provider credential, open egress, a
# borrowed binding, or using the posture to walk away from a snapshot binding
# that already exists -- still fails closed.

SOURCES_OFF_COMPONENTS = {
    "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64},
    "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "9" * 64},
}


def clean_sources_inventory() -> list[dict]:
    return [
        {
            "source_id": source_id,
            "status": "disabled",
            "credentials_present": False,
            "public_egress": "denied",
        }
        for source_id in EXTERNAL_SOURCE_INVENTORY
    ]


def sources_off_rollback_release(current_sha: str = "1" * 40) -> dict:
    """A previous release that was itself admitted with sources off."""

    rollback = valid_rollback_release(current_sha)
    rollback.pop("data_snapshot")
    rollback["sources_off_attestation"] = {"binding_digest": "sha256:" + "d" * 64}
    return rollback


def sources_off_manifest(**overrides) -> dict:
    candidate_sha = overrides.pop("candidate_sha", "1" * 40)
    components = overrides.pop("components", copy.deepcopy(SOURCES_OFF_COMPONENTS))
    inventory = overrides.pop("sources_inventory", clean_sources_inventory())
    provider_mode = overrides.pop("provider_mode", SOURCES_OFF_PROVIDER_MODE)
    binding_components = overrides.pop("binding_components", components)
    binding_candidate_sha = overrides.pop("binding_candidate_sha", candidate_sha)
    binding_source_policy_digest = overrides.pop(
        "binding_source_policy_digest", compute_source_policy_digest(root=ROOT)
    )
    attestation = overrides.pop(
        "sources_off_attestation",
        build_sources_off_attestation(
            candidate_sha=binding_candidate_sha,
            components=binding_components,
            source_policy_digest=binding_source_policy_digest,
            provider_mode=provider_mode,
            sources_inventory=inventory,
        ),
    )
    manifest = build_release_manifest(
        release_id=overrides.pop("release_id", "odp-test-sources-off"),
        candidate_sha=candidate_sha,
        components=components,
        sbom_refs=overrides.pop(
            "sbom_refs",
            ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64],
        ),
        signature_refs=overrides.pop(
            "signature_refs",
            ["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64],
        ),
        created_at=overrides.pop("created_at", "2026-08-26T00:00:00Z"),
        created_by_workflow=overrides.pop(
            "created_by_workflow",
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml",
        ),
        data_snapshot=overrides.pop("data_snapshot", None),
        sources_off_attestation=attestation,
        rollback_release=overrides.pop(
            "rollback_release", sources_off_rollback_release(candidate_sha)
        ),
        release_status="ready",
        root=ROOT,
    )
    manifest.update(overrides)
    if overrides:
        manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def test_canonical_source_inventory_matches_the_dev_rollout_audit() -> None:
    """The schema-side inventory and the deployed audit must name one set."""

    audit = json.loads(
        (
            ROOT
            / "docs/evidence/runtime/ODP-DEV-ROLLOUT-001"
            / "external-sources-provider-off-audit.json"
        ).read_text(encoding="utf-8")
    )
    assert EXPECTED_EXTERNAL_SOURCE_COUNT == 16
    assert audit["total_sources_audited"] == EXPECTED_EXTERNAL_SOURCE_COUNT
    assert {entry["source_id"] for entry in audit["sources_inventory"]} == set(
        EXTERNAL_SOURCE_INVENTORY
    )


def _registry_env_vars() -> tuple[set[str], set[str]]:
    """讀 runtime provider registry 真正宣告的 credential / endpoint 變數名。

    這個檔案在 ``tests/**``，是 disposition record 明文允許持有 provider
    credential 名稱的路徑之一；``delivery_toolchain/release/`` 不是。所以「名單」
    留在測試這一側，production 端只留形狀判斷。
    """

    from modules.external_data.connectors.provider_registry import provider_registry

    credentials: set[str] = set()
    endpoints: set[str] = set()
    for provider in provider_registry():
        for credential in provider.credentials:
            credentials.add(credential.env_var)
        if provider.endpoint_env_var:
            endpoints.add(provider.endpoint_env_var)
    return credentials, endpoints


def test_every_registry_credential_is_attributed_to_a_source_as_a_credential() -> None:
    """形狀判斷必須完整覆蓋 registry 宣告的每一個 provider credential。

    這是取代「把名單抄一份到 release 端」的保護：release code 不再記得任何
    provider 變數名，所以改由測試證明它推導出來的結果和封閉的 registry 一致。
    """

    registry_credentials, _ = _registry_env_vars()
    assert registry_credentials, "provider registry declared no credentials"

    for env_var in sorted(registry_credentials):
        owners = [
            source_id
            for source_id in EXTERNAL_SOURCE_INVENTORY
            if env_var_belongs_to_source(env_var, source_id)
        ]
        assert owners, f"{env_var} is attributed to no source in the inventory"
        assert classify_source_env_var(env_var) == "credential", (
            f"{env_var} is a registry credential but reads as an endpoint"
        )


def test_every_registry_endpoint_is_attributed_to_a_source_as_an_endpoint() -> None:
    registry_credentials, registry_endpoints = _registry_env_vars()
    assert registry_endpoints, "provider registry declared no endpoints"
    # A variable is one or the other. If that ever stops holding, the classifier
    # needs a rule for the overlap rather than a silent winner.
    assert not (registry_credentials & registry_endpoints)

    for env_var in sorted(registry_endpoints):
        owners = [
            source_id
            for source_id in EXTERNAL_SOURCE_INVENTORY
            if env_var_belongs_to_source(env_var, source_id)
        ]
        assert owners, f"{env_var} is attributed to no source in the inventory"
        assert classify_source_env_var(env_var) == "endpoint", (
            f"{env_var} is a registry endpoint but reads as a credential"
        )


def test_a_source_never_claims_another_source_s_variable() -> None:
    """字詞比對不能把某個 source 的變數算到另一個 source 頭上。

    ``competitor_store_snapshot`` 和 ``store_opening_authority_snapshot`` 共用
    ``store``；如果 ``store`` 沒有被當成通用字丟掉，前者會吃下後者的 attestation。
    """

    registry_credentials, registry_endpoints = _registry_env_vars()
    for env_var in sorted(registry_credentials | registry_endpoints):
        owners = [
            source_id
            for source_id in EXTERNAL_SOURCE_INVENTORY
            if env_var_belongs_to_source(env_var, source_id)
        ]
        assert len(owners) == 1, f"{env_var} is ambiguous across sources: {owners}"


def test_an_unrecognised_secret_shape_is_read_as_a_credential() -> None:
    """認不出來的字尾必須算成 credential，不能因為看不懂就放行。"""

    assert classify_source_env_var("ODP_POI_PROVIDER_MYSTERY_BLOB") == "credential"


def test_sources_off_release_is_admissible_without_a_masked_snapshot() -> None:
    manifest = sources_off_manifest()

    assert "data_snapshot" not in manifest
    assert validate_manifest(manifest) == []
    assert validate_release_admission(manifest) == []
    assert manifest["sources_off_attestation"]["egress_posture"] == SOURCES_OFF_EGRESS_POSTURE
    assert manifest["sources_off_attestation"]["total_sources_audited"] == 16


def test_sources_off_release_without_any_data_plane_evidence_fails_closed() -> None:
    manifest = sources_off_manifest()
    manifest.pop("sources_off_attestation")
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    assert any("data_snapshot" in err for err in validate_manifest(manifest))
    assert any(
        "manifest.data_snapshot" in err for err in validate_release_admission(manifest)
    )


def test_sources_off_attestation_requires_the_checked_in_egress_contract() -> None:
    manifest = sources_off_manifest()
    manifest["sources_off_attestation"]["egress_evidence"]["firewall_egress"] = "allow-all"
    manifest["sources_off_attestation"]["binding_digest"] = compute_sources_off_binding_digest(
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        source_policy_digest=manifest["source_policy_digest"],
        posture=sources_off_posture_payload(manifest["sources_off_attestation"]),
    )
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any("egress_evidence.firewall_egress" in err for err in errors)


def test_sources_off_attestation_binds_resolved_egress_and_probe_receipt_digest() -> None:
    manifest = sources_off_manifest()
    evidence = manifest["sources_off_attestation"]["egress_evidence"]
    evidence["resolved_cloud_run_egress"] = "PRIVATE_RANGES_ONLY"
    manifest["sources_off_attestation"]["binding_digest"] = compute_sources_off_binding_digest(
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        source_policy_digest=manifest["source_policy_digest"],
        posture=sources_off_posture_payload(manifest["sources_off_attestation"]),
    )
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any("egress_evidence.resolved_cloud_run_egress" in err for err in errors)

    manifest = sources_off_manifest()
    manifest["sources_off_attestation"]["egress_evidence"][
        "runtime_probe_receipt_content_digest"
    ] = "sha256:" + "0" * 64
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    errors = validate_manifest(manifest)
    assert any(
        "egress_evidence.runtime_probe_receipt_content_digest" in err
        for err in errors
    )


def _valid_sources_off_probe_receipt() -> dict:
    receipt = {
        "schema_version": 1,
        "receipt_kind": "public_egress_probe",
        "secret_values_redacted": True,
        "candidate_sha": "1" * 40,
        "manifest_digest": "sha256:" + "2" * 64,
        "job": "oday-worker-r-111111111111",
        "probe_url": "https://example.com/",
        "expected": "denied",
        "vpc_egress": SOURCES_OFF_CLOUD_RUN_EGRESS,
        "result": SOURCES_OFF_RUNTIME_PROBE_RESULT,
        "reason": SOURCES_OFF_RUNTIME_PROBE_REASON,
        "execution": "succeeded",
        "recorded_at": "2026-09-02T00:00:00Z",
    }
    receipt["receipt_content_digest"] = compute_sources_off_probe_receipt_content_digest()
    return receipt


def test_sources_off_probe_receipt_binds_actual_content_and_release_identity() -> None:
    receipt = _valid_sources_off_probe_receipt()
    assert validate_sources_off_probe_receipt(
        receipt,
        expected_candidate_sha=receipt["candidate_sha"],
        expected_manifest_digest=receipt["manifest_digest"],
        expected_egress=SOURCES_OFF_CLOUD_RUN_EGRESS,
    ) == []

    tampered = copy.deepcopy(receipt)
    tampered["vpc_egress"] = "PRIVATE_RANGES_ONLY"
    assert validate_sources_off_probe_receipt(
        tampered,
        expected_candidate_sha=receipt["candidate_sha"],
        expected_manifest_digest=receipt["manifest_digest"],
    )

    tampered = copy.deepcopy(receipt)
    tampered["receipt_content_digest"] = "sha256:" + "0" * 64
    assert any(
        "receipt_content_digest" in error
        for error in validate_sources_off_probe_receipt(tampered)
    )


def test_sources_off_attestation_with_an_enabled_source_fails_closed() -> None:
    inventory = clean_sources_inventory()
    inventory[8]["status"] = "enabled"
    manifest = sources_off_manifest(sources_inventory=inventory)

    errors = validate_manifest(manifest)
    assert any("status must be 'disabled'" in err for err in errors)


def test_sources_off_attestation_with_a_provider_credential_fails_closed() -> None:
    inventory = clean_sources_inventory()
    inventory[11]["credentials_present"] = True
    manifest = sources_off_manifest(sources_inventory=inventory)

    errors = validate_manifest(manifest)
    assert any("credentials_present must be False" in err for err in errors)


def test_sources_off_attestation_with_open_public_egress_fails_closed() -> None:
    inventory = clean_sources_inventory()
    inventory[9]["public_egress"] = "allowed"
    manifest = sources_off_manifest(sources_inventory=inventory)

    errors = validate_manifest(manifest)
    assert any("public_egress must be 'denied'" in err for err in errors)
    assert any("egress_posture" in err for err in errors)


def test_sources_off_attestation_that_skips_a_source_fails_closed() -> None:
    inventory = [
        entry for entry in clean_sources_inventory()
        if entry["source_id"] != "listing_raw_snapshot"
    ]
    manifest = sources_off_manifest(sources_inventory=inventory)

    errors = validate_manifest(manifest)
    assert any("missing: listing_raw_snapshot" in err for err in errors)
    assert any("total_sources_audited must be 16" in err for err in errors)


def test_sources_off_attestation_claiming_clean_over_a_dirty_inventory_fails_closed() -> None:
    """Re-sealing the digest cannot make the verdict fields outrank the audit."""

    inventory = clean_sources_inventory()
    inventory[3]["credentials_present"] = True
    manifest = sources_off_manifest(sources_inventory=inventory)
    attestation = manifest["sources_off_attestation"]
    attestation["zero_credentials_present"] = True
    attestation["binding_digest"] = compute_sources_off_binding_digest(
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        source_policy_digest=manifest["source_policy_digest"],
        posture=sources_off_posture_payload(attestation),
    )
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any(
        "zero_credentials_present does not match its own sources_inventory" in err
        for err in errors
    )


def test_sources_off_attestation_borrowed_from_another_candidate_fails_closed() -> None:
    manifest = sources_off_manifest(binding_candidate_sha="7" * 40)

    errors = validate_manifest(manifest)
    assert any("binding_digest is not bound to this release" in err for err in errors)


def test_sources_off_attestation_bound_to_other_images_fails_closed() -> None:
    other_images = {
        "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "4" * 64},
        "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "5" * 64},
    }
    manifest = sources_off_manifest(binding_components=other_images)

    errors = validate_manifest(manifest)
    assert any("binding_digest is not bound to this release" in err for err in errors)


def test_sources_off_attestation_bound_to_another_source_policy_fails_closed() -> None:
    manifest = sources_off_manifest(binding_source_policy_digest="sha256:" + "6" * 64)

    errors = validate_manifest(manifest)
    assert any("binding_digest is not bound to this release" in err for err in errors)


def test_enabled_sources_still_require_the_approved_masked_snapshot() -> None:
    manifest = built_manifest(schema_version=2)
    manifest.pop("data_snapshot")
    manifest["external_sources_expected_enabled"] = ["listing_raw_snapshot"]
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any("missing required field: data_snapshot" in err for err in errors)
    assert any(
        "release admission with enabled external sources" in err
        for err in validate_release_admission(manifest)
    )


def test_sources_off_attestation_on_a_release_with_enabled_sources_fails_closed() -> None:
    manifest = sources_off_manifest()
    manifest["external_sources_expected_enabled"] = ["poi_snapshot"]
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    errors = validate_manifest(manifest)
    assert any(
        "sources_off_attestation must not appear on a manifest" in err for err in errors
    )


def test_sources_off_attestation_may_not_sit_beside_a_data_snapshot() -> None:
    manifest = sources_off_manifest(data_snapshot=valid_data_snapshot())

    errors = validate_manifest(manifest)
    assert any(
        "may not override an existing snapshot binding" in err for err in errors
    )


def test_sources_off_attestation_may_not_replace_a_previous_snapshot_binding() -> None:
    """Anti-downgrade: a snapshot-bound predecessor keeps the next release strict."""

    manifest = sources_off_manifest(rollback_release=valid_rollback_release("1" * 40))

    errors = validate_release_admission(manifest)
    assert any(
        "rollback_release still binds a data_snapshot" in err for err in errors
    )


def test_rollback_release_without_snapshot_or_attestation_fails_closed() -> None:
    rollback = valid_rollback_release("1" * 40)
    rollback.pop("data_snapshot")
    manifest = sources_off_manifest(rollback_release=rollback)

    errors = validate_manifest(manifest)
    assert any(
        "missing required data_snapshot pointer or sources_off_attestation" in err
        for err in errors
    )


def test_rollback_release_sources_off_binding_must_be_a_digest() -> None:
    rollback = sources_off_rollback_release("1" * 40)
    rollback["sources_off_attestation"] = {"binding_digest": "not-a-digest"}
    manifest = sources_off_manifest(rollback_release=rollback)

    errors = validate_manifest(manifest)
    assert any(
        "sources_off_attestation.binding_digest must be a sha256" in err for err in errors
    )


def test_extract_rollback_binding_from_a_sources_off_release_carries_the_digest() -> None:
    previous = sources_off_manifest(candidate_sha="2" * 40, release_id="odp-test-prev-off")
    assert validate_release_admission(previous) == []

    binding = extract_rollback_release_binding(previous)
    assert "data_snapshot" not in binding
    assert binding["sources_off_attestation"] == {
        "binding_digest": previous["sources_off_attestation"]["binding_digest"]
    }


def test_a_sources_off_release_is_valid_rollback_evidence() -> None:
    previous = sources_off_manifest(candidate_sha="2" * 40, release_id="odp-test-prev-off")
    assert validate_rollback_manifest(previous, current_candidate_sha="1" * 40) == []
