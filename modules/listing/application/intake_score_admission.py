"""Check recorded source and parser admission against the live SQL registry.

This guard cannot issue an approval or infer provenance from a URL, display
label, or parser default. The intake pipeline must have recorded the exact
policy and snapshot bindings before a candidate can enter scoring.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def _deny(code: str) -> None:
    # Imported when called to keep delivery/admission composition acyclic.
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied

    raise ScoreDeliveryDenied(code)


def _uuid(value: Any, code: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        _deny(code)
    raise AssertionError("unreachable")


def _time(value: Any, code: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        _deny(code)
    raise AssertionError("unreachable")


def _positive_version(value: Any, code: str) -> int:
    if type(value) is not int or value < 1:
        _deny(code)
    return value


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _json_field(value: Any, expected_type: type, code: str) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            _deny(code)
    if not isinstance(value, expected_type):
        _deny(code)
    return value


def validate_score_admission(engine: Any, intake: dict[str, Any], candidate: Any) -> dict[str, Any]:
    """Return immutable admission facts while holding the registry rows stable.

    The caller owns the SQL transaction and tenant context. Rechecking this
    value before committing an inference prevents revocation or a new policy
    version from being hidden by an old successful enqueue receipt.
    """
    if getattr(engine, "dialect", None) != "postgresql":
        _deny("SITESCORE_ADMISSION_REGISTRY_UNAVAILABLE")
    now = datetime.now(UTC)
    tenant = _uuid(intake.get("scope", {}).get("tenant_id"), "SITESCORE_ADMISSION_TENANT_REQUIRED")
    intake_id = _uuid(intake.get("intake_id"), "SITESCORE_ADMISSION_INTAKE_REQUIRED")
    listing = candidate.listing
    source_id = intake.get("source_id")
    if not _nonempty(source_id) or source_id != listing.source_id:
        _deny("SITESCORE_SOURCE_BINDING_DENIED")
    policy_version = _positive_version(intake.get("source_policy_version"), "SITESCORE_SOURCE_POLICY_PIN_REQUIRED")
    policy = engine.query_one("SELECT * FROM intake.source_registry WHERE source_id=? FOR SHARE", (source_id,))
    if (policy is None or policy["version"] != policy_version
            or policy["kill_switch"] or not policy["production_enabled"]
            or policy["retrieval_mode"] not in {"APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY"}
            or not _nonempty(policy["legal_approval_ref"])
            or not _nonempty(policy["license_approval_ref"])):
        _deny("SITESCORE_SOURCE_POLICY_DENIED")
    expiry = _time(policy["review_expires_at"], "SITESCORE_SOURCE_REVIEW_EXPIRY_REQUIRED")
    if expiry <= now:
        _deny("SITESCORE_SOURCE_REVIEW_EXPIRED")
    if intake.get("policy_state") != policy["retrieval_mode"]:
        _deny("SITESCORE_SOURCE_POLICY_STATE_MISMATCH")

    snapshot_id = _uuid(intake.get("source_snapshot_id"), "SITESCORE_SOURCE_SNAPSHOT_REQUIRED")
    if snapshot_id != listing.snapshot_id or candidate.dataset_snapshot_id != snapshot_id:
        _deny("SITESCORE_SOURCE_SNAPSHOT_BINDING_DENIED")
    snapshot = engine.query_one(
        "SELECT * FROM intake.source_snapshots WHERE source_snapshot_id=? AND tenant_id=? FOR SHARE",
        (snapshot_id, tenant),
    )
    if (snapshot is None or str(snapshot["intake_id"]) != intake_id or snapshot["source_id"] != source_id
            or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot["content_sha256"]))
            or not _nonempty(snapshot["raw_object_uri"]) or not _nonempty(snapshot["encryption_key_ref"])
            or not _nonempty(snapshot["media_type"])):
        _deny("SITESCORE_SOURCE_SNAPSHOT_PROVENANCE_DENIED")
    snapshot_version = _positive_version(snapshot["version"], "SITESCORE_SOURCE_SNAPSHOT_VERSION_REQUIRED")
    captured_at = _time(snapshot["captured_at"], "SITESCORE_SOURCE_CAPTURE_TIME_REQUIRED")
    observed_at = _time(snapshot["observed_at"], "SITESCORE_SOURCE_OBSERVED_TIME_REQUIRED")
    if captured_at > now or observed_at > now:
        _deny("SITESCORE_SOURCE_SNAPSHOT_FUTURE_DENIED")
    if snapshot.get("purge_after") is not None and not snapshot["legal_hold"]:
        if _time(snapshot["purge_after"], "SITESCORE_SOURCE_RETENTION_REQUIRED") <= now:
            _deny("SITESCORE_SOURCE_SNAPSHOT_EXPIRED")

    admission = {
        "source_id": source_id, "source_policy_version": policy_version,
        "source_policy_sha256": _row_hash(policy), "source_snapshot_metadata_sha256": _row_hash(snapshot),
        "retrieval_mode": policy["retrieval_mode"],
        "legal_approval_ref": policy["legal_approval_ref"], "license_approval_ref": policy["license_approval_ref"],
        "review_expires_at": expiry.isoformat(),
        "source_snapshot_id": snapshot_id, "source_snapshot_version": snapshot_version,
        "source_content_sha256": snapshot["content_sha256"], "capture_method": snapshot["capture_method"],
        "captured_at": captured_at.isoformat(), "observed_at": observed_at.isoformat(),
    }
    if snapshot["capture_method"] == "MANUAL_ENTRY":
        if any(intake.get(key) is not None for key in (
            "parser_run_id", "parser_release_id", "parser_release_version",
            "parser_input_schema_version", "parser_output_schema_version",
        )):
            _deny("SITESCORE_MANUAL_PARSER_BINDING_DENIED")
        admission["parser_release_id"] = None
        return admission
    if snapshot["capture_method"] not in {"SERVER_RETRIEVAL", "APPROVED_FEED", "OPERATOR_UPLOAD"}:
        _deny("SITESCORE_SOURCE_CAPTURE_METHOD_DENIED")
    if snapshot["capture_method"] in {"SERVER_RETRIEVAL", "APPROVED_FEED"} and policy["retrieval_mode"] != "APPROVED_RETRIEVAL":
        _deny("SITESCORE_SOURCE_RETRIEVAL_DENIED")

    release_id = _uuid(intake.get("parser_release_id"), "SITESCORE_PARSER_RELEASE_REQUIRED")
    release_version = _positive_version(intake.get("parser_release_version"), "SITESCORE_PARSER_VERSION_REQUIRED")
    parser_run_id = _uuid(intake.get("parser_run_id"), "SITESCORE_PARSER_RUN_REQUIRED")
    release = engine.query_one("SELECT * FROM intake.parser_releases WHERE parser_release_id=? FOR SHARE", (release_id,))
    if (release is None or release["source_id"] != source_id or release["version"] != release_version
            or release["validation_status"] != "PRODUCTION" or release["deprecated_at"] is not None
            or not _nonempty(release["artifact_uri"])
            or not re.fullmatch(r"[0-9a-f]{64}", str(release["artifact_sha256"]))
            or not release["released_by"]):
        _deny("SITESCORE_PARSER_RELEASE_DENIED")
    if _time(release["released_at"], "SITESCORE_PARSER_RELEASE_TIME_REQUIRED") > now:
        _deny("SITESCORE_PARSER_RELEASE_FUTURE_DENIED")
    for name in ("input_schema_version", "output_schema_version"):
        if not _nonempty(release[name]) or intake.get(f"parser_{name}") != release[name]:
            _deny("SITESCORE_PARSER_SCHEMA_COMPATIBILITY_DENIED")
    run = engine.query_one(
        "SELECT * FROM intake.parser_runs WHERE parser_run_id=? AND tenant_id=? FOR SHARE", (parser_run_id, tenant),
    )
    if (run is None or str(run["intake_id"]) != intake_id or str(run["source_snapshot_id"]) != snapshot_id
            or str(run["parser_release_id"]) != release_id or run["status"] != "SUCCEEDED"):
        _deny("SITESCORE_PARSER_RUN_BINDING_DENIED")
    if _json_field(run["validation_errors"], list, "SITESCORE_PARSER_RUN_BINDING_DENIED"):
        _deny("SITESCORE_PARSER_RUN_BINDING_DENIED")
    _json_field(run["normalized_payload"], dict, "SITESCORE_PARSER_RUN_BINDING_DENIED")
    if _time(run["completed_at"], "SITESCORE_PARSER_COMPLETION_REQUIRED") > now:
        _deny("SITESCORE_PARSER_COMPLETION_FUTURE_DENIED")
    admission.update({
        "parser_run_id": parser_run_id,
        "parser_run_version": _positive_version(run["version"], "SITESCORE_PARSER_RUN_VERSION_REQUIRED"),
        "parser_release_id": release_id, "parser_release_version": release_version,
        "parser_artifact_sha256": release["artifact_sha256"],
        "parser_release_metadata_sha256": _row_hash(release), "parser_run_metadata_sha256": _row_hash(run),
        "parser_input_schema_version": release["input_schema_version"],
        "parser_output_schema_version": release["output_schema_version"],
    })
    return admission
