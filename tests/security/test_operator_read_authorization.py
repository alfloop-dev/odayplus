"""Opt-in operator reads: no implicit admin/business mutation or tenant bypass."""

import re

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.main import create_app
from apps.api.oday_api.security.dependencies import (
    OPERATOR_NETWORK_READ_RESOURCE,
    require_operator_permission,
)
from modules.listing.application.intake_authorization import authorize_intake_action
from shared.audit import InMemoryAuditLog
from shared.auth import (
    AccessRequest,
    Action,
    AuthorizationEngine,
    DataClassification,
    Principal,
    ResourceDescriptor,
    Role,
    Scope,
    permissions_for,
    rbac_allows,
)

READ_RESOURCES = {"operator_console", "operator_network"}
# General domain routers are RBAC-only (no tenant/object projection).
DOMAIN_RESOURCES = {"listing", "sitescore", "heatzone"}


def viewer(*roles: Role, **scope) -> Principal:
    return Principal(
        subject_id="read-admin",
        roles=frozenset({Role.OPERATOR_VIEWER, *roles}),
        scope=Scope(tenant_id="tenant-a", **scope),
    )


def test_viewer_is_finite_view_only_and_composes_with_admin() -> None:
    grants = permissions_for(frozenset({Role.OPERATOR_VIEWER}))
    assert {p.resource for p in grants} == READ_RESOURCES
    assert {p.action for p in grants} == {Action.VIEW}
    for resource in READ_RESOURCES:
        assert rbac_allows(viewer(), resource, Action.VIEW)
        assert not rbac_allows(Principal("pure-admin", frozenset({Role.PLATFORM_ADMIN})), resource, Action.VIEW)
        for action in Action:
            if action != Action.VIEW:
                assert not rbac_allows(viewer(Role.PLATFORM_ADMIN), resource, action)
    assert rbac_allows(viewer(Role.PLATFORM_ADMIN), "user", Action.UPDATE)
    assert not rbac_allows(viewer(), "user", Action.VIEW)
    for resource in DOMAIN_RESOURCES:
        for action in Action:
            assert not rbac_allows(viewer(Role.PLATFORM_ADMIN), resource, action)
    assert not rbac_allows(Principal("tenantless", frozenset({Role.OPERATOR_VIEWER})), "listing", Action.VIEW)


@pytest.mark.parametrize("tenant", [None, "tenant-b"])
def test_viewer_object_tenant_evidence_required_and_audited(tenant) -> None:
    log = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=log)
    denied = engine.authorize(AccessRequest(viewer(Role.PLATFORM_ADMIN), Action.VIEW, ResourceDescriptor(type="operator_network", tenant_id=tenant)))
    assert not denied.allowed
    assert denied.policy_id == "operator.tenant_isolation"
    assert log.list_events()[-1].outcome == "deny"


@pytest.mark.parametrize(("scope", "resource"), [
    ({"brand_ids": frozenset({"brand-a"})}, {"brand_id": "brand-b"}),
    ({"region_ids": frozenset({"north"})}, {"region_id": "south"}),
    ({"store_ids": frozenset({"store-a"})}, {"store_id": "store-b"}),
    ({"modules": frozenset({"network"})}, {"module": "other"}),
    ({"clearance": DataClassification.INTERNAL}, {"data_classification": DataClassification.RESTRICTED}),
])
def test_read_grant_keeps_abac_restrictions(scope, resource) -> None:
    engine = AuthorizationEngine()
    assert engine.authorize(AccessRequest(viewer(), Action.VIEW, ResourceDescriptor(type="operator_network", tenant_id="tenant-a"))).allowed
    assert not engine.authorize(AccessRequest(viewer(), Action.VIEW, ResourceDescriptor(type="operator_network", tenant_id="tenant-b"))).allowed
    assert not engine.authorize(AccessRequest(viewer(**scope), Action.VIEW, ResourceDescriptor(type="operator_network", tenant_id="tenant-a", **resource))).allowed


def test_intake_view_composes_with_admin_but_not_writes_or_foreign_objects() -> None:
    log = InMemoryAuditLog()
    p = viewer(Role.PLATFORM_ADMIN)
    authorize_intake_action(p, "view", tenant_id="tenant-a", audit_log=log)
    assert log.list_events()[-1].outcome == "allow"
    for action in ("submit_url", "correct", "decide", "merge", "promote", "convert", "purge", "export", "reopen_failed"):
        with pytest.raises(HTTPException) as exc:
            authorize_intake_action(p, action, tenant_id="tenant-a", risk_acknowledged=True, risk_summary="test risk")
        assert exc.value.status_code == 403
    for resource in ({"id": "foreign", "tenantId": "tenant-b"}, {"id": "missing"}):
        with pytest.raises(HTTPException) as exc:
            authorize_intake_action(p, "view", resource=resource)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        authorize_intake_action(Principal("admin", frozenset({Role.PLATFORM_ADMIN}), Scope(tenant_id="tenant-a")), "view", tenant_id="tenant-a")


def test_collection_reads_check_supplied_filters_but_creates_need_complete_scope() -> None:
    combined = viewer(
        Role.PLATFORM_ADMIN,
        brand_ids=frozenset({"brand-a"}),
        heat_zone_ids=frozenset({"HZ-01"}),
    )
    authorize_intake_action(
        combined, "view", collection_scope={"heatZoneId": "HZ-01"}, tenant_id="tenant-a"
    )
    authorize_intake_action(
        combined, "view", collection_scope={"brandId": "brand-a", "heatZoneId": "HZ-01"},
        tenant_id="tenant-a",
    )
    for filters in ({"heatZoneId": "HZ-02"}, {"brandId": "brand-b", "heatZoneId": "HZ-01"}):
        with pytest.raises(HTTPException) as denied:
            authorize_intake_action(combined, "view", collection_scope=filters, tenant_id="tenant-a")
        assert denied.value.detail == "SCOPE_DENIED"
    # Target objects still need evidence on every restricted axis.
    with pytest.raises(HTTPException) as denied:
        authorize_intake_action(
            combined, "view", {"id": "L-1", "tenantId": "tenant-a", "heatZoneId": "HZ-01"}
        )
    assert denied.value.detail == "SCOPE_DENIED"
    # A create lands an object, so its envelope must be complete too.
    creator = Principal(
        subject_id="staff", roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-a", brand_ids=frozenset({"brand-a"}),
                    heat_zone_ids=frozenset({"HZ-01"})),
    )
    with pytest.raises(HTTPException) as denied:
        authorize_intake_action(
            creator, "submit_url", collection_scope={"heatZoneId": "HZ-01"}, tenant_id="tenant-a"
        )
    assert denied.value.detail == "SCOPE_DENIED"


def test_intake_collections_filter_scope_before_counts_and_keep_field_masks() -> None:
    own = {"id": "own", "tenantId": "tenant-a", "regionId": "north", "storeId": "store-a", "stage": "READY", "parsedFields": {"contactPhone": {"sourceValue": "private"}}}
    other = {**own, "id": "other", "regionId": "south"}
    missing = {**own, "id": "missing", "storeId": None}

    class Service:
        def list_intakes(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-a"
            return [own, other, missing]

        def snapshot(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-a"
            return {"listings": [own, other, missing], "assistedIntakes": [own, other, missing]}

        def get_intake(self, intake_id):
            return other

    app = FastAPI()
    app.include_router(create_network_listings_sub_router(
        Service(),
        require_view_permission_fn=require_operator_permission("listing", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE),
    ))
    client = TestClient(app)
    headers = {"X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer", "X-Tenant-Id": "tenant-a", "X-Operator-Role": "pm-audit", "X-Region-Ids": "north", "X-Store-Ids": "store-a"}
    result = client.get("/network-listings/intake", headers=headers)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["total"] == body["counts"]["ready"] == 1
    assert body["items"][0]["id"] == "own"
    assert body["items"][0]["parsedFields"]["contactPhone"]["sourceValue"] is None
    snap = client.get("/network-listings", headers=headers).json()
    assert [row["id"] for row in snap["listings"]] == ["own"]
    assert [row["id"] for row in snap["assistedIntakes"]] == ["own"]
    assert client.get("/network-listings/intake/other", headers=headers).status_code == 403


def test_partial_collection_filters_stay_denied_for_unprojected_callers() -> None:
    # Only the operator read grant has its returned rows projected against the
    # complete scope, so only it may declare a partial filter. A manager-style
    # caller restricted to brand-a + HZ-01 keeps the complete-envelope denial.
    reviewer = Principal(
        subject_id="reviewer", roles=frozenset({Role.SITE_REVIEWER}),
        scope=Scope(tenant_id="tenant-a", brand_ids=frozenset({"brand-a"}),
                    heat_zone_ids=frozenset({"HZ-01"})),
    )
    with pytest.raises(HTTPException) as denied:
        authorize_intake_action(
            reviewer, "view", collection_scope={"heatZoneId": "HZ-01"}, tenant_id="tenant-a"
        )
    assert denied.value.detail == "SCOPE_DENIED"
    authorize_intake_action(
        reviewer, "view", collection_scope={"brandId": "brand-a", "heatZoneId": "HZ-01"},
        tenant_id="tenant-a",
    )

    own = {"id": "own", "tenantId": "tenant-a", "brandId": "brand-a", "heatZoneId": "HZ-01", "stage": "READY"}
    foreign = {**own, "id": "foreign", "brandId": "brand-b", "stage": "NEEDS_REVIEW"}

    class Service:
        def list_intakes(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-a"
            return [own, foreign]

        def snapshot(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-a"
            return {"listings": [own, foreign], "assistedIntakes": [own, foreign]}

    app = FastAPI()
    app.include_router(create_network_listings_sub_router(
        Service(),
        require_view_permission_fn=require_operator_permission("listing", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE),
    ))
    client = TestClient(app)
    scope_headers = {"X-Tenant-Id": "tenant-a", "X-Brand-Ids": "brand-a", "X-Heat-Zone-Ids": "HZ-01"}
    reviewer_headers = {**scope_headers, "X-Subject-Id": "reviewer", "X-Roles": "site_reviewer", "X-Operator-Role": "site-reviewer"}
    for path in ("/network-listings/intake", "/network-listings"):
        refused = client.get(path, params={"selectedHeatZoneId": "HZ-01"}, headers=reviewer_headers)
        assert refused.status_code == 403, (path, refused.text)
        assert refused.json()["detail"] == "SCOPE_DENIED"

    viewer_headers = {**scope_headers, "X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer", "X-Operator-Role": "pm-audit"}
    allowed = client.get("/network-listings/intake", params={"selectedHeatZoneId": "HZ-01"}, headers=viewer_headers)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert [row["id"] for row in body["items"]] == ["own"]
    assert body["total"] == body["counts"]["ready"] == 1
    assert body["counts"]["needsReview"] == 0
    snap = client.get("/network-listings", params={"selectedHeatZoneId": "HZ-01"}, headers=viewer_headers)
    assert snap.status_code == 200, snap.text
    assert [row["id"] for row in snap.json()["listings"]] == ["own"]
    assert [row["id"] for row in snap.json()["assistedIntakes"]] == ["own"]
    foreign_filter = client.get("/network-listings/intake", params={"selectedHeatZoneId": "HZ-02"}, headers=viewer_headers)
    assert foreign_filter.status_code == 403


def test_operator_routes_select_verified_read_persona_and_refuse_business_writes() -> None:
    log = InMemoryAuditLog()
    client = TestClient(create_app(audit_log=log, external_provider_validation=lambda: None))
    headers = {"X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer", "X-Tenant-Id": "tenant-a", "X-Operator-Role": "pm-audit"}
    for path in ("bootstrap", "network-listings", "network-listings/intake", "network-scoring", "network-reviews", "governance/snapshot"):
        result = client.get(f"/api/v1/operator/{path}", headers=headers)
        assert result.status_code == 200, (path, result.text)
    assert client.get("/api/v1/operator/bootstrap", headers={**headers, "X-Operator-Role": "expansion-manager"}).status_code == 403
    assert client.get("/api/v1/operator/bootstrap", headers={**headers, "X-Tenant-Id": ""}).status_code == 403
    for path in ("network-listings/reset", "network-scoring/score", "network-reviews/unknown/decide", "governance/decisions"):
        result = client.post(f"/api/v1/operator/{path}", headers=headers, json={"action": "approve"})
        assert result.status_code == 403, (path, result.text)
    assert any(event.outcome == "allow" for event in log.list_events())
    assert any(event.outcome == "deny" for event in log.list_events())


@pytest.mark.parametrize(
    ("roles", "decide", "export"),
    [
        # The deployed dev account: server-verified, but no business decision grant.
        ("auditor,platform_admin", False, False),
        ("platform_admin,operator_viewer", False, False),
        ("operations_manager", True, True),
    ],
)
def test_governance_snapshot_reports_the_write_guards_verdict_not_the_persona(roles, decide, export) -> None:
    client = TestClient(create_app(audit_log=InMemoryAuditLog(), external_provider_validation=lambda: None))
    # Every principal views through the 營運主管 persona; it must not matter.
    headers = {"X-Subject-Id": "authority-probe", "X-Roles": roles, "X-Tenant-Id": "tenant-a", "X-Operator-Role": "ops-lead"}
    snapshot = client.get("/api/v1/operator/governance/snapshot", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    authority = snapshot.json()["actionAuthority"]
    assert authority == {
        "verified": True,
        "systemRoles": sorted(roles.split(",")),
        "decide": decide,
        "exportEvidence": export,
    }
    # The flag mirrors the unchanged guard: a principal it calls read-only is
    # refused by POST /decisions, and a deciding principal gets past RBAC.
    decision = client.post(
        "/api/v1/operator/governance/decisions",
        headers=headers,
        json={"approvalId": "APR-AUTHORITY-PROBE", "action": "approve"},
    )
    assert (decision.status_code == 403) is not decide, decision.text


def test_scoped_read_resource_admits_only_view_on_opted_in_guards() -> None:
    app = FastAPI()
    guards = {
        "scoped": require_operator_permission("listing", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        "unscoped": require_operator_permission("listing", Action.VIEW),
        "write": require_operator_permission("listing", Action.UPDATE, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
    }
    for name, guard in guards.items():
        app.add_api_route(f"/{name}", lambda: {"ok": True}, dependencies=[Depends(guard)])
    client = TestClient(app)
    headers = {"X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer", "X-Tenant-Id": "tenant-a", "X-Operator-Role": "pm-audit"}
    assert client.get("/scoped", headers=headers).status_code == 200
    assert client.get("/unscoped", headers=headers).status_code == 403
    assert client.get("/write", headers=headers).status_code == 403
    assert client.get("/scoped", headers={**headers, "X-Roles": "platform_admin"}).status_code == 403
    assert client.get("/scoped", headers={**headers, "X-Tenant-Id": ""}).status_code == 403


VIEWER = {"X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer", "X-Operator-Role": "pm-audit"}
# General RBAC-only domain reads (no principal tenant/scope filtering) plus the
# Operator rebalance snapshot, which has no scoped projection for this reader.
UNSCOPED_DOMAIN_READS = (
    "/api/v1/sitescore/realized", "/api/v1/sitescore/reports", "/api/v1/sitescore/reports/probe",
    "/api/v1/sitescore/decisions/probe", "/api/v1/heatzones", "/api/v1/heatzones/map",
    "/api/v1/heatzones/probe", "/api/v1/heatzones/compositions", "/api/v1/listings/candidates",
    "/api/v1/listings/addresses/probe", "/api/v1/operator/network-rebalance",
)


@pytest.fixture(scope="module")
def real_app_client() -> TestClient:
    return TestClient(create_app(external_provider_validation=lambda: None))


@pytest.mark.parametrize("scope_headers", [
    {"X-Tenant-Id": "tenant-a"},
    {"X-Tenant-Id": "tenant-b"},
    {"X-Tenant-Id": "tenant-a", "X-Heat-Zone-Ids": "HZ-01"},
    {"X-Tenant-Id": "tenant-b", "X-Brand-Ids": "brand-a", "X-Region-Ids": "north"},
])
def test_viewer_is_refused_by_unscoped_domain_routes_for_every_tenant_and_scope(real_app_client, scope_headers) -> None:
    control = {"X-Subject-Id": "control", "X-Roles": "expansion_user", "X-Operator-Role": "expansion-staff", **scope_headers}
    for path in UNSCOPED_DOMAIN_READS:
        result = real_app_client.get(path, headers={**VIEWER, **scope_headers})
        assert result.status_code == 403, (path, result.text)
        # Non-vacuous: the same route admits a domain role past its RBAC guard.
        assert real_app_client.get(path, headers=control).status_code != 403, path


def test_viewer_adds_no_read_outside_scoped_operator_routes(real_app_client) -> None:
    """Differential over every real GET route: the read grant may only open
    Operator Console routes, never a general domain router."""

    paths = sorted({
        re.sub(r"\{[^}]+\}", "probe", path)
        for path, ops in real_app_client.app.openapi()["paths"].items() if "get" in ops
    })
    assert set(UNSCOPED_DOMAIN_READS) <= set(paths)
    gained = []
    for path in paths:
        for tenant in ("tenant-a", "tenant-b"):
            base = {"X-Subject-Id": "read-admin", "X-Operator-Role": "pm-audit", "X-Tenant-Id": tenant}
            granted = real_app_client.get(path, headers={**base, "X-Roles": "platform_admin,operator_viewer"})
            admin = real_app_client.get(path, headers={**base, "X-Roles": "platform_admin"})
            if granted.status_code < 300 and admin.status_code >= 300:
                gained.append(path)
    assert gained, "read grant must still open the Operator Console"
    assert [path for path in gained if not path.startswith("/api/v1/operator/")] == []
    assert not any(path.startswith("/api/v1/operator/network-rebalance") for path in gained)
