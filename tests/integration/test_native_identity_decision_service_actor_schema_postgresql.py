"""Actual existing-table actor migration and old installed-stack forward upgrade.

Synthetic database rows exercise storage contracts only; none grants source use,
service execution, human confirmation, review approval or release activation.
"""
from uuid import uuid4

import pytest
from psycopg import errors, sql

from shared.infrastructure.persistence import assisted_listing_intake as migration

pytestmark = pytest.mark.requires_live_env
SERVICE = "native-intake-identity"


def _apply(connection):
    connection.execute(migration._without_transaction_control(migration.upgrade_statements()[4][1]))


def _insert(connection, ids, **changes):
    row = {"match_decision_id": str(uuid4()), "tenant_id": ids["tenant"], "match_case_id": ids["case"],
        "decision_type": "CREATE", "status": "DRAFT", "proposer_subject_id": ids["human"],
        "reason": "SYNTHETIC_SCHEMA_ONLY_NOT_APPROVAL", "before_graph": "{}", "after_graph": "{}"}
    row.update(changes)
    connection.execute(sql.SQL("INSERT INTO identity.match_decisions ({}) VALUES ({})").format(
        sql.SQL(",").join(map(sql.Identifier, row)), sql.SQL(",").join(sql.Placeholder() for _ in row)), tuple(row.values()))
    return row["match_decision_id"]


@pytest.fixture
def old_decisions(intake_blank_db):
    ids = {key: str(uuid4()) for key in ("tenant", "intake", "case", "human", "reviewer")}
    with intake_blank_db.connect() as connection:
        # Real original DDL, independent of the appended fifth step.
        for _, statement in migration.upgrade_statements()[:4]:
            connection.execute(migration._without_transaction_control(statement))
        connection.execute("INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,"
            "processing_state,correlation_id) VALUES (%s,%s,%s,'MANUAL','MATCHING',%s)",
            (ids["intake"], ids["tenant"], ids["human"], str(uuid4())))
        connection.execute("INSERT INTO identity.match_cases (match_case_id,tenant_id,intake_id,outcome,status,"
            "confidence,proposed_by) VALUES (%s,%s,%s,'EXACT_DUPLICATE','PROPOSED',1,'synthetic-matcher')",
            (ids["case"], ids["tenant"], ids["intake"]))
        ids["decision"] = _insert(connection, ids)
    return intake_blank_db, ids


def _snapshot(connection):
    return {
        "columns": connection.execute("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns "
            "WHERE table_schema='identity' AND table_name='match_decisions' ORDER BY ordinal_position").fetchall(),
        "constraints": connection.execute("SELECT conname,pg_get_constraintdef(oid),convalidated FROM pg_constraint "
            "WHERE conrelid='identity.match_decisions'::regclass ORDER BY conname").fetchall(),
        "triggers": connection.execute("SELECT tgname,tgenabled,pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid='identity.match_decisions'::regclass AND NOT tgisinternal ORDER BY tgname").fetchall(),
        "function": connection.execute("SELECT prosrc,prosecdef,proconfig FROM pg_proc "
            "WHERE oid=to_regprocedure('identity.protect_match_decision_proposer_actor()')").fetchall(),
        "rows": connection.execute("SELECT to_jsonb(d) FROM identity.match_decisions d ORDER BY match_decision_id").fetchall(),
    }


def test_original_human_rows_survive_actor_extension_and_own_replay(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        before = _snapshot(connection)["rows"]
        _apply(connection)
        after = _snapshot(connection)
        assert [(dict(row[0], proposer_service_principal=None),) for row in before] == after["rows"]
        _apply(connection)
        assert _snapshot(connection) == after


def test_service_duplicate_and_original_human_lifecycles_preserve_recorded_actor(old_decisions):
    database, ids = old_decisions
    with database.connect() as connection:
        _apply(connection)
        service_id = _insert(connection, ids, decision_type="DUPLICATE", status="APPROVED",
            proposer_subject_id=None, proposer_service_principal=SERVICE)
        connection.execute("UPDATE identity.match_decisions SET status='EXECUTED',executed_at=clock_timestamp(),version=version+1 "
            "WHERE match_decision_id=%s", (service_id,))
        connection.execute("UPDATE identity.match_decisions SET status='APPROVED',reviewer_subject_id=%s,version=version+1 "
            "WHERE match_decision_id=%s", (ids["reviewer"], ids["decision"]))
        for decision_id, changes in ((service_id, {"proposer_subject_id": ids["human"], "proposer_service_principal": None}),
                (ids["decision"], {"proposer_subject_id": ids["reviewer"]})):
            before = _snapshot(connection)
            with pytest.raises(errors.CheckViolation, match="recorded decision proposer"), connection.transaction():
                connection.execute(sql.SQL("UPDATE identity.match_decisions SET {} WHERE match_decision_id=%s").format(
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(name)) for name in changes)),
                    (*changes.values(), decision_id))
            assert _snapshot(connection) == before


@pytest.mark.parametrize("changes", [
    {"proposer_subject_id": None},
    {"proposer_service_principal": SERVICE},
    {"proposer_subject_id": None, "proposer_service_principal": "service:invented"},
    {"proposer_subject_id": None, "proposer_service_principal": SERVICE, "decision_type": "MERGE"},
    {"proposer_subject_id": None, "proposer_service_principal": SERVICE, "decision_type": "DUPLICATE", "reviewer_subject_id": "SELF"},
    {"reviewer_subject_id": "SELF"},
])
def test_actor_combinations_cannot_impersonate_humans_or_bypass_independent_review(old_decisions, changes):
    database, ids = old_decisions
    with database.connect() as connection:
        _apply(connection)
        before = _snapshot(connection)
        changes = {key: ids["human"] if value == "SELF" else value for key, value in changes.items()}
        with pytest.raises(errors.CheckViolation), connection.transaction():
            _insert(connection, ids, **changes)
        assert _snapshot(connection) == before


@pytest.mark.parametrize("tamper", [
    "ALTER TABLE identity.match_decisions DROP CONSTRAINT ck_match_decisions_proposer_actor",
    "ALTER TABLE identity.match_decisions DROP CONSTRAINT ck_match_decisions_proposer_actor; "
        "ALTER TABLE identity.match_decisions ADD CONSTRAINT ck_match_decisions_proposer_actor CHECK(true)",
    "ALTER TABLE identity.match_decisions DISABLE TRIGGER trg_match_decision_proposer_immutable",
    "CREATE OR REPLACE FUNCTION identity.protect_match_decision_proposer_actor() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
])
def test_tampered_actor_contract_is_refused_without_repair(old_decisions, tamper):
    database, _ = old_decisions
    with database.connect() as connection:
        _apply(connection)
        connection.execute(tamper)
        before = _snapshot(connection)
        with pytest.raises(errors.RaiseException, match="incompatible"), connection.transaction():
            _apply(connection)
        assert _snapshot(connection) == before


def test_partial_extension_is_refused_atomically(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        connection.execute("ALTER TABLE identity.match_decisions ADD COLUMN proposer_service_principal varchar(256)")
        before = _snapshot(connection)
        with pytest.raises(errors.RaiseException, match="incompatible"), connection.transaction():
            _apply(connection)
        assert _snapshot(connection) == before


def _record_old_stack(connection):
    connection.execute("CREATE SCHEMA odp_runtime")
    connection.execute("CREATE TABLE odp_runtime.assisted_intake_schema_migrations (migration_name text PRIMARY KEY,"
        "manifest_sha256 char(64) NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())")
    connection.execute("INSERT INTO odp_runtime.assisted_intake_schema_migrations (migration_name,manifest_sha256) "
        "VALUES ('001-004',%s)", (migration.manifest_checksum(migration.migration_steps()[:4]),))


def test_recorded_populated_four_step_install_upgrades_without_reapplying_or_rewriting_history(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        _record_old_stack(connection)
        old_receipt = connection.execute("SELECT * FROM odp_runtime.assisted_intake_schema_migrations").fetchone()
        old_rows = _snapshot(connection)["rows"]
    first = migration.apply_upgrade_to_database(database.url())
    assert first == migration.apply_upgrade_to_database(database.url())
    assert first.steps[-2:] == ("005_decision_service_actor.sql", "006_workflow_schedule.sql")
    with database.connect() as connection:
        assert connection.execute("SELECT * FROM odp_runtime.assisted_intake_schema_migrations WHERE migration_name='001-004'").fetchone() == old_receipt
        assert connection.execute("SELECT manifest_sha256 FROM odp_runtime.assisted_intake_schema_migrations WHERE migration_name='001-005'").fetchone() == (migration.manifest_checksum(migration.migration_steps()[:5]),)
        assert connection.execute("SELECT manifest_sha256 FROM odp_runtime.assisted_intake_schema_migrations WHERE migration_name='001-006'").fetchone() == (first.manifest_sha256,)
        assert [(dict(row[0], proposer_service_principal=None),) for row in old_rows] == _snapshot(connection)["rows"]


def test_forward_upgrade_rejects_old_receipt_drift_without_applying_fifth_step(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        _record_old_stack(connection)
        connection.execute("UPDATE odp_runtime.assisted_intake_schema_migrations SET manifest_sha256=%s", ("0" * 64,))
        before = _snapshot(connection)
    with pytest.raises(migration.AssistedIntakeSchemaError, match="manifest"):
        migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        assert _snapshot(connection) == before
        assert connection.execute("SELECT migration_name FROM odp_runtime.assisted_intake_schema_migrations").fetchall() == [("001-004",)]


def test_recorded_current_stack_refuses_physical_actor_drift_instead_of_repairing_it(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        _record_old_stack(connection)
    migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        connection.execute("ALTER TABLE identity.match_decisions DISABLE TRIGGER trg_match_decision_proposer_immutable")
        before = _snapshot(connection)
    with pytest.raises(migration.AssistedIntakeSchemaError, match="Unable to apply"):
        migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        assert _snapshot(connection) == before


@pytest.mark.parametrize("receipt_change", ["unknown", "missing_prefix"])
def test_installed_receipts_cannot_claim_unknown_or_incomplete_prefixes(old_decisions, receipt_change):
    database, _ = old_decisions
    with database.connect() as connection:
        _record_old_stack(connection)
        if receipt_change == "unknown":
            connection.execute("INSERT INTO odp_runtime.assisted_intake_schema_migrations (migration_name,manifest_sha256) "
                "VALUES ('001-099',%s)", ("a" * 64,))
        else:
            connection.execute("UPDATE odp_runtime.assisted_intake_schema_migrations SET migration_name='001-005',manifest_sha256=%s",
                (migration.manifest_checksum(migration.migration_steps()[:5]),))
        before = _snapshot(connection)
        receipts = connection.execute("SELECT * FROM odp_runtime.assisted_intake_schema_migrations ORDER BY migration_name").fetchall()
    with pytest.raises(migration.AssistedIntakeSchemaError, match="manifest"):
        migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        assert _snapshot(connection) == before
        assert connection.execute("SELECT * FROM odp_runtime.assisted_intake_schema_migrations ORDER BY migration_name").fetchall() == receipts


def test_current_receipt_cannot_authorize_recreation_of_a_removed_actor_extension(old_decisions):
    database, _ = old_decisions
    with database.connect() as connection:
        _record_old_stack(connection)
    migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_match_decision_proposer_immutable ON identity.match_decisions")
        connection.execute("DROP FUNCTION identity.protect_match_decision_proposer_actor()")
        connection.execute("ALTER TABLE identity.match_decisions DROP COLUMN proposer_service_principal CASCADE")
        connection.execute("ALTER TABLE identity.match_decisions ALTER COLUMN proposer_subject_id SET NOT NULL")
        before = _snapshot(connection)
    with pytest.raises(migration.AssistedIntakeSchemaError, match="Recorded decision service actor schema is missing"):
        migration.apply_upgrade_to_database(database.url())
    with database.connect() as connection:
        assert _snapshot(connection) == before
