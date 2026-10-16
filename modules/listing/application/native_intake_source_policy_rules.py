"""Classify a locked registry snapshot; this alone grants no source access.

The native owner must prove the triggering event, current tenant/input, global
host-match count and transaction/lease fences. Positive decisions expire and
must be checked again by a future retrieval or supplied-evidence command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

from modules.listing.application.native_intake_url_identity import (
    NATIVE_URL_RULE_VERSION,
    normalize_native_intake_url,
)

POLICY_RULE_VERSION = "native-source-policy-v1"
SOURCE_POLICY_FIELDS = (
    "source_id", "allowed_hosts", "canonicalization_rule_version", "retrieval_mode",
    "legal_approval_ref", "license_approval_ref", "policy_owner_subject_id",
    "review_expires_at", "requests_per_minute", "concurrent_requests",
    "kill_switch", "production_enabled", "version",
)


class NativeSourcePolicyRuleUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class NativeSourcePolicyClassification:
    target_state: str
    effective_policy: str
    reason_code: str
    permission: str
    audit_action: str
    valid_until: datetime | None
    source_snapshot_json: str
    source_snapshot_sha256: str


def _aware_time(value) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("An aware database time is required")
    return result


def _hosts(value):
    return json.loads(value) if isinstance(value, str) else value


def source_policy_snapshot_json(source: dict) -> str:
    """Keep only decision inputs, preserving invalid-but-serializable values."""
    snapshot = {key: source.get(key) for key in SOURCE_POLICY_FIELDS}
    for key, value in snapshot.items():
        if isinstance(value, (datetime, UUID)):
            snapshot[key] = value.isoformat() if isinstance(value, datetime) else str(value)
    try:
        snapshot["allowed_hosts"] = _hosts(snapshot["allowed_hosts"])
    except (ValueError, TypeError):
        pass
    try:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise NativeSourcePolicyRuleUnavailable("NATIVE_SOURCE_POLICY_SNAPSHOT_UNAVAILABLE") from error


def _associated(source: dict, source_host: str, expected_source_id: str, count: int) -> bool:
    try:
        if (type(count) is not int or count != 1
                or not isinstance(expected_source_id, str) or not expected_source_id
                or source.get("source_id") != expected_source_id
                or source.get("canonicalization_rule_version") != NATIVE_URL_RULE_VERSION
                or normalize_native_intake_url("https://" + source_host).source_host != source_host):
            return False
        hosts = _hosts(source.get("allowed_hosts"))
        return (isinstance(hosts, list) and 1 <= len(hosts) <= 64
                and all(isinstance(host, str)
                        and normalize_native_intake_url("https://" + host).source_host == host.lower()
                        for host in hosts)
                and source_host in [host.lower() for host in hosts])
    except (ValueError, TypeError, AttributeError):
        return False


def _approval_current(source: dict, now: datetime) -> datetime | None:
    try:
        owner = source["policy_owner_subject_id"]
        if (not isinstance(owner, str) or not UUID(owner).int or str(UUID(owner)) != owner
                or owner == str(uuid5(NAMESPACE_DNS, "system"))
                or type(source.get("version")) is not int or source["version"] < 1
                or any(not isinstance(source.get(key), str) or not source[key].strip()
                       for key in ("legal_approval_ref", "license_approval_ref"))):
            return None
        expiry = _aware_time(source["review_expires_at"])
        return expiry if expiry > now else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def evaluate_native_source_policy(source: dict, *, source_host: str, expected_source_id: str,
                                  host_match_count: int, now: datetime) -> NativeSourcePolicyClassification:
    """Use current DB time and explicit approval; never infer an AUTH fallback.

    AUTH_REQUIRED's native destination is an OPEN contract question. It takes
    the explicit unsupported path even with a kill switch, rather than imposing
    quarantine's independent-unlock requirement without a product decision.
    """
    try:
        now = _aware_time(now)
    except (ValueError, TypeError, AttributeError) as error:
        raise NativeSourcePolicyRuleUnavailable("NATIVE_SOURCE_POLICY_DB_CLOCK_UNAVAILABLE") from error
    if source.get("retrieval_mode") == "AUTH_REQUIRED":
        raise NativeSourcePolicyRuleUnavailable("NATIVE_SOURCE_POLICY_AUTH_REQUIRED_CONTRACT_OPEN")

    snapshot = source_policy_snapshot_json(source)

    def decision(target, policy, reason, *, expiry=None):
        permission, action = {
            "RETRIEVING": ("source.retrieve", "intake.retrieval_approved.v1"),
            "AWAITING_ASSISTED_ENTRY": ("intake.assisted_entry", "intake.assisted_entry_required.v1"),
            # This is a state-contract audit action. Publication still uses
            # the existing closed-schema intake.state_changed domain event.
            "QUARANTINED": ("intake.quarantine", "intake.quarantined.v1"),
        }[target]
        return NativeSourcePolicyClassification(target, policy, reason, permission, action,
            expiry, snapshot, hashlib.sha256(snapshot.encode()).hexdigest())

    if (source.get("kill_switch") is True or source.get("production_enabled") is False
            or source.get("retrieval_mode") == "SOURCE_BLOCKED"):
        return decision("QUARANTINED", "SOURCE_BLOCKED", "SOURCE_ACCESS_BLOCKED")
    if source.get("kill_switch") is not False or source.get("production_enabled") is not True:
        return decision("QUARANTINED", "POLICY_UNKNOWN", "SOURCE_ENABLEMENT_UNVERIFIED")
    if not _associated(source, source_host, expected_source_id, host_match_count):
        return decision("QUARANTINED", "POLICY_UNKNOWN", "SOURCE_ASSOCIATION_UNVERIFIED")
    expiry = _approval_current(source, now)
    if expiry is None:
        return decision("QUARANTINED", "POLICY_UNKNOWN", "SOURCE_APPROVAL_UNVERIFIED")
    mode = source.get("retrieval_mode")
    if mode == "ASSISTED_ENTRY_ONLY":
        return decision("AWAITING_ASSISTED_ENTRY", mode, "ASSISTED_ENTRY_APPROVED", expiry=expiry)
    if mode == "APPROVED_RETRIEVAL":
        if any(type(source.get(key)) is not int or source[key] < 1
               for key in ("requests_per_minute", "concurrent_requests")):
            # Explicit interpretation: zero budgets are incomplete operational
            # policy, and cannot authorize a subsequent network side effect.
            return decision("QUARANTINED", "POLICY_UNKNOWN", "SOURCE_RETRIEVAL_LIMITS_UNVERIFIED")
        return decision("RETRIEVING", mode, "RETRIEVAL_APPROVED", expiry=expiry)
    return decision("QUARANTINED", "POLICY_UNKNOWN", "SOURCE_POLICY_UNKNOWN")


POLICY_RULE_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
