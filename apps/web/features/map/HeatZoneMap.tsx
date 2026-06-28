"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Deck } from "@deck.gl/core";
import { GeoJsonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
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
  const deckRef = useRef<Deck<any> | null>(null);
  const [layers, setLayers] = useState({
    h3: true,
    listings: true,
    candidates: true,
    confidence: true,
    freshness: true,
  });

  const zoneFeatures = useMemo(() => zones.map(zoneToFeature), [zones]);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: localMapStyle,
      center: [121.48, 25.0],
      zoom: 9.1,
      attributionControl: false,
      interactive: true,
      renderWorldCopies: false,
    });

    mapRef.current = map;
    map.on("load", () => {
      fitToZones(map, zones);
      map.resize();
    });

    const deck = new Deck({
      parent: mapContainerRef.current,
      controller: false,
      initialViewState: {
        longitude: 121.48,
        latitude: 25.0,
        zoom: 9.1,
        pitch: 0,
        bearing: 0,
      },
      layers: [],
    });
    deckRef.current = deck;

    const syncDeckView = () => {
      const center = map.getCenter();
      deck.setProps({
        viewState: {
          longitude: center.lng,
          latitude: center.lat,
          zoom: map.getZoom(),
          pitch: map.getPitch(),
          bearing: map.getBearing(),
        },
      });
    };
    map.on("move", syncDeckView);
    syncDeckView();

    return () => {
      map.off("move", syncDeckView);
      deck.finalize();
      deckRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [zones]);

  useEffect(() => {
    deckRef.current?.setProps({
      layers: buildDeckLayers({
        zones,
        zoneFeatures,
        listings,
        candidates,
        selectedZoneId,
        visible: layers,
      }),
    });
  }, [candidates, layers, listings, selectedZoneId, zoneFeatures, zones]);

  return (
    <section
      aria-label="Interactive HeatZone map"
      className={styles.mapShell}
      data-selected-zone={selectedZoneId}
      data-testid="heat-zone-map"
    >
      <div className={styles.mapToolbar} aria-label="Map layer controls">
        <LayerToggle checked={layers.h3} label="H3 HeatZones" onChange={(checked) => setLayers({ ...layers, h3: checked })} />
        <LayerToggle checked={layers.listings} label="Listings" onChange={(checked) => setLayers({ ...layers, listings: checked })} />
        <LayerToggle checked={layers.candidates} label="Candidate sites" onChange={(checked) => setLayers({ ...layers, candidates: checked })} />
        <LayerToggle checked={layers.confidence} label="Confidence" onChange={(checked) => setLayers({ ...layers, confidence: checked })} />
        <LayerToggle checked={layers.freshness} label="Freshness" onChange={(checked) => setLayers({ ...layers, freshness: checked })} />
      </div>
      <div className={styles.mapCanvas} ref={mapContainerRef} data-testid="heat-zone-map-canvas">
        <p className={styles.mapStatus} data-testid="heat-zone-map-status">
          local MapLibre style · {freshness.status} · {freshness.sourceSnapshotId} · {freshness.modelVersion}
        </p>
      </div>
      <div className={styles.legend} aria-label="Map legend">
        <LegendItem swatch={styles.swatchGreen} label="expandable H3" />
        <LegendItem swatch={styles.swatchYellow} label="under-realized" />
        <LegendItem swatch={styles.swatchOrange} label="low confidence" />
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
  visible: Record<"h3" | "listings" | "candidates" | "confidence" | "freshness", boolean>;
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
      pickable: false,
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
      pickable: false,
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
      pickable: false,
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
