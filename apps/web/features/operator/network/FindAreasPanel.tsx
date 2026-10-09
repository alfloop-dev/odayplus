"use client";

import { useMemo, useState } from "react";
import dynamic from "next/dynamic";
import type { OperatorRoleId } from "../navigation";
import type { Candidate, Listing, OperatorHeatZone } from "../types";
import type {
  NetworkFindAreasLens,
  NetworkFindAreasViewModel,
  NetworkFindAreasZoneViewModel,
} from "../networkFindAreasViewModel";
import type { HeatZoneLensScore, HeatZoneMapProps } from "./HeatZoneMap";
import {
  OPERATOR_MAP_FRESHNESS,
  operatorCandidateToMapSite,
  operatorHeatZoneToMapZone,
  operatorListingToMapListing,
} from "./heatZoneMapAdapters";
import { GeocoderSearchPanel } from "./geocoder";
import type { GeocodeAuditEvent } from "./geocoder";
import { canSearchAddress, canSelectGeocodeCandidate } from "./geocoder/geocoderPermissions";
import type {
  CandidateSite as MapCandidateSite,
  HeatZone as MapHeatZone,
  Listing as MapListing,
} from "./mapTypes";
import styles from "./findAreasPanel.module.css";

// The map stack (deck.gl + maplibre-gl + h3-js) is by far the heaviest thing on
// the operator surface. It is only ever rendered inside the Find Areas tab, so
// it is loaded as its own chunk instead of being charged to the first load of
// every /operator and /intake request. `ssr: false` is correct here as well:
// HeatZoneMap builds the maplibre instance in an effect against a real DOM node,
// so the server render only ever produced an empty container.
const HeatZoneMap = dynamic<HeatZoneMapProps>(
  () => import("./HeatZoneMap").then((mod) => mod.HeatZoneMap),
  {
    ssr: false,
    loading: function HeatZoneMapLoading() {
      return (
        <div aria-live="polite" className={styles.mapLoading} data-testid="heat-zone-map-loading" role="status">
          HeatZone 地圖載入中…
        </div>
      );
    },
  },
);

// Only the layers Package 10 draws on this screen: zones, listings, candidates
// and the zone labels. Confidence has its own lens, so its overlay and the risk
// outline stay off here instead of muddying the lens colours.
const FIND_AREAS_MAP_LAYERS = "h3,listings,candidates,freshness";

const ZONE_NAME_SUFFIX = /(生活圈|商圈|住宅圈|學區|副都心)$/;

export type FindAreasPanelProps = {
  activeRoleId: OperatorRoleId;
  fixturesAllowed: boolean;
  viewModel: NetworkFindAreasViewModel;
  selectedZone: NetworkFindAreasZoneViewModel | null;
  effectiveLens: NetworkFindAreasLens;
  isSelectedTracked: boolean;
  heatZones: OperatorHeatZone[];
  listings: Listing[];
  candidates: Candidate[];
  /** Whether the active role may submit a listing URL (intake `submit`). */
  canAddListing: boolean;
  onSelectZone: (zone: NetworkFindAreasZoneViewModel) => void;
  onChangeLens: (lens: NetworkFindAreasLens) => void;
  onToggleTracked: () => void;
  onSourceListings: () => void;
  onAddListingFromUrl: () => void;
  onViewCandidates: () => void;
  onScoreCandidate: () => void;
  onSubmitReview: () => void;
};

export function FindAreasPanel({
  activeRoleId,
  canAddListing,
  candidates,
  effectiveLens,
  fixturesAllowed,
  heatZones,
  isSelectedTracked,
  listings,
  onAddListingFromUrl,
  onChangeLens,
  onScoreCandidate,
  onSelectZone,
  onSourceListings,
  onSubmitReview,
  onToggleTracked,
  onViewCandidates,
  selectedZone,
  viewModel,
}: FindAreasPanelProps) {
  const mapZones = useMemo<MapHeatZone[]>(() => heatZones.map(operatorHeatZoneToMapZone), [heatZones]);
  const mapListings = useMemo<MapListing[]>(
    () => listings.map((l, i) => operatorListingToMapListing(l, heatZones, i)),
    [listings, heatZones],
  );
  const mapCandidates = useMemo<MapCandidateSite[]>(
    () => candidates.map((c, i) => operatorCandidateToMapSite(c, heatZones, i)),
    [candidates, heatZones],
  );
  const lensScores = useMemo<HeatZoneLensScore[]>(
    () =>
      viewModel.zones.map((zone) => ({
        id: zone.id,
        label: zone.label.replace(ZONE_NAME_SUFFIX, ""),
        points: zone.lensPoints,
      })),
    [viewModel.zones],
  );
  const selectedMapZoneId = selectedZone?.id ?? heatZones[0]?.id ?? "";
  const lensName = viewModel.lenses.find((lens) => lens.id === effectiveLens)?.label ?? effectiveLens;
  // The accepted geocode is held here as a receipt rather than written through:
  // the production geocoder endpoint is not yet wired (see
  // docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md
  // §5, UX-SCR-EXP-001), so this surface shows what WOULD be persisted, with
  // its audit fields, instead of silently dropping the operator's decision.
  const [geocodeReceipt, setGeocodeReceipt] = useState<GeocodeAuditEvent | null>(null);

  return (
    <div className={styles.panel} data-screen-label="Network 找區域" data-testid="network-panel-find-areas" role="tabpanel">
      <div className={styles.side}>
        <section className={styles.card} aria-labelledby="find-areas-lens-title">
          <h3 className={styles.cardKicker} id="find-areas-lens-title">
            LENS · 分數越高越有利
          </h3>
          <div className={styles.lensList} data-testid="find-areas-lens-list">
            {viewModel.lenses.map((lens) => (
              <button
                aria-pressed={effectiveLens === lens.id}
                className={styles.lensButton}
                key={lens.id}
                onClick={() => onChangeLens(lens.id)}
                title={lens.description}
                type="button"
              >
                {lens.label}
              </button>
            ))}
          </div>
        </section>
        <section className={`${styles.card} ${styles.legend}`} aria-labelledby="find-areas-legend-title">
          <h3 className={styles.cardKicker} id="find-areas-legend-title">
            圖例
          </h3>
          <ul>
            <li>
              <i className={styles.swatchHigh} aria-hidden="true" />
              HeatZone ≥ 80
            </li>
            <li>
              <i className={styles.swatchMid} aria-hidden="true" />
              HeatZone 70–79
            </li>
            <li>
              <i className={styles.swatchWatch} aria-hidden="true" />
              HeatZone 60–69
            </li>
            <li>
              <i className={styles.swatchLow} aria-hidden="true" />
              HeatZone &lt; 60
            </li>
            <li>
              <i className={styles.dotListing} aria-hidden="true" />
              物件
            </li>
            <li>
              <i className={styles.dotCandidate} aria-hidden="true" />
              候選點
            </li>
            <li>
              <i className={styles.dotBlocked} aria-hidden="true" />
              候選點（缺資料）
            </li>
          </ul>
        </section>
      </div>

      <div className={styles.center}>
        <div className={styles.mapFrame} data-testid="find-areas-map-frame">
          <HeatZoneMap
            caption={`HeatZone Lens：${lensName}`}
            candidates={mapCandidates}
            dataSource={fixturesAllowed ? "fixture" : "api"}
            freshness={OPERATOR_MAP_FRESHNESS}
            layerQuery={FIND_AREAS_MAP_LAYERS}
            lensScores={lensScores}
            listings={mapListings}
            productionMode={!fixturesAllowed}
            selectedZoneId={selectedMapZoneId}
            zones={mapZones}
          />
        </div>

        <section className={styles.tray} aria-labelledby="find-areas-tray-title">
          <div className={styles.trayHeading}>
            <h3 id="find-areas-tray-title">推薦找點區域</h3>
            <span>依「{lensName}」排序</span>
          </div>
          <div className={styles.trayGrid} data-testid="find-areas-tray">
            {viewModel.rankedZones.map((zone) => (
              <button
                aria-current={selectedZone?.id === zone.id ? "true" : undefined}
                aria-label={`${zone.label} ${zone.lensPoints} 分`}
                className={styles.trayCard}
                data-zone-id={zone.id}
                key={zone.id}
                onClick={() => onSelectZone(zone)}
                type="button"
              >
                <span className={styles.trayCardTop}>
                  <strong>{zone.label}</strong>
                  <span className={styles.score} data-tier={zone.lensTier}>
                    {zone.lensPoints}
                  </span>
                </span>
                <span className={styles.trayMeta}>
                  物件 {zone.availableListingCount} · {zone.rentBand}
                </span>
                <span className={styles.traySub}>
                  {zone.id} · 候選點 {zone.candidateCount}
                </span>
              </button>
            ))}
          </div>
        </section>

        {/*
          Address search is not in the Package 10 Find Areas layout; it was added
          later (UX-SCR-EXP-001). It sits under the tray as one more card in the
          same column so it never pushes the map or the zone detail around.
        */}
        <div className={styles.geocoder}>
          <GeocoderSearchPanel
            actorRoleId={activeRoleId}
            canSearch={canSearchAddress(activeRoleId)}
            canSelect={canSelectGeocodeCandidate(activeRoleId)}
            onAudit={setGeocodeReceipt}
            onSelect={() => undefined}
          />
          {geocodeReceipt ? <GeocodeReceipt receipt={geocodeReceipt} /> : null}
        </div>
      </div>

      <article className={styles.detail} aria-labelledby="find-areas-detail-title" data-testid="find-areas-zone-detail">
        {selectedZone ? (
          <>
            <header>
              <div className={styles.detailTitleRow}>
                <h3 id="find-areas-detail-title">{selectedZone.label}</h3>
                {isSelectedTracked ? <span className={styles.trackedPill}>追蹤中</span> : null}
                <span
                  aria-label={`${lensName} ${selectedZone.lensPoints} 分`}
                  className={styles.bigScore}
                  data-testid="find-areas-zone-score"
                  data-tier={selectedZone.lensTier}
                >
                  {selectedZone.lensPoints}
                </span>
              </div>
              <p className={styles.detailSub}>
                {selectedZone.id} · HeatZone 綜合 {selectedZone.overallPoints} · Lens：{lensName}
              </p>
            </header>

            <dl className={styles.kvTable} data-testid="find-areas-zone-facts">
              {selectedZone.detailRows.map((row) => (
                <div key={row.key}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
            </dl>

            <section className={styles.bullets} data-tone="why" aria-labelledby="find-areas-why-title">
              <h4 id="find-areas-why-title">為什麼是這一區</h4>
              <ul>
                {selectedZone.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </section>

            <section className={styles.bullets} data-tone="risk" aria-labelledby="find-areas-risk-title">
              <h4 id="find-areas-risk-title">主要風險</h4>
              <ul>
                {selectedZone.risks.map((risk) => (
                  <li key={risk}>{risk}</li>
                ))}
              </ul>
            </section>

            <p className={styles.nextBox}>
              <span>下一步</span>
              {selectedZone.nextStep}
            </p>

            <div className={styles.actions} data-testid="find-areas-zone-actions">
              <button className={styles.primaryAction} onClick={onSourceListings} type="button">
                查看本區物件（{selectedZone.availableListingCount}）
              </button>
              <button
                className={styles.secondaryAction}
                disabled={!canAddListing}
                onClick={onAddListingFromUrl}
                title={canAddListing ? undefined : "目前角色沒有新增物件權限"}
                type="button"
              >
                ＋ 從網址新增物件（帶入本區）
              </button>
              <button
                aria-pressed={isSelectedTracked}
                className={styles.secondaryAction}
                onClick={onToggleTracked}
                type="button"
              >
                {isSelectedTracked ? "✓ 已追蹤（點擊移除）" : "加入追蹤"}
              </button>
              <button className={styles.secondaryAction} onClick={onViewCandidates} type="button">
                查看本區候選點（{selectedZone.candidateCount}）
              </button>
              <button
                className={styles.secondaryAction}
                disabled={!selectedZone.bestCandidate}
                onClick={onScoreCandidate}
                title={selectedZone.bestCandidate ? undefined : "本區尚無候選點"}
                type="button"
              >
                {selectedZone.bestCandidate ? `評分最佳候選點（${selectedZone.bestCandidate.id}）` : "評分最佳候選點"}
              </button>
              <button className={styles.secondaryAction} onClick={onSubmitReview} type="button">
                送出審核
              </button>
            </div>
          </>
        ) : (
          <div className={styles.empty}>尚無 HeatZone 資料</div>
        )}
      </article>
    </div>
  );
}

function GeocodeReceipt({ receipt }: { receipt: GeocodeAuditEvent }) {
  return (
    <div className={styles.geocodeReceipt} data-testid="find-areas-geocode-receipt" role="status">
      <strong>
        {receipt.action === "low_confidence_override"
          ? "已採用（人工覆核）"
          : receipt.action === "candidate_selected"
            ? "已採用"
            : "已記錄為無法定位"}
      </strong>
      <span>{receipt.addressRaw}</span>
      {receipt.selected ? (
        <span>
          {receipt.selected.latitude.toFixed(6)}, {receipt.selected.longitude.toFixed(6)} ·
          精度 {receipt.selected.precision || "未提供"} · 來源 {receipt.selected.provider || "未提供"}
        </span>
      ) : (
        <span>未取得座標；後續流程不會有推估位置。</span>
      )}
      {receipt.flags.length > 0 ? <span>品質旗標 {receipt.flags.join("、")}</span> : null}
      {receipt.reviewReason ? <span>覆核理由 {receipt.reviewReason}</span> : null}
      <span>
        操作者 {receipt.actorRoleId} · {receipt.occurredAt}
        {receipt.correlationId ? ` · correlation_id ${receipt.correlationId}` : ""}
      </span>
    </div>
  );
}
