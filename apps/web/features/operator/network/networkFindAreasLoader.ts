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
import { cellToLatLng, isValidCell } from "h3-js";
import type { ApiBinding } from "../../../src/lib/api/binding.ts";
import { loadApiBinding } from "../../../src/lib/api/binding.ts";
import type { Candidate, Listing, OperatorHeatZone } from "../types.ts";
import { payloadContainsSeedData } from "../operatorDataMode.ts";
import { projectRecordedHeatZone, type RecordedOperatorHeatZone } from "./heatZoneRecordedEvidence.ts";

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

function adaptHeatZone(raw: Record<string, unknown>): RecordedOperatorHeatZone {
  // The backend scores use h3_index as the stable zone id; we propagate it
  // directly so the frontend lens map can show real IDs.
  const id = (raw["h3_index"] as string | undefined) ?? String(raw["id"] ?? "HZ-UNKNOWN");
  const unmetDemand = Number(raw["unmet_demand_score"] ?? raw["unmet_demand"] ?? raw["unmetDemand"] ?? 0);
  const confidence = raw["confidence"] != null ? Number(raw["confidence"]) : null;
  const observedCentroid = raw["centroid"];
  let centroid: [number, number];
  if (Array.isArray(observedCentroid) && observedCentroid.length === 2
    && observedCentroid.every((value) => typeof value === "number" && Number.isFinite(value))
    && Math.abs(observedCentroid[0]) <= 180 && Math.abs(observedCentroid[1]) <= 90) {
    centroid = observedCentroid as [number, number];
  } else if (observedCentroid == null && typeof raw["h3_index"] === "string" && isValidCell(raw["h3_index"])) {
    // The cell center is derived geometry, not a fabricated address geocode.
    const [lat, lng] = cellToLatLng(raw["h3_index"]);
    centroid = [lng, lat];
  } else {
    throw new Error("HEATZONE_GEOMETRY_UNAVAILABLE");
  }

  const zone: OperatorHeatZone = {
    id,
    label: (raw["label"] as string | undefined) ?? id,
    rank: Number(raw["priority_rank"] ?? raw["rank"] ?? 999),
    centroid,
    demandGap: unmetDemand,
    competitionIndex: Number(raw["competition_index"] ?? raw["competitionIndex"] ?? 0),
    cannibalizationRisk:
      (raw["cannibalization_risk"] as OperatorHeatZone["cannibalizationRisk"]) ??
      (raw["cannibalizationRisk"] as OperatorHeatZone["cannibalizationRisk"]) ??
      "low",
    rentBand: (raw["rent_band"] as string | undefined) ?? (raw["rentBand"] as string | undefined) ?? "N/A",
    confidence,
    recommendedLens:
      (raw["recommended_lens"] as OperatorHeatZone["recommendedLens"]) ??
      (raw["recommendedLens"] as OperatorHeatZone["recommendedLens"]) ??
      "demand",
    reasons: Array.isArray(raw["reasons"]) ? (raw["reasons"] as string[]) : [],
    risks: Array.isArray(raw["risks"]) ? (raw["risks"] as string[]) : [],
    nextStep: (raw["next_step"] as string | undefined) ?? (raw["nextStep"] as string | undefined) ?? "",
  };
  return { ...zone, recordedMap: projectRecordedHeatZone(raw, zone) };
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
