"""Native snapshot SQL preserves RLS, transaction ownership and compensation."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_DNS, uuid4, uuid5

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from modules.external_data.application.source_snapshots import (
    SnapshotProvenanceError,
    SourceSnapshotService,
)
from shared.infrastructure.object_store.client import InMemoryObjectStore
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.requires_live_env
TRANSPORTS = ["raw_tuple", "raw_dict", "engine"]
INTAKE_INSERT = (
    "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,"
    "original_url,source_id,processing_state,correlation_id) VALUES (%s,%s,%s,%s,%s,%s,'SUBMITTED',%s)"
)


def intake_params(tenant, intake, source=None):
    return (intake, tenant, str(uuid4()), "URL" if source else "MANUAL",
            "https://example.test/listing" if source else None, source, str(uuid4()))


@contextmanager
def backend(intake_db, transport):
    if transport == "engine":
        engine = PostgresEngine(intake_db.url(), bootstrap=False, validate_schema=False)
        try:
            yield engine
        finally:
            engine.close()
    else:
        with intake_db.connect(**({"row_factory": dict_row} if transport == "raw_dict" else {})) as conn:
            yield conn


def snapshot_setup(intake_db):
    tenant, intake, source = str(uuid4()), str(uuid4()), "synthetic-sql-owner"
    with intake_db.connect() as conn:
        conn.execute(
            "INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
            "retrieval_mode,policy_owner_subject_id,legal_approval_ref,license_approval_ref,review_expires_at,"
            "kill_switch,production_enabled) VALUES (%s,'Synthetic SQL owner','synthetic-v1',"
            "'APPROVED_RETRIEVAL',%s,'synthetic-legal','synthetic-license',%s,false,true)",
            (source, str(uuid4()), datetime.now(UTC) + timedelta(hours=1)),
        )
        conn.execute(INTAKE_INSERT, intake_params(tenant, intake, source))
    return {
        "tenant_id": tenant, "intake_id": intake, "source_id": source,
        "raw_data": b"local synthetic snapshot body", "original_url": "https://example.test/listing",
        "canonical_url": None, "media_type": "text/html", "capture_method": "SERVER_RETRIEVAL",
        "retention_class": "STANDARD", "encryption_key_ref": "synthetic-key",
        "observed_at": datetime.now(UTC), "captured_at": datetime.now(UTC), "bucket": "taiwan-snapshots",
    }


def test_raw_snapshot_write_waits_for_its_owner_to_commit(intake_db):
    tenant, intake = str(uuid4()), str(uuid4())
    with intake_db.connect(autocommit=False) as conn:
        service = SourceSnapshotService(conn, object())
        service._execute(INTAKE_INSERT, intake_params(tenant, intake), tenant_id=tenant)
        with intake_db.connect() as observer:
            assert observer.execute("SELECT count(*) FROM intake.intakes").fetchone()[0] == 0
        conn.rollback()
    with intake_db.connect() as observer:
        assert observer.execute("SELECT count(*) FROM intake.intakes").fetchone()[0] == 0


def test_engine_snapshot_write_rolls_back_with_the_native_root(intake_db):
    tenant, intake = str(uuid4()), str(uuid4())
    with backend(intake_db, "engine") as engine:
        with pytest.raises(RuntimeError, match="owner rollback"), engine.transaction():
            SourceSnapshotService(engine, object())._execute(INTAKE_INSERT, intake_params(tenant, intake), tenant_id=tenant)
            with intake_db.connect() as observer:
                assert observer.execute("SELECT count(*) FROM intake.intakes").fetchone()[0] == 0
            raise RuntimeError("owner rollback")
        assert engine.query_one("SELECT count(*) AS count FROM intake.intakes")["count"] == 0


@pytest.mark.parametrize("transport", ["raw_tuple", "engine"])
def test_snapshot_sql_binds_rls_without_replacing_a_foreign_root(intake_db, transport):
    tenant, other = str(uuid4()), str(uuid4())
    role = "snapshot_reader_" + uuid4().hex
    with intake_db.connect() as owner:
        for target in [tenant, other]:
            owner.execute(INTAKE_INSERT, intake_params(target, str(uuid4())))
        owner.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOBYPASSRLS").format(sql.Identifier(role)))
        owner.execute(sql.SQL("GRANT USAGE ON SCHEMA intake TO {}").format(sql.Identifier(role)))
        owner.execute(sql.SQL("GRANT SELECT ON intake.intakes TO {}").format(sql.Identifier(role)))
        quoted_role = sql.Identifier(role).as_string(owner)
        assert owner.execute("SELECT relrowsecurity FROM pg_class WHERE oid='intake.intakes'::regclass").fetchone()[0]
    try:
        with backend(intake_db, transport) as connection:
            service = SourceSnapshotService(connection, object())
            if transport == "raw_tuple":
                # SET LOCAL must survive until the query even in autocommit mode.
                connection.execute("SET ROLE " + quoted_role)
                rows = service._execute("SELECT tenant_id FROM intake.intakes", tenant_id=tenant)
                assert [str(row["tenant_id"]) for row in rows] == [tenant]
                assert connection.execute("SELECT current_setting('app.tenant_id',true)").fetchone()[0] in {None, ""}
            with connection.transaction():
                if transport == "engine":
                    connection.execute("SET LOCAL ROLE " + quoted_role)
                rows = service._execute("SELECT tenant_id FROM intake.intakes", tenant_id=tenant)
                assert [str(row["tenant_id"]) for row in rows] == [tenant]
                with pytest.raises(SnapshotProvenanceError, match="TENANT_CONTEXT_CONFLICT"):
                    service._execute("SELECT tenant_id FROM intake.intakes", tenant_id=other)
                assert service._execute("SELECT current_setting('app.tenant_id',true) AS tenant")[0]["tenant"] == tenant
            if transport == "raw_tuple":
                connection.execute("RESET ROLE")
            else:
                assert connection.query_one("SELECT current_setting('app.tenant_id',true) AS tenant")["tenant"] in {None, ""}
    finally:
        with intake_db.connect() as owner:
            owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_public_snapshot_capture_reads_and_replays_actual_native_metadata(intake_db, transport):
    kwargs = snapshot_setup(intake_db)
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, InMemoryObjectStore())
        snapshot_id = service.create_snapshot(**kwargs)
        row = service.get_snapshot(kwargs["tenant_id"], snapshot_id)
        assert str(row["source_snapshot_id"]) == snapshot_id
        assert str(row["intake_id"]) == kwargs["intake_id"]
        assert row["content_sha256"].strip() == sha256(kwargs["raw_data"]).hexdigest()
        assert service.create_snapshot(**kwargs) == snapshot_id
        with intake_db.connect() as observer:
            assert observer.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 1


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("preexisting_object", [False, True])
def test_native_lookup_failure_preserves_only_objects_created_by_prior_attempts(intake_db, transport, preexisting_object):
    kwargs = snapshot_setup(intake_db)
    with intake_db.connect() as owner:
        owner.execute("DROP TABLE intake.source_snapshots CASCADE")
    store = InMemoryObjectStore()
    snapshot_id = str(uuid5(NAMESPACE_DNS, f"{kwargs['tenant_id']}:{kwargs['source_id']}:{sha256(kwargs['raw_data']).hexdigest()}"))
    key = f"tenants/{kwargs['tenant_id']}/snapshots/{snapshot_id}/raw"
    if preexisting_object:
        store.upload_object(tenant_id=kwargs["tenant_id"], bucket=kwargs["bucket"], key=key,
                            data=kwargs["raw_data"], content_type="text/html", if_generation_match=0)
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        with pytest.raises(psycopg.errors.UndefinedTable):
            service.create_snapshot(**kwargs)
        assert service._in_memory_snapshots == {}
    assert store.list_objects(kwargs["tenant_id"], kwargs["bucket"]) == ([key] if preexisting_object else [])


def test_raw_sql_failure_leaves_rollback_with_the_existing_owner(intake_db):
    tenant = str(uuid4())
    with intake_db.connect(autocommit=False) as conn:
        conn.execute(INTAKE_INSERT, intake_params(tenant, str(uuid4())))
        with pytest.raises(psycopg.errors.UndefinedTable):
            SourceSnapshotService(conn, object())._execute("SELECT * FROM intake.missing_snapshot_relation", tenant_id=tenant)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM intake.intakes").fetchone()[0] == 0


def test_engine_sql_failure_rolls_back_its_own_short_unit(intake_db):
    with backend(intake_db, "engine") as engine:
        with pytest.raises(psycopg.errors.UndefinedTable):
            SourceSnapshotService(engine, object())._execute("SELECT * FROM intake.missing_snapshot_relation", tenant_id=str(uuid4()))
        assert engine.query_one("SELECT 1 AS value")["value"] == 1
