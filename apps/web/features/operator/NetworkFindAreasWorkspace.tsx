"use client";

import type { CSSProperties, ReactNode } from "react";
import { useMemo, useState } from "react";
import {
  CANDIDATE_FIXTURES,
  HEAT_ZONE_FIXTURES,
  LISTING_FIXTURES,
  LISTING_SOURCE_FIXTURES,
  REBALANCE_STORE_FIXTURES,
  SITE_REVIEW_FIXTURES,
} from "./fixtures";
import styles from "./networkFindAreas.module.css";
import type { Candidate, Listing, ListingSource, OperatorHeatZone, RebalanceStore, SiteReview } from "./types";
import {
  buildNetworkFindAreasViewModel,
  type CandidatePipelineRow,
  type ListingRadarRow,
  type NetworkCompareViewModel,
  type NetworkFindAreasLens,
  type NetworkFindAreasMapPoint,
  type NetworkFindAreasViewModel,
  type NetworkFindAreasZoneViewModel,
  type RebalanceQueueRow,
  type ReviewQueueRow,
  type SiteScoreLabRow,
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
  listingSources?: ListingSource[];
  siteReviews?: SiteReview[];
  rebalanceStores?: RebalanceStore[];
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
] as const;

export function NetworkFindAreasWorkspace({
  activeLens,
  callbacks,
  candidates = CANDIDATE_FIXTURES,
  heatZones = HEAT_ZONE_FIXTURES,
  listings = LISTING_FIXTURES,
  listingSources = LISTING_SOURCE_FIXTURES,
  rebalanceStores = REBALANCE_STORE_FIXTURES,
  siteReviews = SITE_REVIEW_FIXTURES,
  selectedHeatZoneId,
  trackedHeatZoneIds,
}: NetworkFindAreasWorkspaceProps) {
  const [localSelectedId, setLocalSelectedId] = useState(selectedHeatZoneId ?? "HZ-01");
  const [localLens, setLocalLens] = useState<NetworkFindAreasLens>(activeLens ?? "demand");
  const [localTrackedIds, setLocalTrackedIds] = useState(() => new Set(trackedHeatZoneIds ?? ["HZ-01"]));
  const [activeTab, setActiveTab] = useState(0);
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
        listingSources,
        rebalanceStores,
        selectedHeatZoneId: effectiveSelectedId,
        siteReviews,
      }),
    [candidates, effectiveLens, effectiveSelectedId, heatZones, listings, listingSources, rebalanceStores, siteReviews],
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
          <span>{viewModel.totals.reviews} reviews</span>
          <span>{viewModel.totals.rebalances} rebalances</span>
          <span>{viewModel.totals.averageConfidence} avg confidence</span>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="Network tabs" role="tablist">
        {networkTabs.map((tab, index) => (
          <button
            aria-current={index === activeTab ? "page" : undefined}
            aria-selected={index === activeTab}
            className={classNames(styles.tab, index === activeTab && styles.tabActive)}
            data-testid={`network-tab-${index}`}
            key={tab}
            onClick={() => setActiveTab(index)}
            role="tab"
            type="button"
          >
            {tab}
          </button>
        ))}
      </nav>

      {activeTab === 1 ? (
        <ListingRadarPanel rows={viewModel.listingRadar} sources={listingSources} />
      ) : activeTab === 2 ? (
        <CandidatePipelinePanel rows={viewModel.candidatePipeline} />
      ) : activeTab === 3 ? (
        <SiteScoreLabPanel rows={viewModel.siteScoreLab} />
      ) : activeTab === 4 ? (
        <ComparePanel compare={viewModel.compare} />
      ) : activeTab === 5 ? (
        <ReviewQueuePanel rows={viewModel.reviewQueue} onSubmitReview={submitReview} />
      ) : activeTab === 6 ? (
        <RebalancePanel rows={viewModel.rebalanceQueue} />
      ) : (
        <FindAreasPanel
          viewModel={viewModel}
          selectedZone={selectedZone}
          effectiveLens={effectiveLens}
          isSelectedTracked={isSelectedTracked}
          onSelectZone={selectHeatZone}
          onChangeLens={changeLens}
          onToggleTracked={toggleTracked}
          onSourceListings={sourceListings}
          onScoreCandidate={scoreCandidate}
          onSubmitReview={submitReview}
        />
      )}
    </section>
  );
}

type FindAreasPanelProps = {
  viewModel: NetworkFindAreasViewModel;
  selectedZone: NetworkFindAreasZoneViewModel | null;
  effectiveLens: NetworkFindAreasLens;
  isSelectedTracked: boolean;
  onSelectZone: (zone: NetworkFindAreasZoneViewModel) => void;
  onChangeLens: (lens: NetworkFindAreasLens) => void;
  onToggleTracked: () => void;
  onSourceListings: () => void;
  onScoreCandidate: () => void;
  onSubmitReview: () => void;
};

function FindAreasPanel({
  effectiveLens,
  isSelectedTracked,
  onChangeLens,
  onScoreCandidate,
  onSelectZone,
  onSourceListings,
  onSubmitReview,
  onToggleTracked,
  selectedZone,
  viewModel,
}: FindAreasPanelProps) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-find-areas" role="tabpanel">
      <section className={styles.lensBar} aria-label="HeatZone lenses">
        <div className={styles.lensSelector}>
          {viewModel.lenses.map((lens) => (
            <button
              aria-pressed={effectiveLens === lens.id}
              className={styles.lensButton}
              key={lens.id}
              onClick={() => onChangeLens(lens.id)}
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
                onClick={() => onSelectZone(zone)}
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
                onClick={() => onSelectZone(zone)}
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
                  <button aria-pressed={isSelectedTracked} onClick={onToggleTracked} type="button">
                    {isSelectedTracked ? "Tracked" : "Track"}
                  </button>
                  <button onClick={onSourceListings} type="button">
                    Source Listings
                  </button>
                  <button disabled={!selectedZone.bestCandidate} onClick={onScoreCandidate} type="button">
                    Score Candidate
                  </button>
                  <button onClick={onSubmitReview} type="button">
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
    </div>
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

function ListingRadarPanel({ rows, sources }: { rows: ListingRadarRow[]; sources: ListingSource[] }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-listings" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>物件雷達 / Listing Radar</h3>
        <span>{rows.length} listings</span>
      </div>
      {rows.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.dataTable} data-testid="network-listing-table">
            <thead>
              <tr>
                <th>Listing</th>
                <th>HeatZone</th>
                <th>Status</th>
                <th>Rent / area</th>
                <th>Geocode</th>
                <th>Signals</th>
                <th>Candidate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-tone={row.tone}>
                  <td>
                    <strong>{row.id}</strong>
                    <small>{row.address}</small>
                    <small>{row.sourceName}</small>
                  </td>
                  <td>{row.zoneLabel}</td>
                  <td>
                    <ToneBadge tone={row.tone}>{row.statusLabel}</ToneBadge>
                  </td>
                  <td>
                    {row.rentLabel}
                    <small>{row.areaPing} ping</small>
                  </td>
                  <td>{row.geocodeConfidenceLabel}</td>
                  <td>
                    {row.isDuplicate ? <span className={styles.flag}>Dup {row.duplicateOfId ?? ""}</span> : null}
                    {row.hardRuleFailures.length ? (
                      <span className={styles.flagRisk}>{row.hardRuleFailures.join("; ")}</span>
                    ) : null}
                    {!row.isDuplicate && !row.hardRuleFailures.length ? <span className={styles.muted}>Clean</span> : null}
                  </td>
                  <td>{row.candidateId ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className={styles.emptyState}>No listings sourced yet</div>
      )}
      <div className={styles.cardRow} aria-label="Listing sources">
        {sources.map((source) => (
          <article className={styles.sourceCard} key={source.id}>
            <div className={styles.sourceCardHead}>
              <strong>{source.name}</strong>
              <span className={styles.muted}>{source.status}</span>
            </div>
            <p>{source.complianceNote}</p>
            {source.lastSyncedAt ? <small className={styles.muted}>Synced {source.lastSyncedAt}</small> : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function CandidatePipelinePanel({ rows }: { rows: CandidatePipelineRow[] }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-candidates" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>候選點 / Candidates</h3>
        <span>{rows.length} candidates</span>
      </div>
      {rows.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.dataTable} data-testid="network-candidate-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>HeatZone</th>
                <th>SiteScore</th>
                <th>Recommendation</th>
                <th>Status</th>
                <th>Missing data</th>
                <th>Model / snapshot</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-tone={row.tone}>
                  <td>
                    <strong>
                      {row.id}
                      {row.isBestInZone ? <span className={styles.bestTag}>Top</span> : null}
                    </strong>
                    <small>{row.title}</small>
                  </td>
                  <td>{row.zoneLabel}</td>
                  <td>
                    <ScoreMeter score={row.score} meter={row.scoreMeter} tone={row.tone} />
                  </td>
                  <td>
                    <ToneBadge tone={row.tone}>{row.recommendation}</ToneBadge>
                  </td>
                  <td>{row.statusLabel}</td>
                  <td>{row.missingData.length ? row.missingData.join("; ") : <span className={styles.muted}>None</span>}</td>
                  <td>
                    {row.modelVersion}
                    <small>{row.datasetSnapshotId}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className={styles.emptyState}>No candidates yet</div>
      )}
    </div>
  );
}

function SiteScoreLabPanel({ rows }: { rows: SiteScoreLabRow[] }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-sitescore" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>SiteScore / Score Lab</h3>
        <span>Recommendation ≠ decision · inputs frozen on approval</span>
      </div>
      {rows.length ? (
        <div className={styles.cardGrid}>
          {rows.map((row) => (
            <article className={styles.scoreCard} key={row.id} data-tone={row.tone} data-testid={`sitescore-card-${row.id}`}>
              <header className={styles.scoreCardHead}>
                <div>
                  <span className={styles.kicker}>{row.id}</span>
                  <strong>{row.title}</strong>
                  <small>{row.zoneLabel}</small>
                </div>
                <ToneBadge tone={row.tone}>{row.recommendation}</ToneBadge>
              </header>
              <ScoreMeter score={row.score} meter={row.scoreMeter} tone={row.tone} wide />
              <p className={styles.scoreBand}>{row.band}</p>
              <dl className={styles.scoreMeta}>
                <div>
                  <dt>Model</dt>
                  <dd>{row.modelVersion}</dd>
                </div>
                <div>
                  <dt>Snapshot</dt>
                  <dd>{row.datasetSnapshotId}</dd>
                </div>
              </dl>
              <div className={styles.gateRow}>
                <span className={row.evidenceReady ? styles.gateOk : styles.gateWarn}>{row.gateLabel}</span>
              </div>
              {row.missingData.length ? (
                <ul className={styles.missingList}>
                  {row.missingData.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <div className={styles.emptyState}>No SiteScore runs</div>
      )}
    </div>
  );
}

function ComparePanel({ compare }: { compare: NetworkCompareViewModel }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-compare" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>比較 / Compare</h3>
        <span>{compare.columns.length} HeatZones · leader highlighted</span>
      </div>
      {compare.columns.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.dataTable} data-testid="network-compare-table">
            <thead>
              <tr>
                <th>Metric</th>
                {compare.columns.map((column) => (
                  <th key={column.zoneId}>
                    {column.label}
                    <small>rank #{column.rank}</small>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {compare.metrics.map((metric) => (
                <tr key={metric.key}>
                  <th scope="row">{metric.label}</th>
                  {metric.values.map((value) => (
                    <td key={value.zoneId} className={value.isLeader ? styles.leaderCell : undefined}>
                      {value.label}
                      {value.isLeader ? <span className={styles.leaderMark}>▲</span> : null}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className={styles.emptyState}>Nothing to compare</div>
      )}
    </div>
  );
}

function ReviewQueuePanel({ rows, onSubmitReview }: { rows: ReviewQueueRow[]; onSubmitReview: () => void }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-review" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>審核 / Review</h3>
        <span>{rows.length} in queue</span>
      </div>
      {rows.length ? (
        <div className={styles.cardGrid}>
          {rows.map((row) => (
            <article className={styles.reviewCard} key={row.id} data-tone={row.tone} data-testid={`review-card-${row.id}`}>
              <header className={styles.scoreCardHead}>
                <div>
                  <span className={styles.kicker}>{row.id}</span>
                  <strong>{row.candidateTitle}</strong>
                  <small>{row.zoneLabel}</small>
                </div>
                <ToneBadge tone={row.tone}>{row.statusLabel}</ToneBadge>
              </header>
              <dl className={styles.scoreMeta}>
                <div>
                  <dt>Candidate</dt>
                  <dd>{row.candidateId}</dd>
                </div>
                <div>
                  <dt>SiteScore</dt>
                  <dd>{row.score !== undefined ? `${row.score} · ${row.recommendation ?? "—"}` : "—"}</dd>
                </div>
                <div>
                  <dt>Requested by</dt>
                  <dd>{row.requestedByLabel}</dd>
                </div>
                <div>
                  <dt>Reviewers</dt>
                  <dd>{row.reviewerLabels.join("、")}</dd>
                </div>
                <div>
                  <dt>Requested at</dt>
                  <dd>{row.requestedAt}</dd>
                </div>
              </dl>
              {row.reasonRequired ? <p className={styles.reasonNote}>此高風險審核需填寫決策理由。</p> : null}
              {row.reason ? <p className={styles.muted}>{row.reason}</p> : null}
              {row.status === "pending" ? (
                <div className={styles.detailActions}>
                  <button onClick={onSubmitReview} type="button">
                    Submit Review
                  </button>
                </div>
              ) : row.decidedAt ? (
                <small className={styles.muted}>Decided {row.decidedAt}</small>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <div className={styles.emptyState}>No reviews pending</div>
      )}
    </div>
  );
}

function RebalancePanel({ rows }: { rows: RebalanceQueueRow[] }) {
  return (
    <div className={styles.tabPanel} data-testid="network-panel-rebalance" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>低效重配 / Rebalance</h3>
        <span>{rows.length} stores</span>
      </div>
      {rows.length ? (
        <div className={styles.cardGrid}>
          {rows.map((row) => (
            <article className={styles.reviewCard} key={row.id} data-tone={row.tone} data-testid={`rebalance-card-${row.id}`}>
              <header className={styles.scoreCardHead}>
                <div>
                  <span className={styles.kicker}>{row.id}</span>
                  <strong>{row.storeName}</strong>
                  <small>{row.storeId}</small>
                </div>
                <ToneBadge tone={row.tone}>{row.statusLabel}</ToneBadge>
              </header>
              <p>{row.summary}</p>
              <dl className={styles.scoreMeta}>
                <div>
                  <dt>AVM</dt>
                  <dd>{row.avmRequestId ?? "—"}</dd>
                </div>
                <div>
                  <dt>NetPlan</dt>
                  <dd>{row.netPlanOptionId ?? "—"}</dd>
                </div>
                <div>
                  <dt>Approval</dt>
                  <dd>{row.relatedApprovalId ?? "—"}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <div className={styles.emptyState}>No rebalance candidates</div>
      )}
    </div>
  );
}

function ScoreMeter({ score, meter, tone, wide }: { score: number; meter: number; tone: "good" | "watch" | "risk"; wide?: boolean }) {
  return (
    <div className={classNames(styles.scoreMeter, wide && styles.scoreMeterWide)} data-tone={tone}>
      <strong>{score}</strong>
      <i aria-hidden="true">
        <b style={{ width: `${Math.max(4, Math.min(100, Math.round(meter * 100)))}%` }} />
      </i>
    </div>
  );
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
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
