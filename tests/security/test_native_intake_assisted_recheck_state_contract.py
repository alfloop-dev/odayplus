"""Both waiting edges require an actual verified service command."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.listing.application.native_intake_source_policy_recheck import (
    NativeIntakeSourcePolicyRecheckConflict,
    NativeIntakeSourcePolicyRecheckOwner,
)
from modules.listing.application.native_intake_source_policy_recheck_partition import (
    NativeRecheckPartition,
    _position,
)
from modules.listing.domain.intake_states import (
    Actor,
    DenialCode,
    DomainValidationError,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    PrincipalRole,
    TransitionContext,
)

TENANT = "11111111-1111-4111-8111-111111111111"
ACTOR = "22222222-2222-4222-8222-222222222222"
INTAKE = "33333333-3333-4333-8333-333333333333"
WAITS = [IntakeStage.AWAITING_ASSISTED_ENTRY, IntakeStage.AWAITING_SOURCE_AUTHORIZATION]


def context(role):
    return TransitionContext(actor=Actor(ACTOR, role, TENANT), version_before=6,
        idempotency_key="synthetic-verified-recheck", causation_id="synthetic-request-event",
        reason="Recheck current source policy", source_policy_recheck_verified=True)


def aggregate(stage):
    return IntakeAggregate(id=INTAKE, tenant_id=TENANT, stage=stage, version=6,
        created_by=ACTOR, source_id="synthetic-feed", owner_id=ACTOR)


@pytest.mark.parametrize("stage", WAITS)
@pytest.mark.parametrize("role", list(PrincipalRole))
def test_every_role_on_both_waiting_edges(stage, role):
    intake = aggregate(stage)
    if role in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
        IntakeStateMachine.transition(intake, IntakeStage.CHECKING_SOURCE_POLICY, context(role))
        assert intake.stage == IntakeStage.CHECKING_SOURCE_POLICY and intake.version == 7
    else:
        with pytest.raises(DomainValidationError) as raised:
            IntakeStateMachine.transition(intake, IntakeStage.CHECKING_SOURCE_POLICY, context(role))
        assert raised.value.code == DenialCode.ROLE_DENIED
        assert intake.stage == stage and intake.version == 6


@pytest.mark.parametrize("stage", WAITS)
@pytest.mark.parametrize("role", [PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN])
@pytest.mark.parametrize("missing,value", [("source_policy_recheck_verified", False),
    ("source_policy_recheck_verified", None), ("causation_id", None), ("idempotency_key", None), ("reason", "   ")])
def test_even_emergency_cannot_bypass_any_verified_command_field(stage, role, missing, value):
    intake = aggregate(stage)
    with pytest.raises(DomainValidationError) as raised:
        IntakeStateMachine.transition(intake, IntakeStage.CHECKING_SOURCE_POLICY, replace(context(role), **{missing: value}))
    assert raised.value.code == DenialCode.SOURCE_POLICY_RECHECK_NOT_VERIFIED
    assert intake.stage == stage and intake.version == 6


@pytest.mark.parametrize("state,policy", [("AWAITING_SOURCE_AUTHORIZATION", "AUTH_REQUIRED"),
    ("AWAITING_ASSISTED_ENTRY", "AUTH_REQUIRED"), ("AWAITING_ASSISTED_ENTRY", "ASSISTED_ENTRY_ONLY")])
def test_only_closed_waiting_context_pairs_are_admitted(state, policy):
    assert NativeIntakeSourcePolicyRecheckOwner._waiting_context({"waiting_state": state, "waiting_source_policy": policy}) == (state, policy)


def test_legacy_absence_retains_only_the_old_authorization_wait_context():
    assert NativeIntakeSourcePolicyRecheckOwner._waiting_context({}) == ("AWAITING_SOURCE_AUTHORIZATION", "AUTH_REQUIRED")


@pytest.mark.parametrize("data", [{"waiting_state": "AWAITING_ASSISTED_ENTRY"}, {"waiting_source_policy": "ASSISTED_ENTRY_ONLY"},
    {"waiting_state": "AWAITING_SOURCE_AUTHORIZATION", "waiting_source_policy": "ASSISTED_ENTRY_ONLY"},
    {"waiting_state": "PARSING", "waiting_source_policy": "AUTH_REQUIRED"},
    {"waiting_state": "AWAITING_ASSISTED_ENTRY", "waiting_source_policy": "APPROVED_RETRIEVAL"},
    {"waiting_state": "AWAITING_ASSISTED_ENTRY", "waiting_source_policy": []}])
def test_partial_forged_or_nonwaiting_context_never_widens_legacy_history(data):
    with pytest.raises(NativeIntakeSourcePolicyRecheckConflict):
        NativeIntakeSourcePolicyRecheckOwner._waiting_context(data)


@pytest.mark.parametrize("status", [401, 403, 409])
def test_recheck_scan_continues_after_candidate_authority_or_proof_conflict(monkeypatch, status):
    """Exercise scan orchestration only; no current SQL authority is simulated."""
    first, second = "44444444-4444-4444-8444-444444444444", "55555555-5555-4555-8555-555555555555"
    at = datetime(2026, 10, 17, tzinfo=UTC)
    rows = [{"event_id": event, "occurred_at": at.isoformat()} for event in (first, second)]
    calls = []

    def admit(tenant, event):
        calls.append(event)
        if event == first:
            raise HTTPException(status)
        return {"job_id": "synthetic-admission-receipt"}

    engine = SimpleNamespace()
    owner = SimpleNamespace(graph=SimpleNamespace(engine=engine), engine=engine, admit_event=admit)
    partition = NativeRecheckPartition(owner)
    monkeypatch.setattr(partition, "_rows", lambda tenant, position, limit: rows if position is None else [])
    monkeypatch.setattr(partition, "_withdrawn", lambda *_: False)
    assert list(partition.admit(TENANT, limit=1)) == [{"job_id": "synthetic-admission-receipt"}]
    assert calls == [first, second]
    progress = owner.last_partition_progress
    assert (progress["scanned"], progress["conflicts"], progress["admitted"], progress["has_more"]) == (2, 1, 1, False)
    assert _position(progress["next_cursor"], TENANT) == (at.isoformat(), second)


def test_recheck_scan_preserves_service_outage_instead_of_skipping_candidate(monkeypatch):
    error = HTTPException(503)

    def admit(*_):
        raise error

    engine = SimpleNamespace()
    owner = SimpleNamespace(graph=SimpleNamespace(engine=engine), engine=engine, admit_event=admit)
    partition = NativeRecheckPartition(owner)
    monkeypatch.setattr(partition, "_rows", lambda *_: [{"event_id": INTAKE, "occurred_at": "2026-10-17T00:00:00+00:00"}])
    monkeypatch.setattr(partition, "_withdrawn", lambda *_: False)
    with pytest.raises(HTTPException) as raised:
        list(partition.admit(TENANT, limit=1))
    assert raised.value is error
    assert owner.last_partition_progress["scanned"] == 0
    assert owner.last_partition_progress["next_cursor"] is None
