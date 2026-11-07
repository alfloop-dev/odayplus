"""Assisted capture requires current approval and never grants retrieval."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.external_data.application.source_snapshots import (
    SourcePolicyViolation,
    SourceSnapshotService,
)
from tests.security.test_native_snapshot_policy_authority import (
    NativePostgresEngine,
    approved_policy,
)


@pytest.mark.parametrize("field,value", [
    ("legal_approval_ref", None), ("legal_approval_ref", " "),
    ("license_approval_ref", None), ("license_approval_ref", ""),
    ("policy_owner_subject_id", "00000000-0000-0000-0000-000000000000"),
    ("policy_owner_subject_id", "system"), ("version", 0), ("version", True),
    ("review_expires_at", None), ("review_expires_at", "invalid"),
    ("review_expires_at", datetime(2020, 1, 1, tzinfo=UTC)),
    ("review_expires_at", datetime(2090, 1, 1)),
    ("kill_switch", None), ("production_enabled", "true"),
])
def test_native_assisted_policy_requires_current_complete_approval(field, value):
    row = {**approved_policy(), "retrieval_mode": "ASSISTED_ENTRY_ONLY", field: value}
    assert SourceSnapshotService(NativePostgresEngine(row), object()).check_source_policy(
        str(uuid4()), "synthetic-manual-source",
    ) == "POLICY_UNKNOWN"


def test_current_assisted_approval_retains_its_retrieval_prohibition():
    row = {**approved_policy(), "retrieval_mode": "ASSISTED_ENTRY_ONLY"}
    assert SourceSnapshotService(NativePostgresEngine(row), object()).check_source_policy(
        str(uuid4()), "synthetic-manual-source",
    ) == "ASSISTED_ENTRY_ONLY"


@pytest.mark.parametrize("method", ["SERVER_RETRIEVAL", "APPROVED_FEED"])
def test_assisted_policy_never_authorizes_automatic_capture(method):
    class NoObjectWrites:
        def upload_object(self, **_kwargs):
            pytest.fail("Assisted policy cannot authorize an automatic capture")

    row = {**approved_policy(), "retrieval_mode": "ASSISTED_ENTRY_ONLY"}
    with pytest.raises(SourcePolicyViolation, match="ASSISTED_ENTRY_ONLY"):
        SourceSnapshotService(NativePostgresEngine(row), NoObjectWrites()).create_snapshot(
            tenant_id=str(uuid4()), intake_id=str(uuid4()), source_id="synthetic-manual-source",
            raw_data=b"synthetic supplied evidence", original_url=None, canonical_url=None,
            media_type="application/json", capture_method=method, retention_class="STANDARD",
            encryption_key_ref="synthetic-key", observed_at=datetime.now(UTC),
            captured_at=datetime.now(UTC), bucket="taiwan-snapshots",
        )
