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
from tests.intake_registry_fixture import APPROVER, MANAGER, OWNER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import (
    BASE,
    _client,
    _headers,
    _seed_historical_submission,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)
from tests.support.listing_search_integration import (
    assert_receipt_revision_intent,
    listing_search_events,
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
            "x-operator-role": "expansion-manager",
            **({"If-Match": 'W/"2"'} if str(key).endswith("-decide") else {})}


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
    # Explicit synthetic recorded passing verdict, independent of geocoding.
    object.__setattr__(listing, "hard_rule_failures", [])
    address = AddressLocation(address_id=listing.address_id,
                              raw_address="Synthetic measured address", normalized_address="Synthetic measured address",
                              geocode_confidence=0.95, h3_res_9="8929a1d4d67ffff")
    bundle.listing_repository_for_tenant(TENANT).save_listing(
        listing, address, ListingDedupKey(source_id=listing.source_id,
                                         source_listing_id=listing.source_listing_id,
                                         normalized_address=address.normalized_address,
                                         rent_amount=listing.rent_amount, area_ping=listing.area_ping))
    # Promotion acts on retained Operator work, not either retired URL fresh
    # writer. The original bridge initializes its actual Assignment/SLA in SQL.
    retained = _seed_historical_submission(
        bundle, "https://rent.591.com.tw/synthetic-promotion",
        heat_zone_id="synthetic-permitted-zone",
    )
    submitted = client.get(f"{BASE}/intake/{retained['id']}", headers=_headers())
    assert submitted.status_code == 200, submitted.text
    initial = submitted.json()
    assert initial["stage"] == "AWAITING_ASSISTED_ENTRY"
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
                          headers={**_reviewer_headers("synthetic-promotion-decide"),
                                   "If-Match": f'W/"{corrected.json()["version"]}"'})
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


def _request(client, record, *, key="synthetic-promotion-request", actor=MANAGER, extra_headers=None, target_format_code="FORMAT-B"):
    return client.post(f"/api/v1/intakes/{record['id']}/promotion-requests",
                       json={"target_format_code": target_format_code, "reason": "Synthetic independent review",
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
        "matching_events": listing_search_events(bundle, TENANT),
        "score_jobs": bundle.engine.query("SELECT job_id,status,version,payload FROM workflow.jobs WHERE job_type='intake-sitescore' ORDER BY job_id")})


def test_browser_ready_fixture_uses_retained_history_without_fresh_http(intake_blank_db, monkeypatch):
    from modules.opsboard.application.network_listings import NetworkListingService
    from tests.support.intake_score_synthetic import (
        SCOPE,
        headers,
        prepare_database,
        ready_scenario,
    )

    db = intake_blank_db
    prepare_database(db.url())
    bundle = _bundle(db, monkeypatch)

    def forbid_fresh(*_args, **_kwargs):
        pytest.fail("The browser's retained setup must not invoke either fresh writer")

    monkeypatch.setattr(NetworkListingService, "submit_intake", forbid_fresh)
    try:
        client = _promotion_client(bundle)
        case = ready_scenario(db.url(), bundle, client)
        assert case["scope"] == SCOPE
        record = client.get(f"{BASE}/intake/{case['intake_id']}", headers=headers())
        assert record.status_code == 200, record.text
        intake = record.json()
        assert (intake["stage"], intake["version"]) == ("READY", 4)
        assert intake["submittedBy"] == MANAGER
        assert intake["assignmentId"] and intake["slaInstanceId"]
        assert intake["matchResult"]["targetListingId"] == case["listing_id"]
        assert [event["action"] for event in intake["auditEvents"]] == [
            "intake.correct", "intake.decide.revise",
        ]
        assert [event["actorName"] for event in intake["auditEvents"]] == [MANAGER, APPROVER]
        assert intake["parsedFields"]["floor"]["correctedValue"] == "1F"
        assert intake["rawSnapshot"] is None and intake["parserVersion"] is None
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        assert len(store.intakes) == len(store.assignments) == len(store.slas) == 1
        assert not store.promotions and not store.jobs
        assert all(
            replay.action != "submit_intake"
            for replay in bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records()
        )
        with db.connect() as conn:
            # The explicit manual source/snapshot fixture is unchanged. It is
            # not a native URL command, parser output or worker result.
            assert conn.execute("SELECT intake_method,processing_state FROM intake.intakes WHERE intake_id=%s",
                                (case["intake_id"],)).fetchone() == ("MANUAL", "READY")
            assert conn.execute("SELECT capture_method FROM intake.source_snapshots WHERE intake_id=%s",
                                (case["intake_id"],)).fetchone() == ("MANUAL_ENTRY",)
            assert conn.execute("SELECT count(*) FROM workflow.jobs").fetchone()[0] == 0
        assert bundle.engine.query_one(
            "SELECT count(*) AS count FROM durable_jobs WHERE job_type='assisted-listing-intake'",
        )["count"] == 0
        # The original promotion API still sees the real resolved listing and
        # linked workflow version, rather than a fabricated browser receipt.
        requested = _request(client, intake, key="synthetic-browser-retained-promotion")
        assert requested.status_code == 202, requested.text
        assert requested.json()["status"] == "PENDING_REVIEW"
        assert requested.json()["listing_id"] == case["listing_id"]
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("history", ["possible-match", "retrieval-timeout", "revision"])
def test_browser_retained_review_and_failure_keep_real_guards(intake_blank_db, monkeypatch, history):
    from modules.opsboard.application.network_listings import NetworkListingService
    from tests.support.intake_score_synthetic import headers, prepare_database, ready_scenario

    db = intake_blank_db
    prepare_database(db.url())
    bundle = _bundle(db, monkeypatch)

    def forbid_fresh(*_args, **_kwargs):
        pytest.fail("Retained browser setup must not submit or retrieve a fresh URL")

    monkeypatch.setattr(NetworkListingService, "submit_intake", forbid_fresh)
    monkeypatch.setattr(NetworkListingService, "_get_snapshot_service", forbid_fresh)
    try:
        client = _promotion_client(bundle)
        case = ready_scenario(db.url(), bundle, client, retained_history=history)
        path = f"{BASE}/intake/{case['intake_id']}"
        observed = client.get(path, headers=headers())
        assert observed.status_code == 200, observed.text
        initial = observed.json()
        assert initial["version"] == 1 and initial["auditEvents"] == []
        # These retained original synthetic retrieval histories preserve their
        # recorded policy for legacy presentation. They confer no current
        # source approval and contain no manufactured capture/parser evidence.
        assert initial["policy"] == "APPROVED_RETRIEVAL"
        assert initial["originalUrl"] == case["original_url"]
        assert initial["originalUrl"].startswith("https://www.synthetic.example/retained-")
        assert initial["assignmentId"] and initial["slaInstanceId"]
        for field in ("rawSnapshot", "snapshotId", "capturedAt", "parserVersion", "lastObservedAt"):
            assert initial[field] is None
        assert client.get(path, headers={**headers(), "x-tenant-id": str(uuid4())}).status_code == 404
        prepared = _snapshot(bundle)
        assert not bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records()
        with db.connect() as connection:
            for table in ("intake.intakes", "intake.source_registry", "intake.source_snapshots", "workflow.jobs"):
                assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        if history == "retrieval-timeout":
            assert initial["stage"] == "FAILED" and case["listing_id"] is None
            assert initial["failure"]["code"] == "ODP-INTAKE-RETRIEVAL-TIMEOUT"
            assert initial["failure"]["retryable"] and initial["failure"]["nextAction"]
            assert initial["correlationId"] and initial["parsedFields"] == {}
            assert bundle.listing_repository_for_tenant(TENANT).list_listings() == []
            record = initial
        else:
            revision = history == "revision"
            assert initial["stage"] == ("READY" if revision else "NEEDS_REVIEW")
            assert initial["matchResult"]["outcome"] == ("REVISION" if revision else "POSSIBLE_MATCH")
            assert initial["matchResult"]["targetListingId"] == case["listing_id"]
            assert initial["matchResult"]["agreeingSignals"] and initial["matchResult"]["contradictingSignals"]
            assert initial["parsedFields"]["address"]["sourceValue"]
            if revision:
                target = bundle.listing_repository_for_tenant(TENANT).get_listing(case["listing_id"])
                address = bundle.listing_repository_for_tenant(TENANT).get_address(target.address_id)
                assert target.source_listing_id == initial["parsedFields"]["providerListingId"]["normalizedValue"]
                assert address.normalized_address == initial["parsedFields"]["address"]["normalizedValue"]
                assert target.rent_amount == 58000
            body = {"action": "revise" if revision else "create", "reason": "Explicit retained browser human decision",
                    "riskSummary": "Retained ambiguous history; no new source acquisition", "riskAcknowledged": True}
            for precondition, status in ((None, 428), ('W/"9"', 409)):
                response = client.post(path + "/decide", json=body,
                    headers={**headers(), **({"If-Match": precondition} if precondition else {})})
                assert response.status_code == status, response.text
                assert _snapshot(bundle) == prepared
            denied = client.post(path + "/decide", json=body,
                headers={**_headers(OWNER), "If-Match": 'W/"1"'})
            assert denied.status_code == 422, denied.text
            assert _snapshot(bundle) == prepared
            command_headers = {**headers(key="synthetic-retained-browser-decision"), "If-Match": 'W/"1"'}
            decided = client.post(path + "/decide", json=body, headers=command_headers)
            assert decided.status_code == 200, decided.text
            record = decided.json()
            assert (record["stage"], record["version"]) == ("READY", 2)
            assert record["matchResult"]["outcome"] == ("REVISION" if revision else "NEW")
            if revision:
                assert record["matchResult"]["targetListingId"] == case["listing_id"]
                target = bundle.listing_repository_for_tenant(TENANT).get_listing(case["listing_id"])
                assert target.rent_amount == initial["parsedFields"]["rent"]["normalizedValue"]
                assert record["auditEvents"][0]["metadata"]["beforeAfter"]["target_rent"] == {
                    "before": 58000, "after": target.rent_amount,
                }
            else:
                assert record["matchResult"]["targetListingId"] != case["listing_id"]
            assert [event["action"] for event in record["auditEvents"]] == ["intake.decide.revise" if revision else "intake.decide.create"]
            assert record["parsedFields"] == initial["parsedFields"]
            assert record["parserVersion"] is record["snapshotId"] is record["rawSnapshot"] is None
            committed = _snapshot(bundle)
            replay = client.post(path + "/decide", json=body, headers=command_headers)
            assert replay.status_code == 200 and replay.json() == record
            assert _snapshot(bundle) == committed
            assert len(bundle.listing_repository_for_tenant(TENANT).list_listings()) == (1 if revision else 2)
        assert record["assignmentId"] == initial["assignmentId"]
        assert record["slaInstanceId"] == initial["slaInstanceId"]
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
        assert bundle.engine.query_one(
            "SELECT count(*) AS count FROM durable_jobs WHERE job_type='assisted-listing-intake'",
        )["count"] == 0
    finally:
        bundle.engine.close()
    replica = _bundle(db, monkeypatch)
    try:
        read = _promotion_client(replica).get(path, headers=headers())
        assert read.status_code == 200 and read.json() == record
    finally:
        replica.engine.close()


def test_browser_retained_assisted_entry_preserves_real_correction_without_fresh_http(intake_blank_db, monkeypatch):
    from modules.opsboard.application.network_listings import NetworkListingService
    from tests.support.intake_score_synthetic import headers, prepare_database, ready_scenario

    db = intake_blank_db
    prepare_database(db.url())
    bundle = _bundle(db, monkeypatch)

    def forbid_fresh(*_args, **_kwargs):
        pytest.fail("Retained manual-entry setup must not invoke a fresh writer")

    monkeypatch.setattr(NetworkListingService, "submit_intake", forbid_fresh)
    try:
        client = _promotion_client(bundle)
        case = ready_scenario(db.url(), bundle, client, retained_history="assisted-entry-only")
        assert case["listing_id"] is None
        path = f"{BASE}/intake/{case['intake_id']}"
        initial = client.get(path, headers=headers()).json()
        assert (initial["stage"], initial["version"]) == ("AWAITING_ASSISTED_ENTRY", 1)
        assert initial["originalUrl"] == case["original_url"]
        assert initial["parsedFields"] == {} and initial["auditEvents"] == []
        assert initial["assignmentId"] and initial["slaInstanceId"]
        body = {"fields": {"address": "Synthetic retained manual address", "rent": "54000", "areaPing": "22"},
                "reason": "Explicit retained manual-entry correction", "riskSummary": "No retrieved evidence",
                "riskAcknowledged": True}
        command_headers = headers(key="synthetic-retained-manual-correction")
        corrected = client.post(path + "/correct", json=body, headers=command_headers)
        assert corrected.status_code == 200, corrected.text
        record = corrected.json()
        assert record["version"] == 2
        assert record["assignmentId"] == initial["assignmentId"]
        assert record["slaInstanceId"] == initial["slaInstanceId"]
        assert [event["action"] for event in record["auditEvents"]] == ["intake.correct"]
        assert record["auditEvents"][0]["actorName"] == MANAGER
        for name in ("capturedAt", "rawSnapshot", "snapshotId", "parserVersion", "lastObservedAt"):
            assert record[name] is None
        replay = client.post(path + "/correct", json=body, headers=command_headers)
        assert replay.status_code == 200 and replay.json() == record
        assert client.get(path, headers={**headers(), "x-tenant-id": str(uuid4())}).status_code == 404
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        assert len(store.intakes) == len(store.assignments) == len(store.slas) == 1
        assert not store.jobs and not store.promotions
        assert bundle.listing_repository_for_tenant(TENANT).list_listings() == []
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
        receipts = bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records()
        assert [receipt.action for receipt in receipts] == ["correct_intake"]
        assert bundle.engine.query_one(
            "SELECT count(*) AS count FROM durable_jobs WHERE job_type='assisted-listing-intake'",
        )["count"] == 0
        with db.connect() as connection:
            # Historical Operator presentation has not manufactured a native
            # Intake, source approval, snapshot, worker result or command.
            for table in ("intake.intakes", "intake.source_registry", "intake.source_snapshots", "workflow.jobs"):
                assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        bundle.engine.close()
    replica = _bundle(db, monkeypatch)
    try:
        read = _promotion_client(replica).get(path, headers=headers())
        assert read.status_code == 200 and read.json() == record
    finally:
        replica.engine.close()


def test_canonical_promotion_positive_restart_and_replay(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _promotion_client(bundle)
        record, listing_id = _ready_admitted_intake(bridge_database, bundle, client)
        target = "  Synthetic-MixedCase-9  "
        requested = _request(client, record, target_format_code=target)
        assert requested.status_code == 202, requested.text
        decision = requested.json()
        assert decision["target_format_code"] == target
        assert decision["intake_id"] == record["id"]
        assert decision["listing_id"] == listing_id
        assert decision["proposer_subject_id"] == MANAGER
        review_correlation = str(uuid4())
        reviewed = _review(client, decision, extra_headers={"x-correlation-id": review_correlation})
        assert reviewed.status_code == 200, reviewed.text
        receipt = reviewed.json()
        assert receipt["target_format_code"] == target
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
        assert candidate.candidate_site.target_format_code == target
        assert candidate.candidate_site.created_by == APPROVER
        events = bundle.outbox_repository.get_unpublished_events()
        created = [event for event in events if event.event_type == "candidate.created"]
        assert len(created) == 1 and created[0].payload["target_format_code"] == target
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
        assert _request(client, record, target_format_code=target).json() == decision
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
        revocation_intent = {}

        def revoke_before_lock(collection, key):
            nonlocal revoked
            if not revoked and collection == _STATE_COLLECTIONS["intakes"]:
                revoked = True
                other = second.assisted_intake_store
                other.refresh(TENANT)
                other.intakes[record["id"]]["scope"]["heat_zone_id"] = "synthetic-revoked-zone"
                other.intakes[record["id"]]["version"] += 1
                other.flush()
                event, job = assert_receipt_revision_intent(second, other.intakes[record["id"]])
                revocation_intent.update(event=event, job=job)
            return original_lock(collection, key)

        with monkeypatch.context() as patch:
            patch.setattr(bundle.assisted_intake_store, "_read_locked", revoke_before_lock)
            scoped = {"x-heat-zone-ids": "synthetic-permitted-zone"}
            response = (_request(client, record, extra_headers=scoped) if operation == "request"
                        else _review(client, decision, extra_headers=scoped))
        assert revoked and response.status_code == 403, response.text
        before["canonical"]["intakes"][record["id"]]["scope"]["heat_zone_id"] = "synthetic-revoked-zone"
        before["canonical"]["intakes"][record["id"]]["version"] += 1
        # Only the explicitly verified external revocation revision/event/job
        # may differ. Every denied promotion, score, audit, outbox and replay
        # row still has to match the complete pre-request snapshot.
        before["matching_events"] = sorted(
            [*before["matching_events"], revocation_intent["event"]], key=lambda row: row["id"],
        )
        before["delivery_jobs"] = sorted(
            [*before["delivery_jobs"], revocation_intent["job"]], key=lambda row: row["job_id"],
        )
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


# ---------------------------------------------------------------------------
# Native SQL Promotion & Completion Audit Hydration Tests (Explicitly Synthetic)
# ---------------------------------------------------------------------------


def _ensure_native_address_geo_schema(db) -> None:
    """Ensure core.address_locations and geo.h3_cells exist in isolated intake_db fixtures."""
    with db.connect(autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS core")
        conn.execute("CREATE SCHEMA IF NOT EXISTS geo")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS core.tenants ("
            "tenant_id UUID PRIMARY KEY, tenant_name VARCHAR(255) NOT NULL, "
            "status VARCHAR(50) NOT NULL DEFAULT 'active', "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS core.address_locations ("
            "address_id UUID PRIMARY KEY, tenant_id UUID REFERENCES core.tenants(tenant_id), "
            "revision INTEGER NOT NULL DEFAULT 1, raw_address TEXT NOT NULL, "
            "normalized_address TEXT, city VARCHAR(100), district VARCHAR(100), "
            "village VARCHAR(100), road VARCHAR(255), latitude NUMERIC(10, 7), "
            "longitude NUMERIC(10, 7), geocode_precision VARCHAR(50) DEFAULT 'manual', "
            "geocode_confidence NUMERIC(3, 2), h3_res_8 VARCHAR(15), h3_res_9 VARCHAR(15), "
            "h3_res_10 VARCHAR(15), manual_override_flag BOOLEAN DEFAULT FALSE, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS geo.h3_cells ("
            "geo_cell_id UUID PRIMARY KEY, h3_index VARCHAR(15) NOT NULL UNIQUE, "
            "h3_resolution INTEGER NOT NULL DEFAULT 9, parent_h3_index VARCHAR(15), "
            "centroid_latitude NUMERIC(10, 7) NOT NULL, centroid_longitude NUMERIC(10, 7) NOT NULL, "
            "admin_city VARCHAR(100), admin_district VARCHAR(100), service_area_id VARCHAR(100), "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO core.tenants(tenant_id, tenant_name) VALUES (%s, 'Synthetic native tenant') "
            "ON CONFLICT (tenant_id) DO NOTHING",
            (TENANT,),
        )


def _native_bundle(db, worm_dir):
    """Build a PersistenceBundle-compatible native SQL runtime bound to one PostgresEngine."""
    from types import MethodType, SimpleNamespace

    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import PersistenceBundle
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
    from shared.infrastructure.persistence.outbox import DurableOutboxRepository
    from shared.infrastructure.persistence.postgresql import PostgresDocumentStore, PostgresEngine
    from shared.infrastructure.persistence.repositories import (
        DurableListingRepository,
        DurableSiteScoreRepository,
    )
    from tests.intake_workflow_fixture import (
        SyntheticIntakeAuditRetention,
        SyntheticIntakeWorkflowPolicy,
    )

    engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    engine.apply_runtime_migration()
    doc_store = PostgresDocumentStore(engine)
    queue = DurableJobQueue(engine)
    outbox = DurableOutboxRepository(engine)
    store = DurableAssistedIntakeStore(doc_store)
    worm = LocalAppendOnlyWormSink(worm_dir)
    writer = CanonicalPostgresAuditLog(engine)
    delivery = CanonicalAuditDelivery(writer, queue, worm)
    sink = CanonicalIntakeAuditSink(writer, SyntheticIntakeAuditRetention(), delivery)
    store.configure_canonical_audit(sink)
    listing_repo = DurableListingRepository(TenantScopedDocumentStore(doc_store, TENANT))
    sitescore_repo = DurableSiteScoreRepository(TenantScopedDocumentStore(doc_store, TENANT))
    bundle = SimpleNamespace(
        engine=engine,
        assisted_intake_store=store,
        assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
        job_queue=queue,
        outbox_repository=outbox,
        listing_repository=listing_repo,
        sitescore_repository=sitescore_repo,
        learninghub_repository=None,
    )
    for name in (
        "canonical_identity_graph",
        "canonical_intakes",
        "_scoped_repository",
        "listing_repository_for_tenant",
    ):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    return bundle


def _seed_native_ready_intake(
    db,
    *,
    tenant_id: str = TENANT,
    h3_index: str = "8929a1d4d67ffff",
    floor: str = "1F",
    area_ping: float = 25.0,
    frontage_m: float = 6.0,
    retrieved: bool = False,
    property_id: str | None = None,
    address_id: str | None = None,
) -> dict[str, str]:
    """Seed one explicitly synthetic native SQL READY Intake + Listing + Revision + Address + Geo."""
    import hashlib
    from datetime import timedelta

    _ensure_native_address_geo_schema(db)
    intake_id = str(uuid4())
    listing_id = str(uuid4())
    revision_id = str(uuid4())
    prop_id = property_id or str(uuid4())
    addr_id = address_id or str(uuid4())
    geo_cell_id = str(uuid4())
    snapshot_id = str(uuid4())
    source_id = f"synthetic-native-src-{uuid4().hex[:8]}"
    correlation_id = str(uuid4())
    assignment_id = str(uuid4())
    sla_id = str(uuid4())
    parser_release_id = str(uuid4()) if retrieved else None
    parser_run_id = str(uuid4()) if retrieved else None
    now = datetime.now(UTC) - timedelta(minutes=10)
    addr_text = f"台北市信義區松仁路{uuid4().hex[:4]}號"
    if address_id is not None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT normalized_address FROM core.address_locations WHERE address_id=%s",
                (address_id,),
            ).fetchone()
            assert row is not None
            addr_text = row[0]
    normalized = {
        "address": addr_text,
        "rent": 54000,
        "area_ping": area_ping,
        "floor": floor,
        "frontage_m": frontage_m,
        "depth_m": 12.0,
        "corner_flag": True,
        "parking_flag": True,
        "utility_electricity_flag": True,
        "utility_drainage_flag": True,
        "utility_gas_flag": True,
        "confidence": 0.92,
        "address_id": addr_id,
        "source_snapshot_id": snapshot_id,
        "feature_snapshot_time": now.isoformat(),
        "prior_90d_cell_net_revenue": 1_800_000.0,
        "prior_90d_cell_transaction_count": 720,
        "prior_90d_cell_store_count": 4,
    }
    with db.connect(autocommit=False) as conn:
        conn.execute(
            "INSERT INTO core.tenants(tenant_id, tenant_name) VALUES (%s, 'Synthetic native tenant') "
            "ON CONFLICT (tenant_id) DO NOTHING",
            (tenant_id,),
        )
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        if address_id is None:
            conn.execute(
                "INSERT INTO core.address_locations(address_id,tenant_id,revision,raw_address,normalized_address,"
                "city,district,latitude,longitude,geocode_precision,geocode_confidence,h3_res_9) "
                "VALUES (%s,%s,1,%s,%s,'Taipei','Xinyi',25.033964,121.564468,'rooftop',0.95,%s)",
                (addr_id, tenant_id, addr_text, addr_text, h3_index),
            )
        conn.execute(
            "INSERT INTO geo.h3_cells(geo_cell_id,h3_index,h3_resolution,centroid_latitude,centroid_longitude,"
            "admin_city,admin_district) VALUES (%s,%s,9,25.033964,121.564468,'Taipei','Xinyi') "
            "ON CONFLICT (h3_index) DO NOTHING",
            (geo_cell_id, h3_index),
        )
        mode = "APPROVED_RETRIEVAL" if retrieved else "ASSISTED_ENTRY_ONLY"
        conn.execute(
            "INSERT INTO intake.source_registry(source_id,display_name,allowed_hosts,"
            "canonicalization_rule_version,retrieval_mode,policy_owner_subject_id,"
            "legal_approval_ref,license_approval_ref,review_expires_at,kill_switch,production_enabled) "
            "VALUES (%s,'Synthetic native source',ARRAY['example.invalid'],'synthetic-v1',%s,%s,"
            "'synthetic-legal','synthetic-license',now()+interval '7 days',false,true)",
            (source_id, mode, APPROVER),
        )
        if property_id is None:
            conn.execute(
                "INSERT INTO identity.properties(property_id,tenant_id,normalized_address,address_fingerprint,"
                "status,version) VALUES (%s,%s,%s,%s,'ACTIVE',1)",
                (prop_id, tenant_id, addr_text, hashlib.sha256(addr_text.encode()).hexdigest()),
            )
        conn.execute(
            "INSERT INTO intake.intakes(intake_id,tenant_id,submitter_subject_id,intake_method,original_url,"
            "source_id,source_policy_state,processing_state,correlation_id,version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'READY',%s,3)",
            (
                intake_id,
                tenant_id,
                MANAGER,
                "URL" if retrieved else "MANUAL",
                f"https://example.invalid/synthetic-{intake_id[:8]}" if retrieved else None,
                source_id,
                mode,
                correlation_id,
            ),
        )
        conn.execute(
            "INSERT INTO intake.source_snapshots(source_snapshot_id,tenant_id,intake_id,source_id,"
            "raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,"
            "retention_class,encryption_key_ref) VALUES (%s,%s,%s,%s,%s,%s,'application/json',2,%s,%s,"
            "%s,'BUSINESS_5Y','synthetic-key')",
            (
                snapshot_id,
                tenant_id,
                intake_id,
                source_id,
                f"gs://synthetic-native/{snapshot_id}",
                hashlib.sha256(snapshot_id.encode()).hexdigest(),
                now,
                now,
                "SERVER_RETRIEVAL" if retrieved else "MANUAL_ENTRY",
            ),
        )
        if retrieved:
            conn.execute(
                "INSERT INTO intake.parser_releases(parser_release_id,source_id,package_name,semantic_version,"
                "input_schema_version,output_schema_version,artifact_uri,artifact_sha256,test_corpus_version,"
                "validation_status,released_by,released_at) VALUES (%s,%s,'synthetic-native-parser','1.0.0',"
                "'synthetic-raw-v1','synthetic-normalized-v1','gs://synthetic-parser/artifact.zip',%s,"
                "'synthetic-corpus-v1','PRODUCTION',%s,%s)",
                (parser_release_id, source_id, "c" * 64, APPROVER, now - timedelta(days=1)),
            )
            conn.execute(
                "INSERT INTO intake.parser_runs(parser_run_id,tenant_id,intake_id,source_snapshot_id,"
                "parser_release_id,status,normalized_payload,validation_errors,started_at,completed_at,correlation_id) "
                "VALUES (%s,%s,%s,%s,%s,'SUCCEEDED',%s::jsonb,'[]'::jsonb,%s,%s,%s)",
                (
                    parser_run_id,
                    tenant_id,
                    intake_id,
                    snapshot_id,
                    parser_release_id,
                    json.dumps({"floor": floor}),
                    now,
                    now + timedelta(minutes=1),
                    correlation_id,
                ),
            )
        conn.execute(
            "INSERT INTO intake.intake_stage_transitions(transition_id,tenant_id,intake_id,sequence_no,from_state,"
            "to_state,actor_subject_id,permission,reason_text,resulting_version,source_policy_version,"
            "parser_release_id,source_snapshot_id,correlation_id) "
            "VALUES (%s,%s,%s,1,'SUBMITTED','READY',%s,'intake:write','Synthetic native ready',3,1,%s,%s,%s)",
            (str(uuid4()), tenant_id, intake_id, MANAGER, parser_release_id, snapshot_id, correlation_id),
        )
        conn.execute(
            "INSERT INTO expansion.listings(listing_id,tenant_id,property_id,source_id,source_listing_id,"
            "lifecycle_state,version) VALUES (%s,%s,%s,%s,%s,'ACTIVE',1)",
            (listing_id, tenant_id, prop_id, source_id, f"prov-{listing_id[:8]}"),
        )
        conn.execute(
            "INSERT INTO expansion.listing_revisions(listing_revision_id,tenant_id,listing_id,revision_no,"
            "revision_kind,source_snapshot_id,parser_run_id,normalized_values,effective_values,"
            "material_fingerprint,created_by) "
            "VALUES (%s,%s,%s,1,'CREATED',%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
            (
                revision_id,
                tenant_id,
                listing_id,
                snapshot_id,
                parser_run_id,
                json.dumps(normalized),
                json.dumps(normalized),
                hashlib.sha256(revision_id.encode()).hexdigest(),
                MANAGER,
            ),
        )
        conn.execute(
            "UPDATE expansion.listings SET current_revision_id=%s WHERE tenant_id=%s AND listing_id=%s",
            (revision_id, tenant_id, listing_id),
        )
        conn.execute(
            "UPDATE intake.intakes SET resolved_listing_id=%s WHERE tenant_id=%s AND intake_id=%s",
            (listing_id, tenant_id, intake_id),
        )
        conn.execute(
            "INSERT INTO workflow.assignments(assignment_id,tenant_id,intake_id,status,owner_subject_id,"
            "owner_role,assigned_by,queue_key,due_at,version) "
            "VALUES (%s,%s,%s,'CLAIMED',%s,'EXPANSION_STAFF',%s,'default',now()+interval '2 days',1)",
            (assignment_id, tenant_id, intake_id, MANAGER, MANAGER),
        )
        conn.execute(
            "INSERT INTO workflow.sla_instances(sla_instance_id,tenant_id,intake_id,assignment_id,policy_version,"
            "calendar_version,state,started_at,due_soon_at,due_at,breach_at,version) "
            "VALUES (%s,%s,%s,%s,'synthetic-sla-v1','synthetic-cal-v1','ON_TRACK',now(),"
            "now()+interval '1 day',now()+interval '2 days',now()+interval '3 days',1)",
            (sla_id, tenant_id, intake_id, assignment_id),
        )
        conn.commit()
    return {
        "intake_id": intake_id,
        "listing_id": listing_id,
        "revision_id": revision_id,
        "property_id": prop_id,
        "address_id": addr_id,
        "geo_cell_id": geo_cell_id,
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "correlation_id": correlation_id,
        "parser_release_id": parser_release_id,
        "parser_run_id": parser_run_id,
    }


def _claim_native_score_job(bundle, job_id: str):
    from shared.audit.canonical_delivery import JOB_TYPE as CANONICAL_AUDIT_JOB_TYPE
    from shared.audit.durable_delivery import JOB_TYPE as DURABLE_AUDIT_JOB_TYPE

    for _ in range(40):
        job = bundle.job_queue.claim_next(worker_id="synthetic-native-score-worker")
        assert job is not None, "Native score delivery job must be claimable"
        if job.job_id == job_id:
            return job
        assert job.job_type in {CANONICAL_AUDIT_JOB_TYPE, DURABLE_AUDIT_JOB_TYPE}
    raise AssertionError("Native score job never reached worker claim")


def test_native_sql_promotion_positive_cold_read_replay_and_active_listing_preserved(intake_db, tmp_path):
    import hashlib

    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    worm_dir = tmp_path / "synthetic-native-worm"
    bundle = _native_bundle(intake_db, worm_dir)
    try:
        ids = _seed_native_ready_intake(intake_db)
        canonical = bundle.canonical_intakes()
        score_jobs = IntakeScoreJobRepository(bundle)
        req_digest = hashlib.sha256(b"synthetic-native-req-oday-g2").hexdigest()
        proposed, replayed = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic native ODAY_G2 request",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-native-req-1",
            request_digest=req_digest,
            return_replay_status=True,
        )
        assert not replayed and proposed["status"] == "PENDING_REVIEW"
        assert proposed["gate_snapshot"]["policy_target_format_code"] == "ODAY_G2"
        assert proposed["gate_snapshot"]["caller_gate_snapshot_sha256"] == "a" * 64
        assert len(proposed["gate_snapshot"]["server_gate_snapshot_sha256"]) == 64
        assert proposed["gate_snapshot"]["listing_version"] == 1
        assert proposed["gate_snapshot"]["listing_revision_id"] == ids["revision_id"]
        assert proposed["gate_snapshot"]["property_version"] == 1
        assert proposed["gate_snapshot"]["address_id"] == ids["address_id"]
        assert proposed["gate_snapshot"]["address_revision"] == 1
        assert proposed["gate_snapshot"]["source_policy_version"] == 1

        # Idempotent replay returns exact receipt without duplicating SQL rows.
        replayed_req, was_replayed = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic native ODAY_G2 request",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-native-req-1",
            request_digest=req_digest,
            return_replay_status=True,
        )
        assert was_replayed and replayed_req == proposed

        rev_digest = hashlib.sha256(b"synthetic-native-rev-approve").hexdigest()
        reviewed, rev_replayed = canonical.review_promotion(
            TENANT,
            APPROVER,
            promotion_decision_id=proposed["promotion_decision_id"],
            expected_version=proposed["version"],
            decision="APPROVE",
            reason="Synthetic independent native approval",
            risk_acknowledged=True,
            idempotency_key="synthetic-native-rev-1",
            score_dispatcher=score_jobs,
            request_digest=rev_digest,
            return_replay_status=True,
        )
        assert not rev_replayed and reviewed["status"] == "SCORE_QUEUED"
        assert reviewed["candidate_site_id"] and reviewed["site_score_job_id"]
        assert reviewed["reviewer_subject_id"] == APPROVER

        # Replay of review returns exact receipt.
        reviewed_again, rev_was_replayed = canonical.review_promotion(
            TENANT,
            APPROVER,
            promotion_decision_id=proposed["promotion_decision_id"],
            expected_version=proposed["version"],
            decision="APPROVE",
            reason="Synthetic independent native approval",
            risk_acknowledged=True,
            idempotency_key="synthetic-native-rev-1",
            score_dispatcher=score_jobs,
            request_digest=rev_digest,
            return_replay_status=True,
        )
        assert rev_was_replayed and reviewed_again == reviewed

        with intake_db.connect() as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
            # SQL Listing MUST remain ACTIVE (never illegal CANDIDATE); zero shadow candidate docs.
            assert conn.execute(
                "SELECT lifecycle_state FROM expansion.listings WHERE listing_id=%s",
                (ids["listing_id"],),
            ).fetchone() == ("ACTIVE",)
            assert conn.execute(
                "SELECT status,version FROM expansion.candidate_sites WHERE candidate_site_id=%s",
                (reviewed["candidate_site_id"],),
            ).fetchone() == ("NEW", 1)
            assert conn.execute(
                "SELECT count(*) FROM odp_runtime.durable_documents WHERE collection LIKE '%%listing.candidates%%'"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT count(*) FROM workflow.outbox_events WHERE tenant_id=%s AND event_type='candidate.created'",
                (TENANT,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM workflow.outbox_events WHERE tenant_id=%s AND event_type='sitescore.requested'",
                (TENANT,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM audit.audit_events WHERE tenant_id=%s AND audit_event_id=%s",
                (TENANT, reviewed["audit_event_id"]),
            ).fetchone()[0] == 1
    finally:
        bundle.engine.close()

    # Cold read after engine close/reopen.
    reopened = _native_bundle(intake_db, worm_dir)
    try:
        loaded = reopened.canonical_intakes().get_promotion(TENANT, reviewed["promotion_decision_id"])
        assert loaded is not None and loaded["status"] == "SCORE_QUEUED"
        assert loaded["candidate_site_id"] == reviewed["candidate_site_id"]
        assert loaded["site_score_job_id"] == reviewed["site_score_job_id"]
        assert loaded["audit_event_id"] == reviewed["audit_event_id"]
        by_intake = reopened.canonical_intakes().get_promotion_for_intake(TENANT, ids["intake_id"])
        assert by_intake == loaded
        candidates = reopened.listing_repository_for_tenant(TENANT).list_candidates()
        assert len(candidates) == 1
        assert candidates[0].candidate_site.candidate_site_id == reviewed["candidate_site_id"]
        assert candidates[0].listing.listing_status == "candidate"
    finally:
        reopened.engine.close()


def test_native_sql_promotion_denials_no_shadow_fallback_and_duplicate_conflict(intake_db, tmp_path):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from modules.listing.domain.intake_states import DomainValidationError

    bundle = _native_bundle(intake_db, tmp_path / "synthetic-native-denials-worm")
    try:
        ids = _seed_native_ready_intake(intake_db)
        canonical = bundle.canonical_intakes()
        score_jobs = IntakeScoreJobRepository(bundle)

        # 1. Unavailable target_format_code fails closed.
        for bad_target in ("FORMAT-A", "oday_g2", " ODAY_G2 "):
            with pytest.raises(DomainValidationError, match="HARD_RULE_POLICY_UNAVAILABLE"):
                canonical.request_promotion(
                    TENANT,
                    MANAGER,
                    intake_id=ids["intake_id"],
                    expected_intake_version=3,
                    target_format_code=bad_target,
                    reason="Synthetic unavailable target",
                    gate_snapshot_sha256="a" * 64,
                    idempotency_key=f"synthetic-bad-{bad_target}",
                )

        # 2. Hard-rule failure (2F floor) denied without persisting promotion.
        bad_floor_ids = _seed_native_ready_intake(intake_db, h3_index="8929a1d4d63ffff", floor="2F")
        with pytest.raises(DomainValidationError, match="hard rule failures"):
            canonical.request_promotion(
                TENANT,
                MANAGER,
                intake_id=bad_floor_ids["intake_id"],
                expected_intake_version=3,
                target_format_code="ODAY_G2",
                reason="Synthetic 2F floor rejection",
                gate_snapshot_sha256="a" * 64,
                idempotency_key="synthetic-bad-floor",
            )
        assert canonical.get_promotion_for_intake(TENANT, bad_floor_ids["intake_id"]) is None

        # 3. Missing/revoked SQL address fails closed even if a shadow address exists in durable_documents.
        shadow_repo = bundle.listing_repository_for_tenant(TENANT)
        shadow_addr = AddressLocation(
            address_id=ids["address_id"],
            raw_address="Synthetic shadow address",
            normalized_address="Synthetic shadow address",
            geocode_confidence=0.99,
            h3_res_9="8929a1d4d67ffff",
        )
        shadow_repo._store.put(shadow_repo._ADDRESSES, ids["address_id"], shadow_addr)
        with intake_db.connect() as conn:
            conn.execute("DELETE FROM core.address_locations WHERE address_id=%s", (ids["address_id"],))
        with pytest.raises(DomainValidationError, match="NATIVE_ADDRESS_UNAVAILABLE"):
            canonical.request_promotion(
                TENANT,
                MANAGER,
                intake_id=ids["intake_id"],
                expected_intake_version=3,
                target_format_code="ODAY_G2",
                reason="Synthetic missing SQL address with shadow doc present",
                gate_snapshot_sha256="a" * 64,
                idempotency_key="synthetic-no-shadow-fallback",
            )
        assert canonical.get_promotion_for_intake(TENANT, ids["intake_id"]) is None

        # 4. Self-review, foreign tenant, version drift, and duplicate active (property_id, target_format_code).
        good_ids = _seed_native_ready_intake(intake_db, h3_index="8929a1d4d6bffff")
        proposed = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=good_ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic native request",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-good-req",
        )
        with pytest.raises(DomainValidationError, match="SELF_REVIEW_DENIED"):
            canonical.review_promotion(
                TENANT,
                MANAGER,
                promotion_decision_id=proposed["promotion_decision_id"],
                expected_version=proposed["version"],
                decision="APPROVE",
                reason="Synthetic self review",
                risk_acknowledged=True,
                idempotency_key="synthetic-self-review",
                score_dispatcher=score_jobs,
            )
        assert canonical.get_promotion(str(uuid4()), proposed["promotion_decision_id"]) is None

        # Mutate address revision between request and review -> PROMOTION_GATE_INPUT_CHANGED.
        with intake_db.connect() as conn:
            conn.execute(
                "UPDATE core.address_locations SET revision=revision+1 WHERE address_id=%s",
                (good_ids["address_id"],),
            )
        with pytest.raises(DomainValidationError, match="PROMOTION_GATE_INPUT_CHANGED"):
            canonical.review_promotion(
                TENANT,
                APPROVER,
                promotion_decision_id=proposed["promotion_decision_id"],
                expected_version=proposed["version"],
                decision="APPROVE",
                reason="Synthetic drift review",
                risk_acknowledged=True,
                idempotency_key="synthetic-drift-review",
                score_dispatcher=score_jobs,
            )
        # Restore address revision and approve, then test duplicate active candidate on same property + ODAY_G2.
        with intake_db.connect() as conn:
            conn.execute(
                "UPDATE core.address_locations SET revision=1 WHERE address_id=%s",
                (good_ids["address_id"],),
            )
        approved = canonical.review_promotion(
            TENANT,
            APPROVER,
            promotion_decision_id=proposed["promotion_decision_id"],
            expected_version=proposed["version"],
            decision="APPROVE",
            reason="Synthetic valid review",
            risk_acknowledged=True,
            idempotency_key="synthetic-valid-review",
            score_dispatcher=score_jobs,
        )
        assert approved["status"] == "SCORE_QUEUED"
        dup_ids = _seed_native_ready_intake(
            intake_db,
            h3_index="8929a1d4d6bffff",
            property_id=good_ids["property_id"],
            address_id=good_ids["address_id"],
        )
        with pytest.raises(DomainValidationError, match="DUPLICATE_CANDIDATE"):
            canonical.request_promotion(
                TENANT,
                MANAGER,
                intake_id=dup_ids["intake_id"],
                expected_intake_version=3,
                target_format_code="ODAY_G2",
                reason="Synthetic duplicate property+format promotion",
                gate_snapshot_sha256="a" * 64,
                idempotency_key="synthetic-dup-prop-format",
            )
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("fault", ["audit", "outbox", "queue"])
def test_native_sql_promotion_review_fault_rolls_back_all_sql_artifacts(intake_db, tmp_path, monkeypatch, fault):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _native_bundle(intake_db, tmp_path / f"synthetic-native-fault-{fault}")
    try:
        ids = _seed_native_ready_intake(intake_db)
        canonical = bundle.canonical_intakes()
        score_jobs = IntakeScoreJobRepository(bundle)
        proposed = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic fault rollback request",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-fault-req",
        )
        if fault == "audit":
            original = canonical.graph.audit_sink.persist

            def failing_audit(event, scope):
                res = original(event, scope)
                if event.get("action") == "promotion.score_queued":
                    raise RuntimeError("Synthetic native audit fault")
                return res

            monkeypatch.setattr(canonical.graph.audit_sink, "persist", failing_audit)
        elif fault == "outbox":
            original_outbox = canonical.graph._outbox

            def failing_outbox(event):
                original_outbox(event)
                if event.event_type == "candidate.created":
                    raise RuntimeError("Synthetic native outbox fault")

            monkeypatch.setattr(canonical.graph, "_outbox", failing_outbox)
        else:
            def failing_enqueue(*_args, **_kwargs):
                raise RuntimeError("Synthetic native score queue fault")

            monkeypatch.setattr(score_jobs, "enqueue", failing_enqueue)

        with pytest.raises(RuntimeError, match="Synthetic native"):
            canonical.review_promotion(
                TENANT,
                APPROVER,
                promotion_decision_id=proposed["promotion_decision_id"],
                expected_version=proposed["version"],
                decision="APPROVE",
                reason="Synthetic fault review",
                risk_acknowledged=True,
                idempotency_key="synthetic-fault-rev",
                score_dispatcher=score_jobs,
            )
        after = canonical.get_promotion(TENANT, proposed["promotion_decision_id"])
        assert after["status"] == "PENDING_REVIEW"
        assert after["candidate_site_id"] is None and after["site_score_job_id"] is None
        assert bundle.listing_repository_for_tenant(TENANT).list_candidates() == []
        with intake_db.connect() as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
            assert conn.execute(
                "SELECT count(*) FROM workflow.jobs WHERE tenant_id=%s AND job_type='intake-sitescore'",
                (TENANT,),
            ).fetchone()[0] == 0
    finally:
        bundle.engine.close()


def test_native_sql_completion_audit_hydration_for_rejected_completed_and_tampered(intake_db, tmp_path):
    from modules.listing.application.intake_completion import (
        CompletionRequirement,
        IntakeWorkIncomplete,
        verify_completion,
    )
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository
    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime

    worm_dir = tmp_path / "synthetic-native-completion-worm"
    bundle = _native_bundle(intake_db, worm_dir)
    try:
        canonical = bundle.canonical_intakes()
        score_jobs = IntakeScoreJobRepository(bundle)

        # 1. REJECTED native promotion hydrates completion audit cleanly.
        rej_ids = _seed_native_ready_intake(intake_db, h3_index="8929a1d4d63ffff")
        p_rej = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=rej_ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic reject completion test",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-comp-rej-req",
        )
        r_rej = canonical.review_promotion(
            TENANT,
            APPROVER,
            promotion_decision_id=p_rej["promotion_decision_id"],
            expected_version=p_rej["version"],
            decision="REJECT",
            reason="Synthetic independent rejection",
            risk_acknowledged=True,
            idempotency_key="synthetic-comp-rej-rev",
        )
        assert r_rej["status"] == "REJECTED"
        rej_req = CompletionRequirement.parse({
            "kind": "promotion",
            "allowed_states": ["REJECTED"],
            "minimum_count": 1,
            "require_audit": True,
        })
        with bundle.canonical_identity_graph().transaction(TENANT):
            st_rej = canonical.workflow_command_state(TENANT, rej_ids["intake_id"])
            proofs = verify_completion(st_rej, st_rej.intakes[rej_ids["intake_id"]], [rej_req])
            assert len(proofs) == 1 and proofs[0]["state"] == "REJECTED"

        # 2. Approved promotion distinguishes SCORE_QUEUED -> COMPLETED -> score SUCCEEDED + immutable report.
        ids = _seed_native_ready_intake(intake_db, h3_index="8929a1d4d67ffff")
        p_ok = canonical.request_promotion(
            TENANT,
            MANAGER,
            intake_id=ids["intake_id"],
            expected_intake_version=3,
            target_format_code="ODAY_G2",
            reason="Synthetic completion test",
            gate_snapshot_sha256="a" * 64,
            idempotency_key="synthetic-comp-ok-req",
        )
        r_ok = canonical.review_promotion(
            TENANT,
            APPROVER,
            promotion_decision_id=p_ok["promotion_decision_id"],
            expected_version=p_ok["version"],
            decision="APPROVE",
            reason="Synthetic independent approval",
            risk_acknowledged=True,
            idempotency_key="synthetic-comp-ok-rev",
            score_dispatcher=score_jobs,
        )
        promo_req = CompletionRequirement.parse({
            "kind": "promotion",
            "allowed_states": ["COMPLETED"],
            "minimum_count": 1,
            "require_audit": True,
        })
        job_req = CompletionRequirement.parse({
            "kind": "job",
            "allowed_states": ["SUCCEEDED"],
            "minimum_count": 1,
            "require_audit": True,
            "action": "intake-sitescore",
        })
        with bundle.canonical_identity_graph().transaction(TENANT):
            st_queued = canonical.workflow_command_state(TENANT, ids["intake_id"])
            with pytest.raises(IntakeWorkIncomplete):
                verify_completion(st_queued, st_queued.intakes[ids["intake_id"]], [promo_req])
            with pytest.raises(IntakeWorkIncomplete):
                verify_completion(st_queued, st_queued.intakes[ids["intake_id"]], [job_req])

        # Execute worker -> promotion COMPLETED AND score job SUCCEEDED with immutable report.
        claimed = _claim_native_score_job(bundle, r_ok["site_score_job_id"])
        handle_intake_score(claimed, bundle, model_runtime=RecordingRuntime())
        with bundle.canonical_identity_graph().transaction(TENANT):
            st_done = canonical.workflow_command_state(TENANT, ids["intake_id"])
            both_proofs = verify_completion(st_done, st_done.intakes[ids["intake_id"]], [promo_req, job_req])
            assert {item["resource_type"] for item in both_proofs} == {"promotion", "job"}

        # 3. Missing immutable report breaks job completion proof even when workflow.jobs says SUCCEEDED.
        report_id = st_done.jobs[r_ok["site_score_job_id"]]["result"]["report_id"]
        bundle.engine.execute(
            "DELETE FROM durable_documents WHERE doc_id=?",
            (report_id,),
        )
        with bundle.canonical_identity_graph().transaction(TENANT):
            st_tampered = canonical.workflow_command_state(TENANT, ids["intake_id"])
            # Promotion acceptance proof still holds, while score job completion proof fails closed.
            assert verify_completion(st_tampered, st_tampered.intakes[ids["intake_id"]], [promo_req])
            with pytest.raises(IntakeWorkIncomplete):
                verify_completion(st_tampered, st_tampered.intakes[ids["intake_id"]], [job_req])
    finally:
        bundle.engine.close()
