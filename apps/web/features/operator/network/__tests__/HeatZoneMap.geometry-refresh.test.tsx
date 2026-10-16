import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cellToBoundary } from "h3-js";
import { HeatZoneMap } from "../HeatZoneMap";
import type { HeatZone } from "../mapTypes";

const maps = vi.hoisted(() => ({ instances: [] as any[], layers: [] as any[] }));
vi.mock("@deck.gl/react", () => ({ default: ({ layers }: { layers: any[] }) => { maps.layers = layers; return null; } }));
vi.mock("@deck.gl/layers", () => ({
  GeoJsonLayer: class { constructor(public props: any) {} },
  ScatterplotLayer: class { constructor(public props: any) {} },
  TextLayer: class { constructor(public props: any) {} },
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
    sources = new Map<string, { data: unknown; setData: ReturnType<typeof vi.fn> }>();
    loaded = false;
    fitBounds = vi.fn(); resize = vi.fn(); triggerRepaint = vi.fn(); remove = vi.fn();
    setPaintProperty = vi.fn();
    constructor() { maps.instances.push(this); }
    on(name: string, callback: () => void) { this.listeners.set(name, callback); }
    once(name: string, callback: () => void) { this.listeners.set(name, callback); }
    off() {}
    emit(name: string) { if (name === "load") this.loaded = true; this.listeners.get(name)?.(); }
    addSource(name: string, source: { data: unknown }) { this.sources.set(name, { data: source.data, setData: vi.fn() }); }
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
    maps.layers = [];
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

  it.each(["", "h3-synthetic-zone"])("keeps production zones with unavailable geometry off every polygon, label and pick target (%s)", (h3) => {
    vi.stubEnv("NEXT_PUBLIC_ODP_MAP_TILE_URL", "https://tiles.example.invalid/{z}/{x}/{y}.png");
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "https://geocoder.example.invalid/search");
    const missing = { ...zone("actual-row-without-geometry", [121.5, 25]), h3 };
    render(<HeatZoneMap zones={[missing]} listings={[]} candidates={[]} selectedZoneId={missing.id}
      freshness={{ ...freshness, status: "MISSING", modelVersion: "", sourceSnapshotId: "" }}
      productionMode dataSource="api" />);
    const map = maps.instances[0];
    act(() => map.emit("load"));
    expect(map.getSource("odp-local-heatzones").data.features).toEqual([]);
    expect(maps.layers.find((layer) => layer.props.id === "odp-heatzone-labels").props.data).toEqual([]);
    expect(screen.getByTestId("map-geometry-unavailable")).toHaveTextContent("地圖 geometry 尚未可用；列表仍可用於審查。");
    // The mock deliberately has no project(): a missing zone must never be a
    // centroid-based canvas pick target merely because its row has a location.
    fireEvent.click(screen.getByTestId("heat-zone-map-canvas"));
  });

  it("preserves the actual valid H3 boundary beside a missing production geometry", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_MAP_TILE_URL", "https://tiles.example.invalid/{z}/{x}/{y}.png");
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "https://geocoder.example.invalid/search");
    const valid = { ...zone("recorded-cell", [-122.4, 37.7]), h3: "8928308280fffff" };
    const missing = zone("missing-cell", [121.5, 25]);
    render(<HeatZoneMap zones={[valid, missing]} listings={[]} candidates={[]} selectedZoneId={valid.id}
      freshness={freshness} productionMode dataSource="api" />);
    act(() => maps.instances[0].emit("load"));
    const features = maps.instances[0].getSource("odp-local-heatzones").data.features;
    const ring = cellToBoundary(valid.h3, true);
    expect(features.map((feature: any) => feature.properties.id)).toEqual([valid.id]);
    expect(features[0].geometry.coordinates).toEqual([[...ring, ring[0]]]);
    expect(maps.layers.find((layer) => layer.props.id === "odp-heatzone-labels").props.data).toEqual([valid]);
    expect(screen.getByTestId("map-geometry-unavailable")).toBeInTheDocument();
  });

  it("does not use fixture rectangles for an API source outside production either", () => {
    const missing = zone("api-row", [121.5, 25]);
    render(<HeatZoneMap zones={[missing]} listings={[]} candidates={[]} selectedZoneId={missing.id}
      freshness={freshness} productionMode={false} dataSource="api" />);
    act(() => maps.instances[0].emit("load"));
    expect(maps.instances[0].getSource("odp-local-heatzones").data.features).toEqual([]);
    expect(screen.getByTestId("map-geometry-unavailable")).toBeInTheDocument();
  });

  it("preserves a canonical explicit-null geometry even beside a real H3 reference", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_MAP_TILE_URL", "https://tiles.example.invalid/{z}/{x}/{y}.png");
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "https://geocoder.example.invalid/search");
    const native = { ...zone("native-null", [-122.4, 37.7]), h3: "8928308280fffff", geometry: null };
    render(<HeatZoneMap zones={[native]} listings={[]} candidates={[]} selectedZoneId={native.id}
      freshness={freshness} productionMode dataSource="api" />);
    act(() => maps.instances[0].emit("load"));
    expect(maps.instances[0].getSource("odp-local-heatzones").data.features).toEqual([]);
    expect(screen.getByTestId("map-geometry-unavailable")).toBeInTheDocument();
  });

  it("uses an actual supplied polygon without replacing it with an H3-derived or centroid rectangle", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_MAP_TILE_URL", "https://tiles.example.invalid/{z}/{x}/{y}.png");
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "https://geocoder.example.invalid/search");
    const geometry: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[121.5, 25], [121.51, 25], [121.5, 25.01], [121.5, 25]]] };
    const native = { ...zone("native-polygon", [121.5, 25]), h3: "8928308280fffff", geometry };
    render(<HeatZoneMap zones={[native]} listings={[]} candidates={[]} selectedZoneId={native.id}
      freshness={freshness} productionMode dataSource="api" />);
    act(() => maps.instances[0].emit("load"));
    expect(maps.instances[0].getSource("odp-local-heatzones").data.features[0].geometry).toEqual(geometry);
    expect(screen.queryByTestId("map-geometry-unavailable")).toBeNull();
  });

  it("still explains native null geometry when the production provider is unavailable", () => {
    vi.stubEnv("NEXT_PUBLIC_ODP_MAP_TILE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_ODP_GEOCODER_URL", "");
    const native = { ...zone("native-null-provider-unavailable", [-122.4, 37.7]), h3: "8928308280fffff", geometry: null };
    render(<HeatZoneMap zones={[native]} listings={[]} candidates={[]} selectedZoneId={native.id}
      freshness={freshness} productionMode dataSource="api" />);
    expect(screen.getByTestId("heat-zone-map-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("map-geometry-unavailable")).toHaveTextContent("地圖 geometry 尚未可用；列表仍可用於審查。");
    expect(screen.queryByTestId("heat-zone-map-canvas")).toBeNull();
    expect(maps.instances).toHaveLength(0);
  });

});
