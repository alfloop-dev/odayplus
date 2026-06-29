from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_remote_staging_proof.py"
EXPECTED_SHA = "fd70b4f40d9bc178bb9e21ce1a24a8b4e4e95203"


class StagingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        correlation_id = self.headers.get("x-correlation-id", "")
        if self.path == "/platform/health":
            self._json({"status": "ok", "service": "oday-api", "correlation_id": correlation_id})
            return
        if self.path == "/platform/version":
            self._json(
                {
                    "status": "ok",
                    "service": "oday-api",
                    "api_version": "0.1.0",
                    "release_sha": EXPECTED_SHA,
                    "correlation_id": correlation_id,
                }
            )
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_staging_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StagingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_remote_staging_checker_fails_closed_when_configuration_is_missing(tmp_path, monkeypatch) -> None:
    for name in ("ODP_STAGING_DEPLOY_URL", "ODP_STAGING_API_URL", "ODP_STAGING_SECRET_OWNER"):
        monkeypatch.delenv(name, raising=False)

    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--expected-sha",
            EXPECTED_SHA,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "env:ODP_STAGING_DEPLOY_URL" in result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["staging"]["secret_values_redacted"] is True


def test_remote_staging_checker_verifies_health_and_release_sha(tmp_path, monkeypatch) -> None:
    server, url = start_staging_server()
    try:
        monkeypatch.setenv("ODP_STAGING_DEPLOY_URL", url)
        monkeypatch.setenv("ODP_STAGING_API_URL", url)
        monkeypatch.setenv("ODP_STAGING_SECRET_OWNER", "Platform/Ops")
        output = tmp_path / "report.json"

        result = subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--expected-sha",
                EXPECTED_SHA,
                "--correlation-id",
                "corr-test-remote-staging",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["ok"] is True
        assert report["health"]["correlation_id"] == "corr-test-remote-staging"
        assert report["version"]["release_sha"] == EXPECTED_SHA
        assert all(check["ok"] for check in report["checks"])
    finally:
        server.shutdown()
