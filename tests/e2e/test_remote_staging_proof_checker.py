from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "delivery_toolchain/e2e/check_remote_staging_proof.py"
STAGING_WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
EXPECTED_SHA = "fd70b4f40d9bc178bb9e21ce1a24a8b4e4e95203"


class StagingHandler(BaseHTTPRequestHandler):
    expected_token: str | None = "valid-staging-id-token"
    authorization_headers: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        correlation_id = self.headers.get("x-correlation-id", "")
        auth_header = self.headers.get("Authorization", "")
        self.authorization_headers.append(auth_header)

        if self.expected_token is not None:
            expected_auth = f"Bearer {self.expected_token}"
            if auth_header != expected_auth:
                self.send_response(401)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"detail":"Unauthorized: missing or invalid IAM identity token"}'
                )
                return

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


def start_staging_server(
    *, expected_token: str | None = "valid-staging-id-token"
) -> tuple[ThreadingHTTPServer, str]:
    StagingHandler.expected_token = expected_token
    StagingHandler.authorization_headers = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), StagingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def install_fake_gcloud(tmp_path: Path, *, token: str, exit_code: int = 0) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$GCLOUD_LOG"\n'
        f'printf "%s\\n" "{token}"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)
    return fake_bin


def test_remote_staging_checker_fails_closed_when_configuration_is_missing(
    tmp_path, monkeypatch
) -> None:
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
    server, url = start_staging_server(expected_token="valid-staging-id-token")
    try:
        monkeypatch.setenv("ODP_STAGING_DEPLOY_URL", url)
        monkeypatch.setenv("ODP_STAGING_API_URL", url)
        monkeypatch.setenv("ODP_STAGING_SECRET_OWNER", "Platform/Ops")
        monkeypatch.delenv("ODP_STAGING_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("ODP_IDENTITY_TOKEN", raising=False)
        monkeypatch.delenv("ODP_STAGING_API_AUDIENCE", raising=False)
        gcloud_log = tmp_path / "gcloud.log"
        fake_bin = install_fake_gcloud(tmp_path, token="valid-staging-id-token")
        monkeypatch.setenv("GCLOUD_LOG", str(gcloud_log))
        monkeypatch.setenv("PATH", str(fake_bin))
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
        checks_by_name = {check["name"]: check for check in report["checks"]}
        assert checks_by_name["auth:identity_token"]["ok"] is True
        assert gcloud_log.read_text(encoding="utf-8").splitlines() == [
            f"auth print-identity-token --audiences={url}"
        ]
        assert StagingHandler.authorization_headers == [
            "Bearer valid-staging-id-token",
            "Bearer valid-staging-id-token",
        ]
        assert "Authorization" not in json.dumps(report)
        assert "valid-staging-id-token" not in json.dumps(report)
        assert "valid-staging-id-token" not in result.stdout
        assert "valid-staging-id-token" not in result.stderr
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    ("token", "exit_code"),
    [("sensitive-token-from-failed-command", 1), ("", 0)],
)
def test_remote_staging_checker_fails_closed_when_identity_token_cannot_be_minted(
    tmp_path, monkeypatch, token, exit_code
) -> None:
    server, url = start_staging_server()
    try:
        monkeypatch.setenv("ODP_STAGING_DEPLOY_URL", url)
        monkeypatch.setenv("ODP_STAGING_API_URL", url)
        monkeypatch.setenv("ODP_STAGING_SECRET_OWNER", "Platform/Ops")
        monkeypatch.delenv("ODP_STAGING_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("ODP_IDENTITY_TOKEN", raising=False)
        monkeypatch.delenv("ODP_STAGING_API_AUDIENCE", raising=False)
        gcloud_log = tmp_path / "gcloud.log"
        fake_bin = install_fake_gcloud(tmp_path, token=token, exit_code=exit_code)
        monkeypatch.setenv("GCLOUD_LOG", str(gcloud_log))
        monkeypatch.setenv("PATH", str(fake_bin))
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
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["ok"] is False
        checks_by_name = {check["name"]: check for check in report["checks"]}
        assert checks_by_name["auth:identity_token"]["ok"] is False
        assert "failed to mint identity token" in checks_by_name["auth:identity_token"]["detail"]
        assert report["staging"]["secret_values_redacted"] is True
        assert gcloud_log.read_text(encoding="utf-8").splitlines() == [
            f"auth print-identity-token --audiences={url}"
        ]
        if token:
            assert token not in json.dumps(report)
            assert token not in result.stdout
            assert token not in result.stderr
    finally:
        server.shutdown()


def test_remote_staging_checker_fails_closed_when_gcloud_cannot_be_invoked(
    tmp_path, monkeypatch
) -> None:
    server, url = start_staging_server()
    try:
        monkeypatch.setenv("ODP_STAGING_DEPLOY_URL", url)
        monkeypatch.setenv("ODP_STAGING_API_URL", url)
        monkeypatch.setenv("ODP_STAGING_SECRET_OWNER", "Platform/Ops")
        monkeypatch.delenv("ODP_STAGING_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("ODP_IDENTITY_TOKEN", raising=False)
        monkeypatch.delenv("ODP_STAGING_API_AUDIENCE", raising=False)
        monkeypatch.setenv("PATH", str(tmp_path / "missing-bin"))
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
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["ok"] is False
        checks_by_name = {check["name"]: check for check in report["checks"]}
        assert checks_by_name["auth:identity_token"]["ok"] is False
        assert "failed to mint identity token" in checks_by_name["auth:identity_token"]["detail"]
        assert report["staging"]["secret_values_redacted"] is True
    finally:
        server.shutdown()


def test_remote_staging_checker_fails_closed_on_unauthorized_response(
    tmp_path, monkeypatch
) -> None:
    server, url = start_staging_server(expected_token="server-expected-token")
    try:
        monkeypatch.setenv("ODP_STAGING_DEPLOY_URL", url)
        monkeypatch.setenv("ODP_STAGING_API_URL", url)
        monkeypatch.setenv("ODP_STAGING_SECRET_OWNER", "Platform/Ops")
        monkeypatch.delenv("ODP_STAGING_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("ODP_IDENTITY_TOKEN", raising=False)
        monkeypatch.delenv("ODP_STAGING_API_AUDIENCE", raising=False)
        fake_bin = install_fake_gcloud(tmp_path, token="wrong-or-expired-token")
        monkeypatch.setenv("PATH", str(fake_bin))
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
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["ok"] is False
        assert "HTTP Error 401" in json.dumps(report)
        assert "wrong-or-expired-token" not in json.dumps(report)
    finally:
        server.shutdown()


def test_deploy_staging_workflow_fails_closed_through_remote_checker() -> None:
    workflow = STAGING_WORKFLOW.read_text(encoding="utf-8")

    assert "Runtime Release" in workflow
    assert "workflow_dispatch" in workflow
    assert "inputs.release_sha" in workflow
    assert "inputs.task_id" in workflow
    assert "inputs.release_lease" in workflow
    assert "ODAY_RELEASE_SHA" in workflow
    assert "ODP_STAGING_DEPLOY_URL" in workflow
    assert "ODP_STAGING_API_URL" in workflow
    assert "ODP_STAGING_SECRET_OWNER" in workflow
    assert "delivery_toolchain/e2e/check_remote_staging_proof.py" in workflow
    assert '--expected-sha "$ODAY_RELEASE_SHA"' in workflow
    assert "Validate supervisor release admission" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "TODO: replace with real deploy" not in workflow
