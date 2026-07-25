from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from modules.external_data.connectors import (
    probe_external_provider_connectivity,
    validate_external_providers,
)


class _DeterministicProviderServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]
    failures: dict[str, str]
    delays: dict[str, float]


class _DeterministicProviderHandler(BaseHTTPRequestHandler):
    server: _DeterministicProviderServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        content_length = int(self.headers.get("content-length", "0"))
        request_body = self.rfile.read(content_length)
        self._respond(request_body=request_body)

    def _respond(self, *, request_body: bytes = b"") -> None:
        path = urlsplit(self.path).path
        self.server.requests.append(
            {
                "path": path,
                "method": self.command,
                "api_key": self.headers.get("X-API-Key"),
                "authorization": self.headers.get("Authorization"),
                "request_body": request_body,
            }
        )
        delay = self.server.delays.get(path, 0)
        if delay:
            time.sleep(delay)
        failure = self.server.failures.get(path)
        if failure == "unauthorized":
            self._json({"error": "unauthorized"}, status=401)
            return
        observed_at = datetime.now(UTC).isoformat()
        if path == "/listing":
            payload: dict[str, Any] = {
                "snapshot_id": "listing-probe-snapshot",
                "records": [],
            }
        elif path == "/poi":
            payload = {
                "snapshot_id": "poi-probe-snapshot",
                "observed_at": observed_at,
                "records": [],
            }
        elif path == "/geocode":
            payload = {
                "request_id": "geocode-probe-request",
                "observed_at": observed_at,
                "result": {
                    "latitude": 25.0375,
                    "longitude": 121.5637,
                },
            }
            if failure == "schema":
                payload["result"] = {"latitude": "not-a-coordinate"}
        elif path == "/admin":
            payload = {
                "snapshot_id": "admin-probe-snapshot",
                "observed_at": observed_at,
                "records": [],
            }
        else:
            self._json({"error": "not_found"}, status=404)
            return
        self._json(
            payload,
            status=201 if failure == "unexpected_status" else 200,
            checksum=path in {"/poi", "/admin"},
        )

    def _json(
        self,
        payload: dict[str, Any],
        *,
        status: int = 200,
        checksum: bool = False,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if checksum:
            self.send_header("X-Content-SHA256", hashlib.sha256(body).hexdigest())
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, _format: str, *args: object) -> None:
        del args


@contextmanager
def _deterministic_provider_server(
    *,
    failures: dict[str, str] | None = None,
    delays: dict[str, float] | None = None,
) -> Iterator[_DeterministicProviderServer]:
    server = _DeterministicProviderServer(
        ("127.0.0.1", 0),
        _DeterministicProviderHandler,
    )
    server.requests = []
    server.failures = failures or {}
    server.delays = delays or {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _production_env(server: _DeterministicProviderServer) -> dict[str, str]:
    base_url = f"http://127.0.0.1:{server.server_port}"
    return {
        "ODP_EXTERNAL_PROVIDER_MODE": "live",
        "ODP_DEPLOY_ENV": "production",
        "ODP_PRODUCTION_PROVIDER_IDS": (
            "listing.partner_feed,poi.commercial_api,"
            "geocode.primary_api,admin_boundary.official_dataset"
        ),
        "ODP_LISTING_PROVIDER_FEED_URL": f"{base_url}/listing",
        "ODP_LISTING_PROVIDER_API_KEY": "listing-probe-secret",
        "ODP_LISTING_PROVIDER_AUTH_STATUS": "active",
        "ODP_POI_PROVIDER_URL": f"{base_url}/poi",
        "ODP_POI_PROVIDER_API_KEY": "poi-probe-secret",
        "ODP_POI_PROVIDER_AUTH_STATUS": "active",
        "ODP_GEOCODE_PROVIDER_URL": f"{base_url}/geocode",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-probe-secret",
        "ODP_GEOCODE_PROVIDER_AUTH_STATUS": "active",
        "ODP_ADMIN_BOUNDARY_PROVIDER_URL": f"{base_url}/admin",
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN": "admin-probe-secret",
        "ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS": "active",
        "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS": "1",
    }


def test_deterministic_http_server_exercises_probe_contract_without_claiming_live_proof() -> None:
    with _deterministic_provider_server() as server:
        env = _production_env(server)
        validation = validate_external_providers(
            env=env,
            correlation_id="corr-provider-configuration",
        )
        result = probe_external_provider_connectivity(
            validation=validation,
            env=env,
            correlation_id="corr-provider-connectivity",
        )

    assert validation.ok is True
    assert result.configuration_valid is True
    assert result.connectivity_healthy is True
    assert {probe.provider_id for probe in result.probes} == {
        "listing.partner_feed",
        "poi.commercial_api",
        "geocode.primary_api",
        "admin_boundary.official_dataset",
    }
    assert all(probe.authentication_accepted for probe in result.probes)
    assert all(probe.response_valid for probe in result.probes)
    assert all(probe.schema_valid for probe in result.probes)
    assert all(probe.reason_code == "ok" for probe in result.probes)
    by_path = {request["path"]: request for request in server.requests}
    assert by_path["/listing"]["api_key"] == "listing-probe-secret"
    assert by_path["/poi"]["api_key"] == "poi-probe-secret"
    assert by_path["/geocode"]["method"] == "POST"
    assert by_path["/geocode"]["api_key"] == "geocode-probe-secret"
    assert by_path["/admin"]["authorization"] == "Bearer admin-probe-secret"


def test_configuration_valid_does_not_mask_provider_auth_failure_or_secret() -> None:
    with _deterministic_provider_server(failures={"/poi": "unauthorized"}) as server:
        env = _production_env(server)
        validation = validate_external_providers(env=env)
        result = probe_external_provider_connectivity(
            validation=validation,
            env=env,
        )

    evidence = {probe.provider_id: probe for probe in result.probes}["poi.commercial_api"]
    assert validation.ok is True
    assert result.configuration_valid is True
    assert result.connectivity_healthy is False
    assert evidence.reason_code == "unauthorized"
    assert evidence.http_status == 401
    rendered = json.dumps(result.to_dict(), sort_keys=True)
    assert "poi-probe-secret" not in rendered
    assert "listing-probe-secret" not in rendered


def test_probe_fails_closed_for_provider_specific_schema_failure() -> None:
    with _deterministic_provider_server(failures={"/geocode": "schema"}) as server:
        env = _production_env(server)
        validation = validate_external_providers(env=env)
        result = probe_external_provider_connectivity(
            validation=validation,
            env=env,
        )

    evidence = {probe.provider_id: probe for probe in result.probes}["geocode.primary_api"]
    assert result.connectivity_healthy is False
    assert evidence.authentication_accepted is True
    assert evidence.response_valid is True
    assert evidence.schema_valid is False
    assert evidence.reason_code == "schema_invalid"


def test_probe_requires_exact_http_200_for_health() -> None:
    with _deterministic_provider_server(failures={"/listing": "unexpected_status"}) as server:
        env = _production_env(server)
        validation = validate_external_providers(env=env)
        result = probe_external_provider_connectivity(
            validation=validation,
            env=env,
        )

    evidence = {probe.provider_id: probe for probe in result.probes}["listing.partner_feed"]
    assert result.connectivity_healthy is False
    assert evidence.connectivity_healthy is False
    assert evidence.http_status == 201
    assert evidence.reason_code == "unexpected_http_status"


def test_probe_timeout_is_bounded_and_provider_specific() -> None:
    with _deterministic_provider_server(delays={"/listing": 0.2}) as server:
        env = _production_env(server)
        env["ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"] = "0.05"
        validation = validate_external_providers(env=env)
        started = time.monotonic()
        result = probe_external_provider_connectivity(
            validation=validation,
            env=env,
        )
        elapsed = time.monotonic() - started

    evidence = {probe.provider_id: probe for probe in result.probes}["listing.partner_feed"]
    assert elapsed < 0.5
    assert result.connectivity_healthy is False
    assert evidence.reason_code == "timeout"
