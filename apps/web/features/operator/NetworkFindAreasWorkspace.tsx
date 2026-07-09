"use client";

import type { CSSProperties } from "react";
import { useMemo, useState } from "react";
import { CANDIDATE_FIXTURES, HEAT_ZONE_FIXTURES, LISTING_FIXTURES } from "./fixtures";
import styles from "./networkFindAreas.module.css";
import type { Candidate, Listing, OperatorHeatZone } from "./types";
import {
  buildNetworkFindAreasViewModel,
  type NetworkFindAreasLens,
  type NetworkFindAreasMapPoint,
  type NetworkFindAreasZoneViewModel,
} from "./networkFindAreasViewModel";

export type NetworkFindAreasWorkspaceCallbacks = {
  onSelectHeatZone?: (heatZone: OperatorHeatZone) => void;
  onChangeLens?: (lens: NetworkFindAreasLens) => void;
  onToggleTracked?: (heatZone: OperatorHeatZone, tracked: boolean) => void;
  onSourceListings?: (heatZone: OperatorHeatZone) => void;
  onScoreCandidate?: (candidate: Candidate, heatZone: OperatorHeatZone) => void;
  onSubmitReview?: (heatZone: OperatorHeatZone) => void;
};

export type NetworkFindAreasWorkspaceProps = {
  heatZones?: OperatorHeatZone[];
  listings?: Listing[];
  candidates?: Candidate[];
  selectedHeatZoneId?: string;
  activeLens?: NetworkFindAreasLens;
  trackedHeatZoneIds?: string[];
  callbacks?: NetworkFindAreasWorkspaceCallbacks;
};

const networkTabs = [
  "找區域 / Find Areas",
  "物件雷達 / Listing Radar",
  "候選點 / Candidates",
  "SiteScore / Score Lab",
  "比較 / Compare",
  "審核 / Review",
  "低效重配 / Rebalance",
];

export function NetworkFindAreasWorkspace({
  activeLens,
  callbacks,
  candidates = CANDIDATE_FIXTURES,
  heatZones = HEAT_ZONE_FIXTURES,
  listings = LISTING_FIXTURES,
  selectedHeatZoneId,
  trackedHeatZoneIds,
}: NetworkFindAreasWorkspaceProps) {
  const [localSelectedId, setLocalSelectedId] = useState(selectedHeatZoneId ?? "HZ-01");
  const [localLens, setLocalLens] = useState<NetworkFindAreasLens>(activeLens ?? "demand");
  const [localTrackedIds, setLocalTrackedIds] = useState(() => new Set(trackedHeatZoneIds ?? ["HZ-01"]));
  const effectiveLens = activeLens ?? localLens;
  const effectiveSelectedId = selectedHeatZoneId ?? localSelectedId;
  const effectiveTrackedIds = trackedHeatZoneIds ?? Array.from(localTrackedIds);
  const trackedSet = useMemo(() => new Set(effectiveTrackedIds), [effectiveTrackedIds]);

  const viewModel = useMemo(
    () =>
      buildNetworkFindAreasViewModel({
        activeLens: effectiveLens,
        candidates,
        heatZones,
        listings,
        selectedHeatZoneId: effectiveSelectedId,
      }),
    [candidates, effectiveLens, effectiveSelectedId, heatZones, listings],
  );

  const selectedZone = viewModel.selectedZone;
  const isSelectedTracked = selectedZone ? trackedSet.has(selectedZone.id) : false;

  function selectHeatZone(zone: NetworkFindAreasZoneViewModel) {
    setLocalSelectedId(zone.id);
    callbacks?.onSelectHeatZone?.(zone.zone);
  }

  function changeLens(lens: NetworkFindAreasLens) {
    setLocalLens(lens);
    callbacks?.onChangeLens?.(lens);
  }

  function toggleTracked() {
    if (!selectedZone) {
      return;
    }
    const nextTracked = !trackedSet.has(selectedZone.id);
    if (!trackedHeatZoneIds) {
      setLocalTrackedIds((current) => {
        const next = new Set(current);
        if (nextTracked) {
          next.add(selectedZone.id);
        } else {
          next.delete(selectedZone.id);
        }
        return next;
      });
    }
    callbacks?.onToggleTracked?.(selectedZone.zone, nextTracked);
  }

  function sourceListings() {
    if (selectedZone) {
      callbacks?.onSourceListings?.(selectedZone.zone);
    }
  }

  function scoreCandidate() {
    if (selectedZone?.bestCandidate) {
      callbacks?.onScoreCandidate?.(selectedZone.bestCandidate, selectedZone.zone);
    }
  }

  function submitReview() {
    if (selectedZone) {
      callbacks?.onSubmitReview?.(selectedZone.zone);
    }
  }

  return (
    <section className={styles.workspace} data-testid="network-find-areas-workspace">
      <header className={styles.header}>
        <div>
          <p className={styles.kicker}>Network</p>
          <h2>Find Areas</h2>
        </div>
        <div className={styles.headerStats} aria-label="Network Find Areas state">
          <span>{viewModel.totals.heatZones} HeatZones</span>
          <span>{viewModel.totals.listings} listings</span>
          <span>{viewModel.totals.candidates} candidates</span>
          <span>{viewModel.totals.averageConfidence} avg confidence</span>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="Network tabs">
        {networkTabs.map((tab, index) => (
          <button
            aria-current={index === 0 ? "page" : undefined}
            aria-disabled={index !== 0}
            className={classNames(styles.tab, index !== 0 && styles.tabDisabled)}
            key={tab}
            type="button"
          >
            {tab}
          </button>
        ))}
      </nav>

      <section className={styles.lensBar} aria-label="HeatZone lenses">
        <div className={styles.lensSelector}>
          {viewModel.lenses.map((lens) => (
            <button
              aria-pressed={effectiveLens === lens.id}
              className={styles.lensButton}
              key={lens.id}
              onClick={() => changeLens(lens.id)}
              title={lens.description}
              type="button"
            >
              <span>{lens.shortLabel}</span>
              <small>{lens.label}</small>
            </button>
          ))}
        </div>
        <div className={styles.legend} aria-label="Map legend">
          <span className={styles.legendItem}>
            <i className={styles.legendGood} aria-hidden="true" /> High lens fit
          </span>
          <span className={styles.legendItem}>
            <i className={styles.legendWatch} aria-hidden="true" /> Watch tradeoff
          </span>
          <span className={styles.legendItem}>
            <i className={styles.legendRisk} aria-hidden="true" /> Risk pressure
          </span>
          <span className={styles.legendItem}>
            <i className={styles.legendCandidate} aria-hidden="true" /> Candidate
          </span>
        </div>
      </section>

      <section className={styles.mainGrid} aria-label="Find Areas workbench">
        <div className={styles.mapPanel}>
          <div className={styles.panelHeader}>
            <h3>HeatZone Lens Map</h3>
            <span>{viewModel.activeLens}</span>
          </div>
          <div className={styles.mapCanvas} aria-label="Deterministic local HeatZone map">
            <div className={styles.mapGrid} aria-hidden="true" />
            <div className={styles.mapRoadA} aria-hidden="true" />
            <div className={styles.mapRoadB} aria-hidden="true" />
            <div className={styles.mapRoadC} aria-hidden="true" />
            {viewModel.mapPoints.map((point) => (
              <MapPoint key={`${point.type}-${point.id}`} point={point} />
            ))}
            {viewModel.zones.map((zone) => (
              <button
                aria-current={selectedZone?.id === zone.id ? "true" : undefined}
                className={styles.zoneMarker}
                data-tone={zone.mapTone}
                key={zone.id}
                onClick={() => selectHeatZone(zone)}
                style={
                  {
                    "--marker-size": `${zone.mapSize}px`,
                    "--x": `${zone.mapX}%`,
                    "--y": `${zone.mapY}%`,
                  } as CSSProperties
                }
                type="button"
              >
                <strong>{zone.id}</strong>
                <span>{zone.lensLabel}</span>
              </button>
            ))}
          </div>
        </div>

        <aside className={styles.trayPanel} aria-label="Recommended find area tray">
          <div className={styles.panelHeader}>
            <h3>Recommended Areas</h3>
            <span>{viewModel.rankedZones.length} ranked</span>
          </div>
          <div className={styles.zoneList}>
            {viewModel.rankedZones.map((zone, index) => (
              <button
                aria-current={selectedZone?.id === zone.id ? "true" : undefined}
                className={styles.zoneRow}
                key={zone.id}
                onClick={() => selectHeatZone(zone)}
                type="button"
              >
                <span className={styles.rank}>#{index + 1}</span>
                <span className={styles.zoneRowMain}>
                  <strong>
                    {zone.id} · {zone.label}
                  </strong>
                  <small>
                    demand {zone.demandLabel} · fit {zone.fitLabel} · comp {zone.competitionLabel}
                  </small>
                </span>
                <span className={styles.zoneRowScore}>{zone.lensLabel}</span>
              </button>
            ))}
          </div>
        </aside>

        <article className={styles.detailPanel} aria-label="Selected HeatZone detail">
          {selectedZone ? (
            <>
              <div className={styles.detailTopline}>
                <div>
                  <span className={styles.kicker}>{selectedZone.id}</span>
                  <h3>{selectedZone.label}</h3>
                  <p>{selectedZone.centroidLabel}</p>
                </div>
                <div className={styles.detailActions}>
                  <button aria-pressed={isSelectedTracked} onClick={toggleTracked} type="button">
                    {isSelectedTracked ? "Tracked" : "Track"}
                  </button>
                  <button onClick={sourceListings} type="button">
                    Source Listings
                  </button>
                  <button disabled={!selectedZone.bestCandidate} onClick={scoreCandidate} type="button">
                    Score Candidate
                  </button>
                  <button onClick={submitReview} type="button">
                    Submit Review
                  </button>
                </div>
              </div>

              <div className={styles.metricGrid}>
                <Metric label="Demand" value={selectedZone.demandLabel} meter={selectedZone.demandGap} />
                <Metric label="Fit" value={selectedZone.fitLabel} meter={selectedZone.fitScore} />
                <Metric label="Competition" value={selectedZone.competitionLabel} meter={selectedZone.competitionIndex} />
                <Metric
                  label="Cannibalization"
                  value={selectedZone.cannibalizationLabel}
                  meter={1 - selectedZone.cannibalizationScore}
                />
                <Metric label="Rent" value={selectedZone.rentBand} meter={selectedZone.rentScore} />
                <Metric label="Confidence" value={selectedZone.confidenceLabel} meter={selectedZone.confidence} />
              </div>

              <div className={styles.detailGrid}>
                <section>
                  <h4>Reasons</h4>
                  <ul>
                    {selectedZone.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </section>
                <section>
                  <h4>Risks</h4>
                  <ul>
                    {selectedZone.risks.map((risk) => (
                      <li key={risk}>{risk}</li>
                    ))}
                  </ul>
                </section>
                <section>
                  <h4>Next Step</h4>
                  <p>{selectedZone.nextStep}</p>
                </section>
                <section>
                  <h4>Pipeline</h4>
                  <dl className={styles.pipelineStats}>
                    <div>
                      <dt>Listings</dt>
                      <dd>{selectedZone.listingCount}</dd>
                    </div>
                    <div>
                      <dt>Candidates</dt>
                      <dd>{selectedZone.candidateCount}</dd>
                    </div>
                    <div>
                      <dt>Best</dt>
                      <dd>{selectedZone.candidateSummary}</dd>
                    </div>
                  </dl>
                </section>
              </div>

              <div className={styles.linkedRows} aria-label="Linked listings and candidates">
                {selectedZone.listings.map((listing) => (
                  <span key={listing.id}>
                    <strong>{listing.id}</strong> {listing.status} · rent {formatCurrency(listing.rentPerMonth)} ·{" "}
                    {listing.areaPing} ping
                  </span>
                ))}
                {selectedZone.candidates.map((candidate) => (
                  <span key={candidate.id}>
                    <strong>{candidate.id}</strong> {candidate.recommendation} · score {candidate.score} ·{" "}
                    {candidate.status}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <div className={styles.emptyState}>No HeatZones</div>
          )}
        </article>
      </section>
    </section>
  );
}

function Metric({ label, meter, value }: { label: string; meter: number; value: string }) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
      <i aria-hidden="true">
        <b style={{ width: `${Math.max(4, Math.min(100, Math.round(meter * 100)))}%` }} />
      </i>
    </div>
  );
}

function MapPoint({ point }: { point: NetworkFindAreasMapPoint }) {
  return (
    <span
      aria-label={`${point.id} ${point.status}`}
      className={classNames(styles.mapPoint, point.type === "candidate" && styles.mapPointCandidate)}
      style={{ "--x": `${point.x}%`, "--y": `${point.y}%` } as CSSProperties}
      title={point.label}
    >
      {point.type === "candidate" ? "C" : "L"}
    </span>
  );
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
    style: "currency",
    currency: "TWD",
  }).format(value);
}

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}
