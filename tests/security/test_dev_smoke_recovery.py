"""Offline recovery-root shape tests; no execution or credential authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from shared.identity.dev_smoke_recovery import (
    OLD_EXECUTION_ID,
    RecoveryRefused,
    RecoveryReservation,
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
