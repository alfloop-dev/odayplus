from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import listings
from modules.listing.application import intake_assignment_commands
from shared.auth import Role, Scope
from shared.domain.events import validate_event
from shared.identity.store import Account
from tests.intake_registry_fixture import AT, MANAGER, OWNER, TENANT, _provider

TARGET = "00000000-0000-0000-0000-000000000104"


def _headers(actor=MANAGER):
    return {
        "x-subject-id": actor,
        "x-tenant-id": TENANT,
        "x-roles": "site_reviewer,expansion_user" if actor == MANAGER else "expansion_user",
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
    identities.save_account(
        Account(
            account_id=UUID(TARGET),
            tenant_id=UUID(TENANT),
            username=TARGET,
            email="target@example.invalid",
        )
    )
    identities.set_account_roles(TARGET, {Role.EXPANSION_USER})
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT))
    store = listings.AssistedIntakeStore()
    app = FastAPI()
    app.include_router(
        listings.create_assisted_intake_router(store, workflow_policy=provider), prefix="/api/v1"
    )
    client = TestClient(app)
    r = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.invalid/assignment", "scope": {"tenant_id": TENANT}},
        headers={**_headers(), "Idempotency-Key": "synthetic-journey-" + str(uuid4())},
    )
    assert r.status_code == 202, r.text
    iid = r.json()["intake_id"]
    r = client.put(
        f"/api/v1/intakes/{iid}/assignment",
        json={
            "owner_subject_id": OWNER,
            "owner_role": "expansion-staff",
            "due_at": "2026-10-13T02:30:00Z",
            "reason": "Synthetic initial routing",
        },
        headers={
            **_headers(),
            "Idempotency-Key": "synthetic-route-" + str(uuid4()),
            "If-Match": 'W/"1"',
        },
    )
    assert r.status_code == 200, r.text
    aid = r.json()["assignment_id"]
    return client, store, iid, aid, identities, Clock


def _action(journey, action, *, actor=OWNER, key=None, version=None, body=None):
    client, store, _, aid, _, _ = journey
    return client.post(
        f"/api/v1/assignments/{aid}/actions/{action}",
        json=body or {"reason": "Synthetic work command"},
        headers={
            **_headers(actor),
            "Idempotency-Key": key or "synthetic-action-" + str(uuid4()),
            "If-Match": f'W/"{version or store.assignments[aid]["version"]}"',
        },
    )


def _cancel(journey):
    client, store, iid, _, _, _ = journey
    r = client.post(
        f"/api/v1/intakes/{iid}/actions/cancel",
        json={"reason": "Synthetic task ends by cancellation"},
        headers={
            **_headers(),
            "Idempotency-Key": "synthetic-cancel-" + str(uuid4()),
            "If-Match": f'W/"{store.intakes[iid]["version"]}"',
        },
    )
    assert r.status_code == 200, r.text


def _targets(journey, actor=OWNER):
    client, _, _, aid, _, _ = journey
    return client.get(f"/api/v1/assignments/{aid}/transfer-targets", headers=_headers(actor))


def test_transfer_directory_returns_live_authorized_uuid_targets_without_mutations(journey):
    _, store, _, aid, identities, _ = journey
    account = identities.find_account_by_id(TARGET)
    identities.save_account(replace(account, display_name="Synthetic recipient"))
    before = _snapshot(store)
    for actor in (OWNER, MANAGER):
        response = _targets(journey, actor)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "assignment_id": aid,
            "assignment_version": store.assignments[aid]["version"],
            "items": [{"subject_id": TARGET, "display_name": "Synthetic recipient",
                       "owner_role": "expansion-staff"}],
        }
        assert account.email not in response.text
    assert _snapshot(store) == before


@pytest.mark.parametrize("mode", ["disabled", "cross_tenant", "role_revoked", "scope_missing"])
def test_transfer_directory_omits_revoked_or_foreign_recipients(journey, mode):
    _, _, _, _, identities, _ = journey
    assert len(_targets(journey).json()["items"]) == 1
    account = identities.find_account_by_id(TARGET)
    if mode == "disabled":
        identities.save_account(replace(account, status="disabled"))
    elif mode == "cross_tenant":
        identities.save_account(replace(account, tenant_id=uuid4()))
    elif mode == "role_revoked":
        identities.set_account_roles(TARGET, {Role.SITE_REVIEWER})
    else:
        identities._scopes.pop(UUID(TARGET))
    response = _targets(journey)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.parametrize("axis", [
    "brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules",
])
def test_transfer_directory_enforces_every_recipient_scope_axis(journey, axis):
    _, _, _, _, identities, _ = journey
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, **{axis: frozenset({"other"})}))
    response = _targets(journey)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.parametrize("mode", ["nonowner", "disabled_actor", "foreign_actor", "narrow_actor", "owner_role_revoked"])
def test_transfer_directory_denies_unauthorized_actor_or_revoked_owner(journey, mode):
    _, store, _, _, identities, _ = journey
    actor = TARGET if mode == "nonowner" else OWNER
    if mode in {"disabled_actor", "foreign_actor"}:
        account = identities.find_account_by_id(actor)
        identities.save_account(replace(account, **(
            {"status": "disabled"} if mode == "disabled_actor" else {"tenant_id": uuid4()}
        )))
    elif mode == "narrow_actor":
        identities.set_account_scope(actor, Scope(tenant_id=TENANT, brand_ids=frozenset({"other"})))
    elif mode == "owner_role_revoked":
        identities.set_account_roles(OWNER, {Role.DATA_OWNER})
    before = _snapshot(store)
    response = _targets(journey, actor)
    assert response.status_code == 403, response.text
    assert TARGET not in response.text
    assert _snapshot(store) == before


def test_transfer_directory_requires_registry_and_current_policy_approval(journey):
    _, store, _, aid, identities, _ = journey
    app = FastAPI()
    app.include_router(listings.create_assisted_intake_router(store), prefix="/api/v1")
    response = TestClient(app).get(
        f"/api/v1/assignments/{aid}/transfer-targets", headers=_headers(OWNER),
    )
    assert response.status_code == 503
    from tests.intake_registry_fixture import APPROVER
    identities.save_account(replace(identities.find_account_by_id(APPROVER), status="disabled"))
    assert _targets(journey).status_code == 503


def test_transfer_directory_does_not_offer_targets_for_illegal_transition(journey):
    response = _action(journey, "transfer", body={
        "target_owner_subject_id": TARGET, "target_owner_role": "expansion-staff",
        "reason": "Synthetic transfer", "handoff_note": "Synthetic verified handoff",
    })
    assert response.status_code == 200, response.text
    response = _targets(journey, TARGET)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.parametrize("state", ["UNASSIGNED", "COMPLETED", "TRANSFERRED", "ESCALATED"])
def test_transfer_directory_follows_all_forbidden_transfer_states(journey, state):
    _, store, _, aid, _, _ = journey
    store.assignments[aid]["status"] = state
    response = _targets(journey)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


@pytest.mark.parametrize("revocation", ["role", "scope"])
def test_transfer_write_revalidates_recipient_after_directory_read(journey, revocation):
    _, store, _, _, identities, _ = journey
    assert _targets(journey).json()["items"][0]["subject_id"] == TARGET
    if revocation == "role":
        identities.set_account_roles(TARGET, {Role.SITE_REVIEWER})
    else:
        identities._scopes.pop(UUID(TARGET))
    before = _snapshot(store)
    response = _action(journey, "transfer", body={
        "target_owner_subject_id": TARGET, "target_owner_role": "expansion-staff",
        "reason": "Synthetic transfer", "handoff_note": "Synthetic verified handoff",
    })
    assert response.status_code == 403, response.text
    assert _snapshot(store) == before


def _snapshot(store):
    return deepcopy(
        tuple(
            getattr(store, n)
            for n in (
                "intakes",
                "assignments",
                "slas",
                "workflow_audits",
                "workflow_outbox",
                "replays",
            )
        )
    )


def _initial_request(journey, *, actor=MANAGER):
    client, store, _, _, _, _ = journey
    response = client.post(
        "/api/v1/intakes/url",
        json={"original_url": "https://example.invalid/initial-assignment",
              "scope": {"tenant_id": TENANT}},
        headers={**_headers(actor), "Idempotency-Key": "synthetic-initial-" + str(uuid4())},
    )
    assert response.status_code == 202, response.text
    intake_id = response.json()["intake_id"]
    body = {"owner_subject_id": TARGET, "owner_role": "expansion-staff",
            "due_at": "2026-10-13T02:30:00Z", "reason": "Synthetic initial routing"}
    headers = {**_headers(actor), "Idempotency-Key": "synthetic-assignment-" + str(uuid4()),
               "If-Match": f'W/"{store.intakes[intake_id]["version"]}"'}
    return intake_id, body, headers


def test_initial_assignment_records_the_domain_transition_with_original_identity(journey):
    _, store, intake_id, assignment_id, _, _ = journey
    assignment = store.assignments[assignment_id]
    history = assignment.get("transition_history", [])
    assert len(history) == 1
    assert {key: history[0][key] for key in
            ("from_status", "to_status", "version_after", "actor_subject_id", "reason")} == {
        "from_status": "UNASSIGNED", "to_status": "ASSIGNED", "version_after": 2,
        "actor_subject_id": MANAGER, "reason": "Synthetic initial routing",
    }
    assert store.intakes[intake_id]["assignment_id"] == assignment_id
    assert assignment["version"] == 2
    assert store.workflow_audits[assignment["audit_event_id"]]["action"] == "assignment.assign"


def test_staff_cannot_directly_assign_its_own_unassigned_intake(journey):
    client, store, _, _, _, _ = journey
    intake_id, body, headers = _initial_request(journey, actor=TARGET)
    before = _snapshot(store)
    response = client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body, headers=headers)
    assert response.status_code == 403, response.text
    assert _snapshot(store) == before


@pytest.mark.parametrize("revocation", ["target_disabled", "target_role", "actor_disabled", "policy_approval"])
def test_initial_assignment_replay_rechecks_current_actor_target_and_policy(journey, revocation):
    from tests.intake_registry_fixture import APPROVER

    client, store, _, _, identities, _ = journey
    intake_id, body, headers = _initial_request(journey)
    path = f"/api/v1/intakes/{intake_id}/assignment"
    first = client.put(path, json=body, headers=headers)
    assert first.status_code == 200, first.text
    before = _snapshot(store)
    replay = client.put(path, json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json(), replay.text
    assert replay.headers["etag"] == first.headers["etag"]
    assert _snapshot(store) == before
    if revocation == "target_role":
        identities.set_account_roles(TARGET, {Role.SITE_REVIEWER})
    else:
        actor = {"target_disabled": TARGET, "actor_disabled": MANAGER,
                 "policy_approval": APPROVER}[revocation]
        identities.save_account(replace(identities.find_account_by_id(actor), status="disabled"))
    rejected = client.put(path, json=body, headers=headers)
    assert rejected.status_code == (503 if revocation == "policy_approval" else 403), rejected.text
    assert _snapshot(store) == before


def test_initial_assignment_audit_failure_rolls_back_all_workflow_and_replay(journey, monkeypatch):
    client, store, _, _, _, _ = journey
    intake_id, body, headers = _initial_request(journey)
    before = _snapshot(store)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Synthetic initial assignment audit unavailable")

    monkeypatch.setattr(intake_assignment_commands, "record_workflow_change", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic initial assignment audit unavailable"):
        client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body, headers=headers)
    assert _snapshot(store) == before


def test_claim_transfer_accept_complete_and_sla_are_audited_atomic_and_replayable(journey):
    client, store, iid, aid, _, _ = journey
    r = _action(journey, "claim", key="synthetic-original-claim")
    assert r.status_code == 200, r.text
    claim = r.json()
    assert claim["audit_event_id"] in store.workflow_audits
    version = claim["version"]
    deadline = claim["due_at"]
    transferred = _action(
        journey,
        "transfer",
        body={
            "target_owner_subject_id": TARGET,
            "target_owner_role": "expansion-staff",
            "reason": "Synthetic owner transfer",
            "handoff_note": "Synthetic verified handoff",
        },
    )
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["due_at"] == deadline
    assert store.assignments[aid]["handoff_note"] == "Synthetic verified handoff"
    assert (
        _action(journey, "claim", key="synthetic-original-claim", version=version - 1).json()
        == claim
    )
    accepted = _action(journey, "claim", actor=TARGET)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["version"] == transferred.json()["version"] + 2
    assert store.assignments[aid]["claimed_by"] == TARGET
    assert store.assignments[aid]["claimed_at"] == AT.isoformat().replace("+00:00", "Z")
    snapshot = _snapshot(store)
    incomplete = _action(journey, "complete", actor=TARGET)
    assert incomplete.status_code == 409 and "WORK_INCOMPLETE" in incomplete.text
    assert _snapshot(store) == snapshot
    _cancel(journey)
    old_version = store.assignments[aid]["version"]
    r = _action(journey, "complete", actor=TARGET, key="synthetic-final-completion")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED"
    detail = client.get(f"/api/v1/intakes/{iid}", headers=_headers(TARGET))
    assert detail.status_code == 200, detail.text
    assert (
        detail.json()["assignment_id"] == aid and detail.json()["assignment_status"] == "COMPLETED"
    )
    assert detail.json()["sla_state"] == "COMPLETED"
    sla = next(iter(store.slas.values()))
    assert sla["completion_evidence"] == store.assignments[aid]["completion_evidence"]
    assert sla["audit_event_id"] in store.workflow_audits
    for event in store.workflow_outbox.values():
        assert not validate_event(event), validate_event(event)
    assert Counter(
        a["action"] for a in store.workflow_audits.values() if a["resource_id"] == aid
    ) == {
        "assignment.create": 1,
        "assignment.assign": 1,
        "assignment.claim": 2,
        "assignment.accept": 1,
        "assignment.transfer": 1,
        "assignment.complete": 1,
    }
    snapshot = _snapshot(store)
    assert (
        _action(
            journey, "complete", actor=TARGET, key="synthetic-final-completion", version=old_version
        ).json()
        == r.json()
    )
    assert _snapshot(store) == snapshot


def test_completed_assignment_rejects_reassignment_as_state_conflict_without_mutations(journey):
    client, store, iid, aid, _, _ = journey
    assert _action(journey, "claim").status_code == 200
    _cancel(journey)
    completed = _action(journey, "complete")
    assert completed.status_code == 200, completed.text
    assert store.assignments[aid]["status"] == "COMPLETED"
    before = _snapshot(store)

    response = client.put(
        f"/api/v1/intakes/{iid}/assignment",
        json={
            "owner_subject_id": OWNER,
            "owner_role": "expansion-staff",
            "due_at": "2026-10-13T02:30:00Z",
            "reason": "Synthetic attempt to reassign completed work",
        },
        headers={
            **_headers(),
            "Idempotency-Key": "synthetic-completed-reassignment",
            "If-Match": f'W/"{store.intakes[iid]["version"]}"',
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "WORKFLOW_STATE_DENIED"
    assert _snapshot(store) == before


@pytest.mark.parametrize(
    "mode",
    [
        "manager_nonowner_claim",
        "disabled",
        "revoked",
        "target_scope",
        "target_disabled",
        "target_role",
    ],
)
def test_current_owner_actor_and_target_grants_are_required(journey, mode):
    _, store, _, _, identities, _ = journey
    actor = MANAGER if mode == "manager_nonowner_claim" else OWNER
    action = "claim"
    body = None
    if mode == "disabled":
        identities.save_account(replace(identities.find_account_by_id(OWNER), status="disabled"))
    if mode == "revoked":
        identities.set_account_roles(OWNER, {Role.AUDITOR})
    if mode.startswith("target"):
        action = "transfer"
        body = {
            "target_owner_subject_id": TARGET,
            "target_owner_role": "expansion-staff",
            "reason": "Synthetic transfer",
            "handoff_note": "Synthetic handoff",
        }
        if mode == "target_scope":
            identities.set_account_scope(
                TARGET, Scope(tenant_id=TENANT, brand_ids=frozenset({"other-brand"}))
            )
        if mode == "target_disabled":
            identities.save_account(
                replace(identities.find_account_by_id(TARGET), status="disabled")
            )
        if mode == "target_role":
            identities.set_account_roles(TARGET, {Role.AUDITOR})
    before = _snapshot(store)
    r = _action(journey, action, actor=actor, body=body)
    assert r.status_code == 403, r.text
    assert _snapshot(store) == before


def test_replay_rechecks_live_registry_grant(journey):
    _, store, _, aid, identities, _ = journey
    version = store.assignments[aid]["version"]
    assert _action(journey, "claim", key="synthetic-revoke-replay").status_code == 200
    identities.set_account_roles(OWNER, {Role.AUDITOR})
    before = _snapshot(store)
    assert (
        _action(journey, "claim", key="synthetic-revoke-replay", version=version).status_code == 403
    )
    assert _snapshot(store) == before


@pytest.mark.parametrize("action", ["claim", "transfer", "complete"])
def test_failure_after_audit_rolls_back_everything_and_same_key_can_retry(
    journey, monkeypatch, action
):
    _, store, _, _, _, _ = journey
    body = None
    if action == "complete":
        assert _action(journey, "claim").status_code == 200
        _cancel(journey)
    if action == "transfer":
        body = {
            "target_owner_subject_id": TARGET,
            "target_owner_role": "expansion-staff",
            "reason": "Synthetic transfer",
            "handoff_note": "Synthetic handoff",
        }
    original = intake_assignment_commands.enqueue_workflow_event

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic event persistence failure")

    monkeypatch.setattr(intake_assignment_commands, "enqueue_workflow_event", fail)
    before = _snapshot(store)
    with pytest.raises(RuntimeError, match="synthetic event persistence failure"):
        _action(journey, action, key="synthetic-retry-" + action, body=body)
    assert _snapshot(store) == before
    monkeypatch.setattr(intake_assignment_commands, "enqueue_workflow_event", original)
    assert _action(journey, action, key="synthetic-retry-" + action, body=body).status_code == 200


def test_paused_sla_closes_real_interval_when_work_completes(journey):
    client, store, iid, _, _, clock = journey
    assert _action(journey, "claim").status_code == 200
    sla = next(iter(store.slas.values()))
    sid = sla["sla_instance_id"]
    clock.instant = AT + timedelta(minutes=5)
    r = client.post(
        f"/api/v1/sla-instances/{sid}/actions/pause",
        json={"reason": "Synthetic approved hold", "expected_resume_at": "2026-10-13T03:00:00Z"},
        headers={
            **_headers(),
            "Idempotency-Key": "synthetic-completion-pause",
            "If-Match": f'W/"{sla["version"]}"',
        },
    )
    assert r.status_code == 200, r.text
    _cancel(journey)
    clock.instant = AT + timedelta(minutes=15)
    r = _action(journey, "complete")
    assert r.status_code == 200, r.text
    completed = store.slas[sid]
    assert completed["state"] == "COMPLETED" and completed["active_pause_interval_id"] is None
    assert completed["paused_duration_seconds"] == 600
    assert completed["pause_intervals"][0]["resumed_at"] == clock.instant.isoformat().replace(
        "+00:00", "Z"
    )
    assert completed["due_at"] == "2026-10-13T02:40:00Z"
    assert store.intakes[iid]["due_at"] == completed["due_at"]


def test_concurrent_distinct_claims_cannot_overwrite_same_version(journey):
    _, store, _, aid, _, _ = journey
    version = store.assignments[aid]["version"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda key: _action(journey, "claim", key=key, version=version),
                ["synthetic-concurrent-claim-a", "synthetic-concurrent-claim-b"],
            )
        )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert store.assignments[aid]["version"] == version + 1
    assert (
        sum(
            a["action"] == "assignment.claim" and a["resource_id"] == aid
            for a in store.workflow_audits.values()
        )
        == 1
    )
