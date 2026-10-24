"""Synthetic HTTP commands, actual SQL policy/identity/audit/commit boundaries."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from psycopg import errors
from psycopg.types.json import Jsonb

from apps.api.app.routes import listings
from modules.listing.application.intake_completion import (
    CompletionRequirement,
    IntakeWorkIncomplete,
    verify_completion,
)
from shared.api.errors import install_error_handlers
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.contract.test_assisted_listing_operations import ContractTestClient
from tests.intake_registry_fixture import (
    APPROVER,
    AT,
    MANAGER,
    OWNER,
    TENANT,
    _audit_retention_row,
    _calendar_row,
    _workflow_row,
)
from tests.integration.test_intake_registry_calendar_postgresql import (
    _bundle,
    _insert_policy,
    _seed_identities,
    _upgrade,
)

pytestmark = pytest.mark.requires_live_env
HEADERS = {
    "x-subject-id": MANAGER,
    "x-tenant-id": TENANT,
    "x-roles": "site_reviewer,expansion_user",
}


def _client(bundle):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        listings.create_assisted_intake_router(
            bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy
        ),
        prefix="/api/v1",
    )
    return ContractTestClient(app, raise_server_exceptions=False)


@pytest.fixture
def case(intake_blank_db, monkeypatch):
    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT.astimezone(tz or UTC)

    monkeypatch.setattr(listings, "datetime", Clock)
    bundle = _bundle(db, monkeypatch)
    try:
        yield db, bundle, monkeypatch
    finally:
        bundle.engine.close()


def _submit(bundle, *, key=None):
    return _client(bundle).post(
        "/api/v1/intakes/url",
        json={
            "original_url": "https://example.invalid/canonical-audit",
            "scope": {"tenant_id": TENANT},
        },
        headers={**HEADERS, "Idempotency-Key": key or "synthetic-audit-submit-" + str(uuid4())},
    )


def _snapshot(bundle):
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    return deepcopy(
        {
            name: getattr(store, name)
            for name in (
                "intakes",
                "jobs",
                "assignments",
                "slas",
                "replays",
                "workflow_audits",
                "workflow_outbox",
            )
        }
    )


def test_http_receipts_have_actual_policy_bound_canonical_rows_and_restart(case):
    db, bundle, monkeypatch = case
    key = "synthetic-canonical-submit-" + str(uuid4())
    response = _submit(bundle, key=key)
    assert response.status_code == 202, response.text
    iid = response.json()["intake_id"]
    before = _snapshot(bundle)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT audit_event_id,event_sha256,resource_id,after_value,retained_until,worm_object_uri FROM audit.audit_events WHERE tenant_id=%s",
            (TENANT,),
        ).fetchall()
    assert len(rows) == 3
    assert {str(r[0]) for r in rows} == set(before["workflow_audits"])
    for aid, sha, resource, after, deadline, worm_uri in rows:
        envelope = before["workflow_audits"][str(aid)]
        assert envelope["canonical_event_sha256"] == sha
        assert envelope["resource_id"] == str(resource) and envelope["after_value"] == after
        policy = after["audit_retention_policy"]
        assert policy["policy_version_id"] == _audit_retention_row().policy_version_id
        assert policy["approved_by"] == APPROVER and len(policy["policy_sha256"]) == 64
        assert datetime.fromisoformat(policy["retained_until"]) == deadline
        assert worm_uri is None
    restarted = _bundle(db, monkeypatch)
    try:
        assert _snapshot(restarted) == before
        detail = _client(restarted).get(f"/api/v1/intakes/{iid}", headers=HEADERS)
        assert detail.status_code == 200
        replay = _submit(restarted, key=key)
        assert replay.status_code == 200 and replay.json() == response.json()
        assert _snapshot(restarted) == before
    finally:
        restarted.engine.close()


@pytest.mark.parametrize("mode", ["missing", "bad_duration", "wrong_owner", "undeclared_input"])
def test_unadmitted_retention_cannot_leave_business_audit_or_replay_rows(case, mode):
    db, bundle, _ = case
    with db.connect() as conn:
        # Governed version rows cannot be rewritten; close the old version once.
        conn.execute(
            "UPDATE workflow.decision_policies SET effective_to=effective_from + interval '1 day' WHERE policy_kind='intake_audit_retention'"
        )
        if mode != "missing":
            row = _audit_retention_row()
            params = dict(row.parameters)
            if mode == "bad_duration":
                params["retention_days"] = 0
            row = replace(
                row,
                policy_label="synthetic-audit-v2",
                policy_version_id="synthetic-audit-v2:" + TENANT,
                policy_version="2",
                parameters=params,
                owner_role=Role.DATA_OWNER.value if mode == "wrong_owner" else row.owner_role,
                declared_inputs=("scope",) if mode == "undeclared_input" else row.declared_inputs,
            )
            _insert_policy(conn, row)
    response = _submit(bundle)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "DEPENDENCY_CONFLICT"
    assert response.json()["reason_code"] == "AUDIT_POLICY_UNAVAILABLE"
    assert response.json()["next_action"] == "CONTACT_SUPPORT"
    assert response.json()["retryable"] is False
    assert all(not value for value in _snapshot(bundle).values())
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 0


def test_revoked_policy_approver_role_denies_new_write_without_changing_existing_work(case):
    db, bundle, _ = case
    submitted = _submit(bundle)
    assert submitted.status_code == 202, submitted.text
    iid = submitted.json()["intake_id"]
    before = _snapshot(bundle)
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM identity.account_roles WHERE account_id=%s AND role=%s",
            (APPROVER, Role.RECORDS_MANAGER.value),
        )
    response = _client(bundle).put(
        f"/api/v1/intakes/{iid}/assignment",
        json={
            "owner_subject_id": OWNER,
            "owner_role": "expansion-staff",
            "due_at": "2026-10-13T02:30:00Z",
            "reason": "Synthetic new assignment",
        },
        headers={
            **HEADERS,
            "Idempotency-Key": "synthetic-revoked-audit-" + str(uuid4()),
            "If-Match": 'W/"1"',
        },
    )
    assert response.status_code == 503, response.text
    assert _snapshot(bundle) == before
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 3


def test_failure_after_canonical_insert_rolls_back_everything_and_same_key_can_retry(case):
    db, bundle, monkeypatch = case
    store = bundle.assisted_intake_store
    original = store._store.put

    def fail(collection, *args, **kwargs):
        original(collection, *args, **kwargs)
        if collection.endswith("workflow_audits"):
            raise RuntimeError("synthetic crash after physical audit and document write")

    key = "synthetic-audit-crash-" + str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(store._store, "put", fail)
        response = _submit(bundle, key=key)
    assert response.status_code == 500, response.text
    assert all(not value for value in _snapshot(bundle).values())
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 0
    retried = _submit(bundle, key=key)
    assert retried.status_code == 202, retried.text
    assert retried.headers["Idempotency-Replayed"] == "false"
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 3


def test_policy_parameters_and_approval_cannot_be_rewritten_after_audit_commit(case):
    db, bundle, _ = case
    assert _submit(bundle).status_code == 202
    with db.connect() as conn:
        for sql, args in [
            (
                "UPDATE workflow.decision_policies SET parameters=%s WHERE policy_version_id=%s",
                (Jsonb({"retention_days": 1}), _audit_retention_row().policy_version_id),
            ),
            (
                "DELETE FROM workflow.decision_policies WHERE policy_version_id=%s",
                (_audit_retention_row().policy_version_id,),
            ),
        ]:
            with pytest.raises(errors.CheckViolation):
                conn.execute(sql, args)


@pytest.mark.parametrize("tamper", ["envelope", "physical_missing"])
def test_completion_rechecks_actual_canonical_record_instead_of_envelope_flags(case, tamper):
    db, bundle, _ = case
    submitted = _submit(bundle)
    assert submitted.status_code == 202, submitted.text
    iid = submitted.json()["intake_id"]
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    # Controlled parser precondition only; no parser/UAT claim.
    store.intakes[iid]["state"] = "NEEDS_REVIEW"
    store.intakes[iid]["version"] += 1
    store.flush()
    response = _client(bundle).post(
        f"/api/v1/intakes/{iid}/corrections",
        json={
            "field_path": "floor",
            "corrected_value": 12,
            "reason": "Synthetic document verification",
            "risk_acknowledged": True,
        },
        headers={
            **HEADERS,
            "Idempotency-Key": "synthetic-proof-correction-" + str(uuid4()),
            "If-Match": f'W/"{store.intakes[iid]["version"]}"',
        },
    )
    assert response.status_code == 201, response.text
    store.refresh(TENANT)
    requirements = [
        CompletionRequirement(
            kind="correction",
            allowed_states=frozenset({"APPLIED"}),
            minimum_count=1,
            require_audit=True,
            action=None,
        )
    ]
    assert verify_completion(store, store.intakes[iid], requirements)
    aid = response.json()["audit_event_id"]
    if tamper == "envelope":
        store.workflow_audits[aid]["canonical_event_sha256"] = "0" * 64
    else:
        with db.connect() as conn:
            conn.execute("DELETE FROM audit.audit_events WHERE audit_event_id=%s", (aid,))
    with pytest.raises(IntakeWorkIncomplete, match="committed canonical audit"):
        verify_completion(store, store.intakes[iid], requirements)
