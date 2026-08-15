"""Approved mock external provider service for live-provider E2E.

This service is intentionally HTTP-based so tests exercise the same live
adapter boundary used by real providers without committing third-party secrets.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

MOCK_PROVIDER_API_KEY = "approved-mock-provider-key"  # pragma: allowlist-secret


def listing_provider_mock_payload(*, snapshot_id: str, observed_at: str) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "records": [
            {
                "snapshot_id": snapshot_id,
                "source_listing_id": "LST-MOCK-LIVE-001",
                "address_raw": "台北市大安區復興南路二段100號1樓",
                "rent_amount": 45000.0,
                "currency": "TWD",
                "area_ping": 25.5,
                "floor": "1F",
                "available_from": "2026-07-01",
                "listing_status": "active",
                "confidence": 0.86,
                "source_snapshot_time": observed_at,
            }
        ],
    }


@dataclass(frozen=True)
class ProviderMockRequest:
    path: str
    scenario: str
    api_key_seen: bool
    correlation_id: str


class ListingProviderMockService:
    """Small HTTP server covering auth, quota, and freshness scenarios."""

    def __init__(self, *, api_key: str = MOCK_PROVIDER_API_KEY) -> None:
        self.api_key = api_key
        self.requests: list[ProviderMockRequest] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def listing_feed_url(self, scenario: str) -> str:
        return f"{self.base_url}/listing-feed?scenario={scenario}"

    def start(self) -> ListingProviderMockService:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> ListingProviderMockService:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                scenario = parse_qs(parsed.query).get("scenario", ["fresh"])[0]
                key = self.headers.get("X-API-Key", "")
                correlation_id = self.headers.get("X-Correlation-Id", "")
                service.requests.append(
                    ProviderMockRequest(
                        path=parsed.path,
                        scenario=scenario,
                        api_key_seen=bool(key),
                        correlation_id=correlation_id,
                    )
                )
                if parsed.path != "/listing-feed":
                    self._send_json(404, {"error": "not_found"})
                    return
                if key != service.api_key:
                    self._send_json(401, {"error": "unauthorized", "scenario": scenario})
                    return
                if scenario == "quota":
                    self._send_json(429, {"error": "quota_exhausted", "scenario": scenario})
                    return
                if scenario == "stale":
                    self._send_json(
                        200,
                        listing_provider_mock_payload(
                            snapshot_id="listing-mock-stale-20260628",
                            observed_at="2026-06-20T00:00:00Z",
                        ),
                    )
                    return
                self._send_json(
                    200,
                    listing_provider_mock_payload(
                        snapshot_id="listing-mock-fresh-20260628",
                        observed_at="2026-06-28T09:30:00Z",
                    ),
                )

            def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


__all__ = [
    "MOCK_PROVIDER_API_KEY",
    "ListingProviderMockService",
    "ProviderMockRequest",
    "listing_provider_mock_payload",
]
