"""Explicit, purpose-bound settings for the native human assisted-entry owner.

There is no default tenant, storage, signing key, schedule or source grant.
Storage concurrency is independent of a manual source's HTTP request budget.
The engineering caps do not resolve the formal numeric/SLO approval table.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass

from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    load_native_intake_policy,
    native_intake_uuid,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_retrieval_policy import (
    NativeSnapshotBinding,
    canonical_sha256,
)
from modules.listing.domain.identity_graph import IdentityConflictError

JOB_TYPE = "native-intake-assisted-entry"
POLICY_ENV = "ODP_NATIVE_INTAKE_ASSISTED_ENTRY_POLICY_JSON"
STORAGE_ENV = "ODP_NATIVE_INTAKE_ASSISTED_ENTRY_STORAGE_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_ASSISTED_ENTRY_SIGNING_KEY"
MAX_COMMAND_SECONDS = 120
MAX_ATTEMPTS = 5
MAX_GLOBAL_CONCURRENCY = 100
HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 45
_BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]\Z")
_KEY = re.compile(r"projects/[A-Za-z0-9_-]{1,128}/locations/asia-east1/"
                  r"keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}"
                  r"(?:/cryptoKeyVersions/[1-9][0-9]{0,18})?\Z")
_RETENTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")


class NativeAssistedEntryUnavailable(RuntimeError):
    pass


class NativeAssistedEntryConflict(IdentityConflictError):
    pass


@dataclass(frozen=True)
class NativeAssistedEntryPolicy:
    policy_id: str
    version: int
    tenant_id: str
    max_attempts: int
    deadline_seconds: int
    backoff_seconds: tuple[int | float, ...]
    storage_version: int
    global_max_concurrency: int
    per_source_max_concurrency: int
    storage: NativeSnapshotBinding

    def snapshot(self) -> dict:
        return {"policy_id": self.policy_id, "version": self.version, "tenant_id": self.tenant_id,
            "max_attempts": self.max_attempts, "deadline_seconds": self.deadline_seconds,
            "backoff_seconds": list(self.backoff_seconds), "storage_version": self.storage_version,
            "global_max_concurrency": self.global_max_concurrency,
            "per_source_max_concurrency": self.per_source_max_concurrency, "storage_binding": asdict(self.storage)}

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.snapshot())


def native_intake_assisted_entry_policy(tenant_id: str) -> NativeAssistedEntryPolicy:
    policy = load_native_intake_policy(env_name=POLICY_ENV, max_seconds=MAX_COMMAND_SECONDS,
        max_attempts=MAX_ATTEMPTS, unavailable=NativeAssistedEntryUnavailable,
        error_code="NATIVE_ASSISTED_POLICY_UNAVAILABLE")
    try:
        tenant = native_intake_uuid(tenant_id)
        if tenant not in policy["tenant_ids"]:
            raise PermissionError("NATIVE_ASSISTED_TENANT_NOT_CONFIGURED")
        if (len(policy["policy_id"]) > 128 or len(policy["tenant_ids"]) > 100
                or not policy["version"] <= 2**63 - 1
                or sum(policy["backoff_seconds"]) >= policy["deadline_seconds"]):
            raise ValueError
        settings = json.loads(os.environ.get(STORAGE_ENV, ""))
        if (not isinstance(settings, dict) or set(settings) != {
                "version", "global_max_concurrency", "per_source_max_concurrency", "storage_bindings"}
                or type(settings["version"]) is not int or not 1 <= settings["version"] <= 2**63 - 1
                or type(settings["global_max_concurrency"]) is not int
                or not 1 <= settings["global_max_concurrency"] <= MAX_GLOBAL_CONCURRENCY
                or type(settings["per_source_max_concurrency"]) is not int
                or not 1 <= settings["per_source_max_concurrency"] <= settings["global_max_concurrency"]
                or not isinstance(settings["storage_bindings"], list)):
            raise ValueError
        bindings = {}
        for value in settings["storage_bindings"]:
            if not isinstance(value, dict) or set(value) != {
                    "tenant_id", "bucket", "kms_key_name", "residency_mode", "retention_class"}:
                raise ValueError
            bound_tenant = native_intake_uuid(value["tenant_id"])
            if (bound_tenant in bindings or value["residency_mode"] != "TW_ONLY"
                    or not isinstance(value["bucket"], str) or not _BUCKET.fullmatch(value["bucket"])
                    or ".." in value["bucket"] or not isinstance(value["kms_key_name"], str)
                    or not _KEY.fullmatch(value["kms_key_name"])
                    or not isinstance(value["retention_class"], str) or not _RETENTION.fullmatch(value["retention_class"])):
                raise ValueError
            bindings[bound_tenant] = NativeSnapshotBinding(**value)
        if set(bindings) != set(policy["tenant_ids"]):
            raise ValueError
        return NativeAssistedEntryPolicy(policy["policy_id"], policy["version"], tenant,
            policy["max_attempts"], policy["deadline_seconds"], tuple(policy["backoff_seconds"]),
            settings["version"], settings["global_max_concurrency"], settings["per_source_max_concurrency"], bindings[tenant])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise NativeAssistedEntryUnavailable("NATIVE_ASSISTED_STORAGE_OR_BUDGET_UNAVAILABLE") from error


class NativeAssistedEntryAuthenticator:
    @staticmethod
    def sign(job_id: str, tenant_id: str, generation: int) -> str:
        return sign_native_intake_attempt(JOB_TYPE, SIGNING_KEY_ENV, job_id, tenant_id, generation,
            unavailable=NativeAssistedEntryUnavailable, error_code="NATIVE_ASSISTED_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery) -> tuple[str, int]:
        return decode_native_intake_attempt(delivery, job_type=JOB_TYPE, sign=self.sign,
            error_code="NATIVE_ASSISTED_TOKEN_REJECTED")
