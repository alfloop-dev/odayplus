"""Exercise the fixed GCS REST protocol against in-process provider doubles."""

import base64
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from modules.external_data.application import native_intake_io_child as child

BUCKET = "test-native-snapshots"
KEY = "tenants/11111111-1111-4111-8111-111111111111/native-intake-retrieval/33333333-3333-4333-8333-333333333333/1/2/44444444-4444-4444-8444-444444444444/raw"
KMS = "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw"


class Response:
    def __init__(self, status, body, headers):
        self.status, self.body, self.headers = status, body, headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        part, self.body = self.body[:size], self.body[size:]
        return part


def call(monkeypatch, command, *, status=200, body=None, headers=None):
    operations = []
    response = Response(status, json.dumps(body or {}).encode() if isinstance(body, dict) or body is None else body, headers or {})
    connection = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda v: None),
        request=lambda *a, **k: operations.append((a, k)), getresponse=lambda: response, close=lambda: None)
    connections = []

    def connect(*args):
        connections.append(args[:3])
        return connection

    monkeypatch.setattr(child, "_connection", connect)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"test-private-storage-only")
    os.close(write_fd)
    try:
        result = child.gcs_operation({"bucket": BUCKET, "timeout_seconds": 1, **command}, time.monotonic() + 1, read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    assert connections == [("storage.googleapis.com", 443, True)]
    assert len(operations) == 1
    args, kwargs = operations[0]
    assert kwargs["headers"]["Authorization"] == "Bearer test-private-storage-only"
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert "Cookie" not in kwargs["headers"]
    return result, args, kwargs


def test_upload_is_create_only_and_explicit_cmek_with_only_projected_provider_proof(monkeypatch):
    metadata = {"kind": "storage#object", "bucket": BUCKET, "name": KEY, "generation": "7", "size": "3",
        "contentType": "text/html", "kmsKeyName": KMS + "/cryptoKeyVersions/2", "mediaLink": "secret-link",
        "metadata": {"hash": "untrusted"}, "owner": "private-provider-owner"}
    result, args, kwargs = call(monkeypatch, {"operation": "gcs_upload", "key": KEY,
        "body_base64": base64.b64encode(b"raw").decode(), "media_type": "text/html", "kms_key_name": KMS}, body=metadata)
    assert args[0] == "POST"
    target = urlsplit(args[1])
    assert target.path == "/upload/storage/v1/b/" + BUCKET + "/o"
    assert parse_qs(target.query) == {"uploadType": ["media"], "name": [KEY], "ifGenerationMatch": ["0"], "kmsKeyName": [KMS]}
    assert kwargs["body"] == b"raw" and kwargs["headers"]["Content-Type"] == "text/html"
    assert set(result) == {"kind", "bucket", "name", "generation", "size", "contentType", "kmsKeyName"}
    assert "secret-link" not in json.dumps(result) and "untrusted" not in json.dumps(result)


@pytest.mark.parametrize("operation,generation,method,expected_query", [
    ("gcs_head", None, "GET", {}),
    ("gcs_head", 7, "GET", {"generation": ["7"]}),
    ("gcs_download", 7, "GET", {"generation": ["7"], "alt": ["media"]}),
    ("gcs_delete", 7, "DELETE", {"generation": ["7"], "ifGenerationMatch": ["7"]}),
])
def test_reconciliation_read_download_and_cleanup_are_exact_key_and_generation(monkeypatch, operation, generation, method, expected_query):
    body = b"raw" if operation == "gcs_download" else {}
    result, args, _ = call(monkeypatch, {"operation": operation, "key": KEY, "generation": generation},
                          status=204 if operation == "gcs_delete" else 200, body=body,
                          headers={"x-goog-generation": "7"})
    target = urlsplit(args[1])
    assert args[0] == method
    assert unquote(target.path) == "/storage/v1/b/" + BUCKET + "/o/" + KEY
    assert parse_qs(target.query) == expected_query
    assert result == {"body_base64": base64.b64encode(b"raw").decode()} if operation == "gcs_download" else result == {}


def test_bucket_provider_location_is_read_from_fixed_bucket_endpoint(monkeypatch):
    result, args, _ = call(monkeypatch, {"operation": "gcs_bucket"}, body={"kind": "storage#bucket", "name": BUCKET,
        "location": "ASIA-EAST1", "locationType": "region", "labels": {"secret": "private"}})
    assert args == ("GET", "/storage/v1/b/" + BUCKET)
    assert result == {"kind": "storage#bucket", "name": BUCKET, "location": "ASIA-EAST1", "locationType": "region"}


def test_actual_download_generation_header_cannot_contradict_selected_generation(monkeypatch):
    with pytest.raises(child.ChildRejected) as error:
        call(monkeypatch, {"operation": "gcs_download", "key": KEY, "generation": 7}, body=b"raw",
             headers={"x-goog-generation": "8"})
    assert error.value.code == "NATIVE_GCS_GENERATION_REJECTED"


@pytest.mark.parametrize("operation", ["gcs_download", "gcs_delete"])
@pytest.mark.parametrize("generation", [None, True, 0, -1, "7", 2**63])
def test_invalid_generations_do_not_open_credential_or_provider_connection(monkeypatch, operation, generation):
    monkeypatch.setattr(child, "_storage_token", lambda *a: pytest.fail("no credential read"))
    monkeypatch.setattr(child, "_connection", lambda *a: pytest.fail("no connection"))
    with pytest.raises(child.ChildRejected) as error:
        child.gcs_operation({"operation": operation, "bucket": BUCKET, "timeout_seconds": 1, "key": KEY,
                             "generation": generation}, time.monotonic() + 1, None)
    assert error.value.code == "NATIVE_GCS_GENERATION_REJECTED"


@pytest.mark.parametrize("status", [301, 401, 403, 404, 412, 429, 500])
def test_provider_redirect_conflict_and_unknown_errors_are_not_hidden_retries(monkeypatch, status):
    with pytest.raises(child.ChildRejected) as error:
        call(monkeypatch, {"operation": "gcs_head", "key": KEY, "generation": None}, status=status,
             body=b"secret-token-and-provider-details")
    assert error.value.code == "NATIVE_GCS_HTTP_REJECTED" and error.value.status == status
    assert "secret" not in str(error.value)
