from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import listings
from modules.listing.application import intake_sla_commands
from shared.auth import Role
from shared.domain.events import validate_event
from tests.intake_registry_fixture import APPROVER, AT, MANAGER, OWNER, TENANT, _provider

HEADERS = {
    "x-subject-id": MANAGER,
    "x-tenant-id": TENANT,
    "x-roles": "site_reviewer,expansion_user",
}


@pytest.fixture
def journey(monkeypatch):
    class Clock(datetime):
        instant = AT

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz or UTC)

    monkeypatch.setattr(listings, "datetime", Clock)
    provider, identities, _ = _provider()
    store = listings.AssistedIntakeStore()
    app = FastAPI()
    app.include_router(
        listings.create_assisted_intake_router(store, workflow_policy=provider), prefix="/api/v1"
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.invalid/calendar", "scope": {"tenant_id": TENANT}},
        headers={**HEADERS, "Idempotency-Key": f"synthetic-submit-{uuid4()}"},
    )
    assert response.status_code == 202, response.text
    sla = next(iter(store.slas.values()))
    Clock.instant = AT + timedelta(minutes=5)
    return client, store, sla, Clock, provider, identities


def _command(
    journey,
    action="pause",
    *,
    headers=None,
    reason="Synthetic approved hold",
    key=None,
    version=None,
):
    client, store, initial, _, _, _ = journey
    sla = store.slas[initial["sla_instance_id"]]
    body = {"reason": reason}
    if action == "pause":
        body["expected_resume_at"] = "2026-10-13T03:00:00Z"
    return client.post(
        f"/api/v1/sla-instances/{sla['sla_instance_id']}/actions/{action}",
        json=body,
        headers={
            **(headers or HEADERS),
            "Idempotency-Key": key or f"synthetic-{action}-{uuid4()}",
            "If-Match": f'W/"{version or sla["version"]}"',
        },
    )


def _snapshot(store):
    return deepcopy(
        tuple(
            getattr(store, name)
            for name in (
                "intakes",
                "assignments",
                "slas",
                "workflow_audits",
                "workflow_outbox",
                "replays",
            )
        )
    )


def test_http_calendar_pause_resume_fresh_detail_audit_and_idempotency(journey):
    client, store, initial, clock, _, _ = journey
    response = _command(journey, key="synthetic-pause-replay")
    assert response.status_code == 200, response.text
    paused = response.json()
    record = store.slas[initial["sla_instance_id"]]
    interval = record["pause_intervals"][0]
    assert interval["approved_by"] == APPROVER and interval["created_by"] == MANAGER
    assert paused["active_pause_interval_id"] == interval["pause_interval_id"]
    assert paused["audit_event_id"] in store.workflow_audits
    snapshot = _snapshot(store)
    assert _command(journey, key="synthetic-pause-replay", version=1).json() == paused
    assert _snapshot(store) == snapshot

    clock.instant = datetime(2026, 10, 13, 2, tzinfo=UTC)
    response = _command(journey, "resume", key="synthetic-resume-replay")
    assert response.status_code == 200, response.text
    resumed = response.json()
    assert resumed["state"] == "ON_TRACK"
    assert resumed["due_at"] == "2026-10-13T03:55:00Z"
    assert resumed["due_soon_at"] == "2026-10-13T02:55:00Z"
    assert resumed["paused_duration_seconds"] == int(
        (clock.instant - (AT + timedelta(minutes=5))).total_seconds()
    )
    assert resumed["active_pause_interval_id"] is None
    record = store.slas[initial["sla_instance_id"]]
    assert record["pause_intervals"][0]["resumed_at"] == "2026-10-13T02:00:00Z"
    assert record["breach_at"] == "2026-10-13T05:55:00Z"
    assert record["version"] == 3
    detail = client.get(f"/api/v1/intakes/{record['intake_id']}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json()["due_at"] == resumed["due_at"]
    assert detail.json()["sla_state"] == resumed["state"]
    assert detail.json()["sla_receipt"] == resumed["audit_event_id"]
    audit = store.workflow_audits[resumed["audit_event_id"]]
    assert (
        audit["before_value"]["state"] == "PAUSED"
        and audit["after_value"]["due_at"] == resumed["due_at"]
    )
    assert audit["correlation_id"] == resumed["correlation_id"]
    for event in store.workflow_outbox.values():
        assert not validate_event(event)
    snapshot = _snapshot(store)
    assert _command(journey, "resume", key="synthetic-resume-replay", version=2).json() == resumed
    assert _snapshot(store) == snapshot


@pytest.mark.parametrize(
    "mode,status",
    [
        ("reason", 409),
        ("self_approval", 403),
        ("disabled", 403),
        ("revoked_role", 403),
        ("staff", 403),
        ("missing_schedule", 503),
    ],
)
def test_authority_denials_leave_no_state_or_receipt(journey, mode, status):
    _, store, initial, _, _, identities = journey
    headers, reason = HEADERS, "Synthetic approved hold"
    if mode == "reason":
        reason = "Unapproved synthetic reason"
    elif mode == "self_approval":
        headers = {**HEADERS, "x-subject-id": APPROVER, "x-roles": "data_owner,expansion_user"}
    elif mode == "disabled":
        account = identities.find_account_by_id(MANAGER)
        identities.save_account(replace(account, status="disabled"))
    elif mode == "revoked_role":
        identities.set_account_roles(MANAGER, {Role.EXPANSION_USER})
    elif mode == "staff":
        headers = {**HEADERS, "x-subject-id": OWNER, "x-roles": "expansion_user"}
    else:
        initial.pop("schedule")
    snapshot = _snapshot(store)
    response = _command(journey, headers=headers, reason=reason)
    assert response.status_code == status, response.text
    assert _snapshot(store) == snapshot


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_evidence_failure_rolls_back_and_same_key_can_retry(journey, monkeypatch, action):
    _, store, _, clock, _, _ = journey
    if action == "resume":
        assert _command(journey).status_code == 200
        clock.instant = datetime(2026, 10, 13, 2, tzinfo=UTC)
    original = intake_sla_commands.enqueue_workflow_event

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic outbox write failure after audit creation")

    monkeypatch.setattr(intake_sla_commands, "enqueue_workflow_event", fail)
    snapshot = _snapshot(store)
    response = _command(journey, action, key=f"synthetic-retry-{action}")
    assert response.status_code == 503
    assert _snapshot(store) == snapshot
    monkeypatch.setattr(intake_sla_commands, "enqueue_workflow_event", original)
    assert _command(journey, action, key=f"synthetic-retry-{action}").status_code == 200


def test_corrupt_active_interval_cannot_be_resumed(journey):
    _, store, initial, clock, _, _ = journey
    assert _command(journey).status_code == 200
    store.slas[initial["sla_instance_id"]]["active_pause_interval_id"] = str(uuid4())
    clock.instant = datetime(2026, 10, 13, 2, tzinfo=UTC)
    snapshot = _snapshot(store)
    assert _command(journey, "resume").status_code == 503
    assert _snapshot(store) == snapshot


def test_steward_uses_the_same_approved_calendar(journey):
    _, _, _, _, _, identities = journey
    identities.set_account_roles(MANAGER, {Role.DATA_OWNER})
    response = _command(journey, headers={**HEADERS, "x-roles": "data_owner,expansion_user"})
    assert response.status_code == 200, response.text


def test_stale_on_track_state_cannot_pause_after_authoritative_breach(journey):
    _, store, _, clock, _, _ = journey
    clock.instant = datetime(2026, 10, 13, 3, 30, tzinfo=UTC)
    snapshot = _snapshot(store)
    assert _command(journey).status_code == 409
    assert _snapshot(store) == snapshot


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_cached_receipt_rechecks_current_registry_identity_authority(journey, action):
    _, store, _, clock, _, identities = journey
    if action == "resume":
        assert _command(journey).status_code == 200
        clock.instant = datetime(2026, 10, 13, 2, tzinfo=UTC)
    version = 1 if action == "pause" else 2
    key = f"synthetic-revoked-cache-{action}"
    assert _command(journey, action, key=key, version=version).status_code == 200
    identities.set_account_roles(MANAGER, {Role.EXPANSION_USER})
    snapshot = _snapshot(store)
    response = _command(journey, action, key=key, version=version)
    assert response.status_code == 403, response.text
    assert _snapshot(store) == snapshot
