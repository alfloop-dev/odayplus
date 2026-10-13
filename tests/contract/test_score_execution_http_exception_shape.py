"""HTTP protocol controls with an explicit synthetic middleware principal.

No database, model inference or actual authentication is used in this fixture.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from modules.opsboard.application.network_scoring import NetworkScoringService
from shared.auth import Principal, Role, Scope


class RefusingOwner:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def context(self, *_args, **_kwargs):
        self.calls += 1
        raise self.error

    def retry(self, *_args, **_kwargs):
        self.calls += 1
        raise self.error

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return None


def fixture(owner=None):
    app = FastAPI()
    principal = Principal(str(uuid4()), frozenset({Role.EXPANSION_USER}), Scope(tenant_id=str(uuid4())))

    @app.middleware("http")
    async def synthetic_identity(request: Request, call_next):
        request.state.operator_principal = principal
        request.state.correlation_id = "synthetic-http-shape"
        return await call_next(request)

    service = NetworkScoringService(seed_fixtures=False)
    app.include_router(create_network_scoring_sub_router(service,
        require_view_permission_fn=lambda: None, require_write_permission_fn=lambda: None,
        execution_service=owner), prefix="/operator")
    return TestClient(app), principal


def send(client, principal, endpoint):
    if endpoint == "context":
        return client.get("/operator/network-scoring/candidates/synthetic-candidate/execution-context")
    return client.post("/operator/network-scoring/execution-requests/synthetic-request/retry",
        headers={"If-Actor-Match": principal.subject_id, "Idempotency-Key": "synthetic-key"},
        json={"expectedVersion": 1, "expectedJobVersion": 1, "reason": "Synthetic protocol reason",
              "expectedActorSubjectId": principal.subject_id, "expectedTenantId": principal.tenant_id})


@pytest.mark.parametrize("endpoint", ["context", "retry"])
@pytest.mark.parametrize("status", [401, 403, 409, 503])
def test_authoritative_http_error_preserves_status_detail_and_headers(endpoint, status):
    detail = {"code": f"SYNTHETIC_PUBLIC_ERROR_{status}", "reason": "Synthetic public refusal"}
    owner = RefusingOwner(HTTPException(status, detail, headers={"Retry-After": "7"}))
    client, principal = fixture(owner)
    response = send(client, principal, endpoint)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["retry-after"] == "7"
    assert owner.calls == 1


@pytest.mark.parametrize("endpoint", ["context", "retry"])
def test_missing_owner_has_exact_machine_readable_unavailable_code(endpoint):
    client, principal = fixture()
    response = send(client, principal, endpoint)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "SITESCORE_EXECUTION_OWNER_UNAVAILABLE"}}


def test_existing_missing_execution_read_still_has_exact_404_code():
    owner = RefusingOwner(HTTPException(503, "unused"))
    client, _principal = fixture(owner)
    response = client.get("/operator/network-scoring/execution-requests/synthetic-request")
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "SITESCORE_EXECUTION_NOT_FOUND"}}
    assert owner.calls == 1
