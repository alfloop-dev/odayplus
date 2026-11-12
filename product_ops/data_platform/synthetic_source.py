"""Deterministic synthetic fongniao_prod source generator (dev seeding tool).

This is an *operator seeding tool*, not a data-plane source. It emits MongoDB
documents shaped exactly like the approved ``fongniao_prod`` collections
(``apps/data_platform/source.py`` projections and ``apps/data_platform/mapping.py``
canonical contracts) so the real, unmodified backfill
(``product_ops/data_platform/backfill.py`` → ``apps.data_platform.pipeline``) can
load them into a *sample* Mongo and on to PostgreSQL.

It deliberately lives outside ``apps/data_platform`` and is never imported by
``DataPlaneRunner.from_env`` — the production constructor stays fixture/synthetic
free (enforced by
``apps/data_platform/tests/test_pipeline.py::test_production_constructor_has_no_fixture_or_synthetic_fallback``).
The sample↔real swap is a single seam: point ``ODP_DATA_MONGO_URI`` at the sample
Mongo now, at real fongniao Mongo later. Nothing here touches that path.

Design invariants:
  * Every emitted document passes the real canonical projection without a
    ``QuarantineReason`` — verified in-process via ``verify_bundle`` using the
    genuine ``apps.data_platform.mapping`` projectors, so this generator cannot
    drift from the contracts.
  * Output is a pure function of ``SyntheticConfig`` (seeded RNG) — reruns are
    byte-identical, which keeps content-addressed snapshots idempotent.
  * Tenant/brand/store identity is derived by the production
    ``apps.data_platform.identifiers`` helpers, so the derived tenant UUID is
    knowable up front (printed by the CLI) and the operator console can be scoped
    to it.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.data_platform.contracts import SourceKind

# --- Realistic Taiwan store geography (lon, lat, city, district) -------------
# Real municipal coordinates so normalize_address / H3 indexing produce genuine
# market cells rather than being dropped as out-of-market points.
_TW_LOCATIONS: tuple[tuple[str, str, float, float], ...] = (
    ("台北市", "信義區", 121.5680, 25.0330),
    ("台北市", "大安區", 121.5435, 25.0265),
    ("台北市", "中山區", 121.5330, 25.0640),
    ("台北市", "松山區", 121.5577, 25.0497),
    ("新北市", "板橋區", 121.4628, 25.0118),
    ("新北市", "新店區", 121.5418, 24.9677),
    ("新北市", "三重區", 121.4884, 25.0615),
    ("桃園市", "中壢區", 121.2250, 24.9537),
    ("桃園市", "桃園區", 121.3010, 24.9937),
    ("台中市", "西屯區", 120.6420, 24.1817),
    ("台中市", "北區", 120.6839, 24.1640),
    ("台南市", "東區", 120.2270, 22.9860),
    ("高雄市", "前鎮區", 120.3133, 22.5880),
    ("高雄市", "左營區", 120.2949, 22.6760),
    ("新竹市", "東區", 120.9810, 24.8015),
)

_STORE_FORMATS: tuple[str, ...] = ("laundry", "laundry", "laundry", "vending")
_GATEWAYS: tuple[str, ...] = ("linepay", "jkopay", "creditcard", "applepay")
_PRODUCT_CATALOG: tuple[str, ...] = (
    "標準洗衣",
    "烘衣",
    "精緻洗衣",
    "鞋類清洗",
    "寢具清洗",
)


@dataclass(frozen=True)
class SyntheticConfig:
    """All knobs for a deterministic synthetic fongniao_prod dataset."""

    merchant_source_id: str = "ODP-DEMO-FONGNIAO-001"
    merchant_name: str = "蜂鳥洗衣（示範）"
    store_count: int = 15
    devices_per_store: int = 2
    #: Inclusive window start for daily history. End is ``as_of`` (yesterday).
    years_of_history: int = 3
    #: "today"; history ends the day before, forecasts run forward from here.
    as_of: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    )
    forecast_horizon_days: int = 90
    #: Recent window (days) for which per-order rows are emitted; older revenue is
    #: carried compactly by device_daily_statistics.
    order_history_days: int = 180
    orders_per_store_per_day: int = 2
    members: int = 200
    seed: int = 20261111

    @property
    def history_start(self) -> datetime:
        return self.as_of - timedelta(days=365 * self.years_of_history)

    @property
    def history_end(self) -> datetime:
        # Daily statistics close the day before as_of so no row is future-dated.
        return self.as_of - timedelta(days=1)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _daterange(start: datetime, end: datetime) -> Iterator[datetime]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def _seasonal_factor(day: datetime) -> float:
    """Weekly + yearly seasonality so revenue curves look alive, not flat."""
    import math

    weekday_boost = 1.18 if day.weekday() >= 5 else 1.0  # weekends busier
    yearly = 1.0 + 0.15 * math.sin((day.timetuple().tm_yday / 365.0) * 2 * math.pi)
    return weekday_boost * yearly


class SyntheticDataset:
    """Holds generated dimension + fact documents for one tenant."""

    def __init__(self, config: SyntheticConfig) -> None:
        self.config = config
        self._rng = random.Random(config.seed)
        self.collections: dict[SourceKind, list[dict[str, Any]]] = {
            kind: [] for kind in SourceKind
        }
        self._place_ids: list[str] = []
        self._device_ids: list[str] = []
        self._place_of_device: dict[str, str] = {}
        self._generate()

    # -- public ---------------------------------------------------------------
    def documents(self, kind: SourceKind) -> list[dict[str, Any]]:
        return self.collections[kind]

    def counts(self) -> dict[str, int]:
        return {
            kind.value: len(docs)
            for kind, docs in self.collections.items()
            if docs
        }

    # -- generation -----------------------------------------------------------
    def _generate(self) -> None:
        self._gen_merchant()
        self._gen_places_and_devices()
        self._gen_products()
        self._gen_daily_statistics()
        self._gen_forecasts()
        self._gen_orders()
        self._gen_campaigns_and_promotions()
        self._gen_members()
        self._gen_device_logs()

    def _gen_merchant(self) -> None:
        cfg = self.config
        self.collections[SourceKind.MERCHANT].append(
            {
                "_id": cfg.merchant_source_id,
                "id": cfg.merchant_source_id,
                "companyName": cfg.merchant_name,
                "country": "TW",
                "currency": "TWD",
                "operation": "active",
                "createdAt": _iso(cfg.history_start),
                "updatedAt": _iso(cfg.history_start),
            }
        )

    def _gen_places_and_devices(self) -> None:
        cfg = self.config
        merchant_ref = {"_id": cfg.merchant_source_id}
        for index in range(cfg.store_count):
            city, district, lon, lat = _TW_LOCATIONS[index % len(_TW_LOCATIONS)]
            place_id = f"{cfg.merchant_source_id}-STORE-{index + 1:03d}"
            self._place_ids.append(place_id)
            # Stagger store openings across the history window so the展店/店網
            # panel shows a growth curve rather than every store opening at once.
            opened = cfg.history_start + timedelta(
                days=int(index / max(cfg.store_count - 1, 1) * 365 * cfg.years_of_history * 0.8)
            )
            self.collections[SourceKind.PLACE].append(
                {
                    "_id": place_id,
                    "id": place_id,
                    "title": f"{city}{district}門市",
                    "address": {
                        "city": city,
                        "district": district,
                        "full": f"{city}{district}示範路{index + 1}號",
                    },
                    "geolocation": {"type": "Point", "coordinates": [lon, lat]},
                    "merchant": dict(merchant_ref),
                    "merchantId": cfg.merchant_source_id,
                    "operation": "active",
                    "publish": True,
                    "type": _STORE_FORMATS[index % len(_STORE_FORMATS)],
                    "createdAt": _iso(opened),
                    "updatedAt": _iso(opened),
                }
            )
            for d in range(cfg.devices_per_store):
                device_id = f"{place_id}-DEV-{d + 1:02d}"
                self._device_ids.append(device_id)
                self._place_of_device[device_id] = place_id
                self.collections[SourceKind.DEVICE].append(
                    {
                        "_id": device_id,
                        "id": device_id,
                        "hwid": f"HW-{index + 1:03d}{d + 1:02d}",
                        "merchant": dict(merchant_ref),
                        "place": {"_id": place_id},
                        "product": {"_id": "WASHER-STD"},
                        "model": "FN-W200" if d % 2 == 0 else "FN-D150",
                        "modelStatus": "online",
                        "machineType": "washer" if d % 2 == 0 else "dryer",
                        "enable": True,
                        "connection": "online",
                        "operation": "active",
                        "createdAt": _iso(opened),
                        "updatedAt": _iso(opened),
                    }
                )

    def _place_opened_at(self, place_id: str) -> datetime:
        doc = next(
            d for d in self.collections[SourceKind.PLACE] if d["_id"] == place_id
        )
        return datetime.strptime(doc["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )

    def _gen_products(self) -> None:
        cfg = self.config
        merchant_ref = {"_id": cfg.merchant_source_id}
        for i, name in enumerate(_PRODUCT_CATALOG):
            self.collections[SourceKind.PRODUCTS].append(
                {
                    "_id": f"{cfg.merchant_source_id}-PROD-{i + 1:03d}",
                    "id": f"{cfg.merchant_source_id}-PROD-{i + 1:03d}",
                    "merchant": dict(merchant_ref),
                    "name": name,
                    "category": "laundry",
                    "country": "TW",
                    "publish": True,
                    "template": {"priceTWD": 60 + i * 20, "cycleMinutes": 30 + i * 5},
                    "createdAt": _iso(cfg.history_start),
                    "updatedAt": _iso(cfg.history_start),
                }
            )

    def _gen_daily_statistics(self) -> None:
        """One row per device per operating day: the revenue/ops backbone."""
        cfg = self.config
        for device_id in self._device_ids:
            place_id = self._place_of_device[device_id]
            opened = self._place_opened_at(place_id)
            base = self._rng.uniform(1800.0, 4200.0)  # per-device daily TWD mean
            for day in _daterange(max(opened, cfg.history_start), cfg.history_end):
                factor = _seasonal_factor(day)
                noise = self._rng.uniform(0.8, 1.2)
                amount = round(base * factor * noise, 2)
                count = max(0, int(amount / self._rng.uniform(70.0, 110.0)))
                start = day.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(hours=23, minutes=59)
                self.collections[SourceKind.DEVICE_DAILY_STATISTICS].append(
                    {
                        "_id": f"{device_id}-DS-{day.strftime('%Y%m%d')}",
                        "id": f"{device_id}-DS-{day.strftime('%Y%m%d')}",
                        "device": {"_id": device_id},
                        "merchant": {"_id": cfg.merchant_source_id},
                        "place": {"_id": place_id},
                        "startDatetime": _iso(start),
                        "endDatetime": _iso(end),
                        "amount": amount,
                        "count": count,
                        "gateway": self._rng.choice(_GATEWAYS),
                        "createdAt": _iso(end),
                        "updatedAt": _iso(end),
                    }
                )

    def _gen_forecasts(self) -> None:
        """Per-store AI revenue forecast: recent history + forward horizon."""
        cfg = self.config
        forecast_start = cfg.history_end - timedelta(days=30)
        forecast_end = cfg.as_of + timedelta(days=cfg.forecast_horizon_days)
        for place_id in self._place_ids:
            opened = self._place_opened_at(place_id)
            store_base = self._rng.uniform(5000.0, 12000.0)
            for day in _daterange(max(opened, forecast_start), forecast_end):
                factor = _seasonal_factor(day)
                predict = round(store_base * factor, 2)
                self.collections[SourceKind.AI_REVENUE_STATS].append(
                    {
                        "_id": f"{place_id}-FC-{day.strftime('%Y%m%d')}",
                        "id": f"{place_id}-FC-{day.strftime('%Y%m%d')}",
                        "merchant": {"_id": cfg.merchant_source_id},
                        "place": {"_id": place_id},
                        "date": _iso(day),
                        "predict": predict,
                        "createdAt": _iso(cfg.history_end),
                        "updatedAt": _iso(cfg.history_end),
                    }
                )

    def _gen_orders(self) -> None:
        cfg = self.config
        order_start = max(
            cfg.history_start, cfg.history_end - timedelta(days=cfg.order_history_days)
        )
        for place_id in self._place_ids:
            opened = self._place_opened_at(place_id)
            for day in _daterange(max(opened, order_start), cfg.history_end):
                for n in range(cfg.orders_per_store_per_day):
                    device_id = self._rng.choice(
                        [d for d in self._device_ids if self._place_of_device[d] == place_id]
                    )
                    amount = round(self._rng.uniform(60.0, 320.0), 2)
                    ts = day.replace(
                        hour=self._rng.randint(7, 22),
                        minute=self._rng.randint(0, 59),
                    )
                    succeeded = self._rng.random() > 0.05
                    order_id = f"{place_id}-ORD-{day.strftime('%Y%m%d')}-{n + 1}"
                    gateway = self._rng.choice(_GATEWAYS)
                    self.collections[SourceKind.ORDERS].append(
                        {
                            "_id": order_id,
                            "id": order_id,
                            "orderId": order_id,
                            "merchant": {"_id": cfg.merchant_source_id},
                            "place": {"_id": place_id},
                            "device": {"_id": device_id},
                            "amount": amount,
                            "currency": "TWD",
                            "state": "TRADE_SUCCESS" if succeeded else "TRADE_FAIL",
                            "payment": {"payGateway": gateway, "currency": "TWD"},
                            "createdAt": _iso(ts),
                            "updatedAt": _iso(ts + timedelta(minutes=2)),
                        }
                    )

    def _gen_campaigns_and_promotions(self) -> None:
        cfg = self.config
        for place_id in self._place_ids:
            opened = self._place_opened_at(place_id)
            start = max(opened, cfg.history_end - timedelta(days=120))
            end = start + timedelta(days=30)
            self.collections[SourceKind.CAMPAIGN].append(
                {
                    "_id": f"{place_id}-CAMP-001",
                    "id": f"{place_id}-CAMP-001",
                    "merchant": {"_id": cfg.merchant_source_id},
                    "place": {"_id": place_id},
                    "offerName": "會員首洗 8 折",
                    "isActive": True,
                    "startDatetime": _iso(start),
                    "endDatetime": _iso(end),
                    "discountAmount": 0,
                    "discountPercentage": 20,
                    "offerType": "percentage",
                    "offerMethod": "member",
                    "createdAt": _iso(start),
                    "updatedAt": _iso(start),
                }
            )
            self.collections[SourceKind.PROMOTIONS].append(
                {
                    "_id": f"{place_id}-PROMO-001",
                    "id": f"{place_id}-PROMO-001",
                    "merchant": {"_id": cfg.merchant_source_id},
                    "place": {"_id": place_id},
                    "enabled": True,
                    "start": _iso(start),
                    "end": _iso(end),
                    "rule": {"minSpend": 100, "rewardPoints": 10},
                    "createdAt": _iso(start),
                    "updatedAt": _iso(start),
                }
            )

    def _gen_members(self) -> None:
        cfg = self.config
        for i in range(cfg.members):
            created = cfg.history_start + timedelta(
                days=self._rng.randint(0, max((cfg.history_end - cfg.history_start).days, 1))
            )
            self.collections[SourceKind.MEMBER].append(
                {
                    "_id": f"{cfg.merchant_source_id}-MBR-{i + 1:05d}",
                    "id": f"{cfg.merchant_source_id}-MBR-{i + 1:05d}",
                    "merchant": {"_id": cfg.merchant_source_id},
                    "createdAt": _iso(created),
                    "updatedAt": _iso(created),
                }
            )

    def _gen_device_logs(self) -> None:
        cfg = self.config
        for device_id in self._device_ids:
            place_id = self._place_of_device[device_id]
            opened = self._place_opened_at(place_id)
            for k in range(5):
                day = opened + timedelta(
                    days=self._rng.randint(0, max((cfg.history_end - opened).days, 1))
                )
                self.collections[SourceKind.DEVICE_LOG].append(
                    {
                        "_id": f"{device_id}-LOG-{k + 1:02d}",
                        "id": f"{device_id}-LOG-{k + 1:02d}",
                        "merchant": {"_id": cfg.merchant_source_id},
                        "place": {"_id": place_id},
                        "device": {"_id": device_id},
                        "logType": "error",
                        "logData": {
                            "errCode": self._rng.choice(["E-12", "E-42", "E-07"]),
                            "action": "raise",
                            "state": "failed",
                        },
                        "createdAt": _iso(day),
                        "updatedAt": _iso(day),
                    }
                )


# --- Verification against the real canonical contracts -----------------------

# Kinds that have a canonical projector; MEMBER is raw/minimized-only (no
# canonical projection), so it is captured but not projected.
def _build_lookup(dataset: SyntheticDataset):
    from apps.data_platform.contracts import QuarantineReason
    from apps.data_platform.identifiers import (
        brand_id_for_merchant,
        machine_id_for_device,
        store_id_for_place,
        tenant_id_for_merchant,
    )
    from apps.data_platform.mapping import (
        MachineIdentity,
        MerchantIdentity,
        MissingMappingError,
        StoreIdentity,
    )

    merchants = {
        doc["_id"]: MerchantIdentity(
            doc["_id"],
            tenant_id_for_merchant(doc["_id"]),
            brand_id_for_merchant(doc["_id"]),
        )
        for doc in dataset.documents(SourceKind.MERCHANT)
    }
    places = {
        doc["_id"]: StoreIdentity(
            doc["_id"],
            doc["merchant"]["_id"],
            tenant_id_for_merchant(doc["merchant"]["_id"]),
            brand_id_for_merchant(doc["merchant"]["_id"]),
            store_id_for_place(doc["_id"]),
        )
        for doc in dataset.documents(SourceKind.PLACE)
    }
    devices = {
        doc["_id"]: MachineIdentity(
            doc["_id"],
            tenant_id_for_merchant(doc["merchant"]["_id"]),
            store_id_for_place(doc["place"]["_id"]),
            machine_id_for_device(doc["_id"]),
        )
        for doc in dataset.documents(SourceKind.DEVICE)
    }

    class _Lookup:
        def require_merchant(self, source_merchant_id: str) -> MerchantIdentity:
            try:
                return merchants[source_merchant_id]
            except KeyError as exc:
                raise MissingMappingError(
                    QuarantineReason.MISSING_MERCHANT_MAPPING, source_merchant_id
                ) from exc

        def require_place(self, source_place_id: str) -> StoreIdentity:
            try:
                return places[source_place_id]
            except KeyError as exc:
                raise MissingMappingError(
                    QuarantineReason.MISSING_PLACE_MAPPING, source_place_id
                ) from exc

        def require_device(self, source_device_id: str) -> MachineIdentity:
            try:
                return devices[source_device_id]
            except KeyError as exc:
                raise MissingMappingError(
                    QuarantineReason.MISSING_DEVICE_MAPPING, source_device_id
                ) from exc

    return _Lookup()


def verify_bundle(dataset: SyntheticDataset) -> dict[str, Any]:
    """Run every projectable document through the REAL canonical projectors.

    Returns a report with the derived tenant id, per-kind counts, and any
    quarantine reasons. ``quarantined == 0`` means the backfill would accept the
    whole dataset without dropping a row.
    """
    from apps.data_platform.identifiers import tenant_id_for_merchant
    from apps.data_platform.mapping import (
        SourceContractError,
        project_daily_statistic,
        project_device,
        project_domain_input,
        project_forecast_input,
        project_machine_status_event,
        project_merchant,
        project_place,
        project_transaction,
    )
    from apps.data_platform.source import envelope_for_document

    lookup = _build_lookup(dataset)
    observed_at = dataset.config.as_of + timedelta(days=1)

    projectors = {
        SourceKind.MERCHANT: lambda e: project_merchant(e),
        SourceKind.PLACE: lambda e: project_place(e, lookup),
        SourceKind.DEVICE: lambda e: project_device(e, lookup),
        SourceKind.DEVICE_DAILY_STATISTICS: lambda e: project_daily_statistic(e, lookup),
        SourceKind.AI_REVENUE_STATS: lambda e: project_forecast_input(e, lookup),
        SourceKind.ORDERS: lambda e: project_transaction(e, lookup),
        SourceKind.CAMPAIGN: lambda e: project_domain_input(e, lookup),
        SourceKind.PRODUCTS: lambda e: project_domain_input(e, lookup),
        SourceKind.PROMOTIONS: lambda e: project_domain_input(e, lookup),
        SourceKind.DEVICE_LOG: lambda e: project_machine_status_event(e, lookup),
    }

    quarantine: dict[str, int] = {}
    projected = 0
    for kind, project in projectors.items():
        for document in dataset.documents(kind):
            envelope = envelope_for_document(
                kind,
                document,
                run_id="00000000-0000-4000-8000-000000000001",
                observed_at=observed_at,
            )
            try:
                project(envelope)
                projected += 1
            except SourceContractError as exc:
                key = f"{kind.value}:{exc.reason_code.value}"
                quarantine[key] = quarantine.get(key, 0) + 1

    merchant_id = dataset.config.merchant_source_id
    return {
        "merchant_source_id": merchant_id,
        "derived_tenant_id": str(tenant_id_for_merchant(merchant_id)),
        "counts": dataset.counts(),
        "projected_ok": projected,
        "quarantined": sum(quarantine.values()),
        "quarantine_reasons": quarantine,
    }


def write_bundle(dataset: SyntheticDataset, out_dir: Path) -> dict[str, int]:
    """Write one JSONL file per non-empty collection; returns written counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for kind, documents in dataset.collections.items():
        if not documents:
            continue
        path = out_dir / f"{kind.value}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for document in documents:
                handle.write(json.dumps(document, ensure_ascii=False) + "\n")
        written[kind.value] = len(documents)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic fongniao_prod source data"
    )
    parser.add_argument("--merchant-id", default=SyntheticConfig.merchant_source_id)
    parser.add_argument("--stores", type=int, default=SyntheticConfig.store_count)
    parser.add_argument("--years", type=int, default=SyntheticConfig.years_of_history)
    parser.add_argument("--out", type=Path, help="Directory for JSONL output")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Project every document through the real canonical contracts",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = SyntheticConfig(
        merchant_source_id=args.merchant_id,
        store_count=args.stores,
        years_of_history=args.years,
    )
    dataset = SyntheticDataset(config)
    report: dict[str, Any] = {
        "merchant_source_id": config.merchant_source_id,
        "counts": dataset.counts(),
    }
    if args.verify:
        report = verify_bundle(dataset)
    if args.out:
        report["written"] = write_bundle(dataset, args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("quarantined", 0) == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
