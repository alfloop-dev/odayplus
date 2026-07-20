from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.api.oday_api.security import dependencies as auth_dependencies
from modules.opsboard.auth import SigningKey, encode_compact_jwt

HEADERS = {
    "X-Tenant-Id": "00000000-0000-0000-0000-000000000001",
    "X-Subject-Id": "00000000-0000-0000-0000-000000000101",
    "X-Roles": "expansion_user",
}


def submit(client: TestClient, key: str = "url-idempotency-key-1"):
    return client.post("/api/v1/intakes/url", headers={**HEADERS, "Idempotency-Key": key}, json={
        "original_url": "https://example.test/listing/1",
        "scope": {"tenant_id": HEADERS["X-Tenant-Id"]},
    })


def test_url_submission_replay_and_conflict() -> None:
    client = TestClient(create_app())
    first = submit(client)
    assert first.status_code == 202
    assert first.headers["idempotency-replayed"] == "false"
    replayed = submit(client)
    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    conflict = client.post("/api/v1/intakes/url", headers={**HEADERS, "Idempotency-Key": "url-idempotency-key-1"}, json={
        "original_url": "https://example.test/listing/2", "scope": {"tenant_id": HEADERS["X-Tenant-Id"]}})
    assert conflict.status_code == 409


def test_batch_partial_success_and_cursor_failure() -> None:
    client = TestClient(create_app())
    result = client.post("/api/v1/intake-batches", headers={**HEADERS, "Idempotency-Key": "batch-idempotency-key-1"}, json={
        "batch_id": "00000000-0000-0000-0000-000000000002", "method": "MANUAL",
        "scope": {"tenant_id": HEADERS["X-Tenant-Id"]},
        "rows": [{"address_raw": "Taipei"}, {"address_raw": ""}],
    })
    assert result.status_code == 207
    assert (result.json()["accepted_count"], result.json()["rejected_count"]) == (1, 1)
    assert client.get("/api/v1/intakes?cursor=tampered", headers=HEADERS).status_code == 400


def test_if_match_and_assignment_contract() -> None:
    client = TestClient(create_app())
    intake = submit(client, "url-assignment-key-1").json()
    url = f'/api/v1/intakes/{intake["intake_id"]}/assignment'
    body = {"owner_subject_id": "00000000-0000-0000-0000-000000000003", "owner_role": "reviewer",
            "due_at": "2026-07-19T00:00:00Z", "reason": "triage"}
    manager_headers = {
        **HEADERS,
        "X-Roles": "site_reviewer",
        "X-Operator-Role": "expansion-manager",
    }
    assert client.put(url, headers={**manager_headers, "Idempotency-Key": "assign-idempotency-key-1"}, json=body).status_code == 428
    ok = client.put(url, headers={**manager_headers, "Idempotency-Key": "assign-idempotency-key-1", "If-Match": 'W/"1"'}, json=body)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ASSIGNED"


def _manager_only_listing_merge(
    client: TestClient,
    *,
    headers: dict[str, str],
    key: str,
):
    return client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        headers={
            **headers,
            "X-Operator-Role": "expansion-manager",
            "Idempotency-Key": key,
        },
        json={
            "targetListingId": "L-2025",
            "reason": "Attempt a manager-only listing merge",
            "riskSummary": "Merging listings changes canonical property identity.",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
    )


def test_local_expansion_user_cannot_forge_manager_role() -> None:
    response = _manager_only_listing_merge(
        TestClient(create_app()),
        headers={
            "X-Tenant-Id": "tenant-a",
            "X-Subject-Id": "operator-expansion-staff",
            "X-Roles": "expansion_user",
        },
        key="forged-local-manager-role-1",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "outside principal roles" in response.json()["detail"]


def test_live_expansion_user_cannot_forge_manager_role(monkeypatch) -> None:
    issuer = "https://idp.assisted-intake.test"
    audience = "assisted-intake-api"
    signing_key = SigningKey(
        kid="intake-key",
        algorithm="HS256",
        secret=b"assisted-intake-live-test-secret",
    )
    monkeypatch.setenv("ODP_AUTH_ISSUER", issuer)
    monkeypatch.setenv("ODP_AUTH_AUDIENCES", audience)
    monkeypatch.setenv(
        "ODP_AUTH_HS256_KEYS",
        "intake-key:assisted-intake-live-test-secret",
    )
    auth_dependencies.reset_default_boundary()
    now = datetime.now(UTC)
    token = encode_compact_jwt(
        {
            "sub": HEADERS["X-Subject-Id"],
            "iss": issuer,
            "aud": audience,
            "iat": now.timestamp(),
            "exp": (now + timedelta(hours=1)).timestamp(),
            "tenant_id": "tenant-a",
            "roles": ["expansion_user"],
        },
        signing_key,
    )
    try:
        response = _manager_only_listing_merge(
            TestClient(create_app()),
            headers={
                "Authorization": f"Bearer {token}",
            },
            key="forged-live-manager-role-1",
        )
    finally:
        auth_dependencies.reset_default_boundary()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "outside principal roles" in response.json()["detail"]


@pytest.mark.parametrize(
    ("header_name", "scope_field"),
    [
        ("X-Brand-Ids", "brand_id"),
        ("X-Region-Ids", "region_id"),
        ("X-Assigned-Area-Ids", "assigned_area_id"),
        ("X-Heat-Zone-Ids", "heat_zone_id"),
    ],
)
def test_all_intake_scope_axes_guard_create_read_mutate_and_list(
    header_name: str,
    scope_field: str,
) -> None:
    client = TestClient(create_app())
    allowed_id = str(uuid4())
    denied_id = str(uuid4())
    restricted_headers = {
        **HEADERS,
        "X-Roles": "site_reviewer",
        header_name: allowed_id,
    }
    unrestricted_headers = {
        **HEADERS,
        "X-Roles": "site_reviewer",
    }

    allowed = client.post(
        "/api/v1/intakes/url",
        headers={
            **restricted_headers,
            "Idempotency-Key": f"scope-allowed-{scope_field}",
        },
        json={
            "original_url": f"https://example.test/scope/{scope_field}/allowed",
            "scope": {
                "tenant_id": HEADERS["X-Tenant-Id"],
                scope_field: allowed_id,
            },
        },
    )
    assert allowed.status_code == 202
    allowed_intake_id = allowed.json()["intake_id"]

    denied_create = client.post(
        "/api/v1/intakes/url",
        headers={
            **restricted_headers,
            "Idempotency-Key": f"scope-denied-{scope_field}",
        },
        json={
            "original_url": f"https://example.test/scope/{scope_field}/denied-create",
            "scope": {
                "tenant_id": HEADERS["X-Tenant-Id"],
                scope_field: denied_id,
            },
        },
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["code"] == "SCOPE_DENIED"

    outside = client.post(
        "/api/v1/intakes/url",
        headers={
            **unrestricted_headers,
            "Idempotency-Key": f"scope-outside-{scope_field}",
        },
        json={
            "original_url": f"https://example.test/scope/{scope_field}/outside",
            "scope": {
                "tenant_id": HEADERS["X-Tenant-Id"],
                scope_field: denied_id,
            },
        },
    )
    assert outside.status_code == 202
    outside_intake_id = outside.json()["intake_id"]

    assert client.get(
        f"/api/v1/intakes/{allowed_intake_id}",
        headers=restricted_headers,
    ).status_code == 200
    denied_read = client.get(
        f"/api/v1/intakes/{outside_intake_id}",
        headers=restricted_headers,
    )
    assert denied_read.status_code == 403
    assert denied_read.json()["code"] == "SCOPE_DENIED"

    listed = client.get("/api/v1/intakes", headers=restricted_headers)
    assert listed.status_code == 200
    listed_ids = {item["intake_id"] for item in listed.json()["items"]}
    assert allowed_intake_id in listed_ids
    assert outside_intake_id not in listed_ids

    denied_mutation = client.post(
        f"/api/v1/intakes/{outside_intake_id}/actions/cancel",
        headers={
            **restricted_headers,
            "Idempotency-Key": f"scope-mutation-{scope_field}",
            "If-Match": 'W/"1"',
        },
        json={"reason": "Verify the restricted scope mutation boundary"},
    )
    assert denied_mutation.status_code == 403
    assert denied_mutation.json()["code"] == "SCOPE_DENIED"


def test_tenant_scope_is_fail_closed() -> None:
    assert TestClient(create_app()).get("/api/v1/intakes").status_code == 401
    assert TestClient(create_app()).get(
        "/api/v1/intakes",
        headers={"X-Subject-Id": "00000000-0000-0000-0000-000000000101"},
    ).status_code == 403
