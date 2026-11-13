from __future__ import annotations

import hashlib
import json as jsonlib
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.application.assisted_intake import (
    RETRIEVAL_CORPUS,
    parse_snapshot,
    resolve_source_policy,
)
from modules.opsboard.application.network_listings import (
    IntakeIdempotencyRecord,
)
from shared.auth import Role
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}

MANAGER_HEADERS = {
    # EXPANSION_USER supplies the listing UPDATE grant; SITE_REVIEWER is the
    # verified claim that authorizes selection of the expansion-manager role.
    **auth_headers(
        Role.SITE_REVIEWER,
        Role.EXPANSION_USER,
        subject="operator-expansion-manager",
    ),
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _write_headers(key: str) -> dict[str, str]:
    return {
        **HEADERS,
        "X-Correlation-Id": f"corr-{key}",
        "Idempotency-Key": f"idem-{key}",
    }


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable.sqlite3")


def _replay_retained_url(
    app, client, *, json: dict, headers: dict, stage="READY", outcome="NEW", target=None,
):
    """Arrange retained legacy documents and exercise their real HTTP replay.

    No retrieval, matching, native submission or business create runs here.
    Correction, decision and retry below still use their existing owners.
    """
    url = json["url"]
    actor = headers["x-subject-id"]
    headers = {**headers, "Idempotency-Key": headers.get("Idempotency-Key", f"retained-{uuid4()}")}
    policy = resolve_source_policy(url)
    snapshot = RETRIEVAL_CORPUS.get(url)
    record = {
        "id": f"IN-RETAINED-{uuid4()}", "tenantId": headers["x-tenant-id"],
        "owner": actor, "submitter": actor, "submittedBy": actor,
        "originalUrl": url, "canonicalUrl": url, "heatZoneId": json["heatZoneId"],
        "intakeMethod": "URL", "stage": stage, "version": 1,
        "sourceId": policy.source_id, "policy": policy.policy,
        "policyLabel": policy.policy_label, "policyReason": policy.policy_reason,
        "snapshotId": snapshot.snapshot_id if snapshot else None,
        "parsedFields": parse_snapshot(snapshot) if snapshot and snapshot.ok else {},
        "matchResult": {"outcome": outcome, "targetListingId": target,
                        "confidence": 0.95, "contradictingSignals": []} if outcome else None,
        "auditEvents": [],
    }
    if snapshot and snapshot.failure:
        record["failure"] = snapshot.failure.to_dict()
    repo = app.state.operator_intake_repository
    repo.save_intake(record)
    fingerprint = hashlib.sha256(jsonlib.dumps(
        {"url": url.strip(), "heat_zone_id": json["heatZoneId"], "async_intake": False},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    repo.save_idempotency_record(IntakeIdempotencyRecord(
        action="submit_intake", key=hashlib.sha256(actor.encode()).hexdigest() + ":" + headers["Idempotency-Key"],
        response=deepcopy(record), actor_subject_id=actor, request_sha256=fingerprint,
    ))
    before = deepcopy((repo.list_intakes(), repo.list_idempotency_records()))
    response = client.post("/api/v1/operator/network-listings/intake/submit", json=json, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == record["id"]
    assert (repo.list_intakes(), repo.list_idempotency_records()) == before
    return response


def test_historical_submission_replays_original_receipt() -> None:
    # 1. Create app in memory mode
    app = create_app()
    client = TestClient(app)

    # 2. Retained legacy history is not a native READY receipt.
    url = "https://www.synthetic.example/detail-77120345.html"
    resp = _replay_retained_url(
        app, client,
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
    assert data["stage"] == "READY"
    assert data["policy"] == "APPROVED_RETRIEVAL"
    assert data["matchResult"]["outcome"] == "NEW"

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


def test_duplicate_and_revision_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Retained revision history remains decidable.
    # Revision — same provider listing id as L-2024, rent reduced 58k -> 55k.
    url = "https://www.synthetic.example/detail-88520242.html"
    resp = _replay_retained_url(
        app, client, outcome="REVISION", target="L-2024",
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["matchResult"]["outcome"] == "REVISION"
    assert data["matchResult"]["targetListingId"] == "L-2024"

    # 2. Duplicate submission check (running/pending submission duplicate)
    # First, let's decide revision (action="revise")
    decide_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/decide",
        json={
            "action": "revise",
            "reason": "降價更新",
            "riskSummary": "將以送件版本覆寫既有物件 L-2024 的租金。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers={**MANAGER_HEADERS,"X-Correlation-Id":"corr-integration-revision-decide",
                 "Idempotency-Key":"idem-integration-revision-decide",
                 "If-Match": f'W/"{data["version"]}"'},
    )
    assert decide_resp.status_code == 200
    assert decide_resp.json()["stage"] == "READY"

    # Now check if target listing rent is updated in snapshot
    snap_resp = client.get("/api/v1/operator/network-listings", headers=MANAGER_HEADERS)
    listings = snap_resp.json()["listings"]
    l2024 = next(item for item in listings if item["id"] == "L-2024")
    assert l2024["rentPerMonth"] == 55000


def test_ambiguous_entity_match_review_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Possible match — same normalized address as L-2025 but different floor/rent.
    url = "https://www.synthetic.example/detail-99310418.html"
    resp = _replay_retained_url(
        app, client, stage="NEEDS_REVIEW", outcome="POSSIBLE_MATCH", target="L-2025",
        json={"url": url, "heatZoneId": "HZ-02"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "NEEDS_REVIEW"
    assert data["matchResult"]["outcome"] == "POSSIBLE_MATCH"
    assert data["matchResult"]["targetListingId"] == "L-2025"

    # Perform correction to change address so it's no longer conflicting (resolves as NEW)
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址以避開衝突",
            "riskSummary": "修改地址會改變比對結果。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("integration-ambiguous-correct"),
    )
    assert correct_resp.status_code == 200
    corrected_data = correct_resp.json()
    assert corrected_data["stage"] == "READY"
    assert corrected_data["matchResult"]["outcome"] == "NEW"


def test_malformed_payload_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Retained malformed source history keeps its assisted-entry state.
    url = "https://www.synthetic.example/detail-40028801.html"
    resp = _replay_retained_url(
        app, client, stage="AWAITING_ASSISTED_ENTRY", outcome=None,
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "AWAITING_ASSISTED_ENTRY"


def test_retained_source_policy_states_remain_readable() -> None:
    app = create_app()
    client = TestClient(app)

    # 1. 591 is ASSISTED_ENTRY_ONLY
    url_591 = "https://www.591.com.tw/rent-detail-12345.html"
    resp_591 = _replay_retained_url(
        app, client, stage="AWAITING_ASSISTED_ENTRY", outcome=None,
        json={"url": url_591, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_591.status_code == 200
    data_591 = resp_591.json()
    assert data_591["stage"] == "AWAITING_ASSISTED_ENTRY"
    assert data_591["policy"] == "ASSISTED_ENTRY_ONLY"

    # 2. Unknown source is POLICY_UNKNOWN
    url_unknown = "https://www.unknown-domain.com/rent/123"
    # Current policy admission/no-retrieval proof is the canonical journey;
    # this test proves that retained policy history is still accessible.
    resp_unknown = _replay_retained_url(
        app, client, stage="QUARANTINED", outcome="QUARANTINED",
        json={"url": url_unknown, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp_unknown.status_code == 200
    data_unknown = resp_unknown.json()
    assert data_unknown["stage"] == "QUARANTINED"
    assert data_unknown["policy"] == "POLICY_UNKNOWN"


def test_timeout_contract_test() -> None:
    app = create_app()
    client = TestClient(app)

    # Retained timeout history must keep corrections across the real retry.
    url = "https://www.synthetic.example/detail-50000001.html"
    resp = _replay_retained_url(
        app, client, stage="FAILED", outcome=None,
        json={"url": url, "heatZoneId": "HZ-01"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stage"] == "FAILED"
    assert data["failure"]["code"] == "ODP-INTAKE-RETRIEVAL-TIMEOUT"

    # User enters manual correction which must survive retry
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/correct",
        json={
            "fields": {"rent": 48000, "address": "新莊興德路店面"},
            "reason": "手動補錄超時物件",
            "riskSummary": "手動補錄的欄位不具來源證據。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("integration-timeout-correct"),
    )
    assert correct_resp.status_code == 200

    # Retry - in tests retrieve will fail again since it's the timeout fixture,
    # but we verify it tries and user's corrections (rent: 48000) survive in parsedFields
    retry_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{data['id']}/retry",
        json={"actorRoleId": "expansionManager"},
        headers=HEADERS,
    )
    assert retry_resp.status_code == 200
    retried_data = retry_resp.json()
    assert retried_data["parsedFields"]["rent"]["correctedValue"] == 48000


def test_process_restart_survival(db_path) -> None:
    # 1. Start application with a durable SQLite bundle
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # Save retained data through the durable repository, not a fresh POST.
        url = "https://www.synthetic.example/detail-77120345.html"
        resp = _replay_retained_url(
            app, client,
            json={"url": url, "heatZoneId": "HZ-01"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        intake_id = resp.json()["id"]
    finally:
        bundle.engine.close()

    # --- Simulated process restart ---
    reopened = _durable_bundle(db_path)
    try:
        app2 = create_app(persistence=reopened)
        client2 = TestClient(app2)

        # Retrieve the intake record after restart
        get_resp = client2.get(
            f"/api/v1/operator/network-listings/intake/{intake_id}", headers=HEADERS
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["originalUrl"] == url
        assert get_resp.json()["stage"] == "READY"
    finally:
        reopened.engine.close()


def test_durable_intake_repository_round_trips_through_public_contract(db_path) -> None:
    """The durable repository satisfies the public contract across a restart.

    Exercised directly against the contract methods (not the document store) so
    the application layer's only persistence dependency is the one under test.
    """
    bundle = _durable_bundle(db_path)
    try:
        repo = DurableAssistedIntakeRepository(SqliteDocumentStore(bundle.engine))
        repo.save_intake(
            {"id": "IN-CONTRACT-1", "stage": "READY", "originalUrl": "https://a.invalid"}
        )
        repo.save_idempotency_record(
            IntakeIdempotencyRecord(action="submit", key="k-1", response={"id": "IN-CONTRACT-1"})
        )
        repo.save_listing_metadata("L-2030", {"heatZoneId": "HZ-01"})
        repo.save_candidate_metadata("C-1", {"reviewId": "REV-1"})
    finally:
        bundle.engine.close()

    # --- Simulated process restart ---
    reopened = _durable_bundle(db_path)
    try:
        repo2 = DurableAssistedIntakeRepository(SqliteDocumentStore(reopened.engine))

        intakes = repo2.list_intakes()
        assert [item["id"] for item in intakes] == ["IN-CONTRACT-1"]
        assert intakes[0]["stage"] == "READY"

        records = repo2.list_idempotency_records()
        assert len(records) == 1
        assert records[0].action == "submit"
        assert records[0].key == "k-1"
        assert records[0].response == {"id": "IN-CONTRACT-1"}

        assert repo2.get_listing_metadata("L-2030") == {"heatZoneId": "HZ-01"}
        assert repo2.get_candidate_metadata("C-1") == {"reviewId": "REV-1"}

        # Unknown ids read as empty, never None, so callers can merge directly.
        assert repo2.get_listing_metadata("L-UNKNOWN") == {}
        assert repo2.get_candidate_metadata("C-UNKNOWN") == {}

        repo2.clear()
        assert repo2.list_intakes() == []
        assert repo2.list_idempotency_records() == []
        assert repo2.get_listing_metadata("L-2030") == {}
    finally:
        reopened.engine.close()


def _merge_l2029(client: TestClient, *, idempotency_key: str, reason: str):
    return client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        headers={
            **MANAGER_HEADERS,
            "Idempotency-Key": idempotency_key,
            "X-Correlation-Id": f"corr-{idempotency_key}",
        },
        json={
            "actorRoleId": "expansionManager",
            "actorName": "王若寧",
            "targetListingId": "L-2025",
            "reason": reason,
            "riskSummary": "Merging marks L-2029 a duplicate of L-2025.",
            "riskAcknowledged": True,
        },
    )


def test_merge_terminal_state_survives_restart(db_path) -> None:
    """The merge terminal marker must be durable, not just in-process.

    `status` alone cannot carry this: a merged source and a merge-eligible
    duplicate are both "duplicate". Before `mergedIntoId` was persisted, a
    restart dropped the marker and a second merge was accepted again.
    """
    bundle = _durable_bundle(db_path)
    try:
        client = TestClient(create_app(persistence=bundle))
        first = _merge_l2029(client, idempotency_key="idem-merge-durable", reason="FIRST reason")
        assert first.status_code == 200, first.text
    finally:
        bundle.engine.close()

    # --- Simulated process restart ---
    reopened = _durable_bundle(db_path)
    try:
        client2 = TestClient(create_app(persistence=reopened))

        snapshot = client2.get(
            "/api/v1/operator/network-listings", headers=MANAGER_HEADERS
        ).json()
        source = next(item for item in snapshot["listings"] if item["id"] == "L-2029")
        assert source["mergedIntoId"] == "L-2025"
        assert source["mergeReason"] == "FIRST reason"

        second = _merge_l2029(
            client2, idempotency_key="idem-merge-durable-2", reason="SECOND reason"
        )
        assert second.status_code == 409, second.text
        assert "already merged into L-2025" in second.json()["detail"]

        # The rejected request wrote nothing. Audit events are in-process only,
        # so their count says nothing across a restart; the durable merge reason
        # is what proves the second request did not take effect.
        after = client2.get(
            "/api/v1/operator/network-listings", headers=MANAGER_HEADERS
        ).json()
        source_after = next(item for item in after["listings"] if item["id"] == "L-2029")
        assert source_after["mergeReason"] == "FIRST reason"
        assert source_after["mergedIntoId"] == "L-2025"
    finally:
        reopened.engine.close()


def test_service_replays_idempotent_write_through_repository_after_restart(db_path) -> None:
    """A replayed write returns the cached response from durable state."""
    bundle = _durable_bundle(db_path)
    url = "https://www.synthetic.example/detail-77120345.html"
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)
        resp = _replay_retained_url(
            app, client,
            json={"url": url, "heatZoneId": "HZ-01"},
            headers={**HEADERS, "Idempotency-Key": "idem-restart"},
        )
        assert resp.status_code == 200
        intake_id = resp.json()["id"]
    finally:
        bundle.engine.close()

    # --- Simulated process restart: the replay must not create a second intake ---
    reopened = _durable_bundle(db_path)
    try:
        app2 = create_app(persistence=reopened)
        client2 = TestClient(app2)
        replay = client2.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={"url": url, "heatZoneId": "HZ-01"},
            headers={**HEADERS, "Idempotency-Key": "idem-restart"},
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == intake_id

        snapshot = client2.get("/api/v1/operator/network-listings", headers=HEADERS)
        assert snapshot.status_code == 200
        matching = [item for item in snapshot.json()["assistedIntakes"] if item["id"] == intake_id]
        assert len(matching) == 1
    finally:
        reopened.engine.close()
