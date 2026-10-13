"use client";

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
  OperatorLoadFailureError,
  operatorFixturesAllowed,
  operatorLoadFailureFromError,
  operatorLoadFailureFromResponse,
  payloadContainsSeedData,
  unreadCountLabel,
  type OperatorDataAvailability,
  type OperatorLoadFailure,
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
import { ReviewPanel, resolveEffectiveReviews } from "./network/ReviewPanel";
import { NetworkShell } from "./network/NetworkShell";
import { FindAreasPanel } from "./network/FindAreasPanel";
import { canPerform as canPerformIntake } from "./network/intake/intakePermissions";
import type { RebalancePanelProps } from "./network/RebalancePanel";
import type {
  HeatZoneMergeSplitPanelProps,
  HeatZoneProposal,
  ProposalPreviewData,
} from "./network/HeatZoneMergeSplitPanel";
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
  type NetworkFindAreasLens,
  type NetworkFindAreasZoneViewModel,
} from "./networkFindAreasViewModel";

// Rebalance is the tab-6 panel: it carries the AVM valuation card, the NetPlan
// scenario table and the review submission flow, and none of it is on screen
// until the operator opens that tab. It is deferred for the same reason the
// merge/split panel below is, so /operator and /intake do not pay for it on
// first load. `ssr: false` matches the other deferred panels -- the panel is
// driven entirely by client state and callbacks from this workspace.
const RebalancePanel = dynamic<RebalancePanelProps>(
  () => import("./network/RebalancePanel").then((mod) => mod.RebalancePanel),
  {
    ssr: false,
    loading: function RebalancePanelLoading() {
      return (
        <div
          aria-live="polite"
          className={styles.mapLoading}
          data-testid="rebalance-panel-loading"
          role="status"
        >
          低效重配面板載入中…
        </div>
      );
    },
  },
);

const HeatZoneMergeSplitPanel = dynamic<HeatZoneMergeSplitPanelProps>(
  () =>
    import("./network/HeatZoneMergeSplitPanel").then(
      (mod) => mod.HeatZoneMergeSplitPanel,
    ),
  {
    ssr: false,
    loading: function HeatZoneMergeSplitPanelLoading() {
      return (
        <div
          aria-live="polite"
          className={styles.mapLoading}
          data-testid="heat-zone-merge-split-loading"
          role="status"
        >
          熱區重組提案載入中…
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
  "空間治理 / Merge & Split",
] as const;

const EMPTY_CANDIDATES: Candidate[] = [];
const CANDIDATE_ZONE_PARAM = "zone";
const EMPTY_HEAT_ZONES: OperatorHeatZone[] = [];
const EMPTY_LISTINGS: Listing[] = [];
const EMPTY_LISTING_SOURCES: ListingSource[] = [];
const EMPTY_REBALANCE_STORES: RebalanceStore[] = [];
const EMPTY_SITE_REVIEWS: SiteReview[] = [];

type NetworkListingDetail = Listing & {
  archivedReason?: string;
  convertedAt?: string;
  firstSeenAt?: string;
  fitScore?: number | null;
  floor?: string;
  frontageMeters?: number | null;
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
  findAreasLoadState,
  fixturesAllowed,
  proposalsLoadState,
  rebalanceLoadState,
  reviewsLoadState,
  scoringLoadState,
}: {
  activeTab: number;
  /** The scoped snapshot state as seen by Find Areas (HeatZones required). */
  findAreasLoadState: OperatorDataAvailability;
  fixturesAllowed: boolean;
  proposalsLoadState?: OperatorDataAvailability;
  rebalanceLoadState: OperatorDataAvailability;
  reviewsLoadState: OperatorDataAvailability;
  scoringLoadState: OperatorDataAvailability;
}): Exclude<OperatorDataAvailability, "ready" | "fixture"> | null {
  if (fixturesAllowed || activeTab === 1) return null;
  // Find Areas renders the scoped operator snapshot, which supersedes the
  // legacy heatzone/candidate domain bindings. Those domain reads stay denied
  // for operator-only grants, so they must not gate a ready scoped snapshot.
  if (activeTab === 0) {
    return resolveNetworkDataUnavailableState([findAreasLoadState]);
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
  if (activeTab === 7) {
    return proposalsLoadState ? resolveNetworkDataUnavailableState([proposalsLoadState]) : null;
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
  // A scoped read may legitimately withhold whole-zone HeatZone aggregates
  // while still authorizing individual listings/candidates. The snapshot is
  // usable when it carries any authorized record; each tab then decides
  // whether the records it renders are present (see resolveNetworkTabGateState).
  const hasUsableRows =
    (snapshot.heatZones?.length ?? 0) > 0 ||
    (snapshot.listings?.length ?? 0) > 0 ||
    (snapshot.candidates?.length ?? 0) > 0;
  return hasRequiredShape && hasUsableRows ? "ready" : "empty";
}

/** Find Areas renders HeatZones; a ready snapshot without any is empty for that tab. */
export function resolveNetworkFindAreasLoadState(
  networkLoadState: OperatorDataAvailability,
  heatZoneCount: number,
): OperatorDataAvailability {
  return networkLoadState === "ready" && heatZoneCount === 0 ? "empty" : networkLoadState;
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

const NETWORK_ACTOR = {
  actorRoleId: "expansionManager",
};

// Network Review decision authority is bound to the ACTIVE operator console
// role, not hardcoded. Deciding a review requires sitescore APPROVE, which the
// Site Reviewer backend role holds but Expansion (expansion_user) does not.
//
// UI decision visibility is only presentation: every request retains the
// active persona and the API enforces sitescore APPROVE against durable grants.
// Read-only administrators/auditors must never be projected as expansion users.
// Never substitute a reviewer/expansion persona for the active console role.
// The API resolves the durable identity and enforces its real grants.

// The canonical expansion-manager persona requires site_reviewer/executive
// grants; ops-lead alone is operations_manager, not a SiteScore approver.
// This only controls presentation. The API verifies APPROVE and derives the
// decision actor from its authenticated principal, never from this persona.
const NETWORK_REVIEW_DECIDER_ROLE_IDS: ReadonlySet<OperatorRoleId> = new Set(["expansion-manager"]);

type NetworkReviewIdentity = {
  canDecide: boolean;
  readHeaders: Record<string, string>;
  decideHeaders: Record<string, string>;
  actor: { actorRoleId: string };
};

function resolveNetworkReviewIdentity(roleId: OperatorRoleId): NetworkReviewIdentity {
  const headers = operatorSecurityHeaders(roleId);
  return {
    canDecide: NETWORK_REVIEW_DECIDER_ROLE_IDS.has(roleId),
    readHeaders: headers,
    decideHeaders: headers,
    actor: { actorRoleId: roleId },
  };
}

/**
 * A Network read either produced a snapshot or a typed failure that keeps the
 * HTTP status, canonical error code and correlation ID. A refused read is never
 * collapsed into "no snapshot", which used to surface a received 403 as a
 * transport failure and its collection as an authoritative zero.
 */
type NetworkRead<T> = { ok: true; snapshot: T } | { ok: false; failure: OperatorLoadFailure };

async function readNetworkSnapshot<T>(
  label: string,
  path: string,
  headers: Record<string, string>,
  correlationId: string,
): Promise<NetworkRead<T>> {
  try {
    const response = await fetch(path, {
      cache: "no-store",
      headers: {
        ...headers,
        "X-Correlation-Id": correlationId,
      },
    });
    if (!response.ok) {
      return { ok: false, failure: await operatorLoadFailureFromResponse(response, label, correlationId) };
    }
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null) {
      return {
        ok: false,
        failure: {
          correlationId: response.headers?.get?.("x-correlation-id") ?? correlationId,
          httpStatus: response.status,
          kind: "unknown",
          occurredAt: new Date().toISOString(),
          technicalDetail: `${label} returned ${response.status} without a snapshot body`,
        },
      };
    }
    return { ok: true, snapshot: body as T };
  } catch (error) {
    return { ok: false, failure: operatorLoadFailureFromError(error, label, correlationId) };
  }
}

function fetchNetworkReviewsSnapshot(
  readHeaders: Record<string, string>,
): Promise<NetworkRead<NetworkReviewsSnapshot>> {
  return readNetworkSnapshot(
    "network-reviews",
    "/api/v1/operator/network-reviews",
    readHeaders,
    "corr-r4-007-reviews-read",
  );
}

// The scoped operator snapshot is the authoritative Network read. Without a
// selection it is requested unfiltered, and the API projects it to the verified
// scope and names the first visible zone; legacy /heatzones grants are never
// needed to choose one.
function fetchNetworkSnapshot(
  selectedHeatZoneId: string | null,
  lens: NetworkFindAreasLens,
  roleId: OperatorRoleId,
): Promise<NetworkRead<NetworkListingsSnapshot>> {
  const params = new URLSearchParams({ lens });
  if (selectedHeatZoneId) params.set("selectedHeatZoneId", selectedHeatZoneId);
  return readNetworkSnapshot(
    "network-listings",
    `/api/v1/operator/network-listings?${params.toString()}`,
    operatorSecurityHeaders(roleId),
    `corr-r4-005-read-${selectedHeatZoneId ?? "initial"}-${lens}`,
  );
}

function fetchNetworkScoringSnapshot(roleId: OperatorRoleId): Promise<NetworkRead<NetworkScoringSnapshot>> {
  return readNetworkSnapshot(
    "network-scoring",
    "/api/v1/operator/network-scoring",
    operatorSecurityHeaders(roleId),
    "corr-r4-006-scoring-read",
  );
}

function fetchNetworkRebalanceSnapshot(roleId: OperatorRoleId): Promise<NetworkRead<NetworkRebalanceSnapshot>> {
  return readNetworkSnapshot(
    "network-rebalance",
    "/api/v1/operator/network-rebalance",
    operatorSecurityHeaders(roleId),
    "corr-r4-008-rebalance-read",
  );
}

function proposalsFailure(error: unknown): OperatorLoadFailure {
  return error instanceof OperatorLoadFailureError
    ? error.failure
    : operatorLoadFailureFromError(error, "heatzone merge-split proposals");
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
  const NETWORK_OPERATOR_HEADERS = useMemo(() => operatorSecurityHeaders(activeRoleId), [activeRoleId]);
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
  // time and stays authoritative while the source tab+zone URL is unchanged.
  // Acknowledged navigation or same-tab Back/Forward releases the override;
  // tab-only comparison would retain a cleared scope over a restored zone.
  const urlCandidateZoneId = searchParams.get(CANDIDATE_ZONE_PARAM) || null;
  const [tabOverride, setTabOverride] = useState<{
    from: number;
    fromZoneId: string | null;
    requested: number;
    candidateZoneId: string | null;
  } | null>(null);
  const overrideApplies = tabOverride !== null && tabOverride.from === urlTab
    && tabOverride.fromZoneId === urlCandidateZoneId;
  const activeTab = overrideApplies ? tabOverride.requested : urlTab;
  // 「查看本區候選點」 scopes the Candidates tab to one HeatZone. The scope is
  // carried in the URL (`zone=<heatZoneId>`) so a reload or back/forward
  // restores it, and in the click-time override for the same reason the tab is.
  const candidateZoneId =
    activeTab !== 2
      ? null
      : overrideApplies
        ? tabOverride.candidateZoneId
        : urlCandidateZoneId;

  useEffect(() => {
    setTabOverride((current) => (current !== null
      && (current.from !== urlTab || current.fromZoneId !== urlCandidateZoneId) ? null : current));
  }, [urlTab, urlCandidateZoneId]);
  const [networkSnapshot, setNetworkSnapshot] = useState<NetworkListingsSnapshot | null>(null);
  const [networkApiError, setNetworkApiError] = useState<string | null>(null);
  const [networkLoadFailure, setNetworkLoadFailure] = useState<OperatorLoadFailure | null>(null);
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
  const [scoringLoadFailure, setScoringLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);
  const [rebalanceSnapshot, setRebalanceSnapshot] = useState<NetworkRebalanceSnapshot | null>(null);
  const [rebalanceApiError, setRebalanceApiError] = useState<string | null>(null);
  const [rebalanceLoadState, setRebalanceLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [rebalanceLoadFailure, setRebalanceLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const [busyRebalanceAction, setBusyRebalanceAction] = useState<string | null>(null);
  const [reviewsSnapshot, setReviewsSnapshot] = useState<NetworkReviewsSnapshot | null>(null);
  const [reviewsLoadState, setReviewsLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [reviewsLoadFailure, setReviewsLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  const [proposals, setProposals] = useState<HeatZoneProposal[]>([]);
  const [proposalsLoadState, setProposalsLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const [proposalsApiError, setProposalsApiError] = useState<string | null>(null);
  const [proposalsLoadFailure, setProposalsLoadFailure] = useState<OperatorLoadFailure | null>(null);

  const getCompositionClient = useCallback(async () => {
    const { buildHeatZoneCompositionClient } = await import(
      "./network/heatZoneCompositionClient"
    );
    return buildHeatZoneCompositionClient(activeRoleId);
  }, [activeRoleId]);

  const reloadProposals = useCallback(async () => {
    try {
      const client = await getCompositionClient();
      const items = await client.fetchProposals();
      if (items.length > 0 || !fixturesAllowed) {
        setProposals(items);
        setProposalsLoadState("ready");
      } else {
        setProposals([]);
        setProposalsLoadState(fixturesAllowed ? "fixture" : "empty");
      }
      setProposalsApiError(null);
      setProposalsLoadFailure(null);
    } catch (error) {
      setProposalsLoadState(fixturesAllowed ? "fixture" : "error");
      setProposalsApiError("Failed to load merge/split proposals");
      setProposalsLoadFailure(proposalsFailure(error));
    }
  }, [fixturesAllowed, getCompositionClient]);

  useEffect(() => {
    if (activeTab !== 7) {
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const client = await getCompositionClient();
        const items = await client.fetchProposals();
        if (!cancelled) {
          if (items.length > 0 || !fixturesAllowed) {
            setProposals(items);
            setProposalsLoadState("ready");
          } else {
            setProposals([]);
            setProposalsLoadState(fixturesAllowed ? "fixture" : "empty");
          }
          setProposalsApiError(null);
          setProposalsLoadFailure(null);
        }
      } catch (error) {
        if (!cancelled) {
          setProposalsLoadState(fixturesAllowed ? "fixture" : "error");
          setProposalsApiError("Failed to load merge/split proposals");
          setProposalsLoadFailure(proposalsFailure(error));
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [activeTab, fixturesAllowed, getCompositionClient]);

  const handleApproveProposal = useCallback(
    async (proposalId: string, notes?: string) => {
      const client = await getCompositionClient();
      const ok = await client.approveProposal(proposalId, notes);
      if (!ok) {
        throw new Error("Failed to approve proposal");
      }
      await reloadProposals();
    },
    [getCompositionClient, reloadProposals],
  );

  const handleRejectProposal = useCallback(
    async (proposalId: string, reason: string) => {
      const client = await getCompositionClient();
      const ok = await client.rejectProposal(proposalId, reason);
      if (!ok) {
        throw new Error("Failed to reject proposal");
      }
      await reloadProposals();
    },
    [getCompositionClient, reloadProposals],
  );

  const handlePreviewProposal = useCallback(
    async (proposalId: string): Promise<ProposalPreviewData | null> => {
      const client = await getCompositionClient();
      return await client.previewProposal(proposalId);
    },
    [getCompositionClient],
  );

  const changeActiveTab = useCallback((tabIndex: number, extraParams?: Record<string, string>) => {
    setTabOverride({
      from: urlTab,
      fromZoneId: urlCandidateZoneId,
      requested: tabIndex,
      candidateZoneId: tabIndex === 2 ? extraParams?.[CANDIDATE_ZONE_PARAM] || null : null,
    });
    const params = new URLSearchParams(searchParams.toString());
    // The zone scope only lives as long as the navigation that asked for it;
    // any other tab change (tab bar, stepper, convert) shows every candidate.
    params.delete(CANDIDATE_ZONE_PARAM);
    for (const [key, value] of Object.entries(extraParams ?? {})) params.set(key, value);
    const href = buildNetworkTabHref(
      pathname,
      tabIndex,
      params,
      typeof window === "undefined" ? "" : window.location.hash,
    );
    router.push(href, { scroll: false });
  }, [pathname, router, searchParams, urlTab, urlCandidateZoneId]);

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
    fixturesAllowed && networkSnapshot?.source !== "api" &&
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
      const read = await fetchNetworkSnapshot(effectiveSelectedId || null, effectiveLens, activeRoleId);
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkListingsSnapshot(snapshot);
        const serverSelectedId = snapshot.selectedHeatZoneId;
        if (
          inspection === "ready" &&
          !effectiveSelectedId &&
          serverSelectedId &&
          snapshot.heatZones?.some((zone) => zone.id === serverSelectedId)
        ) {
          setLocalSelectedId(serverSelectedId);
        }
        setNetworkSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setNetworkApiError(null);
        setNetworkLoadFailure(null);
        setNetworkLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled && !read.ok) {
        // A refused read leaves no snapshot behind: its collections were not
        // read, so nothing below may present them as an authorized result.
        setNetworkSnapshot(null);
        setNetworkLoadFailure(read.failure);
        setNetworkApiError(
          fixturesAllowed
            ? "network-listings API unavailable; using local fixtures"
            : read.failure.technicalDetail ?? "network-listings read failed",
        );
        setNetworkLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, effectiveLens, effectiveSelectedId, fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadScoring() {
      const read = await fetchNetworkScoringSnapshot(activeRoleId);
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkScoringSnapshot(snapshot);
        setScoringSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setScoringLoadFailure(null);
        setScoringLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled && !read.ok) {
        setScoringLoadFailure(read.failure);
        setScoringLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadScoring();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadRebalance() {
      const read = await fetchNetworkRebalanceSnapshot(activeRoleId);
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && snapshot) {
        const inspection = inspectNetworkRebalanceSnapshot(snapshot);
        setRebalanceSnapshot(
          inspection === "ready" || fixturesAllowed
            ? snapshot
            : null,
        );
        setRebalanceApiError(null);
        setRebalanceLoadFailure(null);
        setRebalanceLoadState(
          inspection === "ready"
            ? "ready"
            : fixturesAllowed
              ? "fixture"
              : inspection,
        );
      } else if (!cancelled && !read.ok) {
        setRebalanceLoadFailure(read.failure);
        setRebalanceApiError(
          fixturesAllowed
            ? "network-rebalance API unavailable; using local fixtures"
            : read.failure.technicalDetail ?? "network-rebalance read failed",
        );
        setRebalanceLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadRebalance();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, fixturesAllowed]);

  const applyReviewsSnapshot = useCallback((snapshot: NetworkReviewsSnapshot) => {
    const inspection = inspectNetworkReviewsSnapshot(snapshot);
    // An authorized empty dedicated read is authoritative too. Do not replace
    // its zero queue with the independently projected network-listings rows.
    setReviewsSnapshot(
      inspection === "ready" || (inspection === "empty" && snapshot.source === "api") || fixturesAllowed
        ? snapshot
        : null,
    );
    setReviewsLoadFailure(null);
    setReviewsLoadState(
      inspection === "ready" ? "ready" : fixturesAllowed ? "fixture" : inspection,
    );
  }, [fixturesAllowed]);

  useEffect(() => {
    let cancelled = false;
    async function loadReviews() {
      const read = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders);
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && snapshot) {
        applyReviewsSnapshot(snapshot);
      } else if (!cancelled && !read.ok) {
        setReviewsSnapshot(null);
        setReviewsLoadFailure(read.failure);
        setReviewsLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadReviews();
    return () => {
      cancelled = true;
    };
  }, [applyReviewsSnapshot, fixturesAllowed, reviewIdentity]);

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
      const read = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders);
      if (read.ok) {
        applyReviewsSnapshot(read.snapshot);
      } else {
        setReviewsSnapshot(null);
        setReviewsLoadFailure(read.failure);
        setReviewsLoadState(fixturesAllowed ? "fixture" : "error");
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
    const read = await fetchNetworkScoringSnapshot(activeRoleId);
    if (read.ok) {
      setScoringSnapshot(read.snapshot);
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

  async function scoreAllCandidates(candidateIds?: string[]) {
    // An empty candidateIds list means "every candidate" server-side, so a
    // scoped batch is only sent with at least one id.
    if (candidateIds && candidateIds.length === 0) return;
    await postScoringAction(
      "score",
      candidateIds ? { candidateIds } : {},
      "batch",
      candidateIds ? `r4-006-score-batch-${candidateIds.join(",")}` : "r4-006-score-batch",
    );
  }

  async function toggleCompareCandidate(candidateId: string) {
    const current = scoringSnapshot?.compareSet ?? [];
    const next = current.includes(candidateId)
      ? current.filter((id) => id !== candidateId)
      : [...current, candidateId];
    await postScoringAction("compare", { candidateIds: next }, candidateId, `r4-006-compare-${candidateId}`);
  }

  async function reloadRebalanceSnapshot() {
    const read = await fetchNetworkRebalanceSnapshot(activeRoleId);
    if (read.ok) {
      setRebalanceSnapshot(read.snapshot);
      setRebalanceApiError(null);
    }
  }

  async function postRebalanceAction(
    storeId: string,
    action: "request-avm" | "complete-avm" | "solve-netplan" | "submit-review",
    submission?: {
      reason?: string;
      actorRoleId?: string;
      actorName?: string;
      acknowledgedClasses?: string[];
      acknowledgementReason?: string;
      acknowledgementActorId?: string;
      approvalReceiptId?: string;
    },
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
      const bodyPayload =
        action === "submit-review" && submission
          ? {
              // The requester's role, recorded as the requester. It is never the
              // acknowledging authority -- the API reads that off the approval
              // receipt named in the submission.
              actorRoleId: submission.actorRoleId || NETWORK_ACTOR.actorRoleId,
              actorName: submission.actorName,
              reason: submission.reason || "Move scenario selected for Govern approval; relocation remains unexecuted.",
              acknowledgedClasses: submission.acknowledgedClasses,
              acknowledgementReason: submission.acknowledgementReason,
              acknowledgementActorId: submission.acknowledgementActorId,
              approvalReceiptId: submission.approvalReceiptId,
            }
          : {
              ...NETWORK_ACTOR,
              reason: "Move scenario selected for Govern approval; relocation remains unexecuted.",
            };

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": `r4-008-${action}-${storeId}`,
          "X-Correlation-Id": `corr-r4-008-${action}-${storeId}`,
          ...NETWORK_OPERATOR_HEADERS,
        },
        body: JSON.stringify(bodyPayload),
      });
      if (!response.ok) {
        // A disclosure refusal names which constraint classes blocked the
        // submission and under which policy version. A bare status code would
        // leave the operator with "422" and no way to tell a missing
        // acknowledgement field from a plan that cannot be submitted at all.
        const detail = await readErrorDetail(response);
        setRebalanceApiError(
          detail
            ? `network-rebalance ${action} failed (${response.status}): ${detail}`
            : `network-rebalance ${action} failed (${response.status})`,
        );
        return;
      }
      await reloadRebalanceSnapshot();
    } catch {
      setRebalanceApiError(`network-rebalance ${action} failed`);
    } finally {
      setBusyRebalanceAction(null);
    }
  }

  async function readErrorDetail(response: Response): Promise<string | null> {
    try {
      const body = await response.json();
      const detail = (body as { detail?: unknown })?.detail;
      if (typeof detail === "string") return detail;
      if (detail && typeof detail === "object") {
        const message = (detail as { message?: unknown }).message;
        if (typeof message === "string") return message;
      }
      return null;
    } catch {
      return null;
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
  // One effective review source feeds the header chip, the tab badge and the
  // Review panel. Fallback rows exist only in fixture mode, where the panel
  // lists them whenever the dedicated read has no rows (null or an empty
  // response), so the count follows the listed queue there. In production an
  // authorized dedicated read (empty included) is authoritative; only a refused
  // or failed read keeps the projected scoped-snapshot count.
  const reviewFallbackRows = useMemo(
    () => (fixturesAllowed ? viewModel.reviewQueue : []),
    [fixturesAllowed, viewModel.reviewQueue],
  );
  const dedicatedReviews = useMemo(() => reviewsSnapshot?.reviews ?? [], [reviewsSnapshot]);
  const displayedReviews = useMemo(
    () => resolveEffectiveReviews(dedicatedReviews, reviewFallbackRows),
    [dedicatedReviews, reviewFallbackRows],
  );
  const pendingReviewCount = reviewsSnapshot !== null || fixturesAllowed
    ? displayedReviews.filter((review) => review.status === "pending").length
    : viewModel.totals.pendingReviews;
  const tabCounts = [
    null,
    viewModel.totals.newListings,
    viewModel.totals.activeCandidates,
    null,
    scoringSnapshot?.compareSet?.length ?? null,
    pendingReviewCount,
    viewModel.totals.openRebalances,
    null,
  ];

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

  /**
   * Opens the canonical add-from-URL dialog on Listing Radar (dialog=add), so
   * the intake keeps its single dialog and permission checks. The radar is
   * already scoped to the selected HeatZone, which the dialog pre-fills.
   */
  function addListingFromUrl() {
    if (selectedZone) {
      changeActiveTab(1, { dialog: "add" });
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
    const read = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens, activeRoleId);
    if (read.ok) {
      setNetworkSnapshot(read.snapshot);
      setNetworkApiError(null);
      setNetworkLoadFailure(null);
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
  const candidateZoneScope = candidateZoneId
    ? {
        id: candidateZoneId,
        label: heatZones.find((zone) => zone.id === candidateZoneId)?.label ?? candidateZoneId,
      }
    : null;
  const selectedZoneLabel = selectedZone?.label ?? heatZones.find((zone) => zone.id === effectiveSelectedId)?.label;

  const findAreasLoadState = resolveNetworkFindAreasLoadState(networkLoadState, heatZones.length);
  const activeTabGateState = resolveNetworkTabGateState({
    activeTab,
    findAreasLoadState,
    fixturesAllowed,
    proposalsLoadState,
    rebalanceLoadState,
    reviewsLoadState,
    scoringLoadState,
  });
  const activeTabGateDetail =
    activeTab === 0
      ? networkApiError ??
        (findAreasLoadState !== networkLoadState
          ? "HeatZone aggregates are not authorized for this read scope; scoped listings remain in Listing Radar."
          : null)
      : activeTab === 6
        ? rebalanceApiError
        : activeTab === 7
          ? proposalsApiError
          : null;
  // The received failure (status, canonical code, correlation ID) of the read
  // backing the active tab. It names a 403 as an authenticated denial instead
  // of letting the gate guess a kind from the prose detail.
  const activeTabGateFailure =
    activeTabGateState !== "error"
      ? null
      : activeTab === 0
        ? networkLoadFailure
        : activeTab >= 2 && activeTab <= 4
          ? scoringLoadFailure
          : activeTab === 5
            ? reviewsLoadFailure
            : activeTab === 6
              ? rebalanceLoadFailure
              : activeTab === 7
                ? proposalsLoadFailure
                : null;
  // Listing Radar is never gated as a whole (its intake queue owns its own
  // binding), so a refused listings read is handed to the panel instead.
  const listingsReadFailure =
    !fixturesAllowed && networkLoadState === "error" ? networkLoadFailure : null;
  const snapshotCountState = resolveNetworkCountState(networkLoadState, networkLoadFailure, fixturesAllowed);
  // Package10 uses four workflow KPIs, not unauthorized HeatZone aggregates.
  // Match the count to its actual source: a refused dedicated reviews read
  // cannot erase known authorized reviews in the scoped network snapshot.
  const reviewCountState = reviewsSnapshot !== null
    ? resolveNetworkCountState(reviewsLoadState, reviewsLoadFailure, fixturesAllowed)
    : snapshotCountState;
  const rebalanceCountState = resolveNetworkCountState(rebalanceLoadState, rebalanceLoadFailure, fixturesAllowed);

  const listingRadarPanel = (
    <ListingRadarPanel
      activeRoleId={activeRoleId}
      busyListingId={busyListingId}
      intakeDetailOpen={intakeDetailOpen}
      listings={listingsEffective}
      listingsReadFailure={listingsReadFailure}
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
      {/* Package 10 S05: one row — title, flow summary, four truthful KPI chips. */}
      <header className={styles.header} data-testid="network-header">
        <h2>展店與店網</h2>
        <p className={styles.headerSummary}>找區域 → 物件收件 → 候選點 → SiteScore → 比較 → 審核；低效門市另走重配</p>
        {isFixtureFallback ? (
          <span className={styles.fixtureChip} title="API unavailable — showing bundled fixture data">
            示範資料
          </span>
        ) : null}
        <ul className={styles.headerStats} aria-label="展店與店網摘要" data-testid="network-header-stats">
          <li><NetworkCount count={viewModel.totals.newListings} label="今日新物件" state={snapshotCountState} /></li>
          <li><NetworkCount count={viewModel.totals.activeCandidates} label="進行中候選" state={snapshotCountState} /></li>
          <li><NetworkCount count={pendingReviewCount} label="待審 Review" state={reviewCountState} /></li>
          <li><NetworkCount count={viewModel.totals.openRebalances} label="重配候選" state={rebalanceCountState} /></li>
        </ul>
        {networkApiError ? (
          <p className={styles.headerNotice} role="status">
            {networkApiError}
          </p>
        ) : null}
      </header>

      <NetworkShell
        activeTab={activeTab}
        onTabChange={changeActiveTab}
        steps={expansionSteps}
        tabCounts={tabCounts}
        tabs={networkTabs}
      >
        {activeTabGateState ? (
          <OperatorDataUnavailableGate
            detail={activeTabGateDetail}
            failure={activeTabGateFailure}
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
            onClearZoneScope={() => changeActiveTab(2)}
            onScoreAll={scoreAllCandidates}
            onToggleCompare={toggleCompareCandidate}
            zoneScope={candidateZoneScope}
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
            reviews={dedicatedReviews}
            fallbackRows={reviewFallbackRows}
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
            onSubmitReview={(storeId, submission) => postRebalanceAction(storeId, "submit-review", submission)}
            rows={viewModel.rebalanceQueue}
          />
        ) : activeTab === 7 ? (
          <HeatZoneMergeSplitPanel
            activeRoleId={activeRoleId}
            isLoading={proposalsLoadState === "loading"}
            onApproveProposal={handleApproveProposal}
            onPreviewProposal={handlePreviewProposal}
            onRejectProposal={handleRejectProposal}
            proposals={proposals}
          />
        ) : (
          <FindAreasPanel
            activeRoleId={activeRoleId}
            canAddListing={canPerformIntake("submit", activeRoleId)}
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
            onAddListingFromUrl={addListingFromUrl}
            onViewCandidates={() =>
              selectedZone && changeActiveTab(2, { [CANDIDATE_ZONE_PARAM]: selectedZone.id })
            }
            onScoreCandidate={scoreCandidate}
            onSubmitReview={submitReview}
          />
        )}
      </NetworkShell>

      {listingMergeDialog}
    </section>
  );
}

/**
 * Whether a header count is an authoritative result. Only an answered read
 * (ready, or an authorized empty 200) yields a number; a refused, failed,
 * blocked or pending read yields no count at all, never a manufactured 0.
 */
export type NetworkCountState =
  | { known: true }
  | { known: false; label: string; reason: "pending" | "unread" | "withheld" };

export function resolveNetworkCountState(
  loadState: OperatorDataAvailability,
  failure: OperatorLoadFailure | null,
  fixturesAllowed: boolean,
  aggregateWithheld = false,
): NetworkCountState {
  if (!fixturesAllowed && aggregateWithheld) {
    return { known: false, label: "未授權", reason: "withheld" };
  }
  if (fixturesAllowed || loadState === "ready" || loadState === "empty" || loadState === "fixture") {
    return { known: true };
  }
  if (loadState === "loading") return { known: false, label: "載入中", reason: "pending" };
  if (loadState === "seed") return { known: false, label: "資料來源未通過", reason: "unread" };
  return { known: false, label: unreadCountLabel(failure?.kind), reason: "unread" };
}

function NetworkCount({
  count,
  label,
  state,
}: {
  count: number | string;
  label: string;
  state: NetworkCountState;
}) {
  if (state.known) {
    return (
      <span data-count-state="known">
        <strong>{count}</strong> {label}
      </span>
    );
  }
  return (
    <span data-count-state={state.reason} title={`${label}：${state.label}，不是 0 筆`}>
      <strong>—</strong> {label}（{state.label}）
    </span>
  );
}
