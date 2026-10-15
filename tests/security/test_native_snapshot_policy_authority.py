"""A native policy read cannot inherit a fixture grant after SQL fails."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from modules.external_data.application.source_snapshots import (
    SourcePolicyViolation,
    SourceSnapshotService,
)


def approved_policy():
    return {
        "retrieval_mode": "APPROVED_RETRIEVAL",
        "kill_switch": False,
        "production_enabled": True,
        "version": 3,
        "policy_owner_subject_id": str(uuid4()),
        "legal_approval_ref": "synthetic-legal-receipt",
        "license_approval_ref": "synthetic-license-receipt",
        "review_expires_at": datetime.now(UTC) + timedelta(hours=1),
    }


class NativePostgresEngine:
    dialect = "postgresql"

    def __init__(self, row=None, error=None):
        self.row, self.error, self.reads = row, error, []

    def query_one(self, sql, params):
        self.reads.append(params)
        if self.error:
            raise self.error
        return self.row


@pytest.mark.parametrize("source_id", ["src-1", "SRC-591", "only-in-memory"])
def test_native_missing_policy_never_inherits_fixture_or_static_grant(source_id):
    engine = NativePostgresEngine()
    service = SourceSnapshotService(engine, object())
    service._in_memory_registry[source_id] = approved_policy()
    assert service.check_source_policy(str(uuid4()), source_id) == "POLICY_UNKNOWN"
    assert engine.reads == [(source_id,)]


def test_native_policy_query_failure_is_visible_and_does_not_fall_back():
    service = SourceSnapshotService(NativePostgresEngine(error=OSError("unavailable")), object())
    with pytest.raises(SourcePolicyViolation, match="POLICY_UNKNOWN"):
        service.check_source_policy(str(uuid4()), "src-1")


def test_runtime_postgres_engine_reads_the_current_registry_policy():
    engine = NativePostgresEngine(approved_policy())
    assert SourceSnapshotService(engine, object()).check_source_policy(str(uuid4()), "src-1") == "APPROVED_RETRIEVAL"
    assert engine.reads == [("src-1",)]


@pytest.mark.parametrize("field,value", [
    ("legal_approval_ref", None), ("legal_approval_ref", " "),
    ("license_approval_ref", None), ("license_approval_ref", ""),
    ("policy_owner_subject_id", "00000000-0000-0000-0000-000000000000"),
    ("policy_owner_subject_id", "system"),
    ("version", 0), ("version", True),
    ("review_expires_at", None), ("review_expires_at", "invalid"),
    ("review_expires_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ("review_expires_at", datetime(2090, 1, 1)),
    ("kill_switch", None), ("kill_switch", "false"),
    ("production_enabled", None), ("production_enabled", "true"),
    ("retrieval_mode", "unexpected"),
])
def test_native_retrieval_requires_current_well_formed_approval(field, value):
    row = {**approved_policy(), field: value}
    assert SourceSnapshotService(NativePostgresEngine(row), object()).check_source_policy(
        str(uuid4()), "src-1",
    ) != "APPROVED_RETRIEVAL"


@pytest.mark.parametrize("field", ["kill_switch", "production_enabled"])
def test_native_revocation_overrides_recorded_retrieval_approval(field):
    row = {**approved_policy(), field: field == "kill_switch"}
    assert SourceSnapshotService(NativePostgresEngine(row), object()).check_source_policy(
        str(uuid4()), "src-1",
    ) == "SOURCE_BLOCKED"


@pytest.mark.parametrize("error", [None, OSError("unavailable")])
def test_unavailable_native_policy_stops_before_object_upload(error):
    class NoObjectWrites:
        def upload_object(self, **_kwargs):
            pytest.fail("A missing native policy must not upload an object")

    service = SourceSnapshotService(NativePostgresEngine(error=error), NoObjectWrites())
    with pytest.raises(SourcePolicyViolation, match="POLICY_UNKNOWN"):
        service.create_snapshot(
            tenant_id=str(uuid4()), intake_id=str(uuid4()), source_id="src-1",
            raw_data=b"synthetic evidence", original_url="https://example.test/listing",
            canonical_url=None, media_type="text/html", capture_method="SERVER_RETRIEVAL",
            retention_class="STANDARD", encryption_key_ref="synthetic-key-ref",
            observed_at=datetime.now(UTC), captured_at=datetime.now(UTC), bucket="taiwan-snapshots",
        )
