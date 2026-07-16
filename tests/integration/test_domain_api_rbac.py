"""Server-side RBAC enforcement on the domain API (ODP-GAP-API-001).

Acceptance covered:
- domain routes enforce role-based access on the server, not the client;
- an anonymous or under-privileged caller is denied with HTTP 403 and the
  denial is written to the platform security audit log (ODP-AC-AUTH-005);
- an authorized role reaches the handler, including high-risk verbs
  (approve/execute/publish) that must not be blocked at the route/type level;
- the generated OpenAPI contract exposes the integration/opsboard/data/ML
  domain paths this service is responsible for.
"""

from __future__ import annotations

from fastapi import status
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.audit.events import InMemoryAuditLog
from shared.audit.policy import SECURITY_EVENT_TYPE
from shared.auth import Role
from tests.integration._authz import (
    EXTERNAL_DATA_HEADERS,
    FORECASTOPS_HEADERS,
    HEATZONE_HEADERS,
    LISTING_HEADERS,
    auth_headers,
)


def _security_denials(audit_log: InMemoryAuditLog) -> list:
    return [
        event
        for event in audit_log.list_events()
        if event.event_type == SECURITY_EVENT_TYPE and event.outcome == "deny"
    ]


def test_domain_route_denies_anonymous_and_writes_security_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log))

    response = client.get("/forecastops/timeseries")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    denials = [
        event
        for event in audit_log.list_events()
        if event.event_type == SECURITY_EVENT_TYPE and event.outcome == "deny"
    ]
    assert denials, "a 403 must record a security authorization event"
    assert denials[-1].action == "view"
    assert denials[-1].resource == "forecastops"


def test_domain_route_denies_wrong_role() -> None:
    # MARKETING_MANAGER is only granted the adlift resource, never forecastops.
    client = TestClient(
        create_app(), headers=auth_headers(Role.MARKETING_MANAGER)
    )

    response = client.get("/forecastops/timeseries")

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_domain_route_allows_authorized_role() -> None:
    client = TestClient(create_app(), headers=FORECASTOPS_HEADERS)

    response = client.get("/forecastops/timeseries")

    assert response.status_code == status.HTTP_200_OK
    assert "items" in response.json()


def test_high_risk_execute_route_is_reachable_with_authorized_role() -> None:
    # Regression guard: EXECUTE/APPROVE/... are HIGH_RISK verbs. Route-level RBAC
    # must gate them on role only; the high-risk feature-flag / separation-of-
    # duties governance is enforced inside the handler/workflow once the target
    # object is loaded. If the whole engine ran at the route/type level these
    # would 403 unconditionally (no active flag, no object context).
    client = TestClient(create_app(), headers=FORECASTOPS_HEADERS)

    response = client.post("/forecastops/forecast-jobs", json={})

    assert response.status_code != status.HTTP_403_FORBIDDEN
    assert response.status_code != status.HTTP_401_UNAUTHORIZED


def test_heatzone_routes_deny_anonymous_and_write_security_audit() -> None:
    # Regression guard for the RBAC coverage gap: the heatzone router had no
    # require_permission guard, so anonymous callers reached read + score-job
    # routes (ODP-GAP-API-001 review). Both the read and the create verb must
    # now deny anonymous callers and record a security audit event.
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log))

    listing = client.get("/heatzones")
    score_job = client.post("/heatzones/score-jobs", json={"features": []})

    assert listing.status_code == status.HTTP_403_FORBIDDEN
    assert score_job.status_code == status.HTTP_403_FORBIDDEN
    denials = _security_denials(audit_log)
    assert denials, "a 403 must record a security authorization event"
    assert {event.resource for event in denials} == {"heatzone"}
    assert {event.action for event in denials} == {"view", "create"}


def test_heatzone_routes_allow_expansion_user() -> None:
    client = TestClient(create_app(), headers=HEATZONE_HEADERS)

    listing = client.get("/heatzones")
    score_job = client.post("/heatzones/score-jobs", json={"features": []})

    assert listing.status_code == status.HTTP_200_OK
    # CREATE is not a HIGH_RISK verb, but the route must be reachable (not 403).
    assert score_job.status_code != status.HTTP_403_FORBIDDEN
    assert score_job.status_code != status.HTTP_401_UNAUTHORIZED


def test_listing_routes_deny_anonymous_and_write_security_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log))

    import_job = client.post("/listings/import-jobs", json={"records": []})
    candidates = client.get("/listings/candidates")

    assert import_job.status_code == status.HTTP_403_FORBIDDEN
    assert candidates.status_code == status.HTTP_403_FORBIDDEN
    denials = _security_denials(audit_log)
    assert denials, "a 403 must record a security authorization event"
    assert {event.resource for event in denials} == {"listing"}
    assert {event.action for event in denials} == {"view", "create"}


def test_listing_routes_allow_expansion_user() -> None:
    client = TestClient(create_app(), headers=LISTING_HEADERS)

    import_job = client.post("/listings/import-jobs", json={"records": []})
    candidates = client.get("/listings/candidates")

    assert import_job.status_code != status.HTTP_403_FORBIDDEN
    assert import_job.status_code != status.HTTP_401_UNAUTHORIZED
    assert candidates.status_code == status.HTTP_200_OK


def test_external_data_route_denies_anonymous_and_writes_security_audit() -> None:
    audit_log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=audit_log))

    response = client.get("/external-data/freshness")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    denials = _security_denials(audit_log)
    assert denials, "a 403 must record a security authorization event"
    assert denials[-1].resource == "integration"
    assert denials[-1].action == "view"


def test_external_data_route_allows_data_owner() -> None:
    client = TestClient(create_app(), headers=EXTERNAL_DATA_HEADERS)

    response = client.get("/external-data/freshness")

    assert response.status_code == status.HTTP_200_OK
    assert "freshness" in response.json()


def test_openapi_contract_exposes_domain_paths() -> None:
    client = TestClient(create_app())

    openapi = client.get("/openapi.json")

    assert openapi.status_code == status.HTTP_200_OK
    paths = openapi.json()["paths"]
    # integration / opsboard / data / ML domains this service exposes. The
    # documented contract is versioned as of ODP-PGAP-API-001; the unversioned
    # paths still serve as deprecated aliases (covered by
    # tests/contract/test_api_versioning.py) but are deliberately kept out of
    # the schema so the generated client cannot target them.
    for path in (
        "/forecastops/timeseries",
        "/avm/cases",
        "/priceops/plans",
        "/netplan/scenarios",
        "/interventions",
        "/learninghub/releases",
        "/audit/evidence/exports",
        "/external-data/freshness",
    ):
        assert f"/api/v1{path}" in paths, f"missing domain path /api/v1{path} in OpenAPI contract"
        assert path not in paths, f"deprecated alias {path} must not be in the OpenAPI contract"
