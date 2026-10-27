"""Real PostgreSQL expansion; no source approval is created by migration."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL_PATH = ROOT / "infra/db/migrations/000034_native_intake_auth_wait.sql"
CONTRACT_PATH = ROOT / "docs/data/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTH_WAIT_PATCH.sql"
pytestmark = pytest.mark.requires_live_env


def test_additive_schema_preserves_source_flags_and_never_backfills_manual_approval(intake_db):
    with intake_db.connect() as conn:
        conn.execute("INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
                     "retrieval_mode,policy_owner_subject_id) VALUES ('synthetic-auth-source','synthetic',"
                     "'native-url-exact-v2','AUTH_REQUIRED',%s)", (str(uuid4()),))
    assert SQL_PATH.read_bytes() == CONTRACT_PATH.read_bytes()
    for _ in range(2):
        with intake_db.connect() as conn:
            conn.execute(SQL_PATH.read_text())
            assert conn.execute("SELECT retrieval_mode,kill_switch,production_enabled,version,"
                "assisted_entry_approval_ref,assisted_entry_approved_by,assisted_entry_review_expires_at "
                "FROM intake.source_registry WHERE source_id='synthetic-auth-source'").fetchone() == (
                    "AUTH_REQUIRED", True, False, 1, None, None, None)


def test_new_waiting_state_expands_only_declared_states_and_keeps_tenant_RLS(intake_db):
    with intake_db.connect() as conn:
        conn.execute(SQL_PATH.read_text())
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                            "WHERE oid='intake.intakes'::regclass").fetchone() == (True, True)
        conn.execute("INSERT INTO intake.intakes (tenant_id,submitter_subject_id,intake_method,"
                     "processing_state,correlation_id) VALUES (%s,%s,'MANUAL',%s,%s)",
                     (str(uuid4()), str(uuid4()), "AWAITING_SOURCE_AUTHORIZATION", str(uuid4())))
    with pytest.raises(psycopg.errors.CheckViolation), intake_db.connect() as conn:
        conn.execute("INSERT INTO intake.intakes (tenant_id,submitter_subject_id,intake_method,"
                     "processing_state,correlation_id) VALUES (%s,%s,'MANUAL',%s,%s)",
                     (str(uuid4()), str(uuid4()), "UNDECLARED_STATE", str(uuid4())))


@pytest.mark.parametrize("namespace", ["absent", "empty"])
def test_uninstalled_optional_intake_context_is_not_created_or_required(intake_blank_db, namespace):
    with intake_blank_db.connect() as conn:
        assert conn.execute("SELECT to_regnamespace('intake')").fetchone() == (None,)
        if namespace == "empty":
            conn.execute("CREATE SCHEMA intake")
        for _ in range(2):
            conn.execute(SQL_PATH.read_text())
            assert conn.execute("SELECT to_regclass('intake.source_registry'),to_regclass('intake.intakes')").fetchone() == (None, None)
        present = conn.execute("SELECT to_regnamespace('intake') IS NOT NULL").fetchone()[0]
        assert present is (namespace == "empty")


@pytest.mark.parametrize("existing", ["source_registry", "intakes"])
def test_partial_optional_intake_installation_is_rejected_without_mutation(intake_blank_db, existing):
    # These two identifiers are a compiled test parameter set, never input.
    with intake_blank_db.connect() as conn:
        conn.execute("CREATE SCHEMA intake")
        conn.execute("CREATE TABLE intake." + existing + " (marker integer NOT NULL)")
        conn.execute("INSERT INTO intake." + existing + " VALUES (7)")
    with pytest.raises(psycopg.Error) as error, intake_blank_db.connect() as conn:
        conn.execute(SQL_PATH.read_text())
    assert error.value.sqlstate == "55000"
    with intake_blank_db.connect() as conn:
        assert conn.execute("SELECT marker FROM intake." + existing).fetchall() == [(7,)]
        assert conn.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema='intake' AND table_name=%s", (existing,)).fetchall() == [("marker",)]
        other = "intakes" if existing == "source_registry" else "source_registry"
        assert conn.execute("SELECT to_regclass(%s)", ("intake." + other,)).fetchone() == (None,)


def test_optional_context_installed_later_requires_explicit_auth_patch(intake_blank_db):
    with intake_blank_db.connect() as conn:
        conn.execute(SQL_PATH.read_text())
    intake_blank_db.apply_migration()
    with intake_blank_db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='intake' "
                            "AND table_name='source_registry' AND column_name='assisted_entry_approval_ref'").fetchone() == (0,)
        conn.execute(SQL_PATH.read_text())
        assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='intake' "
                            "AND table_name='source_registry' AND column_name IN "
                            "('assisted_entry_approval_ref','assisted_entry_approved_by','assisted_entry_review_expires_at')").fetchone() == (3,)
        assert conn.execute("SELECT convalidated FROM pg_constraint WHERE conrelid='intake.intakes'::regclass "
                            "AND conname='intakes_processing_state_check'").fetchone() == (True,)
