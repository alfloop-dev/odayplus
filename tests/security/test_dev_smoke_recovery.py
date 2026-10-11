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
    RecoveryBundleLineage,
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


def recovery_consumer_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Synthetic consumer contracts only; NOT a binding producer/live receipt."""
    from shared.identity import dev_smoke_recovery as r
    from shared.identity.dev_smoke_journal import (
        AUTHORIZATION_ID as old_authorization, PRESERVED_ACCOUNT_ID, TENANT_ID,
        CREDENTIAL_BUNDLE_SECRET_NAME, ProvisioningJournal,
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, ProvisioningRefused
    raw, _, _ = recovery_consumer_inputs()
    raw.update(change)
    with pytest.raises(ProvisioningRefused, match="^PROVISIONING_CREDENTIAL_BUNDLE_INVALID$"):
        read_dev_credential_bundle(json.dumps(raw), environment="dev", release_profile="dev-admin")


@pytest.mark.parametrize("field", list(RecoveryBundleLineage.__dataclass_fields__))
def test_recovery_bundle_all_lineage_fields_required(field: str) -> None:
    import json
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, ProvisioningRefused
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, credential_bundle_acknowledged
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
    from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle, ProvisioningRefused
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
