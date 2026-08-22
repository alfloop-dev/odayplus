"""Contract tests for the pinned ODay data-platform product client.

ODP-XR-PRODUCT-CLIENT-001 / contract ``odayplus.data-platform-product-client.v1``.

These tests are the CI gate the task's acceptance criteria ask for:

``generate consumer models only from the released product package``
    The client resolves contracts from the vendored release bundle published by
    ``alfloop-dev/oday-data-platform``, and the producer's storage DDL and
    relation-ownership catalog are provably absent from this repository.

``pin exact product release version/checksum and expose it through runtime diagnostics``
    ``product_version()`` and ``diagnostics()`` report the exact pinned release,
    producer commit and content digest, and verify before answering.

``fail CI on incompatible product schemas without copying producer implementation tables``
    Every drift a producer can introduce — a removed contract, a bumped
    contract version, edited schema content, a declared breaking change, a new
    unpinned product contract — is asserted to raise, not to warn.
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

from packages.oday_data_product_contracts_client import (
    ArtifactDigestError,
    IncompatibleContractError,
    canonical_digest,
    check_release,
    diagnostics,
    load_pin,
    load_release,
    product_contracts,
    product_version,
    reset_cache,
    verify_release,
)
from packages.oday_data_product_contracts_client.codegen import check_generated, render_all
from packages.oday_data_product_contracts_client.pin import REPO_ROOT
from packages.oday_data_product_contracts_client.release import ProductRelease

ENFORCED_SCHEMA_CATEGORIES = {"source_evidence", "domain_observation", "decision_product"}
EXPECTED_RELEASE_ID = "oday-data-product-contracts.v0.4.1"


@pytest.fixture(scope="module")
def pin():
    return load_pin()


@pytest.fixture(scope="module")
def release(pin):
    return load_release(pin)


def _mutated(release: ProductRelease, **overrides: Any) -> ProductRelease:
    """A deep copy of the release with selected parts replaced."""
    base = {
        "manifest": copy.deepcopy(dict(release.manifest)),
        "compatibility": copy.deepcopy(dict(release.compatibility)),
        "schemas": copy.deepcopy(dict(release.schemas)),
        "dependency_closure": copy.deepcopy(dict(release.dependency_closure)),
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


def test_pin_names_the_released_product_package(pin):
    assert pin.client_contract == "odayplus.data-platform-product-client.v1"
    assert pin.release.id == EXPECTED_RELEASE_ID
    assert pin.release.status == "PUBLISHED"
    assert pin.release.owner_task_id == "XR-CONTRACTS-PRODUCT-001"
    assert pin.source.repository == "alfloop-dev/oday-data-platform"
    assert re.fullmatch(r"[0-9a-f]{40}", pin.source.commit_sha), (
        "the pin must name an exact producer commit, not a moving ref"
    )
    assert pin.source.release_path == "contracts/releases/emgi/product"


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

    package_root = REPO_ROOT / "packages" / "oday_data_product_contracts_client"
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
# The CI gate: incompatible product schemas must fail
# ---------------------------------------------------------------------------


def test_pinned_release_is_currently_compatible(release):
    report = verify_release(release)
    assert report.compatible
    assert report.release_id == EXPECTED_RELEASE_ID
    assert len(report.checked_contracts) == len(release.pin.enforced_contracts)


@pytest.mark.parametrize(
    "contract_id",
    [
        "emgi.field-survey.v1",
        "emgi.property-observation.v1",
        "emgi.coverage-surface.v1",
        "emgi.market-cell-profile.v1",
        "emgi.catchment-profile.v1",
        "emgi.site-market-context.v1",
        "emgi.data-acquisition-plan.v1",
    ],
)
def test_edited_product_schema_fails(release, contract_id):
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
    ["emgi.field-survey.v1", "emgi.property-observation.v1", "emgi.catchment-profile.v1"],
)
def test_removed_product_contract_fails(release, contract_id):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["contract_catalog"] = [
        entry for entry in manifest["contract_catalog"] if entry["contract_id"] != contract_id
    ]
    drifted = _mutated(release, manifest=manifest)

    with pytest.raises(IncompatibleContractError, match="no longer published"):
        verify_release(drifted)


def test_bumped_contract_version_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    _catalog_entry(manifest, "emgi.site-market-context.v1")["contract_version"] = "2.0.0"
    with pytest.raises(IncompatibleContractError, match="contract version changed"):
        verify_release(_mutated(release, manifest=manifest))


def test_moved_schema_file_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    _catalog_entry(manifest, "emgi.data-acquisition-plan.v1")["schema_file"] = (
        "schemas/moved.json"
    )
    with pytest.raises(IncompatibleContractError, match="schema file moved"):
        verify_release(_mutated(release, manifest=manifest))


def test_declared_breaking_change_fails(release):
    compatibility = copy.deepcopy(dict(release.compatibility))
    compatibility["breaking_change"] = True
    with pytest.raises(IncompatibleContractError, match="breaking change"):
        verify_release(_mutated(release, compatibility=compatibility))


def test_release_identity_change_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["release_id"] = "oday-data-product-contracts.v0.5.0"
    manifest["semantic_version"] = "0.5.0"
    with pytest.raises(IncompatibleContractError, match="release identity changed"):
        verify_release(_mutated(release, manifest=manifest))


def test_new_unpinned_product_contract_fails(release):
    manifest = copy.deepcopy(dict(release.manifest))
    manifest["contract_catalog"].append(
        {
            "category": "decision_product",
            "contract_id": "emgi.custom-simulation.v1",
            "contract_version": "1.0.0",
            "description": "A product contract the consumer has not pinned.",
            "document_model": "oday_data_platform.products.simulation.models.CustomSimulation",
            "schema_file": "schemas/custom-simulation.schema.json",
            "sha256": "0" * 64,
        }
    )
    with pytest.raises(IncompatibleContractError, match="not pinned by the consumer"):
        verify_release(_mutated(release, manifest=manifest))


# ---------------------------------------------------------------------------
# Generated client
# ---------------------------------------------------------------------------


def test_generated_client_is_current(release):
    stale = check_generated(release)
    assert not stale, (
        "regenerate with: uv run python -m packages.oday_data_product_contracts_client.codegen --write"
    )


def test_generation_is_deterministic(release):
    assert render_all(release) == render_all(release)


def test_every_pinned_contract_has_a_generated_module(release):
    from packages.oday_data_product_contracts_client import models

    assert set(models.CONTRACT_MODELS) == {c.contract_id for c in release.pin.enforced_contracts}
    for contract in release.pin.enforced_contracts:
        module = __import__(
            f"packages.oday_data_product_contracts_client.models.{contract.module}",
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
        assert text.startswith(
            "# Generated by packages/oday_data_product_contracts_client/codegen.py"
        )


def test_generated_field_survey_round_trips(release):
    from packages.oday_data_product_contracts_client.models.field_survey import (
        FieldSurveyDocument,
        MediaKind,
        ReviewStatus,
        SurveyLifecycleKind,
        SurveyType,
        TargetEntityKind,
    )

    pinned = release.pin.contract("emgi.field-survey.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_id": "emgi.field-survey.v1",
        "field_surveys": [
            {
                "survey_id": "srv-1",
                "submission_id": "sub-1",
                "observation_id": "obs-1",
                "blob_id": "blob-1",
                "scope_principal_id": "sp-1",
                "campaign_id": "camp-1",
                "survey_type": "CANDIDATE_SITE",
                "target_entity_kind": "CANDIDATE_SITE",
                "target_entity_id": "target-1",
                "submitter_id": "user-1",
                "review": {
                    "reviewer_id": "rev-1",
                    "review_status": "APPROVED",
                    "reviewed_at": "2026-08-14T10:00:00Z",
                },
                "surveyed_at": "2026-08-14T09:00:00Z",
                "submitted_at": "2026-08-14T09:30:00Z",
                "effective_from": "2026-08-14T10:00:00Z",
                "location": {
                    "latitude": 25.04,
                    "longitude": 121.56,
                    "address": "Taipei",
                    "srid": 4326,
                },
                "lifecycle_kind": "INITIAL",
                "media_attachments": [
                    {
                        "media_id": "med-1",
                        "media_kind": "PHOTO",
                        "blob_id": "b-1",
                        "storage_uri": "gs://b/1",
                        "sha256": "a" * 64,
                        "captured_at": "2026-08-14T09:10:00Z",
                    }
                ],
                "attributes": {"frontage": 12.5},
                "confidence": 1.0,
                "metadata": {},
            }
        ],
        "retractions": [],
    }

    model = FieldSurveyDocument.from_dict(payload)
    assert model.contract_id == "emgi.field-survey.v1"
    obs = model.field_surveys[0]
    assert obs.survey_type is SurveyType.CANDIDATE_SITE
    assert obs.target_entity_kind is TargetEntityKind.CANDIDATE_SITE
    assert obs.lifecycle_kind is SurveyLifecycleKind.INITIAL
    assert obs.location.latitude == 25.04
    assert obs.review.review_status is ReviewStatus.APPROVED
    assert obs.media_attachments[0].media_kind is MediaKind.PHOTO

    wire = model.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert FieldSurveyDocument.from_dict(wire).to_dict() == wire


def test_generated_property_observation_round_trips(release):
    from packages.oday_data_product_contracts_client.models.property_observation import (
        ListingStatus,
        PropertyObservationDocument,
    )

    pinned = release.pin.contract("emgi.property-observation.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_id": "emgi.property-observation.v1",
        "created_at": "2026-08-14T14:40:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "metadata": {},
        "properties": [
            {
                "property_id": "prop-1",
                "normalized_address": "Xinyi Rd Sec 5",
                "county": "Taipei",
                "district": "Xinyi",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
        "listing_observations": [
            {
                "listing_obs_id": "list-1",
                "property_id": "prop-1",
                "channel": "591",
                "observed_at": "2026-08-14T10:00:00Z",
                "first_seen_at": "2026-08-01T00:00:00Z",
                "last_seen_at": "2026-08-14T10:00:00Z",
                "listing_status": "ACTIVE",
                "listing_kind": "RENTAL",
            }
        ],
        "status_histories": [],
    }

    model = PropertyObservationDocument.from_dict(payload)
    assert model.contract_id == "emgi.property-observation.v1"
    prop = model.properties[0]
    assert prop.county == "Taipei"
    listing = model.listing_observations[0]
    assert listing.listing_status is ListingStatus.ACTIVE

    wire = model.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert PropertyObservationDocument.from_dict(wire).to_dict() == wire


def test_generated_site_market_context_round_trips(release):
    from packages.oday_data_product_contracts_client.models.site_market_context import (
        CoverageState,
        DomainStatus,
        PeriodGrain,
        ReadinessLevel,
        SiteMarketContextDocument,
    )

    pinned = release.pin.contract("emgi.site-market-context.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_version": "emgi.site-market-context.v1",
        "document_id": "smc-doc-001",
        "product_version": "0.4.1",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "generated_at": "2026-08-14T14:40:00Z",
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "component_manifest_refs": [],
        "contexts": [
            {
                "context_id": "ctx-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "identity": {
                    "site_id": "site-101",
                    "site_name": "Taipei Store",
                    "county": "Taipei",
                    "district": "Xinyi",
                    "address": "Xinyi Rd",
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "primary_h3_index": "884a1072b7fffff",
                    "h3_resolution": 8,
                    "admin_code": "63000",
                },
                "catchment": {
                    "status": "available",
                    "catchment_id": "cat-1",
                    "travel_mode": "motorcycle",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "area_sq_meters": 50000.0,
                    "estimation_status": "exact",
                    "h3_cells": ["884a1072b7fffff"],
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demand": {
                    "status": "available",
                    "total_population": 50000,
                    "male_population": 24000,
                    "female_population": 26000,
                    "household_count": 18000,
                    "density_per_sq_km": 1000.0,
                    "daytime_population_ratio": 1.2,
                    "age_distribution": {"20-29": 10000},
                },
                "poi": {
                    "status": "available",
                    "total_poi_count": 500,
                    "convenience_stores_count": 20,
                    "commercial_centers_count": 5,
                    "transit_stations_count": 4,
                    "schools_count": 6,
                    "hospitals_count": 2,
                    "density_per_sq_km": 50.0,
                    "poi_by_category": {"convenience_store": 20},
                },
                "competitor": {
                    "status": "available",
                    "total_competitors": 10,
                    "active_competitors": 8,
                    "competitor_density_per_sq_km": 2.0,
                    "brands_present": ["BrandA"],
                    "price_tier_distribution": {"MID": 8},
                    "average_capacity": 50.0,
                    "capacity_sample_count": 5,
                },
                "rent": {
                    "status": "available",
                    "mean_rent_per_ping": 2500.0,
                    "median_rent_per_ping": 2400.0,
                    "p25_rent_per_ping": 2000.0,
                    "p75_rent_per_ping": 3000.0,
                    "sample_count": 30,
                    "confidence_pct": 95.0,
                    "tier": "STANDARD",
                    "fallback_level": "CELL",
                },
                "listing": {
                    "status": "available",
                    "active_listings_count": 15,
                    "average_area_ping": 30.0,
                    "mean_asking_rent_per_ping": 2600.0,
                    "median_asking_rent_per_ping": 2500.0,
                    "listings_by_property_type": {"APARTMENT": 15},
                },
                "mobility": {
                    "status": "available",
                    "activity_population": 15000,
                    "resident_population": 10000,
                    "work_population": 8000,
                    "hourly_distribution": {"0": 500},
                    "dwell_time_minutes": 45.0,
                    "dominant_archetype": "OFFICE_WORKER",
                    "is_calibrated": True,
                },
                "traffic": {
                    "status": "available",
                    "mean_speed_kph": 35.0,
                    "hourly_volume_vph": 1200,
                    "motorcycle_hourly_volume": 600,
                    "motorcycle_ratio": 0.5,
                    "level_of_service": "C",
                    "is_congestion_hotspot": False,
                },
                "event": {
                    "status": "available",
                    "active_events_count": 2,
                    "events": [],
                    "events_by_type": {},
                    "source_support": {
                        "source_dataset_ids": ["ds-1"],
                        "observation_count": 10,
                        "sample_count": 10,
                        "first_observed_at": "2026-01-01T00:00:00Z",
                        "last_observed_at": "2026-08-14T00:00:00Z",
                    },
                },
                "coverage": {
                    "status": "available",
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "domain_freshness": {"GEOGRAPHY": "fresh"},
                    "domain_status": {"GEOGRAPHY": "available"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                    "blocked_domains": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 100,
                    "sample_count": 100,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
        "metadata": {},
    }

    doc = SiteMarketContextDocument.from_dict(payload)
    assert doc.document_id == "smc-doc-001"
    assert doc.period_grain is PeriodGrain.MONTHLY
    ctx = doc.contexts[0]
    assert ctx.identity.latitude == 25.033
    assert ctx.catchment.status is DomainStatus.available
    assert ctx.coverage.overall_readiness is ReadinessLevel.ready
    assert ctx.coverage.domain_coverage["GEOGRAPHY"] == CoverageState.complete.value

    wire = doc.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert SiteMarketContextDocument.from_dict(wire).to_dict() == wire


def test_generated_catchment_profile_round_trips(release):
    from packages.oday_data_product_contracts_client.models.catchment_profile import (
        CatchmentProfileDocument,
        PeriodGrain,
        ReadinessLevel,
        TravelMode,
    )

    pinned = release.pin.contract("emgi.catchment-profile.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_version": "emgi.catchment-profile.v1",
        "document_id": "cat-doc-001",
        "product_version": "0.4.1",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "generated_at": "2026-08-14T14:40:00Z",
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "component_manifest_refs": [],
        "profiles": [
            {
                "profile_id": "prof-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "origin": {
                    "origin_id": "orig-1",
                    "latitude": 25.04,
                    "longitude": 121.56,
                    "origin_h3": "884a1072b7fffff",
                    "origin_geom": {"type": "Point", "coordinates": [121.56, 25.04]},
                },
                "boundary": {
                    "catchment_id": "cat-1",
                    "travel_mode": "motorcycle",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "area_sq_meters": 50000.0,
                    "estimation_status": "exact",
                    "h3_cells": ["884a1072b7fffff"],
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demographics": {"status": "available"},
                "competitors": {"status": "available"},
                "rent": {"status": "available"},
                "mobility": {"status": "available"},
                "traffic": {"status": "available"},
                "coverage": {
                    "status": "available",
                    "overall_readiness": "ready",
                    "domain_coverage": {"MOBILITY": "complete"},
                    "domain_status": {"MOBILITY": "available"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 100,
                    "sample_count": 100,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
        "metadata": {},
    }

    doc = CatchmentProfileDocument.from_dict(payload)
    assert doc.document_id == "cat-doc-001"
    assert doc.period_grain is PeriodGrain.MONTHLY
    prof = doc.profiles[0]
    assert prof.boundary.travel_mode is TravelMode.motorcycle
    assert prof.boundary.cutoff_seconds == 600
    assert prof.coverage.overall_readiness is ReadinessLevel.ready

    wire = doc.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert CatchmentProfileDocument.from_dict(wire).to_dict() == wire


def test_generated_coverage_surface_round_trips(release):
    from packages.oday_data_product_contracts_client.models.coverage_surface import (
        CoverageSurface,
    )

    pinned = release.pin.contract("emgi.coverage-surface.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_version": "emgi.coverage-surface.v1",
        "surface_id": "cov-surf-1",
        "product_version": "0.4.1",
        "domain": "mobility",
        "dataset_ids": ["ds-1"],
        "scope_principal_id": "sp-1",
        "spatial_grain": "h3_cell",
        "temporal_grain": "month",
        "store_timezone": "Asia/Taipei",
        "generated_at": "2026-08-14T14:40:00Z",
        "h3_resolution": 8,
        "cells": [
            {
                "cell_id": "884a1072b7fffff",
                "state": "complete",
                "is_complete": True,
                "negative_evidence_valid": True,
                "observed_count": 10,
                "freshness_state": "fresh",
                "readiness": "ready",
            }
        ],
        "state_breakdown": {
            "complete": 1,
            "empty": 0,
            "saturated": 0,
            "truncated": 0,
            "partial": 0,
            "source_error": 0,
            "unknown": 0,
        },
        "freshness": {
            "state": "fresh",
            "evaluated_at": "2026-08-14T14:40:00Z",
            "stale_cell_count": 0,
            "unknown_cell_count": 0,
        },
        "search_completeness": {
            "complete": 1,
            "saturated": 0,
            "truncated": 0,
            "partial": 0,
            "source_error": 0,
            "unknown": 0,
        },
        "readiness": "ready",
        "readiness_reasons": [
            {
                "code": "complete_coverage",
                "severity": "informational",
                "detail": "Coverage meets criteria",
            }
        ],
        "blockers": [],
    }

    model = CoverageSurface.from_dict(payload)
    assert model.surface_id == "cov-surf-1"
    assert model.h3_resolution == 8
    assert model.spatial_grain == "h3_cell"

    wire = model.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert CoverageSurface.from_dict(wire).to_dict() == wire


def test_generated_market_cell_profile_round_trips(release):
    from packages.oday_data_product_contracts_client.models.market_cell_profile import (
        MarketCellProfileDocument,
        PeriodGrain,
    )

    pinned = release.pin.contract("emgi.market-cell-profile.v1")
    schema = release.schema_for(pinned)

    payload = {
        "contract_version": "emgi.market-cell-profile.v1",
        "profile_id": "mcp-doc-1",
        "product_version": "0.4.1",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "h3_resolution": 8,
        "generated_at": "2026-08-14T14:40:00Z",
        "effective_as_of": "2026-08-14T00:00:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "component_manifest_refs": [],
        "cells": [
            {
                "cell_id": "884a1072b7fffff",
                "h3_index": "884a1072b7fffff",
                "h3_resolution": 8,
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "as_of_date": "2026-08-14",
                "centroid_lat": 25.04,
                "centroid_lng": 121.56,
                "county": "Taipei",
                "district": "Xinyi",
                "admin_code": "63000",
                "demographics": {
                    "total_population": 5000,
                    "male_population": 2400,
                    "female_population": 2600,
                    "household_count": 1800,
                    "density_per_sq_km": 1000.0,
                    "daytime_population_ratio": 1.2,
                    "age_distribution": {"20-29": 1000},
                },
                "competitors": {
                    "total_competitors": 2,
                    "active_competitors": 2,
                    "competitor_density": 1.0,
                    "brands_present": ["BrandA"],
                    "price_tier_distribution": {"MID": 2},
                    "average_capacity": 50.0,
                    "capacity_sample_count": 2,
                    "stores_by_brand": {"BrandA": 2},
                    "stores_by_category": {"convenience": 2},
                },
                "rent": {
                    "mean_rent_per_ping": 2500.0,
                    "median_rent_per_ping": 2400.0,
                    "p25_rent_per_ping": 2000.0,
                    "p75_rent_per_ping": 3000.0,
                    "sample_count": 10,
                    "confidence_pct": 95.0,
                    "tier": "STANDARD",
                    "fallback_level": "CELL",
                },
                "mobility": {
                    "activity_population": 3000,
                    "resident_population": 2000,
                    "work_population": 1500,
                    "hourly_distribution": {"0": 100},
                    "dwell_time_minutes": 30.0,
                    "is_calibrated": True,
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "domain_freshness": {"GEOGRAPHY": "fresh"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 50,
                    "sample_count": 50,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-14T00:00:00Z",
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 50,
            "sample_count": 50,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-14T00:00:00Z",
        },
        "metadata": {},
    }

    doc = MarketCellProfileDocument.from_dict(payload)
    assert doc.profile_id == "mcp-doc-1"
    assert doc.period_grain is PeriodGrain.MONTHLY

    wire = doc.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert MarketCellProfileDocument.from_dict(wire).to_dict() == wire


def test_generated_data_acquisition_plan_round_trips(release):
    from packages.oday_data_product_contracts_client.models.data_acquisition_plan import (
        DataAcquisitionPlan,
        PlanStatus,
    )

    pinned = release.pin.contract("emgi.data-acquisition-plan.v1")
    schema = release.schema_for(pinned)

    payload = {
        "plan_id": "plan-1",
        "site_context_id": "ctx-1",
        "coverage_surface_id": "cov-1",
        "plan_version": 1,
        "status": "proposed",
        "effective_as_of": "2026-08-14T14:40:00Z",
        "knowledge_as_of": "2026-08-14T14:40:00Z",
        "gaps": [
            {
                "gap_id": "gap-1",
                "domain": "mobility",
                "measure": "activity",
                "decision_sensitivity": 0.8,
                "current_uncertainty_pct": 60.0,
                "expected_uncertainty_reduction_pct": 30.0,
                "estimated_cost_units": 1.0,
                "estimated_latency_hours": 2.0,
                "quota_units": 10.0,
                "survey_effort_hours": 0.0,
                "priority_rank": 1,
                "rationale": "critical",
            }
        ],
        "experiments": [],
        "policy": {},
        "metadata": {},
    }

    model = DataAcquisitionPlan.from_dict(payload)
    assert model.plan_id == "plan-1"
    assert model.status is PlanStatus.proposed
    assert model.gaps[0].domain == "mobility"

    wire = model.to_dict()
    jsonschema.validate(wire, dict(schema))
    assert DataAcquisitionPlan.from_dict(wire).to_dict() == wire


# ---------------------------------------------------------------------------
# Runtime version exposure
# ---------------------------------------------------------------------------


def test_runtime_reports_the_exact_product_version(pin):
    reset_cache()
    version = product_version()
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
    assert block["product"]["release_id"] == pin.release.id
    reported = {entry["contract_id"] for entry in block["contracts"]}
    assert reported == {contract.contract_id for contract in pin.enforced_contracts}


def test_runtime_contract_inventory_matches_the_pin(pin):
    reset_cache()
    inventory = {contract.contract_id: contract for contract in product_contracts()}
    for pinned in pin.enforced_contracts:
        reported = inventory[pinned.contract_id]
        assert reported.contract_version == pinned.contract_version
        assert reported.sha256 == pinned.sha256
        assert reported.category == pinned.category
        assert reported.module == pinned.module


def test_pin_path_is_the_documented_config_file(pin):
    assert pin.path == REPO_ROOT / "config" / "oday_data_product_contracts.toml"
    assert Path(pin.path).exists()
