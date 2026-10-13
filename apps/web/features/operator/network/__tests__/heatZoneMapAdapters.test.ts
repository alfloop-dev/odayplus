import { describe, expect, it } from "vitest";
import type { NetworkListingRadarSnapshot } from "@oday-plus/openapi-client";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import { operatorListingToMapListing } from "../heatZoneMapAdapters";

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
