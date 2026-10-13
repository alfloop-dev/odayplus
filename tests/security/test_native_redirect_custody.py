"""Real loopback HTTP/stdlib redirect handling, not deployed/native authority."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from delivery_toolchain.release import consume_dev_smoke as c


@pytest.mark.parametrize("mode", ["redirect", "header-loss"])
def test_native_real_urllib_never_loses_intermediate_cookie_custody(mode):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload, cookie=None, location=None):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if cookie is not None:
                self.send_header(
                    "Set-Cookie", f"{c._SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax"
                )
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, self.headers.get("Cookie", "")))
            if self.path == "/login":
                self.reply(200, {"ok": True, "subject": "offline-user"}, "owned-a")
            else:
                self.reply(200, {"ok": True}, "")

        def do_GET(self):
            cookie = self.headers.get("Cookie", "")
            calls.append((self.command, self.path, cookie))
            if self.path == "/ordinary":
                if mode == "header-loss":
                    self.send_response(200)
                    self.send_header(
                        "Set-Cookie",
                        f"{c._SESSION_COOKIE}=owned-b; Path=/; Secure; HttpOnly; SameSite=Lax",
                    )
                    for i in range(110):
                        self.send_header(f"X-Header-{i}", "x")
                    self.end_headers()  # Actual stdlib >100-header parser refusal.
                else:
                    self.reply(302, {}, "owned-b", "/terminal")
            elif self.path == "/terminal":
                self.reply(200, {"subject": "offline-user"})
            else:
                self.reply(401, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = c.gate.UrllibHttpClient(
            f"http://127.0.0.1:{server.server_port}",
            timeout=2,
            bearer_token="",
            operator_role="",
            operator_subject="",
            operator_tenant="",
            correlation_id="offline",
        )
        now = time.monotonic()
        web = c._ObservedWeb(
            transport,
            execution_id="offline",
            deadline=now + 10,
            cleanup_deadline=now + 15,
            login_pair=("offline-user", "offline-password"),
        )
        assert (
            web.request(
                "POST",
                "/login",
                body={
                    "username": "offline-user",
                    "password": "offline-password",
                    "returnTo": c._FORM_RETURN_TO,
                },
                headers={"content-type": "application/json"},
            ).status
            == 200
        )
        web.request("GET", "/ordinary", headers={"cookie": f"{c._SESSION_COOKIE}=owned-a"})
        assert web.uncertainty_latched and not any(path == "/terminal" for _, path, _ in calls)
        assert not web.cleanup_owned_sessions()
        logouts = [cookie for method, path, cookie in calls if path == "/auth/logout"]
        assert logouts == [
            f"{c._SESSION_COOKIE}=owned-a",
            *([f"{c._SESSION_COOKIE}=owned-b"] if mode == "redirect" else []),
        ]
        assert len(logouts) == len(set(logouts))
        # Header-loss B was not safely recovered; NEVER claim its revocation.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
