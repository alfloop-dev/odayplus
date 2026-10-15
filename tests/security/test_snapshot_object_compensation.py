"""Snapshot failures may delete only object generations created by that call."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService
from shared.infrastructure.object_store.client import InMemoryObjectStore


@pytest.fixture
def capture():
    return dict(
        tenant_id=str(uuid4()), intake_id=str(uuid4()), source_id="src-1",
        raw_data=b"synthetic raw snapshot", redacted_data=b"synthetic redacted snapshot",
        original_url=None, canonical_url=None, media_type="text/html",
        capture_method="SERVER_RETRIEVAL", retention_class="STANDARD",
        encryption_key_ref="synthetic-key", observed_at=datetime.now(UTC),
        captured_at=datetime.now(UTC), bucket="taiwan-snapshots",
    )


def object_key(capture, kind):
    digest = sha256(capture["raw_data"]).hexdigest()
    snapshot = uuid5(NAMESPACE_DNS, f"{capture['tenant_id']}:{capture['source_id']}:{digest}")
    return f"tenants/{capture['tenant_id']}/snapshots/{snapshot}/{kind}"


def upload(store, capture, kind, data):
    return store.upload_object(
        capture["tenant_id"], capture["bucket"], object_key(capture, kind), data, "text/html",
    )


@pytest.mark.parametrize("error", [OSError("synthetic upload failure"), ValueError("synthetic upload rejection")])
@pytest.mark.parametrize("prior_raw", [False, True])
def test_failed_redacted_upload_cleans_only_the_new_raw_object(capture, error, prior_raw):
    class Store(InMemoryObjectStore):
        def upload_object(self, *args, **kwargs):
            if kwargs.get("key", "").endswith("/redacted"):
                raise error
            return super().upload_object(*args, **kwargs)

    store = Store()
    if prior_raw:
        upload(store, capture, "raw", capture["raw_data"])
    with pytest.raises(type(error)) as failure:
        SourceSnapshotService(None, store).create_snapshot(**capture)
    assert failure.value is error
    assert store.list_objects(capture["tenant_id"], capture["bucket"]) == (
        [object_key(capture, "raw")] if prior_raw else []
    )


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("prior_kind", [None, "raw", "redacted"])
def test_failed_metadata_write_compensates_only_created_generations(capture, replacement, prior_kind):
    error = OSError("synthetic SQL metadata failure")

    class Store(InMemoryObjectStore):
        def __init__(self):
            super().__init__()
            self.deletes = []

        def delete_object(self, tenant_id, uri, if_generation_match=None):
            self.deletes.append((uri.rsplit("/", 1)[1], if_generation_match))
            return super().delete_object(tenant_id, uri, if_generation_match)

    store = Store()
    if prior_kind:
        upload(store, capture, prior_kind, capture[prior_kind + "_data"])

    class FailingMetadata(SourceSnapshotService):
        def _is_sqlite(self):
            return False

        def check_source_policy(self, *_args):
            # This test isolates compensation after policy admission. Native
            # policy and actual PostgreSQL behavior have separate integration cases.
            return "APPROVED_RETRIEVAL"

        def _execute(self, *_args, **_kwargs):
            if replacement:
                for kind in ("raw", "redacted"):
                    store.upload_object(
                        capture["tenant_id"], capture["bucket"], object_key(capture, kind),
                        b"later writer", "text/html", if_generation_match=1,
                    )
            raise error

    service = FailingMetadata(object(), store)
    with pytest.raises(OSError) as failure:
        service.create_snapshot(**capture)
    assert failure.value is error
    assert sorted(store.deletes) == sorted((kind, 1) for kind in ("raw", "redacted") if kind != prior_kind)
    expected = [object_key(capture, kind) for kind in ("raw", "redacted") if replacement or kind == prior_kind]
    assert sorted(store.list_objects(capture["tenant_id"], capture["bucket"])) == sorted(expected)
    if replacement:
        for key in expected:
            assert store.download_object(capture["tenant_id"], f"gs://{capture['bucket']}/{key}") == b"later writer"


def test_cleanup_failure_is_visible_and_preserves_the_original_upload_error(capture, caplog):
    original = OSError("synthetic redacted upload failure")

    class Store(InMemoryObjectStore):
        def upload_object(self, *args, **kwargs):
            if kwargs.get("key", "").endswith("/redacted"):
                raise original
            return super().upload_object(*args, **kwargs)

        def delete_object(self, *_args, **_kwargs):
            raise OSError("synthetic cleanup failure")

    store = Store()
    with pytest.raises(OSError) as failure:
        SourceSnapshotService(None, store).create_snapshot(**capture)
    assert failure.value is original
    assert store.list_objects(capture["tenant_id"], capture["bucket"]) == [object_key(capture, "raw")]
    assert any("Snapshot object compensation failed" in record.message for record in caplog.records)
