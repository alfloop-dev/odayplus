"use client";

import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";
import { OperatorDataUnavailableGate } from "./OperatorDataUnavailableGate";
import {
  isSeedDataSource,
  operatorFixturesAllowed,
  payloadContainsSeedData,
  type OperatorDataAvailability,
} from "./operatorDataMode";
import styles from "./networkFindAreas.module.css";
import type { Candidate, Listing, ListingSource, OperatorHeatZone, RebalanceStore, SiteReview } from "./types";
import { ListingRadarPanel } from "./network/ListingRadarPanel";
import { ListingMergeDialog, type ListingMergeForm, type ListingMergeRequest } from "./network/ListingMergeDialog";
import {
  buildListingsClient,
  listingsApi,
  missingListingsClientError,
  newMergeIdempotencyKey,
  type ListingApiError,
} from "./network/listingsClient";
import { canMergeListing } from "./network/listingPermissions";
import { CandidatePanel } from "./network/CandidatePanel";
import { SiteScorePanel } from "./network/SiteScorePanel";
import { ComparePanel } from "./network/ComparePanel";
import { ReviewPanel } from "./network/ReviewPanel";
import { NetworkShell } from "./network/NetworkShell";
import { RebalancePanel } from "./network/RebalancePanel";
import {
  buildNetworkTabHref,
  parseNetworkTabIndex,
} from "./network/networkUrlState";
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
import type { HeatZoneMapProps } from "./network/HeatZoneMap";
import {
  OPERATOR_MAP_FRESHNESS,
  operatorCandidateToMapSite,
  operatorHeatZoneToMapZone,
  operatorListingToMapListing,
} from "./network/heatZoneMapAdapters";
import { GeocoderSearchPanel } from "./network/geocoder";
import type { GeocodeAuditEvent } from "./network/geocoder";
import {
  canSearchAddress,
  canSelectGeocodeCandidate,
} from "./network/geocoder/geocoderPermissions";
import type {
  CandidateSite as MapCandidateSite,
  HeatZone as MapHeatZone,
  Listing as MapListing,
} from "./network/mapTypes";

// The map stack (deck.gl + maplibre-gl + h3-js) is by far the heaviest thing on
// the operator surface. It is only ever rendered inside the Find Areas tab, so
// it is loaded as its own chunk instead of being charged to the first load of
// every /operator and /intake request. `ssr: false` is correct here as well:
// HeatZoneMap builds the maplibre instance in an effect against a real DOM node,
// so the server render only ever produced an empty container.
const HeatZoneMap = dynamic<HeatZoneMapProps>(
  () => import("./network/HeatZoneMap").then((mod) => mod.HeatZoneMap),
  {
    ssr: false,
    loading: function HeatZoneMapLoading() {
      return (
        <div
          aria-live="polite"
          className={styles.mapLoading}
          data-testid="heat-zone-map-loading"
          role="status"
        >
          HeatZone 地圖載入中…
        </div>
      );
    },
  },
);

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
  initialTabId?: string;
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
   * workspace renders live items; local/POC may otherwise use bundled
   * HEAT_ZONE_FIXTURES with a fixture-mode indicator.
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

const EMPTY_CANDIDATES: Candidate[] = [];
const EMPTY_HEAT_ZONES: OperatorHeatZone[] = [];
const EMPTY_LISTINGS: Listing[] = [];
const EMPTY_LISTING_SOURCES: ListingSource[] = [];
const EMPTY_REBALANCE_STORES: RebalanceStore[] = [];
const EMPTY_SITE_REVIEWS: SiteReview[] = [];

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
  assistedIntakes?: any[];
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

export function resolveNetworkDataUnavailableState(
  loadStates: readonly OperatorDataAvailability[],
): Exclude<OperatorDataAvailability, "ready" | "fixture"> | null {
  if (loadStates.includes("error")) return "error";
  if (loadStates.includes("seed") || loadStates.includes("fixture")) return "seed";
  if (loadStates.includes("empty")) return "empty";
  if (loadStates.includes("loading")) return "loading";
  return null;
}

export function resolveNetworkTabGateState({
  activeTab,
  bindingLoadStates,
  fixturesAllowed,
  networkLoadState,
  rebalanceLoadState,
  reviewsLoadState,
  scoringLoadState,
}: {
  activeTab: number;
  bindingLoadStates: readonly OperatorDataAvailability[];
  fixturesAllowed: boolean;
  networkLoadState: OperatorDataAvailability;
  rebalanceLoadState: OperatorDataAvailability;
  reviewsLoadState: OperatorDataAvailability;
  scoringLoadState: OperatorDataAvailability;
}): Exclude<OperatorDataAvailability, "ready" | "fixture"> | null {
  if (fixturesAllowed || activeTab === 1) return null;
  if (activeTab === 0) {
    return resolveNetworkDataUnavailableState([
      ...bindingLoadStates,
      networkLoadState,
    ]);
  }
  if (activeTab >= 2 && activeTab <= 4) {
    return resolveNetworkDataUnavailableState([scoringLoadState]);
  }
  if (activeTab === 5) {
    return resolveNetworkDataUnavailableState([reviewsLoadState]);
  }
  if (activeTab === 6) {
    return resolveNetworkDataUnavailableState([rebalanceLoadState]);
  }
  return null;
}

export function inspectNetworkListingsSnapshot(
  snapshot: NetworkListingsSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  const requiredCollections = [
    snapshot.heatZones,
    snapshot.listingSources,
    snapshot.listings,
    snapshot.candidates,
    snapshot.siteReviews,
  ];
  const hasRequiredShape =
    snapshot.source === "api" &&
    requiredCollections.every(Array.isArray);
  const hasUsableRows =
    (snapshot.heatZones?.length ?? 0) > 0 &&
    (snapshot.listingSources?.length ?? 0) > 0 &&
    ((snapshot.listings?.length ?? 0) > 0 || (snapshot.candidates?.length ?? 0) > 0);
  return hasRequiredShape && hasUsableRows ? "ready" : "empty";
}

export function inspectNetworkScoringSnapshot(
  snapshot: NetworkScoringSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  return snapshot.source === "api" &&
    snapshot.candidates.length > 0 &&
    snapshot.scorecards.length > 0 &&
    snapshot.compare.columns.length > 0
    ? "ready"
    : "empty";
}

export function inspectNetworkRebalanceSnapshot(
  snapshot: NetworkRebalanceSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  return snapshot.source === "api" && (snapshot.stores?.length ?? 0) > 0
    ? "ready"
    : "empty";
}

export function inspectNetworkReviewsSnapshot(
  snapshot: NetworkReviewsSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  return snapshot.source === "api" && snapshot.reviews.length > 0
    ? "ready"
    : "empty";
}

const NETWORK_OPERATOR_HEADERS = operatorSecurityHeaders(
  "expansion-manager",
);

const NETWORK_ACTOR = {
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
const SITE_REVIEWER_REVIEW_HEADERS = operatorSecurityHeaders("site-reviewer");

const EXPANSION_REVIEW_HEADERS = operatorSecurityHeaders(
  "expansion-manager",
);

const SITE_REVIEWER_ACTOR = {
  actorRoleId: "siteReviewer",
};

const EXPANSION_ACTOR = {
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
  actor: { actorRoleId: string };
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

/**
 * True when the canonical intake detail owns the page, on either entry point:
 * the Operator query context (`selected=<intakeId>` plus a detail dialog) or
 * the durable `/intake/<intakeId>` route. `fix`, `decide` and `assignmentSla`
 * are included because `AssistedIntakeSection` keeps the full-page detail
 * mounted underneath those confirmation dialogs, and it must sit in the same
 * canonical position there (ADD-006 §3.1).
 */
export function isNetworkIntakeDetailRoute(
  pathname: string | null | undefined,
  searchParams: Pick<URLSearchParams, "get">,
): boolean {
  if (/^\/intake\/[^/]+$/.test(pathname ?? "")) return true;
  if (!searchParams.get("selected")) return false;
  const dialog = searchParams.get("dialog");
  return (
    dialog === "detail" ||
    dialog === "fix" ||
    dialog === "decide" ||
    dialog === "assignmentSla"
  );
}

export function NetworkFindAreasWorkspace({
  activeLens,
  activeRoleId = DEFAULT_OPERATOR_ROLE_ID,
  callbacks,
  candidates: candidatesInput,
  heatZones: heatZonesInput,
  initialTabId,
  listings: listingsInput,
  listingSources: listingSourcesInput,
  rebalanceStores: rebalanceStoresInput,
  siteReviews: siteReviewsInput,
  selectedHeatZoneId,
  trackedHeatZoneIds,
  liveHeatZones,
  liveCandidates,
}: NetworkFindAreasWorkspaceProps) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const fixturesAllowed = operatorFixturesAllowed();
  const candidatesProp = candidatesInput ?? (fixturesAllowed ? CANDIDATE_FIXTURES : EMPTY_CANDIDATES);
  const heatZonesProp = heatZonesInput ?? (fixturesAllowed ? HEAT_ZONE_FIXTURES : EMPTY_HEAT_ZONES);
  const listings = listingsInput ?? (fixturesAllowed ? LISTING_FIXTURES : EMPTY_LISTINGS);
  const listingSources = listingSourcesInput ?? (fixturesAllowed ? LISTING_SOURCE_FIXTURES : EMPTY_LISTING_SOURCES);
  const rebalanceStores = rebalanceStoresInput ?? (fixturesAllowed ? REBALANCE_STORE_FIXTURES : EMPTY_REBALANCE_STORES);
  const siteReviews = siteReviewsInput ?? (fixturesAllowed ? SITE_REVIEW_FIXTURES : EMPTY_SITE_REVIEWS);
  const reviewIdentity = useMemo(() => resolveNetworkReviewIdentity(activeRoleId), [activeRoleId]);
  const [localSelectedId, setLocalSelectedId] = useState(
    selectedHeatZoneId ?? (fixturesAllowed ? "HZ-01" : ""),
  );
  const [localLens, setLocalLens] = useState<NetworkFindAreasLens>(activeLens ?? "demand");
  const [localTrackedIds, setLocalTrackedIds] = useState(
    () => new Set(trackedHeatZoneIds ?? (fixturesAllowed ? ["HZ-01"] : [])),
  );
  const intakeDetailOpen = isNetworkIntakeDetailRoute(pathname, searchParams);
  const urlTab = searchParams.get("tab")
    ? parseNetworkTabIndex(searchParams)
    : parseNetworkTabIndex(`tab=${initialTabId ?? ""}`);
  // A Network tab click has to survive the console's initial preference
  // hydration, shell bootstrap and URL hydration. While any of those are still
  // in flight the console keeps re-publishing the server-rendered
  // `initialTabId`, and a tab selection that only existed in the pushed URL was
  // silently dropped — the Listing Radar click was lost and Find Areas stayed
  // on screen. The selection is therefore committed to workspace state at click
  // time and stays authoritative until the URL reports a different tab, at
  // which point the URL (deep link, back/forward) takes over again.
  const [tabOverride, setTabOverride] = useState<{ from: number; requested: number } | null>(null);
  const overrideApplies = tabOverride !== null && tabOverride.from === urlTab;
  const activeTab = overrideApplies ? tabOverride.requested : urlTab;

  useEffect(() => {
    setTabOverride((current) => (current !== null && current.from !== urlTab ? null : current));
  }, [urlTab]);
  const [networkSnapshot, setNetworkSnapshot] = useState<NetworkListingsSnapshot | null>(null);
  const [networkApiError, setNetworkApiError] = useState<string | null>(null);
  const [networkLoadState, setNetworkLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [busyListingId, setBusyListingId] = useState<string | null>(null);
  const [mergeRequest, setMergeRequest] = useState<ListingMergeRequest | null>(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<ListingApiError | null>(null);
  const [scoringSnapshot, setScoringSnapshot] = useState<NetworkScoringSnapshot | null>(null);
  const [scoringLoadState, setScoringLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);
  const [rebalanceSnapshot, setRebalanceSnapshot] = useState<NetworkRebalanceSnapshot | null>(null);
  const [rebalanceApiError, setRebalanceApiError] = useState<string | null>(null);
  const [rebalanceLoadState, setRebalanceLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [busyRebalanceAction, setBusyRebalanceAction] = useState<string | null>(null);
  const [reviewsSnapshot, setReviewsSnapshot] = useState<NetworkReviewsSnapshot | null>(null);
  const [reviewsLoadState, setReviewsLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const changeActiveTab = useCallback((tabIndex: number) => {
    setTabOverride({ from: urlTab, requested: tabIndex });
    const href = buildNetworkTabHref(
      pathname,
      tabIndex,
      searchParams,
      typeof window === "undefined" ? "" : window.location.hash,
    );
    router.push(href, { scroll: false });
  }, [pathname, router, searchParams, urlTab]);

  const snapshotHeatZones = networkSnapshot?.heatZones?.length
    ? networkSnapshot.heatZones
    : fixturesAllowed
      ? undefined
      : networkSnapshot?.heatZones;
  const heatZones =
    snapshotHeatZones ??
    (liveHeatZones?.source === "api" && liveHeatZones.items.length > 0
      ? liveHeatZones.items
      : heatZonesProp);
  const listingsEffective = networkSnapshot?.listings?.length
    ? networkSnapshot.listings
    : fixturesAllowed
      ? listings
      : networkSnapshot?.listings ?? [];
  const listingSourcesEffective = networkSnapshot?.listingSources?.length
    ? networkSnapshot.listingSources
    : fixturesAllowed
      ? listingSources
      : networkSnapshot?.listingSources ?? [];
  const candidates =
    networkSnapshot?.candidates ??
    (liveCandidates?.source === "api" && liveCandidates.items.length > 0
      ? liveCandidates.items
      : candidatesProp);
  const siteReviewsEffective = networkSnapshot?.siteReviews ?? siteReviews;
  const rebalanceStoresEffective = rebalanceSnapshot?.stores?.length
    ? rebalanceSnapshot.stores
    : fixturesAllowed
      ? rebalanceStores
      : rebalanceSnapshot?.stores ?? [];

  // True when every Network R4 binding is using the local/POC fixture path.
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
    if (
      selectedHeatZoneId === undefined &&
      !localSelectedId &&
      heatZones.length > 0
    ) {
      setLocalSelectedId(heatZones[0].id);
    }
  }, [heatZones, localSelectedId, selectedHeatZoneId]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!effectiveSelectedId) {
        if (!fixturesAllowed) setNetworkLoadState("loading");
        return;
      }
      const snapshot = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens);
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkListingsSnapshot(snapshot);
        setNetworkSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setNetworkApiError(null);
        setNetworkLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled && !snapshot) {
        setNetworkApiError(
          fixturesAllowed
            ? "network-listings API unavailable; using local fixtures"
            : "network-listings API unavailable",
        );
        setNetworkLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [effectiveLens, effectiveSelectedId, fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadScoring() {
      const snapshot = await fetchNetworkScoringSnapshot();
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkScoringSnapshot(snapshot);
        setScoringSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setScoringLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled) {
        setScoringLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadScoring();
    return () => {
      cancelled = true;
    };
  }, [fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadRebalance() {
      const snapshot = await fetchNetworkRebalanceSnapshot();
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkRebalanceSnapshot(snapshot);
        setRebalanceSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setRebalanceApiError(null);
        setRebalanceLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled && !snapshot) {
        setRebalanceApiError(
          fixturesAllowed
            ? "network-rebalance API unavailable; using local fixtures"
            : "network-rebalance API unavailable",
        );
        setRebalanceLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadRebalance();
    return () => {
      cancelled = true;
    };
  }, [fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadReviews() {
      const snapshot = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders);
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkReviewsSnapshot(snapshot);
        setReviewsSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setReviewsLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled) {
        setReviewsLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadReviews();
    return () => {
      cancelled = true;
    };
  }, [fixturesAllowed, reviewIdentity]);

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
      changeActiveTab(1);
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
      changeActiveTab(2);
    }
  }

  /**
   * Merge is a high-impact, irreversible write, so the button only OPENS the
   * confirmation surface — it never writes. ListingMergeDialog collects the
   * operator's own reason and their acknowledgement of the exact risk summary
   * it rendered; submitMergeListing then performs the write. Cancelling, or
   * closing without acknowledging, writes nothing.
   */
  function mergeListing(sourceListingId: string, targetListingId: string) {
    setMergeError(null);
    setMergeRequest({
      sourceListingId,
      targetListingId,
      sourceLabel: listingsEffective.find((item) => item.id === sourceListingId)?.address,
      targetLabel: listingsEffective.find((item) => item.id === targetListingId)?.address,
      // Minted once per initiated merge and carried across every retry, so a
      // retry after a lost response replays the original result server-side
      // instead of merging a second time.
      idempotencyKey: newMergeIdempotencyKey(sourceListingId, targetListingId),
    });
  }

  /**
   * The write goes through the typed OpenAPI client, which carries a
   * correlation ID and a per-attempt idempotency key and surfaces structured
   * errors. `reason` and `riskSummary` come from the dialog verbatim — the
   * audit record must store what the operator actually wrote and read.
   */
  async function submitMergeListing(form: ListingMergeForm) {
    if (!mergeRequest) return;
    const client = buildListingsClient(activeRoleId);
    if (!client) {
      setMergeError(missingListingsClientError());
      return;
    }

    setMergeBusy(true);
    setBusyListingId(mergeRequest.sourceListingId);
    try {
      const result = await listingsApi.merge(client, mergeRequest.sourceListingId, {
        targetListingId: mergeRequest.targetListingId,
        actorRoleId: activeRoleId,
        reason: form.reason,
        riskSummary: form.riskSummary,
        riskAcknowledged: form.riskAcknowledged,
        // Same key on every retry of this request — see mergeListing().
        idempotencyKey: mergeRequest.idempotencyKey,
      });
      if (!result.ok) {
        // The dialog stays open so the operator keeps their reason and can read
        // the error code / correlation ID.
        setMergeError(result.error);
        return;
      }
      setMergeRequest(null);
      setMergeError(null);
      await reloadNetworkSnapshot();
    } finally {
      setMergeBusy(false);
      setBusyListingId(null);
    }
  }

  async function archiveListing(listingId: string) {
    await postNetworkListingAction(listingId, "archive", {
      reason: "Hard-rule archive: area and floor exceed ODAY_G2 intake policy.",
    });
  }

  const expansionSteps =
    networkSnapshot?.expansionSteps ??
    (fixturesAllowed
      ? buildFallbackExpansionSteps(
          effectiveSelectedId,
          viewModel.candidatePipeline.some((row) => row.id === "CS-1001"),
        )
      : []);
  const selectedZoneLabel = selectedZone?.label ?? heatZones.find((zone) => zone.id === effectiveSelectedId)?.label;

  const bindingLoadStates: OperatorDataAvailability[] = [liveHeatZones, liveCandidates].map(
    (binding) => {
      if (!binding) return "loading";
      if (binding.state === "ready" && binding.source === "api") return "ready";
      if (binding.state === "empty") return "empty";
      if (binding.state === "error" || binding.state === "unconfigured") return "error";
      return "seed";
    },
  );
  const activeTabGateState = resolveNetworkTabGateState({
    activeTab,
    bindingLoadStates,
    fixturesAllowed,
    networkLoadState,
    rebalanceLoadState,
    reviewsLoadState,
    scoringLoadState,
  });
  const activeTabGateDetail =
    activeTab === 0
      ? networkApiError
      : activeTab === 6
        ? rebalanceApiError
        : null;

  const listingRadarPanel = (
    <ListingRadarPanel
      activeRoleId={activeRoleId}
      busyListingId={busyListingId}
      intakeDetailOpen={intakeDetailOpen}
      listings={listingsEffective}
      onArchive={archiveListing}
      onConvert={convertListing}
      onMerge={mergeListing}
      rows={viewModel.listingRadar}
      selectedHeatZoneId={effectiveSelectedId}
      selectedZoneLabel={selectedZoneLabel}
      sources={listingSourcesEffective}
    />
  );

  const listingMergeDialog = mergeRequest ? (
    <ListingMergeDialog
      busy={mergeBusy}
      error={mergeError}
      onClose={() => {
        setMergeRequest(null);
        setMergeError(null);
      }}
      onSubmit={submitMergeListing}
      request={mergeRequest}
    />
  ) : null;

  /*
   * Package 10 renders the intake detail as the first workspace surface under
   * the global Operator topbar/status banner: no Network heading, KPI strip,
   * expansion stepper, tab strip, compliance strip or Network status row may
   * precede it. The canonical source cards and Listing Radar stay below the
   * detail, inside the same single production graph
   * (NetworkFindAreasWorkspace -> ListingRadarPanel -> AssistedIntakeSection),
   * so no second intake UI, modal, drawer or fixed overlay is introduced. The
   * unrelated Network tab data gate is bypassed here for the same reason the
   * durable /intake/<id> route bypasses the shell bootstrap gate: the detail
   * owns its own authoritative API binding (ADD-006 §3.1).
   */
  if (intakeDetailOpen) {
    return (
      <section
        className={styles.workspace}
        data-intake-detail-open="true"
        data-screen-label="Network 展店與店網"
        data-testid="network-find-areas-workspace"
      >
        {listingRadarPanel}
        {listingMergeDialog}
      </section>
    );
  }

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

      <NetworkShell activeTab={activeTab} onTabChange={changeActiveTab} steps={expansionSteps} tabs={networkTabs}>
        {activeTabGateState ? (
          <OperatorDataUnavailableGate
            detail={activeTabGateDetail}
            onRetry={() => window.location.reload()}
            status={activeTabGateState}
          />
        ) : activeTab === 1 ? (
          listingRadarPanel
        ) : activeTab === 2 ? (
          <CandidatePanel
            busyCandidateId={busyCandidateId}
            candidates={scoringSnapshot?.candidates ?? []}
            fallbackRows={fixturesAllowed ? viewModel.candidatePipeline : []}
            onScore={runSiteScore}
            onScoreAll={scoreAllCandidates}
            onToggleCompare={toggleCompareCandidate}
          />
        ) : activeTab === 3 ? (
          <SiteScorePanel
            busyCandidateId={busyCandidateId}
            candidates={scoringSnapshot?.candidates ?? []}
            fallbackRows={fixturesAllowed ? viewModel.siteScoreLab : []}
            modelVersion={scoringSnapshot?.modelVersion}
            onRescore={runSiteScore}
            scorecards={scoringSnapshot?.scorecards ?? []}
          />
        ) : activeTab === 4 ? (
          <ComparePanel
            compare={scoringSnapshot?.compare ?? null}
            fallback={fixturesAllowed ? viewModel.compare : { columns: [], metrics: [] }}
          />
        ) : activeTab === 5 ? (
          <ReviewPanel
            reviews={reviewsSnapshot?.reviews ?? []}
            fallbackRows={fixturesAllowed ? viewModel.reviewQueue : []}
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
            activeRoleId={activeRoleId}
            fixturesAllowed={fixturesAllowed}
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

      {listingMergeDialog}
    </section>
  );
}

type FindAreasPanelProps = {
  activeRoleId: OperatorRoleId;
  fixturesAllowed: boolean;
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
  activeRoleId,
  candidates,
  effectiveLens,
  fixturesAllowed,
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
  const mapZones = useMemo<MapHeatZone[]>(
    () => heatZones.map(operatorHeatZoneToMapZone),
    [heatZones],
  );
  const mapListings = useMemo<MapListing[]>(
    () => listings.map((l, i) => operatorListingToMapListing(l, heatZones, i)),
    [listings, heatZones],
  );
  const mapCandidates = useMemo<MapCandidateSite[]>(
    () => candidates.map((c, i) => operatorCandidateToMapSite(c, heatZones, i)),
    [candidates, heatZones],
  );
  const selectedMapZoneId = selectedZone?.id ?? (heatZones[0]?.id ?? "");
  // The accepted geocode is held here as a receipt rather than written through:
  // the production geocoder endpoint is not yet wired (see
  // docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md
  // §5, UX-SCR-EXP-001), so this surface shows what WOULD be persisted, with
  // its audit fields, instead of silently dropping the operator's decision.
  const [geocodeReceipt, setGeocodeReceipt] = useState<GeocodeAuditEvent | null>(null);
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
          <HeatZoneMap
            dataSource={fixturesAllowed ? "fixture" : "api"}
            zones={mapZones}
            listings={mapListings}
            candidates={mapCandidates}
            productionMode={!fixturesAllowed}
            selectedZoneId={selectedMapZoneId}
            freshness={OPERATOR_MAP_FRESHNESS}
          />
        </div>

        <aside className={styles.trayPanel} aria-label="Recommended find area tray">
          {/*
            Address search sits in the tray rather than in .mapPanel: that panel
            is a fixed-height grid area with overflow:hidden on this screen, so
            anything stacked above the canvas is clipped.
          */}
          <GeocoderSearchPanel
            actorRoleId={activeRoleId}
            canSearch={canSearchAddress(activeRoleId)}
            canSelect={canSelectGeocodeCandidate(activeRoleId)}
            onAudit={setGeocodeReceipt}
            onSelect={() => undefined}
          />
          {geocodeReceipt ? (
            <div className={styles.geocodeReceipt} data-testid="find-areas-geocode-receipt" role="status">
              <strong>
                {geocodeReceipt.action === "low_confidence_override"
                  ? "已採用（人工覆核）"
                  : geocodeReceipt.action === "candidate_selected"
                    ? "已採用"
                    : "已記錄為無法定位"}
              </strong>
              <span>{geocodeReceipt.addressRaw}</span>
              {geocodeReceipt.selected ? (
                <span>
                  {geocodeReceipt.selected.latitude.toFixed(6)}, {geocodeReceipt.selected.longitude.toFixed(6)} ·
                  精度 {geocodeReceipt.selected.precision || "未提供"} · 來源 {geocodeReceipt.selected.provider || "未提供"}
                </span>
              ) : (
                <span>未取得座標；後續流程不會有推估位置。</span>
              )}
              {geocodeReceipt.flags.length > 0 ? <span>品質旗標 {geocodeReceipt.flags.join("、")}</span> : null}
              {geocodeReceipt.reviewReason ? <span>覆核理由 {geocodeReceipt.reviewReason}</span> : null}
              <span>
                操作者 {geocodeReceipt.actorRoleId} · {geocodeReceipt.occurredAt}
                {geocodeReceipt.correlationId ? ` · correlation_id ${geocodeReceipt.correlationId}` : ""}
              </span>
            </div>
          ) : null}
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
