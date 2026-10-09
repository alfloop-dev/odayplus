"""Real service envelopes, offline inputs only; never live grant evidence."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.app.routes.operator_modules.network_reviews import create_network_review_sub_router
from apps.api.app.routes.operator_modules.network_scoring import create_network_scoring_sub_router
from apps.api.oday_api.security.dependencies import (
    OPERATOR_NETWORK_READ_RESOURCE,
    require_operator_permission,
)
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_reviews import NetworkReviewService
from modules.opsboard.application.network_scoring import NetworkScoringService
from shared.auth import Action

HEADERS = {
    "X-Subject-Id": "read-admin", "X-Roles": "platform_admin,operator_viewer",
    "X-Tenant-Id": "tenant-a", "X-Operator-Role": "pm-audit",
}
AXES = [
    ("X-Heat-Zone-Ids", "heatZoneId", "HZ-01"),
    ("X-Brand-Ids", "brandId", "brand-a"),
    ("X-Region-Ids", "regionId", "north"),
    ("X-Store-Ids", "storeId", "store-a"),
    ("X-Assigned-Area-Ids", "assignedAreaId", "area-a"),
]


def services_and_client(axis: str = "heatZoneId", allowed: str = "HZ-01"):
    listing_seed = NetworkListingService().export_state()
    # Preserve the full real producer envelope (including pipeline summaries,
    # counts, heat-zone demand/rent/coordinates and canonical relationships).
    listing_seed["candidates"] = [
        {"id": "CS-1001", "listingId": "L-2024", "heatZoneId": "HZ-01", "reviewId": "RV-702"},
        {"id": "CS-1002", "listingId": "L-2025", "heatZoneId": "HZ-02", "reviewId": "RV-701", "tenantId": "tenant-a"},
    ]
    listing_seed["siteReviews"] = [
        {"id": "RV-702", "candidateId": "CS-1001"},
        {"id": "RV-701", "candidateId": "CS-1002"},
    ]
    for row in listing_seed["listings"]:
        if axis != "heatZoneId":
            row[axis] = allowed if row["id"] == "L-2024" else "excluded"
    listing_seed["auditEvents"] = [
        {"id": "A1", "targetType": "listing", "targetId": "L-2024", "message": "includes excluded L-2025", "metadata": {"other": "L-2025"}},
        {"id": "A2", "targetType": "listing", "targetId": "L-2025"},
        {"id": "A3", "targetType": "unknown", "targetId": "L-2024"},
    ]
    listings = NetworkListingService(initial_state=listing_seed, seed_fixtures=False)
    scoring = NetworkScoringService()
    reviews = NetworkReviewService()
    app = FastAPI()

    def scope_snapshot(request):
        return listings.snapshot(tenant_id=request.state.operator_principal.tenant_id)

    app.include_router(create_network_listings_sub_router(
        listings, require_view_permission_fn=require_operator_permission("listing", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE),
    ))
    app.include_router(create_network_scoring_sub_router(
        scoring, require_view_permission_fn=require_operator_permission("sitescore", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        require_write_permission_fn=require_operator_permission("sitescore", Action.EXECUTE),
        read_scope_snapshot_fn=scope_snapshot,
    ))
    app.include_router(create_network_review_sub_router(
        reviews, require_view_permission_fn=require_operator_permission("sitescore", Action.VIEW, scoped_read_resource=OPERATOR_NETWORK_READ_RESOURCE),
        require_decide_permission_fn=require_operator_permission("sitescore", Action.APPROVE),
        read_scope_snapshot_fn=scope_snapshot,
    ))
    return listings, scoring, reviews, TestClient(app)


@pytest.mark.parametrize(("header", "axis", "allowed"), AXES)
def test_all_network_envelopes_apply_verified_scope_before_aggregates(header, axis, allowed):
    listings, scoring, reviews, client = services_and_client(axis, allowed)
    before = deepcopy((listings.export_state(), scoring.export_state(), reviews.export_state()))
    headers = {**HEADERS, header: allowed}
    response = client.get("/network-listings", headers=headers)
    assert response.status_code == 200, response.text
    snap = response.json()
    expected_listings = {"L-2024", "L-2030"} if axis == "heatZoneId" else {"L-2024"}
    assert {r["id"] for r in snap["listings"]} == expected_listings
    assert [r["id"] for r in snap["candidates"]] == ["CS-1001"]
    assert [r["id"] for r in snap["siteReviews"]] == ["RV-702"]
    assert snap["counts"] == {key: len(snap[key]) for key in snap["counts"]}
    assert [r["id"] for r in snap["heatZones"]] == (["HZ-01"] if axis == "heatZoneId" else [])
    assert snap["selectedHeatZoneId"] == ("HZ-01" if axis == "heatZoneId" else None)
    assert snap["expansionSteps"] == []
    assert [r["id"] for r in snap["auditEvents"]] == ["A1"]
    assert "message" not in snap["auditEvents"][0] and "metadata" not in snap["auditEvents"][0]
    assert "L-2025" not in str(snap) and "CS-1002" not in str(snap)
    score = client.get("/network-scoring", headers=headers)
    assert score.status_code == 200, score.text
    score = score.json()
    assert [r["id"] for r in score["candidates"]] == ["CS-1001"]
    assert [r["id"] for r in score["scorecards"]] == ["CS-1001"]
    assert [r["id"] for r in score["batchResults"]] == ["CS-1001"]
    assert score["counts"] == {"candidates": 1, "scored": 1, "gateBlocked": 0}
    assert score["compareSet"] == ["CS-1001"]
    assert [r["id"] for r in score["compare"]["columns"]] == ["CS-1001"]
    assert all([v["id"] for v in metric["values"]] == ["CS-1001"] for metric in score["compare"]["metrics"])
    assert "CS-1002" not in str(score) and "L-2025" not in str(score)
    review = client.get("/network-reviews", headers=headers)
    assert review.status_code == 200, review.text
    review = review.json()
    assert [r["id"] for r in review["reviews"]] == ["RV-702"]
    assert [r["id"] for r in review["candidates"]] == ["CS-1001"]
    assert [r["reviewId"] for r in review["approvals"]] == ["RV-702"]
    assert review["counts"] == {"reviews": 1, "pending": 1, "decided": 0}
    assert review["reviews"][0]["compareText"] == ""
    assert "CS-1002" not in str(review) and "RV-701" not in str(review)
    assert (listings.export_state(), scoring.export_state(), reviews.export_state()) == before


def test_review_decisions_and_audit_are_scoped_with_real_producer_records():
    _, _, reviews, client = services_and_client()
    for review_id in ("RV-702", "RV-701"):
        reviews.decide_review(
            review_id=review_id, decision="GO", reason="Reviewed complete evidence independently",
            conditions="Rent improvement required", actor_role_id="siteReviewer", actor_name="reviewer",
            override_ack=True, idempotency_key=review_id, correlation_id="scope-test",
        )
    response = client.get("/network-reviews", headers={**HEADERS, "X-Heat-Zone-Ids": "HZ-01"})
    assert response.status_code == 200, response.text
    snap = response.json()
    assert [row["reviewId"] for row in snap["decisions"]] == ["RV-702"]
    assert [row["targetId"] for row in snap["auditEvents"]] == ["RV-702"]
    assert snap["counts"] == {"reviews": 1, "pending": 0, "decided": 1}
    assert "CS-1002" not in response.text and "RV-701" not in response.text


def test_nonviewer_routes_retain_existing_full_envelopes():
    listings, scoring, reviews, client = services_and_client()
    headers = {**HEADERS, "X-Roles": "site_reviewer", "X-Operator-Role": "expansion-manager"}
    for path, service in (("network-listings", listings), ("network-scoring", scoring), ("network-reviews", reviews)):
        result = client.get(f"/{path}", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["counts"] == service.snapshot()["counts"]
    assert client.get("/network-listings", headers={**headers, "X-Roles": "platform_admin", "X-Operator-Role": "platform-admin"}).status_code == 403


def test_unknown_scope_and_unauthorized_zone_query_fail_closed():
    _, _, _, client = services_and_client()
    headers = {**HEADERS, "X-Heat-Zone-Ids": "HZ-01"}
    assert client.get("/network-listings?selectedHeatZoneId=HZ-02", headers=headers).status_code == 403
    for path in ("network-listings", "network-scoring", "network-reviews"):
        result = client.get(f"/{path}", headers={**HEADERS, "X-Store-Ids": "unknown"})
        assert result.status_code == 200, result.text
        snap = result.json()
        assert not any(snap["counts"].values())
        assert snap["candidates"] == []
        assert snap["auditEvents"] == []


def test_child_tenant_scope_conflicts_and_related_intake_data_are_not_disclosed():
    listings, scoring, reviews, client = services_and_client()
    listings._state["candidates"][0]["tenant_id"] = "tenant-b"
    scoring._candidates[0]["scope"] = {"heat_zone_id": "HZ-02"}
    headers = {**HEADERS, "X-Heat-Zone-Ids": "HZ-01"}
    assert client.get("/network-listings", headers=headers).json()["candidates"] == []
    assert client.get("/network-scoring", headers=headers).json()["candidates"] == []
    assert client.get("/network-reviews", headers=headers).json()["reviews"] == []
    intake = {"id": "I1", "tenantId": "tenant-a", "heatZoneId": "HZ-01", "stage": "READY",
              "matchResult": {"targetListingId": "L-2025", "summary": "Excluded address"},
              "auditEvents": [{"id": "IA", "targetType": "intake", "targetId": "I1", "message": "Excluded L-2025"}]}
    listings._save_intake(intake)
    for path in ("/network-listings", "/network-listings/intake", "/network-listings/intake/I1"):
        result = client.get(path, headers=headers)
        assert result.status_code == 200, result.text
        assert "L-2025" not in result.text and "Excluded address" not in result.text
