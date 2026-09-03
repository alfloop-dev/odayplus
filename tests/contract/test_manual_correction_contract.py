"""Contract tests for Manual Correction, Authorization, Audit, and Rollback (ODP-INT-006 / ODP-INT-MANUAL-CORRECTION-AUDIT-001).

Acceptance Criteria:
1. API allows submitting corrections to canonical records; server-side actor ignores/rejects payload spoofing.
2. Immutable audit trail preserves old/new, reason, actor, timestamp, revision, correlation, and DecisionCard.
3. Cross-tenant, unauthorized role, stale revision, and missing/short reason are all rejected fail-closed.
4. Rollback / compensation restores old values and manual_override_flag with complete audit trail.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.app.routes.listings import create_listings_router
from shared.audit.events import InMemoryAuditLog
from shared.domain.models import AddressLocation
from shared.infrastructure.persistence.repositories import (
    InMemoryAddressLocationRepository,
    InMemoryManualCorrectionRepository,
)


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def correction_repo() -> InMemoryManualCorrectionRepository:
    return InMemoryManualCorrectionRepository()


@pytest.fixture
def address_repo(correction_repo: InMemoryManualCorrectionRepository) -> InMemoryAddressLocationRepository:
    return InMemoryAddressLocationRepository(_corrections=correction_repo)


@pytest.fixture
def client(
    audit_log: InMemoryAuditLog,
    address_repo: InMemoryAddressLocationRepository,
    correction_repo: InMemoryManualCorrectionRepository,
) -> TestClient:
    app = FastAPI()
    router = create_listings_router(
        audit_log=audit_log,
        address_repository=address_repo,
        correction_repository=correction_repo,
    )
    app.include_router(router)
    return TestClient(app)


def test_submit_correction_success_and_server_actor_provenance(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
    audit_log: InMemoryAuditLog,
) -> None:
    # 1. Seed canonical AddressLocation with manual_override_flag=False
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="100 Taipei City Xinyi Dist Songgao Rd 1",
        normalized_address="Taipei City Xinyi District Songgao Road 1",
        city="Taipei City",
        district="Xinyi District",
        road="Songgao Road",
        latitude=25.0380,
        longitude=121.5670,
        geocode_precision="rooftop",
        geocode_confidence=0.95,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    address_repo.save_address(address)

    # 2. Submit correction with authentic principal headers, but body attempts to SPOOF actor
    headers = {
        "x-subject-id": "real-reviewer-42",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-alpha",
        "x-correlation-id": "corr-test-001",
    }
    payload = {
        "latitude": 25.0395,
        "longitude": 121.5685,
        "normalized_address": "Taipei City Xinyi District Songgao Road 1 Corrected",
        "reason": "On-site GPS survey confirmed entrance coordinates",
        "expected_revision": 1,
        "actor": "spoofed-attacker-99",
        "actor_id": "spoofed-attacker-99",
        "actorRoleId": "platformAdmin",
    }

    response = client.post(f"/listings/addresses/{addr_id}/corrections", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()

    # Acceptance 1: Server-side actor was used, spoofed actor ignored
    assert data["actor_id"] == "real-reviewer-42"
    assert data["manual_override_flag"] is True
    assert data["source_revision"] == 1
    assert data["applied_revision"] == 2
    assert data["address"]["latitude"] == 25.0395
    assert data["address"]["longitude"] == 121.5685
    assert data["address"]["manual_override_flag"] is True
    assert data["address"]["revision"] == 2

    # Acceptance 2: Immutable audit trail preserves old/new, reason, actor, revision, DecisionCard
    events = audit_log.list_events()
    assert len(events) >= 1
    corr_event = [e for e in events if e.action == "manual_override"][-1]
    assert corr_event.actor == "real-reviewer-42"
    assert corr_event.resource == f"address_location:{addr_id}"
    assert corr_event.metadata["old_value"]["latitude"] == 25.0380
    assert corr_event.metadata["old_value"]["manual_override_flag"] is False
    assert corr_event.metadata["new_value"]["latitude"] == 25.0395
    assert corr_event.metadata["new_value"]["manual_override_flag"] is True
    assert corr_event.metadata["reason"] == "On-site GPS survey confirmed entrance coordinates"
    assert corr_event.metadata["source_revision"] == 1
    assert corr_event.metadata["applied_revision"] == 2

    # Verify audit chain integrity
    assert audit_log.verify_chain().ok is True

    # DecisionCard shape check
    decision_card = corr_event.metadata["decision_card"]
    assert decision_card["decision_type"] == "MANUAL_CORRECTION"
    assert decision_card["owner"] == "real-reviewer-42"
    assert decision_card["outcome"] == "APPLIED"


def test_cross_tenant_modification_rejected(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    # Seed address for tenant-alpha
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei",
        latitude=25.0,
        longitude=121.0,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    address_repo.save_address(address)

    # Actor belongs to tenant-beta
    headers = {
        "x-subject-id": "user-beta",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-beta",
    }
    payload = {
        "latitude": 25.1,
        "longitude": 121.1,
        "reason": "Attempting cross tenant edit",
    }

    response = client.post(f"/listings/addresses/{addr_id}/corrections", json=payload, headers=headers)
    assert response.status_code == 403
    assert "TENANT_SCOPE_DENIED" in response.text


def test_unauthorized_role_rejected(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei",
        latitude=25.0,
        longitude=121.0,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    address_repo.save_address(address)

    # Role auditor is read-only for listing
    headers = {
        "x-subject-id": "user-auditor",
        "x-roles": "auditor",
        "x-tenant-id": "tenant-alpha",
    }
    payload = {
        "latitude": 25.1,
        "longitude": 121.1,
        "reason": "Auditor trying to edit coordinates",
    }

    response = client.post(f"/listings/addresses/{addr_id}/corrections", json=payload, headers=headers)
    assert response.status_code == 403


def test_missing_or_short_reason_rejected(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei",
        latitude=25.0,
        longitude=121.0,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    address_repo.save_address(address)

    headers = {
        "x-subject-id": "reviewer-1",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-alpha",
    }

    # Short reason (< 5 chars)
    payload_short = {
        "latitude": 25.1,
        "longitude": 121.1,
        "reason": "fix",
    }
    response = client.post(f"/listings/addresses/{addr_id}/corrections", json=payload_short, headers=headers)
    assert response.status_code == 422


def test_stale_revision_optimistic_concurrency_rejected(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei",
        latitude=25.0,
        longitude=121.0,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=5,
    )
    address_repo.save_address(address)

    headers = {
        "x-subject-id": "reviewer-1",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-alpha",
        "If-Match": '"3"',
    }
    payload = {
        "latitude": 25.1,
        "longitude": 121.1,
        "reason": "Valid reason for coordinate change",
    }

    response = client.post(f"/listings/addresses/{addr_id}/corrections", json=payload, headers=headers)
    assert response.status_code == 409
    assert "STALE_REVISION" in response.text


def test_rollback_compensation_and_audit(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
    audit_log: InMemoryAuditLog,
) -> None:
    # 1. Seed address
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Original Address 1",
        normalized_address="Original Address 1 Normalized",
        latitude=25.0100,
        longitude=121.5100,
        manual_override_flag=False,
        tenant_id="tenant-alpha",
        revision=1,
    )
    address_repo.save_address(address)

    headers = {
        "x-subject-id": "reviewer-1",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-alpha",
    }

    # 2. Apply correction
    corr_payload = {
        "latitude": 25.0999,
        "longitude": 121.5999,
        "normalized_address": "Modified Address 1",
        "reason": "Operator manual override applied",
    }
    corr_resp = client.post(f"/listings/addresses/{addr_id}/corrections", json=corr_payload, headers=headers)
    assert corr_resp.status_code == 200, corr_resp.text
    corr_data = corr_resp.json()
    correction_id = corr_data["correction_id"]
    assert corr_data["manual_override_flag"] is True

    # 3. Read back address corrections history
    hist_resp = client.get(f"/listings/addresses/{addr_id}/corrections", headers=headers)
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()["corrections"]) == 1

    # 4. Rollback correction
    rollback_headers = {
        "x-subject-id": "admin-1",
        "x-roles": "platform_admin",
        "x-tenant-id": "tenant-alpha",
    }
    rollback_payload = {
        "reason": "Reverting incorrect GPS override back to original",
    }
    rb_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections/{correction_id}/rollback",
        json=rollback_payload,
        headers=rollback_headers,
    )
    assert rb_resp.status_code == 200, rb_resp.text
    rb_data = rb_resp.json()

    # Acceptance 4: Values restored, manual_override_flag is False, revision incremented
    assert rb_data["status"] == "rolled_back"
    assert rb_data["manual_override_flag"] is False
    assert rb_data["address"]["latitude"] == 25.0100
    assert rb_data["address"]["longitude"] == 121.5100
    assert rb_data["address"]["normalized_address"] == "Original Address 1 Normalized"
    assert rb_data["address"]["manual_override_flag"] is False
    assert rb_data["applied_revision"] == 3

    # Verify rollback audit event
    events = audit_log.list_events()
    rb_event = [e for e in events if e.action == "rollback_manual_override"][-1]
    assert rb_event.actor == "admin-1"
    assert rb_event.metadata["reason"] == "Reverting incorrect GPS override back to original"
    assert rb_event.metadata["decision_card"]["outcome"] == "ROLLED_BACK"
    assert audit_log.verify_chain().ok is True
