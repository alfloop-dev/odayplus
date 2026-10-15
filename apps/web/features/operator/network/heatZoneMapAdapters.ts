/**
 * Operator → Map type adapters.
 *
 * These bridge the operator-layer types (OperatorHeatZone, Listing, Candidate)
 * to the expansion-layer types that HeatZoneMap expects. Candidate locations
 * preserve recorded address evidence; missing coordinates remain absent.
 *
 * Extracted from NetworkFindAreasWorkspace.tsx under ODP-ENG-FRONTEND-BUILD-001:
 * this layer is pure data mapping with no React or DOM dependency, so keeping it
 * inside the 1.7k-line workspace component made both harder to read and left the
 * mapping rules untestable without mounting the workspace.
 */

import type { Candidate, Listing, OperatorHeatZone } from "../types";
import { getResolution, isValidCell } from "h3-js";
import type {
  CandidateSite as MapCandidateSite,
  HeatZone as MapHeatZone,
  Listing as MapListing,
} from "./mapTypes";

/** Fallback centroid (Taipei) for records whose heat zone cannot be resolved. */
const FALLBACK_CENTROID: [number, number] = [121.48, 25.0];

export const OPERATOR_MAP_FRESHNESS = {
  status: "FRESH",
  updatedAt: "",
  modelVersion: "network-ops-local",
  featureSnapshotTime: "",
  sourceSnapshotId: "snap-network-ops-local",
};

/**
 * Spread records deterministically around their zone centroid so markers do not
 * stack on top of the zone itself.
 */
function offsetFromZone(
  heatZoneId: string,
  heatZones: OperatorHeatZone[],
  index: number,
  offset: number,
  angleStepDegrees: number,
): [number, number] {
  const zone = heatZones.find((candidate) => candidate.id === heatZoneId);
  const [lng, lat] = zone?.centroid ?? FALLBACK_CENTROID;
  const angle = (index * angleStepDegrees * Math.PI) / 180;
  return [lng + offset * Math.cos(angle), lat + offset * Math.sin(angle)];
}

/** Derive a canonical HeatZone state from OperatorHeatZone metrics. */
export function deriveHeatZoneState(zone: OperatorHeatZone): MapHeatZone["state"] {
  if (zone.confidence === null || zone.confidence < 0.7) return "SUPPRESSED_LOW_CONFIDENCE";
  if (zone.demandGap >= 0.75) return "STILL_EXPANDABLE";
  if (zone.demandGap >= 0.5) return "UNDER_REALIZED";
  if (zone.competitionIndex >= 0.7) return "SATURATED";
  return "PARTIALLY_ABSORBED";
}

/**
 * Convert an OperatorHeatZone to the MapHeatZone type expected by HeatZoneMap.
 * Fields not tracked by the operator layer are synthesised deterministically
 * so that the map renders correctly without requiring API data.
 */
export function operatorHeatZoneToMapZone(zone: OperatorHeatZone): MapHeatZone {
  const indexedCell = isValidCell(zone.id);
  return {
    id: zone.id,
    district: zone.label,
    // API-backed H3 cells use their actual boundary in zoneToFeature.
    h3: indexedCell ? zone.id : `h3-${zone.id}`,
    centroid: zone.centroid,
    h3Resolution: indexedCell ? getResolution(zone.id) : 9,
    score: Math.round(zone.demandGap * 100),
    confidence: zone.confidence,
    state: deriveHeatZoneState(zone),
    rank: zone.rank,
    listings: 0,
    warnings: zone.risks,
    reasons: zone.reasons,
    modelVersion: "network-ops-local",
    featureVersion: "operator-proxy-v1",
    featureSnapshotTime: "",
    predictionOriginTime: "",
    lastScoredAt: "",
    sourceSnapshotIds: ["snap-network-ops-local"],
    unmetDemandScore: zone.demandGap,
    formatFitScore: 1 - zone.competitionIndex,
    cannibalizationRisk: zone.cannibalizationRisk === "low" ? 0.1 : zone.cannibalizationRisk === "medium" ? 0.35 : 0.65,
    rentFeasibility: 0.7,
    listingAvailability: 0.5,
    poiCount: 10,
    competitorCount: Math.round(zone.competitionIndex * 10),
    competitorCapacity: 20,
    medianListingRent: 0,
    existingStoreCount: 0,
    dataQualityScore: zone.confidence,
    isMerged: zone.id.startsWith("MZ-"),
    compositionKind: zone.id.startsWith("MZ-") ? "MERGED" : "ATOMIC",
  };
}

/**
 * Convert an operator Listing to the MapListing type expected by HeatZoneMap.
 * Missing geocodes stay unknown. Only an explicit fixture caller may spread
 * records around a zone centroid for its illustrative map.
 */
export function operatorListingToMapListing(
  listing: Listing,
  heatZones: OperatorHeatZone[],
  index: number,
  allowFixtureCoordinates = false,
): MapListing {
  const confidence = listing.geocodeConfidence;
  const hasGeocode = typeof confidence === "number" && Number.isFinite(confidence) && confidence > 0;
  const hasCoordinates = typeof listing.longitude === "number" && Number.isFinite(listing.longitude)
    && typeof listing.latitude === "number" && Number.isFinite(listing.latitude);
  const coordinates: [number, number] | null = hasGeocode && hasCoordinates
    ? [listing.longitude!, listing.latitude!]
    : hasGeocode && allowFixtureCoordinates
      ? offsetFromZone(listing.heatZoneId ?? "", heatZones, index, 0.003, 137.5)
      : null;
  return {
    id: listing.id,
    source: listing.sourceId,
    address: listing.address,
    status: listing.status === "hardfail" ? "FAILED_HARD_RULE"
      : listing.status === "duplicate" ? "DUPLICATE"
      : listing.status === "candidate" ? "CANDIDATE"
      : listing.status === "geocoded" || listing.status === "scored" || listing.status === "watching"
        ? hasGeocode ? "GEOCODED" : "PARSED"
      : listing.status === "parsed" ? "PARSED"
      : "RAW",
    issue: Array.isArray(listing.hardRuleFailures) ? listing.hardRuleFailures.join("; ") : "硬規則尚未檢核",
    rent: listing.rentPerMonth > 0 ? `NT$${listing.rentPerMonth.toLocaleString()}` : "NT$ *** / 月",
    area: `${listing.areaPing} ping`,
    geocode: typeof confidence === "number" && Number.isFinite(confidence)
      ? `${confidence.toFixed(2)} / operator` : "未知",
    duplicate: listing.duplicateOfId ?? "",
    heatZoneId: listing.heatZoneId,
    coordinates,
    updatedAt: "",
    action: listing.candidateId ? "候選點已建立" : "待處理",
  };
}

/**
 * Convert an operator Candidate to the MapCandidateSite type expected by HeatZoneMap.
 */
export function operatorCandidateToMapSite(
  candidate: Candidate,
): MapCandidateSite {
  const confidence = candidate.geocodeConfidence;
  const longitude = candidate.longitude;
  const latitude = candidate.latitude;
  const hasGeocode = typeof confidence === "number" && Number.isFinite(confidence) && confidence > 0;
  const coordinates: [number, number] | null = hasGeocode
    && typeof longitude === "number" && Number.isFinite(longitude) && Math.abs(longitude) <= 180
    && typeof latitude === "number" && Number.isFinite(latitude) && Math.abs(latitude) <= 90
    ? [longitude, latitude] : null;
  const isReady = candidate.status === "ready" || candidate.status === "pendingreview" || candidate.status === "approved";
  return {
    id: candidate.id,
    address: candidate.address,
    status: candidate.status === "approved" ? "approved"
      : candidate.status === "rejected" ? "rejected"
      : candidate.status === "scored" || candidate.status === "pendingreview" ? "scored"
      : candidate.status === "wait" || candidate.status === "ready" ? "screened"
      : "new",
    heatZoneId: candidate.heatZoneId,
    coordinates,
    heatZoneScore: candidate.score,
    rentArea: "",
    geocode: coordinates === null ? "未定位" : `${confidence!.toFixed(2)} / operator`,
    feasibility: candidate.missingData.length ? candidate.missingData.join("; ") : "OK",
    listingSource: candidate.listingId ?? "",
    siteScore: candidate.recommendation === null ? "等待評分"
      : candidate.score === null ? candidate.recommendation : `${candidate.score} / ${candidate.recommendation}`,
    readiness: isReady && coordinates !== null ? "ready" : "blocked",
    disabledReason: coordinates === null ? "尚無已驗證座標，可在候選點清單查看"
      : candidate.missingData.length ? candidate.missingData.join("; ") : undefined,
  };
}
