"""Purpose-bound parser settings, unavailable until explicitly configured.

The initial realm supports isolated engineering in its own named database,
source and storage namespace. A PostgreSQL backend or PRODUCTION catalog enum
never establishes deployment authority. Production activation remains gated
by the separately governed release evidence and is unavailable here.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass

from modules.listing.application.native_intake_job_runtime import (
    decode_native_intake_attempt,
    native_intake_uuid,
    sign_native_intake_attempt,
)
from modules.listing.application.native_intake_retrieval_policy import (
    NativeSnapshotBinding,
    canonical_sha256,
)
from modules.listing.domain.identity_graph import IdentityConflictError

JOB_TYPE = "native-intake-parser"
POLICY_ENV = "ODP_NATIVE_INTAKE_PARSER_POLICY_JSON"
STORAGE_ENV = "ODP_NATIVE_INTAKE_PARSER_STORAGE_JSON"
REALM_ENV = "ODP_NATIVE_INTAKE_PARSER_REALM_JSON"
SIGNING_KEY_ENV = "ODP_NATIVE_INTAKE_PARSER_SIGNING_KEY"
MAX_COMMAND_SECONDS = 120
MAX_ATTEMPTS = 5
MAX_GLOBAL_CONCURRENCY = 100
MAX_CHILD_SECONDS = 3
MAX_IO_SECONDS = 10
HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 45
CLEANUP_SECONDS = 1
_LIMIT = 2**63 - 1
_NAMESPACE = re.compile(r"[a-z][a-z0-9_]{2,23}\Z")
_KEY = re.compile(r"projects/[A-Za-z0-9_-]{1,128}/locations/asia-east1/"
    r"keyRings/[A-Za-z0-9_-]{1,63}/cryptoKeys/[A-Za-z0-9_-]{1,63}"
    r"(?:/cryptoKeyVersions/[1-9][0-9]{0,18})?\Z")
_RETENTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")


class NativeParserUnavailable(RuntimeError):
    pass


class NativeParserConflict(IdentityConflictError):
    pass


def _unavailable():
    raise NativeParserUnavailable("NATIVE_PARSER_CONFIGURATION_UNAVAILABLE") from None


def _closed(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _unavailable()
        value[key] = item
    return value


def _json(env):
    raw = os.environ.get(env, "")
    if not 1 <= len(raw) <= 65_536:
        _unavailable()
    value = json.loads(raw, object_pairs_hook=_closed, parse_constant=lambda _: _unavailable())
    if not isinstance(value, dict):
        _unavailable()
    return value


def _int(value, maximum=_LIMIT):
    if type(value) is not int or not 1 <= value <= maximum:
        _unavailable()
    return value


@dataclass(frozen=True)
class NativeParserRealm:
    version: int
    mode: str
    namespace: str
    database_name: str

    @property
    def source_prefix(self):
        return "native-parser-" + self.namespace.replace("_", "-") + "."

    @property
    def bucket(self):
        return "native-parser-" + self.namespace.replace("_", "-")


@dataclass(frozen=True)
class NativeParserPolicy:
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
    realm: NativeParserRealm

    def snapshot(self):
        return asdict(self)

    @property
    def sha256(self):
        return canonical_sha256(self.snapshot())


def native_intake_parser_policy(tenant_id):
    try:
        tenant = native_intake_uuid(tenant_id)
        policy, storage, realm = _json(POLICY_ENV), _json(STORAGE_ENV), _json(REALM_ENV)
        if (set(policy) != {"policy_id", "version", "tenant_ids", "max_attempts", "deadline_seconds", "backoff_seconds"}
                or not isinstance(policy["policy_id"], str) or not policy["policy_id"].strip()
                or len(policy["policy_id"]) > 128 or any(ord(c) < 32 for c in policy["policy_id"])
                or not isinstance(policy["tenant_ids"], list) or not 1 <= len(policy["tenant_ids"]) <= 100
                or not isinstance(policy["backoff_seconds"], list)):
            _unavailable()
        tenants = [native_intake_uuid(value) for value in policy["tenant_ids"]]
        if tenant not in tenants:
            raise PermissionError("NATIVE_PARSER_TENANT_NOT_CONFIGURED")
        if len(set(tenants)) != len(tenants):
            _unavailable()
        _int(policy["version"])
        _int(policy["max_attempts"], MAX_ATTEMPTS)
        _int(policy["deadline_seconds"], MAX_COMMAND_SECONDS)
        backoff = policy["backoff_seconds"]
        if (len(backoff) != policy["max_attempts"] - 1
                or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0 for v in backoff)
                or sum(backoff) >= policy["deadline_seconds"]):
            _unavailable()
        if (set(realm) != {"version", "mode", "namespace", "database_name"}
                or realm["mode"] != "isolated_engineering" or not isinstance(realm["namespace"], str)
                or not _NAMESPACE.fullmatch(realm["namespace"])
                or realm["database_name"] != "oday_native_parser_" + realm["namespace"]):
            _unavailable()
        _int(realm["version"])
        runtime = NativeParserRealm(**realm)
        if (set(storage) != {"version", "global_max_concurrency", "per_source_max_concurrency", "storage_bindings"}
                or not isinstance(storage["storage_bindings"], list)
                or len(storage["storage_bindings"]) != len(tenants)):
            _unavailable()
        _int(storage["version"])
        _int(storage["global_max_concurrency"], MAX_GLOBAL_CONCURRENCY)
        _int(storage["per_source_max_concurrency"], storage["global_max_concurrency"])
        bindings = {}
        for value in storage["storage_bindings"]:
            if not isinstance(value, dict) or set(value) != {
                    "tenant_id", "bucket", "kms_key_name", "residency_mode", "retention_class"}:
                _unavailable()
            bound = native_intake_uuid(value["tenant_id"])
            if (bound in bindings or value["bucket"] != runtime.bucket or value["residency_mode"] != "TW_ONLY"
                    or not isinstance(value["kms_key_name"], str) or not _KEY.fullmatch(value["kms_key_name"])
                    or not isinstance(value["retention_class"], str) or not _RETENTION.fullmatch(value["retention_class"])):
                _unavailable()
            bindings[bound] = NativeSnapshotBinding(**value)
        if set(bindings) != set(tenants):
            _unavailable()
        return NativeParserPolicy(policy["policy_id"], policy["version"], tenant, policy["max_attempts"],
            policy["deadline_seconds"], tuple(backoff), storage["version"], storage["global_max_concurrency"],
            storage["per_source_max_concurrency"], bindings[tenant], runtime)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        _unavailable()


class NativeParserAuthenticator:
    @staticmethod
    def require_available():
        """Availability only; no signature, source or execution grant."""
        if not 32 <= len(os.environ.get(SIGNING_KEY_ENV, "").encode()) <= 4096:
            raise NativeParserUnavailable("NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE")

    @staticmethod
    def sign(job_id, tenant_id, generation):
        _int(generation)
        NativeParserAuthenticator.require_available()
        return sign_native_intake_attempt(JOB_TYPE, SIGNING_KEY_ENV, job_id, tenant_id, generation,
            unavailable=NativeParserUnavailable, error_code="NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE")

    def decode(self, delivery):
        return decode_native_intake_attempt(delivery, job_type=JOB_TYPE, sign=self.sign,
            error_code="NATIVE_PARSER_TOKEN_REJECTED")
