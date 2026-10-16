"""Loopback JSON API protocol fixture; never connects to Google Cloud."""

import json
import socket
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from types import SimpleNamespace

import pytest

from shared.audit.worm import GcsWormEvidenceSink

SYNTHETIC_WRITER = "owned-audit-writer@synthetic-project.iam.gserviceaccount.com"
SYNTHETIC_RECOVERY = "owned-audit-reader@synthetic-project.iam.gserviceaccount.com"


class AuditIdentityHTTPControl:
    """Control only the real Google auth SDK's metadata/IAM HTTP boundary."""

    def __init__(self, protocol):
        self.protocol = protocol
        self.calls = []
        self.generations = {SYNTHETIC_WRITER: 0, SYNTHETIC_RECOVERY: 0}
        self.denied = None
        self.malformed_expiry = False

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.calls.append((url, method, timeout))
        assert timeout is not None and 0 < timeout <= 10
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == "metadata.google.internal":
            assert method == "GET" and headers.get("metadata-flavor") == "Google"
            if parsed.path.endswith("/token"):
                data = dict(access_token="synthetic-runtime-metadata-only", expires_in=3600, token_type="Bearer")
            else:
                assert parsed.path.endswith("/default/")
                data = dict(email="owned-runtime@synthetic-project.iam.gserviceaccount.com",
                            scopes=["https://www.googleapis.com/auth/cloud-platform"], aliases=["default"])
        else:
            assert parsed.scheme == "https" and parsed.hostname == "iamcredentials.googleapis.com"
            assert method == "POST" and headers["authorization"] == "Bearer synthetic-runtime-metadata-only"
            target = next((principal for principal in self.generations
                           if parsed.path.endswith("/" + principal + ":generateAccessToken")), None)
            assert target is not None
            request = json.loads(body)
            assert request["lifetime"] == "300s"
            assert request["scope"] == ["https://www.googleapis.com/auth/devstorage." +
                                        ("read_write" if target == SYNTHETIC_WRITER else "read_only")]
            if self.denied == target:
                return SimpleNamespace(status=403, data=b'{"error":"synthetic IAM denial"}',
                                       headers={"content-type": "application/json"})
            self.generations[target] += 1
            data = dict(accessToken=(self.protocol.writer_token if target == SYNTHETIC_WRITER
                                     else self.protocol.recovery_token),
                        expireTime=(datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if self.malformed_expiry:
                data["accessToken"] = "synthetic-token-that-must-not-appear-in-a-log"
                if self.malformed_expiry == "missing":
                    del data["expireTime"]
                else:
                    data["expireTime"] = "synthetic-invalid-expiry"
        return SimpleNamespace(status=200, data=json.dumps(data).encode(),
                               headers={"content-type": "application/json", "metadata-flavor": "Google"})


def configure_loopback_workload_identity(monkeypatch, protocol):
    import google.auth.transport.requests

    control = AuditIdentityHTTPControl(protocol)
    monkeypatch.setenv("ODP_AUDIT_WORM_WRITER_SERVICE_ACCOUNT", SYNTHETIC_WRITER)
    monkeypatch.setenv("ODP_AUDIT_WORM_RECOVERY_SERVICE_ACCOUNT", SYNTHETIC_RECOVERY)
    monkeypatch.delenv("ODP_AUDIT_WORM_GCS_TOKEN", raising=False)
    monkeypatch.delenv("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", raising=False)
    monkeypatch.setattr(google.auth.transport.requests, "Request", lambda: control)
    return control


class GcsWormProtocolStub:
    writer_token = "synthetic-create-only"
    recovery_token = "synthetic-get-only"

    def __init__(self):
        self.objects = {}
        self.creations = 0
        self.requests = []
        self.drop_after_create_event_type = None
        self.read_status = None
        self.read_transform = None
        self.redirect_on_read = False
        self.write_status = None
        self.lock = RLock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, payload=None, **headers):
                self.send_response(code)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if payload is not None:
                    self.wfile.write(json.dumps(payload).encode())

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                with stub.lock:
                    stub.requests.append(("POST", self.path, self.headers.get("Authorization")))
                    if self.headers.get("Authorization") != "Bearer " + stub.writer_token:
                        self.respond(403)
                        return
                    if parsed.path != "/upload/storage/v1/b/synthetic-bucket/o" or query.get("ifGenerationMatch") != ["0"]:
                        self.respond(400)
                        return
                    if stub.write_status:
                        if stub.write_status == 302:
                            self.respond(302, Location="/unapproved")
                        else:
                            self.respond(stub.write_status)
                        return
                    name = query["name"][0]
                    if name in stub.objects:
                        self.respond(412)
                        return
                    data = self.rfile.read(int(self.headers["Content-Length"]))
                    envelope = json.loads(data)
                    stub.objects[name] = envelope
                    stub.creations += 1
                    if stub.drop_after_create_event_type is not None and stub.drop_after_create_event_type == envelope["payload"].get("event_type"):
                        stub.drop_after_create_event_type = None
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.respond(200, {"name": name, "generation": "1"})

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                with stub.lock:
                    stub.requests.append(("GET", self.path, self.headers.get("Authorization")))
                    if stub.redirect_on_read:
                        self.respond(302, Location="/unapproved")
                        return
                    if self.headers.get("Authorization") != "Bearer " + stub.recovery_token:
                        self.respond(403)
                        return
                    if stub.read_status:
                        self.respond(stub.read_status)
                        return
                    prefix = "/storage/v1/b/synthetic-bucket/o/"
                    if not parsed.path.startswith(prefix) or urllib.parse.parse_qs(parsed.query).get("alt") != ["media"]:
                        self.respond(400)
                        return
                    name = urllib.parse.unquote(parsed.path[len(prefix):])
                    if name not in stub.objects:
                        self.respond(404)
                        return
                    envelope = deepcopy(stub.objects[name])
                    if stub.read_transform:
                        envelope = stub.read_transform(envelope)
                    self.respond(200, envelope)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def bind(self, sink, monkeypatch):
        real_open = sink._opener.open

        def loopback_open(request, *, timeout):
            parsed = urllib.parse.urlsplit(request.full_url)
            assert parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com"
            translated = urllib.request.Request(
                f"http://127.0.0.1:{self.server.server_port}" + parsed.path + "?" + parsed.query,
                data=request.data,
                method=request.get_method(),
                headers=dict(request.header_items()),
            )
            return real_open(translated, timeout=timeout)

        monkeypatch.setattr(sink._opener, "open", loopback_open)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@contextmanager
def owned_production_gcs_worm(monkeypatch: pytest.MonkeyPatch) -> Iterator[GcsWormProtocolStub]:
    protocol = GcsWormProtocolStub()
    original_init = GcsWormEvidenceSink.__init__
    sink_uri = "gs://synthetic-bucket/production-engineering-fixture"
    sinks: list[GcsWormEvidenceSink] = []
    try:
        with monkeypatch.context() as binding:
            binding.setenv("ODP_AUDIT_WORM_SINK_URI", sink_uri)
            configure_loopback_workload_identity(binding, protocol)

            def bind_actual_sink(sink: GcsWormEvidenceSink, configured_uri: str, *,
                                 token: str | None = None, recovery_token: str | None = None,
                                 writer_service_account: str | None = None,
                                 recovery_service_account: str | None = None,
                                 timeout_seconds: float = 10.0) -> None:
                original_init(sink, configured_uri, token=token, recovery_token=recovery_token,
                              writer_service_account=writer_service_account,
                              recovery_service_account=recovery_service_account,
                              timeout_seconds=timeout_seconds)
                assert configured_uri == sink_uri
                protocol.bind(sink, binding)
                sinks.append(sink)

            binding.setattr(GcsWormEvidenceSink, "__init__", bind_actual_sink)
            yield protocol
            assert sinks, "explicit production callback did not build its GCS sink"
            assert protocol.writer_token != protocol.recovery_token
    finally:
        protocol.close()
        assert not protocol.thread.is_alive(), "owned GCS protocol thread did not stop"
