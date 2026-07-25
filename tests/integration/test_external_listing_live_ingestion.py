from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from modules.external_data.application.listing_feed_adapter import (
    FeedSchemaError,
    ListingFeedClient,
    ListingFeedConfigurationError,
    LiveListingFeedAdapter,
    RateLimitError,
    TimeoutError,
    UnauthorizedError,
    UpstreamError,
)
from modules.external_data.application.listing_feed_store import (
    DocumentListingFeedIngestionStore,
)
from modules.external_data.geo import GeoPipeline
from modules.listing.application.pipeline import ListingPipeline
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import build_persistence


@dataclass
class _ServerState:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    delay_seconds: float = 0.0
    requests: list[dict[str, str]] = field(default_factory=list)


@contextmanager
def _http_server(state: _ServerState) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append({key.lower(): value for key, value in self.headers.items()})
            if state.delay_seconds:
                time.sleep(state.delay_seconds)
            self.send_response(state.status)
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            try:
                self.wfile.write(state.body)
            except BrokenPipeError:
                pass

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/partner/listings"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _payload() -> dict[str, Any]:
    return {
        "contract_id": "listing_raw_snapshot",
        "snapshot_id": "partner-snapshot-20260724T120000Z",
        "observed_at": "2026-07-24T12:00:00Z",
        "records": [
            {
                "source_listing_id": "LIVE-LST-001",
                "snapshot_id": "partner-snapshot-20260724T120000Z",
                "address_raw": "台北市大安區復興南路二段100號1樓",
                "rent_amount": 45_000.0,
                "currency": "TWD",
                "area_ping": 25.5,
                "floor": "1F",
                "available_from": "2026-08-01",
                "listing_status": "active",
                "latitude": 25.026,
                "longitude": 121.543,
                "confidence": 0.95,
            }
        ],
    }


def _state(
    *,
    status: int = 200,
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
    delay_seconds: float = 0.0,
) -> _ServerState:
    return _ServerState(
        status=status,
        body=json.dumps(_payload() if payload is None else payload).encode(),
        headers=headers or {},
        delay_seconds=delay_seconds,
    )


def test_http_client_performs_real_bounded_request_without_sentinel_behavior() -> None:
    state = _state()
    with _http_server(state) as endpoint:
        sentinel_like_key = "unauthorized" + "_key"
        response = ListingFeedClient(
            endpoint,
            sentinel_like_key,
            allow_insecure_localhost=True,
        ).fetch_listings(correlation_id="corr-live-http-1")

    assert response.payload["snapshot_id"] == "partner-snapshot-20260724T120000Z"
    assert response.checksum_sha256
    assert state.requests[0]["x-api-key"] == sentinel_like_key
    assert state.requests[0]["x-correlation-id"] == "corr-live-http-1"


@pytest.mark.parametrize(
    ("status", "headers", "error_type"),
    [
        (401, {}, UnauthorizedError),
        (403, {}, UnauthorizedError),
        (429, {"Retry-After": "30"}, RateLimitError),
        (503, {}, UpstreamError),
    ],
)
def test_http_client_classifies_upstream_statuses(
    status: int,
    headers: dict[str, str],
    error_type: type[Exception],
) -> None:
    state = _state(status=status, headers=headers)
    with _http_server(state) as endpoint:
        client = ListingFeedClient(
            endpoint,
            "provider-key",
            allow_insecure_localhost=True,
        )
        with pytest.raises(error_type) as exc_info:
            client.fetch_listings(correlation_id=f"corr-status-{status}")
    assert exc_info.value.status_code == status
    if status == 429:
        assert exc_info.value.retry_after == "30"


def test_http_client_enforces_timeout_response_and_schema_bounds() -> None:
    timeout_state = _state(delay_seconds=0.15)
    with _http_server(timeout_state) as endpoint:
        with pytest.raises(TimeoutError):
            ListingFeedClient(
                endpoint,
                "provider-key",
                timeout=0.03,
                allow_insecure_localhost=True,
            ).fetch_listings(correlation_id="corr-timeout")

    oversized_state = _state(payload={"records": [{"value": "x" * 500}]})
    with _http_server(oversized_state) as endpoint:
        with pytest.raises(FeedSchemaError, match="byte limit"):
            ListingFeedClient(
                endpoint,
                "provider-key",
                max_response_bytes=100,
                allow_insecure_localhost=True,
            ).fetch_listings(correlation_id="corr-size")

    invalid_state = _state(
        payload={
            "contract_id": "listing_raw_snapshot",
            "snapshot_id": "bad",
            "records": "not-an-array",
        }
    )
    with _http_server(invalid_state) as endpoint:
        with pytest.raises(FeedSchemaError, match="records array"):
            ListingFeedClient(
                endpoint,
                "provider-key",
                allow_insecure_localhost=True,
            ).fetch_listings(correlation_id="corr-schema")


def test_http_client_requires_https_and_exact_approved_endpoint() -> None:
    with pytest.raises(ListingFeedConfigurationError, match="HTTPS"):
        ListingFeedClient(
            "http://provider.example/listings",
            "provider-key",
        ).fetch_listings(correlation_id="corr-http-rejected")

    with pytest.raises(ListingFeedConfigurationError, match="approved endpoint"):
        ListingFeedClient(
            "https://provider.example/listings",
            "provider-key",
            approved_endpoint_url="https://approved.example/listings",
        ).fetch_listings(correlation_id="corr-unapproved")


def test_live_ingestion_and_idempotency_survive_restart(tmp_path) -> None:
    database_path = tmp_path / "external-listing.sqlite3"
    state = _state()
    with _http_server(state) as endpoint:
        first_bundle = build_persistence(mode="durable", db_path=database_path)
        first_store = DocumentListingFeedIngestionStore(
            SqliteDocumentStore(first_bundle.engine)
        )
        first_adapter = LiveListingFeedAdapter(
            client=ListingFeedClient(
                endpoint,
                "provider-key",
                approved_endpoint_url=endpoint,
                allow_insecure_localhost=True,
            ),
            pipeline=ListingPipeline(
                repository=first_bundle.listing_repository,
                geo_pipeline=GeoPipeline(),
            ),
            store=first_store,
            mode="live",
            tenant_id="tenant-live-a",
        )
        first = first_adapter.process_feed(
            correlation_id="corr-restart-first",
            idempotency_key="feed-window-20260724T12",
        )
        assert first["status"] == "success"
        assert first["accepted_count"] == 1
        assert first["raw_snapshot_uri"].startswith("document://")
        assert len(first_bundle.listing_repository.list_listings()) == 1
        assert first_store.get_snapshot(
            tenant_id="tenant-live-a",
            provider_id="listing.partner_feed",
            snapshot_id=first["snapshot_id"],
            kind="raw",
        ) is not None
        first_bundle.engine.close()

        # A new process composition must replay the durable receipt before I/O.
        state.status = 503
        second_bundle = build_persistence(mode="durable", db_path=database_path)
        second_store = DocumentListingFeedIngestionStore(
            SqliteDocumentStore(second_bundle.engine)
        )
        second_adapter = LiveListingFeedAdapter(
            client=ListingFeedClient(
                endpoint,
                "provider-key",
                approved_endpoint_url=endpoint,
                allow_insecure_localhost=True,
            ),
            pipeline=ListingPipeline(
                repository=second_bundle.listing_repository,
                geo_pipeline=GeoPipeline(),
            ),
            store=second_store,
            mode="live",
            tenant_id="tenant-live-a",
        )
        replay = second_adapter.process_feed(
            correlation_id="corr-restart-second",
            idempotency_key="feed-window-20260724T12",
        )

        assert replay["status"] == "duplicate"
        assert replay["snapshot_id"] == first["snapshot_id"]
        assert replay["payload_checksum_sha256"] == first["payload_checksum_sha256"]
        assert len(second_bundle.listing_repository.list_listings()) == 1
        assert len(state.requests) == 1
        second_bundle.engine.close()


def test_live_adapter_fails_closed_without_durable_store() -> None:
    with pytest.raises(ListingFeedConfigurationError, match="durable"):
        LiveListingFeedAdapter(
            client=ListingFeedClient("https://provider.example/listings", "key"),
            pipeline=ListingPipeline(),
            mode="live",
            tenant_id="tenant-a",
        )


def test_live_adapter_rejects_fixture_replay(tmp_path) -> None:
    bundle = build_persistence(mode="durable", db_path=tmp_path / "live.sqlite3")
    adapter = LiveListingFeedAdapter(
        client=ListingFeedClient("https://provider.example/listings", "key"),
        pipeline=ListingPipeline(repository=bundle.listing_repository),
        store=DocumentListingFeedIngestionStore(SqliteDocumentStore(bundle.engine)),
        mode="live",
        tenant_id="tenant-a",
    )
    with pytest.raises(ListingFeedConfigurationError, match="prohibited"):
        adapter.process_feed(replay_payload=_payload())
    bundle.engine.close()


def test_backfill_live_mode_uses_durable_canonical_repositories(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.external_data_backfill import main

    database_path = tmp_path / "backfill.sqlite3"
    evidence_dir = tmp_path / "evidence"
    state = _state()
    with _http_server(state) as endpoint:
        monkeypatch.setenv("ODP_DEPLOY_ENV", "development")
        monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "live")
        monkeypatch.setenv(
            "ODP_PRODUCTION_PROVIDER_IDS",
            "listing.partner_feed",
        )
        monkeypatch.setenv("ODP_LISTING_PROVIDER_FEED_URL", endpoint)
        monkeypatch.setenv("ODP_LISTING_PROVIDER_API_KEY", "provider-key")
        monkeypatch.setenv("ODP_TENANT_ID", "tenant-cli")
        monkeypatch.setenv("ODP_PERSISTENCE", "durable")
        monkeypatch.setenv("ODP_DB_PATH", str(database_path))
        monkeypatch.setenv("ODP_ALLOW_INSECURE_LOCAL_PROVIDER", "true")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "external_data_backfill.py",
                "--mode",
                "live",
                "--evidence-dir",
                str(evidence_dir),
                "--idempotency-key",
                "cli-live-window-1",
            ],
        )
        assert main() == 0

    output = capsys.readouterr().out
    assert "Ingestion Status:   success" in output
    assert "document://external_data.listing_feed_snapshots/" in output
    assert (evidence_dir / "ODP-EXT-002_BACKFILL_EVIDENCE.json").exists()

    reopened = build_persistence(mode="durable", db_path=database_path)
    assert len(reopened.listing_repository.list_listings()) == 1
    reopened.engine.close()


def test_backfill_production_live_mode_rejects_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.external_data_backfill import main

    monkeypatch.setenv("ODP_DEPLOY_ENV", "production")
    monkeypatch.setenv("ODP_EXTERNAL_PROVIDER_MODE", "live")
    monkeypatch.setenv("ODP_PRODUCTION_PROVIDER_IDS", "listing.partner_feed")
    monkeypatch.setenv(
        "ODP_LISTING_PROVIDER_FEED_URL",
        "https://provider.example/listings",
    )
    monkeypatch.setenv("ODP_LISTING_PROVIDER_API_KEY", "provider-key")
    monkeypatch.setenv("ODP_TENANT_ID", "tenant-prod")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "external_data_backfill.py",
            "--mode",
            "live",
            "--persistence",
            "durable",
        ],
    )
    assert main() == 6
