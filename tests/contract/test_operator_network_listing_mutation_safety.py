"""The real HTTP guard uses stored holds and preserves listing mutation receipts."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.network_listings import create_network_listings_sub_router
from apps.api.oday_api.security.dependencies import require_operator_permission
from modules.opsboard.application.network_listings import NetworkListingService
from shared.auth import Action

SOURCE = "00000000-0000-0000-0000-000000000201"
TARGET = "00000000-0000-0000-0000-000000000202"
HEADERS = {
    "x-subject-id": "operator-expansion-manager", "x-roles": "site_reviewer,expansion_user",
    "x-operator-role": "expansion-manager", "x-tenant-id": "tenant-a",
    "Idempotency-Key": "http-mutation-once",
    "X-Correlation-Id": "00000000-0000-0000-0000-000000000301",
}


def _client():
    service = NetworkListingService()
    source = deepcopy(service._listing("L-2029"))
    target = deepcopy(service._listing("L-2025"))
    source.update(id=SOURCE, duplicateOfId=TARGET, version=7, hasLegalHold=False)
    target.update(id=TARGET, version=11, hasLegalHold=False)
    # Existing fixture expansion steps still refer to their original records;
    # add independent UUID rows rather than deleting those supporting inputs.
    service._state["listings"].extend([source, target])
    app = FastAPI()
    app.include_router(create_network_listings_sub_router(
        service, require_view_permission_fn=require_operator_permission("listing", Action.VIEW),
        require_write_permission_fn=require_operator_permission("listing", Action.UPDATE),
    ), prefix="/api/v1/operator")
    return TestClient(app), service


def _post(client, operation, *, headers=None, **payload):
    body = {"reason": "Operator supplied actual reason"}
    if operation == "merge":
        body.update(targetListingId=TARGET, riskSummary="Retain recorded source evidence",
                    riskAcknowledged=True, targetIfMatch='W/"11"')
    current_headers = {"If-Match": 'W/"7"', **(headers or HEADERS)}
    return client.post(f"/api/v1/operator/network-listings/listings/{SOURCE}/{operation}",
                       headers=current_headers, json={**body, **payload})


@pytest.mark.parametrize("field", ["hasLegalHold", "legalHold", "has_legal_hold", "legal_hold"])
@pytest.mark.parametrize("operation,record", [("archive", SOURCE), ("merge", SOURCE),
                                               ("merge", TARGET)])
def test_http_recorded_hold_is_409_even_when_client_sends_no_hold(field, operation, record):
    client, service = _client()
    service._listing(record)[field] = True
    before = service.export_state()
    response = _post(client, operation, hasLegalHold=False, legalHold=False)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "LEGAL_HOLD_CONFLICT"
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("operation,record", [("archive", SOURCE), ("merge", SOURCE),
                                               ("merge", TARGET)])
@pytest.mark.parametrize("terminal", [{"status": "archived"},
                                    {"mergedIntoId": "previous-survivor"}])
def test_http_terminal_record_is_409_and_preserves_evidence(operation, record, terminal):
    client, service = _client()
    service._listing(record).update(terminal)
    before = service.export_state()
    response = _post(client, operation)
    assert response.status_code == 409, response.text
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


def test_http_archive_replay_is_original_and_new_operation_does_not_replace_reason():
    client, service = _client()
    first = _post(client, "archive")
    assert first.status_code == 200, first.text
    before = service.export_state()
    replay = _post(client, "archive")
    assert replay.status_code == 200 and replay.json() == first.json()
    denied = _post(client, "archive", headers={**HEADERS, "Idempotency-Key": "new-key"},
                   reason="Changed reason on new logical operation")
    assert denied.status_code == 409, denied.text
    assert service.export_state() == before
    assert len([event for event in before["auditEvents"] if event["action"] == "listing.archive"]) == 1


@pytest.mark.parametrize("operation", ["merge", "archive"])
def test_http_permissions_are_checked_before_hold_or_terminal_state_is_disclosed(operation):
    client, service = _client()
    service._listing(SOURCE).update(legalHold=True, status="archived")
    before = service.export_state()
    response = _post(client, operation, headers={**HEADERS, "x-roles": "auditor",
                                               "x-operator-role": "audit-pm"})
    assert response.status_code == 403, response.text
    assert service.export_state() == before


@pytest.mark.parametrize("operation", ["merge", "archive"])
def test_http_other_tenant_is_denied_without_effect(operation):
    client, service = _client()
    before = service.export_state()
    response = _post(client, operation, headers={**HEADERS, "x-tenant-id": "tenant-b"})
    assert response.status_code == 403, response.text
    assert service.export_state() == before


@pytest.mark.parametrize("operation,record", [("archive", SOURCE), ("merge", SOURCE),
                                               ("merge", TARGET)])
def test_http_new_hold_denies_original_receipt_on_replay(operation, record):
    client, service = _client()
    first = _post(client, operation)
    assert first.status_code == 200, first.text
    service._listing(record)["legalHold"] = True
    before = service.export_state()
    replay = _post(client, operation)
    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"] == "LEGAL_HOLD_CONFLICT"
    assert service.export_state() == before



INTAKE = "00000000-0000-0000-0000-000000000401"
OTHER_INTAKE = "00000000-0000-0000-0000-000000000402"
DECIDE_AXES = [
    ("X-Brand-Ids", "brandId", "brand-a"),
    ("X-Region-Ids", "regionId", "north"),
    ("X-Store-Ids", "storeId", "store-a"),
    ("X-Assigned-Area-Ids", "assignedAreaId", "area-a"),
    ("X-Heat-Zone-Ids", "heatZoneId", "HZ-01"),
]


def _decide_client():
    client, service = _client()
    scope = {field: value for _, field, value in DECIDE_AXES}
    for ident in (SOURCE, TARGET):
        service._listing(ident).update(scope)
    intake = {
        "id": INTAKE, "tenantId": "tenant-a", "sourceId": "SRC-SYNTHETIC",
        "originalUrl": "https://example.invalid/recorded-intake", "snapshotId": "recorded-source",
        "stage": "NEEDS_REVIEW", "version": 1, "auditEvents": [], **scope,
        "submitter": "independent-submitter",
        "matchResult": {"outcome": "REVISION", "targetListingId": TARGET, "confidence": 0.63},
        "parsedFields": {name: {"normalizedValue": value, "correctedValue": None} for name, value in {
            "providerListingId": "recorded-intake", "address": "Synthetic current address",
            "rent": 55000, "areaPing": 25, "floor": "1F",
        }.items()},
    }
    service._intakes.save_intake(intake)
    service.get_intake(INTAKE)
    return client, service, intake


def _post_decide(client, action="revise", *, intake_id=INTAKE, headers=None, target=TARGET):
    return client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        headers=headers or HEADERS,
        json={"action": action, "targetListingId": target,
              "reason": "Operator names the actual listing to decide",
              "riskSummary": "Only current authorized target may be revised",
              "riskAcknowledged": True},
    )


@pytest.mark.parametrize("token", ['W/"2"', 'W/"0"', '"1"', 'W/"01"'])
def test_decide_http_rejects_a_stale_or_invalid_intake_token_without_effect(token):
    client, service, _ = _decide_client()
    before = service.export_state()
    response = _post_decide(client, headers={**HEADERS, "If-Match": token})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "VERSION_CONFLICT"
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


def test_decide_http_replay_binds_the_original_intake_token_after_version_advances():
    client, service, _ = _decide_client()
    headers = {**HEADERS, "If-Match": 'W/"1"'}
    first = _post_decide(client, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2
    before = service.export_state()
    replay = _post_decide(client, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    changed = _post_decide(client, headers={**headers, "If-Match": 'W/"2"'})
    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"] == "IDEMPOTENCY_CONFLICT"
    assert service.export_state() == before


@pytest.mark.parametrize("token,version,status,code", [
    (None, 1, 428, "PRECONDITION_REQUIRED"),
    ('W/"1"', None, 503, "INTAKE_VERSION_UNAVAILABLE"),
])
def test_nonfixture_decide_http_maps_missing_or_unavailable_intake_version(token, version, status, code):
    client, service, intake = _decide_client()
    tenant = "00000000-0000-0000-0000-000000000001"
    actor = "00000000-0000-0000-0000-000000000101"
    service._seed_fixtures = False
    service._tenant_id = tenant
    for row in service._state["listings"]:
        row["tenantId"] = tenant
    intake.update(tenantId=tenant, version=version)
    service._intakes.save_intake(intake)
    service.get_intake(INTAKE)
    headers = {**HEADERS, "x-tenant-id": tenant, "x-subject-id": actor}
    if token is not None:
        headers["If-Match"] = token
    before = service.export_state()
    response = _post_decide(client, headers=headers)
    assert response.status_code == status, response.text
    assert response.json()["detail"] == code
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("action", ["revise", "duplicate"])
@pytest.mark.parametrize("header,field,allowed", DECIDE_AXES)
@pytest.mark.parametrize("actual_value", [None, "excluded"])
def test_decide_http_authorizes_actual_target_on_every_restricted_scope_axis(
    action, header, field, allowed, actual_value,
):
    client, service, _ = _decide_client()
    service._listing(TARGET)[field] = actual_value
    before = service.export_state()
    response = _post_decide(client, action, headers={**HEADERS, header: allowed})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "SCOPE_DENIED"
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("action", ["revise", "duplicate"])
@pytest.mark.parametrize("target_tenant", [None, "tenant-b"])
def test_decide_http_does_not_assign_caller_tenant_to_unowned_or_foreign_target(action, target_tenant):
    client, service, _ = _decide_client()
    service._listing(TARGET)["tenantId"] = target_tenant
    before = service.export_state()
    response = _post_decide(client, action)
    assert response.status_code == 403, response.text
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("action", ["revise", "duplicate"])
def test_decide_http_refuses_ambiguous_target_id_before_changing_any_listing(action):
    client, service, _ = _decide_client()
    duplicate = deepcopy(service._listing(TARGET))
    duplicate["rentPerMonth"] = 1
    service._state["listings"].append(duplicate)
    before = service.export_state()
    response = _post_decide(client, action)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "AMBIGUOUS_LISTING_ID"
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == []


@pytest.mark.parametrize("original_action", ["revise", "duplicate"])
@pytest.mark.parametrize("changed_action", ["revise", "quarantine"])
@pytest.mark.parametrize("revoked", ["original-intake", "original-target"])
def test_decide_http_changed_intake_and_body_cannot_escape_original_receipt_authorization(
    original_action, changed_action, revoked,
):
    client, service, intake = _decide_client()
    headers = {**HEADERS, "X-Heat-Zone-Ids": "HZ-01"}
    first = _post_decide(client, original_action, headers=headers)
    assert first.status_code == 200, first.text
    other = deepcopy(intake)
    other.update(id=OTHER_INTAKE, matchResult={"targetListingId": SOURCE, "confidence": None})
    service._intakes.save_intake(other)
    if revoked == "original-intake":
        original = service._intakes.get_intake(INTAKE)
        original["heatZoneId"] = "excluded"
        service._intakes.save_intake(original)
    else:
        service._listing(TARGET)["heatZoneId"] = "excluded"
    service.get_intake(OTHER_INTAKE)
    before = service.export_state()
    receipts = deepcopy(service._intakes.list_idempotency_records())
    response = _post_decide(client, changed_action, intake_id=OTHER_INTAKE,
                            headers=headers, target=SOURCE)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "SCOPE_DENIED"
    assert service.export_state() == before
    assert service._intakes.list_idempotency_records() == receipts


@pytest.mark.parametrize("action", ["revise", "duplicate"])
def test_decide_http_valid_replay_is_exact_but_changed_target_still_conflicts(action):
    client, service, _ = _decide_client()
    first = _post_decide(client, action)
    assert first.status_code == 200, first.text
    before = service.export_state()
    replay = _post_decide(client, action)
    assert replay.status_code == 200 and replay.json() == first.json()
    changed = _post_decide(client, action, target=SOURCE)
    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"] == "IDEMPOTENCY_CONFLICT"
    assert service.export_state() == before
    assert len(service._intakes.list_idempotency_records()) == 1
