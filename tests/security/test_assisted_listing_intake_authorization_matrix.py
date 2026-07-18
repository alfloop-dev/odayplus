from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.auth import DataClassification

HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}


def _write_headers(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    res = {
        **HEADERS,
        "Idempotency-Key": f"idem-{key}",
        "X-Correlation-Id": f"corr-{key}",
    }
    if extra:
        res.update(extra)
    return res


@pytest.fixture(autouse=True)
def reset_service_state():
    from modules.opsboard.application.network_listings import NetworkListingService

    # Reset in-memory singleton state
    NetworkListingService._state = {}


def test_tenant_isolation() -> None:
    client = TestClient(create_app())

    # Submit an intake for tenant-a
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-77120345.html", "heatZoneId": "HZ-01"},
        headers=_write_headers("tenant-isolation-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # Try to access it with tenant-b -> expect 403 TENANT_SCOPE_DENIED (or default tenant isolation message)
    cross_tenant_headers = _write_headers("tenant-isolation-access", {"x-tenant-id": "tenant-b"})
    get_resp = client.get(
        f"/api/v1/operator/network-listings/intake/{intake_id}",
        headers=cross_tenant_headers,
    )
    assert get_resp.status_code == 403
    # May return default cross-tenant message from existing RBAC/ABAC engine
    assert (
        "tenant" in get_resp.json()["detail"].lower()
        or "tenant_scope_denied" in get_resp.json()["detail"].lower()
    )


def test_region_heatzone_scope_isolation() -> None:
    client = TestClient(create_app())

    # Submit intake in HZ-01
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-77120345.html", "heatZoneId": "HZ-01"},
        headers=_write_headers("hz-isolation-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # Access it with scope restricted only to HZ-02 -> expect 403 SCOPE_DENIED (or region outside scope)
    restricted_headers = _write_headers("hz-isolation-access", {"x-region-ids": "HZ-02"})
    get_resp = client.get(
        f"/api/v1/operator/network-listings/intake/{intake_id}",
        headers=restricted_headers,
    )
    assert get_resp.status_code == 403
    assert (
        "region" in get_resp.json()["detail"].lower()
        or "scope_denied" in get_resp.json()["detail"].lower()
    )


def test_ownership_enforcement() -> None:
    client = TestClient(create_app())

    # Submit intake as user-a (Staff role)
    headers_user_a = _write_headers(
        "ownership-user-a",
        {
            "x-subject-id": "user-a",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={
            "url": "https://www.synthetic.example/detail-ownership-unique.html",
            "heatZoneId": "HZ-01",
        },
        headers=headers_user_a,
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # Try to access/correct as user-b (another Staff role) -> expect 403 OWNERSHIP_REQUIRED
    headers_user_b = _write_headers(
        "ownership-user-b",
        {
            "x-subject-id": "user-b",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-staff",
        },
    )
    correct_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址",
            "riskSummary": "修改地址會改變比對結果。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=headers_user_b,
    )
    assert correct_resp.status_code == 403
    assert correct_resp.json()["detail"] == "OWNERSHIP_REQUIRED"


def test_self_review_prohibition() -> None:
    client = TestClient(create_app())

    # Submit intake as user-a (who is a Manager)
    # Manager must have write permission (so expansion_user platform role + expansion-manager operator role)
    headers_user_a = _write_headers(
        "self-review-manager",
        {
            "x-subject-id": "user-a",
            "x-roles": "expansion_user",
            "x-operator-role": "expansion-manager",
        },
    )
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-88520242.html", "heatZoneId": "HZ-01"},
        headers=headers_user_a,
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # Manager user-a tries to promote/approve their own submission -> expect 403 SELF_REVIEW_DENIED
    promote_resp = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        json={
            "reason": "自我核准",
            "riskSummary": "自我核准展店",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=headers_user_a,
    )
    assert promote_resp.status_code == 403
    assert promote_resp.json()["detail"] == "SELF_REVIEW_DENIED"


def test_second_actor_segregation() -> None:
    client = TestClient(create_app())

    # Submit a listing merge action
    # prospoer/submitter is user-a
    # If user-a tries to call merge with first_actor_id set as user-a in headers -> expect SECOND_ACTOR_REQUIRED
    headers_user_a = _write_headers(
        "second-actor-segregation",
        {
            "x-subject-id": "user-a",
            "x-roles": "expansion_user,site_reviewer",
            "x-operator-role": "expansion-manager",
            "x-first-actor-id": "user-a",
        },
    )
    merge_resp = client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        json={
            "targetListingId": "L-2025",
            "reason": "重複來源",
            "riskSummary": "合併可能導致數據覆蓋",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
        headers=headers_user_a,
    )
    assert merge_resp.status_code == 409
    assert merge_resp.json()["detail"] == "SECOND_ACTOR_REQUIRED"


def test_risk_acknowledgement_required() -> None:
    client = TestClient(create_app())

    # Submit intake
    submit_resp = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        json={"url": "https://www.synthetic.example/detail-77120345.html", "heatZoneId": "HZ-01"},
        headers=_write_headers("risk-ack-submit"),
    )
    assert submit_resp.status_code == 200
    intake_id = submit_resp.json()["id"]

    # Try correct identity fields without riskAcknowledged -> expect 422 RISK_ACKNOWLEDGEMENT_REQUIRED
    bad_correct = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址",
            "riskSummary": "修改地址會改變比對結果。",
            "riskAcknowledged": False,
            "actorRoleId": "expansionManager",
        },
        headers=_write_headers("risk-ack-correct"),
    )
    assert bad_correct.status_code == 422
    assert bad_correct.json()["detail"] == "RISK_ACKNOWLEDGEMENT_REQUIRED"


def test_legal_hold_conflict() -> None:
    # Set hasLegalHold or legalHold on the listing or intake and try to archive
    # For testing, we can patch listing's legalHold attribute or test our authorizer
    from modules.listing.application.intake_authorization import authorize_intake_action
    from shared.auth import Principal, Role, Scope

    scope = Scope(tenant_id="tenant-a")
    principal = Principal(subject_id="user-a", roles=frozenset({Role.SITE_REVIEWER}), scope=scope)

    # Calling purge/archive with has_legal_hold=True should raise legal hold conflict
    with pytest.raises(Exception) as excinfo:
        authorize_intake_action(
            principal,
            "purge",
            resource={"tenantId": "tenant-a"},
            has_legal_hold=True,
        )
    assert "LEGAL_HOLD_CONFLICT" in str(excinfo.value)


def test_residency_compliance() -> None:
    from modules.listing.application.intake_authorization import authorize_intake_action
    from shared.auth import Principal, Role, Scope

    scope = Scope(tenant_id="tenant-a")
    principal = Principal(subject_id="user-a", roles=frozenset({Role.SITE_REVIEWER}), scope=scope)

    # Calling export with is_residency_compliant=False should raise residency denied
    with pytest.raises(Exception) as excinfo:
        authorize_intake_action(
            principal,
            "export",
            resource={"tenantId": "tenant-a"},
            is_residency_compliant=False,
        )
    assert "RESIDENCY_DENIED" in str(excinfo.value)


def test_data_classification_masking() -> None:
    from modules.listing.application.intake_authorization import mask_intake
    from shared.auth import Principal, Scope

    # Low clearance (PUBLIC)
    scope = Scope(tenant_id="tenant-a", clearance=DataClassification.PUBLIC)
    principal = Principal(subject_id="user-a", scope=scope)

    intake = {
        "id": "IN-3001",
        "parsedFields": {
            "address": {"correctedValue": "Taipei"},
            "rent": {"correctedValue": 10000},
            "contactPhone": {"correctedValue": "0912345678"},
        },
    }

    masked = mask_intake(principal, intake)
    assert masked["parsedFields"]["address"]["correctedValue"] is None
    assert masked["parsedFields"]["address"]["masked"] is True
    assert masked["parsedFields"]["address"]["mask_reason_code"] == "FIELD_MASKED"

    assert masked["parsedFields"]["contactPhone"]["correctedValue"] is None
    assert masked["parsedFields"]["contactPhone"]["masked"] is True
    assert masked["parsedFields"]["contactPhone"]["mask_reason_code"] == "FIELD_MASKED"
