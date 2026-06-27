from __future__ import annotations

from datetime import UTC, datetime, timedelta

from modules.integration.application.identity_resolution import InMemoryIdentityResolver
from modules.integration.application.mapping import SourceToCanonicalMapper
from pipelines.data_quality import SourceBatchQualityGate
from shared.domain import Listing, Transaction


def test_source_listing_maps_to_canonical_with_lineage_and_stable_identity() -> None:
    resolver = InMemoryIdentityResolver()
    mapper = SourceToCanonicalMapper(resolver)
    payload = {
        "source_id": "591",
        "listing_id": "L-100",
        "rent": "45000",
        "area": "25.5",
        "corner_flag": "true",
        "utility_electricity_flag": "1",
        "snapshot_id": "snap-20260627",
    }

    first = mapper.map_record("listing", payload)
    second = mapper.map_record("listing", payload)

    assert isinstance(first.canonical, Listing)
    assert first.canonical.listing_id == second.canonical.listing_id
    assert first.canonical.source_listing_id == "L-100"
    assert first.canonical.source_id == "591"
    assert first.canonical.rent_amount == 45000.0
    assert first.canonical.area_ping == 25.5
    assert first.canonical.corner_flag is True
    assert first.identity.is_new is True
    assert second.identity.is_new is False
    assert {lineage.canonical_field for lineage in first.field_lineage} >= {
        "source_listing_id",
        "rent_amount",
        "area_ping",
    }


def test_transaction_mapping_preserves_temporal_lineage_and_defaults() -> None:
    mapper = SourceToCanonicalMapper()
    result = mapper.map_record(
        "transaction",
        {
            "source_system": "POS",
            "transaction_id": "TX-9",
            "store_id": "store-canonical-id",
            "machine_id": "machine-canonical-id",
            "business_time": "2026-06-27T09:00:00Z",
            "observed_at": "2026-06-27T09:00:05Z",
            "loaded_at": "2026-06-27T09:00:20Z",
            "gross_amount": "150",
            "discount_amount": "20",
            "net_amount": "130",
            "payment_method": "LINE Pay",
        },
    )

    assert isinstance(result.canonical, Transaction)
    assert result.canonical.source_transaction_id == "TX-9"
    assert result.canonical.source_system == "POS"
    assert result.canonical.event_time == datetime(2026, 6, 27, 9, 0, tzinfo=UTC)
    assert result.canonical.observation_time == datetime(2026, 6, 27, 9, 0, 5, tzinfo=UTC)
    assert result.canonical.ingested_at == datetime(2026, 6, 27, 9, 0, 20, tzinfo=UTC)
    assert result.canonical.net_amount == 130.0


def test_data_quality_gate_rejects_null_duplicate_and_pit_violations() -> None:
    gate = SourceBatchQualityGate(
        entity_type="transaction",
        source_id="POS",
        required_fields=("source_transaction_id", "store_id", "event_time", "observation_time"),
        unique_fields=("source_transaction_id",),
        max_age=timedelta(days=1),
    )

    report = gate.evaluate(
        [
            {
                "source_transaction_id": "TX-1",
                "store_id": "S1",
                "event_time": "2026-06-27T09:00:00Z",
                "observation_time": "2026-06-27T09:00:03Z",
                "ingested_at": "2026-06-27T09:00:04Z",
            },
            {
                "source_transaction_id": "TX-1",
                "store_id": "",
                "event_time": "2026-06-27T09:00:10Z",
                "observation_time": "2026-06-27T09:00:00Z",
                "ingested_at": "2026-06-27T09:00:20Z",
            },
        ],
        as_of=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
    )

    assert report.passed is False
    assert report.row_count == 2
    issue_names = [issue.check_name for issue in report.issues]
    assert "required_field" in issue_names
    assert "unique_key" in issue_names
    assert "point_in_time" in issue_names
