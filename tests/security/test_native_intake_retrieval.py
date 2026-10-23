"""Native retrieval configuration and capability security boundaries."""

import json
from types import SimpleNamespace

import pytest

from modules.listing.application.native_intake_job_runtime import sign_native_intake_attempt
from modules.listing.application.native_intake_retrieval_policy import (
    JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    STORAGE_CREDENTIAL_ENV,
    STORAGE_ENV,
    NativeIntakeRetrievalAuthenticator,
    NativeIntakeRetrievalUnavailable,
    native_intake_retrieval_policy,
    native_snapshot_credential,
)
from shared.jobs.queue import JobFenceRejectedError

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"
KEY = "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw"


@pytest.fixture
def configured(monkeypatch):
    policy = {
        "policy_id": "explicit-test-only", "version": 1, "tenant_ids": [TENANT, OTHER],
        "max_attempts": 5, "deadline_seconds": 120, "backoff_seconds": [10, 30, 120, 600],
    }
    storage = {
        "version": 1, "global_max_concurrency": 2,
        "storage_bindings": [
            {"tenant_id": tenant, "bucket": bucket, "kms_key_name": KEY,
             "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}
            for tenant, bucket in [(TENANT, "tenant-one-snapshots"), (OTHER, "tenant-two-snapshots")]
        ],
    }

    def apply():
        monkeypatch.setenv(POLICY_ENV, json.dumps(policy))
        monkeypatch.setenv(STORAGE_ENV, json.dumps(storage))

    apply()
    monkeypatch.setenv(SIGNING_KEY_ENV, "r" * 64)
    return policy, storage, apply


def test_tenant_job_snapshot_never_copies_foreign_storage_binding(configured):
    current = native_intake_retrieval_policy(TENANT)
    snapshot = current.snapshot()
    text = json.dumps(snapshot)
    assert OTHER not in text and "tenant-two-snapshots" not in text
    assert snapshot["storage_binding"]["tenant_id"] == TENANT
    assert snapshot["storage_binding"]["bucket"] == "tenant-one-snapshots"
    # Hashing must cover this tenant's actual storage choice without exposing
    # another tenant's storage configuration or any runtime credential.
    _, storage, apply = configured
    before = current.sha256
    storage["storage_bindings"][1]["bucket"] = "foreign-storage-changed"
    apply()
    assert native_intake_retrieval_policy(TENANT).sha256 == before
    storage["storage_bindings"][0]["bucket"] = "own-storage-changed"
    apply()
    assert native_intake_retrieval_policy(TENANT).sha256 != before


@pytest.mark.parametrize("field,value", [
    ("deadline_seconds", 121), ("deadline_seconds", True),
    ("max_attempts", 6), ("max_attempts", True),
    ("backoff_seconds", [10, 30, float("nan"), 600]),
    ("backoff_seconds", [10, 30, -1, 600]), ("backoff_seconds", [10]),
])
def test_invalid_work_budget_cannot_load(configured, field, value):
    policy, _, apply = configured
    policy[field] = value
    apply()
    with pytest.raises(NativeIntakeRetrievalUnavailable, match="^NATIVE_RETRIEVAL_POLICY_UNAVAILABLE$"):
        native_intake_retrieval_policy(TENANT)


@pytest.mark.parametrize("value", [0, 101, True, "2"])
def test_global_concurrency_requires_explicit_bounded_integer(configured, value):
    _, storage, apply = configured
    storage["global_max_concurrency"] = value
    apply()
    with pytest.raises(NativeIntakeRetrievalUnavailable, match="^NATIVE_RETRIEVAL_STORAGE_UNAVAILABLE$"):
        native_intake_retrieval_policy(TENANT)


@pytest.mark.parametrize("case", ["missing", "duplicate", "foreign", "dr", "secret", "kms-region"])
def test_incomplete_or_unsupported_storage_is_unavailable(configured, case):
    _, storage, apply = configured
    if case == "missing":
        storage["storage_bindings"].pop()
    elif case == "duplicate":
        storage["storage_bindings"].append(dict(storage["storage_bindings"][0]))
    elif case == "foreign":
        storage["storage_bindings"][1]["tenant_id"] = JOB
    elif case == "dr":
        storage["storage_bindings"][0]["residency_mode"] = "APPROVED_APAC_DR"
    elif case == "secret":
        storage["token"] = "test-only-secret-must-not-be-echoed"
    elif case == "kms-region":
        storage["storage_bindings"][0]["kms_key_name"] = KEY.replace("asia-east1", "us-central1")
    apply()
    with pytest.raises(NativeIntakeRetrievalUnavailable) as error:
        native_intake_retrieval_policy(TENANT)
    assert str(error.value) == "NATIVE_RETRIEVAL_STORAGE_UNAVAILABLE"


def test_unconfigured_tenant_is_denied_even_with_complete_foreign_settings(configured):
    with pytest.raises(PermissionError, match="^NATIVE_RETRIEVAL_TENANT_NOT_CONFIGURED$"):
        native_intake_retrieval_policy(JOB)


def test_exact_kms_version_is_independently_checked(configured):
    binding = native_intake_retrieval_policy(TENANT).storage
    assert binding.accepts_actual_key_version(KEY + "/cryptoKeyVersions/7")
    assert not binding.accepts_actual_key_version(KEY)
    assert not binding.accepts_actual_key_version(KEY + "/cryptoKeyVersions/0")
    assert not binding.accepts_actual_key_version(KEY + "/cryptoKeyVersions/01")
    assert not binding.accepts_actual_key_version(KEY + "-other/cryptoKeyVersions/7")
    _, storage, apply = configured
    storage["storage_bindings"][0]["kms_key_name"] = KEY + "/cryptoKeyVersions/7"
    apply()
    binding = native_intake_retrieval_policy(TENANT).storage
    assert binding.upload_key_name == KEY
    assert binding.accepts_actual_key_version(KEY + "/cryptoKeyVersions/7")
    assert not binding.accepts_actual_key_version(KEY + "/cryptoKeyVersions/8")


@pytest.mark.parametrize("purpose", ["native-intake-start", "native-intake-identity", "native-intake-source-policy"])
def test_valid_signature_for_other_phase_never_authorizes_retrieval(configured, purpose):
    token = sign_native_intake_attempt(purpose, SIGNING_KEY_ENV, JOB, TENANT, 1,
        unavailable=NativeIntakeRetrievalUnavailable, error_code="TEST_KEY_UNAVAILABLE")
    delivery = SimpleNamespace(job_type=JOB_TYPE, job_id=JOB,
        payload={"job_id": JOB, "tenant_id": TENANT, "attempt_token": token})
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_RETRIEVAL_TOKEN_REJECTED$"):
        NativeIntakeRetrievalAuthenticator().decode(delivery)


def test_signed_tenant_cannot_be_replaced(configured):
    token = NativeIntakeRetrievalAuthenticator.sign(JOB, TENANT, 1)
    payload = {"job_id": JOB, "tenant_id": TENANT, "attempt_token": token}
    auth = NativeIntakeRetrievalAuthenticator()
    assert auth.decode(SimpleNamespace(job_type=JOB_TYPE, job_id=JOB, payload=payload)) == (TENANT, 1)
    with pytest.raises(JobFenceRejectedError):
        auth.decode(SimpleNamespace(job_type=JOB_TYPE, job_id=JOB, payload={**payload, "tenant_id": OTHER}))


def test_storage_never_falls_back_to_audit_or_general_cloud_token(monkeypatch):
    monkeypatch.delenv(STORAGE_CREDENTIAL_ENV, raising=False)
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", "test-only-audit-token")
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "test-only-general-token")
    with pytest.raises(NativeIntakeRetrievalUnavailable) as error:
        native_snapshot_credential()
    assert str(error.value) == "NATIVE_RETRIEVAL_STORAGE_CREDENTIAL_UNAVAILABLE"


@pytest.mark.parametrize("value", ["", "test\r\ninjected", "test token", "中文", "x" * 8193])
def test_storage_credential_is_bounded_and_cannot_inject_headers(monkeypatch, value):
    monkeypatch.setenv(STORAGE_CREDENTIAL_ENV, value)
    with pytest.raises(NativeIntakeRetrievalUnavailable) as error:
        native_snapshot_credential()
    assert str(error.value) == "NATIVE_RETRIEVAL_STORAGE_CREDENTIAL_UNAVAILABLE"
