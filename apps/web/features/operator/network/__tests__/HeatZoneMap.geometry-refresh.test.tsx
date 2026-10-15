import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HeatZoneMap } from "../HeatZoneMap";
import type { HeatZone } from "../mapTypes";

const maps = vi.hoisted(() => ({ instances: [] as any[] }));
vi.mock("@deck.gl/react", () => ({ default: () => null }));
vi.mock("@deck.gl/layers", () => ({
  GeoJsonLayer: class {}, ScatterplotLayer: class {}, TextLayer: class {},
}));
vi.mock("maplibre-gl", () => ({
  setWorkerUrl: vi.fn(),
  LngLatBounds: class {
    points: number[][] = [];
    extend(point: number[]) { this.points.push(point); return this; }
    isEmpty() { return this.points.length === 0; }
  },
  Map: class {
    listeners = new Map<string, () => void>();
    sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
    loaded = false;
    fitBounds = vi.fn(); resize = vi.fn(); triggerRepaint = vi.fn(); remove = vi.fn();
    setPaintProperty = vi.fn();
    constructor() { maps.instances.push(this); }
    on(name: string, callback: () => void) { this.listeners.set(name, callback); }
    once(name: string, callback: () => void) { this.listeners.set(name, callback); }
    off() {}
    emit(name: string) { if (name === "load") this.loaded = true; this.listeners.get(name)?.(); }
    addSource(name: string, source: { data: unknown }) { this.sources.set(name, { setData: vi.fn() }); }
    addLayer() {}
    getLayer() { return this.loaded; }
    getSource(name: string) { return this.sources.get(name); }
    isStyleLoaded() { return this.loaded; }
    getCenter() { return { lng: 0, lat: 0 }; }
    getZoom() { return 9; } getPitch() { return 0; } getBearing() { return 0; }
  },
}));

function zone(id: string, centroid: [number, number]): HeatZone {
  return { id, district: id, centroid, h3: "", h3Resolution: 9, score: 80,
    confidence: 0.8, state: "STILL_EXPANDABLE", rank: 1, listings: 0,
    warnings: [], reasons: [], modelVersion: "unit-recorded", featureVersion: "unit-recorded",
    featureSnapshotTime: "", predictionOriginTime: "", lastScoredAt: "", sourceSnapshotIds: [],
    unmetDemandScore: 0.8, formatFitScore: 0.8, cannibalizationRisk: 0.1,
    rentFeasibility: 0.8, listingAvailability: 0.8, poiCount: 0, competitorCount: 0,
    competitorCapacity: 0, medianListingRent: 0, existingStoreCount: 0, dataQualityScore: 0.8 };
}
const freshness = { status: "FRESH", updatedAt: "", modelVersion: "unit-recorded", featureSnapshotTime: "", sourceSnapshotId: "unit-recorded" };
describe("map viewport follows a real geometry replacement", () => {
  beforeEach(() => {
    maps.instances = [];
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
    window.history.replaceState(null, "", "/operator?ws=network");
  });
  afterEach(() => { cleanup(); vi.unstubAllEnvs(); });
  it("fits replacement zones after the already-loaded map receives their GeoJSON", () => {
    const initial = [zone("earlier-zone", [0, 0])];
    const replacement = [zone("HZ-01", [121.5668, 25.033]), zone("HZ-02", [121.4575, 25.01])];
    const props = { listings: [], candidates: [], freshness, productionMode: false };
    const view = render(<HeatZoneMap {...props} zones={initial} selectedZoneId="earlier-zone" />);
    const map = maps.instances[0];
    act(() => map.emit("load"));
    map.fitBounds.mockClear();
    view.rerender(<HeatZoneMap {...props} zones={replacement} selectedZoneId="HZ-01" />);
    const actualData = map.getSource("odp-local-heatzones").setData.mock.lastCall[0];
    expect(actualData.features.map((feature: any) => feature.properties.id)).toEqual(["HZ-01", "HZ-02"]);
    expect(map.fitBounds).toHaveBeenCalledTimes(1);
    expect(map.fitBounds.mock.lastCall[0].points).toEqual(replacement.map((item) => item.centroid));
    expect(map.fitBounds.mock.lastCall[1]).toEqual({ padding: 80, maxZoom: 11, duration: 0 });
    map.fitBounds.mockClear();
    view.rerender(<HeatZoneMap {...props} zones={[...replacement]} selectedZoneId="HZ-02"
      lensScores={[{ id: "HZ-01", label: "new lens", points: 60 }]} />);
    expect(map.fitBounds).not.toHaveBeenCalled();
  });
});
