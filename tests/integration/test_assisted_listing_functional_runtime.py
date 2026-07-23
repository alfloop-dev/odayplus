from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.assisted_intake import RetrievalResult
from modules.external_data.security.assisted_listing_retrieval import FetchResponse
from modules.opsboard.application.network_listings import NetworkListingService
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.jobs.queue import InMemoryJobQueue, JobStatus

TENANT_ID = "00000000-0000-0000-0000-000000000001"
SUBMITTER = "00000000-0000-0000-0000-000000000101"
REVIEWER = "00000000-0000-0000-0000-000000000102"


class _JsonHandler(BaseHTTPRequestHandler):
    payload: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802
        encoded = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


@contextmanager
def _json_origin(payload: dict[str, Any]) -> Iterator[str]:
    handler = type("RuntimeJsonHandler", (_JsonHandler,), {"payload": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/listing"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class _ApprovedHttpJsonProvider:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.requested_urls: list[str] = []

    def __call__(self, canonical_url: str, *, policy: Any) -> RetrievalResult:
        assert policy.policy == "APPROVED_RETRIEVAL"
        self.requested_urls.append(canonical_url)
        with urllib.request.urlopen(self.origin, timeout=2) as response:
            raw = json.loads(response.read())
        return RetrievalResult(
            snapshot_id=str(uuid4()),
            captured_at="2026-07-23T12:00:00Z",
            raw=raw,
        )


def _repo(db_path: str):
    bundle = _durable_bundle(db_path)
    repository = DurableAssistedIntakeRepository(SqliteDocumentStore(bundle.engine))
    return bundle, repository


def _api_headers(
    subject: str,
    *,
    key: str | None = None,
    reviewer: bool = False,
) -> dict[str, str]:
    headers = {
        "x-subject-id": subject,
        "x-tenant-id": TENANT_ID,
        "x-roles": ("site_reviewer,data_owner,expansion_user" if reviewer else "expansion_user"),
    }
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _submit_and_process(
    service: NetworkListingService,
    *,
    queue: InMemoryJobQueue,
    url: str,
    raw: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    intake = service.submit_intake(
        url=url,
        heat_zone_id="HZ-01",
        actor_role_id="expansionStaff",
        actor_name=SUBMITTER,
        idempotency_key=f"submit:{uuid4()}",
        correlation_id=str(uuid4()),
        job_queue=queue,
        async_intake=True,
        tenant_id=TENANT_ID,
        intake_id=str(uuid4()),
    )
    job = queue.claim_next("runtime-test-worker")
    assert job is not None
    assert job.status == JobStatus.RUNNING

    with _json_origin(raw) as origin:
        provider = _ApprovedHttpJsonProvider(origin)
        processed = service.process_queued_intake(
            intake_id=intake["id"],
            retrieval_provider=provider,
            correlation_id=job.correlation_id,
            attempt=job.attempts,
        )
        assert provider.requested_urls == [url]
    assert queue.complete(job.job_id)
    return processed, job


def test_canonical_api_submit_runs_through_production_worker_and_persisted_get(
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _durable_bundle(str(tmp_path / "api-worker.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)
    raw = {
        "source_listing_id": "synthetic-worker-1001",
        "title": "核准來源測試店面",
        "address_raw": "台北市中山區南京東路二段 100 號 1F",
        "rent_amount": 68000,
        "area_ping": 22.0,
        "floor": "1F",
        "listing_type": "店面",
        "listing_status": "active",
    }

    from modules.external_data.security import assisted_listing_retrieval

    monkeypatch.setattr(
        assisted_listing_retrieval,
        "_resolve_host",
        lambda _host: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        assisted_listing_retrieval.DefaultRetrievalFetcher,
        "__call__",
        lambda _self, _url, *, timeout_seconds, max_response_bytes: FetchResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            body=json.dumps(raw).encode(),
        ),
    )

    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://www.synthetic.example/detail-99001001.html",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(SUBMITTER, key=f"api-worker-{uuid4()}"),
    )
    assert submit.status_code == 202
    receipt = submit.json()
    assert receipt["job_id"]
    assert ODayWorker(persistence=bundle).run_once() is True

    detail = client.get(
        f"/api/v1/intakes/{receipt['intake_id']}",
        headers=_api_headers(SUBMITTER),
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["state"] in {"READY", "NEEDS_REVIEW"}
    stages = [transition["to_state"] for transition in body["processing_history"]]
    assert stages == [
        "SUBMITTED",
        "CHECKING_IDENTITY",
        "CHECKING_SOURCE_POLICY",
        "RETRIEVING",
        "PARSING",
        "MATCHING",
        body["state"],
    ]
    persisted = NetworkListingService(
        listing_repository=bundle.listing_repository,
        intake_repository=DurableAssistedIntakeRepository(
            SqliteDocumentStore(bundle.engine)
        ),
    ).get_intake(receipt["intake_id"])
    assert persisted["stage"] == body["state"]
    assert persisted["snapshotId"]
    assert persisted["parserVersion"]
    assert bundle.job_queue.get(receipt["job_id"]).status == JobStatus.SUCCEEDED
    bundle.engine.close()


def test_canonical_promotion_persists_candidate_and_sitescore_job(
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _durable_bundle(str(tmp_path / "canonical-promotion.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)
    raw = {
        "source_listing_id": "synthetic-promotion-1001",
        "address_raw": "台中市西屯區台灣大道三段 900 號 1F",
        "rent_amount": 88000,
        "area_ping": 30,
        "floor": "1F",
        "listing_type": "店面",
        "listing_status": "active",
    }
    from modules.external_data.security import assisted_listing_retrieval

    monkeypatch.setattr(
        assisted_listing_retrieval,
        "_resolve_host",
        lambda _host: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        assisted_listing_retrieval.DefaultRetrievalFetcher,
        "__call__",
        lambda _self, _url, *, timeout_seconds, max_response_bytes: FetchResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            body=json.dumps(raw).encode(),
        ),
    )
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://www.synthetic.example/detail-99001002.html",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(SUBMITTER, key=f"promotion-submit-{uuid4()}"),
    ).json()
    assert ODayWorker(persistence=bundle).run_once() is True

    service = NetworkListingService(
        listing_repository=bundle.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    )
    processed = service.get_intake(submitted["intake_id"])
    created = service.decide_intake(
        intake_id=processed["id"],
        action="create",
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        reason="Create the reviewed Listing before candidate promotion.",
        risk_summary="Creates one Listing with append-only intake lineage.",
        risk_acknowledged=True,
        idempotency_key=f"promotion-create-{uuid4()}",
        correlation_id=str(uuid4()),
    )
    request_promotion = client.post(
        f"/api/v1/intakes/{processed['id']}/promotion-requests",
        json={
            "target_format_code": "FORMAT-A",
            "reason": "Promote the reviewed Listing to Candidate Site.",
            "gate_snapshot_sha256": "a" * 64,
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(SUBMITTER, key=f"promotion-request-{uuid4()}"),
            "If-Match": f'W/"{created["version"]}"',
        },
    )
    assert request_promotion.status_code == 202, request_promotion.text
    promotion = request_promotion.json()
    reviewed = client.post(
        f"/api/v1/promotion-decisions/{promotion['promotion_decision_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Independent manager confirms gate evidence.",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                REVIEWER,
                key=f"promotion-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": f'W/"{promotion["version"]}"',
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    receipt = reviewed.json()
    assert receipt["status"] == "COMPLETED"
    assert receipt["candidate_site_id"]
    assert receipt["site_score_job_id"]
    detail = client.get(
        f"/api/v1/intakes/{processed['id']}",
        headers=_api_headers(REVIEWER, reviewer=True),
    ).json()
    assert detail["lifecycle"]["promotion"]["candidate_site_id"] == (
        receipt["candidate_site_id"]
    )
    assert detail["lifecycle"]["job"]["job_id"] == receipt["site_score_job_id"]
    bundle.engine.close()


def test_backend_bootstrap_and_detail_authorize_all_six_role_modes() -> None:
    app = create_app()
    client = TestClient(app)
    roles = (
        "expansion-staff",
        "expansion-manager",
        "data-steward",
        "governance-reviewer",
        "privacy-officer",
        "permission-limited",
    )

    def role_headers(role: str, subject: str) -> dict[str, str]:
        return {
            "x-subject-id": subject,
            "x-tenant-id": TENANT_ID,
            "x-roles": role,
            "x-operator-role": role,
        }

    for index, role in enumerate(roles, start=1):
        response = client.get(
            "/api/v1/operator/bootstrap",
            headers={
                **role_headers(
                    role,
                    f"00000000-0000-0000-0000-{index:012d}",
                ),
                "x-tenant-id": "tenant-a",
            },
        )
        assert response.status_code == 200, (role, response.text)
        assert response.json()["meta"]["role"]["id"] == role

    submitter = "00000000-0000-0000-0000-000000000201"
    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://unregistered.example/listing/role-check",
            "scope": {"tenant_id": TENANT_ID},
            "purpose": "Governed assisted-intake role and masking verification.",
        },
        headers={
            **role_headers("expansion-manager", submitter),
            "Idempotency-Key": f"role-submit-{uuid4()}",
        },
    )
    assert submit.status_code == 202
    intake_id = submit.json()["intake_id"]

    for index, role in enumerate(roles, start=11):
        subject = (
            submitter
            if role == "expansion-staff"
            else f"00000000-0000-0000-0000-{index:012d}"
        )
        detail = client.get(
            f"/api/v1/intakes/{intake_id}",
            headers=role_headers(role, subject),
        )
        assert detail.status_code == 200, (role, detail.text)
        body = detail.json()
        facts = body["lifecycle"]["actor_facts"]
        assert facts["role_mode"] == role
        assert "VIEW" in facts["allowed_actions"]
        if role in {"governance-reviewer", "privacy-officer"}:
            assert facts["purpose"]["required"] is True
            assert facts["purpose"]["bound"] is True
        if role == "permission-limited":
            assert facts["allowed_actions"] == ["VIEW"]
            assert body["original_url"] is None
            assert "original_url" in body["masked_fields"]


def test_structured_batch_is_durable_and_detail_exposes_lineage_evidence(
    tmp_path,
) -> None:
    bundle = _durable_bundle(str(tmp_path / "structured-batch.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)
    batch_id = str(uuid4())
    response = client.post(
        "/api/v1/intake-batches",
        json={
            "batch_id": batch_id,
            "method": "MANUAL",
            "scope": {
                "tenant_id": TENANT_ID,
                "heat_zone_id": "00000000-0000-0000-0000-000000000301",
                "assigned_area_id": "00000000-0000-0000-0000-000000000302",
            },
            "rows": [
                {
                    "address_raw": "台北市信義區測試路 100 號 1F",
                    "area_ping": 20,
                    "rent_amount": 65000,
                    "floor": "1F",
                    "source_id": "manual.operator",
                    "source_listing_id": "manual-100",
                }
            ],
        },
        headers={
            **_api_headers(REVIEWER, key=f"batch-{uuid4()}", reviewer=True),
            "x-operator-role": "expansion-manager",
        },
    )
    assert response.status_code == 202, response.text
    intake_id = response.json()["rows"][0]["intake_id"]

    for store in AssistedIntakeStore._instances:
        store.intakes.pop(intake_id, None)
    detail = client.get(
        f"/api/v1/intakes/{intake_id}",
        headers=_api_headers(REVIEWER, reviewer=True),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["intake_method"] == "MANUAL"
    assert body["state"] in {"READY", "NEEDS_REVIEW"}
    assert body["scope"]["assigned_area_id"] == (
        "00000000-0000-0000-0000-000000000302"
    )
    assert body["evidence"]["parser_version"] == "structured-intake-v1"
    assert body["evidence"]["freshness_state"] == "NOT_CAPTURED"
    assert body["audit"][0]["actor"] == REVIEWER
    assert body["audit"][0]["correlation_id"]
    assert next(
        field for field in body["fields"] if field["field_path"] == "address"
    )["parser_version"] == "structured-intake-v1"
    persisted = next(
        intake
        for intake in app.state.operator_intake_repository.list_intakes()
        if intake["id"] == intake_id
    )
    assert persisted["stage"] == body["state"]
    bundle.engine.close()


def test_canonical_inbox_filters_saved_views_bootstrap_and_claim_contract() -> None:
    app = create_app()
    client = TestClient(app)
    headers = {
        **_api_headers(REVIEWER),
        "x-roles": "site_reviewer,expansion_user",
        "x-operator-role": "expansion-manager",
    }
    store = AssistedIntakeStore._instances[-1]

    def intake(
        intake_id: str,
        *,
        state: str,
        method: str,
        source_id: str,
        heat_zone_id: str,
        assigned_area_id: str,
        observed_at: str,
        updated_at: str,
        restricted: bool = False,
        retryable: bool = False,
    ) -> dict[str, Any]:
        classification = "RESTRICTED" if restricted else "INTERNAL"
        return {
            "intake_id": intake_id,
            "id": intake_id,
            "state": state,
            "intake_method": method,
            "source_id": source_id,
            "match_outcome": (
                "QUARANTINED"
                if state == "QUARANTINED"
                else ("POSSIBLE_MATCH" if state == "FAILED" else "NEW")
            ),
            "submitted_by": REVIEWER,
            "assigned_to": None,
            "submitted_at": "2026-07-23T09:00:00Z",
            "updated_at": updated_at,
            "last_observed_at": observed_at,
            "version": 3,
            "scope": {
                "tenant_id": TENANT_ID,
                "heat_zone_id": heat_zone_id,
                "assigned_area_id": assigned_area_id,
            },
            "original_url": f"https://example.com/{intake_id}",
            "canonical_url": f"https://example.com/{intake_id}",
            "fields": [
                {
                    "field_path": "address",
                    "classification": classification,
                    "masked": False,
                    "effective": f"台北市測試路 {intake_id[-1]} 號",
                }
            ],
            "restricted_data": restricted,
            "runtime_record": {
                "capturedAt": observed_at,
                "failure": (
                    {
                        "code": "PARSER_TIMEOUT",
                        "retryable": retryable,
                        "nextAction": "RETRY",
                    }
                    if state == "FAILED"
                    else None
                ),
            },
        }

    ready_id = "10000000-0000-0000-0000-000000000001"
    quarantined_id = "10000000-0000-0000-0000-000000000002"
    failed_id = "10000000-0000-0000-0000-000000000003"
    store.intakes[ready_id] = intake(
        ready_id,
        state="READY",
        method="URL",
        source_id="source.ready",
        heat_zone_id="HZ-A",
        assigned_area_id="A-01",
        observed_at="2026-07-23T10:00:00Z",
        updated_at="2026-07-23T10:05:00Z",
    )
    store.intakes[quarantined_id] = intake(
        quarantined_id,
        state="QUARANTINED",
        method="MANUAL",
        source_id="source.quarantined",
        heat_zone_id="HZ-B",
        assigned_area_id="A-02",
        observed_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:05:00Z",
        restricted=True,
    )
    store.intakes[failed_id] = intake(
        failed_id,
        state="FAILED",
        method="CSV",
        source_id="source.failed",
        heat_zone_id="HZ-C",
        assigned_area_id="A-03",
        observed_at="2026-07-23T14:00:00Z",
        updated_at="2026-07-23T14:05:00Z",
        retryable=True,
    )
    store.assignments.update(
        {
            "A-200": {
                "assignment_id": "A-200",
                "intake_id": quarantined_id,
                "tenant_id": TENANT_ID,
                "status": "CLAIMED",
                "owner_subject_id": REVIEWER,
                "queue_id": "intake-review",
                "due_at": "2026-07-24T12:00:00Z",
                "version": 2,
                "updated_at": "2026-07-23T12:10:00Z",
            },
            "A-300": {
                "assignment_id": "A-300",
                "intake_id": failed_id,
                "tenant_id": TENANT_ID,
                "status": "ESCALATED",
                "owner_subject_id": SUBMITTER,
                "queue_id": "data-steward",
                "due_at": "2026-07-23T13:00:00Z",
                "version": 3,
                "updated_at": "2026-07-23T14:10:00Z",
            },
        }
    )
    store.slas.update(
        {
            "S-200": {
                "sla_instance_id": "S-200",
                "intake_id": quarantined_id,
                "state": "OVERDUE",
                "due_at": "2026-07-24T12:00:00Z",
                "version": 2,
                "updated_at": "2026-07-23T12:10:00Z",
            },
            "S-300": {
                "sla_instance_id": "S-300",
                "intake_id": failed_id,
                "state": "BREACHED",
                "due_at": "2026-07-23T13:00:00Z",
                "version": 3,
                "updated_at": "2026-07-23T14:10:00Z",
            },
        }
    )

    def ids(**params: Any) -> set[str]:
        response = client.get(
            "/api/v1/intakes",
            params=params,
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return {row["intake_id"] for row in response.json()["items"]}

    assert ids(intake_method="MANUAL") == {quarantined_id}
    assert ids(owner_subject_id=REVIEWER) == {quarantined_id}
    assert ids(assignment_status="CLAIMED") == {quarantined_id}
    assert ids(assigned="true") == {quarantined_id, failed_id}
    assert ids(sla_state="OVERDUE") == {quarantined_id}
    assert ids(
        observed_from="2026-07-23T11:30:00Z",
        observed_to="2026-07-23T12:30:00Z",
    ) == {quarantined_id}
    assert ids(
        updated_from="2026-07-23T11:30:00Z",
        updated_to="2026-07-23T12:30:00Z",
    ) == {quarantined_id}
    assert ids(heat_zone_id="HZ-B") == {quarantined_id}
    assert ids(assigned_area_id="A-02") == {quarantined_id}
    assert ids(restricted_data="true") == {quarantined_id}
    assert ids(quarantined="true") == {quarantined_id}
    assert ids(failed="true") == {failed_id}
    assert ids(retryable="true") == {failed_id}

    create_view = client.post(
        "/api/v1/saved-views",
        json={
            "name": "可重試失敗",
            "query": {"failed": True, "retryable": True},
            "resource": "intake",
            "visibility": "PRIVATE",
        },
        headers={
            **headers,
            "Idempotency-Key": f"saved-view-{uuid4()}",
        },
    )
    assert create_view.status_code == 201, create_view.text
    saved_view = create_view.json()
    assert ids(saved_view_id=saved_view["saved_view_id"]) == {failed_id}

    inbox = client.get(
        "/api/v1/intakes",
        params={"quarantined": "true"},
        headers=headers,
    ).json()["items"][0]
    assert inbox["issue"] == "QUARANTINED"
    assert inbox["next_action"] == "REVIEW_QUARANTINE"
    assert inbox["original_url"] == f"https://example.com/{quarantined_id}"
    assert inbox["canonical_url"] == f"https://example.com/{quarantined_id}"
    assert inbox["owner_subject_id"] == REVIEWER
    assert inbox["assignment_status"] == "CLAIMED"
    assert inbox["sla_state"] == "OVERDUE"
    assert inbox["last_observed_at"] == "2026-07-23T12:00:00Z"
    assert inbox["location"]["heat_zone_id"] == "HZ-B"
    assert inbox["masking"]["restricted_data"] is True

    bootstrap = client.get("/api/v1/intakes/bootstrap", headers=headers)
    assert bootstrap.status_code == 200, bootstrap.text
    bootstrap_body = bootstrap.json()
    assert bootstrap_body["role_mode"] == "expansion-manager"
    assert bootstrap_body["heat_zones"]
    assert any(
        view["saved_view_id"] == saved_view["saved_view_id"]
        for view in bootstrap_body["saved_views"]
    )
    assert bootstrap_body["commands"]["claim"] == {
        "method": "POST",
        "path_template": "/api/v1/assignments/{assignment_id}/actions/claim",
        "requires_if_match": True,
        "requires_idempotency_key": True,
    }
    listed_views = client.get("/api/v1/saved-views", headers=headers)
    assert listed_views.status_code == 200
    assert any(
        view["saved_view_id"] == saved_view["saved_view_id"]
        for view in listed_views.json()
    )

    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://unregistered.example/listing/claim-contract",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers={
            **headers,
            "Idempotency-Key": f"claim-submit-{uuid4()}",
        },
    )
    assert submit.status_code == 202, submit.text
    submitted = submit.json()
    assignment = client.put(
        f"/api/v1/intakes/{submitted['intake_id']}/assignment",
        json={
            "owner_subject_id": REVIEWER,
            "owner_role": "expansion-manager",
            "due_at": "2026-07-24T16:00:00Z",
            "reason": "Assign canonical Inbox work.",
        },
        headers={
            **headers,
            "Idempotency-Key": f"claim-assign-{uuid4()}",
            "If-Match": f'W/"{submitted["version"]}"',
        },
    )
    assert assignment.status_code == 200, assignment.text
    assignment_body = assignment.json()
    claim = client.post(
        f"/api/v1/assignments/{assignment_body['assignment_id']}/actions/claim",
        json={"reason": "Claim from canonical Inbox."},
        headers={
            **headers,
            "Idempotency-Key": f"claim-command-{uuid4()}",
            "If-Match": f'W/"{assignment_body["version"]}"',
        },
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["status"] == "CLAIMED"
    detail = client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["lifecycle"]["assignment"]["status"] == "CLAIMED"
    assert detail.json()["lifecycle"]["assignment_history"][-1]["action"] == "CLAIM"


def test_assignment_sla_saved_view_and_replay_survive_api_restart(tmp_path) -> None:
    db_path = str(tmp_path / "lifecycle-restart.sqlite3")
    headers = {
        **_api_headers(REVIEWER, reviewer=True),
        "x-operator-role": "expansion-manager",
    }
    assignment_key = f"restart-assign-{uuid4()}"
    assignment_request = {
        "owner_subject_id": REVIEWER,
        "owner_role": "expansion-manager",
        "queue_id": "intake-review",
        "due_at": "2026-07-24T16:00:00Z",
        "reason": "Persist assignment and SLA across an API restart.",
    }

    first_bundle = _durable_bundle(db_path)
    first_client = TestClient(create_app(persistence=first_bundle))
    submitted = first_client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://unregistered.example/listing/restart-contract",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers={
            **headers,
            "Idempotency-Key": f"restart-submit-{uuid4()}",
        },
    ).json()
    assigned = first_client.put(
        f"/api/v1/intakes/{submitted['intake_id']}/assignment",
        json=assignment_request,
        headers={
            **headers,
            "Idempotency-Key": assignment_key,
            "If-Match": f'W/"{submitted["version"]}"',
        },
    )
    assert assigned.status_code == 200, assigned.text
    assignment = assigned.json()
    saved = first_client.post(
        "/api/v1/saved-views",
        json={
            "name": "跨重啟待審",
            "query": {"assignment_status": "ASSIGNED"},
            "resource": "intake",
            "visibility": "PRIVATE",
        },
        headers={
            **headers,
            "Idempotency-Key": f"restart-view-{uuid4()}",
        },
    )
    assert saved.status_code == 201, saved.text
    saved_view_id = saved.json()["saved_view_id"]
    first_detail = first_client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=headers,
    ).json()
    sla_instance_id = first_detail["lifecycle"]["sla"]["sla_instance_id"]
    first_history_size = len(first_detail["lifecycle"]["assignment_history"])
    first_bundle.engine.close()

    second_bundle = _durable_bundle(db_path)
    second_client = TestClient(create_app(persistence=second_bundle))
    replayed = second_client.put(
        f"/api/v1/intakes/{submitted['intake_id']}/assignment",
        json=assignment_request,
        headers={
            **headers,
            "Idempotency-Key": assignment_key,
            "If-Match": f'W/"{submitted["version"]}"',
        },
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json() == assignment

    listed_views = second_client.get("/api/v1/saved-views", headers=headers)
    assert listed_views.status_code == 200
    assert saved_view_id in {
        view["saved_view_id"] for view in listed_views.json()
    }
    restarted_detail = second_client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=headers,
    ).json()
    assert restarted_detail["lifecycle"]["assignment"]["status"] == "ASSIGNED"
    assert restarted_detail["lifecycle"]["sla"]["state"] in {
        "ON_TRACK",
        "DUE_SOON",
        "OVERDUE",
    }
    assert (
        len(restarted_detail["lifecycle"]["assignment_history"])
        == first_history_size
    )

    claimed = second_client.post(
        f"/api/v1/assignments/{assignment['assignment_id']}/actions/claim",
        json={"reason": "Claim after restart."},
        headers={
            **headers,
            "Idempotency-Key": f"restart-claim-{uuid4()}",
            "If-Match": f'W/"{assignment["version"]}"',
        },
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "CLAIMED"
    paused = second_client.post(
        f"/api/v1/sla-instances/{sla_instance_id}/actions/pause",
        json={
            "reason": "Pause while waiting for corrected source evidence.",
            "expected_resume_at": "2026-07-24T10:00:00Z",
        },
        headers={
            **headers,
            "Idempotency-Key": f"restart-pause-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "PAUSED"
    second_bundle.engine.close()

    third_bundle = _durable_bundle(db_path)
    third_client = TestClient(create_app(persistence=third_bundle))
    final_detail = third_client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=headers,
    ).json()
    assert final_detail["lifecycle"]["assignment"]["status"] == "CLAIMED"
    assert final_detail["lifecycle"]["sla"]["state"] == "PAUSED"
    resumed = third_client.post(
        f"/api/v1/sla-instances/{sla_instance_id}/actions/resume",
        json={"reason": "Corrected evidence is available."},
        headers={
            **headers,
            "Idempotency-Key": f"restart-resume-{uuid4()}",
            "If-Match": f'W/"{paused.json()["version"]}"',
        },
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] in {"ON_TRACK", "DUE_SOON", "OVERDUE"}
    third_bundle.engine.close()


def test_queued_http_runtime_persists_legal_history_and_revision_readback(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "runtime.sqlite3")
    bundle, repository = _repo(db_path)
    queue = InMemoryJobQueue()
    service = NetworkListingService(intake_repository=repository)
    before_listing = service._listing("L-2024").copy()
    raw = {
        "source_listing_id": "synthetic-2024",
        "title": "信義松仁路臨路一樓店面降價",
        "address_raw": "台北市信義區松仁路 96 號 1F",
        "rent_amount": 55000,
        "area_ping": 18.0,
        "floor": "1F 臨路",
        "listing_type": "店面",
        "listing_status": "active",
        "confidence": 0.94,
    }
    processed, job = _submit_and_process(
        service,
        queue=queue,
        url="https://www.synthetic.example/detail-88520242.html",
        raw=raw,
    )

    assert processed["stage"] == "READY"
    assert processed["matchResult"]["outcome"] == "REVISION"
    assert [item["toStage"] for item in processed["processingHistory"]] == [
        "SUBMITTED",
        "CHECKING_IDENTITY",
        "CHECKING_SOURCE_POLICY",
        "RETRIEVING",
        "PARSING",
        "MATCHING",
        "READY",
    ]
    assert all(
        "checkpoint" in item
        and "attempt" in item
        and "timeoutSeconds" in item
        and "failure" in item
        for item in processed["processingHistory"]
    )
    assert queue.get(job.job_id).status == JobStatus.SUCCEEDED

    decided = service.decide_intake(
        intake_id=processed["id"],
        action="revise",
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        reason="租金已由來源證據更新，保留既有 Listing 歷史。",
        risk_summary="Append immutable ListingRevision.",
        risk_acknowledged=True,
        target_listing_id="L-2024",
        idempotency_key=f"revision:{processed['id']}",
        correlation_id=str(uuid4()),
    )
    assert decided["latestDecisionReceipt"]["listingRevisionId"]
    assert service._listing("L-2024")["rentPerMonth"] == before_listing["rentPerMonth"]

    restarted = NetworkListingService(intake_repository=repository)
    persisted = restarted.get_intake(processed["id"])
    revisions = restarted.list_listing_revisions("L-2024")
    edges = restarted.list_identity_edges(listing_id="L-2024", intake_id=processed["id"])
    assert persisted["latestDecisionReceipt"]["listingRevisionId"] == revisions[-1]["revisionId"]
    assert revisions[-1]["beforeValues"]["rentPerMonth"] == 58000
    assert revisions[-1]["effectiveValues"]["rentPerMonth"] == 55000
    assert edges[-1]["relation"] == "REVISION_OF"
    assert restarted.snapshot()["listings"][0]["rentPerMonth"] == 55000
    assert restarted._listing("L-2024")["rentPerMonth"] == 58000
    bundle.engine.close()


def test_identity_graph_reversal_and_quarantine_effects_survive_readback(
    tmp_path,
) -> None:
    bundle, repository = _repo(str(tmp_path / "identity.sqlite3"))
    service = NetworkListingService(intake_repository=repository)
    correlation_id = str(uuid4())

    proposed = service.propose_identity_decision(
        tenant_id=TENANT_ID,
        action="merge",
        plan={
            "sourcePropertyIds": ["property-source"],
            "targetPropertyId": "property-target",
            "relatedIds": {
                "sourcePropertyIds": ["property-source"],
                "targetPropertyId": "property-target",
            },
            "evidenceState": "COMPLETE",
        },
        actor_role_id="dataSteward",
        actor_name=SUBMITTER,
        reason="Consolidate two verified identities after source review.",
        risk_acknowledged=True,
        correlation_id=correlation_id,
    )
    reviewed = service.review_identity_decision(
        tenant_id=TENANT_ID,
        decision_id=proposed["decisionId"],
        approve=True,
        reviewer_role_id="expansionManager",
        reviewer_name=REVIEWER,
        reason="Independent review confirms the merge plan.",
        risk_acknowledged=True,
        correlation_id=correlation_id,
    )
    assert reviewed["status"] == "EXECUTED"
    assert reviewed["effectReceipt"]["identityEdgeIds"]

    reversal = service.request_identity_reversal(
        tenant_id=TENANT_ID,
        original_decision_id=proposed["decisionId"],
        actor_role_id="dataSteward",
        actor_name=SUBMITTER,
        reason="New evidence requires reversal of the merge.",
        correlation_id=correlation_id,
    )
    reversed_decision = service.review_identity_decision(
        tenant_id=TENANT_ID,
        decision_id=reversal["decisionId"],
        approve=True,
        reviewer_role_id="expansionManager",
        reviewer_name=REVIEWER,
        reason="Independent reviewer accepts the reversal evidence.",
        risk_acknowledged=True,
        correlation_id=correlation_id,
    )
    assert reversed_decision["status"] == "EXECUTED"

    quarantined = service.submit_intake(
        url="https://unknown.example/listing/1",
        heat_zone_id=None,
        actor_role_id="expansionStaff",
        actor_name=SUBMITTER,
        idempotency_key=f"quarantine:{uuid4()}",
        correlation_id=correlation_id,
        tenant_id=TENANT_ID,
        intake_id=str(uuid4()),
    )
    assert quarantined["stage"] == "QUARANTINED"
    proposed_release = service.propose_quarantine_release(
        intake_id=quarantined["id"],
        actor_role_id="dataSteward",
        actor_name=SUBMITTER,
        reason="Governance review requested before manual review.",
        correlation_id=correlation_id,
    )
    assert proposed_release["stage"] == "QUARANTINED"
    assert proposed_release["pendingQuarantineRelease"]["proposer"] == SUBMITTER
    released = service.release_quarantine(
        intake_id=quarantined["id"],
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        reason="Governance approved manual review.",
        correlation_id=correlation_id,
    )
    assert released["stage"] == "NEEDS_REVIEW"
    rejected = service.decide_intake(
        intake_id=quarantined["id"],
        action="reject",
        actor_role_id="expansionManager",
        actor_name=SUBMITTER,
        reason="Source evidence remains insufficient after review.",
        risk_summary="Reject without creating a Listing.",
        risk_acknowledged=True,
        idempotency_key=f"reject:{quarantined['id']}",
        correlation_id=correlation_id,
    )
    assert rejected["latestDecisionReceipt"]["decision"] == "REJECT"

    restarted = NetworkListingService(intake_repository=repository)
    graph_edges = restarted.list_global_identity_edges(
        tenant_id=TENANT_ID,
        include_superseded=True,
    )
    effective_edges = restarted.list_global_identity_edges(
        tenant_id=TENANT_ID,
        include_superseded=False,
    )
    assert any(edge["relation"] == "MERGED_INTO" for edge in graph_edges)
    assert any(edge["relation"] == "REVERSAL_OF" for edge in effective_edges)
    assert (
        restarted.get_identity_decision(
            tenant_id=TENANT_ID,
            decision_id=reversal["decisionId"],
        )["status"]
        == "EXECUTED"
    )
    assert restarted.get_intake(quarantined["id"])["stage"] == "FAILED"
    bundle.engine.close()


def test_failure_retry_and_cancel_are_persisted_queue_transitions(tmp_path) -> None:
    bundle, repository = _repo(str(tmp_path / "recovery.sqlite3"))
    queue = InMemoryJobQueue()
    service = NetworkListingService(intake_repository=repository)
    intake = service.submit_intake(
        url="https://www.synthetic.example/detail-70000001.html",
        heat_zone_id="HZ-01",
        actor_role_id="expansionStaff",
        actor_name=SUBMITTER,
        idempotency_key=f"recovery:{uuid4()}",
        correlation_id=str(uuid4()),
        job_queue=queue,
        async_intake=True,
        tenant_id=TENANT_ID,
        intake_id=str(uuid4()),
    )
    job = queue.claim_next("recovery-worker")
    assert job is not None

    def fail_retrieval(_url: str, *, policy: Any) -> RetrievalResult:
        assert policy.policy == "APPROVED_RETRIEVAL"
        raise TimeoutError("approved provider timed out")

    failed = service.process_queued_intake(
        intake_id=intake["id"],
        retrieval_provider=fail_retrieval,
        correlation_id=job.correlation_id,
        attempt=job.attempts,
        timeout_seconds=9,
    )
    assert failed["stage"] == "FAILED"
    assert failed["processingHistory"][-1]["checkpoint"] == "RETRIEVING"
    assert failed["processingHistory"][-1]["timeoutSeconds"] == 9
    assert failed["processingHistory"][-1]["failure"]["retryable"] is True
    assert queue.fail(job.job_id)

    retried = service.retry_intake(
        intake_id=intake["id"],
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        correlation_id=str(uuid4()),
        job_queue=queue,
        tenant_id=TENANT_ID,
    )
    assert retried["stage"] == "SUBMITTED"
    assert retried["processingHistory"][-1]["reasonCode"] == "RETRY_QUEUED"
    assert queue.get(job.job_id).status == JobStatus.QUEUED

    cancelled = service.cancel_intake(
        intake_id=intake["id"],
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        reason="Operator cancelled before the retry was claimed.",
        correlation_id=str(uuid4()),
        job_queue=queue,
    )
    assert cancelled["stage"] == "CANCELLED"
    assert queue.get(job.job_id).status == JobStatus.CANCELLED

    restarted = NetworkListingService(intake_repository=repository)
    readback = restarted.get_intake(intake["id"])
    assert readback["stage"] == "CANCELLED"
    assert [row["toStage"] for row in readback["processingHistory"]][-3:] == [
        "FAILED",
        "SUBMITTED",
        "CANCELLED",
    ]
    bundle.engine.close()


def test_canonical_api_revision_review_and_persisted_readback() -> None:
    app = create_app()
    client = TestClient(app)
    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": ("https://www.synthetic.example/detail-88520242.html"),
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(
            SUBMITTER,
            key=f"canonical-submit-{uuid4()}",
        ),
    )
    assert submit.status_code == 202
    receipt = submit.json()
    assert receipt["state"] == "SUBMITTED"
    assert receipt["job_id"]

    runtime = NetworkListingService(
        listing_repository=app.state.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    )
    processed = runtime.process_queued_intake(
        intake_id=receipt["intake_id"],
        retrieval_provider=lambda _url, *, policy: RetrievalResult(
            snapshot_id=str(uuid4()),
            captured_at="2026-07-23T12:30:00Z",
            raw={
                "source_listing_id": "synthetic-2024",
                "address_raw": "台北市信義區松仁路 96 號 1F",
                "rent_amount": 55000,
                "area_ping": 18.0,
                "floor": "1F 臨路",
                "listing_type": "店面",
                "listing_status": "active",
            },
        ),
    )
    assert processed["matchResult"]["outcome"] == "REVISION"

    for active_store in AssistedIntakeStore._instances:
        active_store.intakes.pop(receipt["intake_id"], None)
    detail = client.get(
        f"/api/v1/intakes/{receipt['intake_id']}",
        headers=_api_headers(SUBMITTER),
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["state"] == "READY"
    assert detail_body["match_case_id"]
    assert detail_body["match_case_version"] == 1
    assert detail_body["match_case"]["outcome"] == "REVISION"
    assert detail_body["match_case"]["comparison_fields"]
    assert detail_body["match_case"]["signals"]
    assert (
        detail_body["match_case"]["graph_plan"]["plan_type"]
        == "APPEND_LISTING_REVISION"
    )
    assert detail_body["lifecycle"]["etag"] == detail.headers["etag"]
    assert detail_body["lifecycle"]["actor_facts"]["role_mode"] == "expansion-staff"
    assert "VIEW" in detail_body["lifecycle"]["actor_facts"]["allowed_actions"]
    match_case = client.get(
        f"/api/v1/match-cases/{detail_body['match_case_id']}",
        headers=_api_headers(SUBMITTER),
    )
    assert match_case.status_code == 200
    assert match_case.headers["etag"] == 'W/"1"'
    assert match_case.json() == detail_body["match_case"]

    proposal = client.post(
        f"/api/v1/match-cases/{detail_body['match_case_id']}/decisions",
        json={
            "decision_type": "REVISE",
            "reason": "來源快照確認租金下降，提出 immutable revision。",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                SUBMITTER,
                key=f"canonical-revise-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": f'W/"{detail_body["match_case_version"]}"',
        },
    )
    assert proposal.status_code == 201
    proposal_body = proposal.json()
    decision_id = proposal_body["decision_id"]
    assert proposal_body["graph_plan"]["plan_id"]
    assert proposal_body["graph_plan"]["before_graph"]["version"] >= 0
    assert proposal_body["graph_plan"]["after_graph"]["nodes"]
    assert proposal_body["graph_plan"]["expected_graph_version"] >= 0
    assert proposal_body["graph_plan"]["lineage_impact"]["append_only"] is True
    assert proposal_body["graph_plan"]["proposer"]["subject_id"] == SUBMITTER

    reviewed = client.post(
        f"/api/v1/identity-decisions/{decision_id}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "第二人覆核來源證據與目標 Listing，准予新增版本。",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                REVIEWER,
                key=f"canonical-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": 'W/"1"',
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "EXECUTED"

    revisions = client.get(
        "/api/v1/listings/L-2024/revisions",
        headers=_api_headers(REVIEWER, reviewer=True),
    )
    assert revisions.status_code == 200
    revision_rows = revisions.json()["revisions"]
    assert revision_rows[-1]["effectiveValues"]["rentPerMonth"] == 55000
    assert app.state.listing_repository.get_listing("L-2024").rent_amount == 58000

    edge_readback = client.get(
        "/api/v1/identity/edges",
        params={
            "listing_id": "L-2024",
            "intake_id": receipt["intake_id"],
        },
        headers=_api_headers(REVIEWER, reviewer=True),
    )
    assert edge_readback.status_code == 200
    assert edge_readback.json()["edges"][-1]["relation"] == "REVISION_OF"

    duplicate_submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": ("https://www.synthetic.example/detail-88520242.html"),
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(
            SUBMITTER,
            key=f"canonical-duplicate-{uuid4()}",
        ),
    )
    assert duplicate_submit.status_code == 200
    duplicate_receipt = duplicate_submit.json()
    assert duplicate_receipt["intake_id"] == receipt["intake_id"]
    assert duplicate_receipt["duplicate_hint"] == "L-2024"
    assert duplicate_receipt["identity_outcome"] == "EXACT_DUPLICATE"
    assert duplicate_receipt["existing_listing_id"] == "L-2024"
    assert duplicate_receipt["navigation_target"].endswith("/L-2024")
    assert duplicate_receipt["submission_receipt_id"]
    persisted_duplicate = app.state.operator_intake_repository.intakes[receipt["intake_id"]][
        "submissionReceipt"
    ]
    assert persisted_duplicate["existingListingId"] == "L-2024"
    assert persisted_duplicate["navigationTarget"].endswith("/L-2024")


def test_canonical_api_quarantine_release_requires_persisted_second_actor(
    tmp_path,
) -> None:
    bundle = _durable_bundle(str(tmp_path / "quarantine-worker.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)
    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://unregistered.example/listing/99",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(
            SUBMITTER,
            key=f"canonical-quarantine-{uuid4()}",
            reviewer=True,
        ),
    )
    assert submit.status_code == 202
    submitted = submit.json()
    assert submitted["state"] == "SUBMITTED"
    assert submitted["job_id"]
    assert ODayWorker(persistence=bundle).run_once() is True
    quarantined = client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=_api_headers(SUBMITTER, reviewer=True),
    )
    assert quarantined.status_code == 200
    assert quarantined.json()["state"] == "QUARANTINED"
    quarantined_version = quarantined.json()["version"]

    proposal = client.post(
        f"/api/v1/intakes/{submitted['intake_id']}/actions/reopen",
        json={
            "reason": "Request governance release into an explicitly reviewed manual path.",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                SUBMITTER,
                key=f"canonical-release-proposal-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": f'W/"{quarantined_version}"',
        },
    )
    assert proposal.status_code == 200, proposal.text
    proposal_receipt = proposal.json()
    assert proposal_receipt["from_state"] == "QUARANTINED"
    assert proposal_receipt["to_state"] == "QUARANTINED"
    assert proposal_receipt["reason_code"] == "SECOND_ACTOR_REQUIRED"

    self_review = client.post(
        f"/api/v1/intakes/{submitted['intake_id']}/actions/reopen",
        json={
            "reason": "The proposer must not approve the same release.",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                SUBMITTER,
                key=f"canonical-release-self-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": f'W/"{proposal_receipt["version_after"]}"',
        },
    )
    assert self_review.status_code == 403
    assert "SELF_REVIEW_DENIED" in self_review.text

    approved = client.post(
        f"/api/v1/intakes/{submitted['intake_id']}/actions/reopen",
        json={
            "reason": "Independent reviewer accepts the governance evidence.",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                REVIEWER,
                key=f"canonical-release-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": f'W/"{proposal_receipt["version_after"]}"',
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["to_state"] == "NEEDS_REVIEW"

    for active_store in AssistedIntakeStore._instances:
        active_store.intakes.pop(submitted["intake_id"], None)
    readback = client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=_api_headers(REVIEWER, reviewer=True),
    )
    assert readback.status_code == 200
    assert readback.json()["state"] == "NEEDS_REVIEW"
    persisted = NetworkListingService(
        listing_repository=bundle.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    ).get_intake(submitted["intake_id"])
    assert "pendingQuarantineRelease" not in persisted
    assert persisted["lastQuarantineRelease"]["reviewer"] == REVIEWER
    bundle.engine.close()


def test_canonical_identity_correction_is_applied_only_after_persisted_review() -> None:
    app = create_app()
    client = TestClient(app)
    submit = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://www.synthetic.example/detail-70000077.html",
            "scope": {"tenant_id": TENANT_ID},
        },
        headers=_api_headers(
            SUBMITTER,
            key=f"canonical-correction-submit-{uuid4()}",
        ),
    )
    assert submit.status_code == 202
    submitted = submit.json()
    runtime = NetworkListingService(
        listing_repository=app.state.listing_repository,
        intake_repository=app.state.operator_intake_repository,
    )
    processed = runtime.process_queued_intake(
        intake_id=submitted["intake_id"],
        retrieval_provider=lambda _url, *, policy: RetrievalResult(
            snapshot_id=str(uuid4()),
            captured_at="2026-07-23T13:00:00Z",
            raw={
                "source_listing_id": "synthetic-other",
                "address_raw": "台北市信義區松仁路 96 號 1F",
                "rent_amount": 61000,
                "area_ping": 18.0,
                "floor": "1F 臨路",
                "listing_type": "店面",
                "listing_status": "active",
            },
        ),
    )
    assert processed["stage"] == "NEEDS_REVIEW"
    assert processed["matchResult"]["outcome"] == "POSSIBLE_MATCH"

    proposal = client.post(
        f"/api/v1/intakes/{submitted['intake_id']}/corrections",
        json={
            "field_path": "address",
            "corrected_value": "台北市信義區松仁路 98 號 1F",
            "reason": "來源快照與門牌證據顯示地址需調整。",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                SUBMITTER,
                key=f"canonical-correction-proposal-{uuid4()}",
            ),
            "If-Match": f'W/"{processed["version"]}"',
        },
    )
    assert proposal.status_code == 201, proposal.text
    proposal_body = proposal.json()
    assert proposal_body["status"] == "PENDING_REVIEW"
    persisted_before_review = app.state.operator_intake_repository.intakes[
        submitted["intake_id"]
    ]
    assert (
        persisted_before_review["parsedFields"]["address"]["correctedValue"]
        is None
    )

    self_review_correlation = str(uuid4())
    self_review = client.post(
        f"/api/v1/identity-decisions/{proposal_body['correction_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "提案者不得覆核自己的 identity correction。",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                SUBMITTER,
                key=f"canonical-correction-self-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": 'W/"1"',
            "x-correlation-id": self_review_correlation,
        },
    )
    assert self_review.status_code == 403
    assert "SELF_REVIEW_DENIED" in self_review.text
    authoritative_error = self_review.json()
    assert authoritative_error["code"] == "SELF_REVIEW_DENIED"
    assert authoritative_error["correlation_id"] == self_review_correlation
    assert authoritative_error["occurred_at"].endswith("Z")
    assert authoritative_error["retryable"] is False
    assert authoritative_error["current_version"] is None
    assert authoritative_error["next_action"] == "REQUEST_ACCESS"

    review = client.post(
        f"/api/v1/identity-decisions/{proposal_body['correction_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "第二人比對快照與門牌證據後核准校正。",
            "risk_acknowledged": True,
        },
        headers={
            **_api_headers(
                REVIEWER,
                key=f"canonical-correction-review-{uuid4()}",
                reviewer=True,
            ),
            "If-Match": 'W/"1"',
        },
    )
    assert review.status_code == 200, review.text
    assert review.json()["status"] == "EXECUTED"

    for active_store in AssistedIntakeStore._instances:
        active_store.intakes.pop(submitted["intake_id"], None)
    readback = client.get(
        f"/api/v1/intakes/{submitted['intake_id']}",
        headers=_api_headers(REVIEWER, reviewer=True),
    )
    assert readback.status_code == 200
    address = next(
        field for field in readback.json()["fields"] if field["field_path"] == "address"
    )
    assert address["corrected"] == "台北市信義區松仁路 98 號 1F"
    persisted_after_review = app.state.operator_intake_repository.intakes[
        submitted["intake_id"]
    ]
    correction = persisted_after_review["correctionProposals"][-1]
    assert correction["status"] == "APPLIED"
    assert correction["reviewer"] == REVIEWER
    latest_audit = persisted_after_review["auditEvents"][-1]
    assert latest_audit["metadata"]["before"]["version"] < latest_audit["metadata"]["after"][
        "version"
    ]
    assert latest_audit["metadata"]["sourceSnapshotId"]
    assert latest_audit["metadata"]["parserVersion"]
    assert latest_audit["metadata"]["evidenceState"] == "COMPLETE"


def test_new_possible_match_and_assisted_entry_effects_survive_readback(tmp_path) -> None:
    bundle, repository = _repo(str(tmp_path / "core-flows.sqlite3"))
    service = NetworkListingService(intake_repository=repository)
    queue = InMemoryJobQueue()

    new_intake, _new_job = _submit_and_process(
        service,
        queue=queue,
        url="https://www.synthetic.example/detail-70001001.html",
        raw={
            "source_listing_id": "synthetic-new",
            "address_raw": "台中市西屯區台灣大道三段 999 號 9F",
            "rent_amount": 125000,
            "area_ping": 60.0,
            "floor": "9F",
            "listing_type": "辦公室",
            "listing_status": "active",
        },
    )
    assert new_intake["matchResult"]["outcome"] == "NEW"
    created = service.decide_intake(
        intake_id=new_intake["id"],
        action="create",
        actor_role_id="expansionManager",
        actor_name=REVIEWER,
        reason="No reliable identity or entity match; create a new Listing.",
        risk_summary="Create one Listing and preserve the source edge.",
        risk_acknowledged=True,
        idempotency_key=f"new-decision:{uuid4()}",
        correlation_id=str(uuid4()),
    )
    created_listing_id = created["latestDecisionReceipt"]["listingId"]
    assert created_listing_id
    assert (
        service.list_identity_edges(
            listing_id=created_listing_id,
            intake_id=new_intake["id"],
        )[-1]["relation"]
        == "SOURCE_OF"
    )

    possible, _possible_job = _submit_and_process(
        service,
        queue=queue,
        url="https://www.synthetic.example/detail-70001002.html",
        raw={
            "source_listing_id": "synthetic-other",
            "address_raw": "台北市信義區松仁路 96 號 1F",
            "rent_amount": 61000,
            "area_ping": 18.0,
            "floor": "1F 臨路",
            "listing_type": "店面",
            "listing_status": "active",
        },
    )
    assert possible["stage"] == "NEEDS_REVIEW"
    assert possible["matchResult"]["outcome"] == "POSSIBLE_MATCH"
    proposed = service.propose_identity_decision(
        tenant_id=TENANT_ID,
        action="match_decision",
        plan={
            "intakeId": possible["id"],
            "decisionType": "DUPLICATE",
            "targetListingId": "L-2024",
            "sourceSnapshotId": possible["snapshotId"],
            "parserVersion": possible["parserVersion"],
            "relatedIds": {
                "intakeId": possible["id"],
                "listingId": "L-2024",
            },
            "evidenceState": "COMPLETE",
        },
        actor_role_id="dataSteward",
        actor_name=SUBMITTER,
        reason="Entity signals require a human duplicate decision.",
        risk_acknowledged=True,
        correlation_id=str(uuid4()),
    )
    duplicate = service.review_identity_decision(
        tenant_id=TENANT_ID,
        decision_id=proposed["decisionId"],
        approve=True,
        reviewer_role_id="expansionManager",
        reviewer_name=REVIEWER,
        reason="Independent reviewer confirms the duplicate edge.",
        risk_acknowledged=True,
        correlation_id=str(uuid4()),
    )
    assert duplicate["status"] == "EXECUTED"
    assert duplicate["effectReceipt"]["runtimeReceipt"]["identityEdgeId"]

    assisted = service.submit_intake(
        url="https://www.591.com.tw/rent-detail-999999.html",
        heat_zone_id="HZ-01",
        actor_role_id="expansionStaff",
        actor_name=SUBMITTER,
        idempotency_key=f"assisted:{uuid4()}",
        correlation_id=str(uuid4()),
        tenant_id=TENANT_ID,
        intake_id=str(uuid4()),
    )
    assert assisted["stage"] == "AWAITING_ASSISTED_ENTRY"
    completed_entry = service.correct_intake(
        intake_id=assisted["id"],
        fields={
            "address": "高雄市左營區博愛三路 100 號 1F",
            "rent": 72000,
            "areaPing": 24.0,
        },
        reason="Operator transcribed required values from approved evidence.",
        risk_summary="Apply attributable assisted-entry values and rerun matching.",
        risk_acknowledged=True,
        actor_role_id="dataSteward",
        actor_name=REVIEWER,
        idempotency_key=f"assisted-correction:{uuid4()}",
        correlation_id=str(uuid4()),
    )
    assert completed_entry["stage"] in {"READY", "NEEDS_REVIEW"}
    assert completed_entry["auditEvents"][-1]["metadata"]["before"]["version"] < (
        completed_entry["auditEvents"][-1]["metadata"]["after"]["version"]
    )

    restarted = NetworkListingService(intake_repository=repository)
    assert restarted.get_intake(new_intake["id"])["latestDecisionReceipt"]["listingId"] == (
        created_listing_id
    )
    assert restarted.get_intake(possible["id"])["latestDecisionReceipt"]["decision"] == (
        "DUPLICATE"
    )
    persisted_assisted = restarted.get_intake(assisted["id"])
    assert persisted_assisted["parsedFields"]["address"]["correctedValue"]
    assert persisted_assisted["parsedFields"]["address"]["correctionActor"] == REVIEWER
    bundle.engine.close()
