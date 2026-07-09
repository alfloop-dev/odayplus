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
from tests.integration._authz import FORECASTOPS_HEADERS, auth_headers


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


def test_openapi_contract_exposes_domain_paths() -> None:
    client = TestClient(create_app())

    openapi = client.get("/openapi.json")

    assert openapi.status_code == status.HTTP_200_OK
    paths = openapi.json()["paths"]
    # integration / opsboard / data / ML domains this service exposes.
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
        assert path in paths, f"missing domain path {path} in OpenAPI contract"
