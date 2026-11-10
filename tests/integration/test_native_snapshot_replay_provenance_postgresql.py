"""Native retries must verify immutable evidence, without new orphan writes."""

from hashlib import sha256

import pytest

from modules.external_data.application.source_snapshots import (
    SnapshotProvenanceError,
    SourceSnapshotService,
)
from shared.infrastructure.object_store.client import InMemoryObjectStore
from tests.integration.test_native_snapshot_sql_owner_postgresql import (
    TRANSPORTS,
    backend,
    snapshot_setup,
)
from tests.security.test_snapshot_object_compensation import object_key, upload

pytestmark = pytest.mark.requires_live_env


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("lying_checksum", [False, True])
def test_native_conflicting_raw_bytes_never_receive_caller_checksum(intake_db, transport, lying_checksum):
    capture = snapshot_setup(intake_db)

    class Store(InMemoryObjectStore):
        def head_object(self, *args):
            row = super().head_object(*args)
            return {**row, "sha256": sha256(capture["raw_data"]).hexdigest()} if lying_checksum else row

    store = Store()
    uri, generation = upload(store, capture, "raw", b"different existing bytes", native=True)
    with backend(intake_db, transport) as connection:
        with pytest.raises(SnapshotProvenanceError):
            SourceSnapshotService(connection, store).create_snapshot(**capture)
    assert store.download_object(capture["tenant_id"], uri, generation=generation) == b"different existing bytes"
    with intake_db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_native_conflicting_redaction_preserves_old_object_and_cleans_new_raw(intake_db, transport):
    capture = {**snapshot_setup(intake_db), "redacted_data": b"requested sanitized bytes"}
    store = InMemoryObjectStore()
    uri, generation = upload(store, capture, "redacted", b"different redaction", native=True)
    with backend(intake_db, transport) as connection:
        with pytest.raises(SnapshotProvenanceError):
            SourceSnapshotService(connection, store).create_snapshot(**capture)
    assert store.list_objects(capture["tenant_id"], capture["bucket"]) == [object_key(capture, "redacted", native=True)]
    assert store.download_object(capture["tenant_id"], uri, generation=generation) == b"different redaction"
    with intake_db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_native_matching_orphan_can_be_recovered_with_actual_generation(intake_db, transport):
    capture = snapshot_setup(intake_db)
    store = InMemoryObjectStore()
    uri, generation = upload(store, capture, "raw", capture["raw_data"], native=True)
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        row = service.get_snapshot(capture["tenant_id"], key)
        assert row["raw_object_uri"] == uri and int(row["object_generation"]) == generation
        assert row["content_sha256"].strip() == sha256(store.download_object(capture["tenant_id"], uri, generation)).hexdigest()


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_native_committed_replay_keeps_recorded_bucket_without_uploads(intake_db, transport):
    capture = {**snapshot_setup(intake_db), "redacted_data": b"existing redaction"}

    class Store(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.uploads = 0

        def upload_object(self, *args, **kwargs):
            self.uploads += 1
            return super().upload_object(*args, **kwargs)

    store = Store()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        original = service.get_snapshot(capture["tenant_id"], key)
        before = store.uploads
        assert service.create_snapshot(**{**capture, "bucket": "tw-intake-snapshots"}) == key
        assert store.uploads == before
        assert service.get_snapshot(capture["tenant_id"], key) == original
    assert store.list_objects(capture["tenant_id"], "tw-intake-snapshots") == []


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("change", ["missing", "replacement"])
def test_native_committed_replay_does_not_invent_a_replacement_generation(intake_db, transport, change):
    capture = snapshot_setup(intake_db)
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        row = service.get_snapshot(capture["tenant_id"], key)
        if change == "missing":
            store.delete_object(capture["tenant_id"], row["raw_object_uri"], if_generation_match=int(row["object_generation"]))
        else:
            store.upload_object(
                capture["tenant_id"], capture["bucket"], object_key(capture, "raw", native=True),
                capture["raw_data"], capture["media_type"], if_generation_match=int(row["object_generation"]),
            )
        with pytest.raises(SnapshotProvenanceError):
            service.create_snapshot(**capture)
        assert service.get_snapshot(capture["tenant_id"], key) == row
        if change == "missing":
            assert not store.list_objects(capture["tenant_id"], capture["bucket"])
        else:
            assert store.head_object(capture["tenant_id"], row["raw_object_uri"])["generation"] == int(row["object_generation"]) + 1


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("existing_redaction", [False, True])
def test_native_committed_replay_cannot_add_or_substitute_redacted_bytes(intake_db, transport, existing_redaction):
    capture = snapshot_setup(intake_db)
    if existing_redaction:
        capture["redacted_data"] = b"original sanitized bytes"
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        row = service.get_snapshot(capture["tenant_id"], key)
        before = store.list_objects(capture["tenant_id"], capture["bucket"])
        with pytest.raises(SnapshotProvenanceError):
            service.create_snapshot(**{**capture, "redacted_data": b"different new redaction"})
        assert store.list_objects(capture["tenant_id"], capture["bucket"]) == before
        assert service.get_snapshot(capture["tenant_id"], key) == row


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("recorded_generation", [None, 0, -1])
def test_nullable_recorded_generation_is_not_an_immutable_anchor(intake_db, transport, recorded_generation):
    capture = snapshot_setup(intake_db)
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        with intake_db.connect() as editor:
            editor.execute("UPDATE intake.source_snapshots SET object_generation=%s WHERE source_snapshot_id=%s",
                           (recorded_generation, key))
        row = service.get_snapshot(capture["tenant_id"], key)
        with pytest.raises(SnapshotProvenanceError):
            service.create_snapshot(**capture)
        assert service.get_snapshot(capture["tenant_id"], key) == row
        assert store.download_object(capture["tenant_id"], row["raw_object_uri"], generation=1) == capture["raw_data"]


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_late_metadata_adoption_cleans_only_unreferenced_new_objects(intake_db, transport, cleanup_fails, caplog):
    capture = snapshot_setup(intake_db)

    class Store(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.injected = False

        def upload_object(self, *args, **kwargs):
            result = super().upload_object(*args, **kwargs)
            if kwargs.get("bucket") == "tw-intake-snapshots" and not self.injected:
                self.injected = True
                with backend(intake_db, transport) as competing_connection:
                    SourceSnapshotService(competing_connection, self).create_snapshot(**capture)
            return result

        def delete_object(self, tenant_id, uri, if_generation_match=None):
            if cleanup_fails and uri.startswith("gs://tw-intake-snapshots/"):
                raise OSError("synthetic cleanup unavailable")
            return super().delete_object(tenant_id, uri, if_generation_match)

    store = Store()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**{**capture, "bucket": "tw-intake-snapshots"})
        row = service.get_snapshot(capture["tenant_id"], key)
        assert row["raw_object_uri"].startswith("gs://taiwan-snapshots/")
        assert store.download_object(capture["tenant_id"], row["raw_object_uri"],
                                     generation=int(row["object_generation"])) == capture["raw_data"]
    if cleanup_fails:
        assert "Snapshot object compensation failed" in caplog.text
        assert store.list_objects(capture["tenant_id"], "tw-intake-snapshots") == [object_key(capture, "raw", native=True)]
    else:
        assert store.list_objects(capture["tenant_id"], "tw-intake-snapshots") == []
    with intake_db.connect() as observer:
        assert observer.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 1
