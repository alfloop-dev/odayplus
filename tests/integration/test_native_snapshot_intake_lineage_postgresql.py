"""Equal source bytes on separate native Intakes retain separate lineage."""

from hashlib import sha256
from uuid import NAMESPACE_DNS, uuid4, uuid5

import psycopg
import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService
from shared.infrastructure.object_store.client import InMemoryObjectStore
from tests.integration.test_native_snapshot_policy_authority_postgresql import seed_policy
from tests.integration.test_native_snapshot_sql_owner_postgresql import (
    INTAKE_INSERT,
    TRANSPORTS,
    backend,
    intake_params,
    snapshot_setup,
)

pytestmark = pytest.mark.requires_live_env


def another_intake(intake_db, capture):
    intake = str(uuid4())
    with intake_db.connect() as conn:
        conn.execute(INTAKE_INSERT, intake_params(capture["tenant_id"], intake, capture["source_id"]))
    return {**capture, "intake_id": intake}


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_equal_content_on_two_intakes_keeps_both_native_parents(intake_db, transport):
    first = snapshot_setup(intake_db)
    second = another_intake(intake_db, first)
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        left = service.create_snapshot(**first)
        right = service.create_snapshot(**second)
        assert left != right
        for capture, snapshot in [(first, left), (second, right)]:
            row = service.get_snapshot(capture["tenant_id"], snapshot)
            assert str(row["intake_id"]) == capture["intake_id"]
            assert row["content_sha256"].strip() == sha256(capture["raw_data"]).hexdigest()
            assert store.download_object(capture["tenant_id"], row["raw_object_uri"],
                                         generation=int(row["object_generation"])) == capture["raw_data"]
            assert service.create_snapshot(**capture) == snapshot
        assert service.get_snapshot(first["tenant_id"], left)["raw_object_uri"] != service.get_snapshot(first["tenant_id"], right)["raw_object_uri"]
    with intake_db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 2


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_legacy_snapshot_id_replays_only_its_exact_intake(intake_db, transport):
    first = snapshot_setup(intake_db)
    second = another_intake(intake_db, first)
    digest = sha256(first["raw_data"]).hexdigest()
    legacy_id = str(uuid5(NAMESPACE_DNS, f"{first['tenant_id']}:{first['source_id']}:{digest}"))
    store = InMemoryObjectStore()
    uri, generation = store.upload_object(
        first["tenant_id"], first["bucket"], f"tenants/{first['tenant_id']}/snapshots/{legacy_id}/raw",
        first["raw_data"], first["media_type"],
    )
    with intake_db.connect() as conn:
        conn.execute(
            "INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
            "raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,"
            "retention_class,encryption_key_ref,object_generation,residency_mode) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'TW_ONLY')",
            (legacy_id, first["tenant_id"], first["intake_id"], first["source_id"], uri, digest,
             first["media_type"], len(first["raw_data"]), first["captured_at"], first["observed_at"],
             first["capture_method"], first["retention_class"], first["encryption_key_ref"], generation),
        )
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        assert service.create_snapshot(**first) == legacy_id
        right = service.create_snapshot(**second)
        assert right != legacy_id
        assert str(service.get_snapshot(second["tenant_id"], right)["intake_id"]) == second["intake_id"]
        row = service.get_snapshot(first["tenant_id"], legacy_id)
        assert str(row["intake_id"]) == first["intake_id"]
        assert row["raw_object_uri"] == uri and int(row["object_generation"]) == generation
        assert store.download_object(first["tenant_id"], uri, generation=generation) == first["raw_data"]


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_metadata_disappears_after_upload_still_compensates_the_new_generation(intake_db, transport):
    capture = snapshot_setup(intake_db)

    class Store(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.deleted_generations = []

        def upload_object(self, *args, **kwargs):
            result = super().upload_object(*args, **kwargs)
            with intake_db.connect() as conn:
                conn.execute("DROP TABLE intake.source_snapshots CASCADE")
            return result

        def delete_object(self, tenant_id, uri, if_generation_match=None):
            self.deleted_generations.append(if_generation_match)
            return super().delete_object(tenant_id, uri, if_generation_match)

    store = Store()
    with backend(intake_db, transport) as connection:
        with pytest.raises(psycopg.errors.UndefinedTable):
            SourceSnapshotService(connection, store).create_snapshot(**capture)
    assert store.deleted_generations == [1]
    assert store.list_objects(capture["tenant_id"], capture["bucket"]) == []


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_native_namespace_cannot_adopt_a_legacy_source_prefix(intake_db, transport):
    first = snapshot_setup(intake_db)
    second = another_intake(intake_db, first)
    legacy_source = second["intake_id"] + ":" + first["source_id"]
    digest = sha256(first["raw_data"]).hexdigest()
    legacy_id = str(uuid5(NAMESPACE_DNS, f"{first['tenant_id']}:{legacy_source}:{digest}"))
    store = InMemoryObjectStore()
    uri, generation = store.upload_object(
        first["tenant_id"], first["bucket"], f"tenants/{first['tenant_id']}/snapshots/{legacy_id}/raw",
        first["raw_data"], first["media_type"],
    )
    with intake_db.connect() as conn:
        seed_policy(conn, legacy_source)
        conn.execute("UPDATE intake.intakes SET source_id=%s WHERE intake_id=%s", (legacy_source, first["intake_id"]))
        conn.execute(
            "INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
            "raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,capture_method,"
            "retention_class,encryption_key_ref,object_generation,residency_mode) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'TW_ONLY')",
            (legacy_id, first["tenant_id"], first["intake_id"], legacy_source, uri, digest,
             first["media_type"], len(first["raw_data"]), first["captured_at"], first["observed_at"],
             first["capture_method"], first["retention_class"], first["encryption_key_ref"], generation),
        )
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        current = service.create_snapshot(**second)
        assert current != legacy_id
        row = service.get_snapshot(second["tenant_id"], current)
        assert str(row["intake_id"]) == second["intake_id"] and row["source_id"] == second["source_id"]
        prior = service.get_snapshot(first["tenant_id"], legacy_id)
        assert str(prior["intake_id"]) == first["intake_id"] and prior["source_id"] == legacy_source
        assert prior["raw_object_uri"] == uri and int(prior["object_generation"]) == generation
        assert store.download_object(first["tenant_id"], uri, generation=generation) == first["raw_data"]
