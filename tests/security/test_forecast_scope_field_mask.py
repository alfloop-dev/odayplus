"""Forecast object scope and canonical field classification; offline fixture only."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies as deps
from modules.listing.application.intake_authorization import mask_intake
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary, JwksResolver
from shared.auth import DataClassification, Principal, Scope
from shared.jobs.queue import JobRecord
from tests.security._offline_job_app import job_app as job_app
from tests.security._offline_job_app import offline_job_app as offline_job_app
from tests.security.test_auth_composition_extension import TENANT
from tests.security.test_auth_composition_extension import isolation as isolation
from tests.security.test_opsboard_jwks_auth import _jwk, _token


@pytest.mark.parametrize("alias", ["/jobs", "/api/v1/jobs"])
def test_forecast_read_enqueue_replay_require_complete_resource_scope(monkeypatch, alias, job_app):
    key, bundle, app = job_app
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            issuer="https://idp.example.test",
            audiences=frozenset({"oday-api"}),
            jwks_uri="https://offline.invalid/jwks",
            principal_mappings={
                "operator-real-1": {
                    "roles": ["operations_manager"],
                    "scope": {
                        "tenant_id": TENANT,
                        "store_ids": ["store-A"],
                        "brand_ids": ["brand-A"],
                        "region_ids": ["region-A"],
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
    good = {"store_id": "store-A", "brand_id": "brand-A", "region_id": "region-A"}
    cases = [
        ("healthy", good, True),
        ("healthy-nested", {"scope": good}, True),
        ("foreign-store", {**good, "store_id": "store-B"}, False),
        ("missing-store", {k: v for k, v in good.items() if k != "store_id"}, False),
        ("foreign-brand", {**good, "brand_id": "brand-B"}, False),
        ("conflict", {**good, "scope": {"storeId": "store-B"}}, False),
    ]
    errors = []
    client = TestClient(app)
    try:
        for name, scope, allowed in cases:
            record = JobRecord(
                job_type="forecast",
                payload={
                    "tenant_id": TENANT,
                    **scope,
                    "summary": {"private": "offline-forecast-marker"},
                },
                correlation_id="offline",
            )
            bundle.job_queue._jobs[record.job_id] = record
            response = client.get(f"{alias}/{record.job_id}", headers=headers)
            if allowed:
                assert response.status_code == 200, (name, response.status_code)
            elif (
                response.status_code not in {403, 422} or "offline-forecast-marker" in response.text
            ):
                errors.append(f"{name} read {response.status_code}")
            before = deepcopy(bundle.job_queue._jobs)
            enqueue = client.post(
                alias, headers=headers, json={"job_type": "forecast", "payload": scope}
            )
            if allowed:
                assert enqueue.status_code == 202, (name, enqueue.status_code)
            elif enqueue.status_code not in {403, 422} or bundle.job_queue._jobs != before:
                errors.append(f"{name} enqueue {enqueue.status_code}/changed queue")
        old = JobRecord(
            job_type="forecast",
            payload={"tenant_id": TENANT, **good, "store_id": "store-B"},
            correlation_id="offline",
        )
        bundle.job_queue._jobs[old.job_id] = old
        token = "offline-forecast-replay"
        bundle.job_queue._idempotency_index[
            f"forecast:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
        ] = old.job_id
        before = deepcopy(bundle.job_queue._jobs)
        replay = client.post(
            alias,
            headers={**headers, "Idempotency-Key": token},
            json={"job_type": "forecast", "payload": good},
        )
        if replay.status_code != 403 or bundle.job_queue._jobs != before:
            errors.append(f"foreign actual replay {replay.status_code}")
        for modules in (["listing"], ["forecastops"], []):
            allowed = not modules or "forecastops" in modules
            boundary = AuthenticationBoundary(
                AuthBoundaryConfig(
                    issuer="https://idp.example.test",
                    audiences=frozenset({"oday-api"}),
                    jwks_uri="https://offline.invalid/jwks",
                    principal_mappings={
                        "operator-real-1": {
                            "roles": ["operations_manager"],
                            "scope": {
                                "tenant_id": TENANT,
                                "store_ids": ["store-A"],
                                "brand_ids": ["brand-A"],
                                "region_ids": ["region-A"],
                                "modules": modules,
                            },
                        }
                    },
                ),
                key_resolver=JwksResolver(
                    "https://offline.invalid/jwks",
                    fetch=lambda _: {"keys": [_jwk(key, "offline-key")]},
                ),
            )
            record = JobRecord(
                job_type="forecast",
                payload={"tenant_id": TENANT, **good},
                correlation_id="offline-module",
            )
            bundle.job_queue._jobs[record.job_id] = record
            response = client.get(f"{alias}/{record.job_id}", headers=headers)
            if response.status_code != (200 if allowed else 403):
                errors.append(f"module {modules} read={response.status_code}")
            before = deepcopy(bundle.job_queue._jobs)
            created = client.post(
                alias, headers=headers, json={"job_type": "forecast", "payload": good}
            )
            if allowed:
                assert created.status_code == 202
            elif created.status_code != 403 or bundle.job_queue._jobs != before:
                errors.append(f"module {modules} enqueue={created.status_code}")
            token = f"module-replay-{len(modules)}-{'-'.join(modules)}"
            bundle.job_queue._idempotency_index[
                f"forecast:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
            ] = record.job_id
            replay = client.post(
                alias,
                headers={**headers, "Idempotency-Key": token},
                json={"job_type": "forecast", "payload": good},
            )
            if allowed:
                assert (
                    replay.status_code == 202
                    and replay.json()["created"] is False
                    and replay.json()["job_id"] == record.job_id
                )
            elif replay.status_code != 403 or bundle.job_queue._jobs != before:
                errors.append(f"module {modules} replay={replay.status_code}")
    finally:
        client.close()
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("classification", ["HIGHLY_RESTRICTED", "unknown-new-classification"])
def test_restricted_intake_canonical_field_mask_never_skips_stricter_values(classification):
    raw = {
        "fields": [
            {
                "field_path": "private",
                "classification": classification,
                **{
                    k: "offline-private" for k in ("parsed", "normalized", "corrected", "effective")
                },
            },
            {"field_path": "public", "classification": "PUBLIC", "effective": "permitted"},
        ]
    }
    before = deepcopy(raw)
    result = mask_intake(
        Principal("offline", scope=Scope(clearance=DataClassification.RESTRICTED)), raw
    )
    assert all(
        result["fields"][0][k] is None for k in ("parsed", "normalized", "corrected", "effective")
    )
    assert (
        result["fields"][0]["masked"] and result["fields"][0]["mask_reason_code"] == "FIELD_MASKED"
    )
    assert result["fields"][1]["effective"] == "permitted" and raw == before
