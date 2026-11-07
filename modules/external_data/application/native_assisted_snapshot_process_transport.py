"""Fixed JSON-only GCS process capability with its own dispatch credential.

No production worker selects this capability until the native assisted owner
is implemented. Calling it never substitutes for current human/source proof.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable

from modules.external_data.application.native_assisted_snapshot_transport import (
    ASSISTED_JSON_MEDIA,
    NativeAssistedSnapshotObjectMetadata,
    bound_assisted_key,
)
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
)
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_retrieval_policy import MAX_RESPONSE_BYTES

ASSISTED_STORAGE_CREDENTIAL_ENV = "ODP_NATIVE_INTAKE_ASSISTED_SNAPSHOT_GCS_TOKEN"


def native_assisted_snapshot_credential() -> str:
    value = os.environ.get(ASSISTED_STORAGE_CREDENTIAL_ENV, "")
    if (not value or len(value) > 8192 or not value.isascii()
            or any(ord(character) < 33 or ord(character) > 126 for character in value)):
        raise NativeIORejected("NATIVE_GCS_ASSISTED_CREDENTIAL_UNAVAILABLE")
    return value


def _generation(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise NativeIORejected("NATIVE_GCS_GENERATION_REJECTED")


class NativeAssistedSnapshotProcessTransport:
    def __init__(self, *, guard: Callable[[], None], runner=None):
        self.guard = guard
        self.runner = runner or NativeIntakeProcessRunner()

    def _run(self, binding, operation, deadline, **values):
        self.guard()
        return self.runner.run({"operation": operation, "bucket": binding.bucket, **values},
            deadline=deadline, guard=self.guard, credential=native_assisted_snapshot_credential())

    def bucket_info(self, binding, *, deadline: float) -> NativeBucketMetadata:
        return NativeBucketMetadata.from_provider(self._run(binding, "gcs_assisted_bucket", deadline), binding)

    def upload_with_kms_if_absent(self, binding, key: str, data: bytes, media_type: str,
                                 *, deadline: float) -> NativeAssistedSnapshotObjectMetadata:
        bound_assisted_key(binding, key)
        if (not isinstance(data, bytes) or not 1 <= len(data) <= MAX_RESPONSE_BYTES
                or not isinstance(media_type, str) or media_type not in ASSISTED_JSON_MEDIA):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        result = self._run(binding, "gcs_assisted_upload", deadline, key=key,
            body_base64=base64.b64encode(data).decode("ascii"), media_type=media_type,
            kms_key_name=binding.upload_key_name)
        return NativeAssistedSnapshotObjectMetadata.from_provider(result, binding, key)

    def head_version(self, binding, key: str, generation: int | None,
                     *, deadline: float) -> NativeAssistedSnapshotObjectMetadata:
        bound_assisted_key(binding, key)
        if generation is not None:
            _generation(generation)
        result = self._run(binding, "gcs_assisted_head", deadline, key=key, generation=generation)
        return NativeAssistedSnapshotObjectMetadata.from_provider(result, binding, key, expected_generation=generation)

    def download_version_bounded(self, binding, key: str, generation: int, *, deadline: float) -> bytes:
        bound_assisted_key(binding, key)
        _generation(generation)
        result = self._run(binding, "gcs_assisted_download", deadline, key=key, generation=generation)
        if set(result) != {"body_base64"}:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        try:
            data = base64.b64decode(result["body_base64"], validate=True)
        except (ValueError, TypeError):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED") from None
        if not 1 <= len(data) <= MAX_RESPONSE_BYTES:
            raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
        return data

    def delete_exact_generation(self, binding, key: str, generation: int, *, deadline: float) -> None:
        bound_assisted_key(binding, key)
        _generation(generation)
        if self._run(binding, "gcs_assisted_delete", deadline, key=key, generation=generation) != {}:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
