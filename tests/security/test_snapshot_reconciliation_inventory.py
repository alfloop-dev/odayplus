"""A failed or partial inventory cannot establish clean reconciliation."""

import io
import json
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

from modules.external_data.application.source_snapshots import SourceSnapshotService
from shared.infrastructure.object_store.client import (
    GcsObjectStore,
    InMemoryObjectStore,
    ResidencyDeniedError,
)

TENANT = "00000000-0000-0000-0000-000000000001"
BUCKET = "taiwan-snapshots"
PREFIX = f"tenants/{TENANT}/"


def inventory(monkeypatch, pages):
    """Use the actual HTTP client with synthetic responses; never contact GCS."""
    requests = []
    pages = iter(pages)

    def open_page(request, *, timeout):
        requests.append((request, timeout))
        page = next(pages)
        if isinstance(page, Exception):
            raise page
        return io.BytesIO(json.dumps(page).encode())

    monkeypatch.setattr("shared.infrastructure.object_store.client.monotonic", lambda: 0, raising=False)
    store = GcsObjectStore(timeout_seconds=2.5)
    monkeypatch.setattr(store._opener, "open", open_page)
    monkeypatch.setattr(store, "_get_token", lambda: "synthetic-not-a-credential")
    return store, requests


def test_inventory_reads_every_page_including_empty_intermediate_page(monkeypatch):
    keys = [PREFIX + "snapshots/first/raw", PREFIX + "snapshots/last/raw"]
    store, requests = inventory(monkeypatch, [
        {"items": [{"name": keys[0]}], "nextPageToken": "opaque +/&="},
        {"nextPageToken": "last-page"},
        {"items": [{"name": keys[1]}]},
    ])
    assert store.list_objects(TENANT, BUCKET) == keys
    assert len(requests) == 3
    for index, (request, timeout) in enumerate(requests):
        parsed = urlparse(request.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com"
        assert parsed.path == f"/storage/v1/b/{BUCKET}/o"
        assert parse_qs(parsed.query) == {
            "prefix": [PREFIX], **({"pageToken": [["opaque +/&=", "last-page"][index - 1]]} if index else {}),
        }
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == "Bearer synthetic-not-a-credential"
        assert timeout == 2.5


@pytest.mark.parametrize("status", [403, 404, 503])
def test_inventory_http_error_is_not_an_empty_bucket(monkeypatch, status):
    error = urllib.error.HTTPError("https://example.test", status, "synthetic failure", {}, io.BytesIO(b"denied"))
    store, _ = inventory(monkeypatch, [error])
    with pytest.raises(OSError, match="GCS list failed") as raised:
        store.list_objects(TENANT, BUCKET)
    assert raised.value.__cause__ is error


def test_later_page_failure_never_returns_partial_inventory(monkeypatch):
    error = TimeoutError("synthetic second-page timeout")
    store, requests = inventory(monkeypatch, [
        {"items": [{"name": PREFIX + "first"}], "nextPageToken": "second"}, error,
    ])
    with pytest.raises(OSError, match="GCS list failed") as raised:
        store.list_objects(TENANT, BUCKET)
    assert raised.value.__cause__ is error
    assert len(requests) == 2


@pytest.mark.parametrize("page", [
    [], {"items": None}, {"items": [{}]}, {"items": [{"name": 7}]},
    {"nextPageToken": None}, {"nextPageToken": ""}, {"nextPageToken": 7},
])
def test_invalid_or_foreign_inventory_is_visible(monkeypatch, page):
    store, _ = inventory(monkeypatch, [page])
    with pytest.raises(OSError, match="GCS list failed"):
        store.list_objects(TENANT, BUCKET)


def test_foreign_inventory_preserves_typed_tenant_denial(monkeypatch):
    store, _ = inventory(monkeypatch, [{"items": [{"name": "tenants/another-tenant/raw"}]}])
    with pytest.raises(ResidencyDeniedError):
        store.list_objects(TENANT, BUCKET)


@pytest.mark.parametrize("elapsed_per_page", [[1.0, 1.0, 1.0], [3.0], [0.6] * 5])
def test_inventory_total_deadline_bounds_distinct_tokens_and_late_final_page(monkeypatch, elapsed_per_page):
    clock, timeouts = [0.0], []
    store, _ = inventory(monkeypatch, [])
    monkeypatch.setattr("shared.infrastructure.object_store.client.monotonic", lambda: clock[0], raising=False)

    def open_page(_request, *, timeout):
        index = len(timeouts)
        assert index < len(elapsed_per_page), "Inventory exceeded its total budget"
        assert timeout == pytest.approx(2.5 - clock[0])
        timeouts.append(timeout)
        clock[0] += elapsed_per_page[index]
        body = {"items": [{"name": PREFIX + str(index)}]}
        if len(elapsed_per_page) != 1:
            body["nextPageToken"] = "distinct-" + str(index)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(store._opener, "open", open_page)
    with pytest.raises(OSError) as raised:
        store.list_objects(TENANT, BUCKET)
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert len(timeouts) == len(elapsed_per_page)
    assert all(0 < value <= 2.5 for value in timeouts)


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
def test_inventory_invalid_total_budget_never_opens_socket(monkeypatch, budget):
    store, requests = inventory(monkeypatch, [])
    store._timeout_seconds = budget
    with pytest.raises(OSError):
        store.list_objects(TENANT, BUCKET)
    assert not requests


def test_repeated_page_token_fails_instead_of_looping_or_reporting_clean(monkeypatch):
    store, requests = inventory(monkeypatch, [
        {"items": [{"name": PREFIX + "first"}], "nextPageToken": "same"},
        {"nextPageToken": "same"},
    ])
    with pytest.raises(OSError, match="GCS list failed"):
        store.list_objects(TENANT, BUCKET)
    assert len(requests) == 2


@pytest.mark.parametrize("error", [PermissionError("synthetic denied"), TimeoutError("synthetic timeout")])
def test_reconciliation_propagates_inventory_failure_without_clean_receipt(error):
    class UnavailableInventory(InMemoryObjectStore):
        def list_objects(self, tenant_id, bucket):
            assert (tenant_id, bucket) == (TENANT, BUCKET)
            raise error

    service = SourceSnapshotService(None, UnavailableInventory())
    with pytest.raises(type(error)) as raised:
        service.reconcile_snapshots(TENANT, BUCKET)
    assert raised.value is error
    assert not service._in_memory_findings


def test_reconciliation_records_orphans_from_all_pages(monkeypatch):
    keys = [PREFIX + "snapshots/a/raw", PREFIX + "snapshots/b/raw"]
    store, requests = inventory(monkeypatch, [
        {"items": [{"name": keys[0]}], "nextPageToken": "second"},
        {"items": [{"name": keys[1]}]},
    ])
    service = SourceSnapshotService(None, store)
    assert service.reconcile_snapshots(TENANT, BUCKET) == {
        "reconciled": 0, "missing": 0, "corrupt": 0, "orphans": 2,
    }
    assert len(requests) == 2
    assert {finding["source_id"] for finding in service._in_memory_findings.values()} == {
        f"gs://{BUCKET}/{key}" for key in keys
    }


def test_successful_empty_inventory_remains_a_clean_receipt(monkeypatch):
    store, requests = inventory(monkeypatch, [{}])
    assert SourceSnapshotService(None, store).reconcile_snapshots(TENANT, BUCKET) == {
        "reconciled": 0, "missing": 0, "corrupt": 0, "orphans": 0,
    }
    assert len(requests) == 1
