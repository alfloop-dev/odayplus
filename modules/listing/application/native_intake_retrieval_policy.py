"""Explicit runtime settings and purpose-bound native retrieval capabilities.

Loading settings does not approve a source, tenant storage or a rollout. Each
network intent must prove those permissions against current native SQL state.
Only the current tenant's storage binding may enter its durable job payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    native_intake_uuid,
    sign_native_intake_attempt,
)

JOB_TYPE = "native-intake-retrieval"
POLICY_ENV = "ODP_NATIVE_INTAKE_RETRIEVAL_POLICY_JSON"
STORAGE_ENV = "ODP_NATIVE_INTAKE_RETRIEVAL_STORAGE_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_RETRIEVAL_SIGNING_KEY"
STORAGE_CREDENTIAL_ENV = "ODP_NATIVE_INTAKE_SNAPSHOT_GCS_TOKEN"

# Conservative TOTAL admitted-job budget, including queue delay and retries.
# The contract's 10/30/120/600/1800s backoff cannot fit all five attempts in
# 120s. Numeric/backoff reconciliation remains OPEN for the named SLO owner;
# these engineering caps do not approve source access or production rollout.
MAX_COMMAND_SECONDS = 120
MAX_ATTEMPTS = 5
MAX_GLOBAL_CONCURRENCY = 100
MAX_IO_SECONDS = 10
MAX_RESPONSE_BYTES = 512_000
MAX_REDIRECTS = 3
HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 45

_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]\Z")
_KMS_KEY = re.compile(
    r"projects/[A-Za-z0-9_-]{1,128}/locations/asia-east1/"
    r"keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}"
    r"(?:/cryptoKeyVersions/[1-9][0-9]{0,18})?\Z"
)
_RETENTION_CLASS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")


class NativeIntakeRetrievalUnavailable(RuntimeError):
    pass


class NativeIntakeRetrievalConflict(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class NativeSnapshotBinding:
    tenant_id: str
    bucket: str
    kms_key_name: str
    residency_mode: str
    retention_class: str

    @property
    def upload_key_name(self) -> str:
        # GCS selects a key's active version. A requested exact version is
        # additionally checked against the actual object metadata afterwards.
        return self.kms_key_name.split("/cryptoKeyVersions/", 1)[0]

    def accepts_actual_key_version(self, value: Any) -> bool:
        if (not isinstance(value, str) or not _KMS_KEY.fullmatch(value)
                or "/cryptoKeyVersions/" not in value):
            return False
        if "/cryptoKeyVersions/" in self.kms_key_name:
            return value == self.kms_key_name
        return value.split("/cryptoKeyVersions/", 1)[0] == self.kms_key_name


@dataclass(frozen=True)
class NativeRetrievalPolicy:
    policy_id: str
    version: int
    tenant_id: str
    max_attempts: int
    deadline_seconds: int
    backoff_seconds: tuple[int | float, ...]
    storage_version: int
    global_max_concurrency: int
    storage: NativeSnapshotBinding

    def snapshot(self) -> dict:
        """Return only this tenant's immutable admission inputs, never a token."""
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "tenant_id": self.tenant_id,
            "max_attempts": self.max_attempts,
            "deadline_seconds": self.deadline_seconds,
            "backoff_seconds": list(self.backoff_seconds),
            "storage_version": self.storage_version,
            "global_max_concurrency": self.global_max_concurrency,
            "storage_binding": asdict(self.storage),
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.snapshot())


def native_intake_retrieval_policy(tenant_id: str) -> NativeRetrievalPolicy:
    """Validate complete explicit configuration before opening tenant SQL.

    Native APAC DR is deliberately unsupported in this initial implementation;
    it requires a separate authoritative residency binding. Legacy DR callers
    continue to use their existing storage interfaces.
    """
    policy = load_native_intake_policy(
        env_name=POLICY_ENV, max_seconds=MAX_COMMAND_SECONDS,
        max_attempts=MAX_ATTEMPTS, unavailable=NativeIntakeRetrievalUnavailable,
        error_code="NATIVE_RETRIEVAL_POLICY_UNAVAILABLE",
    )
    try:
        tenant = native_intake_uuid(tenant_id)
        if tenant not in policy["tenant_ids"]:
            raise PermissionError("NATIVE_RETRIEVAL_TENANT_NOT_CONFIGURED")
        settings = json.loads(os.environ.get(STORAGE_ENV, ""))
        if (not isinstance(settings, dict)
                or set(settings) != {"version", "global_max_concurrency", "storage_bindings"}
                or type(settings["version"]) is not int or settings["version"] < 1
                or type(settings["global_max_concurrency"]) is not int
                or not 1 <= settings["global_max_concurrency"] <= MAX_GLOBAL_CONCURRENCY
                or not isinstance(settings["storage_bindings"], list)):
            raise ValueError
        bindings: dict[str, NativeSnapshotBinding] = {}
        for entry in settings["storage_bindings"]:
            if (not isinstance(entry, dict) or set(entry) != {
                "tenant_id", "bucket", "kms_key_name", "residency_mode", "retention_class",
            }):
                raise ValueError
            bound_tenant = native_intake_uuid(entry["tenant_id"])
            if (bound_tenant in bindings
                    or entry["residency_mode"] != "TW_ONLY"
                    or not isinstance(entry["bucket"], str)
                    or not _BUCKET.fullmatch(entry["bucket"]) or ".." in entry["bucket"]
                    or not isinstance(entry["kms_key_name"], str)
                    or not _KMS_KEY.fullmatch(entry["kms_key_name"])
                    or not isinstance(entry["retention_class"], str)
                    or not _RETENTION_CLASS.fullmatch(entry["retention_class"])):
                raise ValueError
            bindings[bound_tenant] = NativeSnapshotBinding(**entry)
        if set(bindings) != set(policy["tenant_ids"]):
            raise ValueError
        return NativeRetrievalPolicy(
            policy_id=policy["policy_id"], version=policy["version"], tenant_id=tenant,
            max_attempts=policy["max_attempts"], deadline_seconds=policy["deadline_seconds"],
            backoff_seconds=tuple(policy["backoff_seconds"]), storage_version=settings["version"],
            global_max_concurrency=settings["global_max_concurrency"], storage=bindings[tenant],
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise NativeIntakeRetrievalUnavailable("NATIVE_RETRIEVAL_STORAGE_UNAVAILABLE") from error


def native_snapshot_credential() -> str:
    """Read the dedicated storage credential at I/O dispatch, never admission."""
    value = os.environ.get(STORAGE_CREDENTIAL_ENV, "")
    if (not value or len(value) > 8192 or not value.isascii()
            or any(ord(character) < 33 or ord(character) > 126 for character in value)):
        raise NativeIntakeRetrievalUnavailable("NATIVE_RETRIEVAL_STORAGE_CREDENTIAL_UNAVAILABLE")
    return value


class NativeIntakeRetrievalAuthenticator:
    @staticmethod
    def sign(job_id: str, tenant_id: str, generation: int) -> str:
        return sign_native_intake_attempt(
            JOB_TYPE, SIGNING_KEY_ENV, job_id, tenant_id, generation,
            unavailable=NativeIntakeRetrievalUnavailable,
            error_code="NATIVE_RETRIEVAL_SIGNING_KEY_UNAVAILABLE",
        )

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(
            delivery, job_type=JOB_TYPE, sign=self.sign,
            error_code="NATIVE_RETRIEVAL_TOKEN_REJECTED",
        )
