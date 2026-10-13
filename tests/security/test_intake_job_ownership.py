"""Stored-job ownership: actual JWT, aliases and memory mutation; no workers."""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
from shared.jobs.queue import JobRecord, JobStatus
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_opsboard_jwks_auth import _jwk, _token


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
@pytest.mark.parametrize("role", ["expansion_user", "site_reviewer"])
def test_affirmative_staff_ownership_before_read_and_retry(monkeypatch, alias, role, job_app):
    key, bundle, app = job_app
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {
                    "roles": [role],
                    "scope": {
                        "tenant_id": TENANT,
                        "store_ids": ["store-A"],
                        "clearance": "CONFIDENTIAL",
                    },
                }
            },
        ),
        key_resolver=JwksResolver(
            "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]}
        ),
    )
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    cases = [
        ("missing", {}, False),
        ("empty", {"submitter": "", "owner": ""}, False),
        ("unassigned", {"submitter": "unassigned"}, False),
        ("other", {"owner": "other-staff"}, False),
        ("own-submitter", {"submitter": "operator-real-1"}, True),
        ("own-owner", {"owner": "operator-real-1"}, True),
    ]
    failures = []
    client = TestClient(app)  # No lifespan/dispatch.
    try:
        for case, ownership, staff_allowed in cases:
            allowed = staff_allowed or role == "site_reviewer"
            record = JobRecord(
                job_type="batch-listing-intake",
                status=JobStatus.PARTIAL,
                payload={
                    "tenant_id": TENANT,
                    **ownership,
                    "items": [{"item_id": "one", "storeId": "store-A"}],
                    "receipt": {
                        "items": [
                            {
                                "item_id": "one",
                                "result_ref": "private-reference",
                                "item_status": "FAILED",
                                "error": {"retryable": True},
                            }
                        ]
                    },
                },
                correlation_id="offline-owner",
            )
            bundle.job_queue._jobs[record.job_id] = record
            before = deepcopy(record.to_dict())
            response = client.get(f"{alias}/{record.job_id}", headers=headers)
            if response.status_code != (200 if allowed else 403):
                failures.append(f"{case} read={response.status_code}")
            if not allowed and "private-reference" in response.text:
                failures.append(f"{case} serialized receipt")
            retry = client.post(f"{alias}/{record.job_id}/retries", headers=headers)
            after = bundle.job_queue.get(record.job_id).to_dict()
            if allowed:
                assert retry.status_code == 202 and after["version"] == before["version"] + 1, case
            else:
                if retry.status_code != 403 or retry.json().get("detail") != "OWNERSHIP_REQUIRED":
                    failures.append(f"{case} retry={retry.status_code}")
                if after != before:
                    failures.append(f"{case} unauthorized state/version/payload mutation")
    finally:
        client.close()
    assert not failures, "\n".join(failures)
