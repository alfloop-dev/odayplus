from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import is_record_owner
from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.main import ODayWorker
from modules.opsboard.application.network_listings import (
    NetworkListingConflict,
    NetworkListingService,
)
from shared.auth import Principal
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import InMemoryJobQueue, JobStatus

HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "site_reviewer,expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _write_headers(key: str) -> dict[str, str]:
    return {
        **HEADERS,
        "X-Correlation-Id": f"corr-{key}",
        "Idempotency-Key": f"idem-{key}",
    }


def _advance_submitted_intake(
    client: TestClient,
    submitted: dict,
    *,
    headers: dict[str, str] | None = None,
) -> dict:
    """Run one queued intake through the deterministic, injected test adapter."""

    assert submitted["stage"] == "SUBMITTED"
    queue = client.app.state.job_queue
    job = queue.claim_next(worker_id="contract-intake-worker")
    assert job is not None
    assert job.job_type == "assisted-listing-intake"
    assert job.payload["intake_id"] == submitted["id"]
    assert job.status == JobStatus.RUNNING

    from modules.external_data.application.assisted_intake import retrieve

    service = NetworkListingService(
        listing_repository=client.app.state.listing_repository,
        intake_repository=client.app.state.operator_intake_repository,
    )
    service.process_queued_intake(
        intake_id=submitted["id"],
        retrieval_provider=retrieve,
        correlation_id=job.correlation_id,
        attempt=job.attempts,
    )
    assert queue.complete(job.job_id)

    readback = client.get(
        f"/api/v1/operator/network-listings/intake/{submitted['id']}",
        headers=headers or HEADERS,
    )
    assert readback.status_code == 200, readback.text
    return readback.json()


def test_first_submission_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Submit a new valid synthetic URL
    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-first-submit",
            "Idempotency-Key": "idem-first",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"].startswith("IN-")
    assert data["originalUrl"] == url
    assert data["canonicalUrl"] == url
    assert data["stage"] == "SUBMITTED"
    assert data["policy"] == "APPROVED_RETRIEVAL"
    processed = _advance_submitted_intake(client, data)
    assert processed["stage"] == "READY"
    assert processed["matchResult"]["outcome"] == "NEW"

    # Replay with idempotency key
    replay = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-first-submit",
            "Idempotency-Key": "idem-first",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == data["id"]


def test_listing_inbox_server_query_contract() -> None:
    app = create_app()
    client = TestClient(app)

    created_ids: list[str] = []
    for suffix in ("query-a", "query-b"):
        response = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={
                "url": f"https://www.synthetic.example/detail-{suffix}.html",
                "heatZoneId": "HZ-01",
            },
            headers=_write_headers(suffix),
        )
        assert response.status_code == 200
        assert response.json()["intakeMethod"] == "URL"
        created_ids.append(response.json()["id"])

    page = client.get(
        "/api/v1/operator/network-listings/intake",
        params={
            "page": 1,
            "pageSize": 1,
            "intakeMethod": "URL",
            "sortBy": "id",
            "sortOrder": "asc",
        },
        headers=HEADERS,
    )
    assert page.status_code == 200
    payload = page.json()
    assert payload["page"] == 1
    assert payload["pageSize"] == 1
    assert payload["total"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == min(created_ids)
    assert sum(payload["counts"].values()) == 2
    assert payload["evidenceState"] in {"complete", "partial", "degraded"}

    no_match = client.get(
        "/api/v1/operator/network-listings/intake",
        params={"intakeMethod": "APPROVED_FEED"},
        headers=HEADERS,
    )
    assert no_match.status_code == 200
    assert no_match.json()["items"] == []
    assert no_match.json()["total"] == 0


def test_exact_duplicate_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. First submission -> READY
    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    processed = _advance_submitted_intake(client, submitted)
    assert processed["stage"] == "READY"
    id1 = processed["id"]

    # 2. Second submission -> exact duplicate returned (terminal state idempotency)
    resp2 = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == id1

    # 3. Test URL concurrency check (raising conflict if stage is not terminal)
    service = NetworkListingService()
    queue = InMemoryJobQueue()
    service.submit_intake(
        url=url,
        heat_zone_id="HZ-01",
        actor_role_id="expansionManager",
        actor_name="林曉青",
        idempotency_key="idem-1",
        correlation_id="corr-1",
        job_queue=queue,
        async_intake=True,
    )

    # A second submission cannot race the still-SUBMITTED queued command.
    with pytest.raises(NetworkListingConflict):
        service.submit_intake(
            url=url,
            heat_zone_id="HZ-01",
            actor_role_id="expansionManager",
            actor_name="林曉青",
            idempotency_key="idem-2",
            correlation_id="corr-2",
            job_queue=queue,
            async_intake=True,
        )


def test_changed_price_revision_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Submit a revision URL (L-2024 duplicate but rent is 55000 instead of 58000)
    url = "https://www.synthetic.example/detail-88520242.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    data = _advance_submitted_intake(client, submitted)
    assert data["matchResult"]["outcome"] == "REVISION"
    assert data["matchResult"]["targetListingId"] == "L-2024"

    # Perform decision: action="revise"
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/decide",
        json={
            "action": "revise",
            "reason": "降價更新至 55000",
            "riskSummary": "將以送件版本覆寫既有物件 L-2024 的租金與樓層。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("changed-price-revise"),
    )
    assert decide_resp.status_code == 200
    assert decide_resp.json()["stage"] == "READY"

    # Verify that target listing rent is updated in listings snapshot
    snap_resp = client.get("/api/v1/operator/network-listings", headers=HEADERS)
    listings = snap_resp.json()["listings"]
    l2024 = next(item for item in listings if item["id"] == "L-2024")
    assert l2024["rentPerMonth"] == 55000


def test_ambiguous_entity_match_review_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Possible match - same normalized address as L-2025 but different floor/rent
    url = "https://www.synthetic.example/detail-99310418.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-02"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    data = _advance_submitted_intake(client, submitted)
    assert data["stage"] == "NEEDS_REVIEW"
    assert data["matchResult"]["outcome"] == "POSSIBLE_MATCH"
    assert data["matchResult"]["targetListingId"] == "L-2025"

    # Try correct without a reason (identity fields require a reason)
    bad_correct = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": " ",
            "riskSummary": "修改地址會改變比對結果，可能指向不同物件。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("ambiguous-bad-correct"),
    )
    assert bad_correct.status_code == 422

    # Perform correct with a valid reason
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址以避開衝突",
            "riskSummary": "修改地址會改變比對結果，可能指向不同物件。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("ambiguous-good-correct"),
    )
    assert correct_resp.status_code == 200
    corrected_data = correct_resp.json()
    assert corrected_data["stage"] == "READY"
    assert corrected_data["matchResult"]["outcome"] == "NEW"


def test_created_listing_keeps_address_and_heat_zone_for_v1_promotion_gate() -> None:
    app = create_app()
    client = TestClient(app)
    submitted = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={
            "url": "https://www.synthetic.example/detail-99310418.html",
            "heatZoneId": "HZ-02",
        },
        headers=_write_headers("promotion-gate-submit"),
    )
    assert submitted.status_code == 200

    queued = submitted.json()
    assert queued["stage"] == "SUBMITTED"
    intake = _advance_submitted_intake(client, queued)
    decided = client.post(
        f"/api/v1/operator/network-listings/intake/{intake['id']}/decide",
        json={
            "action": "create",
            "reason": "來源與樓層證據顯示為獨立物件",
            "riskSummary": "建立新物件後將可另行提出 Candidate 晉升。",
            "riskAcknowledged": True,
            "actorRoleId": "expansion-manager",
        },
        headers=_write_headers("promotion-gate-decide"),
    )
    assert decided.status_code == 200
    listing_id = decided.json()["matchResult"]["targetListingId"]

    repository = app.state.listing_repository
    listing = repository.get_listing(listing_id)
    assert listing is not None
    assert listing.address_id == f"ADDR-{listing_id}"
    address = next(item for item in repository.addresses if item.address_id == listing.address_id)
    assert address.normalized_address
    assert address.h3_res_9 == "HZ-02"

    promotion = client.post(
        f"/api/v1/intakes/{intake['id']}/promotion-requests",
        json={
            "target_format_code": "FMT-STANDARD-STORE",
            "reason": "商圈缺口與物件資料均已覆核，提出 Candidate 晉升申請。",
            "gate_snapshot_sha256": "a" * 64,
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS,
            "X-Correlation-Id": "corr-promotion-gate-request",
            "Idempotency-Key": "promotion-gate-request-001",
            "If-Match": f'W/"{decided.json()["version"]}"',
        },
    )
    assert promotion.status_code == 202
    assert promotion.json()["status"] == "PENDING_REVIEW"


def test_malformed_payload_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Malformed payload (empty address raw)
    url = "https://www.synthetic.example/detail-40028801.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    data = _advance_submitted_intake(client, submitted)
    assert data["stage"] == "AWAITING_ASSISTED_ENTRY"


def test_unapproved_source_fail_closed_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. 591 is ASSISTED_ENTRY_ONLY
    url_591 = "https://www.591.com.tw/rent-detail-12345.html"
    resp_591 = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url_591, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_591.status_code == 200
    queued_591 = resp_591.json()
    assert queued_591["stage"] == "SUBMITTED"
    data_591 = _advance_submitted_intake(client, queued_591)
    assert data_591["stage"] == "AWAITING_ASSISTED_ENTRY"
    assert data_591["policy"] == "ASSISTED_ENTRY_ONLY"

    # 2. Unknown source is POLICY_UNKNOWN
    url_unknown = "https://www.unknown-domain.com/rent/123"
    resp_unknown = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url_unknown, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_unknown.status_code == 200
    queued_unknown = resp_unknown.json()
    assert queued_unknown["stage"] == "SUBMITTED"
    data_unknown = _advance_submitted_intake(client, queued_unknown)
    assert data_unknown["stage"] == "QUARANTINED"
    assert data_unknown["policy"] == "POLICY_UNKNOWN"


def test_timeout_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Timeout URL
    url = "https://www.synthetic.example/detail-50000001.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    data = _advance_submitted_intake(client, submitted)
    assert data["stage"] == "FAILED"
    assert data["failure"]["code"] == "ODP-INTAKE-RETRIEVAL-TIMEOUT"

    # User manual correction should survive retry
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"rent": 48000, "address": "新莊興德路店面"},
            "reason": "手動補錄超時物件",
            "riskSummary": "手動補錄的欄位不具來源證據。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("timeout-manual-correct"),
    )
    assert correct_resp.status_code == 200

    # Retry - fails again due to timeout fixture, but checks that manual rent correction survives
    retry_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/retry",
        json={"actorRoleId": "expansionManager"},
        headers=HEADERS,
    )
    assert retry_resp.status_code == 200
    retried_data = retry_resp.json()
    assert retried_data["stage"] == "SUBMITTED"
    assert retried_data["parsedFields"]["rent"]["correctedValue"] == 48000
    failed_again = _advance_submitted_intake(client, retried_data)
    assert failed_again["stage"] == "FAILED"
    assert failed_again["parsedFields"]["rent"]["correctedValue"] == 48000


def test_fixture_compatible_replay() -> None:
    # Verify that the entire retrieval corpus remains queryable and matches exact schemas
    from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS

    for result in RETRIEVAL_CORPUS.values():
        assert result.snapshot_id is not None
        if result.ok:
            assert isinstance(result.raw, dict)
            assert result.failure is None
        else:
            assert result.failure is not None
            assert (
                result.failure.code.startswith("ODP-INTAKE-")
                or result.failure.code
                in {"AUTH_WALL_ENCOUNTERED", "BOT_CHALLENGE_ENCOUNTERED"}
            )


def test_role_based_server_checks() -> None:
    app = create_app()
    client = TestClient(app)

    url = "https://www.synthetic.example/detail-77120345.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    submitted = resp.json()
    intake_id = submitted["id"]

    # Try correct with an unauthorized role (e.g. platform_admin or franchisee)
    bad_correct = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "手動修改",
            "actorRoleId": "franchisee",
        },
        headers=HEADERS,
    )
    assert bad_correct.status_code == 422


def test_promote_intake_contract_test(tmp_path) -> None:
    bundle = _durable_bundle(str(tmp_path / "operator-promotion.sqlite3"))
    app = create_app(persistence=bundle)
    client = TestClient(app)

    # 1. Submit a revision URL to resolve it to L-2024
    url = "https://www.synthetic.example/detail-88520242.html"
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    data = _advance_submitted_intake(
        client,
        submitted,
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )
    intake_id = data["id"]
    decided = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            "action": "revise",
            "reason": "先將核准的來源版本加入既有物件",
            "riskSummary": "將送件資料加入 L-2024 的版本沿革。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("promote-prerequisite-revision"),
    )
    assert decided.status_code == 200, decided.text

    # Try promote without reason -> expect 422
    bad_promote = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={"actorRoleId": "expansionManager", "reason": ""},
        headers=_write_headers("bad-promote-no-reason"),
    )
    assert bad_promote.status_code == 422

    # Promote with valid reason
    promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "actorRoleId": "expansionManager",
            "reason": "核准物件轉換為候選店",
            "riskSummary": "轉換為候選店會建立 SiteScore 待審紀錄。",
            "riskAcknowledged": True,
        },
        headers=_write_headers("good-promote"),
    )
    assert promote_resp.status_code == 200
    res_data = promote_resp.json()
    assert res_data["status"] == "PENDING_REVIEW"
    assert "candidate" not in res_data

    review_resp = client.post(
        f"/api/v1/promotion-decisions/{res_data['promotion_decision_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Independent review approved",
            "risk_acknowledged": True,
        },
        headers={
            **_write_headers("good-promote-review"),
            "x-subject-id": "00000000-0000-0000-0000-000000000102",
            "If-Match": f'W/"{res_data["version"]}"',
        },
    )
    assert review_resp.status_code == 200, review_resp.text
    reviewed = review_resp.json()
    assert reviewed["status"] == "SCORE_QUEUED"
    assert reviewed["candidate_site_id"]
    assert ODayWorker(persistence=bundle).run_once() is True
    completed = client.get(
        f"/api/v1/promotion-decisions/{res_data['promotion_decision_id']}",
        headers=HEADERS,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    bundle.engine.close()


# --- Risk disclosure contract (ODP-OC-R5-011 review finding P0-2) ---
#
# High-impact writes must carry a caller-supplied risk summary AND an explicit
# acknowledgement. The server must not invent the summary: an audit record is
# only evidence of consent if it stores the text the operator actually saw.


def _ready_intake_id(client) -> str:
    resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-88520242.html", "heatZoneId": "HZ-01"},
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )
    assert resp.status_code == 200
    submitted = resp.json()
    assert submitted["stage"] == "SUBMITTED"
    return _advance_submitted_intake(
        client,
        submitted,
        headers={
            **HEADERS,
            "x-subject-id": "operator-expansion-staff",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )["id"]


CORRECT_FIELDS = {"fields": {"address": "新北市板橋區府中路 99 號 1F"}, "reason": "勘誤地址"}
DECIDE_BODY = {"action": "revise", "reason": "降價更新"}
PROMOTE_BODY = {"reason": "核准物件轉換為候選店"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("correct", CORRECT_FIELDS),
        ("decide", DECIDE_BODY),
        ("promote", PROMOTE_BODY),
    ],
)
def test_high_impact_write_rejects_missing_risk_summary(path, body) -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/{path}",
        json={**body, "riskAcknowledged": True, "actorRoleId": "expansionManager"},
        headers=_write_headers(f"missing-risk-{path}"),
    )
    assert resp.status_code == 422
    assert "risk summary is required" in resp.json()["detail"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("correct", CORRECT_FIELDS),
        ("decide", DECIDE_BODY),
        ("promote", PROMOTE_BODY),
    ],
)
def test_high_impact_write_rejects_unacknowledged_risk(path, body) -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    # Summary supplied, but the operator never accepted it.
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/{path}",
        json={
            **body,
            "riskSummary": "此變更會覆寫既有物件。",
            "riskAcknowledged": False,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers(f"unack-risk-{path}"),
    )
    assert resp.status_code == 422
    assert "risk acknowledgement is required" in resp.json()["detail"]


def test_merge_rejects_missing_risk_disclosure() -> None:
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        json={"targetListingId": "L-2025", "reason": "重複來源", "actorRoleId": "expansionManager"},
        headers=_write_headers("merge-missing-risk"),
    )
    assert resp.status_code == 422
    assert "risk summary is required" in resp.json()["detail"]


def test_correct_persists_caller_risk_summary_in_audit() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "修改地址會改變比對結果，可能指向不同物件。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            **CORRECT_FIELDS,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("correct-risk-audit"),
    )
    assert resp.status_code == 200

    audit = resp.json()["auditEvents"][-1]
    assert audit["action"] == "intake.correct"
    # The stored summary is the caller's text verbatim, not a server-built one.
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True


def test_decide_persists_caller_risk_summary_alongside_server_effect() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "將以送件版本覆寫既有物件 L-2024 的租金。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        json={
            **DECIDE_BODY,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("decide-risk-audit"),
    )
    assert resp.status_code == 200

    audit = resp.json()["auditEvents"][-1]
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True
    # The server-derived description of what happened is kept, but under a
    # separate key so it can never be mistaken for acknowledged text.
    assert "L-2024" in audit["metadata"]["effectSummary"]


def test_promote_persists_caller_risk_summary_in_audit() -> None:
    app = create_app()
    client = TestClient(app)
    intake_id = _ready_intake_id(client)

    caller_summary = "轉換為候選店會建立 SiteScore 待審紀錄。"
    resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            **PROMOTE_BODY,
            "riskSummary": caller_summary,
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("promote-risk-audit"),
    )
    assert resp.status_code == 200

    detail = client.get(f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS)
    audit = detail.json()["auditEvents"][-1]
    assert audit["action"] == "intake.promote_request"
    assert audit["metadata"]["riskSummary"] == caller_summary
    assert audit["metadata"]["riskAcknowledged"] is True


def test_unassigned_operator_intake_is_not_owned_by_unrelated_staff() -> None:
    principal = Principal(subject_id="staff-a")

    assert not is_record_owner(
        principal,
        {"owner": "unassigned", "submitter": "staff-b"},
    )
    assert is_record_owner(
        principal,
        {"owner": None, "submitter": "staff-a"},
    )
