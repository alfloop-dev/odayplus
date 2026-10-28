"""The normal app must not expose or accept private search delivery records."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.api.idempotency import request_fingerprint
from shared.infrastructure.persistence.listing_search_matches import (
    ListingSearchMatchStore,
    evidence_digest,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobStatus
from tests.intake_registry_fixture import MANAGER, OWNER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import _headers, bridge_database
from tests.support.listing_search_integration import (
    execute_claimed_listing_search,
    listing_search_events,
)
from tests.unit.test_saved_listing_search_criteria import receipt

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


@pytest.mark.parametrize("method", ["MANUAL", "CSV"])
def test_pre_currency_match_and_pending_receipt_survive_upgrade_without_rewriting_history(
    bridge_database, monkeypatch, method
):
    zone, intake_id = str(uuid4()), str(uuid4())
    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        created = client.post(
            BASE, json=body(zone), headers=_headers(key="synthetic-upgrade-search-0001")
        )
        assert created.status_code == 201, created.text
        identity = created.json()["search"]["saved_view_id"]
        record = receipt(
            intake_id=intake_id, version=1, state="NEEDS_REVIEW", intake_method=method,
            submitted_by=MANAGER, scope={"tenant_id": TENANT, "heat_zone_id": zone},
        )
        record["fields"] = [f for f in record["fields"] if f["field_path"] != "currency"]
        for field in record["fields"]:
            field["classification"] = "INTERNAL"
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        store.intakes[intake_id] = deepcopy(record)
        store.flush()
        original_events = listing_search_events(bundle, TENANT)
        search_event = next(e for e in original_events if e["kind"] == "search")
        receipt_event = next(e for e in original_events if e["kind"] == "receipt")
        assert len(original_events) == 2
        assert not any(f["field_path"] == "currency" for f in receipt_event["facts"]["fields"])

        # Golden persisted output of the pre-currency matcher (68f2dd6e4044):
        # the completed S1 job saw both commits and recorded MATCH for R1.
        # This is an initial historical DB fixture, not a rewrite of an event
        # or a patched current matcher. R1's server-reserved job stays pending.
        match_id = sha256(json.dumps([identity, 1, intake_id, 1], separators=(",", ":")).encode()).hexdigest()
        legacy = {
            "id": match_id,
            "tenantId": TENANT,
            "searchId": identity,
            "searchVersion": 1,
            "ownerSubjectId": MANAGER,
            "criteriaSha256": sha256(json.dumps(
                search_event["facts"]["query"], sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode()).hexdigest(),
            "intakeId": intake_id,
            "intakeVersion": 1,
            "receiptFactsSha256": evidence_digest(receipt_event["facts"]),
            "outcome": "MATCH",
            "reasonCodes": [],
            "recordedAt": datetime.now(UTC).isoformat(),
        }
        documents = TenantScopedDocumentStore(store._store, TENANT)
        ledger = ListingSearchMatchStore(documents)
        with bundle.engine.transaction():
            assert documents.put_if_absent(
                ledger.COLLECTION, match_id, legacy, group_key=f"{identity}:1"
            )
        for _ in range(10):
            job = bundle.job_queue.claim_next(worker_id="synthetic-pre-upgrade-search")
            assert job is not None
            if job.job_id == search_event["job_id"]:
                bundle.job_queue.update_status(
                    job.job_id, JobStatus.SUCCEEDED,
                    expected_version=job.version, fence_token=job.fence_token,
                )
                break
            assert job.job_type == "durable-audit-delivery", job.job_type
        else:
            pytest.fail("The pre-upgrade search job was not claimable")
        assert bundle.job_queue.get(receipt_event["job_id"]).status == JobStatus.QUEUED
        assert ledger.history(search_id=identity, search_version=1) == [legacy]
    finally:
        bundle.engine.close()

    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        store = bundle.assisted_intake_store
        ledger = ListingSearchMatchStore(TenantScopedDocumentStore(store._store, TENANT))
        path = BASE + "/" + identity + "/results"
        # Historical MATCH is not a current currency grant, even before replay.
        result = client.get(path, headers=_headers())
        assert result.status_code == 200 and result.json()["total"] == 0
        pending = bundle.job_queue.claim_next(worker_id="synthetic-post-upgrade-receipt")
        assert pending is not None and pending.job_id == receipt_event["job_id"]
        # Actual registered default handler, original bound event, real PG fence.
        # Before the repair this raises LISTING_SEARCH_MATCH_VERSION_REUSED.
        assert execute_claimed_listing_search(bundle, pending)
        assert bundle.job_queue.get(pending.job_id).status == JobStatus.SUCCEEDED
        assert ledger.history(search_id=identity, search_version=1) == [legacy]
        assert listing_search_events(bundle, TENANT) == original_events
        store.refresh(TENANT)
        assert store.intakes[intake_id] == record
        result = client.get(path, headers=_headers())
        assert result.status_code == 200 and result.json()["items"] == []
        assert client.get(path, headers=_headers(OWNER)).status_code == 404

        # A genuinely new explicit-currency revision can match normally; the
        # immutable old receipt/event/ledger must not be backfilled to get it.
        store.refresh(TENANT)
        current = store.intakes[intake_id]
        current["version"] = 2
        current["fields"].append({
            "field_path": "currency", "effective": "TWD", "masked": False,
            "classification": "INTERNAL",
        })
        store.flush()
        while job := bundle.job_queue.claim_next(worker_id="synthetic-post-upgrade-twd"):
            assert execute_claimed_listing_search(bundle, job)
        result = client.get(path, headers=_headers())
        assert result.status_code == 200 and result.json()["total"] == 1
        assert result.json()["items"][0]["intakeVersion"] == 2
        assert legacy in ledger.history(search_id=identity, search_version=1)
        assert all(event in listing_search_events(bundle, TENANT) for event in original_events)
    finally:
        bundle.engine.close()


def test_currency_revision_delivery_and_current_masking_through_normal_http_app(
    bridge_database, monkeypatch
):
    zone, intake_id = str(uuid4()), str(uuid4())
    bundle, client = setup_case(bridge_database, monkeypatch)
    try:
        rent_body = body(zone)
        no_rent_body = deepcopy(rent_body)
        no_rent_body["query"].pop("maxRentPerMonth")
        identities = []
        for index, data in enumerate([rent_body, no_rent_body]):
            response = client.post(
                BASE, json=data, headers=_headers(key=f"synthetic-currency-search-{index:04d}")
            )
            assert response.status_code == 201, response.text
            identities.append(response.json()["search"]["saved_view_id"])
        record = receipt(
            intake_id=intake_id, version=1, state="NEEDS_REVIEW", intake_method="MANUAL",
            submitted_by=MANAGER, scope={"tenant_id": TENANT, "heat_zone_id": zone},
        )
        for field in record["fields"]:
            field["classification"] = "INTERNAL"
        # Only currency exceeds the low-clearance reader's grant.
        next(f for f in record["fields"] if f["field_path"] == "currency")[
            "classification"
        ] = "RESTRICTED"
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        store.intakes[intake_id] = record
        store.flush()
    finally:
        bundle.engine.close()

    bundle, client = setup_case(bridge_database, monkeypatch)
    reader_headers = {**_headers(), "x-clearance": "RESTRICTED"}
    try:
        for version, change in enumerate(["TWD", "USD", "missing", "masked", "conflict", "TWD"], 1):
            if version > 1:
                store = bundle.assisted_intake_store
                store.refresh(TENANT)
                record = store.intakes[intake_id]
                record["version"] = version
                record["fields"] = [f for f in record["fields"] if f["field_path"] != "currency"]
                if change != "missing":
                    currency = {
                        "field_path": "currency", "effective": "USD" if change == "USD" else "TWD",
                        "masked": change == "masked", "classification": "RESTRICTED",
                    }
                    record["fields"].append(currency)
                    if change == "conflict":
                        record["fields"].append({**currency, "effective": "USD"})
                store.flush()
                # No old match can stand in for an undelivered current revision.
                for identity in identities:
                    response = client.get(BASE + "/" + identity + "/results", headers=reader_headers)
                    assert response.status_code == 200 and response.json()["total"] == 0
            while job := bundle.job_queue.claim_next(worker_id="synthetic-currency-http"):
                if not execute_claimed_listing_search(bundle, job):
                    # Like the existing bridge fixture, claim unrelated audit
                    # intents without executing an external WORM delivery.
                    assert job.job_type == "durable-audit-delivery", job.job_type
            for index, identity in enumerate(identities):
                response = client.get(BASE + "/" + identity + "/results", headers=reader_headers)
                assert response.status_code == 200, response.text
                expected = int(index == 1 or change == "TWD")
                assert response.json()["total"] == expected
                if expected:
                    assert response.json()["items"][0]["intakeVersion"] == version
                masked = client.get(
                    BASE + "/" + identity + "/results",
                    headers=_headers(),
                )
                assert masked.status_code == 200 and masked.json()["total"] == int(index == 1)
                other = client.get(
                    BASE + "/" + identity + "/results", headers=_headers(OWNER)
                )
                assert other.status_code == 404
            if version == 1:
                original_events = listing_search_events(bundle, TENANT)
        events = listing_search_events(bundle, TENANT)
        assert all(event in events for event in original_events)
        # A currency-only current-row drift without a new immutable event is
        # hidden by the reader's shared facts hash, even for a no-rent query.
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        record = deepcopy(store.intakes[intake_id])
        next(f for f in record["fields"] if f["field_path"] == "currency")["effective"] = "USD"
        store._store.put(
            "operator.assisted_intakes", f"{TENANT}:{intake_id}",
            store._wrap(TENANT, intake_id, record),
        )
        for identity in identities:
            response = client.get(BASE + "/" + identity + "/results", headers=reader_headers)
            assert response.status_code == 200 and response.json()["total"] == 0
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
