from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.external_data.geo import (
    GeocodeProvider,
    GeocodeResult,
    GeoFeatureSnapshot,
    GeoPipeline,
)


@dataclass(frozen=True)
class GeocodeJobResult:
    job_id: str
    status: str
    geocoded: tuple[GeocodeResult, ...]
    feature_snapshots: tuple[GeoFeatureSnapshot, ...]
    completed_at: datetime
    warnings: tuple[str, ...] = ()


class GeocodeWorker:
    def __init__(self, provider: GeocodeProvider | None = None) -> None:
        self.pipeline = GeoPipeline(provider)

    def run(
        self,
        *,
        job_id: str,
        address_records: Iterable[Mapping[str, Any]] = (),
        poi_records: Iterable[Mapping[str, Any]] = (),
        competitor_records: Iterable[Mapping[str, Any]] = (),
        listing_records: Iterable[Mapping[str, Any]] = (),
        feature_resolution: int = 9,
        feature_snapshot_time: datetime | None = None,
    ) -> GeocodeJobResult:
        geocoded = tuple(self.pipeline.geocode_record(record) for record in address_records)
        features = tuple(
            self.pipeline.build_feature_snapshots(
                poi_records=poi_records,
                competitor_records=competitor_records,
                listing_records=listing_records,
                resolution=feature_resolution,
                feature_snapshot_time=feature_snapshot_time,
            )
        )
        warnings = tuple(
            f"{result.address.normalized_address}: {','.join(result.quality_flags)}"
            for result in geocoded
            if result.quality_flags
        )
        return GeocodeJobResult(
            job_id=job_id,
            status="succeeded",
            geocoded=geocoded,
            feature_snapshots=features,
            completed_at=datetime.now(UTC),
            warnings=warnings,
        )


def run_geocode_job(
    *,
    job_id: str,
    provider: GeocodeProvider | None = None,
    address_records: Iterable[Mapping[str, Any]] = (),
    poi_records: Iterable[Mapping[str, Any]] = (),
    competitor_records: Iterable[Mapping[str, Any]] = (),
    listing_records: Iterable[Mapping[str, Any]] = (),
    feature_resolution: int = 9,
    feature_snapshot_time: datetime | None = None,
) -> GeocodeJobResult:
    return GeocodeWorker(provider).run(
        job_id=job_id,
        address_records=address_records,
        poi_records=poi_records,
        competitor_records=competitor_records,
        listing_records=listing_records,
        feature_resolution=feature_resolution,
        feature_snapshot_time=feature_snapshot_time,
    )


__all__ = ["GeocodeJobResult", "GeocodeWorker", "run_geocode_job"]
