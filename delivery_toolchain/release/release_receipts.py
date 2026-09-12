#!/usr/bin/env python3
"""Canonical release evidence receipts and secret redaction.

Release evidence is consumed by more than one deployment stage.  This module
keeps the identity that makes a receipt useful (release, manifest, commit,
environment, and stage) in one small, dependency-free envelope.  Producers
may put arbitrary diagnostic data in ``details``; it is recursively redacted
before it becomes part of the receipt.

The Runtime Release workflow currently produces the files in
``RUNTIME_RELEASE_ARTIFACT_ALLOWLIST``.  The list is intentionally literal:
the raw Cloud Run ``describe`` dumps written beside those reports contain
secret selectors and are not evidence artifacts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from delivery_toolchain.release.release_manifest import (
    RELEASE_ID_PATTERN,
    is_exact_sha,
    is_sha256_digest,
)

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION,)
REDACTED = "[REDACTED]"

ENVIRONMENT_ALIASES = {"dev": "dev", "staging": "staging", "prod": "prod", "production": "prod"}
ENVIRONMENTS = frozenset(ENVIRONMENT_ALIASES.values())

# These are the admission boundaries from the rollout plan.  A receipt must
# never claim, for example, that a dev artifact is a staging-verified proof.
STAGE_ENVIRONMENT = {
    "candidate-built": "dev",
    "dev-verified": "dev",
    "staging-verified": "staging",
    "prod-admitted": "prod",
    "prod-switched": "prod",
    "release-complete": "prod",
}
STAGES = frozenset(STAGE_ENVIRONMENT)

RECEIPT_KINDS = frozenset({"deployment", "verification", "cleanup", "rollback"})
RECEIPT_RESULTS = frozenset({"pass", "fail"})

# The paths below are the files explicitly uploaded by
# .github/workflows/deploy-dev.yml.  Keep this tuple in sync with the actual
# producers; do not replace it with a recursive glob.
RUNTIME_RELEASE_ARTIFACT_ALLOWLIST = (
    ".odp_data/deployment/cloud-run-preflight.json",
    ".odp_data/deployment/cloud-run-smoke.json",
    ".odp_data/deployment/cloud-run-migration-compatibility.json",
    ".odp_data/deployment/live-e2e-gate.json",
    ".odp_data/deployment/public-egress-probe.json",
    ".odp_data/deployment/cloud-run-jobs/migration-validation.json",
    ".odp_data/deployment/cloud-run-jobs/scheduler-validation.json",
    ".odp_data/deployment/cloud-run-jobs/worker-validation.json",
    ".odp_data/remote-staging-proof/staging-{run_id}.json",
)
# Public aliases make the contract easy for producer and contract-test code to
# discover without duplicating the path list.
ARTIFACT_ALLOWLIST = RUNTIME_RELEASE_ARTIFACT_ALLOWLIST
RELEASE_ARTIFACT_ALLOWLIST = RUNTIME_RELEASE_ARTIFACT_ALLOWLIST

UNREDACTED_ARTIFACT_SUFFIXES = ("-job.json", "-execution.json", "-execution-list.json")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_SECRET_KEY_PARTS = frozenset(
    {
        "configured",
        "env",
        "environment",
        "id",
        "name",
        "owner",
        "ref",
        "reference",
        "selector",
        "selectors",
        "status",
        "version",
    }
)
SAFE_SECRET_KEY_RE = re.compile(
    r"^(?:secret|credential)(?:[_-](?:configured|env|environment|id|name|owner|ref|"
    r"reference|selector|selectors|status|values_redacted|version))+$|"
    r"^(?:required[_-])?secret[_-]env[_-]vars$",
    re.IGNORECASE,
)
SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(access[_-]?token|api[_-]?key|authorization|bearer|client[_-]?secret|"
    r"connection[_-]?string|cookie|credential|database[_-]?url|dsn|password|passwd|"
    r"private[_-]?key|refresh[_-]?token|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_STRING_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)([?&](?:access_token|api_key|apikey|client_secret|password|secret|token)=)[^&#\s]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"), r"\1" + REDACTED + r"\2"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.|\n)*?-----END [A-Z ]*PRIVATE KEY-----"), REDACTED),
)


class ReceiptValidationError(ValueError):
    """Raised when a receipt cannot be safely created or written."""


def iso_now() -> str:
    """Return a UTC RFC3339 timestamp suitable for a receipt."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Serialize a JSON value deterministically for evidence hashing."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalize_environment(environment: Any) -> str:
    """Normalize the external ``production`` spelling to receipt ``prod``."""

    normalized = str(environment or "").strip().lower()
    try:
        return ENVIRONMENT_ALIASES[normalized]
    except KeyError as exc:
        raise ReceiptValidationError(
            f"environment must be one of {sorted(ENVIRONMENTS)}, got {environment!r}"
        ) from exc


def _key_parts(key: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", key.lower()) if part}


def _is_sensitive_key(key: str) -> bool:
    if SAFE_SECRET_KEY_RE.fullmatch(key):
        return False
    parts = _key_parts(key)
    if not parts or parts & SAFE_SECRET_KEY_PARTS == parts:
        return False
    return bool(SECRET_KEY_RE.search(key))


def _redact_string(value: str, secret_values: Sequence[str]) -> str:
    result = value
    # Explicit values are collected from caller-provided secrets and from
    # sensitive keyed fields before recursion.  Longest-first avoids replacing
    # a short token inside a longer token in a surprising order.
    for secret in sorted({item for item in secret_values if item}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    for pattern, replacement in SECRET_STRING_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _collect_keyed_secret_values(value: Any, output: list[str], *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_name = str(child_key)
            if _is_sensitive_key(child_name) and isinstance(child_value, str):
                output.append(child_value)
            _collect_keyed_secret_values(child_value, output, key=child_name)
    elif isinstance(value, list):
        for child in value:
            _collect_keyed_secret_values(child, output, key=key)


def _redact_value(value: Any, secret_values: Sequence[str], redacted_paths: list[str], path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                result[key] = REDACTED
                redacted_paths.append(child_path)
            else:
                result[key] = _redact_value(child, secret_values, redacted_paths, child_path)
        return result
    if isinstance(value, list):
        return [
            _redact_value(child, secret_values, redacted_paths, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return [
            _redact_value(child, secret_values, redacted_paths, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, str):
        return _redact_string(value, secret_values)
    return copy.deepcopy(value)


def redact_secrets(value: Any, *, secret_values: Iterable[str] = ()) -> tuple[Any, dict[str, Any]]:
    """Return a deep-redacted copy and a non-sensitive redaction summary.

    ``secret_values`` is useful when a provider returns an opaque token under
    a neutral key.  Sensitive-looking keys and common bearer/query/PEM forms
    are redacted even when the caller does not know the concrete value.  The
    input is never mutated and the summary contains paths/counts only.
    """

    explicit = [str(item) for item in secret_values if isinstance(item, str) and item]
    _collect_keyed_secret_values(value, explicit)
    redacted_paths: list[str] = []
    result = _redact_value(value, explicit, redacted_paths, "")
    return result, {
        "secret_values_redacted": True,
        "redacted_field_count": len(redacted_paths),
        "redacted_fields": sorted(set(redacted_paths)),
    }


def redact(value: Any, *, secret_values: Iterable[str] = ()) -> Any:
    """Convenience wrapper returning only the redacted value."""

    return redact_secrets(value, secret_values=secret_values)[0]


def artifact_allowlist_for_run(run_id: str | int | None = None) -> tuple[str, ...]:
    """Return the literal artifact paths for a workflow run.

    Without a run id, the run-scoped staging proof keeps its ``{run_id}``
    template so callers can compare it to the workflow source.  A supplied id
    is validated before interpolation to prevent path injection.
    """

    if run_id is None:
        return RUNTIME_RELEASE_ARTIFACT_ALLOWLIST
    rendered = str(run_id).strip()
    if not rendered or not RUN_ID_PATTERN.fullmatch(rendered):
        raise ReceiptValidationError("run_id must contain only letters, digits, '.', '_' or '-'")
    return tuple(path.replace("{run_id}", rendered) for path in RUNTIME_RELEASE_ARTIFACT_ALLOWLIST)


def _normalize_artifact_path(path: Any) -> str:
    if not isinstance(path, str):
        raise ReceiptValidationError("artifact path must be a string")
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
        raise ReceiptValidationError(f"artifact path is not repository-relative: {path!r}")
    if any(char in normalized for char in "*?[]"):
        raise ReceiptValidationError(f"artifact path must be literal, not a glob: {path!r}")
    return normalized


def is_allowed_artifact(path: Any, *, run_id: str | int | None = None) -> bool:
    """Return whether *path* is one of the files emitted by Runtime Release."""

    try:
        normalized = _normalize_artifact_path(path)
    except ReceiptValidationError:
        return False
    allowed = artifact_allowlist_for_run(run_id)
    if normalized in allowed:
        return True
    # A template is useful for a source-level check; when no run id was given,
    # accept only a well-formed concrete staging filename, never a wildcard.
    template = ".odp_data/remote-staging-proof/staging-{run_id}.json"
    if run_id is None and normalized.startswith(template.removesuffix("{run_id}.json")):
        suffix = normalized.removeprefix(".odp_data/remote-staging-proof/staging-")
        return bool(suffix.endswith(".json") and RUN_ID_PATTERN.fullmatch(suffix[:-5] or ""))
    return False


def validate_artifact_allowlist(
    artifacts: Iterable[Any], *, run_id: str | int | None = None
) -> list[str]:
    """Validate receipt artifact references against the producer allowlist."""

    errors: list[str] = []
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        path = artifact.get("path") if isinstance(artifact, Mapping) else artifact
        try:
            normalized = _normalize_artifact_path(path)
        except ReceiptValidationError as exc:
            errors.append(f"artifacts[{index}]: {exc}")
            continue
        if normalized in seen:
            errors.append(f"artifacts[{index}] duplicates {normalized}")
        seen.add(normalized)
        if not is_allowed_artifact(normalized, run_id=run_id):
            errors.append(f"artifacts[{index}] is not produced by Runtime Release: {normalized}")
        if normalized.endswith(UNREDACTED_ARTIFACT_SUFFIXES):
            errors.append(f"artifacts[{index}] is an unredacted Cloud Run dump: {normalized}")
        if isinstance(artifact, Mapping) and "sha256" in artifact:
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"artifacts[{index}].sha256 must be 64 lowercase hex characters")
    return errors


def _artifact_entries(artifacts: Iterable[Any], artifact: Any | None) -> list[Any]:
    values = list(artifacts)
    if artifact is not None:
        values.insert(0, artifact)
    if not values:
        raise ReceiptValidationError("at least one artifact reference is required")
    result: list[Any] = []
    for item in values:
        if isinstance(item, Mapping):
            path = _normalize_artifact_path(item.get("path"))
            entry: dict[str, Any] = {"path": path}
            if "sha256" in item:
                entry["sha256"] = item["sha256"]
            result.append(entry)
        else:
            result.append(_normalize_artifact_path(item))
    return result


def _artifact_path(entry: Any) -> str:
    return entry.get("path", "") if isinstance(entry, Mapping) else str(entry)


def _validate_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _unredacted_sensitive_paths(value: Any, path: str = "") -> list[str]:
    """Find suspicious keyed values that a producer failed to redact."""

    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                if child not in (REDACTED, None, False, "", [], {}):
                    found.append(child_path)
            else:
                found.extend(_unredacted_sensitive_paths(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_unredacted_sensitive_paths(child, f"{path}[{index}]"))
    return found


def _validate_common_identity(receipt: Mapping[str, Any], errors: list[str]) -> None:
    release_id = receipt.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append("release_id must be a stable release identifier")
    for field in ("release_sha", "candidate_sha"):
        value = receipt.get(field)
        if not is_exact_sha(value):
            errors.append(f"{field} must be an exact 40-character lowercase git SHA")
    if is_exact_sha(receipt.get("release_sha")) and is_exact_sha(receipt.get("candidate_sha")):
        if receipt["release_sha"] != receipt["candidate_sha"]:
            errors.append("release_sha and candidate_sha must identify the same candidate")
    manifest_ref = receipt.get("manifest_ref")
    if not isinstance(manifest_ref, str) or not manifest_ref.strip():
        errors.append("manifest_ref must be a non-empty repository-relative path")
    elif Path(manifest_ref).is_absolute() or ".." in Path(manifest_ref).parts:
        errors.append("manifest_ref must not escape the repository root")
    if not is_sha256_digest(receipt.get("manifest_digest")):
        errors.append("manifest_digest must be a sha256:<64 lowercase hex> digest")


def validate_receipt(
    receipt: Any,
    *,
    expected_release_id: str | None = None,
    expected_candidate_sha: str | None = None,
    expected_manifest_digest: str | None = None,
    run_id: str | int | None = None,
) -> list[str]:
    """Return all receipt contract errors; an empty list means valid.

    The function is deliberately non-throwing so gate checkers can report all
    missing or stale evidence in one run.  It fails closed on identity drift,
    stage/environment mismatch, unredacted keyed values, and artifacts not
    emitted by the reviewed Runtime Release producers.
    """

    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return ["receipt must be a JSON object"]
    if receipt.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"schema_version must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}")
    required = (
        "receipt_id",
        "receipt_kind",
        "release_id",
        "manifest_ref",
        "manifest_digest",
        "release_sha",
        "candidate_sha",
        "environment",
        "stage",
        "result",
        "recorded_at",
        "recorded_by",
        "artifacts",
        "secret_values_redacted",
    )
    for field in required:
        if field not in receipt:
            errors.append(f"receipt missing required field: {field}")

    if not isinstance(receipt.get("receipt_id"), str) or not str(receipt.get("receipt_id")).strip():
        errors.append("receipt_id must be a non-empty string")
    if receipt.get("receipt_kind") not in RECEIPT_KINDS:
        errors.append(f"receipt_kind must be one of {sorted(RECEIPT_KINDS)}")
    _validate_common_identity(receipt, errors)
    if expected_release_id is not None and receipt.get("release_id") != expected_release_id:
        errors.append("release_id does not match the expected release")
    if expected_candidate_sha is not None and receipt.get("candidate_sha") != expected_candidate_sha:
        errors.append("candidate_sha does not match the expected release candidate")
    if expected_manifest_digest is not None and receipt.get("manifest_digest") != expected_manifest_digest:
        errors.append("manifest_digest does not match the expected release manifest")

    try:
        environment = normalize_environment(receipt.get("environment"))
    except ReceiptValidationError as exc:
        errors.append(str(exc))
        environment = ""
    stage = receipt.get("stage")
    if stage not in STAGES:
        errors.append(f"stage must be one of {sorted(STAGES)}")
    elif environment and STAGE_ENVIRONMENT[stage] != environment:
        errors.append(f"stage {stage!r} requires environment {STAGE_ENVIRONMENT[stage]!r}")

    if receipt.get("result") not in RECEIPT_RESULTS:
        errors.append(f"result must be one of {sorted(RECEIPT_RESULTS)}")
    if not _validate_timestamp(receipt.get("recorded_at")):
        errors.append("recorded_at must be an RFC3339 timestamp with timezone")
    if not isinstance(receipt.get("recorded_by"), str) or not receipt.get("recorded_by").strip():
        errors.append("recorded_by must be a non-empty string")
    if receipt.get("secret_values_redacted") is not True:
        errors.append("secret_values_redacted must be true")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts must be a non-empty list")
        artifacts = []
    else:
        errors.extend(validate_artifact_allowlist(artifacts, run_id=run_id))
        primary = receipt.get("artifact")
        if primary is not None and primary not in {_artifact_path(item) for item in artifacts}:
            errors.append("artifact must name one of the entries in artifacts")

    for path in _unredacted_sensitive_paths(receipt):
        errors.append(f"unredacted sensitive value at {path}")
    return errors


def build_receipt(
    *,
    receipt_id: str,
    receipt_kind: str,
    release_id: str,
    manifest_ref: str,
    manifest_digest: str,
    release_sha: str,
    environment: str,
    stage: str,
    result: str,
    recorded_by: str,
    artifacts: Iterable[Any] = (),
    artifact: Any | None = None,
    details: Any | None = None,
    recorded_at: str | None = None,
    secret_values: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a validated, redacted release receipt.

    ``release_sha`` is duplicated as ``candidate_sha`` intentionally: the
    former matches the existing Gate 0-6 receipt contract while the latter
    makes the binding to the immutable manifest explicit to new consumers.
    """

    normalized_environment = normalize_environment(environment)
    entries = _artifact_entries(artifacts, artifact)
    raw_details = copy.deepcopy(details) if details is not None else {}
    redacted_details, redaction = redact_secrets(raw_details, secret_values=secret_values)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "receipt_kind": receipt_kind,
        "release_id": release_id,
        "manifest_ref": manifest_ref,
        "manifest_digest": manifest_digest,
        "release_sha": release_sha,
        "candidate_sha": release_sha,
        "environment": normalized_environment,
        "stage": stage,
        "result": result,
        "status": "passed" if result == "pass" else "failed",
        "recorded_at": recorded_at or iso_now(),
        "recorded_by": recorded_by,
        "artifact": _artifact_path(entries[0]),
        "artifacts": entries,
        "secret_values_redacted": True,
        "redaction": redaction,
        "details": redacted_details,
    }
    errors = validate_receipt(receipt)
    if errors:
        raise ReceiptValidationError("invalid release receipt: " + "; ".join(errors))
    return receipt


def create_receipt(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for callers that use a creation verb."""

    return build_receipt(**kwargs)


def receipt_from_report(
    report: Mapping[str, Any],
    *,
    receipt_id: str,
    receipt_kind: str,
    release_id: str,
    manifest_ref: str,
    manifest_digest: str,
    release_sha: str,
    environment: str,
    stage: str,
    recorded_by: str,
    artifacts: Iterable[Any] = (),
    result: str | None = None,
    secret_values: Iterable[str] = (),
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Wrap an existing producer report in the canonical redacted envelope."""

    inferred = result or report.get("result") or report.get("status") or report.get("ok")
    if inferred in (True, "passed", "success", "succeeded", "ok"):
        inferred = "pass"
    elif inferred in (False, "failed", "failure", "error"):
        inferred = "fail"
    return build_receipt(
        receipt_id=receipt_id,
        receipt_kind=receipt_kind,
        release_id=release_id,
        manifest_ref=manifest_ref,
        manifest_digest=manifest_digest,
        release_sha=release_sha,
        environment=environment,
        stage=stage,
        result=str(inferred),
        recorded_by=recorded_by,
        artifacts=artifacts,
        details=report,
        recorded_at=recorded_at,
        secret_values=secret_values,
    )


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Validate and atomically write one receipt as UTF-8 JSON."""

    errors = validate_receipt(receipt)
    if errors:
        raise ReceiptValidationError("refusing to write invalid release receipt: " + "; ".join(errors))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(target)


def read_receipt(
    path: Path,
    *,
    expected_release_id: str | None = None,
    expected_candidate_sha: str | None = None,
    expected_manifest_digest: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and validate a JSON receipt without guessing missing identity."""

    target = Path(path)
    if not target.is_file():
        return None, [f"receipt file does not exist: {target}"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"receipt cannot be read as JSON: {exc}"]
    errors = validate_receipt(
        payload,
        expected_release_id=expected_release_id,
        expected_candidate_sha=expected_candidate_sha,
        expected_manifest_digest=expected_manifest_digest,
    )
    return (dict(payload) if isinstance(payload, Mapping) else None), errors


__all__ = [
    "ARTIFACT_ALLOWLIST",
    "ENVIRONMENTS",
    "RECEIPT_KINDS",
    "RECEIPT_RESULTS",
    "REDACTED",
    "RELEASE_ARTIFACT_ALLOWLIST",
    "RUNTIME_RELEASE_ARTIFACT_ALLOWLIST",
    "STAGES",
    "ReceiptValidationError",
    "artifact_allowlist_for_run",
    "build_receipt",
    "canonical_json",
    "create_receipt",
    "is_allowed_artifact",
    "iso_now",
    "normalize_environment",
    "read_receipt",
    "receipt_from_report",
    "redact",
    "redact_secrets",
    "sha256_bytes",
    "sha256_file",
    "validate_artifact_allowlist",
    "validate_receipt",
    "write_receipt",
]
