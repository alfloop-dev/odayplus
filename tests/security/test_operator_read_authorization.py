"""Opt-in operator reads: no implicit admin/business mutation or tenant bypass."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.main import create_app
from apps.api.oday_api.security.dependencies import require_operator_permission
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

READ_RESOURCES = {"operator_console", "listing", "sitescore", "heatzone"}


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
    assert not rbac_allows(Principal("tenantless", frozenset({Role.OPERATOR_VIEWER})), "listing", Action.VIEW)


@pytest.mark.parametrize("tenant", [None, "tenant-b"])
def test_viewer_object_tenant_evidence_required_and_audited(tenant) -> None:
    log = InMemoryAuditLog()
    engine = AuthorizationEngine(audit_log=log)
    denied = engine.authorize(AccessRequest(viewer(Role.PLATFORM_ADMIN), Action.VIEW, ResourceDescriptor(type="listing", tenant_id=tenant)))
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
    assert not engine.authorize(AccessRequest(viewer(), Action.VIEW, ResourceDescriptor(type="listing", tenant_id="tenant-b"))).allowed
    assert not engine.authorize(AccessRequest(viewer(**scope), Action.VIEW, ResourceDescriptor(type="listing", tenant_id="tenant-a", **resource))).allowed


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
        require_view_permission_fn=require_operator_permission("listing", Action.VIEW),
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
