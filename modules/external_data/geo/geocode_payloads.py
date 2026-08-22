"""Geocode payload mapping retained after XR-CUTOVER-001.

Turning a provider's response body into a :class:`GeocodeCandidate` is a pure
mapping: it reads a payload this repository already holds and never opens a
connection. The live geocode client that used to own it was decommissioned
with the rest of odayplus-side external ingestion by XR-CUTOVER-001, so the
mapper moves here, next to the geo pipeline it feeds, and becomes public API.

The mapper is deliberately total. A payload missing or misspelling its
coordinates yields a candidate flagged ``malformed_provider_response`` at
confidence 0 rather than an exception, so a single bad record is a quality
signal the pipeline can quarantine instead of a failure that aborts a batch.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from modules.external_data.geo.pipeline import GeocodeCandidate, NormalizedAddress


def normalized_geocode_precision(value: str) -> str:
    """Fold provider precision vocabularies onto the canonical one."""
    precision = value.strip().lower()
    if precision == "address":
        return "rooftop"
    return precision or "unknown"


def parse_provider_datetime(value: Any) -> datetime | None:
    """Parse a provider timestamp, defaulting a naive value to UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def candidate_from_geocode_payload(
    payload: Mapping[str, Any],
    normalized_address: NormalizedAddress,
    provider_id: str,
) -> GeocodeCandidate | None:
    """Map one geocode response payload onto a candidate, or ``None`` if empty."""
    if not payload:
        return None
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else payload
    request_id = str(
        payload.get("request_id")
        or payload.get("provider_request_id")
        or result.get("request_id")
        or result.get("provider_request_id")
        or ""
    )
    observed_at = parse_provider_datetime(
        payload.get("observed_at")
        or payload.get("provider_observed_at")
        or result.get("observed_at")
        or result.get("provider_observed_at")
    )
    flags: list[str] = []
    try:
        latitude = float(result.get("latitude"))
        longitude = float(result.get("longitude"))
        confidence = float(result.get("confidence", result.get("geocode_confidence", 0.0)))
    except (TypeError, ValueError):
        latitude = 0.0
        longitude = 0.0
        confidence = 0.0
        flags.append("malformed_provider_response")
    precision = normalized_geocode_precision(
        str(result.get("precision") or result.get("geocode_precision") or "unknown")
    )
    return GeocodeCandidate(
        latitude=latitude,
        longitude=longitude,
        precision=precision,
        confidence=confidence,
        provider=str(result.get("provider_id") or result.get("geocode_provider") or provider_id),
        admin_city=str(result.get("city") or result.get("admin_city") or normalized_address.city),
        admin_district=str(
            result.get("district") or result.get("admin_district") or normalized_address.district
        ),
        provider_request_id=request_id,
        provider_observed_at=observed_at,
        quality_flags=tuple(flags),
    )


__all__ = [
    "candidate_from_geocode_payload",
    "normalized_geocode_precision",
    "parse_provider_datetime",
]
