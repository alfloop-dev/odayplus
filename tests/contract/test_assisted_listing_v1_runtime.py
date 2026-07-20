from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app

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
    manager_headers = {**HEADERS, "X-Operator-Role": "expansion-manager"}
    assert client.put(url, headers={**manager_headers, "Idempotency-Key": "assign-idempotency-key-1"}, json=body).status_code == 428
    ok = client.put(url, headers={**manager_headers, "Idempotency-Key": "assign-idempotency-key-1", "If-Match": 'W/"1"'}, json=body)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ASSIGNED"


def test_tenant_scope_is_fail_closed() -> None:
    assert TestClient(create_app()).get("/api/v1/intakes").status_code == 401
    assert TestClient(create_app()).get(
        "/api/v1/intakes",
        headers={"X-Subject-Id": "00000000-0000-0000-0000-000000000101"},
    ).status_code == 403
