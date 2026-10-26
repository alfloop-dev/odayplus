"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { operatorSecurityHeaders, operatorSubjectId } from "./operatorSecurityHeaders";
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
import { buildDurableDetailHref } from "./network/intake/AssistedIntakeSection";
import { parseUrlState } from "./network/intake/urlState";
import { navigationIncludesTarget, resolveListingNavigation, type ListingNavigationTarget, type RelatedIntake } from "./network/listingNavigation";
import { ListingMergeDialog, type ListingMergeForm, type ListingMergeRequest } from "./network/ListingMergeDialog";
import { ListingArchiveDialog, type ListingArchiveRequest } from "./network/ListingArchiveDialog";
import { listingWriteReceiptMatches, listingWriteTerminalMatches, resolveListingAction, type ListingAction } from "./network/listingActions";
import {
  buildListingsClient,
  listingsApi,
  missingListingsClientError,
  newMergeIdempotencyKey,
  newArchiveIdempotencyKey,
  type ListingApiError,
} from "./network/listingsClient";
import { canMergeListing } from "./network/listingPermissions";
import { boundReviewDecisionBody, hasReviewDecisionContext, reviewDecisionContextMatches, reviewDecisionReceiptMatches, reviewDecisionReadbackMatches, type BoundReviewDecisionBody, type ReviewDecisionContext } from "./network/networkReviewDecision";
import { reviewDecisionFreshGraphMatches } from "./network/networkReviewDecisionRefresh";
import { readReviewDecisionIdentity } from "./network/reviewDecisionAuthContext";
import { ReviewSubmissionDialog } from "./network/ReviewSubmissionDialog";
import { canOfferReviewSubmission, hasReviewSubmissionContext, reviewSubmissionTargetsMatch, reviewSubmissionReceiptMatches, reviewSubmissionReadbackMatches, reviewSubmissionOffers } from "./network/networkReviewSubmission";
import type { ReviewSubmissionContext, ReviewSubmissionBody, ReviewSubmissionForm, ReviewSubmissionOffer, ReviewSubmissionReceipt } from "./network/networkReviewSubmissionTypes";
import { CandidatePanel } from "./network/CandidatePanel";
import { SiteScorePanel, StandardFormatAssumptions } from "./network/SiteScorePanel";
import { ComparePanel } from "./network/ComparePanel";
import { ReviewPanel, resolveEffectiveReviews } from "./network/ReviewPanel";
import { hasRecordedReviewReport, inspectReviewsReadProjection, isReviewPending } from "./network/networkReviewProjection";
import { NetworkShell } from "./network/NetworkShell";
import { projectCandidateWorkflow } from "./network/networkWorkflowProjection";
import { projectCandidateNativeWorkflow, resolveCandidateReviewTarget } from "./network/candidateNativeWorkflowProjection";
import { hasNetworkListingsRenderShape, hasNetworkScoringRenderShape, hasCanonicalScoreSession, hasScoreExecutionContext, hasScoreExecutionReceipt, scoreExecutionCommandReceiptMatches, hasVerifiedCurrentScoreAdmission, readId, readObject } from "./network/networkReadValidation";
import { FindAreasPanel } from "./network/FindAreasPanel";
import type { ListingSearchDialogProps } from "./network/ListingSearchDialog";
import type { AreaPlanningDraftDialogProps } from "./network/AreaPlanningDraftDialog";
import { canPerform as canPerformIntake } from "./network/intake/intakePermissions";
import type { RebalancePanelProps } from "./network/RebalancePanel";
import { createNetPlanBuilderApi } from "./network/netplanBuilderClient";
import { createSurveyAssignmentApi, type SurveyTarget } from "./network/surveyAssignmentClient";
import { createSurveyEvidenceApi } from "./network/surveyEvidenceClient";
import { SurveyAssignmentDialog } from "./network/SurveyAssignmentDialog";
import { parseHeatZoneSelection } from "./network/heatZoneNavigation";
import {
  heatZoneRequestArgument,
  heatZoneSelectionParams,
  heatZoneSnapshotReceipt,
  heatZoneTargetGate,
  resolveHeatZoneRequestedTarget,
} from "./network/heatZoneRequestedSnapshot";
import type {
  HeatZoneMergeSplitPanelProps,
  HeatZoneProposal,
  ProposalPreviewData,
} from "./network/HeatZoneMergeSplitPanel";
import {
  buildNetworkTabHref,
  NETWORK_TAB_IDS,
  candidateWorkflowNavigationParams,
  isCandidateWorkflowTab,
  CANDIDATE_SELECTION_PARAM,
  parseCandidateSelection,
  parseReviewSelection,
  REVIEW_SELECTION_PARAM,
  parseNetworkTabIndex,
} from "./network/networkUrlState";
import type { ExpansionStep } from "./network/ExpansionStepper";
import type { NetworkScoringSnapshot, ScoreExecutionReceipt } from "./network/networkScoringTypes";
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
const ListingSearchDialog = dynamic<ListingSearchDialogProps>(
  () => import("./network/ListingSearchDialog").then(mod => mod.ListingSearchDialog), { ssr: false },
);
const AreaPlanningDraftDialog = dynamic<AreaPlanningDraftDialogProps>(
  () => import("./network/AreaPlanningDraftDialog").then(mod => mod.AreaPlanningDraftDialog), { ssr: false },
);
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

type NetworkReadSessionGuard = () => Promise<boolean>;

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
  /** Verified bootstrap projection; absent metadata fails closed outside fixtures. */
  allowedTabIds?: readonly string[];
  /** Console-owned session lease for every canonical read. The sole production
   * mount always supplies it. Optional only for standalone renderer/unit and
   * explicit POC callers; it never grants tabs, roles, or API permissions. */
  createAuthenticatedReadGuard?: (current: () => boolean) => NetworkReadSessionGuard;
  trackedHeatZoneIds?: string[];
  /**
   * Active operator console role. Binds the Network Review read/decide security
   * headers, the decision actor, and whether the review decision bar is shown
   * (canDecide). Defaults to the console default role.
   */
  activeRoleId?: OperatorRoleId;
  callbacks?: NetworkFindAreasWorkspaceCallbacks;
  onReviewDecisionConfirmed?: () => Promise<void> | void;
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
  mergedIntoId?: string | null;
  sourceEvidence?: string[];
  sourceListingId?: string;
  sourceUrl?: string;
};

type NetworkListingsSnapshot = {
  source?: "api" | "fixture";
  heatZones?: OperatorHeatZone[];
  heatZoneAvailability?: { state: "available" | "unavailable"; message?: string };
  listingSources?: ListingSource[];
  listings?: NetworkListingDetail[];
  candidates?: Candidate[];
  siteReviews?: SiteReview[];
  expansionSteps?: ExpansionStep[];
  selectedHeatZoneId?: string;
  selectedLens?: NetworkFindAreasLens;
  correlationId?: string;
  assistedIntakes?: RelatedIntake[];
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
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty" | "error"> {
  if (snapshot == null) return "empty";
  if (!readObject(snapshot)) return "error";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  const requiredCollections = [
    snapshot.heatZones,
    snapshot.listingSources,
    snapshot.listings,
    snapshot.candidates,
    snapshot.siteReviews,
  ];
  if (snapshot.source === "api" && requiredCollections.some((value) => value !== undefined && !Array.isArray(value))) return "error";
  const hasRequiredShape =
    snapshot.source === "api" &&
    requiredCollections.every(Array.isArray);
  if (hasRequiredShape && !hasNetworkListingsRenderShape(snapshot)) return "error";
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
  nativeZoneState?: string,
): OperatorDataAvailability {
  if (nativeZoneState === "unavailable") return "error";
  return networkLoadState === "ready" && heatZoneCount === 0 ? "empty" : networkLoadState;
}

export function inspectNetworkScoringSnapshot(
  snapshot: NetworkScoringSnapshot | null,
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty" | "error"> {
  if (snapshot == null) return "empty";
  if (!readObject(snapshot)) return "error";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  if (snapshot.source === "api" || snapshot.source === "canonical") {
    if ([snapshot.candidates, snapshot.scorecards].some((value) => value !== undefined && !Array.isArray(value))
      || snapshot.compare !== undefined && !readObject(snapshot.compare)
      || snapshot.compare?.columns !== undefined && !Array.isArray(snapshot.compare.columns)) return "error";
  }
  if ((snapshot.source !== "api" && snapshot.source !== "canonical")
    || !Array.isArray(snapshot.candidates) || !Array.isArray(snapshot.scorecards)
    || !Array.isArray(snapshot.compare?.columns)) return "empty";
  if (!hasNetworkScoringRenderShape(snapshot)) return "error";
  return snapshot.candidates.length > 0 || snapshot.scorecards.length > 0 ? "ready" : "empty";
}

type ScoringWorkflowProvenance = "canonical" | "fixture" | "unknown";

function inspectScoringWorkflowProvenance(snapshot: unknown): ScoringWorkflowProvenance {
  if (!readObject(snapshot)) return "unknown";
  // The actual canonical scoring producer owns this marker. It selects the
  // strict projector; existing shape, generation and exact joins still admit
  // each observation, so a marker alone cannot assert progress or permission.
  if (snapshot.source === "canonical") {
    if (!hasNetworkScoringRenderShape(snapshot)) return "unknown";
    const associated = (snapshot.candidates as unknown[]).every((row) => {
      if (!readObject(row)) return false;
      const evidence = row.readEvidence;
      const admission = row.currentAdmission;
      if (Object.prototype.hasOwnProperty.call(row, "readEvidence")
        && (!readObject(evidence) || evidence.candidateId !== row.id || evidence.listingId !== row.listingId || !readId(evidence.addressId))) return false;
      if (Object.prototype.hasOwnProperty.call(row, "currentAdmission")
        && (!readObject(admission) || admission.candidateId !== row.id || admission.listingId !== row.listingId || !readId(admission.addressId)
          || readObject(evidence) && admission.addressId !== evidence.addressId)) return false;
      return true;
    });
    return associated ? "canonical" : "unknown";
  }
  if (snapshot.source !== "api" || !hasNetworkScoringRenderShape(snapshot)) return "unknown";
  // The actual legacy fixture producer predates these canonical fields.
  // Presence, including explicit null, cannot silently become fixture data.
  const hasOwn = (row: unknown, keys: string[]) => readObject(row)
    && keys.some((key) => Object.prototype.hasOwnProperty.call(row, key));
  if ((snapshot.candidates as unknown[]).some((row) => hasOwn(row, ["readEvidence", "currentAdmission", "pipelineEvidence"]))
    || (snapshot.scorecards as unknown[]).some((row) => hasOwn(row, ["reportId", "reportVersion", "sitescoreRunId"]))) return "unknown";
  return "fixture";
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
): Extract<OperatorDataAvailability, "ready" | "seed" | "empty" | "error"> {
  if (!snapshot) return "empty";
  if (payloadContainsSeedData(snapshot) || isSeedDataSource(snapshot.source)) return "seed";
  return inspectReviewsReadProjection(snapshot);
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
type NetworkRead<T> = { ok: true; snapshot: T } | { ok: false; failure: OperatorLoadFailure }
  | { ok: false; superseded: true };

async function readNetworkSnapshot<T>(
  label: string,
  path: string,
  headers: Record<string, string>,
  correlationId: string,
  sessionGuard?: NetworkReadSessionGuard,
): Promise<NetworkRead<T>> {
  // A superseded lease is not a current owner's HTTP failure. Consumers
  // discard it before touching any snapshot, error, or loading state.
  const unowned = (): NetworkRead<T> => ({ ok: false, superseded: true });
  try {
    if (sessionGuard && !await sessionGuard()) return unowned();
    const response = await fetch(path, {
      cache: "no-store",
      headers: {
        ...headers,
        "X-Correlation-Id": correlationId,
      },
    });
    if (sessionGuard && !await sessionGuard()) return unowned();
    if (!response.ok) {
      const failure = await operatorLoadFailureFromResponse(response, label, correlationId);
      if (sessionGuard && !await sessionGuard()) return unowned();
      return { ok: false, failure };
    }
    const body: unknown = await response.json();
    if (sessionGuard && !await sessionGuard()) return unowned();
    if (!readObject(body)) {
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
    if (sessionGuard && !await sessionGuard()) return unowned();
    return { ok: false, failure: operatorLoadFailureFromError(error, label, correlationId) };
  }
}

function fetchNetworkReviewsSnapshot(
  readHeaders: Record<string, string>,
  sessionGuard?: NetworkReadSessionGuard,
): Promise<NetworkRead<NetworkReviewsSnapshot>> {
  return readNetworkSnapshot(
    "network-reviews",
    "/api/v1/operator/network-reviews",
    readHeaders,
    "corr-r4-007-reviews-read", sessionGuard,
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
  sessionGuard?: NetworkReadSessionGuard,
): Promise<NetworkRead<NetworkListingsSnapshot>> {
  const params = new URLSearchParams({ lens });
  if (selectedHeatZoneId) params.set("selectedHeatZoneId", selectedHeatZoneId);
  return readNetworkSnapshot(
    "network-listings",
    `/api/v1/operator/network-listings?${params.toString()}`,
    operatorSecurityHeaders(roleId),
    `corr-r4-005-read-${selectedHeatZoneId ?? "initial"}-${lens}`, sessionGuard,
  );
}

function fetchNetworkScoringSnapshot(roleId: OperatorRoleId, sessionGuard?: NetworkReadSessionGuard): Promise<NetworkRead<NetworkScoringSnapshot>> {
  return readNetworkSnapshot(
    "network-scoring",
    "/api/v1/operator/network-scoring",
    operatorSecurityHeaders(roleId),
    "corr-r4-006-scoring-read", sessionGuard,
  );
}

function fetchNetworkRebalanceSnapshot(roleId: OperatorRoleId, sessionGuard?: NetworkReadSessionGuard): Promise<NetworkRead<NetworkRebalanceSnapshot>> {
  return readNetworkSnapshot(
    "network-rebalance",
    "/api/v1/operator/network-rebalance",
    operatorSecurityHeaders(roleId),
    "corr-r4-008-rebalance-read", sessionGuard,
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
  allowedTabIds,
  createAuthenticatedReadGuard,
  callbacks,
  candidates: candidatesInput,
  heatZones: heatZonesInput,
  initialTabId,
  onReviewDecisionConfirmed,
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
  const allowedTabIndices = useMemo(() => NETWORK_TAB_IDS.flatMap((tab, index) =>
    (allowedTabIds?.includes(tab) ?? fixturesAllowed) ? [index] : []), [allowedTabIds, fixturesAllowed]);
  const canReadListings = allowedTabIndices.includes(0) || allowedTabIndices.includes(1);
  const canReadScoring = allowedTabIndices.some((index) => index >= 2 && index <= 4);
  const canReadReviews = allowedTabIndices.includes(5);
  const canReadRebalance = allowedTabIndices.includes(6);
  // This observed browser context invalidates cached links. Clicks also re-read
  // the guarded endpoints because the API exposes no client scope/session epoch.
  const navigationContextKey = JSON.stringify([
    activeRoleId, operatorSubjectId(activeRoleId), allowedTabIndices,
    typeof window === "undefined" ? "" : window.sessionStorage.getItem("oday.operator.tenant"),
  ]);
  const navigationContext = useRef({ key: navigationContextKey, generation: 0 });
  if (navigationContext.current.key !== navigationContextKey) {
    navigationContext.current = { key: navigationContextKey, generation: navigationContext.current.generation + 1 };
  }
  const navigationIntent = useRef(0);
  const networkReadSequence = useRef(0);
  const scoringReadSequence = useRef(0);
  const reviewsReadSequence = useRef(0);
  const navigationLocationKey = `${pathname}?${searchParams.toString()}`;
  const navigationLocation = useRef({ key: navigationLocationKey, version: 0, mounted: true });
  if (navigationLocation.current.key !== navigationLocationKey) {
    navigationLocation.current = { ...navigationLocation.current, key: navigationLocationKey, version: navigationLocation.current.version + 1 };
  }
  useEffect(() => {
    navigationLocation.current.mounted = true;
    return () => {
      navigationLocation.current.mounted = false;
      navigationIntent.current += 1;
    };
  }, []);
  const readGuardFactory = useRef(createAuthenticatedReadGuard);
  readGuardFactory.current = createAuthenticatedReadGuard;
  function snapshotReadGuard(): NetworkReadSessionGuard | undefined {
    const context = navigationContext.current;
    const locationVersion = navigationLocation.current.version;
    return readGuardFactory.current?.(() => navigationLocation.current.mounted
      && context === navigationContext.current && locationVersion === navigationLocation.current.version);
  }
  const [networkNavigationGeneration, setNetworkNavigationGeneration] = useState<number | null>(null);
  const [scoringNavigationGeneration, setScoringNavigationGeneration] = useState<number | null>(null);
  const [rebalanceNavigationGeneration, setRebalanceNavigationGeneration] = useState<number | null>(null);
  const [reviewsNavigationGeneration, setReviewsNavigationGeneration] = useState<number | null>(null);
  const [navigationError, setNavigationError] = useState<string | null>(null);
  const NETWORK_OPERATOR_HEADERS = useMemo(() => operatorSecurityHeaders(activeRoleId), [activeRoleId]);
  const candidatesProp = candidatesInput ?? (fixturesAllowed ? CANDIDATE_FIXTURES : EMPTY_CANDIDATES);
  const heatZonesProp = heatZonesInput ?? (fixturesAllowed ? HEAT_ZONE_FIXTURES : EMPTY_HEAT_ZONES);
  const listings = listingsInput ?? (fixturesAllowed ? LISTING_FIXTURES : EMPTY_LISTINGS);
  const listingSources = listingSourcesInput ?? (fixturesAllowed ? LISTING_SOURCE_FIXTURES : EMPTY_LISTING_SOURCES);
  const rebalanceStores = rebalanceStoresInput ?? (fixturesAllowed ? REBALANCE_STORE_FIXTURES : EMPTY_REBALANCE_STORES);
  const siteReviews = siteReviewsInput ?? (fixturesAllowed ? SITE_REVIEW_FIXTURES : EMPTY_SITE_REVIEWS);
  const reviewIdentity = useMemo(() => ({
    ...resolveNetworkReviewIdentity(activeRoleId),
    observedContextKey: navigationContextKey,
  }), [activeRoleId, navigationContextKey]);
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
  const urlCandidateSelection = parseCandidateSelection(searchParams);
  const urlReviewSelection = parseReviewSelection(searchParams);
  const [tabOverride, setTabOverride] = useState<{
    from: number;
    fromZoneId: string | null;
    fromCandidateQuery: string;
    fromReviewQuery: string;
    candidateId: string | null;
    reviewId: string | null;
    requested: number;
    candidateZoneId: string | null;
  } | null>(null);
  const overrideApplies = tabOverride !== null && tabOverride.from === urlTab
    && tabOverride.fromZoneId === urlCandidateZoneId
    && tabOverride.fromCandidateQuery === JSON.stringify(searchParams.getAll(CANDIDATE_SELECTION_PARAM))
    && tabOverride.fromReviewQuery === JSON.stringify(searchParams.getAll(REVIEW_SELECTION_PARAM));
  const requestedTab = overrideApplies ? tabOverride.requested : urlTab;
  const activeTab = allowedTabIndices.includes(requestedTab)
    ? requestedTab : allowedTabIndices[0] ?? -1;
  const workflowCandidateId = isCandidateWorkflowTab(activeTab) && !intakeDetailOpen
    ? overrideApplies ? tabOverride.candidateId : urlCandidateSelection.id : null;
  // 「查看本區候選點」 scopes the Candidates tab to one HeatZone. The scope is
  // carried in the URL (`zone=<heatZoneId>`) so a reload or back/forward
  // restores it, and in the click-time override for the same reason the tab is.
  const candidateZoneId =
    activeTab !== 2 || workflowCandidateId !== null
      ? null
      : overrideApplies
        ? tabOverride.candidateZoneId
        : urlCandidateZoneId;

  const selectedCandidateId = activeTab === 2 ? workflowCandidateId : null;
  const workflowSelectionError = isCandidateWorkflowTab(activeTab) && !overrideApplies && urlCandidateSelection.invalid
    ? "候選點連結包含不一致或空白的選取值。" : null;
  const candidateSelectionError = activeTab === 2 ? workflowSelectionError : null;
  // Exact Task Center HeatZone identity (frozen Root parser). Only Find Areas
  // consumes it; the intake detail route and every other tab keep their own
  // owners. It names a target only — the guarded read below decides access.
  const heatZoneSelection = parseHeatZoneSelection(searchParams, intakeDetailOpen ? -1 : activeTab);
  const heatZoneRequestId = heatZoneRequestArgument(heatZoneSelection, selectedHeatZoneId);

  useEffect(() => {
    setTabOverride((current) => (current !== null
      && (current.from !== urlTab || current.fromZoneId !== urlCandidateZoneId
        || current.fromCandidateQuery !== JSON.stringify(searchParams.getAll(CANDIDATE_SELECTION_PARAM))
        || current.fromReviewQuery !== JSON.stringify(searchParams.getAll(REVIEW_SELECTION_PARAM))) ? null : current));
  }, [urlTab, urlCandidateZoneId, searchParams]);
  const [networkSnapshotValue, setNetworkSnapshot] = useState<NetworkListingsSnapshot | null>(null);
  const networkSnapshot = networkNavigationGeneration === navigationContext.current.generation ? networkSnapshotValue : null;
  // Generation+URL+location-epoch receipt of the last admitted scoped read (answer or refusal).
  const [networkSnapshotReceipt, setNetworkSnapshotReceipt] = useState<string | null>(null);
  const [networkApiError, setNetworkApiError] = useState<string | null>(null);
  const [networkLoadFailureValue, setNetworkLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const networkLoadFailure = networkNavigationGeneration === navigationContext.current.generation ? networkLoadFailureValue : null;
  const [networkLoadStateValue, setNetworkLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const networkLoadState: OperatorDataAvailability = networkNavigationGeneration === navigationContext.current.generation || fixturesAllowed
    ? networkLoadStateValue : "loading";
  const [busyListingId, setBusyListingId] = useState<string | null>(null);
  useEffect(() => {
    navigationIntent.current += 1;
    setBusyListingId(null);
    setNavigationError(null);
  }, [navigationContextKey, navigationLocationKey]);
  const [mergeRequest, setMergeRequest] = useState<ListingMergeRequest | null>(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<ListingApiError | null>(null);
  const [archiveRequest, setArchiveRequest] = useState<ListingArchiveRequest | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveError, setArchiveError] = useState<ListingApiError | null>(null);
  const listingWriteSession = useRef<{
    action: ListingAction; contextKey: string; generation: number; locationVersion: number;
    submittedForm?: string;
    verifiedActorSubjectId?: string;
  } | null>(null);
  const listingWriteInFlight = useRef(false);
  useEffect(() => {
    listingWriteSession.current = null;
    setMergeRequest(null); setMergeError(null); setMergeBusy(false);
    setArchiveRequest(null); setArchiveError(null); setArchiveBusy(false);
  }, [navigationContextKey, navigationLocationKey]);
  const [scoringSnapshotValue, setScoringSnapshot] = useState<NetworkScoringSnapshot | null>(null);
  const [scoringWorkflowProvenance, setScoringWorkflowProvenance] = useState<ScoringWorkflowProvenance>("unknown");
  const scoringSnapshot = scoringNavigationGeneration === navigationContext.current.generation ? scoringSnapshotValue : null;
  const [scoringLoadStateValue, setScoringLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const scoringLoadState: OperatorDataAvailability = scoringNavigationGeneration === navigationContext.current.generation || fixturesAllowed
    ? scoringLoadStateValue : "loading";
  const [scoringLoadFailureValue, setScoringLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const scoringLoadFailure = scoringNavigationGeneration === navigationContext.current.generation ? scoringLoadFailureValue : null;
  const [busyCandidateId, setBusyCandidateId] = useState<string | null>(null);
  const [scoreExecution, setScoreExecution] = useState<ScoreExecutionReceipt | null>(null);
  const [scoreExecutionError, setScoreExecutionError] = useState<string | null>(null);
  const executionSequence = useRef(0);
  const executionSubjectIntent = useRef<string | null>(null);
  const executionKey = useRef<{ payload: string; key: string; correlation: string; actorSubjectId: string; tenantId: string } | null>(null);
  useEffect(() => {
    executionSequence.current += 1;
    setScoreExecution(null);
    setScoreExecutionError(null);
    setBusyCandidateId(null);
    executionKey.current = null;
    executionSubjectIntent.current = null;
  }, [navigationContextKey, navigationLocationKey]);
  const [rebalanceSnapshotValue, setRebalanceSnapshot] = useState<NetworkRebalanceSnapshot | null>(null);
  const rebalanceSnapshot = rebalanceNavigationGeneration === navigationContext.current.generation ? rebalanceSnapshotValue : null;
  const [rebalanceApiErrorValue, setRebalanceApiError] = useState<string | null>(null);
  const rebalanceApiError = rebalanceNavigationGeneration === navigationContext.current.generation ? rebalanceApiErrorValue : null;
  const [rebalanceLoadStateValue, setRebalanceLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const rebalanceLoadState: OperatorDataAvailability = rebalanceNavigationGeneration === navigationContext.current.generation || fixturesAllowed
    ? rebalanceLoadStateValue : "loading";
  const [rebalanceLoadFailureValue, setRebalanceLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const rebalanceLoadFailure = rebalanceNavigationGeneration === navigationContext.current.generation ? rebalanceLoadFailureValue : null;
  const [busyRebalanceAction, setBusyRebalanceAction] = useState<string | null>(null);
  const [reviewsSnapshotValue, setReviewsSnapshot] = useState<NetworkReviewsSnapshot | null>(null);
  const reviewsSnapshot = reviewsNavigationGeneration === navigationContext.current.generation ? reviewsSnapshotValue : null;
  const [reviewsLoadStateValue, setReviewsLoadState] = useState<OperatorDataAvailability>(
    fixturesAllowed ? "fixture" : "loading",
  );
  const reviewsLoadState: OperatorDataAvailability = reviewsNavigationGeneration === navigationContext.current.generation || fixturesAllowed
    ? reviewsLoadStateValue : "loading";
  const [reviewsLoadFailureValue, setReviewsLoadFailure] = useState<OperatorLoadFailure | null>(null);
  const reviewsLoadFailure = reviewsNavigationGeneration === navigationContext.current.generation ? reviewsLoadFailureValue : null;
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewIntentEpoch, setReviewIntentEpoch] = useState(0);
  const reviewDecisionInFlight = useRef<symbol | null>(null);
  const reviewDecisionAttempt = useRef<{ reviewId: string; generation: number; contextKey: string; locationVersion: number;
    observedTenant: string | null; actor: string | null; tenant: string | null; context: ReviewDecisionContext | null;
    frames: { network: number; scoring: number; reviews: number }; requestToken: symbol | null;
    body: BoundReviewDecisionBody | null; payload: string | null; key: string; correlation: string; unknownOutcome: boolean } | null>(null);
  useEffect(() => {
    reviewDecisionAttempt.current = null; reviewDecisionInFlight.current = null;
    setReviewSubmitting(false); setReviewError(null);
    setReviewIntentEpoch((epoch) => epoch + 1);
  },
    [navigationContextKey, navigationLocationKey]);

  const invalidateReviewScope = useCallback((session: NonNullable<typeof reviewDecisionAttempt.current>) => {
    if (reviewDecisionAttempt.current !== session || !navigationLocation.current.mounted) return;
    // A request owns each frame separately. Newer authenticated reads retain
    // their exact bytes even if an old request discovers a changed cookie.
    if (networkReadSequence.current === session.frames.network) {
      ++networkReadSequence.current; setNetworkSnapshot(null); setNetworkLoadState("error");
    }
    if (scoringReadSequence.current === session.frames.scoring) {
      ++scoringReadSequence.current; setScoringSnapshot(null); setScoringLoadState("error");
    }
    if (reviewsReadSequence.current === session.frames.reviews) {
      ++reviewsReadSequence.current; setReviewsSnapshot(null);
      setReviewsNavigationGeneration(session.generation); setReviewsLoadState("error");
    }
    reviewDecisionAttempt.current = null;
    if (reviewDecisionInFlight.current === session.requestToken) {
      reviewDecisionInFlight.current = null; setReviewSubmitting(false);
    }
    setReviewError(null); setReviewIntentEpoch((epoch) => epoch + 1);
  }, []);

  useEffect(() => {
    const session = reviewDecisionAttempt.current;
    if (!session || session.actor === null || session.tenant === null
      || networkReadSequence.current === session.frames.network
        && scoringReadSequence.current === session.frames.scoring && reviewsReadSequence.current === session.frames.reviews) return;
    let cancelled = false;
    const sequences = [networkReadSequence.current, scoringReadSequence.current, reviewsReadSequence.current];
    const isCurrent = () => !cancelled && navigationLocation.current.mounted && reviewDecisionAttempt.current === session
      && sequences[0] === networkReadSequence.current && sequences[1] === scoringReadSequence.current
      && sequences[2] === reviewsReadSequence.current;
    void readReviewDecisionIdentity(isCurrent).then((identity) => {
      if (isCurrent() && (!identity || identity.actorSubjectId !== session.actor || identity.tenantId !== session.tenant)) {
        invalidateReviewScope(session);
      }
    });
    return () => { cancelled = true; };
  }, [networkSnapshotValue, scoringSnapshotValue, reviewsSnapshotValue, invalidateReviewScope]);


  const [submissionOffer, setSubmissionOffer] = useState<ReviewSubmissionOffer | null>(null);
  const [submissionContext, setSubmissionContext] = useState<ReviewSubmissionContext | null>(null);
  const [submissionBusy, setSubmissionBusy] = useState(false);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submissionCorrelation, setSubmissionCorrelation] = useState<string | null>(null);
  const [submissionConfirmed, setSubmissionConfirmed] = useState<ReviewSubmissionReceipt | null>(null);
  const submissionSession = useRef<{ generation: number; contextKey: string; locationVersion: number;
    observedTenant: string | null; offer: ReviewSubmissionOffer; actor: string | null;
    context: ReviewSubmissionContext | null; attempt: { body: ReviewSubmissionBody; payload: string; key: string; correlation: string } | null } | null>(null);
  const submissionInFlight = useRef(false);
  useEffect(() => {
    submissionSession.current = null;
    setSubmissionOffer(null); setSubmissionContext(null); setSubmissionBusy(false);
    setSubmissionError(null); setSubmissionCorrelation(null); setSubmissionConfirmed(null);
  }, [navigationContextKey, navigationLocationKey]);

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
    if (!allowedTabIndices.includes(tabIndex)) return;
    navigationIntent.current += 1;
    setBusyListingId(null);
    setNavigationError(null);
    const params = candidateWorkflowNavigationParams(tabIndex, searchParams, workflowCandidateId, extraParams);
    const exactCandidate = params.get(CANDIDATE_SELECTION_PARAM);
    setTabOverride({
      from: urlTab,
      fromZoneId: urlCandidateZoneId,
      fromCandidateQuery: JSON.stringify(searchParams.getAll(CANDIDATE_SELECTION_PARAM)),
      fromReviewQuery: JSON.stringify(searchParams.getAll(REVIEW_SELECTION_PARAM)),
      candidateId: exactCandidate,
      reviewId: params.get(REVIEW_SELECTION_PARAM),
      requested: tabIndex,
      candidateZoneId: tabIndex === 2 ? extraParams?.[CANDIDATE_ZONE_PARAM] || null : null,
    });
    // Zone owns the Candidates panel. Exact workflow identity survives only
    // Candidates → SiteScore → Compare → Review; an exit or zone intent clears it.
    if (exactCandidate) params.set("ws", "network");
    const href = buildNetworkTabHref(
      exactCandidate ? "/operator" : pathname,
      tabIndex,
      params,
      typeof window === "undefined" || exactCandidate && window.location.hash.startsWith("#intake/") ? "" : window.location.hash,
    );
    router.push(href, { scroll: false });
  }, [allowedTabIndices, pathname, router, searchParams, urlTab, urlCandidateZoneId, workflowCandidateId]);

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

  // These are read projections, not editable drafts. Keeping a second state
  // copy would retain the previous principal's rows for an extra render.
  const localSiteReviews = siteReviewsEffective;
  const localCandidates = candidates;

  const effectiveLens = activeLens ?? localLens;
  // An explicit valid request is the original selectedHeatZoneId argument; a
  // refused one reads unfiltered without choosing any zone for it.
  const effectiveSelectedId = heatZoneRequestId === undefined
    ? selectedHeatZoneId ?? localSelectedId
    : heatZoneRequestId ?? "";
  const effectiveTrackedIds = trackedHeatZoneIds ?? Array.from(localTrackedIds);
  const trackedSet = useMemo(() => new Set(effectiveTrackedIds), [effectiveTrackedIds]);

  useEffect(() => {
    if (
      heatZoneRequestId === undefined &&
      selectedHeatZoneId === undefined &&
      !localSelectedId &&
      heatZones.length > 0
    ) {
      setLocalSelectedId(heatZones[0].id);
    }
  }, [heatZoneRequestId, heatZones, localSelectedId, selectedHeatZoneId]);

  // Read leases include the observed URL and scope. When either changes,
  // replace the invalidated read; a late response alone cannot restart it.
  useEffect(() => {
    if (!canReadListings) {
      setNetworkSnapshot(null);
      setNetworkLoadState("empty");
      setNetworkLoadFailure(null);
      setNetworkApiError(null);
      return;
    }
    let cancelled = false;
    const generation = navigationContext.current.generation;
    const sequence = ++networkReadSequence.current;
    // Explicit intent never adopts the server's default first zone, and the
    // receipt lets Find Areas confirm a target only from this exact read.
    const explicitHeatZoneRequest = heatZoneRequestId !== undefined;
    // The location epoch is captured once, when this read starts. A late answer
    // keeps the epoch it was issued under, so returning to a byte-identical URL
    // (a newer epoch) never re-matches an earlier snapshot.
    const locationEpoch = navigationLocation.current.version;
    const receipt = heatZoneSnapshotReceipt(generation, navigationLocationKey, locationEpoch);
    async function load() {
      const read = await fetchNetworkSnapshot(effectiveSelectedId || null, effectiveLens, activeRoleId, snapshotReadGuard());
      if ("superseded" in read) return;
      if (sequence !== networkReadSequence.current) return;
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && generation === navigationContext.current.generation && snapshot) {
        setNetworkNavigationGeneration(generation);
        setNetworkSnapshotReceipt(receipt);
        const inspection = inspectNetworkListingsSnapshot(snapshot);
        const authorizedEmpty = inspection === "empty" && snapshot.source === "api" && hasNetworkListingsRenderShape(snapshot);
        const serverSelectedId = snapshot.selectedHeatZoneId;
        if (
          inspection === "ready" &&
          !explicitHeatZoneRequest &&
          !effectiveSelectedId &&
          serverSelectedId &&
          snapshot.heatZones?.some((zone) => zone.id === serverSelectedId)
        ) {
          setLocalSelectedId(serverSelectedId);
        }
        setNetworkSnapshot(
          inspection === "ready"
            || authorizedEmpty
            || fixturesAllowed && hasNetworkListingsRenderShape(snapshot)
            ? snapshot
            : null,
        );
        setNetworkApiError(null);
        setNetworkLoadFailure(null);
        setNetworkLoadState(
          inspection === "ready"
            ? "ready"
            : authorizedEmpty
              ? "empty"
              : fixturesAllowed
                ? "fixture"
                : inspection,
        );
      } else if (!cancelled && generation === navigationContext.current.generation && !read.ok) {
        setNetworkNavigationGeneration(generation);
        setNetworkSnapshotReceipt(receipt);
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
  }, [activeRoleId, canReadListings, effectiveLens, effectiveSelectedId, fixturesAllowed, heatZoneRequestId, navigationContextKey, navigationLocationKey]);

  useEffect(() => {
    if (!canReadScoring) {
      setScoringSnapshot(null);
      setScoringLoadState("empty");
      setScoringLoadFailure(null);
      return;
    }
    let cancelled = false;
    const generation = navigationContext.current.generation;
    const sequence = ++scoringReadSequence.current;
    async function loadScoring() {
      const read = await fetchNetworkScoringSnapshot(activeRoleId, snapshotReadGuard());
      if ("superseded" in read) return;
      if (sequence !== scoringReadSequence.current) return;
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && generation === navigationContext.current.generation && snapshot) {
        setScoringNavigationGeneration(generation);
        setScoringWorkflowProvenance(inspectScoringWorkflowProvenance(snapshot));
        const inspection = inspectNetworkScoringSnapshot(snapshot);
        setScoringSnapshot(
          inspection === "ready" || inspection === "empty" && snapshot.source === "canonical" && hasNetworkScoringRenderShape(snapshot)
            || fixturesAllowed && hasNetworkScoringRenderShape(snapshot)
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
      } else if (!cancelled && generation === navigationContext.current.generation && !read.ok) {
        setScoringSnapshot(null);
        setScoringNavigationGeneration(generation);
        setScoringLoadFailure(read.failure);
        setScoringLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadScoring();
    return () => {
      cancelled = true;
    };
  }, [activeRoleId, canReadScoring, fixturesAllowed, navigationContextKey, navigationLocationKey]);

  useEffect(() => {
    if (!canReadRebalance) {
      setRebalanceSnapshot(null);
      setRebalanceLoadState("empty");
      setRebalanceLoadFailure(null);
      setRebalanceApiError(null);
      return;
    }
    let cancelled = false;
    const generation = navigationContext.current.generation;
    async function loadRebalance() {
      const read = await fetchNetworkRebalanceSnapshot(activeRoleId, snapshotReadGuard());
      if ("superseded" in read) return;
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && generation === navigationContext.current.generation && snapshot) {
        setRebalanceNavigationGeneration(generation);
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
      } else if (!cancelled && generation === navigationContext.current.generation && !read.ok) {
        setRebalanceSnapshot(null);
        setRebalanceNavigationGeneration(generation);
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
  }, [activeRoleId, canReadRebalance, fixturesAllowed, navigationContextKey, navigationLocationKey]);

  const applyReviewsSnapshot = useCallback((snapshot: NetworkReviewsSnapshot, generation: number, sequence: number) => {
    if (!navigationLocation.current.mounted || generation !== navigationContext.current.generation
        || sequence !== reviewsReadSequence.current) return false;
    const inspection = inspectNetworkReviewsSnapshot(snapshot);
    // An authorized empty dedicated read is authoritative too. Do not replace
    // its zero queue with the independently projected network-listings rows.
    setReviewsSnapshot(
      inspection === "ready" || (inspection === "empty" && (snapshot.source === "api" || snapshot.source === "canonical")) || (fixturesAllowed && inspection !== "error")
        ? snapshot
        : null,
    );
    setReviewsNavigationGeneration(generation);
    setReviewsLoadFailure(inspection === "error"
      ? operatorLoadFailureFromError(new Error("Network review read contract is incomplete or conflicting"), "network-reviews")
      : null);
    setReviewsLoadState(
      inspection === "ready" ? "ready"
        : inspection === "empty" && (snapshot.source === "api" || snapshot.source === "canonical") ? "empty"
        : fixturesAllowed ? "fixture" : inspection,
    );
    return inspection === "ready" || inspection === "empty";
  }, [fixturesAllowed]);

  useEffect(() => {
    if (!canReadReviews) {
      setReviewsSnapshot(null);
      setReviewsLoadState("empty");
      setReviewsLoadFailure(null);
      return;
    }
    let cancelled = false;
    const generation = navigationContext.current.generation;
    const sequence = ++reviewsReadSequence.current;
    setReviewError(null);
    setReviewSubmitting(false);
    setReviewsLoadState(fixturesAllowed ? "fixture" : "loading");
    async function loadReviews() {
      const read = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders, snapshotReadGuard());
      if ("superseded" in read) return;
      const snapshot = read.ok ? read.snapshot : null;
      if (!cancelled && snapshot) {
        applyReviewsSnapshot(snapshot, generation, sequence);
      } else if (!cancelled && navigationLocation.current.mounted && generation === navigationContext.current.generation
          && sequence === reviewsReadSequence.current && !read.ok) {
        setReviewsSnapshot(null);
        setReviewsNavigationGeneration(generation);
        setReviewsLoadFailure(read.failure);
        setReviewsLoadState(fixturesAllowed ? "fixture" : "error");
      }
    }
    loadReviews();
    return () => {
      cancelled = true;
    };
  }, [applyReviewsSnapshot, canReadReviews, fixturesAllowed, reviewIdentity, navigationLocationKey]);

  // Decide using the current review identity and reload the authoritative queue.
  // Returns false so the dialog
  // stays open when the server rejects the decision (policy 422 / role 403 /
  // conflict 409). A non-deciding role reaching this path carries its own
  // non-approving headers, so the API fails closed with 403.
  async function submitReviewDecision(
    reviewId: string,
    action: ReviewDecisionAction,
    form: ReviewDecisionForm,
  ): Promise<boolean> {
    if (reviewsSnapshot?.source === "canonical") return submitBoundReviewDecision(reviewId, action, form);
    if (!fixturesAllowed) return false; // Production never enters the fixture decision transport.
    const generation = navigationContext.current.generation;
    if (!navigationLocation.current.mounted || reviewIdentity.observedContextKey !== navigationContext.current.key) return false;
    if (!reviewIdentity.canDecide) return false;
    if (!fixturesAllowed && !reviewsSnapshot?.reviews.some((review) => review.id === reviewId && isReviewPending(review) && hasRecordedReviewReport(review))) return false;
    const sequence = ++reviewsReadSequence.current;
    const isCurrent = () => navigationLocation.current.mounted && generation === navigationContext.current.generation
      && sequence === reviewsReadSequence.current;
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
      if (!isCurrent()) return false;
      if (!response.ok) {
        setReviewError(`review decision failed (${response.status})`);
        return false;
      }
      const read = await fetchNetworkReviewsSnapshot(reviewIdentity.readHeaders, snapshotReadGuard());
      if ("superseded" in read) return false;
      if (!isCurrent()) return false;
      if (read.ok) {
        return applyReviewsSnapshot(read.snapshot, generation, sequence);
      } else {
        setReviewsSnapshot(null);
        setReviewsNavigationGeneration(generation);
        setReviewsLoadFailure(read.failure);
        setReviewsLoadState(fixturesAllowed ? "fixture" : "error");
      }
      return false;
    } catch {
      if (isCurrent()) setReviewError("review decision failed");
      return false;
    } finally {
      if (isCurrent()) setReviewSubmitting(false);
    }
  }

  async function submitBoundReviewDecision(reviewId: string, action: ReviewDecisionAction, form: ReviewDecisionForm | null): Promise<boolean> {
    if (reviewDecisionInFlight.current || !navigationLocation.current.mounted || !reviewIdentity.canDecide) return false;
    const capturedScoringSequence = scoringReadSequence.current;
    const snapshot = reviewsSnapshot;
    const rows = snapshot?.reviews.filter((row) => row.id === reviewId) ?? [];
    const row = rows[0];
    if (!snapshot || rows.length !== 1 || !row || !isReviewPending(row) || !hasRecordedReviewReport(row)) return false;
    let attempt = reviewDecisionAttempt.current;
    if (attempt?.body && attempt.reviewId === reviewId && attempt.unknownOutcome && form !== null
      && JSON.stringify(boundReviewDecisionBody(attempt.context!, action, form)) !== attempt.payload) {
      setReviewError(`原核決結果尚未確認；請保留原原因、動作與風險確認，以同一請求核對。${attempt.correlation}`);
      return false;
    }
    const sameForm = attempt?.context && attempt.reviewId === reviewId && (!attempt.body || form === null || form !== null
      && JSON.stringify(boundReviewDecisionBody(attempt.context, action, form)) === attempt.payload);
    if (!sameForm) {
      attempt = { reviewId, generation: navigationContext.current.generation, contextKey: navigationContext.current.key,
        locationVersion: navigationLocation.current.version, observedTenant: window.sessionStorage.getItem("oday.operator.tenant"),
        actor: attempt?.reviewId === reviewId ? attempt.actor : null,
        tenant: attempt?.reviewId === reviewId ? attempt.tenant : null, context: null, body: null, payload: null,
        frames: { network: networkReadSequence.current, scoring: capturedScoringSequence, reviews: reviewsReadSequence.current }, requestToken: null,
        key: crypto.randomUUID(), correlation: crypto.randomUUID(), unknownOutcome: false };
      reviewDecisionAttempt.current = attempt;
    }
    const session = attempt!;
    const requestToken = Symbol("native-review-request");
    session.frames = { network: networkReadSequence.current, scoring: capturedScoringSequence, reviews: reviewsReadSequence.current };
    session.requestToken = requestToken;
    const ownsAttempt = () => reviewDecisionAttempt.current === session && navigationLocation.current.mounted;
    const scopeError = "登入身分或審核範圍已變更；未確認核決結果，請重新讀取。";
    const invalidateOwnedScope = () => {
      invalidateReviewScope(session);
    };
    const current = () => reviewDecisionAttempt.current === session && navigationLocation.current.mounted
      && session.generation === navigationContext.current.generation && session.contextKey === navigationContext.current.key
      && session.locationVersion === navigationLocation.current.version
      && session.observedTenant === window.sessionStorage.getItem("oday.operator.tenant");
    const subject = async () => {
      const identity = await readReviewDecisionIdentity(current);
      if (!identity || !current() || session.actor !== null && session.actor !== identity.actorSubjectId
        || session.tenant !== null && session.tenant !== identity.tenantId
        || session.context && (session.context.actorSubjectId !== identity.actorSubjectId || session.context.tenantId !== identity.tenantId)
        || session.body && (session.body.expectedActorSubjectId !== identity.actorSubjectId || session.body.expectedTenantId !== identity.tenantId)) {
        invalidateOwnedScope();
        throw new Error(scopeError);
      }
      session.actor = identity.actorSubjectId; session.tenant = identity.tenantId; return identity.actorSubjectId;
    };
    const read = async (path: string, init?: RequestInit, ownsRead: () => boolean = current): Promise<unknown> => {
      await subject();
      if (!ownsRead()) throw new Error(`正式資料讀取已被新的讀取取代；${session.correlation}`);
      const response = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout(20_000), ...init });
      await subject();
      if (!ownsRead()) throw new Error(`正式資料讀取已被新的讀取取代；${session.correlation}`);
      const value: unknown = await response.json();
      await subject();
      if (!ownsRead()) throw new Error(`正式資料讀取已被新的讀取取代；${session.correlation}`);
      if (init?.method === "POST" && response.status >= 400 && response.status < 500) session.unknownOutcome = false;
      if (!response.ok) throw new Error(`審核服務拒絕或未完成讀取（HTTP ${response.status}）；${session.correlation}`);
      return value;
    };
    reviewDecisionInFlight.current = requestToken; setReviewSubmitting(true); setReviewError(null);
    try {
      await subject();
      if (!session.body) {
        const context = await read(`/api/v1/operator/network-reviews/${encodeURIComponent(reviewId)}/decision-context?${new URLSearchParams({ candidateId: row.candidateId })}`);
        if (hasReviewDecisionContext(context) && (context.actorSubjectId !== session.actor
          || context.tenantId !== session.tenant || session.observedTenant && context.tenantId !== session.observedTenant)) {
          invalidateOwnedScope();
          throw new Error(scopeError);
        }
        if (!hasReviewDecisionContext(context) || !reviewDecisionContextMatches(context, snapshot, row)) {
          throw new Error("目前登入者、候選點、凍結報告或審核版本尚未唯一核對；未送出決策。");
        }
        if (session.context && JSON.stringify(session.context.target) !== JSON.stringify(context.target)) {
          throw new Error("審核內容已變更；保留表單，請重新讀取並確認原核決結果。");
        }
        session.context = context;
        if (form === null) return true;
        session.body = boundReviewDecisionBody(context, action, form); session.payload = JSON.stringify(session.body);
      }
      if (form === null) return session.context !== null;
      const body = session.body;
      // Unknown outcomes retain the original request/version/key for an authorized replay.
      session.unknownOutcome = true;
      const receipt = await read(`/api/v1/operator/network-reviews/${encodeURIComponent(reviewId)}/decision`, {
        method: "POST", headers: { ...reviewIdentity.decideHeaders, "Content-Type": "application/json",
          "If-Match": `W/"${body.expectedDecisionVersion}"`, "If-Actor-Match": body.expectedActorSubjectId,
          "Idempotency-Key": session.key, "X-Correlation-Id": session.correlation }, body: session.payload!,
      });
      if (!reviewDecisionReceiptMatches(receipt, reviewId, body, session.key, session.correlation)) {
        throw new Error(`核決回覆未核對同一份 Decision、Approval 與處理人員；${session.correlation}`);
      }
      const networkSequence = ++networkReadSequence.current;
      const reviewsSequence = ++reviewsReadSequence.current;
      session.frames.network = networkSequence; session.frames.reviews = reviewsSequence;
      const scoringSequence = capturedScoringSequence;
      const freshCurrent = () => current() && networkSequence === networkReadSequence.current
        && reviewsSequence === reviewsReadSequence.current && scoringSequence === scoringReadSequence.current;
      const params = new URLSearchParams({ lens: effectiveLens });
      if (effectiveSelectedId) params.set("selectedHeatZoneId", effectiveSelectedId);
      const network = await read(`/api/v1/operator/network-listings?${params}`, { headers: NETWORK_OPERATOR_HEADERS }, freshCurrent);
      if (!freshCurrent()) throw new Error(`候選點讀取已被新的範圍或資料讀取取代；${session.correlation}`);
      const confirmed = await read("/api/v1/operator/network-reviews", { headers: reviewIdentity.readHeaders }, freshCurrent);
      if (!freshCurrent() || scoringNavigationGeneration !== session.generation || scoringLoadState !== "ready"
        || !reviewDecisionFreshGraphMatches(network, scoringSnapshot, confirmed, reviewId, body, session.generation)
        || inspectNetworkListingsSnapshot(network as NetworkListingsSnapshot) !== "ready") {
        throw new Error(`正式 Decision、Approval、凍結報告與決策內容尚未吻合；${session.correlation}`);
      }
      await subject();
      if (!freshCurrent()) return false;
      // Publish the two coherent, authorized reads in one synchronous batch.
      setNetworkSnapshot(network as NetworkListingsSnapshot); setNetworkNavigationGeneration(session.generation);
      setNetworkLoadState("ready"); setNetworkLoadFailure(null); setNetworkApiError(null);
      if (!applyReviewsSnapshot(confirmed as NetworkReviewsSnapshot, session.generation, reviewsSequence)) return false;
      session.unknownOutcome = false;
      await onReviewDecisionConfirmed?.();
      await subject();
      if (!current()) return false;
      reviewDecisionAttempt.current = null;
      return true;
    } catch (error) {
      if (current()) setReviewError(error instanceof Error ? error.message : `核決結果尚未確認；${session.correlation}`);
      else invalidateOwnedScope();
      return false;
    } finally {
      if (reviewDecisionInFlight.current === requestToken) {
        reviewDecisionInFlight.current = null;
        if (ownsAttempt() || reviewDecisionAttempt.current === null) setReviewSubmitting(false);
      }
    }
  }

  const reloadScoringSnapshot = useCallback(async () => {
    const generation = navigationContext.current.generation;
    const sequence = ++scoringReadSequence.current;
    const read = await fetchNetworkScoringSnapshot(activeRoleId, snapshotReadGuard());
    if ("superseded" in read) return;
    if (!navigationLocation.current.mounted || sequence !== scoringReadSequence.current
      || navigationContextKey !== navigationContext.current.key || generation !== navigationContext.current.generation) return;
    setScoringNavigationGeneration(generation);
    if (read.ok) {
      setScoringWorkflowProvenance(inspectScoringWorkflowProvenance(read.snapshot));
      const inspection = inspectNetworkScoringSnapshot(read.snapshot);
      setScoringSnapshot(inspection === "ready"
        || inspection === "empty" && read.snapshot.source === "canonical" && hasNetworkScoringRenderShape(read.snapshot)
        || fixturesAllowed && hasNetworkScoringRenderShape(read.snapshot) ? read.snapshot : null);
      setScoringLoadFailure(null);
      setScoringLoadState(inspection);
    } else {
      setScoringSnapshot(null);
      setScoringLoadFailure(read.failure);
      setScoringLoadState("error");
    }
  }, [activeRoleId, fixturesAllowed, navigationContextKey]);

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

  async function readCanonicalScoreSubject(isCurrent: () => boolean): Promise<string | null> {
    try {
      const response = await fetch("/auth/session", { cache: "no-store", headers: { accept: "application/json" } });
      if (!isCurrent()) return null;
      const session: unknown = await response.json();
      if (!isCurrent()) return null;
      return response.status === 200 && hasCanonicalScoreSession(session) ? session.subject : null;
    } catch { return null; }
  }

  async function runSiteScore(candidateId: string, reason?: string) {
    if (scoringSnapshot?.source !== "canonical") {
      if (fixturesAllowed) await postScoringAction(`candidates/${candidateId}/score`, {}, candidateId, `r4-006-score-${candidateId}`);
      return;
    }
    const admissionCandidates = scoringSnapshot.candidates.filter((candidate) => candidate.id === candidateId);
    if (scoringLoadState !== "ready" || scoringNavigationGeneration !== navigationContext.current.generation
      || candidateId !== workflowCandidateId || admissionCandidates.length !== 1 || !hasVerifiedCurrentScoreAdmission(admissionCandidates[0])) {
      setScoreExecutionError("目前資料、來源、PIT 或模型的再評分資格尚未確認；已保存報告仍可查閱。");
      return;
    }
    const generation = navigationContext.current.generation;
    const locationVersion = navigationLocation.current.version;
    const sequence = ++executionSequence.current;
    const isCurrent = () => navigationLocation.current.mounted && generation === navigationContext.current.generation
      && locationVersion === navigationLocation.current.version && sequence === executionSequence.current;
    const selectedCards = scoringSnapshot.scorecards.filter((card) => card.id === candidateId);
    if (selectedCards.length !== 1 || !selectedCards[0].reportId || !selectedCards[0].reportVersion || !reason?.trim()) {
      setScoreExecutionError("請確認候選點已保存報告並填寫重新評分原因。");
      return;
    }
    const retry = scoreExecution && ["FAILED", "CANCELLED"].includes(scoreExecution.status)
      && scoreExecution.candidateSiteId === candidateId && scoreExecution.jobVersion !== null ? scoreExecution : null;
    setBusyCandidateId(candidateId);
    setScoreExecutionError(null);
    try {
      // Even fixture-enabled callers need the actual session for a canonical command.
      const actorSubjectId = await readCanonicalScoreSubject(isCurrent);
      if (!isCurrent()) return;
      if (!actorSubjectId || executionSubjectIntent.current && executionSubjectIntent.current !== actorSubjectId
        || executionKey.current && executionKey.current.actorSubjectId !== actorSubjectId) {
        setScoreExecutionError("目前登入身分無法核對或已變更，請重新載入後確認；已保存報告仍可查閱。");
        return;
      }
      executionSubjectIntent.current = actorSubjectId;
      const contextResponse = await fetch(`/api/v1/operator/network-scoring/candidates/${encodeURIComponent(candidateId)}/execution-context`,
        { cache: "no-store", headers: NETWORK_OPERATOR_HEADERS });
      if (!isCurrent()) return;
      const context: unknown = await contextResponse.json();
      if (!isCurrent()) return;
      if (!contextResponse.ok || !hasScoreExecutionContext(context) || context.candidateSiteId !== candidateId
        || context.reportId !== selectedCards[0].reportId || context.reportVersion !== selectedCards[0].reportVersion
        || executionKey.current && executionKey.current.tenantId !== context.tenantId) {
        setScoreExecutionError("目前報告、租戶或工作資格已變更，請重新載入後確認。");
        return;
      }
      if (retry) {
        const current = context.activeExecutionRequest;
        if (!current || current.executionRequestId !== retry.executionRequestId || current.jobId !== retry.jobId
          || current.status !== retry.status || current.actualVersion !== retry.actualVersion || current.jobVersion !== retry.jobVersion
          || retry.baseReportId !== context.reportId || retry.baseReportVersion !== context.reportVersion) {
          setScoreExecutionError("重試資格或工作版本未確認；請重新載入後確認。已保存報告仍可查閱。");
          return;
        }
      }
      // The context read may have awaited a session rotation. Do not adopt its new actor.
      const actorBeforePost = await readCanonicalScoreSubject(isCurrent);
      if (!isCurrent()) return;
      if (actorBeforePost !== actorSubjectId) {
        setScoreExecutionError("目前登入身分無法核對或已變更，請重新載入後確認；已保存報告仍可查閱。");
        return;
      }
      const operation = retry ? "retry" : "create";
      const body = retry
        ? { expectedVersion: retry.actualVersion, expectedJobVersion: retry.jobVersion, reason,
          expectedActorSubjectId: actorSubjectId, expectedTenantId: context.tenantId }
        : { expectedReportId: context.reportId, expectedReportVersion: context.reportVersion, expectedJobVersion: context.jobVersion, reason,
          expectedActorSubjectId: actorSubjectId, expectedTenantId: context.tenantId };
      const payload = JSON.stringify({ candidateId, requestId: retry?.executionRequestId, operation, ...body });
      if (executionKey.current?.payload !== payload) executionKey.current = {
        payload, key: crypto.randomUUID(), correlation: crypto.randomUUID(), actorSubjectId, tenantId: context.tenantId,
      };
      const key = executionKey.current;
      const path = retry ? `execution-requests/${encodeURIComponent(retry.executionRequestId)}/retry` : `candidates/${encodeURIComponent(candidateId)}/score`;
      const response = await fetch(`/api/v1/operator/network-scoring/${path}`, {
        method: "POST", headers: { "Content-Type": "application/json", ...NETWORK_OPERATOR_HEADERS,
          "Idempotency-Key": key.key, "X-Correlation-Id": key.correlation, "If-Actor-Match": key.actorSubjectId }, body: JSON.stringify(body),
      });
      if (!isCurrent()) return;
      const value: unknown = await response.json();
      if (!isCurrent()) return;
      const actorAfter = await readCanonicalScoreSubject(isCurrent);
      if (!isCurrent()) return;
      if (actorAfter !== key.actorSubjectId) {
        setScoreExecutionError("目前登入身分無法核對或已變更，請重新載入後確認；已保存報告仍可查閱。");
        return;
      }
      if (response.status !== 202 || !readObject(value) || !hasScoreExecutionReceipt(value.executionRequest)
        || value.executionRequest.candidateSiteId !== candidateId || value.executionRequest.baseReportId !== context.reportId
        || value.executionRequest.baseReportVersion !== context.reportVersion
        || retry && (value.executionRequest.executionRequestId !== retry.executionRequestId || value.executionRequest.jobId !== retry.jobId)
        || !scoreExecutionCommandReceiptMatches(value.commandReceipt,
          { operation, actorSubjectId: key.actorSubjectId, tenantId: key.tenantId, idempotencyKey: key.key, correlationId: key.correlation }, value.executionRequest)) {
        setScoreExecutionError(`${retry ? "重試資格或工作版本" : "重新評分請求"}未確認（${response.status}）。已保存報告仍可查閱。`);
        return;
      }
      setScoreExecution(value.executionRequest);
    } catch {
      if (isCurrent()) setScoreExecutionError("重新評分請求無法確認；請重新載入後查看工作狀態。");
    } finally {
      if (isCurrent()) setBusyCandidateId(null);
    }
  }

  useEffect(() => {
    if (!scoreExecution || ["SUCCEEDED", "FAILED", "DEAD_LETTER", "CANCELLED"].includes(scoreExecution.status)) return;
    const generation = navigationContext.current.generation;
    const locationVersion = navigationLocation.current.version;
    const sequence = executionSequence.current;
    const requestId = scoreExecution.executionRequestId;
    const candidateId = scoreExecution.candidateSiteId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const isCurrent = () => !cancelled && navigationLocation.current.mounted && generation === navigationContext.current.generation
      && locationVersion === navigationLocation.current.version && sequence === executionSequence.current;
    async function poll() {
      try {
        const response = await fetch(`/api/v1/operator/network-scoring/execution-requests/${encodeURIComponent(requestId)}`, { headers: NETWORK_OPERATOR_HEADERS });
        if (!isCurrent()) return;
        const value: unknown = await response.json();
        if (!isCurrent()) return;
        if (!response.ok || !hasScoreExecutionReceipt(value) || value.executionRequestId !== requestId || value.candidateSiteId !== candidateId) {
          setScoreExecutionError("工作狀態無法核對，已保存報告仍可查閱。");
          return;
        }
        setScoreExecution(value);
        if (value.status === "SUCCEEDED") {
          executionKey.current = null;
          executionSubjectIntent.current = null;
          await reloadScoringSnapshot();
        } else if (!["FAILED", "DEAD_LETTER", "CANCELLED"].includes(value.status)) {
          timer = setTimeout(poll, 1000);
        }
      } catch {
        if (isCurrent()) setScoreExecutionError("工作狀態讀取失敗，已保存報告仍可查閱。");
      }
    }
    timer = setTimeout(poll, 1000);
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [scoreExecution, navigationContextKey, navigationLocationKey, NETWORK_OPERATOR_HEADERS, reloadScoringSnapshot]);

  useEffect(() => {
    if (activeTab !== 3 || !workflowCandidateId || scoringSnapshot?.source !== "canonical" || scoringLoadState !== "ready") return;
    const generation = navigationContext.current.generation;
    const locationVersion = navigationLocation.current.version;
    let cancelled = false;
    const sequence = executionSequence.current;
    async function restore() {
      try {
        const response = await fetch(`/api/v1/operator/network-scoring/candidates/${encodeURIComponent(workflowCandidateId!)}/execution-context`, { headers: NETWORK_OPERATOR_HEADERS });
        if (cancelled || !navigationLocation.current.mounted || sequence !== executionSequence.current || generation !== navigationContext.current.generation || locationVersion !== navigationLocation.current.version) return;
        const value: unknown = await response.json();
        if (cancelled || !navigationLocation.current.mounted || sequence !== executionSequence.current || generation !== navigationContext.current.generation || locationVersion !== navigationLocation.current.version) return;
        if (response.ok && hasScoreExecutionContext(value) && value.candidateSiteId === workflowCandidateId && value.activeExecutionRequest) {
          setScoreExecution(value.activeExecutionRequest);
        }
      } catch { /* An unverified context cannot change the historical report. */ }
    }
    restore();
    return () => { cancelled = true; };
  }, [activeTab, workflowCandidateId, scoringSnapshot?.source, scoringLoadState, navigationContextKey, navigationLocationKey, NETWORK_OPERATOR_HEADERS]);

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
    const generation = navigationContext.current.generation;
    const read = await fetchNetworkRebalanceSnapshot(activeRoleId, snapshotReadGuard());
    if ("superseded" in read || generation !== navigationContext.current.generation) return;
    if (read.ok) {
      setRebalanceSnapshot(read.snapshot);
      setRebalanceNavigationGeneration(generation);
      setRebalanceApiError(null);
    }
  }

  const [surveyRequest, setSurveyRequest] = useState<{ target: SurveyTarget; api: ReturnType<typeof createSurveyAssignmentApi>; tab: number } | null>(null);
  const [listingSearchRequest, setListingSearchRequest] = useState<{
    zoneId: string; zoneLabel: string; guard: NetworkReadSessionGuard; contextKey: string; locationVersion: number;
  } | null>(null);
  const [areaPlanningRequest, setAreaPlanningRequest] = useState<{
    zoneId: string; zoneLabel: string; guard: NetworkReadSessionGuard; contextKey: string; locationVersion: number;
  } | null>(null);
  const surveyBinding = useMemo(() => {
    const context = navigationContext.current;
    const locationVersion = navigationLocation.current.version;
    const current = () => context === navigationContext.current
      && locationVersion === navigationLocation.current.version && navigationLocation.current.mounted;
    const capture = () => current() ? snapshotReadGuard() : async () => false;
    const api = createSurveyAssignmentApi(NETWORK_OPERATOR_HEADERS, capture);
    api.work = createSurveyEvidenceApi(NETWORK_OPERATOR_HEADERS, capture);
    return { api, open(target: SurveyTarget, tab: number) {
      if (current() && !fixturesAllowed) setSurveyRequest({ target, api, tab });
    } };
  }, [NETWORK_OPERATOR_HEADERS, navigationContextKey, navigationLocationKey, fixturesAllowed]);

  const netPlanBuilderApi = useMemo(() => {
    const context = navigationContext.current;
    const locationVersion = navigationLocation.current.version;
    const current = () => context === navigationContext.current
      && locationVersion === navigationLocation.current.version && navigationLocation.current.mounted;
    return createNetPlanBuilderApi(NETWORK_OPERATOR_HEADERS,
      () => current() ? snapshotReadGuard() : async () => false,
      async () => { if (current()) await reloadRebalanceSnapshot(); });
  }, [NETWORK_OPERATOR_HEADERS, navigationContextKey, navigationLocationKey]);

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
        fixturesAllowed,
        candidates: localCandidates,
        heatZones,
        listings: listingsEffective,
        listingSources: listingSourcesEffective,
        rebalanceStores: rebalanceStoresEffective,
        selectedHeatZoneId: effectiveSelectedId,
        siteReviews: localSiteReviews,
      }),
    [fixturesAllowed, localCandidates, effectiveLens, effectiveSelectedId, heatZones, listingsEffective, listingSourcesEffective, rebalanceStoresEffective, localSiteReviews],
  );

  const heatZoneTarget = resolveHeatZoneRequestedTarget({
    selection: heatZoneSelection,
    controlledSelectedId: selectedHeatZoneId,
    snapshot: networkSnapshot,
    loadState: networkLoadState,
    snapshotReceipt: networkSnapshotReceipt,
    currentReceipt: heatZoneSnapshotReceipt(navigationContext.current.generation, navigationLocationKey, navigationLocation.current.version),
  });
  const confirmedHeatZoneId = heatZoneTarget.state === "confirmed" ? heatZoneTarget.id : null;
  // The view model falls back to HZ-01 or the first row. Under explicit intent
  // only the exactly confirmed zone may be presented or acted on.
  const selectedZone = heatZoneTarget.state === "none"
    || confirmedHeatZoneId !== null && viewModel.selectedZone?.id === confirmedHeatZoneId
    ? viewModel.selectedZone : null;
  useEffect(() => {
    // A confirmed target becomes the ordinary local selection, matching a
    // user's own click, so leaving Find Areas keeps the same verified zone.
    if (confirmedHeatZoneId !== null) setLocalSelectedId(confirmedHeatZoneId);
  }, [confirmedHeatZoneId]);
  // Carry an area only when the intake options and the visible selection are
  // verified by the same current read. An earlier native map binding can have
  // different IDs while the scoped aggregate is still arriving.
  const canAddListingFromSelectedZone = Boolean(
    canPerformIntake("submit", activeRoleId) && selectedZone
    && networkLoadState === "ready" && networkSnapshot?.source === "api"
    && networkSnapshot.heatZoneAvailability?.state !== "unavailable"
    && networkSnapshot.heatZones?.some(zone => zone.id === selectedZone.id),
  );
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
    ? displayedReviews.filter(isReviewPending).length
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
    // With an explicit URL identity, the selected row must match reload and
    // Back/Forward: both identities move together through the existing tab
    // navigation helper. Ordinary visits keep their original local selection.
    if (heatZoneSelection.requested && activeTab === 0 && zone.id !== confirmedHeatZoneId) {
      changeActiveTab(0, heatZoneSelectionParams(zone.id));
    }
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
      changeActiveTab(1, { heatZoneId: selectedZone.id, page: "1" });
    }
  }

  /**
   * Opens the canonical add-from-URL dialog on Listing Radar (dialog=add), so
   * the intake keeps its single dialog and permission checks. The radar is
   * explicitly scoped to this HeatZone even after the inbox was set to all areas.
   */
  function addListingFromUrl() {
    if (canAddListingFromSelectedZone && selectedZone) {
      changeActiveTab(1, { dialog: "add", heatZoneId: selectedZone.id, page: "1" });
    }
  }

  async function reloadNetworkSnapshot() {
    const generation = navigationContext.current.generation;
    const sequence = ++networkReadSequence.current;
    const read = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens, activeRoleId, snapshotReadGuard());
    if ("superseded" in read) return;
    if (!navigationLocation.current.mounted || sequence !== networkReadSequence.current
      || navigationContextKey !== navigationContext.current.key || generation !== navigationContext.current.generation) return;
    setNetworkNavigationGeneration(generation);
    if (read.ok) {
      const inspection = inspectNetworkListingsSnapshot(read.snapshot);
      const authorizedEmpty = inspection === "empty" && read.snapshot.source === "api" && hasNetworkListingsRenderShape(read.snapshot);
      setNetworkSnapshot(inspection === "ready" || authorizedEmpty || fixturesAllowed && hasNetworkListingsRenderShape(read.snapshot) ? read.snapshot : null);
      setNetworkLoadState(inspection);
      setNetworkApiError(null);
      setNetworkLoadFailure(null);
    } else {
      setNetworkSnapshot(null);
      setNetworkLoadFailure(read.failure);
      setNetworkLoadState("error");
    }
  }

  const navigationByListing = new Map((networkSnapshot?.listings ?? []).map((listing) => [
    listing.id,
    resolveListingNavigation(listing.id, networkSnapshot,
      networkLoadState === "ready" && networkNavigationGeneration === navigationContext.current.generation),
  ]));

  async function navigateListing(listingId: string, target: ListingNavigationTarget) {
    if (navigationContextKey !== navigationContext.current.key) return;
    const generation = navigationContext.current.generation;
    const intent = ++navigationIntent.current;
    const locationVersion = navigationLocation.current.version;
    const networkSequence = ++networkReadSequence.current;
    const scoringSequence = target.kind === "candidate" ? ++scoringReadSequence.current : null;
    const isCurrentIntent = () => navigationLocation.current.mounted
      && locationVersion === navigationLocation.current.version
      && generation === navigationContext.current.generation && intent === navigationIntent.current;
    setBusyListingId(listingId);
    setNavigationError(null);
    try {
      // These are reads under the current authenticated principal. Cached
      // relations are previews, not permission to bypass destination reads.
      const [read, scoringRead] = await Promise.all([
        fetchNetworkSnapshot(effectiveSelectedId || null, effectiveLens, activeRoleId, snapshotReadGuard()),
        target.kind === "candidate" ? fetchNetworkScoringSnapshot(activeRoleId, snapshotReadGuard()) : Promise.resolve(null),
      ]);
      if ("superseded" in read || scoringRead && "superseded" in scoringRead) return;
      if (!isCurrentIntent() || networkSequence !== networkReadSequence.current
        || scoringSequence !== null && scoringSequence !== scoringReadSequence.current) return;
      if (!read.ok) {
        setNetworkSnapshot(null);
        setNetworkNavigationGeneration(generation);
        setNetworkLoadFailure(read.failure);
        setNetworkLoadState("error");
        setNavigationError("無法重新讀取物件關聯，尚未導覽。");
        return;
      }
      const inspection = inspectNetworkListingsSnapshot(read.snapshot);
      setNetworkLoadFailure(null);
      setNetworkApiError(null);
      setNetworkSnapshot(inspection === "ready" ? read.snapshot : null);
      setNetworkLoadState(inspection);
      setNetworkNavigationGeneration(generation);
      const navigation = resolveListingNavigation(listingId, read.snapshot, inspection === "ready");
      if (!navigationIncludesTarget(navigation, target)) {
        setNavigationError("關聯已變更或目前不可存取，請重新選擇。");
        return;
      }
      if (target.kind === "candidate") {
        if (scoringRead?.ok) setScoringWorkflowProvenance(inspectScoringWorkflowProvenance(scoringRead.snapshot));
        if (!scoringRead?.ok || inspectNetworkScoringSnapshot(scoringRead.snapshot) !== "ready"
          || scoringRead.snapshot.candidates.filter((row) => row.id === target.id).length !== 1
          || !scoringRead.snapshot.candidates.some((row) => row.id === target.id && row.listingId === listingId)) {
          setScoringSnapshot(null);
          setScoringNavigationGeneration(generation);
          if (scoringRead && !scoringRead.ok) setScoringLoadFailure(scoringRead.failure);
          setScoringLoadState(scoringRead && !scoringRead.ok ? "error" : "empty");
          setNavigationError("指定候選點目前無法讀取，尚未導覽。");
          return;
        }
        setScoringSnapshot(scoringRead.snapshot);
        setScoringLoadFailure(null);
        setScoringLoadState("ready");
        setScoringNavigationGeneration(generation);
        changeActiveTab(2, { [CANDIDATE_SELECTION_PARAM]: target.id });
      } else {
        const params = new URLSearchParams(searchParams.toString());
        for (const key of [CANDIDATE_SELECTION_PARAM, CANDIDATE_ZONE_PARAM, "selected", "dialog", "section", "field", "decision", "receipt", "compare"]) params.delete(key);
        router.push(buildDurableDetailHref(target.id, parseUrlState(params), params), { scroll: false });
      }
    } catch (error) {
      if (isCurrentIntent() && networkSequence === networkReadSequence.current
        && (scoringSequence === null || scoringSequence === scoringReadSequence.current)) {
        const failure = operatorLoadFailureFromError(error, "network-listings");
        setNetworkSnapshot(null);
        setNetworkNavigationGeneration(generation);
        setNetworkLoadFailure(failure);
        setNetworkLoadState("error");
        setNetworkApiError(failure.technicalDetail ?? "network-listings read failed");
        if (target.kind === "candidate") {
          setScoringSnapshot(null);
          setScoringNavigationGeneration(generation);
          setScoringLoadFailure(operatorLoadFailureFromError(error, "network-scoring"));
          setScoringLoadState("error");
        }
        setNavigationError("關聯讀取失敗，尚未導覽。請重新載入後再試。");
      }
    } finally {
      if (isCurrentIntent()) setBusyListingId(null);
    }
  }

  const listingWriteContext = {
    ready: networkLoadState === "ready" || fixturesAllowed && networkLoadState === "fixture",
    fixturesAllowed, candidates,
  };

  function openListingWrite(listingId: string, kind: "merge" | "archive", targetId?: string) {
    if (listingWriteInFlight.current || listingWriteSession.current) return;
    const action = resolveListingAction(listingId, listingsEffective, activeRoleId, listingWriteContext);
    if (!action || action.kind !== kind || kind === "merge" && action.targetListingId !== targetId) return;
    listingWriteSession.current = { action, contextKey: navigationContext.current.key,
      generation: navigationContext.current.generation, locationVersion: navigationLocation.current.version };
    setMergeError(null); setArchiveError(null);
    if (kind === "merge" && action.targetListingId) {
      setArchiveRequest(null);
      setMergeRequest({ sourceListingId: listingId, targetListingId: action.targetListingId,
        sourceLabel: action.sourceLabel, targetLabel: action.targetLabel,
        ifMatch: action.ifMatch, targetIfMatch: action.targetIfMatch,
        idempotencyKey: newMergeIdempotencyKey(listingId, action.targetListingId) });
    } else {
      setMergeRequest(null);
      setArchiveRequest({ listingId, label: action.sourceLabel, ifMatch: action.ifMatch,
        idempotencyKey: newArchiveIdempotencyKey(listingId) });
    }
  }

  function mergeListing(sourceId: string, targetId: string) { openListingWrite(sourceId, "merge", targetId); }
  function archiveListing(listingId: string) { openListingWrite(listingId, "archive"); }

  async function readListingWriteSubject(): Promise<string | null> {
    if (fixturesAllowed) return operatorSubjectId(activeRoleId) || null;
    try {
      const response = await fetch("/auth/session", { cache: "no-store", headers: { accept: "application/json" } });
      if (!response.ok) return null;
      const session: unknown = await response.json();
      return readObject(session) && typeof session.subject === "string" && session.subject.trim() ? session.subject : null;
    } catch { return null; }
  }

  async function submitListingWrite(kind: "merge" | "archive", form: ListingMergeForm | string) {
    const session = listingWriteSession.current;
    const request = kind === "merge" ? mergeRequest : archiveRequest;
    if (!session || !request || session.action.kind !== kind || listingWriteInFlight.current) return;
    const current = () => listingWriteSession.current === session && navigationLocation.current.mounted
      && session.contextKey === navigationContext.current.key && session.generation === navigationContext.current.generation
      && session.locationVersion === navigationLocation.current.version
      && session.contextKey === JSON.stringify([activeRoleId, operatorSubjectId(activeRoleId), allowedTabIndices,
        typeof window === "undefined" ? "" : window.sessionStorage.getItem("oday.operator.tenant")]);
    if (!current()) return;
    const setError = kind === "merge" ? setMergeError : setArchiveError;
    const setBusy = kind === "merge" ? setMergeBusy : setArchiveBusy;
    const client = buildListingsClient(activeRoleId);
    if (!client) { setError(missingListingsClientError()); return; }
    // The ref closes the rapid-double-submit gap before React commits busy.
    listingWriteInFlight.current = true;
    setBusy(true); setBusyListingId(session.action.sourceListingId);
    try {
      const reason = typeof form === "string" ? form : form.reason;
      const formIdentity = JSON.stringify(form);
      if (session.submittedForm !== undefined && session.submittedForm !== formIdentity) {
        setError({ status: 409, code: "ODP-LISTING-REQUEST-CHANGED", summary: "此操作已送出，原因與風險確認不可改成另一筆請求。",
          nextAction: "請先確認原操作結果，再開啟新的確認視窗。", correlationId: null,
          occurredAt: new Date().toISOString(), retryable: false });
        return;
      }
      const actorSubjectId = await readListingWriteSubject();
      if (!current()) return;
      if (!fixturesAllowed && !actorSubjectId || session.verifiedActorSubjectId && actorSubjectId !== session.verifiedActorSubjectId) {
        setError({ status: 409, code: "ODP-LISTING-ACTOR-UNVERIFIED", summary: "尚無法確認原操作的登入操作者，未送出寫入。",
          nextAction: "請確認登入狀態並重新開啟確認視窗。", correlationId: null, occurredAt: new Date().toISOString(), retryable: false });
        return;
      }
      if (actorSubjectId) session.verifiedActorSubjectId = actorSubjectId;
      const fresh = await fetchNetworkSnapshot(effectiveSelectedId, effectiveLens, activeRoleId, snapshotReadGuard());
      if ("superseded" in fresh) return;
      if (!current()) return;
      const admitted = fresh.ok && (inspectNetworkListingsSnapshot(fresh.snapshot) === "ready"
        || fixturesAllowed && hasNetworkListingsRenderShape(fresh.snapshot));
      const projected = admitted && fresh.ok && Array.isArray(fresh.snapshot.listings) ? resolveListingAction(session.action.sourceListingId, fresh.snapshot.listings,
        activeRoleId, { ready: true, fixturesAllowed, candidates: fresh.snapshot.candidates }) : null;
      const receiptRetry = session.submittedForm !== undefined && admitted && fresh.ok && Array.isArray(fresh.snapshot.listings)
        && listingWriteTerminalMatches(session.action, fresh.snapshot.listings, reason);
      const action = receiptRetry ? session.action : projected;
      if (!action || !receiptRetry && action.fingerprint !== session.action.fingerprint || action.kind !== kind) {
        setError({ status: 409, code: "ODP-LISTING-CURRENT-STATE-UNAVAILABLE",
          summary: "物件、關聯或權限資料已變更，尚未送出寫入。", nextAction: "請關閉此視窗並重新載入後確認目前紀錄。",
          occurredAt: new Date().toISOString(), correlationId: null, retryable: false });
        return;
      }
      if (!current()) return;
      session.submittedForm = formIdentity;
      const result = kind === "merge" && typeof form !== "string" && action.targetListingId
        ? await listingsApi.merge(client, action.sourceListingId, { targetListingId: action.targetListingId,
          actorRoleId: activeRoleId, reason: form.reason, riskSummary: form.riskSummary,
          riskAcknowledged: form.riskAcknowledged, idempotencyKey: request.idempotencyKey,
          ifMatch: action.ifMatch, targetIfMatch: action.targetIfMatch, ifActorMatch: actorSubjectId ?? undefined })
        : kind === "archive" && typeof form === "string"
          ? await listingsApi.archive(client, action.sourceListingId, { actorRoleId: activeRoleId, reason: form,
            idempotencyKey: request.idempotencyKey, ifMatch: action.ifMatch, ifActorMatch: actorSubjectId ?? undefined }) : null;
      if (!current() || !result) return;
      if (!result.ok) { setError(result.error); return; }
      const subjectAfter = await readListingWriteSubject();
      if (!current()) return;
      if (subjectAfter !== actorSubjectId || !listingWriteReceiptMatches(action, result.value, reason, {
        actorSubjectId, fixturesAllowed,
        idempotencyKey: request.idempotencyKey,
        riskSummary: typeof form === "string" ? undefined : form.riskSummary,
        riskAcknowledged: typeof form === "string" ? undefined : form.riskAcknowledged,
      })) {
        setError({ status: 0, code: "ODP-LISTING-RECEIPT-UNVERIFIED", summary: "回應未包含此操作的完整紀錄，尚無法確認結果。",
          nextAction: "請保留此視窗與操作識別，重新讀取紀錄或以同一操作重試。", correlationId: null,
          occurredAt: new Date().toISOString(), retryable: true });
        return;
      }
      listingWriteSession.current = null;
      setMergeRequest(null); setArchiveRequest(null); setError(null);
      await reloadNetworkSnapshot();
    } finally {
      listingWriteInFlight.current = false;
      if (current() || listingWriteSession.current === null && session.contextKey === navigationContext.current.key
        && session.generation === navigationContext.current.generation && session.locationVersion === navigationLocation.current.version) {
        setBusy(false); setBusyListingId(null);
      }
    }
  }

  function submitMergeListing(form: ListingMergeForm) { void submitListingWrite("merge", form); }

  function submissionIsCurrent(session: NonNullable<typeof submissionSession.current>) {
    return submissionSession.current === session && navigationLocation.current.mounted
      && session.generation === navigationContext.current.generation && session.contextKey === navigationContext.current.key
      && session.locationVersion === navigationLocation.current.version
      && session.observedTenant === window.sessionStorage.getItem("oday.operator.tenant");
  }

  async function submissionSubject(session: NonNullable<typeof submissionSession.current>) {
    const subject = await readCanonicalScoreSubject(() => submissionIsCurrent(session));
    if (!subject || !submissionIsCurrent(session) || session.actor !== null && session.actor !== subject) {
      throw new Error("登入身分或操作上下文已變更；尚未確認送審結果，請重新讀取。");
    }
    session.actor = subject;
    return subject;
  }

  async function submissionRead(session: NonNullable<typeof submissionSession.current>, path: string, init?: RequestInit): Promise<unknown> {
    const response = await fetch(path, { cache: "no-store", ...init });
    await submissionSubject(session);
    const value: unknown = await response.json();
    await submissionSubject(session);
    if (!response.ok) throw new Error(`送審服務拒絕或未完成讀取（${response.status}）；${session.attempt?.correlation ?? "結果尚未確認"}`);
    return value;
  }

  async function requestReviewSubmission(offer: ReviewSubmissionOffer) {
    if (submissionInFlight.current || !navigationLocation.current.mounted
      || nativeReadGeneration !== navigationContext.current.generation || nativeReadContextKey !== navigationContext.current.key
      || submissionCandidates[offer.candidateId] !== offer) return;
    const scored = scoringSnapshot?.candidates.filter((row) => row.id === offer.candidateId) ?? [];
    const reports = scoringSnapshot?.scorecards.filter((row) => row.id === offer.candidateId) ?? [];
    const frozen = offer.decisionId ? reviewsSnapshot?.reviews.find((row) => row.id === offer.decisionId) : reports[0];
    const decision = offer.decisionId ? reviewsSnapshot?.decisions?.find((row) => row.decision_id === offer.decisionId) : null;
    if (scored.length !== 1 || reports.length !== 1 || !readId(scored[0].listingId)) return;
    submissionInFlight.current = true;
    const session = { generation: navigationContext.current.generation, contextKey: navigationContext.current.key,
      locationVersion: navigationLocation.current.version, observedTenant: window.sessionStorage.getItem("oday.operator.tenant"),
      offer, actor: null as string | null, context: null as ReviewSubmissionContext | null,
      attempt: null as { body: ReviewSubmissionBody; payload: string; key: string; correlation: string } | null };
    submissionSession.current = session;
    setSubmissionOffer(offer); setSubmissionContext(null); setSubmissionError(null);
    setSubmissionCorrelation(null); setSubmissionConfirmed(null); setSubmissionBusy(true);
    try {
      const actor = await submissionSubject(session);
      const query = offer.decisionId ? `?decisionId=${encodeURIComponent(offer.decisionId)}` : "";
      const context = await submissionRead(session,
        `/api/v1/operator/network-reviews/candidates/${encodeURIComponent(offer.candidateId)}/submission-context${query}`,
        { headers: NETWORK_OPERATOR_HEADERS });
      if (!hasReviewSubmissionContext(context) || context.actorSubjectId !== actor || context.candidateId !== offer.candidateId
        || context.listingId !== scored[0].listingId || scored[0].readEvidence && context.addressId !== scored[0].readEvidence.addressId || session.observedTenant && context.tenantId !== session.observedTenant
        || context.target.expectedLatestReportId !== reports[0].reportId || context.target.expectedLatestReportVersion !== reports[0].reportVersion
        || context.canSubmit && context.target.decisionId !== offer.decisionId
        || context.canSubmit && (context.target.expectedReportId !== frozen?.reportId || context.target.expectedReportVersion !== frozen?.reportVersion)
        || context.canSubmit && offer.decisionId !== null && context.target.expectedDecisionVersion !== decision?.version
        || context.canSubmit && offer.previousDecisionId !== context.previousReview?.decisionId
        || context.canSubmit && offer.newRound === true
          && !["approved", "opened"].includes(context.target.expectedCandidateState)) {
        throw new Error("送審上下文與目前候選點、登入者或報告版本不一致；尚未受理送審。");
      }
      if (!submissionIsCurrent(session)) return;
      session.context = context; setSubmissionContext(context);
      if (!context.canSubmit) setSubmissionError("已有待審紀錄；請重新讀取候選點以查閱正式 Decision 與 Approval。未建立新的送審。");
    } catch (error) {
      if (submissionIsCurrent(session)) setSubmissionError(error instanceof Error ? error.message : "送審資格尚未確認。");
    } finally {
      submissionInFlight.current = false;
      if (submissionIsCurrent(session)) setSubmissionBusy(false);
    }
  }

  async function submitCanonicalReview(form: ReviewSubmissionForm) {
    const session = submissionSession.current;
    const context = session?.context;
    if (!session || !context?.canSubmit || submissionInFlight.current || !submissionIsCurrent(session)
      || !form.submissionSummary.trim() || !context.reviewerGroups.some((group) => group.id === form.reviewerGroupId)) return;
    submissionInFlight.current = true; setSubmissionBusy(true); setSubmissionError(null);
    try {
      const actor = await submissionSubject(session);
      const body: ReviewSubmissionBody = { ...context.target, submissionSummary: form.submissionSummary.trim(),
        reviewerGroupId: form.reviewerGroupId, expectedActorSubjectId: actor, expectedTenantId: context.tenantId };
      const payload = JSON.stringify(body);
      if (!session.attempt || session.attempt.payload !== payload) {
        session.attempt = { body, payload, key: globalThis.crypto.randomUUID(), correlation: globalThis.crypto.randomUUID() };
      }
      const attempt = session.attempt;
      setSubmissionCorrelation(attempt.correlation);
      const query = context.target.decisionId ? `?decisionId=${encodeURIComponent(context.target.decisionId)}` : "";
      const current = await submissionRead(session,
        `/api/v1/operator/network-reviews/candidates/${encodeURIComponent(context.candidateId)}/submission-context${query}`,
        { headers: NETWORK_OPERATOR_HEADERS });
      if (!hasReviewSubmissionContext(current) || current.candidateId !== context.candidateId || current.listingId !== context.listingId
        || current.addressId !== context.addressId || current.tenantId !== context.tenantId || current.actorSubjectId !== actor
        || !current.reviewerGroups.some((group) => group.id === form.reviewerGroupId)
        || current.canSubmit && !reviewSubmissionTargetsMatch(current.target, context.target)) {
        throw new Error("送審資料已變更；保留表單，請重新確認候選點與報告版本。");
      }
      await submissionSubject(session);
      const receipt = await submissionRead(session,
        `/api/v1/operator/network-reviews/candidates/${encodeURIComponent(context.candidateId)}/submit`, {
          method: "POST", headers: { ...NETWORK_OPERATOR_HEADERS, "Content-Type": "application/json", "If-Actor-Match": actor,
            "Idempotency-Key": attempt.key, "X-Correlation-Id": attempt.correlation }, body: attempt.payload,
        });
      if (!reviewSubmissionReceiptMatches(receipt, context, attempt.body, attempt.key, attempt.correlation)) {
        throw new Error("送審回覆的 Decision、Approval 或操作人員資料未吻合；結果尚未確認。");
      }
      // Receipt never grants VIEW. Each fresh read passes through the current
      // authenticated BFF and API permissions before any pending UI is applied.
      const network = await submissionRead(session,
        `/api/v1/operator/network-listings?${new URLSearchParams({ lens: localLens })}`, { headers: NETWORK_OPERATOR_HEADERS });
      const scoring = await submissionRead(session, "/api/v1/operator/network-scoring", { headers: NETWORK_OPERATOR_HEADERS });
      const reviews = await submissionRead(session, "/api/v1/operator/network-reviews", { headers: reviewIdentity.readHeaders });
      if (!hasNetworkListingsRenderShape(network) || !hasNetworkScoringRenderShape(scoring)
        || !reviewSubmissionReadbackMatches(network, reviews, receipt)) {
        throw new Error("正式審核讀取尚未核對同一份送審紀錄；結果尚未確認，請保留識別碼重新確認。");
      }
      const confirmedWorkflow = projectCandidateNativeWorkflow({ currentGeneration: session.generation, tenantId: context.tenantId,
        network: { snapshot: network as NetworkListingsSnapshot, status: "ready", generation: session.generation },
        scoring: { snapshot: scoring as NetworkScoringSnapshot, status: "ready", generation: session.generation },
        reviews: { snapshot: reviews as NetworkReviewsSnapshot, status: "ready", generation: session.generation } });
      const confirmedTarget = confirmedWorkflow[context.candidateId];
      if (confirmedTarget?.availability !== "recorded" || confirmedTarget.stage !== "pendingreview"
        || confirmedTarget.review?.reviewId !== receipt.records.decisionId || confirmedTarget.review.reportId !== receipt.records.reportId
        || confirmedTarget.review.reportVersion !== receipt.records.reportVersion) {
        throw new Error("待審指標與凍結／最新報告關聯尚未吻合；送審結果尚未確認。");
      }
      await submissionSubject(session);
      if (!submissionIsCurrent(session)) return;
      setNetworkSnapshot(network as NetworkListingsSnapshot); setNetworkNavigationGeneration(session.generation); setNetworkLoadState("ready"); setNetworkLoadFailure(null);
      setScoringSnapshot(scoring as NetworkScoringSnapshot); setScoringNavigationGeneration(session.generation); setScoringLoadState("ready"); setScoringLoadFailure(null); setScoringWorkflowProvenance("canonical");
      applyReviewsSnapshot(reviews as NetworkReviewsSnapshot, session.generation, ++reviewsReadSequence.current);
      setSubmissionConfirmed(receipt); setSubmissionOffer(null); setSubmissionContext(null);
      submissionSession.current = null;
    } catch (error) {
      if (submissionIsCurrent(session)) setSubmissionError(error instanceof Error ? error.message : "送審结果尚未確認。");
    } finally {
      submissionInFlight.current = false;
      if (submissionIsCurrent(session) || submissionSession.current === null) setSubmissionBusy(false);
    }
  }

  const workflowProjection = projectCandidateWorkflow({
    selection: { id: workflowCandidateId, invalid: isCandidateWorkflowTab(activeTab) && !overrideApplies && urlCandidateSelection.invalid },
    currentGeneration: navigationContext.current.generation,
    network: { snapshot: networkSnapshot, status: networkLoadState, generation: networkNavigationGeneration },
    scoring: { snapshot: scoringSnapshot, status: fixturesAllowed && scoringWorkflowProvenance === "unknown" ? "error" : scoringLoadState, generation: scoringNavigationGeneration },
    // Use only the admitted review read from the current access generation.
    reviews: { snapshot: reviewsSnapshot, status: reviewsLoadState, generation: reviewsNavigationGeneration },
  });
  // Both snapshots are masked to the current access generation above. A prior
  // fixture read cannot survive a role/tenant change or a failed current read.
  const useFixtureWorkflow = fixturesAllowed && scoringWorkflowProvenance === "fixture"
    && networkSnapshot !== null && scoringSnapshot !== null;
  const nativeWorkflow = useFixtureWorkflow ? undefined : projectCandidateNativeWorkflow({
    currentGeneration: navigationContext.current.generation,
    tenantId: typeof window === "undefined" ? null : window.sessionStorage.getItem("oday.operator.tenant"),
    network: { snapshot: networkSnapshot, status: networkLoadState, generation: networkNavigationGeneration },
    scoring: { snapshot: scoringSnapshot, status: scoringWorkflowProvenance === "canonical" ? scoringLoadState : "error", generation: scoringNavigationGeneration },
    reviews: { snapshot: reviewsSnapshot, status: reviewsLoadState, generation: reviewsNavigationGeneration },
  });
  const submissionCandidates = canOfferReviewSubmission(activeRoleId)
    ? reviewSubmissionOffers(networkSnapshot, scoringSnapshot, reviewsSnapshot, nativeWorkflow) : {};
  const explicitReview = resolveCandidateReviewTarget(nativeWorkflow ?? {}, workflowCandidateId,
    activeTab === 5 ? overrideApplies ? { id: tabOverride.reviewId, invalid: false } : urlReviewSelection
      : { id: null, invalid: false });
  const nativeReadGeneration = navigationContext.current.generation;
  const nativeReadContextKey = navigationContext.current.key;
  const expansionSteps = useFixtureWorkflow
    ? networkSnapshot?.expansionSteps ?? buildFallbackExpansionSteps(
          effectiveSelectedId,
          viewModel.candidatePipeline.some((row) => row.id === "CS-1001"),
        )
    : workflowProjection.steps;
  const candidateZoneScope = candidateZoneId
    ? {
        id: candidateZoneId,
        label: heatZones.find((zone) => zone.id === candidateZoneId)?.label ?? candidateZoneId,
      }
    : null;
  const selectedZoneLabel = selectedZone?.label ?? heatZones.find((zone) => zone.id === effectiveSelectedId)?.label;

  const findAreasLoadState = resolveNetworkFindAreasLoadState(networkLoadState, heatZones.length,
    networkSnapshot?.heatZoneAvailability?.state);
  // Explicit Task Center target that is not exactly confirmed by the fresh
  // read: existing unavailable styling, no unrelated zone detail or actions.
  const findAreasTargetGate = activeTab === 0 ? heatZoneTargetGate(heatZoneTarget) : null;
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
      ? networkApiError ?? networkSnapshot?.heatZoneAvailability?.message ??
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
  const snapshotCountState = resolveNetworkCountState(networkLoadState, networkLoadFailure, fixturesAllowed, !canReadListings);
  // Package10 uses four workflow KPIs, not unauthorized HeatZone aggregates.
  // Match the count to its actual source: a refused dedicated reviews read
  // cannot erase known authorized reviews in the scoped network snapshot.
  const reviewCountState = !canReadReviews || reviewsSnapshot !== null
    ? resolveNetworkCountState(reviewsLoadState, reviewsLoadFailure, fixturesAllowed, !canReadReviews)
    : snapshotCountState;
  const rebalanceCountState = resolveNetworkCountState(rebalanceLoadState, rebalanceLoadFailure, fixturesAllowed, !canReadRebalance);

  const listingRadarPanel = (
    <ListingRadarPanel
      activeRoleId={activeRoleId}
      busyListingId={busyListingId}
      intakeDetailOpen={intakeDetailOpen}
      listings={listingsEffective}
      listingsReadFailure={listingsReadFailure}
      onArchive={archiveListing}
      navigationByListing={navigationByListing}
      navigationError={navigationError}
      onNavigate={navigateListing}
      onMerge={mergeListing}
      rows={viewModel.listingRadar}
      selectedHeatZoneId={effectiveSelectedId}
      heatZoneOptions={{
        state: (networkLoadState === "ready" || networkLoadState === "empty") && networkSnapshot?.source === "api" ? "ready"
          : networkLoadState === "loading" ? "loading" : "error",
        options: (networkLoadState === "ready" || networkLoadState === "empty") && networkSnapshot?.source === "api"
          ? (networkSnapshot.heatZones ?? []).map(({ id, label }) => ({ id, label })) : [],
      }}
      selectedZoneLabel={selectedZoneLabel}
      sources={listingSourcesEffective}
      writeContext={listingWriteContext}
    />
  );

  const listingMergeDialog = mergeRequest ? (
    <ListingMergeDialog
      key={mergeRequest.idempotencyKey}
      busy={mergeBusy}
      error={mergeError}
      onClose={() => {
        listingWriteSession.current = null;
        setMergeRequest(null);
        setMergeError(null);
      }}
      onSubmit={submitMergeListing}
      request={mergeRequest}
    />
  ) : null;

  const listingArchiveDialog = archiveRequest ? <ListingArchiveDialog key={archiveRequest.idempotencyKey} busy={archiveBusy} error={archiveError}
    request={archiveRequest} onClose={() => { listingWriteSession.current = null; setArchiveRequest(null); setArchiveError(null); }}
    onSubmit={(reason) => { void submitListingWrite("archive", reason); }} /> : null;

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
        {listingArchiveDialog}
      </section>
    );
  }

  if (activeTab < 0) {
    return <section role="status">目前身分沒有可讀取的 Network 分頁。</section>;
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
        allowedTabIndices={allowedTabIndices}
        onTabChange={changeActiveTab}
        steps={expansionSteps}
        workflowNotice={useFixtureWorkflow || activeTab === 0 ? undefined : workflowProjection.notice}
        workflowNextAction={useFixtureWorkflow ? undefined : workflowProjection.nextAction}
        tabCounts={tabCounts}
        tabs={networkTabs}
      >
        {activeTabGateState ? (
          <>
            <OperatorDataUnavailableGate
              detail={activeTabGateDetail}
              failure={activeTabGateFailure}
              onRetry={() => window.location.reload()}
              status={activeTabGateState}
            />
            {activeTab === 3 && activeTabGateState === "empty" && scoringSnapshot?.source === "canonical" && scoringSnapshot.targetFormat
              ? <StandardFormatAssumptions format={scoringSnapshot.targetFormat} /> : null}
          </>
        ) : activeTab === 1 ? (
          listingRadarPanel
        ) : activeTab === 2 ? (
          <CandidatePanel
            busyCandidateId={submissionBusy ? submissionOffer?.candidateId : busyCandidateId}
            submissionOffers={submissionCandidates}
            onOpenSurvey={fixturesAllowed ? undefined : (target) => surveyBinding.open(target, activeTab)}
            onRequestSubmission={(offer) => { void requestReviewSubmission(offer); }}
            candidates={scoringNavigationGeneration === navigationContext.current.generation ? scoringSnapshot?.candidates ?? [] : []}
            fallbackRows={fixturesAllowed && !selectedCandidateId ? viewModel.candidatePipeline : []}
            selectedCandidateId={selectedCandidateId}
            nativeWorkflow={nativeWorkflow}
            onViewReview={(target) => {
              if (!navigationLocation.current.mounted || target.generation !== navigationContext.current.generation
                || nativeReadGeneration !== navigationContext.current.generation || nativeReadContextKey !== navigationContext.current.key
                || nativeWorkflow?.[target.candidateId]?.review !== target) return;
              changeActiveTab(5, { [CANDIDATE_SELECTION_PARAM]: target.candidateId, [REVIEW_SELECTION_PARAM]: target.reviewId });
            }}
            selectionError={candidateSelectionError}
            onSelectCandidate={(id) => changeActiveTab(2, { [CANDIDATE_SELECTION_PARAM]: id })}
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
            targetFormat={scoringSnapshot?.targetFormat}
            onRescore={scoringSnapshot?.source === "canonical" || fixturesAllowed ? runSiteScore : undefined}
            requireRescoreReason={scoringSnapshot?.source === "canonical"}
            executionRequest={scoreExecution}
            executionError={scoreExecutionError}
            rescoreUnavailableReason={scoringSnapshot?.source === "canonical" && (scoringLoadState !== "ready" || !workflowCandidateId || scoringSnapshot.candidates.filter((candidate) => candidate.id === workflowCandidateId).length !== 1 || !hasVerifiedCurrentScoreAdmission(scoringSnapshot.candidates.find((candidate) => candidate.id === workflowCandidateId))) ? "目前資料、來源、PIT 或模型的再評分資格尚未確認；已保存報告仍可查閱。" : scoreExecution && ["QUEUED", "RUNNING", "RETRYING"].includes(scoreExecution.status) ? "評分工作處理中，已保存報告仍可查閱。" : scoreExecution && ["DEAD_LETTER"].includes(scoreExecution.status) ? "既有工作尚未解除，請確認重試資格。已保存報告仍可查閱。" : undefined}
            scorecards={scoringSnapshot?.scorecards ?? []}
            selectedCandidateId={workflowCandidateId}
            onSelectCandidate={(id) => changeActiveTab(3, { [CANDIDATE_SELECTION_PARAM]: id })}
            selectionError={workflowSelectionError}
          />
        ) : activeTab === 4 ? (
          <ComparePanel
            compare={scoringSnapshot?.compare ?? null}
            fallback={fixturesAllowed ? viewModel.compare : { columns: [], metrics: [] }}
          />
        ) : activeTab === 5 ? (
          <ReviewPanel
            key={reviewIntentEpoch}
            reviews={dedicatedReviews}
            fallbackRows={reviewFallbackRows}
            // Role eligibility and admission to the native transport are
            // separate. Production legacy reads remain readable without
            // presenting controls that their preflight would refuse.
            canDecide={reviewIdentity.canDecide && (reviewsSnapshot?.source === "canonical" || fixturesAllowed)}
            decisionUnavailableReason={reviewIdentity.canDecide && !fixturesAllowed && reviewsSnapshot?.source !== "canonical"
              ? "這筆審核資料尚未與正式審核流程核對，目前僅供查閱。請從 Today 開啟對應的候選點審核。"
              : undefined}
            submitting={reviewSubmitting}
            decideError={reviewError}
            onDecide={submitReviewDecision}
            onRequestDecision={(id, action) => reviewsSnapshot?.source === "canonical"
              ? submitBoundReviewDecision(id, action, null) : fixturesAllowed}
            workflowCandidateId={workflowCandidateId}
            expectedReport={workflowProjection.report}
            explicitReadTarget={explicitReview.target}
            canonicalCurrentReviewTarget={workflowCandidateId
              && nativeWorkflow?.[workflowCandidateId]?.availability === "recorded"
              && nativeWorkflow[workflowCandidateId].stage === "pendingreview"
              && nativeWorkflow[workflowCandidateId].review?.generation === navigationContext.current.generation
              ? nativeWorkflow[workflowCandidateId].review : null}
            explicitSelectionError={explicitReview.error}
            fixtureCandidateContext={fixturesAllowed}
            onSelectCandidate={(id) => changeActiveTab(5, { [CANDIDATE_SELECTION_PARAM]: id })}
            selectionError={workflowSelectionError}
          />
        ) : activeTab === 6 ? (
          <RebalancePanel
            builderApi={netPlanBuilderApi}
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
        ) : findAreasTargetGate ? (
          <OperatorDataUnavailableGate
            failure={findAreasTargetGate.failure}
            loadingDetail={findAreasTargetGate.loadingDetail}
            onRetry={findAreasTargetGate.retry ? () => window.location.reload() : undefined}
            status={findAreasTargetGate.status}
          />
        ) : (
          <FindAreasPanel
            activeRoleId={activeRoleId}
            canAddListing={canAddListingFromSelectedZone}
            fixturesAllowed={fixturesAllowed}
            viewModel={viewModel}
            selectedZone={selectedZone}
            effectiveLens={effectiveLens}
            isSelectedTracked={isSelectedTracked}
            heatZones={heatZones}
            listings={listingsEffective}
            candidates={localCandidates}
            onSelectZone={selectHeatZone}
            onOpenSurvey={fixturesAllowed ? undefined : (id) => surveyBinding.open({ kind: "HEAT_ZONE", id }, activeTab)}
            onOpenListingSearch={networkLoadState !== "ready" || networkSnapshot?.source !== "api"
              || networkSnapshot.heatZoneAvailability?.state === "unavailable"
              || !selectedZone || !networkSnapshot.heatZones?.some(zone => zone.id === selectedZone.id)
              || !canPerformIntake("submit", activeRoleId) ? undefined : () => {
              const guard = snapshotReadGuard();
              if (!guard || !selectedZone) return;
              setListingSearchRequest({ zoneId: selectedZone.id, zoneLabel: selectedZone.label, guard,
                contextKey: navigationContext.current.key, locationVersion: navigationLocation.current.version });
            }}
            onChangeLens={changeLens}
            onOpenAreaPlanning={networkLoadState !== "ready" || networkSnapshot?.source !== "api"
              || networkSnapshot.heatZoneAvailability?.state === "unavailable"
              || !selectedZone || !networkSnapshot.heatZones?.some(zone => zone.id === selectedZone.id)
              || !["expansion-manager", "expansion-staff"].includes(activeRoleId) ? undefined : () => {
              const guard = snapshotReadGuard();
              if (!guard || !selectedZone) return;
              setAreaPlanningRequest({ zoneId: selectedZone.id, zoneLabel: selectedZone.label, guard,
                contextKey: navigationContext.current.key, locationVersion: navigationLocation.current.version });
            }}
            onToggleTracked={toggleTracked}
            onSourceListings={sourceListings}
            onAddListingFromUrl={addListingFromUrl}
          />
        )}
      </NetworkShell>

        {submissionConfirmed ? <p role="status" data-testid="review-submission-confirmed">已核對正式送審紀錄：Decision {submissionConfirmed.records.decisionId} v{submissionConfirmed.records.decisionVersion} · Approval {submissionConfirmed.records.approvalId}。<br />
        送審摘要：{submissionConfirmed.commandReceipt.submissionSummary} · 審核群組：{submissionConfirmed.commandReceipt.reviewerGroupId} · 送審者：{submissionConfirmed.commandReceipt.verifiedSubmitterSubjectId}。
        <button type="button" onClick={() => {
          const target = nativeWorkflow?.[submissionConfirmed.records.candidateId]?.review;
          if (!target || target.reviewId !== submissionConfirmed.records.decisionId || target.generation !== navigationContext.current.generation) return;
          changeActiveTab(5, { [CANDIDATE_SELECTION_PARAM]: target.candidateId, [REVIEW_SELECTION_PARAM]: target.reviewId });
        }}>查看已確認審核</button></p> : null}
      {submissionOffer ? <ReviewSubmissionDialog candidateId={submissionOffer.candidateId} context={submissionContext} busy={submissionBusy}
        error={submissionError} correlationId={submissionCorrelation} onSubmit={(form) => { void submitCanonicalReview(form); }}
        onClose={() => { if (!submissionInFlight.current) { submissionSession.current = null; setSubmissionOffer(null); setSubmissionContext(null); setSubmissionError(null); } }} /> : null}
      {surveyRequest?.api === surveyBinding.api && surveyRequest.tab === activeTab && (activeTab === 0 || activeTab === 2)
        && allowedTabIndices.includes(activeTab) ? <SurveyAssignmentDialog api={surveyRequest.api} target={surveyRequest.target}
          onClose={() => setSurveyRequest(null)} /> : null}
      {listingSearchRequest && activeTab === 0 && allowedTabIndices.includes(0)
        && listingSearchRequest.contextKey === navigationContext.current.key
        && listingSearchRequest.locationVersion === navigationLocation.current.version ?
        <ListingSearchDialog zoneId={listingSearchRequest.zoneId} zoneLabel={listingSearchRequest.zoneLabel}
          headers={NETWORK_OPERATOR_HEADERS} guard={listingSearchRequest.guard}
          onClose={() => setListingSearchRequest(null)} onOpenIntake={(id) => {
            const params = new URLSearchParams(searchParams.toString());
            params.set("heatZoneId", listingSearchRequest.zoneId); params.set("hz", listingSearchRequest.zoneId);
            router.push(buildDurableDetailHref(id, parseUrlState(params), params), { scroll: false });
          }} /> : null}
      {listingMergeDialog}
      {areaPlanningRequest && activeTab === 0 && allowedTabIndices.includes(0)
        && areaPlanningRequest.contextKey === navigationContext.current.key
        && areaPlanningRequest.locationVersion === navigationLocation.current.version ?
        <AreaPlanningDraftDialog zoneId={areaPlanningRequest.zoneId} zoneLabel={areaPlanningRequest.zoneLabel}
          candidates={localCandidates.filter(candidate => candidate.heatZoneId === areaPlanningRequest.zoneId)
            .map(candidate => ({ id: candidate.id, title: candidate.title }))}
          headers={NETWORK_OPERATOR_HEADERS} guard={areaPlanningRequest.guard}
          onClose={() => setAreaPlanningRequest(null)} /> : null}
        {listingArchiveDialog}
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
