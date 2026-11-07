"""Native one-operation process transports; never a source permission grant."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
)
from modules.external_data.application.native_snapshot_transport import (
    NATIVE_MEDIA_TYPES,
    NativeBucketMetadata,
    NativeSnapshotObjectMetadata,
    _bound_key,
)
from modules.listing.application.native_intake_retrieval_policy import (
    MAX_RESPONSE_BYTES,
    NativeSnapshotBinding,
    native_snapshot_credential,
)


def _bytes(value):
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
    return data


@dataclass(frozen=True)
class NativeHttpHop:
    status: int
    location: str | None
    media_type: str | None
    body: bytes
    auth_wall: bool


class NativeHttpProcessTransport:
    def __init__(self, runner=None):
        self.runner = runner or NativeIntakeProcessRunner()

    def fetch_hop(self, url: str, *, deadline: float, guard: Callable[[], None]) -> NativeHttpHop:
        result = self.runner.run({"operation": "http_hop", "url": url}, deadline=deadline, guard=guard)
        if (set(result) != {"status", "location", "media_type", "body_base64", "auth_wall"}
                or type(result["status"]) is not int or not 100 <= result["status"] <= 599
                or type(result["auth_wall"]) is not bool
                or (result["media_type"] is not None and (not isinstance(result["media_type"], str)
                                                         or result["media_type"] not in NATIVE_MEDIA_TYPES))
                or (result["location"] is not None and (not isinstance(result["location"], str) or len(result["location"]) > 4096))):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        body = _bytes(result["body_base64"])
        if ((result["status"] != 200 or result["auth_wall"]) and body
                or (result["status"] == 200 and not result["auth_wall"] and result["media_type"] not in NATIVE_MEDIA_TYPES)):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        return NativeHttpHop(result["status"], result["location"], result["media_type"], body, result["auth_wall"])


class NativeSnapshotProcessTransport:
    def __init__(self, *, guard: Callable[[], None], runner=None):
        self.guard = guard
        self.runner = runner or NativeIntakeProcessRunner()

    def _run(self, binding, operation, deadline, **values):
        self.guard()
        return self.runner.run({"operation": operation, "bucket": binding.bucket, **values},
            deadline=deadline, guard=self.guard, credential=native_snapshot_credential())

    def bucket_info(self, binding: NativeSnapshotBinding, *, deadline: float) -> NativeBucketMetadata:
        return NativeBucketMetadata.from_provider(self._run(binding, "gcs_bucket", deadline), binding)

    def upload_with_kms_if_absent(self, binding: NativeSnapshotBinding, key: str, data: bytes, media_type: str,
                                 *, deadline: float) -> NativeSnapshotObjectMetadata:
        _bound_key(binding, key)
        if (not isinstance(data, bytes) or len(data) > MAX_RESPONSE_BYTES
                or not isinstance(media_type, str) or media_type not in NATIVE_MEDIA_TYPES):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        result = self._run(binding, "gcs_upload", deadline, key=key, body_base64=base64.b64encode(data).decode("ascii"),
                           media_type=media_type, kms_key_name=binding.upload_key_name)
        return NativeSnapshotObjectMetadata.from_provider(result, binding, key)

    def head_version(self, binding: NativeSnapshotBinding, key: str, generation: int | None,
                     *, deadline: float) -> NativeSnapshotObjectMetadata:
        _bound_key(binding, key)
        if generation is not None and (type(generation) is not int or not 1 <= generation <= 2**63 - 1):
            raise NativeIORejected("NATIVE_GCS_GENERATION_REJECTED")
        result = self._run(binding, "gcs_head", deadline, key=key, generation=generation)
        return NativeSnapshotObjectMetadata.from_provider(result, binding, key, expected_generation=generation)

    def download_version_bounded(self, binding: NativeSnapshotBinding, key: str, generation: int,
                                 *, deadline: float) -> bytes:
        _bound_key(binding, key)
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise NativeIORejected("NATIVE_GCS_GENERATION_REJECTED")
        result = self._run(binding, "gcs_download", deadline, key=key, generation=generation)
        if set(result) != {"body_base64"}:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        return _bytes(result["body_base64"])

    def delete_exact_generation(self, binding: NativeSnapshotBinding, key: str, generation: int,
                               *, deadline: float) -> None:
        _bound_key(binding, key)
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise NativeIORejected("NATIVE_GCS_GENERATION_REJECTED")
        if self._run(binding, "gcs_delete", deadline, key=key, generation=generation) != {}:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
