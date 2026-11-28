"""Execute the canonical migrations and read actual SQL identities/policies."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg.types.json import Jsonb

from apps.api.app.routes import listings
from modules.listing.application.intake_registry_calendar import RegistryIntakeWorkflowPolicy
from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    initialize_intake_workflow,
)
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.factory import build_persistence
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
    conn.execute(
        "INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
        (APPROVER, Role.RECORDS_MANAGER.value),
    )


def _bundle(database, monkeypatch):
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODAY_DATABASE_URL", database.url())
    return build_persistence(mode="postgresql")


def _retained_url_workflow(bundle):
    """Persist the original V1 task fixture, independent of fresh HTTP create.

    The real store, registry policy, audit sink and same-engine transaction own
    this historical setup. It is not a native Intake or source-use approval.
    """
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    intake_id, job_id, correlation_id = (str(uuid4()) for _ in range(3))
    at = AT.isoformat().replace("+00:00", "Z")
    intake = {
        "intake_id": intake_id, "state": "SUBMITTED", "intake_method": "URL",
        "scope": {"tenant_id": TENANT}, "submitted_by": MANAGER,
        "submitted_at": at, "updated_at": at, "version": 1,
        "original_url": "https://example.invalid/retained-sql-workflow",
        "canonical_url": "https://example.invalid/retained-sql-workflow",
        "policy_state": "POLICY_UNKNOWN", "job_id": job_id,
        "correlation_id": correlation_id, "fields": [], "audit": [],
        "processing_history": [{
            "transition_id": str(uuid4()), "from_state": None, "to_state": "SUBMITTED",
            "occurred_at": at, "actor": MANAGER, "version_after": 1,
        }],
    }
    with store.transaction():
        store.intakes[intake_id] = intake
        initialize_intake_workflow(store, intake, bundle.assisted_intake_workflow_policy)
        store.jobs[job_id] = {
            "job_id": job_id, "intake_id": intake_id, "tenant_id": TENANT,
            "status": "QUEUED", "checkpoint": "RETRIEVING", "attempt": 0,
            "version": 1, "correlation_id": correlation_id,
        }
        store.flush()
    return intake_id


def test_native_submission_uses_the_existing_sql_workflow_policy_and_current_approver(intake_blank_db, monkeypatch):
    from apps.api.app.routes import canonical_intakes
    from shared.audit import InMemoryAuditLog
    from shared.auth import Principal, Scope
    from tests.intake_workflow_fixture import seed_synthetic_intake_read_flag

    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())
    bundle = _bundle(db, monkeypatch)
    try:
        seed_synthetic_intake_read_flag(bundle.feature_flag_registry, TENANT)
        monkeypatch.setattr(canonical_intakes, "principal_from_headers", lambda _: Principal(
            MANAGER, frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=TENANT),
        ))
        app = FastAPI()
        app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
        app.include_router(listings.create_assisted_intake_router(bundle.assisted_intake_store), prefix="/api/v1")
        with TestClient(app) as client:
            body = {"intake_method": "MANUAL", "scope": {}, "reason": "Explicit synthetic SQL native routing"}
            created = client.post("/api/v1/canonical-intakes", json=body,
                headers={"Idempotency-Key": "synthetic-native-real-registry-001"})
            assert created.status_code == 201, created.text
            iid = created.json()["intake_id"]
            selected = client.get("/api/v1/canonical-intakes/" + iid)
            assert selected.status_code == 200, selected.text
            workflow = selected.json()["workflow"]
            assert workflow["policy_version"] == _workflow_row().policy_version_id
            assert workflow["calendar_version"] == _calendar_row().policy_version_id
            assert workflow["queue_key"] == _workflow_row().parameters["queue_key"]
            assert workflow["owner_subject_id"] is None
            bundle.assisted_intake_store.refresh(TENANT)
            assert iid not in bundle.assisted_intake_store.intakes
            tables = ("intake.intakes", "workflow.assignments", "workflow.sla_instances",
                      "workflow.assignment_transitions", "workflow.sla_transitions", "audit.audit_events", "workflow.outbox_events")
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (APPROVER,))
                before = [conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in tables]
            rejected = client.post("/api/v1/canonical-intakes", json=body,
                headers={"Idempotency-Key": "synthetic-native-real-registry-002"})
            assert rejected.status_code == 503, rejected.text
            with db.connect() as conn:
                assert [conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in tables] == before
    finally:
        bundle.engine.close()


def test_expand_migration_preserves_registry_rows_and_rejects_unknown_kind(intake_blank_db):
    db = intake_blank_db
    migration_head = ScriptDirectory.from_config(Config("infra/db/migrations/alembic.ini")).get_current_head()
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
            == migration_head
        )
        assert (
            conn.execute(
                "SELECT row_to_json(p) FROM workflow.decision_policies p ORDER BY policy_version_id"
            ).fetchall()
            == before
        )
        _insert_policy(conn, _workflow_row())
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())
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
        _insert_policy(conn, _audit_retention_row())
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
        _insert_policy(conn, _audit_retention_row())

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
        intake_id = _retained_url_workflow(bundle)
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
        _insert_policy(conn, _audit_retention_row())

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
        iid = _retained_url_workflow(bundle)
        assignment_body = {
                "owner_subject_id": OWNER,
                "owner_role": "expansion-staff",
                "due_at": "2026-10-13T02:30:00Z",
                "reason": "Synthetic SQL assignment",
            }
        assignment_headers = {
                **headers(MANAGER),
                "Idempotency-Key": "synthetic-pg-assign",
                "If-Match": 'W/"1"',
            }
        assigned = client.put(
            f"/api/v1/intakes/{iid}/assignment",
            json=assignment_body,
            headers=assignment_headers,
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
        if failure is None:
            # A restart and later claim retain the original receipt, while
            # current SQL target authorization still governs access to it.
            fields = ("intakes", "assignments", "slas", "workflow_audits", "workflow_outbox", "replays")
            before_replay = deepcopy(tuple(getattr(store, field) for field in fields))
            replayed = client.put(
                f"/api/v1/intakes/{iid}/assignment",
                json=assignment_body, headers=assignment_headers,
            )
            assert replayed.status_code == 200, replayed.text
            assert replayed.json() == assigned.json()
            assert replayed.headers["etag"] == assigned.headers["etag"]
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (OWNER,))
            revoked_replay = client.put(
                f"/api/v1/intakes/{iid}/assignment",
                json=assignment_body, headers=assignment_headers,
            )
            assert revoked_replay.status_code == 403, revoked_replay.text
            store.refresh(TENANT)
            assert tuple(getattr(store, field) for field in fields) == before_replay
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='active' WHERE account_id=%s", (OWNER,))

        def directory():
            return client.get(
                f"/api/v1/assignments/{aid}/transfer-targets", headers=headers(OWNER),
            )

        expected_directory = {
            "assignment_id": aid,
            "assignment_version": claimed.json()["version"],
            "items": [{"subject_id": MANAGER, "display_name": MANAGER,
                       "owner_role": "site-reviewer"}],
        }
        assert directory().json() == expected_directory
        if failure is None:
            # Prove the SQL implementation does not cache recipient identity grants.
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (MANAGER,))
            assert directory().json()["items"] == []
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='active' WHERE account_id=%s", (MANAGER,))
            assert directory().json() == expected_directory
            store.refresh(TENANT)
            fields = ("intakes", "assignments", "slas", "workflow_audits", "workflow_outbox", "replays")
            before_revoked_transfer = deepcopy(tuple(getattr(store, field) for field in fields))
            for invalidate_scope in (
                "UPDATE identity.account_scopes SET brand_ids='null'::jsonb WHERE account_id=%s",
                "DELETE FROM identity.account_scopes WHERE account_id=%s",
            ):
                with db.connect() as conn:
                    conn.execute(invalidate_scope, (MANAGER,))
                assert directory().json()["items"] == []
                revoked = client.post(
                    f"/api/v1/assignments/{aid}/actions/transfer",
                    json={"target_owner_subject_id": MANAGER, "target_owner_role": "site-reviewer",
                          "reason": "Synthetic scope revocation check", "handoff_note": "Synthetic handoff"},
                    headers={**headers(OWNER), "Idempotency-Key": "synthetic-revoked-scope-transfer",
                             "If-Match": f'W/"{claimed.json()["version"]}"'},
                )
                assert revoked.status_code == 403, revoked.text
                store.refresh(TENANT)
                assert tuple(getattr(store, field) for field in fields) == before_revoked_transfer
            with db.connect() as conn:
                conn.execute("INSERT INTO identity.account_scopes(account_id,brand_ids) VALUES (%s,%s)",
                             (MANAGER, Jsonb(["outside-intake-scope"])))
            assert directory().json()["items"] == []
            with db.connect() as conn:
                conn.execute("UPDATE identity.account_scopes SET brand_ids='[]'::jsonb WHERE account_id=%s", (MANAGER,))
                conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (MANAGER,))
            assert directory().json()["items"] == []
            with db.connect() as conn:
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
                             (MANAGER, Role.SITE_REVIEWER.value))
            assert directory().json() == expected_directory

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


def test_native_workflow_actions_use_live_sql_registry_directory_and_approver(intake_blank_db, monkeypatch):
    """Original V1 actions on a fresh native Intake, against the real SQL registry."""
    from apps.api.app.routes import canonical_intakes
    from apps.api.oday_api.security import dependencies
    from shared.audit import InMemoryAuditLog
    from shared.auth import Principal, Scope
    from tests.intake_workflow_fixture import seed_synthetic_intake_read_flag

    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    row = _workflow_row()
    parameters = deepcopy(row.parameters)
    parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
    parameters["assignee_role_bindings"]["staff-secondary"] = Role.EXPANSION_USER.value
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, replace(row, parameters=parameters))
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())
    caller = {}
    roles = {MANAGER: Role.SITE_REVIEWER, OWNER: Role.EXPANSION_USER}

    def act(actor):
        caller["principal"] = Principal(actor, frozenset({roles[actor]}), Scope(tenant_id=TENANT))

    monkeypatch.setattr(canonical_intakes, "principal_from_headers", lambda _: caller["principal"])
    monkeypatch.setattr(dependencies, "principal_from_headers", lambda _: caller["principal"])
    bundle = _bundle(db, monkeypatch)
    tables = ("workflow.assignments", "workflow.sla_instances", "workflow.assignment_transitions",
              "workflow.sla_transitions", "workflow.sla_pause_intervals", "audit.audit_events",
              "workflow.outbox_events", "odp_runtime.durable_documents")

    def counts():
        with db.connect() as conn:
            return [conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in tables]

    def account(account_id, status):
        with db.connect() as conn:
            conn.execute("UPDATE identity.accounts SET status=%s WHERE account_id=%s", (status, account_id))

    try:
        seed_synthetic_intake_read_flag(bundle.feature_flag_registry, TENANT)
        app = FastAPI()
        app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
        app.include_router(listings.create_assisted_intake_router(
            bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy), prefix="/api/v1")
        with TestClient(app) as client:
            act(MANAGER)
            created = client.post("/api/v1/canonical-intakes", headers={"Idempotency-Key": "synthetic-native-registry-001"},
                json={"intake_method": "MANUAL", "scope": {}, "reason": "Explicit synthetic SQL native workflow"})
            assert created.status_code == 201, created.text
            iid = created.json()["intake_id"]
            workflow = client.get("/api/v1/canonical-intakes/" + iid).json()["workflow"]
            aid, sid = workflow["assignment_id"], workflow["sla_instance_id"]
            assert len({iid, aid, sid}) == 3
            targets_path = f"/api/v1/intakes/{iid}/assignment/targets"

            def snapshot():
                # Include full row values: counts alone cannot detect a GET update.
                with db.connect() as conn:
                    return [conn.execute("SELECT row_to_json(t) FROM " + table + " t ORDER BY row_to_json(t)::text").fetchall()
                            for table in (*tables, "intake.intakes", "workflow.idempotency_records")]

            def directory(expected=200, **kwargs):
                before_read = snapshot()
                result = client.get(targets_path, **kwargs)
                assert result.status_code == expected, result.text
                assert snapshot() == before_read
                if expected == 200:
                    assert result.headers["cache-control"] == "no-store"
                return result

            page = directory().json()
            assert page == {"intake_id": iid, "intake_version": 1, "assignment_id": aid,
                "assignment_version": 1, "due_at": workflow["due_at"], "items": [
                    {"subject_id": MANAGER, "display_name": MANAGER, "owner_role": "site-reviewer"},
                    {"subject_id": OWNER, "display_name": OWNER, "owner_role": "expansion-staff"},
                    {"subject_id": OWNER, "display_name": OWNER, "owner_role": "staff-secondary"},
                ]}
            # Initial discovery cannot borrow transfer's current-owner authority.
            before = snapshot()
            assert client.get(f"/api/v1/assignments/{aid}/transfer-targets").status_code == 403
            assert snapshot() == before
            act(OWNER)
            directory(403)
            act(MANAGER)
            directory(409, headers={"If-Actor-Match": OWNER})
            directory(409, headers={"If-Tenant-Match": str(uuid4())})
            caller["principal"] = Principal(MANAGER, frozenset({Role.SITE_REVIEWER}),
                Scope(tenant_id=TENANT, brand_ids=frozenset({str(uuid4())})))
            directory(403)
            caller["principal"] = Principal(MANAGER, frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=str(uuid4())))
            directory(404)
            act(MANAGER)
            for actor in (MANAGER, APPROVER):
                account(actor, "disabled")
                directory(403 if actor == MANAGER else 503)
                account(actor, "active")
            with db.connect() as conn:
                conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (MANAGER,))
            directory(403)
            with db.connect() as conn:
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
                    (MANAGER, Role.SITE_REVIEWER.value))
            # Both verified HTTP scope and the live SQL actor scope must cover the Intake.
            for actor in (MANAGER, OWNER):
                for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules"):
                    with db.connect() as conn:
                        conn.execute(f"UPDATE identity.account_scopes SET {axis}=%s WHERE account_id=%s",
                            (Jsonb([str(uuid4())]), actor))
                    result = directory(403 if actor == MANAGER else 200)
                    if actor == OWNER:
                        assert all(item["subject_id"] != OWNER for item in result.json()["items"])
                    with db.connect() as conn:
                        conn.execute(f"UPDATE identity.account_scopes SET {axis}='[]'::jsonb WHERE account_id=%s", (actor,))
            account(OWNER, "disabled")
            assert all(item["subject_id"] != OWNER for item in directory().json()["items"])
            account(OWNER, "active")
            with db.connect() as conn:
                conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (OWNER,))
            assert all(item["subject_id"] != OWNER for item in directory().json()["items"])
            with db.connect() as conn:
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
                    (OWNER, Role.EXPANSION_USER.value))
            # No eligible target is a successful empty observation, not registry failure.
            account(OWNER, "disabled")
            with db.connect() as conn:
                conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (MANAGER,))
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
                    (MANAGER, Role.EXECUTIVE.value))
            assert directory().json()["items"] == []
            with db.connect() as conn:
                conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (MANAGER,))
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,'isolated-synthetic-test')",
                    (MANAGER, Role.SITE_REVIEWER.value))
            account(OWNER, "active")
            # SQL directory rows are still verified; duplicate observations cannot
            # duplicate a (subject, role) option or collapse its distinct roles.
            provider = bundle.assisted_intake_workflow_policy
            identities = provider._identities
            original_directory = identities.list_scoped_active_accounts
            with monkeypatch.context() as patch:
                patch.setattr(identities, "list_scoped_active_accounts", lambda tenant:
                    original_directory(tenant) * 2)
                assert directory().json() == page
            with monkeypatch.context() as patch:
                def unavailable(_tenant):
                    raise RuntimeError("Synthetic SQL directory unavailable")
                patch.setattr(identities, "list_scoped_active_accounts", unavailable)
                directory(503)
            # A second recorded Assignment, including completed history, is not
            # an unambiguous initial workflow. The existing SQL owner refuses it.
            duplicate = str(uuid4())
            with db.connect() as conn:
                conn.execute("INSERT INTO workflow.assignments(assignment_id,tenant_id,intake_id,status,queue_key,due_at) "
                    "VALUES (%s,%s,%s,'COMPLETED','synthetic-duplicate',%s)", (duplicate, TENANT, iid, workflow["due_at"]))
            directory(503)
            with db.connect() as conn:
                conn.execute("DELETE FROM workflow.assignments WHERE assignment_id=%s", (duplicate,))
                conn.execute("UPDATE workflow.assignments SET owner_subject_id=%s WHERE assignment_id=%s", (OWNER, aid))
            directory(409)
            with db.connect() as conn:
                conn.execute("UPDATE workflow.assignments SET owner_subject_id=NULL WHERE assignment_id=%s", (aid,))
            body = {"owner_subject_id": OWNER, "owner_role": "expansion-staff", "due_at": page["due_at"],
                    "reason": "Synthetic SQL native assignment"}
            assign_headers = {"Idempotency-Key": "synthetic-native-reg-assign",
                              "If-Match": f'W/"{page["intake_version"]}"'}
            before = snapshot()
            stale = client.put(f"/api/v1/intakes/{iid}/assignment", json=body,
                headers={**assign_headers, "If-Match": 'W/"2"'})
            assert stale.status_code == 409, stale.text
            assert snapshot() == before
            account(OWNER, "disabled")
            assert client.put(f"/api/v1/intakes/{iid}/assignment", json=body, headers=assign_headers).status_code == 403
            account(OWNER, "active")
            assigned = client.put(f"/api/v1/intakes/{iid}/assignment", json=body, headers=assign_headers)
            assert assigned.status_code == 200, assigned.text
            directory(409)
            # A stored receipt never retains a revoked target's authority.
            account(OWNER, "disabled")
            before = counts()
            assert client.put(f"/api/v1/intakes/{iid}/assignment", json=body, headers=assign_headers).status_code == 403
            # The reader's own authority is current, so the stored outcome is
            # observable; observation re-grants nothing and writes nothing.
            observed = client.get(f"/api/v1/intakes/{iid}/assignment/receipt",
                headers={"Idempotency-Key": "synthetic-native-reg-assign"})
            assert observed.status_code == 200 and observed.json()["receipt"] == assigned.json()
            assert counts() == before
            account(OWNER, "active")
            replayed = client.put(f"/api/v1/intakes/{iid}/assignment", json=body, headers=assign_headers)
            assert replayed.status_code == 200 and replayed.json() == assigned.json()

            act(OWNER)
            claimed = client.post(f"/api/v1/assignments/{aid}/actions/claim", json={"reason": "Synthetic SQL claim"},
                headers={"Idempotency-Key": "synthetic-native-reg-claim", "If-Match": f'W/"{assigned.json()["version"]}"'})
            assert claimed.status_code == 200, claimed.text
            directory = client.get(f"/api/v1/assignments/{aid}/transfer-targets")
            assert directory.status_code == 200, directory.text
            assert directory.json() == {"assignment_id": aid, "assignment_version": claimed.json()["version"],
                "items": [{"subject_id": MANAGER, "display_name": MANAGER, "owner_role": "site-reviewer"}]}
            account(MANAGER, "disabled")
            assert client.get(f"/api/v1/assignments/{aid}/transfer-targets").json()["items"] == []
            before = counts()
            transfer = {"target_owner_subject_id": MANAGER, "target_owner_role": "site-reviewer",
                        "reason": "Synthetic SQL transfer", "handoff_note": "Synthetic SQL handoff"}
            transfer_headers = {"Idempotency-Key": "synthetic-native-reg-transfer",
                                "If-Match": f'W/"{claimed.json()["version"]}"'}
            assert client.post(f"/api/v1/assignments/{aid}/actions/transfer", json=transfer,
                               headers=transfer_headers).status_code == 403
            assert counts() == before
            account(MANAGER, "active")
            transferred = client.post(f"/api/v1/assignments/{aid}/actions/transfer", json=transfer, headers=transfer_headers)
            assert transferred.status_code == 200, transferred.text
            assert transferred.json()["status"] == "TRANSFERRED"

            act(MANAGER)
            accepted = client.post(f"/api/v1/assignments/{aid}/actions/claim", json={"reason": "Synthetic SQL accept"},
                headers={"Idempotency-Key": "synthetic-native-reg-accept", "If-Match": f'W/"{transferred.json()["version"]}"'})
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == "CLAIMED" and accepted.json()["owner_subject_id"] == MANAGER
            sla_version = client.get("/api/v1/canonical-intakes/" + iid).json()["workflow"]["sla_version"]
            with db.connect() as conn:
                now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            expected = (now + (datetime(2026, 1, 2, tzinfo=UTC) - datetime(2026, 1, 1, tzinfo=UTC))).isoformat()
            paused = client.post(f"/api/v1/sla-instances/{sid}/actions/pause",
                json={"reason": "Synthetic approved hold", "expected_resume_at": expected},
                headers={"Idempotency-Key": "synthetic-native-reg-pause", "If-Match": f'W/"{sla_version}"'})
            assert paused.status_code == 200, paused.text
            resumed = client.post(f"/api/v1/sla-instances/{sid}/actions/resume", json={"reason": "Synthetic SQL resume"},
                headers={"Idempotency-Key": "synthetic-native-reg-resume", "If-Match": f'W/"{paused.json()["version"]}"'})
            assert resumed.status_code == 200, resumed.text
            with db.connect() as conn:
                pause = conn.execute("SELECT approved_by::text,created_by::text,expected_resume_at,resumed_at "
                                     "FROM workflow.sla_pause_intervals WHERE sla_instance_id=%s", (sid,)).fetchall()
            assert len(pause) == 1 and pause[0][0] == APPROVER and pause[0][1] == MANAGER
            assert pause[0][2] == datetime.fromisoformat(expected) and pause[0][3] is not None
            bundle.assisted_intake_store.refresh(TENANT)
            assert iid not in bundle.assisted_intake_store.intakes
            assert aid not in bundle.assisted_intake_store.assignments and sid not in bundle.assisted_intake_store.slas
    finally:
        bundle.engine.close()
