"""Parser settings and attempts cannot borrow another owner's authority."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application import native_intake_parser_policy as parser
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryAuthenticator,
)
from shared.jobs.queue import JobFenceRejectedError

TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def configured(monkeypatch):
    policy = {"policy_id": "synthetic-native-parser-v1", "version": 1, "tenant_ids": [TENANT],
        "max_attempts": 3, "deadline_seconds": 60, "backoff_seconds": [1, 2]}
    realm = {"version": 1, "mode": "isolated_engineering", "namespace": "synthetic_001",
        "database_name": "oday_native_parser_synthetic_001"}
    storage = {"version": 1, "global_max_concurrency": 2, "per_source_max_concurrency": 1,
        "storage_bindings": [{"tenant_id": TENANT, "bucket": "native-parser-synthetic-001",
            "kms_key_name": "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw",
            "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}
    values = {parser.POLICY_ENV: policy, parser.STORAGE_ENV: storage, parser.REALM_ENV: realm}
    for env, value in values.items():
        monkeypatch.setenv(env, json.dumps(value))
    monkeypatch.setenv(parser.SIGNING_KEY_ENV, "synthetic-parser-key-not-a-real-secret-0001")
    return values


def test_explicit_engineering_settings_capture_purpose_namespace_and_original_caps(configured):
    value = parser.native_intake_parser_policy(TENANT)
    assert value.tenant_id == TENANT and value.max_attempts == 3 and value.deadline_seconds == 60
    assert value.backoff_seconds == (1, 2)
    assert value.realm.database_name == "oday_native_parser_synthetic_001"
    assert value.realm.source_prefix == "native-parser-synthetic-001."
    assert value.storage.bucket == value.realm.bucket == "native-parser-synthetic-001"
    assert value.realm.mode == "isolated_engineering"
    assert parser.LEASE_SECONDS > 2 * parser.HEARTBEAT_SECONDS + max(parser.MAX_IO_SECONDS,
        parser.MAX_CHILD_SECONDS) + parser.CLEANUP_SECONDS
    assert not hasattr(value, "approved") and not hasattr(value, "production_enabled")
    snapshot = value.snapshot()
    snapshot["realm"]["namespace"] = "changed-copy"
    assert parser.native_intake_parser_policy(TENANT).sha256 == value.sha256


@pytest.mark.parametrize("env", [parser.POLICY_ENV, parser.STORAGE_ENV, parser.REALM_ENV])
def test_missing_own_configuration_stays_unavailable_even_with_other_owner_settings(configured, monkeypatch, env):
    monkeypatch.delenv(env)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_ASSISTED_ENTRY_POLICY_JSON", json.dumps(configured[parser.POLICY_ENV]))
    with pytest.raises(parser.NativeParserUnavailable):
        parser.native_intake_parser_policy(TENANT)


@pytest.mark.parametrize("key,value", [("version", True), ("version", 0), ("version", 2**63),
    ("max_attempts", 6), ("max_attempts", True), ("max_attempts", 0), ("deadline_seconds", 121),
    ("deadline_seconds", 0), ("backoff_seconds", [30, 30]), ("backoff_seconds", [True, 1]),
    ("backoff_seconds", [float("inf"), 1]), ("backoff_seconds", [1]),
    ("tenant_ids", [TENANT, TENANT]), ("policy_id", "bad\npolicy"), ("approval", True)])
def test_command_budgets_are_strict_and_have_no_approval_shortcut(configured, monkeypatch, key, value):
    monkeypatch.setenv(parser.POLICY_ENV, json.dumps({**configured[parser.POLICY_ENV], key: value}))
    with pytest.raises(parser.NativeParserUnavailable, match="^NATIVE_PARSER_CONFIGURATION_UNAVAILABLE$"):
        parser.native_intake_parser_policy(TENANT)


@pytest.mark.parametrize("key,value", [("mode", "production"), ("mode", "PRODUCTION"),
    ("namespace", "../../production"), ("namespace", ""), ("database_name", "oday_product"),
    ("version", True), ("is_production", True)])
def test_catalog_production_or_backend_classification_cannot_activate_a_realm(configured, monkeypatch, key, value):
    monkeypatch.setenv(parser.REALM_ENV, json.dumps({**configured[parser.REALM_ENV], key: value}))
    with pytest.raises(parser.NativeParserUnavailable):
        parser.native_intake_parser_policy(TENANT)


@pytest.mark.parametrize("change", ["bucket", "kms", "residency", "tenant", "capacity", "source_capacity", "duplicate"])
def test_parser_storage_needs_its_explicit_namespace_and_independent_capacity(configured, monkeypatch, change):
    value = configured[parser.STORAGE_ENV]
    if change == "bucket":
        value["storage_bindings"][0]["bucket"] = "unrelated-production-bucket"
    elif change == "kms":
        value["storage_bindings"][0]["kms_key_name"] = "projects/prod/locations/us/keyRings/key/cryptoKeys/raw"
    elif change == "residency":
        value["storage_bindings"][0]["residency_mode"] = "ANY"
    elif change == "tenant":
        value["storage_bindings"][0]["tenant_id"] = str(uuid4())
    elif change == "capacity":
        value["global_max_concurrency"] = 101
    elif change == "source_capacity":
        value["per_source_max_concurrency"] = 3
    else:
        value["storage_bindings"].append(value["storage_bindings"][0])
    monkeypatch.setenv(parser.STORAGE_ENV, json.dumps(value))
    with pytest.raises(parser.NativeParserUnavailable):
        parser.native_intake_parser_policy(TENANT)


def test_current_policy_generation_is_reloaded_and_not_cached(configured, monkeypatch):
    first = parser.native_intake_parser_policy(TENANT)
    changed = {**configured[parser.REALM_ENV], "version": 2}
    monkeypatch.setenv(parser.REALM_ENV, json.dumps(changed))
    second = parser.native_intake_parser_policy(TENANT)
    assert second.realm.version == 2 and second.sha256 != first.sha256
    with pytest.raises(PermissionError, match="^NATIVE_PARSER_TENANT_NOT_CONFIGURED$"):
        parser.native_intake_parser_policy(str(uuid4()))


def test_duplicate_json_configuration_keys_fail_closed(configured, monkeypatch):
    original = json.dumps(configured[parser.REALM_ENV])
    monkeypatch.setenv(parser.REALM_ENV, original[:-1] + ', "version":1}')
    with pytest.raises(parser.NativeParserUnavailable):
        parser.native_intake_parser_policy(TENANT)


def test_signed_attempt_is_purpose_bound_and_revalidates_current_key(configured, monkeypatch):
    job = str(uuid4())
    key = "synthetic-parser-key-not-a-real-secret-0001"
    monkeypatch.setenv("ODP_NATIVE_INTAKE_ASSISTED_ENTRY_SIGNING_KEY", key)
    token = parser.NativeParserAuthenticator.sign(job, TENANT, 1)
    assert token != NativeAssistedEntryAuthenticator.sign(job, TENANT, 1)
    delivery = SimpleNamespace(job_type=parser.JOB_TYPE, job_id=job,
        payload={"job_id": job, "tenant_id": TENANT, "attempt_token": token})
    assert parser.NativeParserAuthenticator().decode(delivery) == (TENANT, 1)
    monkeypatch.setenv(parser.SIGNING_KEY_ENV, "rotated-synthetic-parser-key-not-a-secret")
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_TOKEN_REJECTED$"):
        parser.NativeParserAuthenticator().decode(delivery)
    monkeypatch.delenv(parser.SIGNING_KEY_ENV)
    with pytest.raises(parser.NativeParserUnavailable, match="^NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE$"):
        parser.NativeParserAuthenticator.sign(job, TENANT, 1)
