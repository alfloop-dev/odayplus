// Governed geocoder address search — review policy (ODP-CAP-GEOCODER-SEARCH-001).
//
// Owned layer  : the console's decision about whether a geocode candidate may
//                be accepted directly or must pass an explicit human review,
//                and the zh-TW copy explaining why.
// Not changing : the thresholds themselves. Every constant here mirrors
//                modules/external_data/geo/pipeline.py so the console and the
//                ingestion pipeline cannot disagree about what "low confidence"
//                means. If the pipeline's threshold moves, this moves with it.
// Composes with: GeocoderSearchPanel (which renders the verdict) and
//                geocoderAudit (which records it).
//
// The policy fails CLOSED: anything it cannot positively assess — an unknown
// precision tier, a non-finite confidence, a coordinate outside the served
// market — becomes explicit_review_required rather than auto_selectable.

import type {
  CandidateAssessment,
  GeocodeCandidate,
  GeocodePrecision,
  GeocodeQualityFlag,
  ReviewRequirement,
} from "./geocoderTypes";

/** Mirrors `candidate.confidence < 0.7` in GeoPipeline.geocode_record. */
export const LOW_CONFIDENCE_THRESHOLD = 0.7;

/** Mirrors TAIWAN_LATITUDE_RANGE / TAIWAN_LONGITUDE_RANGE in geo/pipeline.py. */
export const MARKET_LATITUDE_RANGE: readonly [number, number] = [21.8, 25.4];
export const MARKET_LONGITUDE_RANGE: readonly [number, number] = [119.3, 122.1];

/**
 * Precision tiers precise enough to accept without a second look. Everything
 * else (district/centroid/manual/approximate) locates a neighbourhood rather
 * than a unit, which is not enough to open a store on.
 */
const PRECISE_TIERS: readonly GeocodePrecision[] = ["rooftop", "street", "interpolated"];

/** Recognised-but-coarse tiers. Anything outside both lists is `unknown`. */
const COARSE_TIERS: readonly GeocodePrecision[] = ["district", "centroid", "manual", "approximate"];

/** Minimum characters before a search is worth issuing. */
export const MIN_QUERY_LENGTH = 4;

/** Minimum length of the justification recorded on a low-confidence override. */
export const MIN_REVIEW_REASON_LENGTH = 10;

const FLAG_REASONS: Record<GeocodeQualityFlag, string> = {
  low_geocode_confidence: `定位信心低於門檻（< ${LOW_CONFIDENCE_THRESHOLD}），與 ingestion pipeline 同一標準。`,
  coordinates_out_of_market: "座標落在服務市場範圍之外，可能是同名地址或跨區誤判。",
  admin_mismatch: "回傳的行政區與輸入地址不一致，請確認是否為同名路段。",
  missing_geocode: "此筆結果缺少可用座標，系統不會替它推估位置。",
  coarse_precision: "定位精度僅到區域／中心點層級，無法對應到門牌。",
  unknown_precision: "無法辨識的定位精度等級，依規定一律送人工覆核。",
};

/** True when the coordinate falls inside the served market bounding box. */
export function coordinatesInMarket(latitude: number, longitude: number): boolean {
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
  return (
    latitude >= MARKET_LATITUDE_RANGE[0] &&
    latitude <= MARKET_LATITUDE_RANGE[1] &&
    longitude >= MARKET_LONGITUDE_RANGE[0] &&
    longitude <= MARKET_LONGITUDE_RANGE[1]
  );
}

/**
 * Normalise a Taiwanese address the same way geo/pipeline.py's
 * `normalize_address` does: NFKC, 臺→台, whitespace collapsed, floor/unit
 * suffixes dropped. Used only to compare admin levels; the raw string the
 * operator typed is what gets audited.
 */
export function normalizeAddress(rawAddress: string): {
  raw: string;
  normalized: string;
  city: string;
  district: string;
} {
  const raw = rawAddress ?? "";
  let text = raw.normalize("NFKC").replace(/臺/g, "台");
  text = text.replace(/\s+/g, " ").trim();
  text = text.replace(/(?:\d+\s*[fF樓]|地下\d*樓?|B\d+|之\d+|,\s*.*$)/g, "").trim();
  text = text.replace(/[\s,，]+$/g, "").replace(/ /g, "");

  const city = firstMatch(/([\w一-鿿]+(?:市|縣))/, text);
  // District is searched after the city prefix so "新北市新莊區" does not match
  // the city token twice (the district pattern also accepts a trailing 市).
  const afterCity = city && text.startsWith(city) ? text.slice(city.length) : text;
  const district = firstMatch(/([\w一-鿿]+(?:區|鄉|鎮|市))/, afterCity);
  return { raw, normalized: text, city, district };
}

/**
 * Mirrors GeoPipeline._admin_matches: a mismatch is only asserted when BOTH
 * sides state a value and they differ. A provider that omits the admin level
 * is not evidence of a mismatch.
 */
export function adminMatches(
  query: { city: string; district: string },
  candidate: Pick<GeocodeCandidate, "adminCity" | "adminDistrict">,
): boolean {
  const city = normalizeAdminToken(candidate.adminCity);
  const district = normalizeAdminToken(candidate.adminDistrict);
  if (city && query.city && city !== normalizeAdminToken(query.city)) return false;
  if (district && query.district && district !== normalizeAdminToken(query.district)) return false;
  return true;
}

/**
 * Classify one candidate against the query it answered.
 *
 * Flags are ordered pipeline-first (out-of-market, admin mismatch, low
 * confidence) so the reason list reads the same way as the ingestion pipeline's
 * `quality_flags` tuple, with the UI-side precision flags appended.
 */
export function assessCandidate(
  candidate: GeocodeCandidate,
  query: { city: string; district: string },
): CandidateAssessment {
  const flags: GeocodeQualityFlag[] = [];

  if (!coordinatesInMarket(candidate.latitude, candidate.longitude)) {
    flags.push("coordinates_out_of_market");
  }
  if (!adminMatches(query, candidate)) {
    flags.push("admin_mismatch");
  }
  // A non-finite confidence is treated as low rather than coerced to 0, so the
  // operator sees "below threshold" and not a confident-looking 0.00.
  if (!Number.isFinite(candidate.confidence) || candidate.confidence < LOW_CONFIDENCE_THRESHOLD) {
    flags.push("low_geocode_confidence");
  }

  const tier = precisionTier(candidate.precision);
  if (tier === "coarse") flags.push("coarse_precision");
  if (tier === "unknown") flags.push("unknown_precision");

  const requirement: ReviewRequirement =
    flags.length === 0 ? "auto_selectable" : "explicit_review_required";

  return {
    candidateId: candidate.candidateId,
    flags,
    requirement,
    reasons: flags.map((flag) => FLAG_REASONS[flag]),
  };
}

/** Classify a whole result set, preserving candidate order. */
export function assessCandidates(
  candidates: readonly GeocodeCandidate[],
  query: { city: string; district: string },
): CandidateAssessment[] {
  return candidates.map((candidate) => assessCandidate(candidate, query));
}

export function requiresExplicitReview(assessment: CandidateAssessment): boolean {
  return assessment.requirement === "explicit_review_required";
}

/** `precise` may be auto-selected; `coarse` and `unknown` may not. */
export function precisionTier(precision: string): "precise" | "coarse" | "unknown" {
  const value = (precision ?? "").trim().toLowerCase();
  if ((PRECISE_TIERS as readonly string[]).includes(value)) return "precise";
  if ((COARSE_TIERS as readonly string[]).includes(value)) return "coarse";
  return "unknown";
}

/** Rejects blank/too-short searches locally instead of spending a provider call. */
export function validateQuery(query: string): { ok: true; value: string } | { ok: false; message: string } {
  const trimmed = (query ?? "").trim();
  if (!trimmed) return { ok: false, message: "請先輸入要搜尋的地址。" };
  if (trimmed.length < MIN_QUERY_LENGTH) {
    return { ok: false, message: `地址過短（至少 ${MIN_QUERY_LENGTH} 個字）— 請輸入含縣市與路名的完整地址。` };
  }
  return { ok: true, value: trimmed };
}

/**
 * Gate for confirming a selection. An auto-selectable candidate needs nothing;
 * a flagged one needs BOTH an acknowledgement and a substantive written reason,
 * because the acknowledgement alone records no rationale for the audit reader.
 */
export function validateSelection(
  assessment: CandidateAssessment,
  input: { reviewAcknowledged: boolean; reviewReason: string },
): { ok: true } | { ok: false; message: string } {
  if (!requiresExplicitReview(assessment)) return { ok: true };
  if (!input.reviewAcknowledged) {
    return { ok: false, message: "此候選點被標記為需人工覆核，請先確認你已了解上述風險。" };
  }
  const reason = (input.reviewReason ?? "").trim();
  if (reason.length < MIN_REVIEW_REASON_LENGTH) {
    return {
      ok: false,
      message: `覆核理由必填且至少 ${MIN_REVIEW_REASON_LENGTH} 個字（寫入 Audit）。`,
    };
  }
  return { ok: true };
}

/** The disclosure shown to the operator and stored verbatim on the audit event. */
export function riskSummaryFor(assessment: CandidateAssessment): string {
  if (!requiresExplicitReview(assessment)) {
    return "此候選點通過定位品質檢核；選取結果（座標、精度、信心、來源）將寫入 Audit。";
  }
  return (
    "此候選點未通過定位品質檢核，採用後將以人工覆核座標記錄（manual_override_flag）。" +
    "覆核理由、操作者、前後值與 correlation ID 會一併寫入 Audit。"
  );
}

function firstMatch(pattern: RegExp, text: string): string {
  const match = pattern.exec(text);
  return match ? match[1] : "";
}

function normalizeAdminToken(value: string): string {
  return (value ?? "").normalize("NFKC").replace(/臺/g, "台").trim();
}
