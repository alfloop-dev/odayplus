"""Pure policy classification controls, not native execution or live approval."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from modules.listing.application import native_intake_source_policy_rules as rules
from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner

NOW = datetime(2026, 10, 16, 1, tzinfo=UTC)


def source():
    return {
        "source_id": "synthetic-policy-source", "allowed_hosts": ["synthetic.example"],
        "canonicalization_rule_version": "native-url-exact-v2", "retrieval_mode": "APPROVED_RETRIEVAL",
        "legal_approval_ref": "synthetic-legal", "license_approval_ref": "synthetic-license",
        "policy_owner_subject_id": str(uuid4()), "review_expires_at": NOW + timedelta(hours=1),
        "requests_per_minute": 5, "concurrent_requests": 1,
        "kill_switch": False, "production_enabled": True, "version": 2,
    }


def evaluate(row, **kwargs):
    return rules.evaluate_native_source_policy(row, source_host="synthetic.example",
        expected_source_id="synthetic-policy-source", host_match_count=1, now=NOW, **kwargs)


@pytest.mark.parametrize(("mode", "target", "permission", "audit"), [
    ("APPROVED_RETRIEVAL", "RETRIEVING", "source.retrieve", "intake.retrieval_approved.v1"),
    ("ASSISTED_ENTRY_ONLY", "AWAITING_ASSISTED_ENTRY", "intake.assisted_entry", "intake.assisted_entry_required.v1"),
    ("SOURCE_BLOCKED", "QUARANTINED", "intake.quarantine", "intake.quarantined.v1"),
    ("POLICY_UNKNOWN", "QUARANTINED", "intake.quarantine", "intake.quarantined.v1"),
])
def test_supported_outlets_use_exact_contract_permission_and_audit(mode, target, permission, audit):
    row = {**source(), "retrieval_mode": mode}
    before = dict(row)
    result = evaluate(row)
    assert (result.target_state, result.effective_policy, result.permission, result.audit_action) == (
        target, mode, permission, audit)
    assert result.valid_until == (row["review_expires_at"] if target != "QUARANTINED" else None)
    assert row == before
    assert result.source_snapshot_sha256 == hashlib.sha256(result.source_snapshot_json.encode()).hexdigest()
    assert json.loads(result.source_snapshot_json)["review_expires_at"] == row["review_expires_at"].isoformat()


@pytest.mark.parametrize("mode", ["APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY"])
@pytest.mark.parametrize("patch", [
    {"legal_approval_ref": None}, {"legal_approval_ref": " "},
    {"license_approval_ref": None}, {"license_approval_ref": ""},
    {"review_expires_at": None}, {"review_expires_at": NOW},
    {"review_expires_at": NOW - timedelta(microseconds=1)},
    {"review_expires_at": NOW.replace(tzinfo=None)}, {"review_expires_at": "invalid"},
    {"policy_owner_subject_id": "00000000-0000-0000-0000-000000000000"},
    {"policy_owner_subject_id": str(uuid5(NAMESPACE_DNS, "system"))},
    {"policy_owner_subject_id": None}, {"policy_owner_subject_id": "invalid"},
])
def test_incomplete_approval_never_grants_either_branch_and_agrees_with_AN(mode, patch):
    row = {**source(), "retrieval_mode": mode, **patch}
    assert NativeIntakeIdentityOwner._source_reusable(row, NOW) is False
    result = evaluate(row)
    assert (result.target_state, result.effective_policy, result.reason_code, result.valid_until) == (
        "QUARANTINED", "POLICY_UNKNOWN", "SOURCE_APPROVAL_UNVERIFIED", None)


@pytest.mark.parametrize("patch", [{"kill_switch": True}, {"production_enabled": False},
                                  {"retrieval_mode": "SOURCE_BLOCKED"}])
def test_explicit_block_is_not_a_fallback_or_retrieval_grant(patch):
    row = {**source(), **patch}
    assert NativeIntakeIdentityOwner._source_reusable(row, NOW) is False
    result = evaluate(row)
    assert (result.target_state, result.effective_policy, result.valid_until) == ("QUARANTINED", "SOURCE_BLOCKED", None)


@pytest.mark.parametrize("patch", [{"kill_switch": 0}, {"kill_switch": None},
                                  {"production_enabled": 1}, {"production_enabled": None}])
def test_truthy_or_missing_flags_do_not_become_boolean_approval(patch):
    row = {**source(), **patch}
    assert NativeIntakeIdentityOwner._source_reusable(row, NOW) is False
    result = evaluate(row)
    assert (result.target_state, result.effective_policy) == ("QUARANTINED", "POLICY_UNKNOWN")


@pytest.mark.parametrize("field", ["version", "requests_per_minute", "concurrent_requests"])
@pytest.mark.parametrize("value", [True, 0, -1, None, 1.5])
def test_positive_integer_version_and_retrieval_limits_are_required(field, value):
    row = {**source(), field: value}
    result = evaluate(row)
    assert (result.target_state, result.effective_policy, result.valid_until) == ("QUARANTINED", "POLICY_UNKNOWN", None)


def test_assisted_capture_has_no_network_budget_requirement_and_AN_reuse_stays_denied():
    row = {**source(), "retrieval_mode": "ASSISTED_ENTRY_ONLY", "requests_per_minute": 0, "concurrent_requests": 0}
    assert NativeIntakeIdentityOwner._source_reusable(row, NOW) is False
    assert evaluate(row).target_state == "AWAITING_ASSISTED_ENTRY"


@pytest.mark.parametrize("patch", [{}, {"kill_switch": True}, {"production_enabled": False},
                                  {"legal_approval_ref": None}, {"allowed_hosts": []}])
def test_AUTH_destination_remains_explicitly_unsupported_without_invented_quarantine(patch):
    row = {**source(), "retrieval_mode": "AUTH_REQUIRED", **patch}
    before = dict(row)
    with pytest.raises(rules.NativeSourcePolicyRuleUnavailable, match="^NATIVE_SOURCE_POLICY_AUTH_REQUIRED_CONTRACT_OPEN$"):
        evaluate(row)
    assert row == before


@pytest.mark.parametrize("hosts", [[], ["other.example"], ["synthetic.example."], ["例え.jp"],
                                    ["8.8.8.8"], ["synthetic.example:443"], [None],
                                    "invalid JSON", ["synthetic.example"] * 65])
def test_registry_association_has_the_same_supported_host_semantics_as_hashing(hosts):
    result = evaluate({**source(), "allowed_hosts": hosts})
    assert (result.target_state, result.reason_code) == ("QUARANTINED", "SOURCE_ASSOCIATION_UNVERIFIED")


@pytest.mark.parametrize("hosts", [["SYNTHETIC.EXAMPLE"], '["synthetic.example"]'])
def test_case_insensitive_registry_and_driver_JSON_array_are_supported(hosts):
    result = evaluate({**source(), "allowed_hosts": hosts})
    assert result.target_state == "RETRIEVING"
    assert json.loads(result.source_snapshot_json)["allowed_hosts"] == (
        hosts if isinstance(hosts, list) else json.loads(hosts))


@pytest.mark.parametrize("patch", [{"source_id": "other"}, {"canonicalization_rule_version": "v1.0"}])
def test_another_source_or_legacy_rule_does_not_reinterpret_recorded_URL_identity(patch):
    assert evaluate({**source(), **patch}).reason_code == "SOURCE_ASSOCIATION_UNVERIFIED"


@pytest.mark.parametrize("count", [0, 2, True, None])
def test_missing_ambiguous_or_untyped_global_host_count_is_not_recognition(count):
    result = rules.evaluate_native_source_policy(source(), source_host="synthetic.example",
        expected_source_id="synthetic-policy-source", host_match_count=count, now=NOW)
    assert result.target_state == "QUARANTINED" and result.reason_code == "SOURCE_ASSOCIATION_UNVERIFIED"


def test_approval_is_evaluated_at_the_supplied_database_clock_without_extending_expiry():
    row = source()
    row["review_expires_at"] = NOW + timedelta(microseconds=1)
    assert NativeIntakeIdentityOwner._source_reusable(row, NOW) is True
    before = evaluate(row)
    after = rules.evaluate_native_source_policy(row, source_host="synthetic.example",
        expected_source_id=row["source_id"], host_match_count=1, now=row["review_expires_at"])
    assert before.target_state == "RETRIEVING" and before.valid_until == row["review_expires_at"]
    assert after.target_state == "QUARANTINED" and after.valid_until is None
    assert before.source_snapshot_sha256 == after.source_snapshot_sha256


@pytest.mark.parametrize("clock", [None, "invalid", NOW.replace(tzinfo=None)])
def test_unverified_clock_cannot_issue_a_policy_decision(clock):
    with pytest.raises(rules.NativeSourcePolicyRuleUnavailable, match="^NATIVE_SOURCE_POLICY_DB_CLOCK_UNAVAILABLE$"):
        rules.evaluate_native_source_policy(source(), source_host="synthetic.example",
            expected_source_id="synthetic-policy-source", host_match_count=1, now=clock)


def test_decision_snapshot_is_immutable_and_binds_exact_relevant_policy_inputs():
    row = source()
    result = evaluate(row)
    with pytest.raises(FrozenInstanceError):
        result.target_state = "READY"
    row["allowed_hosts"].append("other.example")
    row["legal_approval_ref"] = "changed"
    assert json.loads(result.source_snapshot_json)["allowed_hosts"] == ["synthetic.example"]
    assert json.loads(result.source_snapshot_json)["legal_approval_ref"] == "synthetic-legal"
    assert result.source_snapshot_sha256 != evaluate(row).source_snapshot_sha256
    assert rules.POLICY_RULE_SOURCE_SHA256 == hashlib.sha256(Path(rules.__file__).read_bytes()).hexdigest()
