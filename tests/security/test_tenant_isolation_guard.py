"""Tests for Shared Tenant Isolation Guard and PLATFORM_ADMIN Fail-Closed Policy.

Task ID: `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001`
Acceptance Criteria:
1. 三個 production entry 使用同一共用 guard 且 PLATFORM_ADMIN 預設不能跨租戶
2. resource tenant 取自目標資源而非 principal 自己
3. 同租戶 allow／跨租戶 deny／missing tenant deny 均由 production-shaped 測試覆蓋
4. 若存在正式例外則驗證 scope／expiry 並寫 actor／resource／reason audit
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from modules.external_data.application.market_data_facade import (
    MarketDataAuthorizationError,
    MarketDataFacade,
)
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    InMemoryDataPlatformTransport,
)
from modules.listing.application.intake_authorization import authorize_intake_action
from modules.market_intelligence_api.application.auth import (
    MarketIntelligenceAuthorizationError,
    authorize_market_intelligence,
)
from shared.audit import InMemoryAuditLog
from shared.auth import (
    ANONYMOUS,
    DataClassification,
    Principal,
    Role,
    Scope,
    TenantAccessWaiver,
    TenantAccessWaiverRegistry,
    check_tenant_isolation,
)
from shared.auth.engine import AuthorizationEngine

TENANT_ALPHA = "00000000-0000-0000-0000-000000000001"
TENANT_BETA = "00000000-0000-0000-0000-000000000002"

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=1)


@pytest.fixture
def user_alpha() -> Principal:
    return Principal(
        subject_id="user-alpha-001",
        roles=frozenset({Role.EXPANSION_USER, Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=TENANT_ALPHA, clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )


@pytest.fixture
def user_beta() -> Principal:
    return Principal(
        subject_id="user-beta-001",
        roles=frozenset({Role.EXPANSION_USER, Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=TENANT_BETA, clearance=DataClassification.CONFIDENTIAL),
        authenticated=True,
    )


@pytest.fixture
def admin_alpha() -> Principal:
    return Principal(
        subject_id="admin-alpha-001",
        roles=frozenset({Role.PLATFORM_ADMIN}),
        scope=Scope(tenant_id=TENANT_ALPHA, clearance=DataClassification.HIGHLY_RESTRICTED),
        authenticated=True,
    )


@pytest.fixture
def admin_no_tenant() -> Principal:
    return Principal(
        subject_id="admin-global-001",
        roles=frozenset({Role.PLATFORM_ADMIN}),
        scope=Scope(tenant_id=None, clearance=DataClassification.HIGHLY_RESTRICTED),
        authenticated=True,
    )


@pytest.fixture
def valid_waiver() -> TenantAccessWaiver:
    return TenantAccessWaiver(
        waiver_id="WAIVER-2026-001",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        scope=frozenset({"site_market_context", "listing", "market_cell_profile"}),
        approved_by="security_officer_01",
        reason="Cross-tenant operational audit approved under ticket SEC-4091",
        expires_at=FUTURE,
        created_at=NOW,
    )


def _registry_with(*waivers: TenantAccessWaiver) -> TenantAccessWaiverRegistry:
    registry = TenantAccessWaiverRegistry()
    for waiver in waivers:
        registry.register(waiver)
    return registry


@pytest.fixture
def sample_site_context_payload() -> dict[str, Any]:
    return {
        "document_id": "smc-doc-test-001",
        "generated_at": "2026-08-14T00:00:00Z",
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "tenant_id": TENANT_ALPHA,
        "contexts": [
            {
                "context_id": "smc-ctx-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "identity": {
                    "site_id": "site-taipei-001",
                    "site_name": "Taipei Xinyi Store #1",
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "primary_h3_index": "8928308280fffff",
                    "h3_resolution": 9,
                    "district": "Xinyi",
                    "county": "Taipei City",
                },
                "catchment": {
                    "catchment_id": "cat-001",
                    "travel_mode": "pedestrian",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "status": "available",
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demand": {
                    "status": "available",
                    "total_population": 50000.0,
                    "male_population": 24000.0,
                    "female_population": 26000.0,
                    "household_count": 18000.0,
                    "density_per_sq_km": 1000.0,
                    "daytime_population_ratio": 1.2,
                },
                "poi": {
                    "status": "available",
                    "total_poi_count": 500,
                    "convenience_stores_count": 20,
                    "commercial_centers_count": 5,
                    "transit_stations_count": 4,
                    "schools_count": 6,
                    "hospitals_count": 2,
                    "density_per_sq_km": 50.0,
                },
                "competitor": {
                    "status": "available",
                    "total_competitors": 10,
                    "active_competitors": 8,
                    "competitor_density_per_sq_km": 2.0,
                    "brands_present": ["BrandA"],
                },
                "rent": {
                    "status": "available",
                    "mean_rent_per_ping": 2500.0,
                    "median_rent_per_ping": 2400.0,
                    "p25_rent_per_ping": 2000.0,
                    "p75_rent_per_ping": 3000.0,
                    "sample_count": 30,
                    "confidence_pct": 95.0,
                },
                "listing": {
                    "status": "available",
                    "active_listings_count": 15,
                    "average_area_ping": 30.0,
                    "mean_asking_rent_per_ping": 2600.0,
                    "median_asking_rent_per_ping": 2500.0,
                },
                "mobility": {
                    "status": "available",
                    "activity_population": 15000,
                    "resident_population": 10000,
                    "work_population": 8000,
                },
                "traffic": {
                    "status": "available",
                    "mean_speed_kph": 35.0,
                    "hourly_volume_vph": 1200,
                    "motorcycle_hourly_volume": 600,
                    "motorcycle_ratio": 0.5,
                    "level_of_service": "C",
                    "is_congestion_hotspot": False,
                },
                "event": {
                    "status": "available",
                    "active_events_count": 0,
                    "events": [],
                },
                "coverage": {
                    "overall_readiness": "ready",
                    "domain_coverage": {"GEOGRAPHY": "complete"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-1"],
                    "observation_count": 100,
                    "sample_count": 100,
                },
            }
        ],
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
        },
    }





# ===========================================================================
# 1. Pure Guard Tests: check_tenant_isolation
# ===========================================================================


def test_guard_same_tenant_allowed(user_alpha: Principal, admin_alpha: Principal) -> None:
    decision_user = check_tenant_isolation(user_alpha, TENANT_ALPHA)
    assert decision_user.allowed is True

    decision_admin = check_tenant_isolation(admin_alpha, TENANT_ALPHA)
    assert decision_admin.allowed is True


def test_guard_cross_tenant_denied_for_normal_user(user_alpha: Principal) -> None:
    decision = check_tenant_isolation(user_alpha, TENANT_BETA)
    assert decision.allowed is False
    assert decision.policy_id == "tenant_isolation"
    assert "Cross-tenant access denied" in decision.reason


def test_guard_cross_tenant_denied_for_platform_admin_by_default(
    admin_alpha: Principal,
) -> None:
    """PLATFORM_ADMIN must fail closed on cross-tenant access without a formal waiver."""
    decision = check_tenant_isolation(admin_alpha, TENANT_BETA)
    assert decision.allowed is False
    assert decision.policy_id == "tenant_isolation"
    assert "without an approved time-bounded risk acceptance" in decision.reason


def test_guard_missing_resource_tenant_denied(user_alpha: Principal) -> None:
    decision_none = check_tenant_isolation(user_alpha, None)
    assert decision_none.allowed is False
    assert decision_none.policy_id == "tenant_isolation"
    assert "Resource tenant missing" in decision_none.reason

    decision_empty = check_tenant_isolation(user_alpha, "")
    assert decision_empty.allowed is False
    assert decision_empty.policy_id == "tenant_isolation"


def test_guard_missing_principal_tenant_denied(admin_no_tenant: Principal) -> None:
    decision = check_tenant_isolation(admin_no_tenant, TENANT_ALPHA)
    assert decision.allowed is False
    assert decision.policy_id == "tenant_isolation"
    assert "Principal missing tenant scope" in decision.reason


def test_guard_unauthenticated_principal_denied() -> None:
    decision = check_tenant_isolation(ANONYMOUS, TENANT_ALPHA)
    assert decision.allowed is False
    assert decision.policy_id == "tenant_isolation"
    assert "not authenticated" in decision.reason


# ===========================================================================
# 2. Formal Waiver / Risk Acceptance Validation Tests
# ===========================================================================


def test_valid_waiver_permits_cross_tenant_access(
    admin_alpha: Principal, valid_waiver: TenantAccessWaiver
) -> None:
    registry = _registry_with(valid_waiver)
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="site_market_context",
        waiver=valid_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is True
    assert "cross_tenant_waiver" in decision.obligations
    assert "audit" in decision.obligations
    assert "WAIVER-2026-001" in decision.reason


def test_expired_waiver_is_rejected(
    admin_alpha: Principal, valid_waiver: TenantAccessWaiver
) -> None:
    expired_waiver = TenantAccessWaiver(
        waiver_id="WAIVER-EXPIRED",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        scope=frozenset({"*"}),
        approved_by="sec_lead",
        reason="Routine check",
        expires_at=PAST,
    )
    registry = _registry_with(expired_waiver)
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="site_market_context",
        waiver=expired_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is False
    assert "expired" in decision.reason.lower() or "invalid" in decision.reason.lower()


def test_waiver_out_of_scope_is_rejected(
    admin_alpha: Principal, valid_waiver: TenantAccessWaiver
) -> None:
    registry = _registry_with(valid_waiver)
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="unauthorized_module",
        waiver=valid_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is False
    assert "out of scope" in decision.reason.lower()


def test_waiver_wrong_principal_or_target_is_rejected(
    user_beta: Principal, valid_waiver: TenantAccessWaiver
) -> None:
    # valid_waiver is for admin-alpha-001 targeting TENANT_BETA
    # user_beta (user-beta-001) attempting to access TENANT_ALPHA using this waiver
    registry = _registry_with(valid_waiver)
    decision = check_tenant_isolation(
        user_beta,
        TENANT_ALPHA,
        resource_type="site_market_context",
        waiver=valid_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is False


def test_unbounded_waiver_without_expiry_is_rejected(admin_alpha: Principal) -> None:
    unbounded_waiver = TenantAccessWaiver(
        waiver_id="WAIVER-UNBOUNDED",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        approved_by="sec_lead",
        reason="Indefinite bypass attempt",
        expires_at=None,
    )
    registry = _registry_with(unbounded_waiver)
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="site_market_context",
        waiver=unbounded_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is False


def test_waiver_missing_signer_or_reason_is_rejected(admin_alpha: Principal) -> None:
    no_signer_waiver = TenantAccessWaiver(
        waiver_id="WAIVER-NO-SIGNER",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        approved_by="",
        reason="Reason provided",
        expires_at=FUTURE,
    )
    registry = _registry_with(no_signer_waiver)
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="site_market_context",
        waiver=no_signer_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert decision.allowed is False


# ===========================================================================
# 3. Production Entry 1: Market Intelligence API
# ===========================================================================


def test_market_intelligence_api_tenant_authorization_matrix(
    user_alpha: Principal,
    admin_alpha: Principal,
    valid_waiver: TenantAccessWaiver,
) -> None:
    audit_log = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=audit_log)
    registry = _registry_with(valid_waiver)

    # 1. Same tenant allow
    res_tenant = authorize_market_intelligence(
        "site_market_context",
        "site-001",
        tenant_id=TENANT_ALPHA,
        principal=user_alpha,
        auth_engine=engine,
    )
    assert res_tenant == TENANT_ALPHA

    # 2. Missing tenant deny (fail closed)
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "site-001",
            tenant_id=None,
            principal=user_alpha,
            auth_engine=engine,
        )
    assert exc_info.value.code == "missing_tenant"

    # 3. Cross-tenant deny for normal user
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "site-001",
            tenant_id=TENANT_BETA,
            principal=user_alpha,
            auth_engine=engine,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 4. Cross-tenant deny for PLATFORM_ADMIN by default
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "site-001",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
            auth_engine=engine,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 5. Formal waiver allows PLATFORM_ADMIN cross-tenant access with immutable audit log
    res_waiver_tenant = authorize_market_intelligence(
        "site_market_context",
        "site-001",
        tenant_id=TENANT_BETA,
        principal=admin_alpha,
        auth_engine=engine,
        waiver=valid_waiver,
        waiver_registry=registry,
    )
    assert res_waiver_tenant == TENANT_BETA

    # Verify audit events
    events = audit_log.list_events()
    assert len(events) >= 3
    # Check that denial and waiver-allow events are recorded
    denial_events = [e for e in events if e.outcome == "deny"]
    assert any("tenant_isolation" in e.metadata.get("policy_id", "") for e in denial_events)

    waiver_allow_events = [
        e for e in events if e.outcome == "allow" and "WAIVER-2026-001" in e.metadata.get("reason", "")
    ]
    assert len(waiver_allow_events) == 1
    assert waiver_allow_events[0].actor == "admin-alpha-001"
    assert waiver_allow_events[0].resource == "site_market_context/site-001"


# ===========================================================================
# 4. Production Entry 2: Market Data Read Facade
# ===========================================================================


def test_market_data_facade_tenant_authorization_matrix(
    user_alpha: Principal,
    admin_alpha: Principal,
    valid_waiver: TenantAccessWaiver,
    sample_site_context_payload: dict[str, Any],
) -> None:
    import copy

    registry = _registry_with(valid_waiver)
    transport = InMemoryDataPlatformTransport()
    # Seed data
    doc_alpha = copy.deepcopy(sample_site_context_payload)
    doc_alpha["tenant_id"] = TENANT_ALPHA
    doc_alpha["contexts"][0]["identity"]["site_id"] = "site-001"
    transport.store_document("emgi.site-market-context.v1", "smc-001", doc_alpha)

    doc_beta = copy.deepcopy(sample_site_context_payload)
    doc_beta["document_id"] = "smc-beta-001"
    doc_beta["tenant_id"] = TENANT_BETA
    doc_beta["contexts"][0]["identity"]["site_id"] = "site-beta-001"
    transport.store_document("emgi.site-market-context.v1", "smc-beta-001", doc_beta)


    client = DataPlatformClient(transport=transport)
    audit_log = InMemoryAuditLog()
    auth_engine = AuthorizationEngine(audit_log=audit_log)
    facade = MarketDataFacade(
        client=client,
        auth_engine=auth_engine,
        enforce_auth=True,
        waiver_registry=registry,
    )

    # 1. Same tenant allow
    ctx = facade.get_site_market_context(
        "site-001",
        period_key="2026-08",
        tenant_id=TENANT_ALPHA,
        principal=user_alpha,
    )
    assert ctx.identity.site_id == "site-001"


    # 2. Missing tenant deny (fail closed)
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-001",
            period_key="2026-08",
            tenant_id=None,
            principal=user_alpha,
        )
    assert exc_info.value.code == "missing_tenant"

    # 3. Cross-tenant deny for normal user
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-beta-001",
            period_key="2026-08",
            tenant_id=TENANT_BETA,
            principal=user_alpha,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 4. Cross-tenant deny for PLATFORM_ADMIN by default
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-beta-001",
            period_key="2026-08",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 5. Formal waiver allows PLATFORM_ADMIN cross-tenant access with immutable audit
    ctx_waiver = facade.get_site_market_context(
        "site-beta-001",
        period_key="2026-08",
        tenant_id=TENANT_BETA,
        principal=admin_alpha,
        waiver=valid_waiver,
    )
    assert ctx_waiver.identity.site_id == "site-beta-001"

    # Verify audit events
    events = audit_log.list_events()
    waiver_events = [
        e for e in events if e.outcome == "allow" and "WAIVER-2026-001" in e.metadata.get("reason", "")
    ]
    assert len(waiver_events) == 1
    assert waiver_events[0].actor == "admin-alpha-001"
    assert waiver_events[0].resource == "site_market_context/site-beta-001"


# ===========================================================================
# 5. Production Entry 3: Assisted Listing Intake Authorization
# ===========================================================================


def test_intake_authorization_tenant_guard_matrix(
    user_alpha: Principal,
    admin_alpha: Principal,
    valid_waiver: TenantAccessWaiver,
) -> None:
    audit_log = InMemoryAuditLog()
    registry = _registry_with(valid_waiver)

    # 1. Same tenant allow
    authorize_intake_action(
        user_alpha,
        "view",
        resource={"id": "L-101", "tenantId": TENANT_ALPHA, "owner": "user-alpha-001"},
        audit_log=audit_log,
        correlation_id="corr-same-tenant",
    )

    # 2. Missing tenant on target resource fails closed
    with pytest.raises(HTTPException) as exc_info:
        authorize_intake_action(
            user_alpha,
            "view",
            resource={"id": "L-101", "owner": "user-alpha-001"},
            audit_log=audit_log,
            correlation_id="corr-missing-res-tenant",
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "TENANT_SCOPE_DENIED"

    # 3. Cross-tenant deny for normal user
    with pytest.raises(HTTPException) as exc_info:
        authorize_intake_action(
            user_alpha,
            "view",
            resource={"id": "L-102", "tenantId": TENANT_BETA, "owner": "user-alpha-001"},
            audit_log=audit_log,
            correlation_id="corr-cross-tenant-user",
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "TENANT_SCOPE_DENIED"

    # 4. Cross-tenant deny for PLATFORM_ADMIN by default
    with pytest.raises(HTTPException) as exc_info:
        authorize_intake_action(
            admin_alpha,
            "view",
            resource={"id": "L-102", "tenantId": TENANT_BETA},
            audit_log=audit_log,
            correlation_id="corr-cross-tenant-admin",
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail in {"TENANT_SCOPE_DENIED", "ROLE_DENIED"}

    # 5. Formal waiver allows cross-tenant access with immutable audit log
    # Manager role required for intake operations
    admin_manager = Principal(
        subject_id="admin-alpha-001",
        roles=frozenset({Role.PLATFORM_ADMIN, Role.SITE_REVIEWER}),
        scope=Scope(tenant_id=TENANT_ALPHA, clearance=DataClassification.HIGHLY_RESTRICTED),
        authenticated=True,
    )
    authorize_intake_action(
        admin_manager,
        "view",
        resource={"id": "L-102", "tenantId": TENANT_BETA},
        audit_log=audit_log,
        correlation_id="corr-waiver-admin",
        waiver=valid_waiver,
        waiver_registry=registry,
    )

    events = audit_log.list_events()
    waiver_events = [e for e in events if e.correlation_id == "corr-waiver-admin"]
    assert len(waiver_events) == 1
    assert waiver_events[0].outcome == "allow"
    assert waiver_events[0].actor == "admin-alpha-001"
    assert waiver_events[0].resource == "listing/L-102"


def test_untrusted_signer_waiver_is_rejected_fail_closed(
    admin_alpha: Principal,
    sample_site_context_payload: dict[str, Any],
) -> None:
    """Attacker-crafted waivers with unauthorized signers are rejected fail-closed across all entry points."""
    forged_waiver = TenantAccessWaiver(
        waiver_id="FORGED-001",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        approved_by="attacker",
        reason="Malicious cross-tenant bypass attempt",
        expires_at=FUTURE,
    )
    registry = _registry_with(forged_waiver)

    # 1. Pure guard denies forged signer
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        waiver=forged_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert not decision.allowed
    assert "unsigned/unauthorized" in decision.reason or "invalid" in decision.reason

    # 2. Market intelligence entry point denies forged signer
    audit_log = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=audit_log)
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "site-beta-001",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
            auth_engine=engine,
            waiver=forged_waiver,
            waiver_registry=registry,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 3. Market data facade denies forged signer
    import copy

    transport = InMemoryDataPlatformTransport()
    doc_beta = copy.deepcopy(sample_site_context_payload)
    doc_beta["document_id"] = "smc-beta-001"
    doc_beta["tenant_id"] = TENANT_BETA
    doc_beta["contexts"][0]["identity"]["site_id"] = "site-beta-001"
    transport.store_document("emgi.site-market-context.v1", "smc-beta-001", doc_beta)

    client = DataPlatformClient(transport=transport)
    facade_audit_log = InMemoryAuditLog()
    facade_engine = AuthorizationEngine(audit_log=facade_audit_log)
    facade = MarketDataFacade(
        client=client,
        auth_engine=facade_engine,
        enforce_auth=True,
        waiver_registry=registry,
    )

    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-beta-001",
            period_key="2026-08",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
            waiver=forged_waiver,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 4. Assisted listing intake denies forged signer
    with pytest.raises(HTTPException) as exc_info:
        authorize_intake_action(
            admin_alpha,
            "view",
            resource={"id": "L-forged", "tenantId": TENANT_BETA},
            audit_log=InMemoryAuditLog(),
            correlation_id="corr-forged-waiver-intake",
            waiver=forged_waiver,
            waiver_registry=registry,
        )
    assert exc_info.value.status_code == 403


def test_known_authorized_signer_without_registered_waiver_fails_closed(
    admin_alpha: Principal,
    sample_site_context_payload: dict[str, Any],
) -> None:
    """A caller cannot self-issue a waiver by copying a recognized signer name."""
    forged_waiver = TenantAccessWaiver(
        waiver_id="UNREGISTERED-KNOWN-SIGNER-001",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        approved_by="security_officer_01",
        reason="Caller supplied waiver with a known signer name",
        expires_at=FUTURE,
    )
    registry = TenantAccessWaiverRegistry()

    # 1. Shared guard rejects the unregistered waiver object.
    decision = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        resource_type="site_market_context",
        waiver=forged_waiver,
        waiver_registry=registry,
        on=NOW,
    )
    assert not decision.allowed
    assert "not registered" in decision.reason

    # 2. Market intelligence rejects the same unregistered waiver object.
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "site-beta-known-signer",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
            waiver=forged_waiver,
            waiver_registry=registry,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 3. Market data facade rejects it before reading the target document.
    import copy

    transport = InMemoryDataPlatformTransport()
    doc_beta = copy.deepcopy(sample_site_context_payload)
    doc_beta["document_id"] = "smc-known-signer-beta-001"
    doc_beta["tenant_id"] = TENANT_BETA
    doc_beta["contexts"][0]["identity"]["site_id"] = "site-beta-known-signer"
    transport.store_document(
        "emgi.site-market-context.v1", "smc-known-signer-beta-001", doc_beta
    )
    facade = MarketDataFacade(
        client=DataPlatformClient(transport=transport),
        auth_engine=AuthorizationEngine(audit_log=InMemoryAuditLog()),
        enforce_auth=True,
        waiver_registry=registry,
    )
    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-beta-known-signer",
            period_key="2026-08",
            tenant_id=TENANT_BETA,
            principal=admin_alpha,
            waiver=forged_waiver,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 4. Assisted listing intake rejects it through the shared guard.
    with pytest.raises(HTTPException) as exc_info:
        authorize_intake_action(
            admin_alpha,
            "view",
            resource={"id": "L-known-signer", "tenantId": TENANT_BETA},
            audit_log=InMemoryAuditLog(),
            correlation_id="corr-known-signer-intake",
            waiver=forged_waiver,
            waiver_registry=registry,
        )
    assert exc_info.value.status_code == 403


def test_dynamic_signer_registration_and_registry_lookup(
    admin_alpha: Principal,
) -> None:
    """Waiver registry supports string ID resolution and dynamic authorized signers."""
    custom_signer = "custom_sec_lead_99"
    registry = TenantAccessWaiverRegistry()

    waiver = TenantAccessWaiver(
        waiver_id="WAIVER-REG-99",
        principal_id="admin-alpha-001",
        target_tenant_id=TENANT_BETA,
        approved_by=custom_signer,
        reason="Registered waiver test",
        expires_at=FUTURE,
    )
    registry.register(waiver)

    # Before signer authorization: fails
    dec_unauthorized = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        waiver="WAIVER-REG-99",
        waiver_registry=registry,
        on=NOW,
    )
    assert not dec_unauthorized.allowed

    # Authorize signer in custom registry
    registry.register_signer(custom_signer)
    dec_authorized = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        waiver="WAIVER-REG-99",
        waiver_registry=registry,
        on=NOW,
    )
    assert dec_authorized.allowed

    # Unregister signer: fails closed
    registry.unregister_signer(custom_signer)
    dec_revoked = check_tenant_isolation(
        admin_alpha,
        TENANT_BETA,
        waiver="WAIVER-REG-99",
        waiver_registry=registry,
        on=NOW,
    )
    assert not dec_revoked.allowed


def test_market_intelligence_resource_envelope_tenant_and_missing_envelope_deny(
    user_alpha: Principal,
) -> None:
    """Market intelligence derives tenant from target resource envelope and rejects missing/cross envelope tenants."""
    audit_log = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=audit_log)

    # 1. Resource envelope with matching tenant: allow
    valid_envelope = {"tenant_id": TENANT_ALPHA, "document_id": "doc-001"}
    tenant_out = authorize_market_intelligence(
        "site_market_context",
        "doc-001",
        resource_envelope=valid_envelope,
        principal=user_alpha,
        auth_engine=engine,
    )
    assert tenant_out == TENANT_ALPHA

    # 2. Resource envelope with foreign tenant: cross-tenant access denied even if caller passes tenant_id=TENANT_ALPHA
    foreign_envelope = {"tenant_id": TENANT_BETA, "document_id": "doc-beta"}
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "doc-beta",
            tenant_id=TENANT_ALPHA,  # caller claims alpha
            resource_envelope=foreign_envelope,  # target envelope is beta
            principal=user_alpha,
            auth_engine=engine,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"

    # 3. Resource envelope with missing/empty tenant: fail closed
    missing_envelope = {"document_id": "doc-missing"}
    with pytest.raises(MarketIntelligenceAuthorizationError) as exc_info:
        authorize_market_intelligence(
            "site_market_context",
            "doc-missing",
            resource_envelope=missing_envelope,
            principal=user_alpha,
            auth_engine=engine,
        )
    assert exc_info.value.code == "missing_tenant"


def test_market_data_facade_missing_payload_envelope_failclosed(
    user_alpha: Principal,
    sample_site_context_payload: dict[str, Any],
) -> None:
    """MarketDataFacade fails closed on payloads missing envelope tenant or mismatched tenant."""
    import copy
    from collections.abc import Mapping

    # 1. Payload explicitly lacking tenant_id
    doc_no_tenant = copy.deepcopy(sample_site_context_payload)
    doc_no_tenant.pop("tenant_id", None)
    doc_no_tenant["tenant_id"] = None
    doc_no_tenant["contexts"][0]["identity"]["site_id"] = "site-no-tenant"

    class _MissingTenantTransport:
        def fetch_document(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
            return doc_no_tenant

    client = DataPlatformClient(transport=_MissingTenantTransport())
    audit_log = InMemoryAuditLog()
    auth_engine = AuthorizationEngine(audit_log=audit_log)
    facade = MarketDataFacade(client=client, auth_engine=auth_engine, enforce_auth=True)

    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade.get_site_market_context(
            "site-no-tenant",
            period_key="2026-08",
            tenant_id=TENANT_ALPHA,
            principal=user_alpha,
        )
    assert exc_info.value.code == "missing_tenant"

    # 2. Payload returned from platform has different tenant (mismatched envelope)
    doc_beta = copy.deepcopy(sample_site_context_payload)
    doc_beta["tenant_id"] = TENANT_BETA
    doc_beta["contexts"][0]["identity"]["site_id"] = "site-beta"

    class _BetaTransport:
        def fetch_document(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
            return doc_beta

    client_beta = DataPlatformClient(transport=_BetaTransport())
    facade_beta = MarketDataFacade(client=client_beta, auth_engine=auth_engine, enforce_auth=True)

    with pytest.raises(MarketDataAuthorizationError) as exc_info:
        facade_beta.get_site_market_context(
            "site-beta",
            period_key="2026-08",
            tenant_id=TENANT_ALPHA,
            principal=user_alpha,
        )
    assert exc_info.value.code == "cross_tenant_access_denied"


def test_immutable_audit_on_all_successful_allow_paths(
    user_alpha: Principal,
    sample_site_context_payload: dict[str, Any],
) -> None:
    """All successful allow paths in market intelligence, facade, and intake write immutable audit events."""
    import copy

    # 1. Market intelligence plain same-tenant VIEW
    mi_audit_log = InMemoryAuditLog()
    mi_engine = AuthorizationEngine(audit_log=mi_audit_log)
    authorize_market_intelligence(
        "site_market_context",
        "site-001",
        tenant_id=TENANT_ALPHA,
        principal=user_alpha,
        auth_engine=mi_engine,
    )
    mi_events = mi_audit_log.list_events()
    assert len(mi_events) == 1
    assert mi_events[0].outcome == "allow"
    assert mi_events[0].actor == "user-alpha-001"

    # 2. Market data facade plain same-tenant VIEW
    transport = InMemoryDataPlatformTransport()
    doc_alpha = copy.deepcopy(sample_site_context_payload)
    doc_alpha["tenant_id"] = TENANT_ALPHA
    doc_alpha["contexts"][0]["identity"]["site_id"] = "site-001"
    transport.store_document("emgi.site-market-context.v1", "smc-001", doc_alpha)

    client = DataPlatformClient(transport=transport)
    facade_audit_log = InMemoryAuditLog()
    facade_engine = AuthorizationEngine(audit_log=facade_audit_log)
    facade = MarketDataFacade(client=client, auth_engine=facade_engine, enforce_auth=True)

    ctx = facade.get_site_market_context(
        "site-001",
        period_key="2026-08",
        tenant_id=TENANT_ALPHA,
        principal=user_alpha,
    )
    assert ctx.identity.site_id == "site-001"
    facade_events = facade_audit_log.list_events()
    assert len(facade_events) == 1
    assert facade_events[0].outcome == "allow"
    assert facade_events[0].actor == "user-alpha-001"

    # 3. Intake authorization plain same-tenant VIEW
    intake_audit_log = InMemoryAuditLog()
    authorize_intake_action(
        user_alpha,
        "view",
        resource={"id": "L-101", "tenantId": TENANT_ALPHA, "owner": "user-alpha-001"},
        audit_log=intake_audit_log,
        correlation_id="corr-plain-view",
    )
    intake_events = intake_audit_log.list_events()
    assert len(intake_events) == 1
    assert intake_events[0].outcome == "allow"
    assert intake_events[0].actor == "user-alpha-001"
