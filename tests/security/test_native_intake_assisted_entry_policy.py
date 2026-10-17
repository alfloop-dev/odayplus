"""Manual job authority is explicit, closed and distinct from retrieval tokens."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeAssistedEntryAuthenticator,
    NativeAssistedEntryUnavailable,
    native_intake_assisted_entry_policy,
)
from modules.listing.application.native_intake_retrieval_policy import (
    NativeIntakeRetrievalAuthenticator,
)
from shared.jobs.queue import JobFenceRejectedError

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def settings(monkeypatch):
    policy = {"policy_id": "assisted-explicit", "version": 1, "tenant_ids": [TENANT],
              "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 3]}
    storage = {"version": 1, "global_max_concurrency": 5, "per_source_max_concurrency": 1,
        "storage_bindings": [{"tenant_id": TENANT, "bucket": "native-test-snapshots", "residency_mode": "TW_ONLY",
            "kms_key_name": "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/raw/cryptoKeyVersions/3",
            "retention_class": "STANDARD"}]}
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy))
    monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))
    return policy, storage, monkeypatch


def test_no_default_or_retrieval_configuration_fallback(monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_RETRIEVAL_POLICY_JSON", "{}")
    monkeypatch.setenv("ODP_NATIVE_INTAKE_RETRIEVAL_STORAGE_JSON", "{}")
    with pytest.raises(NativeAssistedEntryUnavailable):
        native_intake_assisted_entry_policy(TENANT)


@pytest.mark.parametrize("key,value", [
    ("max_attempts", 6), ("max_attempts", True), ("deadline_seconds", 121), ("deadline_seconds", 0),
    ("version", 2**63), ("backoff_seconds", [1, 120]), ("backoff_seconds", [1, float("inf")]),
    ("backoff_seconds", [0, 1]), ("backoff_seconds", [True, 1]), ("backoff_seconds", [1]),
    ("tenant_ids", [TENANT, TENANT]), ("tenant_ids", []), ("tenant_ids", ["default"]),
    ("arbitrary_default_source", "injected"),
])
def test_closed_explicit_job_budget_and_tenant_partition(settings, key, value):
    policy, _, monkeypatch = settings
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, key: value}))
    with pytest.raises(NativeAssistedEntryUnavailable):
        native_intake_assisted_entry_policy(TENANT)


@pytest.mark.parametrize("key,value", [
    ("global_max_concurrency", 101), ("global_max_concurrency", True), ("global_max_concurrency", 0),
    ("per_source_max_concurrency", 0), ("per_source_max_concurrency", 6), ("per_source_max_concurrency", True),
    ("version", 0), ("version", 2**63), ("HTTP_budget", 1),
])
def test_storage_concurrency_is_closed_and_does_not_require_source_http_budgets(settings, key, value):
    _, storage, monkeypatch = settings
    monkeypatch.setenv(STORAGE_ENV, json.dumps({**storage, key: value}))
    with pytest.raises(NativeAssistedEntryUnavailable):
        native_intake_assisted_entry_policy(TENANT)


@pytest.mark.parametrize("key,value", [
    ("residency_mode", "APPROVED_APAC_DR"), ("bucket", "unknown/endpoint"),
    ("bucket", "has..dots"), ("kms_key_name", "projects/test/locations/us-east1/keyRings/native/cryptoKeys/raw"),
    ("kms_key_name", "unproven-key"), ("retention_class", ""), ("tenant_id", OTHER),
    ("credential", "not part of durable config"),
])
def test_storage_binding_is_explicit_taiwan_cmek_and_has_no_secret_fields(settings, key, value):
    _, storage, monkeypatch = settings
    storage["storage_bindings"][0][key] = value
    monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))
    with pytest.raises(NativeAssistedEntryUnavailable):
        native_intake_assisted_entry_policy(TENANT)


def test_snapshot_contains_only_current_tenant_and_changes_with_current_settings(settings):
    policy, storage, monkeypatch = settings
    policy["tenant_ids"].append(OTHER)
    storage["storage_bindings"].append({**storage["storage_bindings"][0], "tenant_id": OTHER, "bucket": "other-tenant-only"})
    monkeypatch.setenv(POLICY_ENV, json.dumps(policy))
    monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))
    loaded = native_intake_assisted_entry_policy(TENANT)
    frozen = loaded.snapshot()
    assert loaded.tenant_id == TENANT and loaded.per_source_max_concurrency == 1
    assert OTHER not in json.dumps(frozen) and "other-tenant-only" not in json.dumps(frozen)
    assert "requests_per_minute" not in frozen and "concurrent_requests" not in frozen
    storage["per_source_max_concurrency"] = 2
    monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))
    assert native_intake_assisted_entry_policy(TENANT).sha256 != loaded.sha256


def test_unconfigured_tenant_is_denied(settings):
    with pytest.raises(PermissionError):
        native_intake_assisted_entry_policy(OTHER)


def test_attempt_signatures_cannot_reuse_a_retrieval_capability_even_with_same_secret(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, "x" * 32)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_RETRIEVAL_SIGNING_KEY", "x" * 32)
    job = str(uuid4())
    assisted = NativeAssistedEntryAuthenticator()
    payload = {"job_id": job, "tenant_id": TENANT, "attempt_token": assisted.sign(job, TENANT, 1)}
    assert assisted.decode(SimpleNamespace(job_id=job, job_type=JOB_TYPE, payload=payload)) == (TENANT, 1)
    foreign = NativeIntakeRetrievalAuthenticator.sign(job, TENANT, 1)
    assert foreign != payload["attempt_token"]
    with pytest.raises(JobFenceRejectedError):
        assisted.decode(SimpleNamespace(job_id=job, job_type=JOB_TYPE, payload={**payload, "attempt_token": foreign}))
    with pytest.raises(JobFenceRejectedError):
        assisted.decode(SimpleNamespace(job_id=job, job_type=JOB_TYPE, payload={**payload, "source_id": "caller"}))
    with pytest.raises(JobFenceRejectedError):
        assisted.decode(SimpleNamespace(job_id=job, job_type="native-intake-retrieval", payload=payload))


def test_no_signing_key_fallback(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_RETRIEVAL_SIGNING_KEY", "x" * 32)
    with pytest.raises(NativeAssistedEntryUnavailable):
        NativeAssistedEntryAuthenticator.sign(str(uuid4()), TENANT, 1)
