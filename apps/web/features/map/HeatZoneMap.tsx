"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { GeoJsonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import DeckGL from "@deck.gl/react";
import { cellToBoundary } from "h3-js";
import maplibregl from "maplibre-gl";
import type { CandidateSite, HeatZone, Listing } from "../expansion/data.ts";
import styles from "./map.module.css";

type Freshness = {
  status: string;
  updatedAt: string;
  modelVersion: string;
  featureSnapshotTime: string;
  sourceSnapshotId: string;
};

type HeatZoneMapProps = {
  zones: HeatZone[];
  listings: Listing[];
  candidates: CandidateSite[];
  selectedZoneId: string;
  freshness: Freshness;
};

type ZoneFeature = GeoJSON.Feature<GeoJSON.Polygon, HeatZone>;
type PickInfo = { object?: unknown; layer?: { id?: string } | null };
type LayerKey = "h3" | "listings" | "candidates" | "confidence" | "freshness" | "risk";
type LayerState = Record<LayerKey, boolean>;

const layerKeys: LayerKey[] = ["h3", "listings", "candidates", "confidence", "freshness", "risk"];
const directPickRadius = {
  candidate: 80,
  listing: 56,
  zone: 90,
};
const defaultLayers: LayerState = {
  h3: true,
  listings: true,
  candidates: true,
  confidence: true,
  freshness: true,
  risk: true,
};

const stateFill: Record<HeatZone["state"], [number, number, number, number]> = {
  UNTOUCHED: [49, 130, 206, 72],
  PARTIALLY_ABSORBED: [49, 130, 206, 92],
  SATURATED: [113, 128, 150, 72],
  UNDER_REALIZED: [183, 121, 31, 118],
  STILL_EXPANDABLE: [47, 133, 90, 128],
  SUPPRESSED_LOW_CONFIDENCE: [192, 86, 33, 128],
};

const confidenceFill: Record<"high" | "medium" | "low", [number, number, number, number]> = {
  high: [47, 133, 90, 90],
  medium: [183, 121, 31, 90],
  low: [192, 86, 33, 90],
};

export function HeatZoneMap({
  zones,
  listings,
  candidates,
  selectedZoneId,
  freshness,
}: HeatZoneMapProps) {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [viewState, setViewState] = useState({
    longitude: 121.48,
    latitude: 25.0,
    zoom: 9.1,
    pitch: 0,
    bearing: 0,
  });
  const [layers, setLayers] = useState<LayerState>(defaultLayers);

  const zoneFeatures = useMemo(() => zones.map(zoneToFeature), [zones]);
  const deckLayers = useMemo(
    () => buildDeckLayers({
      zones,
      zoneFeatures,
      listings,
      candidates,
      selectedZoneId,
      visible: layers,
    }),
    [candidates, layers, listings, selectedZoneId, zoneFeatures, zones],
  );

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: localMapStyle,
      center: [121.48, 25.0],
      zoom: 9.1,
      attributionControl: false,
      interactive: true,
      canvasContextAttributes: {
        preserveDrawingBuffer: true,
      },
      renderWorldCopies: false,
    });

    mapRef.current = map;
    map.on("load", () => {
      map.addSource("odp-local-heatzones", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: zoneFeatures,
        },
      });
      map.addLayer({
        id: "odp-local-heatzone-fill",
        type: "fill",
        source: "odp-local-heatzones",
        paint: {
          "fill-color": [
            "match",
            ["get", "state"],
            "UNDER_REALIZED",
            "#b7791f",
            "STILL_EXPANDABLE",
            "#2f855a",
            "SUPPRESSED_LOW_CONFIDENCE",
            "#c05621",
            "#3182ce",
          ],
          "fill-opacity": 0.34,
        },
      });
      map.addLayer({
        id: "odp-local-heatzone-line",
        type: "line",
        source: "odp-local-heatzones",
        paint: {
          "line-color": [
            "case",
            ["==", ["get", "id"], selectedZoneId],
            "#172554",
            "#ffffff",
          ],
          "line-width": [
            "case",
            ["==", ["get", "id"], selectedZoneId],
            4,
            1.5,
          ],
        },
      });
      fitToZones(map, zones);
      map.resize();
    });
    window.__odpHeatZoneMapProject = (coordinates: [number, number]) => {
      const projected = map.project(coordinates);
      return { x: projected.x, y: projected.y };
    };

    const syncDeckView = () => {
      const center = map.getCenter();
      setViewState({
        longitude: center.lng,
        latitude: center.lat,
        zoom: map.getZoom(),
        pitch: map.getPitch(),
        bearing: map.getBearing(),
      });
    };
    map.on("move", syncDeckView);
    syncDeckView();

    return () => {
      map.off("move", syncDeckView);
      delete window.__odpHeatZoneMapProject;
      map.remove();
      mapRef.current = null;
    };
  }, [selectedZoneId, zoneFeatures, zones]);

  useEffect(() => {
    setLayers(readLayerStateFromUrl());
  }, []);

  const updateLayer = (layer: LayerKey, checked: boolean) => {
    setLayers((current) => {
      const next = { ...current, [layer]: checked };
      writeLayerStateToUrl(next);
      return next;
    });
  };

  const handlePick = (info: PickInfo) => {
    const layerId = info.layer?.id;
    if (!layerId || !info.object) return;
    const href = pickHref(layerId, info.object, layers);
    if (href) window.location.assign(href);
  };

  const handleCanvasClick = (event: MouseEvent<HTMLDivElement>) => {
    const map = mapRef.current;
    if (!map) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const href = nearestPickHref(point, { zones, listings, candidates, layers, map });
    if (href) window.location.assign(href);
  };

  return (
    <section
      aria-label="Interactive HeatZone map"
      className={styles.mapShell}
      data-selected-zone={selectedZoneId}
      data-testid="heat-zone-map"
    >
      <div className={styles.mapToolbar} aria-label="Map layer controls">
        <LayerToggle checked={layers.h3} label="H3 HeatZones" onChange={(checked) => updateLayer("h3", checked)} />
        <LayerToggle checked={layers.listings} label="Listings" onChange={(checked) => updateLayer("listings", checked)} />
        <LayerToggle checked={layers.candidates} label="Candidate sites" onChange={(checked) => updateLayer("candidates", checked)} />
        <LayerToggle checked={layers.confidence} label="Confidence" onChange={(checked) => updateLayer("confidence", checked)} />
        <LayerToggle checked={layers.freshness} label="Freshness" onChange={(checked) => updateLayer("freshness", checked)} />
        <LayerToggle checked={layers.risk} label="Risk" onChange={(checked) => updateLayer("risk", checked)} />
      </div>
      <div className={styles.mapCanvas} onClickCapture={handleCanvasClick} ref={mapContainerRef} data-testid="heat-zone-map-canvas">
        <DeckGL
          controller={false}
          layers={deckLayers}
          onClick={handlePick}
          pickingRadius={8}
          style={{ cursor: "crosshair", inset: "0", pointerEvents: "auto", position: "absolute", zIndex: "1" }}
          viewState={viewState}
        >
          <div
            aria-label="deck.gl HeatZone overlay"
            className={styles.deckOverlay}
            data-testid="heat-zone-deck-overlay"
          />
        </DeckGL>
        <p className={styles.mapStatus} data-testid="heat-zone-map-status">
          local MapLibre style · layers {encodeLayerState(layers)} · {freshness.status} · {freshness.sourceSnapshotId} · {freshness.modelVersion}
        </p>
      </div>
      <div className={styles.legend} aria-label="Map legend">
        <LegendItem swatch={styles.swatchGreen} label="expandable H3" />
        <LegendItem swatch={styles.swatchYellow} label="under-realized" />
        <LegendItem swatch={styles.swatchOrange} label="low confidence" />
        <LegendItem swatch={styles.swatchOrange} label="risk boundary" />
        <LegendItem swatch={styles.swatchBlue} label="listing point" />
        <LegendItem swatch={styles.swatchPurple} label="candidate site" />
      </div>
    </section>
  );
}

function LayerToggle({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label>
      <input checked={checked} onChange={(event) => onChange(event.currentTarget.checked)} type="checkbox" />
      {label}
    </label>
  );
}

function LegendItem({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className={styles.legendItem}>
      <span className={`${styles.swatch} ${swatch}`} aria-hidden="true" />
      {label}
    </span>
  );
}

function buildDeckLayers({
  zones,
  zoneFeatures,
  listings,
  candidates,
  selectedZoneId,
  visible,
}: {
  zones: HeatZone[];
  zoneFeatures: ZoneFeature[];
  listings: Listing[];
  candidates: CandidateSite[];
  selectedZoneId: string;
  visible: LayerState;
}) {
  return [
    new GeoJsonLayer<ZoneFeature["properties"]>({
      id: "odp-heatzone-h3",
      data: zoneFeatures,
      visible: visible.h3,
      stroked: true,
      filled: true,
      getFillColor: (feature) => stateFill[feature.properties.state],
      getLineColor: (feature) => feature.properties.id === selectedZoneId ? [23, 37, 84, 255] : [255, 255, 255, 220],
      getLineWidth: (feature) => feature.properties.id === selectedZoneId ? 5 : 2,
      lineWidthMinPixels: 1,
      lineWidthMaxPixels: 6,
      pickable: true,
    }),
    new GeoJsonLayer<ZoneFeature["properties"]>({
      id: "odp-heatzone-confidence",
      data: zoneFeatures,
      visible: visible.confidence,
      stroked: false,
      filled: true,
      getFillColor: (feature) => confidenceFill[confidenceBand(feature.properties.confidence)],
      pickable: false,
    }),
    new GeoJsonLayer<ZoneFeature["properties"]>({
      id: "odp-heatzone-risk",
      data: zoneFeatures,
      visible: visible.risk,
      stroked: true,
      filled: false,
      getLineColor: (feature) => riskStroke(feature.properties),
      getLineWidth: 4,
      lineWidthMinPixels: 2,
      lineWidthMaxPixels: 8,
      pickable: false,
    }),
    new ScatterplotLayer<Listing>({
      id: "odp-listing-points",
      data: listings,
      visible: visible.listings,
      getPosition: (item) => item.coordinates,
      getFillColor: [43, 108, 176, 210],
      getLineColor: [255, 255, 255, 240],
      getRadius: 260,
      radiusUnits: "meters",
      lineWidthMinPixels: 1,
      stroked: true,
      filled: true,
      pickable: true,
    }),
    new ScatterplotLayer<CandidateSite>({
      id: "odp-candidate-sites",
      data: candidates,
      visible: visible.candidates,
      getPosition: (item) => item.coordinates,
      getFillColor: (item) => item.readiness === "ready" ? [107, 70, 193, 230] : [192, 86, 33, 230],
      getLineColor: [255, 255, 255, 240],
      getRadius: 360,
      radiusUnits: "meters",
      lineWidthMinPixels: 1,
      stroked: true,
      filled: true,
      pickable: true,
    }),
    new TextLayer<HeatZone>({
      id: "odp-heatzone-labels",
      data: zones,
      visible: visible.freshness,
      getPosition: (zone) => zone.centroid,
      getText: (zone) => `${zone.id}\n${zone.score} / ${zone.confidence.toFixed(2)}`,
      getColor: [23, 37, 84, 255],
      getSize: 13,
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      background: true,
      getBackgroundColor: [255, 255, 255, 210],
      backgroundPadding: [6, 4],
      pickable: false,
    }),
  ];
}

function readLayerStateFromUrl(): LayerState {
  if (typeof window === "undefined") return defaultLayers;
  const query = new URLSearchParams(window.location.search);
  const encoded = query.get("layers");
  if (!encoded) return defaultLayers;
  const enabled = new Set(encoded.split(",").filter(Boolean));
  return Object.fromEntries(layerKeys.map((key) => [key, enabled.has(key)])) as LayerState;
}

function writeLayerStateToUrl(layers: LayerState) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const encoded = encodeLayerState(layers);
  if (encoded === encodeLayerState(defaultLayers)) {
    url.searchParams.delete("layers");
  } else {
    url.searchParams.set("layers", encoded);
  }
  window.history.replaceState(window.history.state, "", url);
}

function encodeLayerState(layers: LayerState): string {
  return layerKeys.filter((key) => layers[key]).join(",");
}

function riskStroke(zone: HeatZone): [number, number, number, number] {
  if (zone.state === "SUPPRESSED_LOW_CONFIDENCE" || zone.confidence < 0.7) return [192, 86, 33, 245];
  if (zone.state === "UNDER_REALIZED") return [183, 121, 31, 230];
  if (zone.state === "SATURATED") return [113, 128, 150, 210];
  return [47, 133, 90, 210];
}

function pickHref(layerId: string, object: unknown, layers: LayerState): string {
  if (layerId === "odp-heatzone-h3") {
    const zone = (object as ZoneFeature).properties;
    if (!zone?.id) return "";
    const query = new URLSearchParams({ selected: zone.id, drawer: "zone" });
    const encodedLayers = encodeLayerState(layers);
    if (encodedLayers !== encodeLayerState(defaultLayers)) query.set("layers", encodedLayers);
    return `/w/expansion/heatzone?${query.toString()}`;
  }
  if (layerId === "odp-listing-points") {
    const listing = object as Listing;
    if (!listing.id) return "";
    return `/w/expansion/listings?selected=${encodeURIComponent(listing.id)}&drawer=listing`;
  }
  if (layerId === "odp-candidate-sites") {
    const candidate = object as CandidateSite;
    if (!candidate.id) return "";
    return `/w/expansion/candidates?selected=${encodeURIComponent(candidate.id)}&drawer=candidate`;
  }
  return "";
}

function nearestPickHref(
  point: { x: number; y: number },
  context: {
    zones: HeatZone[];
    listings: Listing[];
    candidates: CandidateSite[];
    layers: LayerState;
    map: maplibregl.Map;
  },
): string {
  const { zones, listings, candidates, layers, map } = context;
  const candidate = layers.candidates
    ? nearestProjected(candidates, point, map, (item) => item.coordinates, directPickRadius.candidate)
    : undefined;
  if (candidate) return pickHref("odp-candidate-sites", candidate, layers);

  const listing = layers.listings
    ? nearestProjected(listings, point, map, (item) => item.coordinates, directPickRadius.listing)
    : undefined;
  if (listing) return pickHref("odp-listing-points", listing, layers);

  const zone = layers.h3
    ? nearestProjected(zones, point, map, (item) => item.centroid, directPickRadius.zone)
    : undefined;
  if (zone) {
    return pickHref(
      "odp-heatzone-h3",
      { type: "Feature", properties: zone },
      layers,
    );
  }
  return "";
}

function nearestProjected<T>(
  items: T[],
  point: { x: number; y: number },
  map: maplibregl.Map,
  getCoordinates: (item: T) => [number, number],
  maxDistance: number,
): T | undefined {
  let nearest: { item: T; distance: number } | undefined;
  for (const item of items) {
    const projected = map.project(getCoordinates(item));
    const distance = Math.hypot(projected.x - point.x, projected.y - point.y);
    if (distance <= maxDistance && (!nearest || distance < nearest.distance)) {
      nearest = { item, distance };
    }
  }
  return nearest?.item;
}

declare global {
  interface Window {
    __odpHeatZoneMapProject?: (coordinates: [number, number]) => { x: number; y: number };
  }
}

function zoneToFeature(zone: HeatZone): ZoneFeature {
  try {
    const ring = cellToBoundary(zone.h3, true);
    return {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] },
      properties: zone,
    };
  } catch {
    const [lng, lat] = zone.centroid;
    const delta = 0.012;
    return {
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [[
          [lng - delta, lat - delta],
          [lng + delta, lat - delta],
          [lng + delta, lat + delta],
          [lng - delta, lat + delta],
          [lng - delta, lat - delta],
        ]],
      },
      properties: zone,
    };
  }
}

function confidenceBand(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.7) return "medium";
  return "low";
}

function fitToZones(map: maplibregl.Map, zones: HeatZone[]) {
  const bounds = new maplibregl.LngLatBounds();
  zones.forEach((zone) => bounds.extend(zone.centroid));
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: 80, maxZoom: 11, duration: 0 });
  }
}

const localMapStyle: maplibregl.StyleSpecification = {
  version: 8,
  name: "ODay Plus local fallback",
  sources: {},
  layers: [
    {
      id: "odp-local-background",
      type: "background",
      paint: { "background-color": "#eef4f7" },
    },
  ],
};
