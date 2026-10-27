"""Read-only canonical HeatZone projection for the scoped Operator snapshot.

The listing workflow aggregate is not a HeatZone owner. Never backfill it from
this read or revive an old aggregate/fixture when the native source is absent.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import datetime
from typing import Any

import h3

from modules.heatzone.domain.scoring import HeatZoneState
from modules.opsboard.application.network_read_scope import record_in_scope


def unavailable_heat_zones(code: str = "HEAT_ZONE_SOURCE_UNAVAILABLE") -> dict[str, Any]:
    return {
        "state": "unavailable",
        "code": code,
        "source": "canonical-heatzone",
        "message": "區域資料來源尚未可用，請重新載入或通知維運人員。",
    }


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any, maximum: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def _time(value: Any) -> bool:
    if not _text(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def _nonproduction(value: Any, metadata: bool = False) -> bool:
    if isinstance(value, (list, tuple)):
        return any(_nonproduction(v, metadata) for v in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        key = str(key).replace("_", "").replace("-", "").lower()
        source = (
            metadata
            and key == "description"
            or key in {"source", "datasource", "datamode", "kind", "mode", "origin", "originkind"}
        )
        marker = key in {
            "fixture",
            "isfixture",
            "mock",
            "ismock",
            "prototype",
            "isprototype",
            "seed",
            "isseed",
        }
        if marker and item is True:
            return True
        if (
            (source or marker)
            and isinstance(item, str)
            and (
                item.strip().lower() in {"r4", "seed-r4", "operator-shell-api-envelope"}
                or re.search(
                    r"(^|[^a-z0-9])(demo|fixture|mock|prototype|seed)([^a-z0-9]|$)", item, re.I
                )
            )
        ):
            return True
        if _nonproduction(item, metadata or key in {"meta", "metadata"}):
            return True
    return False


def _zone(row: Any, tenant_id: str) -> dict[str, Any]:
    if not isinstance(row, dict) or _nonproduction(row):
        raise ValueError("Native HeatZone row required")
    cell, resolution = row.get("h3_index"), row.get("h3_resolution")
    if (
        not _text(row.get("heat_zone_id"))
        or not isinstance(cell, str)
        or not h3.is_valid_cell(cell)
        or type(resolution) is not int
        or h3.get_resolution(cell) != resolution
        or not _number(row.get("score"), 100)
        or type(row.get("priority_rank")) is not int
        or row["priority_rank"] < 1
        or row.get("state") not in {state.value for state in HeatZoneState}
        or not all(
            _number(row.get(k), 1)
            for k in (
                "unmet_demand_score",
                "format_fit_score",
                "cannibalization_risk_score",
                "rent_feasibility_score",
                "listing_availability_score",
                "confidence",
            )
        )
        or not all(_text(row.get(k)) for k in ("model_version", "feature_version"))
        or not all(
            _time(row.get(k))
            for k in ("feature_snapshot_time", "prediction_origin_time", "last_scored_at")
        )
        or not all(
            isinstance(row.get(k), (list, tuple)) and all(_text(v) for v in row[k])
            for k in ("reasons", "warnings")
        )
        or not isinstance(row.get("source_snapshot_ids"), (list, tuple))
        or not row["source_snapshot_ids"]
        or not all(_text(v) for v in row["source_snapshot_ids"])
    ):
        raise ValueError("Native HeatZone evidence is incomplete")
    # Repository tenant context is explicit. An embedded contradictory scope
    # cannot be repaired with the current principal's attributes.
    scope = row.get("scope", {})
    if not isinstance(scope, dict) or any(
        source.get(key) not in (None, tenant_id)
        for source in (row, scope)
        for key in ("tenantId", "tenant_id")
    ):
        raise ValueError("Native HeatZone tenant conflicts with its source")
    lat, lon = h3.cell_to_latlng(cell)
    label = " ".join(row[k].strip() for k in ("admin_city", "admin_district") if _text(row.get(k))) or row["heat_zone_id"]
    return {
        "id": row["heat_zone_id"],
        "tenantId": tenant_id,
        "label": label,
        "rank": row["priority_rank"],
        "centroid": [lon, lat],
        "demandGap": None,
        "competitionIndex": None,
        "cannibalizationRisk": None,
        "rentBand": "—",
        "confidence": row["confidence"],
        "recommendedLens": "demand",
        "reasons": list(row["reasons"]),
        "risks": list(row["warnings"]),
        "nextStep": "",
        "recordedMap": {
            "id": row["heat_zone_id"],
            "district": label,
            "h3": cell,
            "centroid": [lon, lat],
            "h3Resolution": resolution,
            # No native geometry in this list DTO. H3 alone does not
            # manufacture a recorded polygon or a precise listing point.
            "geometry": None,
            "score": row["score"],
            "rank": row["priority_rank"],
            "state": row["state"],
            "confidence": row["confidence"],
            "executionMode": "SHADOW" if row.get("is_shadow") is True else (
                row["execution_mode"] if _text(row.get("execution_mode")) else ""
            ),
            "warnings": list(row["warnings"]),
            "reasons": list(row["reasons"]),
            "listings": None,
            "poiCount": None,
            "competitorCount": None,
            "competitorCapacity": None,
            "competitorPressureScore": None,
            "medianListingRent": None,
            "existingStoreCount": None,
            "dataQualityScore": None,
            "unmetDemandScore": row["unmet_demand_score"],
            "formatFitScore": row["format_fit_score"],
            "cannibalizationRisk": row["cannibalization_risk_score"],
            "rentFeasibility": row["rent_feasibility_score"],
            "listingAvailability": row["listing_availability_score"],
            "modelVersion": row["model_version"],
            "featureVersion": row["feature_version"],
            "featureSnapshotTime": row["feature_snapshot_time"],
            "predictionOriginTime": row["prediction_origin_time"],
            "lastScoredAt": row["last_scored_at"],
            "sourceSnapshotIds": list(row["source_snapshot_ids"]),
        },
    }


def project_canonical_heat_zones(
    snapshot: dict[str, Any], source: dict[str, Any], *, principal: Any, selected_id: str | None
) -> dict[str, Any]:
    result = deepcopy(snapshot)
    zones: list[dict[str, Any]] = []
    availability = unavailable_heat_zones()
    try:
        if (
            _nonproduction(source)
            or source.get("tenantId") != principal.tenant_id
            or source.get("state") != "available"
            or source.get("source") != "heatzone_store.list_scores"
            or source.get("persistenceMode") != "postgresql"
            or not isinstance(source.get("rows"), list)
        ):
            raise ValueError("Canonical tenant source required")
        projected = [_zone(row, source["tenantId"]) for row in source["rows"]]
        if len({row["id"] for row in projected}) != len(projected):
            raise ValueError("Duplicate native HeatZone identity")
        # Every caller, including SITE_REVIEWER and EXECUTIVE, must pass all
        # scope axes. Missing brand/region/store/assigned-area evidence cannot
        # be supplied by a principal or one visible listing in this zone.
        zones = [
            row
            for row in projected
            if record_in_scope(
                principal,
                {
                    **row,
                    "heatZoneId": row["id"],
                },
            )
        ]
        zones.sort(key=lambda row: (row["rank"], row["id"]))
        availability = {"state": "available", "source": "canonical-heatzone", "count": len(zones)}
    except (TypeError, ValueError, AttributeError, OverflowError):
        pass
    result["heatZones"] = zones
    result["heatZoneAvailability"] = availability
    visible = {row["id"] for row in zones}
    result["selectedHeatZoneId"] = (
        selected_id if selected_id in visible else next((row["id"] for row in zones), None)
    )
    result["counts"] = {
        **result.get("counts", {}),
        "heatZones": len(zones) if availability["state"] == "available" else None,
    }
    return result
