from __future__ import annotations

import json

import pytest

from delivery_toolchain.e2e import seed_product_e2e_data

FIXTURE_FRESHNESS = {
    "availability": {"status": "AVAILABLE", "source": "fixture"},
    "freshness": [{"source_snapshot_id": "snap-expansion-20260628-0100"}],
}
PERSISTED_FRESHNESS = {
    "availability": {"status": "AVAILABLE", "source": "persisted"},
    "freshness": [{"source_snapshot_id": "listing-2026-06-26"}],
}


def test_seed_requests_carry_verified_tenant_scope(monkeypatch) -> None:
    requests = []

    class JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode()

    def open_url(request, *, timeout: int):
        requests.append((request, timeout))
        return JsonResponse()

    monkeypatch.setattr(seed_product_e2e_data, "urlopen", open_url)

    seed_product_e2e_data.get_json("http://api/external-data/freshness")
    seed_product_e2e_data.post_json("http://api/heatzones/score-jobs", {})

    assert [request.get_header("X-tenant-id") for request, _timeout in requests] == [
        seed_product_e2e_data.TENANT_ID,
        seed_product_e2e_data.TENANT_ID,
    ]


def test_seed_creates_ingestion_run_through_tenant_scoped_api(monkeypatch) -> None:
    calls = []

    def post(url, payload):
        calls.append((url, payload))
        return {"run_id": "ingestion-run-e2e"}

    monkeypatch.setattr(seed_product_e2e_data, "post_json", post)

    result = seed_product_e2e_data.seed_tenant_ingestion("http://api")

    assert result["run_id"] == "ingestion-run-e2e"
    assert calls == [
        (
            "http://api/external-data/ingestion-runs",
            {
                "provider_id": "listing.partner_feed",
                "schedule_id": "product-e2e-seed",
                "window_start": "2026-06-28T08:00:00Z",
                "window_end": "2026-06-28T09:00:00Z",
                "idempotency_key": "product-e2e-external-ingestion-001",
            },
        )
    ]


def test_web_readiness_retries_transient_disconnect(monkeypatch) -> None:
    attempts = iter((OSError("server closed startup connection"), None))

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    def open_url(_url: str, *, timeout: int):
        assert timeout == 10
        outcome = next(attempts)
        if outcome is not None:
            raise outcome
        return ReadyResponse()

    monkeypatch.setattr(seed_product_e2e_data, "urlopen", open_url)
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _seconds: None)

    seed_product_e2e_data.wait_for_http_url("http://127.0.0.1:3100")


def test_seed_waits_past_the_fixture_freshness_fallback(monkeypatch) -> None:
    """The poc fixture fallback must not be mistaken for ingested evidence.

    ``/external-data/freshness`` serves a hardcoded fixture entry while the
    durable store is still empty, so seeding that returned at the first 200
    would hand the expansion spec whichever of the two the scheduler/worker
    start-up timing happened to produce.
    """
    responses = iter((FIXTURE_FRESHNESS, FIXTURE_FRESHNESS, PERSISTED_FRESHNESS))
    monkeypatch.setattr(seed_product_e2e_data, "get_json", lambda _url: next(responses))
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _seconds: None)

    payload = seed_product_e2e_data.wait_for_persisted_freshness("http://127.0.0.1:8099")

    assert payload == PERSISTED_FRESHNESS


def test_seed_fails_loudly_when_no_ingestion_run_is_ever_persisted(monkeypatch) -> None:
    monkeypatch.setattr(seed_product_e2e_data, "get_json", lambda _url: FIXTURE_FRESHNESS)
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError) as excinfo:
        seed_product_e2e_data.wait_for_persisted_freshness(
            "http://127.0.0.1:8099", timeout_seconds=0.01
        )

    message = str(excinfo.value)
    assert "tenant-scoped ingestion API wrote no readable run" in message
    assert "fixture" in message
