#!/usr/bin/env python3
"""Supervisor-owned release lease issuer and durable state manager.

A release lease is the single authoritative credential that admits a
deployment workflow to act on a specific environment.  The Supervisor
issues a lease only when the task dependency graph and the corresponding
gate stage have been satisfied.

Design decisions (from EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN §6.2):

1. Lease binds: lease_id, task_id, release_id, candidate_sha,
   manifest_digest, target environment, allowed action, issued/expiry,
   nonce.
2. Signed with HMAC-SHA256 using a key held only by the Supervisor.
   In production this key comes from KMS or an equivalent non-exportable
   store; for offline / test use a file-based key is accepted.
3. Durable CAS (compare-and-set) state file transitions each lease from
   ``issued`` → ``consumed``, preventing replay.
4. Verification requires: valid signature, unexpired, correct target,
   correct SHA, correct manifest digest, and ``issued`` (not yet consumed)
   state.
5. Verification failure is always fail-closed; the receipt must not
   contain the signing key or any secret material.

This module does NOT handle GitHub environment approval or workflow
concurrency — those remain separate responsibilities.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]+$")
ENVIRONMENT_RE = re.compile(r"^(dev|staging|production)$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

LEASE_SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 3600  # 1 hour
MAX_TTL_SECONDS = 86400  # 24 hours

VALID_STATES = ("issued", "consumed", "revoked")


class LeaseError(Exception):
    """Base error for lease operations — always fail-closed."""


class LeaseSignatureError(LeaseError):
    """Signature verification failed."""


class LeaseExpiredError(LeaseError):
    """Lease has passed its expiry time."""


class LeaseStateError(LeaseError):
    """CAS state transition is invalid (replay, already consumed, etc.)."""


class LeaseValidationError(LeaseError):
    """Lease field validation failed."""


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------

def _canonical_payload(lease: dict[str, Any]) -> bytes:
    """Produce the canonical byte representation for signing.

    The ``signature`` field is excluded so the hash does not depend on
    itself.  Fields are sorted for determinism.
    """
    payload = {k: v for k, v in lease.items() if k != "signature"}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_lease(lease: dict[str, Any], *, key: bytes) -> str:
    """Return the HMAC-SHA256 hex signature for *lease*."""
    return hmac.new(key, _canonical_payload(lease), hashlib.sha256).hexdigest()


def verify_signature(lease: dict[str, Any], *, key: bytes) -> bool:
    """Return whether the lease signature is valid."""
    recorded = lease.get("signature")
    if not isinstance(recorded, str):
        return False
    expected = sign_lease(lease, key=key)
    return hmac.compare_digest(recorded, expected)


# ---------------------------------------------------------------------------
# Lease issuance
# ---------------------------------------------------------------------------

def issue_lease(
    *,
    task_id: str,
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    target_environment: str,
    allowed_action: str,
    key: bytes,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    """Issue a new signed release lease.

    Returns the lease dict including the ``signature`` field.
    Raises ``LeaseValidationError`` if any input is malformed.
    """
    errors = _validate_issuance_inputs(
        task_id=task_id,
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        target_environment=target_environment,
        allowed_action=allowed_action,
        ttl_seconds=ttl_seconds,
    )
    if errors:
        raise LeaseValidationError(
            "lease issuance blocked:\n- " + "\n- ".join(errors)
        )

    now = issued_at or datetime.now(UTC)
    nonce = secrets.token_hex(16)
    lease_id = f"lease-{nonce}"
    expiry = now + timedelta(seconds=ttl_seconds)

    lease: dict[str, Any] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "lease_id": lease_id,
        "task_id": task_id,
        "release_id": release_id,
        "candidate_sha": candidate_sha,
        "manifest_digest": manifest_digest,
        "target_environment": target_environment,
        "allowed_action": allowed_action,
        "issued_at": now.isoformat(),
        "expires_at": expiry.isoformat(),
        "nonce": nonce,
    }
    lease["signature"] = sign_lease(lease, key=key)
    return lease


def _validate_issuance_inputs(
    *,
    task_id: str,
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    target_environment: str,
    allowed_action: str,
    ttl_seconds: int,
) -> list[str]:
    errors: list[str] = []
    if not TASK_ID_RE.fullmatch(task_id):
        errors.append(f"task_id {task_id!r} is not a valid identifier")
    if not RELEASE_ID_RE.fullmatch(release_id):
        errors.append(f"release_id {release_id!r} is not a valid release identifier")
    if not SHA_RE.fullmatch(candidate_sha):
        errors.append("candidate_sha must be a 40-character lowercase hex git SHA")
    if not SHA256_DIGEST_RE.fullmatch(manifest_digest):
        errors.append("manifest_digest must be sha256:<64 hex chars>")
    if not ENVIRONMENT_RE.fullmatch(target_environment):
        errors.append(f"target_environment {target_environment!r} must be dev, staging, or production")
    if not ACTION_RE.fullmatch(allowed_action):
        errors.append(f"allowed_action {allowed_action!r} is not a valid action identifier")
    if not isinstance(ttl_seconds, int) or ttl_seconds < 1:
        errors.append("ttl_seconds must be a positive integer")
    elif ttl_seconds > MAX_TTL_SECONDS:
        errors.append(f"ttl_seconds {ttl_seconds} exceeds maximum {MAX_TTL_SECONDS}")
    return errors


# ---------------------------------------------------------------------------
# Durable CAS state
# ---------------------------------------------------------------------------

class LeaseStateStore:
    """File-backed compare-and-set store for lease state transitions.

    Each lease is stored as ``<lease_id>.json`` inside *state_dir*.
    State transitions are atomic via write-to-temp + rename.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, lease_id: str) -> Path:
        safe_id = lease_id.replace("/", "_").replace("..", "_")
        return self._dir / f"{safe_id}.json"

    def record_issued(self, lease: dict[str, Any]) -> None:
        """Persist a newly issued lease in ``issued`` state."""
        lease_id = lease["lease_id"]
        path = self._path(lease_id)
        if path.exists():
            raise LeaseStateError(f"lease {lease_id} already exists in state store")
        record = {
            "lease_id": lease_id,
            "state": "issued",
            "lease": lease,
            "issued_at": lease["issued_at"],
            "consumed_at": None,
            "revoked_at": None,
        }
        self._atomic_write(path, record)

    def consume(self, lease_id: str) -> dict[str, Any]:
        """CAS transition: ``issued`` → ``consumed``.

        Returns the updated record.
        Raises ``LeaseStateError`` if the lease is not in ``issued`` state.
        """
        return self._transition(lease_id, from_state="issued", to_state="consumed")

    def revoke(self, lease_id: str) -> dict[str, Any]:
        """CAS transition: ``issued`` → ``revoked``.

        Returns the updated record.
        """
        return self._transition(lease_id, from_state="issued", to_state="revoked")

    def get(self, lease_id: str) -> dict[str, Any] | None:
        """Read the current record for *lease_id*, or ``None``."""
        path = self._path(lease_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _transition(
        self,
        lease_id: str,
        *,
        from_state: str,
        to_state: str,
    ) -> dict[str, Any]:
        path = self._path(lease_id)
        if not path.exists():
            raise LeaseStateError(f"lease {lease_id} not found in state store")
        record = json.loads(path.read_text(encoding="utf-8"))
        current = record.get("state")
        if current != from_state:
            raise LeaseStateError(
                f"lease {lease_id} state is {current!r}, expected {from_state!r}; "
                f"cannot transition to {to_state!r}"
            )
        record["state"] = to_state
        ts_field = f"{to_state}_at"
        record[ts_field] = datetime.now(UTC).replace(microsecond=0).isoformat()
        self._atomic_write(path, record)
        return record

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.rename(path)


# ---------------------------------------------------------------------------
# Lease verification (the authoritative admission decision)
# ---------------------------------------------------------------------------

def verify_lease(
    lease: dict[str, Any],
    *,
    key: bytes,
    state_store: LeaseStateStore,
    expected_sha: str | None = None,
    expected_manifest_digest: str | None = None,
    expected_environment: str | None = None,
    expected_action: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return all lease verification errors; empty list means admitted.

    This is the single authoritative verifier that replaces shape-only
    admission.  Every check is mandatory; omitting a parameter means
    that dimension is not cross-checked against an external expectation
    (the lease's own fields are still validated).
    """
    errors: list[str] = []
    check_time = now or datetime.now(UTC)

    # --- schema version ---
    if lease.get("schema_version") != LEASE_SCHEMA_VERSION:
        errors.append(
            f"lease schema_version must be {LEASE_SCHEMA_VERSION}, "
            f"got {lease.get('schema_version')!r}"
        )

    # --- structural field presence ---
    required = (
        "lease_id", "task_id", "release_id", "candidate_sha",
        "manifest_digest", "target_environment", "allowed_action",
        "issued_at", "expires_at", "nonce", "signature",
    )
    for field in required:
        if field not in lease:
            errors.append(f"lease missing required field: {field}")
    if errors:
        return errors  # can't proceed without structural fields

    # --- signature ---
    if not verify_signature(lease, key=key):
        errors.append("lease signature verification failed")
        return errors  # no point checking content of a tampered lease

    # --- expiry ---
    try:
        expiry = datetime.fromisoformat(
            lease["expires_at"].replace("Z", "+00:00")
        )
    except (ValueError, AttributeError):
        errors.append("lease expires_at is not a valid ISO timestamp")
        expiry = None
    if expiry and check_time > expiry:
        errors.append(
            f"lease expired at {lease['expires_at']}; current time is "
            f"{check_time.isoformat()}"
        )

    # --- field format ---
    if not SHA_RE.fullmatch(lease.get("candidate_sha", "")):
        errors.append("lease candidate_sha is not a valid 40-char hex SHA")
    if not SHA256_DIGEST_RE.fullmatch(lease.get("manifest_digest", "")):
        errors.append("lease manifest_digest is not a valid sha256 digest")
    if not ENVIRONMENT_RE.fullmatch(lease.get("target_environment", "")):
        errors.append("lease target_environment is not a valid environment")

    # --- cross-checks against caller expectations ---
    if expected_sha and lease.get("candidate_sha") != expected_sha:
        errors.append(
            f"lease candidate_sha {lease.get('candidate_sha')!r} does not "
            f"match expected {expected_sha!r}"
        )
    if expected_manifest_digest and lease.get("manifest_digest") != expected_manifest_digest:
        errors.append(
            f"lease manifest_digest does not match expected digest"
        )
    if expected_environment and lease.get("target_environment") != expected_environment:
        errors.append(
            f"lease target_environment {lease.get('target_environment')!r} "
            f"does not match expected {expected_environment!r}"
        )
    if expected_action and lease.get("allowed_action") != expected_action:
        errors.append(
            f"lease allowed_action {lease.get('allowed_action')!r} "
            f"does not match expected {expected_action!r}"
        )

    # --- CAS state: must be 'issued' and not yet consumed/revoked ---
    lease_id = lease["lease_id"]
    record = state_store.get(lease_id)
    if record is None:
        errors.append(f"lease {lease_id} not found in state store")
    elif record.get("state") != "issued":
        errors.append(
            f"lease {lease_id} state is {record.get('state')!r}, "
            f"expected 'issued' (possible replay)"
        )

    return errors


def admit_and_consume(
    lease: dict[str, Any],
    *,
    key: bytes,
    state_store: LeaseStateStore,
    expected_sha: str | None = None,
    expected_manifest_digest: str | None = None,
    expected_environment: str | None = None,
    expected_action: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    """Verify lease and atomically consume it if valid.

    Returns ``(admitted, errors, receipt)``.
    - ``admitted`` is True only when the lease passed all checks and was
      successfully consumed.
    - ``receipt`` is a safe-to-log dict that never contains the signing key.
    """
    errors = verify_lease(
        lease,
        key=key,
        state_store=state_store,
        expected_sha=expected_sha,
        expected_manifest_digest=expected_manifest_digest,
        expected_environment=expected_environment,
        expected_action=expected_action,
        now=now,
    )

    receipt: dict[str, Any] = {
        "lease_id": lease.get("lease_id", "unknown"),
        "task_id": lease.get("task_id", "unknown"),
        "release_id": lease.get("release_id", "unknown"),
        "target_environment": lease.get("target_environment", "unknown"),
        "allowed_action": lease.get("allowed_action", "unknown"),
        "candidate_sha": lease.get("candidate_sha", "unknown"),
        "admitted": False,
        "errors": errors,
        "verified_at": (now or datetime.now(UTC)).isoformat(),
    }

    if errors:
        return False, errors, receipt

    # Atomic consume — if this fails the lease stays issued
    try:
        state_store.consume(lease["lease_id"])
    except LeaseStateError as exc:
        errors.append(str(exc))
        receipt["errors"] = errors
        return False, errors, receipt

    receipt["admitted"] = True
    receipt["consumed_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
    return True, [], receipt


# ---------------------------------------------------------------------------
# Convenience: load key from file or env
# ---------------------------------------------------------------------------

def load_signing_key(
    *,
    key_path: Path | None = None,
    env_var: str = "RELEASE_LEASE_SIGNING_KEY",
) -> bytes:
    """Load the HMAC signing key from a file or environment variable.

    In production the key should come from KMS; this helper is for
    file-based or env-based setups (CI, tests, bootstrap).
    """
    if key_path and key_path.exists():
        return key_path.read_bytes().strip()
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return raw.encode("utf-8")
    raise LeaseError(
        f"no signing key: provide --key-path or set {env_var}"
    )


__all__ = [
    "LEASE_SCHEMA_VERSION",
    "LeaseError",
    "LeaseExpiredError",
    "LeaseSignatureError",
    "LeaseStateError",
    "LeaseStateStore",
    "LeaseValidationError",
    "admit_and_consume",
    "issue_lease",
    "load_signing_key",
    "sign_lease",
    "verify_lease",
    "verify_signature",
]
