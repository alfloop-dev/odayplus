import { describe, expect, it } from "vitest";
import type { NetworkListingRadarSnapshot } from "@oday-plus/openapi-client";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import { operatorCandidateToMapSite, operatorListingToMapListing } from "../heatZoneMapAdapters";
import type { Candidate } from "../../types";

const manualListing = {
  id: "synthetic-manual-listing",
  sourceId: "manual-source",
  sourceListingId: "synthetic-source-id",
  heatZoneId: null,
  address: "Synthetic manually entered address",
  status: "watching" as const,
  rentPerMonth: 42500,
  areaPing: 22.5,
  floor: "",
  frontageMeters: null,
  geocodeConfidence: null,
  hardRuleFailures: ["missing_geocode_evidence"],
  hardRuleSummary: "尚未檢核",
  sourceEvidence: [],
  fitScore: null,
  firstSeenAt: "2026-10-13T00:00:00Z",
  sourceUrl: "https://example.invalid/manual-listing",
  latitude: null,
  longitude: null,
  h3Index: null,
  snapshotId: null,
} satisfies NetworkListingRadarSnapshot["listings"][number];

describe("Listing map projection with missing measurements", () => {
  it.each([null, undefined])("preserves an unrecorded hard-rule verdict (%s) without crashing the real listing projection", (missing) => {
    // The generated read contract admits missing operator verdict evidence.
    const listing = { ...manualListing, hardRuleFailures: missing } satisfies NetworkListingRadarSnapshot["listings"][number];
    expect(operatorListingToMapListing(listing, [], 0).issue).toBe("硬規則尚未檢核");
    const row = buildNetworkFindAreasViewModel({ heatZones: [], listings: [listing], candidates: [] }).listingRadar[0];
    expect(row).toMatchObject({ hardRuleStatus: "unmeasured", hardRuleFailures: [], tone: "watch" });
    expect(row.geocodeConfidence).toBeNull();
  });

  it("distinguishes a recorded empty failure list from an unmeasured verdict", () => {
    const listing = { ...manualListing, hardRuleFailures: [] };
    const row = buildNetworkFindAreasViewModel({ heatZones: [], listings: [listing], candidates: [] }).listingRadar[0];
    expect(row).toMatchObject({ hardRuleStatus: "measured", hardRuleFailures: [], tone: "good" });
    expect(operatorListingToMapListing(listing, [], 0).issue).toBe("");
  });

  it("keeps a manual row readable without inventing geocode confidence or a map point", () => {
    const projected = operatorListingToMapListing(manualListing, [], 0);
    expect(projected.geocode).toBe("未知");
    expect(projected.coordinates).toBeNull();
    expect(projected.heatZoneId).toBeNull();
    expect(projected.status).toBe("PARSED");
    expect(projected.issue).toBe("missing_geocode_evidence");

    const view = buildNetworkFindAreasViewModel({ heatZones: [], listings: [manualListing], candidates: [] });
    expect(view.listingRadar[0]).toMatchObject({
      geocodeConfidence: null,
      geocodeConfidenceLabel: "未知",
      zoneLabel: "待定位",
    });
    expect(view.totals.listings).toBe(1);
  });

  it("preserves recorded zero coordinates without shifting them to a zone centroid", () => {
    const projected = operatorListingToMapListing({
      ...manualListing, geocodeConfidence: 0.85, longitude: 0, latitude: 0,
    }, [], 7);
    expect(projected.coordinates).toEqual([0, 0]);
    expect(projected.geocode).toBe("0.85 / operator");
  });

  it("requires the explicit fixture flag before approximating an unlocated listing", () => {
    const measuredConfidenceOnly = { ...manualListing, geocodeConfidence: 0.85 };
    expect(operatorListingToMapListing(measuredConfidenceOnly, [], 0).coordinates).toBeNull();
    expect(operatorListingToMapListing(measuredConfidenceOnly, [], 0, true).coordinates).not.toBeNull();
    expect(operatorListingToMapListing(manualListing, [], 0, true).coordinates).toBeNull();
  });
});

const candidate: Candidate = {
  id: "synthetic-candidate", heatZoneId: "HZ-01", title: "Candidate", address: "Recorded address",
  status: "new", score: null, recommendation: null, modelVersion: null, datasetSnapshotId: null,
  missingData: [], latitude: 25.04231, longitude: 121.50872, geocodeConfidence: 0.91,
};

describe("Candidate map location provenance", () => {
  it("uses the exact recorded point, including measured zero coordinates", () => {
    expect(operatorCandidateToMapSite(candidate).coordinates).toEqual([121.50872, 25.04231]);
    expect(operatorCandidateToMapSite({ ...candidate, longitude: 0, latitude: 0 }).coordinates).toEqual([0, 0]);
  });

  it.each([
    { latitude: null }, { longitude: undefined }, { latitude: Number.NaN },
    { longitude: 181 }, { latitude: -91 }, { geocodeConfidence: null },
  ])("keeps incomplete or invalid evidence off the map (%j)", (missing) => {
    const result = operatorCandidateToMapSite({ ...candidate, ...missing });
    expect(result.coordinates).toBeNull();
    expect(result.geocode).toBe("未定位");
    expect(result.readiness).toBe("blocked");
    expect(result.disabledReason).toContain("候選點清單");
  });
});
