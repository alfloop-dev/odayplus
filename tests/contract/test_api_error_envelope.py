"""Every error leaves the API in one structured envelope (ODP-PGAP-API-001).

The envelope is installed at the boundary via exception handlers, so these
tests deliberately exercise *legacy* routes that still raise plain
``HTTPException`` -- proving the contract holds without those 118 call sites
being edited.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.api.errors import ErrorCode

ENVELOPE_FIELDS = {"code", "message", "next_action", "occurred_at", "details", "correlation_id"}

OPERATOR_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def test_envelope_carries_every_required_field() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/jobs/no-such-job", headers={"x-correlation-id": "corr-env-1"})

    assert response.status_code == 404
    envelope = response.json()["error"]
    assert set(envelope) == ENVELOPE_FIELDS
    assert envelope["code"] == ErrorCode.NOT_FOUND
    assert envelope["message"]
    assert envelope["next_action"]
    assert envelope["occurred_at"].endswith("Z")


def test_envelope_correlation_id_matches_the_response_header() -> None:
    """The envelope's correlation ID must be the one the audit log recorded.

    An error body carrying a *different* id than the header would send an
    operator to the wrong audit event -- worse than carrying none.
    """
    client = TestClient(create_app())
    response = client.get("/api/v1/jobs/missing", headers={"x-correlation-id": "corr-env-2"})

    assert response.json()["error"]["correlation_id"] == "corr-env-2"
    assert response.headers["x-correlation-id"] == "corr-env-2"


def test_legacy_string_detail_is_preserved_verbatim() -> None:
    """`detail` keeps its exact legacy value; `error` is purely additive.

    The operator console renders `detail`, so changing it would blank refusal
    copy in the UI.
    """
    client = TestClient(create_app())
    response = client.get("/api/v1/jobs/missing")

    body = response.json()
    assert body["detail"] == "job not found"
    assert body["error"]["message"] == "job not found"


def test_object_detail_is_not_flattened_into_a_string() -> None:
    """A route returning an object `detail` keeps every field callers branch on.

    ``network_scoring`` sends ``{"message": ..., "missing": [...]}`` and the
    console reads ``missing``. Summarising it to a string is a silent break --
    it type-errors at the caller, not here.
    """
    client = TestClient(create_app(external_provider_validation=lambda: None))
    # CS-1003 fails the geocode data gate, so the route refuses with its
    # {message, missing} object rather than a bare string.
    response = client.post(
        "/api/v1/operator/network-scoring/candidates/CS-1003/score",
        headers={**OPERATOR_HEADERS, "Idempotency-Key": "idem-envelope-object"},
        json={},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert isinstance(body["detail"], dict), "object detail must not be flattened"
    assert "missing" in body["detail"]
    # ...and the envelope still summarises it, so a generic client is served too.
    assert body["error"]["message"] == body["detail"]["message"]
    assert body["error"]["details"] == [body["detail"]]


def test_validation_errors_use_the_envelope_and_keep_the_pydantic_array() -> None:
    client = TestClient(create_app())
    response = client.post("/api/v1/jobs", json={"payload": {}})  # job_type missing

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == ErrorCode.VALIDATION_FAILED
    # Legacy shape retained for existing parsers...
    assert isinstance(body["detail"], list)
    assert body["detail"][0]["loc"]
    # ...and restated as field/message rows in the envelope.
    assert body["error"]["details"][0]["field"] == "job_type"


def test_forbidden_uses_the_forbidden_code_not_a_generic_failure() -> None:
    """Server-derived authz denial is branchable by code, not by prose."""
    client = TestClient(create_app())
    response = client.get("/api/v1/priceops/plans", headers={"x-roles": "viewer"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


def test_unknown_route_also_returns_the_envelope() -> None:
    """Starlette's own routing 404 is normalised too, not just route raises."""
    client = TestClient(create_app())
    response = client.get("/api/v1/definitely-not-a-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND
