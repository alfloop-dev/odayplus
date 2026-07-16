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
 *   3. Workspace falls back to bundled fixtures for any non-"ready" binding.
 *
 * NOTE: Rebalance is now loaded directly by NetworkFindAreasWorkspace from
 * /operator/network-rebalance because it owns an interactive AVM/NetPlan
 * workflow. This loader remains scoped to heatzones/candidates/SiteScore.
 */

import type { OdpApiClient } from "@oday-plus/openapi-client";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { loadApiBinding } from "../../src/lib/api/binding.ts";
import type { Candidate, Listing, OperatorHeatZone } from "./types.ts";

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

function adaptHeatZone(raw: Record<string, unknown>): OperatorHeatZone {
  // The backend scores use h3_index as the stable zone id; we propagate it
  // directly so the frontend lens map can show real IDs.
  const id = (raw["h3_index"] as string | undefined) ?? String(raw["id"] ?? "HZ-UNKNOWN");
  const unmetDemand = Number(raw["unmet_demand"] ?? raw["unmetDemand"] ?? 0);
  const confidence = Number(raw["confidence"] ?? 0);

  return {
    id,
    label: (raw["label"] as string | undefined) ?? id,
    rank: Number(raw["rank"] ?? 999),
    // centroid: [lng, lat] — fall back to [0,0] if not provided
    centroid: Array.isArray(raw["centroid"])
      ? (raw["centroid"] as [number, number])
      : [0, 0],
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
}

function adaptCandidate(raw: Record<string, unknown>): Candidate {
  const id =
    (raw["candidateSiteId"] as string | undefined) ??
    (raw["candidate_site_id"] as string | undefined) ??
    (raw["id"] as string | undefined) ??
    "CS-UNKNOWN";

  const recommendation = (() => {
    const r =
      (raw["recommendation"] as string | undefined) ??
      (raw["final_recommendation"] as string | undefined) ??
      "WAIT";
    if (r === "GO" || r === "go") return "GO" as const;
    if (r === "REJECT" || r === "reject") return "REJECT" as const;
    return "WAIT" as const;
  })();

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
    status: (raw["status"] as Candidate["status"]) ?? "scoring",
    score: Number(raw["score"] ?? raw["composite_score"] ?? 0),
    recommendation,
    modelVersion:
      (raw["modelVersion"] as string | undefined) ??
      (raw["model_version"] as string | undefined) ??
      "unknown",
    datasetSnapshotId:
      (raw["datasetSnapshotId"] as string | undefined) ??
      (raw["dataset_snapshot_id"] as string | undefined) ??
      (raw["featureSnapshotTime"] as string | undefined) ??
      "",
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
 * `unconfigured` envelopes that tell the workspace to use fixture data.
 */
export async function loadNetworkFindAreasBindings(
  client: OdpApiClient | null,
): Promise<NetworkFindAreasBindings> {
  const [heatZones, rawCandidateBinding] = await Promise.all([
    loadApiBinding<OperatorHeatZone>({
      client,
      fetcher: async (c) => {
        const response = await c.listHeatzones();
        return response.items.map((item) =>
          adaptHeatZone(item as unknown as Record<string, unknown>),
        );
      },
    }),
    loadApiBinding<Candidate>({
      client,
      fetcher: async (c) => {
        const response = await c.listCandidates();
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
      const reportByCandidateId = new Map(
        (reportResponse.items ?? []).map(
          (r) => [r.candidateSiteId as string, r as unknown as Record<string, unknown>],
        ),
      );
      const enrichedItems = rawCandidateBinding.items.map((candidate) => {
        const report = reportByCandidateId.get(candidate.id);
        if (!report) return candidate;
        const rawRec = (report["recommendation"] as string | undefined) ?? "";
        const recommendation: Candidate["recommendation"] =
          rawRec === "GO" ? "GO" : rawRec === "REJECT" ? "REJECT" : "WAIT";
        return {
          ...candidate,
          score: Number(report["confidence"] ?? candidate.score),
          recommendation,
          modelVersion: (report["modelVersion"] as string | undefined) ?? candidate.modelVersion,
          datasetSnapshotId:
            (report["featureSnapshotTime"] as string | undefined) ?? candidate.datasetSnapshotId,
        };
      });
      candidates = { ...rawCandidateBinding, items: enrichedItems };
    } catch {
      // Enrichment is best-effort; keep the raw candidate binding on failure.
    }
  }

  return { heatZones, candidates };
}
