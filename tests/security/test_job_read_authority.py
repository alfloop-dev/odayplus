"""Mounted aliases/verified synthetic JWTs, read-only memory queue, no live jobs.

Reuse app/key per module, fresh queue/principal per alias: initialization is not authority.
Every permission/tenant/type case still executes its own actual signed request.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
from shared.audit import AuditEvent
from shared.jobs.queue import JobRecord
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import OTHER, TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_opsboard_jwks_auth import _jwk, _token


@pytest.mark.parametrize("path", ["/jobs/", "/api/v1/jobs/"])
def test_job_read_checks_authority_before_serialization(monkeypatch: Any, path: str, job_app: Any) -> None:
    key, bundle, app = job_app
    original = JobRecord.to_dict
    serialized = []

    def to_dict(self: Any) -> Any:
        serialized.append(self.job_id)
        return original(self)

    monkeypatch.setattr(JobRecord, "to_dict", to_dict)
    c = TestClient(app)  # No lifespan/worker activation.
    try:
        for case in [
            "same-tenant-no-permission",
            "tenantless-anonymous",
            "unknown-type",
            "other-tenant",
            "healthy",
        ]:
            serialized.clear()
            record = JobRecord(
                job_type="unsupported-fixture" if case == "unknown-type" else "external-fetch",
                payload={
                    "summary": {"private": "offline-private-job-marker"},
                    **(
                        {}
                        if case == "tenantless-anonymous"
                        else {"tenant_id": OTHER if case == "other-tenant" else TENANT}
                    ),
                },
                correlation_id="offline",
            )
            bundle.job_queue._jobs[record.job_id] = record
            boundary = AuthenticationBoundary(
                AuthBoundaryConfig(
                    issuer="https://idp.example.test",
                    audiences=frozenset({"oday-api"}),
                    jwks_uri="https://offline.invalid/jwks",
                    principal_mappings={
                        "operator-real-1": {
                            "roles": [
                                "platform_admin"
                                if case == "same-tenant-no-permission"
                                else "data_owner"
                            ],
                            "scope": {"tenant_id": TENANT},
                        }
                    },
                ),
                key_resolver=JwksResolver(
                    "https://offline.invalid/jwks",
                    fetch=lambda _: {"keys": [_jwk(key, "offline-key")]},
                ),
            )
            monkeypatch.setattr(deps, "default_boundary", lambda boundary=boundary: boundary)
            headers = (
                {}
                if case == "tenantless-anonymous"
                else {"authorization": "Bearer " + _token(key, "offline-key")}
            )
            result = c.get(path + record.job_id, headers=headers)
            if case == "healthy":
                assert result.status_code == 200 and serialized == [record.job_id], case
                assert result.json()["payload"]["tenant_id"] == TENANT
            else:
                assert result.status_code in {401, 403, 404}, (case, result.status_code)
                assert not serialized and "offline-private-job-marker" not in result.text
        assert c.get(path + "missing-offline-job").status_code == 401
    finally:
        c.close()


@pytest.mark.parametrize("alias", ["", "/api/v1"])
def test_enqueue_audit_is_tenant_attributed_without_adopting_legacy_events(
    monkeypatch: Any, alias: str, job_app: Any, offline_job_app: Any,
) -> None:
    key, bundle, app = job_app
    audit = offline_job_app[3]
    correlation_id = f"corr-offline-enqueue-{uuid4().hex}"
    legacy = audit.record(AuditEvent(
        event_type="job.enqueue", actor="fixture", action="enqueue", resource="legacy",
        outcome="accepted", correlation_id=correlation_id, metadata={"legacy": True},
    ))

    def bind_tenant(tenant: str) -> None:
        boundary = AuthenticationBoundary(
            AuthBoundaryConfig(
                issuer="https://idp.example.test",
                audiences=frozenset({"oday-api"}),
                jwks_uri="https://offline.invalid/jwks",
                principal_mappings={"operator-real-1": {
                    "roles": ["data_owner"], "scope": {"tenant_id": tenant},
                }},
            ),
            key_resolver=JwksResolver(
                "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]},
            ),
        )
        monkeypatch.setattr(deps, "default_boundary", lambda: boundary)

    bind_tenant(TENANT)
    headers = {
        "authorization": "Bearer " + _token(key, "offline-key"),
        "x-correlation-id": correlation_id,
        "Idempotency-Key": f"offline-enqueue-{uuid4().hex}",
    }
    client = TestClient(app)  # No lifespan or job execution.
    try:
        event_ids = []
        for created in (True, False):
            response = client.post(f"{alias}/jobs", headers=headers, json={
                "job_type": "external-fetch", "payload": {"tenant_id": TENANT},
            })
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["created"] is created
            assert bundle.job_queue.get(body["job_id"]).payload["tenant_id"] == TENANT
            event_ids.append(body["audit_event_id"])
        visible = client.get(f"{alias}/audit/events", headers=headers,
                             params={"correlation_id": correlation_id})
        assert visible.status_code == 200, visible.text
        events = visible.json()["events"]
        assert {event["event_id"] for event in events} == set(event_ids)
        assert {event["outcome"] for event in events} == {"accepted", "idempotent_replay"}
        assert all(event["metadata"]["tenant_id"] == TENANT for event in events)

        # An arbitrary request tenant cannot attribute accepted work to a foreign
        # tenant; retain both the mutation refusal and the audit read isolation.
        spoofed = client.post(f"{alias}/jobs", headers=headers, json={
            "job_type": "external-fetch", "payload": {"tenant_id": OTHER},
        })
        assert spoofed.status_code == 403
        assert len(bundle.job_queue._jobs) == 1
        bind_tenant(OTHER)
        foreign = client.get(f"{alias}/audit/events", headers=headers,
                             params={"correlation_id": correlation_id})
        assert foreign.status_code == 200 and foreign.json()["events"] == []
        assert client.get(f"{alias}/audit/events").status_code == 401
        assert legacy.metadata == {"legacy": True}  # No historical rewrite/adoption.
        assert legacy.event_id not in {event.event_id for event in audit.list_events(tenant_id=TENANT)}
    finally:
        client.close()
