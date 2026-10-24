import { zoneToFeature } from "../heatZoneRecordedEvidence";
import { describe, expect, it } from "vitest";
import { OdpApiClient } from "@oday-plus/openapi-client";
import { loadNetworkFindAreasBindings } from "../networkFindAreasLoader";
import { operatorHeatZoneToMapZone } from "../heatZoneMapAdapters";

// Contract shape captured from the batch scoring API in the hosted map trace.
const recordedCell = {
  heat_zone_id: "heatzone:8928308280fffff",
  h3_index: "8928308280fffff",
  h3_resolution: 9,
  priority_rank: 2,
  unmet_demand_score: 0.73,
  confidence: 0.82,
};

function clientFor(row: Record<string, unknown>) {
  return new OdpApiClient({
    baseUrl: "https://console.example.invalid",
    fetchImpl: async (input) => {
      const path = new URL(String(input)).pathname;
      if (path === "/api/v1/heatzones") return Response.json({ items: [row], count: 1 });
      if (path === "/api/v1/listings/candidates") return Response.json({ candidates: [] });
      throw new Error(`Unexpected endpoint: ${path}`);
    },
  });
}

describe("HeatZone batch API geometry", () => {
  it("locates the indexed San Francisco cell and preserves producer ranking/demand", async () => {
    const binding = (await loadNetworkFindAreasBindings(clientFor(recordedCell))).heatZones;
    expect(binding.state).toBe("ready");
    const zone = binding.items[0]!;
    expect(zone.id).toBe(recordedCell.heat_zone_id);
    expect(zone.centroid).not.toBeNull();
    expect(zone.centroid![0]).toBeGreaterThan(-123);
    expect(zone.centroid![0]).toBeLessThan(-122);
    expect(zone.centroid![1]).toBeGreaterThan(37);
    expect(zone.centroid![1]).toBeLessThan(38);
    expect(zone.rank).toBe(2);
    expect(zone.demandGap).toBeNull();
    const projected = operatorHeatZoneToMapZone(zone);
    expect(zone.recordedMap).toBeUndefined();
    expect(projected.score).toBeNull();
    expect(zoneToFeature(projected)).toBeNull();
    expect(projected.centroid).toEqual(zone.centroid);
  });

  it("preserves a supplied zero coordinate as recorded geometry", async () => {
    const binding = (await loadNetworkFindAreasBindings(clientFor({ ...recordedCell, centroid: [0, 0] }))).heatZones;
    expect(binding.state).toBe("ready");
    expect(binding.items[0]?.centroid).toEqual([0, 0]);
  });

  it.each([{ h3_index: "not-a-cell" }, { id: "without-geometry" }])("retains an unlocated row without inventing map authority: %j", async (row) => {
    const binding = (await loadNetworkFindAreasBindings(clientFor(row))).heatZones;
    expect(binding.state).toBe("ready");
    expect(binding.items[0]?.centroid).toBeNull();
    expect(binding.items[0]?.recordedMap).toBeUndefined();
    expect(zoneToFeature(operatorHeatZoneToMapZone(binding.items[0]!))).toBeNull();
  });

  it.each([
    { ...recordedCell, centroid: [181, 25] },
    { ...recordedCell, centroid: [121, 91] },
    { ...recordedCell, centroid: ["121", 25] },
    { ...recordedCell, centroid: [121] },
  ])("keeps absent or malformed geometry unavailable: %j", async (row) => {
    const binding = (await loadNetworkFindAreasBindings(clientFor(row))).heatZones;
    expect(binding.state).toBe("error");
    expect(binding.source).toBe("unavailable");
    expect(binding.items).toEqual([]);
    expect(binding.error).toBe("HEATZONE_GEOMETRY_UNAVAILABLE");
  });
});
