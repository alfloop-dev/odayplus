// Governed geocoder address search (ODP-CAP-GEOCODER-SEARCH-001).
//
// Public surface for the capability. Callers import from here rather than
// reaching into the modules, so the lookup port (geocoderClient) stays the only
// path to a provider and the policy stays the only source of a review verdict.

export { GeocoderSearchPanel } from "./GeocoderSearchPanel";
export type { GeocoderSearchPanelProps } from "./GeocoderSearchPanel";
export {
  isGeocoderConfigured,
  parseSearchPayload,
  resolveGeocoderUrl,
  searchAddress,
  unconfiguredGeocoderError,
} from "./geocoderClient";
export type { GeocoderApiError, GeocoderResult } from "./geocoderClient";
export {
  LOW_CONFIDENCE_THRESHOLD,
  MARKET_LATITUDE_RANGE,
  MARKET_LONGITUDE_RANGE,
  MIN_QUERY_LENGTH,
  MIN_REVIEW_REASON_LENGTH,
  adminMatches,
  assessCandidate,
  assessCandidates,
  coordinatesInMarket,
  normalizeAddress,
  precisionTier,
  requiresExplicitReview,
  riskSummaryFor,
  validateQuery,
  validateSelection,
} from "./geocoderPolicy";
export { buildRejectionAuditEvent, buildSelectionAuditEvent } from "./geocoderAudit";
export type {
  CandidateAssessment,
  GeocodeAuditAction,
  GeocodeAuditEvent,
  GeocodeCandidate,
  GeocodePrecision,
  GeocodeQualityFlag,
  GeocodeSearchResult,
  ReviewRequirement,
} from "./geocoderTypes";
