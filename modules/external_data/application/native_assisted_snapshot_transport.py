"""Purpose-specific JSON evidence proofs for native human assisted entry.

These checks confer no human, source or job authority. The future transaction
owner must journal each operation and revalidate its human command separately.
Retrieval's HTML media types and object-key namespace remain independent.
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


def _reject():
    raise NativeAssistedSnapshotProofRejected("NATIVE_ASSISTED_SNAPSHOT_PROOF_REJECTED") from None


def _integer(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, str) and _UNSIGNED.fullmatch(value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


def native_assisted_snapshot_object_key(
    tenant_id: str, job_id: str, generation: int, business_fence: int, capture_id: str,
) -> str:
    try:
        tenant = native_intake_uuid(tenant_id)
        job = native_intake_uuid(job_id)
        capture = native_intake_uuid(capture_id)
    except (ValueError, TypeError, AttributeError):
        _reject()
    for number in (generation, business_fence):
        if type(number) is not int or not 1 <= number <= 2**63 - 1:
            _reject()
    return f"tenants/{tenant}/native-intake-assisted-entry/{job}/{generation}/{business_fence}/{capture}/raw"


def bound_assisted_key(binding: NativeSnapshotBinding, key: str) -> None:
    if not isinstance(key, str) or len(key) > 256:
        _reject()
    parts = key.split("/")
    if len(parts) != 8:
        _reject()
    try:
        generation = _integer(parts[4], minimum=1, maximum=2**63 - 1)
        fence = _integer(parts[5], minimum=1, maximum=2**63 - 1)
        expected = native_assisted_snapshot_object_key(binding.tenant_id, parts[3], generation, fence, parts[6])
    except (ValueError, TypeError, AttributeError):
        _reject()
    if key != expected:
        _reject()


@dataclass(frozen=True)
class NativeAssistedSnapshotObjectMetadata:
    uri: str
    generation: int
    byte_length: int
    media_type: str
    kms_key_version_name: str

    @classmethod
    def from_provider(cls, value: Any, binding: NativeSnapshotBinding, key: str,
                      *, expected_generation: int | None = None) -> NativeAssistedSnapshotObjectMetadata:
        bound_assisted_key(binding, key)
        if (not isinstance(value, dict) or value.get("kind") != "storage#object"
                or value.get("bucket") != binding.bucket or value.get("name") != key
                or not isinstance(value.get("contentType"), str)
                or value["contentType"] not in ASSISTED_JSON_MEDIA
                or value.get("contentEncoding") not in (None, "", "identity")
                or not binding.accepts_actual_key_version(value.get("kmsKeyName"))):
            _reject()
        generation = _integer(value.get("generation"), minimum=1, maximum=2**63 - 1)
        if expected_generation is not None and (
            type(expected_generation) is not int or generation != expected_generation
        ):
            _reject()
        length = _integer(value.get("size"), minimum=1, maximum=MAX_RESPONSE_BYTES)
        return cls(f"gs://{binding.bucket}/{key}", generation, length,
                   value["contentType"], value["kmsKeyName"])


@dataclass(frozen=True)
class NativeVerifiedAssistedSnapshot:
    metadata: NativeAssistedSnapshotObjectMetadata
    content_sha256: str

    @classmethod
    def from_download(cls, data: bytes, metadata: NativeAssistedSnapshotObjectMetadata,
                      *, expected_sha256: str) -> NativeVerifiedAssistedSnapshot:
        if (not isinstance(metadata, NativeAssistedSnapshotObjectMetadata)
                or not isinstance(data, bytes) or not 1 <= len(data) <= MAX_RESPONSE_BYTES
                or len(data) != metadata.byte_length or not isinstance(expected_sha256, str)
                or not _SHA256.fullmatch(expected_sha256)):
            _reject()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            _reject()
        return cls(metadata, actual)
