import { cellToBoundary, getResolution, isValidCell } from "h3-js";
import type { OperatorHeatZone } from "../types";
import type { NetworkFindAreasLens } from "../networkFindAreasViewModel";
import type { HeatZone } from "./mapTypes";

export type RecordedOperatorHeatZone = OperatorHeatZone & { recordedMap: HeatZone };
export type HeatZoneFeature = GeoJSON.Feature<GeoJSON.Polygon, HeatZone>;

const states: readonly HeatZone["state"][] = [
  "UNTOUCHED", "PARTIALLY_ABSORBED", "SATURATED", "UNDER_REALIZED",
  "STILL_EXPANDABLE", "SUPPRESSED_LOW_CONFIDENCE", "ABSTAINED",
];

function value(row: Record<string, unknown>, primary: string, alias?: string): unknown {
  return primary in row ? row[primary] : alias ? row[alias] : undefined;
}

function measured(input: unknown, maximum = Number.POSITIVE_INFINITY, integer = false): number | null {
  return typeof input === "number" && Number.isFinite(input) && input >= 0 && input <= maximum
    && (!integer || Number.isInteger(input)) ? input : null;
}

function text(input: unknown): string {
  return typeof input === "string" && input.trim() ? input : "";
}

function strings(input: unknown): string[] {
  return Array.isArray(input) && input.every((item) => typeof item === "string" && item.trim()) ? [...input] : [];
}

/** Project the score producer's observations; absent values remain absent. */
export function projectRecordedHeatZone(row: Record<string, unknown>, zone: OperatorHeatZone): HeatZone {
  const indexed = isValidCell(zone.id);
  const state = row["state"];
  return {
    id: zone.id, district: zone.label, h3: indexed ? zone.id : `h3-${zone.id}`,
    centroid: zone.centroid, h3Resolution: indexed ? getResolution(zone.id) : 9,
    score: measured(row["score"], 100),
    confidence: "confidence" in row ? measured(row["confidence"], 1) : zone.confidence,
    state: states.includes(state as HeatZone["state"]) ? state as HeatZone["state"] : "UNKNOWN",
    rank: zone.rank,
    listings: measured(row["active_listing_count"], undefined, true),
    warnings: strings(value(row, "warnings", "risks")), reasons: strings(row["reasons"]),
    modelVersion: text(value(row, "model_version", "modelVersion")),
    executionMode: row["is_shadow"] === true ? "SHADOW" : text(row["execution_mode"]),
    featureVersion: text(value(row, "feature_version", "featureVersion")),
    featureSnapshotTime: text(value(row, "feature_snapshot_time", "featureSnapshotTime")),
    predictionOriginTime: text(value(row, "prediction_origin_time", "predictionOriginTime")),
    lastScoredAt: text(value(row, "last_scored_at", "lastScoredAt")),
    sourceSnapshotIds: strings(value(row, "source_snapshot_ids", "sourceSnapshotIds")),
    unmetDemandScore: measured(value(row, "unmet_demand_score", "unmet_demand"), 1),
    formatFitScore: measured(row["format_fit_score"], 1),
    cannibalizationRisk: measured(value(row, "cannibalization_risk_score", "cannibalization_risk"), 1),
    rentFeasibility: measured(value(row, "rent_feasibility_score", "rent_feasibility"), 1),
    listingAvailability: measured(value(row, "listing_availability_score", "listing_availability"), 1),
    competitorPressureScore: measured(row["competitor_pressure_score"], 1),
    poiCount: measured(row["poi_count"], undefined, true),
    competitorCount: measured(row["competitor_count"], undefined, true),
    competitorCapacity: measured(row["competitor_capacity"]),
    medianListingRent: measured(row["median_listing_rent"]),
    existingStoreCount: measured(row["existing_store_count"], undefined, true),
    dataQualityScore: measured(row["data_quality_score"], 1),
  };
}

/** Missing factors are not re-created from neighbouring metrics or map position. */
export function recordedLensPoints(zone: HeatZone, lens: NetworkFindAreasLens): number | null {
  if (zone.state === "ABSTAINED" || zone.state === "UNKNOWN" || zone.executionMode?.toUpperCase() === "SHADOW") return null;
  let factor: number | null;
  switch (lens) {
    case "demand": case "unmet": factor = zone.unmetDemandScore; break;
    case "fit": factor = zone.formatFitScore; break;
    case "rent": factor = zone.rentFeasibility; break;
    case "confidence": factor = zone.confidence; break;
    case "cannibalization": factor = zone.cannibalizationRisk === null ? null : 1 - zone.cannibalizationRisk; break;
    case "competition": factor = zone.competitorPressureScore == null ? null : 1 - zone.competitorPressureScore; break;
    default: factor = null;
  }
  return factor === null ? null : Math.round(factor * 100);
}

export function recordedZoneDetailRows(zone: HeatZone) {
  const percent = (input: number | null | undefined) => input == null ? "未評估" : `${Math.round(input * 100)}%`;
  return [
    { key: "unmetDemand", label: "未滿足需求", value: percent(zone.unmetDemandScore) },
    { key: "formatFit", label: "店型適配", value: percent(zone.formatFitScore) },
    { key: "competition", label: "競爭壓力", value: percent(zone.competitorPressureScore) },
    { key: "cannibalization", label: "自家稀釋風險", value: percent(zone.cannibalizationRisk) },
    { key: "rentFeasibility", label: "租金可行性", value: percent(zone.rentFeasibility) },
    { key: "listingAvailability", label: "物件供給", value: percent(zone.listingAvailability) },
    { key: "confidence", label: "資料信心", value: percent(zone.confidence) },
    { key: "executionMode", label: "評估模式", value: zone.executionMode === "SHADOW" ? "影子評估" : zone.executionMode || "未知" },
  ];
}

/** A centroid locates a point; it does not supply an area's polygon. */
export function zoneToFeature(zone: HeatZone, allowFixtureGeometry = false): HeatZoneFeature | null {
  if (isValidCell(zone.h3)) {
    try {
      const ring = cellToBoundary(zone.h3, true);
      const first = ring[0];
      const last = ring[ring.length - 1];
      if (ring.length) return {
        type: "Feature", properties: zone,
        geometry: { type: "Polygon", coordinates: [
          first![0] === last![0] && first![1] === last![1] ? ring : [...ring, first!],
        ] },
      };
    } catch {
      // An invalid/unavailable live boundary must not become an invented polygon.
    }
  }
  if (!allowFixtureGeometry) return null;
  const [lng, lat] = zone.centroid;
  const delta = 0.012;
  return {
    type: "Feature", properties: zone,
    geometry: { type: "Polygon", coordinates: [[
      [lng - delta, lat - delta], [lng + delta, lat - delta],
      [lng + delta, lat + delta], [lng - delta, lat + delta], [lng - delta, lat - delta],
    ]] },
  };
}

function common(values: string[]): string {
  if (values.length === 0 || values.some((item) => !item)) return "";
  return values.every((item) => item === values[0]) ? values[0]! : "MIXED";
}

/** Timestamps and model identities do not establish a freshness policy verdict. */
export function recordedMapFreshness(zones: HeatZone[]) {
  return {
    status: "UNKNOWN",
    updatedAt: common(zones.map((zone) => zone.lastScoredAt)),
    modelVersion: common(zones.map((zone) => zone.modelVersion)),
    featureSnapshotTime: common(zones.map((zone) => zone.featureSnapshotTime)),
    sourceSnapshotId: zones.length && zones.every((zone) => zone.sourceSnapshotIds.length)
      ? [...new Set(zones.flatMap((zone) => zone.sourceSnapshotIds))].join(" / ") : "",
  };
}
