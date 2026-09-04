#!/usr/bin/env python3
"""The one Supervisor-owned bridge from release admission to Runtime Release.

The existing lease module owns the signed schema, precondition checks and CAS
store.  This bridge deliberately owns only orchestration:

* it recognises an explicit, Human/Ops-approved release task;
* it obtains the signing key from Secret Manager only for the in-memory signing
  operation, never from a file or an environment variable;
* it persists the existing GCS CAS record and a secret-free task receipt before
  it dispatches the existing Runtime Release ``deploy`` phase; and
* it treats every uncertainty (including a failed status CAS after issuance or
  a failed GitHub response) as terminal for that approval nonce.

There is no second workflow, gate registry, lease format or scheduler here.
``process_release_lease_issuance`` is called from the normal Supervisor cycle
and is disabled unless the authoritative config explicitly enables it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from common import config_path, load_status, parse_iso_timestamp, write_activity_log
from release_lease import ISSUER_NAME, issue_release_lease, issuance_errors

from delivery_toolchain.release.release_lease import (
    DEFAULT_ACTION,
    LeaseError,
    LeaseStateStore,
    SHA256_DIGEST_PATTERN,
    SHA_PATTERN,
    build_receipt,
    load_private_key_material,
)
from delivery_toolchain.release.release_manifest import load_manifest


REQUEST_FIELD = "release_lease_request"
ISSUANCE_FIELD = "release_lease_issuance"
ISSUANCE_HISTORY_FIELD = "release_lease_issuance_history"
REQUEST_KIND = "runtime_release_deploy"
RUNTIME_RELEASE_WORKFLOW_PATH = ".github/workflows/deploy-dev.yml"
RUNTIME_RELEASE_WORKFLOW_ID = "deploy-dev.yml"
DEFAULT_SECRET_REFERENCE = (
    "projects/767864276141/secrets/odp-release-lease-private-key"
)
DEFAULT_TTL_SECONDS = 300
MAX_ISSUER_TTL_SECONDS = 600
MIN_ISSUER_TTL_SECONDS = 60
_SECRET_REFERENCE = re.compile(
    r"^projects/(?P<project>[0-9]+)/secrets/(?P<secret>[A-Za-z0-9_-]{1,255})$"
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MANIFEST_RUN_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_FINAL_NO_RETRY_STATES = {"issued", "dispatched", "dispatch_unknown"}


class SecretManagerAccessError(RuntimeError):
    """Secret Manager was unavailable or did not return a usable issuer key."""


class RuntimeReleaseDispatchError(RuntimeError):
    """GitHub did not conclusively accept the existing Runtime Release dispatch."""


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _safe_digest(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def issuer_settings(config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Return the public issuer settings, or only safe configuration errors."""

    raw = config.get("release_lease_issuer")
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["release_lease_issuer must be an object"]
    if raw.get("enabled") is not True:
        return None, []

    secret_reference = str(raw.get("secret_reference") or "").strip()
    state_uri = str(raw.get("state_uri") or "").strip()
    repository = str(raw.get("github_repository") or "").strip()
    workflow = str(raw.get("workflow") or RUNTIME_RELEASE_WORKFLOW_PATH).strip()
    errors: list[str] = []
    if not _SECRET_REFERENCE.fullmatch(secret_reference):
        errors.append("secret_reference must be a Secret Manager projects/<number>/secrets/<id> reference")
    if not re.fullmatch(r"gs://[^/]+/.+", state_uri):
        errors.append("state_uri must be an existing gs://bucket/prefix CAS store")
    if not _REPOSITORY.fullmatch(repository):
        errors.append("github_repository must be an owner/repository identifier")
    if workflow != RUNTIME_RELEASE_WORKFLOW_PATH:
        errors.append("workflow must remain the existing .github/workflows/deploy-dev.yml Runtime Release")
    try:
        ttl_seconds = int(raw.get("ttl_seconds", DEFAULT_TTL_SECONDS))
    except (TypeError, ValueError):
        ttl_seconds = 0
    if not MIN_ISSUER_TTL_SECONDS <= ttl_seconds <= MAX_ISSUER_TTL_SECONDS:
        errors.append(
            f"ttl_seconds must be between {MIN_ISSUER_TTL_SECONDS} and {MAX_ISSUER_TTL_SECONDS}"
        )
    if errors:
        return None, errors
    return {
        "secret_reference": secret_reference,
        "state_uri": state_uri,
        "github_repository": repository,
        "workflow": workflow,
        "ttl_seconds": ttl_seconds,
    }, []


def load_private_key_from_secret_reference(secret_reference: str):
    """Load a signing key from Secret Manager without persisting or logging it.

    ``gcloud`` receives only the public resource reference. Its stdout stays in
    this process' memory long enough to parse the key, then is discarded. Error
    details are intentionally not surfaced: command output could contain a
    provider diagnostic that a future implementation accidentally makes secret.
    """

    match = _SECRET_REFERENCE.fullmatch(secret_reference)
    if match is None:
        raise SecretManagerAccessError("secret_reference is invalid")
    command = [
        "gcloud",
        "secrets",
        "versions",
        "access",
        "latest",
        "--quiet",
        "--project",
        match.group("project"),
        "--secret",
        match.group("secret"),
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecretManagerAccessError("Secret Manager key access failed") from exc
    if result.returncode != 0 or not result.stdout:
        raise SecretManagerAccessError("Secret Manager key access failed")

    # bytearray lets us overwrite the mutable copy after parsing. Python and
    # cryptography can still keep implementation-level copies, but this bridge
    # never makes the key durable, printable, an environment variable, or a
    # child-process argument.
    material = bytearray(result.stdout)
    result.stdout = b""
    try:
        return load_private_key_material(bytes(material))
    except LeaseError as exc:
        raise SecretManagerAccessError("Secret Manager returned an unusable signing key") from exc
    finally:
        for index in range(len(material)):
            material[index] = 0


def request_fingerprint(task_id: str, request: dict[str, Any]) -> str:
    """Bind a task's one human approval nonce to its exact deploy identity."""

    return _stable_digest(
        {
            "task_id": task_id,
            "approval_id": str(request.get("approval_id") or ""),
            "approval_nonce_digest": _safe_digest(request.get("nonce")),
            "candidate_sha": str(request.get("candidate_sha") or ""),
            "manifest_digest": str(request.get("manifest_digest") or ""),
            "target_environment": str(request.get("target_environment") or ""),
            "action": str(request.get("action") or ""),
            "manifest_run_id": str(request.get("manifest_run_id") or ""),
        }
    )


def _task_index(status: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in status.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("id") or "").strip() == task_id:
            return task
    return None


def _open_task_blockers(status: dict[str, Any], task_id: str) -> bool:
    cleared = {"resolved", "closed", "done", "cancelled"}
    for blocker in status.get("blockers") or []:
        if not isinstance(blocker, dict):
            continue
        if str(blocker.get("task_id") or "").strip() != task_id:
            continue
        if str(blocker.get("status") or "open").strip().lower() not in cleared:
            return True
    return False


def request_errors(status: dict[str, Any], task: dict[str, Any], request: Any, *, now: datetime) -> list[str]:
    """Validate the explicit Human/Ops release-admission record fail closed."""

    task_id = str(task.get("id") or "").strip()
    if not isinstance(request, dict):
        return ["release_lease_request must be an object"]
    errors: list[str] = []
    if str(task.get("task_class") or "").strip() != "runtime_release":
        errors.append("task_class must be runtime_release")
    status_value = str(task.get("status") or "").strip().lower()
    if status_value != "in_progress":
        errors.append("release task status must be in_progress")
    if task.get("blocker") or task.get("blocked_by") or task.get("waiting_for") or _open_task_blockers(status, task_id):
        errors.append("release task is blocked or waiting for an unresolved prerequisite")
    if str(request.get("kind") or "").strip() != REQUEST_KIND:
        errors.append(f"release_lease_request.kind must be {REQUEST_KIND!r}")
    if str(request.get("status") or "").strip() != "approved":
        errors.append("release_lease_request.status must be approved")
    if str(request.get("task_id") or "").strip() != task_id:
        errors.append("release_lease_request.task_id must exactly match the task")
    if str(request.get("approved_by") or "").strip().casefold() != "human/ops":
        errors.append("release_lease_request.approved_by must be Human/Ops")
    if not str(request.get("approval_id") or "").strip():
        errors.append("release_lease_request.approval_id is required")
    nonce = str(request.get("nonce") or "").strip()
    if not nonce or len(nonce) > 256:
        errors.append("release_lease_request.nonce is required and must be at most 256 characters")
    candidate_sha = request.get("candidate_sha")
    if not isinstance(candidate_sha, str) or not SHA_PATTERN.fullmatch(candidate_sha):
        errors.append("release_lease_request.candidate_sha must be an exact 40-character lowercase SHA")
    manifest_digest = request.get("manifest_digest")
    if not isinstance(manifest_digest, str) or not SHA256_DIGEST_PATTERN.fullmatch(manifest_digest):
        errors.append("release_lease_request.manifest_digest must be a sha256:<64 lowercase hex> digest")
    if str(request.get("target_environment") or "") not in {"dev", "staging", "production"}:
        errors.append("release_lease_request.target_environment must be dev, staging, or production")
    if str(request.get("action") or "") != DEFAULT_ACTION:
        errors.append("release_lease_request.action must be deploy")
    if not _MANIFEST_RUN_ID.fullmatch(str(request.get("manifest_run_id") or "")):
        errors.append("release_lease_request.manifest_run_id must be a positive GitHub Actions run id")

    approved_at = parse_iso_timestamp(str(request.get("approved_at") or ""))
    expires_at = parse_iso_timestamp(str(request.get("expires_at") or ""))
    if approved_at is None:
        errors.append("release_lease_request.approved_at must be an ISO-8601 timestamp")
    if expires_at is None:
        errors.append("release_lease_request.expires_at must be an ISO-8601 timestamp")
    elif expires_at <= now:
        errors.append("release_lease_request has expired")
    elif approved_at is not None and expires_at <= approved_at:
        errors.append("release_lease_request.expires_at must be after approved_at")
    return errors


def _nonce_reuse_errors(
    status: dict[str, Any],
    task_id: str,
    fingerprint: str,
    nonce_digest: str | None,
    *,
    archive_dir: Path,
) -> list[str]:
    if not nonce_digest:
        return ["release_lease_request nonce is unavailable"]

    def reused(records: list[Any], record_task_id: str) -> bool:
        for record in records:
            if not isinstance(record, dict) or record.get("approval_nonce_digest") != nonce_digest:
                continue
            if record_task_id != task_id or record.get("request_fingerprint") != fingerprint:
                return True
        return False

    for task in status.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        records = [task.get(ISSUANCE_FIELD), *(task.get(ISSUANCE_HISTORY_FIELD) or [])]
        if reused(records, str(task.get("id") or "").strip()):
            return ["release_lease_request nonce was already used by a different issuance"]

    try:
        if not archive_dir.exists():
            return []
        if not archive_dir.is_dir():
            return ["release lease archive path is not a directory"]
        snapshots = sorted(archive_dir.glob("*.json"))
    except OSError:
        return ["release lease archive cannot be scanned"]
    for snapshot in snapshots:
        try:
            envelope = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ["release lease archive contains an unreadable task snapshot"]
        archived_task = envelope.get("task") if isinstance(envelope, dict) else None
        if not isinstance(archived_task, dict):
            return ["release lease archive contains a malformed task snapshot"]
        records = [
            archived_task.get(ISSUANCE_FIELD),
            *(archived_task.get(ISSUANCE_HISTORY_FIELD) or []),
        ]
        if reused(records, str(archived_task.get("id") or "").strip()):
            return ["release_lease_request nonce was already used by an archived issuance"]
    return []


def _archive_current_issuance(task: dict[str, Any]) -> None:
    current = task.get(ISSUANCE_FIELD)
    if not isinstance(current, dict):
        return
    history = task.get(ISSUANCE_HISTORY_FIELD)
    history = list(history) if isinstance(history, list) else []
    history.append(current)
    task[ISSUANCE_HISTORY_FIELD] = history[-20:]


def _receipt(
    lease: dict[str, Any] | None,
    *,
    errors: list[str],
    issued_at: datetime,
) -> dict[str, Any]:
    document = build_receipt(
        lease or {},
        errors=errors,
        admitted=not errors and lease is not None,
        verified_at=issued_at,
        verifier=ISSUER_NAME,
    )
    document["event"] = "lease_issued" if lease is not None and not errors else "lease_issue_blocked"
    return document


def _issuance_record(
    *,
    state: str,
    task_id: str,
    request: dict[str, Any],
    fingerprint: str,
    settings: dict[str, Any],
    receipt: dict[str, Any],
    updated_at: datetime,
) -> dict[str, Any]:
    """Build a durable, publishable record. Never add the lease document."""

    return {
        "schema_version": 1,
        "state": state,
        "task_id": task_id,
        "request_fingerprint": fingerprint,
        "approval_id": str(request.get("approval_id") or ""),
        "approval_nonce_digest": _safe_digest(request.get("nonce")),
        "candidate_sha": request.get("candidate_sha"),
        "manifest_digest": request.get("manifest_digest"),
        "target_environment": request.get("target_environment"),
        "manifest_run_id": request.get("manifest_run_id"),
        "secret_reference": settings["secret_reference"],
        "state_uri": settings["state_uri"],
        "workflow": RUNTIME_RELEASE_WORKFLOW_PATH,
        "updated_at": updated_at.replace(microsecond=0).isoformat(),
        "receipt": receipt,
    }


def _write_activity(
    config: dict[str, Any],
    event_type: str,
    *,
    task_id: str,
    record: dict[str, Any],
) -> None:
    receipt = record.get("receipt") if isinstance(record.get("receipt"), dict) else {}
    write_activity_log(
        config,
        {
            "type": event_type,
            "task_id": task_id,
            "issuance_state": record.get("state"),
            "request_fingerprint": record.get("request_fingerprint"),
            "approval_id": record.get("approval_id"),
            "approval_nonce_digest": record.get("approval_nonce_digest"),
            "candidate_sha": record.get("candidate_sha"),
            "manifest_digest": record.get("manifest_digest"),
            "target_environment": record.get("target_environment"),
            "lease_id": receipt.get("lease_id"),
            "signature_key_id": receipt.get("signature_key_id"),
            "errors": receipt.get("errors", []),
            "message": "Supervisor release lease decision recorded without key material or lease bearer data.",
        },
    )


def _commit_result(
    config: dict[str, Any],
    status: dict[str, Any],
    task: dict[str, Any],
    record: dict[str, Any],
    *,
    commit_status: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> bool:
    task[ISSUANCE_FIELD] = record
    return bool(commit_status(config, status))


def _read_release_inputs(root: Path, candidate_sha: str) -> tuple[Any, dict[str, Any] | None, list[str]]:
    registry_path = root / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
    manifest_path = root / "docs/evidence/gates/RELEASE_MANIFEST.json"
    errors: list[str] = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        registry = None
        errors.append("release gate registry is unavailable")
    manifest, manifest_errors = load_manifest(manifest_path, expected_candidate_sha=candidate_sha)
    errors.extend(manifest_errors)
    return registry, manifest, errors


def _exact_binding_errors(request: dict[str, Any], registry: Any, manifest: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    release = registry.get("release") if isinstance(registry, dict) else None
    if not isinstance(release, dict):
        return ["release gate registry has no release binding"]
    if release.get("candidate_sha") != request.get("candidate_sha"):
        errors.append("release request candidate_sha does not match the gate registry")
    if release.get("manifest_digest") != request.get("manifest_digest"):
        errors.append("release request manifest_digest does not match the gate registry")
    if not isinstance(manifest, dict):
        errors.append("release manifest is unavailable")
        return errors
    if manifest.get("candidate_sha") != request.get("candidate_sha"):
        errors.append("release request candidate_sha does not match the manifest")
    if manifest.get("manifest_digest") != request.get("manifest_digest"):
        errors.append("release request manifest_digest does not match the manifest")
    return errors


def _build_run_binding_errors(request: dict[str, Any], registry: Any) -> list[str]:
    """Require the deploy input to name the registry's exact build handoff."""

    candidate_rebind = registry.get("candidate_rebind") if isinstance(registry, dict) else None
    if not isinstance(candidate_rebind, dict):
        return ["release gate registry has no canonical candidate_rebind build binding"]
    if candidate_rebind.get("to_candidate_sha") != request.get("candidate_sha"):
        return ["candidate_rebind does not bind the requested candidate_sha"]
    if candidate_rebind.get("to_manifest_digest") != request.get("manifest_digest"):
        return ["candidate_rebind does not bind the requested manifest_digest"]
    build_run = candidate_rebind.get("build_run")
    if not isinstance(build_run, dict):
        return ["candidate_rebind has no canonical Runtime Release build run"]
    if str(build_run.get("run_id") or "") != str(request.get("manifest_run_id") or ""):
        return ["release_lease_request.manifest_run_id does not match candidate_rebind.build_run.run_id"]
    errors: list[str] = []
    if build_run.get("phase") != "build":
        errors.append("candidate_rebind.build_run must name the build phase")
    if build_run.get("conclusion") != "success":
        errors.append("candidate_rebind.build_run must be successful")
    if build_run.get("event") != "workflow_dispatch":
        errors.append("candidate_rebind.build_run must be a Runtime Release workflow_dispatch")
    return errors


def _runtime_release_inputs(lease: dict[str, Any], request: dict[str, Any], manifest: dict[str, Any]) -> dict[str, str]:
    components = manifest.get("components")
    if not isinstance(components, dict):
        raise RuntimeReleaseDispatchError("manifest components are unavailable")
    images: dict[str, str] = {}
    for component in ("api", "web", "worker", "scheduler"):
        value = (components.get(component) or {}).get("image") if isinstance(components.get(component), dict) else None
        if not isinstance(value, str) or "@sha256:" not in value:
            raise RuntimeReleaseDispatchError("manifest does not contain every immutable Runtime Release image")
        images[f"{component}_image"] = value
    encoded_lease = base64.b64encode(
        json.dumps(lease, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {
        "phase": "deploy",
        "environment": str(request["target_environment"]),
        "release_sha": str(lease["candidate_sha"]),
        "task_id": str(lease["task_id"]),
        "release_lease": encoded_lease,
        "manifest_run_id": str(request["manifest_run_id"]),
        "manifest_digest": str(lease["manifest_digest"]),
        **images,
    }


def dispatch_runtime_release(
    *,
    lease: dict[str, Any],
    request: dict[str, Any],
    manifest: dict[str, Any],
    settings: dict[str, Any],
) -> None:
    """Dispatch only the reviewed Runtime Release deploy phase via stdin.

    The base64 lease is a bearer credential. It is supplied to ``gh api`` via
    stdin so it never appears in a process argument, status file, activity log,
    receipt, or captured command output.
    """

    payload = {
        "ref": str(lease["candidate_sha"]),
        "inputs": _runtime_release_inputs(lease, request, manifest),
    }
    command = [
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{settings['github_repository']}/actions/workflows/{RUNTIME_RELEASE_WORKFLOW_ID}/dispatches",
        "--input",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeReleaseDispatchError("Runtime Release dispatch is not confirmed") from exc
    if result.returncode != 0:
        raise RuntimeReleaseDispatchError("Runtime Release dispatch is not confirmed")


def _status_still_reserved(
    config: dict[str, Any],
    *,
    task_id: str,
    fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    latest = load_status(config)
    task = _task_index(latest, task_id)
    if not isinstance(task, dict):
        return None
    request = task.get(REQUEST_FIELD)
    issuance = task.get(ISSUANCE_FIELD)
    if (
        not isinstance(request, dict)
        or request_fingerprint(task_id, request) != fingerprint
        or not isinstance(issuance, dict)
        or issuance.get("state") != "issuing"
        or issuance.get("request_fingerprint") != fingerprint
    ):
        return None
    return latest, task, request


def _record_blocked(
    config: dict[str, Any],
    status: dict[str, Any],
    task: dict[str, Any],
    request: dict[str, Any],
    fingerprint: str,
    settings: dict[str, Any],
    errors: list[str],
    *,
    commit_status: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> bool:
    record = _issuance_record(
        state="blocked",
        task_id=str(task["id"]),
        request=request,
        fingerprint=fingerprint,
        settings=settings,
        receipt=_receipt(None, errors=errors, issued_at=_utc()),
        updated_at=_utc(),
    )
    if not _commit_result(config, status, task, record, commit_status=commit_status):
        return False
    _write_activity(config, "release_lease_issue_blocked", task_id=str(task["id"]), record=record)
    return True


def process_release_lease_issuance(
    config: dict[str, Any],
    *,
    commit_status: Callable[[dict[str, Any], dict[str, Any]], bool],
    private_key_loader: Callable[[str], Any] = load_private_key_from_secret_reference,
    dispatch: Callable[..., None] = dispatch_runtime_release,
    now: datetime | None = None,
) -> bool:
    """Run release issuance within the existing Supervisor cycle.

    A failed or unknown step consumes no deploy capability. In particular, a
    lease that reached GCS but could not be recorded in the task board is never
    dispatched and is not retried automatically under the same approval nonce.
    """

    settings, settings_errors = issuer_settings(config)
    if settings is None:
        if settings_errors:
            # No task is selected without valid issuer settings, therefore no
            # secret lookup or deploy dispatch can occur.
            write_activity_log(
                config,
                {
                    "type": "release_lease_issuer_configuration_blocked",
                    "errors": settings_errors,
                    "message": "Release lease issuer configuration is invalid; no lease or deployment was attempted.",
                },
            )
        return False

    changed = False
    status = load_status(config)
    if not isinstance(status, dict):
        return False
    timestamp = _utc(now)
    root = config_path(config, "status_file").parent
    archive_dir = root / "ai-task-archive/tasks"

    for task in status.get("tasks") or []:
        if not isinstance(task, dict) or REQUEST_FIELD not in task:
            continue
        task_id = str(task.get("id") or "").strip()
        request = task.get(REQUEST_FIELD)
        if not task_id or not isinstance(request, dict):
            continue
        fingerprint = request_fingerprint(task_id, request)
        previous = task.get(ISSUANCE_FIELD)
        if isinstance(previous, dict) and previous.get("request_fingerprint") == fingerprint:
            if previous.get("state") in _FINAL_NO_RETRY_STATES | {"blocked"}:
                continue
            if previous.get("state") == "issuing":
                # A prior process could be between GCS CAS and task CAS. That
                # ambiguity is never retried automatically.
                continue

        errors = request_errors(status, task, request, now=timestamp)
        errors.extend(
            _nonce_reuse_errors(
                status, task_id, fingerprint, _safe_digest(request.get("nonce")), archive_dir=archive_dir
            )
        )
        if errors:
            changed = _record_blocked(
                config, status, task, request, fingerprint, settings, errors, commit_status=commit_status
            ) or changed
            continue

        # Reserve the exact approval before any secret or GCS access. A later
        # CAS conflict means the request may have changed, so it cannot issue.
        _archive_current_issuance(task)
        reservation = _issuance_record(
            state="issuing",
            task_id=task_id,
            request=request,
            fingerprint=fingerprint,
            settings=settings,
            receipt=_receipt(None, errors=[], issued_at=timestamp),
            updated_at=timestamp,
        )
        if not _commit_result(config, status, task, reservation, commit_status=commit_status):
            continue
        changed = True
        _write_activity(config, "release_lease_issuance_reserved", task_id=task_id, record=reservation)

        reserved = _status_still_reserved(config, task_id=task_id, fingerprint=fingerprint)
        if reserved is None:
            continue
        status, task, request = reserved
        registry, manifest, input_errors = _read_release_inputs(root, str(request.get("candidate_sha") or ""))
        errors = request_errors(status, task, request, now=_utc(now))
        errors.extend(input_errors)
        errors.extend(_exact_binding_errors(request, registry, manifest))
        errors.extend(_build_run_binding_errors(request, registry))
        errors.extend(
            _nonce_reuse_errors(
                status, task_id, fingerprint, _safe_digest(request.get("nonce")), archive_dir=archive_dir
            )
        )
        errors.extend(
            issuance_errors(
                status=status,
                registry=registry,
                manifest=manifest,
                manifest_errors=input_errors,
                task_id=task_id,
                target_environment=str(request.get("target_environment") or ""),
                release_sha=str(request.get("candidate_sha") or ""),
                archive_dir=archive_dir,
                root=root,
            )
        )
        if errors:
            changed = _record_blocked(
                config, status, task, request, fingerprint, settings, errors, commit_status=commit_status
            ) or changed
            continue

        try:
            state_store = LeaseStateStore(settings["state_uri"], require_existing=True)
        except LeaseError:
            changed = _record_blocked(
                config,
                status,
                task,
                request,
                fingerprint,
                settings,
                ["durable GCS lease state is unavailable"],
                commit_status=commit_status,
            ) or changed
            continue

        try:
            private_key = private_key_loader(settings["secret_reference"])
        except Exception:
            changed = _record_blocked(
                config,
                status,
                task,
                request,
                fingerprint,
                settings,
                ["Secret Manager signing key is unavailable"],
                commit_status=commit_status,
            ) or changed
            continue

        # Re-read every mutable authority after key acquisition. Gate, task,
        # manifest or SHA drift turns into a failure before any lease exists.
        reserved = _status_still_reserved(config, task_id=task_id, fingerprint=fingerprint)
        if reserved is None:
            del private_key
            continue
        status, task, request = reserved
        registry, manifest, input_errors = _read_release_inputs(root, str(request.get("candidate_sha") or ""))
        errors = request_errors(status, task, request, now=_utc(now))
        errors.extend(input_errors)
        errors.extend(_exact_binding_errors(request, registry, manifest))
        errors.extend(_build_run_binding_errors(request, registry))
        errors.extend(
            _nonce_reuse_errors(
                status, task_id, fingerprint, _safe_digest(request.get("nonce")), archive_dir=archive_dir
            )
        )
        errors.extend(
            issuance_errors(
                status=status,
                registry=registry,
                manifest=manifest,
                manifest_errors=input_errors,
                task_id=task_id,
                target_environment=str(request.get("target_environment") or ""),
                release_sha=str(request.get("candidate_sha") or ""),
                archive_dir=archive_dir,
                root=root,
            )
        )
        if errors:
            del private_key
            changed = _record_blocked(
                config, status, task, request, fingerprint, settings, errors, commit_status=commit_status
            ) or changed
            continue

        try:
            lease = issue_release_lease(
                task_id=task_id,
                target_environment=str(request["target_environment"]),
                status=status,
                registry=registry,
                manifest=manifest,
                manifest_errors=input_errors,
                private_key=private_key,
                state_store=state_store,
                allowed_action=DEFAULT_ACTION,
                ttl_seconds=settings["ttl_seconds"],
                release_sha=str(request["candidate_sha"]),
                archive_dir=archive_dir,
                issued_at=_utc(now),
                root=root,
            )
        except (LeaseError, ValueError):
            del private_key
            changed = _record_blocked(
                config,
                status,
                task,
                request,
                fingerprint,
                settings,
                ["lease issuance or durable GCS CAS failed"],
                commit_status=commit_status,
            ) or changed
            continue
        del private_key

        issued_record = _issuance_record(
            state="issued",
            task_id=task_id,
            request=request,
            fingerprint=fingerprint,
            settings=settings,
            receipt=_receipt(lease, errors=[], issued_at=_utc(now)),
            updated_at=_utc(now),
        )
        if not _commit_result(config, status, task, issued_record, commit_status=commit_status):
            # GCS has a credential but task CAS is uncertain. Do not dispatch or
            # reissue it: an operator must create a fresh approval after audit.
            continue
        _write_activity(config, "release_lease_issued", task_id=task_id, record=issued_record)

        try:
            if not isinstance(manifest, dict):
                raise RuntimeReleaseDispatchError("manifest is unavailable")
            dispatch(lease=lease, request=request, manifest=manifest, settings=settings)
        except Exception:
            dispatch_record = dict(issued_record)
            dispatch_record["state"] = "dispatch_unknown"
            dispatch_record["updated_at"] = _utc(now).replace(microsecond=0).isoformat()
            dispatch_record["dispatch"] = "not_confirmed"
            if _commit_result(config, status, task, dispatch_record, commit_status=commit_status):
                _write_activity(
                    config, "release_lease_dispatch_unknown", task_id=task_id, record=dispatch_record
                )
            continue

        dispatched_record = dict(issued_record)
        dispatched_record["state"] = "dispatched"
        dispatched_record["updated_at"] = _utc(now).replace(microsecond=0).isoformat()
        dispatched_record["dispatch"] = "accepted"
        if _commit_result(config, status, task, dispatched_record, commit_status=commit_status):
            _write_activity(
                config, "release_lease_runtime_release_dispatched", task_id=task_id, record=dispatched_record
            )

    return changed


__all__ = [
    "DEFAULT_SECRET_REFERENCE",
    "ISSUANCE_FIELD",
    "REQUEST_FIELD",
    "REQUEST_KIND",
    "RUNTIME_RELEASE_WORKFLOW_ID",
    "RUNTIME_RELEASE_WORKFLOW_PATH",
    "dispatch_runtime_release",
    "issuer_settings",
    "load_private_key_from_secret_reference",
    "process_release_lease_issuance",
    "request_errors",
    "request_fingerprint",
]
