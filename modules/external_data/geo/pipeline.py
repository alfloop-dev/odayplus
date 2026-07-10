from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any, Protocol

from shared.domain import AddressLocation, GeoCell

TAIWAN_LATITUDE_RANGE = (21.8, 25.4)
TAIWAN_LONGITUDE_RANGE = (119.3, 122.1)
DEFAULT_H3_RESOLUTIONS = (8, 9, 10)

_SPACE_RE = re.compile(r"\s+")
_ROAD_RE = re.compile(r"(?P<road>[\w\u4e00-\u9fff]+(?:路|街|大道|巷|弄))")
_CITY_RE = re.compile(r"(?P<city>[\w\u4e00-\u9fff]+(?:市|縣))")
_DISTRICT_RE = re.compile(r"(?P<district>[\w\u4e00-\u9fff]+(?:區|鄉|鎮|市))")
_FLOOR_RE = re.compile(r"(?:\d+\s*[fF樓]|地下\d*樓?|B\d+|之\d+|,\s*.*$)")


@dataclass(frozen=True)
class NormalizedAddress:
    raw_address: str
    normalized_address: str
    city: str = ""
    district: str = ""
    road: str = ""
    address_normalized_version: str = "addr-normalize-v1"


@dataclass(frozen=True)
class GeocodeCandidate:
    latitude: float
    longitude: float
    precision: str
    confidence: float
    provider: str
    admin_city: str = ""
    admin_district: str = ""
    provider_request_id: str = ""
    provider_observed_at: datetime | None = None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeocodeResult:
    address: AddressLocation
    geocode_provider: str
    admin_match_flag: bool
    quality_flags: tuple[str, ...] = ()
    h3_resolution_map: Mapping[int, str] = field(default_factory=dict)
    provider_request_id: str = ""
    provider_observed_at: datetime | None = None


@dataclass(frozen=True)
class GeoFeatureSnapshot:
    h3_index: str
    h3_resolution: int
    feature_snapshot_time: datetime
    view_version: str
    poi_count: int = 0
    competitor_count: int = 0
    active_listing_count: int = 0
    median_listing_rent: float = 0.0
    competitor_capacity: float = 0.0
    average_confidence: float = 0.0
    source_snapshot_ids: tuple[str, ...] = ()


class GeocodeProvider(Protocol):
    def lookup(self, normalized_address: NormalizedAddress) -> GeocodeCandidate | None:
        ...


class StaticGeocodeProvider:
    """Deterministic in-memory geocoder for tests, fixtures, and manual overrides."""

    def __init__(self, candidates: Mapping[str, GeocodeCandidate]) -> None:
        self._candidates = {normalize_address(key).normalized_address: value for key, value in candidates.items()}

    def lookup(self, normalized_address: NormalizedAddress) -> GeocodeCandidate | None:
        return self._candidates.get(normalized_address.normalized_address)


def normalize_address(raw_address: str) -> NormalizedAddress:
    text = unicodedata.normalize("NFKC", raw_address or "")
    text = text.replace("臺", "台")
    text = _SPACE_RE.sub(" ", text).strip()
    text = _FLOOR_RE.sub("", text).strip(" ,，")
    text = text.replace(" ", "")

    city = _first_match(_CITY_RE, text, "city")
    district = _first_match(_DISTRICT_RE, text.removeprefix(city), "district") if city else _first_match(_DISTRICT_RE, text, "district")
    road = _first_match(_ROAD_RE, text, "road")
    return NormalizedAddress(
        raw_address=raw_address,
        normalized_address=text,
        city=city,
        district=district,
        road=road,
    )


def coordinates_in_market(latitude: float, longitude: float) -> bool:
    return (
        TAIWAN_LATITUDE_RANGE[0] <= latitude <= TAIWAN_LATITUDE_RANGE[1]
        and TAIWAN_LONGITUDE_RANGE[0] <= longitude <= TAIWAN_LONGITUDE_RANGE[1]
    )


def stable_h3_index(latitude: float, longitude: float, resolution: int) -> str:
    """Return a stable H3 cell key.
    """
    import h3

    if not coordinates_in_market(latitude, longitude):
        raise ValueError("coordinates are outside the configured market bounds")
    return h3.latlng_to_cell(latitude, longitude, resolution)


def build_geo_cell(result: GeocodeResult, *, resolution: int = 9) -> GeoCell:
    h3_index = result.h3_resolution_map[resolution]
    return GeoCell(
        geo_cell_id=f"geo-cell:{h3_index}",
        h3_index=h3_index,
        h3_resolution=resolution,
        parent_h3_index=result.h3_resolution_map.get(resolution - 1),
        centroid_latitude=result.address.latitude,
        centroid_longitude=result.address.longitude,
        admin_city=result.address.city,
        admin_district=result.address.district,
    )


class GeoPipeline:
    def __init__(
        self,
        provider: GeocodeProvider | None = None,
        *,
        h3_resolutions: Sequence[int] = DEFAULT_H3_RESOLUTIONS,
        view_version: str = "geo-grid-view-v1",
        max_record_age: timedelta = timedelta(days=90),
    ) -> None:
        self.provider = provider
        self.h3_resolutions = tuple(h3_resolutions)
        self.view_version = view_version
        self.max_record_age = max_record_age

    def geocode_record(self, record: Mapping[str, Any], *, as_of: datetime | None = None) -> GeocodeResult:
        normalized = normalize_address(str(record.get("address_raw") or record.get("raw_address") or record.get("address") or ""))
        flags: list[str] = []
        freshness_time = as_of or datetime.now(UTC)
        if _is_stale_record(record, freshness_time, self.max_record_age):
            flags.append("stale_source_snapshot")
        candidate = self._candidate_from_coordinates(record)
        if candidate is None and self.provider is not None:
            candidate = self.provider.lookup(normalized)
        if candidate is None:
            candidate = GeocodeCandidate(0.0, 0.0, "manual", 0.0, "unresolved")
            flags.append("missing_geocode")
        flags.extend(candidate.quality_flags)

        if not coordinates_in_market(candidate.latitude, candidate.longitude):
            flags.append("coordinates_out_of_market")
            h3_map: dict[int, str] = {}
        else:
            h3_map = {
                resolution: stable_h3_index(candidate.latitude, candidate.longitude, resolution)
                for resolution in self.h3_resolutions
            }

        admin_match = self._admin_matches(normalized, candidate)
        if not admin_match:
            flags.append("admin_mismatch")
        if candidate.confidence < 0.7:
            flags.append("low_geocode_confidence")

        address = AddressLocation(
            raw_address=normalized.raw_address,
            normalized_address=normalized.normalized_address,
            city=candidate.admin_city or normalized.city,
            district=candidate.admin_district or normalized.district,
            road=normalized.road,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            geocode_precision=candidate.precision,
            geocode_confidence=_bounded_confidence(candidate.confidence),
            h3_res_8=h3_map.get(8, ""),
            h3_res_9=h3_map.get(9, ""),
            h3_res_10=h3_map.get(10, ""),
            manual_override_flag=bool(record.get("manual_override_flag", False)),
        )
        return GeocodeResult(
            address=address,
            geocode_provider=candidate.provider,
            admin_match_flag=admin_match,
            quality_flags=tuple(flags),
            h3_resolution_map=h3_map,
            provider_request_id=candidate.provider_request_id,
            provider_observed_at=candidate.provider_observed_at,
        )

    def build_feature_snapshots(
        self,
        *,
        poi_records: Iterable[Mapping[str, Any]] = (),
        competitor_records: Iterable[Mapping[str, Any]] = (),
        listing_records: Iterable[Mapping[str, Any]] = (),
        resolution: int = 9,
        feature_snapshot_time: datetime | None = None,
    ) -> list[GeoFeatureSnapshot]:
        snapshot_time = feature_snapshot_time or datetime.now(UTC)
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "poi_count": 0,
                "competitor_count": 0,
                "active_listing_count": 0,
                "listing_rents": [],
                "competitor_capacity": 0.0,
                "confidences": [],
                "snapshot_ids": set(),
            }
        )

        for record in poi_records:
            h3_index = self._h3_for_record(record, resolution, snapshot_time)
            if h3_index is None:
                continue
            bucket = buckets[h3_index]
            bucket["poi_count"] += 1
            self._track_common(bucket, record)

        for record in competitor_records:
            h3_index = self._h3_for_record(record, resolution, snapshot_time)
            if h3_index is None:
                continue
            bucket = buckets[h3_index]
            bucket["competitor_count"] += 1
            bucket["competitor_capacity"] += _float(record.get("estimated_capacity"))
            self._track_common(bucket, record)

        for record in listing_records:
            if str(record.get("listing_status") or "active") != "active":
                continue
            h3_index = self._h3_for_record(record, resolution, snapshot_time)
            if h3_index is None:
                continue
            bucket = buckets[h3_index]
            bucket["active_listing_count"] += 1
            rent = _float(record.get("rent_amount"))
            if rent:
                bucket["listing_rents"].append(rent)
            self._track_common(bucket, record)

        return [
            GeoFeatureSnapshot(
                h3_index=h3_index,
                h3_resolution=resolution,
                feature_snapshot_time=snapshot_time,
                view_version=self.view_version,
                poi_count=values["poi_count"],
                competitor_count=values["competitor_count"],
                active_listing_count=values["active_listing_count"],
                median_listing_rent=float(median(values["listing_rents"])) if values["listing_rents"] else 0.0,
                competitor_capacity=round(values["competitor_capacity"], 4),
                average_confidence=round(sum(values["confidences"]) / len(values["confidences"]), 4)
                if values["confidences"]
                else 0.0,
                source_snapshot_ids=tuple(sorted(values["snapshot_ids"])),
            )
            for h3_index, values in sorted(buckets.items())
        ]

    def _candidate_from_coordinates(self, record: Mapping[str, Any]) -> GeocodeCandidate | None:
        latitude = record.get("latitude") or record.get("lat")
        longitude = record.get("longitude") or record.get("lng") or record.get("lon")
        if latitude in (None, "") or longitude in (None, ""):
            return None
        lat_value = _float(latitude)
        lon_value = _float(longitude)
        confidence = _bounded_confidence(record.get("confidence", 0.95))
        precision = str(record.get("geocode_precision") or "rooftop")
        return GeocodeCandidate(
            lat_value,
            lon_value,
            precision,
            confidence,
            str(record.get("geocode_provider") or "source_coordinates"),
            str(record.get("city") or ""),
            str(record.get("district") or ""),
            str(record.get("provider_request_id") or ""),
            _parse_datetime(record.get("provider_observed_at")),
        )

    def _h3_for_record(self, record: Mapping[str, Any], resolution: int, as_of: datetime) -> str | None:
        result = self.geocode_record(record, as_of=as_of)
        return result.h3_resolution_map.get(resolution)

    def _track_common(self, bucket: dict[str, Any], record: Mapping[str, Any]) -> None:
        bucket["confidences"].append(_bounded_confidence(record.get("confidence", 1.0)))
        if record.get("snapshot_id"):
            bucket["snapshot_ids"].add(str(record["snapshot_id"]))

    @staticmethod
    def _admin_matches(normalized: NormalizedAddress, candidate: GeocodeCandidate) -> bool:
        if candidate.admin_city and normalized.city and candidate.admin_city != normalized.city:
            return False
        if candidate.admin_district and normalized.district and candidate.admin_district != normalized.district:
            return False
        return True


def _first_match(pattern: re.Pattern[str], text: str, group_name: str) -> str:
    match = pattern.search(text)
    return match.group(group_name) if match else ""


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _bounded_confidence(value: Any) -> float:
    return max(0.0, min(1.0, _float(value)))


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_stale_record(record: Mapping[str, Any], as_of: datetime, max_age: timedelta) -> bool:
    timestamp = (
        _parse_datetime(record.get("source_snapshot_time"))
        or _parse_datetime(record.get("snapshot_time"))
        or _parse_datetime(record.get("observed_at"))
        or _parse_datetime(record.get("last_verified_at"))
    )
    return timestamp is not None and as_of - timestamp > max_age


__all__ = [
    "DEFAULT_H3_RESOLUTIONS",
    "GeocodeCandidate",
    "GeocodeProvider",
    "GeocodeResult",
    "GeoFeatureSnapshot",
    "GeoPipeline",
    "NormalizedAddress",
    "StaticGeocodeProvider",
    "build_geo_cell",
    "coordinates_in_market",
    "normalize_address",
    "stable_h3_index",
]
