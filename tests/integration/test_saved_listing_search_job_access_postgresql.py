"""The normal app must not expose or accept private search delivery records."""

from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.api.idempotency import request_fingerprint
from tests.intake_registry_fixture import MANAGER, OWNER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import _headers, bridge_database

pytestmark = pytest.mark.requires_live_env
assert bridge_database
BASE = "/api/v1/operator/network-listings/searches"


def setup_case(database, monkeypatch):
    bundle = _bundle(database, monkeypatch)
    app = create_app(persistence=bundle)
    return bundle, TestClient(app)


def body(zone):
    return {
        "name": "Synthetic private search secret",
        "query": {
            "kind": "listing-search.v1",
            "heatZoneId": zone,
            "minAreaPing": 14,
            "maxAreaPing": 26,
            "maxRentPerMonth": 60000,
            "groundFloorOnly": True,
        },
    }


def test_private_command_and_match_jobs_have_no_public_job_read_or_retry_surface(
    bridge_database, monkeypatch
):
    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        created = client.post(
            BASE, json=body(str(uuid4())), headers=_headers(key="synthetic-private-create-0001")
        )
        assert created.status_code == 201, created.text
        record = created.json()
        receipt = bundle.job_queue.get(record["receiptId"])
        assert receipt.payload["response"]["search"]["name"] == "Synthetic private search secret"
        matches = bundle.engine.query(
            "SELECT job_id FROM durable_jobs WHERE job_type = ?", ("listing-search-receipt-match",)
        )
        assert matches
        for identity in [record["receiptId"], *(x["job_id"] for x in matches)]:
            for prefix in ("/jobs", "/api/v1/jobs"):
                for headers in (
                    _headers(),
                    _headers(OWNER),
                    {
                        **_headers(OWNER),
                        "x-roles": "operations_manager",
                        "x-operator-role": "ops-lead",
                    },
                    _headers(tenant=str(uuid4())),
                ):
                    response = client.get(prefix + "/" + identity, headers=headers)
                    assert response.status_code == 404, response.text
                    assert "Synthetic private search secret" not in response.text
                    retry = client.post(prefix + "/" + identity + "/retries", headers=headers)
                    assert retry.status_code == 404, retry.text
                assert client.get(prefix + "/" + identity).status_code == 401
        assert (
            client.get(
                BASE + "/" + record["search"]["saved_view_id"], headers=_headers()
            ).status_code
            == 200
        )
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("prefix", ["/jobs", "/api/v1/jobs"])
def test_public_enqueue_cannot_forge_private_command_replay_or_reserve_its_key(
    bridge_database, monkeypatch, prefix
):
    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        zone = str(uuid4())
        data = body(zone)
        key = "synthetic-forgery-control-0001"
        scope = "create:create:" + sha256(MANAGER.encode()).hexdigest()
        payload = {"operation": "create", "identity": None, "input": data, "actor": MANAGER}
        reserved = f"command:v1:listing-search:{TENANT}:{scope}:{key}"
        forged = {
            "tenant_id": TENANT,
            "receipt_service": "listing-search",
            "command_scope": scope,
            "request_fingerprint": request_fingerprint(payload),
            "response": {
                "search": {
                    "saved_view_id": str(uuid4()),
                    "name": "Forged success without persistence",
                },
                "auditEventIds": [str(uuid4())],
            },
        }
        attack = client.post(
            prefix,
            json={
                "job_type": "listing-search.command-receipt",
                "payload": forged,
                "idempotency_key": reserved,
            },
            headers=_headers(),
        )
        assert attack.status_code == 400, attack.text
        # Changing the job type cannot squat the internal command key either.
        for key_location in ("body", "header"):
            message = {
                "job_type": "forecast",
                "payload": {"tenant_id": TENANT, "store_id": str(uuid4())},
            }
            headers = _headers(
                key=reserved if key_location == "header" else "synthetic-ordinary-queue-key"
            )
            if key_location == "body":
                message["idempotency_key"] = reserved
            attack = client.post(prefix, json=message, headers=headers)
            assert attack.status_code == 422, attack.text
        assert bundle.job_queue.get_by_idempotency_key(reserved) is None
        assert (
            client.post(
                prefix,
                json={"job_type": "listing-search-receipt-match", "payload": forged},
                headers=_headers(),
            ).status_code
            == 400
        )
        created = client.post(BASE, json=data, headers=_headers(key=key))
        assert created.status_code == 201, created.text
        actual = created.json()
        assert actual["search"]["name"] == data["name"] and actual["auditEventIds"]
        assert not actual["replayed"]
        assert (
            client.get(
                BASE + "/" + actual["search"]["saved_view_id"], headers=_headers()
            ).status_code
            == 200
        )
        replay = client.post(BASE, json=data, headers=_headers(key=key))
        assert replay.status_code == 201 and replay.json()["replayed"]
        assert replay.json()["receiptId"] == actual["receiptId"]
    finally:
        bundle.engine.close()


def test_native_saved_views_cannot_bypass_current_zone_scope_or_tombstone(
    bridge_database, monkeypatch
):
    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        zone = str(uuid4())
        created = client.post(
            BASE, json=body(zone), headers=_headers(key="synthetic-private-native-0001")
        )
        assert created.status_code == 201, created.text
        identity = created.json()["search"]["saved_view_id"]
        native = client.get("/api/v1/saved-views", headers=_headers())
        assert native.status_code == 200 and [x["saved_view_id"] for x in native.json()] == [
            identity
        ]
        restricted = {**_headers(), "x-heat-zone-ids": str(uuid4())}
        assert client.get(BASE + "/" + identity, headers=restricted).status_code == 403
        native = client.get("/api/v1/saved-views", headers=restricted)
        assert native.status_code == 200 and native.json() == []
        assert client.get("/api/v1/saved-views", headers=_headers(OWNER)).json() == []
        deleted = client.request(
            "DELETE",
            BASE + "/" + identity,
            json={"expectedVersion": 1},
            headers=_headers(key="synthetic-private-native-delete-0001"),
        )
        assert deleted.status_code == 200, deleted.text
        assert client.get("/api/v1/saved-views", headers=_headers()).json() == []
        assert (
            client.get(
                "/api/v1/jobs/" + created.json()["receiptId"], headers=_headers()
            ).status_code
            == 404
        )
    finally:
        bundle.engine.close()
