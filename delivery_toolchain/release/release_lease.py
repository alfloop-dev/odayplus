#!/usr/bin/env python3
"""Signed, durable Supervisor release lease: schema, signature, and CAS state.

A release lease is the single credential that admits a deployment workflow to
act on one environment for one release candidate.  It replaces the shape-only
`task_id` / `release_lease` string check, which any actor with workflow write
access could satisfy by inventing a value.

The lease is deliberately asymmetric.  The Supervisor holds an Ed25519 private
key (KMS or an equivalent non-exportable store in production; a PEM file for
offline and test use) and is the only party that can mint a lease.  A workflow
receives the public key only, so possessing the verifier grants the ability to
check a lease and never the ability to issue one.  A shared HMAC secret would
have given every verifier issuing power, which is the property this control
exists to remove.

Replay is prevented by durable compare-and-set state, not by the document.  A
lease is recorded `issued` at mint time and can transition to `consumed` or
`revoked` exactly once; a second presentation of the same signed bytes finds a
non-`issued` record and fails closed.  The state store is Supervisor-owned
durable state: a verifier that cannot reach it cannot admit, because a lease
consumed against a runner-local directory is not consumed at all.

Everything fails closed.  `verify_lease` returns the complete error list rather
than raising on the first problem, so a blocked release reports every reason at
once.  Receipts are safe to publish: they carry digests of the nonce and
signature, never the values, and never key material.

Scope: this module is the Supervisor authorisation control.  GitHub environment
approval (the human production gate) and workflow `concurrency` (same-environment
mutual exclusion) remain separate responsibilities and are not replaced here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

LEASE_SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"

DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 86400

# The Supervisor and the runner verifying a lease are different machines with
# independently drifting clocks. A runner whose clock is a few seconds behind
# would otherwise reject a lease that was genuinely issued a moment ago, which
# is an availability failure and not a security one. The allowance is applied
# only to the not-before edge; expiry stays strict, because that is the edge
# where being generous would extend a credential's life.
NOT_BEFORE_SKEW_SECONDS = 60

STATE_ISSUED = "issued"
STATE_CONSUMED = "consumed"
STATE_REVOKED = "revoked"
LEASE_STATES = (STATE_ISSUED, STATE_CONSUMED, STATE_REVOKED)

TARGET_ENVIRONMENTS = ("dev", "staging", "production")
DEFAULT_ACTION = "deploy"

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
LEASE_ID_PATTERN = re.compile(r"^lease-[0-9a-f]{32}$")
NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
HEX_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# The bound identity of a lease. Every field is signed; none may be supplied by
# the verifier at admission time.
LEASE_FIELDS = (
    "schema_version",
    "lease_id",
    "task_id",
    "release_id",
    "candidate_sha",
    "manifest_digest",
    "target_environment",
    "allowed_action",
    "issued_at",
    "expires_at",
    "nonce",
)

PRIVATE_KEY_ENV = "ODP_RELEASE_LEASE_PRIVATE_KEY"
PUBLIC_KEY_ENV = "ODP_RELEASE_LEASE_PUBLIC_KEY"


class LeaseError(Exception):
    """Base error for lease operations. Every subclass is a fail-closed stop."""


class LeaseStateError(LeaseError):
    """A durable CAS transition was refused (replay, missing record, mismatch)."""


class LeaseKeyError(LeaseError):
    """Signing or verification key material is missing or unusable."""


class LeaseIssuanceError(LeaseError):
    """Issuance preconditions were not satisfied; the lease was not minted."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("release lease issuance blocked:\n- " + "\n- ".join(self.errors))


# ---------------------------------------------------------------------------
# Canonical bytes and signatures
# ---------------------------------------------------------------------------


def canonical_payload(lease: dict[str, Any]) -> dict[str, Any]:
    """Return the signed payload: the bound fields, and nothing else.

    Building the payload from an explicit allow-list rather than by deleting
    `signature` means an attacker cannot smuggle an unsigned field into a lease
    and have it survive verification as though it had been attested.
    """

    return {field: lease[field] for field in LEASE_FIELDS if field in lease}


def canonical_bytes(lease: dict[str, Any]) -> bytes:
    return json.dumps(
        canonical_payload(lease),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def public_key_id(public_key: Ed25519PublicKey) -> str:
    """Return a stable, non-secret identifier for a verification key."""

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"{SIGNATURE_ALGORITHM}:{hashlib.sha256(raw).hexdigest()[:32]}"


def sign_lease(lease: dict[str, Any], *, private_key: Ed25519PrivateKey) -> dict[str, str]:
    """Return the detached signature block for *lease*."""

    signature = private_key.sign(canonical_bytes(lease))
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": public_key_id(private_key.public_key()),
        "value": signature.hex(),
    }


def signature_errors(lease: dict[str, Any], *, public_key: Ed25519PublicKey) -> list[str]:
    """Return why the lease signature is not valid, or an empty list."""

    block = lease.get("signature")
    if not isinstance(block, dict):
        return ["lease.signature must be an object with algorithm, key_id, and value"]

    errors: list[str] = []
    algorithm = block.get("algorithm")
    if algorithm != SIGNATURE_ALGORITHM:
        errors.append(
            f"lease.signature.algorithm must be {SIGNATURE_ALGORITHM!r}, got: {algorithm!r}"
        )

    expected_key_id = public_key_id(public_key)
    if block.get("key_id") != expected_key_id:
        errors.append(
            f"lease.signature.key_id {block.get('key_id')!r} was not issued by the "
            f"configured verification key {expected_key_id!r}"
        )

    value = block.get("value")
    if not isinstance(value, str):
        errors.append("lease.signature.value must be a hex string")
        return errors
    try:
        raw_signature = bytes.fromhex(value)
    except ValueError:
        errors.append("lease.signature.value is not valid hex")
        return errors

    if errors:
        # A signature made by a different algorithm or key cannot be verified
        # with this key; reporting the mismatch is the whole answer.
        return errors

    try:
        public_key.verify(raw_signature, canonical_bytes(lease))
    except InvalidSignature:
        errors.append("lease signature does not verify against the configured public key")
    return errors


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------


def load_private_key(
    *, key_path: Path | None = None, env_var: str = PRIVATE_KEY_ENV
) -> Ed25519PrivateKey:
    """Load the Supervisor signing key from a PEM file, PEM env var, or hex seed.

    Production should back this with KMS. The file and environment paths exist
    for offline issuance and tests, and are still private-key-only: no verifier
    is ever given this material.
    """

    material = _read_key_material(key_path=key_path, env_var=env_var, kind="private")
    if HEX_KEY_PATTERN.fullmatch(material.decode("utf-8", "ignore").strip()):
        return Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(material.decode("utf-8").strip())
        )
    try:
        key = serialization.load_pem_private_key(material, password=None)
    except (TypeError, ValueError) as exc:
        raise LeaseKeyError(f"release lease private key is not a usable PEM key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise LeaseKeyError("release lease private key must be an Ed25519 key")
    return key


def load_public_key(
    *, key_path: Path | None = None, env_var: str = PUBLIC_KEY_ENV
) -> Ed25519PublicKey:
    """Load the verification key from a PEM file, PEM env var, or raw hex."""

    material = _read_key_material(key_path=key_path, env_var=env_var, kind="public")
    text = material.decode("utf-8", "ignore").strip()
    if HEX_KEY_PATTERN.fullmatch(text):
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(text))
    try:
        key = serialization.load_pem_public_key(material)
    except (TypeError, ValueError) as exc:
        raise LeaseKeyError(f"release lease public key is not a usable PEM key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise LeaseKeyError("release lease public key must be an Ed25519 key")
    return key


def _read_key_material(*, key_path: Path | None, env_var: str, kind: str) -> bytes:
    if key_path is not None:
        try:
            return key_path.read_bytes()
        except OSError as exc:
            raise LeaseKeyError(f"cannot read release lease {kind} key {key_path}: {exc}") from exc
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return raw.encode("utf-8")
    raise LeaseKeyError(
        f"no release lease {kind} key: pass an explicit key path or set {env_var}"
    )


def generate_keypair() -> tuple[bytes, bytes]:
    """Return a `(private_pem, public_pem)` Ed25519 pair for bootstrap and tests."""

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# Durable compare-and-set state
# ---------------------------------------------------------------------------


class LeaseStateStore:
    """Durable Supervisor-owned CAS store for lease lifecycle state.

    One JSON record per lease. Issuance uses `O_CREAT | O_EXCL` so a lease id
    can never be minted twice, and transitions hold an exclusive `flock` on the
    record for the whole read-check-write, so two workflows presenting the same
    lease concurrently cannot both observe `issued`.

    The store must be Supervisor-durable. `require_existing=True` refuses to
    create the directory, which is what a verifier wants: an admission run that
    silently creates an empty state directory on an ephemeral runner would
    "consume" a lease that stays `issued` everywhere that matters.
    """

    def __init__(self, state_dir: Path, *, require_existing: bool = False) -> None:
        self._dir = Path(state_dir)
        if require_existing:
            if not self._dir.is_dir():
                raise LeaseStateError(
                    f"durable lease state directory does not exist: {self._dir}. "
                    "Admission requires the Supervisor's durable state; refusing to "
                    "create a throwaway store."
                )
        else:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, lease_id: str) -> Path:
        if not LEASE_ID_PATTERN.fullmatch(lease_id):
            raise LeaseStateError(f"lease_id {lease_id!r} is not a valid lease identifier")
        return self._dir / f"{lease_id}.json"

    def get(self, lease_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(lease_id)
        except LeaseStateError:
            return None
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LeaseStateError(f"lease state record {path} is unreadable: {exc}") from exc

    def record_issued(self, lease: dict[str, Any], *, issued_by: str = "supervisor") -> dict[str, Any]:
        """Persist a freshly minted lease. Fails closed if the id already exists."""

        path = self._path(lease["lease_id"])
        record = {
            "lease_id": lease["lease_id"],
            "state": STATE_ISSUED,
            "issued_at": lease["issued_at"],
            "issued_by": issued_by,
            "expires_at": lease["expires_at"],
            "consumed_at": None,
            "consumed_by": None,
            "revoked_at": None,
            "revoked_reason": None,
            "lease": lease,
        }
        payload = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LeaseStateError(
                f"lease {lease['lease_id']} already exists in the durable state store"
            ) from exc
        except OSError as exc:
            raise LeaseStateError(f"cannot record lease state at {path}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LeaseStateError(f"cannot record lease state at {path}: {exc}") from exc
        return record

    def consume(self, lease: dict[str, Any], *, consumed_by: str) -> dict[str, Any]:
        """CAS `issued` -> `consumed`, binding the presented lease to the record."""

        return self._transition(
            lease,
            to_state=STATE_CONSUMED,
            actor_field="consumed_by",
            actor=consumed_by,
        )

    def revoke(self, lease: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """CAS `issued` -> `revoked`. A revoked lease can never be consumed."""

        return self._transition(
            lease,
            to_state=STATE_REVOKED,
            actor_field="revoked_reason",
            actor=reason,
        )

    def _transition(
        self,
        lease: dict[str, Any],
        *,
        to_state: str,
        actor_field: str,
        actor: str,
    ) -> dict[str, Any]:
        lease_id = lease.get("lease_id")
        if not isinstance(lease_id, str):
            raise LeaseStateError("lease is missing lease_id; cannot transition state")
        path = self._path(lease_id)
        if not path.exists():
            raise LeaseStateError(f"lease {lease_id} is not present in the durable state store")

        with open(path, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    record = json.loads(handle.read())
                except json.JSONDecodeError as exc:
                    raise LeaseStateError(f"lease state record {path} is unreadable: {exc}") from exc

                current = record.get("state")
                if current != STATE_ISSUED:
                    raise LeaseStateError(
                        f"lease {lease_id} is {current!r}, not {STATE_ISSUED!r}; "
                        f"refusing to mark it {to_state!r} (replay)"
                    )
                stored = record.get("lease")
                if not isinstance(stored, dict) or canonical_bytes(stored) != canonical_bytes(lease):
                    raise LeaseStateError(
                        f"lease {lease_id} does not match the lease recorded at issuance"
                    )

                record["state"] = to_state
                record[f"{to_state}_at"] = _utc_now().isoformat()
                record[actor_field] = actor
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return record


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------


def build_lease(
    *,
    task_id: str,
    release_id: str,
    candidate_sha: str,
    manifest_digest: str,
    target_environment: str,
    allowed_action: str = DEFAULT_ACTION,
    private_key: Ed25519PrivateKey,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    issued_at: datetime | None = None,
) -> dict[str, Any]:
    """Mint one signed lease. Field validation only; no authorisation policy.

    Callers must not use this directly to admit a release: the Supervisor
    issuer in `.orchestrator/release_lease.py` adds the dependency and gate
    preconditions that make a lease an authorisation rather than a document.
    """

    errors = _issuance_field_errors(
        task_id=task_id,
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        target_environment=target_environment,
        allowed_action=allowed_action,
        ttl_seconds=ttl_seconds,
    )
    if errors:
        raise LeaseIssuanceError(errors)

    now = (issued_at or _utc_now()).astimezone(UTC).replace(microsecond=0)
    nonce = secrets.token_hex(16)
    lease: dict[str, Any] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "lease_id": f"lease-{secrets.token_hex(16)}",
        "task_id": task_id,
        "release_id": release_id,
        "candidate_sha": candidate_sha,
        "manifest_digest": manifest_digest,
        "target_environment": target_environment,
        "allowed_action": allowed_action,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "nonce": nonce,
    }
    lease["signature"] = sign_lease(lease, private_key=private_key)
    return lease


def _issuance_field_errors(
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
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        errors.append(f"task_id {task_id!r} is not a valid Supervisor task identifier")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        errors.append(f"release_id {release_id!r} is not a valid release identifier")
    if not isinstance(candidate_sha, str) or not SHA_PATTERN.fullmatch(candidate_sha):
        errors.append("candidate_sha must be an exact 40-character lowercase git SHA")
    if not isinstance(manifest_digest, str) or not SHA256_DIGEST_PATTERN.fullmatch(manifest_digest):
        errors.append("manifest_digest must be a sha256:<64 lowercase hex> digest")
    if target_environment not in TARGET_ENVIRONMENTS:
        errors.append(
            f"target_environment {target_environment!r} must be one of {list(TARGET_ENVIRONMENTS)}"
        )
    if not isinstance(allowed_action, str) or not ACTION_PATTERN.fullmatch(allowed_action):
        errors.append(f"allowed_action {allowed_action!r} is not a valid action identifier")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
        errors.append("ttl_seconds must be a positive integer")
    elif ttl_seconds > MAX_TTL_SECONDS:
        errors.append(f"ttl_seconds {ttl_seconds} exceeds the maximum {MAX_TTL_SECONDS}")
    return errors


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_lease(
    lease: Any,
    *,
    public_key: Ed25519PublicKey,
    state_store: LeaseStateStore,
    expected_task_id: str | None = None,
    expected_candidate_sha: str | None = None,
    expected_manifest_digest: str | None = None,
    expected_environment: str | None = None,
    expected_action: str | None = DEFAULT_ACTION,
    now: datetime | None = None,
) -> list[str]:
    """Return every reason the lease is not admissible; empty means admissible.

    This is the authoritative admission predicate. It checks signature, expiry,
    target environment, candidate SHA, manifest digest, allowed action, and the
    durable `issued` state together, because any one of them alone is bypassable.
    """

    if not isinstance(lease, dict):
        return ["lease must be a JSON object"]

    errors: list[str] = []
    check_time = (now or _utc_now()).astimezone(UTC)

    if lease.get("schema_version") != LEASE_SCHEMA_VERSION:
        errors.append(
            f"lease.schema_version must be {LEASE_SCHEMA_VERSION}, "
            f"got: {lease.get('schema_version')!r}"
        )

    missing = [field for field in (*LEASE_FIELDS, "signature") if field not in lease]
    if missing:
        # Signature verification over a partial payload would be meaningless.
        return errors + [f"lease missing required field: {field}" for field in missing]

    signature_problems = signature_errors(lease, public_key=public_key)
    if signature_problems:
        # A lease whose signature does not verify has no attested content; the
        # remaining fields are attacker-controlled and must not be reported as
        # if they were facts about a real lease.
        return errors + signature_problems

    errors.extend(_field_format_errors(lease))
    errors.extend(_validity_window_errors(lease, check_time))
    errors.extend(
        _binding_errors(
            lease,
            expected_task_id=expected_task_id,
            expected_candidate_sha=expected_candidate_sha,
            expected_manifest_digest=expected_manifest_digest,
            expected_environment=expected_environment,
            expected_action=expected_action,
        )
    )
    errors.extend(_state_errors(lease, state_store))
    return errors


def _field_format_errors(lease: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not LEASE_ID_PATTERN.fullmatch(str(lease.get("lease_id"))):
        errors.append("lease.lease_id must look like lease-<32 lowercase hex>")
    if not NONCE_PATTERN.fullmatch(str(lease.get("nonce"))):
        errors.append("lease.nonce must be 32 lowercase hex characters")
    if not TASK_ID_PATTERN.fullmatch(str(lease.get("task_id"))):
        errors.append("lease.task_id is not a valid Supervisor task identifier")
    if not RELEASE_ID_PATTERN.fullmatch(str(lease.get("release_id"))):
        errors.append("lease.release_id is not a valid release identifier")
    if not SHA_PATTERN.fullmatch(str(lease.get("candidate_sha"))):
        errors.append("lease.candidate_sha must be an exact 40-character lowercase git SHA")
    if not SHA256_DIGEST_PATTERN.fullmatch(str(lease.get("manifest_digest"))):
        errors.append("lease.manifest_digest must be a sha256:<64 lowercase hex> digest")
    if lease.get("target_environment") not in TARGET_ENVIRONMENTS:
        errors.append(
            f"lease.target_environment must be one of {list(TARGET_ENVIRONMENTS)}, "
            f"got: {lease.get('target_environment')!r}"
        )
    if not ACTION_PATTERN.fullmatch(str(lease.get("allowed_action"))):
        errors.append("lease.allowed_action is not a valid action identifier")
    return errors


def _validity_window_errors(lease: dict[str, Any], check_time: datetime) -> list[str]:
    errors: list[str] = []
    issued_at = _parse_timestamp(lease.get("issued_at"))
    expires_at = _parse_timestamp(lease.get("expires_at"))
    if issued_at is None:
        errors.append("lease.issued_at must be an RFC3339 timestamp with a timezone")
    if expires_at is None:
        errors.append("lease.expires_at must be an RFC3339 timestamp with a timezone")
    if issued_at is None or expires_at is None:
        return errors

    if expires_at <= issued_at:
        errors.append("lease.expires_at must be after lease.issued_at")
    elif expires_at - issued_at > timedelta(seconds=MAX_TTL_SECONDS):
        errors.append(
            f"lease validity window exceeds the maximum {MAX_TTL_SECONDS} seconds"
        )
    if check_time < issued_at - timedelta(seconds=NOT_BEFORE_SKEW_SECONDS):
        errors.append(
            f"lease is not valid until {lease['issued_at']}; current time is "
            f"{check_time.isoformat()}"
        )
    if check_time > expires_at:
        errors.append(
            f"lease expired at {lease['expires_at']}; current time is {check_time.isoformat()}"
        )
    return errors


def _binding_errors(
    lease: dict[str, Any],
    *,
    expected_task_id: str | None,
    expected_candidate_sha: str | None,
    expected_manifest_digest: str | None,
    expected_environment: str | None,
    expected_action: str | None,
) -> list[str]:
    errors: list[str] = []
    expectations = (
        ("task_id", expected_task_id),
        ("candidate_sha", expected_candidate_sha),
        ("manifest_digest", expected_manifest_digest),
        ("target_environment", expected_environment),
        ("allowed_action", expected_action),
    )
    for field, expected in expectations:
        if expected is None:
            continue
        actual = lease.get(field)
        if actual != expected:
            errors.append(
                f"lease.{field} {actual!r} does not match the requested {expected!r}"
            )
    return errors


def _state_errors(lease: dict[str, Any], state_store: LeaseStateStore) -> list[str]:
    lease_id = str(lease.get("lease_id"))
    try:
        record = state_store.get(lease_id)
    except LeaseStateError as exc:
        return [str(exc)]

    if record is None:
        return [
            f"lease {lease_id} has no record in the durable Supervisor state store; "
            "a lease the Supervisor never issued is not an authorisation"
        ]
    state = record.get("state")
    if state != STATE_ISSUED:
        return [
            f"lease {lease_id} is {state!r} in durable state, expected {STATE_ISSUED!r} "
            "(already used, or revoked)"
        ]
    stored = record.get("lease")
    if not isinstance(stored, dict) or canonical_bytes(stored) != canonical_bytes(lease):
        return [
            f"lease {lease_id} does not match the lease the Supervisor recorded at issuance"
        ]
    return []


# ---------------------------------------------------------------------------
# Admission and receipts
# ---------------------------------------------------------------------------


def build_receipt(
    lease: Any,
    *,
    errors: list[str],
    admitted: bool,
    verified_at: datetime,
    verifier: str,
    consumed_at: str | None = None,
) -> dict[str, Any]:
    """Return an audit receipt that is safe to publish.

    The nonce and signature are the bearer parts of the credential, so the
    receipt carries their digests instead of their values. No key material is
    ever included.
    """

    document = lease if isinstance(lease, dict) else {}
    signature = document.get("signature")
    signature_value = signature.get("value") if isinstance(signature, dict) else None
    return {
        "schema_version": LEASE_SCHEMA_VERSION,
        "verifier": verifier,
        "verified_at": verified_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        "admitted": admitted,
        "lease_id": document.get("lease_id"),
        "task_id": document.get("task_id"),
        "release_id": document.get("release_id"),
        "candidate_sha": document.get("candidate_sha"),
        "manifest_digest": document.get("manifest_digest"),
        "target_environment": document.get("target_environment"),
        "allowed_action": document.get("allowed_action"),
        "issued_at": document.get("issued_at"),
        "expires_at": document.get("expires_at"),
        "signature_key_id": (
            signature.get("key_id") if isinstance(signature, dict) else None
        ),
        "nonce_digest": _digest_or_none(document.get("nonce")),
        "signature_digest": _digest_or_none(signature_value),
        "consumed_at": consumed_at,
        "errors": list(errors),
    }


def admit_and_consume(
    lease: Any,
    *,
    public_key: Ed25519PublicKey,
    state_store: LeaseStateStore,
    consumed_by: str,
    verifier: str = "check_runtime_admission",
    extra_errors: list[str] | None = None,
    expected_task_id: str | None = None,
    expected_candidate_sha: str | None = None,
    expected_manifest_digest: str | None = None,
    expected_environment: str | None = None,
    expected_action: str | None = DEFAULT_ACTION,
    now: datetime | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Verify a lease and, only if it is fully valid, consume it exactly once.

    `extra_errors` carries admission failures found outside the lease itself
    (the staged gate registry, for example). They block consumption, so a
    single-use lease is not burned by a release that could never have been
    admitted.

    Returns `(admitted, errors, receipt)`.
    """

    verified_at = (now or _utc_now()).astimezone(UTC)
    errors = list(extra_errors or [])
    errors.extend(
        verify_lease(
            lease,
            public_key=public_key,
            state_store=state_store,
            expected_task_id=expected_task_id,
            expected_candidate_sha=expected_candidate_sha,
            expected_manifest_digest=expected_manifest_digest,
            expected_environment=expected_environment,
            expected_action=expected_action,
            now=verified_at,
        )
    )
    if errors:
        return False, errors, build_receipt(
            lease,
            errors=errors,
            admitted=False,
            verified_at=verified_at,
            verifier=verifier,
        )

    try:
        record = state_store.consume(lease, consumed_by=consumed_by)
    except LeaseStateError as exc:
        # Lost the compare-and-set race against a concurrent admission.
        errors = [str(exc)]
        return False, errors, build_receipt(
            lease,
            errors=errors,
            admitted=False,
            verified_at=verified_at,
            verifier=verifier,
        )

    return True, [], build_receipt(
        lease,
        errors=[],
        admitted=True,
        verified_at=verified_at,
        verifier=verifier,
        consumed_at=record.get("consumed_at"),
    )


def load_lease(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Read a lease document without guessing at malformed input."""

    if not path.exists():
        return None, [f"lease file does not exist: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"lease file cannot be read as JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["lease document must be a JSON object"]
    return payload, []


def _digest_or_none(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DEFAULT_ACTION",
    "DEFAULT_TTL_SECONDS",
    "LEASE_SCHEMA_VERSION",
    "LEASE_STATES",
    "MAX_TTL_SECONDS",
    "NOT_BEFORE_SKEW_SECONDS",
    "STATE_CONSUMED",
    "STATE_ISSUED",
    "STATE_REVOKED",
    "TARGET_ENVIRONMENTS",
    "LeaseError",
    "LeaseIssuanceError",
    "LeaseKeyError",
    "LeaseStateError",
    "LeaseStateStore",
    "admit_and_consume",
    "build_lease",
    "build_receipt",
    "canonical_bytes",
    "canonical_payload",
    "generate_keypair",
    "load_lease",
    "load_private_key",
    "load_public_key",
    "public_key_id",
    "sign_lease",
    "signature_errors",
    "verify_lease",
]
