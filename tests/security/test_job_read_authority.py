"""Mounted aliases/verified synthetic JWTs, read-only memory queue, no live jobs.

Reuse app/key per module, fresh queue/principal per alias: initialization is not authority.
Every permission/tenant/type case still executes its own actual signed request.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
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
