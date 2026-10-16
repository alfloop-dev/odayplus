"""Loopback JSON API protocol fixture; never connects to Google Cloud."""

import json
import socket
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread

import pytest

from shared.audit.worm import GcsWormEvidenceSink


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
            binding.setenv("ODP_AUDIT_WORM_GCS_TOKEN", protocol.writer_token)
            binding.setenv("ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN", protocol.recovery_token)

            def bind_actual_sink(sink: GcsWormEvidenceSink, configured_uri: str, *,
                                 token: str | None = None, recovery_token: str | None = None,
                                 timeout_seconds: float = 10.0) -> None:
                original_init(sink, configured_uri, token=token, recovery_token=recovery_token,
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
