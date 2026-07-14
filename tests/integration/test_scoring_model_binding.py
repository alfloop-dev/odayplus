"""ODP-GAP-ML-002: production model binding + fail-closed guards.

Covers the gap-closure contract for the HeatZone / SiteScore / ForecastOps
scoring services:

* the app seeds a PRODUCTION model version per service into the durable
  registry, and each service resolves + reports that binding as audit metadata;
* the seeded spec never drifts from the module domain ``*_MODEL_VERSION`` /
  ``*_FEATURE_VERSION`` constants;
* a fresh run with absent live inputs fails closed (HTTP 422), while an
  idempotent replay of a prior run is unaffected;
* the fail-closed / resolution helpers behave correctly at the unit level.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from models.shared_ml import (
    SCORING_MODEL_SPECS_BY_SERVICE,
    ModelAlias,
    ProductionModelUnavailableError,
    ScoringInputUnavailableError,
    require_live_inputs,
    resolve_production_binding,
    seed_scoring_models,
)
from modules.forecastops.domain.forecasting import (
    FORECASTOPS_FEATURE_VERSION,
    FORECASTOPS_MODEL_VERSION,
)
from modules.heatzone.domain.scoring import HEATZONE_FEATURE_VERSION, HEATZONE_MODEL_VERSION
from modules.learninghub.infrastructure import InMemoryLearningHubRepository
from modules.sitescore.domain.scoring import SITESCORE_FEATURE_VERSION, SITESCORE_MODEL_VERSION
from tests.integration._authz import (
    FORECASTOPS_HEADERS,
    HEATZONE_HEADERS,
    SITESCORE_HEADERS,
)

_DOMAIN_CONSTANTS = {
    "heatzone": (HEATZONE_MODEL_VERSION, HEATZONE_FEATURE_VERSION),
    "sitescore": (SITESCORE_MODEL_VERSION, SITESCORE_FEATURE_VERSION),
    "forecastops": (FORECASTOPS_MODEL_VERSION, FORECASTOPS_FEATURE_VERSION),
}


# -- unit: specs, seeding, resolution, fail-closed -----------------------------


@pytest.mark.parametrize("service", ["heatzone", "sitescore", "forecastops"])
def test_spec_matches_domain_constants(service: str) -> None:
    """The registry spec must not drift from the module domain literals."""
    model_version, feature_version = _DOMAIN_CONSTANTS[service]
    spec = SCORING_MODEL_SPECS_BY_SERVICE[service]
    assert spec.domain_model_version == model_version
    assert spec.feature_schema_version == feature_version


def test_seed_scoring_models_registers_production_alias_idempotently() -> None:
    repository = InMemoryLearningHubRepository()

    first = seed_scoring_models(repository, git_sha="abc123")
    second = seed_scoring_models(repository, git_sha="def456")

    for service, spec in SCORING_MODEL_SPECS_BY_SERVICE.items():
        production = repository.get_alias(spec.model_name, ModelAlias.PRODUCTION)
        assert production is not None
        assert production.stage.value == "production"
        # Idempotent: only one version registered, git_sha stays from first seed.
        assert len(repository.list_model_versions(spec.model_name)) == 1
        assert first[service].git_sha == "abc123"
        assert second[service].git_sha == "abc123"
        assert first[service].dataset_snapshot_id == spec.dataset_snapshot_id


def test_resolve_production_binding_fails_closed_on_empty_registry() -> None:
    repository = InMemoryLearningHubRepository()
    with pytest.raises(ProductionModelUnavailableError):
        resolve_production_binding(repository, service="heatzone")


def test_resolve_production_binding_returns_audit_metadata() -> None:
    repository = InMemoryLearningHubRepository()
    seed_scoring_models(repository, git_sha="sha-1")

    binding = resolve_production_binding(repository, service="sitescore")
    metadata = binding.to_audit_metadata()

    assert metadata["model_service"] == "sitescore"
    assert metadata["model_stage"] == "production"
    assert "production" in metadata["model_aliases"]
    assert metadata["feature_schema_version"] == SITESCORE_FEATURE_VERSION
    assert metadata["model_git_sha"] == "sha-1"
    assert metadata["dataset_snapshot_id"]


def test_require_live_inputs_rejects_absent_inputs() -> None:
    with pytest.raises(ScoringInputUnavailableError):
        require_live_inputs([], service="heatzone")
    with pytest.raises(ScoringInputUnavailableError):
        require_live_inputs(None, service="heatzone")
    # Non-empty passes silently.
    require_live_inputs([{"h3_index": "x"}], service="heatzone")


# -- API: fail-closed + binding metadata on the wire ---------------------------


def test_heatzone_score_job_binds_model_and_fails_closed() -> None:
    client = TestClient(create_app())

    absent = client.post(
        "/heatzones/score-jobs",
        json={"features": []},
        headers={**HEATZONE_HEADERS, "x-correlation-id": "corr-hz-fc"},
    )
    assert absent.status_code == 422
    assert "fail-closed" in absent.json()["detail"]

    ok = client.post(
        "/heatzones/score-jobs",
        json={
            "features": [
                {
                    "h3_index": "h3r9_0200_0200",
                    "poi_count": 15,
                    "competitor_count": 1,
                    "active_listing_count": 4,
                    "median_listing_rent": 50_000,
                    "average_confidence": 0.9,
                    "source_snapshot_ids": ["poi", "listing"],
                }
            ]
        },
        headers={**HEATZONE_HEADERS, "x-correlation-id": "corr-hz-ok"},
    )
    assert ok.status_code == 202
    binding = ok.json()["model_binding"]
    assert binding["model_service"] == "heatzone"
    assert binding["model_stage"] == "production"

    audit = client.get("/audit/events", params={"correlation_id": "corr-hz-ok"})
    run_events = [e for e in audit.json()["events"] if e["action"] == "run_model"]
    assert run_events
    assert run_events[0]["metadata"]["model_binding"]["model_id"]


def test_sitescore_score_job_binds_model_and_fails_closed() -> None:
    client = TestClient(create_app(), headers=SITESCORE_HEADERS)

    absent = client.post(
        "/sitescore/score-jobs",
        json={"features": []},
        headers={"x-correlation-id": "corr-ss-fc"},
    )
    assert absent.status_code == 422
    assert "fail-closed" in absent.json()["detail"]

    ok = client.post(
        "/sitescore/score-jobs",
        json={
            "features": [
                {
                    "candidate_site_id": "CS-BIND-001",
                    "heat_zone_score": 85,
                    "monthly_rent": 50_000,
                    "area_ping": 25,
                    "comparable_store_count": 5,
                    "comparable_monthly_revenue_p50": 450_000,
                }
            ]
        },
        headers={"x-correlation-id": "corr-ss-ok"},
    )
    assert ok.status_code == 202
    assert ok.json()["model_binding"]["model_service"] == "sitescore"


def test_forecastops_forecast_job_binds_model_and_fails_closed() -> None:
    client = TestClient(create_app(), headers=FORECASTOPS_HEADERS)

    absent = client.post(
        "/forecastops/forecast-jobs",
        json={"inputs": []},
        headers={"x-correlation-id": "corr-fo-fc"},
    )
    assert absent.status_code == 422
    assert "fail-closed" in absent.json()["detail"]

    ok = client.post(
        "/forecastops/forecast-jobs",
        json={
            "inputs": [
                {
                    "store_id": "store-bind-001",
                    "observations": [
                        {
                            "business_date": "2026-06-25",
                            "actual_revenue": 120_000,
                            "site_score_baseline_p50": 120_000,
                            "source_snapshot_ids": ["pos-20260625"],
                        }
                    ],
                }
            ]
        },
        headers={"x-correlation-id": "corr-fo-ok"},
    )
    assert ok.status_code == 202
    assert ok.json()["model_binding"]["model_service"] == "forecastops"


def test_fresh_run_fails_closed_but_idempotent_replay_is_unaffected() -> None:
    """An empty-body replay of an existing idempotency key still returns the
    cached job (created=False) — the fail-closed guard only blocks fresh runs."""
    client = TestClient(create_app())
    headers = {**HEATZONE_HEADERS, "Idempotency-Key": "hz-bind-replay"}

    first = client.post(
        "/heatzones/score-jobs",
        json={
            "features": [
                {"h3_index": "h3r9_0300_0300", "poi_count": 12, "source_snapshot_ids": ["poi"]}
            ]
        },
        headers=headers,
    )
    assert first.status_code == 202
    assert first.json()["created"] is True

    replay = client.post(
        "/heatzones/score-jobs",
        json={"features": []},
        headers=headers,
    )
    assert replay.status_code == 202
    assert replay.json()["created"] is False
