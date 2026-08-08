import { describe, expect, it } from "vitest";
import {
  LOW_CONFIDENCE_THRESHOLD,
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
} from "../geocoderPolicy";
import type { GeocodeCandidate } from "../geocoderTypes";

/** A candidate that passes every gate; each test degrades exactly one field. */
function candidate(overrides: Partial<GeocodeCandidate> = {}): GeocodeCandidate {
  return {
    candidateId: "req-1#0",
    addressRaw: "新北市新莊區興德路100號",
    formattedAddress: "新北市新莊區興德路100號",
    latitude: 25.036,
    longitude: 121.45,
    precision: "rooftop",
    confidence: 0.98,
    provider: "geocode.primary_api",
    providerRequestId: "req-1",
    adminCity: "新北市",
    adminDistrict: "新莊區",
    observedAt: "2026-08-08T10:00:00+00:00",
    ...overrides,
  };
}

const QUERY = { city: "新北市", district: "新莊區" };

describe("geocoderPolicy thresholds mirror the ingestion pipeline", () => {
  it("uses the same 0.7 low-confidence threshold as geo/pipeline.py", () => {
    expect(LOW_CONFIDENCE_THRESHOLD).toBe(0.7);
  });

  it("flags strictly below the threshold and accepts exactly at it", () => {
    expect(assessCandidate(candidate({ confidence: 0.69 }), QUERY).flags).toContain(
      "low_geocode_confidence",
    );
    expect(assessCandidate(candidate({ confidence: 0.7 }), QUERY).flags).not.toContain(
      "low_geocode_confidence",
    );
  });

  it("treats a missing confidence as below threshold rather than assuming certainty", () => {
    const assessment = assessCandidate(candidate({ confidence: Number.NaN }), QUERY);
    expect(assessment.flags).toContain("low_geocode_confidence");
    expect(assessment.requirement).toBe("explicit_review_required");
  });
});

describe("coordinatesInMarket", () => {
  it("accepts coordinates inside the served bounding box", () => {
    expect(coordinatesInMarket(25.036, 121.45)).toBe(true);
    expect(coordinatesInMarket(21.8, 119.3)).toBe(true);
    expect(coordinatesInMarket(25.4, 122.1)).toBe(true);
  });

  it("rejects out-of-market and non-finite coordinates", () => {
    expect(coordinatesInMarket(35.68, 139.76)).toBe(false);
    expect(coordinatesInMarket(0, 0)).toBe(false);
    expect(coordinatesInMarket(Number.NaN, 121.45)).toBe(false);
  });
});

describe("precisionTier", () => {
  it("treats rooftop/street/interpolated as precise", () => {
    expect(precisionTier("rooftop")).toBe("precise");
    expect(precisionTier("street")).toBe("precise");
    expect(precisionTier("interpolated")).toBe("precise");
  });

  it("treats neighbourhood-level tiers as coarse", () => {
    for (const value of ["district", "centroid", "manual", "approximate"]) {
      expect(precisionTier(value)).toBe("coarse");
    }
  });

  it("fails closed on an unrecognised or absent tier", () => {
    expect(precisionTier("")).toBe("unknown");
    expect(precisionTier("PLUS_CODE")).toBe("unknown");
  });
});

describe("assessCandidate", () => {
  it("marks a rooftop, in-market, high-confidence, admin-matching candidate auto-selectable", () => {
    const assessment = assessCandidate(candidate(), QUERY);
    expect(assessment.flags).toEqual([]);
    expect(assessment.requirement).toBe("auto_selectable");
    expect(requiresExplicitReview(assessment)).toBe(false);
  });

  it("requires review for a coarse precision even at high confidence", () => {
    const assessment = assessCandidate(candidate({ precision: "centroid" }), QUERY);
    expect(assessment.flags).toContain("coarse_precision");
    expect(assessment.requirement).toBe("explicit_review_required");
  });

  it("requires review for an unknown precision tier", () => {
    const assessment = assessCandidate(candidate({ precision: "" }), QUERY);
    expect(assessment.flags).toContain("unknown_precision");
    expect(assessment.requirement).toBe("explicit_review_required");
  });

  it("flags coordinates outside the served market", () => {
    const assessment = assessCandidate(candidate({ latitude: 35.68, longitude: 139.76 }), QUERY);
    expect(assessment.flags).toContain("coordinates_out_of_market");
  });

  it("flags an administrative mismatch against the searched address", () => {
    const assessment = assessCandidate(candidate({ adminDistrict: "板橋區" }), QUERY);
    expect(assessment.flags).toContain("admin_mismatch");
  });

  it("gives one operator-facing reason per flag", () => {
    const assessment = assessCandidate(
      candidate({ confidence: 0.2, precision: "centroid", adminDistrict: "板橋區" }),
      QUERY,
    );
    expect(assessment.flags).toHaveLength(3);
    expect(assessment.reasons).toHaveLength(3);
    expect(assessment.reasons.every((reason) => reason.length > 0)).toBe(true);
  });

  it("preserves candidate order when assessing a result set", () => {
    const assessed = assessCandidates(
      [candidate({ candidateId: "a" }), candidate({ candidateId: "b", confidence: 0.1 })],
      QUERY,
    );
    expect(assessed.map((item) => item.candidateId)).toEqual(["a", "b"]);
    expect(assessed[0].requirement).toBe("auto_selectable");
    expect(assessed[1].requirement).toBe("explicit_review_required");
  });
});

describe("adminMatches", () => {
  it("does not assert a mismatch when the provider omits the admin level", () => {
    expect(adminMatches(QUERY, { adminCity: "", adminDistrict: "" })).toBe(true);
  });

  it("does not assert a mismatch when the query has no admin level to compare", () => {
    expect(
      adminMatches({ city: "", district: "" }, { adminCity: "台北市", adminDistrict: "中山區" }),
    ).toBe(true);
  });

  it("treats 臺 and 台 as the same city", () => {
    expect(
      adminMatches({ city: "台北市", district: "" }, { adminCity: "臺北市", adminDistrict: "" }),
    ).toBe(true);
  });
});

describe("normalizeAddress", () => {
  it("normalises 臺 to 台, strips whitespace and drops the floor suffix", () => {
    const result = normalizeAddress("臺北市 中山區 南京東路 3 段 1 號 5F");
    expect(result.normalized).toContain("台北市");
    expect(result.normalized).not.toContain(" ");
    expect(result.normalized).not.toMatch(/5F/);
  });

  it("extracts city and district without matching the city twice", () => {
    const result = normalizeAddress("新北市新莊區興德路100號");
    expect(result.city).toBe("新北市");
    expect(result.district).toBe("新莊區");
  });

  it("keeps the raw address exactly as typed", () => {
    const raw = "  臺北市中山區南京東路  ";
    expect(normalizeAddress(raw).raw).toBe(raw);
  });
});

describe("validateQuery", () => {
  it("rejects a blank query", () => {
    expect(validateQuery("   ")).toEqual({ ok: false, message: expect.stringContaining("請先輸入") });
  });

  it("rejects a query shorter than the minimum", () => {
    const result = validateQuery("台北");
    expect(result.ok).toBe(false);
  });

  it("accepts and trims a usable query", () => {
    expect(validateQuery("  新北市新莊區興德路  ")).toEqual({
      ok: true,
      value: "新北市新莊區興德路",
    });
  });
});

describe("validateSelection", () => {
  const clean = assessCandidate(candidate(), QUERY);
  const flagged = assessCandidate(candidate({ confidence: 0.3 }), QUERY);

  it("lets a clean candidate through with no acknowledgement", () => {
    expect(validateSelection(clean, { reviewAcknowledged: false, reviewReason: "" })).toEqual({
      ok: true,
    });
  });

  it("blocks a flagged candidate without acknowledgement", () => {
    const result = validateSelection(flagged, {
      reviewAcknowledged: false,
      reviewReason: "這個地址位置我已經現場確認過了",
    });
    expect(result.ok).toBe(false);
  });

  it("blocks a flagged candidate acknowledged but without a substantive reason", () => {
    const result = validateSelection(flagged, { reviewAcknowledged: true, reviewReason: "確認過" });
    expect(result.ok).toBe(false);
  });

  it("admits a flagged candidate with both acknowledgement and a written reason", () => {
    expect(
      validateSelection(flagged, {
        reviewAcknowledged: true,
        reviewReason: "已於現場核對門牌與座標，確認為同一位置。",
      }),
    ).toEqual({ ok: true });
  });
});

describe("riskSummaryFor", () => {
  it("names manual override in the copy for a flagged candidate", () => {
    const summary = riskSummaryFor(assessCandidate(candidate({ confidence: 0.2 }), QUERY));
    expect(summary).toContain("manual_override_flag");
    expect(summary).toContain("Audit");
  });

  it("still states that a clean selection is audited", () => {
    expect(riskSummaryFor(assessCandidate(candidate(), QUERY))).toContain("Audit");
  });
});
