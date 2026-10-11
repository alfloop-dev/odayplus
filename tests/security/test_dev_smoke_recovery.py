"""Offline recovery shape/storage contracts; no live execution authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from shared.identity.dev_smoke_recovery import (
    _FIXED_PLAN,
    OLD_EXECUTION_ID,
    RecoveryBundleLineage,
    RecoveryCapability,
    RecoveryRefused,
    RecoveryReservation,
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


def recovery_consumer_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Synthetic consumer contracts only; NOT a binding producer/live receipt."""
    from shared.identity import dev_smoke_recovery as r
    from shared.identity.dev_smoke_journal import (
        AUTHORIZATION_ID as old_authorization,
    )
    from shared.identity.dev_smoke_journal import (
        CREDENTIAL_BUNDLE_SECRET_NAME,
        PRESERVED_ACCOUNT_ID,
        TENANT_ID,
        ProvisioningJournal,
    )
    from shared.identity.invitation_service import _scope
    now = datetime(2026, 10, 11, 5, tzinfo=UTC)
    p = plan(now)
    digest = r._digest(p)
    sid, root_id, cap_id, cap_issue, rotation_id, intent_id = [str(uuid4()) for _ in range(6)]
    lineage = dict(plan_digest=digest, root_event_id=root_id, capability_id=cap_id,
                   capability_issue_event_id=cap_issue, rotation_event_id=rotation_id,
                   binding_intent_event_id=intent_id, **{k: r._FIXED_PLAN[k] for k in (
                       "old_execution_id", "old_plan_digest", "old_quarantine_event_id",
                       "invitation_id", "issue_event_id", "accept_event_id")})
    bundle = {"schema_version": 2, "authorization_id": r.AUTHORIZATION_ID,
              "execution_id": p["execution_id"], "repository": r.REPOSITORY, "environment": "dev",
              "tenant_id": TENANT_ID, "account_id": r.TARGET_ACCOUNT_ID, "username": "odp-dev-smoke",
              "password": "Offline-Recovered-Password-8721!", "recovery_lineage": lineage}
    events = []

    def event(kind: str, actor: str, correlation: str, action: str, metadata: Any,
              seconds: int, event_id: str | None = None, resource: str | None = None) -> None:
        events.append(dict(event_id=event_id or str(uuid4()), event_type=kind, actor=actor,
                           correlation_id=correlation, resource=resource or correlation, action=action,
                           outcome="success", timestamp=(now + timedelta(seconds=seconds)).isoformat(), metadata=metadata))

    old_meta = dict(authorization_id=old_authorization, execution_id=r.OLD_EXECUTION_ID,
                    plan_digest=r.OLD_PLAN_DIGEST, release_sha=SHA, manifest_digest=MANIFEST,
                    tenant_id=TENANT_ID, execution_authorized=False, secret_values_redacted=True)
    event(ProvisioningJournal._TYPE, ProvisioningJournal._ACTOR, ProvisioningJournal._CORRELATION,
          "DEV_SMOKE_RESERVATION", {**old_meta, "stage": "reserved"}, -100)
    scope = _scope(TENANT_ID)
    event("identity.account.invite", PRESERVED_ACCOUNT_ID, f"identity-invitation-{r.INVITATION_ID}",
          "IDENTITY_ACCOUNT_INVITE", dict(tenant_id=TENANT_ID, invitation_id=r.INVITATION_ID,
              preset_roles=["platform_admin"], preset_scope=scope, expires_at=(now + timedelta(hours=1)).isoformat()),
          -90, r.ISSUE_EVENT_ID, f"identity.invitation:{r.INVITATION_ID}")
    event("identity.account.accept", r.TARGET_ACCOUNT_ID, f"identity-invitation-{r.INVITATION_ID}",
          "IDENTITY_ACCOUNT_ACCEPT", dict(tenant_id=TENANT_ID, invitation_id=r.INVITATION_ID,
              account_id=r.TARGET_ACCOUNT_ID, subject_id=r.TARGET_ACCOUNT_ID, roles=["platform_admin"],
              scope=scope, status="active", must_change=False), -80, r.ACCEPT_EVENT_ID,
          f"identity.invitation:{r.INVITATION_ID}")
    event(ProvisioningJournal._TYPE, ProvisioningJournal._ACTOR, ProvisioningJournal._CORRELATION,
          "DEV_SMOKE_RESERVATION", {**old_meta, "stage": "recovery-required"}, -70, r.OLD_QUARANTINE_EVENT_ID)
    root_meta = {**p, "plan_digest": digest, "issuer_session_id": sid,
                 "original_identity_digest": "sha256:" + "c" * 64, "target_identity_digest": "sha256:" + "d" * 64,
                 "original_credential_updated_at": (now - timedelta(days=1)).isoformat(),
                 "target_credential_updated_at": (now - timedelta(days=1)).isoformat(),
                 "original_credential_revision": "1", "target_credential_revision": "1",
                 "stage": "reserved", "execution_authorized": False, "credential_binding_verified": False,
                 "secret_values_redacted": True}
    event(r.RecoveryJournal._TYPE, PRESERVED_ACCOUNT_ID, r.RecoveryJournal._CORRELATION,
          r.RecoveryJournal._ACTION, root_meta, 0, root_id)
    cap_meta = {**p, "plan_digest": digest, "root_event_id": root_id, "issuer_session_id": sid,
                "capability_id": cap_id, "capability_expires_at": (now + timedelta(seconds=610)).isoformat(),
                "issue_event_id": "", "revoked_target_sessions": 0, "execution_authorized": False,
                "credential_binding_verified": False, "secret_values_redacted": True}
    event(r.RecoveryCapabilityService._CAP_TYPE, PRESERVED_ACCOUNT_ID, r.RecoveryCapabilityService._CAP_CORRELATION,
          "DEV_SMOKE_RECOVERY_CAPABILITY", {**cap_meta, "stage": "issued"}, 10, cap_issue)
    event(r.RecoveryCapabilityService._CAP_TYPE, PRESERVED_ACCOUNT_ID, r.RecoveryCapabilityService._CAP_CORRELATION,
          "DEV_SMOKE_RECOVERY_CAPABILITY", {**cap_meta, "stage": "rotated", "issue_event_id": cap_issue,
                                          "revoked_target_sessions": 2}, 20, rotation_id)
    binding_meta = {**p, "plan_digest": digest, "root_event_id": root_id, "issuer_session_id": sid,
                    "capability_id": cap_id, "capability_issue_event_id": cap_issue, "rotation_event_id": rotation_id,
                    "secret_name": CREDENTIAL_BUNDLE_SECRET_NAME, "execution_authorized": False,
                    "credential_binding_verified": False, "secret_values_redacted": True}
    event(r.RECOVERY_BINDING_TYPE, PRESERVED_ACCOUNT_ID, r.RECOVERY_BINDING_CORRELATION,
          r.RECOVERY_BINDING_ACTION, {**binding_meta, "stage": "binding-intent", "put_status": 0}, 30, intent_id)
    event(r.RECOVERY_BINDING_TYPE, PRESERVED_ACCOUNT_ID, r.RECOVERY_BINDING_CORRELATION,
          r.RECOVERY_BINDING_ACTION, {**binding_meta, "stage": "binding-acknowledged", "put_status": 201}, 40)
    target = dict(subject_id=r.TARGET_ACCOUNT_ID, username="odp-dev-smoke", status="active",
                  roles=["platform_admin"], scope=scope, updated_by=PRESERVED_ACCOUNT_ID,
                  attributes={"identity_source": "identity.accounts"})
    return bundle, events, target


def test_recovery_schema2_standing_bundle_requires_complete_separate_lineage() -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        credential_bundle_acknowledged,
        read_dev_credential_bundle,
    )
    raw, events, target = recovery_consumer_inputs()
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    assert bundle.schema_version == 2 and bundle.password == raw["password"]
    assert raw["password"] not in repr(bundle)
    assert credential_bundle_acknowledged(bundle, events, target_account=target)
    assert not credential_bundle_acknowledged(bundle, events)  # no authoritative target, no waiver
    # Legacy schema1 STILL refuses its quarantined root, even with new recovery ACK.
    from dataclasses import replace
    assert not credential_bundle_acknowledged(replace(bundle, schema_version=1, recovery_lineage=None), events)


@pytest.mark.parametrize("missing", range(9))
def test_recovery_lineage_missing_any_required_event_refuses(missing: int) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        credential_bundle_acknowledged,
        read_dev_credential_bundle,
    )
    raw, events, target = recovery_consumer_inputs()
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    events.pop(missing)
    assert not credential_bundle_acknowledged(bundle, events, target_account=target)


@pytest.mark.parametrize("index", range(9))
@pytest.mark.parametrize("field,value", [
    ("actor", "foreign-actor"), ("outcome", "failure"), ("event_id", "00000000-0000-0000-0000-000000000000"),
    ("resource", "foreign-resource"), ("correlation_id", "foreign-correlation"),
    ("timestamp", "2026-10-11T05:00:00"), ("event_type", "foreign-type"),
])
def test_recovery_lineage_event_envelope_corruption_refuses(index: int, field: str, value: Any) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        credential_bundle_acknowledged,
        read_dev_credential_bundle,
    )
    raw, events, target = recovery_consumer_inputs()
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    events[index][field] = value
    assert not credential_bundle_acknowledged(bundle, events, target_account=target)


@pytest.mark.parametrize("index", [0, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("field,value", [
    ("tenant_id", "foreign-tenant"), ("authorization_id", "foreign-authorization"),
    ("execution_id", OLD_EXECUTION_ID), ("plan_digest", "sha256:" + "f" * 64),
    ("stage", "recovery-required"), ("secret_values_redacted", False), ("execution_authorized", True),
    ("password", "private-marker"),
])
def test_recovery_lineage_metadata_corruption_refuses(index: int, field: str, value: Any) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        credential_bundle_acknowledged,
        read_dev_credential_bundle,
    )
    raw, events, target = recovery_consumer_inputs()
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    # Unchanged old reserved/quarantine share the expected old execution by definition.
    if index in (0, 3) and field == "execution_id":
        value = raw["execution_id"]
    if index == 3 and field == "stage":
        value = "reserved"
    events[index]["metadata"][field] = value
    assert not credential_bundle_acknowledged(bundle, events, target_account=target)


@pytest.mark.parametrize("change", [
    {"schema_version": 1}, {"schema_version": True}, {"schema_version": 3},
    {"execution_id": OLD_EXECUTION_ID},
    {"authorization_id": "HUMAN-ODP-DEV-SMOKE-20261010-001"}, {"username": "ajoe734"},
    {"account_id": "17e9cb99-db46-4a07-8e61-6bf9b22cf5d2"}, {"recovery_lineage": {}},
    {"password": ""}, {"extra": "private-marker"},
])
def test_recovery_bundle_never_falls_back_or_downgrades(change: Any) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        ProvisioningRefused,
        read_dev_credential_bundle,
    )
    raw, _, _ = recovery_consumer_inputs()
    raw.update(change)
    with pytest.raises(ProvisioningRefused, match="^PROVISIONING_CREDENTIAL_BUNDLE_INVALID$"):
        read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")


@pytest.mark.parametrize("field", list(RecoveryBundleLineage.__dataclass_fields__))
def test_recovery_bundle_all_lineage_fields_required(field: str) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        ProvisioningRefused,
        read_dev_credential_bundle,
    )
    raw, _, _ = recovery_consumer_inputs()
    raw["recovery_lineage"].pop(field)
    with pytest.raises(ProvisioningRefused):
        read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")


@pytest.mark.parametrize("failure", ["duplicate_ack", "duplicate_root", "old_binding", "new_quarantine",
    "204", "bool_status", "rotated_expired", "long_cap_expiry", "late_ack", "reordered_intent",
    "wrong_sid", "wrong_capability", "wrong_root", "wrong_invitation", "wrong_rotation_issue", "bool_revoked"])
def test_recovery_proof_rejects_uncertainty_replay_expiry_and_cross_lineage(failure: str) -> None:
    import json
    from copy import deepcopy

    from delivery_toolchain.release.provision_dev_smoke import (
        credential_bundle_acknowledged,
        read_dev_credential_bundle,
    )
    from shared.identity.dev_smoke_journal import DevSmokeBindingJournal
    raw, events, target = recovery_consumer_inputs()
    bundle = read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")
    if failure in {"duplicate_ack", "duplicate_root", "old_binding", "new_quarantine"}:
        extra = deepcopy(events[-1] if failure == "duplicate_ack" else events[4])
        if failure == "old_binding":
            extra.update(event_type=DevSmokeBindingJournal._TYPE, correlation_id=DevSmokeBindingJournal._CORRELATION)
        if failure == "new_quarantine":
            extra["event_id"] = str(uuid4())
            extra["metadata"]["stage"] = "recovery-required"
        events.append(extra)
    elif failure in {"204", "bool_status"}:
        events[-1]["metadata"]["put_status"] = 204 if failure == "204" else True
    elif failure == "rotated_expired":
        events[6]["timestamp"] = events[5]["metadata"]["capability_expires_at"]
    elif failure == "long_cap_expiry":
        for i in (5, 6):
            events[i]["metadata"]["capability_expires_at"] = "2026-10-11T05:10:11+00:00"
    elif failure == "late_ack":
        events[-1]["timestamp"] = events[4]["metadata"]["expires_at"]
    elif failure == "reordered_intent":
        events[7]["timestamp"] = events[5]["timestamp"]
    elif failure == "wrong_sid":
        events[6]["metadata"]["issuer_session_id"] = str(uuid4())
    elif failure == "wrong_capability":
        events[-1]["metadata"]["capability_id"] = str(uuid4())
    elif failure == "wrong_root":
        events[5]["metadata"]["root_event_id"] = str(uuid4())
    elif failure == "wrong_invitation":
        events[4]["metadata"]["invitation_id"] = str(uuid4())
    elif failure == "wrong_rotation_issue":
        events[6]["metadata"]["issue_event_id"] = events[1]["event_id"]
    else:
        events[6]["metadata"]["revoked_target_sessions"] = True
    assert not credential_bundle_acknowledged(bundle, events, target_account=target)


@pytest.mark.parametrize("nested", [False, True])
def test_recovery_bundle_duplicate_keys_fail_static_without_plaintext_echo(nested: bool) -> None:
    import json

    from delivery_toolchain.release.provision_dev_smoke import (
        ProvisioningRefused,
        read_dev_credential_bundle,
    )
    raw, _, _ = recovery_consumer_inputs()
    serialized = json.dumps(raw)
    key = "plan_digest" if nested else "password"
    serialized = serialized.replace(f'"{key}":', f'"{key}": "private-marker", "{key}":', 1)
    with pytest.raises(ProvisioningRefused) as exc:
        read_dev_credential_bundle(serialized, environment="dev", release_profile="dev-admin")
    assert str(exc.value) == "PROVISIONING_CREDENTIAL_BUNDLE_INVALID" and "private-marker" not in repr(exc.value)


# Foreground primitives are isolated contract tests, NOT an executable recovery
# or genuine writer-exclusion/native fresh-consumer proof.
def foreground_budget(tmp_path: Path, monkeypatch: Any, *, total: float = 2400):
    from types import SimpleNamespace

    from delivery_toolchain.release import recover_dev_smoke as r
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(r, "time", SimpleNamespace(monotonic=lambda: clock.value))
    p = plan()
    receipts = r.ExclusivePhaseReceipts(tmp_path, execution_id=p["execution_id"], plan_digest=r.validate_recovery_plan(
        p, release_sha=SHA, manifest_digest=MANIFEST, now=datetime.now(UTC)))
    try:
        budget = r.ForegroundRecoveryBudget(p, receipts=receipts, total_seconds=total)
    except Exception:
        receipts.close()
        raise
    return budget, clock, receipts, p


def advance_to(budget: Any, phase: str) -> None:
    from delivery_toolchain.release.recover_dev_smoke import PHASE_CEILINGS
    for name, _ in PHASE_CEILINGS:
        if name == phase:
            return
        budget.begin(name)
        assert budget.finish(outcome="completed", http_status=201 if name == "put" else None) == "completed"


def test_parent_custody_unique_repr_pickle_fork_and_close(monkeypatch: Any) -> None:
    import pickle
    from copy import deepcopy

    from delivery_toolchain.release import recover_dev_smoke as r
    from shared.identity.password_policy import PasswordPolicy
    first, second = r.ParentMemoryCustody(), r.ParentMemoryCustody()
    password = first.password
    assert password != second.password and len(password) >= 64
    assert password not in repr(first) and "redacted" in repr(first)
    assert PasswordPolicy().validate(password, username="odp-dev-smoke").valid
    first.attach_capability("c" * 43)
    assert first.capability == "c" * 43
    with pytest.raises(RecoveryRefused):
        first.attach_capability("d" * 43)
    for serialize in (pickle.dumps, deepcopy):
        with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
            serialize(first)
    monkeypatch.setattr(r.os, "getpid", lambda: first._pid + 1)
    with pytest.raises(RecoveryRefused, match="CUSTODY_LOST"):
        _ = first.password
    first.close()
    second.close()
    with pytest.raises(RecoveryRefused, match="CUSTODY_LOST"):
        _ = first.capability


@pytest.mark.parametrize("token", ["", "private-marker", "c" * 44, None, True])
def test_custody_rejects_bad_capability_without_echo(token: Any) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ParentMemoryCustody
    custody = ParentMemoryCustody()
    with pytest.raises(RecoveryRefused) as exc:
        custody.attach_capability(token)
    assert str(exc.value) == "RECOVERY_CAPABILITY_INPUT_REFUSED"
    with pytest.raises(RecoveryRefused, match="UNAVAILABLE"):
        _ = custody.capability
    custody.close()


def test_phase_receipts_precede_effect_and_no_secret_fields(tmp_path: Path, monkeypatch: Any) -> None:
    import json
    budget, clock, receipts, p = foreground_budget(tmp_path, monkeypatch)
    try:
        ceiling = budget.begin("preflight")
        assert 599 < ceiling <= 600
        files = sorted((tmp_path / p["execution_id"]).iterdir())
        assert len(files) == 1
        start = json.loads(files[0].read_text())
        assert start["stage"] == "start" and start["outcome"] == "pending"
        assert start["execution_authorized"] is False
        assert files[0].stat().st_mode & 0o777 == 0o600
        clock.value += 1
        assert budget.finish(outcome="completed", exit_code=0) == "completed"
        terminal = json.loads(sorted((tmp_path / p["execution_id"]).iterdir())[1].read_text())
        assert terminal["elapsed_seconds"] == 1 and terminal["exit_code"] == 0
        assert set(terminal) == set(start)
        assert not {"password", "token", "cookie", "capability", "body", "plan", "custodian"} & terminal.keys()
    finally:
        receipts.close()


def test_no_receipt_directory_resume_or_overwrite(tmp_path: Path, monkeypatch: Any) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ExclusivePhaseReceipts
    _, _, receipts, p = foreground_budget(tmp_path, monkeypatch)
    try:
        with pytest.raises(RecoveryRefused, match="STORAGE_UNAVAILABLE"):
            ExclusivePhaseReceipts(tmp_path, execution_id=p["execution_id"], plan_digest=receipts._digest)
    finally:
        receipts.close()


@pytest.mark.parametrize("phase", ["rotate", "put", "ack", "gate", "fresh_consumer"])
def test_cannot_skip_preflight_or_downstream_phases(phase: str, tmp_path: Path, monkeypatch: Any) -> None:
    budget, _, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        with pytest.raises(RecoveryRefused, match="START_REFUSED"):
            budget.begin(phase)
        with pytest.raises(RecoveryRefused, match="START_REFUSED"):
            budget.begin("preflight")  # later truth cannot resume this object
        budget.begin("quarantine")
        budget.finish(outcome="completed")
        with pytest.raises(RecoveryRefused):
            budget.begin("quarantine")
    finally:
        receipts.close()


@pytest.mark.parametrize("seconds", [0, -1, 2401, float("nan"), float("inf"), True])
def test_invalid_absolute_deadline_refuses(seconds: Any, tmp_path: Path, monkeypatch: Any) -> None:
    with pytest.raises(RecoveryRefused, match="BUDGET_INVALID"):
        _, _, receipts, _ = foreground_budget(tmp_path, monkeypatch, total=seconds)


def test_full_gate_cleanup_quarantine_reserved_before_first_mutation(tmp_path: Path, monkeypatch: Any) -> None:
    budget, _, receipts, _ = foreground_budget(tmp_path, monkeypatch, total=1799)
    try:
        # Read-only preflight cannot eat the 1800s downstream ceiling either.
        with pytest.raises(RecoveryRefused, match="START_REFUSED"):
            budget.begin("preflight")
        budget.begin("cleanup")
        budget.finish(outcome="completed")
        budget.begin("quarantine")
        budget.finish(outcome="completed")
    finally:
        receipts.close()


def test_timeout_preserves_parent_memory_but_forbids_continuation(tmp_path: Path, monkeypatch: Any) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ParentMemoryCustody
    budget, clock, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    custody = ParentMemoryCustody()
    password = custody.password
    custody.attach_capability("t" * 43)
    try:
        allowance = budget.begin("preflight")
        clock.value += allowance
        assert budget.finish(outcome="completed", exit_code=0) == "unknown"
        assert custody.password == password and custody.capability == "t" * 43
        with pytest.raises(RecoveryRefused):
            budget.begin("reserve")
        budget.begin("quarantine")
        budget.finish(outcome="completed")
    finally:
        receipts.close()
        custody.close()


@pytest.mark.parametrize("status", [None, 200, 204, 302, 403, 500, 201])
def test_put_response_classification_never_reconstructs_ack(status: Any, tmp_path: Path, monkeypatch: Any) -> None:
    budget, _, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        advance_to(budget, "put")
        budget.begin("put")
        assert budget.finish(outcome="completed", http_status=status) == ("completed" if status == 201 else "unknown")
        if status == 201:
            budget.begin("ack")
            budget.finish(outcome="unknown")  # failed/lost ACK cannot retry
        with pytest.raises(RecoveryRefused):
            budget.begin("ack")
        with pytest.raises(RecoveryRefused):
            budget.begin("put")
    finally:
        receipts.close()


@pytest.mark.parametrize("code,expected", [(0, "completed"), (1, "refused"), (-9, "interrupted")])
def test_original_terminal_exit_code_controls_outcome(code: int, expected: str, tmp_path: Path, monkeypatch: Any) -> None:
    budget, _, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        budget.begin("preflight")
        assert budget.finish(outcome="completed", exit_code=code) == expected
    finally:
        receipts.close()


@pytest.mark.parametrize("failure", ["start_fsync", "terminal_fsync", "private_event", "boolean_exit", "overlap", "clock_reverse"])
def test_receipt_and_clock_uncertainty_fail_stop(failure: str, tmp_path: Path, monkeypatch: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    budget, clock, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        def unavailable(fd: int) -> None:
            raise OSError("private-marker")
        if failure == "start_fsync":
            monkeypatch.setattr(r.os, "fsync", unavailable)
            with pytest.raises(RecoveryRefused):
                budget.begin("preflight")
        else:
            budget.begin("preflight")
            if failure == "terminal_fsync":
                monkeypatch.setattr(r.os, "fsync", unavailable)
            if failure == "clock_reverse":
                clock.value -= 1
            with pytest.raises(RecoveryRefused) as exc:
                if failure == "overlap":
                    budget.begin("reserve")
                else:
                    budget.finish(outcome="completed", event_id="private-marker" if failure == "private_event" else "",
                                  exit_code=True if failure == "boolean_exit" else None)
            assert "private-marker" not in str(exc.value)
        with pytest.raises(RecoveryRefused):
            budget.begin("reserve")
    finally:
        receipts.close()


def test_full_success_budget_still_not_binding_or_execution_proof(tmp_path: Path, monkeypatch: Any) -> None:
    from delivery_toolchain.release.recover_dev_smoke import PHASE_CEILINGS
    budget, _, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        for phase, _ in PHASE_CEILINGS[:-1]:
            budget.begin(phase)
            budget.finish(outcome="completed", http_status=201 if phase == "put" else None)
        # Cleanup finished; quarantine can still be recorded, never normal resume.
        budget.begin("quarantine")
        budget.finish(outcome="completed")
        with pytest.raises(RecoveryRefused):
            budget.begin("preflight")
        assert sum(s for _, s in PHASE_CEILINGS) == 2400
    finally:
        receipts.close()


def test_adapter_deadline_is_absolute_and_stopped_cleanup_remains_bounded(tmp_path: Path, monkeypatch: Any) -> None:
    budget, clock, receipts, _ = foreground_budget(tmp_path, monkeypatch)
    try:
        with pytest.raises(RecoveryRefused):
            _ = budget.call_deadline
        budget.begin("preflight")
        deadline = budget.call_deadline
        clock.value += 5
        assert budget.call_deadline == deadline
        budget.finish(outcome="unknown")
        with pytest.raises(RecoveryRefused):
            _ = budget.call_deadline
        budget.begin("cleanup")
        assert budget.call_deadline <= 2500
        budget.finish(outcome="completed")
        budget.begin("quarantine")
        assert budget.call_deadline <= 2500
        budget.finish(outcome="completed")
    finally:
        receipts.close()


def observer_child(tmp_path: Path, program: str, *, seconds: float = 5) -> bytes:
    """Real NON-SECRET test child; no cloud/database/credential transport."""
    import sys
    import time

    from delivery_toolchain.release.recover_dev_smoke import _bounded_observer_child
    return _bounded_observer_child((sys.executable, "-c", program), cwd=tmp_path,
                                   deadline=time.monotonic() + seconds)


def test_real_observer_exec_does_not_inherit_parent_env_or_fds(tmp_path: Path, monkeypatch: Any) -> None:
    import os
    monkeypatch.setenv("RECOVERY_PRIVATE_TEST_MARKER", "not-for-child")
    fd = os.open(tmp_path / "nonsecret-fd", os.O_CREAT | os.O_RDWR, 0o600)
    os.set_inheritable(fd, True)
    try:
        assert observer_child(tmp_path, f'''
import os
assert "RECOVERY_PRIVATE_TEST_MARKER" not in os.environ
assert set(os.environ) <= {{"PATH", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "LC_CTYPE"}}
try:
    os.fstat({fd})
except OSError:
    pass
else:
    raise AssertionError("inherited descriptor")
assert not os.read(0, 1)
print("safe-terminal")
''') == b"safe-terminal\n"
    finally:
        os.close(fd)


def test_real_observer_timeout_reaps_original_handle_without_losing_parent_custody(tmp_path: Path) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ObserverRefused, ParentMemoryCustody
    custody = ParentMemoryCustody()
    password = custody.password
    custody.attach_capability("t" * 43)
    try:
        with pytest.raises(ObserverRefused) as caught:
            observer_child(tmp_path, "import time; time.sleep(20)", seconds=2.2)
        assert caught.value.outcome == "interrupted"
        assert caught.value.exit_code == -9  # ORIGINAL Popen wait(), not inferred liveness
        assert str(caught.value) == "RECOVERY_CHECKOUT_OBSERVER_REFUSED"
        assert custody.password == password and custody.capability == "t" * 43
    finally:
        custody.close()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_observer_output_limit_cancels_without_echo(tmp_path: Path, stream: str) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ObserverRefused
    with pytest.raises(ObserverRefused) as caught:
        observer_child(tmp_path, f"import sys, time; sys.{stream}.write('PRIVATE-MARKER' * 6000); sys.{stream}.flush(); time.sleep(20)")
    assert caught.value.outcome == "refused" and caught.value.exit_code == -9
    assert "PRIVATE-MARKER" not in repr(caught.value)


def test_real_observer_nonzero_original_terminal_is_not_pass(tmp_path: Path) -> None:
    from delivery_toolchain.release.recover_dev_smoke import ObserverRefused
    with pytest.raises(ObserverRefused) as caught:
        observer_child(tmp_path, "import sys; print('PRIVATE-MARKER', file=sys.stderr); sys.exit(7)")
    assert caught.value.exit_code == 7 and caught.value.outcome == "refused"
    assert "PRIVATE-MARKER" not in repr(caught.value)


def test_observer_wait_receipt_loss_is_unknown_not_success(tmp_path: Path, monkeypatch: Any) -> None:
    import subprocess

    from delivery_toolchain.release.recover_dev_smoke import ObserverRefused
    original = subprocess.Popen.wait
    def lost_receipt(self: Any, *args: Any, **kwargs: Any) -> int:
        original(self, *args, **kwargs)  # Actually reap; only receipt is lost.
        raise OSError("PRIVATE-MARKER")
    monkeypatch.setattr(subprocess.Popen, "wait", lost_receipt)
    with pytest.raises(ObserverRefused) as caught:
        observer_child(tmp_path, "pass")
    assert caught.value.outcome == "unknown" and caught.value.exit_code is None
    assert "PRIVATE-MARKER" not in repr(caught.value)


def test_observer_group_cancellation_even_if_original_exited_zero(tmp_path: Path) -> None:
    import time

    from delivery_toolchain.release.recover_dev_smoke import ObserverRefused
    # Descendant retains pipes after parent exit. Finite observation of a test
    # marker supplements group cancellation; it is NOT a grandchild wait receipt.
    child = "import time; from pathlib import Path; time.sleep(1); Path('survived').touch()"
    with pytest.raises(ObserverRefused) as caught:
        observer_child(tmp_path, f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}])", seconds=2.2)
    assert caught.value.exit_code == 0 and caught.value.outcome == "interrupted"
    time.sleep(1.1)
    assert not (tmp_path / "survived").exists()


@pytest.fixture
def isolated_checkout(tmp_path: Path) -> tuple[Path, str, str]:
    import subprocess
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null"}
    def git(*args: str) -> str:
        return subprocess.run(("/usr/bin/git", *args), cwd=tmp_path, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "--quiet")
    (tmp_path / "tracked").write_text("isolated-nonsecret\n")
    git("add", "tracked")
    git("-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "isolated fixture")
    return tmp_path, git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")


@pytest.mark.parametrize("drift", ["none", "tracked", "untracked", "sha", "tree", "fsmonitor"])
def test_checkout_observer_fresh_fulltree_no_cached_pass(isolated_checkout: Any, drift: str) -> None:
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        ObserverRefused,
        TrustedCheckoutObserver,
    )
    checkout, sha, tree = isolated_checkout
    observer = TrustedCheckoutObserver(checkout, release_sha=sha, reviewed_tree=tree)
    receipt = observer.observe(deadline=time.monotonic() + 5)
    assert receipt["release_sha"] == sha and receipt["reviewed_tree"] == tree
    assert receipt["execution_authorized"] is False and receipt["credential_binding_verified"] is False
    if drift in {"tracked", "untracked"}:
        (checkout / ("tracked" if drift == "tracked" else "PRIVATE-MARKER")).write_text("changed")
    elif drift == "sha":
        observer = TrustedCheckoutObserver(checkout, release_sha="a" * 40, reviewed_tree=tree)
    elif drift == "tree":
        observer = TrustedCheckoutObserver(checkout, release_sha=sha, reviewed_tree="b" * 40)
    elif drift == "fsmonitor":
        # Configured executable must NOT run in the child.
        (checkout / ".git/config").write_text("[core]\nfsmonitor = sh -c 'touch PRIVATE-MARKER'\n")
    if drift in {"none", "fsmonitor"}:
        assert observer.observe(deadline=time.monotonic() + 5) == receipt
        assert not (checkout / "PRIVATE-MARKER").exists()
    else:
        with pytest.raises(ObserverRefused) as caught:
            observer.observe(deadline=time.monotonic() + 5)
        assert caught.value.outcome == "refused" and caught.value.exit_code == 0
        assert "PRIVATE-MARKER" not in repr(caught.value)


def test_checkout_head_drift_during_read_refuses(isolated_checkout: Any, monkeypatch: Any) -> None:
    import time

    from delivery_toolchain.release import recover_dev_smoke as r
    checkout, sha, tree = isolated_checkout
    original = r._bounded_observer_child
    calls = []
    def drift(args: Any, **kwargs: Any) -> bytes:
        calls.append(args)
        return b"a" * 40 + b"\n" if len(calls) == 4 else original(args, **kwargs)
    monkeypatch.setattr(r, "_bounded_observer_child", drift)
    with pytest.raises(r.ObserverRefused):
        r.TrustedCheckoutObserver(checkout, release_sha=sha, reviewed_tree=tree).observe(deadline=time.monotonic() + 5)
    assert len(calls) == 4


@pytest.fixture
def tls_recovery_server(tmp_path: Path):
    """Real loopback TLS ONLY; ephemeral fixture certificate, no live secrets.

    Thread gets synthetic request data, not ParentMemoryCustody/foreground state.
    Original thread join is bounded; no process-name or output polling.
    """
    import socket
    import ssl
    import threading
    import time
    from contextlib import contextmanager
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cert_path, key_path = tmp_path / "offline-cert.pem", tmp_path / "offline-key.pem"
    cert_path.write_text(pem)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)

    @contextmanager
    def server(reply: bytes, *, dribble: bool = False, handshake_pause: float = 0):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]
        observed = []
        def serve():
            try:
                with listener.accept()[0] as raw:
                    raw.settimeout(3)
                    if handshake_pause:
                        time.sleep(handshake_pause)
                    with context.wrap_socket(raw, server_side=True) as sock:
                        data = bytearray()
                        while b"\r\n\r\n" not in data:
                            chunk = sock.recv(4096)
                            if not chunk or len(data) > 65536:
                                raise ValueError
                            data.extend(chunk)
                        header, content = bytes(data).split(b"\r\n\r\n", 1)
                        lengths = [line.split(b":", 1)[1].strip() for line in header.split(b"\r\n")
                                   if line.lower().startswith(b"content-length:")]
                        length = int(lengths[0]) if lengths else 0
                        while len(content) < length:
                            chunk = sock.recv(4096)
                            if not chunk:
                                raise ValueError
                            content += chunk
                        observed.append((header, content))
                        if dribble:
                            for byte in reply:
                                sock.sendall(bytes([byte]))
                                time.sleep(0.02)
                        elif reply:
                            sock.sendall(reply)
            except (OSError, ValueError):
                pass  # deliberate TLS/refusal/disconnection fixtures
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield f"https://localhost:{port}", pem, observed
        finally:
            listener.close()
            thread.join(timeout=4)
            assert not thread.is_alive(), "original isolated server thread did not finish"
    return server


def recovery_http_reply(body: bytes = b'{"ok":true}', *, status: int = 200,
                        extra: bytes = b"", chunked: bool = False) -> bytes:
    framing = (b"Transfer-Encoding: chunked\r\n" if chunked else
               f"Content-Length: {len(body)}\r\n".encode())
    encoded = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n" if chunked else body
    return (f"HTTP/1.1 {status} fixture\r\n".encode() + framing +
            b"Content-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\n" + extra + b"\r\n" + encoded)


@pytest.mark.parametrize("chunked", [False, True])
def test_real_https_once_same_cookie_fixed_route_no_dns_env_or_redirect(tls_recovery_server: Any,
                                                                      monkeypatch: Any, chunked: bool) -> None:
    import pickle
    import socket
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        RecoveryHTTPRefused,
        RecoveryHTTPSAttempt,
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://not-a-proxy.invalid")
    def forbidden(*args, **kwargs):
        raise AssertionError("DNS forbidden inside absolute phase")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with tls_recovery_server(recovery_http_reply(b'{"token":"OFFLINE-PRIVATE-MARKER"}', status=201,
                                               chunked=chunked)) as (origin, ca, observed):
        attempt = RecoveryHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        result = attempt.execute(method="POST", cookie="offline-cookie", body={"action": "issue"},
                                 deadline=time.monotonic() + 2)
        assert result.http_status == 201 and result.payload["token"] == "OFFLINE-PRIVATE-MARKER"
        assert "OFFLINE-PRIVATE-MARKER" not in repr(result)
        with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
            pickle.dumps(result)
        with pytest.raises(RecoveryHTTPRefused) as caught:
            attempt.execute(method="POST", cookie="offline-cookie", body={"action": "issue"},
                            deadline=time.monotonic() + 2)
        assert caught.value.outcome == "refused" and caught.value.http_status is None
    assert len(observed) == 1
    header, content = observed[0]
    assert header.startswith(b"POST /api/v1/operator/users/dev-smoke-recovery HTTP/1.1\r\n")
    assert f"Origin: {origin}".encode() in header
    assert b"Cookie: __Host-oday_web_session=offline-cookie" in header
    assert b"actor" not in header and b"tenant" not in header
    assert content == b'{"action":"issue"}'


@pytest.mark.parametrize("failure", ["lost", "redirect", "204", "500", "duplicate_json", "nan",
                                      "oversized", "duplicate_length", "mixed_framing", "truncated",
                                      "truncated_chunk", "cache", "header_cap"])
def test_real_https_unknown_post_never_reconstructs_commit_or_retries(tls_recovery_server: Any, failure: str) -> None:
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        RecoveryHTTPRefused,
        RecoveryHTTPSAttempt,
    )
    replies = {
        "lost": b"", "redirect": recovery_http_reply(status=302, extra=b"Location: https://elsewhere.invalid\r\n"),
        "204": recovery_http_reply(status=204), "500": recovery_http_reply(status=500),
        "duplicate_json": recovery_http_reply(b'{"nested":{"token":1,"token":2}}'),
        "nan": recovery_http_reply(b'{"token":NaN}'),
        "oversized": recovery_http_reply(b"x" * 16385),
        "duplicate_length": recovery_http_reply(extra=b"Content-Length: 11\r\n"),
        "mixed_framing": recovery_http_reply(extra=b"Transfer-Encoding: chunked\r\n"),
        "truncated": recovery_http_reply()[:-2],
        "truncated_chunk": recovery_http_reply(chunked=True)[:-5],
        "cache": recovery_http_reply().replace(b"no-store", b"public"),
        "header_cap": recovery_http_reply(extra=(b"X-Large: " + b"x" * 33000 + b"\r\n") * 2),
    }
    with tls_recovery_server(replies[failure]) as (origin, ca, observed):
        attempt = RecoveryHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        with pytest.raises(RecoveryHTTPRefused) as caught:
            attempt.execute(method="POST", cookie="offline-cookie", body={"action": "consume", "password": "OFFLINE-PRIVATE-MARKER"},
                            deadline=time.monotonic() + 2)
        assert caught.value.outcome == "unknown"
        assert "OFFLINE-PRIVATE-MARKER" not in repr(caught.value)
        with pytest.raises(RecoveryHTTPRefused):
            attempt.execute(method="GET", cookie="offline-cookie", deadline=time.monotonic() + 2)
    assert len(observed) == 1


@pytest.mark.parametrize("phase", ["headers", "handshake"])
def test_real_https_absolute_deadline_cannot_refresh_with_dribble(tls_recovery_server: Any, phase: str) -> None:
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        RecoveryHTTPRefused,
        RecoveryHTTPSAttempt,
    )
    with tls_recovery_server(recovery_http_reply(), dribble=phase == "headers",
                             handshake_pause=0.7 if phase == "handshake" else 0) as (origin, ca, _):
        attempt = RecoveryHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        start = time.monotonic()
        with pytest.raises(RecoveryHTTPRefused) as caught:
            attempt.execute(method="POST", cookie="offline-cookie", body={"action": "consume"}, deadline=start + 0.25)
        assert time.monotonic() - start < 0.6
        assert caught.value.outcome == ("unknown" if phase == "headers" else "refused")
        assert caught.value.http_status is None


def test_real_https_certificate_hostname_verified_before_any_request(tls_recovery_server: Any) -> None:
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        RecoveryHTTPRefused,
        RecoveryHTTPSAttempt,
    )
    with tls_recovery_server(recovery_http_reply()) as (origin, ca, observed):
        attempt = RecoveryHTTPSAttempt(origin=origin.replace("localhost", "wrong.invalid"), address="127.0.0.1", ca_pem=ca)
        with pytest.raises(RecoveryHTTPRefused) as caught:
            attempt.execute(method="POST", cookie="offline-cookie", body={"action": "issue"}, deadline=time.monotonic() + 2)
        assert caught.value.outcome == "refused"
    assert not observed


@pytest.mark.parametrize("bad", ["cookie", "body", "deadline", "get_body"])
def test_https_bad_input_poisons_attempt_without_socket_or_echo(monkeypatch: Any, bad: str) -> None:
    import socket
    import time

    from delivery_toolchain.release.recover_dev_smoke import (
        RecoveryHTTPRefused,
        RecoveryHTTPSAttempt,
    )
    attempt = RecoveryHTTPSAttempt(origin="https://offline.invalid", address="127.0.0.1")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError
    monkeypatch.setattr(socket, "socket", forbidden)
    values = {"method": "POST", "cookie": "offline-cookie", "body": {"action": "issue"}, "deadline": time.monotonic() + 2}
    if bad == "cookie":
        values["cookie"] = "OFFLINE-PRIVATE-MARKER\r\n"
    elif bad == "body":
        values["body"] = {"password": "OFFLINE-PRIVATE-MARKER" * 1000}
    elif bad == "deadline":
        values["deadline"] = float("nan")
    else:
        values["method"] = "GET"
    with pytest.raises(RecoveryHTTPRefused) as caught:
        attempt.execute(**values)
    assert caught.value.outcome == "refused" and not calls
    assert "OFFLINE-PRIVATE-MARKER" not in repr(caught.value)
    with pytest.raises(RecoveryHTTPRefused):
        attempt.execute(method="GET", cookie="offline-cookie", deadline=time.monotonic() + 2)
    assert not calls


@pytest.fixture
def same_session_actions(tmp_path: Path, monkeypatch: Any):
    """Synthetic cookie/responses ONLY; not authority/session/login/PUT proof."""
    from contextlib import contextmanager

    from delivery_toolchain.release import recover_dev_smoke as r

    @contextmanager
    def setup(*, reply_filter=None, real_https=False):
        budget, clock, receipts, p = foreground_budget(tmp_path, monkeypatch)
        budget.begin("preflight")
        budget.finish(outcome="completed")  # simulated, not real preflight approval
        custody = r.ParentMemoryCustody()
        actions = r.SameSessionRecoveryActions(p, budget=budget, custody=custody,
                    origin="https://offline.invalid", address="127.0.0.1", cookie="synthetic-admin-cookie")
        ids = {key: str(uuid4()) for key in ("root", "cap", "issue", "rotate", "quarantine", "intent", "ack", "session")}
        calls = []
        def execute(attempt, *, method, cookie, deadline, body):
            calls.append((method, cookie, deadline, body))
            assert method == "POST" and cookie == "synthetic-admin-cookie"
            assert budget._active is not None
            phase = budget._active[0]
            record = tmp_path / p["execution_id"] / f"{receipts._sequence - 1:03d}-{phase}-start.json"
            assert record.is_file()  # real durable start precedes synthetic transport
            root = RecoveryReservation(p["execution_id"], actions._digest, ids["root"], "reserved").to_receipt()
            replies = {
                "reserve": {"reservation": root},
                "issue": {"capability_id": ids["cap"], "execution_id": p["execution_id"],
                          "expires_at": (datetime.now(UTC) + timedelta(seconds=590)).isoformat(),
                          "audit_event_id": ids["issue"], "token": "s" * 43},
                "consume": {"status": "rotated", "account_id": _FIXED_PLAN["target_account_id"],
                            "tenant_id": _FIXED_PLAN["tenant_id"], "execution_id": p["execution_id"],
                            "capability_id": ids["cap"], "issue_event_id": ids["issue"],
                            "rotation_event_id": ids["rotate"], "revoked_target_sessions": 2,
                            "credential_binding_verified": False, "execution_authorized": False,
                            "secret_values_redacted": True},
                "quarantine": {"reservation": {**root, "event_id": ids["quarantine"], "stage": "recovery-required"}},
            }
            binding = {**p, "plan_digest": actions._digest, "root_event_id": ids["root"],
                       "issuer_session_id": ids["session"], "capability_id": ids["cap"],
                       "capability_issue_event_id": ids["issue"], "rotation_event_id": ids["rotate"],
                       "secret_name": "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", "execution_authorized": False,
                       "credential_binding_verified": False, "secret_values_redacted": True}
            replies["binding-intent"] = {"binding": {**binding, "stage": "binding-intent", "put_status": 0,
                                                   "audit_event_id": ids["intent"]}}
            replies["binding-acknowledged"] = {"binding": {**binding, "stage": "binding-acknowledged",
                                                         "put_status": 201, "audit_event_id": ids["ack"]}}
            payload = replies[body["action"]]
            if reply_filter is not None:
                payload = reply_filter(body["action"], payload)
            return r.RecoveryHTTPResult(200, payload)
        if not real_https:
            monkeypatch.setattr(r.RecoveryHTTPSAttempt, "execute", execute)
        try:
            yield actions, budget, clock, custody, receipts, p, ids, calls
        finally:
            actions.close()
            custody.close()
            receipts.close()
    return setup


def test_same_session_actions_safe_lineage_custody_and_durable_sequence(same_session_actions: Any,
                                                                      tmp_path: Path) -> None:
    import json
    import pickle

    from delivery_toolchain.release import recover_dev_smoke as r

    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, calls):
        root = actions.reserve()
        clock.value += 1
        issued = actions.issue()
        clock.value += 1
        rotated = actions.rotate()
        assert root["event_id"] == ids["root"] and issued["audit_event_id"] == ids["issue"]
        assert "token" not in issued and custody.capability == "s" * 43
        assert rotated["rotation_event_id"] == ids["rotate"]
        assert rotated["credential_binding_verified"] is False
        assert [call[3]["action"] for call in calls] == ["reserve", "issue", "consume"]
        assert calls[2][3]["password"] == custody.password and calls[2][3]["token"] == custody.capability
        assert all(call[1] == "synthetic-admin-cookie" for call in calls)
        assert calls[0][2] == 130 and calls[1][2] == 131 and calls[2][2] == 222
        for marker in (custody.password, custody.capability, "synthetic-admin-cookie"):
            assert marker not in repr(actions)
        with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
            pickle.dumps(actions)
        with pytest.raises(r.RecoveryActionRefused):
            actions.rotate()
        assert len(calls) == 3  # even a fresh HTTPS instance cannot replay
        quarantined = actions.quarantine()
        assert quarantined["stage"] == "recovery-required"
        with pytest.raises(r.RecoveryActionRefused):
            actions.quarantine()
        assert len(calls) == 4
        records = [json.loads(path.read_text()) for path in sorted((tmp_path / p["execution_id"]).glob("*.json"))]
        text = json.dumps(records)
        assert all(marker not in text for marker in (custody.password, custody.capability, "synthetic-admin-cookie"))
        assert [v["event_id"] for v in records if v["stage"] == "terminal"][1:] == [
            ids["root"], ids["issue"], ids["rotate"], ids["quarantine"]]


@pytest.mark.parametrize("action,field,value", [
    ("reserve", "plan_digest", MANIFEST), ("reserve", "execution_id", OLD_EXECUTION_ID),
    ("reserve", "execution_authorized", 0), ("reserve", "secret_values_redacted", 1),
    ("issue", "execution_id", OLD_EXECUTION_ID), ("issue", "token", "PRIVATE-MARKER"),
    ("issue", "expires_at", "2030-01-01T00:00:00Z"),
    ("issue", "capability_id", "00000000-0000-0000-0000-000000000000"),
    ("consume", "account_id", _FIXED_PLAN["actor_account_id"]),
    ("consume", "tenant_id", str(uuid4())), ("consume", "revoked_target_sessions", True),
    ("consume", "issue_event_id", str(uuid4())), ("consume", "credential_binding_verified", 0),
    ("consume", "rotation_event_id", "invalid"), ("consume", "password", "PRIVATE-MARKER"),
])
def test_same_session_malformed_committed_reply_stops_unknown(same_session_actions: Any, tmp_path: Path,
                                                            action: str, field: str, value: Any) -> None:
    import json

    from delivery_toolchain.release import recover_dev_smoke as r

    def corrupt(name, payload):
        if name == action:
            if name == "reserve":
                payload["reservation"][field] = value
            else:
                payload[field] = value
        return payload
    with same_session_actions(reply_filter=corrupt) as (actions, budget, clock, custody, receipts, p, ids, calls):
        if action != "reserve":
            actions.reserve()
        if action == "consume":
            actions.issue()
        operation = {"reserve": actions.reserve, "issue": actions.issue, "consume": actions.rotate}[action]
        with pytest.raises(r.RecoveryActionRefused) as caught:
            operation()
        assert caught.value.outcome == "unknown" and caught.value.http_status == 200
        assert "PRIVATE-MARKER" not in repr(caught.value)
        before = len(calls)
        with pytest.raises(r.RecoveryActionRefused):
            operation()
        assert len(calls) == before
        if action == "reserve":
            with pytest.raises(r.RecoveryActionRefused) as caught:
                actions.quarantine()
            assert caught.value.outcome == "unknown" and len(calls) == before
        else:
            actions.quarantine()  # only proven root, SAME original session
        records = [json.loads(path.read_text()) for path in (tmp_path / p["execution_id"]).glob("*.json")]
        assert any(v["outcome"] == "unknown" for v in records)


@pytest.mark.parametrize("failure", ["start_storage", "issue_terminal_storage", "custody", "fork"])
def test_same_session_storage_custody_pid_failure_never_continues(same_session_actions: Any,
                                                                monkeypatch: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r

    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, calls):
        if failure != "start_storage":
            actions.reserve()
        append = receipts.append
        def broken(**kwargs):
            if failure == "start_storage" or (kwargs["phase"] == "issue" and kwargs["stage"] == "terminal"):
                raise OSError("PRIVATE-STORAGE-MARKER")
            append(**kwargs)
        if "storage" in failure:
            monkeypatch.setattr(receipts, "append", broken)
        elif failure == "custody":
            custody.close()
        else:
            actions._pid -= 1
        operation = actions.reserve if failure == "start_storage" else actions.issue
        with pytest.raises(r.RecoveryActionRefused) as caught:
            operation()
        assert "PRIVATE-STORAGE-MARKER" not in repr(caught.value)
        assert len(calls) == (0 if failure == "start_storage" else 2 if failure == "issue_terminal_storage" else 1)
        before = len(calls)
        with pytest.raises(r.RecoveryActionRefused):
            actions.rotate()
        assert len(calls) == before
        if failure == "issue_terminal_storage":
            assert custody.capability == "s" * 43  # retained memory is NOT resume authority


def test_same_session_real_tls_validated_reserve_and_unknown_reply(same_session_actions: Any,
                                                                  tls_recovery_server: Any) -> None:
    import json

    from delivery_toolchain.release import recover_dev_smoke as r

    with same_session_actions(real_https=True) as (actions, budget, clock, custody, receipts, p, ids, calls):
        root = RecoveryReservation(p["execution_id"], actions._digest, ids["root"], "reserved").to_receipt()
        with tls_recovery_server(recovery_http_reply(json.dumps({"reservation": root}).encode())) as (origin, ca, observed):
            actions._origin, actions._ca = origin, ca  # isolated independently trusted fixture pins
            actions.reserve()
        assert len(observed) == 1
        assert json.loads(observed[0][1]) == {"action": "reserve", "plan": p}
        with tls_recovery_server(b"") as (origin, ca, observed):
            actions._origin, actions._ca = origin, ca
            with pytest.raises(r.RecoveryActionRefused) as caught:
                actions.issue()
            assert caught.value.outcome == "unknown" and caught.value.http_status is None
            with pytest.raises(r.RecoveryActionRefused):
                actions.issue()
        assert len(observed) == 1  # server saw POST; absent reply may have committed


@pytest.mark.parametrize("action", ["issue", "consume"])
@pytest.mark.parametrize("failure", ["lost", "reused_event", "late_parse"])
def test_same_session_unknown_issue_or_rotation_only_quarantines(same_session_actions: Any,
                                                               action: str, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r

    state = {}
    def uncertain(name, payload):
        if name == action:
            if failure == "lost":
                raise r.RecoveryHTTPRefused(outcome="unknown", http_status=None)
            if failure == "late_parse":
                state["clock"].value += 120  # same deadline cannot refresh after parsing
            else:
                key = "audit_event_id" if name == "issue" else "rotation_event_id"
                payload[key] = state["root_id"]
        return payload
    with same_session_actions(reply_filter=uncertain) as (actions, budget, clock, custody, receipts, p, ids, calls):
        state.update(clock=clock, root_id=ids["root"])
        actions.reserve()
        if action == "consume":
            actions.issue()
        operation = actions.issue if action == "issue" else actions.rotate
        with pytest.raises(r.RecoveryActionRefused) as caught:
            operation()
        assert caught.value.outcome == "unknown"
        before = len(calls)
        for normal in (actions.reserve, actions.issue, actions.rotate):
            with pytest.raises(r.RecoveryActionRefused):
                normal()
        assert len(calls) == before
        actions.quarantine()
        assert calls[-1][3]["action"] == "quarantine" and len(calls) == before + 1


@pytest.fixture
def recovery_secret_store(monkeypatch: Any):
    """Mocked GitHub metadata plus an offline sealed-box key, NEVER authority.

    Decryption by this fixture proves ciphertext composition only; GitHub cannot
    retrieve a plaintext secret. Real loopback TLS PUT tests are separate below.
    """
    import base64
    from contextlib import contextmanager

    from nacl.public import PrivateKey

    from delivery_toolchain.release import recover_dev_smoke as r

    @contextmanager
    def setup(*, status=201, fault="", real_put=False):
        key = PrivateKey.generate()
        public = base64.b64encode(bytes(key.public_key)).decode()
        store = r.RecoveryGitHubSecretAttempt(token="offline-gh-token", custodian_login="bounded-custodian",
                    custodian_id=123, repository_id=456, key_id="offline-key-id", public_key=public,
                    address="127.0.0.1")
        calls = []
        original = store._request
        def request(method, path, *, deadline, body=None):
            calls.append((method, path, deadline, body))
            if method == "PUT":
                if real_put:
                    return original(method, path, deadline=deadline, body=body)
                if fault == "lost":
                    raise r.RecoveryHTTPRefused(outcome="unknown", http_status=None)
                return r.RecoveryHTTPResult(status, {})
            if path == "/user":
                payload = {"type": "User", "id": 123, "login": "bounded-custodian"}
                if fault == "custodian":
                    payload["id"] = 999
            elif path.startswith("/repos/"):
                payload = {"full_name": "alfloop-dev/odayplus", "id": 456}
                if fault == "repository":
                    payload["id"] = 999
            elif path.endswith("/public-key"):
                payload = {"key_id": "offline-key-id", "key": public}
                if fault == "key":
                    payload["key_id"] = "changed-key"
            else:
                return r.RecoveryHTTPResult(200 if fault == "exists" else 404, {})
            return r.RecoveryHTTPResult(200, payload)
        monkeypatch.setattr(store, "_request", request)
        try:
            yield store, key, calls
        finally:
            store.close()
    return setup


def reconcile_and_intent(actions: Any, budget: Any) -> dict:
    actions.reserve()
    actions.issue()
    actions.rotate()
    budget.begin("reconcile")
    budget.finish(outcome="completed")  # explicitly synthetic, NOT actual fresh login proof
    return actions.binding_intent()


def test_same_pair_durable_intent_sealed_put_original201_then_ack(same_session_actions: Any,
        recovery_secret_store: Any, tmp_path: Path) -> None:
    import base64
    import json
    import pickle

    from nacl.public import SealedBox

    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle
    from delivery_toolchain.release.recover_dev_smoke import RecoveryActionRefused

    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store() as (store, key, calls):
            prepared = store.prepare(deadline=clock.value + 10)
            assert prepared["execution_authorized"] is False
            intent = reconcile_and_intent(actions, budget)
            receipt = store.put(actions)
            assert receipt["http_status"] == 201 and receipt["credential_binding_verified"] is False
            put = [c for c in calls if c[0] == "PUT"]
            assert len(put) == 1 and len(calls) == 9  # four metadata GETs twice, ONE PUT
            assert len({c[2] for c in calls[4:]}) == 1  # no per-request allowance refresh
            clear = SealedBox(key).decrypt(base64.b64decode(put[0][3]["encrypted_value"]))
            bundle = read_dev_credential_bundle(clear.decode(), environment="dev", release_profile="dev-admin")
            assert bundle.password == custody.password and bundle.username == "odp-dev-smoke"
            assert bundle.recovery_lineage.binding_intent_event_id == intent["audit_event_id"]
            assert bundle.recovery_lineage.rotation_event_id == ids["rotate"]
            assert "token" not in json.loads(clear) and "ajoe734" not in clear.decode()
            ack = actions.binding_acknowledge()
            assert ack["put_status"] == 201 and ack["issuer_session_id"] == intent["issuer_session_id"]
            assert action_calls[-1][3] == {"action": "binding-acknowledged", "reservation": actions._root,
                                          "intent_event_id": ids["intent"], "put_status": 201}
            text = "".join(path.read_text() for path in (tmp_path / p["execution_id"]).glob("*.json"))
            assert all(marker not in text for marker in (custody.password, custody.capability, "offline-gh-token"))
            assert "offline-gh-token" not in repr(store)
            with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
                pickle.dumps(store)
            with pytest.raises(RecoveryActionRefused):
                store.put(actions)
            assert len([c for c in calls if c[0] == "PUT"]) == 1


@pytest.mark.parametrize("status,fault", [(204, ""), (500, ""), (201, "lost")])
def test_unknown_put_never_ack_retries_or_reconstructs(same_session_actions: Any,
        recovery_secret_store: Any, status: int, fault: str) -> None:
    from delivery_toolchain.release.recover_dev_smoke import RecoveryActionRefused
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store(status=status, fault=fault) as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            reconcile_and_intent(actions, budget)
            with pytest.raises(RecoveryActionRefused) as caught:
                store.put(actions)
            assert caught.value.outcome == "unknown"
            with pytest.raises(RecoveryActionRefused):
                actions.binding_acknowledge()
            with pytest.raises(RecoveryActionRefused):
                store.put(actions)
            assert len([c for c in calls if c[0] == "PUT"]) == 1
            actions.quarantine()
            assert action_calls[-1][3]["action"] == "quarantine"
            assert all(c[3]["action"] != "binding-acknowledged" for c in action_calls)


@pytest.mark.parametrize("fault", ["custodian", "repository", "key", "exists"])
def test_preflight_pins_absence_fail_before_put_no_retry(recovery_secret_store: Any, fault: str) -> None:
    import time
    with recovery_secret_store(fault=fault) as (store, key, calls):
        with pytest.raises(RecoveryRefused, match="PREFLIGHT_REFUSED"):
            store.prepare(deadline=time.monotonic() + 5)
        before = len(calls)
        with pytest.raises(RecoveryRefused):
            store.prepare(deadline=time.monotonic() + 5)
        assert len(calls) == before and not any(c[0] != "GET" for c in calls)


@pytest.mark.parametrize("phase", ["intent", "put", "ack"])
def test_binding_terminal_storage_failure_poison_ack_even_after_real201(same_session_actions: Any,
        recovery_secret_store: Any, monkeypatch: Any, phase: str) -> None:
    from delivery_toolchain.release.recover_dev_smoke import RecoveryActionRefused
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store() as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            actions.reserve()
            actions.issue()
            actions.rotate()
            budget.begin("reconcile")
            budget.finish(outcome="completed")
            original = receipts.append
            def broken(**kwargs):
                if kwargs["phase"] == phase and kwargs["stage"] == "terminal":
                    raise OSError("PRIVATE-MARKER")
                original(**kwargs)
            monkeypatch.setattr(receipts, "append", broken)
            if phase != "intent":
                actions.binding_intent()
            if phase == "ack":
                store.put(actions)
            operation = actions.binding_intent if phase == "intent" else (lambda: store.put(actions)) if phase == "put" else actions.binding_acknowledge
            with pytest.raises(RecoveryActionRefused) as caught:
                operation()
            assert "PRIVATE-MARKER" not in repr(caught.value)
            with pytest.raises(RecoveryActionRefused):
                actions.binding_acknowledge()
            assert len([c for c in calls if c[0] == "PUT"]) == (0 if phase == "intent" else 1)


@pytest.mark.parametrize("field,value", [
    ("root_event_id", str(uuid4())), ("rotation_event_id", str(uuid4())),
    ("issuer_session_id", "not-a-session"), ("put_status", True),
    ("credential_binding_verified", 0), ("password", "PRIVATE-MARKER"),
])
def test_binding_reply_lineage_and_exact_shape_refuse(same_session_actions: Any, field: str, value: Any) -> None:
    from delivery_toolchain.release.recover_dev_smoke import RecoveryActionRefused
    def corrupt(action, payload):
        if action == "binding-intent":
            payload["binding"][field] = value
        return payload
    with same_session_actions(reply_filter=corrupt) as (actions, budget, clock, custody, receipts, p, ids, calls):
        with pytest.raises(RecoveryActionRefused) as caught:
            reconcile_and_intent(actions, budget)
        assert caught.value.outcome == "unknown"
        assert actions._intent is None
        with pytest.raises(RecoveryActionRefused):
            actions.binding_acknowledge()
        assert all(c[3]["action"] != "binding-acknowledged" for c in calls)


@pytest.mark.parametrize("reply_kind", ["201", "lost", "204", "redirect", "truncated201"])
def test_real_tls_encrypted_put_status_and_complete_reply_fence(same_session_actions: Any,
        recovery_secret_store: Any, tls_recovery_server: Any, reply_kind: str) -> None:
    import base64
    import json

    from nacl.public import SealedBox

    from delivery_toolchain.release import recover_dev_smoke as r
    reply = {"201": recovery_http_reply(b"", status=201), "lost": b"",
             "204": recovery_http_reply(b"", status=204), "redirect": recovery_http_reply(status=302),
             "truncated201": recovery_http_reply(b'{"ok":true}', status=201)[:-2]}[reply_kind]
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store(real_put=True) as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            reconcile_and_intent(actions, budget)
            with tls_recovery_server(reply) as (origin, ca, observed):
                # ONLY isolated loopback certificate; production origin is fixed
                # api.github.com with verified normal CA/hostname/SNI.
                store._transport = r.RecoveryHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
                if reply_kind == "201":
                    store.put(actions)
                    actions.binding_acknowledge()
                else:
                    with pytest.raises(r.RecoveryActionRefused) as caught:
                        store.put(actions)
                    assert caught.value.outcome == "unknown"
                    with pytest.raises(r.RecoveryActionRefused):
                        actions.binding_acknowledge()
            assert len(observed) == 1
            header, body = observed[0]
            assert header.startswith(b"PUT /repositories/456/environments/dev/secrets/ODP_DEV_ADMIN_CREDENTIAL_BUNDLE ")
            encrypted = json.loads(body)
            assert set(encrypted) == {"key_id", "encrypted_value"}
            clear = json.loads(SealedBox(key).decrypt(base64.b64decode(encrypted["encrypted_value"])))
            assert clear["password"] == custody.password
            assert custody.password.encode() not in body and custody.capability.encode() not in body
            with pytest.raises(r.RecoveryActionRefused):
                store.put(actions)
            assert len([c for c in calls if c[0] == "PUT"]) == 1


@pytest.mark.parametrize("fault", ["key", "custodian", "repository", "exists", "late"])
def test_fresh_observation_drift_stops_before_put_after_intent(same_session_actions: Any,
        recovery_secret_store: Any, monkeypatch: Any, fault: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store() as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            reconcile_and_intent(actions, budget)
            original = store._request
            def drift(method, path, **kwargs):
                result = original(method, path, **kwargs)
                if fault == "key" and path.endswith("/public-key"):
                    result.payload["key"] = "changed-key"
                elif fault == "custodian" and path == "/user":
                    result.payload["id"] = 999
                elif fault == "repository" and path.startswith("/repos/"):
                    result.payload["id"] = 999
                elif fault == "exists" and path == store._secret_path:
                    return r.RecoveryHTTPResult(200, {})
                elif fault == "late":
                    clock.value += 81
                return result
            monkeypatch.setattr(store, "_request", drift)
            with pytest.raises(r.RecoveryActionRefused):
                store.put(actions)
            assert not any(c[0] == "PUT" for c in calls)
            with pytest.raises(r.RecoveryActionRefused):
                actions.binding_acknowledge()
            actions.quarantine()
            assert action_calls[-1][3]["action"] == "quarantine"


def test_repeated_prepare_poison_prevents_put_even_after_passing_observation(same_session_actions: Any,
        recovery_secret_store: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store() as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            with pytest.raises(RecoveryRefused):
                store.prepare(deadline=clock.value + 10)
            reconcile_and_intent(actions, budget)
            with pytest.raises(r.RecoveryActionRefused):
                store.put(actions)
            assert not any(c[0] == "PUT" for c in calls)


def test_no_public_ack_status_or_intent_skip(same_session_actions: Any, recovery_secret_store: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with same_session_actions() as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        reconcile_and_intent(actions, budget)
        with pytest.raises(r.RecoveryActionRefused):
            actions.binding_acknowledge()  # intent alone can never acknowledge PUT
        assert not any(c[3]["action"] == "binding-acknowledged" for c in action_calls)
        with pytest.raises(TypeError):
            actions.binding_acknowledge(put_status=201)


@pytest.mark.parametrize("failure", ["lost", "wrong_session", "wrong_status", "extra"])
def test_ack_uncertainty_after201_never_resumes(same_session_actions: Any,
        recovery_secret_store: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    def corrupt(action, payload):
        if action == "binding-acknowledged":
            if failure == "lost":
                raise r.RecoveryHTTPRefused(outcome="unknown", http_status=None)
            if failure == "wrong_session":
                payload["binding"]["issuer_session_id"] = str(uuid4())
            elif failure == "wrong_status":
                payload["binding"]["put_status"] = 204
            elif failure == "extra":
                payload["binding"]["password"] = "PRIVATE-MARKER"
        return payload
    with same_session_actions(reply_filter=corrupt) as (actions, budget, clock, custody, receipts, p, ids, action_calls):
        with recovery_secret_store() as (store, key, calls):
            store.prepare(deadline=clock.value + 10)
            reconcile_and_intent(actions, budget)
            store.put(actions)
            with pytest.raises(r.RecoveryActionRefused) as caught:
                actions.binding_acknowledge()
            assert caught.value.outcome == "unknown" and "PRIVATE-MARKER" not in repr(caught.value)
            before = len(action_calls)
            with pytest.raises(r.RecoveryActionRefused):
                actions.binding_acknowledge()
            with pytest.raises(r.RecoveryActionRefused):
                store.put(actions)
            assert len(action_calls) == before and len([c for c in calls if c[0] == "PUT"]) == 1
            actions.quarantine()


@pytest.mark.parametrize("phase", ["headers", "handshake"])
def test_real_github_tls_absolute_deadline_with_dribble(tls_recovery_server: Any, phase: str) -> None:
    import base64
    import time

    from nacl.public import PrivateKey

    from delivery_toolchain.release import recover_dev_smoke as r
    public = base64.b64encode(bytes(PrivateKey.generate().public_key)).decode()
    store = r.RecoveryGitHubSecretAttempt(token="offline-gh-token", custodian_login="bounded-custodian",
                custodian_id=123, repository_id=456, key_id="offline-key-id", public_key=public,
                address="127.0.0.1")
    try:
        with tls_recovery_server(recovery_http_reply(b"", status=201), dribble=phase == "headers",
                                 handshake_pause=0.7 if phase == "handshake" else 0) as (origin, ca, observed):
            store._transport = r.RecoveryHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
            start = time.monotonic()
            with pytest.raises(r.RecoveryHTTPRefused) as caught:
                store._request("PUT", store._secret_path, deadline=start + 0.25,
                               body={"key_id": "offline-key-id", "encrypted_value": "OFFLINE-CIPHERTEXT"})
            assert time.monotonic() - start < 0.6
            assert caught.value.outcome == ("unknown" if phase == "headers" else "refused")
            assert caught.value.http_status is None
        assert len(observed) == (1 if phase == "headers" else 0)
    finally:
        store.close()


@pytest.fixture
def owned_sessions(tmp_path: Path, monkeypatch: Any):
    from contextlib import contextmanager

    from delivery_toolchain.release import recover_dev_smoke as r
    from shared.identity.dev_smoke_journal import PRESERVED_ACCOUNT_ID
    from shared.identity.dev_smoke_recovery import TARGET_ACCOUNT_ID, TENANT_ID

    @contextmanager
    def setup(failure=""):
        budget, clock, receipts, p = foreground_budget(tmp_path, monkeypatch)
        original = r.OriginalPasswordMemory("OFFLINE-ORIGINAL-PASSWORD")
        target = r.ParentMemoryCustody()
        owner = r.ForegroundOwnedWebSessions(original=original, target=target, budget=budget,
                    origin="https://offline.invalid", address="127.0.0.1")
        calls = []
        def execute(self, operation, *, cookie, deadline, username="", password=""):
            calls.append((operation, cookie, deadline, username, password))
            role = "original" if username == "ajoe734" or cookie == "offline-original-cookie" else "target"
            subject = "ajoe734" if role == "original" else "odp-dev-smoke"
            if operation == "login":
                assert password == (original.password if role == "original" else target.password)
                if failure == "login_lost":
                    raise r.RecoveryHTTPRefused(outcome="unknown", http_status=None)
                if failure == "body_lost":
                    raise r.RecoveryHTTPRefused(outcome="unknown", http_status=200, cookie=f"offline-{role}-cookie")
                return r.RecoveryHTTPResult(200, {"ok": True, "subject": subject,
                    "returnTo": "/operator?view=admin"}, f"offline-{role}-cookie")
            assert cookie in {"offline-original-cookie", "offline-target-cookie"}
            if operation == "session":
                return r.RecoveryHTTPResult(200, {"subject": "wrong" if failure == "subject" else subject,
                                                  "expiresAt": 2**40})
            if operation == "principal":
                return r.RecoveryHTTPResult(200, {"account_id": PRESERVED_ACCOUNT_ID if role == "original" else TARGET_ACCOUNT_ID,
                    "tenant_id": "wrong" if failure == "tenant" else TENANT_ID,
                    "roles": (["operations_manager", "platform_admin"] if failure == "roles" else
                              ["auditor", "platform_admin"] if role == "original" else ["platform_admin"])})
            if operation == "logout":
                if failure == "logout" or (failure == "target_logout" and role == "target"):
                    raise r.RecoveryHTTPRefused(outcome="unknown", http_status=503)
                return r.RecoveryHTTPResult(200, {"ok": True})
            return r.RecoveryHTTPResult(401, {"error": {"code": "wrong" if failure == "revoked" else "WEB_SESSION_REQUIRED"}})
        monkeypatch.setattr(r.OwnedSessionHTTPSAttempt, "execute_session", execute)
        try:
            yield owner, budget, clock, receipts, p, calls, original, target
        finally:
            owner.close()
            original.close()
            target.close()
            receipts.close()
    return setup


def test_owned_session_pair_once_durable_same_cookie_cleanup_and_redaction(owned_sessions: Any,
                                                                        tmp_path: Path) -> None:
    import json
    import pickle
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        owner.login("original")
        assert budget.finish(outcome="completed") == "completed"
        actions = owner.recovery_actions(p)
        assert actions._cookie == "offline-original-cookie"
        with pytest.raises(RecoveryRefused):
            owner.recovery_actions(p)
        for phase in ("reserve", "issue", "rotate"):  # synthetic, NOT execution proof
            budget.begin(phase)
            budget.finish(outcome="completed")
        budget.begin("reconcile")
        owner.login("target")
        budget.finish(outcome="completed")
        budget.begin("quarantine")
        budget.finish(outcome="completed")  # synthetic, not server quarantine
        safe = owner.cleanup()
        assert safe["own_session_cleanup"] == "completed"
        assert safe["original_incident_session_cleanup"] == "unknown"
        assert [c[0] for c in calls] == ["login", "session", "principal"] * 2 + ["logout", "revoked"] * 2
        assert [c[1] for c in calls[-4:]] == ["offline-target-cookie"] * 2 + ["offline-original-cookie"] * 2
        text = "".join(f.read_text() for f in (tmp_path / p["execution_id"]).glob("*.json"))
        assert all(secret not in text + repr(owner) + repr(original) + repr(safe)
                   for secret in (original.password, target.password, "offline-original-cookie", "offline-target-cookie"))
        records = [json.loads(f.read_text()) for f in sorted((tmp_path / p["execution_id"]).glob("*.json"))]
        assert [v["stage"] for v in records if v["operation"] == "original-login"] == ["start", "terminal"]
        for value in (original, owner):
            with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
                pickle.dumps(value)
        with pytest.raises(RecoveryRefused):
            owner.cleanup()


@pytest.mark.parametrize("failure", ["login_lost", "body_lost", "subject", "tenant", "roles", "logout", "revoked"])
def test_owned_session_uncertainty_no_retry_or_blanket_cleanup(owned_sessions: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with owned_sessions(failure) as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        if failure in {"logout", "revoked"}:
            owner.login("original")
            budget.finish(outcome="completed")
            budget.begin("quarantine")
            budget.finish(outcome="completed")
        else:
            with pytest.raises(r.RecoveryActionRefused):
                owner.login("original")
            with pytest.raises(RecoveryRefused):
                owner.recovery_actions(p)
            with pytest.raises(r.RecoveryActionRefused):
                owner.login("original")
        with pytest.raises(r.RecoveryActionRefused, match="STOPPED"):
            owner.cleanup()
        assert sum(c[0] == "login" for c in calls) == 1
        assert sum(c[0] == "logout" for c in calls) == (0 if failure == "login_lost" else 1)
        assert all(c[1] in {"", "offline-original-cookie"} for c in calls)


def test_owned_cleanup_failure_still_attempts_only_other_known_session_once(owned_sessions: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with owned_sessions("target_logout") as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        owner.login("original")
        budget.finish(outcome="completed")
        for phase in ("reserve", "issue", "rotate"):
            budget.begin(phase)
            budget.finish(outcome="completed")
        budget.begin("reconcile")
        owner.login("target")
        budget.finish(outcome="completed")
        budget.begin("quarantine")
        budget.finish(outcome="completed")
        with pytest.raises(r.RecoveryActionRefused):
            owner.cleanup()
        assert [(c[0], c[1]) for c in calls[-3:]] == [("logout", "offline-target-cookie"),
                    ("logout", "offline-original-cookie"), ("revoked", "offline-original-cookie")]
        assert owner._cookies == {"original": "", "target": ""}


def test_original_memory_pid_close_and_invalid_shape_never_echo(monkeypatch: Any) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    for value in (None, 123, "", "OFFLINE-PRIVATE-MARKER" * 100):
        with pytest.raises(RecoveryRefused) as caught:
            r.OriginalPasswordMemory(value)
        assert "OFFLINE-PRIVATE-MARKER" not in str(caught.value)
    original = r.OriginalPasswordMemory("OFFLINE-PRIVATE-MARKER")
    assert "OFFLINE-PRIVATE-MARKER" not in repr(original)
    monkeypatch.setattr(r.os, "getpid", lambda: original._pid + 1)
    with pytest.raises(RecoveryRefused, match="CUSTODY_LOST"):
        _ = original.password
    original.close()
    with pytest.raises(RecoveryRefused, match="CUSTODY_LOST"):
        _ = original.password


@pytest.mark.parametrize("failure", ["start", "terminal", "fork", "custody", "deadline"])
def test_owned_session_receipt_custody_deadline_fail_stop(owned_sessions: Any, monkeypatch: Any, failure: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        budget.begin("preflight")
        append = r.ExclusivePhaseReceipts.append
        if failure in {"start", "terminal"}:
            def broken(self, **kwargs):
                if kwargs.get("operation") == "original-login" and kwargs["stage"] == failure:
                    raise OSError("OFFLINE-PRIVATE-MARKER")
                return append(self, **kwargs)
            monkeypatch.setattr(r.ExclusivePhaseReceipts, "append", broken)
        elif failure == "fork":
            monkeypatch.setattr(r.os, "getpid", lambda: owner._pid + 1)
        elif failure == "custody":
            original.close()
        else:
            clock.value += 601
        with pytest.raises(r.RecoveryActionRefused) as caught:
            owner.login("original")
        assert "OFFLINE-PRIVATE-MARKER" not in str(caught.value)
        assert len(calls) == (3 if failure == "terminal" else 0)
        with pytest.raises(RecoveryRefused):
            owner.recovery_actions(p)


@pytest.mark.parametrize("bad_cookie", [False, True])
def test_real_owned_login_https_csrf_cookie_validation_and_one_attempt(tls_recovery_server: Any,
                                                                       bad_cookie: bool) -> None:
    import json
    import time

    from delivery_toolchain.release import recover_dev_smoke as r
    extra = (b"Set-Cookie: __Host-oday_web_session=offline-cookie; Path=/; HttpOnly; SameSite=Lax" +
             (b"; Domain=localhost" if bad_cookie else b"; Secure") + b"\r\n")
    with tls_recovery_server(recovery_http_reply(b'{"ok":true,"subject":"ajoe734","returnTo":"/operator?view=admin"}',
                                               extra=extra)) as (origin, ca, observed):
        attempt = r.OwnedSessionHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        if bad_cookie:
            with pytest.raises(r.RecoveryHTTPRefused) as caught:
                attempt.execute_session("login", cookie="", username="ajoe734", password="OFFLINE-PASSWORD",
                                        deadline=time.monotonic() + 2)
            assert caught.value.outcome == "unknown" and caught.value._cookie == ""
        else:
            result = attempt.execute_session("login", cookie="", username="ajoe734", password="OFFLINE-PASSWORD",
                                             deadline=time.monotonic() + 2)
            assert result.cookie == "offline-cookie" and "offline-cookie" not in repr(result)
        with pytest.raises(r.RecoveryHTTPRefused):
            attempt.execute_session("login", cookie="", username="ajoe734", password="OFFLINE-PASSWORD",
                                    deadline=time.monotonic() + 2)
    assert len(observed) == 1
    headers, body = observed[0]
    assert headers.startswith(b"POST /login HTTP/1.1") and b"Cookie:" not in headers
    assert f"Origin: {origin}".encode() in headers
    assert json.loads(body)["password"] == "OFFLINE-PASSWORD"


def test_real_owned_login_lost_body_retains_only_new_valid_cookie(tls_recovery_server: Any) -> None:
    import pickle
    import time

    from delivery_toolchain.release import recover_dev_smoke as r
    reply = (b"HTTP/1.1 200 fixture\r\nSet-Cookie: __Host-oday_web_session=offline-cookie; Path=/; Secure; HttpOnly; SameSite=Lax\r\n"
             b"Content-Length: 500\r\nContent-Type: application/json\r\nCache-Control: no-store\r\n\r\n{\"ok\":")
    with tls_recovery_server(reply) as (origin, ca, observed):
        attempt = r.OwnedSessionHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        with pytest.raises(r.RecoveryHTTPRefused) as caught:
            attempt.execute_session("login", cookie="", username="ajoe734", password="OFFLINE-PASSWORD",
                                    deadline=time.monotonic() + 2)
        assert caught.value._cookie == "offline-cookie" and caught.value.outcome == "unknown"
        assert "offline-cookie" not in str(caught.value) + repr(caught.value)
        with pytest.raises(RecoveryRefused, match="SERIALIZATION_REFUSED"):
            pickle.dumps(caught.value)
    assert len(observed) == 1


def account_observer_payloads(p: dict, actions=None) -> dict:
    """Synthetic safe readbacks; NOT real account/source/admission evidence."""
    from copy import deepcopy

    from shared.identity import dev_smoke_recovery as d
    bundle, events, target = recovery_consumer_inputs()
    now = datetime.now(UTC)
    fixture_now = datetime(2026, 10, 11, 5, tzinfo=UTC)
    for event in events:
        event["timestamp"] = (datetime.fromisoformat(event["timestamp"]) + (now - fixture_now - timedelta(seconds=60))).isoformat()
    events[1]["metadata"]["expires_at"] = (now + timedelta(hours=1)).isoformat()
    original = deepcopy(target)
    original.update(subject_id=p["actor_account_id"], username="ajoe734", roles=["auditor", "platform_admin"],
                    email="original@offline.invalid", name="Original", updated_by=None)
    target.update(email="target@offline.invalid", name="Target")
    old_root = {"execution_id": d.OLD_EXECUTION_ID, "plan_digest": d.OLD_PLAN_DIGEST,
        "event_id": d.OLD_QUARANTINE_EVENT_ID, "stage": "recovery-required",
        "authorization_id": events[0]["metadata"]["authorization_id"], "execution_authorized": False,
        "secret_values_redacted": True}
    if actions is not None:
        for event in events[4:7]:
            event["metadata"].update(p, plan_digest=actions._digest)
        events[4]["event_id"] = actions._root["event_id"]
        for event in events[5:7]:
            event["metadata"].update(root_event_id=actions._root["event_id"], capability_id=actions._capability_id,
                capability_expires_at=(now + timedelta(seconds=540)).isoformat())
        events[5]["event_id"], events[6]["event_id"] = actions._issue_event_id, actions._rotation_event_id
        events[5]["metadata"]["issue_event_id"] = ""
        events[6]["metadata"]["issue_event_id"] = actions._issue_event_id
    events = events[:4 if actions is None else 7]
    return {
        "release": {"release_sha": p["release_sha"], "web_release_sha": p["release_sha"],
            "manifest_digest": p["manifest_digest"], "web_manifest_digest": p["manifest_digest"],
            "release_profile": "dev-admin", "web_release_profile": "dev-admin", "release_profile_valid": True},
        "users": {"users": [original, target], "count": 2, "correlation_id": "offline"},
        "audit": {"events": events, "count": len(events), "correlation_id": "offline"},
        "old-journal": {"reservation": old_root, "binding": None,
                        "execution_authorized": False, "secret_values_redacted": True},
        "recovery": {"reservation": None if actions is None else dict(actions._root), "binding": None,
                     "execution_authorized": False, "secret_values_redacted": True},
    }


def install_account_observer(monkeypatch: Any, payloads: dict, calls: list) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    def observe(self, operation, *, cookie, deadline):
        assert cookie == "offline-original-cookie"
        calls.append((operation, deadline))
        return r.RecoveryHTTPResult(200, payloads[operation])
    monkeypatch.setattr(r.RecoveryObservationHTTPSAttempt, "observe", observe)


def test_account_preflight_reconciliation_same_owner_and_memory_baseline(owned_sessions: Any,
                                                                       monkeypatch: Any, tmp_path: Path) -> None:
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        reads = []
        payloads = account_observer_payloads(p)
        install_account_observer(monkeypatch, payloads, reads)
        budget.begin("preflight")
        owner.login("original")
        safe = owner.observe_accounts(p)
        assert safe["stage"] == "account-preflight-observed"
        budget.finish(outcome="completed")
        actions = owner.recovery_actions(p)
        actions._root = RecoveryReservation(p["execution_id"], actions._digest, str(uuid4()), "reserved").to_receipt()
        actions._capability_id, actions._issue_event_id, actions._rotation_event_id = [str(uuid4()) for _ in range(3)]
        for phase in ("reserve", "issue", "rotate"):
            budget.begin(phase)
            budget.finish(outcome="completed")  # synthetic, no server effect/proof
        budget.begin("reconcile")
        owner.login("target")
        payloads.update(account_observer_payloads(p, actions))
        safe = owner.observe_accounts(p, actions=actions)
        assert safe["stage"] == "rotation-reconciled"
        budget.finish(outcome="completed")
        assert [v[0] for v in reads] == ["release", "users", "audit", "old-journal", "recovery"] * 2
        assert len({v[1] for v in reads[:5]}) == len({v[1] for v in reads[5:]}) == 1
        assert all(secret not in repr(safe) + repr(owner) for secret in (original.password, target.password))
        text = "".join(f.read_text() for f in (tmp_path / p["execution_id"]).glob("*.json"))
        assert "original@offline.invalid" not in text and "offline-original-cookie" not in text
        assert '"operation":"original-observe"' in text and '"operation":"target-observe"' in text


@pytest.mark.parametrize("drift", ["web", "digest", "tenant", "roles", "status", "count", "duplicate",
    "quarantine", "old_binding", "accept", "bootstrap", "pending", "malformed", "audit_count"])
def test_account_observation_drift_stops_before_reservation(owned_sessions: Any, monkeypatch: Any, drift: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        payloads = account_observer_payloads(p)
        records, events = payloads["users"]["users"], payloads["audit"]["events"]
        if drift == "web":
            payloads["release"]["web_release_sha"] = "b" * 40
        elif drift == "digest":
            payloads["release"]["web_manifest_digest"] = ""
        elif drift == "tenant":
            records[1]["scope"]["tenant_id"] = str(uuid4())
        elif drift == "roles":
            records[0]["roles"] = ["operations_manager", "platform_admin"]
        elif drift == "status":
            records[1]["status"] = "disabled"
        elif drift == "count":
            payloads["users"]["count"] = True
        elif drift == "duplicate":
            events.append(events[0])
            payloads["audit"]["count"] += 1
        elif drift == "quarantine":
            payloads["old-journal"]["reservation"]["event_id"] = str(uuid4())
        elif drift == "old_binding":
            payloads["old-journal"]["binding"] = {"stage": "binding-acknowledged"}
        elif drift == "accept":
            events[2]["actor"] = p["actor_account_id"]
        elif drift == "bootstrap":
            events.append({"event_id": str(uuid4()), "event_type": "identity.account.bootstrap",
                           "metadata": {"account_id": p["target_account_id"]}})
            payloads["audit"]["count"] += 1
        elif drift == "pending":
            payloads["recovery"]["reservation"] = {"stage": "reserved"}
        elif drift == "audit_count":
            payloads["audit"]["count"] += 1
        else:
            payloads["old-journal"] = {"PRIVATE-MARKER": True}
        install_account_observer(monkeypatch, payloads, [])
        budget.begin("preflight")
        owner.login("original")
        with pytest.raises(r.RecoveryActionRefused) as caught:
            owner.observe_accounts(p)
        assert "PRIVATE-MARKER" not in str(caught.value)
        assert budget._stopped and owner._baseline is None
        with pytest.raises(RecoveryRefused):
            owner.recovery_actions(p)
        assert all(v[0] not in {"logout"} for v in calls)
        # Preflight failed with no root; only its known OWN original cookie is cleaned.
        assert owner.cleanup()["own_session_cleanup"] == "completed"


@pytest.mark.parametrize("drift", ["original", "target", "other", "rotation", "session", "quarantine", "unknown"])
def test_rotation_reconciliation_never_resumes_or_ignores_preservation(owned_sessions: Any,
                                                                      monkeypatch: Any, drift: str) -> None:
    from delivery_toolchain.release import recover_dev_smoke as r
    with owned_sessions() as (owner, budget, clock, receipts, p, calls, original, target):
        payloads = account_observer_payloads(p)
        reads = []
        install_account_observer(monkeypatch, payloads, reads)
        budget.begin("preflight")
        owner.login("original")
        owner.observe_accounts(p)
        budget.finish(outcome="completed")
        actions = owner.recovery_actions(p)
        actions._root = RecoveryReservation(p["execution_id"], actions._digest, str(uuid4()), "reserved").to_receipt()
        actions._capability_id, actions._issue_event_id, actions._rotation_event_id = [str(uuid4()) for _ in range(3)]
        for phase in ("reserve", "issue", "rotate"):
            budget.begin(phase)
            budget.finish(outcome="completed")
        budget.begin("reconcile")
        owner.login("target")
        payloads.update(account_observer_payloads(p, actions))
        if drift in {"original", "target"}:
            payloads["users"]["users"][0 if drift == "original" else 1]["email"] = "changed@offline.invalid"
        elif drift == "other":
            from copy import deepcopy
            other = deepcopy(payloads["users"]["users"][0])
            other["subject_id"] = str(uuid4())
            payloads["users"]["users"].append(other)
            payloads["users"]["count"] += 1
        elif drift == "rotation":
            payloads["audit"]["events"][6]["event_id"] = str(uuid4())
        elif drift == "session":
            payloads["audit"]["events"][6]["metadata"]["issuer_session_id"] = str(uuid4())
        elif drift == "quarantine":
            payloads["recovery"]["reservation"]["stage"] = "recovery-required"
        else:
            actions._stopped = True
        with pytest.raises(r.RecoveryActionRefused):
            owner.observe_accounts(p, actions=actions)
        assert budget._stopped
        with pytest.raises(r.RecoveryActionRefused):
            actions.binding_intent()
        if drift == "unknown":
            assert len(reads) == 5  # no GET can turn an unknown rotation into continuation


@pytest.mark.parametrize("operation,path", [
    ("release", "/api/v1/platform/release-identity"), ("users", "/api/v1/operator/users"),
    ("audit", "/api/v1/operator/users/audit-trail"), ("old-journal", "/api/v1/operator/users/dev-smoke-journal"),
    ("recovery", "/api/v1/operator/users/dev-smoke-recovery"),
])
def test_real_observation_https_fixed_read_routes_no_retry(tls_recovery_server: Any, operation: str, path: str) -> None:
    import time

    from delivery_toolchain.release import recover_dev_smoke as r
    with tls_recovery_server(recovery_http_reply()) as (origin, ca, observed):
        attempt = r.RecoveryObservationHTTPSAttempt(origin=origin, address="127.0.0.1", ca_pem=ca)
        assert attempt.observe(operation, cookie="offline-own-cookie", deadline=time.monotonic() + 3).http_status == 200
        with pytest.raises(r.RecoveryHTTPRefused):
            attempt.observe(operation, cookie="offline-own-cookie", deadline=time.monotonic() + 3)
    assert len(observed) == 1 and observed[0][0].startswith(f"GET {path} HTTP/1.1\r\n".encode())
    assert b"Cookie: __Host-oday_web_session=offline-own-cookie" in observed[0][0]
