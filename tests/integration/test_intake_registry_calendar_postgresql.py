"""Execute the canonical migrations and read actual SQL identities/policies."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg.types.json import Jsonb

from apps.api.app.routes import listings
from modules.listing.application.intake_registry_calendar import RegistryIntakeWorkflowPolicy
from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.factory import build_persistence
from tests.intake_registry_fixture import (
    APPROVER,
    AT,
    MANAGER,
    OWNER,
    TENANT,
    _calendar_row,
    _workflow_row,
)

pytestmark = pytest.mark.requires_live_env


def _upgrade(database, revision):
    config = Config("infra/db/migrations/alembic.ini")
    config.set_main_option("sqlalchemy.url", database.url(driver="psycopg").replace("%", "%%"))
    command.upgrade(config, revision)


def _insert_policy(conn, row):
    conn.execute(
        """
        INSERT INTO workflow.decision_policies (
            policy_version_id,policy_label,policy_id,policy_version,policy_kind,
            tenant_id,effective_from,owner_role,approved_by,approved_at,
            input_contract,output_contract,change_reason,parameters,declared_inputs
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            row.policy_version_id,
            row.policy_label,
            row.policy_id,
            row.policy_version,
            row.policy_kind,
            row.tenant_id,
            row.effective_from,
            row.owner_role,
            row.approved_by,
            row.effective_from,
            "synthetic-input",
            "synthetic-output",
            "Isolated synthetic acceptance fixture",
            Jsonb(dict(row.parameters)),
            list(row.declared_inputs),
        ),
    )


def _seed_identities(conn):
    conn.execute(
        "INSERT INTO core.tenants (tenant_id,tenant_name) VALUES (%s,%s)",
        (TENANT, "Synthetic calendar acceptance"),
    )
    for account_id, role in (
        (APPROVER, Role.DATA_OWNER),
        (MANAGER, Role.SITE_REVIEWER),
        (OWNER, Role.EXPANSION_USER),
    ):
        conn.execute(
            """INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by)
                        VALUES (%s,%s,%s,%s,'active','isolated-synthetic-test')""",
            (account_id, TENANT, account_id, account_id + "@example.invalid"),
        )
        conn.execute(
            "INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
            (account_id, role.value),
        )
        conn.execute("INSERT INTO identity.account_scopes(account_id) VALUES (%s)", (account_id,))


def _bundle(database, monkeypatch):
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODAY_DATABASE_URL", database.url())
    return build_persistence(mode="postgresql")


def test_expand_migration_preserves_registry_rows_and_rejects_unknown_kind(intake_blank_db):
    db = intake_blank_db
    _upgrade(db, "0022")
    with db.connect() as conn:
        _seed_identities(conn)
        before = conn.execute(
            "SELECT row_to_json(p) FROM workflow.decision_policies p ORDER BY policy_version_id"
        ).fetchall()
        with pytest.raises(errors.CheckViolation):
            _insert_policy(conn, _workflow_row())
    _upgrade(db, "head")
    with db.connect() as conn:
        assert (
            conn.execute("SELECT version_num FROM public.oday_plus_alembic_version").fetchone()[0]
            == "0023"
        )
        assert (
            conn.execute(
                "SELECT row_to_json(p) FROM workflow.decision_policies p ORDER BY policy_version_id"
            ).fetchall()
            == before
        )
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())
        from dataclasses import replace

        for kind in (
            "forecast_alert",
            "heatzone_merge",
            "heatzone_absorption",
            "sitescore_recommendation",
            "price_exploration",
            "netplan_action",
            "model_performance_drift",
        ):
            row = _workflow_row()
            label = "synthetic-" + kind
            _insert_policy(
                conn,
                replace(
                    row,
                    policy_kind=kind,
                    policy_id=label,
                    policy_label=label,
                    policy_version_id=label + ":" + TENANT,
                ),
            )
        with pytest.raises(errors.CheckViolation) as failure:
            row = _workflow_row()
            _insert_policy(
                conn,
                replace(
                    row,
                    policy_kind="unreviewed_kind",
                    policy_label="synthetic-invalid",
                    policy_id="synthetic-invalid",
                    policy_version_id="synthetic-invalid:" + TENANT,
                ),
            )
        assert failure.value.diag.constraint_name == "chk_decision_policy_kind"
        for sql, values in (
            (
                "UPDATE workflow.decision_policies SET parameters=%s WHERE policy_version_id=%s",
                (Jsonb({"replacement": True}), _workflow_row().policy_version_id),
            ),
            (
                "DELETE FROM workflow.decision_policies WHERE policy_version_id=%s",
                (_calendar_row().policy_version_id,),
            ),
        ):
            with pytest.raises(errors.CheckViolation) as failure:
                conn.execute(sql, values)
            assert failure.value.diag.constraint_name == "chk_intake_policy_version_immutable"
        conn.execute(
            "UPDATE workflow.decision_policies SET effective_to=%s WHERE policy_version_id=%s",
            (datetime(2026, 12, 31, tzinfo=UTC), _workflow_row().policy_version_id),
        )
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "UPDATE workflow.decision_policies SET effective_to=NULL WHERE policy_version_id=%s",
                (_workflow_row().policy_version_id,),
            )
        # Existing kinds retain their previous update behavior.
        conn.execute(
            "UPDATE workflow.decision_policies SET parameters=%s WHERE policy_version_id=%s",
            (Jsonb({"replacement": True}), "synthetic-heatzone_merge:" + TENANT),
        )


def test_factory_resolves_sql_versions_and_rechecks_live_identity_scope(
    intake_blank_db, monkeypatch
):
    db = intake_blank_db
    _upgrade(db, "head")
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())
    apply_upgrade_to_database(db.url())
    bundle = _bundle(db, monkeypatch)
    try:
        provider = bundle.assisted_intake_workflow_policy
        assert isinstance(provider, RegistryIntakeWorkflowPolicy)
        scope = {"tenant_id": TENANT}
        plan = provider.resolve_initial(scope, AT)
        assert plan.schedule.due_at == datetime(2026, 10, 13, 2, 30, tzinfo=UTC)
        assert (
            provider.resolve_assignment(
                scope,
                plan.schedule,
                actor_id=MANAGER,
                owner_subject_id=OWNER,
                owner_role="expansion-staff",
                requested_due_at=plan.schedule.due_at,
                reason="Synthetic assignment",
            ).schedule
            == plan.schedule
        )
        with db.connect() as conn:
            conn.execute(
                "UPDATE identity.account_scopes SET brand_ids=%s WHERE account_id=%s",
                (Jsonb(["synthetic-other-brand"]), OWNER),
            )
        with pytest.raises(PermissionError):
            provider.resolve_assignment(
                scope,
                plan.schedule,
                actor_id=MANAGER,
                owner_subject_id=OWNER,
                owner_role="expansion-staff",
                requested_due_at=plan.schedule.due_at,
                reason="Synthetic assignment",
            )
        with db.connect() as conn:
            conn.execute(
                "UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (APPROVER,)
            )
        with pytest.raises(IntakeWorkflowUnavailable):
            provider.resolve_initial(scope, AT)
    finally:
        bundle.engine.close()


def test_sql_policy_http_pause_resume_survives_restart_and_flush_failure(
    intake_blank_db, monkeypatch
):
    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())

    class Clock(datetime):
        instant = AT

        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz or UTC)

    monkeypatch.setattr(listings, "datetime", Clock)
    headers = {
        "x-subject-id": MANAGER,
        "x-tenant-id": TENANT,
        "x-roles": "site_reviewer,expansion_user",
    }

    def client_for(bundle):
        app = FastAPI()
        app.include_router(
            listings.create_assisted_intake_router(
                bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy
            ),
            prefix="/api/v1",
        )
        return TestClient(app)

    bundle = _bundle(db, monkeypatch)
    try:
        response = client_for(bundle).post(
            "/api/v1/intakes/url",
            json={
                "original_url": "https://example.invalid/synthetic-sql",
                "scope": {"tenant_id": TENANT},
            },
            headers={**headers, "Idempotency-Key": "synthetic-sql-submit"},
        )
        assert response.status_code == 202, response.text
        intake_id = response.json()["intake_id"]
    finally:
        bundle.engine.close()

    bundle = _bundle(db, monkeypatch)
    try:
        client = client_for(bundle)
        detail = client.get(f"/api/v1/intakes/{intake_id}", headers=headers)
        assert detail.status_code == 200
        sla_id = detail.json()["sla_instance_id"]
        pause_headers = {**headers, "Idempotency-Key": "synthetic-sql-pause", "If-Match": 'W/"1"'}
        response = client.post(
            f"/api/v1/sla-instances/{sla_id}/actions/pause",
            json={
                "reason": "Synthetic approved hold",
                "expected_resume_at": "2026-10-13T03:00:00Z",
            },
            headers=pause_headers,
        )
        assert response.status_code == 200, response.text
        pause = response.json()
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        client = client_for(bundle)
        Clock.instant = datetime(2026, 10, 13, 2, tzinfo=UTC)
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        assert store.slas[sla_id]["pause_intervals"][0]["approved_by"] == APPROVER
        assert pause["audit_event_id"] in store.workflow_audits
        before = deepcopy((store.slas, store.workflow_audits, store.workflow_outbox, store.replays))
        original = store._store.put

        def failing_put(collection, key, value):
            if collection == "assisted_intake.workflow_outbox":
                raise RuntimeError("synthetic PostgreSQL outbox flush failure")
            return original(collection, key, value)

        monkeypatch.setattr(store._store, "put", failing_put)
        path = f"/api/v1/sla-instances/{sla_id}/actions/resume"
        resume_headers = {
            **headers,
            "Idempotency-Key": "synthetic-sql-resume",
            "If-Match": 'W/"2"',
            "X-Correlation-Id": str(uuid4()),
        }
        with pytest.raises(RuntimeError, match="synthetic PostgreSQL outbox flush failure"):
            client.post(path, json={"reason": "Synthetic resume"}, headers=resume_headers)
        store.refresh(TENANT)
        assert (store.slas, store.workflow_audits, store.workflow_outbox, store.replays) == before
        monkeypatch.setattr(store._store, "put", original)
        response = client.post(path, json={"reason": "Synthetic resume"}, headers=resume_headers)
        assert response.status_code == 200, response.text
        assert response.json()["due_at"] == "2026-10-13T04:00:00Z"
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM identity.account_roles WHERE account_id=%s AND role=%s",
                (MANAGER, Role.SITE_REVIEWER.value),
            )
        store.refresh(TENANT)
        snapshot = deepcopy(
            (store.slas, store.workflow_audits, store.workflow_outbox, store.replays)
        )
        replay = client.post(path, json={"reason": "Synthetic resume"}, headers=resume_headers)
        assert replay.status_code == 403, replay.text
        store.refresh(TENANT)
        assert (store.slas, store.workflow_audits, store.workflow_outbox, store.replays) == snapshot
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        detail = client_for(bundle).get(f"/api/v1/intakes/{intake_id}", headers=headers)
        assert detail.status_code == 200 and detail.json()["due_at"] == "2026-10-13T04:00:00Z"
        bundle.assisted_intake_store.refresh(TENANT)
        assert (
            bundle.assisted_intake_store.slas[sla_id]["pause_intervals"][0]["resumed_at"]
            == "2026-10-13T02:00:00Z"
        )
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("failure", [None, "transfer", "complete"])
def test_assignment_transitions_and_sla_completion_are_durable_atomic_and_replayable(
    intake_blank_db, monkeypatch, failure
):
    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    row = _workflow_row()
    parameters = deepcopy(row.parameters)
    parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, replace(row, parameters=parameters))
        _insert_policy(conn, _calendar_row())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT.astimezone(tz or UTC)

    monkeypatch.setattr(listings, "datetime", Clock)

    def headers(actor):
        return {
            "x-subject-id": actor,
            "x-tenant-id": TENANT,
            "x-roles": "site_reviewer,expansion_user" if actor == MANAGER else "expansion_user",
        }

    def client_for(bundle):
        app = FastAPI()
        app.include_router(
            listings.create_assisted_intake_router(
                bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy
            ),
            prefix="/api/v1",
        )
        return TestClient(app)

    bundle = _bundle(db, monkeypatch)
    try:
        client = client_for(bundle)
        submitted = client.post(
            "/api/v1/intakes/url",
            json={
                "original_url": "https://example.invalid/sql-task",
                "scope": {"tenant_id": TENANT},
            },
            headers={**headers(MANAGER), "Idempotency-Key": "synthetic-pg-submission"},
        )
        assert submitted.status_code == 202, submitted.text
        iid = submitted.json()["intake_id"]
        assigned = client.put(
            f"/api/v1/intakes/{iid}/assignment",
            json={
                "owner_subject_id": OWNER,
                "owner_role": "expansion-staff",
                "due_at": "2026-10-13T02:30:00Z",
                "reason": "Synthetic SQL assignment",
            },
            headers={
                **headers(MANAGER),
                "Idempotency-Key": "synthetic-pg-assign",
                "If-Match": 'W/"1"',
            },
        )
        assert assigned.status_code == 200, assigned.text
        aid = assigned.json()["assignment_id"]
        claimed = client.post(
            f"/api/v1/assignments/{aid}/actions/claim",
            json={"reason": "Synthetic SQL claim"},
            headers={
                **headers(OWNER),
                "Idempotency-Key": "synthetic-pg-claim",
                "If-Match": f'W/"{assigned.json()["version"]}"',
            },
        )
        assert claimed.status_code == 200, claimed.text
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        client = client_for(bundle)
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        assert store.assignments[aid]["status"] == "CLAIMED"
        assert claimed.json()["audit_event_id"] in store.workflow_audits

        def command(action, body, actor, version):
            return client.post(
                f"/api/v1/assignments/{aid}/actions/{action}",
                json=body,
                headers={
                    **headers(actor),
                    "Idempotency-Key": "synthetic-pg-" + action,
                    "If-Match": f'W/"{version}"',
                },
            )

        def run(action, body, actor, version):
            if failure == action:
                store.refresh(TENANT)
                snapshot = deepcopy(
                    (
                        store.intakes,
                        store.assignments,
                        store.slas,
                        store.workflow_audits,
                        store.workflow_outbox,
                        store.replays,
                    )
                )
                original = store._store.put

                def fail(collection, key, value):
                    if collection == "assisted_intake.workflow_outbox":
                        raise RuntimeError("synthetic assignment SQL flush failure")
                    return original(collection, key, value)

                monkeypatch.setattr(store._store, "put", fail)
                with pytest.raises(RuntimeError, match="synthetic assignment SQL flush failure"):
                    command(action, body, actor, version)
                store.refresh(TENANT)
                assert (
                    store.intakes,
                    store.assignments,
                    store.slas,
                    store.workflow_audits,
                    store.workflow_outbox,
                    store.replays,
                ) == snapshot
                monkeypatch.setattr(store._store, "put", original)
            response = command(action, body, actor, version)
            assert response.status_code == 200, response.text
            return response

        transferred = run(
            "transfer",
            {
                "target_owner_subject_id": MANAGER,
                "target_owner_role": "site-reviewer",
                "reason": "Synthetic SQL handoff",
                "handoff_note": "Synthetic SQL reviewed target",
            },
            OWNER,
            claimed.json()["version"],
        )
        accepted = run(
            "claim",
            {"reason": "Synthetic SQL target acceptance"},
            MANAGER,
            transferred.json()["version"],
        )
        store.refresh(TENANT)
        cancelled = client.post(
            f"/api/v1/intakes/{iid}/actions/cancel",
            json={"reason": "Synthetic SQL completed work"},
            headers={
                **headers(MANAGER),
                "Idempotency-Key": "synthetic-pg-cancel",
                "If-Match": f'W/"{store.intakes[iid]["version"]}"',
            },
        )
        assert cancelled.status_code == 200, cancelled.text
        completed = run(
            "complete",
            {"reason": "Synthetic SQL task completion"},
            MANAGER,
            accepted.json()["version"],
        )
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        client = client_for(bundle)
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        assert store.assignments[aid]["status"] == "COMPLETED"
        sla = next(s for s in store.slas.values() if s["assignment_id"] == aid)
        assert sla["state"] == "COMPLETED"
        assert (
            completed.json()["audit_event_id"] in store.workflow_audits
            and sla["audit_event_id"] in store.workflow_audits
        )
        detail = client.get(f"/api/v1/intakes/{iid}", headers=headers(MANAGER))
        assert (
            detail.status_code == 200
            and detail.json()["assignment_id"] == aid
            and detail.json()["sla_state"] == "COMPLETED"
        )
        before = deepcopy((store.workflow_audits, store.workflow_outbox))
        replay = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Synthetic SQL task completion"},
            headers={
                **headers(MANAGER),
                "Idempotency-Key": "synthetic-pg-complete",
                "If-Match": f'W/"{accepted.json()["version"]}"',
            },
        )
        assert replay.status_code == 200 and replay.json() == completed.json()
        store.refresh(TENANT)
        assert (store.workflow_audits, store.workflow_outbox) == before
    finally:
        bundle.engine.close()
