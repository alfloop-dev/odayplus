"""Reject unverifiable native provider metadata and altered snapshot bytes."""

import hashlib
from dataclasses import replace

import pytest

from modules.external_data.application.native_snapshot_transport import (
    NativeBucketMetadata,
    NativeSnapshotObjectMetadata,
    NativeSnapshotProofRejected,
    NativeVerifiedSnapshot,
    native_snapshot_object_key,
)
from modules.listing.application.native_intake_retrieval_policy import NativeSnapshotBinding

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
JOB = "33333333-3333-4333-8333-333333333333"
CAPTURE = "44444444-4444-4444-8444-444444444444"
KEY = "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw"
RAW = b"<html>trusted</html>"


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", "native-test-snapshots")
    return NativeSnapshotBinding(TENANT, "native-test-snapshots", KEY, "TW_ONLY", "STANDARD")


def object_response(binding, *, key=None):
    return {
        "kind": "storage#object", "bucket": binding.bucket,
        "name": key or native_snapshot_object_key(TENANT, JOB, 1, 2, CAPTURE),
        "generation": "7", "size": str(len(RAW)), "contentType": "text/html",
        "kmsKeyName": KEY + "/cryptoKeyVersions/3",
        "metadata": {"sha256": "untrusted-user-metadata"},
    }


def test_name_allowlist_and_actual_provider_location_are_both_required(binding):
    response = {"kind": "storage#bucket", "name": binding.bucket,
                "location": "ASIA-EAST1", "locationType": "region"}
    metadata = NativeBucketMetadata.from_provider(response, binding)
    assert metadata.location == "ASIA-EAST1" and metadata.location_type == "region"
    with pytest.raises(NativeSnapshotProofRejected):
        NativeBucketMetadata.from_provider({**response, "location": "US-CENTRAL1"}, binding)
    with pytest.raises(NativeSnapshotProofRejected):
        NativeBucketMetadata.from_provider({**response, "locationType": "dual-region"}, binding)
    with pytest.raises(NativeSnapshotProofRejected):
        NativeBucketMetadata.from_provider({**response, "name": "another-bucket"}, binding)


def test_provider_region_does_not_substitute_for_explicit_bucket_allowlist(binding, monkeypatch):
    monkeypatch.setenv("ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY", "different-bucket")
    response = {"kind": "storage#bucket", "name": binding.bucket,
                "location": "ASIA-EAST1", "locationType": "region"}
    with pytest.raises(NativeSnapshotProofRejected) as error:
        NativeBucketMetadata.from_provider(response, binding)
    assert str(error.value) == "NATIVE_RETRIEVAL_SNAPSHOT_PROOF_REJECTED"


def test_no_native_dr_permission_is_inferred_from_a_valid_taiwan_bucket(binding):
    response = {"kind": "storage#bucket", "name": binding.bucket,
                "location": "ASIA-EAST1", "locationType": "region"}
    with pytest.raises(NativeSnapshotProofRejected):
        NativeBucketMetadata.from_provider(response, replace(binding, residency_mode="APPROVED_APAC_DR"))


@pytest.mark.parametrize("field,value", [
    ("generation", "0"), ("generation", True), ("generation", "07"),
    ("generation", str(2**63)), ("generation", None),
    ("size", "512001"), ("size", "-1"), ("size", False),
    ("contentType", "application/json"), ("contentType", {"type": "text/html"}),
    ("contentEncoding", "gzip"), ("kmsKeyName", None),
    ("kmsKeyName", KEY), ("kmsKeyName", KEY + "-other/cryptoKeyVersions/3"),
])
def test_invalid_provider_object_cannot_become_snapshot_provenance(binding, field, value):
    response = object_response(binding)
    response[field] = value
    with pytest.raises(NativeSnapshotProofRejected) as error:
        NativeSnapshotObjectMetadata.from_provider(response, binding, response["name"])
    assert str(error.value) == "NATIVE_RETRIEVAL_SNAPSHOT_PROOF_REJECTED"


def test_generation_pin_and_actual_kms_version_are_checked(binding):
    response = object_response(binding)
    pinned = replace(binding, kms_key_name=KEY + "/cryptoKeyVersions/3")
    metadata = NativeSnapshotObjectMetadata.from_provider(response, pinned, response["name"], expected_generation=7)
    assert metadata.generation == 7 and metadata.kms_key_version_name == pinned.kms_key_name
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider(response, pinned, response["name"], expected_generation=8)
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider(
            response, replace(pinned, kms_key_name=KEY + "/cryptoKeyVersions/4"), response["name"],
        )


def test_old_attempt_and_foreign_tenant_object_keys_are_not_adopted(binding):
    own = native_snapshot_object_key(TENANT, JOB, 1, 2, CAPTURE)
    later = native_snapshot_object_key(TENANT, JOB, 2, 3, CAPTURE)
    foreign = native_snapshot_object_key(OTHER, JOB, 1, 2, CAPTURE)
    assert len({own, later, foreign}) == 3
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider(object_response(binding, key=own), binding, later)
    with pytest.raises(NativeSnapshotProofRejected):
        NativeSnapshotObjectMetadata.from_provider(object_response(binding, key=foreign), binding, foreign)


def test_actual_download_bytes_win_over_provider_custom_hash_metadata(binding):
    response = object_response(binding)
    corrupt = RAW.replace(b"trusted", b"corrupt")
    response["metadata"]["sha256"] = hashlib.sha256(corrupt).hexdigest()
    metadata = NativeSnapshotObjectMetadata.from_provider(response, binding, response["name"])
    expected = hashlib.sha256(RAW).hexdigest()
    verified = NativeVerifiedSnapshot.from_download(RAW, metadata, expected_sha256=expected)
    assert verified.content_sha256 == expected
    assert verified.metadata.generation == 7
    with pytest.raises(NativeSnapshotProofRejected):
        NativeVerifiedSnapshot.from_download(corrupt, metadata, expected_sha256=expected)
