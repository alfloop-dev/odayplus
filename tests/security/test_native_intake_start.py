"""Synthetic capability/configuration and worker-settlement boundaries."""

import hashlib
import hmac
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_start import (
    NATIVE_INTAKE_START_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeStartAuthenticator,
    NativeIntakeStartUnavailable,
    native_intake_start_policy,
)
from shared.infrastructure.persistence.factory import build_persistence
from shared.jobs.queue import JobFenceRejectedError, JobRecord, JobRequest, JobStatus
from shared.jobs.registry import JobRegistry


def policy(tenant):
    return {"policy_id": "synthetic-native-start-only", "version": 1, "tenant_ids": [tenant],
            "max_attempts": 2, "deadline_seconds": 30, "backoff_seconds": [0.01]}


@pytest.mark.parametrize("patch", [
    {"version": True}, {"version": 0}, {"max_attempts": 0}, {"max_attempts": True},
    {"deadline_seconds": True}, {"deadline_seconds": 0}, {"deadline_seconds": 41},
    {"deadline_seconds": 45}, {"deadline_seconds": "30"}, {"tenant_ids": []},
    {"tenant_ids": ["00000000-0000-0000-0000-000000000000"]}, {"tenant_ids": ["not-a-tenant"]},
    {"backoff_seconds": []}, {"backoff_seconds": [True]}, {"backoff_seconds": [float("inf")]},
    {"backoff_seconds": [0]}, {"policy_id": " "}, {"unknown_grant": True},
])
def test_invalid_policy_never_becomes_a_default_grant(monkeypatch, patch):
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(str(uuid4())), **patch}))
    with pytest.raises(NativeIntakeStartUnavailable):
        native_intake_start_policy()


def test_missing_policy_and_identity_key_are_not_native_authority(monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    with pytest.raises(NativeIntakeStartUnavailable):
        native_intake_start_policy()
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv("ODP_IDENTITY_ATTEMPT_SIGNING_KEY", "synthetic-identity-key-" * 3)
    with pytest.raises(NativeIntakeStartUnavailable):
        NativeIntakeStartAuthenticator.sign(str(uuid4()), str(uuid4()), 1)


def test_policy_has_explicit_partition_and_lease_headroom(monkeypatch):
    tenant = str(uuid4())
    value = {**policy(tenant), "deadline_seconds": 40}
    monkeypatch.setenv(POLICY_ENV, json.dumps(value))
    assert native_intake_start_policy() == value
    monkeypatch.setenv(POLICY_ENV, json.dumps({**value, "tenant_ids": [tenant, tenant]}))
    with pytest.raises(NativeIntakeStartUnavailable):
        native_intake_start_policy()


@pytest.mark.parametrize("change", ["tenant", "job", "purpose", "type", "generation", "extra"])
def test_tampered_capability_is_rejected_without_any_database_dependency(monkeypatch, change):
    key, tenant, job_id = "synthetic-native-key-" * 3, str(uuid4()), str(uuid4())
    monkeypatch.setenv(SIGNING_KEY_ENV, key)
    auth = NativeIntakeStartAuthenticator()
    token = auth.sign(job_id, tenant, 1)
    delivery = JobRecord(NATIVE_INTAKE_START_JOB_TYPE,
        {"job_id": job_id, "tenant_id": tenant, "attempt_token": token}, "synthetic-correlation", job_id=job_id)
    assert auth.decode(delivery) == (tenant, 1)
    payload = dict(delivery.payload)
    if change == "tenant":
        payload["tenant_id"] = str(uuid4())
    elif change == "job":
        payload["job_id"] = str(uuid4())
    elif change == "purpose":
        payload["attempt_token"] = "1." + hmac.new(key.encode(),
            f"identity-execution:{job_id}:{tenant}:1".encode(), hashlib.sha256).hexdigest()
    elif change == "type":
        delivery = replace(delivery, job_type="identity-execution")
    elif change == "generation":
        payload["attempt_token"] = "01." + token.split(".", 1)[1]
    else:
        payload["actor_role"] = "SVC_INTAKE"
    with pytest.raises(JobFenceRejectedError):
        auth.decode(replace(delivery, payload=payload))


@pytest.mark.parametrize("job_type", [NATIVE_INTAKE_START_JOB_TYPE, "identity-execution"])
@pytest.mark.parametrize("raises", [False, True])
def test_worker_cannot_settle_an_unresolved_canonical_business_attempt(job_type, raises):
    # This tests worker routing only, not SQL admission or service authority.
    bundle, registry = build_persistence(mode="memory"), JobRegistry()

    def unresolved(_job, _bundle):
        if raises:
            raise ValueError("Synthetic unresolved business checkpoint")

    registry.register(job_type, unresolved)
    job, _ = bundle.job_queue.enqueue(JobRequest(job_type, {}), correlation_id="synthetic-routing-only")
    worker = ODayWorker(persistence=bundle, registry=registry, heartbeat_interval_seconds=1)
    assert worker.run_once() is True
    current = bundle.job_queue.get(job.job_id)
    assert current.status == JobStatus.RUNNING
    assert current.payload == {} and current.delivery_state is None
