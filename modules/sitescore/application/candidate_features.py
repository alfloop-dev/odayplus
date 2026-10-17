"""Build a production feature row from recorded candidate evidence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from models.shared_ml.production_runtime import ProductionModelInputError
from modules.sitescore.domain.scoring import SiteScoreFeatureInput


def none_safe_min(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present and len(present) == len(values) else None


def feature_from_candidate(draft: Any, *, tenant_id: str) -> SiteScoreFeatureInput:
    """Preserve missing measurements and original feature time for model admission.

    The tenant comes from the authorized caller. This builder never fills a
    missing time, location, format, or historical feature with a local default.
    The production model input validator checks completeness and freshness.
    """
    def field(name: str, default: Any = None) -> Any:
        value = draft.get(name) if isinstance(draft, Mapping) else getattr(draft, name, None)
        return default if value is None else value

    listing, address, candidate = field("listing"), field("address"), field("candidate_site")
    snapshot_time = field("feature_snapshot_time")
    if snapshot_time is not None and (
        not isinstance(snapshot_time, datetime)
        or snapshot_time.tzinfo is None
        or snapshot_time.utcoffset() is None
    ):
        raise ProductionModelInputError("SITESCORE_FEATURE_TIMEZONE_REQUIRED")

    confidence = none_safe_min(listing.effective_confidence, address.geocode_confidence)
    return SiteScoreFeatureInput(
        candidate_site_id=candidate.candidate_site_id,
        tenant_id=tenant_id,
        target_format_code=candidate.target_format_code or "",
        feature_snapshot_time=snapshot_time,
        view_version="candidate-site-view-v2",
        heat_zone_id=field("heat_zone_id", ""),
        h3_index=address.h3_res_9 or "",
        latitude=address.latitude,
        longitude=address.longitude,
        geocode_confidence=address.geocode_confidence,
        prior_90d_cell_net_revenue=field("prior_90d_cell_net_revenue"),
        prior_90d_cell_transaction_count=field("prior_90d_cell_transaction_count"),
        prior_90d_cell_store_count=field("prior_90d_cell_store_count"),
        monthly_rent=listing.rent_amount,
        area_ping=listing.area_ping,
        frontage_m=listing.frontage_m,
        average_confidence=confidence,
        data_quality_score=confidence,
        source_snapshot_ids=tuple(value for value in (listing.snapshot_id,) if value),
    )


__all__ = ["feature_from_candidate"]
