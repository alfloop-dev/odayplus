from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from modules.external_data.providers import (
    AdminBoundaryDatasetProvider,
    ExternalDatasetProviderAuthError,
    ExternalDatasetProviderChecksumError,
    ExternalDatasetProviderConfigError,
    ExternalDatasetProviderError,
    ExternalDatasetProviderRateLimitError,
    ExternalDatasetProviderResponseError,
    ExternalDatasetProviderStaleError,
    ExternalDatasetProviderTimeoutError,
    PoiCommercialApiProvider,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class _ProviderServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]
    routes: dict[str, dict[str, Any]]


class _ProviderHandler(BaseHTTPRequestHandler):
    server: _ProviderServer

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        self.server.requests.append(
            {
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "headers": dict(self.headers.items()),
            }
        )
        route = self.server.routes[parsed.path]
        delay = float(route.get("delay", 0))
        if delay:
            time.sleep(delay)
        status = int(route.get("status", 200))
        payload = (
            route["response"](parse_qs(parsed.query))
            if callable(route["response"])
            else route["response"]
        )
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        checksum = route.get("checksum", hashlib.sha256(body).hexdigest())
        if checksum is not None:
            self.send_header("X-Content-SHA256", str(checksum))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, _format: str, *args: object) -> None:
        del args


@contextmanager
def _provider_server(routes: dict[str, dict[str, Any]]) -> Iterator[_ProviderServer]:
    server = _ProviderServer(("127.0.0.1", 0), _ProviderHandler)
    server.routes = routes
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _base_env(server: _ProviderServer) -> dict[str, str]:
    return {
        "ODP_EXTERNAL_PROVIDER_MODE": "live",
        "ODP_DEPLOY_ENV": "production",
        "ODP_PRODUCTION_PROVIDER_IDS": ("poi.commercial_api,admin_boundary.official_dataset"),
        "ODP_POI_PROVIDER_URL": f"http://127.0.0.1:{server.server_port}/poi",
        "ODP_POI_PROVIDER_API_KEY": "poi-secret",
        "ODP_POI_PROVIDER_MAX_RETRIES": "0",
        "ODP_POI_PROVIDER_RATE_LIMIT_PER_SECOND": "10000",
        "ODP_POI_PROVIDER_MAX_AGE_SECONDS": "3600",
        "ODP_ADMIN_BOUNDARY_PROVIDER_URL": (f"http://127.0.0.1:{server.server_port}/admin"),
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN": "admin-secret",
        "ODP_ADMIN_BOUNDARY_PROVIDER_MAX_RETRIES": "0",
        "ODP_ADMIN_BOUNDARY_PROVIDER_RATE_LIMIT_PER_SECOND": "10000",
        "ODP_ADMIN_BOUNDARY_PROVIDER_MAX_AGE_SECONDS": "3600",
    }


def _poi_record(source_id: str) -> dict[str, Any]:
    return {
        "source_poi_id": source_id,
        "poi_name": f"真實來源 {source_id}",
        "poi_category": "Transit",
        "poi_subcategory": "station",
        "address_raw": "台北市信義區忠孝東路五段",
        "latitude": 25.040944,
        "longitude": 121.565472,
        "status": "active",
        "confidence": 0.95,
    }


def _admin_record() -> dict[str, Any]:
    return {
        "source_boundary_id": "TW-TPE-XINYI",
        "admin_level": "district",
        "admin_code": "63000050",
        "admin_name": "信義區",
        "parent_admin_code": "63000000",
        "centroid_latitude": 25.033,
        "centroid_longitude": 121.5625,
        "area_km2": 11.2077,
        "effective_date": "2026-01-01",
    }


def _request_header(request: dict[str, Any], name: str) -> str:
    return next(value for key, value in request["headers"].items() if key.lower() == name.lower())


def test_poi_live_provider_uses_real_http_auth_and_pagination() -> None:
    def poi_page(query: dict[str, list[str]]) -> dict[str, Any]:
        token = query.get("page_token", [""])[0]
        return {
            "snapshot_id": "poi-live-20260724",
            "observed_at": "2026-07-24T11:55:00Z",
            "records": [_poi_record("POI-002" if token else "POI-001")],
            "next_page_token": "" if token else "next-2",
        }

    with _provider_server({"/poi": {"response": poi_page}}) as server:
        result = PoiCommercialApiProvider(
            env=_base_env(server),
            clock=lambda: NOW,
        ).fetch_and_ingest(correlation_id="corr-poi")

    assert result.mode.value == "live"
    assert result.raw_snapshot.records[0]["poi_name"] == "真實來源 POI-001"
    assert result.raw_snapshot.lineage.page_count == 2
    assert result.raw_snapshot.lineage.endpoint_origin.endswith("/poi")
    assert len(result.connector_run.accepted) == 2
    assert len(server.requests) == 2
    assert _request_header(server.requests[0], "X-API-Key") == "poi-secret"
    assert _request_header(server.requests[0], "X-Correlation-Id") == "corr-poi"
    assert server.requests[1]["query"] == {"page_token": ["next-2"]}
    assert "fixture" not in repr(result.raw_snapshot).lower()


def test_admin_boundary_live_provider_uses_bearer_snapshot_and_lineage() -> None:
    payload = {
        "snapshot_id": "admin-live-20260724",
        "observed_at": "2026-07-24T11:50:00Z",
        "records": [_admin_record()],
    }
    with _provider_server({"/admin": {"response": payload}}) as server:
        result = AdminBoundaryDatasetProvider(
            env=_base_env(server),
            clock=lambda: NOW,
        ).fetch_and_ingest(correlation_id="corr-admin")

    assert len(result.connector_run.accepted) == 1
    assert result.raw_snapshot.snapshot_id == "admin-live-20260724"
    assert result.raw_snapshot.checksum_sha256
    assert result.raw_snapshot.lineage.page_checksums
    assert _request_header(server.requests[0], "Authorization") == "Bearer admin-secret"


@pytest.mark.parametrize(
    ("env_update", "code"),
    [
        ({"ODP_POI_PROVIDER_URL": ""}, "missing_endpoint"),
        ({"ODP_POI_PROVIDER_API_KEY": ""}, "missing_credential"),
        (
            {"ODP_PRODUCTION_PROVIDER_IDS": "admin_boundary.official_dataset"},
            "provider_not_selected",
        ),
    ],
)
def test_poi_live_provider_fails_closed_before_request(
    env_update: dict[str, str],
    code: str,
) -> None:
    with _provider_server({"/poi": {"response": {}}}) as server:
        env = _base_env(server)
        env.update(env_update)
        with pytest.raises(ExternalDatasetProviderConfigError) as error:
            PoiCommercialApiProvider(env=env, clock=lambda: NOW).fetch_and_ingest()
        assert error.value.code == code
        assert server.requests == []


@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        (401, ExternalDatasetProviderAuthError, "unauthorized"),
        (429, ExternalDatasetProviderRateLimitError, "rate_limited"),
    ],
)
def test_live_provider_classifies_auth_and_quota(
    status: int,
    error_type: type[ExternalDatasetProviderError],
    code: str,
) -> None:
    with _provider_server({"/poi": {"status": status, "response": {"error": code}}}) as server:
        with pytest.raises(error_type) as error:
            PoiCommercialApiProvider(
                env=_base_env(server),
                clock=lambda: NOW,
            ).fetch_and_ingest()
        assert error.value.code == code


def test_live_provider_classifies_timeout() -> None:
    payload = {
        "snapshot_id": "poi-live-timeout",
        "observed_at": "2026-07-24T11:55:00Z",
        "records": [_poi_record("POI-TIMEOUT")],
    }
    with _provider_server({"/poi": {"delay": 0.1, "response": payload}}) as server:
        env = _base_env(server)
        env["ODP_POI_PROVIDER_TIMEOUT_SECONDS"] = "0.01"
        with pytest.raises(ExternalDatasetProviderTimeoutError) as error:
            PoiCommercialApiProvider(env=env, clock=lambda: NOW).fetch_and_ingest()
        assert error.value.code == "timeout"


def test_live_provider_rejects_stale_snapshot() -> None:
    payload = {
        "snapshot_id": "poi-live-stale",
        "observed_at": "2026-07-20T00:00:00Z",
        "records": [_poi_record("POI-STALE")],
    }
    with _provider_server({"/poi": {"response": payload}}) as server:
        env = _base_env(server)
        env["ODP_POI_PROVIDER_MAX_AGE_SECONDS"] = "60"
        with pytest.raises(ExternalDatasetProviderStaleError) as error:
            PoiCommercialApiProvider(env=env, clock=lambda: NOW).fetch_and_ingest()
        assert error.value.code == "snapshot_stale"


def test_live_provider_rejects_checksum_and_record_schema_errors() -> None:
    valid_payload = {
        "snapshot_id": "poi-live-checksum",
        "observed_at": "2026-07-24T11:55:00Z",
        "records": [_poi_record("POI-CHECKSUM")],
    }
    with _provider_server({"/poi": {"response": valid_payload, "checksum": "0" * 64}}) as server:
        with pytest.raises(ExternalDatasetProviderChecksumError):
            PoiCommercialApiProvider(
                env=_base_env(server),
                clock=lambda: NOW,
            ).fetch_and_ingest()

    invalid_payload = {
        "snapshot_id": "poi-live-invalid",
        "observed_at": "2026-07-24T11:55:00Z",
        "records": [{"source_poi_id": "missing-required-fields"}],
    }
    with _provider_server({"/poi": {"response": invalid_payload}}) as server:
        with pytest.raises(ExternalDatasetProviderResponseError) as error:
            PoiCommercialApiProvider(
                env=_base_env(server),
                clock=lambda: NOW,
            ).fetch_and_ingest()
        assert error.value.code == "record_schema_invalid"


def test_live_provider_retries_transport_without_synthetic_fallback() -> None:
    calls = 0
    payload = {
        "snapshot_id": "poi-live-retry",
        "observed_at": "2026-07-24T11:55:00Z",
        "records": [_poi_record("POI-RETRY")],
    }

    def flaky(_query: dict[str, list[str]]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return payload

    with _provider_server({"/poi": {"status": 503, "response": flaky}}) as server:
        env = _base_env(server)
        env["ODP_POI_PROVIDER_MAX_RETRIES"] = "1"
        env["ODP_POI_PROVIDER_RETRY_BACKOFF_SECONDS"] = "0"
        with pytest.raises(ExternalDatasetProviderError) as error:
            PoiCommercialApiProvider(env=env, clock=lambda: NOW).fetch_and_ingest()
        assert error.value.code == "server_error"
        assert calls == 2
        assert len(server.requests) == 2


def test_production_provider_adapter_never_runs_fixture_mode() -> None:
    with pytest.raises(ExternalDatasetProviderConfigError) as error:
        PoiCommercialApiProvider(
            env={"ODP_EXTERNAL_PROVIDER_MODE": "fixture"},
            clock=lambda: NOW + timedelta(days=1),
        ).fetch_and_ingest()
    assert error.value.code == "live_mode_required"
