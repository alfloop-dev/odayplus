"""Verified JWT + mounted aliases + actual memory queue, never worker execution."""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
from shared.jobs.queue import JobRecord, JobStatus
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import OTHER, TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_opsboard_jwks_auth import _jwk, _token


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_complete_scope_precedes_every_read_and_retry_mutation(monkeypatch, alias, job_app):
    key, bundle, app = job_app
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {
                    "roles": ["site_reviewer", "operator_viewer"],
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
    receipt = {
        "items": [
            {
                "item_id": "row-001",
                "result_ref": "private-intake-reference",
                "item_status": "FAILED",
                "error": {"retryable": True},
            }
        ]
    }
    a = {"item_id": "row-A", "storeId": "store-A"}
    b = {"item_id": "row-B", "storeId": "store-B"}
    cases = [
        ("empty-items", {"storeId": "store-B", "items": []}, False),
        ("empty-both", {"scope": {"store_id": "store-B"}, "items": [], "rows": []}, False),
        ("envelope", {"storeId": "store-B"}, False),
        ("both-lists", {"items": [a], "rows": [b]}, False),
        ("foreign-item-tenant", {"items": [{**a, "tenant_id": OTHER}]}, False),
        ("null-list", {"items": None}, False),
        ("object-list", {"items": {"storeId": "store-A"}}, False),
        ("invalid-item", {"items": [None]}, False),
        ("healthy-empty", {"storeId": "store-A", "items": [], "rows": []}, True),
        ("healthy-both", {"items": [a], "rows": [{**a, "tenant_id": TENANT}]}, True),
        ("healthy-envelope", {"scope": {"store_id": "store-A"}}, True),
    ]
    failures = []
    # Same app/key, but a fresh stored record per case; exercise every scenario
    # even against the rejected baseline. Retain complete before/after state.
    client = TestClient(app)  # No lifespan/worker dispatch.
    try:
        for case, fields, allowed in cases:
            record = JobRecord(
                job_type="batch-listing-intake",
                status=JobStatus.PARTIAL,
                payload={"tenant_id": TENANT, **deepcopy(fields), "receipt": deepcopy(receipt)},
                correlation_id="offline-retry",
            )
            bundle.job_queue._jobs[record.job_id] = record
            before = deepcopy(record.to_dict())
            read = client.get(f"{alias}/{record.job_id}", headers=headers)
            if read.status_code != (200 if allowed else 403):
                failures.append(f"{case} read status {read.status_code}")
            if not allowed and "private-intake-reference" in read.text:
                failures.append(f"{case} serialized denied receipt")
            retry = client.post(
                f"{alias}/{record.job_id}/retries",
                headers=headers,
                json={"retry_scope": "FAILED_ONLY"},
            )
            after = bundle.job_queue.get(record.job_id)
            if allowed:
                assert retry.status_code == 202, (case, retry.status_code)
                assert after.status == JobStatus.QUEUED and after.version == before["version"] + 1
                assert (
                    after.payload["_retry_scope"] == "FAILED_ONLY"
                    and retry.json()["retried_items_count"] == 1
                )
            else:
                if retry.status_code != 403:
                    failures.append(f"{case} retry status {retry.status_code}")
                if after.to_dict() != before:
                    failures.append(f"{case} mutated denied status/version/payload")
    finally:
        client.close()
    assert not failures, failures
