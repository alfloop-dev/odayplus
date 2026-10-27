"""Prove the production operator factory writes one durable V1 workflow owner."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import listings
from apps.api.app.routes.operator import create_operator_router
from modules.opsboard.application import network_listings
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.intake_registry_fixture import (
    AT,
    MANAGER,
    OWNER,
    TENANT,
    _audit_retention_row,
    _calendar_row,
    _workflow_row,
)
from tests.integration.test_intake_registry_calendar_postgresql import (
    _bundle,
    _insert_policy,
    _seed_identities,
    _upgrade,
)
from tests.support.listing_search_integration import execute_claimed_listing_search

pytestmark = pytest.mark.requires_live_env
BASE = "/api/v1/operator/network-listings"


def _headers(actor=MANAGER, tenant=TENANT, key="synthetic-operator-submit"):
    return {
        "x-subject-id": actor, "x-tenant-id": tenant,
        "x-roles": "site_reviewer,expansion_user" if actor == MANAGER else "expansion_user",
        "x-operator-role": "expansion-manager" if actor == MANAGER else "expansion-staff",
        "x-correlation-id": str(uuid4()), "Idempotency-Key": key,
    }


def _client(bundle, *, workflow=True, policy=True):
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    app.include_router(create_operator_router(
        audit_log=bundle.audit_log, document_store=bundle.assisted_intake_store._store,
        listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        sitescore_repository_for_tenant=lambda tenant: bundle._scoped_repository("sitescore_repository", tenant),
        sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
        avm_repository_for_tenant=lambda tenant: bundle._scoped_repository("avm_repository", tenant),
        netplan_repository_for_tenant=lambda tenant: bundle._scoped_repository("netplan_repository", tenant),
        priceops_repository_for_tenant=lambda tenant: bundle._scoped_repository("priceops_repository", tenant),
        intake_workflow_store=bundle.assisted_intake_store if workflow else None,
        intake_workflow_policy=bundle.assisted_intake_workflow_policy if policy else None,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True,
        persistence_mode="postgresql", provider_mode="live",
    ), prefix="/api/v1")
    app.include_router(listings.create_assisted_intake_router(
        bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy,
    ), prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def bridge_database(intake_blank_db, monkeypatch):
    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    policy = _workflow_row()
    parameters = deepcopy(policy.parameters)
    parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, replace(policy, parameters=parameters))
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT.astimezone(tz or UTC)

    monkeypatch.setattr(network_listings, "datetime", Clock)
    monkeypatch.setattr(listings, "datetime", Clock)
    return db


def _snapshot(bundle):
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    return deepcopy(tuple(getattr(store, field) for field in (
        "intakes", "assignments", "slas", "workflow_audits", "workflow_outbox", "replays",
        "corrections", "decisions",
    )))


@pytest.mark.parametrize("method", ["MANUAL", "CSV", "APPROVED_FEED"])
def test_batch_declared_rows_survive_new_postgresql_owner_without_source_approval(
    bridge_database, monkeypatch, method,
):
    db = bridge_database
    batch_id = str(uuid4())
    body = {
        "batch_id": batch_id, "method": method, "scope": {"tenant_id": TENANT},
        "rows": [
            {"address_raw": "Synthetic zero row", "rent_amount": 0, "area_ping": 0,
             "currency": "USD", "floor": "1F", "source_id": "submitted.feed.claim",
             "source_listing_id": "submitted-listing-zero"},
            {"address_raw": ""},
            {"address_raw": "Synthetic nullable row", "rent_amount": None,
             "area_ping": None, "currency": "EUR", "floor": None,
             "source_id": "submitted.manual.claim", "source_listing_id": None},
            {"address_raw": "Synthetic existing defaults"},
        ],
    }
    headers = _headers(key=f"synthetic-retained-batch-{method.lower()}")
    bundle = _bundle(db, monkeypatch)
    try:
        submitted = _client(bundle).post("/api/v1/intake-batches", json=body, headers=headers)
        assert submitted.status_code == 207, submitted.text
        receipt = submitted.json()
        assert (receipt["accepted_count"], receipt["rejected_count"]) == (3, 1)
        retained_ids = [row["intake_id"] for row in receipt["rows"] if row["status"] == "ACCEPTED"]
        committed = _snapshot(bundle)
    finally:
        bundle.engine.close()

    replica = _bundle(db, monkeypatch)
    try:
        client = _client(replica)
        expected_rows = [
            {"row_index": 1, "address_raw": "Synthetic zero row", "rent_amount": 0,
             "area_ping": 0, "currency": "USD", "floor": "1F",
             "source_id": "submitted.feed.claim", "source_listing_id": "submitted-listing-zero"},
            {"row_index": 3, "address_raw": "Synthetic nullable row", "rent_amount": None,
             "area_ping": None, "currency": "EUR", "floor": None,
             "source_id": "submitted.manual.claim", "source_listing_id": None},
            {"row_index": 4, "address_raw": "Synthetic existing defaults", "rent_amount": None,
             "area_ping": None, "currency": "TWD", "floor": None,
             "source_id": "manual.operator", "source_listing_id": None},
        ]
        for intake_id, expected in zip(retained_ids, expected_rows, strict=True):
            response = client.get(f"/api/v1/intakes/{intake_id}", headers=headers)
            assert response.status_code == 200, response.text
            detail = response.json()
            fields = {field["field_path"]: field for field in detail["fields"]}
            expected = {**expected, "batch_id": batch_id}
            assert expected.keys() <= fields.keys()
            for name, value in expected.items():
                assert fields[name]["effective"] == value
                assert fields[name]["parsed"] == value
                assert fields[name]["classification"] == "INTERNAL"
            assert detail["original_url"] is None and detail["canonical_url"] is None
            assert detail["state"] == "SUBMITTED"
            assert detail["policy_state"] == "ASSISTED_ENTRY_ONLY"
            assert detail["source_id"] is None and detail["source_snapshot_id"] is None
            assert detail["parser_run_id"] is None and detail["match_case_id"] is None
            assert detail["assignment_id"] and detail["sla_instance_id"]
            assert client.get(f"/api/v1/intakes/{intake_id}", headers=_headers(tenant=str(uuid4()))).status_code == 404
        assert _snapshot(replica) == committed
        replay = client.post("/api/v1/intake-batches", json=body, headers=headers)
        assert replay.status_code == 207 and replay.json() == receipt
        assert _snapshot(replica) == committed
        changed = deepcopy(body)
        changed["rows"][0]["currency"] = "JPY"
        assert client.post("/api/v1/intake-batches", json=changed, headers=headers).status_code == 409
        assert _snapshot(replica) == committed
    finally:
        replica.engine.close()


@pytest.mark.parametrize("supplied_batch_id", [True, False])
def test_xlsx_row_claim_and_submitted_position_survive_new_postgresql_owner(
    bridge_database, monkeypatch, supplied_batch_id,
):
    db = bridge_database
    batch_id = str(uuid4())
    body = {"batch_id": batch_id, "scope": {"tenant_id": TENANT}, "rows": [
        {"address_raw": "Synthetic sheet zero", "rent_amount": 0, "area_ping": 0,
         "currency": "USD", "source_id": "submitted.sheet.claim", "source_listing_id": "sheet-zero",
         "_row_index": 11, "_submitted_row_index": 999},
        {"address_raw": "", "_row_index": 17},
        {"address_raw": "Synthetic sheet nullable", "rent_amount": None, "area_ping": None,
         "currency": "EUR", "source_id": "submitted.sheet.claim", "source_listing_id": None,
         "_row_index": 21, "_SUBMITTED_ROW_INDEX": 777},
    ]}
    if not supplied_batch_id:
        del body["batch_id"]
    headers = _headers(key="synthetic-retained-sheet-rows")
    bundle = _bundle(db, monkeypatch)
    try:
        response = _client(bundle).post("/api/v1/intake-batches/xlsx/commit", json=body, headers=headers)
        assert response.status_code == 202, response.text
        receipt = response.json()
        if supplied_batch_id:
            assert receipt["batch_id"] == batch_id
        else:
            assert receipt["batch_id"].startswith("xlsx-commit-")
            batch_id = receipt["batch_id"]
        assert (receipt["accepted_count"], receipt["rejected_count"]) == (2, 1)
        committed = _snapshot(bundle)
    finally:
        bundle.engine.close()
    replica = _bundle(db, monkeypatch)
    try:
        client = _client(replica)
        expected_rows = [
            {"row_index": 1, "source_row_index": 11, "rent_amount": 0, "area_ping": 0,
             "currency": "USD", "source_listing_id": "sheet-zero"},
            {"row_index": 3, "source_row_index": 21, "rent_amount": None, "area_ping": None,
             "currency": "EUR", "source_listing_id": None},
        ]
        for intake_id, expected in zip(receipt["intake_ids"], expected_rows, strict=True):
            detail = client.get(f"/api/v1/intakes/{intake_id}", headers=headers).json()
            fields = {field["field_path"]: field for field in detail["fields"]}
            expected = {**expected, "source_id": "submitted.sheet.claim", "batch_id": batch_id}
            assert expected.keys() <= fields.keys()
            assert {name: fields[name]["effective"] for name in expected} == expected
            assert detail["source_id"] is None and detail["policy_state"] == "ASSISTED_ENTRY_ONLY"
        replay = client.post("/api/v1/intake-batches/xlsx/commit", json=body, headers=headers)
        assert replay.status_code == 202 and replay.json() == receipt
        assert _snapshot(replica) == committed
    finally:
        replica.engine.close()


@pytest.mark.parametrize("field_path", ["batch_id", "row_index", "source_row_index"])
def test_batch_provenance_cannot_be_corrected_but_ordinary_field_can(
    bridge_database, monkeypatch, field_path,
):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post("/api/v1/intake-batches/xlsx/commit", json={
            "batch_id": str(uuid4()), "scope": {"tenant_id": TENANT}, "rows": [{
                "address_raw": "Synthetic provenance correction", "floor": "1F", "_row_index": 19,
            }],
        }, headers=_headers(key=f"synthetic-provenance-submit-{field_path}"))
        assert submitted.status_code == 202, submitted.text
        intake_id = submitted.json()["intake_ids"][0]
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        # Isolate the existing correction state, without claiming parser/source approval.
        with store.transaction():
            store.intakes[intake_id]["state"] = "NEEDS_REVIEW"
            store.intakes[intake_id]["version"] += 1
            store.flush()
        before = _snapshot(bundle)
        before_detail = client.get(f"/api/v1/intakes/{intake_id}", headers=_headers()).json()
        rejected = client.post(f"/api/v1/intakes/{intake_id}/corrections", json={
            "field_path": field_path, "corrected_value": "forged batch provenance",
            "reason": "Synthetic provenance negative", "risk_acknowledged": True,
        }, headers={**_headers(key=f"synthetic-provenance-reject-{field_path}"), "If-Match": 'W/"2"'})
        assert rejected.status_code == 422, rejected.text
        assert _snapshot(bundle) == before
        assert "Idempotency-Replayed" not in rejected.headers
        accepted = client.post(f"/api/v1/intakes/{intake_id}/corrections", json={
            "field_path": "floor", "corrected_value": "2F",
            "reason": "Synthetic ordinary correction", "risk_acknowledged": True,
        }, headers={**_headers(key=f"synthetic-ordinary-after-{field_path}"), "If-Match": 'W/"2"'})
        assert accepted.status_code == 201 and accepted.json()["status"] == "APPLIED", accepted.text
        detail = client.get(f"/api/v1/intakes/{intake_id}", headers=_headers()).json()
        fields = {field["field_path"]: field for field in detail["fields"]}
        assert fields["floor"]["parsed"] == "1F" and fields["floor"]["effective"] == "2F"
        assert fields[field_path] == next(
            field for field in before_detail["fields"] if field["field_path"] == field_path
        )
        assert detail["source_id"] is None and detail["policy_state"] == "ASSISTED_ENTRY_ONLY"
    finally:
        bundle.engine.close()


def _synthetic_approved_policy(monkeypatch):
    # An isolated policy double exercises transaction/queue wiring only. It is
    # not an admitted live source, and neither fixture gate nor network is used.
    from modules.external_data.application import assisted_intake

    policy = replace(assisted_intake.resolve_source_policy("https://rent.591.com.tw/test"),
                     policy="APPROVED_RETRIEVAL", may_retrieve=True, quarantines=False)
    monkeypatch.setattr(assisted_intake, "resolve_source_policy", lambda _url: policy)

    def no_snapshot(_self):
        raise AssertionError("Submission/retry must never retrieve synchronously")

    monkeypatch.setattr(network_listings.NetworkListingService, "_get_snapshot_service", no_snapshot)


def _claim_intake_job(bundle, job_id):
    # Canonical audit delivery has higher queue priority. Claim those local
    # synthetic rows without executing any delivery, then take this intake job.
    for _ in range(10):
        job = bundle.job_queue.claim_next(worker_id="isolated-intake-test")
        assert job is not None
        if job.job_id == job_id:
            return job
        if execute_claimed_listing_search(bundle, job):
            continue
        assert job.job_type == "canonical-audit-delivery"
    raise AssertionError("Intake job was not claimable")


def test_production_operator_submit_reads_and_transfers_same_canonical_workflow(bridge_database, monkeypatch):
    db = bridge_database
    bundle = _bundle(db, monkeypatch)
    try:
        client = _client(bundle)
        body = {"url": "https://rent.591.com.tw/bridge-real", "actorName": "spoof@example.invalid"}
        headers = _headers()
        submitted = client.post(f"{BASE}/intake/submit", json=body, headers=headers)
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid = record["id"]
        assert str(UUID(iid)) == iid
        assert record["submittedBy"] == MANAGER
        assert record["submittedAt"] == "2026-10-09T08:30:00Z"
        assert record["lastObservedAt"] is None
        assert record["assignmentStatus"] == "UNASSIGNED" and record["ownerSubjectId"] is None
        assert record["assignmentVersion"] == record["slaVersion"] == 1
        aid, sid = record["assignmentId"], record["slaInstanceId"]
        before = _snapshot(bundle)
        replay = client.post(f"{BASE}/intake/submit", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == record
        assert _snapshot(bundle) == before
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_intakes() == []
        assert len(before[0]) == len(before[1]) == len(before[2]) == 1
        assert before[0][iid]["submitted_by"] == MANAGER
        assert before[1][aid]["intake_id"] == before[2][sid]["intake_id"] == iid
        assert record["dueAt"] == before[2][sid]["due_at"]
        assert "spoof@example.invalid" not in str(record)
        assignment = client.put(f"/api/v1/intakes/{iid}/assignment", json={
            "owner_subject_id": OWNER, "owner_role": "expansion-staff",
            "due_at": record["dueAt"], "reason": "Synthetic bridge assignment",
        }, headers={**_headers(key="synthetic-bridge-assign"), "If-Match": 'W/"1"'})
        assert assignment.status_code == 200, assignment.text
        av = assignment.json()["version"]
        transferred = client.post(f"/api/v1/assignments/{aid}/actions/transfer", json={
            "target_owner_subject_id": MANAGER, "target_owner_role": "site-reviewer",
            "handoff_note": "Synthetic verified handoff",
            "due_at": record["dueAt"], "reason": "Synthetic bridge transfer",
        }, headers={**_headers(OWNER, key="synthetic-bridge-transfer"), "If-Match": f'W/"{av}"'})
        assert transferred.status_code == 200, transferred.text
        final_version = transferred.json()["version"]
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        client = _client(bundle)
        detail = client.get(f"{BASE}/intake/{iid}", headers=_headers())
        assert detail.status_code == 200, detail.text
        record = detail.json()
        assert record["assignmentId"] == aid and record["slaInstanceId"] == sid
        assert record["assignmentVersion"] == final_version
        assert record["ownerSubjectId"] == MANAGER
        page = client.get(f"{BASE}/intake", headers=_headers())
        assert page.status_code == 200, page.text
        rows = page.json()["items"]
        assert len(rows) == 1
        for key in ("id", "submittedBy", "submittedAt", "updatedAt", "lastObservedAt", "ownerSubjectId",
                    "assignmentId", "assignmentVersion", "assignmentStatus", "slaInstanceId", "slaVersion", "dueAt"):
            assert rows[0][key] == record[key]
        other = client.get(f"{BASE}/intake/{iid}", headers=_headers(tenant=str(uuid4())))
        assert other.status_code == 404, other.text
        assert "original_url" not in record and "operator_presentation" not in record
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("failure", ["policy", "outbox", "replay"])
def test_operator_submission_rolls_back_workflow_replay_and_queue(bridge_database, monkeypatch, failure):
    _synthetic_approved_policy(monkeypatch)
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle, policy=failure != "policy")
        before = _snapshot(bundle)
        enqueued = []
        original_enqueue = bundle.job_queue.enqueue

        def enqueue(*args, **kwargs):
            result = original_enqueue(*args, **kwargs)
            if result[0].job_type == "assisted-listing-intake":
                enqueued.append(result[0].job_id)
            return result

        monkeypatch.setattr(bundle.job_queue, "enqueue", enqueue)
        with bridge_database.connect() as conn:
            jobs_before = conn.execute("SELECT count(*) FROM odp_runtime.durable_jobs").fetchone()[0]
        if failure != "policy":
            original = bundle.assisted_intake_store._store.put

            def fail(collection, key, value, **kwargs):
                if ((failure == "outbox" and collection == "assisted_intake.workflow_outbox")
                        or (failure == "replay" and collection.startswith("operator.idempotency_cache"))):
                    raise RuntimeError("synthetic bridge persistence failure")
                return original(collection, key, value, **kwargs)

            monkeypatch.setattr(bundle.assisted_intake_store._store, "put", fail)
        body = {"url": "https://www.synthetic.example/detail-999.html"}
        if failure == "policy":
            response = client.post(f"{BASE}/intake/submit", json=body, headers=_headers())
            assert response.status_code == 503, response.text
        else:
            with pytest.raises(RuntimeError, match="synthetic bridge persistence failure"):
                client.post(f"{BASE}/intake/submit", json=body, headers=_headers())
        assert _snapshot(bundle) == before
        assert len(enqueued) == 1
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_intakes() == []
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records() == []
        with bridge_database.connect() as conn:
            assert conn.execute("SELECT count(*) FROM odp_runtime.durable_jobs").fetchone()[0] == jobs_before
    finally:
        bundle.engine.close()


def test_linked_job_retry_after_restart_is_atomic_and_idempotent(bridge_database, monkeypatch):
    from shared.jobs.queue import JobStatus

    _synthetic_approved_policy(monkeypatch)
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/approved-double"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid, job_id = record["id"], record["jobId"]
        assert bundle.job_queue.get(job_id).payload["intake_id"] == iid
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        with store.transaction():
            store.intakes[iid]["state"] = "FAILED"
            store.intakes[iid]["version"] += 1
            store.flush()
        bundle.job_queue.update_status(job_id, JobStatus.FAILED)
    finally:
        bundle.engine.close()
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        headers = _headers(key="synthetic-linked-retry")
        response = client.post(f"{BASE}/intake/{iid}/retry", json={}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 3 and response.json()["stage"] == "SUBMITTED"
        assert response.json()["jobId"] == job_id
        job_after = bundle.job_queue.get(job_id)
        assert job_after.status == JobStatus.QUEUED
        after = _snapshot(bundle)
        replay = client.post(f"{BASE}/intake/{iid}/retry", json={}, headers=headers)
        assert replay.status_code == 200 and replay.json() == response.json()
        assert _snapshot(bundle) == after
        assert bundle.job_queue.get(job_id).version == job_after.version
    finally:
        bundle.engine.close()


def test_operator_workflow_store_unavailable_returns_503_on_reads(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle, workflow=False)
        for path in ("/intake", f"/intake/{uuid4()}"):
            response = client.get(BASE + path, headers=_headers())
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == "INTAKE_WORKFLOW_STORE_UNAVAILABLE"
    finally:
        bundle.engine.close()


def test_production_worker_failure_is_visible_on_same_canonical_intake(bridge_database, monkeypatch):
    from types import SimpleNamespace

    from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
    from modules.external_data.application import source_snapshots
    from shared.jobs.queue import NonRetryableJobError

    _synthetic_approved_policy(monkeypatch)
    # Constructor double only: the real security gate must still reject the
    # worker's legacy fixture transport, without network or object-store use.
    monkeypatch.setattr(source_snapshots, "build_source_snapshot_service", lambda *_: SimpleNamespace(
        check_source_policy=lambda *_args: "APPROVED_RETRIEVAL",
    ))
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/no-live-adapter"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        job = _claim_intake_job(bundle, record["jobId"])
        assert job is not None and job.job_id == record["jobId"]
        # Synthetic authenticated submission above; production retrieval gate
        # below. Production HTTP authentication is deliberately not weakened.
        with monkeypatch.context() as production:
            production.setenv("ODP_PRODUCT_MODE", "production")
            production.setenv("EXTERNAL_PROVIDER_MODE", "live")
            with pytest.raises(NonRetryableJobError):
                handle_assisted_listing_intake(job, bundle)
        detail = client.get(f"{BASE}/intake/{record['id']}", headers=_headers())
        assert detail.status_code == 200, detail.text
        result = detail.json()
        assert result["stage"] == "FAILED" and result["version"] == record["version"] + 1
        assert result["failure"]["code"] == "ODP-INTAKE-SYNTHETIC-FIXTURE-DISABLED"
        assert result["failure"]["retryable"] is False
        assert result["lastObservedAt"] is None and result["snapshotId"] is None
        assert result["assignmentId"] == record["assignmentId"]
        assert result["slaInstanceId"] == record["slaInstanceId"]
    finally:
        bundle.engine.close()


def test_concurrent_submission_replays_one_workflow(bridge_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    bundle = _bundle(bridge_database, monkeypatch)
    replica = _bundle(bridge_database, monkeypatch)
    try:
        clients = [_client(bundle), _client(replica)]
        barrier = Barrier(2)
        headers = _headers()

        def submit(index):
            barrier.wait(timeout=10)
            return clients[index].post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/concurrent"},
                                       headers=headers)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        assert all(result.status_code == 200 for result in results), [r.text for r in results]
        assert results[0].json() == results[1].json()
        state = _snapshot(bundle)
        assert len(state[0]) == len(state[1]) == len(state[2]) == 1
        assert len(bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records()) == 1
    finally:
        bundle.engine.close()
        replica.engine.close()


def test_operator_correction_and_decision_versions_survive_replay(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/edits"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        initial = submitted.json()
        iid = initial["id"]
        correction_body = {"fields": {"floor": "2"}, "reason": "Synthetic correction",
                           "riskSummary": "Synthetic non-identity edit", "riskAcknowledged": True}
        correction_headers = _headers(key="synthetic-bridge-correction")
        corrected = client.post(f"{BASE}/intake/{iid}/correct", json=correction_body, headers=correction_headers)
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["version"] == 2
        replay = client.post(f"{BASE}/intake/{iid}/correct", json=correction_body, headers=correction_headers)
        assert replay.status_code == 200 and replay.json() == corrected.json()
        decision_body = {"action": "reject", "reason": "Synthetic rejected source evidence",
                         "riskSummary": "Synthetic decision", "riskAcknowledged": True}
        # A distinct reviewer preserves the existing segregation-of-duty rule.
        from tests.intake_registry_fixture import APPROVER

        with bridge_database.connect() as conn:
            for role in (Role.SITE_REVIEWER, Role.EXPANSION_USER):
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,%s)",
                             (APPROVER, role.value, "isolated-synthetic-test"))
        decision_headers = {**_headers(APPROVER, key="synthetic-bridge-decision"),
                            "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager"}
        decided = client.post(f"{BASE}/intake/{iid}/decide", json=decision_body, headers=decision_headers)
        assert decided.status_code == 200, decided.text
        assert decided.json()["version"] == 3 and decided.json()["stage"] == "FAILED"
        replay = client.post(f"{BASE}/intake/{iid}/decide", json=decision_body, headers=decision_headers)
        assert replay.status_code == 200 and replay.json() == decided.json()
        for key in ("assignmentId", "slaInstanceId"):
            assert decided.json()[key] == initial[key]
        assert _snapshot(bundle)[0][iid]["version"] == 3
    finally:
        bundle.engine.close()


def test_parent_authorization_lock_serializes_v1_owner_change(bridge_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from shared.infrastructure.persistence.operator_intake_workflow import (
        OperatorIntakeWorkflowRepository,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    replica = _bundle(bridge_database, monkeypatch)
    try:
        submitted = _client(bundle).post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/locked"},
                                         headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid = record["id"]
        repo = OperatorIntakeWorkflowRepository(bundle.assisted_intake_store,
            bundle.operator_intake_repository_for_tenant(TENANT), TENANT, bundle.assisted_intake_workflow_policy)
        reached_parent = Event()
        original_lock = replica.assisted_intake_store._read_locked

        def observed_lock(collection, key):
            if collection == "operator.assisted_intakes" and key == f"{TENANT}:{iid}":
                reached_parent.set()
            return original_lock(collection, key)

        monkeypatch.setattr(replica.assisted_intake_store, "_read_locked", observed_lock)
        client = _client(replica)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with repo.transaction(intake_id=iid):
                future = pool.submit(client.put, f"/api/v1/intakes/{iid}/assignment", json={
                    "owner_subject_id": OWNER, "owner_role": "expansion-staff",
                    "due_at": record["dueAt"], "reason": "Synthetic serialized ownership change",
                }, headers={**_headers(key="synthetic-locked-assignment"), "If-Match": 'W/"1"'})
                assert reached_parent.wait(timeout=5)
                assert not future.done(), "V1 writer committed across the authorization row lock"
                assert repo.get_intake(iid)["ownerSubjectId"] is None
            response = future.result(timeout=5)
        assert response.status_code == 200, response.text
        assert repo.get_intake(iid)["ownerSubjectId"] == OWNER
    finally:
        bundle.engine.close()
        replica.engine.close()


def test_stale_worker_cannot_publish_after_job_fence_changes(bridge_database, monkeypatch):
    from types import SimpleNamespace

    from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
    from modules.external_data.application import assisted_intake, source_snapshots
    from modules.external_data.security import assisted_listing_retrieval
    from shared.jobs.queue import JobFenceRejectedError

    _synthetic_approved_policy(monkeypatch)
    snapshot = SimpleNamespace(create_snapshot=lambda **_: "synthetic-snapshot")
    monkeypatch.setattr(source_snapshots, "build_source_snapshot_service", lambda *_: snapshot)
    monkeypatch.setattr(assisted_listing_retrieval, "RetrievalSecurityGate", lambda **_: SimpleNamespace(
        fetch=lambda *_args, **_kwargs: SimpleNamespace(ok=True, body=b'{}'),
    ))
    monkeypatch.setattr(assisted_intake, "parse_snapshot", lambda _: {
        key: {"normalizedValue": value, "correctedValue": None}
        for key, value in {"address": "Synthetic address", "rent": 10, "areaPing": 12}.items()
    })
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/fence-double"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        job = _claim_intake_job(bundle, record["jobId"])
        before = _snapshot(bundle)

        def match(**_):
            # Represent another claimant acquiring the lease while MATCHING
            # was computing. The Intake version itself has not changed.
            bundle.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (job.job_id,))
            return assisted_intake.MatchResult(
                outcome="NEW", confidence=1.0, target_listing_id=None,
                signals=(), summary="Synthetic new listing after lease change",
            )

        monkeypatch.setattr(assisted_intake, "match_listing", match)
        with pytest.raises(JobFenceRejectedError):
            handle_assisted_listing_intake(job, bundle)
        assert _snapshot(bundle) == before
        detail = client.get(f"{BASE}/intake/{record['id']}", headers=_headers()).json()
        assert detail["stage"] == "SUBMITTED" and detail["lastObservedAt"] is None
    finally:
        bundle.engine.close()
