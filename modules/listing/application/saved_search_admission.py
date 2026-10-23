"""Read current approved-feed policy and snapshot bindings without retrieval."""

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def _time(value: Any) -> datetime | None:
    try:
        result = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return result.astimezone(UTC) if result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def listing_search_source_admitted(engine: Any, receipt: dict[str, Any], tenant: str) -> bool:
    if not isinstance(receipt.get("scope"), dict) or receipt["scope"].get("tenant_id") != tenant:
        return False
    method, policy_state = receipt.get("intake_method"), receipt.get("policy_state")
    if method in {"URL", "MANUAL", "CSV"} and policy_state == "ASSISTED_ENTRY_ONLY":
        # Explicit human supplied effective facts confer no fetch permission.
        return True
    if (
        method not in {"URL", "MANUAL", "CSV", "APPROVED_FEED"}
        or policy_state != "APPROVED_RETRIEVAL"
    ):
        return False
    source, version = receipt.get("source_id"), receipt.get("source_policy_version")
    snapshot_id, snapshot_version = (
        receipt.get("source_snapshot_id"),
        receipt.get("source_snapshot_version"),
    )
    if (
        not isinstance(source, str)
        or not source.strip()
        or type(version) is not int
        or version < 1
        or not isinstance(snapshot_id, str)
        or not snapshot_id
        or type(snapshot_version) is not int
        or snapshot_version < 1
        or receipt.get("scope", {}).get("tenant_id") != tenant
    ):
        return False
    try:
        UUID(snapshot_id)
        UUID(receipt.get("intake_id"))
        UUID(tenant)
    except (ValueError, TypeError, AttributeError):
        return False
    now = datetime.now(UTC)
    with engine.transaction():
        present = engine.query_one(
            "SELECT to_regclass('intake.source_registry') AS policy, "
            "to_regclass('intake.source_snapshots') AS snapshot"
        )
        if not present or present["policy"] is None or present["snapshot"] is None:
            return False
        policy = engine.query_one(
            "SELECT * FROM intake.source_registry WHERE source_id = ?", (source,)
        )
        expiry = _time(policy.get("review_expires_at")) if policy else None
        if (
            policy is None
            or policy.get("version") != version
            or policy.get("kill_switch") is not False
            or policy.get("production_enabled") is not True
            or policy.get("retrieval_mode") != "APPROVED_RETRIEVAL"
            or not isinstance(policy.get("legal_approval_ref"), str)
            or not policy["legal_approval_ref"].strip()
            or not isinstance(policy.get("license_approval_ref"), str)
            or not policy["license_approval_ref"].strip()
            or expiry is None
            or expiry <= now
        ):
            return False
        engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (tenant,))
        snapshot = engine.query_one(
            "SELECT * FROM intake.source_snapshots WHERE source_snapshot_id = ? AND tenant_id = ?",
            (snapshot_id, tenant),
        )
        if (
            snapshot is None
            or str(snapshot.get("intake_id")) != receipt.get("intake_id")
            or snapshot.get("source_id") != source
            or snapshot.get("version") != snapshot_version
            or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("content_sha256")))
            or not snapshot.get("raw_object_uri")
            or not snapshot.get("encryption_key_ref")
        ):
            return False
        allowed_capture = (
            {"APPROVED_FEED"}
            if method == "APPROVED_FEED"
            else {"SERVER_RETRIEVAL", "MANUAL_ENTRY", "OPERATOR_UPLOAD"}
        )
        if snapshot.get("capture_method") not in allowed_capture:
            return False
        for name in ("captured_at", "observed_at"):
            stamp = _time(snapshot.get(name))
            if stamp is None or stamp > now:
                return False
        if snapshot.get("purge_after") is not None and not snapshot.get("legal_hold"):
            expiry = _time(snapshot["purge_after"])
            if expiry is None or expiry <= now:
                return False
        return True
