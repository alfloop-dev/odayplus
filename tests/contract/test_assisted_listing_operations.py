from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

from apps.api.oday_api.main import create_app

# Standard headers for authenticating as tenant-a
HEADERS_A = {
    "x-subject-id": "actor-a",
    "x-tenant-id": "tenant-a",
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for reviewer in tenant-a (to avoid self-review errors)
HEADERS_A_REVIEWER = {
    "x-subject-id": "actor-a-reviewer",
    "x-tenant-id": "tenant-a",
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for actor-c in tenant-a
HEADERS_C = {
    "x-subject-id": "actor-c",
    "x-tenant-id": "tenant-a",
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for authenticating as tenant-b
HEADERS_B = {
    "x-subject-id": "actor-b",
    "x-tenant-id": "tenant-b",
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}




@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_authentication_and_tenant_isolation_boundary(client: TestClient) -> None:
    # 1. 401 Unauthorized when missing x-subject-id (not authenticated)
    resp = client.get("/api/v1/intakes", headers={"x-tenant-id": "tenant-a"})
    assert resp.status_code == 401
    assert "code" in resp.json()

    # 2. 403 Forbidden when missing x-tenant-id
    resp = client.get("/api/v1/intakes", headers={"x-subject-id": "actor-a"})
    assert resp.status_code == 403
    assert "code" in resp.json()



def test_url_intake_and_concurrency_lifecycle(client: TestClient) -> None:
    # 1. submitUrlIntake (POST /api/v1/intakes/url)
    payload = {
        "original_url": "https://example.com/listings/123",
        "scope": {
            "tenant_id": "tenant-a",
            "brand_id": "brand-x",
        }
    }
    idem_key = f"idem-url-{uuid4()}"
    headers = {**HEADERS_A, "Idempotency-Key": idem_key}

    resp = client.post("/api/v1/intakes/url", json=payload, headers=headers)
    assert resp.status_code == 202
    assert "ETag" in resp.headers
    assert resp.headers["Idempotency-Replayed"] == "false"

    receipt = resp.json()
    intake_id = receipt["intake_id"]
    job_id = receipt["job_id"]
    version = receipt["version"]
    assert intake_id is not None
    assert receipt["state"] == "SUBMITTED"

    # Replay with idempotency key -> returns 200
    resp_replay = client.post("/api/v1/intakes/url", json=payload, headers=headers)
    assert resp_replay.status_code == 200
    assert resp_replay.headers["Idempotency-Replayed"] == "true"
    assert resp_replay.json()["intake_id"] == intake_id

    # Tenant isolation on submit Url Intake (submitting body with tenant-a scope but using tenant-b credentials)
    resp_bad = client.post("/api/v1/intakes/url", json=payload, headers={**HEADERS_B, "Idempotency-Key": f"idem-url-bad-{uuid4()}"})
    assert resp_bad.status_code == 403


    # 2. getIntake (GET /api/v1/intakes/{id})
    resp_get = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS_A)
    assert resp_get.status_code == 200
    assert "ETag" in resp_get.headers
    detail = resp_get.json()
    assert detail["intake_id"] == intake_id
    assert detail["original_url"] == payload["original_url"]
    assert detail["scope"]["tenant_id"] == "tenant-a"

    # Tenant isolation on getIntake
    resp_get_b = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS_B)
    assert resp_get_b.status_code == 403

    # 3. listIntakes (GET /api/v1/intakes)
    resp_list = client.get("/api/v1/intakes", headers=HEADERS_A)
    assert resp_list.status_code == 200
    page = resp_list.json()
    assert len(page["items"]) >= 1
    assert any(item["intake_id"] == intake_id for item in page["items"])

    # ListIntakes for tenant-b should NOT see tenant-a's intake
    resp_list_b = client.get("/api/v1/intakes", headers=HEADERS_B)
    assert resp_list_b.status_code == 200
    page_b = resp_list_b.json()
    assert not any(item["intake_id"] == intake_id for item in page_b["items"])

    # 4. assignIntake (PUT /api/v1/intakes/{id}/assignment)
    assign_payload = {
        "owner_subject_id": "operator-steward",
        "owner_role": "steward",
        "due_at": "2026-07-25T12:00:00Z",
        "reason": "Initial assignment logic review",
    }
    # Missing If-Match -> 428 Precondition Required
    resp_assign_fail = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json=assign_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-{uuid4()}"}
    )
    assert resp_assign_fail.status_code == 428

    # Correct If-Match -> 200
    resp_assign = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json=assign_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-assign-{uuid4()}",
            "If-Match": f'W/"{version}"'
        }
    )
    assert resp_assign.status_code == 200
    assert "ETag" in resp_assign.headers
    assign_receipt = resp_assign.json()
    assert assign_receipt["status"] == "ASSIGNED"
    assert assign_receipt["owner_subject_id"] == "operator-steward"
    assignment_id = assign_receipt["assignment_id"]
    new_version = assign_receipt["version"]

    # 5. proposeCorrection (POST /api/v1/intakes/{id}/corrections)
    correct_payload = {
        "field_path": "rent_amount",
        "corrected_value": 1500.0,
        "reason": "Operator corrected data"
    }
    resp_correct = client.post(
        f"/api/v1/intakes/{intake_id}/corrections",
        json=correct_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-correct-{uuid4()}",
            "If-Match": f'W/"{new_version}"'
        }
    )
    assert resp_correct.status_code == 201
    correct_receipt = resp_correct.json()
    assert correct_receipt["status"] == "APPLIED"
    assert correct_receipt["intake_id"] == intake_id
    version_after_correct = correct_receipt["version"]

    # 6. quarantineIntake (POST /api/v1/intakes/{id}/actions/quarantine)
    quarantine_payload = {"reason": "Quarantine due to active incident triage", "risk_acknowledged": True, "incident_or_change_id": "INC-101"}
    resp_quar = client.post(
        f"/api/v1/intakes/{intake_id}/actions/quarantine",
        json=quarantine_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-quar-{uuid4()}",
            "If-Match": f'W/"{version_after_correct}"'
        }
    )
    assert resp_quar.status_code == 200
    quar_receipt = resp_quar.json()
    assert quar_receipt["from_state"] == "SUBMITTED"
    assert quar_receipt["to_state"] == "QUARANTINED"
    version_after_quar = quar_receipt["version_after"]

    # 7. reopenIntake (POST /api/v1/intakes/{id}/actions/reopen)
    resp_reopen = client.post(
        f"/api/v1/intakes/{intake_id}/actions/reopen",
        json={"reason": "Reopen after incident resolution", "risk_acknowledged": True},
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-reopen-{uuid4()}",
            "If-Match": f'W/"{version_after_quar}"'
        }
    )
    assert resp_reopen.status_code == 200
    reopen_receipt = resp_reopen.json()
    assert reopen_receipt["from_state"] == "QUARANTINED"
    assert reopen_receipt["to_state"] == "CHECKING_SOURCE_POLICY"
    version_after_reopen = reopen_receipt["version_after"]

    # 8. cancelIntake (POST /api/v1/intakes/{id}/actions/cancel)
    cancel_payload = {"reason": "Duplicate submission"}
    resp_cancel = client.post(
        f"/api/v1/intakes/{intake_id}/actions/cancel",
        json=cancel_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-cancel-{uuid4()}",
            "If-Match": f'W/"{version_after_reopen}"'
        }
    )
    assert resp_cancel.status_code == 200
    cancel_receipt = resp_cancel.json()
    assert cancel_receipt["from_state"] == "CHECKING_SOURCE_POLICY"
    assert cancel_receipt["to_state"] == "CANCELLED"
    version_after_cancel = cancel_receipt["version_after"]

    # Transition the intake state to READY in store to satisfy the promotion prerequisite
    from apps.api.app.routes.listings import AssistedIntakeStore
    for store in getattr(AssistedIntakeStore, "_instances", []):
        if intake_id in store.intakes:
            store.intakes[intake_id]["state"] = "READY"

    # 9. requestCandidatePromotion (POST /api/v1/intakes/{id}/promotion-requests)

    promo_payload = {
        "target_format_code": "FORMAT-A",
        "reason": "Promoting standard listing",
        "gate_snapshot_sha256": "a000000000000000000000000000000000000000000000000000000000000000",
        "risk_acknowledged": True
    }
    resp_promo = client.post(
        f"/api/v1/intakes/{intake_id}/promotion-requests",
        json=promo_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-promo-{uuid4()}",
            "If-Match": f'W/"{version_after_cancel}"'
        }
    )
    assert resp_promo.status_code == 202
    promo_receipt = resp_promo.json()
    assert promo_receipt["status"] == "PENDING_REVIEW"
    promo_id = promo_receipt["promotion_decision_id"]

    # 10. getPromotionDecision (GET /api/v1/promotion-decisions/{id})
    resp_get_promo = client.get(f"/api/v1/promotion-decisions/{promo_id}", headers=HEADERS_A)
    assert resp_get_promo.status_code == 200
    assert resp_get_promo.json()["promotion_decision_id"] == promo_id

    # getPromotionDecision tenant isolation
    resp_get_promo_b = client.get(f"/api/v1/promotion-decisions/{promo_id}", headers=HEADERS_B)
    assert resp_get_promo_b.status_code == 403

    # 11. reviewPromotionDecision (POST /api/v1/promotion-decisions/{id}/actions/review)
    review_promo_payload = {
        "decision": "APPROVE",
        "reason": "Reviewer approves promotion",
        "risk_acknowledged": True
    }
    resp_rev_promo = client.post(
        f"/api/v1/promotion-decisions/{promo_id}/actions/review",
        json=review_promo_payload,
        headers={
            **HEADERS_A_REVIEWER,
            "Idempotency-Key": f"idem-rev-promo-{uuid4()}",
            "If-Match": 'W/"1"'
        }
    )
    assert resp_rev_promo.status_code == 200
    assert resp_rev_promo.json()["status"] == "APPROVED"



def test_batch_intake_operation(client: TestClient) -> None:
    batch_payload = {
        "batch_id": f"batch-{uuid4()}",
        "method": "MANUAL",
        "scope": {
            "tenant_id": "tenant-a"
        },
        "rows": [
            {
                "address_raw": "123 Main St",
                "rent_amount": 1200.0,
                "original_url": "https://example.com/batch/1"
            },
            {
                "address_raw": "",  # invalid row (missing address)
                "rent_amount": 1000.0
            }
        ]
    }

    resp = client.post(
        "/api/v1/intake-batches",
        json=batch_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-batch-{uuid4()}"}
    )
    # Since one is valid and one is rejected -> 207 Multi-Status
    assert resp.status_code == 207
    receipt = resp.json()
    assert receipt["batch_id"] == batch_payload["batch_id"]
    assert receipt["accepted_count"] == 1
    assert receipt["rejected_count"] == 1
    assert receipt["rows"][0]["status"] == "ACCEPTED"
    assert receipt["rows"][1]["status"] == "REJECTED"


def test_job_retry_operation(client: TestClient) -> None:
    # Set up a fake job in store
    # Since our store is in memory, let's create a submitUrlIntake to auto-register a job
    payload = {
        "original_url": "https://example.com/listings/job-test",
        "scope": {"tenant_id": "tenant-a"}
    }
    resp = client.post(
        "/api/v1/intakes/url",
        json=payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-job-{uuid4()}"}
    )
    receipt = resp.json()
    job_id = receipt["job_id"]

    retry_payload = {
        "checkpoint": "PARSING",
        "reason": "Transient failure retry"
    }

    # Retry with correct If-Match -> 202
    resp_retry = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        json=retry_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-retry-{uuid4()}",
            "If-Match": 'W/"1"'
        }
    )
    assert resp_retry.status_code == 202
    job_receipt = resp_retry.json()
    assert job_receipt["job_id"] == job_id
    assert job_receipt["status"] == "QUEUED"
    assert job_receipt["checkpoint"] == "PARSING"

    # Retry job tenant isolation
    resp_retry_b = client.post(
        f"/api/v1/jobs/{job_id}/retry",
        json=retry_payload,
        headers={
            **HEADERS_B,
            "Idempotency-Key": f"idem-retry-b-{uuid4()}",
            "If-Match": 'W/"2"'
        }
    )
    assert resp_retry_b.status_code == 403


def test_saved_views_operations(client: TestClient) -> None:
    view_payload = {
        "name": "My Active Intakes",
        "query": {"state": "SUBMITTED"},
        "visibility": "PRIVATE"
    }

    # 1. createSavedView
    resp = client.post(
        "/api/v1/saved-views",
        json=view_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-sv-{uuid4()}"}
    )
    assert resp.status_code == 201
    view = resp.json()
    assert view["name"] == view_payload["name"]
    assert view["owner_subject_id"] == "actor-a"
    view_id = view["saved_view_id"]

    # 2. listSavedViews
    resp_list = client.get("/api/v1/saved-views", headers=HEADERS_A)
    assert resp_list.status_code == 200
    views = resp_list.json()
    assert len(views) >= 1
    assert any(v["saved_view_id"] == view_id for v in views)

    # listSavedViews tenant isolation (actor-b should NOT see actor-a's saved view)
    resp_list_b = client.get("/api/v1/saved-views", headers=HEADERS_B)
    assert resp_list_b.status_code == 200
    views_b = resp_list_b.json()
    assert not any(v["saved_view_id"] == view_id for v in views_b)


def test_assignment_actions(client: TestClient) -> None:
    # Setup a fake assignment id in the system
    # We can assign an intake using the assignIntake endpoint first
    payload = {
        "original_url": "https://example.com/listings/assign-test",
        "scope": {"tenant_id": "tenant-a"}
    }
    resp = client.post(
        "/api/v1/intakes/url",
        json=payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-setup-{uuid4()}"}
    )
    intake_id = resp.json()["intake_id"]

    resp_assign = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": "actor-a",
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-action-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assignment_id = resp_assign.json()["assignment_id"]
    version = resp_assign.json()["version"]

    # 1. claimAssignment
    resp_claim = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/claim",
        json={"reason": "Claiming assignment for manual triage review"},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-claim-{uuid4()}", "If-Match": f'W/"{version}"'}
    )
    assert resp_claim.status_code == 200
    claim_receipt = resp_claim.json()
    assert claim_receipt["status"] == "CLAIMED"
    version_after_claim = claim_receipt["version"]

    # 2. transferAssignment
    resp_transfer = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/transfer",
        json={
            "target_owner_subject_id": "actor-c",
            "target_owner_role": "reviewer",
            "reason": "Escalate to senior reviewer",
            "handoff_note": "Awaiting escalation triage review",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-transfer-{uuid4()}", "If-Match": f'W/"{version_after_claim}"'}
    )
    assert resp_transfer.status_code == 200
    transfer_receipt = resp_transfer.json()
    assert transfer_receipt["status"] == "ASSIGNED"
    assert transfer_receipt["owner_subject_id"] == "actor-c"
    version_after_transfer = transfer_receipt["version"]

    # 3. completeAssignment
    resp_complete = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/complete",
        json={"reason": "Completed manual triage review with no issues found"},
        headers={**HEADERS_C, "Idempotency-Key": f"idem-complete-{uuid4()}", "If-Match": f'W/"{version_after_transfer}"'}
    )
    assert resp_complete.status_code == 200
    assert resp_complete.json()["status"] == "COMPLETED"




def test_sla_actions(client: TestClient) -> None:
    sla_id = f"sla-{uuid4()}"

    # 1. pauseSla
    resp_pause = client.post(
        f"/api/v1/sla-instances/{sla_id}/actions/pause",
        json={"reason": "Awaiting customer feedback", "expected_resume_at": "2026-07-26T12:00:00Z"},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-pause-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_pause.status_code == 200
    pause_receipt = resp_pause.json()
    assert pause_receipt["state"] == "PAUSED"
    assert pause_receipt["sla_instance_id"] == sla_id
    version = pause_receipt["version"]

    # 2. resumeSla
    resp_resume = client.post(
        f"/api/v1/sla-instances/{sla_id}/actions/resume",
        json={"reason": "Resuming SLA triage"},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-resume-{uuid4()}", "If-Match": f'W/"{version}"'}
    )
    assert resp_resume.status_code == 200
    assert resp_resume.json()["state"] == "ON_TRACK"



def test_identity_and_match_case_operations(client: TestClient) -> None:
    match_case_id = f"mc-{uuid4()}"

    # 1. decideMatchCase
    decision_payload = {
        "decision_type": "MERGE",
        "reason": "Same physical property verified",
        "risk_acknowledged": True,
        "target_property_id": "prop-123"
    }
    resp_decide = client.post(
        f"/api/v1/match-cases/{match_case_id}/decisions",
        json=decision_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-decide-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_decide.status_code == 201
    decision = resp_decide.json()
    assert decision["status"] == "PENDING_REVIEW"
    decision_id = decision["decision_id"]

    # 2. getIdentityDecision
    resp_get = client.get(f"/api/v1/identity-decisions/{decision_id}", headers=HEADERS_A)
    assert resp_get.status_code == 200
    assert resp_get.json()["decision_id"] == decision_id

    # 3. reviewIdentityDecision
    review_payload = {
        "decision": "APPROVE",
        "reason": "Independent manager review approved",
        "risk_acknowledged": True
    }
    resp_rev = client.post(
        f"/api/v1/identity-decisions/{decision_id}/actions/review",
        json=review_payload,
        headers={**HEADERS_A_REVIEWER, "Idempotency-Key": f"idem-rev-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_rev.status_code == 200
    assert resp_rev.json()["status"] == "APPROVED"
    version_after_rev = resp_rev.headers["ETag"].strip('W/"')

    # 4. requestIdentityDecisionReversal
    resp_reverse = client.post(
        f"/api/v1/identity-decisions/{decision_id}/actions/reverse",
        json={"reason": "Reversing incorrect merge", "risk_acknowledged": True},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-reverse-{uuid4()}", "If-Match": f'W/"{version_after_rev}"'}
    )
    assert resp_reverse.status_code == 202
    assert resp_reverse.json()["status"] == "REVERSAL_PENDING"



def test_identity_graph_mutations(client: TestClient) -> None:
    # 1. mergeProperties
    merge_payload = {
        "source_property_ids": ["prop-1", "prop-2"],
        "target_property_id": "prop-target",
        "reason": "Duplicate properties merged",
        "risk_acknowledged": True
    }
    resp_merge = client.post(
        "/api/v1/identity/merge",
        json=merge_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-merge-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_merge.status_code == 202
    assert resp_merge.json()["status"] == "PENDING_REVIEW"

    # 2. splitProperty
    split_payload = {
        "source_property_id": "prop-target",
        "partitions": [
            {
                "target_property_id": "prop-split-1",
                "source_identity_edge_ids": ["edge-1"]
            },
            {
                "target_property_id": "prop-split-2",
                "source_identity_edge_ids": ["edge-2"]
            }
        ],
        "reason": "Property split by steward",
        "risk_acknowledged": True
    }
    resp_split = client.post(
        "/api/v1/identity/split",
        json=split_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-split-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_split.status_code == 202
    assert resp_split.json()["status"] == "PENDING_REVIEW"

    # 3. unmergeProperty
    unmerge_payload = {
        "original_decision_id": f"dec-{uuid4()}",
        "replacement_edges": [
            {
                "target_property_id": "prop-1",
                "source_identity_edge_ids": ["edge-1"]
            }
        ],
        "reason": "Unmerging incorrect match",
        "risk_acknowledged": True
    }
    resp_unmerge = client.post(
        "/api/v1/identity/unmerge",
        json=unmerge_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-unmerge-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_unmerge.status_code == 202
    assert resp_unmerge.json()["status"] == "PENDING_REVIEW"
