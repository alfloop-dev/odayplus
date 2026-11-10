"""Verify current source policy against both supported native SQL transports."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.requires_live_env


def seed_policy(conn, source_id="src-1"):
    conn.execute(
        "INSERT INTO intake.source_registry (source_id,display_name,allowed_hosts,"
        "canonicalization_rule_version,retrieval_mode,policy_owner_subject_id,"
        "legal_approval_ref,license_approval_ref,review_expires_at,kill_switch,production_enabled) "
        "VALUES (%s,'Synthetic native policy',ARRAY['example.test'],'synthetic-v1',"
        "'APPROVED_RETRIEVAL',%s,'synthetic-legal','synthetic-license',%s,false,true)",
        (source_id, str(uuid4()), datetime.now(UTC) + timedelta(hours=1)),
    )


@pytest.mark.parametrize("transport", ["raw_connection", "runtime_engine"])
def test_native_policy_uses_current_sql_row_without_fixture_fallback(intake_db, transport):
    tenant = str(uuid4())
    with intake_db.connect() as conn:
        engine = PostgresEngine(intake_db.url(), bootstrap=False, validate_schema=False)
        try:
            service = SourceSnapshotService(conn if transport == "raw_connection" else engine, object())
            service._in_memory_registry["src-1"] = {
                "retrieval_mode": "APPROVED_RETRIEVAL", "kill_switch": False, "production_enabled": True,
            }
            assert service.check_source_policy(tenant, "src-1") == "POLICY_UNKNOWN"
            seed_policy(conn)
            assert service.check_source_policy(tenant, "src-1") == "APPROVED_RETRIEVAL"
            conn.execute("UPDATE intake.source_registry SET kill_switch=true WHERE source_id='src-1'")
            assert service.check_source_policy(tenant, "src-1") == "SOURCE_BLOCKED"
            conn.execute("UPDATE intake.source_registry SET kill_switch=false,production_enabled=false WHERE source_id='src-1'")
            assert service.check_source_policy(tenant, "src-1") == "SOURCE_BLOCKED"
            conn.execute("UPDATE intake.source_registry SET production_enabled=true,legal_approval_ref=NULL WHERE source_id='src-1'")
            assert service.check_source_policy(tenant, "src-1") == "POLICY_UNKNOWN"
            conn.execute("UPDATE intake.source_registry SET legal_approval_ref='synthetic-legal',review_expires_at=now()-interval '1 second' WHERE source_id='src-1'")
            assert service.check_source_policy(tenant, "src-1") == "POLICY_UNKNOWN"
        finally:
            engine.close()


def test_raw_policy_read_preserves_uncommitted_native_transaction(intake_db):
    with intake_db.connect(autocommit=False) as conn:
        seed_policy(conn)
        service = SourceSnapshotService(conn, object())
        assert service.check_source_policy(str(uuid4()), "src-1") == "APPROVED_RETRIEVAL"
        with intake_db.connect() as observer:
            assert observer.execute("SELECT count(*) FROM intake.source_registry").fetchone()[0] == 0
        conn.rollback()
    with intake_db.connect() as observer:
        assert observer.execute("SELECT count(*) FROM intake.source_registry").fetchone()[0] == 0


def test_engine_policy_read_shares_and_does_not_commit_native_root(intake_db):
    engine = PostgresEngine(intake_db.url(), bootstrap=False, validate_schema=False)
    try:
        with pytest.raises(RuntimeError, match="rollback marker"), engine.transaction():
            engine.execute(
                "INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
                "retrieval_mode,policy_owner_subject_id,legal_approval_ref,license_approval_ref,"
                "review_expires_at,kill_switch,production_enabled) "
                "VALUES ('src-1','Synthetic','synthetic-v1','APPROVED_RETRIEVAL',?,"
                "'synthetic-legal','synthetic-license',?,false,true)",
                (str(uuid4()), (datetime.now(UTC) + timedelta(hours=1)).isoformat()),
            )
            assert SourceSnapshotService(engine, object()).check_source_policy(str(uuid4()), "src-1") == "APPROVED_RETRIEVAL"
            with intake_db.connect() as observer:
                assert observer.execute("SELECT count(*) FROM intake.source_registry").fetchone()[0] == 0
            raise RuntimeError("rollback marker")
        assert engine.query_one("SELECT count(*) AS count FROM intake.source_registry")["count"] == 0
    finally:
        engine.close()
