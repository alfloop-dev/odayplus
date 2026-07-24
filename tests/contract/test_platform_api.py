from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import Role

# External-data freshness is an integration-domain read guarded by RBAC
# (ODP-GAP-API-001); DATA_OWNER holds the integration view grant.
_EXTERNAL_DATA_HEADERS = {"x-subject-id": "test-operator", "x-roles": Role.DATA_OWNER.value}


def test_health_routes_publish_correlation_id() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"x-correlation-id": "corr-test-1"})

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "corr-test-1"
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "oday-api"
    assert body["version"] == "0.1.0"
    assert body["correlation_id"] == "corr-test-1"
    assert "time" in body


def test_platform_version_exposes_release_sha(monkeypatch) -> None:
    monkeypatch.setenv("ODAY_RELEASE_SHA", "fd70b4f40d9bc178bb9e21ce1a24a8b4e4e95203")
    client = TestClient(create_app())

    response = client.get("/platform/version", headers={"x-correlation-id": "corr-version-1"})

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "corr-version-1"
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "oday-api"
    assert body["api_version"] == "0.1.0"
    assert body["release_sha"] == "fd70b4f40d9bc178bb9e21ce1a24a8b4e4e95203"
    assert body["correlation_id"] == "corr-version-1"
    assert "time" in body


def test_job_enqueue_is_idempotent_and_audited() -> None:
    client = TestClient(create_app())
    headers = {"x-correlation-id": "corr-job-1", "Idempotency-Key": "idem-1"}
    payload = {"job_type": "forecastops.score", "payload": {"site_id": "site-123"}}

    first = client.post("/jobs", json=payload, headers=headers)
    second = client.post("/jobs", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    first_body = first.json()
    second_body = second.json()
    assert first_body["created"] is True
    assert second_body["created"] is False
    assert first_body["job_id"] == first_body["job"]["job_id"]
    assert first_body["status"] == "queued"
    assert first_body["correlation_id"] == "corr-job-1"
    assert first_body["idempotency_key"] == "idem-1"
    assert first_body["job"]["job_id"] == second_body["job"]["job_id"]
    assert first_body["job"]["status"] == "queued"
    assert first_body["job"]["correlation_id"] == "corr-job-1"
    assert first_body["job"]["idempotency_key"] == "idem-1"

    audit = client.get("/audit/events", params={"correlation_id": "corr-job-1"})

    assert audit.status_code == 200
    events = audit.json()["events"]
    assert [event["outcome"] for event in events] == ["accepted", "idempotent_replay"]
    assert [event["result"] for event in events] == ["accepted", "idempotent_replay"]
    assert {event["job_id"] for event in events} == {first_body["job"]["job_id"]}


def test_job_lookup_and_openapi_contract() -> None:
    client = TestClient(create_app())

    enqueue = client.post("/jobs", json={"job_type": "netplan.solve", "payload": {}})
    job_id = enqueue.json()["job"]["job_id"]

    lookup = client.get(f"/jobs/{job_id}")
    openapi = client.get("/openapi.json")

    assert lookup.status_code == 200
    assert lookup.json()["job_id"] == job_id
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    # Probes stay unversioned: they are wired into deploy manifests and load
    # balancers that must not be asked to learn a version prefix.
    assert "/health" in paths
    assert "/healthz" in paths
    assert "/platform/health" in paths
    assert "/platform/version" in paths
    # Jobs and audit reads are product operations, so the *documented* contract
    # is versioned (ODP-PGAP-API-001). The unversioned paths this test's own
    # requests above still use keep working as deprecated aliases, but are
    # deliberately absent from the schema so the generated client cannot target
    # them.
    assert "/api/v1/jobs" in paths
    assert "/api/v1/jobs/{job_id}" in paths
    assert "/api/v1/audit/events" in paths
    assert "/jobs" not in paths
    assert "/audit/events" not in paths


def test_external_data_freshness_api_exposes_lineage_and_correlation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    client = TestClient(create_app())

    response = client.get(
        "/external-data/freshness",
        headers={**_EXTERNAL_DATA_HEADERS, "x-correlation-id": "corr-fresh-api"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == "corr-fresh-api"
    freshness = body["freshness"][0]
    assert freshness["provider_id"] == "listing.partner_feed"
    assert freshness["data_status"] == "FRESH"
    assert freshness["source_snapshot_id"] == "snap-expansion-20260628-0100"
    assert freshness["provider_observed_at"] == "2026-06-28T09:00:00+00:00"
    assert freshness["ingested_at"] == "2026-06-28T09:12:00+00:00"
    assert freshness["correlation_id"] == "corr-fresh-api"
