"""SLA action boundary regressions, with explicitly seeded linked SLA resources.

Actual HTTP actions/read projection; not Operator resource provisioning or a
successful directory/Transfer proof. PostgreSQL restart coverage is separate.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes import listings
from apps.api.oday_api.main import create_app

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000101"
ALLOWED = "00000000-0000-0000-0000-000000000201"
OUTSIDE = "00000000-0000-0000-0000-000000000202"
HEADERS = {
    "x-subject-id": ACTOR, "x-tenant-id": TENANT,
    "x-roles": "site_reviewer,data_owner,expansion_user",
    "x-operator-role": "expansion-manager",
}
AXES = [
    ("brand_id", "x-brand-ids"), ("region_id", "x-region-ids"),
    ("store_id", "x-store-ids"), ("assigned_area_id", "x-assigned-area-ids"),
    ("heat_zone_id", "x-heat-zone-ids"),
]
PAUSE = {"reason": "Awaiting source feedback", "expected_resume_at": "2027-01-10T12:00:00Z"}


@pytest.fixture
def seeded():
    client = TestClient(create_app())
    submitted = client.post(
        "/api/v1/intakes/url",
        json={"original_url": f"https://example.com/sla-boundary-{uuid4()}", "scope": {"tenant_id": TENANT}},
        headers={**HEADERS, "Idempotency-Key": f"sla-boundary-submit-{uuid4()}"},
    )
    assert submitted.status_code == 202, submitted.text
    intake_id = submitted.json()["intake_id"]
    store = next(s for s in reversed(listings.AssistedIntakeStore._instances) if intake_id in s.intakes)
    sla_id = str(uuid4())
    store.slas[sla_id] = {
        "sla_instance_id": sla_id, "intake_id": intake_id, "tenant_id": TENANT,
        "state": "ON_TRACK", "due_at": "2027-01-09T12:00:00Z",
        "paused_duration_seconds": 19, "version": 23,
        "audit_event_id": str(uuid4()), "correlation_id": str(uuid4()),
    }
    return client, store, intake_id, sla_id


def command(client, sla_id, action, version=23, *, scope=None, key=None):
    return client.post(
        f"/api/v1/sla-instances/{sla_id}/actions/{action}",
        json=PAUSE if action == "pause" else {"reason": "Source feedback received"},
        headers={
            **HEADERS, **(scope or {}), "If-Match": f'W/"{version}"',
            "Idempotency-Key": key or f"sla-boundary-{action}-{uuid4()}",
        },
    )


@pytest.mark.parametrize("action", ["pause", "resume"])
@pytest.mark.parametrize("axis,header", AXES)
@pytest.mark.parametrize("metadata", [OUTSIDE, None])
def test_linked_resource_scope_denies_without_mutation(seeded, action, axis, header, metadata):
    client, store, intake_id, sla_id = seeded
    if action == "resume":
        assert command(client, sla_id, "pause").status_code == 200
    store.intakes[intake_id]["scope"][axis] = metadata
    before = copy.deepcopy((store.intakes, store.slas, store.replays))
    version = store.slas[sla_id]["version"]
    denied = command(client, sla_id, action, version, scope={header: ALLOWED})
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "SCOPE_DENIED"
    assert (store.intakes, store.slas, store.replays) == before


@pytest.mark.parametrize("action", ["pause", "resume"])
@pytest.mark.parametrize("link", ["dangling", "foreign-tenant", "standalone-missing-axis"])
def test_child_tenant_cannot_substitute_for_resource_authority(seeded, action, link):
    client, store, intake_id, sla_id = seeded
    if action == "resume":
        assert command(client, sla_id, "pause").status_code == 200
    scope = {}
    if link == "dangling":
        store.slas[sla_id]["intake_id"] = str(uuid4())
    elif link == "foreign-tenant":
        store.intakes[intake_id]["scope"]["tenant_id"] = OUTSIDE
    else:
        store.slas[sla_id].pop("intake_id")
        scope = {"x-brand-ids": ALLOWED}
    before = copy.deepcopy((store.intakes, store.slas, store.replays))
    denied = command(client, sla_id, action, store.slas[sla_id]["version"], scope=scope)
    assert denied.status_code == 403, denied.text
    assert (store.intakes, store.slas, store.replays) == before


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_replay_rechecks_current_resource_scope(seeded, action):
    client, store, intake_id, sla_id = seeded
    store.intakes[intake_id]["scope"]["brand_id"] = ALLOWED
    scope = {"x-brand-ids": ALLOWED}
    version = 23
    if action == "resume":
        assert command(client, sla_id, "pause", scope=scope).status_code == 200
        version = 24
    key = f"sla-scope-replay-{uuid4()}"
    original = command(client, sla_id, action, version, scope=scope, key=key)
    assert original.status_code == 200, original.text
    store.intakes[intake_id]["scope"]["brand_id"] = OUTSIDE
    before = copy.deepcopy((store.intakes, store.slas, store.replays))
    denied = command(client, sla_id, action, version, scope=scope, key=key)
    assert denied.status_code == 403, denied.text
    assert (store.intakes, store.slas, store.replays) == before
    store.intakes[intake_id]["scope"]["brand_id"] = ALLOWED
    replayed = command(client, sla_id, action, version, scope=scope, key=key)
    assert replayed.status_code == 200
    assert replayed.json() == original.json()
    assert replayed.headers["ETag"] == original.headers["ETag"]


def test_pause_interval_retains_disclosure_inputs_and_elapsed_time_once(seeded, monkeypatch):
    client, store, intake_id, sla_id = seeded

    class Clock(datetime):
        current = datetime(2026, 10, 9, 12, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(listings, "datetime", Clock)
    store.intakes[intake_id]["scope"].update({axis: ALLOWED for axis, _ in AXES})
    scope = {header: ALLOWED for _, header in AXES}
    intake_before = copy.deepcopy(store.intakes[intake_id])
    pause_key, resume_key = f"sla-pause-{uuid4()}", f"sla-resume-{uuid4()}"
    paused = command(client, sla_id, "pause", scope=scope, key=pause_key)
    assert paused.status_code == 200, paused.text
    interval = store.slas[sla_id]["pause_intervals"][0]
    assert interval["reason"] == PAUSE["reason"]
    assert interval["expected_resume_at"] == PAUSE["expected_resume_at"]
    assert interval["actor_subject_id"] == ACTOR
    assert interval["pause_interval_id"] == paused.json()["active_pause_interval_id"]
    assert interval["audit_event_id"] == paused.json()["audit_event_id"]
    assert interval["correlation_id"] == paused.json()["correlation_id"]
    assert interval["version_after_pause"] == 24
    assert interval["state_before_pause"] == "ON_TRACK"
    assert interval["ended_at"] is None
    Clock.current += timedelta(seconds=125)
    assert command(client, sla_id, "pause", scope=scope, key=pause_key).json() == paused.json()
    assert len(store.slas[sla_id]["pause_intervals"]) == 1
    resumed = command(client, sla_id, "resume", 24, scope=scope, key=resume_key)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["paused_duration_seconds"] == 19 + 125
    assert resumed.json()["active_pause_interval_id"] is None
    assert interval["ended_at"] == Clock.current.isoformat().replace("+00:00", "Z")
    assert interval["resume_reason"] == "Source feedback received"
    assert interval["resumed_by_subject_id"] == ACTOR
    assert interval["resume_audit_event_id"] == resumed.json()["audit_event_id"]
    assert interval["version_after_resume"] == 25
    snapshot = copy.deepcopy(store.slas[sla_id])
    Clock.current += timedelta(hours=1)
    assert command(client, sla_id, "resume", 24, scope=scope, key=resume_key).json() == resumed.json()
    assert store.slas[sla_id] == snapshot
    second_pause = command(client, sla_id, "pause", 25, scope=scope)
    assert second_pause.status_code == 200
    assert len(store.slas[sla_id]["pause_intervals"]) == 2
    assert store.slas[sla_id]["pause_intervals"][0] == snapshot["pause_intervals"][0]
    assert store.slas[sla_id]["pause_intervals"][1]["pause_interval_id"] != interval["pause_interval_id"]
    assert store.intakes[intake_id] == intake_before
    detail = client.get(f"/api/v1/intakes/{intake_id}", headers={**HEADERS, **scope})
    assert detail.status_code == 200
    assert detail.json()["sla_version"] == 26
    assert detail.json()["sla_state"] == "PAUSED"


@pytest.mark.parametrize("reason", ["   ", "\n\t ", " a "])
def test_pause_rejects_blank_or_short_trimmed_reason(seeded, reason):
    client, store, _, sla_id = seeded
    before = copy.deepcopy((store.slas, store.replays))
    response = client.post(
        f"/api/v1/sla-instances/{sla_id}/actions/pause", json={**PAUSE, "reason": reason},
        headers={**HEADERS, "If-Match": 'W/"23"', "Idempotency-Key": f"sla-empty-{uuid4()}"},
    )
    assert response.status_code == 422
    assert (store.slas, store.replays) == before
