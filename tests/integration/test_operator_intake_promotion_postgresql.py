"""Canonical promotion with isolated SQL and explicitly synthetic measured evidence."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.listing.domain.models import ListingDedupKey
from shared.auth import Role
from shared.domain.models import AddressLocation, Listing
from tests.intake_registry_fixture import APPROVER, MANAGER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import (
    BASE,
    _client,
    _headers,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


@pytest.fixture(autouse=True)
def synthetic_score_signing_key(monkeypatch):
    monkeypatch.setenv("ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY", "synthetic-local-score-delivery-test-key-20261013")
    monkeypatch.setenv("ODP_INTAKE_SCORE_RETRY_POLICY_JSON", json.dumps({
        "policy_id": "synthetic-local-score-retry-v1", "version": 1,
        "max_attempts": 5, "backoff_seconds": [10, 30, 120, 600],
    }))


def _promotion_client(bundle):
    client = _client(bundle)
    client.app.state.persistence_bundle = bundle
    client.app.state.operator_intake_repository = bundle.operator_intake_repository
    return client


def _reviewer_headers(key="synthetic-promotion-review"):
    return {**_headers(APPROVER, key=key), "x-roles": "site_reviewer,expansion_user",
            "x-operator-role": "expansion-manager"}


def _ready_intake(db, bundle, client):
    with db.connect() as conn:
        for role in (Role.SITE_REVIEWER, Role.EXPANSION_USER):
            conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,%s)",
                         (APPROVER, role.value, "isolated-synthetic-test"))
    listing_id = str(uuid4())
    listing = Listing(listing_id=listing_id, source_listing_id="synthetic-measured-provider-record",
                      source_id="synthetic-measured-source", address_id=str(uuid4()),
                      listing_status="watching", rent_amount=54000, area_ping=22,
                      floor="1F", frontage_m=5, confidence=0.9)
    # Explicit fixture bindings, never inferred from the intake URL or display
    # names. The complete property reconciliation workflow is outside this test.
    object.__setattr__(listing, "property_id", str(uuid4()))
    object.__setattr__(listing, "listing_obs_id", str(uuid4()))
    address = AddressLocation(address_id=listing.address_id,
                              raw_address="Synthetic measured address", normalized_address="Synthetic measured address",
                              geocode_confidence=0.95, h3_res_9="8929a1d4d67ffff")
    bundle.listing_repository_for_tenant(TENANT).save_listing(
        listing, address, ListingDedupKey(source_id=listing.source_id,
                                         source_listing_id=listing.source_listing_id,
                                         normalized_address=address.normalized_address,
                                         rent_amount=listing.rent_amount, area_ping=listing.area_ping))
    submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/synthetic-promotion",
                                                       "heatZoneId": "synthetic-permitted-zone"},
                            headers=_headers())
    assert submitted.status_code == 200, submitted.text
    initial = submitted.json()
    iid = initial["id"]
    corrected = client.post(f"{BASE}/intake/{iid}/correct",
                            json={"fields": {"floor": "1F"}, "reason": "Synthetic measured revision",
                                  "riskSummary": "Synthetic non-identity correction", "riskAcknowledged": True},
                            headers=_headers(key="synthetic-promotion-correct"))
    assert corrected.status_code == 200, corrected.text
    decided = client.post(f"{BASE}/intake/{iid}/decide",
                          json={"action": "revise", "targetListingId": listing_id,
                                "reason": "Revise the synthetic measured listing",
                                "riskSummary": "Synthetic local acceptance", "riskAcknowledged": True},
                          headers=_reviewer_headers("synthetic-promotion-decide"))
    assert decided.status_code == 200, decided.text
    record = decided.json()
    assert record["matchResult"]["targetListingId"] == listing_id
    assert record["version"] == 3 and record["stage"] == "READY"
    assert record["submittedBy"] == MANAGER
    assert record["assignmentId"] == initial["assignmentId"]
    assert record["slaInstanceId"] == initial["slaInstanceId"]
    return record, listing_id


def _ready_admitted_intake(db, bundle, client):
    """Explicit synthetic SQL admission for this promotion command suite only."""
    from tests.integration.test_intake_score_delivery_postgresql import _seed_manual_admission

    intake, listing_id = _ready_intake(db, bundle, client)
    repo = bundle.listing_repository_for_tenant(TENANT)
    listing = repo.get_listing(listing_id)
    object.__setattr__(listing, "snapshot_id", str(uuid4()))
    object.__setattr__(listing, "feature_snapshot_time", datetime(2026, 10, 9, 8, 0, tzinfo=UTC))
    address = repo.get_address(listing.address_id)
    repo.save_listing(listing, address, ListingDedupKey(
        source_id=listing.source_id, source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount, area_ping=listing.area_ping,
    ))
    _seed_manual_admission(db, bundle, intake, listing)
    return intake, listing_id


def _request(client, record, *, key="synthetic-promotion-request", actor=MANAGER, extra_headers=None):
    return client.post(f"/api/v1/intakes/{record['id']}/promotion-requests",
                       json={"target_format_code": "FORMAT-B", "reason": "Synthetic independent review",
                             "gate_snapshot_sha256": "a" * 64, "risk_acknowledged": True},
                       headers={**_headers(actor, key=key), "If-Match": f'W/"{record["version"]}"',
                                **(extra_headers or {})})


def _review(client, decision, *, key="synthetic-promotion-review", fault=False, extra_headers=None):
    return client.post(f"/api/v1/promotion-decisions/{decision['promotion_decision_id']}/actions/review",
                       json={"decision": "APPROVE", "reason": "Independent synthetic review",
                             "risk_acknowledged": True},
                       headers={**_reviewer_headers(key), "If-Match": f'W/"{decision["version"]}"',
                                **(extra_headers or {}),
                                **({"X-ODP-Test-Fault": "score-failure"} if fault else {})})


def _snapshot(bundle):
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    repo = bundle.listing_repository_for_tenant(TENANT)
    return deepcopy({"canonical": {name: getattr(store, name) for name in (
        "intakes", "assignments", "slas", "promotions", "jobs", "replays", "workflow_audits", "workflow_outbox")},
        "listings": repo.list_listings(), "candidates": repo.list_candidates(),
        "events": bundle.outbox_repository.get_unpublished_events(),
        "audit_rows": bundle.engine.query("SELECT audit_event_id,event_sha256,result FROM audit.audit_events ORDER BY audit_event_id"),
        "delivery_jobs": bundle.engine.query("SELECT job_id,status,version FROM durable_jobs ORDER BY job_id"),
        "score_jobs": bundle.engine.query("SELECT job_id,status,version,payload FROM workflow.jobs WHERE job_type='intake-sitescore' ORDER BY job_id")})


def test_canonical_promotion_positive_restart_and_replay(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, listing_id = _ready_admitted_intake(bridge_database, bundle, client)
        requested = _request(client, record)
        assert requested.status_code == 202, requested.text
        decision = requested.json()
        assert decision["intake_id"] == record["id"]
        assert decision["listing_id"] == listing_id
        assert decision["proposer_subject_id"] == MANAGER
        review_correlation = str(uuid4())
        reviewed = _review(client, decision, extra_headers={"x-correlation-id": review_correlation})
        assert reviewed.status_code == 200, reviewed.text
        receipt = reviewed.json()
        assert receipt["status"] == "SCORE_QUEUED"
        assert receipt["reviewer_subject_id"] == APPROVER
        assert receipt["proposer_subject_id"] == MANAGER
        with bridge_database.connect() as conn:
            for audit_receipt, actor, action in ((decision, MANAGER, "intake.promotion_requested"),
                                                (receipt, APPROVER, "promotion.score_queued")):
                row = conn.execute(
                    "SELECT actor_subject_id,resource_id,action,result,after_value,correlation_id FROM audit.audit_events WHERE audit_event_id=%s",
                    (audit_receipt["audit_event_id"],)).fetchone()
                assert row is not None
                assert tuple(str(value) for value in row[:4]) == (actor, decision["promotion_decision_id"], action, "SUCCEEDED")
                assert row[4]["promotion_decision_id"] == decision["promotion_decision_id"]
                if actor == APPROVER:
                    assert str(row[5]) == review_correlation
        repo = bundle.listing_repository_for_tenant(TENANT)
        candidates = repo.list_candidates()
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.candidate_site.candidate_site_id == receipt["candidate_site_id"]
        assert candidate.candidate_site.listing_id == listing_id
        assert candidate.candidate_site.target_format_code == "FORMAT-B"
        assert candidate.candidate_site.created_by == APPROVER
        events = bundle.outbox_repository.get_unpublished_events()
        created = [event for event in events if event.event_type == "candidate.created"]
        assert len(created) == 1 and created[0].payload["target_format_code"] == "FORMAT-B"
        assert candidate.property_entity_id == created[0].payload["property_id"]
        assert repo.get_listing(listing_id).property_id == candidate.property_entity_id
        before = _snapshot(bundle)
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        client = _promotion_client(bundle)
        for path in (f"/api/v1/intakes/{record['id']}/promotion-decision",
                     f"/api/v1/promotion-decisions/{decision['promotion_decision_id']}"):
            read = client.get(path, headers=_reviewer_headers())
            assert read.status_code == 200 and read.json() == receipt, read.text
        detail = client.get(f"{BASE}/intake/{record['id']}", headers=_headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["version"] == record["version"] + 2
        assert detail.json()["assignmentId"] == record["assignmentId"]
        assert detail.json()["slaInstanceId"] == record["slaInstanceId"]
        job = client.get(f"/api/v1/jobs/{receipt['site_score_job_id']}/receipt", headers=_reviewer_headers())
        assert job.status_code == 200, job.text
        assert job.json()["job_id"] == receipt["site_score_job_id"]
        assert job.json()["status"] == "QUEUED"
        from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

        stored_job = IntakeScoreJobRepository(bundle).get_job(receipt["site_score_job_id"], TENANT)
        assert stored_job["job_id"] == receipt["site_score_job_id"]
        assert stored_job["intake_id"] == record["id"]
        assert stored_job["candidate_site_id"] == receipt["candidate_site_id"]
        assert stored_job["tenant_id"] == TENANT
        assert bundle.job_queue.get(receipt["site_score_job_id"]) is not None
        bundle.assisted_intake_store.refresh(TENANT)
        assert receipt["site_score_job_id"] not in bundle.assisted_intake_store.jobs
        assert _request(client, record).json() == decision
        replayed = _review(client, decision)
        assert replayed.status_code == 200 and replayed.json() == receipt, replayed.text
        assert replayed.headers["idempotency-replayed"] == "true"
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()


def test_operator_promotion_is_visible_to_v1_review(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, listing_id = _ready_admitted_intake(bridge_database, bundle, client)
        requested = client.post(f"{BASE}/intake/{record['id']}/promote",
                                json={"reason": "Synthetic operator request", "riskSummary": "Synthetic promotion",
                                      "riskAcknowledged": True},
                                headers=_headers(key="synthetic-operator-promote"))
        assert requested.status_code == 200, requested.text
        decision = requested.json()
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        client = _promotion_client(bundle)
        read = client.get(f"/api/v1/promotion-decisions/{decision['promotion_decision_id']}",
                          headers=_reviewer_headers())
        assert read.status_code == 200, read.text
        assert read.json()["listing_id"] == listing_id
        with bridge_database.connect() as conn:
            row = conn.execute("SELECT actor_subject_id,action FROM audit.audit_events WHERE audit_event_id=%s",
                               (read.json()["audit_event_id"],)).fetchone()
            assert row is not None and (str(row[0]), row[1]) == (MANAGER, "intake.promotion_requested")
        assert len([event for event in bundle.outbox_repository.get_unpublished_events()
                    if event.event_type == "candidate.promotion_requested"]) == 1
        reviewed = _review(client, read.json())
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["intake_id"] == record["id"]
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
    finally:
        bundle.engine.close()


def test_queue_transaction_fault_rolls_back_without_fabricated_failure_receipt(bridge_database, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CI", "1")
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, _ = _ready_admitted_intake(bridge_database, bundle, client)
        requested = _request(client, record)
        assert requested.status_code == 202, requested.text
        decision = requested.json()
        before = _snapshot(bundle)
        failed = _review(TestClient(client.app, raise_server_exceptions=False), decision, fault=True)
        assert failed.status_code == 500, failed.text
        assert _snapshot(bundle) == before
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        client = _promotion_client(bundle)
        assert _snapshot(bundle) == before
        read = client.get(f"/api/v1/promotion-decisions/{decision['promotion_decision_id']}", headers=_reviewer_headers())
        assert read.status_code == 200 and read.json()["status"] == "PENDING_REVIEW", read.text
        assert not read.json().get("site_score_job_id")
        assert not read.json().get("candidate_site_id")
        # The same command can be retried once the failed transaction recovers.
        retried = _review(client, decision)
        assert retried.status_code == 200 and retried.json()["status"] == "SCORE_QUEUED", retried.text
        assert retried.headers["idempotency-replayed"] == "false"
        assert len(bundle.listing_repository_for_tenant(TENANT).list_candidates()) == 1
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("failure", ["request_outbox", "candidate", "review_outbox", "review_replay", "audit"])
def test_promotion_persistence_failure_rolls_back_all_artifacts(bridge_database, monkeypatch, failure):
    from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, _ = _ready_admitted_intake(bridge_database, bundle, client)
        if failure != "request_outbox":
            requested = _request(client, record)
            assert requested.status_code == 202, requested.text
            decision = requested.json()
        before = _snapshot(bundle)
        original_put = bundle.assisted_intake_store._store.put
        original_event = bundle.outbox_repository.save
        audit_sink = bundle.assisted_intake_store._canonical_audit_sink
        original_audit = audit_sink.persist

        def failing_put(collection, doc_id, value, **kwargs):
            original_put(collection, doc_id, value, **kwargs)
            if ((failure == "candidate" and collection.startswith("listing.candidates"))
                    or (failure == "review_replay" and collection == _STATE_COLLECTIONS["replays"])):
                raise RuntimeError("Synthetic promotion persistence failure")

        def failing_event(event):
            original_event(event)
            if ((failure == "request_outbox" and event.event_type == "candidate.promotion_requested")
                    or (failure == "review_outbox" and event.event_type == "candidate.created")):
                raise RuntimeError("Synthetic promotion outbox failure")

        def failing_audit(*args, **kwargs):
            from shared.audit.errors import CanonicalAuditUnavailable

            value = original_audit(*args, **kwargs)
            if failure == "audit":
                raise CanonicalAuditUnavailable("Synthetic promotion audit failure")
            return value

        with monkeypatch.context() as patch:
            patch.setattr(bundle.assisted_intake_store._store, "put", failing_put)
            patch.setattr(bundle.outbox_repository, "save", failing_event)
            patch.setattr(audit_sink, "persist", failing_audit)
            try:
                response = _request(client, record) if failure == "request_outbox" else _review(client, decision)
                assert response.status_code >= 400, response.text
            except RuntimeError as exc:
                assert "Synthetic promotion" in str(exc)
        assert _snapshot(bundle) == before
        bundle.engine.close()
        bundle = _bundle(bridge_database, monkeypatch)
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()


def test_two_replicas_converge_request_and_review_replays(bridge_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    bundle = _bundle(bridge_database, monkeypatch)
    second = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        other_client = _promotion_client(second)
        record, _ = _ready_admitted_intake(bridge_database, bundle, client)
        barrier = Barrier(2)

        def request(local_client):
            barrier.wait(timeout=10)
            return _request(local_client, record)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, (client, other_client)))
        assert [result.status_code for result in results] == [202, 202], [r.text for r in results]
        assert results[0].json() == results[1].json()
        assert sorted(result.headers["idempotency-replayed"] for result in results) == ["false", "true"]
        decision = results[0].json()
        barrier = Barrier(2)

        def review(local_client):
            barrier.wait(timeout=10)
            return _review(local_client, decision)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, (client, other_client)))
        assert [result.status_code for result in results] == [200, 200], [r.text for r in results]
        assert results[0].json() == results[1].json()
        assert sorted(result.headers["idempotency-replayed"] for result in results) == ["false", "true"]
        state = _snapshot(bundle)
        assert len(state["canonical"]["promotions"]) == 1
        assert len(state["score_jobs"]) == 1
        assert state["canonical"]["jobs"] == {}
        assert len(state["candidates"]) == 1
        assert len([e for e in state["events"] if e.event_type == "candidate.created"]) == 1
    finally:
        bundle.engine.close()
        second.engine.close()


@pytest.mark.parametrize("operation", ["request", "review"])
def test_fresh_parent_scope_is_checked_after_route_read(bridge_database, monkeypatch, operation):
    from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS

    bundle = _bundle(bridge_database, monkeypatch)
    second = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, _ = _ready_admitted_intake(bridge_database, bundle, client)
        if operation == "review":
            requested = _request(client, record)
            assert requested.status_code == 202, requested.text
            decision = requested.json()
        before = _snapshot(bundle)
        original_lock = bundle.assisted_intake_store._read_locked
        revoked = False

        def revoke_before_lock(collection, key):
            nonlocal revoked
            if not revoked and collection == _STATE_COLLECTIONS["intakes"]:
                revoked = True
                other = second.assisted_intake_store
                other.refresh(TENANT)
                other.intakes[record["id"]]["scope"]["heat_zone_id"] = "synthetic-revoked-zone"
                other.flush()
            return original_lock(collection, key)

        with monkeypatch.context() as patch:
            patch.setattr(bundle.assisted_intake_store, "_read_locked", revoke_before_lock)
            scoped = {"x-heat-zone-ids": "synthetic-permitted-zone"}
            response = (_request(client, record, extra_headers=scoped) if operation == "request"
                        else _review(client, decision, extra_headers=scoped))
        assert revoked and response.status_code == 403, response.text
        before["canonical"]["intakes"][record["id"]]["scope"]["heat_zone_id"] = "synthetic-revoked-zone"
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()
        second.engine.close()


def test_missing_recorded_property_cannot_create_promotion_or_artifacts(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, listing_id = _ready_admitted_intake(bridge_database, bundle, client)
        repo = bundle.listing_repository_for_tenant(TENANT)
        listing = repo.get_listing(listing_id)
        object.__delattr__(listing, "property_id")
        repo._store.put(repo._LISTINGS, listing_id, listing)
        before = _snapshot(bundle)
        response = _request(client, record)
        assert response.status_code == 422, response.text
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()


def test_production_promotion_requires_transaction_bound_outbox(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, _ = _ready_admitted_intake(bridge_database, bundle, client)
        before = _snapshot(bundle)
        with monkeypatch.context() as patch:
            patch.setattr(client.app.state, "persistence_bundle", replace(bundle, outbox_repository=None))
            response = _request(client, record)
        assert response.status_code == 503 and response.json()["detail"] == "PROMOTION_OUTBOX_UNAVAILABLE"
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()


def test_unresolved_canonical_match_cannot_use_stale_flat_alias(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, listing_id = _ready_admitted_intake(bridge_database, bundle, client)
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        parent = store.intakes[record["id"]]
        parent["operator_presentation"]["matchResult"] = None
        parent["matchResult"] = {"targetListingId": listing_id}
        store.flush()
        before = _snapshot(bundle)
        response = _request(client, record)
        assert response.status_code == 422 and "not resolved" in response.text, response.text
        assert _snapshot(bundle) == before
    finally:
        bundle.engine.close()
