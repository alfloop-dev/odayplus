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
