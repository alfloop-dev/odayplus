"use client";

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  CANDIDATE_FIXTURES,
  HEAT_ZONE_FIXTURES,
  LISTING_FIXTURES,
  LISTING_SOURCE_FIXTURES,
  REBALANCE_STORE_FIXTURES,
  SITE_REVIEW_FIXTURES,
} from "./fixtures";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import styles from "./networkFindAreas.module.css";
import type { Candidate, Listing, ListingSource, OperatorHeatZone, RebalanceStore, SiteReview, SiteReviewStatus, CandidateStatus } from "./types";
import { ListingRadarPanel } from "./network/ListingRadarPanel";
import { NetworkShell } from "./network/NetworkShell";
import type { ExpansionStep } from "./network/ExpansionStepper";
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
import { HeatZoneMap } from "../map/HeatZoneMap";
import type { HeatZone as MapHeatZone, Listing as MapListing, CandidateSite as MapCandidateSite } from "../expansion/data";

export type NetworkFindAreasWorkspaceCallbacks = {
  onSelectHeatZone?: (heatZone: OperatorHeatZone) => void;
  onChangeLens?: (lens: NetworkFindAreasLens) => void;
  onToggleTracked?: (heatZone: OperatorHeatZone, tracked: boolean) => void;
  onSourceListings?: (heatZone: OperatorHeatZone) => void;
  onScoreCandidate?: (candidate: Candidate, heatZone: OperatorHeatZone) => void;
  onSubmitReview?: (heatZone: OperatorHeatZone) => void;
  onDecideReview?: (reviewId: string, status: SiteReviewStatus, reason: string) => void;
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
  /**
   * Live API binding for heatzone scores. When `source === "api"` the
   * workspace renders live items; otherwise it falls back to bundled
   * HEAT_ZONE_FIXTURES and shows a fixture-mode indicator.
   */
  liveHeatZones?: ApiBinding<OperatorHeatZone>;
  /**
   * Live API binding for candidate sites. When `source === "api"` the
   * workspace renders live items; otherwise it falls back to bundled
   * CANDIDATE_FIXTURES.
   */
  liveCandidates?: ApiBinding<Candidate>;
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

type NetworkListingDetail = Listing & {
  archivedReason?: string;
  convertedAt?: string;
  firstSeenAt?: string;
  fitScore?: number;
  floor?: string;
  frontageMeters?: number;
  hardRuleSummary?: string;
  mergeReason?: string;
  mergedIntoId?: string;
  sourceEvidence?: string[];
  sourceListingId?: string;
  sourceUrl?: string;
};

type NetworkListingsSnapshot = {
  source?: "api" | "fixture";
  heatZones?: OperatorHeatZone[];
  listingSources?: ListingSource[];
  listings?: NetworkListingDetail[];
  candidates?: Candidate[];
  siteReviews?: SiteReview[];
  expansionSteps?: ExpansionStep[];
  selectedHeatZoneId?: string;
  selectedLens?: NetworkFindAreasLens;
  correlationId?: string;
};

const NETWORK_OPERATOR_HEADERS = {
  "X-Operator-Role": "expansion-manager",
  "X-Roles": "expansion_user",
  "X-Subject-Id": "operator-expansion-manager",
  "X-Tenant-Id": "tenant-a",
};

const NETWORK_ACTOR = {
  actorName: "王若寧",
  actorRoleId: "expansionManager",
};

async function fetchNetworkSnapshot(
  selectedHeatZoneId: string,
  lens: NetworkFindAreasLens,
): Promise<NetworkListingsSnapshot | null> {
  try {
    const params = new URLSearchParams({
      lens,
      selectedHeatZoneId,
    });
    const response = await fetch(`/api/v1/operator/network-listings?${params.toString()}`, {
      cache: "no-store",
      headers: {
        ...NETWORK_OPERATOR_HEADERS,
        "X-Correlation-Id": `corr-r4-005-read-${selectedHeatZoneId}-${lens}`,
      },
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as NetworkListingsSnapshot;
  } catch {
    return null;
  }
}

function buildFallbackExpansionSteps(selectedHeatZoneId: string, hasCandidate: boolean): ExpansionStep[] {
  return [
    {
      id: "find",
      label: "Find Area",
      state: "completed",
      tabIndex: 0,
      entityId: selectedHeatZoneId,
      summary: `${selectedHeatZoneId} selected.`,
    },
    {
      id: "radar",
      label: "Listing Radar",
      state: hasCandidate ? "completed" : "current",
      tabIndex: 1,
      entityId: "L-2024",
      summary: "Review clean, duplicate, and hard-rule listings.",
    },
    {
      id: "candidate",
      label: "Candidate",
      state: hasCandidate ? "current" : "next",
      tabIndex: 2,
      entityId: hasCandidate ? "CS-1001" : "L-2024",
      summary: "Convert listing into a candidate site.",
    },
    {
      id: "sitescore",
      label: "SiteScore",
      state: hasCandidate ? "next" : "blocked",
      tabIndex: 3,
      entityId: "CS-1001",
      summary: "Score candidate after conversion.",
    },
    {
      id: "compare",
      label: "Compare",
      state: hasCandidate ? "next" : "blocked",
      tabIndex: 4,
      entityId: "CS-1001",
      summary: "Compare candidate alternatives.",
    },
    {
      id: "review",
      label: "Review",
      state: "blocked",
      tabIndex: 5,
      entityId: null,
      summary: "Review opens after scoring gate.",
    },
  ];
}

export function NetworkFindAreasWorkspace({
  activeLens,
  callbacks,
  candidates: candidatesProp = CANDIDATE_FIXTURES,
  heatZones: heatZonesProp = HEAT_ZONE_FIXTURES,
  listings = LISTING_FIXTURES,
  listingSources = LISTING_SOURCE_FIXTURES,
  rebalanceStores = REBALANCE_STORE_FIXTURES,
  siteReviews = SITE_REVIEW_FIXTURES,
  selectedHeatZoneId,
  trackedHeatZoneIds,
  liveHeatZones,
  liveCandidates,
}: NetworkFindAreasWorkspaceProps) {
  const [localSelectedId, setLocalSelectedId] = useState(selectedHeatZoneId ?? "HZ-01");
  const [localLens, setLocalLens] = useState<NetworkFindAreasLens>(activeLens ?? "demand");
  const [localTrackedIds, setLocalTrackedIds] = useState(() => new Set(trackedHeatZoneIds ?? ["HZ-01"]));
  const [activeTab, setActiveTab] = useState(0);
  const [networkSnapshot, setNetworkSnapshot] = useState<NetworkListingsSnapshot | null>(null);
  const [networkApiError, setNetworkApiError] = useState<string | null>(null);
  const [busyListingId, setBusyListingId] = useState<string | null>(null);

  const snapshotHeatZones = networkSnapshot?.heatZones?.length ? networkSnapshot.heatZones : undefined;
  const heatZones =
    snapshotHeatZones ??
    (liveHeatZones?.source === "api" && liveHeatZones.items.length > 0
      ? liveHeatZones.items
      : heatZonesProp);
  const listingsEffective = networkSnapshot?.listings?.length ? networkSnapshot.listings : listings;
  const listingSourcesEffective = networkSnapshot?.listingSources?.length ? networkSnapshot.listingSources : listingSources;
  const candidates =
    networkSnapshot?.candidates ??
    (liveCandidates?.source === "api" && liveCandidates.items.length > 0
      ? liveCandidates.items
      : candidatesProp);
  const siteReviewsEffective = networkSnapshot?.siteReviews ?? siteReviews;

  // True when every Network R4 intake binding is still falling back to fixtures.
  const isFixtureFallback =
    networkSnapshot?.source !== "api" &&
    ((liveHeatZones !== undefined && liveHeatZones.source !== "api") ||
      (liveCandidates !== undefined && liveCandidates.source !== "api"));

  const [localSiteReviews, setLocalSiteReviews] = useState<SiteReview[]>(() => siteReviewsEffective);
  const [localCandidates, setLocalCandidates] = useState<Candidate[]>(() => candidates);

  useEffect(() => {
    setLocalSiteReviews(siteReviewsEffective);
  }, [siteReviewsEffective]);

  useEffect(() => {
    setLocalCandidates(candidates);
  }, [candidates]);

  const effectiveLens = activeLens ?? localLens;
  const effectiveSelectedId = selectedHeatZoneId ?? localSelectedId;
  const effectiveTrackedIds = trackedHeatZoneIds ?? Array.from(localTrackedIds);
  const trackedSet = useMemo(() => new Set(effectiveTrackedIds), [effectiveTrackedIds]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const snapshot = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens);
      if (!cancelled && snapshot) {
        setNetworkSnapshot(snapshot);
        setNetworkApiError(null);
      } else if (!cancelled && !snapshot) {
        setNetworkApiError("network-listings API unavailable; using fixtures");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [effectiveLens, effectiveSelectedId]);

  const viewModel = useMemo(
    () =>
      buildNetworkFindAreasViewModel({
        activeLens: effectiveLens,
        candidates: localCandidates,
        heatZones,
        listings: listingsEffective,
        listingSources: listingSourcesEffective,
        rebalanceStores,
        selectedHeatZoneId: effectiveSelectedId,
        siteReviews: localSiteReviews,
      }),
    [localCandidates, effectiveLens, effectiveSelectedId, heatZones, listingsEffective, listingSourcesEffective, rebalanceStores, localSiteReviews],
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

  async function reloadNetworkSnapshot() {
    const snapshot = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens);
    if (snapshot) {
      setNetworkSnapshot(snapshot);
      setNetworkApiError(null);
    }
  }

  async function postNetworkListingAction(
    listingId: string,
    action: "convert" | "merge" | "archive",
    body: Record<string, unknown>,
  ) {
    setBusyListingId(listingId);
    try {
      const response = await fetch(`/api/v1/operator/network-listings/listings/${listingId}/${action}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `r4-005-${action}-${listingId}`,
          "X-Correlation-Id": `corr-r4-005-${action}-${listingId}`,
          ...NETWORK_OPERATOR_HEADERS,
        },
        body: JSON.stringify({
          ...NETWORK_ACTOR,
          ...body,
        }),
      });
      if (!response.ok) {
        setNetworkApiError(`network-listings ${action} failed (${response.status})`);
        return null;
      }
      const payload = (await response.json()) as NetworkListingsSnapshot;
      await reloadNetworkSnapshot();
      return payload;
    } catch {
      setNetworkApiError(`network-listings ${action} failed`);
      return null;
    } finally {
      setBusyListingId(null);
    }
  }

  async function convertListing(listingId: string) {
    const payload = await postNetworkListingAction(listingId, "convert", {});
    if (payload) {
      setActiveTab(2);
    }
  }

  async function mergeListing(sourceListingId: string, targetListingId: string) {
    await postNetworkListingAction(sourceListingId, "merge", {
      reason: "Same address, rent, and source evidence verified; retain source evidence on target.",
      targetListingId,
    });
  }

  async function archiveListing(listingId: string) {
    await postNetworkListingAction(listingId, "archive", {
      reason: "Hard-rule archive: area and floor exceed ODAY_G2 intake policy.",
    });
  }

  function handleDecideReview(reviewId: string, status: SiteReviewStatus, reason: string) {
    setLocalSiteReviews((prev) =>
      prev.map((r) =>
        r.id === reviewId
          ? {
              ...r,
              status,
              reason,
              decidedAt: new Date().toISOString().substring(0, 19).replace("T", " "),
            }
          : r
      )
    );

    const review = localSiteReviews.find((r) => r.id === reviewId);
    if (review) {
      const candidateStatus: CandidateStatus | undefined =
        status === "approved" ? "approved" : status === "rejected" ? "rejected" : status === "returned" ? "wait" : undefined;
      if (candidateStatus) {
        setLocalCandidates((prev) =>
          prev.map((c) => (c.id === review.candidateId ? { ...c, status: candidateStatus } : c))
        );
      }
    }

    callbacks?.onDecideReview?.(reviewId, status, reason);
  }

  const expansionSteps =
    networkSnapshot?.expansionSteps ??
    buildFallbackExpansionSteps(
      effectiveSelectedId,
      viewModel.candidatePipeline.some((row) => row.id === "CS-1001"),
    );
  const selectedZoneLabel = selectedZone?.label ?? heatZones.find((zone) => zone.id === effectiveSelectedId)?.label;

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
          {isFixtureFallback && (
            <span className={styles.muted} aria-label="Data source: fixtures" title="API unavailable — showing bundled fixture data">
              fixture data
            </span>
          )}
          {networkApiError ? <span className={styles.muted}>{networkApiError}</span> : null}
        </div>
      </header>

      <NetworkShell activeTab={activeTab} onTabChange={setActiveTab} steps={expansionSteps} tabs={networkTabs}>
        {activeTab === 1 ? (
          <ListingRadarPanel
            busyListingId={busyListingId}
            listings={listingsEffective}
            onArchive={archiveListing}
            onConvert={convertListing}
            onMerge={mergeListing}
            rows={viewModel.listingRadar}
            selectedHeatZoneId={effectiveSelectedId}
            selectedZoneLabel={selectedZoneLabel}
            sources={listingSourcesEffective}
          />
        ) : activeTab === 2 ? (
          <CandidatePipelinePanel rows={viewModel.candidatePipeline} />
        ) : activeTab === 3 ? (
          <SiteScoreLabPanel rows={viewModel.siteScoreLab} />
        ) : activeTab === 4 ? (
          <ComparePanel compare={viewModel.compare} />
        ) : activeTab === 5 ? (
          <ReviewQueuePanel rows={viewModel.reviewQueue} onDecideReview={handleDecideReview} />
        ) : activeTab === 6 ? (
          <RebalancePanel rows={viewModel.rebalanceQueue} />
        ) : (
          <FindAreasPanel
            viewModel={viewModel}
            selectedZone={selectedZone}
            effectiveLens={effectiveLens}
            isSelectedTracked={isSelectedTracked}
            heatZones={heatZones}
            listings={listingsEffective}
            candidates={localCandidates}
            onSelectZone={selectHeatZone}
            onChangeLens={changeLens}
            onToggleTracked={toggleTracked}
            onSourceListings={sourceListings}
            onScoreCandidate={scoreCandidate}
            onSubmitReview={submitReview}
          />
        )}
      </NetworkShell>
    </section>
  );
}

type FindAreasPanelProps = {
  viewModel: NetworkFindAreasViewModel;
  selectedZone: NetworkFindAreasZoneViewModel | null;
  effectiveLens: NetworkFindAreasLens;
  isSelectedTracked: boolean;
  heatZones: OperatorHeatZone[];
  listings: Listing[];
  candidates: Candidate[];
  onSelectZone: (zone: NetworkFindAreasZoneViewModel) => void;
  onChangeLens: (lens: NetworkFindAreasLens) => void;
  onToggleTracked: () => void;
  onSourceListings: () => void;
  onScoreCandidate: () => void;
  onSubmitReview: () => void;
};

function FindAreasPanel({
  candidates,
  effectiveLens,
  heatZones,
  isSelectedTracked,
  listings,
  onChangeLens,
  onScoreCandidate,
  onSelectZone,
  onSourceListings,
  onSubmitReview,
  onToggleTracked,
  selectedZone,
  viewModel,
}: FindAreasPanelProps) {
  const mapZones = useMemo(
    () => heatZones.map(operatorHeatZoneToMapZone),
    [heatZones],
  );
  const mapListings = useMemo(
    () => listings.map((l, i) => operatorListingToMapListing(l, heatZones, i)),
    [listings, heatZones],
  );
  const mapCandidates = useMemo(
    () => candidates.map((c, i) => operatorCandidateToMapSite(c, heatZones, i)),
    [candidates, heatZones],
  );
  const selectedMapZoneId = selectedZone?.id ?? (heatZones[0]?.id ?? "");
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
          {/* Real MapLibre/deck.gl HeatZoneMap – replaces CSS-grid placeholder.
              When no tile URL is configured (default), HeatZoneMap falls back to
              its local MapLibre style (deterministic CSS background), preserving
              the tile-fallback contract from the task brief. */}
          <HeatZoneMap
            zones={mapZones}
            listings={mapListings}
            candidates={mapCandidates}
            selectedZoneId={selectedMapZoneId}
            freshness={OPERATOR_MAP_FRESHNESS}
          />
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

function ReviewQueuePanel({
  rows,
  onDecideReview,
}: {
  rows: ReviewQueueRow[];
  onDecideReview: (reviewId: string, status: SiteReviewStatus, reason: string) => void;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  const handleDecision = (row: ReviewQueueRow, status: SiteReviewStatus) => {
    const reason = (reasons[row.id] ?? "").trim();
    if (reason.length < 10) {
      setErrors((prev) => ({ ...prev, [row.id]: "決策理由需至少 10 個字" }));
      return;
    }
    setErrors((prev) => {
      const next = { ...prev };
      delete next[row.id];
      return next;
    });
    onDecideReview(row.id, status, reason);
  };

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
              {row.reasonRequired && row.status === "pending" ? (
                <p className={styles.reasonNote}>此高風險審核需填寫決策理由。</p>
              ) : null}
              {row.reason ? (
                <p className={styles.muted} data-testid={`review-reason-${row.id}`}>
                  <strong>決策理由：</strong>{row.reason}
                </p>
              ) : null}
              {row.status === "pending" ? (
                <div className={styles.reasonInputGroup}>
                  <label htmlFor={`reason-${row.id}`}>決策理由 (至少 10 個字):</label>
                  <textarea
                    id={`reason-${row.id}`}
                    data-testid={`review-reason-input-${row.id}`}
                    placeholder="請輸入核准/退回/駁回理由..."
                    value={reasons[row.id] ?? ""}
                    onChange={(e) => {
                      const val = e.target.value;
                      setReasons((prev) => ({ ...prev, [row.id]: val }));
                      if (val.trim().length >= 10) {
                        setErrors((prev) => {
                          const next = { ...prev };
                          delete next[row.id];
                          return next;
                        });
                      }
                    }}
                    className={styles.textarea}
                  />
                  {errors[row.id] && (
                    <p className={styles.errorText} data-testid={`review-error-${row.id}`}>
                      {errors[row.id]}
                    </p>
                  )}
                  <div className={styles.actionButtons}>
                    <button
                      onClick={() => handleDecision(row, "approved")}
                      className={styles.btnApprove}
                      data-testid={`review-btn-approve-${row.id}`}
                      type="button"
                    >
                      核准 (Approve)
                    </button>
                    <button
                      onClick={() => handleDecision(row, "returned")}
                      className={styles.btnReturn}
                      data-testid={`review-btn-return-${row.id}`}
                      type="button"
                    >
                      退回 (Return)
                    </button>
                    <button
                      onClick={() => handleDecision(row, "rejected")}
                      className={styles.btnReject}
                      data-testid={`review-btn-reject-${row.id}`}
                      type="button"
                    >
                      駁回 (Reject)
                    </button>
                  </div>
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
              {row.avmP50 !== undefined && (
                <div className={styles.rebalanceAvmBlock} data-testid={`rebalance-avm-${row.id}`}>
                  <div className={styles.rebalanceAvmHeader}>
                    <span>AVM 估值（P50 公允價值）</span>
                    <span className={styles.muted}>{row.avmConf ?? "中高（收益法＋市場比較）"}</span>
                  </div>
                  <div className={styles.avmValueP50}>
                    {formatCurrency(row.avmP50)}
                  </div>
                  <div className={styles.avmBands}>
                    <span>P10: {row.avmP10 ? formatCurrency(row.avmP10) : "—"}</span>
                    <span>P90: {row.avmP90 ? formatCurrency(row.avmP90) : "—"}</span>
                  </div>
                  {row.avmReserve && <div className={styles.avmReserveNote}>{row.avmReserve}</div>}
                </div>
              )}
              {row.netPlanScenarios && row.netPlanScenarios.length > 0 && (
                <div className={styles.rebalanceNetPlanBlock} data-testid={`rebalance-netplan-${row.id}`}>
                  <div className={styles.rebalanceNetPlanHeader}>NETPLAN 三案</div>
                  <div className={styles.netPlanScenarioList}>
                    {row.netPlanScenarios.map((sc, i) => (
                      <div
                        key={i}
                        className={classNames(
                          styles.netPlanScenarioCard,
                          sc.isSystemRecommendation && styles.netPlanScenarioCardRec
                        )}
                        data-testid={`rebalance-scenario-${i}`}
                      >
                        <div className={styles.scenarioTitleRow}>
                          <strong>{sc.name}</strong>
                          {sc.isSystemRecommendation && <span className={styles.recBadge}>系統建議</span>}
                          <span className={styles.roiValue}>{sc.roi}</span>
                        </div>
                        <p className={styles.scenarioDetails}>
                          投資 {sc.inv} · 回本 {sc.payback} · 風險 {sc.risk} · 時程 {sc.time}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
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

// ─── Operator → Map type adapters ──────────────────────────────────────────
// These bridge the operator-layer types (OperatorHeatZone, Listing, Candidate)
// to the expansion-layer types that HeatZoneMap expects.
// Synthetic/missing fields are derived deterministically from available data.

const OPERATOR_MAP_FRESHNESS = {
  status: "FRESH",
  updatedAt: "",
  modelVersion: "network-ops-local",
  featureSnapshotTime: "",
  sourceSnapshotId: "snap-network-ops-local",
};

/** Derive a canonical HeatZone state from OperatorHeatZone metrics. */
function deriveHeatZoneState(zone: OperatorHeatZone): MapHeatZone["state"] {
  if (zone.confidence < 0.7) return "SUPPRESSED_LOW_CONFIDENCE";
  if (zone.demandGap >= 0.75) return "STILL_EXPANDABLE";
  if (zone.demandGap >= 0.5) return "UNDER_REALIZED";
  if (zone.competitionIndex >= 0.7) return "SATURATED";
  return "PARTIALLY_ABSORBED";
}

/**
 * Convert an OperatorHeatZone to the MapHeatZone type expected by HeatZoneMap.
 * Fields not tracked by the operator layer are synthesised deterministically
 * so that the map renders correctly without requiring API data.
 */
function operatorHeatZoneToMapZone(zone: OperatorHeatZone): MapHeatZone {
  return {
    id: zone.id,
    district: zone.label,
    // h3 is intentionally invalid so that zoneToFeature falls back to the
    // centroid-delta polygon – a deterministic, no-network fallback.
    h3: `operator-${zone.id}`,
    centroid: zone.centroid,
    h3Resolution: 9,
    score: Math.round(zone.demandGap * 100),
    confidence: zone.confidence,
    state: deriveHeatZoneState(zone),
    rank: zone.rank,
    listings: 0,
    warnings: zone.risks,
    reasons: zone.reasons,
    modelVersion: "network-ops-local",
    featureVersion: "operator-proxy-v1",
    featureSnapshotTime: "",
    predictionOriginTime: "",
    lastScoredAt: "",
    sourceSnapshotIds: ["snap-network-ops-local"],
    unmetDemandScore: zone.demandGap,
    formatFitScore: 1 - zone.competitionIndex,
    cannibalizationRisk: zone.cannibalizationRisk === "low" ? 0.1 : zone.cannibalizationRisk === "medium" ? 0.35 : 0.65,
    rentFeasibility: 0.7,
    listingAvailability: 0.5,
    poiCount: 10,
    competitorCount: Math.round(zone.competitionIndex * 10),
    competitorCapacity: 20,
    medianListingRent: 0,
    existingStoreCount: 0,
    dataQualityScore: zone.confidence,
  };
}

/**
 * Convert an operator Listing to the MapListing type expected by HeatZoneMap.
 * Coordinates are inferred from the associated HeatZone centroid with a small
 * deterministic offset so listings don't stack on top of the zone marker.
 */
function operatorListingToMapListing(
  listing: Listing,
  heatZones: OperatorHeatZone[],
  index: number,
): MapListing {
  const zone = heatZones.find((z) => z.id === listing.heatZoneId);
  const [lng, lat] = zone?.centroid ?? [121.48, 25.0];
  // Small deterministic offsets so listings spread around the centroid
  const offset = 0.003;
  const angle = (index * 137.5 * Math.PI) / 180; // golden angle spread
  const coordinates: [number, number] = [
    lng + offset * Math.cos(angle),
    lat + offset * Math.sin(angle),
  ];
  return {
    id: listing.id,
    source: listing.sourceId,
    address: listing.address,
    status: listing.status === "hardfail" ? "FAILED_HARD_RULE"
      : listing.status === "duplicate" ? "DUPLICATE"
      : listing.status === "candidate" ? "CANDIDATE"
      : listing.status === "geocoded" || listing.status === "scored" || listing.status === "watching" ? "GEOCODED"
      : listing.status === "parsed" ? "PARSED"
      : "RAW",
    issue: listing.hardRuleFailures.join("; ") || "",
    rent: listing.rentPerMonth > 0 ? `NT$${listing.rentPerMonth.toLocaleString()}` : "NT$ *** / 月",
    area: `${listing.areaPing} ping`,
    geocode: `${listing.geocodeConfidence.toFixed(2)} / operator`,
    duplicate: listing.duplicateOfId ?? "",
    heatZoneId: listing.heatZoneId,
    coordinates,
    updatedAt: "",
    action: listing.candidateId ? "候選點已建立" : "待處理",
  };
}

/**
 * Convert an operator Candidate to the MapCandidateSite type expected by HeatZoneMap.
 */
function operatorCandidateToMapSite(
  candidate: Candidate,
  heatZones: OperatorHeatZone[],
  index: number,
): MapCandidateSite {
  const zone = heatZones.find((z) => z.id === candidate.heatZoneId);
  const [lng, lat] = zone?.centroid ?? [121.48, 25.0];
  const offset = 0.005;
  const angle = (index * 97.3 * Math.PI) / 180;
  const coordinates: [number, number] = [
    lng + offset * Math.cos(angle),
    lat + offset * Math.sin(angle),
  ];
  const isReady = candidate.status === "ready" || candidate.status === "pendingreview" || candidate.status === "approved";
  return {
    id: candidate.id,
    address: candidate.address,
    status: candidate.status === "approved" ? "approved"
      : candidate.status === "rejected" ? "rejected"
      : candidate.status === "scoring" || candidate.status === "pendingreview" ? "scored"
      : candidate.status === "wait" || candidate.status === "ready" ? "screened"
      : "new",
    heatZoneId: candidate.heatZoneId,
    coordinates,
    heatZoneScore: candidate.score,
    rentArea: "",
    geocode: "",
    feasibility: candidate.missingData.length ? candidate.missingData.join("; ") : "OK",
    listingSource: candidate.listingId ?? "",
    siteScore: `${candidate.score} / ${candidate.recommendation}`,
    readiness: isReady ? "ready" : "blocked",
    disabledReason: candidate.missingData.length ? candidate.missingData.join("; ") : undefined,
  };
}
