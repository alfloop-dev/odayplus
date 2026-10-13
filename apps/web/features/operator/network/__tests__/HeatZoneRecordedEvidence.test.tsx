import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OdpApiClient } from "@oday-plus/openapi-client";
import { HEAT_ZONE_FIXTURES } from "../../fixtures";
import { buildNetworkFindAreasViewModel } from "../../networkFindAreasViewModel";
import { FindAreasPanel } from "../FindAreasPanel";
import { loadNetworkFindAreasBindings } from "../networkFindAreasLoader";
import { operatorHeatZoneToMapZone } from "../heatZoneMapAdapters";
import { recordedLensPoints, recordedMapFreshness, zoneToFeature } from "../heatZoneRecordedEvidence";

const observed = vi.hoisted(() => ({ map: null as null | Record<string, any> }));
vi.mock("next/dynamic", () => ({
  default: () => (props: Record<string, unknown>) => {
    observed.map = props;
    return <div />;
  },
}));
vi.mock("../geocoder", () => ({ GeocoderSearchPanel: () => null }));

// Synthetic values in the actual score producer's schema; not live UAT evidence.
const recorded = {
  heat_zone_id: "heatzone:8928308280fffff",
  h3_index: "8928308280fffff",
  h3_resolution: 9,
  score: 41.25,
  priority_rank: 2,
  unmet_demand_score: 0.73,
  format_fit_score: 0.24,
  cannibalization_risk_score: 0.62,
  rent_feasibility_score: 0.31,
  listing_availability_score: 0.18,
  confidence: 0.82,
  state: "UNTOUCHED",
  model_version: "approved-model-42",
  feature_version: "recorded-features-7",
  feature_snapshot_time: "2026-10-01T10:00:00+00:00",
  prediction_origin_time: "2026-10-02T10:00:00+00:00",
  last_scored_at: "2026-10-02T10:00:02+00:00",
  source_snapshot_ids: ["snapshot-17", "snapshot-18"],
  warnings: ["incomplete_listing_coverage"],
  reasons: ["recorded-demand"],
};

async function load(row: Record<string, unknown> = recorded) {
  const client = new OdpApiClient({
    baseUrl: "https://console.example.invalid",
    fetchImpl: async (input) => new URL(String(input)).pathname === "/api/v1/heatzones"
      ? Response.json({ items: [row], count: 1 }) : Response.json({ candidates: [] }),
  });
  const binding = (await loadNetworkFindAreasBindings(client)).heatZones;
  expect(binding.state).toBe("ready");
  return binding.items[0]!;
}

afterEach(() => { cleanup(); observed.map = null; });

describe("recorded HeatZone map evidence", () => {
  it("preserves the producer's score, state and independent factors", async () => {
    const map = operatorHeatZoneToMapZone(await load());
    expect(map).toMatchObject({
      score: 41.25, state: "UNTOUCHED", rank: 2, unmetDemandScore: 0.73,
      formatFitScore: 0.24, cannibalizationRisk: 0.62, rentFeasibility: 0.31,
      listingAvailability: 0.18,
    });
  });

  it("keeps an abstained score null instead of rebuilding it from demand", async () => {
    expect(operatorHeatZoneToMapZone(await load({ ...recorded, score: null })).score).toBeNull();
  });

  it("preserves V3 abstention and does not rank its partially computed factors", async () => {
    const map = operatorHeatZoneToMapZone(await load({
      ...recorded, state: "ABSTAINED", score: null, abstained: true, format_fit_score: 0.99,
    }));
    expect(map.state).toBe("ABSTAINED");
    expect(map.score).toBeNull();
    expect(recordedLensPoints(map, "fit")).toBeNull();
    expect(recordedLensPoints(map, "demand")).toBeNull();
  });

  it("keeps an unsupported producer state unknown", async () => {
    const map = operatorHeatZoneToMapZone(await load({ ...recorded, state: "unregistered-state" }));
    expect(map.state).toBe("UNKNOWN");
    expect(recordedLensPoints(map, "fit")).toBeNull();
  });

  it.each([{ is_shadow: true }, { execution_mode: "SHADOW" }])("keeps shadow evaluations out of operational lens ranking: %j", async (mode) => {
    const map = operatorHeatZoneToMapZone(await load({ ...recorded, ...mode }));
    expect(map.executionMode).toBe("SHADOW");
    expect(recordedLensPoints(map, "fit")).toBeNull();
    expect(recordedLensPoints(map, "demand")).toBeNull();
    expect(map.score).toBe(recorded.score);
  });

  it("retains producer provenance and warnings", async () => {
    expect(operatorHeatZoneToMapZone(await load())).toMatchObject({
      modelVersion: recorded.model_version, featureVersion: recorded.feature_version,
      featureSnapshotTime: recorded.feature_snapshot_time,
      predictionOriginTime: recorded.prediction_origin_time, lastScoredAt: recorded.last_scored_at,
      sourceSnapshotIds: recorded.source_snapshot_ids, warnings: recorded.warnings,
    });
  });

  it("leaves counts, rent observations and data quality unknown when absent", async () => {
    expect(operatorHeatZoneToMapZone(await load())).toMatchObject({
      listings: null, poiCount: null, competitorCount: null, competitorCapacity: null,
      medianListingRent: null, existingStoreCount: null, dataQualityScore: null,
    });
  });

  it("preserves observed zeroes instead of confusing them with missing data", async () => {
    const map = operatorHeatZoneToMapZone(await load({
      ...recorded, score: 0, active_listing_count: 0, poi_count: 0,
      competitor_count: 0, competitor_capacity: 0, median_listing_rent: 0,
      existing_store_count: 0, data_quality_score: 0,
    }));
    expect(map).toMatchObject({
      score: 0, listings: 0, poiCount: 0, competitorCount: 0, competitorCapacity: 0,
      medianListingRent: 0, existingStoreCount: 0, dataQualityScore: 0,
    });
  });

  it("rejects fractional counts, numeric strings and negative measurements", async () => {
    expect(operatorHeatZoneToMapZone(await load({
      ...recorded, active_listing_count: "2", poi_count: 1.5, competitor_count: -1,
      median_listing_rent: -50, data_quality_score: 2,
    }))).toMatchObject({
      listings: null, poiCount: null, competitorCount: null, medianListingRent: null, dataQualityScore: null,
    });
  });

  it.each([null, "0.9", Number.NaN, -0.1, 1.1])("rejects invalid factor observations: %s", async (value) => {
    expect(operatorHeatZoneToMapZone(await load({ ...recorded, format_fit_score: value })).formatFitScore).toBeNull();
  });

  it("does not replace an explicit null with an older alias", async () => {
    const map = operatorHeatZoneToMapZone(await load({
      ...recorded, rent_feasibility_score: null, rent_feasibility: 0.99,
      model_version: null, modelVersion: "stale-model",
    }));
    expect(map.rentFeasibility).toBeNull();
    expect(map.modelVersion).toBe("");
  });

  it("does not claim canonical evidence for an unprojected operator row", () => {
    expect(operatorHeatZoneToMapZone(HEAT_ZONE_FIXTURES[0]!)).toMatchObject({
      score: null, state: "UNKNOWN", modelVersion: "", featureVersion: "",
      sourceSnapshotIds: [], formatFitScore: null, rentFeasibility: null,
    });
  });

  it("requires explicit fixture mode for illustrative metric synthesis", () => {
    const map = operatorHeatZoneToMapZone(HEAT_ZONE_FIXTURES[0]!, true);
    expect(map.score).toBe(Math.round(HEAT_ZONE_FIXTURES[0]!.demandGap * 100));
    expect(map.modelVersion).toBe("network-ops-fixture");
  });

  it("keeps unavailable traffic/life/competition lenses unscored", async () => {
    const map = operatorHeatZoneToMapZone(await load());
    expect(recordedLensPoints(map, "traffic")).toBeNull();
    expect(recordedLensPoints(map, "life")).toBeNull();
    expect(recordedLensPoints(map, "competition")).toBeNull();
    expect(recordedLensPoints(map, "cannibalization")).toBe(38);
  });

  it("uses the measured V3 competitor pressure for the favorable competition lens", async () => {
    expect(recordedLensPoints(operatorHeatZoneToMapZone(await load({
      ...recorded, competitor_pressure_score: 0.72,
    })), "competition")).toBe(28);
  });

  it("does not choose one model identity for a mixed-source map", async () => {
    const a = operatorHeatZoneToMapZone(await load());
    const b = operatorHeatZoneToMapZone(await load({ ...recorded, model_version: "model-43" }));
    expect(recordedMapFreshness([a, b]).modelVersion).toBe("MIXED");
    expect(recordedMapFreshness([a, { ...b, modelVersion: "" }]).modelVersion).toBe("");
    expect(recordedMapFreshness([])).toMatchObject({ status: "UNKNOWN", modelVersion: "", sourceSnapshotId: "" });
  });

  it("uses the actual H3 boundary and refuses invented live polygons", async () => {
    const map = operatorHeatZoneToMapZone(await load());
    const feature = zoneToFeature(map);
    expect(feature?.geometry.coordinates[0]).toHaveLength(7);
    expect(feature?.geometry.coordinates[0]?.[0]).toEqual(feature?.geometry.coordinates[0]?.[6]);
    const pointOnly = { ...map, h3: "not-a-cell" };
    expect(zoneToFeature(pointOnly)).toBeNull();
    expect(zoneToFeature(pointOnly, true)?.geometry.coordinates[0]).toHaveLength(5);
  });

  it("passes recorded model/snapshot evidence and unknown freshness to the live map", async () => {
    const zone = await load();
    const viewModel = buildNetworkFindAreasViewModel({ heatZones: [zone], candidates: [], listings: [], activeLens: "fit" });
    render(<FindAreasPanel
      activeRoleId="expansion-manager" canAddListing={false} candidates={[]}
      effectiveLens="fit" fixturesAllowed={false} heatZones={[zone]} isSelectedTracked={false}
      listings={[]} selectedZone={viewModel.zones[0]!}
      viewModel={viewModel}
      onAddListingFromUrl={() => {}} onChangeLens={() => {}} onScoreCandidate={() => {}}
      onSelectZone={() => {}} onSourceListings={() => {}} onSubmitReview={() => {}}
      onToggleTracked={() => {}} onViewCandidates={() => {}}
    />);
    expect(observed.map?.freshness).toMatchObject({
      status: "UNKNOWN", modelVersion: recorded.model_version,
      featureSnapshotTime: recorded.feature_snapshot_time,
      sourceSnapshotId: "snapshot-17 / snapshot-18",
    });
    expect(observed.map?.lensScores).toEqual([{ id: zone.id, label: zone.label, points: 24 }]);
    expect(screen.getByTestId("find-areas-zone-score").textContent).toBe("24");
    const detail = screen.getByTestId("find-areas-zone-detail");
    expect(detail.textContent).toContain("HeatZone 綜合 41.25");
    expect(within(screen.getByTestId("find-areas-zone-facts")).getByText("31%")).toBeTruthy();
    expect(within(detail).getByText(recorded.warnings[0]!)).toBeTruthy();
  });

  it("shows unknown lens values in both the live map and recommendation cards", async () => {
    const zone = await load();
    const viewModel = buildNetworkFindAreasViewModel({ heatZones: [zone], candidates: [], listings: [], activeLens: "traffic" });
    render(<FindAreasPanel
      activeRoleId="expansion-manager" canAddListing={false} candidates={[]}
      effectiveLens="traffic" fixturesAllowed={false} heatZones={[zone]} isSelectedTracked={false}
      listings={[]} selectedZone={viewModel.zones[0]!} viewModel={viewModel}
      onAddListingFromUrl={() => {}} onChangeLens={() => {}} onScoreCandidate={() => {}}
      onSelectZone={() => {}} onSourceListings={() => {}} onSubmitReview={() => {}}
      onToggleTracked={() => {}} onViewCandidates={() => {}}
    />);
    expect(observed.map?.lensScores[0]?.points).toBeNull();
    expect(screen.getByTestId("find-areas-zone-score").textContent).toBe("未評估");
    expect(screen.getByTestId("find-areas-zone-score").dataset.tier).toBe("unknown");
    expect(within(screen.getByTestId("find-areas-tray")).getByRole("button", { name: `${zone.label} 未評估` })).toBeTruthy();
  });
});
