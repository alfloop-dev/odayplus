from __future__ import annotations

from datetime import UTC, datetime

from apps.data_platform.contracts import SourceKind
from apps.data_platform.source import (
    AI_REVENUE_STATS_PROJECTION,
    CAMPAIGN_PROJECTION,
    DEVICE_DAILY_STATISTICS_PROJECTION,
    DEVICE_LOG_PROJECTION,
    DEVICE_PROJECTION,
    KMEANS_PROJECTION,
    PRODUCTS_PROJECTION,
    PRODUCT_PROJECTION,
    PROMOTION_PROJECTION,
    SOURCE_TIME_FIELDS,
    envelope_for_document,
)


def test_device_projection_matches_live_schema() -> None:
    assert {
        "id",
        "hwid",
        "merchant",
        "place",
        "product",
        "model",
        "modelStatus",
        "enable",
        "connection",
        "machineType",
    }.issubset(DEVICE_PROJECTION)
    assert "status" not in DEVICE_PROJECTION


def test_daily_statistics_projection_matches_live_schema() -> None:
    assert {"startDatetime", "endDatetime"}.issubset(
        DEVICE_DAILY_STATISTICS_PROJECTION
    )
    assert "start" not in DEVICE_DAILY_STATISTICS_PROJECTION
    assert "end" not in DEVICE_DAILY_STATISTICS_PROJECTION
    assert SOURCE_TIME_FIELDS[SourceKind.DEVICE_DAILY_STATISTICS][0] == (
        "startDatetime"
    )


def test_commercial_projections_match_live_schema() -> None:
    assert {
        "offerName",
        "isActive",
        "startDatetime",
        "endDatetime",
        "discountAmount",
        "discountPercentage",
        "offerType",
        "offerMethod",
    }.issubset(CAMPAIGN_PROJECTION)
    assert {"title", "details"}.issubset(PRODUCT_PROJECTION)
    assert {"name", "category", "country", "publish", "template"}.issubset(
        PRODUCTS_PROJECTION
    )
    assert {"enabled", "start", "end", "rule"}.issubset(PROMOTION_PROJECTION)


def test_nested_live_values_are_preserved_in_raw_evidence() -> None:
    observed_at = datetime(2026, 7, 24, tzinfo=UTC)
    examples = (
        (
            SourceKind.PRODUCT,
            {
                "_id": "product-1",
                "details": {"unknownLiveKey": {"value": 7}},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "details",
        ),
        (
            SourceKind.PRODUCTS,
            {
                "_id": "products-1",
                "template": {"unknownLiveKey": ["a", "b"]},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "template",
        ),
        (
            SourceKind.PROMOTIONS,
            {
                "_id": "promotion-1",
                "rule": {"unknownLiveKey": {"threshold": 2}},
                "createdAt": "2026-07-23T00:00:00Z",
            },
            "rule",
        ),
    )
    for kind, document, nested_field in examples:
        envelope = envelope_for_document(
            kind,
            document,
            run_id="00000000-0000-4000-8000-000000000001",
            observed_at=observed_at,
        )
        assert envelope.source_document[nested_field] == document[nested_field]


def test_kmeans_uses_segment_label_list() -> None:
    assert "segmentLabel" in KMEANS_PROJECTION
    assert "label" not in KMEANS_PROJECTION
    assert "model" not in AI_REVENUE_STATS_PROJECTION


def test_device_log_projection_and_raw_redaction() -> None:
    assert {
        "device",
        "merchant",
        "place",
        "logType",
        "logData",
    }.issubset(DEVICE_LOG_PROJECTION)
    envelope = envelope_for_document(
        SourceKind.DEVICE_LOG,
        {
            "_id": "log-1",
            "device": {"_id": "device-1"},
            "logType": "error",
            "logData": {
                "action": "raise",
                "errCode": "E-42",
                "state": "failed",
                "info": "free form",
                "payload": {"secret": "not retained"},
                "orders": [{"member": "not retained"}],
                "refundOrders": [{"member": "not retained"}],
                "result": {"free": "form"},
            },
            "createdAt": "2026-07-23T00:00:00Z",
        },
        run_id="00000000-0000-4000-8000-000000000001",
        observed_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    log_data = envelope.source_document["logData"]
    assert log_data["errCode"] == "E-42"
    assert set(log_data["_redacted_fields"]) == {
        "info",
        "orders",
        "payload",
        "refundOrders",
        "result",
    }
    assert "payload" not in log_data


def test_device_log_non_object_payload_is_never_landed_verbatim() -> None:
    envelope = envelope_for_document(
        SourceKind.DEVICE_LOG,
        {
            "_id": "log-2",
            "device": {"_id": "device-1"},
            "logType": "error",
            "logData": "free-form sensitive text",
            "createdAt": "2026-07-23T00:00:00Z",
        },
        run_id="00000000-0000-4000-8000-000000000001",
        observed_at=datetime(2026, 7, 24, tzinfo=UTC),
    )
    assert envelope.source_document["logData"] == {
        "_redacted_non_object": True
    }
