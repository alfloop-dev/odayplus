"""Run the fixed manual provider outside SQL roots under the owner's guard."""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
)
from modules.external_data.application.native_parser_artifacts import VerifiedNativeParserArtifact
from modules.external_data.application.native_parser_output import (
    validate_native_manual_parser_output,
)

_CHILD = Path(__file__).with_name("native_manual_parser_child.py").resolve()


class NativeManualParserChildRunner(NativeIntakeProcessRunner):
    def _command(self, credential_fd):
        if credential_fd is not None:
            raise NativeIORejected("NATIVE_IO_CREDENTIAL_CHANNEL_REJECTED")
        return [sys.executable, "-I", "-S", str(_CHILD)]


class NativeManualParserProcess:
    def parse(self, data: bytes, *, lineage: provider.ManualParserLineage,
            artifact: VerifiedNativeParserArtifact, original_fields: dict, deadline: float, guard):
        if (not isinstance(data, bytes) or not 1 <= len(data) <= provider.MAX_DOCUMENT_BYTES
                or not isinstance(lineage, provider.ManualParserLineage)
                or not isinstance(artifact, VerifiedNativeParserArtifact)
                or artifact.package_name != provider.PACKAGE_NAME
                or artifact.input_schema_version != provider.INPUT_SCHEMA_VERSION
                or artifact.output_schema_version != provider.OUTPUT_SCHEMA_VERSION
                or artifact.source_id != lineage.source_id):
            raise NativeIORejected("NATIVE_IO_PARSER_BINDING_REJECTED")
        provider_sha = dict(artifact.installed_sha256).get("provider.py")
        if not isinstance(provider_sha, str) or len(provider_sha) != 64:
            raise NativeIORejected("NATIVE_IO_PARSER_BINDING_REJECTED")
        nonce = str(uuid4())
        request = {"operation": "parse_manual", "nonce": nonce, "provider_sha256": provider_sha,
            "document_base64": base64.b64encode(data).decode("ascii"), "lineage": asdict(lineage)}
        reply = NativeManualParserChildRunner().run(request, deadline=deadline, guard=guard)
        if (set(reply) != {"nonce", "provider_sha256", "payload"} or reply["nonce"] != nonce
                or reply["provider_sha256"] != provider_sha or not isinstance(reply["payload"], dict)):
            raise NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")
        guard()
        encoded = json.dumps(reply["payload"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        result = validate_native_manual_parser_output(encoded, artifact=artifact, lineage=lineage,
            original_fields=original_fields)
        guard()
        return result
