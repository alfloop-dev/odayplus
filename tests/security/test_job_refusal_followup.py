"""Immutable-parent controls for malformed field custody and retry visibility."""

from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from shared.jobs.queue import JobRecord
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import OTHER, TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_job_classification_denial_audit import boundary_for
from tests.security.test_opsboard_jwks_auth import _token


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
@pytest.mark.parametrize("clearance", ["PUBLIC", "CONFIDENTIAL"])
def test_existing_malformed_fields_never_escape_read_or_actual_replay(
    monkeypatch, alias, clearance, job_app
):
    key, bundle, app = job_app
    boundary = boundary_for(key, modules=["listing"], clearance=clearance)
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    fields = [
        "fixture-unclassified-private",
        None,
        ["fixture-unclassified-private"],
        {"field_path": "public", "classification": "PUBLIC", "effective": "public-value"},
    ]
    record = JobRecord(
        job_type="batch-listing-intake",
        payload={
            "tenant_id": TENANT,
            "fields": fields,
            "items": [{"item_id": "one", "fields": fields}],
            "rows": [{"item_id": "two", "fields": fields}],
        },
        correlation_id="offline",
    )
    bundle.job_queue._jobs[record.job_id] = record
    token = "malformed-existing-replay"
    bundle.job_queue._idempotency_index[
        f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
    ] = record.job_id
    before = deepcopy(record.to_dict())
    calls = []
    enqueue = bundle.job_queue.enqueue

    def observed(*args, **kwargs):
        result = enqueue(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(bundle.job_queue, "enqueue", observed)
    with _client(app) as client:
        read = client.get(f"{alias}/{record.job_id}", headers=headers)
        replay = client.post(
            alias,
            headers={**headers, "Idempotency-Key": token},
            json={"job_type": "batch-listing-intake", "payload": {"items": [{"item_id": "one"}]}},
        )
        assert read.status_code == 200 and replay.status_code == 202
        assert calls == [(record, False)] and replay.json()["created"] is False
        for response, view in [(read, read.json()), (replay, replay.json()["job"])]:
            assert "fixture-unclassified-private" not in response.text
            for collection in [
                view["payload"]["fields"],
                view["payload"]["items"][0]["fields"],
                view["payload"]["rows"][0]["fields"],
            ]:
                assert collection[-1]["effective"] == "public-value"
        assert record.to_dict() == before


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
@pytest.mark.parametrize("canonical_value", [True, False, "legacy", "unclassified-alias",
    "legacy-weaker-last", "legacy-stricter-last", "bad-dict", "bad-list", "bad-field-list",
    "bad-legacy-member", "nested-legacy", "nested-canonical", "bad-classification", "bad-path",
    "snapshot-public", "snapshot-internal", "snapshot-confidential", "mixed-container-drop"])
def test_standalone_classified_summary_receipt_replaces_unsupported_values(
    monkeypatch, alias, job_app, canonical_value
):
    key, bundle, app = job_app
    clearance = ("PUBLIC" if canonical_value == "snapshot-public" else
        "INTERNAL" if canonical_value == "snapshot-internal" else "CONFIDENTIAL")
    boundary = boundary_for(key, modules=["listing"], clearance=clearance)
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    private = {
        "classification": "HIGHLY_RESTRICTED",
        "effective": "fixture-inline-private",
        "raw_value": "fixture-inline-private",
    }
    if not canonical_value:
        private.pop("effective")
    if canonical_value == "legacy":
        private = {"parsedFields": {"contactPhone": {"sourceValue": "fixture-inline-private",
            "normalizedValue": "fixture-inline-private", "correctedValue": "fixture-inline-private",
            "extra": "fixture-inline-private"}}}
    if canonical_value == "unclassified-alias":
        private = {"fields": [{"field_path": "unknown", "effective": "fixture-inline-private"}]}
    alias_value = (private["parsedFields"]["contactPhone"] if canonical_value == "legacy" else
        private["fields"][0] if canonical_value == "unclassified-alias" else private)
    secret = "fixture-inline-private"
    if canonical_value in ("legacy-weaker-last", "legacy-stricter-last"):
        alias_value = {"sourceValue": secret, "normalizedValue": secret, "correctedValue": secret}
        entries = [("contactPhone",alias_value),("address",alias_value)]
        if canonical_value == "legacy-stricter-last":
            entries.reverse()
        private = {"parsedFields":dict(entries)}
    if canonical_value == "bad-dict":
        alias_value = {"effective":secret}
        private = {"fields":alias_value}
    if canonical_value == "bad-list":
        alias_value = [secret]
        private = {"parsedFields":alias_value}
    if canonical_value == "bad-field-list":
        alias_value = [secret]
        private = {"fields":[alias_value]}
    if canonical_value == "bad-legacy-member":
        alias_value = [secret]
        private = {"parsedFields":{"contactPhone":alias_value}}
    if canonical_value == "nested-legacy":
        alias_value = {"nested":secret}
        private = {"parsedFields":{"contactPhone":{"sourceValue":alias_value}}}
    if canonical_value == "nested-canonical":
        alias_value = {"nested":secret}
        private = {"fields":[{"effective":alias_value}]}
    if canonical_value == "bad-classification":
        alias_value = {"nested":secret}
        private = {"classification":alias_value,"effective":secret}
    if canonical_value == "bad-path":
        alias_value = {"nested":secret}
        private = {"classification":"HIGHLY_RESTRICTED","field_path":alias_value,"effective":secret}
    if canonical_value in ("snapshot-public", "snapshot-internal", "snapshot-confidential"):
        alias_value = {"nested":secret}
        private = {"rawSnapshot":alias_value,"originalUrl":f"https://example.invalid/{secret}"}
    if canonical_value == "mixed-container-drop":
        alias_value = {"classification":"HIGHLY_RESTRICTED",secret:[secret]}
        private = {"parsedFields":alias_value}
    expect_hidden = canonical_value != "snapshot-confidential"
    public = {"classification": "PUBLIC", "effective": "visible-public"}
    record = JobRecord(
        job_type="batch-listing-intake",
        payload={
            "tenant_id": TENANT,
            "items": [{"item_id": "one"}],
            "summary": private,
            "receipt": {"private": private, "public": public, "alias": alias_value},
        },
        correlation_id="offline",
    )
    bundle.job_queue._jobs[record.job_id] = record
    token = "inline-replay"
    bundle.job_queue._idempotency_index[
        f"batch-listing-intake:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
    ] = record.job_id
    before = deepcopy(record.to_dict())
    seen = []
    enqueue = bundle.job_queue.enqueue

    def observed(*args, **kwargs):
        result = enqueue(*args, **kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(bundle.job_queue, "enqueue", observed)
    with _client(app) as client:
        responses = [
            client.get(f"{alias}/{record.job_id}", headers=headers),
            client.post(
                alias,
                headers={**headers, "Idempotency-Key": token},
                json={
                    "job_type": "batch-listing-intake",
                    "payload": {"items": [{"item_id": "one"}]},
                },
            ),
        ]
    assert [r.status_code for r in responses] == [200, 202]
    assert seen == [(record, False)] and responses[1].json()["created"] is False
    for response in responses:
        assert ("fixture-inline-private" not in response.text) == expect_hidden and "visible-public" in response.text
    assert record.to_dict() == before


@contextmanager
def _client(app):
    client = TestClient(app)  # NO lifespan/worker startup.
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_new_malformed_field_collection_is_refused_without_enqueue(monkeypatch, alias, job_app):
    key, bundle, app = job_app
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary_for(key, modules=["listing"]))
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    before = deepcopy(bundle.job_queue._jobs)
    with _client(app) as client:
        response = client.post(
            alias,
            headers=headers,
            json={
                "job_type": "batch-listing-intake",
                "payload": {"items": [{"item_id": "one", "fields": ["fixture-private"]}]},
            },
        )
    assert response.status_code == 422 and bundle.job_queue._jobs == before


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
@pytest.mark.parametrize("job_type", ["external-fetch", "forecast"])
def test_unsupported_retry_requires_visibility_before_type_response(
    monkeypatch, alias, job_type, job_app, offline_job_app
):
    key, bundle, app = job_app
    audit = offline_job_app[3]
    boundary = None
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    headers = {
        "authorization": "Bearer " + _token(key, "offline-key"),
        "x-correlation-id": "retry-visibility",
    }
    record = JobRecord(job_type=job_type, payload={"tenant_id": OTHER}, correlation_id="offline")
    bundle.job_queue._jobs[record.job_id] = record
    with _client(app) as client:
        for mode in ["foreign", "no-module"]:
            boundary = boundary_for(key, modules=[] if mode == "foreign" else ["unrelated"])
            if mode == "no-module":
                record.payload["tenant_id"] = TENANT
            before = deepcopy(record.to_dict())
            prior = len(audit.list_events())
            response = client.post(
                f"{alias}/{record.job_id}/retries",
                headers=headers,
                json={"retry_scope": "FAILED_ONLY"},
            )
            assert response.status_code in {403, 404} and job_type not in response.text
            assert record.to_dict() == before
            events = [
                e
                for e in audit.list_events()[prior:]
                if e.event_type == "security.authorization" and e.outcome == "deny"
            ]
            assert len(events) == 1 and events[0].actor == "operator-real-1"
        # Legitimate data_owner has applicable module/permission, but this TYPE
        # still cannot retry. Preserve the original unsupported-operation result.
        from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
        from tests.security.test_opsboard_jwks_auth import _jwk

        boundary = AuthenticationBoundary(
            AuthBoundaryConfig(
                issuer="https://idp.example.test",
                audiences=frozenset({"oday-api"}),
                jwks_uri="https://offline.invalid/jwks",
                principal_mappings={
                    "operator-real-1": {
                        "roles": ["data_owner", "operations_manager"],
                        "scope": {"tenant_id": TENANT},
                    }
                },
            ),
            key_resolver=JwksResolver(
                "https://offline.invalid/jwks", fetch=lambda _: {"keys": [_jwk(key, "offline-key")]}
            ),
        )
        assert (
            client.post(f"{alias}/{record.job_id}/retries", headers=headers, json={}).status_code
            == 400
        )
