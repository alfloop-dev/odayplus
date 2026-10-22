"""Promotion authority must follow the current canonical parent before any replay."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore, create_assisted_intake_router
from apps.api.oday_api.security import dependencies
from modules.listing.infrastructure.repositories import InMemoryListingRepository
from modules.opsboard.application.network_listings import InMemoryAssistedIntakeRepository
from shared.auth import Principal, Role, Scope

TENANT, OTHER_TENANT, REVIEWER, PROPOSER, INTAKE, PROMOTION, LISTING, JOB, CORRELATION = (
    str(uuid4()) for _ in range(9)
)
REVIEW_BODY = {"decision": "REJECT", "reason": "Synthetic independent rejection", "risk_acknowledged": True}
REVIEW_HEADERS = {"Idempotency-Key": "synthetic-authority-review", "If-Match": 'W/"2"',
                  "x-correlation-id": CORRELATION}
PROMOTION_PATH = f"/api/v1/promotion-decisions/{PROMOTION}"


def _snapshot(context):
    return deepcopy((context.store.intakes, context.store.promotions, context.store.jobs,
                     context.store.replays, context.legacy.intakes, context.legacy.promotions,
                     context.legacy.idempotency))


@pytest.fixture
def authority(monkeypatch):
    store = AssistedIntakeStore()
    store.intakes[INTAKE] = {
        "intake_id": INTAKE, "scope": {"tenant_id": TENANT, "assigned_area_id": "allowed-area"},
        "submitted_by": PROPOSER, "state": "READY", "version": 1,
        "operator_presentation": {"id": INTAKE, "tenantId": TENANT, "stage": "READY",
                                  "matchResult": {"targetListingId": LISTING}},
    }
    store.promotions[PROMOTION] = {
        "promotion_decision_id": PROMOTION, "intake_id": INTAKE, "listing_id": LISTING,
        "tenant_id": TENANT, "status": "PENDING_REVIEW", "decision_type": "STANDARD",
        "version": 2, "audit_event_id": str(uuid4()), "correlation_id": CORRELATION,
        "proposer": PROPOSER, "proposer_subject_id": PROPOSER, "target_format_code": "synthetic-format",
        "gate_snapshot_sha256": "a" * 64,
    }
    context = SimpleNamespace(
        store=store, legacy=InMemoryAssistedIntakeRepository(),
        principal=Principal(subject_id=REVIEWER, roles=frozenset({Role.SITE_REVIEWER}),
                            scope=Scope(tenant_id=TENANT, assigned_area_ids=frozenset({"allowed-area"}))),
    )
    # Only the authentication boundary is supplied. The real route, adapters,
    # scope authorizer, domain transitions and replay logic all execute.
    monkeypatch.setattr(dependencies, "principal_from_headers", lambda _headers: context.principal)
    app = FastAPI()
    app.state.operator_intake_repository = context.legacy
    app.state.listing_repository = InMemoryListingRepository()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    context.client = TestClient(app)
    return context


def _request(context, operation):
    if operation == "read":
        return context.client.get(PROMOTION_PATH)
    return context.client.post(PROMOTION_PATH + "/actions/review", json=REVIEW_BODY, headers=REVIEW_HEADERS)


@pytest.mark.parametrize("operation", ["read", "review"])
def test_orphan_promotion_does_not_supply_its_own_scope_authority(authority, operation):
    del authority.store.intakes[INTAKE]
    authority.store.promotions[PROMOTION]["scope"] = {"assigned_area_id": "allowed-area"}
    before = _snapshot(authority)
    response = _request(authority, operation)
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


@pytest.mark.parametrize("operation", ["read", "review"])
def test_stale_legacy_intake_cannot_shadow_current_canonical_scope(authority, operation):
    authority.store.intakes[INTAKE]["scope"]["assigned_area_id"] = "outside-grant"
    authority.legacy.intakes[INTAKE] = {
        "id": INTAKE, "tenantId": TENANT, "assignedAreaId": "allowed-area",
        "submitter": PROPOSER, "stage": "READY", "version": 1,
    }
    before = _snapshot(authority)
    response = _request(authority, operation)
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


@pytest.mark.parametrize("operation", ["read", "review"])
def test_stale_legacy_decision_cannot_change_canonical_proposer(authority, operation):
    canonical = authority.store.promotions[PROMOTION]
    canonical["proposer"] = canonical["proposer_subject_id"] = REVIEWER
    authority.legacy.promotions[PROMOTION] = {**deepcopy(canonical), "proposer": PROPOSER,
                                             "proposer_subject_id": PROPOSER}
    before = _snapshot(authority)
    response = _request(authority, operation)
    if operation == "read":
        assert response.status_code == 200, response.text
        assert response.json()["proposer_subject_id"] == REVIEWER
    else:
        assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


@pytest.mark.parametrize("operation", ["read", "review"])
def test_promotion_tenant_is_not_replaced_with_the_callers_tenant(authority, operation):
    authority.principal = Principal(
        subject_id=REVIEWER, roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=OTHER_TENANT),
    )
    before = _snapshot(authority)
    response = _request(authority, operation)
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


def test_decision_tenant_cannot_authorize_a_foreign_canonical_parent(authority):
    authority.store.intakes[INTAKE]["scope"]["tenant_id"] = OTHER_TENANT
    before = _snapshot(authority)
    response = _request(authority, "review")
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


def test_promotion_proposer_cannot_review_their_own_request(authority):
    authority.principal = Principal(subject_id=PROPOSER, roles=frozenset({Role.SITE_REVIEWER}),
                                    scope=Scope(tenant_id=TENANT))
    before = _snapshot(authority)
    response = _request(authority, "review")
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


@pytest.mark.parametrize("revocation", ["scope", "parent"])
def test_successful_review_receipt_does_not_keep_revoked_resource_access(authority, revocation):
    first = _request(authority, "review")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "REJECTED"
    if revocation == "scope":
        authority.store.intakes[INTAKE]["scope"]["assigned_area_id"] = "outside-grant"
    else:
        del authority.store.intakes[INTAKE]
    before = _snapshot(authority)
    replay = _request(authority, "review")
    assert replay.status_code == 403, replay.text
    assert _snapshot(authority) == before


def test_checkpoint_retry_receipt_is_authorized_again_after_scope_revocation(authority):
    authority.store.jobs[JOB] = {
        "job_id": JOB, "intake_id": INTAKE, "tenant_id": TENANT, "status": "FAILED",
        "checkpoint": "PARSING", "attempt": 0, "version": 1, "correlation_id": CORRELATION,
    }
    headers = {"Idempotency-Key": "synthetic-checkpoint-retry", "If-Match": 'W/"1"'}
    body = {"checkpoint": "PARSING", "reason": "Synthetic checkpoint recovery"}
    path = f"/api/v1/jobs/{JOB}/retry"
    first = authority.client.post(path, json=body, headers=headers)
    assert first.status_code == 202, first.text
    authority.store.intakes[INTAKE]["scope"]["assigned_area_id"] = "outside-grant"
    before = _snapshot(authority)
    assert authority.client.get(f"/api/v1/jobs/{JOB}/receipt").status_code == 403
    replay = authority.client.post(path, json=body, headers=headers)
    assert replay.status_code == 403, replay.text
    assert _snapshot(authority) == before


def test_intake_latest_decision_read_uses_canonical_decisions_even_when_legacy_is_empty(authority):
    before = _snapshot(authority)
    response = authority.client.get(f"/api/v1/intakes/{INTAKE}/promotion-decision")
    assert response.status_code == 200, response.text
    assert response.json()["promotion_decision_id"] == PROMOTION
    assert _snapshot(authority) == before


def test_child_tenant_cannot_authorize_a_parent_without_tenant_evidence(authority):
    del authority.store.intakes[INTAKE]
    authority.legacy.intakes[INTAKE] = {
        "id": INTAKE, "assignedAreaId": "allowed-area", "submitter": PROPOSER,
        "stage": "READY", "version": 1,
    }
    before = _snapshot(authority)
    response = _request(authority, "review")
    assert response.status_code == 403, response.text
    assert _snapshot(authority) == before


@pytest.mark.parametrize("operation", ["get", "retry"])
def test_legacy_score_mirror_is_not_delivery_authority(authority, operation):
    authority.store.jobs[JOB] = {
        "job_id": JOB, "intake_id": INTAKE, "tenant_id": TENANT, "status": "FAILED",
        "checkpoint": "SCORE_QUEUED", "attempt": 0, "version": 1, "correlation_id": CORRELATION,
    }
    before = _snapshot(authority)
    response = (
        authority.client.get(f"/api/v1/jobs/{JOB}/receipt") if operation == "get"
        else authority.client.post(
            f"/api/v1/jobs/{JOB}/retry",
            json={"checkpoint": "SCORE_QUEUED", "reason": "Synthetic checkpoint recovery"},
            headers={"Idempotency-Key": "synthetic-legacy-score-retry", "If-Match": 'W/"1"'},
        )
    )
    assert response.status_code == 409, response.text
    assert "SCORE_JOB_AUTHORITY_UNAVAILABLE" in response.text
    assert _snapshot(authority) == before
