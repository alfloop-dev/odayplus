"""Exercise manual JSON's compiled child protocol without contacting a provider."""

import base64
import json
import os
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from modules.external_data.application import native_intake_io_child as child
from modules.external_data.application.native_intake_process import NativeIORejected
from tests.security.test_native_intake_retrieval_gcs_process import Response
from tests.security.test_native_intake_retrieval_process import runner

BUCKET = "native-test-snapshots"
KEY = "tenants/11111111-1111-4111-8111-111111111111/native-intake-assisted-entry/33333333-3333-4333-8333-333333333333/1/2/44444444-4444-4444-8444-444444444444/raw"
KMS = "projects/test-project/locations/asia-east1/keyRings/snapshots/cryptoKeys/raw"
RAW = b'{"fields":{"address":"human evidence"}}'


def call(monkeypatch, command, *, status=200, body=None, headers=None):
    calls = []
    raw = json.dumps(body or {}).encode() if isinstance(body, dict) or body is None else body
    response = Response(status, raw, headers or {})
    connection = SimpleNamespace(sock=SimpleNamespace(settimeout=lambda value: None),
        request=lambda *a, **k: calls.append((a, k)), getresponse=lambda: response, close=lambda: None)
    connections = []

    def connect(*args):
        connections.append(args[:3])
        return connection

    monkeypatch.setattr(child, "_connection", connect)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"dedicated-manual-token")
    os.close(write_fd)
    try:
        result = child.assisted_gcs_operation({"bucket": BUCKET, "timeout_seconds": 1, **command},
                                              time.monotonic() + 1, read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
    assert connections == [("storage.googleapis.com", 443, True)]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer dedicated-manual-token"
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert "Cookie" not in kwargs["headers"]
    return result, args, kwargs


def test_json_upload_is_create_only_and_projects_only_actual_metadata(monkeypatch):
    metadata = {"kind": "storage#object", "name": KEY, "bucket": BUCKET, "generation": "7",
        "size": str(len(RAW)), "contentType": "application/json", "kmsKeyName": KMS + "/cryptoKeyVersions/3",
        "mediaLink": "sensitive-provider-link", "metadata": {"sha256": "forged"}}
    result, args, kwargs = call(monkeypatch, {"operation": "gcs_assisted_upload", "key": KEY,
        "body_base64": base64.b64encode(RAW).decode("ascii"), "media_type": "application/json",
        "kms_key_name": KMS}, body=metadata)
    target = urlsplit(args[1])
    assert args[0] == "POST" and target.path == "/upload/storage/v1/b/" + BUCKET + "/o"
    assert parse_qs(target.query) == {"uploadType": ["media"], "name": [KEY],
        "ifGenerationMatch": ["0"], "kmsKeyName": [KMS]}
    assert kwargs["body"] == RAW and kwargs["headers"]["Content-Type"] == "application/json"
    assert set(result) == {"kind", "name", "bucket", "generation", "size", "contentType", "kmsKeyName"}
    assert "sensitive-provider-link" not in json.dumps(result) and "forged" not in json.dumps(result)


@pytest.mark.parametrize("operation,generation,method,query", [
    ("gcs_assisted_head", None, "GET", {}), ("gcs_assisted_head", 7, "GET", {"generation": ["7"]}),
    ("gcs_assisted_download", 7, "GET", {"generation": ["7"], "alt": ["media"]}),
    ("gcs_assisted_delete", 7, "DELETE", {"generation": ["7"], "ifGenerationMatch": ["7"]}),
])
def test_exact_key_and_pinned_generation_for_reconciliation_and_cleanup(monkeypatch, operation, generation, method, query):
    result, args, _ = call(monkeypatch, {"operation": operation, "key": KEY, "generation": generation},
        body=RAW if operation == "gcs_assisted_download" else {},
        status=204 if operation == "gcs_assisted_delete" else 200, headers={"x-goog-generation": "7"})
    target = urlsplit(args[1])
    assert args[0] == method and parse_qs(target.query) == query
    assert unquote(target.path) == "/storage/v1/b/" + BUCKET + "/o/" + KEY
    assert result == ({"body_base64": base64.b64encode(RAW).decode("ascii")}
                      if operation == "gcs_assisted_download" else {})


def test_actual_bucket_region_endpoint_and_projection(monkeypatch):
    result, args, _ = call(monkeypatch, {"operation": "gcs_assisted_bucket"}, body={
        "kind": "storage#bucket", "name": BUCKET, "location": "ASIA-EAST1", "locationType": "region",
        "labels": {"secret": "private"}})
    assert args == ("GET", "/storage/v1/b/" + BUCKET)
    assert set(result) == {"kind", "name", "location", "locationType"}


@pytest.mark.parametrize("operation", ["gcs_assisted_download", "gcs_assisted_delete"])
@pytest.mark.parametrize("generation", [None, True, 0, -1, "7", 2**63])
def test_invalid_generation_is_rejected_before_secret_or_network(monkeypatch, operation, generation):
    monkeypatch.setattr(child, "_storage_token", lambda *args: pytest.fail("credential read"))
    monkeypatch.setattr(child, "_connection", lambda *args: pytest.fail("network opened"))
    with pytest.raises(child.ChildRejected) as error:
        child.assisted_gcs_operation({"operation": operation, "bucket": BUCKET, "timeout_seconds": 1,
            "key": KEY, "generation": generation}, time.monotonic() + 1, None)
    assert error.value.code == "NATIVE_GCS_GENERATION_REJECTED"


@pytest.mark.parametrize("value", ["text/html", "application/xhtml+xml", "application/json;charset=utf-8", {}, None])
def test_assisted_child_rejects_other_media_before_secret_or_network(monkeypatch, value):
    monkeypatch.setattr(child, "_storage_token", lambda *args: pytest.fail("credential read"))
    monkeypatch.setattr(child, "_connection", lambda *args: pytest.fail("network opened"))
    with pytest.raises(child.ChildRejected):
        child.assisted_gcs_operation({"operation": "gcs_assisted_upload", "bucket": BUCKET, "timeout_seconds": 1,
            "key": KEY, "body_base64": base64.b64encode(RAW).decode(), "media_type": value,
            "kms_key_name": KMS}, time.monotonic() + 1, None)


@pytest.mark.parametrize("mutate", [
    lambda r: {**r, "key": KEY.replace("native-intake-assisted-entry", "native-intake-retrieval")},
    lambda r: {**r, "key": KEY.replace("/1/2/", "/01/2/")},
    lambda r: {**r, "key": KEY.replace("/1/2/", "/9223372036854775808/2/")},
    lambda r: {**r, "key": KEY.replace("11111111-1111-4111-8111-111111111111", "00000000-0000-0000-0000-000000000000")},
    lambda r: {**r, "key": KEY + "/extra"}, lambda r: {**r, "arbitrary_media_allowlist": ["text/html"]},
    lambda r: {**r, "endpoint": "http://127.0.0.1/"}, lambda r: {**r, "credential": "secret"},
    lambda r: {**r, "operation": "gcs_assisted_arbitrary"},
])
def test_closed_compiled_request_refuses_foreign_keys_and_caller_capabilities(monkeypatch, mutate):
    monkeypatch.setattr(child, "_storage_token", lambda *args: pytest.fail("credential read"))
    monkeypatch.setattr(child, "_connection", lambda *args: pytest.fail("network opened"))
    with pytest.raises(child.ChildRejected):
        child.assisted_gcs_operation(mutate({"operation": "gcs_assisted_head", "bucket": BUCKET,
            "timeout_seconds": 1, "key": KEY, "generation": None}), time.monotonic() + 1, None)


@pytest.mark.parametrize("status", [301, 401, 403, 404, 412, 429, 500])
def test_provider_failure_and_unknown_upload_are_never_silent_retry(monkeypatch, status):
    with pytest.raises(child.ChildRejected) as error:
        call(monkeypatch, {"operation": "gcs_assisted_upload", "key": KEY,
            "body_base64": base64.b64encode(RAW).decode(), "media_type": "application/json", "kms_key_name": KMS},
            status=status, body=b"secret-provider-details")
    assert error.value.code == "NATIVE_GCS_HTTP_REJECTED" and error.value.status == status
    assert "secret" not in str(error.value)


def test_real_isolated_child_dispatches_assisted_family_and_private_token(tmp_path):
    # Run actual child.main and its actual operation through the actual bounded
    # process runner. Only the provider connection is an owned in-child double.
    script = f"""
import importlib.util,json,sys
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('child',{child.__file__!r})
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class Response:
 status=200
 def __init__(self): self.body=json.dumps({{'kind':'storage#bucket','name':{BUCKET!r},'location':'ASIA-EAST1','locationType':'region'}}).encode()
 def getheader(self,name,default=None): return default
 def read(self,size): part,self.body=self.body[:size],self.body[size:];return part
def request(method,path,body=None,headers=None):
 assert headers['Authorization']=='Bearer dedicated-assist-only'
c._connection=lambda *args:SimpleNamespace(sock=SimpleNamespace(settimeout=lambda v:None),request=request,getresponse=Response,close=lambda:None)
sys.argv=[sys.argv[0],'--credential-fd',sys.argv[1]]
c.main()
"""
    result = runner(tmp_path, script).run({"operation": "gcs_assisted_bucket", "bucket": BUCKET},
        deadline=time.monotonic() + 2, guard=lambda: None, credential="dedicated-assist-only")
    assert result == {"kind": "storage#bucket", "name": BUCKET, "location": "ASIA-EAST1", "locationType": "region"}


def test_actual_assisted_child_is_killed_and_reaped_at_deadline(tmp_path, monkeypatch):
    pid_file = tmp_path / "owned-assist-pid"
    spawned = []
    real_popen = subprocess.Popen

    def capture_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture_process)
    script = f"""
import importlib.util,pathlib,os,sys,time
pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))
spec=importlib.util.spec_from_file_location('child',{child.__file__!r})
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
def block(*args): time.sleep(30)
c._connection=block
sys.argv=[sys.argv[0],'--credential-fd',sys.argv[1]]
c.main()
"""
    with pytest.raises(NativeIORejected, match="NATIVE_IO_DEADLINE_EXPIRED"):
        runner(tmp_path, script).run({"operation": "gcs_assisted_bucket", "bucket": BUCKET},
            deadline=time.monotonic() + 0.4, guard=lambda: None, credential="dedicated-assist-only")
    assert len(spawned) == 1
    process = spawned[0]
    # The deadline also covers startup, so the child may be killed before it
    # writes a PID file. Verify the actual owned process and completed reaping.
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    if pid_file.exists():
        assert int(pid_file.read_text()) == process.pid
