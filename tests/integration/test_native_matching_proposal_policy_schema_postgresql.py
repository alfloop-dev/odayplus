"""Real registry DDL compatibility only; no workflow approval or runtime grant.

Every inserted policy is disabled, future-dated, uses non-runtime contracts and
is labeled NOT_APPROVED_SCHEMA_COMPAT_ONLY. No identities, service credentials,
source permissions, accepted Jobs/Intakes, decisions or approval history exist.
The existing blank-PostgreSQL fixture owns database creation and normal cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from psycopg import errors, sql
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.requires_live_env

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "infra/db/migrations/000036_native_matching_proposal_authority_kind.sql"
MARKER = "NOT_APPROVED_SCHEMA_COMPAT_ONLY"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"
NEW_KIND = "native_matching_proposal_authority"
OLD_KINDS = (
    "forecast_alert", "heatzone_merge", "heatzone_absorption",
    "sitescore_recommendation", "price_exploration", "netplan_action",
    "model_performance_drift", "intake_workflow", "intake_business_calendar",
    "intake_audit_retention",
)
PROTECTED_KINDS = (*OLD_KINDS[-3:], NEW_KIND)


def _config(db):
    config = Config(str(ROOT / "infra/db/migrations/alembic.ini"))
    config.set_main_option("sqlalchemy.url", db.url(driver="psycopg").replace("%", "%%"))
    return config


@pytest.fixture
def registry_db(intake_blank_db):
    # Apply the real old migration chain, including 000014/000029/000030.
    # No tenants exist during its historical policy backfills.
    command.upgrade(_config(intake_blank_db), "0028")
    with intake_blank_db.connect(autocommit=False) as conn:
        assert conn.execute("SELECT count(*) FROM workflow.decision_policies").fetchone() == (0,)
        # Tenant keys only, not actors. Suppress the historical seed hooks
        # during fixture setup so they cannot manufacture effective policies.
        # All hooks are restored in the same transaction before testing DDL.
        conn.execute("ALTER TABLE core.tenants DISABLE TRIGGER trg_seed_forecast_alert_policy")
        conn.execute("ALTER TABLE core.tenants DISABLE TRIGGER trg_seed_model_performance_drift_policy")
        conn.execute("ALTER TABLE core.tenants DISABLE TRIGGER trg_seed_netplan_disclosure_policy")
        conn.execute("ALTER TABLE core.tenants DISABLE TRIGGER trg_seed_heatzone_merge_policy")
        conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name, status) "
            "VALUES (%s, %s, 'disabled'), (%s, %s, 'disabled')",
            (TENANT_A, MARKER, TENANT_B, MARKER),
        )
        conn.execute("ALTER TABLE core.tenants ENABLE TRIGGER trg_seed_forecast_alert_policy")
        conn.execute("ALTER TABLE core.tenants ENABLE TRIGGER trg_seed_model_performance_drift_policy")
        conn.execute("ALTER TABLE core.tenants ENABLE TRIGGER trg_seed_netplan_disclosure_policy")
        conn.execute("ALTER TABLE core.tenants ENABLE TRIGGER trg_seed_heatzone_merge_policy")
        assert conn.execute("SELECT count(*) FROM workflow.decision_policies").fetchone() == (0,)
    return intake_blank_db


def _row(label, kind=NEW_KIND, **changes):
    row = {
        "policy_label": label,
        "policy_id": label,
        "policy_version": "schema-only-v1",
        "policy_kind": kind,
        "tenant_id": TENANT_A,
        "effective_from": "2999-01-01T00:00:00+00:00",
        "effective_to": None,
        "owner_role": MARKER,
        "approved_by": MARKER,
        "approved_at": "2999-01-01T00:00:00+00:00",
        "input_contract": MARKER,
        "output_contract": MARKER,
        "change_reason": MARKER,
        "rollback_policy_version": None,
        "parameters": {"enabled": False, "schema_compat_only": True},
        "declared_inputs": [MARKER],
    }
    row.update(changes)
    row.setdefault("policy_version_id", f"{row['policy_label']}:{row['tenant_id']}")
    return row


def _insert(conn, row):
    conn.execute(
        sql.SQL("INSERT INTO workflow.decision_policies ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() for _ in row),
        ),
        tuple(Jsonb(value) if name == "parameters" else value for name, value in row.items()),
    )


def _rows(conn):
    return conn.execute(
        "SELECT to_jsonb(p) FROM workflow.decision_policies p ORDER BY policy_version_id"
    ).fetchall()


def _reject(conn, statement, params, constraint):
    with pytest.raises(errors.IntegrityError) as failure, conn.transaction():
        conn.execute(statement, params)
    assert failure.value.diag.constraint_name == constraint


def _update(key, column, value):
    return (
        sql.SQL("UPDATE workflow.decision_policies SET {} = %s WHERE policy_version_id = %s").format(
            sql.Identifier(column)
        ),
        (Jsonb(value) if column == "parameters" else value, key),
    )


def test_empty_registry_bridge_upgrade_and_expand_only_downgrade(registry_db):
    config = _config(registry_db)
    with registry_db.connect() as conn:
        assert _rows(conn) == []
    command.upgrade(config, "0029")
    with registry_db.connect() as conn:
        assert conn.execute("SELECT version_num FROM public.oday_plus_alembic_version").fetchone() == ("0029",)
        assert _rows(conn) == []
        conn.execute(MIGRATION.read_text())
        assert _rows(conn) == []
        _insert(conn, _row("schema-downgrade"))
        before = _rows(conn)
    command.downgrade(config, "0028")
    with registry_db.connect() as conn:
        assert conn.execute("SELECT version_num FROM public.oday_plus_alembic_version").fetchone() == ("0028",)
        assert _rows(conn) == before
        _reject(
            conn, "DELETE FROM workflow.decision_policies", (),
            "chk_intake_policy_version_immutable",
        )
    command.upgrade(config, "0029")
    with registry_db.connect() as conn:
        assert _rows(conn) == before
        assert conn.execute("SELECT count(*) FROM identity.accounts").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM workflow.decisions").fetchone() == (0,)


def test_old_ten_kinds_new_kind_and_rerun_preserve_all_rows(registry_db):
    with registry_db.connect() as conn:
        for index, kind in enumerate(OLD_KINDS):
            _insert(conn, _row(f"schema-old-{index}", kind))
        before = _rows(conn)
        conn.execute(MIGRATION.read_text())
        assert _rows(conn) == before
        _insert(conn, _row("schema-new"))
        expanded = _rows(conn)
        assert {row[0]["policy_kind"] for row in expanded} == {*OLD_KINDS, NEW_KIND}
        conn.execute(MIGRATION.read_text())
        assert _rows(conn) == expanded
        with pytest.raises(errors.CheckViolation) as failure, conn.transaction():
            _insert(conn, _row("schema-unknown", "not_a_policy_kind"))
        assert failure.value.diag.constraint_name == "chk_decision_policy_kind"
        assert _rows(conn) == expanded
        # The seven pre-Intake kinds keep their historical mutable behavior.
        for index, _kind in enumerate(OLD_KINDS[:-3]):
            key = f"schema-old-{index}:{TENANT_A}"
            conn.execute(*_update(key, "change_reason", MARKER + "_EDIT"))
            conn.execute("DELETE FROM workflow.decision_policies WHERE policy_version_id = %s", (key,))
        assert len(_rows(conn)) == 4


def test_all_protected_fields_kind_escape_and_entry_are_immutable(registry_db):
    mutations = {
        "policy_version_id": "schema-rewritten:" + TENANT_A,
        "policy_label": "schema-rewritten",
        "policy_id": "schema-other-id",
        "policy_version": "schema-only-v2",
        "policy_kind": "forecast_alert",
        "tenant_id": TENANT_B,
        "effective_from": "2999-01-02T00:00:00+00:00",
        "owner_role": MARKER + "_OTHER",
        "approved_by": MARKER + "_OTHER",
        "approved_at": "2999-01-02T00:00:00+00:00",
        "input_contract": MARKER + "_OTHER",
        "output_contract": MARKER + "_OTHER",
        "change_reason": MARKER + "_OTHER",
        "rollback_policy_version": "schema-nonexistent:" + TENANT_A,
        "parameters": {"enabled": False, "schema_compat_only": True, "changed": True},
        "declared_inputs": [MARKER + "_OTHER"],
    }
    with registry_db.connect() as conn:
        conn.execute(MIGRATION.read_text())
        for index, kind in enumerate(PROTECTED_KINDS):
            row = _row(f"schema-immutable-{index}", kind)
            _insert(conn, row)
            before = _rows(conn)
            for column, value in mutations.items():
                statement, params = _update(row["policy_version_id"], column, value)
                _reject(conn, statement, params, "chk_intake_policy_version_immutable")
                assert _rows(conn) == before
            _reject(
                conn, "DELETE FROM workflow.decision_policies WHERE policy_version_id = %s",
                (row["policy_version_id"],), "chk_intake_policy_version_immutable",
            )
            mutable = _row(f"schema-enter-{index}", "forecast_alert")
            _insert(conn, mutable)
            before = _rows(conn)
            statement, params = _update(mutable["policy_version_id"], "policy_kind", kind)
            _reject(conn, statement, params, "chk_intake_policy_version_immutable")
            assert _rows(conn) == before


def test_protected_windows_close_once_and_retain_history(registry_db):
    with registry_db.connect() as conn:
        conn.execute(MIGRATION.read_text())
        for index, kind in enumerate(PROTECTED_KINDS):
            row = _row(f"schema-close-{index}", kind)
            _insert(conn, row)
            before = _rows(conn)
            statement, params = _update(
                row["policy_version_id"], "effective_to", "2999-06-01T00:00:00+00:00"
            )
            conn.execute(statement, params)
            closed = _rows(conn)
            assert conn.execute(
                "SELECT effective_to = TIMESTAMPTZ '2999-06-01T00:00:00+00:00' "
                "FROM workflow.decision_policies WHERE policy_version_id = %s",
                (row["policy_version_id"],),
            ).fetchone() == (True,)
            assert [{k: v for k, v in r[0].items() if k != "effective_to"} for r in closed] == [
                {k: v for k, v in r[0].items() if k != "effective_to"} for r in before
            ]
            conn.execute(statement, params)  # An unchanged closed row is still allowed.
            assert _rows(conn) == closed
            for value in (None, "2999-07-01T00:00:00+00:00", "2999-05-01T00:00:00+00:00"):
                statement, params = _update(row["policy_version_id"], "effective_to", value)
                _reject(conn, statement, params, "chk_intake_policy_version_immutable")
                assert _rows(conn) == closed
            _insert(conn, _row(
                f"schema-successor-{index}", kind, policy_id=row["policy_id"],
                policy_version="schema-only-v2", effective_from="2999-06-01T00:00:00+00:00",
                rollback_policy_version=row["policy_version_id"],
            ))
            assert conn.execute(
                "SELECT count(*) FROM workflow.decision_policies WHERE policy_id = %s",
                (row["policy_id"],),
            ).fetchone() == (2,)


def test_tenant_identity_window_and_other_constraints_remain_enforced(registry_db):
    with registry_db.connect() as conn:
        conn.execute(MIGRATION.read_text())
        foreign = _row("schema-foreign", tenant_id=TENANT_B)
        _insert(conn, foreign)
        _insert(conn, _row("schema-active", policy_id="schema-shared-id"))
        cases = (
            ({"policy_version_id": "unqualified"}, "chk_decision_policy_version_id_format"),
            ({"policy_label": "schema:separator"}, "chk_decision_policy_label"),
            ({"policy_version_id": f"schema-invalid:{TENANT_B}"}, "chk_decision_policy_version_id_format"),
            ({"rollback_policy_version": foreign["policy_version_id"]}, "fk_decision_policy_rollback_tenant"),
            ({"effective_to": "2998-12-31T00:00:00+00:00"}, "chk_decision_policy_window"),
            ({"change_reason": ""}, "chk_decision_policy_reason"),
            ({"declared_inputs": []}, "chk_decision_policy_inputs"),
            ({"parameters": []}, "chk_decision_policy_params"),
            ({"policy_id": "schema-shared-id", "policy_version": "schema-only-v2"}, "idx_decision_policy_active"),
        )
        before = _rows(conn)
        for overrides, constraint in cases:
            with pytest.raises(errors.IntegrityError) as failure, conn.transaction():
                _insert(conn, _row("schema-invalid", **overrides))
            assert failure.value.diag.constraint_name == constraint
            assert _rows(conn) == before


def test_wholly_absent_registry_is_a_rerunnable_no_op(intake_blank_db):
    with intake_blank_db.connect() as conn:
        for _ in range(2):
            conn.execute(MIGRATION.read_text())
            assert conn.execute("SELECT to_regnamespace('workflow')").fetchone() == (None,)
        conn.execute("CREATE SCHEMA workflow")
        conn.execute(MIGRATION.read_text())
        assert conn.execute("SELECT to_regclass('workflow.decision_policies')").fetchone() == (None,)
        assert conn.execute("SELECT to_regprocedure('workflow.guard_intake_policy_version()')").fetchone() == (None,)


def _schema_state(conn):
    registry = conn.execute("SELECT to_regclass('workflow.decision_policies')::oid").fetchone()[0]
    return (
        registry,
        conn.execute(
            "SELECT attname, atttypid, atttypmod, attnotnull, attisdropped FROM pg_attribute "
            "WHERE attrelid = %s AND attnum > 0 ORDER BY attnum", (registry,),
        ).fetchall(),
        conn.execute(
            "SELECT oid, conname, convalidated, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s ORDER BY oid", (registry,),
        ).fetchall(),
        conn.execute(
            "SELECT oid, tgenabled, pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid = %s ORDER BY oid", (registry,),
        ).fetchall(),
        conn.execute(
            "SELECT p.oid, pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'workflow' AND p.proname = 'guard_intake_policy_version' "
            "ORDER BY p.oid"
        ).fetchall(),
        _rows(conn) if registry else None,
    )


@pytest.mark.parametrize("damage", (
    "missing_column", "wrong_column_type", "missing_kind_constraint",
    "unvalidated_kind_constraint", "wrong_kind_vocabulary", "disabled_trigger",
    "wrong_trigger_events", "wrong_trigger_function", "changed_guard_body",
    "missing_window_constraint", "orphaned_guard",
))
def test_partial_or_incompatible_registry_refuses_without_repair(registry_db, damage):
    with registry_db.connect() as conn:
        _insert(conn, _row("schema-before-refusal", "intake_workflow"))
        if damage == "missing_column":
            conn.execute("ALTER TABLE workflow.decision_policies DROP COLUMN declared_inputs CASCADE")
        elif damage == "wrong_column_type":
            conn.execute("ALTER TABLE workflow.decision_policies ALTER COLUMN owner_role TYPE text")
        elif damage in {"missing_kind_constraint", "unvalidated_kind_constraint", "wrong_kind_vocabulary"}:
            definition = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'workflow.decision_policies'::regclass AND conname = 'chk_decision_policy_kind'"
            ).fetchone()[0]
            conn.execute("ALTER TABLE workflow.decision_policies DROP CONSTRAINT chk_decision_policy_kind")
            if damage == "unvalidated_kind_constraint":
                conn.execute(
                    "ALTER TABLE workflow.decision_policies ADD CONSTRAINT chk_decision_policy_kind "
                    + definition + " NOT VALID"
                )
            elif damage == "wrong_kind_vocabulary":
                conn.execute(
                    "ALTER TABLE workflow.decision_policies ADD CONSTRAINT chk_decision_policy_kind CHECK (true)"
                )
        elif damage == "disabled_trigger":
            conn.execute("ALTER TABLE workflow.decision_policies DISABLE TRIGGER trg_intake_policy_version_immutable")
        elif damage in {"wrong_trigger_events", "wrong_trigger_function"}:
            conn.execute("DROP TRIGGER trg_intake_policy_version_immutable ON workflow.decision_policies")
            if damage == "wrong_trigger_events":
                conn.execute(
                    "CREATE TRIGGER trg_intake_policy_version_immutable BEFORE UPDATE ON workflow.decision_policies "
                    "FOR EACH ROW EXECUTE FUNCTION workflow.guard_intake_policy_version()"
                )
            else:
                conn.execute(
                    "CREATE FUNCTION workflow.schema_compat_other_guard() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN RETURN NEW; END; $$"
                )
                conn.execute(
                    "CREATE TRIGGER trg_intake_policy_version_immutable BEFORE UPDATE OR DELETE "
                    "ON workflow.decision_policies FOR EACH ROW EXECUTE FUNCTION workflow.schema_compat_other_guard()"
                )
        elif damage == "changed_guard_body":
            conn.execute(
                "CREATE OR REPLACE FUNCTION workflow.guard_intake_policy_version() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END; $$"
            )
        elif damage == "missing_window_constraint":
            conn.execute("ALTER TABLE workflow.decision_policies DROP CONSTRAINT chk_decision_policy_window")
        else:
            assert damage == "orphaned_guard"
            conn.execute("DROP TABLE workflow.decision_policies CASCADE")
        before = _schema_state(conn)
        with pytest.raises(errors.ObjectNotInPrerequisiteState) as failure, conn.transaction():
            conn.execute(MIGRATION.read_text())
        assert failure.value.sqlstate == "55000"
        assert "partially installed or incompatible" in str(failure.value)
        assert _schema_state(conn) == before
