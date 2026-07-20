from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app
from scripts.generate_assisted_listing_intake_client import ARTIFACT

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
ACTOR_A = "00000000-0000-0000-0000-000000000101"
ACTOR_A_REVIEWER = "00000000-0000-0000-0000-000000000102"
ACTOR_C = "00000000-0000-0000-0000-000000000103"
ACTOR_B = "00000000-0000-0000-0000-000000000104"
OWNER_STEWARD = "00000000-0000-0000-0000-000000000105"
BRAND_X = "00000000-0000-0000-0000-000000000201"

EFFECTIVE_OPENAPI = json.loads(ARTIFACT.read_text())
RUNTIME_SUCCESS_OPERATIONS: set[str] = set()


def _resolve_contract(node: object) -> object:
    if isinstance(node, dict):
        if "$ref" in node:
            resolved: object = EFFECTIVE_OPENAPI
            for part in node["$ref"].removeprefix("#/").split("/"):
                assert isinstance(resolved, dict)
                resolved = resolved[part]
            return _resolve_contract(resolved)
        return {key: _resolve_contract(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_contract(value) for value in node]
    return node


def _contract_operation(method: str, path: str) -> dict | None:
    for template, path_item in EFFECTIVE_OPENAPI["paths"].items():
        segments = ["[^/]+" if part.startswith("{") else re.escape(part) for part in template.split("/")]
        pattern = "^/api" + "/".join(segments) + "$"
        if re.fullmatch(pattern, path):
            return path_item.get(method.lower())
    return None


class ContractTestClient(TestClient):
    """Validate every exercised declared response against the effective bundle."""

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(method, url, **kwargs)
        operation = _contract_operation(method, response.request.url.path)
        if operation is None:
            return response

        status = str(response.status_code)
        declared = operation.get("responses", {}).get(status)
        if response.status_code < 400:
            assert declared is not None, (
                f"successful runtime status {status} is not declared for "
                f"{operation['operationId']}"
            )
        if declared is not None:
            resolved_response = _resolve_contract(declared)
            assert isinstance(resolved_response, dict)
            for header_name in resolved_response.get("headers", {}):
                assert header_name in response.headers, (
                    f"runtime response for {operation['operationId']} {status} "
                    f"is missing declared header {header_name}"
                )
            schema = resolved_response.get("content", {}).get("application/json", {}).get("schema")
            if schema is not None:
                resolved_schema = _resolve_contract(schema)
                Draft202012Validator(
                    resolved_schema,
                    format_checker=FormatChecker(),
                ).validate(response.json())
        if response.status_code < 400:
            RUNTIME_SUCCESS_OPERATIONS.add(operation["operationId"])
        return response

# Standard headers for authenticating as tenant-a
HEADERS_A = {
    "x-subject-id": ACTOR_A,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for reviewer in tenant-a (to avoid self-review errors)
HEADERS_A_REVIEWER = {
    "x-subject-id": ACTOR_A_REVIEWER,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for actor-c in tenant-a
HEADERS_C = {
    "x-subject-id": ACTOR_C,
    "x-tenant-id": TENANT_A,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}

# Standard headers for authenticating as tenant-b
HEADERS_B = {
    "x-subject-id": ACTOR_B,
    "x-tenant-id": TENANT_B,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}




@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return ContractTestClient(app)


def _store_with_intake(intake_id: str) -> AssistedIntakeStore:
    return next(
        store
        for store in reversed(AssistedIntakeStore._instances)
        if intake_id in store.intakes
    )


MUTATION_VALIDATION_HEADERS = {
    **HEADERS_A,
    "Idempotency-Key": "negative-contract-key-0001",
    "If-Match": 'W/"1"',
}

NEGATIVE_OPERATION_CASES = [
    pytest.param(
        "listIntakes",
        "GET",
        "/api/v1/intakes",
        {"params": {"submitted_by": "invalid-uuid"}, "headers": HEADERS_A},
        400,
        id="listIntakes",
    ),
    pytest.param(
        "submitUrlIntake",
        "POST",
        "/api/v1/intakes/url",
        {
            "json": {},
            "headers": {
                **HEADERS_A,
                "Idempotency-Key": "negative-contract-key-0002",
            },
        },
        422,
        id="submitUrlIntake",
    ),
    pytest.param(
        "submitIntakeBatch",
        "POST",
        "/api/v1/intake-batches",
        {
            "json": {},
            "headers": {
                **HEADERS_A,
                "Idempotency-Key": "negative-contract-key-0003",
            },
        },
        422,
        id="submitIntakeBatch",
    ),
    pytest.param(
        "getIntake",
        "GET",
        "/api/v1/intakes/invalid-uuid",
        {"headers": HEADERS_A},
        404,
        id="getIntake",
    ),
    pytest.param(
        "proposeCorrection",
        "POST",
        "/api/v1/intakes/invalid-uuid/corrections",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="proposeCorrection",
    ),
    pytest.param(
        "decideMatchCase",
        "POST",
        "/api/v1/match-cases/invalid-uuid/decisions",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="decideMatchCase",
    ),
    pytest.param(
        "mergeProperties",
        "POST",
        "/api/v1/identity/merge",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="mergeProperties",
    ),
    pytest.param(
        "splitProperty",
        "POST",
        "/api/v1/identity/split",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="splitProperty",
    ),
    pytest.param(
        "unmergeProperty",
        "POST",
        "/api/v1/identity/unmerge",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="unmergeProperty",
    ),
    pytest.param(
        "assignIntake",
        "PUT",
        f"/api/v1/intakes/{uuid4()}/assignment",
        {
            "json": {
                "owner_subject_id": ACTOR_A,
                "owner_role": "reviewer",
                "due_at": "invalid-date",
                "reason": "Invalid date contract check",
            },
            "headers": MUTATION_VALIDATION_HEADERS,
        },
        422,
        id="assignIntake",
    ),
    pytest.param(
        "retryJob",
        "POST",
        "/api/v1/jobs/invalid-uuid/retry",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="retryJob",
    ),
    pytest.param(
        "listSavedViews",
        "GET",
        "/api/v1/saved-views",
        {
            "headers": {
                "x-subject-id": ACTOR_A,
                "x-tenant-id": TENANT_A,
                "x-roles": "franchisee",
            }
        },
        403,
        id="listSavedViews",
    ),
    pytest.param(
        "createSavedView",
        "POST",
        "/api/v1/saved-views",
        {
            "json": {},
            "headers": {
                **HEADERS_A,
                "Idempotency-Key": "negative-contract-key-0004",
            },
        },
        422,
        id="createSavedView",
    ),
    pytest.param(
        "requestCandidatePromotion",
        "POST",
        "/api/v1/intakes/invalid-uuid/promotion-requests",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="requestCandidatePromotion",
    ),
    pytest.param(
        "getPromotionDecision",
        "GET",
        "/api/v1/promotion-decisions/invalid-uuid",
        {"headers": HEADERS_A},
        404,
        id="getPromotionDecision",
    ),
    pytest.param(
        "reviewPromotionDecision",
        "POST",
        "/api/v1/promotion-decisions/invalid-uuid/actions/review",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="reviewPromotionDecision",
    ),
    pytest.param(
        "cancelIntake",
        "POST",
        "/api/v1/intakes/invalid-uuid/actions/cancel",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="cancelIntake",
    ),
    pytest.param(
        "quarantineIntake",
        "POST",
        "/api/v1/intakes/invalid-uuid/actions/quarantine",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="quarantineIntake",
    ),
    pytest.param(
        "reopenIntake",
        "POST",
        "/api/v1/intakes/invalid-uuid/actions/reopen",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="reopenIntake",
    ),
    pytest.param(
        "claimAssignment",
        "POST",
        "/api/v1/assignments/invalid-uuid/actions/claim",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="claimAssignment",
    ),
    pytest.param(
        "transferAssignment",
        "POST",
        "/api/v1/assignments/invalid-uuid/actions/transfer",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="transferAssignment",
    ),
    pytest.param(
        "completeAssignment",
        "POST",
        "/api/v1/assignments/invalid-uuid/actions/complete",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="completeAssignment",
    ),
    pytest.param(
        "pauseSla",
        "POST",
        "/api/v1/sla-instances/invalid-uuid/actions/pause",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="pauseSla",
    ),
    pytest.param(
        "resumeSla",
        "POST",
        "/api/v1/sla-instances/invalid-uuid/actions/resume",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="resumeSla",
    ),
    pytest.param(
        "getIdentityDecision",
        "GET",
        "/api/v1/identity-decisions/invalid-uuid",
        {"headers": HEADERS_A},
        404,
        id="getIdentityDecision",
    ),
    pytest.param(
        "reviewIdentityDecision",
        "POST",
        "/api/v1/identity-decisions/invalid-uuid/actions/review",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="reviewIdentityDecision",
    ),
    pytest.param(
        "requestIdentityDecisionReversal",
        "POST",
        "/api/v1/identity-decisions/invalid-uuid/actions/reverse",
        {"json": {}, "headers": MUTATION_VALIDATION_HEADERS},
        422,
        id="requestIdentityDecisionReversal",
    ),
]


@pytest.mark.parametrize(
    ("operation_id", "method", "path", "request_kwargs", "expected_status"),
    NEGATIVE_OPERATION_CASES,
)
def test_every_operation_executes_a_declared_negative_error_schema(
    client: ContractTestClient,
    operation_id: str,
    method: str,
    path: str,
    request_kwargs: dict,
    expected_status: int,
) -> None:
    response = client.request(method, path, **request_kwargs)
    operation = _contract_operation(method, response.request.url.path)

    assert operation is not None
    assert operation["operationId"] == operation_id
    assert response.status_code == expected_status
    declared = operation["responses"].get(str(response.status_code))
    assert declared is not None, (
        f"{operation_id} returned undeclared negative status "
        f"{response.status_code}"
    )
    resolved_response = _resolve_contract(declared)
    assert isinstance(resolved_response, dict)
    schema = resolved_response.get("content", {}).get("application/json", {}).get("schema")
    assert schema is not None, f"{operation_id} negative response lacks an error schema"
    Draft202012Validator(
        _resolve_contract(schema),
        format_checker=FormatChecker(),
    ).validate(response.json())


def test_authentication_and_tenant_isolation_boundary(client: TestClient) -> None:
    # 1. 401 Unauthorized when missing x-subject-id (not authenticated)
    resp = client.get("/api/v1/intakes", headers={"x-tenant-id": TENANT_A})
    assert resp.status_code == 401
    assert "code" in resp.json()

    # 2. 403 Forbidden when missing x-tenant-id
    resp = client.get("/api/v1/intakes", headers={"x-subject-id": ACTOR_A})
    assert resp.status_code == 403
    assert "code" in resp.json()



def test_url_intake_and_concurrency_lifecycle(client: TestClient) -> None:
    # 1. submitUrlIntake (POST /api/v1/intakes/url)
    payload = {
        "original_url": "https://example.com/listings/123",
        "scope": {
            "tenant_id": TENANT_A,
            "brand_id": BRAND_X,
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
    assert detail["scope"]["tenant_id"] == TENANT_A

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
        "owner_subject_id": OWNER_STEWARD,
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

    cross_tenant_assign = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json=assign_payload,
        headers={
            **HEADERS_B,
            "Idempotency-Key": f"idem-assign-cross-{uuid4()}",
            "If-Match": f'W/"{version}"',
        },
    )
    assert cross_tenant_assign.status_code == 403
    assert cross_tenant_assign.json()["code"] == "TENANT_SCOPE_DENIED"

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
    assert assign_receipt["owner_subject_id"] == OWNER_STEWARD
    new_version = assign_receipt["version"]

    store = _store_with_intake(intake_id)
    store.intakes[intake_id]["state"] = "NEEDS_REVIEW"

    # 5. proposeCorrection (POST /api/v1/intakes/{id}/corrections)
    correct_payload = {
        "field_path": "rent_amount",
        "corrected_value": 1500.0,
        "reason": "Rent correction changes listing identity and matching risk",
        "risk_acknowledged": True,
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
    assert correct_receipt["status"] == "PENDING_REVIEW"
    assert correct_receipt["intake_id"] == intake_id
    assert not any(
        field.get("field_path") == "rent_amount"
        for field in store.intakes[intake_id]["fields"]
    )

    self_review = client.post(
        f"/api/v1/identity-decisions/{correct_receipt['correction_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "The proposer must not approve the correction",
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-correct-self-review-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert self_review.status_code == 403
    assert self_review.json()["code"] == "SELF_REVIEW_DENIED"

    correction_review = client.post(
        f"/api/v1/identity-decisions/{correct_receipt['correction_id']}/actions/review",
        json={
            "decision": "APPROVE",
            "reason": "Independent reviewer approves the attributable rent correction",
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS_A_REVIEWER,
            "Idempotency-Key": f"idem-correct-review-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert correction_review.status_code == 200
    assert correction_review.json()["status"] == "APPROVED"
    assert "ETag" in correction_review.headers
    assert any(
        field.get("field_path") == "rent_amount"
        and field.get("corrected") == 1500.0
        for field in store.intakes[intake_id]["fields"]
    )
    version_after_correct = store.intakes[intake_id]["version"]

    store.intakes[intake_id]["state"] = "NEEDS_REVIEW"

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
    assert quar_receipt["from_state"] == "NEEDS_REVIEW"
    assert quar_receipt["to_state"] == "QUARANTINED"
    version_after_quar = quar_receipt["version_after"]

    # 7. reopenIntake requires an independent second actor. The first call only
    # records the release proposal and leaves the intake quarantined.
    reopen_proposal = client.post(
        f"/api/v1/intakes/{intake_id}/actions/reopen",
        json={"reason": "Reopen after incident resolution", "risk_acknowledged": True},
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-reopen-{uuid4()}",
            "If-Match": f'W/"{version_after_quar}"'
        }
    )
    assert reopen_proposal.status_code == 200
    assert reopen_proposal.json()["to_state"] == "QUARANTINED"
    pending_reopen_version = reopen_proposal.json()["version_after"]

    self_reopen_review = client.post(
        f"/api/v1/intakes/{intake_id}/actions/reopen",
        json={
            "reason": "The proposer must not release their own quarantine",
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-reopen-self-review-{uuid4()}",
            "If-Match": f'W/"{pending_reopen_version}"',
        },
    )
    assert self_reopen_review.status_code == 403
    assert self_reopen_review.json()["code"] == "SELF_REVIEW_DENIED"

    resp_reopen = client.post(
        f"/api/v1/intakes/{intake_id}/actions/reopen",
        json={"reason": "Independent review confirms the quarantine cause is resolved", "risk_acknowledged": True},
        headers={
            **HEADERS_A_REVIEWER,
            "Idempotency-Key": f"idem-reopen-review-{uuid4()}",
            "If-Match": f'W/"{pending_reopen_version}"',
        },
    )
    assert resp_reopen.status_code == 200
    reopen_receipt = resp_reopen.json()
    assert reopen_receipt["from_state"] == "QUARANTINED"
    assert reopen_receipt["to_state"] == "CHECKING_SOURCE_POLICY"
    version_after_reopen = reopen_receipt["version_after"]

    # 8. cancelIntake on a separate SUBMITTED intake.
    cancel_setup = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.com/listings/cancel", "scope": {"tenant_id": TENANT_A}},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-cancel-setup-{uuid4()}"},
    )
    cancel_intake_id = cancel_setup.json()["intake_id"]
    cancel_payload = {"reason": "Duplicate submission"}
    resp_cancel = client.post(
        f"/api/v1/intakes/{cancel_intake_id}/actions/cancel",
        json=cancel_payload,
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-cancel-{uuid4()}",
            "If-Match": 'W/"1"',
        }
    )
    assert resp_cancel.status_code == 200
    cancel_receipt = resp_cancel.json()
    assert cancel_receipt["from_state"] == "SUBMITTED"
    assert cancel_receipt["to_state"] == "CANCELLED"
    version_after_cancel = cancel_receipt["version_after"]

    cancelled_quarantine = client.post(
        f"/api/v1/intakes/{cancel_intake_id}/actions/quarantine",
        json={"reason": "Invalid transition regression", "risk_acknowledged": True},
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-cancelled-quarantine-{uuid4()}",
            "If-Match": f'W/"{version_after_cancel}"',
        },
    )
    assert cancelled_quarantine.status_code == 409
    assert cancelled_quarantine.json()["code"] == "WORKFLOW_STATE_DENIED"

    # Transition the intake state to READY in store to satisfy the promotion prerequisite
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
            "If-Match": f'W/"{version_after_reopen}"'
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
    assert "ETag" in resp_get_promo.headers

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
        "batch_id": str(uuid4()),
        "method": "MANUAL",
        "scope": {
            "tenant_id": TENANT_A
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
        "scope": {"tenant_id": TENANT_A}
    }
    resp = client.post(
        "/api/v1/intakes/url",
        json=payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-job-{uuid4()}"}
    )
    receipt = resp.json()
    job_id = receipt["job_id"]
    store = _store_with_intake(receipt["intake_id"])
    store.jobs[job_id]["status"] = "FAILED"
    store.jobs[job_id]["checkpoint"] = "PARSING"
    store.intakes[receipt["intake_id"]]["state"] = "FAILED"

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


def test_expansion_staff_can_retry_their_own_failed_intake(
    client: TestClient,
) -> None:
    staff_headers = {
        "x-subject-id": ACTOR_A,
        "x-tenant-id": TENANT_A,
        "x-roles": "expansion_user",
    }
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/listings/staff-owned-retry",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-staff-retry-setup-{uuid4()}",
        },
    )
    assert submitted.status_code == 202
    receipt = submitted.json()
    store = _store_with_intake(receipt["intake_id"])
    store.jobs[receipt["job_id"]]["status"] = "FAILED"
    store.jobs[receipt["job_id"]]["checkpoint"] = "PARSING"
    store.intakes[receipt["intake_id"]]["state"] = "FAILED"

    retried = client.post(
        f"/api/v1/jobs/{receipt['job_id']}/retry",
        json={
            "checkpoint": "PARSING",
            "reason": "Retry the staff member's own transient parser failure",
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-staff-retry-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )

    assert retried.status_code == 202
    assert retried.json()["status"] == "QUEUED"


def test_retry_exact_replay_precedes_mutable_ownership_authorization(
    client: TestClient,
) -> None:
    staff_headers = {
        "x-subject-id": ACTOR_A,
        "x-tenant-id": TENANT_A,
        "x-roles": "expansion_user",
    }
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/listings/staff-retry-replay",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-retry-replay-setup-{uuid4()}",
        },
    )
    receipt = submitted.json()
    store = _store_with_intake(receipt["intake_id"])
    job = store.jobs[receipt["job_id"]]
    intake = store.intakes[receipt["intake_id"]]
    job["status"] = "FAILED"
    job["checkpoint"] = "PARSING"
    intake["state"] = "FAILED"
    retry_body = {
        "checkpoint": "PARSING",
        "reason": "Retry and preserve the immutable accepted receipt",
    }
    retry_headers = {
        **staff_headers,
        "Idempotency-Key": f"idem-retry-replay-{uuid4()}",
        "If-Match": 'W/"1"',
    }

    first = client.post(
        f"/api/v1/jobs/{receipt['job_id']}/retry",
        json=retry_body,
        headers=retry_headers,
    )
    assert first.status_code == 202

    # Ownership is mutable after acceptance. It must not invalidate replay of
    # the exact prior command and receipt for the same actor.
    intake["submitted_by"] = ACTOR_C
    intake["assigned_to"] = ACTOR_C
    replayed = client.post(
        f"/api/v1/jobs/{receipt['job_id']}/retry",
        json=retry_body,
        headers=retry_headers,
    )

    assert replayed.status_code == 202
    assert replayed.json() == first.json()


def test_retry_orphan_job_fails_closed_for_same_tenant_staff(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/listings/orphan-retry",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-orphan-retry-setup-{uuid4()}",
        },
    )
    receipt = submitted.json()
    store = _store_with_intake(receipt["intake_id"])
    store.jobs[receipt["job_id"]]["status"] = "FAILED"
    store.jobs[receipt["job_id"]]["checkpoint"] = "PARSING"
    del store.intakes[receipt["intake_id"]]

    response = client.post(
        f"/api/v1/jobs/{receipt['job_id']}/retry",
        json={
            "checkpoint": "PARSING",
            "reason": "Attempt to retry a job without its authorization resource",
        },
        headers={
            "x-subject-id": ACTOR_C,
            "x-tenant-id": TENANT_A,
            "x-roles": "expansion_user",
            "Idempotency-Key": f"idem-orphan-retry-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "DEPENDENCY_CONFLICT"


def test_saved_views_operations(client: TestClient) -> None:
    view_payload = {
        "name": "My Active Intakes",
        "query": {"state": "SUBMITTED"},
        "resource": "intake",
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
    assert view["owner_subject_id"] == ACTOR_A
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
        "scope": {"tenant_id": TENANT_A}
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
            "owner_subject_id": ACTOR_A,
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Triage assignment",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-action-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assignment_id = resp_assign.json()["assignment_id"]
    version = resp_assign.json()["version"]

    # 1. claimAssignment
    claim_body = {"reason": "Claiming assignment for manual triage review"}
    claim_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-claim-{uuid4()}",
        "If-Match": f'W/"{version}"',
    }
    resp_claim = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/claim",
        json=claim_body,
        headers=claim_headers,
    )
    assert resp_claim.status_code == 200
    claim_receipt = resp_claim.json()
    assert claim_receipt["status"] == "CLAIMED"
    version_after_claim = claim_receipt["version"]

    # 2. transferAssignment
    resp_transfer = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/transfer",
        json={
            "target_owner_subject_id": ACTOR_C,
            "target_owner_role": "reviewer",
            "reason": "Escalate to senior reviewer",
            "handoff_note": "Awaiting escalation triage review",
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-transfer-{uuid4()}", "If-Match": f'W/"{version_after_claim}"'}
    )
    assert resp_transfer.status_code == 200
    transfer_receipt = resp_transfer.json()
    assert transfer_receipt["status"] == "TRANSFERRED"
    assert transfer_receipt["owner_subject_id"] == ACTOR_C
    version_after_transfer = transfer_receipt["version"]

    # A lost-response replay is immutable even though the transfer changed
    # current ownership after the first claim was committed.
    replayed_claim = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/claim",
        json=claim_body,
        headers=claim_headers,
    )
    assert replayed_claim.status_code == 200
    assert replayed_claim.json() == resp_claim.json()
    assert replayed_claim.headers["ETag"] == resp_claim.headers["ETag"]

    # The target owner accepts the transfer by claiming it through the API.
    resp_transferred_claim = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/claim",
        json={"reason": "Accepting the transferred assignment"},
        headers={
            **HEADERS_C,
            "Idempotency-Key": f"idem-claim-transfer-{uuid4()}",
            "If-Match": f'W/"{version_after_transfer}"',
        },
    )
    assert resp_transferred_claim.status_code == 200
    assert resp_transferred_claim.json()["status"] == "CLAIMED"
    version_after_transferred_claim = resp_transferred_claim.json()["version"]

    # 3. completeAssignment
    resp_complete = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/complete",
        json={"reason": "Completed manual triage review with no issues found"},
        headers={
            **HEADERS_C,
            "Idempotency-Key": f"idem-complete-{uuid4()}",
            "If-Match": f'W/"{version_after_transferred_claim}"',
        },
    )
    assert resp_complete.status_code == 200
    assert resp_complete.json()["status"] == "COMPLETED"




def test_sla_actions(client: TestClient) -> None:
    sla_id = str(uuid4())
    latest_store = AssistedIntakeStore._instances[-1]
    latest_store.slas[sla_id] = {
        "sla_instance_id": sla_id,
        "state": "ON_TRACK",
        "due_at": "2026-07-25T12:00:00Z",
        "paused_duration_seconds": 0,
        "version": 1,
        "audit_event_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "tenant_id": TENANT_A,
    }

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
    match_case_id = str(uuid4())

    # 1. decideMatchCase
    decision_payload = {
        "decision_type": "MERGE",
        "reason": "Same physical property verified",
        "risk_acknowledged": True,
        "target_property_id": str(uuid4())
    }
    resp_decide = client.post(
        f"/api/v1/match-cases/{match_case_id}/decisions",
        json=decision_payload,
        headers={**HEADERS_A, "Idempotency-Key": f"idem-decide-{uuid4()}", "If-Match": 'W/"1"'}
    )
    assert resp_decide.status_code == 201
    assert "ETag" in resp_decide.headers
    decision = resp_decide.json()
    assert decision["status"] == "PENDING_REVIEW"
    decision_id = decision["decision_id"]

    # 2. getIdentityDecision
    resp_get = client.get(f"/api/v1/identity-decisions/{decision_id}", headers=HEADERS_A)
    assert resp_get.status_code == 200
    assert resp_get.json()["decision_id"] == decision_id
    assert "ETag" in resp_get.headers

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

    # Execution is owned by the identity service; reversal is only legal once
    # that durable graph mutation has reached EXECUTED.
    latest_store = AssistedIntakeStore._instances[-1]
    latest_store.decisions[decision_id]["status"] = "EXECUTED"

    # 4. requestIdentityDecisionReversal
    resp_reverse = client.post(
        f"/api/v1/identity-decisions/{decision_id}/actions/reverse",
        json={"reason": "Reversing incorrect merge", "risk_acknowledged": True},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-reverse-{uuid4()}", "If-Match": f'W/"{version_after_rev}"'}
    )
    assert resp_reverse.status_code == 202
    assert resp_reverse.json()["status"] == "REVERSAL_PENDING"



def test_identity_graph_mutations(client: TestClient) -> None:
    source_property_1 = str(uuid4())
    source_property_2 = str(uuid4())
    target_property = str(uuid4())
    split_property_1 = str(uuid4())
    split_property_2 = str(uuid4())
    edge_1 = str(uuid4())
    edge_2 = str(uuid4())

    # 1. mergeProperties
    merge_payload = {
        "source_property_ids": [source_property_1, source_property_2],
        "target_property_id": target_property,
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
        "source_property_id": target_property,
        "partitions": [
            {
                "target_property_id": split_property_1,
                "source_identity_edge_ids": [edge_1]
            },
            {
                "target_property_id": split_property_2,
                "source_identity_edge_ids": [edge_2]
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
        "original_decision_id": str(uuid4()),
        "replacement_edges": [
            {
                "target_property_id": source_property_1,
                "source_identity_edge_ids": [edge_1]
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


def test_stateful_idempotency_replay_returns_the_original_receipt(client: TestClient) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.com/replay", "scope": {"tenant_id": TENANT_A}},
        headers={**HEADERS_A, "Idempotency-Key": f"idem-replay-setup-{uuid4()}"},
    )
    intake_id = submitted.json()["intake_id"]
    command = {"reason": "Duplicate submission replay verification"}
    headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-stateful-replay-{uuid4()}",
        "If-Match": 'W/"1"',
    }

    first = client.post(
        f"/api/v1/intakes/{intake_id}/actions/cancel",
        json=command,
        headers=headers,
    )
    replayed = client.post(
        f"/api/v1/intakes/{intake_id}/actions/cancel",
        json=command,
        headers=headers,
    )

    assert first.status_code == replayed.status_code == 200
    assert replayed.json() == first.json()
    assert replayed.headers["ETag"] == first.headers["ETag"]


def test_unassigned_intake_is_not_owned_by_unrelated_same_tenant_staff(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/unassigned-ownership",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-owner-setup-{uuid4()}"},
    )
    intake_id = submitted.json()["intake_id"]
    unrelated_staff_headers = {
        "x-subject-id": ACTOR_C,
        "x-tenant-id": TENANT_A,
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-staff",
    }

    response = client.get("/api/v1/intakes", headers=unrelated_staff_headers)

    assert response.status_code == 200
    assert intake_id not in {item["intake_id"] for item in response.json()["items"]}

    detail = client.get(
        f"/api/v1/intakes/{intake_id}", headers=unrelated_staff_headers
    )
    assert detail.status_code == 403
    assert detail.json()["code"] == "OWNERSHIP_REQUIRED"

    cancelled = client.post(
        f"/api/v1/intakes/{intake_id}/actions/cancel",
        json={"reason": "Attempt to cancel another staff member's intake"},
        headers={
            **unrelated_staff_headers,
            "Idempotency-Key": f"idem-unrelated-cancel-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert cancelled.status_code == 403
    assert cancelled.json()["code"] == "OWNERSHIP_REQUIRED"


def test_restricted_v1_fields_are_masked_in_the_http_detail_response(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/restricted-field-masking",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-mask-setup-{uuid4()}"},
    )
    intake_id = submitted.json()["intake_id"]
    store = _store_with_intake(intake_id)
    store.intakes[intake_id]["fields"] = [
        {
            "field_path": "broker.contact_phone",
            "classification": "RESTRICTED",
            "masked": False,
            "parsed": "+886-2-5555-0101",
            "normalized": "+886255550101",
            "corrected": "+886-2-5555-0199",
            "effective": "+886-2-5555-0199",
            "confidence": 0.99,
        }
    ]

    response = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS_A)

    assert response.status_code == 200
    field = response.json()["fields"][0]
    assert field["parsed"] is None
    assert field["normalized"] is None
    assert field["corrected"] is None
    assert field["effective"] is None
    assert field["masked"] is True
    assert field["mask_reason_code"] == "FIELD_MASKED"


def test_assignment_replay_is_immutable_after_the_assignment_is_claimed(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/assignment-receipt-replay",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-assign-replay-setup-{uuid4()}"},
    )
    intake_id = submitted.json()["intake_id"]
    assignment_body = {
        "owner_subject_id": ACTOR_A,
        "owner_role": "reviewer",
        "due_at": "2026-07-25T12:00:00Z",
        "reason": "Preserve the original assignment receipt",
    }
    assignment_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-assignment-receipt-{uuid4()}",
        "If-Match": 'W/"1"',
    }
    first = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json=assignment_body,
        headers=assignment_headers,
    )
    assignment_id = first.json()["assignment_id"]
    claimed = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/claim",
        json={"reason": "Claim before the lost assignment response is retried"},
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-claim-before-replay-{uuid4()}",
            "If-Match": first.headers["ETag"],
        },
    )
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "CLAIMED"

    replayed = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json=assignment_body,
        headers=assignment_headers,
    )

    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    assert replayed.headers["ETag"] == first.headers["ETag"]


def test_staff_transfer_lost_response_replays_after_ownership_changes(
    client: TestClient,
) -> None:
    staff_headers = {
        "x-subject-id": ACTOR_A,
        "x-tenant-id": TENANT_A,
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-staff",
    }
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/staff-transfer-replay",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-transfer-replay-setup-{uuid4()}",
        },
    )
    intake_id = submitted.json()["intake_id"]
    assigned = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": ACTOR_A,
            "owner_role": "expansion-staff",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Assign the submitted intake to its staff owner",
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-transfer-replay-assign-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert assigned.status_code == 200
    assignment_id = assigned.json()["assignment_id"]
    transfer_body = {
        "target_owner_subject_id": ACTOR_C,
        "target_owner_role": "reviewer",
        "reason": "Transfer to the reviewing operator",
        "handoff_note": "Review the source evidence before deciding",
    }
    transfer_headers = {
        **staff_headers,
        "Idempotency-Key": f"idem-staff-transfer-replay-{uuid4()}",
        "If-Match": assigned.headers["ETag"],
    }

    first = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/transfer",
        json=transfer_body,
        headers=transfer_headers,
    )
    replayed = client.post(
        f"/api/v1/assignments/{assignment_id}/actions/transfer",
        json=transfer_body,
        headers=transfer_headers,
    )

    assert first.status_code == replayed.status_code == 200
    assert replayed.json() == first.json()
    assert replayed.headers["ETag"] == first.headers["ETag"]


def test_assign_intake_rejects_a_second_active_assignment_with_fresh_etag(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/active-owner-conflict",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-owner-conflict-setup-{uuid4()}",
        },
    )
    intake_id = submitted.json()["intake_id"]
    first = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": ACTOR_A,
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Create the active assignment",
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-owner-conflict-first-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )
    assert first.status_code == 200

    second = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": ACTOR_C,
            "owner_role": "reviewer",
            "due_at": "2026-07-26T12:00:00Z",
            "reason": "Attempt to create another active assignment",
        },
        headers={
            **HEADERS_A,
            "Idempotency-Key": f"idem-owner-conflict-second-{uuid4()}",
            "If-Match": first.headers["ETag"],
        },
    )

    assert second.status_code == 409
    assert second.json()["code"] == "OWNER_CONFLICT"


def test_assignment_idempotency_is_scoped_to_the_path_intake(
    client: TestClient,
) -> None:
    intake_ids = []
    for suffix in ("first", "second"):
        submitted = client.post(
            "/api/v1/intakes/url",
            json={
                "original_url": f"https://example.com/resource-idempotency/{suffix}",
                "scope": {"tenant_id": TENANT_A},
            },
            headers={
                **HEADERS_A,
                "Idempotency-Key": f"idem-resource-setup-{suffix}-{uuid4()}",
            },
        )
        assert submitted.status_code == 202
        intake_ids.append(submitted.json()["intake_id"])

    assignment_body = {
        "owner_subject_id": ACTOR_A,
        "owner_role": "reviewer",
        "due_at": "2026-07-25T12:00:00Z",
        "reason": "The same command applies independently to each intake",
    }
    shared_headers = {
        **HEADERS_A,
        "Idempotency-Key": f"idem-resource-scoped-{uuid4()}",
        "If-Match": 'W/"1"',
    }

    first = client.put(
        f"/api/v1/intakes/{intake_ids[0]}/assignment",
        json=assignment_body,
        headers=shared_headers,
    )
    second = client.put(
        f"/api/v1/intakes/{intake_ids[1]}/assignment",
        json=assignment_body,
        headers=shared_headers,
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["assignment_id"] != second.json()["assignment_id"]
    second_detail = client.get(
        f"/api/v1/intakes/{intake_ids[1]}", headers=HEADERS_A
    )
    assert second_detail.status_code == 200
    assert second_detail.json()["assigned_to"] == ACTOR_A


def test_expansion_staff_cannot_assign_an_owned_intake_across_users(
    client: TestClient,
) -> None:
    staff_headers = {
        "x-subject-id": ACTOR_A,
        "x-tenant-id": TENANT_A,
        "x-roles": "expansion_user",
        "x-operator-role": "expansion-staff",
    }
    submitted = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/staff-cross-user-assignment",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-staff-assign-setup-{uuid4()}",
        },
    )
    assert submitted.status_code == 202
    intake_id = submitted.json()["intake_id"]

    assigned = client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={
            "owner_subject_id": ACTOR_C,
            "owner_role": "reviewer",
            "due_at": "2026-07-25T12:00:00Z",
            "reason": "Attempt to route an owned intake to another user",
        },
        headers={
            **staff_headers,
            "Idempotency-Key": f"idem-staff-cross-user-{uuid4()}",
            "If-Match": 'W/"1"',
        },
    )

    assert assigned.status_code == 403
    assert assigned.json()["code"] == "ASSIGNMENT_SCOPE_DENIED"


def test_cursor_uses_configured_signing_and_keyset_snapshot_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_key = "cursor-signing-key-for-contract-tests-0001"
    monkeypatch.setenv("ODP_INTAKE_CURSOR_SIGNING_KEY", signing_key)
    client = ContractTestClient(create_app())

    submitted_ids = []
    for index in range(3):
        response = client.post(
            "/api/v1/intakes/url",
            json={
                "original_url": f"https://example.com/cursor/{index}",
                "scope": {"tenant_id": TENANT_A},
            },
            headers={
                **HEADERS_A,
                "Idempotency-Key": f"idem-cursor-{index}-{uuid4()}",
            },
        )
        submitted_ids.append(response.json()["intake_id"])

    first_page = client.get("/api/v1/intakes?page_size=2", headers=HEADERS_A)
    cursor = first_page.json()["next_cursor"]
    assert cursor is not None
    payload, encoded_signature = cursor.split(".")
    expected_signature = hmac.new(
        signing_key.encode(), payload.encode(), hashlib.sha256
    ).digest()
    actual_signature = base64.urlsafe_b64decode(
        encoded_signature + "=" * (-len(encoded_signature) % 4)
    )
    assert hmac.compare_digest(actual_signature, expected_signature)

    cursor_payload = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    assert "offset" not in cursor_payload
    assert cursor_payload["sort_tuple"][-1] == cursor_payload["last_resource_id"]

    inserted_after_snapshot = client.post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.com/cursor/post-snapshot",
            "scope": {"tenant_id": TENANT_A},
        },
        headers={**HEADERS_A, "Idempotency-Key": f"idem-cursor-new-{uuid4()}"},
    ).json()["intake_id"]
    second_page = client.get(
        "/api/v1/intakes",
        params={"cursor": cursor, "page_size": 2},
        headers=HEADERS_A,
    )

    first_ids = {item["intake_id"] for item in first_page.json()["items"]}
    assert second_page.status_code == 200
    second_ids = {item["intake_id"] for item in second_page.json()["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert inserted_after_snapshot not in second_ids
    assert first_ids | second_ids == set(submitted_ids)


def test_every_effective_operation_has_a_schema_valid_runtime_response() -> None:
    assert RUNTIME_SUCCESS_OPERATIONS == {
        operation["operationId"]
        for path_item in EFFECTIVE_OPENAPI["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
