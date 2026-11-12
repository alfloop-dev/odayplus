"""The sample-Mongo loader must store fact timestamps as BSON datetimes, not
strings: the backfill window query compares against ``datetime`` bounds, and a
stored string would never match, leaving every revenue chart empty. These cover
the offline logic (date coercion, dry-run); the actual Mongo write needs a live
cluster and is exercised against Atlas.
"""

from __future__ import annotations

from datetime import datetime

from apps.data_platform.contracts import SourceKind
from product_ops.data_platform.seed_sample_mongo import (
    bson_documents,
    coerce_dates,
    main,
)
from product_ops.data_platform.synthetic_source import (
    SyntheticConfig,
    SyntheticDataset,
)


def _small() -> SyntheticDataset:
    return SyntheticDataset(
        SyntheticConfig(store_count=2, years_of_history=1, order_history_days=10, members=5)
    )


def test_fact_timestamps_become_bson_datetimes() -> None:
    doc = {
        "_id": "x",
        "startDatetime": "2026-01-02T00:00:00Z",
        "endDatetime": "2026-01-02T23:59:00Z",
        "amount": 100,
    }
    coerced = coerce_dates(SourceKind.DEVICE_DAILY_STATISTICS, doc)
    assert isinstance(coerced["startDatetime"], datetime)
    assert coerced["startDatetime"].tzinfo is not None
    assert isinstance(coerced["endDatetime"], datetime)
    # non-date fields untouched; original not mutated
    assert coerced["amount"] == 100
    assert isinstance(doc["startDatetime"], str)


def test_all_known_date_fields_across_kinds_are_datetimes() -> None:
    dataset = _small()
    bundles = bson_documents(dataset)
    date_fieldsets = {
        SourceKind.AI_REVENUE_STATS: ("date",),
        SourceKind.PROMOTIONS: ("start", "end"),
        SourceKind.ORDERS: ("createdAt", "updatedAt"),
        SourceKind.DEVICE_DAILY_STATISTICS: ("startDatetime", "endDatetime"),
    }
    for kind, fields in date_fieldsets.items():
        for doc in bundles[kind]:
            for field in fields:
                assert isinstance(doc[field], datetime), (kind, field)


def test_dry_run_does_not_require_a_uri(capsys) -> None:
    rc = main(["--stores", "2", "--years", "1", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "fongniao_prod" in out
