"""Purpose, default-off configuration and service-only recheck boundaries."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_source_policy import (
    SIGNING_KEY_ENV as SOURCE_KEY_ENV,
)
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyAuthenticator,
)
from modules.listing.application.native_intake_source_policy_recheck import (
    NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeSourcePolicyRecheckAuthenticator,
    NativeIntakeSourcePolicyRecheckOwner,
    NativeIntakeSourcePolicyRecheckUnavailable,
    native_intake_source_policy_recheck_policy,
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
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobRequest, JobStatus
from shared.jobs.registry import JobRegistry
from tests.security.test_native_intake_source_policy import policy


@pytest.mark.parametrize("patch", [
    {"deadline_seconds": 16}, {"deadline_seconds": True}, {"max_attempts": True},
    {"max_attempts": 4, "backoff_seconds": [1, 2, 3]}, {"tenant_ids": []},
    {"version": True}, {"backoff_seconds": [0]}, {"source_grant": "retrieve"},
])
def test_recheck_configuration_is_explicit_typed_and_within_fifteen_seconds(monkeypatch, patch):
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(str(uuid4())), **patch}))
    with pytest.raises(NativeIntakeSourcePolicyRecheckUnavailable):
        native_intake_source_policy_recheck_policy()


def test_recheck_has_no_implicit_policy_key_or_source_worker_capability(monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv(SOURCE_KEY_ENV, "synthetic-shared-key-byte-content-" * 2)
    with pytest.raises(NativeIntakeSourcePolicyRecheckUnavailable):
        native_intake_source_policy_recheck_policy()
    with pytest.raises(NativeIntakeSourcePolicyRecheckUnavailable):
        NativeIntakeSourcePolicyRecheckAuthenticator.sign(str(uuid4()), str(uuid4()), 1)
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-shared-key-byte-content-" * 2)
    tenant, job = str(uuid4()), str(uuid4())
    token = NativeIntakeSourcePolicyRecheckAuthenticator.sign(job, tenant, 1)
    delivery = JobRecord("native-intake-source-policy", {"job_id": job, "tenant_id": tenant, "attempt_token": token},
                         "synthetic-purpose-only", job_id=job)
    with pytest.raises(JobFenceRejectedError):
        NativeIntakeSourcePolicyAuthenticator().decode(delivery)


@pytest.mark.parametrize("invalid_signature", [False, True])
def test_invalid_capability_or_foreign_partition_cannot_start_sql(monkeypatch, invalid_signature):
    configured, foreign, job = str(uuid4()), str(uuid4()), str(uuid4())
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy(configured)))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-pre-SQL-recheck-key-" * 2)
    touched = []
    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("Invalid recheck must not reach SQL")
    owner = NativeIntakeSourcePolicyRecheckOwner(SimpleNamespace(
        engine=SimpleNamespace(dialect="postgresql", query_one=forbidden), queue=SimpleNamespace(), transaction=forbidden))
    token = owner.auth.sign(job, foreign, 1) + ("0" if invalid_signature else "")
    delivery = JobRecord(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE,
        {"job_id": job, "tenant_id": foreign, "attempt_token": token}, "synthetic-pre-SQL", job_id=job)
    with pytest.raises(JobFenceRejectedError if invalid_signature else PermissionError):
        owner.execute(delivery)
    assert touched == []


@pytest.mark.parametrize("raises", [False, True])
def test_default_worker_never_infers_success_from_an_unresolved_recheck(raises):
    bundle, registry = build_persistence(mode="memory"), JobRegistry()
    def unresolved(job, persistence):
        if raises:
            raise ValueError("Synthetic unresolved recheck")
    registry.register(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, unresolved)
    job, _ = bundle.job_queue.enqueue(JobRequest(NATIVE_INTAKE_SOURCE_POLICY_RECHECK_JOB_TYPE, {}),
                                     correlation_id="synthetic-unresolved-recheck")
    assert ODayWorker(persistence=bundle, registry=registry, heartbeat_interval_seconds=0.01).run_once() is True
    current = bundle.job_queue.get(job.job_id)
    assert current.status == JobStatus.RUNNING and current.delivery_state is None and current.payload == {}


@pytest.mark.parametrize("role", list(PrincipalRole))
@pytest.mark.parametrize("verified", [False, True])
def test_fsm_recheck_requires_service_role_and_verified_explicit_intent(role, verified):
    aggregate = IntakeAggregate("synthetic-intake", "synthetic-tenant", IntakeStage.AWAITING_SOURCE_AUTHORIZATION,
                                4, "synthetic-author", "synthetic-source")
    context = TransitionContext(actor=Actor("synthetic-actor", role, "synthetic-tenant"), version_before=4,
        idempotency_key="synthetic-recheck", causation_id="synthetic-request", reason="Explicit synthetic recheck",
        source_policy_recheck_verified=verified)
    service = role in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}
    if service and verified:
        IntakeStateMachine.transition(aggregate, IntakeStage.CHECKING_SOURCE_POLICY, context)
        assert aggregate.stage == IntakeStage.CHECKING_SOURCE_POLICY and aggregate.version == 5
    else:
        with pytest.raises(DomainValidationError) as error:
            IntakeStateMachine.transition(aggregate, IntakeStage.CHECKING_SOURCE_POLICY, context)
        assert error.value.code == (DenialCode.SOURCE_POLICY_RECHECK_NOT_VERIFIED if service else DenialCode.ROLE_DENIED)
        assert aggregate.stage == IntakeStage.AWAITING_SOURCE_AUTHORIZATION and aggregate.version == 4
