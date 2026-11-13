"""Human JSON evidence cannot adopt retrieval objects or unproved provider data."""

import base64
import hashlib
import time
from dataclasses import replace

import pytest

from modules.external_data.application.native_assisted_snapshot_process_transport import (
    ASSISTED_STORAGE_CREDENTIAL_ENV,
    NativeAssistedSnapshotProcessTransport,
    native_assisted_snapshot_credential,
)
from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotObjectMetadata,
    NativeAssistedSnapshotProofRejected,
    NativeVerifiedAssistedSnapshot,
    native_assisted_snapshot_object_key,
)
from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_snapshot_process_transport import (
    NativeSnapshotProcessTransport,
)
from modules.external_data.application.native_snapshot_transport import (
    NativeSnapshotObjectMetadata,
    NativeSnapshotProofRejected,
    native_snapshot_object_key,
)
from modules.listing.application.native_intake_retrieval_policy import NativeSnapshotBinding

TENANT = "11111111-1111-4111-8111-111111111111"
JOB = "33333333-3333-4333-8333-333333333333"
CAPTURE = "44444444-4444-4444-8444-444444444444"
KMS = "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw"
RAW = b'{"schema_version":1,"fields":{"address":"human evidence"}}'


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", "native-test-snapshots")
    return NativeSnapshotBinding(TENANT, "native-test-snapshots", KMS, "TW_ONLY", "STANDARD")


def key():
    return native_assisted_snapshot_object_key(TENANT, JOB, 1, 2, CAPTURE)


def metadata(binding):
    return {"kind": "storage#object", "bucket": binding.bucket, "name": key(), "generation": "7",
        "size": str(len(RAW)), "contentType": "application/json", "kmsKeyName": KMS + "/cryptoKeyVersions/3"}


@pytest.mark.parametrize("field,value", [
    ("kind", "storage#bucket"), ("bucket", "different-bucket"), ("name", "other-key"),
    ("generation", None), ("generation", True), ("generation", "07"), ("generation", "0"),
    ("generation", str(2**63)), ("size", "0"), ("size", False), ("size", "512001"),
    ("contentType", "text/html"), ("contentType", "application/xhtml+xml"),
    ("contentType", {"type": "application/json"}), ("contentEncoding", "gzip"),
    ("kmsKeyName", KMS), ("kmsKeyName", KMS + "-foreign/cryptoKeyVersions/3"),
])
def test_metadata_refuses_nonmanual_or_unverified_objects(binding, field, value):
    with pytest.raises(NativeAssistedSnapshotProofRejected, match="^NATIVE_ASSISTED_SNAPSHOT_PROOF_REJECTED$"):
        NativeAssistedSnapshotObjectMetadata.from_provider({**metadata(binding), field: value}, binding, key())


def test_json_provider_proof_requires_actual_generation_and_exact_cmek_version(binding):
    pinned = replace(binding, kms_key_name=KMS + "/cryptoKeyVersions/3")
    proof = NativeAssistedSnapshotObjectMetadata.from_provider(metadata(binding), pinned, key(), expected_generation=7)
    assert proof.media_type == "application/json" and proof.uri.endswith(key())
    assert proof.generation == 7 and proof.kms_key_version_name == pinned.kms_key_name
    with pytest.raises(NativeAssistedSnapshotProofRejected):
        NativeAssistedSnapshotObjectMetadata.from_provider(metadata(binding), pinned, key(), expected_generation=8)
    with pytest.raises(NativeAssistedSnapshotProofRejected):
        NativeAssistedSnapshotObjectMetadata.from_provider(metadata(binding),
            replace(pinned, kms_key_name=KMS + "/cryptoKeyVersions/4"), key())


def test_purpose_namespaces_cannot_adopt_each_others_objects(binding):
    retrieval = native_snapshot_object_key(TENANT, JOB, 1, 2, CAPTURE)
    with pytest.raises(NativeAssistedSnapshotProofRejected):
        NativeAssistedSnapshotObjectMetadata.from_provider(
            {**metadata(binding), "name": retrieval}, binding, retrieval)
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider({**metadata(binding), "contentType": "text/html"}, binding, key())
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider({**metadata(binding), "name": retrieval}, binding, retrieval)


@pytest.mark.parametrize("tenant,generation,fence,capture", [
    ("22222222-2222-4222-8222-222222222222", 1, 2, CAPTURE),
    (TENANT, 2, 2, CAPTURE), (TENANT, 1, 3, CAPTURE),
    (TENANT, 1, 2, "55555555-5555-4555-8555-555555555555"),
])
def test_exact_provider_name_cannot_adopt_other_attempts(binding, tenant, generation, fence, capture):
    other = native_assisted_snapshot_object_key(tenant, JOB, generation, fence, capture)
    with pytest.raises(NativeAssistedSnapshotProofRejected):
        NativeAssistedSnapshotObjectMetadata.from_provider({**metadata(binding), "name": other}, binding, key())
    if tenant != TENANT:
        with pytest.raises(NativeAssistedSnapshotProofRejected):
            NativeAssistedSnapshotObjectMetadata.from_provider({**metadata(binding), "name": other}, binding, other)


def test_actual_download_sha_and_size_win_over_custom_metadata(binding):
    expected = hashlib.sha256(RAW).hexdigest()
    proof = NativeAssistedSnapshotObjectMetadata.from_provider(
        {**metadata(binding), "metadata": {"sha256": "forged"}}, binding, key())
    assert NativeVerifiedAssistedSnapshot.from_download(RAW, proof, expected_sha256=expected).content_sha256 == expected
    for data in (RAW.replace(b"human", b"other"), RAW[:-1]):
        with pytest.raises(NativeAssistedSnapshotProofRejected):
            NativeVerifiedAssistedSnapshot.from_download(data, proof, expected_sha256=expected)


@pytest.mark.parametrize("credential", [None, "", "bad\nvalue", "has space", "非ASCII", "x" * 8193])
def test_no_credential_fallback_to_retrieval_audit_or_adc(monkeypatch, credential):
    for name in ("ODP_NATIVE_INTAKE_SNAPSHOT_GCS_TOKEN", "ODP_AUDIT_GCS_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN",
                 "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.setenv(name, "other-credential")
    if credential is None:
        monkeypatch.delenv(ASSISTED_STORAGE_CREDENTIAL_ENV, raising=False)
    else:
        monkeypatch.setenv(ASSISTED_STORAGE_CREDENTIAL_ENV, credential)
    with pytest.raises(NativeIORejected, match="NATIVE_GCS_ASSISTED_CREDENTIAL_UNAVAILABLE"):
        native_assisted_snapshot_credential()


class FakeRunner:
    def __init__(self, binding):
        self.binding, self.calls = binding, []

    def run(self, request, **kwargs):
        self.calls.append((request, kwargs))
        operation = request["operation"]
        if operation == "gcs_assisted_bucket":
            return {"kind": "storage#bucket", "name": self.binding.bucket,
                    "location": "ASIA-EAST1", "locationType": "region"}
        if operation in {"gcs_assisted_upload", "gcs_assisted_head"}:
            return metadata(self.binding)
        if operation == "gcs_assisted_download":
            return {"body_base64": base64.b64encode(RAW).decode("ascii")}
        if operation == "gcs_assisted_delete":
            return {}
        pytest.fail("Unknown operation")


def test_transport_has_only_fixed_json_operations_and_dispatch_time_credential(binding, monkeypatch):
    calls, fake = [], FakeRunner(binding)
    transport = NativeAssistedSnapshotProcessTransport(guard=lambda: calls.append("guard"), runner=fake)
    monkeypatch.setenv(ASSISTED_STORAGE_CREDENTIAL_ENV, "dedicated-first")
    deadline = time.monotonic() + 1
    transport.bucket_info(binding, deadline=deadline)
    monkeypatch.setenv(ASSISTED_STORAGE_CREDENTIAL_ENV, "dedicated-current")
    uploaded = transport.upload_with_kms_if_absent(binding, key(), RAW, "application/json", deadline=deadline)
    assert uploaded.generation == 7
    transport.head_version(binding, key(), None, deadline=deadline)
    transport.head_version(binding, key(), 7, deadline=deadline)
    assert transport.download_version_bounded(binding, key(), 7, deadline=deadline) == RAW
    transport.delete_exact_generation(binding, key(), 7, deadline=deadline)
    assert len(fake.calls) == len(calls) == 6
    assert [c[0]["operation"] for c in fake.calls] == ["gcs_assisted_bucket", "gcs_assisted_upload",
        "gcs_assisted_head", "gcs_assisted_head", "gcs_assisted_download", "gcs_assisted_delete"]
    assert [c[1]["credential"] for c in fake.calls] == ["dedicated-first"] + ["dedicated-current"] * 5
    assert all(c[1]["deadline"] == deadline and c[1]["guard"] is transport.guard for c in fake.calls)
    assert fake.calls[1][0]["kms_key_name"] == KMS and "dedicated" not in str(fake.calls[1][0])


@pytest.mark.parametrize("media", ["text/html", "application/xhtml+xml", "application/json;charset=utf-8", None, {}])
def test_json_parent_rejects_other_media_before_dispatch(binding, media):
    fake = FakeRunner(binding)
    with pytest.raises(NativeIORejected):
        NativeAssistedSnapshotProcessTransport(guard=lambda: None, runner=fake).upload_with_kms_if_absent(
            binding, key(), RAW, media, deadline=time.monotonic() + 1)
    assert not fake.calls


def test_retrieval_parent_still_rejects_json_before_dispatch(binding):
    fake = FakeRunner(binding)
    with pytest.raises(NativeIORejected):
        NativeSnapshotProcessTransport(guard=lambda: None, runner=fake).upload_with_kms_if_absent(
            binding, native_snapshot_object_key(TENANT, JOB, 1, 2, CAPTURE), RAW, "application/json",
            deadline=time.monotonic() + 1)
    assert not fake.calls


@pytest.mark.parametrize("generation", [None, True, 0, -1, "7", 2**63])
@pytest.mark.parametrize("operation", ["download_version_bounded", "delete_exact_generation"])
def test_pinned_operations_never_dispatch_invalid_generation(binding, generation, operation):
    fake = FakeRunner(binding)
    with pytest.raises(NativeIORejected):
        getattr(NativeAssistedSnapshotProcessTransport(guard=lambda: None, runner=fake), operation)(
            binding, key(), generation, deadline=time.monotonic() + 1)
    assert not fake.calls
