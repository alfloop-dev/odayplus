"""Give synthetic storage tests one actual owned process per IO invocation.

Provider data remains the existing explicit synthetic seam. This fixed child
performs no provider request; only its actual wait/group disappearance can
supply lifecycle facts. No boolean, child JSON receipt or past row is forged.
The real parser child is unchanged and receives no extra process here.
"""
import sys
from pathlib import Path

from modules.external_data.application.native_intake_process import (
    NativeIntakeProcessRunner,
    NativeIORejected,
)
from modules.listing.application.native_intake_parser_io import NativeIntakeParserIO


class _SyntheticStorageProcess(NativeIntakeProcessRunner):
    def _command(self, credential_fd):
        if credential_fd is not None:
            raise NativeIORejected("NATIVE_IO_CREDENTIAL_CHANNEL_REJECTED")
        child = Path(__file__).with_name("_native_parser_lifecycle_child.py").resolve()
        return [sys.executable, "-I", "-S", str(child)]


def install_synthetic_storage_lifecycle(monkeypatch, storage):
    original = NativeIntakeParserIO._invoke

    def invoke(io, operation, context, arguments, deadline, data):
        if (operation in {"gcs_bucket", "gcs_head", "gcs_download"} and io.storage is storage
                and getattr(getattr(io.owner.engine, "_local", None), "connection", None) is None):
            _SyntheticStorageProcess().run({"operation": "synthetic_parser_storage"},
                deadline=deadline, guard=io.leases.guard)
        return original(io, operation, context, arguments, deadline, data)

    monkeypatch.setattr(NativeIntakeParserIO, "_invoke", invoke)
