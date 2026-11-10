"""Native snapshot transport contract and verification of provider evidence.

This interface deliberately differs from the legacy ObjectStore: a configured
encryption-key string and a bucket-name allowlist do not prove actual CMEK or
physical storage location. These checks confer no source or job authority.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_retrieval_policy import (
    MAX_RESPONSE_BYTES,
    NativeSnapshotBinding,
)
from shared.infrastructure.object_store.client import ResidencyDeniedError, check_bucket_residency

NATIVE_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNSIGNED = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")


class NativeSnapshotProofRejected(ValueError):
    pass


def _reject() -> None:
    # Provider responses can contain object names, URLs and access details.
    # Never reflect them through a worker error or an operator receipt.
    raise NativeSnapshotProofRejected("NATIVE_RETRIEVAL_SNAPSHOT_PROOF_REJECTED") from None


def _provider_integer(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, str) and _UNSIGNED.fullmatch(value):
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


def native_snapshot_object_key(
    tenant_id: str, job_id: str, generation: int, business_fence: int, capture_id: str,
) -> str:
    """Isolate late writes from every other tenant, job and accepted attempt."""
    tenant = native_intake_uuid(tenant_id)
    job = native_intake_uuid(job_id)
    capture = native_intake_uuid(capture_id)
    if (type(generation) is not int or not 1 <= generation <= 2**63 - 1
            or type(business_fence) is not int or not 1 <= business_fence <= 2**63 - 1):
        _reject()
    return f"tenants/{tenant}/native-intake-retrieval/{job}/{generation}/{business_fence}/{capture}/raw"


def _bound_key(binding: NativeSnapshotBinding, key: str) -> None:
    if not isinstance(key, str):
        _reject()
    parts = key.split("/")
    if len(parts) != 8:
        _reject()
    try:
        generation = _provider_integer(parts[4], minimum=1, maximum=2**63 - 1)
        fence = _provider_integer(parts[5], minimum=1, maximum=2**63 - 1)
        expected = native_snapshot_object_key(binding.tenant_id, parts[3], generation, fence, parts[6])
    except (ValueError, TypeError, AttributeError):
        _reject()
    if key != expected:
        _reject()


@dataclass(frozen=True)
class NativeBucketMetadata:
    bucket_name: str
    location: str
    location_type: str

    @classmethod
    def from_provider(cls, value: Any, binding: NativeSnapshotBinding) -> NativeBucketMetadata:
        if (not isinstance(value, dict) or value.get("kind") != "storage#bucket"
                or value.get("name") != binding.bucket
                or not isinstance(value.get("location"), str)
                or not isinstance(value.get("locationType"), str)):
            _reject()
        metadata = cls(value["name"], value["location"].upper(), value["locationType"].lower())
        if (binding.residency_mode != "TW_ONLY" or metadata.location != "ASIA-EAST1"
                or metadata.location_type != "region"):
            _reject()
        # Both are required: caller configuration may extend the name
        # allowlist, but it cannot override the actual provider region check.
        try:
            check_bucket_residency(binding.residency_mode, binding.bucket)
        except ResidencyDeniedError:
            _reject()
        return metadata


@dataclass(frozen=True)
class NativeSnapshotObjectMetadata:
    uri: str
    generation: int
    byte_length: int
    media_type: str
    kms_key_version_name: str

    @classmethod
    def from_provider(
        cls, value: Any, binding: NativeSnapshotBinding, key: str,
        *, expected_generation: int | None = None,
    ) -> NativeSnapshotObjectMetadata:
        _bound_key(binding, key)
        if (not isinstance(value, dict) or value.get("kind") != "storage#object"
                or value.get("bucket") != binding.bucket or value.get("name") != key
                or not isinstance(value.get("contentType"), str)
                or value.get("contentType") not in NATIVE_MEDIA_TYPES
                or value.get("contentEncoding") not in (None, "", "identity")
                or not binding.accepts_actual_key_version(value.get("kmsKeyName"))):
            _reject()
        generation = _provider_integer(value.get("generation"), minimum=1, maximum=2**63 - 1)
        if expected_generation is not None and (
            type(expected_generation) is not int or generation != expected_generation
        ):
            _reject()
        length = _provider_integer(value.get("size"), minimum=0, maximum=MAX_RESPONSE_BYTES)
        return cls(
            uri=f"gs://{binding.bucket}/{key}", generation=generation, byte_length=length,
            media_type=value["contentType"], kms_key_version_name=value["kmsKeyName"],
        )


@dataclass(frozen=True)
class NativeVerifiedSnapshot:
    metadata: NativeSnapshotObjectMetadata
    content_sha256: str

    @classmethod
    def from_download(
        cls, data: bytes, metadata: NativeSnapshotObjectMetadata, *, expected_sha256: str,
    ) -> NativeVerifiedSnapshot:
        if (not isinstance(data, bytes) or len(data) > MAX_RESPONSE_BYTES
                or len(data) != metadata.byte_length
                or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)):
            _reject()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_sha256:
            _reject()
        return cls(metadata, actual)


class NativeSnapshotTransport(Protocol):
    """All I/O uses an owned process and an absolute monotonic deadline.

    The native owner authorizes each operation separately and journals upload
    intent before dispatch. The transport is not an authorization substitute.
    """

    def bucket_info(
        self, binding: NativeSnapshotBinding, *, deadline: float,
    ) -> NativeBucketMetadata: ...

    def upload_with_kms_if_absent(
        self, binding: NativeSnapshotBinding, key: str, data: bytes, media_type: str,
        *, deadline: float,
    ) -> NativeSnapshotObjectMetadata: ...

    def head_version(
        self, binding: NativeSnapshotBinding, key: str, generation: int | None,
        *, deadline: float,
    ) -> NativeSnapshotObjectMetadata:
        """None is only for resolving an unknown upload at its exact intent key."""
        ...

    def download_version_bounded(
        self, binding: NativeSnapshotBinding, key: str, generation: int,
        *, deadline: float,
    ) -> bytes: ...

    def delete_exact_generation(
        self, binding: NativeSnapshotBinding, key: str, generation: int,
        *, deadline: float,
    ) -> None:
        """Requires a fresh owned SQL decision proving no committed reference."""
        ...
