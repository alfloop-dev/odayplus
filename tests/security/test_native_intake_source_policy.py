"""Purpose/partition and unresolved-worker controls without database authority."""

import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_identity import NativeIntakeIdentityAuthenticator
from modules.listing.application.native_intake_source_policy import (
    NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeSourcePolicyAuthenticator,
    NativeIntakeSourcePolicyOwner,
    NativeIntakeSourcePolicyUnavailable,
    native_intake_source_policy_policy,
)
from modules.listing.application.native_intake_start import NativeIntakeStartAuthenticator
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobRequest, JobStatus
from shared.jobs.registry import JobRegistry
from tests.security.test_native_intake_start import policy


@pytest.mark.parametrize("patch", [
    {"max_attempts": 4, "backoff_seconds": [1, 2, 3]}, {"max_attempts": True},
    {"deadline_seconds": 31}, {"deadline_seconds": True}, {"version": True},
    {"tenant_ids": []}, {"backoff_seconds": [0]}, {"extra_grant": "source.retrieve"},
])
def test_source_policy_runtime_is_explicit_typed_and_bounded(monkeypatch, patch):
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(str(uuid4())), **patch}))
    with pytest.raises(NativeIntakeSourcePolicyUnavailable):
        native_intake_source_policy_policy()


def test_no_other_phase_policy_or_key_is_an_implicit_source_policy_grant(monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_IDENTITY_SIGNING_KEY", "synthetic-identity-only-" * 3)
    with pytest.raises(NativeIntakeSourcePolicyUnavailable):
        native_intake_source_policy_policy()
    with pytest.raises(NativeIntakeSourcePolicyUnavailable):
        NativeIntakeSourcePolicyAuthenticator.sign(str(uuid4()), str(uuid4()), 1)


@pytest.mark.parametrize("change", ["tenant", "job", "start_purpose", "identity_purpose", "type", "generation", "extra"])
def test_source_policy_capability_is_not_interchangeable_even_with_same_key_bytes(monkeypatch, change):
    tenant, job_id, key = str(uuid4()), str(uuid4()), "synthetic-purpose-separated-source-key-" * 2
    for name in [SIGNING_KEY_ENV, "ODP_NATIVE_INTAKE_START_SIGNING_KEY", "ODP_NATIVE_INTAKE_IDENTITY_SIGNING_KEY"]:
        monkeypatch.setenv(name, key)
    auth = NativeIntakeSourcePolicyAuthenticator()
    token = auth.sign(job_id, tenant, 1)
    delivery = JobRecord(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
        {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}, "synthetic-source-correlation", job_id=job_id)
    assert auth.decode(delivery) == (tenant, 1)
    payload = dict(delivery.payload)
    if change == "tenant":
        payload["tenant_id"] = str(uuid4())
    elif change == "job":
        payload["job_id"] = str(uuid4())
    elif change == "start_purpose":
        payload["attempt_token"] = NativeIntakeStartAuthenticator.sign(job_id, tenant, 1)
    elif change == "identity_purpose":
        payload["attempt_token"] = NativeIntakeIdentityAuthenticator.sign(job_id, tenant, 1)
    elif change == "type":
        delivery = replace(delivery, job_type="native-intake-identity")
    elif change == "generation":
        payload["attempt_token"] = "01." + token.split(".", 1)[1]
    else:
        payload["actor_role"] = "SVC_INTAKE"
    with pytest.raises(JobFenceRejectedError):
        auth.decode(replace(delivery, payload=payload))


@pytest.mark.parametrize("kind", ["invalid_signature", "unconfigured_tenant"])
def test_source_policy_authentication_and_tenant_partition_precede_any_SQL(monkeypatch, kind):
    tenant, foreign, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy(tenant)))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-pre-SQL-source-policy-" * 2)
    touched = []

    def forbidden(*_args, **_kwargs):
        touched.append(True)
        raise AssertionError("No SQL or transaction before capability and partition")

    owner = NativeIntakeSourcePolicyOwner(SimpleNamespace(
        engine=SimpleNamespace(dialect="postgresql", query_one=forbidden), queue=SimpleNamespace(), transaction=forbidden))
    token = owner.auth.sign(job_id, foreign, 1) + ("0" if kind == "invalid_signature" else "")
    delivery = JobRecord(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
        {"job_id": job_id, "tenant_id": foreign, "attempt_token": token}, "synthetic-pre-SQL", job_id=job_id)
    with pytest.raises(JobFenceRejectedError if kind == "invalid_signature" else PermissionError):
        owner.execute(delivery)
    assert touched == []


@pytest.mark.parametrize("raises", [False, True])
def test_default_worker_cannot_turn_an_unresolved_source_policy_return_into_physical_success(monkeypatch, raises):
    bundle, registry, renewed = build_persistence(mode="memory"), JobRegistry(), threading.Event()
    original = bundle.job_queue.heartbeat

    def heartbeat(*args, **kwargs):
        version = original(*args, **kwargs)
        renewed.set()
        return version

    monkeypatch.setattr(bundle.job_queue, "heartbeat", heartbeat)

    def unresolved(_job, _bundle):
        assert renewed.wait(3)
        if raises:
            raise ValueError("Synthetic unresolved source policy")

    registry.register(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, unresolved)
    job, _ = bundle.job_queue.enqueue(JobRequest(NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, {}),
                                     correlation_id="synthetic-unresolved-source-policy")
    assert ODayWorker(persistence=bundle, registry=registry, heartbeat_interval_seconds=0.01).run_once() is True
    current = bundle.job_queue.get(job.job_id)
    assert current.status == JobStatus.RUNNING and current.delivery_state is None and current.payload == {}
