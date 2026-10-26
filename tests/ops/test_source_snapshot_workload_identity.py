"""Real auth SDK and loopback HTTP controls; no Cloud IAM or production claims."""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request
from uuid import uuid4

import pytest

from modules.external_data.application.source_snapshots import build_source_snapshot_service
from shared.infrastructure.object_store.client import (
    GcsObjectStore,
    InMemoryObjectStore,
    ResidencyDeniedError,
)
from shared.infrastructure.object_store.workload_identity import (
    MetadataObjectStoreCredentials,
    ObjectStoreWorkloadIdentityError,
)

MODES = [
    ("ODP_REQUIRE_LIVE_DATA", "true"), ("ODP_PRODUCT_MODE", "production"),
    ("ODP_PRODUCT_MODE", "live"), ("ODP_DEPLOY_ENV", "production"),
    ("ODP_DEPLOY_ENV", "staging"), ("ODAY_ENV", "production"),
    ("ODP_ENV", "production"), ("APP_ENV", "production"),
    ("ENVIRONMENT", "production"), ("NODE_ENV", "production"),
]


@pytest.fixture(autouse=True)
def isolated_modes(monkeypatch):
    for name in {name for name, _value in MODES} | {
        "ODP_OBJECT_STORE", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_OAUTH_ACCESS_TOKEN",
        "ODP_AUDIT_WORM_GCS_TOKEN", "ODP_RESIDENCY_APPROVED_BUCKETS",
        "ODP_RESIDENCY_APPROVED_BUCKETS_TW_ONLY",
    }:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def protocol(monkeypatch, tmp_path):
    import google.auth
    import google.auth.transport.requests
    import requests

    import shared.infrastructure.object_store.client as client

    state = SimpleNamespace(token=uuid4().hex, token_reads=0, metadata=[], objects={}, storage=[],
                            status=200, redirect_receipts=0, storage_redirect=False, transport=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, body, **headers):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Metadata-Flavor", "Google")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/computeMetadata/v1/"):
                state.metadata.append((self.path, self.headers.get("Metadata-Flavor")))
                if state.status != 200:
                    self.respond(state.status, {"error": state.token}, Location="/must-not-follow")
                elif parsed.path.endswith("/token"):
                    state.token_reads += 1
                    state.token = uuid4().hex
                    self.respond(200, {"access_token": state.token, "expires_in": 3600, "token_type": "Bearer"})
                else:
                    self.respond(200, {"email": "owned-runtime@synthetic-project.iam.gserviceaccount.com",
                                       "scopes": ["https://www.googleapis.com/auth/devstorage.read_write"], "aliases": ["default"]})
                return
            if parsed.path == "/must-not-follow":
                state.redirect_receipts += 1
                self.respond(200, {})
                return
            state.storage.append(("GET", self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + state.token:
                self.respond(403, {})
                return
            if state.storage_redirect:
                self.respond(307, {}, Location="/must-not-follow")
                return
            key = unquote(parsed.path.split("/o/", 1)[1])
            if key not in state.objects:
                self.respond(404, {})
            elif parse_qs(parsed.query).get("alt") == ["media"]:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(state.objects[key])
            else:
                self.respond(200, {"generation": "47", "size": str(len(state.objects[key])), "metadata": {}})

        def do_POST(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            state.storage.append(("POST", self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer " + state.token:
                self.respond(403, {})
                return
            if state.storage_redirect:
                self.respond(307, {}, Location="/must-not-follow")
                return
            if query.get("ifGenerationMatch") != ["0"]:
                self.respond(400, {})
                return
            key = query["name"][0]
            if key in state.objects:
                self.respond(412, {})
                return
            state.objects[key] = self.rfile.read(int(self.headers["Content-Length"]))
            self.respond(200, {"generation": "47"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    session = requests.Session()
    session.trust_env = False
    actual_request = google.auth.transport.requests.Request(session=session)

    def metadata_http(url, **kwargs):
        parsed = urlsplit(url)
        assert parsed.scheme == "http" and parsed.hostname == "metadata.google.internal"
        assert 0 < kwargs["timeout"] <= 10 and kwargs["allow_redirects"] is False
        state.transport.append((url, kwargs["timeout"]))
        return actual_request(url=base + parsed.path + "?" + parsed.query, **kwargs)

    actual_build_opener = client.urllib.request.build_opener

    def storage_opener(*handlers):
        opener = actual_build_opener(*handlers)
        actual_open = opener.open
        def storage_http(request, *, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.scheme == "https" and parsed.hostname == "storage.googleapis.com"
            assert timeout == 10
            translated = Request(base + parsed.path + "?" + parsed.query, data=request.data,
                                 method=request.get_method(), headers=dict(request.header_items()))
            return actual_open(translated, timeout=timeout)
        monkeypatch.setattr(opener, "open", storage_http)
        return opener

    def no_ambient_adc(*_args, **_kwargs):
        pytest.fail("Runtime snapshots must not discover ambient developer/key-file ADC")

    monkeypatch.setattr(google.auth, "default", no_ambient_adc)
    monkeypatch.setattr(google.auth.transport.requests, "Request", lambda: metadata_http)
    monkeypatch.setattr(client.urllib.request, "build_opener", storage_opener)
    monkeypatch.setenv("ODP_ENV", "production")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "must-not-open-key.json"))
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", uuid4().hex)
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", uuid4().hex)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        session.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("name,value", MODES)
def test_every_live_mode_constructs_gcs_without_environment_keys_or_eager_identity_io(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("ODP_OBJECT_STORE", "in_memory")
    def forbidden_eager_token(*_args):
        pytest.fail("Factory construction is not an authorized object read/write")
    monkeypatch.setattr(MetadataObjectStoreCredentials, "token", forbidden_eager_token)
    service = build_source_snapshot_service(SimpleNamespace(is_durable=False))
    assert type(service.object_store) is GcsObjectStore


def test_explicit_local_gcs_uses_metadata_while_unconfigured_local_store_remains_memory(monkeypatch):
    assert isinstance(build_source_snapshot_service(None).object_store, InMemoryObjectStore)
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", uuid4().hex)
    assert isinstance(build_source_snapshot_service(None).object_store, InMemoryObjectStore)
    monkeypatch.setenv("ODP_OBJECT_STORE", "gcs")
    assert type(build_source_snapshot_service(None).object_store) is GcsObjectStore


def test_explicit_local_oauth_remains_available_without_borrowing_the_audit_identity(monkeypatch):
    marker = uuid4().hex
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", marker)
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", uuid4().hex)
    service = build_source_snapshot_service(None)
    assert type(service.object_store) is GcsObjectStore
    assert service.object_store._get_token() == marker


def test_real_sdk_metadata_http_cache_expiry_and_concurrent_refresh_never_use_ambient_tokens(protocol):
    provider = MetadataObjectStoreCredentials()
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _i: provider.token(), range(8)))
    assert len(set(tokens)) == 1 and tokens[0] == protocol.token and protocol.token_reads == 1
    provider._credentials.expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    refreshed = provider.token()
    assert refreshed == protocol.token and refreshed != tokens[0] and protocol.token_reads == 2
    assert all(flavor == "Google" for _path, flavor in protocol.metadata)
    assert any("devstorage.read_write" in url for url, _timeout in protocol.transport)


@pytest.mark.parametrize("status", [307, 403, 503])
def test_metadata_denial_or_redirect_never_uploads_or_leaks_sdk_error_chain(protocol, status, caplog):
    protocol.status = status
    marker = protocol.token
    service = build_source_snapshot_service(None)
    with pytest.raises(ObjectStoreWorkloadIdentityError) as caught:
        service.object_store.upload_object("tenant-a", "taiwan-snapshots", "tenants/tenant-a/test", b"owned bytes", "text/plain")
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    with caplog.at_level(logging.ERROR):
        logging.getLogger(__name__).error("Identity unavailable", exc_info=(type(error), error, error.__traceback__))
    assert marker not in caplog.text and marker not in str(error)
    assert protocol.storage == [] and protocol.redirect_receipts == 0


def test_factory_actual_snapshot_bytes_create_precondition_generation_and_residency_gates(protocol):
    service = build_source_snapshot_service(None)
    service.register_source("owned-feed", "Synthetic authorized source policy", ["example.invalid"], "APPROVED_RETRIEVAL")
    payload = b"owned immutable snapshot bytes; no external listing fetched"
    now = datetime.now(UTC)
    args = dict(tenant_id="tenant-a", intake_id="owned-intake", source_id="owned-feed", raw_data=payload,
                original_url="https://example.invalid/owned", canonical_url="https://example.invalid/owned",
                media_type="text/plain", capture_method="PARTNER_FEED", retention_class="2y",
                encryption_key_ref="synthetic-cmek-reference", observed_at=now, captured_at=now, bucket="taiwan-snapshots")
    snapshot_id = service.create_snapshot(**args)
    assert service.create_snapshot(**args) == snapshot_id
    record = service.get_snapshot("tenant-a", snapshot_id)
    assert record["object_generation"] == 47 and record["content_sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(protocol.objects) == 1 and list(protocol.objects.values()) == [payload]
    assert service.object_store.download_object("tenant-a", record["raw_object_uri"], generation=47) == payload
    assert protocol.token_reads == 1 and all(auth == "Bearer " + protocol.token for _method, _path, auth in protocol.storage)
    before = (len(protocol.metadata), len(protocol.storage))
    with pytest.raises(ResidencyDeniedError):
        service.object_store.download_object("tenant-b", record["raw_object_uri"])
    with pytest.raises(ResidencyDeniedError):
        service.object_store.upload_object("tenant-a", "unapproved-residency-bucket", "tenants/tenant-a/no", payload, "text/plain")
    assert before == (len(protocol.metadata), len(protocol.storage))


@pytest.mark.parametrize("operation", ["upload", "download"])
def test_actual_gcs_http_redirect_cannot_forward_the_runtime_token(protocol, operation):
    protocol.storage_redirect = True
    store = build_source_snapshot_service(None).object_store
    with pytest.raises(OSError):
        if operation == "upload":
            store.upload_object("tenant-a", "taiwan-snapshots", "tenants/tenant-a/redirect", b"owned bytes", "text/plain")
        else:
            store.download_object("tenant-a", "gs://taiwan-snapshots/tenants/tenant-a/redirect")
    assert len(protocol.storage) == 1 and protocol.redirect_receipts == 0 and protocol.objects == {}
