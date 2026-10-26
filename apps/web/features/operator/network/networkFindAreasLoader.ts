/**
 * networkFindAreasLoader.ts
 *
 * Server-side loader for the Network Find Areas workspace.
 *
 * Owned layer  : FE read-path wiring for heatzones / candidates / sitescore.
 * Not changing : backend routes, write-path (decision callbacks), fixture data.
 * Composes with: NetworkFindAreasWorkspace (client component, receives ApiBinding props).
 *
 * Strategy:
 *   1. Attempt to fetch from /heatzones, /listings/candidates, /sitescore/reports.
 *   2. Return ApiBinding<T> envelopes — `state` = "ready" | "empty" | "error" | "unconfigured".
 *   3. Production renders a data-unavailable gate for non-ready bindings;
 *      local and test modes may use bundled fixtures.
 *
 * NOTE: Rebalance is now loaded directly by NetworkFindAreasWorkspace from
 * /operator/network-rebalance because it owns an interactive AVM/NetPlan
 * workflow. This loader remains scoped to heatzones/candidates/SiteScore.
 */

import type { OdpApiClient } from "@oday-plus/openapi-client";
import { cellToLatLng, getResolution, isValidCell } from "h3-js";
import type { ApiBinding } from "../../../src/lib/api/binding.ts";
import { loadApiBinding } from "../../../src/lib/api/binding.ts";
import type { Candidate, Listing, OperatorHeatZone } from "../types.ts";
import { payloadContainsSeedData } from "../operatorDataMode.ts";

// ---------------------------------------------------------------------------
// Re-exported binding types used by NetworkFindAreasWorkspace
// ---------------------------------------------------------------------------

export type HeatZoneBinding = ApiBinding<OperatorHeatZone>;
export type ListingBinding = ApiBinding<Listing>;
export type CandidateBinding = ApiBinding<Candidate>;

// ---------------------------------------------------------------------------
// Adapter helpers — convert backend camelCase/snake_case shapes to frontend
// types. The adapters are intentionally defensive: unknown fields are dropped,
// missing optional fields get safe defaults.
// ---------------------------------------------------------------------------

function recordedPolygon(value: unknown): GeoJSON.Polygon | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const polygon = value as Record<string, unknown>;
  if (polygon.type !== "Polygon" || !Array.isArray(polygon.coordinates) || polygon.coordinates.length === 0) return null;
  const rings: number[][][] = [];
  for (const ring of polygon.coordinates) {
    if (!Array.isArray(ring) || ring.length < 4) return null;
    const positions: number[][] = [];
    for (const point of ring) {
      if (!Array.isArray(point) || point.length !== 2
        || typeof point[0] !== "number" || !Number.isFinite(point[0]) || Math.abs(point[0]) > 180
        || typeof point[1] !== "number" || !Number.isFinite(point[1]) || Math.abs(point[1]) > 90) return null;
      positions.push([point[0], point[1]]);
    }
    if (positions[0][0] !== positions[positions.length - 1][0]
      || positions[0][1] !== positions[positions.length - 1][1]) return null;
    rings.push(positions);
  }
  return { type: "Polygon", coordinates: rings };
}

function recordedHeatZoneEvidence(raw: Record<string, unknown>): OperatorHeatZone["mapEvidence"] {
  const h3 = raw.h3_index;
  const number = (key: string, max = 1) => {
    const value = raw[key];
    return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max;
  };
  const text = (key: string) => {
    const value = raw[key];
    return typeof value === "string" && value.trim().length > 0;
  };
  const time = (key: string) => text(key) && Number.isFinite(Date.parse(raw[key] as string));
  const states = ["UNTOUCHED", "PARTIALLY_ABSORBED", "SATURATED", "UNDER_REALIZED", "STILL_EXPANDABLE", "SUPPRESSED_LOW_CONFIDENCE"] as const;
  const state = states.find((value) => value === raw.state);
  if (!text("heat_zone_id") || typeof h3 !== "string" || !isValidCell(h3)
    || raw.h3_resolution !== getResolution(h3) || !number("score", 100)
    || typeof raw.priority_rank !== "number" || !Number.isInteger(raw.priority_rank) || raw.priority_rank < 1
    || !state || !number("confidence")
    || !["unmet_demand_score", "format_fit_score", "cannibalization_risk_score", "rent_feasibility_score", "listing_availability_score"].every((key) => number(key))
    || !text("model_version") || !text("feature_version")
    || !["feature_snapshot_time", "prediction_origin_time", "last_scored_at"].every(time)
    || !["reasons", "warnings"].every((key) => Array.isArray(raw[key])
      && (raw[key] as unknown[]).every((value) => typeof value === "string"))
    || !Array.isArray(raw.source_snapshot_ids) || raw.source_snapshot_ids.length === 0
    || !raw.source_snapshot_ids.every((id) => typeof id === "string" && id.trim().length > 0)) return undefined;
  // No geometry property in the list DTO, or explicit geometry:null, remains
  // null. A real H3 reference alone does not override the native null contract.
  return {
    h3Index: h3, h3Resolution: getResolution(h3), geometry: recordedPolygon(raw.geometry),
    score: raw.score as number, priorityRank: raw.priority_rank, state, confidence: raw.confidence as number,
    unmetDemandScore: raw.unmet_demand_score as number, formatFitScore: raw.format_fit_score as number,
    cannibalizationRisk: raw.cannibalization_risk_score as number, rentFeasibility: raw.rent_feasibility_score as number,
    listingAvailability: raw.listing_availability_score as number, modelVersion: raw.model_version as string,
    featureVersion: raw.feature_version as string, featureSnapshotTime: raw.feature_snapshot_time as string,
    predictionOriginTime: raw.prediction_origin_time as string, lastScoredAt: raw.last_scored_at as string,
    sourceSnapshotIds: [...raw.source_snapshot_ids] as string[],
  };
}

function adaptHeatZone(raw: Record<string, unknown>): OperatorHeatZone {
  // The backend scores use h3_index as the stable zone id; we propagate it
  // directly so the frontend lens map can show real IDs.
  const mapEvidence = recordedHeatZoneEvidence(raw);
  const id = (typeof raw.heat_zone_id === "string" && raw.heat_zone_id.trim() ? raw.heat_zone_id : undefined)
    ?? (raw["h3_index"] as string | undefined) ?? String(raw["id"] ?? "HZ-UNKNOWN");
  const unitMetric = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
  const demandGap = unitMetric(raw.demand_gap ?? raw.demandGap);
  const rank = raw.rank;
  const risk = raw.cannibalization_risk ?? raw.cannibalizationRisk;
  const recordedRisk = typeof risk === "string" && ["low", "medium", "high", "critical"].includes(risk)
    ? risk as OperatorHeatZone["cannibalizationRisk"] : null;
  const confidence = unitMetric(raw.confidence);
  const nativeCentroid = mapEvidence ? cellToLatLng(mapEvidence.h3Index) : null;

  return {
    id,
    label: (raw["label"] as string | undefined) ?? id,
    rank: mapEvidence?.priorityRank ?? (typeof rank === "number" && Number.isInteger(rank) && rank >= 1 ? rank : null),
    // A missing coordinate is unavailable, never a recorded [0,0].
    centroid: nativeCentroid ? [nativeCentroid[1], nativeCentroid[0]] : Array.isArray(raw.centroid) && raw.centroid.length === 2
      && raw.centroid.every((value) => typeof value === "number" && Number.isFinite(value))
      && Math.abs(raw.centroid[0]) <= 180 && Math.abs(raw.centroid[1]) <= 90
      ? [raw.centroid[0], raw.centroid[1]] : null,
    demandGap,
    competitionIndex: unitMetric(raw.competition_index ?? raw.competitionIndex),
    cannibalizationRisk: recordedRisk,
    rentBand: (raw["rent_band"] as string | undefined) ?? (raw["rentBand"] as string | undefined) ?? "N/A",
    confidence,
    recommendedLens:
      (raw["recommended_lens"] as OperatorHeatZone["recommendedLens"]) ??
      (raw["recommendedLens"] as OperatorHeatZone["recommendedLens"]) ??
      "demand",
    reasons: Array.isArray(raw["reasons"]) ? (raw["reasons"] as string[]) : [],
    risks: mapEvidence ? [...raw.warnings as string[]]
      : Array.isArray(raw["risks"]) ? (raw["risks"] as string[]) : [],
    nextStep: (raw["next_step"] as string | undefined) ?? (raw["nextStep"] as string | undefined) ?? "",
    ...(mapEvidence ? { mapEvidence } : {}),
  };
}

function measuredScore(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function recommendationValue(value: unknown): Candidate["recommendation"] {
  if (typeof value !== "string") return null;
  const normalized = value.toUpperCase();
  return normalized === "GO" || normalized === "WAIT" || normalized === "REJECT" || normalized === "INVESTIGATE"
    ? normalized : null;
}

function recordedString(raw: Record<string, unknown>, primary: string, secondary: string): string | null {
  const value = primary in raw ? raw[primary] : raw[secondary];
  return typeof value === "string" && value.trim() ? value : null;
}

function adaptCandidate(raw: Record<string, unknown>): Candidate {
  const id =
    (raw["candidateSiteId"] as string | undefined) ??
    (raw["candidate_site_id"] as string | undefined) ??
    (raw["id"] as string | undefined) ??
    "CS-UNKNOWN";

  const recommendation = recommendationValue(
    "recommendation" in raw ? raw["recommendation"] : raw["final_recommendation"],
  );

  return {
    id,
    listingId: (raw["listing_id"] as string | undefined) ?? (raw["listingId"] as string | undefined),
    heatZoneId:
      (raw["heatZone"] as string | undefined) ??
      (raw["heat_zone_id"] as string | undefined) ??
      (raw["heatZoneId"] as string | undefined) ??
      "",
    title: (raw["title"] as string | undefined) ?? (raw["address"] as string | undefined) ?? id,
    address: (raw["address"] as string | undefined) ?? "",
    latitude: finiteNumber(raw["latitude"]),
    longitude: finiteNumber(raw["longitude"]),
    geocodeConfidence: finiteNumber(raw["geocodeConfidence"]),
    h3Index: recordedString(raw, "h3Index", "h3_index"),
    status: (raw["siteStatus"] as Candidate["status"]) ?? (raw["status"] as Candidate["status"]) ?? "new",
    score: measuredScore("score" in raw ? raw["score"] : raw["composite_score"]),
    recommendation,
    modelVersion: recordedString(raw, "modelVersion", "model_version"),
    datasetSnapshotId: recordedString(raw, "datasetSnapshotId", "dataset_snapshot_id"),
    missingData: Array.isArray(raw["missingData"])
      ? (raw["missingData"] as string[])
      : Array.isArray(raw["feasibilityFlags"])
        ? (raw["feasibilityFlags"] as string[])
        : [],
    reviewId: (raw["review_id"] as string | undefined) ?? (raw["reviewId"] as string | undefined),
  };
}

// ---------------------------------------------------------------------------
// Public loader — called from Next.js server components or client useEffect
// ---------------------------------------------------------------------------

export type NetworkFindAreasBindings = {
  heatZones: HeatZoneBinding;
  candidates: CandidateBinding;
};

/**
 * Fetch live bindings for the Network Find Areas workspace.
 *
 * Fetches:
 *   - GET /heatzones               → heatzone lens scores
 *   - GET /listings/candidates     → candidate sites (card format)
 *   - GET /sitescore/reports       → latest sitescore per candidate (enrichment)
 *
 * SiteScore reports are merged into the candidates binding so the SiteScore
 * Lab tab renders authoritative scores without a separate prop. Enrichment is
 * best-effort — a report fetch failure keeps the raw candidate values.
 *
 * Rebalance stores are intentionally omitted here; the client workspace loads
 * /operator/network-rebalance so scenario selection can persist/reload.
 *
 * Pass `client: null` (e.g. when `ODP_API_BASE_URL` is unset) to get
   * `unconfigured` envelopes. Production callers fail closed on that state.
 */
export async function loadNetworkFindAreasBindings(
  client: OdpApiClient | null,
): Promise<NetworkFindAreasBindings> {
  const [heatZones, rawCandidateBinding] = await Promise.all([
    loadApiBinding<OperatorHeatZone>({
      client,
      fetcher: async (c) => {
        const response = await c.listHeatzones();
        if (payloadContainsSeedData(response)) {
          throw new Error("HeatZone API returned seed, fixture, or mock data");
        }
        return response.items.map((item) =>
          adaptHeatZone(item as unknown as Record<string, unknown>),
        );
      },
    }),
    loadApiBinding<Candidate>({
      client,
      fetcher: async (c) => {
        const response = await c.listCandidates();
        if (payloadContainsSeedData(response)) {
          throw new Error("Candidate API returned seed, fixture, or mock data");
        }
        return response.candidates.map((item) =>
          adaptCandidate(item as unknown as Record<string, unknown>),
        );
      },
    }),
  ]);

  // Attempt to enrich candidates with SiteScore report data. A failure here
  // does NOT degrade the candidate binding — original unenriched values hold.
  let candidates = rawCandidateBinding;
  if (client && rawCandidateBinding.source === "api" && rawCandidateBinding.items.length > 0) {
    try {
      const reportResponse = await client.listSiteScoreReports();
      if (payloadContainsSeedData(reportResponse)) {
        throw new Error("SiteScore API returned seed, fixture, or mock data");
      }
      const reportByCandidateId = new Map(
        (reportResponse.items ?? []).map(
          (r) => [r.candidateSiteId as string, r as unknown as Record<string, unknown>],
        ),
      );
      const enrichedItems = rawCandidateBinding.items.map((candidate) => {
        const report = reportByCandidateId.get(candidate.id);
        if (!report) return candidate;
        const recommendation = recommendationValue(report["recommendation"]);
        const confidence = report["confidence"];
        const confidenceStatus = report["confidenceStatus"] ?? report["confidence_status"];
        const measuredConfidence = confidenceStatus === "measured" && typeof confidence === "number"
          && Number.isFinite(confidence) && confidence >= 0 && confidence <= 1
          ? confidence : null;
        return {
          ...candidate,
          score: null,
          confidence: measuredConfidence,
          status: (["new", "scoring", "SCORING_FAILED", "CANDIDATE"].includes(candidate.status)
            ? "scored" : candidate.status) as Candidate["status"],
          recommendation,
          modelVersion: (report["modelVersion"] as string | undefined) ?? candidate.modelVersion,
        };
      });
      candidates = { ...rawCandidateBinding, items: enrichedItems };
    } catch {
      // Enrichment is best-effort; keep the raw candidate binding on failure.
    }
  }

  return { heatZones, candidates };
}
