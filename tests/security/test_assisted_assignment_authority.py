"""Assignment write authority using real HTTP and explicit identity fixtures.

Not an Operator directory, SLA provisioning or browser acceptance proof.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.listings import AssistedIntakeStore
from apps.api.oday_api.main import create_app
from shared.auth import Role, Scope
from shared.identity.store import Account

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000101"
TARGET = "00000000-0000-0000-0000-000000000102"
ALLOWED = "00000000-0000-0000-0000-000000000201"
OUTSIDE = "00000000-0000-0000-0000-000000000202"
HEADERS = {"x-subject-id": ACTOR, "x-tenant-id": TENANT, "x-roles": "site_reviewer"}
AXES = [
    ("brand_id", "brand_ids", "x-brand-ids"),
    ("region_id", "region_ids", "x-region-ids"),
    ("store_id", "store_ids", "x-store-ids"),
    ("assigned_area_id", "assigned_area_ids", "x-assigned-area-ids"),
    ("heat_zone_id", "heat_zone_ids", "x-heat-zone-ids"),
]


@pytest.fixture
def resources():
    app = create_app()
    identities = app.state.persistence_bundle.identity_store
    for subject in (ACTOR, TARGET):
        identities.save_account(Account(UUID(subject), UUID(TENANT), subject, f"{subject}@example.invalid"))
        identities.set_account_roles(subject, [Role.SITE_REVIEWER])
        identities.set_account_scope(subject, Scope(tenant_id=TENANT))
    client = TestClient(app)
    submitted = client.post(
        "/api/v1/intakes/url",
        json={"original_url": f"https://example.com/assignment-{uuid4()}", "scope": {"tenant_id": TENANT}},
        headers={**HEADERS, "Idempotency-Key": f"assignment-submit-{uuid4()}"},
    )
    assert submitted.status_code == 202, submitted.text
    intake_id = submitted.json()["intake_id"]
    store = next(s for s in reversed(AssistedIntakeStore._instances) if intake_id in s.intakes)
    return client, store, identities, intake_id


def assign(resources, *, key=None, role="reviewer", version=1, subject=TARGET):
    client, _, _, intake_id = resources
    return client.put(
        f"/api/v1/intakes/{intake_id}/assignment",
        json={"owner_subject_id": subject, "owner_role": role,
              "due_at": "2027-01-10T12:00:00Z", "reason": "Route source evidence review"},
        headers={**HEADERS, "Idempotency-Key": key or f"assignment-create-{uuid4()}",
                 "If-Match": f'W/"{version}"'},
    )


def command(resources, assignment_id, action, *, version=2, key=None, scope=None, role="reviewer"):
    body = {"reason": "Continue evidence review"}
    if action == "transfer":
        body.update(target_owner_subject_id=TARGET, target_owner_role=role,
                    handoff_note="Review the original source evidence")
    return resources[0].post(
        f"/api/v1/assignments/{assignment_id}/actions/{action}", json=body,
        headers={**HEADERS, **(scope or {}), "If-Match": f'W/"{version}"',
                 "Idempotency-Key": key or f"assignment-{action}-{uuid4()}"},
    )


def snapshot(store):
    return copy.deepcopy((store.intakes, store.assignments, store.replays))


@pytest.mark.parametrize("operation", ["assign", "transfer"])
@pytest.mark.parametrize("defect", ["missing", "disabled", "locked", "invited", "foreign-tenant",
                                    "foreign-scope-tenant", "no-business-role", "role-mismatch",
                                    "unknown-role", "missing-store"])
def test_target_requires_fresh_identity_authority(resources, operation, defect):
    client, store, identities, intake_id = resources
    # Transfer setup is a genuine assignment endpoint, not a seeded child row.
    created = assign(resources) if operation == "transfer" else None
    if created is not None:
        assert created.status_code == 200, created.text
    role = "reviewer"
    account = identities.find_account_by_id(TARGET)
    if defect == "missing":
        identities._accounts.pop(UUID(TARGET))
    elif defect in {"disabled", "locked", "invited"}:
        identities.save_account(replace(account, status=defect))
    elif defect == "foreign-tenant":
        identities.save_account(replace(account, tenant_id=UUID(OUTSIDE)))
    elif defect == "foreign-scope-tenant":
        identities.set_account_scope(TARGET, Scope(tenant_id=OUTSIDE))
    elif defect == "no-business-role":
        identities.set_account_roles(TARGET, [Role.PLATFORM_ADMIN, Role.AUDITOR])
    elif defect == "role-mismatch":
        identities.set_account_roles(TARGET, [Role.EXPANSION_USER])
    elif defect == "unknown-role":
        role = "gov-queue"
    else:
        client.app.state.persistence_bundle = replace(client.app.state.persistence_bundle, identity_store=None)
    before = snapshot(store)
    result = assign(resources, role=role) if operation == "assign" else command(
        resources, created.json()["assignment_id"], "transfer", role=role,
    )
    assert result.status_code == 403, result.text
    assert result.json()["code"] == "ASSIGNMENT_SCOPE_DENIED"
    assert snapshot(store) == before


@pytest.mark.parametrize("operation", ["assign", "transfer"])
@pytest.mark.parametrize("axis,scope_axis,header", AXES)
@pytest.mark.parametrize("metadata", [OUTSIDE, None])
def test_target_scope_checks_all_resource_axes(resources, operation, axis, scope_axis, header, metadata):
    _, store, identities, intake_id = resources
    created = assign(resources) if operation == "transfer" else None
    if created is not None:
        assert created.status_code == 200
    store.intakes[intake_id]["scope"][axis] = metadata
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, **{scope_axis: frozenset({ALLOWED})}))
    before = snapshot(store)
    result = assign(resources) if operation == "assign" else command(
        resources, created.json()["assignment_id"], "transfer",
    )
    assert result.status_code == 403, result.text
    assert result.json()["code"] == "ASSIGNMENT_SCOPE_DENIED"
    assert snapshot(store) == before


@pytest.mark.parametrize("action", ["claim", "transfer", "complete"])
@pytest.mark.parametrize("link", ["dangling", "foreign-tenant", "standalone-missing-axis"])
def test_assignment_child_tenant_does_not_authorize_invalid_link(resources, action, link):
    _, store, _, intake_id = resources
    created = assign(resources)
    assert created.status_code == 200
    assignment_id = created.json()["assignment_id"]
    if action == "complete":
        assert command(resources, assignment_id, "claim").status_code == 200
    scope = {}
    if link == "dangling":
        store.assignments[assignment_id]["intake_id"] = str(uuid4())
    elif link == "foreign-tenant":
        store.intakes[intake_id]["scope"]["tenant_id"] = OUTSIDE
    else:
        store.assignments[assignment_id].pop("intake_id")
        scope = {"x-brand-ids": ALLOWED}
    before = snapshot(store)
    result = command(resources, assignment_id, action,
                     version=store.assignments[assignment_id]["version"], scope=scope)
    assert result.status_code == 403, result.text
    assert snapshot(store) == before


@pytest.mark.parametrize("action", ["claim", "transfer", "complete"])
@pytest.mark.parametrize("axis,scope_axis,header", AXES)
@pytest.mark.parametrize("metadata", [OUTSIDE, None])
def test_actor_scope_checks_all_assignment_resource_axes(resources, action, axis, scope_axis, header, metadata):
    _, store, _, intake_id = resources
    created = assign(resources)
    assert created.status_code == 200
    assignment_id = created.json()["assignment_id"]
    if action == "complete":
        assert command(resources, assignment_id, "claim").status_code == 200
    store.intakes[intake_id]["scope"][axis] = metadata
    before = snapshot(store)
    result = command(resources, assignment_id, action,
                     version=store.assignments[assignment_id]["version"], scope={header: ALLOWED})
    assert result.status_code == 403, result.text
    assert result.json()["code"] == "SCOPE_DENIED"
    assert snapshot(store) == before


@pytest.mark.parametrize("operation", ["assign", "transfer"])
@pytest.mark.parametrize("revocation", ["account", "role", "scope"])
def test_replay_rechecks_target_authority(resources, operation, revocation):
    _, store, identities, intake_id = resources
    store.intakes[intake_id]["scope"]["brand_id"] = ALLOWED
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, brand_ids=frozenset({ALLOWED})))
    key = f"assignment-target-replay-{uuid4()}"
    created = assign(resources, key=key)
    assert created.status_code == 200, created.text
    assignment_id = created.json()["assignment_id"]
    original = created if operation == "assign" else command(resources, assignment_id, "transfer", key=key)
    assert original.status_code == 200, original.text
    account = identities.find_account_by_id(TARGET)
    if revocation == "account":
        identities.save_account(replace(account, status="disabled"))
    elif revocation == "role":
        identities.set_account_roles(TARGET, [Role.AUDITOR])
    else:
        identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, brand_ids=frozenset({OUTSIDE})))
    before = snapshot(store)
    denied = assign(resources, key=key) if operation == "assign" else command(resources, assignment_id, "transfer", key=key)
    assert denied.status_code == 403, denied.text
    assert snapshot(store) == before
    identities.save_account(account)
    identities.set_account_roles(TARGET, [Role.SITE_REVIEWER])
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, brand_ids=frozenset({ALLOWED})))
    replayed = assign(resources, key=key) if operation == "assign" else command(resources, assignment_id, "transfer", key=key)
    assert replayed.status_code == 200
    assert replayed.json() == original.json()
    assert replayed.headers["ETag"] == original.headers["ETag"]
    assert snapshot(store) == before


@pytest.mark.parametrize("action", ["claim", "transfer", "complete"])
def test_assignment_replay_rechecks_actor_resource_scope(resources, action):
    _, store, _, intake_id = resources
    store.intakes[intake_id]["scope"]["brand_id"] = ALLOWED
    created = assign(resources)
    assignment_id = created.json()["assignment_id"]
    version = 2
    if action == "complete":
        assert command(resources, assignment_id, "claim").status_code == 200
        version = 3
    key = f"assignment-scope-replay-{uuid4()}"
    original = command(resources, assignment_id, action, version=version, key=key, scope={"x-brand-ids": ALLOWED})
    assert original.status_code == 200, original.text
    store.intakes[intake_id]["scope"]["brand_id"] = OUTSIDE
    before = snapshot(store)
    denied = command(resources, assignment_id, action, version=version, key=key, scope={"x-brand-ids": ALLOWED})
    assert denied.status_code == 403, denied.text
    assert snapshot(store) == before


def test_authorized_target_with_all_scope_axes_and_independent_token(resources):
    client, store, identities, intake_id = resources
    store.intakes[intake_id]["scope"].update({axis: ALLOWED for axis, _, _ in AXES})
    identities.set_account_scope(TARGET, Scope(tenant_id=TENANT, **{
        scope_axis: frozenset({ALLOWED}) for _, scope_axis, _ in AXES
    }))
    assigned = assign(resources, subject=ACTOR)
    assert assigned.status_code == 200, assigned.text
    assignment_id = assigned.json()["assignment_id"]
    claimed = command(resources, assignment_id, "claim")
    assert claimed.status_code == 200
    before = snapshot(store)
    stale = command(resources, assignment_id, "transfer", version=2)
    assert stale.status_code == 409
    assert snapshot(store) == before
    transferred = command(resources, assignment_id, "transfer", version=3)
    assert transferred.status_code == 200, transferred.text
    detail = client.get(f"/api/v1/intakes/{intake_id}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json()["assigned_to"] == TARGET
    assert detail.json()["assignment_status"] == "TRANSFERRED"
    assert detail.json()["assignment_version"] == 4
    assert detail.json()["version"] == 2


@pytest.mark.parametrize("field", ["reason", "handoff_note"])
@pytest.mark.parametrize("value", ["   ", "\n\t ", " a "])
def test_transfer_requires_nonblank_reason_and_handoff(resources, field, value):
    _, store, _, _ = resources
    created = assign(resources)
    body = {"target_owner_subject_id": TARGET, "target_owner_role": "reviewer",
            "reason": "Route source review", "handoff_note": "Check source evidence", field: value}
    before = snapshot(store)
    result = resources[0].post(
        f"/api/v1/assignments/{created.json()['assignment_id']}/actions/transfer", json=body,
        headers={**HEADERS, "If-Match": created.headers["ETag"], "Idempotency-Key": f"blank-transfer-{uuid4()}"},
    )
    assert result.status_code == 422, result.text
    assert snapshot(store) == before


@pytest.mark.parametrize("role,grant", [
    ("reviewer", Role.SITE_REVIEWER), ("expansion-manager", Role.SITE_REVIEWER),
    ("expansionManager", Role.EXECUTIVE), ("data-steward", Role.DATA_OWNER),
    ("steward", Role.DATA_OWNER), ("expansion-staff", Role.EXPANSION_USER),
    ("expansionStaff", Role.EXPANSION_USER),
])
def test_target_role_alias_is_backed_by_canonical_identity_grant(resources, role, grant):
    resources[2].set_account_roles(TARGET, [grant])
    result = assign(resources, role=role)
    assert result.status_code == 200, result.text
