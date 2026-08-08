// Governed geocoder address search — shared contracts (ODP-CAP-GEOCODER-SEARCH-001).
//
// Owned layer  : the frontend-facing shape of a geocode candidate, the quality
//                flags a candidate can carry, and the review requirement the
//                console derives from them.
// Not changing : the canonical persistence model (shared.domain.AddressLocation)
//                or the `geocode_result_snapshot` external contract. These types
//                are the UI projection of those, not a second source of truth.
// Composes with: modules/external_data/geo/pipeline.py, whose thresholds and
//                flag vocabulary geocoderPolicy.ts mirrors verbatim.

/**
 * Canonical precision tiers, per
 * packages/schemas/source_contracts/external/geocode_result_snapshot.json.
 *
 * The provider gateway (services/provider-gateway/app.py) additionally emits
 * "interpolated" and "approximate" from its Google location_type mapping, so
 * both vocabularies appear on the wire. Anything outside the union is treated
 * as unknown by the policy and fails closed into human review.
 */
export type GeocodePrecision =
  | "rooftop"
  | "street"
  | "district"
  | "centroid"
  | "manual"
  | "interpolated"
  | "approximate";

/**
 * Quality flags. The first four are the canonical names emitted by
 * GeoPipeline.geocode_record; `coarse_precision` and `unknown_precision` are
 * UI-side refinements of the same idea and are namespaced as such in the audit
 * payload so a reader can tell which layer produced them.
 */
export type GeocodeQualityFlag =
  | "low_geocode_confidence"
  | "coordinates_out_of_market"
  | "admin_mismatch"
  | "missing_geocode"
  | "coarse_precision"
  | "unknown_precision";

/**
 * One geocode candidate offered to the operator.
 *
 * `latitude`/`longitude` are required and non-nullable by construction: a
 * provider row that cannot supply both is rejected during parsing rather than
 * defaulted, so this type can never carry a fabricated coordinate.
 */
export type GeocodeCandidate = {
  candidateId: string;
  /** Exactly what the operator typed, preserved for the audit trail. */
  addressRaw: string;
  /** The provider's own formatted address; never synthesised locally. */
  formattedAddress: string;
  latitude: number;
  longitude: number;
  precision: GeocodePrecision | string;
  /** Provider confidence, 0..1. */
  confidence: number;
  provider: string;
  providerRequestId: string;
  adminCity: string;
  adminDistrict: string;
  observedAt: string;
};

/** Whether a candidate may be selected directly, or needs an explicit review. */
export type ReviewRequirement = "auto_selectable" | "explicit_review_required";

/** The policy verdict for one candidate. */
export type CandidateAssessment = {
  candidateId: string;
  flags: GeocodeQualityFlag[];
  requirement: ReviewRequirement;
  /** Operator-facing zh-TW explanation, one line per flag. */
  reasons: string[];
};

/** A successful search: the query as issued, plus the candidates it returned. */
export type GeocodeSearchResult = {
  query: string;
  normalizedQuery: string;
  candidates: GeocodeCandidate[];
  correlationId: string;
  searchedAt: string;
  /**
   * Rows the provider returned that were dropped because they lacked usable
   * coordinates. Surfaced so a partial answer never reads as a complete one.
   */
  rejectedRowCount: number;
};

/** What the operator did, recorded for audit. */
export type GeocodeAuditAction = "candidate_selected" | "low_confidence_override" | "search_rejected";

/**
 * The audit event the console emits for a selection or override.
 *
 * `before`/`after` follow the AuditMeta shape in @oday-plus/domain-types so a
 * downstream audit consumer sees the same envelope as every other governed
 * action on the console.
 */
export type GeocodeAuditEvent = {
  action: GeocodeAuditAction;
  actorRoleId: string;
  occurredAt: string;
  correlationId: string;
  addressRaw: string;
  candidateId: string | null;
  /** Null when nothing was selected (a rejected search). */
  selected: {
    latitude: number;
    longitude: number;
    precision: string;
    confidence: number;
    provider: string;
    providerRequestId: string;
    formattedAddress: string;
  } | null;
  flags: GeocodeQualityFlag[];
  requirement: ReviewRequirement;
  /** Required whenever `requirement` is explicit_review_required. */
  reviewReason: string | null;
  reviewAcknowledged: boolean;
  /** The exact risk copy shown to the operator at the moment they confirmed. */
  riskSummary: string;
};
