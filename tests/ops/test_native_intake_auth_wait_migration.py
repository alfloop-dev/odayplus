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
