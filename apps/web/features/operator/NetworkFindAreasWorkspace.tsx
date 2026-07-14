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
import { DEFAULT_OPERATOR_ROLE_ID, type OperatorRoleId } from "./navigation";
import styles from "./networkFindAreas.module.css";
import type { Candidate, Listing, ListingSource, OperatorHeatZone, RebalanceStore, SiteReview } from "./types";
import { ListingRadarPanel } from "./network/ListingRadarPanel";
import { CandidatePanel } from "./network/CandidatePanel";
import { SiteScorePanel } from "./network/SiteScorePanel";
import { ComparePanel } from "./network/ComparePanel";
import { ReviewPanel } from "./network/ReviewPanel";
import { NetworkShell } from "./network/NetworkShell";
import { RebalancePanel } from "./network/RebalancePanel";
import type { ExpansionStep } from "./network/ExpansionStepper";
import type { NetworkScoringSnapshot } from "./network/networkScoringTypes";
import type {
  NetworkReviewsSnapshot,
  ReviewDecisionAction,
  ReviewDecisionForm,
} from "./network/networkReviewTypes";
import {
  buildNetworkFindAreasViewModel,
  type ListingRadarRow,
  type NetworkFindAreasLens,
  type NetworkFindAreasMapPoint,
  type NetworkFindAreasViewModel,
  type NetworkFindAreasZoneViewModel,
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
  /**
   * Active operator console role. Binds the Network Review read/decide security
   * headers, the decision actor, and whether the review decision bar is shown
   * (canDecide). Defaults to the console default role.
   */
  activeRoleId?: OperatorRoleId;
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

type NetworkRebalanceSnapshot = {
  source?: "api" | "fixture";
  stores?: RebalanceStore[];
  selectedStoreId?: string;
  metadata?: {
    canonicalPackage?: string;
    screenLabels?: string[];
    avm?: Record<string, unknown>;
    netPlan?: Record<string, unknown>;
  };
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

// Network Review decision authority is bound to the ACTIVE operator console
// role, not hardcoded. Deciding a review requires sitescore APPROVE, which the
// Site Reviewer backend role holds but Expansion (expansion_user) does not.
//
// - An authorized reviewer console role (see NETWORK_REVIEW_DECIDER_ROLE_IDS)
//   reads and decides as the Site Reviewer backend identity (sitescore
//   VIEW+APPROVE) and sees the GO / WAIT / 退回 / 駁回 decision bar.
// - Expansion (and any other network-capable role) reads as expansion_user
//   (sitescore VIEW) and can prepare/submit, but the decision bar is hidden
//   (canDecide=false). If a decide POST is still attempted it carries the
//   role's own non-approving identity, so the API fails closed with 403 —
//   defense in depth behind the hidden bar.
const SITE_REVIEWER_REVIEW_HEADERS = {
  "X-Operator-Role": "site-reviewer",
  "X-Roles": "site_reviewer",
  "X-Subject-Id": "operator-site-reviewer",
  "X-Tenant-Id": "tenant-a",
};

const EXPANSION_REVIEW_HEADERS = {
  "X-Operator-Role": "expansion-manager",
  "X-Roles": "expansion_user",
  "X-Subject-Id": "operator-expansion-manager",
  "X-Tenant-Id": "tenant-a",
};

const SITE_REVIEWER_ACTOR = {
  actorName: "陳映辰",
  actorRoleId: "siteReviewer",
};

const EXPANSION_ACTOR = {
  actorName: "王若寧",
  actorRoleId: "expansionManager",
};

// Console roles authorized to decide a Network site review. Only the operations
// lead carries an approval mandate on this surface; Expansion prepares/submits
// but cannot decide (ODP-OC-R4-007 acceptance).
const NETWORK_REVIEW_DECIDER_ROLE_IDS: ReadonlySet<OperatorRoleId> = new Set(["ops-lead"]);

type NetworkReviewIdentity = {
  canDecide: boolean;
  readHeaders: Record<string, string>;
  decideHeaders: Record<string, string>;
  actor: { actorName: string; actorRoleId: string };
};

function resolveNetworkReviewIdentity(roleId: OperatorRoleId): NetworkReviewIdentity {
  if (NETWORK_REVIEW_DECIDER_ROLE_IDS.has(roleId)) {
    return {
      canDecide: true,
      readHeaders: SITE_REVIEWER_REVIEW_HEADERS,
      decideHeaders: SITE_REVIEWER_REVIEW_HEADERS,
      actor: SITE_REVIEWER_ACTOR,
    };
  }
  return {
    canDecide: false,
    readHeaders: EXPANSION_REVIEW_HEADERS,
    decideHeaders: EXPANSION_REVIEW_HEADERS,
    actor: EXPANSION_ACTOR,
  };
}

async function fetchNetworkReviewsSnapshot(
  readHeaders: Record<string, string>,
): Promise<NetworkReviewsSnapshot | null> {
  try {
    const response = await fetch(`/api/v1/operator/network-reviews`, {
      cache: "no-store",
      headers: {
        ...readHeaders,
        "X-Correlation-Id": "corr-r4-007-reviews-read",
      },
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as NetworkReviewsSnapshot;
  } catch {
    return null;
  }
}

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

async function fetchNetworkScoringSnapshot(): Promise<NetworkScoringSnapshot | null> {
  try {
    const response = await fetch(`/api/v1/operator/network-scoring`, {
      cache: "no-store",
      headers: {
        ...NETWORK_OPERATOR_HEADERS,
        "X-Correlation-Id": "corr-r4-006-scoring-read",
      },
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as NetworkScoringSnapshot;
  } catch {
    return null;
  }
}

async function fetchNetworkRebalanceSnapshot(): Promise<NetworkRebalanceSnapshot | null> {
  try {
    const response = await fetch("/api/v1/operator/network-rebalance", {
      cache: "no-store",
      headers: {
        ...NETWORK_OPERATOR_HEADERS,
        "X-Correlation-Id": "corr-r4-008-rebalance-read",
      },
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as NetworkRebalanceSnapshot;
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
  activeRoleId = DEFAULT_OPERATOR_ROLE_ID,
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
  const reviewIdentity = useMemo(() => resolveNetworkReviewIdentity(activeRoleId), [activeRoleId]);
  const [localSelectedId, setLocalSelectedId] = useState(selectedHeatZoneId ?? "HZ-01");
  const [localLens, setLocalLens] = useState<NetworkFindAreasLens>(activeLens ?? "demand");
  const [localTrackedIds, setLocalTrackedIds] = useState(() => new Set(trackedHeatZoneIds ?? ["HZ-01"]));
  const [activeTab, setActiveTab] = useState(0);
  const [networkSnapshot, setNetworkSnapshot] = useState<NetworkListingsSnapshot | null>(null);
  const [networkApiError, setNetworkApiError] = useState<string | null>(null);
  const [busyListingId, setBusyListingId] = useState<string | null>(null);
  const [scoringSnapshot, setScoringSnapshot] = useState<NetworkScoringSnapshot | null>(null);
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);
  const [rebalanceSnapshot, setRebalanceSnapshot] = useState<NetworkRebalanceSnapshot | null>(null);
  const [rebalanceApiError, setRebalanceApiError] = useState<string | null>(null);
  const [busyRebalanceAction, setBusyRebalanceAction] = useState<string | null>(null);
  const [reviewsSnapshot, setReviewsSnapshot] = useState<NetworkReviewsSnapshot | null>(null);
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

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
  const rebalanceStoresEffective = rebalanceSnapshot?.stores?.length ? rebalanceSnapshot.stores : rebalanceStores;

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

  useEffect(() => {
    let cancelled = false;
    async function loadScoring() {
      const snapshot = await fetchNetworkScoringSnapshot();
      if (!cancelled && snapshot) {
        setScoringSnapshot(snapshot);
      }
    }
    loadScoring();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadRebalance() {
      const snapshot = await fetchNetworkRebalanceSnapshot();
      if (!cancelled && snapshot) {
        setRebalanceSnapshot(snapshot);
        setRebalanceApiError(null);
      } else if (!cancelled && !snapshot) {
        setRebalanceApiError("network-rebalance API unavailable; using fixtures");
      }
    }
    loadRebalance();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadReviews() {
      const snapshot = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders);
      if (!cancelled && snapshot) {
        setReviewsSnapshot(snapshot);
      }
    }
    loadReviews();
    return () => {
      cancelled = true;
    };
  }, [reviewIdentity]);

  // Decide a review as the active operator role's review identity. The decision
  // syncs Candidate + Review + Approval + Decision + Audit atomically
  // server-side; on success we reload the queue. Returns false so the dialog
  // stays open when the server rejects the decision (policy 422 / role 403 /
  // conflict 409). A non-deciding role reaching this path carries its own
  // non-approving headers, so the API fails closed with 403.
  async function submitReviewDecision(
    reviewId: string,
    action: ReviewDecisionAction,
    form: ReviewDecisionForm,
  ): Promise<boolean> {
    setReviewSubmitting(true);
    setReviewError(null);
    try {
      const requiredData = form.requiredData
        .split(/[、,]/)
        .map((value) => value.trim())
        .filter(Boolean);
      const response = await fetch(`/api/v1/operator/network-reviews/${reviewId}/decide`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `r4-007-decide-${reviewId}-${action}`,
          "X-Correlation-Id": `corr-r4-007-decide-${reviewId}`,
          ...reviewIdentity.decideHeaders,
        },
        body: JSON.stringify({
          ...reviewIdentity.actor,
          decision: action,
          reason: form.reason,
          conditions: form.conditions,
          requiredData,
          overrideAck: form.overrideAck,
        }),
      });
      if (!response.ok) {
        setReviewError(`review decision failed (${response.status})`);
        return false;
      }
      const snapshot = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders);
      if (snapshot) {
        setReviewsSnapshot(snapshot);
      }
      return true;
    } catch {
      setReviewError("review decision failed");
      return false;
    } finally {
      setReviewSubmitting(false);
    }
  }

  async function reloadScoringSnapshot() {
    const snapshot = await fetchNetworkScoringSnapshot();
    if (snapshot) {
      setScoringSnapshot(snapshot);
    }
  }

  async function postScoringAction(
    path: string,
    body: Record<string, unknown>,
    busyId: string | null,
    idempotencyKey?: string,
  ) {
    setBusyCandidateId(busyId);
    try {
      const response = await fetch(`/api/v1/operator/network-scoring/${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-Id": `corr-r4-006-${path.replace(/\//g, "-")}`,
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
          ...NETWORK_OPERATOR_HEADERS,
        },
        body: JSON.stringify({ ...NETWORK_ACTOR, ...body }),
      });
      if (!response.ok) {
        setNetworkApiError(`network-scoring ${path} failed (${response.status})`);
        return false;
      }
      await reloadScoringSnapshot();
      return true;
    } catch {
      setNetworkApiError(`network-scoring ${path} failed`);
      return false;
    } finally {
      setBusyCandidateId(null);
    }
  }

  async function runSiteScore(candidateId: string) {
    await postScoringAction(
      `candidates/${candidateId}/score`,
      {},
      candidateId,
      `r4-006-score-${candidateId}`,
    );
  }

  async function scoreAllCandidates() {
    await postScoringAction("score", {}, "batch", "r4-006-score-batch");
  }

  async function toggleCompareCandidate(candidateId: string) {
    const current = scoringSnapshot?.compareSet ?? [];
    const next = current.includes(candidateId)
      ? current.filter((id) => id !== candidateId)
      : [...current, candidateId];
    await postScoringAction("compare", { candidateIds: next }, candidateId, `r4-006-compare-${candidateId}`);
  }

  async function reloadRebalanceSnapshot() {
    const snapshot = await fetchNetworkRebalanceSnapshot();
    if (snapshot) {
      setRebalanceSnapshot(snapshot);
      setRebalanceApiError(null);
    }
  }

  async function postRebalanceAction(
    storeId: string,
    action: "request-avm" | "complete-avm" | "solve-netplan" | "submit-review",
  ) {
    const endpoint =
      action === "request-avm"
        ? `/api/v1/operator/network-rebalance/stores/${storeId}/avm/request`
        : action === "complete-avm"
          ? `/api/v1/operator/network-rebalance/stores/${storeId}/avm/complete`
          : action === "solve-netplan"
            ? `/api/v1/operator/network-rebalance/stores/${storeId}/netplan/solve`
            : `/api/v1/operator/network-rebalance/stores/${storeId}/submit-review`;
    const busyKey = `${storeId}:${action}`;
    setBusyRebalanceAction(busyKey);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `r4-008-${action}-${storeId}`,
          "X-Correlation-Id": `corr-r4-008-${action}-${storeId}`,
          ...NETWORK_OPERATOR_HEADERS,
        },
        body: JSON.stringify({
          ...NETWORK_ACTOR,
          reason: "Move scenario selected for Govern approval; relocation remains unexecuted.",
        }),
      });
      if (!response.ok) {
        setRebalanceApiError(`network-rebalance ${action} failed (${response.status})`);
        return;
      }
      await reloadRebalanceSnapshot();
    } catch {
      setRebalanceApiError(`network-rebalance ${action} failed`);
    } finally {
      setBusyRebalanceAction(null);
    }
  }

  async function selectRebalanceScenario(storeId: string, scenarioId: string) {
    const busyKey = `${storeId}:select-scenario:${scenarioId}`;
    setBusyRebalanceAction(busyKey);
    try {
      const response = await fetch(`/api/v1/operator/network-rebalance/stores/${storeId}/scenarios/${scenarioId}/select`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `r4-008-select-${storeId}-${scenarioId}`,
          "X-Correlation-Id": `corr-r4-008-select-${storeId}-${scenarioId}`,
          ...NETWORK_OPERATOR_HEADERS,
        },
        body: JSON.stringify(NETWORK_ACTOR),
      });
      if (!response.ok) {
        setRebalanceApiError(`network-rebalance select failed (${response.status})`);
        return;
      }
      await reloadRebalanceSnapshot();
    } catch {
      setRebalanceApiError("network-rebalance select failed");
    } finally {
      setBusyRebalanceAction(null);
    }
  }

  const viewModel = useMemo(
    () =>
      buildNetworkFindAreasViewModel({
        activeLens: effectiveLens,
        candidates: localCandidates,
        heatZones,
        listings: listingsEffective,
        listingSources: listingSourcesEffective,
        rebalanceStores: rebalanceStoresEffective,
        selectedHeatZoneId: effectiveSelectedId,
        siteReviews: localSiteReviews,
      }),
    [localCandidates, effectiveLens, effectiveSelectedId, heatZones, listingsEffective, listingSourcesEffective, rebalanceStoresEffective, localSiteReviews],
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
      setActiveTab(1);
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

  const expansionSteps =
    networkSnapshot?.expansionSteps ??
    buildFallbackExpansionSteps(
      effectiveSelectedId,
      viewModel.candidatePipeline.some((row) => row.id === "CS-1001"),
    );
  const selectedZoneLabel = selectedZone?.label ?? heatZones.find((zone) => zone.id === effectiveSelectedId)?.label;

  return (
    <section className={styles.workspace} data-screen-label="Network 展店與店網" data-testid="network-find-areas-workspace">
      <header className={styles.header}>
        <div>
          <p className={styles.kicker}>Network</p>
          <h2>展店與店網</h2>
          <p className={styles.headerSummary}>找區域 → 掃物件 → 候選點 → SiteScore → 比較 → 審核；低效門市另走重配</p>
        </div>
        <div className={styles.headerStats} aria-label="Network Find Areas state">
          <span><strong>{viewModel.totals.heatZones}</strong> HeatZones</span>
          <span><strong>{viewModel.totals.listings}</strong> listings</span>
          <span><strong>{viewModel.totals.candidates}</strong> candidates</span>
          <span><strong>{viewModel.totals.reviews}</strong> reviews</span>
          <span><strong>{viewModel.totals.rebalances}</strong> rebalances</span>
          <span><strong>{viewModel.totals.averageConfidence}</strong> avg confidence</span>
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
          <CandidatePanel
            busyCandidateId={busyCandidateId}
            candidates={scoringSnapshot?.candidates ?? []}
            fallbackRows={viewModel.candidatePipeline}
            onScore={runSiteScore}
            onScoreAll={scoreAllCandidates}
            onToggleCompare={toggleCompareCandidate}
          />
        ) : activeTab === 3 ? (
          <SiteScorePanel
            busyCandidateId={busyCandidateId}
            candidates={scoringSnapshot?.candidates ?? []}
            fallbackRows={viewModel.siteScoreLab}
            modelVersion={scoringSnapshot?.modelVersion}
            onRescore={runSiteScore}
            scorecards={scoringSnapshot?.scorecards ?? []}
          />
        ) : activeTab === 4 ? (
          <ComparePanel compare={scoringSnapshot?.compare ?? null} fallback={viewModel.compare} />
        ) : activeTab === 5 ? (
          <ReviewPanel
            reviews={reviewsSnapshot?.reviews ?? []}
            fallbackRows={viewModel.reviewQueue}
            canDecide={reviewIdentity.canDecide}
            submitting={reviewSubmitting}
            decideError={reviewError}
            onDecide={submitReviewDecision}
          />
        ) : activeTab === 6 ? (
          <RebalancePanel
            apiError={rebalanceApiError}
            busyAction={busyRebalanceAction}
            onCompleteAvm={(storeId) => postRebalanceAction(storeId, "complete-avm")}
            onRequestAvm={(storeId) => postRebalanceAction(storeId, "request-avm")}
            onSelectScenario={selectRebalanceScenario}
            onSolveNetPlan={(storeId) => postRebalanceAction(storeId, "solve-netplan")}
            onSubmitReview={(storeId) => postRebalanceAction(storeId, "submit-review")}
            rows={viewModel.rebalanceQueue}
          />
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
    <div className={styles.tabPanel} data-screen-label="Network 找區域" data-testid="network-panel-find-areas" role="tabpanel">
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
