"""Mounted synthetic JWT/memory queue authority; no worker or business execution."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
from shared.audit import InMemoryAuditLog
from shared.infrastructure.persistence import build_persistence
from shared.jobs.queue import JobRecord
from tests.security.test_auth_composition_extension import TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_opsboard_jwks_auth import _jwk, _token


@pytest.mark.parametrize(
    "case",
    [
        "assisted-enqueue",
        "store-flat",
        "store-nested",
        "in-scope-flat",
        "in-scope-nested",
        "missing-store",
        "conflicting-store",
        "replay-store",
    ],
)
def test_intake_queue_mutation_and_complete_object_scope(monkeypatch, case):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bundle = build_persistence(mode="memory")
    app = create_app(
        persistence=bundle, audit_log=InMemoryAuditLog(), external_provider_validation=lambda: None
    )
    roles = (
        ["auditor"]
        if case == "assisted-enqueue"
        else ["data_owner", "operator_viewer"]
        if case == "replay-store"
        else ["auditor", "operator_viewer"]
    )
    scope = {
        "tenant_id": TENANT,
        "clearance": "CONFIDENTIAL",
        **({} if case == "assisted-enqueue" else {"store_ids": ["store-A"]}),
    }
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={"operator-real-1": {"roles": roles, "scope": scope}},
        ),
        key_resolver=JwksResolver(
            "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]}
        ),
    )
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    c = TestClient(app)
    try:
        if case == "assisted-enqueue":
            response = c.post(
                "/api/v1/jobs",
                headers=headers,
                json={
                    "job_type": "assisted-listing-intake",
                    "payload": {"tenant_id": TENANT, "items": [{"item_id": "row-001"}]},
                },
            )
            assert response.status_code == 403, response.status_code
            assert not bundle.job_queue._jobs
        else:
            allowed = case.startswith("in-scope")
            store = "store-A" if allowed else "store-B"
            axis = {"scope": {"store_id": store}} if case.endswith("nested") else {"storeId": store}
            if case == "missing-store":
                axis = {}
            if case == "conflicting-store":
                axis = {"storeId": "store-A", "scope": {"store_id": "store-B"}}
            item = {"item_id": "row-001", "address_raw": "offline-other-store-address", **axis}
            record = JobRecord(
                job_type="batch-listing-intake",
                payload={"tenant_id": TENANT, "items": [item]},
                correlation_id="offline",
            )
            bundle.job_queue._jobs[record.job_id] = record
            if case == "replay-store":
                token = "offline-replay"
                queue_key = f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
                bundle.job_queue._idempotency_index[queue_key] = record.job_id
                response = c.post(
                    "/api/v1/jobs",
                    headers={**headers, "Idempotency-Key": token},
                    json={
                        "job_type": "batch-listing-intake",
                        "payload": {"items": [{"item_id": "safe", "storeId": "store-A"}]},
                    },
                )
                assert (
                    response.status_code == 403
                    and "offline-other-store-address" not in response.text
                )
                assert list(bundle.job_queue._jobs) == [record.job_id]
                return
            for path in ["/jobs/", "/api/v1/jobs/"]:
                response = c.get(path + record.job_id, headers=headers)
                if allowed:
                    assert response.status_code == 200, response.status_code
                    assert response.json()["payload"]["items"][0]["item_id"] == "row-001"
                else:
                    assert response.status_code in {403, 404, 422}, response.status_code
                    assert "offline-other-store-address" not in response.text
    finally:
        c.close()
