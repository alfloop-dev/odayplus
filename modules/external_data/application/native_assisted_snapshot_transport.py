"""Purpose-specific JSON evidence proofs for native Intake JSON snapshots.

Two closed purposes share these mechanics: human assisted entry and the
native Geo provider response. Each has its own object-key family, child
operation family, dispatch credential and rejection code; an object of one
purpose is never accepted as the other. These checks confer no human, source
or job authority. The transaction owner journals each operation and
revalidates its command separately. Retrieval's HTML media types and
object-key namespace remain independent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_retrieval_policy import (
    MAX_RESPONSE_BYTES,
    NativeSnapshotBinding,
)

ASSISTED_JSON_MEDIA = frozenset({"application/json"})
_UNSIGNED = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class NativeAssistedSnapshotProofRejected(ValueError):
    pass


@dataclass(frozen=True)
class NativeJsonSnapshotPurpose:
    """One closed JSON evidence purpose; never selected by payload or config."""

    name: str
    key_family: str
    operation_prefix: str
    credential_env: str
    credential_unavailable_code: str
    proof_rejected_code: str

    def operation(self, verb: str) -> str:
        if verb not in {"bucket", "upload", "head", "download", "delete"}:
            raise NativeAssistedSnapshotProofRejected(self.proof_rejected_code)
        return self.operation_prefix + verb


ASSISTED_JSON_PURPOSE = NativeJsonSnapshotPurpose(
    "ASSISTED", "native-intake-assisted-entry", "gcs_assisted_",
    "ODP_NATIVE_INTAKE_ASSISTED_SNAPSHOT_GCS_TOKEN",
    "NATIVE_GCS_ASSISTED_CREDENTIAL_UNAVAILABLE", "NATIVE_ASSISTED_SNAPSHOT_PROOF_REJECTED")
GEOCODE_JSON_PURPOSE = NativeJsonSnapshotPurpose(
    "GEOCODE", "native-intake-geocode", "gcs_geocode_",
    "ODP_NATIVE_INTAKE_GEOCODE_SNAPSHOT_GCS_TOKEN",
    "NATIVE_GCS_GEOCODE_CREDENTIAL_UNAVAILABLE", "NATIVE_GEOCODE_SNAPSHOT_PROOF_REJECTED")
NATIVE_JSON_PURPOSES = (ASSISTED_JSON_PURPOSE, GEOCODE_JSON_PURPOSE)


def _purpose(value: Any) -> NativeJsonSnapshotPurpose:
    if not any(value is purpose for purpose in NATIVE_JSON_PURPOSES):
        raise NativeAssistedSnapshotProofRejected("NATIVE_JSON_SNAPSHOT_PURPOSE_REJECTED") from None
    return value


def _reject(purpose: NativeJsonSnapshotPurpose = ASSISTED_JSON_PURPOSE):
    raise NativeAssistedSnapshotProofRejected(purpose.proof_rejected_code) from None


def _integer(value: Any, *, minimum: int, maximum: int,
             purpose: NativeJsonSnapshotPurpose = ASSISTED_JSON_PURPOSE) -> int:
    if isinstance(value, str) and _UNSIGNED.fullmatch(value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        _reject(purpose)
    return value


def native_json_snapshot_object_key(
    purpose: NativeJsonSnapshotPurpose, tenant_id: str, job_id: str, generation: int,
    business_fence: int, capture_id: str,
) -> str:
    purpose = _purpose(purpose)
    try:
        tenant = native_intake_uuid(tenant_id)
        job = native_intake_uuid(job_id)
        capture = native_intake_uuid(capture_id)
    except (ValueError, TypeError, AttributeError):
        _reject(purpose)
    for number in (generation, business_fence):
        if type(number) is not int or not 1 <= number <= 2**63 - 1:
            _reject(purpose)
    return f"tenants/{tenant}/{purpose.key_family}/{job}/{generation}/{business_fence}/{capture}/raw"


def bound_json_key(purpose: NativeJsonSnapshotPurpose, binding: NativeSnapshotBinding, key: str) -> None:
    purpose = _purpose(purpose)
    if not isinstance(key, str) or len(key) > 256:
        _reject(purpose)
    parts = key.split("/")
    if len(parts) != 8 or parts[2] != purpose.key_family:
        _reject(purpose)
    try:
        generation = _integer(parts[4], minimum=1, maximum=2**63 - 1, purpose=purpose)
        fence = _integer(parts[5], minimum=1, maximum=2**63 - 1, purpose=purpose)
        expected = native_json_snapshot_object_key(purpose, binding.tenant_id, parts[3], generation, fence, parts[6])
    except (ValueError, TypeError, AttributeError):
        _reject(purpose)
    if key != expected:
        _reject(purpose)


def native_assisted_snapshot_object_key(
    tenant_id: str, job_id: str, generation: int, business_fence: int, capture_id: str,
) -> str:
    return native_json_snapshot_object_key(
        ASSISTED_JSON_PURPOSE, tenant_id, job_id, generation, business_fence, capture_id)


def bound_assisted_key(binding: NativeSnapshotBinding, key: str) -> None:
    bound_json_key(ASSISTED_JSON_PURPOSE, binding, key)


@dataclass(frozen=True)
class NativeAssistedSnapshotObjectMetadata:
    uri: str
    generation: int
    byte_length: int
    media_type: str
    kms_key_version_name: str

    @classmethod
    def from_provider(cls, value: Any, binding: NativeSnapshotBinding, key: str,
                      *, expected_generation: int | None = None,
                      purpose: NativeJsonSnapshotPurpose = ASSISTED_JSON_PURPOSE,
                      ) -> NativeAssistedSnapshotObjectMetadata:
        bound_json_key(purpose, binding, key)
        if (not isinstance(value, dict) or value.get("kind") != "storage#object"
                or value.get("bucket") != binding.bucket or value.get("name") != key
                or not isinstance(value.get("contentType"), str)
                or value["contentType"] not in ASSISTED_JSON_MEDIA
                or value.get("contentEncoding") not in (None, "", "identity")
                or not binding.accepts_actual_key_version(value.get("kmsKeyName"))):
            _reject(purpose)
        generation = _integer(value.get("generation"), minimum=1, maximum=2**63 - 1, purpose=purpose)
        if expected_generation is not None and (
            type(expected_generation) is not int or generation != expected_generation
        ):
            _reject(purpose)
        length = _integer(value.get("size"), minimum=1, maximum=MAX_RESPONSE_BYTES, purpose=purpose)
        return cls(f"gs://{binding.bucket}/{key}", generation, length,
                   value["contentType"], value["kmsKeyName"])


@dataclass(frozen=True)
class NativeVerifiedAssistedSnapshot:
    metadata: NativeAssistedSnapshotObjectMetadata
    content_sha256: str

    @classmethod
    def from_download(cls, data: bytes, metadata: NativeAssistedSnapshotObjectMetadata,
                      *, expected_sha256: str,
                      purpose: NativeJsonSnapshotPurpose = ASSISTED_JSON_PURPOSE,
                      ) -> NativeVerifiedAssistedSnapshot:
        purpose = _purpose(purpose)
        if (not isinstance(metadata, NativeAssistedSnapshotObjectMetadata)
                or f"/{purpose.key_family}/" not in metadata.uri
                or not isinstance(data, bytes) or not 1 <= len(data) <= MAX_RESPONSE_BYTES
                or len(data) != metadata.byte_length or not isinstance(expected_sha256, str)
                or not _SHA256.fullmatch(expected_sha256)):
            _reject(purpose)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            _reject(purpose)
        return cls(metadata, actual)
