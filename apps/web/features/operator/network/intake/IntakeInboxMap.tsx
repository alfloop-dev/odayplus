"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";

const TAIWAN_CENTER: [number, number] = [121.0, 23.7];
const mapTileUrl = process.env.NEXT_PUBLIC_ODP_MAP_TILE_URL?.trim() ?? "";
const mapAttribution =
  process.env.NEXT_PUBLIC_ODP_MAP_ATTRIBUTION?.trim() ?? "ODay Plus map service";

export function intakeDetailHref(intakeId: string, query = ""): string {
  const encoded = encodeURIComponent(intakeId);
  return `/w/expansion/listings/intake/${encoded}${query ? `?${query}` : ""}`;
}

export function existingListingHref(listingId: string): string {
  const query = new URLSearchParams({ selected: listingId, drawer: "listing" });
  return `/w/expansion/listings?${query.toString()}`;
}

export function IntakeInboxMap({ records }: { records: AssistedIntake[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mapError, setMapError] = useState("");
  const located = useMemo(
    () => records.filter((record) => hasAuthoritativeLocation(record)),
    [records],
  );
  const unlocated = useMemo(
    () => records.filter((record) => !hasAuthoritativeLocation(record)),
    [records],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    const map = new maplibregl.Map({
      container,
      style: inboxMapStyle(),
      center: TAIWAN_CENTER,
      zoom: 6.2,
      attributionControl: mapTileUrl ? {} : false,
      interactive: true,
      renderWorldCopies: false,
    });
    mapRef.current = map;

    const markers: maplibregl.Marker[] = [];
    for (const record of located) {
      const anchor = document.createElement("a");
      anchor.className = styles.intakeMapMarker;
      anchor.dataset.testid = `intake-map-marker-${record.id}`;
      anchor.href = intakeDetailHref(record.id);
      anchor.setAttribute(
        "aria-label",
        `開啟收件 ${record.id}，座標 ${record.location!.latitude}, ${record.location!.longitude}`,
      );
      anchor.textContent = record.matchResult?.outcome === "POSSIBLE_MATCH" ? "!" : "•";
      anchor.title = `${record.id} · ${record.sourceId}`;

      markers.push(
        new maplibregl.Marker({ element: anchor, anchor: "center" })
          .setLngLat([record.location!.longitude, record.location!.latitude])
          .addTo(map),
      );
    }

    if (located.length === 1) {
      map.setCenter([located[0].location!.longitude, located[0].location!.latitude]);
      map.setZoom(15);
    } else if (located.length > 1) {
      const bounds = new maplibregl.LngLatBounds();
      for (const record of located) {
        bounds.extend([record.location!.longitude, record.location!.latitude]);
      }
      map.fitBounds(bounds, { maxZoom: 16, padding: 48 });
    }

    map.on("error", (event) => {
      setMapError(event.error?.message ?? "地圖無法繪製");
    });

    return () => {
      for (const marker of markers) marker.remove();
      map.remove();
      mapRef.current = null;
    };
  }, [located]);

  return (
    <section
      aria-label="Listing Inbox 地理位置"
      className={styles.intakeMapLayout}
      data-map-engine="maplibre"
      data-map-source={mapTileUrl ? "configured-raster-tiles" : "local-coordinate-canvas"}
      data-testid="intake-map-view-panel"
    >
      <div>
        <div
          aria-label={`地圖，共 ${located.length} 筆具權威座標的收件`}
          className={styles.intakeMapCanvas}
          data-testid="intake-map-canvas"
          ref={containerRef}
        />
        <p className={styles.queueHint} role="status">
          僅繪製 API 回傳的 parsed field／source snapshot 座標；不使用 HeatZone 中心點代替物件位置。
        </p>
        {mapError ? (
          <div className={styles.errorPanel} data-testid="intake-map-error" role="alert">
            <span className={styles.errorSummary}>{mapError}</span>
            <span className={styles.errorNext}>下一步：改用列表檢視；資料與篩選狀態保持不變。</span>
          </div>
        ) : null}
      </div>

      <aside aria-label="待定位收件" className={styles.intakeUnlocated}>
        <strong>待定位 ({unlocated.length})</strong>
        {unlocated.length === 0 ? (
          <p>本頁所有收件皆有權威座標。</p>
        ) : (
          <ul data-testid="intake-unlocated-list">
            {unlocated.map((record) => (
              <li key={record.id}>
                <a href={intakeDetailHref(record.id)}>
                  {record.id} · {record.sourceId}
                </a>
                <span>缺少可驗證 latitude/longitude</span>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </section>
  );
}

function inboxMapStyle(): maplibregl.StyleSpecification {
  const background: maplibregl.BackgroundLayerSpecification = {
    id: "intake-inbox-background",
    type: "background",
    paint: { "background-color": "#eef2f6" },
  };
  if (!mapTileUrl) {
    return {
      version: 8,
      name: "ODay Plus intake coordinate canvas",
      sources: {},
      layers: [background],
    };
  }
  return {
    version: 8,
    name: "ODay Plus intake geographic map",
    sources: {
      "odp-intake-map-tiles": {
        type: "raster",
        tiles: [mapTileUrl],
        tileSize: 256,
        attribution: mapAttribution,
      },
    },
    layers: [
      background,
      {
        id: "odp-intake-map-raster",
        type: "raster",
        source: "odp-intake-map-tiles",
      },
    ],
  };
}

function hasAuthoritativeLocation(
  record: AssistedIntake,
): record is AssistedIntake & {
  location: { latitude: number; longitude: number; source: string };
} {
  return Boolean(
    record.location &&
      Number.isFinite(record.location.latitude) &&
      Number.isFinite(record.location.longitude) &&
      record.location.latitude >= -90 &&
      record.location.latitude <= 90 &&
      record.location.longitude >= -180 &&
      record.location.longitude <= 180 &&
      record.location.source,
  );
}
