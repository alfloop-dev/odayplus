"""Parser reads retain original generations, guards and credential purpose."""
import base64
import time

import pytest

from modules.external_data.application import native_parser_snapshot_transport as transport
from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotProofRejected,
)
from modules.external_data.application.native_intake_process import NativeIORejected
from tests.security.test_native_assisted_snapshot_transport import (
    RAW,
    key,
    metadata,
)
from tests.security.test_native_assisted_snapshot_transport import (
    binding as binding,
)


class RecordingRunner:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def run(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return self.reply


@pytest.mark.parametrize("method,reply", [("bucket_info", "bucket"), ("head_version", "object"),
    ("download_version_bounded", "body")])
def test_reads_use_own_current_credential_and_exact_original_deadline(binding, monkeypatch, method, reply):
    monkeypatch.setenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, "synthetic-parser-token")
    monkeypatch.setenv("ODP_NATIVE_INTAKE_ASSISTED_SNAPSHOT_GCS_TOKEN", "unrelated-assisted-token")
    raw = {"bucket": {"kind": "storage#bucket", "name": binding.bucket, "location": "ASIA-EAST1",
        "locationType": "region"}, "object": metadata(binding),
        "body": {"body_base64": base64.b64encode(RAW).decode()}}[reply]
    runner = RecordingRunner(raw)
    guards = []
    reader = transport.NativeParserSnapshotTransport(guard=lambda: guards.append(True), runner=runner)
    deadline = time.monotonic() + 2
    args = () if method == "bucket_info" else (key(), 7)
    result = getattr(reader, method)(binding, *args, deadline=deadline)
    if method == "download_version_bounded":
        assert result == RAW
    elif method == "head_version":
        assert result.generation == 7
    else:
        assert result.location == "ASIA-EAST1"
    request, options = runner.calls[0]
    assert request["operation"] == {"bucket_info": "gcs_assisted_bucket", "head_version": "gcs_assisted_head",
        "download_version_bounded": "gcs_assisted_download"}[method]
    if args:
        assert request["generation"] == 7 and request["key"] == key()
    assert options["deadline"] is deadline and options["credential"] == "synthetic-parser-token"
    assert len(guards) == 2
    monkeypatch.setenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, "rotated-parser-token")
    getattr(reader, method)(binding, *args, deadline=deadline)
    assert runner.calls[-1][1]["credential"] == "rotated-parser-token"


@pytest.mark.parametrize("method", ["head_version", "download_version_bounded"])
@pytest.mark.parametrize("generation", [None, True, 0, -1, "7", 2**63])
def test_missing_or_wrong_generation_cannot_become_latest_object_read(binding, method, generation):
    runner = RecordingRunner({})
    reader = transport.NativeParserSnapshotTransport(guard=lambda: None, runner=runner)
    with pytest.raises(NativeIORejected, match="^NATIVE_GCS_GENERATION_REJECTED$"):
        getattr(reader, method)(binding, key(), generation, deadline=time.monotonic() + 2)
    assert runner.calls == []


def test_assisted_token_cannot_substitute_for_missing_parser_credential(binding, monkeypatch):
    monkeypatch.delenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, raising=False)
    monkeypatch.setenv("ODP_NATIVE_INTAKE_ASSISTED_SNAPSHOT_GCS_TOKEN", "unrelated-assisted-token")
    runner = RecordingRunner({})
    reader = transport.NativeParserSnapshotTransport(guard=lambda: None, runner=runner)
    with pytest.raises(NativeIORejected, match="^NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE$"):
        reader.bucket_info(binding, deadline=time.monotonic() + 2)
    assert runner.calls == []


@pytest.mark.parametrize("operation", ["gcs_assisted_upload", "gcs_assisted_delete", "http_hop", "gcs_download"])
def test_parser_transport_has_no_mutation_or_source_http_capability(binding, operation):
    runner = RecordingRunner({})
    reader = transport.NativeParserSnapshotTransport(guard=lambda: None, runner=runner)
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PROTOCOL_REJECTED$"):
        reader._run(binding, operation, time.monotonic() + 2)
    assert runner.calls == []
    assert not hasattr(reader, "delete_exact_generation") and not hasattr(reader, "upload_with_kms_if_absent")


def test_provider_cannot_return_a_different_object_generation(binding, monkeypatch):
    monkeypatch.setenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, "synthetic-parser-token")
    runner = RecordingRunner({**metadata(binding), "generation": "8"})
    reader = transport.NativeParserSnapshotTransport(guard=lambda: None, runner=runner)
    with pytest.raises(NativeAssistedSnapshotProofRejected):
        reader.head_version(binding, key(), 7, deadline=time.monotonic() + 2)


@pytest.mark.parametrize("raw", [{}, {"body_base64": "%%%"}, {"body_base64": ""},
    {"body_base64": "eA==", "metadata": "untrusted"}, {"body_base64": None}])
def test_download_requires_closed_bounded_actual_bytes(binding, monkeypatch, raw):
    monkeypatch.setenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, "synthetic-parser-token")
    runner = RecordingRunner(raw)
    reader = transport.NativeParserSnapshotTransport(guard=lambda: None, runner=runner)
    with pytest.raises(NativeIORejected):
        reader.download_version_bounded(binding, key(), 7, deadline=time.monotonic() + 2)


def test_authority_loss_on_return_prevents_exposing_provider_bytes(binding, monkeypatch):
    monkeypatch.setenv(transport.PARSER_STORAGE_CREDENTIAL_ENV, "synthetic-parser-token")
    runner = RecordingRunner({"body_base64": base64.b64encode(RAW).decode()})
    def guard():
        if runner.calls:
            raise NativeIORejected("NATIVE_IO_AUTHORITY_LOST")
    reader = transport.NativeParserSnapshotTransport(guard=guard, runner=runner)
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_AUTHORITY_LOST$"):
        reader.download_version_bounded(binding, key(), 7, deadline=time.monotonic() + 2)
