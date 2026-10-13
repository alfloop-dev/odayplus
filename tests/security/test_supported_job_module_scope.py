"""Verified mapped module exclusions, both actual aliases, no worker/lifespan."""

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
@pytest.mark.parametrize(
    "kind", ["batch-listing-intake", "assisted-listing-intake", "external-fetch"]
)
def test_each_supported_job_module_precedes_read_enqueue_replay_retry(
    monkeypatch, alias, kind, job_app
):
    key, bundle, app = job_app
    errors = []
    required = "integration" if kind == "external-fetch" else "listing"
    roles = ["data_owner"] if kind == "external-fetch" else ["site_reviewer", "operator_viewer"]
    headers = {"authorization": "Bearer " + _token(key, "offline-key")}
    boundary = None
    monkeypatch.setattr(deps, "default_boundary", lambda: boundary)
    client = TestClient(app)  # Deliberately no lifespan/worker startup.
    try:
        for modules in (["forecastops"], [required], []):
            allowed = not modules or required in modules
            boundary = AuthenticationBoundary(
                AuthBoundaryConfig(
                    issuer="https://idp.example.test",
                    audiences=frozenset({"oday-api"}),
                    jwks_uri="https://offline.invalid/jwks",
                    principal_mappings={
                        "operator-real-1": {
                            "roles": roles,
                            "scope": {"tenant_id": TENANT, "modules": modules},
                        }
                    },
                ),
                key_resolver=JwksResolver(
                    "https://offline.invalid/jwks",
                    fetch=lambda _: {"keys": [_jwk(key, "offline-key")]},
                ),
            )
            payload = (
                {"tenant_id": TENANT, "provider_id": "offline-source"}
                if kind == "external-fetch"
                else {
                    "tenant_id": TENANT,
                    "items": [{"item_id": "row-001", "address_raw": "offline-address"}],
                    "receipt": {
                        "items": [
                            {
                                "item_id": "row-001",
                                "item_status": "FAILED",
                                "error": {"retryable": True},
                            }
                        ]
                    },
                }
            )
            record = JobRecord(
                job_type=kind,
                status=JobStatus.PARTIAL,
                payload=deepcopy(payload),
                correlation_id="offline-module",
            )
            bundle.job_queue._jobs[record.job_id] = record
            before = deepcopy(bundle.job_queue._jobs)
            read = client.get(f"{alias}/{record.job_id}", headers=headers)
            if read.status_code != (200 if allowed else 403):
                errors.append(f"{modules} read={read.status_code}")
            # Assisted intake is NEVER generic-create, including healthy module.
            if kind != "assisted-listing-intake":
                created = client.post(
                    alias, headers=headers, json={"job_type": kind, "payload": payload}
                )
                if allowed:
                    assert created.status_code == 202, created.status_code
                elif created.status_code != 403 or bundle.job_queue._jobs != before:
                    errors.append(f"{modules} create={created.status_code}/mutated")
                token = f"module-{'-'.join(modules)}"
                bundle.job_queue._idempotency_index[
                    f"{kind}:v2:{len(TENANT)}:{TENANT}:{len(token)}:{token}"
                ] = record.job_id
                replay = client.post(
                    alias,
                    headers={**headers, "Idempotency-Key": token},
                    json={"job_type": kind, "payload": payload},
                )
                if allowed:
                    assert (
                        replay.status_code == 202
                        and replay.json()["created"] is False
                        and replay.json()["job_id"] == record.job_id
                    )
                elif replay.status_code != 403 or bundle.job_queue._jobs != before:
                    errors.append(f"{modules} replay={replay.status_code}/mutated")
            else:
                created = client.post(
                    alias, headers=headers, json={"job_type": kind, "payload": payload}
                )
                assert created.status_code == 403 and bundle.job_queue._jobs == before
            if kind != "external-fetch":
                snapshot = deepcopy(record.to_dict())
                retry = client.post(
                    f"{alias}/{record.job_id}/retries",
                    headers=headers,
                    json={"retry_scope": "FAILED_ONLY"},
                )
                after = bundle.job_queue.get(record.job_id)
                if allowed:
                    assert retry.status_code == 202 and after.status == JobStatus.QUEUED, (
                        retry.status_code,
                        retry.json(),
                    )
                    assert (
                        after.version == snapshot["version"] + 1
                        and after.payload["_retry_scope"] == "FAILED_ONLY"
                    )
                elif retry.status_code != 403 or after.to_dict() != snapshot:
                    errors.append(f"{modules} retry={retry.status_code}/mutated")
    finally:
        client.close()
    assert not errors, errors
