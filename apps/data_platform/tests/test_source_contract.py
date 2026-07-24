from __future__ import annotations

from datetime import UTC, datetime

from apps.data_platform.contracts import SourceKind
from apps.data_platform.source import (
    MEMBER_MINIMIZED_PROJECTION,
    MongoSource,
    SOURCE_PROJECTIONS,
    envelope_for_document,
)


def test_every_inventory_collection_has_an_explicit_projection() -> None:
    assert set(SOURCE_PROJECTIONS) == set(SourceKind)
    for kind, projection in SOURCE_PROJECTIONS.items():
        assert projection["_id"] == 1, kind
        assert "createdAt" in projection or kind is SourceKind.AI_REVENUE_STATS


def test_member_projection_minimizes_sensitive_fields() -> None:
    assert set(MEMBER_MINIMIZED_PROJECTION) == {
        "_id",
        "id",
        "merchant",
        "createdAt",
        "updatedAt",
    }
    forbidden = {"name", "email", "phone", "address", "payment", "account"}
    assert forbidden.isdisjoint(MEMBER_MINIMIZED_PROJECTION)


def test_malformed_source_time_still_produces_raw_snapshot() -> None:
    envelope = envelope_for_document(
        SourceKind.MERCHANT,
        {
            "_id": "6a4e1c",
            "companyName": "test merchant",
            "updatedAt": "not-a-date",
        },
        run_id="00000000-0000-4000-8000-000000000001",
        observed_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    assert envelope.source_updated_at is None
    assert envelope.source_snapshot_id
    assert envelope.content_sha256


def test_content_addressed_snapshot_is_idempotent() -> None:
    document = {
        "_id": "merchant-1",
        "companyName": "Merchant",
        "country": "TW",
        "currency": "TWD",
        "createdAt": "2024-01-01T00:00:00Z",
    }
    first = envelope_for_document(
        SourceKind.MERCHANT,
        document,
        run_id="00000000-0000-4000-8000-000000000001",
        observed_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    second = envelope_for_document(
        SourceKind.MERCHANT,
        document,
        run_id="00000000-0000-4000-8000-000000000002",
        observed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    assert first.source_snapshot_id == second.source_snapshot_id
    assert first.content_sha256 == second.content_sha256


def test_snapshot_dimensions_do_not_drop_rows_missing_source_dates() -> None:
    from apps.data_platform.contracts import BackfillWindow

    window = BackfillWindow(
        datetime(2026, 7, 23, tzinfo=UTC),
        datetime(2026, 7, 24, tzinfo=UTC),
        "2026-07-23",
    )
    assert MongoSource._window_query(SourceKind.MERCHANT, window, None) == {}
    assert MongoSource._window_query(SourceKind.PLACE, window, "abc") == {
        "_id": {"$gt": "abc"}
    }


def test_fact_window_uses_live_event_time_field() -> None:
    from apps.data_platform.contracts import BackfillWindow

    window = BackfillWindow(
        datetime(2026, 7, 23, tzinfo=UTC),
        datetime(2026, 7, 24, tzinfo=UTC),
        "2026-07-23",
    )
    query = MongoSource._window_query(
        SourceKind.DEVICE_DAILY_STATISTICS, window, None
    )
    assert {
        "startDatetime": {"$gte": window.start, "$lt": window.end}
    } in query["$or"]
