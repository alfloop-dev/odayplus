"""Persist supplied assisted evidence on the native SQL path, without processing."""

from hashlib import sha256

import pytest

from modules.external_data.application.source_snapshots import (
    SourcePolicyViolation,
    SourceSnapshotService,
)
from shared.infrastructure.object_store.client import InMemoryObjectStore
from tests.integration.test_native_snapshot_sql_owner_postgresql import (
    TRANSPORTS,
    backend,
    snapshot_setup,
)

pytestmark = pytest.mark.requires_live_env


def assisted_capture(intake_db, method):
    capture = {**snapshot_setup(intake_db), "capture_method": method,
               "raw_data": b'{"address":"synthetic manually supplied address"}',
               "media_type": "application/json", "original_url": None, "canonical_url": None}
    with intake_db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET retrieval_mode='ASSISTED_ENTRY_ONLY' WHERE source_id=%s",
                     (capture["source_id"],))
        conn.execute("UPDATE intake.intakes SET intake_method='MANUAL',original_url=NULL WHERE intake_id=%s",
                     (capture["intake_id"],))
    return capture


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("method", ["MANUAL_ENTRY", "OPERATOR_UPLOAD"])
def test_native_assisted_capture_retains_actual_evidence_and_intake(intake_db, transport, method):
    capture = assisted_capture(intake_db, method)
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        row = service.get_snapshot(capture["tenant_id"], key)
        assert row["capture_method"] == method and str(row["intake_id"]) == capture["intake_id"]
        assert row["source_id"] == capture["source_id"]
        assert row["content_sha256"].strip() == sha256(capture["raw_data"]).hexdigest()
        assert store.download_object(capture["tenant_id"], row["raw_object_uri"],
                                     generation=int(row["object_generation"])) == capture["raw_data"]
        assert service.create_snapshot(**capture) == key
    with intake_db.connect() as conn:
        assert conn.execute("SELECT processing_state FROM intake.intakes WHERE intake_id=%s",
                            (capture["intake_id"],)).fetchone()[0] == "SUBMITTED"
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 1


@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("method", ["SERVER_RETRIEVAL", "APPROVED_FEED"])
def test_native_assisted_policy_denies_automatic_capture_before_object_write(intake_db, transport, method):
    capture = assisted_capture(intake_db, method)
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        with pytest.raises(SourcePolicyViolation, match="ASSISTED_ENTRY_ONLY"):
            SourceSnapshotService(connection, store).create_snapshot(**capture)
    assert store.list_objects(capture["tenant_id"], capture["bucket"]) == []
    with intake_db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("transport", ["raw_tuple", "engine"])
@pytest.mark.parametrize("change", ["expiry", "kill_switch", "license"])
def test_native_assisted_replay_rechecks_current_approval_without_deleting_evidence(intake_db, transport, change):
    capture = assisted_capture(intake_db, "MANUAL_ENTRY")
    store = InMemoryObjectStore()
    with backend(intake_db, transport) as connection:
        service = SourceSnapshotService(connection, store)
        key = service.create_snapshot(**capture)
        row = service.get_snapshot(capture["tenant_id"], key)
        with intake_db.connect() as editor:
            update = {"expiry": "review_expires_at=now()-interval '1 second'",
                      "kill_switch": "kill_switch=true", "license": "license_approval_ref=NULL"}[change]
            editor.execute("UPDATE intake.source_registry SET " + update + " WHERE source_id=%s", (capture["source_id"],))
        with pytest.raises(SourcePolicyViolation):
            service.create_snapshot(**capture)
        assert store.download_object(capture["tenant_id"], row["raw_object_uri"],
                                     generation=int(row["object_generation"])) == capture["raw_data"]
