"""Read immutable manual evidence with a dedicated parser storage credential.

This capability exposes generation-pinned reads only. Source/human authority,
the SQL operation journal and anonymous storage quota belong to the owner.
"""
from __future__ import annotations

import base64
import os

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
    bound_assisted_key,
)
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
)
from modules.external_data.application.native_snapshot_transport import NativeBucketMetadata
from modules.listing.application.native_intake_retrieval_policy import MAX_RESPONSE_BYTES

PARSER_STORAGE_CREDENTIAL_ENV = "ODP_NATIVE_INTAKE_PARSER_SNAPSHOT_GCS_TOKEN"
_READS = frozenset({"gcs_assisted_bucket", "gcs_assisted_head", "gcs_assisted_download"})


def native_parser_snapshot_credential():
    token = os.environ.get(PARSER_STORAGE_CREDENTIAL_ENV, "")
    if (not token or len(token) > 8192 or not token.isascii()
            or any(ord(character) < 33 or ord(character) > 126 for character in token)):
        raise NativeIORejected("NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE")
    return token


def _generation(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise NativeIORejected("NATIVE_GCS_GENERATION_REJECTED")


class NativeParserSnapshotTransport:
    def __init__(self, *, guard, runner=None):
        self.guard = guard
        self.runner = runner or NativeIntakeProcessRunner()

    def _run(self, binding, operation, deadline, **values):
        if operation not in _READS:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        self.guard()
        result = self.runner.run({"operation": operation, "bucket": binding.bucket, **values},
            deadline=deadline, guard=self.guard, credential=native_parser_snapshot_credential())
        self.guard()
        return result

    def bucket_info(self, binding, *, deadline):
        return NativeBucketMetadata.from_provider(self._run(binding, "gcs_assisted_bucket", deadline), binding)

    def head_version(self, binding, key, generation, *, deadline):
        bound_assisted_key(binding, key)
        _generation(generation)
        raw = self._run(binding, "gcs_assisted_head", deadline, key=key, generation=generation)
        return NativeAssistedSnapshotObjectMetadata.from_provider(raw, binding, key, expected_generation=generation)

    def download_version_bounded(self, binding, key, generation, *, deadline):
        bound_assisted_key(binding, key)
        _generation(generation)
        raw = self._run(binding, "gcs_assisted_download", deadline, key=key, generation=generation)
        if not isinstance(raw, dict) or set(raw) != {"body_base64"}:
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        try:
            data = base64.b64decode(raw["body_base64"], validate=True)
        except (ValueError, TypeError):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED") from None
        if not 1 <= len(data) <= MAX_RESPONSE_BYTES:
            raise NativeIORejected("NATIVE_IO_LENGTH_REJECTED")
        return data
