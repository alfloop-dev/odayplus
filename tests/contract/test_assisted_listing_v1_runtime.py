from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app
from apps.api.oday_api.security import dependencies as auth_dependencies
from modules.opsboard.auth import SigningKey, encode_compact_jwt
from shared.infrastructure.persistence import build_persistence
from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy

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
    client = TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy()))
    first = submit(client)
    assert first.status_code == 202
    assert first.headers["idempotency-replayed"] == "false"
    replayed = submit(client)
    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    conflict = client.post("/api/v1/intakes/url", headers={**HEADERS, "Idempotency-Key": "url-idempotency-key-1"}, json={
        "original_url": "https://example.test/listing/2", "scope": {"tenant_id": HEADERS["X-Tenant-Id"]}})
    assert conflict.status_code == 409


@pytest.fixture(params=["cached", "locked_recheck"])
def url_replay_journey(request, monkeypatch):
    store = AssistedIntakeStore()
    app = create_app(
        persistence=replace(build_persistence(mode="memory"), assisted_intake_store=store),
        assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
    )
    client = TestClient(app)
    region_a = str(uuid4())
    headers = {**HEADERS, "X-Region-Ids": region_a, "Idempotency-Key": "synthetic-url-replay-auth"}
    body = {
        "original_url": "https://example.test/synthetic-replay-authorization",
        "scope": {"tenant_id": HEADERS["X-Tenant-Id"], "region_id": region_a},
    }
    first = client.post("/api/v1/intakes/url", headers=headers, json=body)
    assert first.status_code == 202, first.text
    intake = store.intakes[first.json()["intake_id"]]

    def replay_request():
        if request.param == "cached":
            return client.post("/api/v1/intakes/url", headers=headers, json=body)

        # Model a same-key winner becoming visible only at the locked recheck.
        # Observe both real load_replay reads, not a mock of replay's return.
        reads = []
        inside_transaction = False
        transaction = store.transaction

        class ConcurrentReceipts(dict):
            def get(self, key, default=None):
                reads.append(inside_transaction)
                return None if len(reads) == 1 else super().get(key, default)

        @contextmanager
        def tracked_transaction():
            nonlocal inside_transaction
            with transaction():
                inside_transaction = True
                try:
                    yield
                finally:
                    inside_transaction = False

        with monkeypatch.context() as patch:
            patch.setattr(store, "replays", ConcurrentReceipts(store.replays))
            patch.setattr(store, "transaction", tracked_transaction)
            result = client.post("/api/v1/intakes/url", headers=headers, json=body)
            assert reads == [False, True], "must reach the actual second cached return"
            return result

    return client, store, headers, body, first, intake, replay_request


def _url_replay_state(store):
    return deepcopy({name: getattr(store, name) for name in (
        "intakes", "jobs", "assignments", "slas", "corrections", "decisions",
        "promotions", "saved_views", "workflow_audits", "workflow_outbox", "replays",
    )})


@pytest.mark.parametrize("change, status, code", [
    ("region", 403, "SCOPE_DENIED"),
    ("tenant", 403, "TENANT_SCOPE_DENIED"),
    ("ownership", 403, "OWNERSHIP_REQUIRED"),
    ("missing", 404, "RESOURCE_NOT_FOUND"),
])
def test_url_replay_authorizes_current_target(url_replay_journey, change, status, code):
    client, store, headers, body, first, intake, replay_request = url_replay_journey
    if change == "region":
        intake["scope"]["region_id"] = str(uuid4())
        assert body["scope"]["region_id"] == headers["X-Region-Ids"]
    elif change == "tenant":
        intake["scope"]["tenant_id"] = str(uuid4())
    elif change == "ownership":
        intake["submitted_by"] = intake["assigned_to"] = str(uuid4())
    else:
        del store.intakes[intake["intake_id"]]
    before = _url_replay_state(store)
    detail = client.get(f'/api/v1/intakes/{intake["intake_id"]}', headers=headers)
    assert detail.status_code == status, detail.text
    assert detail.json()["code"] == code
    response = replay_request()
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert response.json()["message"] == detail.json()["message"]
    assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    assert "idempotency-replayed" not in response.headers
    assert _url_replay_state(store) == before
    assert len(store.replays) == 1


@pytest.mark.parametrize("change", ["none", "version", "owner"])
def test_url_replay_preserves_receipt_without_repeating_work(url_replay_journey, change):
    client, store, headers, _, first, intake, replay_request = url_replay_journey
    if change == "version":
        intake["version"] += 1
    elif change == "owner":
        intake["assigned_to"] = str(uuid4())
        assert intake["submitted_by"] == headers["X-Subject-Id"]
    detail = client.get(f'/api/v1/intakes/{intake["intake_id"]}', headers=headers)
    assert detail.status_code == 200, detail.text
    before = _url_replay_state(store)
    response = replay_request()
    assert response.status_code == 200, response.text
    assert response.headers["idempotency-replayed"] == "true"
    assert response.json() == first.json()
    assert response.headers["etag"] == first.headers["etag"]
    assert _url_replay_state(store) == before
    assert len(store.intakes) == len(store.jobs) == len(store.assignments) == len(store.slas) == 1
    assert len(store.replays) == 1


@pytest.mark.parametrize("change", ["body_scope", "role", "payload"])
def test_url_replay_keeps_original_submission_checks(url_replay_journey, change):
    client, store, headers, body, _, _, _ = url_replay_journey
    headers, body = deepcopy(headers), deepcopy(body)
    if change == "body_scope":
        body["scope"]["region_id"] = str(uuid4())
    elif change == "role":
        headers["X-Roles"] = "auditor"
    else:
        body["original_url"] += "/changed"
    before = _url_replay_state(store)
    response = client.post("/api/v1/intakes/url", headers=headers, json=body)
    assert response.status_code == (409 if change == "payload" else 403), response.text
    assert _url_replay_state(store) == before


def test_batch_partial_success_and_cursor_failure() -> None:
    client = TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy()))
    result = client.post("/api/v1/intake-batches", headers={**HEADERS, "Idempotency-Key": "batch-idempotency-key-1"}, json={
        "batch_id": "00000000-0000-0000-0000-000000000002", "method": "MANUAL",
        "scope": {"tenant_id": HEADERS["X-Tenant-Id"]},
        "rows": [{"address_raw": "Taipei"}, {"address_raw": ""}],
    })
    assert result.status_code == 207
    assert (result.json()["accepted_count"], result.json()["rejected_count"]) == (1, 1)
    assert client.get("/api/v1/intakes?cursor=tampered", headers=HEADERS).status_code == 400


def test_if_match_and_assignment_contract() -> None:
    client = TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy()))
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
        TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())),
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
    # Contract §4.4: with a live boundary configured, roles and scope come
    # only from this server-side declaration. Leaving the subject undeclared
    # fails closed at 401 before any RBAC decision is reached, which would
    # make the 403 assertion below unreachable and leave the role-forging
    # path untested (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
    monkeypatch.setenv(
        "ODP_AUTH_PRINCIPAL_MAP",
        json.dumps(
            {
                HEADERS["X-Subject-Id"]: {
                    "roles": ["expansion_user"],
                    "scope": {"tenant_id": "tenant-a"},
                }
            }
        ),
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
            # Forged authorization claims. The declaration above grants only
            # expansion_user (-> operator role "expansion-staff"), whereas
            # site_reviewer would grant "expansion-manager" and let the merge
            # through. Honouring either claim turns the 403 below into a 2xx.
            "tenant_id": "tenant-b",
            "roles": ["site_reviewer"],
        },
        signing_key,
    )
    try:
        response = _manager_only_listing_merge(
            TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())),
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
    client = TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy()))
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
    assert TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())).get("/api/v1/intakes").status_code == 401
    assert TestClient(create_app(assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy())).get(
        "/api/v1/intakes",
        headers={"X-Subject-Id": "00000000-0000-0000-0000-000000000101"},
    ).status_code == 403
