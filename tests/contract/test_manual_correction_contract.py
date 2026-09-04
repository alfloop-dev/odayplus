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


def test_tenant_fail_closed_without_tenant_header(
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

    # Authenticated site_reviewer with NO tenant header
    headers_no_tenant = {
        "x-subject-id": "site-reviewer-no-tenant",
        "x-roles": "site_reviewer",
    }

    # 1. Attempting correction without tenant header MUST fail closed (403 TENANT_SCOPE_DENIED)
    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"latitude": 25.1, "longitude": 121.1, "reason": "No tenant header modification attempt"},
        headers=headers_no_tenant,
    )
    assert corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in corr_resp.text

    # 2. Reading address without tenant header MUST fail closed (403 TENANT_SCOPE_DENIED)
    get_resp = client.get(f"/listings/addresses/{addr_id}", headers=headers_no_tenant)
    assert get_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in get_resp.text

    # 3. Listing corrections without tenant header MUST fail closed (403 TENANT_SCOPE_DENIED)
    list_resp = client.get(f"/listings/addresses/{addr_id}/corrections", headers=headers_no_tenant)
    assert list_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in list_resp.text


def test_top_of_stack_rollback_ordering(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei Original Road 1",
        city="Taipei",
        road="Original Road",
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

    # 1. Apply correction A (road -> Road A, rev 2)
    resp_a = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"road": "Road A", "reason": "Updated road name to Road A"},
        headers=headers,
    )
    assert resp_a.status_code == 200
    corr_a_id = resp_a.json()["correction_id"]
    assert resp_a.json()["address"]["revision"] == 2

    # 2. Apply correction B (city -> City B, rev 3)
    resp_b = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"city": "City B", "reason": "Updated city name to City B"},
        headers=headers,
    )
    assert resp_b.status_code == 200
    corr_b_id = resp_b.json()["correction_id"]
    assert resp_b.json()["address"]["revision"] == 3
    assert resp_b.json()["address"]["road"] == "Road A"
    assert resp_b.json()["address"]["city"] == "City B"

    # 3. Attempt to rollback A while B is still active on top -> MUST FAIL (422 ROLLBACK_ORDER_VIOLATION)
    rb_a_fail = client.post(
        f"/listings/addresses/{addr_id}/corrections/{corr_a_id}/rollback",
        json={"reason": "Attempting out-of-order rollback of A"},
        headers=headers,
    )
    assert rb_a_fail.status_code == 422
    assert "ROLLBACK_ORDER_VIOLATION" in rb_a_fail.text

    # 4. Rollback B (top of stack) -> SUCCEEDS, leaves A intact (road="Road A", city restored to "Taipei", rev 4)
    rb_b_ok = client.post(
        f"/listings/addresses/{addr_id}/corrections/{corr_b_id}/rollback",
        json={"reason": "Reverting correction B from top of stack"},
        headers=headers,
    )
    assert rb_b_ok.status_code == 200
    assert rb_b_ok.json()["status"] == "rolled_back"
    assert rb_b_ok.json()["manual_override_flag"] is True  # A is still applied!
    assert rb_b_ok.json()["address"]["road"] == "Road A"
    assert rb_b_ok.json()["address"]["city"] == "Taipei"
    assert rb_b_ok.json()["address"]["revision"] == 4

    # 5. Now rollback A (now top of stack) -> SUCCEEDS, restores all to initial, manual_override_flag=False, rev 5
    rb_a_ok = client.post(
        f"/listings/addresses/{addr_id}/corrections/{corr_a_id}/rollback",
        json={"reason": "Reverting correction A from top of stack"},
        headers=headers,
    )
    assert rb_a_ok.status_code == 200
    assert rb_a_ok.json()["status"] == "rolled_back"
    assert rb_a_ok.json()["manual_override_flag"] is False
    assert rb_a_ok.json()["address"]["road"] == "Original Road"
    assert rb_a_ok.json()["address"]["city"] == "Taipei"
    assert rb_a_ok.json()["address"]["revision"] == 5


def test_regression_h3_cell_restoration_on_coordinate_rollback(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    """Regression test (P1): Rollback must restore original H3 cells, not retain corrected ones."""
    import h3

    addr_id = str(uuid4())
    initial_lat, initial_lng = 25.0330, 121.5654
    orig_h3_8 = h3.latlng_to_cell(initial_lat, initial_lng, 8)
    orig_h3_9 = h3.latlng_to_cell(initial_lat, initial_lng, 9)
    orig_h3_10 = h3.latlng_to_cell(initial_lat, initial_lng, 10)

    address = AddressLocation(
        address_id=addr_id,
        raw_address="Taipei 101 Entrance",
        latitude=initial_lat,
        longitude=initial_lng,
        h3_res_8=orig_h3_8,
        h3_res_9=orig_h3_9,
        h3_res_10=orig_h3_10,
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

    # Step 1: Apply correction with coordinates far enough to change H3 cells
    corrected_lat, corrected_lng = 25.0450, 121.5200
    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={
            "latitude": corrected_lat,
            "longitude": corrected_lng,
            "reason": "Corrected location to Zhongzheng district location",
        },
        headers=headers,
    )
    assert corr_resp.status_code == 200
    corr_data = corr_resp.json()
    correction_id = corr_data["correction_id"]
    new_h3_8 = corr_data["address"]["h3_res_8"]
    new_h3_9 = corr_data["address"]["h3_res_9"]
    new_h3_10 = corr_data["address"]["h3_res_10"]

    assert new_h3_8 != orig_h3_8
    assert new_h3_9 != orig_h3_9
    assert new_h3_10 != orig_h3_10

    # Step 2: Rollback correction
    rb_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections/{correction_id}/rollback",
        json={"reason": "Reverting coordinates override back to original 101 spot"},
        headers=headers,
    )
    assert rb_resp.status_code == 200
    rb_data = rb_resp.json()

    # Step 3: Verify restored coordinates AND restored H3 cells
    assert rb_data["address"]["latitude"] == initial_lat
    assert rb_data["address"]["longitude"] == initial_lng
    assert rb_data["address"]["h3_res_8"] == orig_h3_8
    assert rb_data["address"]["h3_res_9"] == orig_h3_9
    assert rb_data["address"]["h3_res_10"] == orig_h3_10
    assert rb_data["address"]["h3_res_8"] != new_h3_8


def test_regression_legacy_empty_tenant_records_cannot_be_claimed_or_bypassed(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    """Regression test (P1): NULL or empty tenant legacy records cannot be claimed or modified by tenant users."""
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Legacy Global Address",
        latitude=25.0,
        longitude=121.0,
        manual_override_flag=False,
        tenant_id="",  # Un-tenanted legacy record
        revision=1,
    )
    address_repo.save_address(address)

    tenant_headers = {
        "x-subject-id": "reviewer-tenant-beta",
        "x-roles": "site_reviewer",
        "x-tenant-id": "tenant-beta",
    }

    # 1. Tenant user attempting to read un-tenanted record MUST fail closed (403)
    get_resp = client.get(f"/listings/addresses/{addr_id}", headers=tenant_headers)
    assert get_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in get_resp.text

    # 2. Tenant user attempting to list corrections for un-tenanted record MUST fail closed (403)
    list_corr_resp = client.get(f"/listings/addresses/{addr_id}/corrections", headers=tenant_headers)
    assert list_corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in list_corr_resp.text

    # 3. Tenant user attempting to apply correction (claim record) MUST fail closed (403)
    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"latitude": 25.5, "longitude": 121.5, "reason": "Attempting to claim un-tenanted record"},
        headers=tenant_headers,
    )
    assert corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in corr_resp.text

    # 4. Verify record in repository was NOT claimed and tenant_id remains empty
    saved = address_repo.get_address(addr_id)
    assert saved is not None
    assert saved.tenant_id == ""
    assert saved.revision == 1
    assert saved.manual_override_flag is False

    # 5. list_addresses for tenant-beta MUST NOT return the un-tenanted record
    tenant_addresses = address_repo.list_addresses(tenant_id="tenant-beta")
    assert not any(a.address_id == addr_id for a in tenant_addresses)


def test_regression_rollback_audit_self_contained_snapshots(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
    audit_log: InMemoryAuditLog,
) -> None:
    """Regression test (P2): Rollback audit and decision card contain self-contained old_value and new_value snapshots."""
    addr_id = str(uuid4())
    address = AddressLocation(
        address_id=addr_id,
        raw_address="Initial Road 1",
        city="Taipei",
        road="Road 1",
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

    # Step 1: Apply correction
    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={
            "road": "Road 1 Modified",
            "latitude": 25.0200,
            "longitude": 121.5200,
            "reason": "Applying surveyor verified road and coordinates",
        },
        headers=headers,
    )
    assert corr_resp.status_code == 200
    corr_id = corr_resp.json()["correction_id"]

    # Step 2: Rollback correction
    rb_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections/{corr_id}/rollback",
        json={"reason": "Rollback survey override back to baseline"},
        headers=headers,
    )
    assert rb_resp.status_code == 200
    rb_data = rb_resp.json()

    # Step 3: Verify self-contained old_value and new_value in rollback response
    assert "old_value" in rb_data
    assert "new_value" in rb_data
    assert rb_data["old_value"]["road"] == "Road 1 Modified"
    assert rb_data["old_value"]["latitude"] == 25.0200
    assert rb_data["old_value"]["manual_override_flag"] is True
    assert rb_data["old_value"]["revision"] == 2

    assert rb_data["new_value"]["road"] == "Road 1"
    assert rb_data["new_value"]["latitude"] == 25.0100
    assert rb_data["new_value"]["manual_override_flag"] is False
    assert rb_data["new_value"]["revision"] == 3

    # Step 4: Verify self-contained snapshots in AuditEvent metadata
    events = audit_log.list_events()
    rb_event = [e for e in events if e.action == "rollback_manual_override"][-1]
    assert "old_value" in rb_event.metadata
    assert "new_value" in rb_event.metadata
    assert rb_event.metadata["old_value"]["road"] == "Road 1 Modified"
    assert rb_event.metadata["old_value"]["latitude"] == 25.0200
    assert rb_event.metadata["new_value"]["road"] == "Road 1"
    assert rb_event.metadata["new_value"]["latitude"] == 25.0100

    # Step 5: Verify DecisionCard metrics contains self-contained snapshots
    decision_card = rb_event.metadata["decision_card"]
    assert "old_value" in decision_card["metrics"]
    assert "new_value" in decision_card["metrics"]
    assert decision_card["metrics"]["old_value"]["road"] == "Road 1 Modified"
    assert decision_card["metrics"]["new_value"]["road"] == "Road 1"




def test_regression_unscoped_caller_cannot_reach_unscoped_legacy_record(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    """Regression test (P1): a blank tenant is not a wildcard on either side.

    A legacy record stores no tenant and an authenticated caller may present no
    tenant. Comparing the two normalized values for equality alone made
    ``"" == ""`` a match, so any authorized caller without a tenant could read
    and modify every un-tenanted record. Both sides must now be non-empty.
    """
    addr_id = str(uuid4())
    address_repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Legacy Unscoped Address",
            latitude=25.0,
            longitude=121.0,
            manual_override_flag=False,
            tenant_id="",  # Un-tenanted legacy record
            revision=1,
        )
    )

    # Authenticated, role-authorized, but carrying no tenant scope at all.
    headers_no_tenant = {
        "x-subject-id": "site-reviewer-no-tenant",
        "x-roles": "site_reviewer",
    }

    get_resp = client.get(f"/listings/addresses/{addr_id}", headers=headers_no_tenant)
    assert get_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in get_resp.text

    list_resp = client.get(
        f"/listings/addresses/{addr_id}/corrections", headers=headers_no_tenant
    )
    assert list_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in list_resp.text

    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={
            "latitude": 25.9,
            "longitude": 121.9,
            "reason": "Unscoped caller modifying un-tenanted record",
        },
        headers=headers_no_tenant,
    )
    assert corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in corr_resp.text

    rollback_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections/{uuid4()}/rollback",
        json={"reason": "Unscoped caller rolling back un-tenanted record"},
        headers=headers_no_tenant,
    )
    assert rollback_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in rollback_resp.text

    # The record must be untouched: not read, not claimed, not revised.
    saved = address_repo.get_address(addr_id)
    assert saved is not None
    assert saved.tenant_id == ""
    assert saved.revision == 1
    assert saved.latitude == 25.0
    assert saved.manual_override_flag is False
    assert address_repo.get_corrections(addr_id) == []


def test_regression_platform_admin_cannot_reach_unscoped_legacy_record(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    """Regression test (P1): cross-tenant admin is not a bypass for *no* tenant.

    ``platform_admin`` crosses tenants by design, but a record with no tenant
    has no scope to cross into. It stays unreachable until it is migrated into
    a real tenant.
    """
    addr_id = str(uuid4())
    address_repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Legacy Unscoped Address",
            latitude=25.0,
            longitude=121.0,
            manual_override_flag=False,
            tenant_id="",
            revision=1,
        )
    )

    admin_headers = {
        "x-subject-id": "admin-1",
        "x-roles": "platform_admin",
        "x-tenant-id": "tenant-alpha",
    }

    get_resp = client.get(f"/listings/addresses/{addr_id}", headers=admin_headers)
    assert get_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in get_resp.text

    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"latitude": 25.9, "reason": "Admin claiming un-tenanted record"},
        headers=admin_headers,
    )
    assert corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in corr_resp.text

    saved = address_repo.get_address(addr_id)
    assert saved is not None
    assert saved.tenant_id == ""
    assert saved.revision == 1


def test_regression_platform_admin_without_tenant_is_denied(
    client: TestClient,
    address_repo: InMemoryAddressLocationRepository,
) -> None:
    """Regression test (P1): the admin role does not substitute for a tenant."""
    addr_id = str(uuid4())
    address_repo.save_address(
        AddressLocation(
            address_id=addr_id,
            raw_address="Scoped Address",
            latitude=25.0,
            longitude=121.0,
            manual_override_flag=False,
            tenant_id="tenant-alpha",
            revision=1,
        )
    )

    admin_no_tenant = {"x-subject-id": "admin-1", "x-roles": "platform_admin"}

    get_resp = client.get(f"/listings/addresses/{addr_id}", headers=admin_no_tenant)
    assert get_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in get_resp.text

    corr_resp = client.post(
        f"/listings/addresses/{addr_id}/corrections",
        json={"latitude": 25.9, "reason": "Admin without tenant scope modifying"},
        headers=admin_no_tenant,
    )
    assert corr_resp.status_code == 403
    assert "TENANT_SCOPE_DENIED" in corr_resp.text

    saved = address_repo.get_address(addr_id)
    assert saved is not None
    assert saved.revision == 1
