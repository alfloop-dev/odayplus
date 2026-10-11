"""Offline recovery shape/storage contracts; no live execution authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from shared.identity.dev_smoke_recovery import (
    OLD_EXECUTION_ID,
    RecoveryRefused,
    RecoveryReservation,
    RecoveryCapability,
    _FIXED_PLAN,
    validate_recovery_plan,
)

SHA = "a" * 40
MANIFEST = "sha256:" + "b" * 64


def plan(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(UTC)
    return {**_FIXED_PLAN, "execution_id": str(uuid4()), "release_sha": SHA,
            "manifest_digest": MANIFEST, "custodian": "bounded-custodian",
            "expires_at": (now + timedelta(minutes=40)).isoformat()}


def test_plan_is_distinct_shape_only_and_receipt_never_claims_authority() -> None:
    now = datetime.now(UTC)
    value = plan(now)
    digest = validate_recovery_plan(value, release_sha=SHA, manifest_digest=MANIFEST, now=now)
    assert digest.startswith("sha256:") and len(digest) == 71
    assert validate_recovery_plan(dict(reversed(list(value.items()))), release_sha=SHA,
                                  manifest_digest=MANIFEST, now=now) == digest
    receipt = RecoveryReservation(value["execution_id"], digest, str(uuid4()), "reserved").to_receipt()
    assert receipt["execution_authorized"] is False
    assert receipt["credential_binding_verified"] is False
    assert "custodian" not in receipt


@pytest.mark.parametrize("field", list(_FIXED_PLAN))
def test_each_pinned_incident_identifier_is_mandatory(field: str) -> None:
    value = plan()
    value[field] = "foreign-or-old-authorization"
    with pytest.raises(RecoveryRefused, match="^RECOVERY_PLAN_INVALID$"):
        validate_recovery_plan(value, release_sha=SHA, manifest_digest=MANIFEST, now=datetime.now(UTC))


@pytest.mark.parametrize("change", [
    {"execution_id": OLD_EXECUTION_ID}, {"execution_id": "00000000-0000-0000-0000-000000000000"},
    {"execution_id": "not-a-uuid"}, {"custodian": "invalid/private/custodian"},
    {"release_sha": ""}, {"manifest_digest": "sha256:"}, {"password": "never-log-this-input"},
    {"expires_at": "2030-01-01T00:00:00"}, {"expires_at": "never-log-this-input"},
    {"execution_authorized": True},
])
def test_invalid_nonsecret_plan_fails_closed_without_echo(change: dict[str, Any]) -> None:
    value = {**plan(), **change}
    with pytest.raises(RecoveryRefused) as caught:
        validate_recovery_plan(value, release_sha=SHA, manifest_digest=MANIFEST, now=datetime.now(UTC))
    assert str(caught.value) == "RECOVERY_PLAN_INVALID"
    assert "never-log" not in repr(caught.value)


@pytest.mark.parametrize("seconds", [-1, 0, 3601])
def test_execution_expiry_is_db_time_bounded(seconds: int) -> None:
    now = datetime.now(UTC)
    value = plan(now)
    value["expires_at"] = (now + timedelta(seconds=seconds)).isoformat()
    with pytest.raises(RecoveryRefused):
        validate_recovery_plan(value, release_sha=SHA, manifest_digest=MANIFEST, now=now)


@pytest.mark.parametrize("value", [None, [], "private-input", True])
def test_invalid_plan_types_never_echo(value: Any) -> None:
    with pytest.raises(RecoveryRefused, match="^RECOVERY_PLAN_INVALID$"):
        validate_recovery_plan(value, release_sha=SHA, manifest_digest=MANIFEST, now=datetime.now(UTC))


def test_capability_receipt_and_repr_do_not_export_bearer() -> None:
    cap = RecoveryCapability(str(uuid4()), str(uuid4()), datetime.now(UTC).isoformat(),
                             str(uuid4()), "private-capability-marker")
    assert "private-capability-marker" not in repr(cap)
    assert "token" not in cap.to_receipt()
    assert set(cap.to_receipt()) == {"capability_id", "execution_id", "expires_at", "audit_event_id"}


def test_new_expand_only_migration_is_reachable_and_never_releases_attempts() -> None:
    from shared.infrastructure.persistence.migrations import discover_migration_steps
    steps = discover_migration_steps(Path("infra/db/migrations/versions"))
    step = next(s for s in steps if s.revision == "0023")
    assert {Path(a.path).name for a in step.assets} == {
        "0023_dev_smoke_recovery_capability.py", "000029_dev_smoke_recovery_capability.sql",
    }
    migration = Path(step.path).read_text()
    assert 'down_revision: str = "0022"' in migration
    downgrade = migration.split("def downgrade()", 1)[1]
    assert "DROP" not in downgrade and "DELETE" not in downgrade and "UPDATE" not in downgrade
    sql = Path("infra/db/migrations/000029_dev_smoke_recovery_capability.sql").read_text()
    assert "INTERVAL '600 seconds'" in sql and "attempt_count BETWEEN 0 AND 5" in sql
    assert "token_hash TEXT" in sql and "ON DELETE CASCADE" not in sql
    assert "identity.login_attempts" not in sql and "ALTER TABLE identity.invitations" not in sql


def test_session_fence_migration_is_reachable_expand_only_and_contains_no_credential_copy() -> None:
    from shared.infrastructure.persistence.migrations import discover_migration_steps
    step = next(s for s in discover_migration_steps(Path("infra/db/migrations/versions")) if s.revision == "0024")
    assert {Path(a.path).name for a in step.assets} == {
        "0024_dev_smoke_session_fence.py", "000030_dev_smoke_session_fence.sql",
    }
    migration = Path(step.path).read_text()
    assert 'down_revision: str = "0023"' in migration
    downgrade = migration.split("def downgrade()", 1)[1]
    assert "DROP" not in downgrade and "DELETE" not in downgrade and "UPDATE" not in downgrade
    sql = Path("infra/db/migrations/000030_dev_smoke_session_fence.sql").read_text()
    assert "FOR SHARE" in sql and "OLD.credential_revision + 1" in sql
    assert "verified_credential_revision BIGINT" in sql
    assert "SET phc_hash" not in sql and "token_hash" not in sql
