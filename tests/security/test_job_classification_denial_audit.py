"""Mounted aliases, real signed principals, memory queue; no lifespan/worker."""

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


def boundary_for(key, *, modules, clearance="RESTRICTED"):
    return AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {
                    "roles": ["site_reviewer", "operator_viewer"],
                    "scope": {"tenant_id": TENANT, "modules": modules, "clearance": clearance},
                }
            },
        ),
        key_resolver=JwksResolver(
            "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]}
        ),
    )


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
@pytest.mark.parametrize("collection", ["items", "rows"])
def test_mounted_batch_read_and_actual_replay_mask_stricter_fields_without_mutating_store(
    monkeypatch, alias, collection, job_app
):
    key, bundle, app = job_app
    boundary = boundary_for(key, modules=["listing"], clearance="HIGHLY_RESTRICTED")
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    fields = [
        {
            "field_path": "private",
            "classification": "HIGHLY_RESTRICTED",
            **{
                k: "fixture-higher-secret"
                for k in ("parsed", "normalized", "corrected", "effective")
            },
        },
        {
            "field_path": "unknown",
            "classification": "unknown-new",
            **{
                k: "fixture-unknown-secret"
                for k in ("parsed", "normalized", "corrected", "effective")
            },
        },
        {"field_path": "public", "classification": "PUBLIC", "effective": "public-value"},
    ]
    client = TestClient(app)
    try:
        created = client.post(
            alias,
            headers=headers,
            json={
                "job_type": "batch-listing-intake",
                "payload": {
                    collection: [
                        {"item_id": "row-001", "address_raw": "offline-address", "fields": fields}
                    ]
                },
            },
        )
        assert created.status_code == 202
        record = bundle.job_queue.get(created.json()["job_id"])
        assert record.payload[collection][0]["fields"] == fields
        before = deepcopy(record.to_dict())
        token = "classified-replay"
        bundle.job_queue._idempotency_index[
            f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
        ] = record.job_id
        seen = []
        enqueue = bundle.job_queue.enqueue

        def observed(*a, **kw):
            result = enqueue(*a, **kw)
            seen.append(result)
            return result

        monkeypatch.setattr(bundle.job_queue, "enqueue", observed)
        for clearance in ["HIGHLY_RESTRICTED", "RESTRICTED"]:
            boundary = boundary_for(key, modules=["listing"], clearance=clearance)
            read = client.get(f"{alias}/{record.job_id}", headers=headers)
            replay = client.post(
                alias,
                headers={**headers, "Idempotency-Key": token},
                json={
                    "job_type": "batch-listing-intake",
                    "payload": {
                        collection: [{"item_id": "row-001", "address_raw": "offline-address"}]
                    },
                },
            )
            assert read.status_code == 200 and replay.status_code == 202
            assert (
                seen[-1] == (record, False)
                and replay.json()["created"] is False
                and replay.json()["job_id"] == record.job_id
            )
            for response, view in [(read, read.json()), (replay, replay.json()["job"])]:
                actual = view["payload"][collection][0]["fields"]
                assert actual[2]["effective"] == "public-value"
                if clearance == "RESTRICTED":
                    assert all(
                        actual[i][k] is None
                        for i in [0, 1]
                        for k in ("parsed", "normalized", "corrected", "effective")
                    )
                    assert all(actual[i]["mask_reason_code"] == "FIELD_MASKED" for i in [0, 1])
                    assert (
                        "fixture-higher-secret" not in response.text
                        and "fixture-unknown-secret" not in response.text
                    )
                else:
                    assert actual == fields
            assert record.to_dict() == before
    finally:
        client.close()


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_module_read_create_replay_retry_denials_record_one_authorization_event_each(
    monkeypatch, alias, job_app, offline_job_app
):
    key, bundle, app = job_app
    audit = offline_job_app[3]
    boundary = boundary_for(key, modules=["forecastops"])
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {
        "authorization": "Bearer " + _token(key, "offline-key"),
        "x-correlation-id": "offline-denial-correlation",
    }
    payload = {
        "tenant_id": TENANT,
        "items": [{"item_id": "row-001", "address_raw": "private-row-not-for-audit"}],
        "receipt": {
            "items": [{"item_id": "row-001", "item_status": "FAILED", "error": {"retryable": True}}]
        },
    }
    record = JobRecord(
        job_type="batch-listing-intake",
        status=JobStatus.PARTIAL,
        payload=payload,
        correlation_id="offline",
    )
    bundle.job_queue._jobs[record.job_id] = record
    token = "module-audit-replay"
    bundle.job_queue._idempotency_index[
        f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
    ] = record.job_id
    client = TestClient(app)
    errors = []
    try:
        calls = [
            ("GET", f"{alias}/{record.job_id}", {}),
            ("POST", alias, {"json": {"job_type": "batch-listing-intake", "payload": payload}}),
            (
                "POST",
                alias,
                {
                    "headers": {**headers, "Idempotency-Key": token},
                    "json": {"job_type": "batch-listing-intake", "payload": payload},
                },
            ),
            ("POST", f"{alias}/{record.job_id}/retries", {"json": {"retry_scope": "FAILED_ONLY"}}),
        ]
        for method, path, kw in calls:
            before = deepcopy(bundle.job_queue._jobs)
            prior = len(audit.list_events())
            response = client.request(method, path, headers=kw.pop("headers", headers), **kw)
            assert response.status_code == 403 and bundle.job_queue._jobs == before
            denied = [
                e
                for e in audit.list_events()[prior:]
                if e.event_type == "security.authorization" and e.outcome == "deny"
            ]
            if len(denied) != 1:
                errors.append(f"{method} {path} denial-event-count={len(denied)}")
                continue
            event = denied[0]
            assert (
                event.actor == "operator-real-1"
                and event.correlation_id == "offline-denial-correlation"
            )
            assert (
                event.metadata["tenant_id"] == TENANT
                and event.metadata["reason"]
                and event.metadata["source_ip"]
            )
            assert "private-row-not-for-audit" not in repr(event) and headers[
                "authorization"
            ] not in repr(event)
        assert not errors, errors
    finally:
        client.close()
