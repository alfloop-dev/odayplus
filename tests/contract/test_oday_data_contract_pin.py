"""Contract tests for the pinned ODay data-platform foundation client.

ODP-XR-CLIENT-001 / contract ``odayplus.data-platform-foundation-client.v1``.

These tests are the CI gate the task's acceptance criteria ask for:

``consume the release, not producer tables``
    The client resolves contracts from the vendored release bundle published by
    ``alfloop-dev/oday-data-platform``, and the producer's storage DDL and
    relation-ownership catalog are provably absent from this repository.

``fail CI on incompatible kernel/internal schemas``
    Every drift a producer can introduce — a removed contract, a bumped
    contract version, edited schema content, a declared breaking change, a new
    unpinned kernel contract — is asserted to raise, not to warn.

``expose the exact foundation version at runtime``
    ``foundation_version()`` is asserted to report the exact pinned release,
    producer commit and content digest, and to verify before it answers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from packages.oday_data_contracts_client import (
    ArtifactDigestError,
    IncompatibleContractError,
    canonical_digest,
    check_release,
    diagnostics,
    foundation_contracts,
    foundation_version,
    load_pin,
    load_release,
    reset_cache,
    verify_release,
)
from packages.oday_data_contracts_client.codegen import check_generated, render_all
from packages.oday_data_contracts_client.pin import REPO_ROOT
from packages.oday_data_contracts_client.release import FoundationRelease

ENFORCED_SCHEMA_CATEGORIES = {"platform_foundation", "manifests", "kernel", "internal_analytical"}
EXPECTED_RELEASE_ID = "oday-data-foundation-contracts.v0.4.1"


@pytest.fixture(scope="module")
def pin():
    return load_pin()


@pytest.fixture(scope="module")
def release(pin):
    return load_release(pin)


def _mutated(release: FoundationRelease, **overrides: Any) -> FoundationRelease:
    """A deep copy of the release with selected parts replaced."""
    base = {
        "manifest": copy.deepcopy(dict(release.manifest)),
        "compatibility": copy.deepcopy(dict(release.compatibility)),
        "schemas": copy.deepcopy(dict(release.schemas)),
    }
    base.update(overrides)
    return replace(release, **base)


def _catalog_entry(manifest: dict[str, Any], contract_id: str) -> dict[str, Any]:
    for entry in manifest["contract_catalog"]:
        if entry["contract_id"] == contract_id:
            return entry
    raise AssertionError(f"{contract_id} is not in the released catalog")


# ---------------------------------------------------------------------------
# The pin itself
# ---------------------------------------------------------------------------


def test_pin_names_the_released_foundation_package(pin):
    assert pin.client_contract == "odayplus.data-platform-foundation-client.v1"
    assert pin.release.id == EXPECTED_RELEASE_ID
    assert pin.release.status == "PUBLISHED"
    assert pin.release.owner_task_id == "XR-CONTRACTS-001"
    assert pin.source.repository == "alfloop-dev/oday-data-platform"
    assert re.fullmatch(r"[0-9a-f]{40}", pin.source.commit_sha), (
        "the pin must name an exact producer commit, not a moving ref"
    )
    assert pin.source.release_path == "contracts/releases/emgi/foundation"


def test_pin_covers_every_enforced_contract_category(pin):
    assert set(pin.compatibility.enforced_categories) == ENFORCED_SCHEMA_CATEGORIES
    pinned_categories = {contract.category for contract in pin.contracts}
    assert pinned_categories == ENFORCED_SCHEMA_CATEGORIES
    assert len({contract.contract_id for contract in pin.contracts}) == len(pin.contracts)
    assert len({contract.module for contract in pin.contracts}) == len(pin.contracts)


def test_pin_records_sha256_digests(pin):
    for contract in pin.contracts:
        assert re.fullmatch(r"[0-9a-f]{64}", contract.sha256), contract.contract_id
    for name, digest in pin.vendor.artifacts.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), name


def test_pin_does_not_accept_breaking_changes(pin):
    assert pin.compatibility.allow_breaking_change is False
    assert pin.compatibility.required_compatibility_mode == "backward-compatible"
    assert pin.release.semantic_version in pin.compatibility.supported_release_versions


# ---------------------------------------------------------------------------
# Consuming the release, not the producer's tables
# ---------------------------------------------------------------------------


def test_vendored_artifacts_match_their_pinned_checksums(pin):
    for name, expected in pin.vendor.artifacts.items():
        raw = (pin.vendor.release_root / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected, name


def test_edited_release_artifact_is_rejected(pin, tmp_path):
    staged = tmp_path / "_release"
    shutil.copytree(pin.vendor.release_root, staged)
    bundle = staged / "schemas.json"
    bundle.write_bytes(bundle.read_bytes() + b"\n")

    tampered = replace(pin, vendor=replace(pin.vendor, release_root=staged))
    with pytest.raises(ArtifactDigestError, match="does not match the pinned"):
        load_release(tampered)


def test_producer_implementation_tables_are_not_vendored(pin):
    assert set(pin.vendor.excluded) == {"storage-schema.sql", "relation-ownership.yaml"}
    for excluded in pin.vendor.excluded:
        assert not (pin.vendor.release_root / excluded).exists()

    package_root = REPO_ROOT / "packages" / "oday_data_contracts_client"
    assert not list(package_root.rglob("*.sql"))
    ddl = re.compile(r"CREATE\s+(TABLE|MATERIALIZED\s+VIEW)", re.IGNORECASE)
    offenders = [
        path.relative_to(REPO_ROOT)
        for path in package_root.rglob("*")
        if path.is_file() and ddl.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, f"producer DDL leaked into the consumer client: {offenders}"


def test_smuggling_producer_ddl_into_the_bundle_is_rejected(pin, tmp_path):
    staged = tmp_path / "_release"
    shutil.copytree(pin.vendor.release_root, staged)
    (staged / "storage-schema.sql").write_text("CREATE TABLE emgi.store();\n", encoding="utf-8")

    tampered = replace(pin, vendor=replace(pin.vendor, release_root=staged))
    with pytest.raises(ArtifactDigestError, match="must not be vendored"):
        load_release(tampered)


def test_released_catalog_is_the_only_schema_source(release):
    for contract in release.pin.contracts:
        schema = release.schema_for(contract)
        assert canonical_digest(schema) == contract.sha256, contract.contract_id
        assert _catalog_entry(dict(release.manifest), contract.contract_id)["sha256"] == (
            contract.sha256
        )


# ---------------------------------------------------------------------------
# The CI gate: incompatible kernel / internal schemas must fail
# ---------------------------------------------------------------------------


def test_pinned_release_is_currently_compatible(release):
    report = verify_release(release)
    assert report.compatible
    assert report.release_id == EXPECTED_RELEASE_ID
    assert len(report.checked_contracts) == len(release.pin.enforced_contracts)


@pytest.mark.parametrize(
    "contract_id",
    ["emgi.measurement-envelope.v4.1", "emgi.time-contract.v4.1", "emgi.coverage.v4.1"],
)
def test_edited_kernel_schema_fails(release, contract_id):
    pinned = release.pin.contract(contract_id)
    schemas = copy.deepcopy(dict(release.schemas))
    schemas[pinned.schema_file] = {
        **schemas[pinned.schema_file],
        "x-unreviewed-consumer-change": True,
    }
    drifted = _mutated(release, schemas=schemas)

    report = check_release(drifted)
    assert not report.compatible
    assert any(drift.reason == "schema content changed under the pin" for drift in report.drifts)
    with pytest.raises(IncompatibleContractError, match="schema content changed"):
        verify_release(drifted)


@pytest.mark.parametrize(
    "contract_id",
    ["oday.store-reference.v1", "oday.store-daily-performance.v1", "oday.machine-capacity.v1"],
)
def test_removed_internal_contract_fails(release, contract_id):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["contract_catalog"] = [
        entry for entry in manifest["contract_catalog"] if entry["contract_id"] != contract_id
    ]
    drifted = _mutated(release, manifest=manifest)

    with pytest.raises(IncompatibleContractError, match="no longer published"):
        verify_release(drifted)


def test_bumped_contract_version_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    _catalog_entry(manifest, "emgi.source-registry.v4.1")["contract_version"] = "5.0.0"
    with pytest.raises(IncompatibleContractError, match="contract version changed"):
        verify_release(_mutated(release, manifest=manifest))


def test_moved_schema_file_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    _catalog_entry(manifest, "emgi.scope-principal.v4.1")["schema_file"] = "schemas/moved.json"
    with pytest.raises(IncompatibleContractError, match="schema file moved"):
        verify_release(_mutated(release, manifest=manifest))


def test_declared_breaking_change_fails(release):
    compatibility = copy.deepcopy(dict(release.compatibility))
    compatibility["breaking_change"] = True
    with pytest.raises(IncompatibleContractError, match="breaking change"):
        verify_release(_mutated(release, compatibility=compatibility))


def test_release_identity_change_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["release_id"] = "oday-data-foundation-contracts.v0.5.0"
    manifest["semantic_version"] = "0.5.0"
    with pytest.raises(IncompatibleContractError, match="release identity changed"):
        verify_release(_mutated(release, manifest=manifest))


def test_new_unpinned_kernel_contract_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["contract_catalog"].append(
        {
            "category": "kernel",
            "contract_id": "emgi.lineage-envelope.v4.2",
            "contract_version": "4.2.0",
            "description": "A kernel contract the consumer has never reviewed.",
            "schema_file": "schemas/lineage-envelope.schema.json",
            "sha256": "0" * 64,
        }
    )
    with pytest.raises(IncompatibleContractError, match="not pinned by the consumer"):
        verify_release(_mutated(release, manifest=manifest))


def test_unenforced_producer_categories_are_ignored(release):
    """Storage DDL and relation ownership are producer-internal, so they do not gate."""
    catalog = release.catalog
    unenforced = {
        entry["category"]
        for entry in catalog.values()
        if entry["category"] not in ENFORCED_SCHEMA_CATEGORIES
    }
    assert unenforced == {"storage_schema", "relation_ownership"}
    assert verify_release(release).compatible


# ---------------------------------------------------------------------------
# Generated client
# ---------------------------------------------------------------------------


def test_generated_client_is_current(release):
    stale = check_generated(release)
    assert not stale, (
        "regenerate with: uv run python -m packages.oday_data_contracts_client.codegen --write"
    )


def test_generation_is_deterministic(release):
    assert render_all(release) == render_all(release)


def test_every_pinned_contract_has_a_generated_module(release):
    from packages.oday_data_contracts_client import models

    assert set(models.CONTRACT_MODELS) == {c.contract_id for c in release.pin.enforced_contracts}
    for contract in release.pin.enforced_contracts:
        module = __import__(
            f"packages.oday_data_contracts_client.models.{contract.module}",
            fromlist=["CONTRACT_ID"],
        )
        assert module.CONTRACT_ID == contract.contract_id
        assert module.CONTRACT_VERSION == contract.contract_version
        assert module.SCHEMA_SHA256 == contract.sha256
        assert module.SCHEMA_FILE == contract.schema_file
        root = models.CONTRACT_MODELS[contract.contract_id]
        assert root.__name__ == module.ROOT_MODEL


def test_generated_modules_are_marked_generated(release):
    for name in render_all(release):
        text = (release.pin.vendor.generated_root / name).read_text(encoding="utf-8")
        assert text.startswith("# Generated by packages/oday_data_contracts_client/codegen.py")


def test_generated_model_round_trips_against_the_released_schema(release):
    from packages.oday_data_contracts_client.models.store_reference import StoreReference

    pinned = release.pin.contract("oday.store-reference.v1")
    schema = release.schema_for(pinned)
    payload = {
        "store_id": "store-001",
        "store_name": "Taipei Main",
        "effective_from": "2026-01-01T00:00:00+08:00",
        "registered_at": "2025-12-01T00:00:00+08:00",
        "source_row_digest": "a" * 64,
        "time_contract": {"knowledge_as_of": "2026-01-02T00:00:00+08:00"},
        "geolocation": {"latitude": 25.05, "longitude": 121.53},
    }

    model = StoreReference.from_dict(payload)
    assert model.store_id == "store-001"
    assert model.geolocation is not None and model.geolocation.srid == 4326
    assert model.time_contract.knowledge_as_of == "2026-01-02T00:00:00+08:00"

    wire = model.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert StoreReference.from_dict(wire).to_dict() == wire


def test_generated_enums_and_nested_models_are_typed(release):
    from packages.oday_data_contracts_client.models.source_registry import (
        PublicationState,
        SourceRegistryDocument,
    )

    pinned = release.pin.contract("emgi.source-registry.v4.1")
    document = SourceRegistryDocument.from_dict(
        {
            "providers": [
                {"provider_id": "internal", "display_name": "Internal", "legal_name": "ODay"}
            ],
            "dataset_versions": [
                {
                    "dataset_version_id": "dv-1",
                    "dataset_id": "ds-1",
                    "contract_version_id": "cv-1",
                    "release_key": "2026-01",
                    "release_kind": "SNAPSHOT",
                    "source_uri": "gs://oday-internal/ds-1/2026-01",
                    "technical_readiness": "DECLARED",
                    "policy_state": "APPROVED",
                    "publication_state": "PUBLISHED",
                    "content_sha256": "b" * 64,
                }
            ],
        }
    )
    version = document.dataset_versions[0]
    assert version.publication_state is PublicationState.PUBLISHED
    assert document.providers[0].provider_id == "internal"

    wire = document.to_dict()
    assert wire["dataset_versions"][0]["publication_state"] == "PUBLISHED"
    jsonschema.validate(wire, dict(release.schema_for(pinned)))


# ---------------------------------------------------------------------------
# Runtime version exposure
# ---------------------------------------------------------------------------


def test_runtime_reports_the_exact_foundation_version(pin):
    reset_cache()
    version = foundation_version()
    assert version.release_id == pin.release.id
    assert version.semantic_version == pin.release.semantic_version
    assert version.content_digest == pin.release.content_digest
    assert version.producer_commit_sha == pin.source.commit_sha
    assert version.client_contract == pin.client_contract
    assert version.contract_count == len(pin.enforced_contracts)
    assert pin.release.id in str(version)
    assert pin.source.commit_sha[:12] in str(version)


def test_runtime_diagnostics_are_json_serialisable(pin):
    reset_cache()
    block = diagnostics()
    assert json.loads(json.dumps(block)) == block
    assert block["foundation"]["release_id"] == pin.release.id
    reported = {entry["contract_id"] for entry in block["contracts"]}
    assert reported == {contract.contract_id for contract in pin.enforced_contracts}


def test_runtime_contract_inventory_matches_the_pin(pin):
    reset_cache()
    inventory = {contract.contract_id: contract for contract in foundation_contracts()}
    for pinned in pin.enforced_contracts:
        reported = inventory[pinned.contract_id]
        assert reported.contract_version == pinned.contract_version
        assert reported.sha256 == pinned.sha256
        assert reported.category == pinned.category
        assert reported.module == pinned.module


def test_pin_path_is_the_documented_config_file(pin):
    assert pin.path == REPO_ROOT / "config" / "oday_data_contracts.toml"
    assert Path(pin.path).exists()
