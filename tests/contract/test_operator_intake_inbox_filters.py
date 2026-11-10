from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.security.dependencies import require_operator_permission
from modules.listing.application.intake_authorization import mask_intake
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable
from modules.opsboard.application.network_listings import (
    InMemoryAssistedIntakeRepository,
    NetworkListingService,
)
from shared.auth import Action, DataClassification, Principal, Role, Scope
from shared.infrastructure.persistence.assisted_listing_intake import (
    AssistedIntakePersistenceConflict,
)
from shared.jobs.queue import JobFenceRejectedError

PATH = "/api/v1/operator/network-listings/intake"
HEADERS = {
    "x-subject-id": "actor-a", "x-roles": "operator_viewer", "x-tenant-id": "tenant-a",
    "x-assigned-area-ids": "area-a", "x-clearance": "CONFIDENTIAL",
}


def _row(identifier, **changes):
    return {
        "id": identifier, "tenantId": "tenant-a", "assignedAreaId": "area-a",
        "heatZoneId": "HZ-01", "sourceId": "source-one", "intakeMethod": "URL",
        "submitter": "actor-a", "submittedBy": "actor-a", "owner": "owner-a",
        "ownerSubjectId": "owner-a", "assignmentStatus": "CLAIMED", "stage": "READY",
        "slaState": "RUNNING", "parsedFields": {}, "auditEvents": [], "rawSnapshot": None,
        **changes,
    }


@pytest.fixture
def inbox():
    repository = InMemoryAssistedIntakeRepository()
    service = NetworkListingService(intake_repository=repository)
    rows = [
        _row("IN-A", stage="NEEDS_REVIEW", submittedAt="2026-10-13T00:00:00Z",
             updatedAt="2026-10-13T11:00:00Z", lastObservedAt="2026-10-13T09:00:00+08:00",
             dueAt="2026-10-13T10:00:00+08:00", failure={"retryable": True},
             parsedFields={"contactPhone": {"sourceValue": "hidden-phone", "normalizedValue": "hidden-phone"}},
             matchResult={"outcome": "POSSIBLE_MATCH"}),
        _row("IN-B", stage="FAILED", sourceId="source-two", submittedBy="actor-b",
             ownerSubjectId="owner-b", assignmentStatus="ASSIGNED", submittedAt="2026-10-13T01:00:00+08:00",
             updatedAt="2026-10-13T10:00:00Z", lastObservedAt="2026-10-13T01:00:00Z",
             dueAt="2026-10-13T01:00:00Z", failure={"retryable": False},
             parsedFields={"address": {"sourceValue": "visible address"}}),
        _row("IN-C", submittedAt=None, updatedAt=None, lastObservedAt=None, dueAt="BREACHED",
             capturedAt="2099-01-01T00:00:00Z", auditEvents=[{"occurredAt": "2099-01-01T00:00:00Z"}]),
        _row("IN-OTHER-AREA", assignedAreaId="area-b", stage="NEEDS_REVIEW", sourceId="secret-area-source"),
        _row("IN-OTHER-TENANT", tenantId="tenant-b", stage="NEEDS_REVIEW", sourceId="secret-tenant-source"),
    ]
    repository.intakes = {row["id"]: row for row in rows}
    app = FastAPI()
    app.include_router(create_network_listings_sub_router(
        service, require_view_permission_fn=require_operator_permission("listing", Action.VIEW,
                                                                         scoped_read_resource="operator_network"),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE),
    ), prefix="/api/v1/operator")
    return TestClient(app), rows, service


def _query(inbox, params=None, headers=None):
    response = inbox[0].get(PATH, params=params or {}, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("query,expected", [
    ({"sourceId": "source-two"}, ["IN-B"]),
    ({"submittedBy": "actor-b"}, ["IN-B"]),
    ({"ownerId": "owner-b"}, ["IN-B"]),
    ({"assignmentStatus": "ASSIGNED"}, ["IN-B"]),
    ({"assignedAreaId": "area-a"}, ["IN-A", "IN-B", "IN-C"]),
    ({"masked": True}, ["IN-A"]), ({"masked": False}, ["IN-B", "IN-C"]),
    ({"restricted": True}, ["IN-A"]), ({"restricted": False}, ["IN-B"]),
    ({"retryable": True}, ["IN-A"]), ({"retryable": False}, ["IN-B"]),
])
def test_each_required_filter_uses_visible_authoritative_fields(inbox, query, expected):
    result = _query(inbox, {**query, "sortBy": "id", "sortOrder": "asc"})
    assert [row["id"] for row in result["items"]] == expected
    assert result["total"] == len(expected)
    assert result["counts"] == {"needsReview": 1, "awaitingEntry": 0, "processing": 0, "blocked": 1, "ready": 1}


def test_filter_combination_and_pagination_run_after_scope_and_mask(inbox):
    result = _query(inbox, {
        "sourceId": "source-one", "submittedBy": "actor-a", "ownerId": "owner-a",
        "assignmentStatus": "CLAIMED", "assignedAreaId": "area-a", "selectedHeatZoneId": "HZ-01",
        "intakeStage": "NEEDS_REVIEW", "slaState": "RUNNING", "masked": True,
        "restricted": True, "retryable": True, "updatedFrom": "2026-10-13T19:00:00+08:00",
        "updatedTo": "2026-10-13T11:00:00Z", "page": 1, "pageSize": 1,
    })
    assert result["total"] == 1 and result["items"][0]["id"] == "IN-A"
    assert result["items"][0]["parsedFields"]["contactPhone"]["sourceValue"] is None
    later = _query(inbox, {"page": 2, "pageSize": 1, "sourceId": "source-two"})
    assert later["total"] == 1 and later["items"] == []


def test_unassigned_is_a_canonical_read_state_and_unknown_assignment_state_is_rejected(inbox):
    inbox[1][2]["assignmentStatus"] = "UNASSIGNED"
    result = _query(inbox, {"assignmentStatus": "UNASSIGNED"})
    assert [item["id"] for item in result["items"]] == ["IN-C"]
    assert inbox[0].get(PATH, params={"assignmentStatus": "invented-state"}, headers=HEADERS).status_code == 422


def test_restricted_indicator_does_not_claim_the_field_was_masked_for_a_cleared_reader(inbox):
    cleared = {**HEADERS, "x-clearance": "RESTRICTED"}
    result = _query(inbox, {"restricted": True, "masked": False}, headers=cleared)
    assert [row["id"] for row in result["items"]] == ["IN-A"]
    assert result["items"][0]["parsedFields"]["contactPhone"]["sourceValue"] == "hidden-phone"


@pytest.mark.parametrize("sort_key", ["submittedAt", "updatedAt", "dueAt"])
@pytest.mark.parametrize("order,expected", [("asc", ["IN-B", "IN-A", "IN-C"]), ("desc", ["IN-A", "IN-B", "IN-C"])])
def test_timestamp_sort_compares_instants_and_places_unknown_last(inbox, sort_key, order, expected):
    result = _query(inbox, {"sortBy": sort_key, "sortOrder": order})
    assert [row["id"] for row in result["items"]] == expected


def test_equal_instants_have_stable_id_tie_break_across_pages(inbox):
    ids = []
    for page in (1, 2, 3):
        result = _query(inbox, {"sortBy": "lastObservedAt", "sortOrder": "asc", "pageSize": 1, "page": page})
        ids.extend(row["id"] for row in result["items"])
    assert ids == ["IN-A", "IN-B", "IN-C"]


def test_inclusive_date_boundaries_timezone_equivalence_and_unknown_exclusion(inbox):
    result = _query(inbox, {"observedFrom": "2026-10-13T09:00:00+08:00", "observedTo": "2026-10-13T01:00:00Z"})
    assert {row["id"] for row in result["items"]} == {"IN-A", "IN-B"}
    assert _query(inbox, {"observedFrom": "2026-10-13T01:00:00.000001Z"})["items"] == []
    assert _query(inbox, {"updatedFrom": "2098-01-01T00:00:00Z"})["items"] == []


@pytest.mark.parametrize("query", [
    {"observedFrom": "2026-10-13"}, {"updatedTo": "2026-10-13T11:00:00"},
    {"updatedFrom": "yesterday"}, {"observedTo": "1730000000"},
    {"observedFrom": "2026-10-14T00:00:00Z", "observedTo": "2026-10-13T00:00:00Z"},
    {"updatedFrom": "2026-10-14T00:00:00Z", "updatedTo": "2026-10-13T00:00:00Z"},
    {"masked": "maybe"},
])
def test_invalid_or_reversed_ranges_are_not_silently_ignored(inbox, query):
    assert inbox[0].get(PATH, params=query, headers=HEADERS).status_code == 422


def test_foreign_records_cannot_affect_filter_results_or_counts(inbox):
    for source in ("secret-area-source", "secret-tenant-source"):
        result = _query(inbox, {"sourceId": source})
        assert result["total"] == 0 and sum(result["counts"].values()) == 3
    denied = inbox[0].get(PATH, params={"assignedAreaId": "area-b"}, headers=HEADERS)
    assert denied.status_code == 403


def test_area_scoped_manager_counts_are_projected_before_filtering(inbox):
    manager = {**HEADERS, "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager"}
    result = _query(inbox, {"assignedAreaId": "area-a"}, headers=manager)
    assert result["total"] == 3 and sum(result["counts"].values()) == 3


@pytest.mark.parametrize("field,query", [
    ("sourceId", "sourceId"), ("submittedBy", "submittedBy"), ("ownerSubjectId", "ownerId"),
    ("assignedAreaId", "assignedAreaId"),
])
def test_withheld_filter_values_are_not_membership_or_search_oracles(inbox, field, query):
    row = inbox[1][0]
    hidden = row[field]
    row["fields"] = [{"field_path": field, "classification": "RESTRICTED", "effective": hidden}]
    baseline = _query(inbox, {"sortBy": "id", "sortOrder": "asc"})
    first = next(item for item in baseline["items"] if item["id"] == "IN-A")
    assert first[field] is None
    result = _query(inbox, {query: hidden})
    assert "IN-A" not in {item["id"] for item in result["items"]}
    search = _query(inbox, {"search": hidden})
    assert "IN-A" not in {item["id"] for item in search["items"]}


def test_masked_date_does_not_affect_filter_or_sort_and_source_state_is_unchanged(inbox):
    inbox[1][0]["updatedAt_masked"] = True
    before = deepcopy(inbox[1])
    assert [row["id"] for row in _query(inbox, {"sortBy": "updatedAt", "sortOrder": "desc"})["items"]] == ["IN-B", "IN-A", "IN-C"]
    result = _query(inbox, {"updatedFrom": "2026-10-13T11:00:00Z"})
    assert result["items"] == []
    assert inbox[1] == before


@pytest.mark.parametrize("classification,known", [
    ("HIGHLY_RESTRICTED", True), ("unrecognized-classification", False),
])
@pytest.mark.parametrize("clearance,visible", [
    ("PUBLIC", False), ("INTERNAL", False), ("CONFIDENTIAL", False),
    ("RESTRICTED", False), ("HIGHLY_RESTRICTED", True),
])
def test_highest_or_unknown_classification_does_not_leak_through_filters_at_lower_clearance(
    inbox, classification, known, clearance, visible,
):
    visible = visible and known
    row = inbox[1][0]
    row["sourceId"] = "private-source-only"
    row["fields"] = [{"field_path": "sourceId", "classification": classification,
                      "parsed": "private-source-only", "normalized": "private-source-only",
                      "corrected": "private-source-only", "effective": "private-source-only"}]
    principal = Principal(subject_id="actor-a", roles=frozenset({Role.OPERATOR_VIEWER}),
                          scope=Scope(tenant_id="tenant-a", clearance=DataClassification[clearance]))
    masked = mask_intake(principal, row)
    if not visible:
        assert masked["fields"][0]["masked"] is True
        assert all(masked["fields"][0][field] is None for field in ("parsed", "normalized", "corrected", "effective"))
    headers = {**HEADERS, "x-clearance": clearance}
    if clearance in {"PUBLIC", "INTERNAL"}:
        assert inbox[0].get(PATH, params={"sourceId": "private-source-only"}, headers=headers).status_code == 403
        return
    record = next(item for item in _query(inbox, headers=headers)["items"] if item["id"] == "IN-A")
    assert record["sourceId"] == ("private-source-only" if visible else None)
    for params in ({"sourceId": "private-source-only"}, {"search": "private-source-only"}):
        result = _query(inbox, params, headers=headers)
        assert ("IN-A" in {item["id"] for item in result["items"]}) is visible


@pytest.mark.parametrize("action,body", [
    ("submit", {"url": "https://www.synthetic.example/detail-failure.html", "heatZoneId": "HZ-01"}),
    ("retry", {}),
    ("correct", {"fields": {"privateNotes": "synthetic correction"}}),
    ("decide", {"action": "new"}),
    ("promote", {}),
])
def test_canonical_persistence_conflict_reaches_the_operator_as_409(inbox, monkeypatch, action, body):
    def conflict(**kwargs):
        raise AssistedIntakePersistenceConflict("Synthetic concurrent write")
    monkeypatch.setattr(inbox[2], f"{action}_intake", conflict)
    headers = {**HEADERS, "x-subject-id": "independent-reviewer", "x-roles": "site_reviewer,expansion_user",
               "x-operator-role": "expansion-manager", "idempotency-key": f"synthetic-conflict-{action}"}
    headers.pop("x-assigned-area-ids")
    path = f"{PATH}/submit" if action == "submit" else f"{PATH}/IN-A/{action}"
    response = inbox[0].post(path, headers=headers, json={
        **body, "reason": "Synthetic operator command", "riskSummary": "Synthetic risk",
        "riskAcknowledged": True,
    })
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("action", ["submit", "retry"])
def test_missing_canonical_workflow_registry_returns_503(inbox, monkeypatch, action):
    def unavailable(**kwargs):
        raise IntakeWorkflowUnavailable("Synthetic missing registry")
    monkeypatch.setattr(inbox[2], f"{action}_intake", unavailable)
    headers = {**HEADERS, "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager"}
    headers.pop("x-assigned-area-ids")
    path = f"{PATH}/submit" if action == "submit" else f"{PATH}/IN-A/{action}"
    response = inbox[0].post(path, headers=headers, json={
        "url": "https://www.synthetic.example/detail-unavailable.html", "heatZoneId": "HZ-01",
    })
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "INTAKE_WORKFLOW_POLICY_UNAVAILABLE"


def test_retry_job_fence_conflict_returns_409(inbox, monkeypatch):
    def conflict(**kwargs):
        raise JobFenceRejectedError("Synthetic job delivery changed")
    monkeypatch.setattr(inbox[2], "retry_intake", conflict)
    headers = {**HEADERS, "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager"}
    headers.pop("x-assigned-area-ids")
    response = inbox[0].post(f"{PATH}/IN-A/retry", headers=headers, json={})
    assert response.status_code == 409, response.text
