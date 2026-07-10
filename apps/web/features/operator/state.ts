import {
  APPROVAL_FIXTURES,
  AUDIT_EVENT_FIXTURES,
  CANDIDATE_FIXTURES,
  DECISION_FIXTURES,
  DEMO_NOW,
  EVIDENCE_FIXTURES,
  GROWTH_ITEM_FIXTURES,
  HEAT_ZONE_FIXTURES,
  ISSUE_FIXTURES,
  LISTING_FIXTURES,
  LISTING_SOURCE_FIXTURES,
  NAV_WORKSPACES,
  OPERATOR_ROLES,
  PRICEOPS_RECOMMENDATION_FIXTURES,
  REBALANCE_STORE_FIXTURES,
  SEGMENT_FIXTURES,
  SITE_REVIEW_FIXTURES,
  STORE_FIXTURES,
} from "./fixtures";
import type {
  ApprovalDecisionStatus,
  ApprovalStatus,
  AuditEvent,
  CandidateStatus,
  GrowthStatus,
  IssueStatus,
  ListingStatus,
  OperatorAction,
  OperatorRoleId,
  OperatorState,
  RebalanceStatus,
  SiteReviewStatus,
  TargetType,
  TransitionAuditInput,
  WorkspaceKey,
} from "./types";

export const ISSUE_TRANSITIONS: Record<IssueStatus, IssueStatus[]> = {
  new: ["triaged", "waitingevidence", "escalated"],
  triaged: ["assigned", "waitingevidence", "waitingapproval", "escalated"],
  assigned: ["inprogress", "waitingapproval", "escalated"],
  inprogress: ["executed", "waitingevidence", "escalated"],
  executed: ["observing", "outcomeready", "escalated"],
  observing: ["outcomeready", "inprogress", "escalated"],
  outcomeready: ["closed", "observing", "escalated"],
  closed: [],
  waitingevidence: ["triaged", "assigned", "escalated"],
  waitingapproval: ["assigned", "inprogress", "escalated"],
  escalated: ["triaged", "closed"],
};

export const GROWTH_TRANSITIONS: Record<GrowthStatus, GrowthStatus[]> = {
  candidate: ["draft", "closed"],
  draft: ["pending", "closed"],
  pending: ["approved", "draft", "closed"],
  approved: ["scheduled", "closed"],
  scheduled: ["running", "closed"],
  running: ["observing", "closed"],
  observing: ["outcomeready", "running"],
  outcomeready: ["effective", "ineffective", "closed"],
  effective: ["closed"],
  ineffective: ["closed", "draft"],
  closed: [],
};

export const LISTING_TRANSITIONS: Record<ListingStatus, ListingStatus[]> = {
  new: ["parsed", "archived", "expired"],
  parsed: ["geocoded", "duplicate", "hardfail", "archived", "expired"],
  geocoded: ["watching", "duplicate", "hardfail", "archived", "expired"],
  watching: ["contacted", "candidate", "archived", "expired"],
  contacted: ["visit", "candidate", "archived", "expired"],
  visit: ["candidate", "scored", "archived", "expired"],
  candidate: ["scored", "archived"],
  scored: ["candidate", "archived"],
  duplicate: ["archived"],
  hardfail: ["archived"],
  archived: [],
  expired: ["archived"],
};

export const CANDIDATE_TRANSITIONS: Record<CandidateStatus, CandidateStatus[]> = {
  missingdata: ["scoring", "blocked", "rejected"],
  scoring: ["wait", "ready", "rejected", "blocked"],
  wait: ["missingdata", "scoring", "pendingreview", "rejected"],
  ready: ["pendingreview", "scoring", "rejected"],
  pendingreview: ["approved", "rejected", "wait"],
  approved: [],
  rejected: [],
  blocked: ["missingdata", "rejected"],
};

export const SITE_REVIEW_TRANSITIONS: Record<SiteReviewStatus, SiteReviewStatus[]> = {
  pending: ["approved", "returned", "rejected"],
  approved: [],
  returned: ["pending", "rejected"],
  rejected: [],
};

export const REBALANCE_TRANSITIONS: Record<RebalanceStatus, RebalanceStatus[]> = {
  watching: ["avmrequested", "closed"],
  avmrequested: ["avmready", "closed"],
  avmready: ["netplanreview", "closed"],
  netplanreview: ["pendingapproval", "closed"],
  pendingapproval: ["approved", "netplanreview", "closed"],
  approved: ["closed"],
  closed: [],
};

export const APPROVAL_TRANSITIONS: Record<ApprovalStatus, ApprovalStatus[]> = {
  pending: ["approved", "returned", "rejected", "cancelled"],
  approved: [],
  returned: ["pending", "cancelled"],
  rejected: [],
  cancelled: [],
};

export const operatorActions = {
  reset: (roleId?: OperatorRoleId): OperatorAction => ({ type: "state/reset", roleId }),
  switchRole: (roleId: OperatorRoleId): OperatorAction => ({ type: "role/switch", roleId }),
  selectWorkspace: (workspaceId: WorkspaceKey): OperatorAction => ({ type: "workspace/select", workspaceId }),
  transitionIssue: (issueId: string, status: IssueStatus, actor: TransitionAuditInput): OperatorAction => ({
    type: "issue/transition",
    issueId,
    status,
    ...actor,
  }),
  transitionGrowth: (growthItemId: string, status: GrowthStatus, actor: TransitionAuditInput): OperatorAction => ({
    type: "growth/transition",
    growthItemId,
    status,
    ...actor,
  }),
  transitionListing: (listingId: string, status: ListingStatus, actor: TransitionAuditInput): OperatorAction => ({
    type: "listing/transition",
    listingId,
    status,
    ...actor,
  }),
  transitionCandidate: (candidateId: string, status: CandidateStatus, actor: TransitionAuditInput): OperatorAction => ({
    type: "candidate/transition",
    candidateId,
    status,
    ...actor,
  }),
  transitionRebalance: (
    rebalanceStoreId: string,
    status: RebalanceStatus,
    actor: TransitionAuditInput,
  ): OperatorAction => ({
    type: "rebalance/transition",
    rebalanceStoreId,
    status,
    ...actor,
  }),
  decideApproval: (
    approvalId: string,
    status: ApprovalDecisionStatus,
    actor: TransitionAuditInput & { reason?: string },
  ): OperatorAction => ({
    type: "approval/decide",
    approvalId,
    status,
    ...actor,
  }),
  decideSiteReview: (
    reviewId: string,
    status: SiteReviewStatus,
    actor: TransitionAuditInput & { reason?: string },
  ): OperatorAction => ({
    type: "siteReview/decide",
    reviewId,
    status,
    ...actor,
  }),
};

export function createInitialOperatorState(roleId: OperatorRoleId = "opsLead"): OperatorState {
  const role = OPERATOR_ROLES.find((item) => item.id === roleId) ?? OPERATOR_ROLES[0];

  return cloneOperatorState({
    roleId: role.id,
    selectedWorkspace: role.defaultWorkspace,
    selectedIssueId: "ISS-1024",
    selectedGrowthItemId: "GRW-201",
    selectedHeatZoneId: "HZ-01",
    selectedCandidateId: "CS-1002",
    roles: OPERATOR_ROLES,
    navWorkspaces: NAV_WORKSPACES,
    stores: STORE_FIXTURES,
    issues: ISSUE_FIXTURES,
    evidence: EVIDENCE_FIXTURES,
    approvals: APPROVAL_FIXTURES,
    decisions: DECISION_FIXTURES,
    auditEvents: AUDIT_EVENT_FIXTURES,
    segments: SEGMENT_FIXTURES,
    priceOpsRecommendations: PRICEOPS_RECOMMENDATION_FIXTURES,
    growthItems: GROWTH_ITEM_FIXTURES,
    heatZones: HEAT_ZONE_FIXTURES,
    listingSources: LISTING_SOURCE_FIXTURES,
    listings: LISTING_FIXTURES,
    candidates: CANDIDATE_FIXTURES,
    siteReviews: SITE_REVIEW_FIXTURES,
    rebalanceStores: REBALANCE_STORE_FIXTURES,
  });
}

export function operatorReducer(state: OperatorState, action: OperatorAction): OperatorState {
  switch (action.type) {
    case "state/reset":
      return createInitialOperatorState(action.roleId ?? state.roleId);
    case "role/switch":
      return switchRole(state, action.roleId);
    case "workspace/select":
      return selectWorkspace(state, action.workspaceId);
    case "issue/transition":
      return transitionIssueState(state, action.issueId, action.status, action);
    case "growth/transition":
      return transitionGrowthState(state, action.growthItemId, action.status, action);
    case "listing/transition":
      return transitionListingState(state, action.listingId, action.status, action);
    case "candidate/transition":
      return transitionCandidateState(state, action.candidateId, action.status, action);
    case "rebalance/transition":
      return transitionRebalanceState(state, action.rebalanceStoreId, action.status, action);
    case "approval/decide":
      return decideApprovalState(state, action.approvalId, action.status, action);
    case "siteReview/decide":
      return decideSiteReviewState(state, action.reviewId, action.status, action);
    case "audit/append":
      return appendAuditEvent(state, {
        ...action.event,
        actorRoleId: action.actorRoleId,
        actorName: resolveActorName(state, action.actorRoleId, action.actorName),
      });
    default:
      return state;
  }
}

export function cloneOperatorState<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function canTransitionIssue(from: IssueStatus, to: IssueStatus): boolean {
  return ISSUE_TRANSITIONS[from].includes(to);
}

export function canTransitionGrowth(from: GrowthStatus, to: GrowthStatus): boolean {
  return GROWTH_TRANSITIONS[from].includes(to);
}

export function canTransitionListing(from: ListingStatus, to: ListingStatus): boolean {
  return LISTING_TRANSITIONS[from].includes(to);
}

export function canTransitionCandidate(from: CandidateStatus, to: CandidateStatus): boolean {
  return CANDIDATE_TRANSITIONS[from].includes(to);
}

export function canTransitionSiteReview(from: SiteReviewStatus, to: SiteReviewStatus): boolean {
  return SITE_REVIEW_TRANSITIONS[from].includes(to);
}

export function canTransitionRebalance(from: RebalanceStatus, to: RebalanceStatus): boolean {
  return REBALANCE_TRANSITIONS[from].includes(to);
}

export function canTransitionApproval(from: ApprovalStatus, to: ApprovalStatus): boolean {
  return APPROVAL_TRANSITIONS[from].includes(to);
}

function switchRole(state: OperatorState, roleId: OperatorRoleId): OperatorState {
  const role = state.roles.find((item) => item.id === roleId);
  if (!role) return state;

  return {
    ...state,
    roleId,
    selectedWorkspace: role.workspaces.includes(state.selectedWorkspace) ? state.selectedWorkspace : role.defaultWorkspace,
  };
}

function selectWorkspace(state: OperatorState, workspaceId: WorkspaceKey): OperatorState {
  const role = state.roles.find((item) => item.id === state.roleId);
  if (!role?.workspaces.includes(workspaceId)) return state;

  return {
    ...state,
    selectedWorkspace: workspaceId,
  };
}

function transitionIssueState(
  state: OperatorState,
  issueId: string,
  status: IssueStatus,
  actor: TransitionAuditInput,
): OperatorState {
  const issue = state.issues.find((item) => item.id === issueId);
  if (!issue || !canTransitionIssue(issue.status, status)) return state;

  const nextState: OperatorState = {
    ...state,
    selectedIssueId: issueId,
    issues: state.issues.map((item) =>
      item.id === issueId
        ? {
            ...item,
            status,
            updatedAt: nextAuditTimestamp(state),
          }
        : item,
    ),
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "issue.transition", "issue", issueId),
    message: `${issueId} moved from ${issue.status} to ${status}.`,
    metadata: { from: issue.status, to: status, note: actor.note },
  });
}

function transitionGrowthState(
  state: OperatorState,
  growthItemId: string,
  status: GrowthStatus,
  actor: TransitionAuditInput,
): OperatorState {
  const growthItem = state.growthItems.find((item) => item.id === growthItemId);
  if (!growthItem || !canTransitionGrowth(growthItem.status, status)) return state;

  const nextState: OperatorState = {
    ...state,
    selectedGrowthItemId: growthItemId,
    growthItems: state.growthItems.map((item) => (item.id === growthItemId ? { ...item, status } : item)),
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "growth.transition", "growthItem", growthItemId),
    message: `${growthItemId} moved from ${growthItem.status} to ${status}.`,
    metadata: { from: growthItem.status, to: status, note: actor.note },
  });
}

function transitionListingState(
  state: OperatorState,
  listingId: string,
  status: ListingStatus,
  actor: TransitionAuditInput,
): OperatorState {
  const listing = state.listings.find((item) => item.id === listingId);
  if (!listing || !canTransitionListing(listing.status, status)) return state;

  const nextState: OperatorState = {
    ...state,
    listings: state.listings.map((item) => (item.id === listingId ? { ...item, status } : item)),
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "listing.transition", "listing", listingId),
    message: `${listingId} moved from ${listing.status} to ${status}.`,
    metadata: { from: listing.status, to: status, note: actor.note },
  });
}

function transitionCandidateState(
  state: OperatorState,
  candidateId: string,
  status: CandidateStatus,
  actor: TransitionAuditInput,
): OperatorState {
  const candidate = state.candidates.find((item) => item.id === candidateId);
  if (!candidate || !canTransitionCandidate(candidate.status, status)) return state;

  const nextState: OperatorState = {
    ...state,
    selectedCandidateId: candidateId,
    candidates: state.candidates.map((item) => (item.id === candidateId ? { ...item, status } : item)),
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "candidate.transition", "candidate", candidateId),
    message: `${candidateId} moved from ${candidate.status} to ${status}.`,
    metadata: { from: candidate.status, to: status, note: actor.note },
  });
}

function transitionRebalanceState(
  state: OperatorState,
  rebalanceStoreId: string,
  status: RebalanceStatus,
  actor: TransitionAuditInput,
): OperatorState {
  const rebalanceStore = state.rebalanceStores.find((item) => item.id === rebalanceStoreId);
  if (!rebalanceStore || !canTransitionRebalance(rebalanceStore.status, status)) return state;

  const nextState: OperatorState = {
    ...state,
    rebalanceStores: state.rebalanceStores.map((item) =>
      item.id === rebalanceStoreId ? { ...item, status } : item,
    ),
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "rebalance.transition", "rebalanceStore", rebalanceStoreId),
    message: `${rebalanceStoreId} moved from ${rebalanceStore.status} to ${status}.`,
    metadata: { from: rebalanceStore.status, to: status, note: actor.note },
  });
}

function decideApprovalState(
  state: OperatorState,
  approvalId: string,
  status: ApprovalDecisionStatus,
  actor: TransitionAuditInput & { reason?: string },
): OperatorState {
  const approval = state.approvals.find((item) => item.id === approvalId);
  if (!approval || !canTransitionApproval(approval.status, status)) return state;

  const decidedAt = nextAuditTimestamp(state);
  const decisionId = nextDecisionId(state);
  const actorName = resolveActorName(state, actor.actorRoleId, actor.actorName);
  const decision = {
    id: decisionId,
    module: approval.module,
    targetType: approval.targetType,
    targetId: approval.targetId,
    approvalId,
    systemRecommendation: approval.systemRecommendation,
    finalDecision: status,
    reason: actor.reason ?? actor.note ?? "",
    actorRoleId: actor.actorRoleId,
    actorName,
    modelVersion: approval.modelVersion,
    datasetSnapshotId: approval.datasetSnapshotId,
    decidedAt,
  };

  const nextState: OperatorState = {
    ...state,
    approvals: state.approvals.map((item) =>
      item.id === approvalId
        ? {
            ...item,
            status,
            reason: actor.reason,
            decidedAt,
            decidedByRoleId: actor.actorRoleId,
            decisionId,
          }
        : item,
    ),
    decisions: [...state.decisions, decision],
    growthItems:
      approval.targetType === "growthItem"
        ? state.growthItems.map((item) =>
            item.id === approval.targetId ? { ...item, status: status === "approved" ? "approved" : "draft" } : item,
          )
        : state.growthItems,
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "approval", "approval.decided", "approval", approvalId),
    message: `${approvalId} was ${status}.`,
    metadata: { targetType: approval.targetType, targetId: approval.targetId, reason: actor.reason },
  });
}

function decideSiteReviewState(
  state: OperatorState,
  reviewId: string,
  status: SiteReviewStatus,
  actor: TransitionAuditInput & { reason?: string },
): OperatorState {
  const review = state.siteReviews.find((item) => item.id === reviewId);
  if (!review || !canTransitionSiteReview(review.status, status)) return state;

  const candidateStatus: CandidateStatus | undefined =
    status === "approved" ? "approved" : status === "rejected" ? "rejected" : status === "returned" ? "wait" : undefined;

  const decidedAt = nextAuditTimestamp(state);
  const decisionId = nextDecisionId(state);
  const actorName = resolveActorName(state, actor.actorRoleId, actor.actorName);
  const candidate = state.candidates.find((c) => c.id === review.candidateId);

  const decision = {
    id: decisionId,
    module: "network" as const,
    targetType: "siteReview" as const,
    targetId: reviewId,
    approvalId: undefined,
    systemRecommendation: candidate?.recommendation ?? "WAIT",
    finalDecision: status === "approved" ? ("approved" as const) : status === "rejected" ? ("rejected" as const) : ("returned" as const),
    reason: actor.reason ?? actor.note ?? "",
    actorRoleId: actor.actorRoleId,
    actorName,
    modelVersion: candidate?.modelVersion ?? "v1.0.0",
    datasetSnapshotId: candidate?.datasetSnapshotId ?? "snap-default",
    decidedAt,
  };

  const nextState: OperatorState = {
    ...state,
    siteReviews: state.siteReviews.map((item) =>
      item.id === reviewId
        ? {
            ...item,
            status,
            reason: actor.reason,
            decidedAt,
          }
        : item,
    ),
    candidates: candidateStatus
      ? state.candidates.map((item) => (item.id === review.candidateId ? { ...item, status: candidateStatus } : item))
      : state.candidates,
    decisions: [...state.decisions, decision],
  };

  return appendAuditEvent(nextState, {
    ...auditBase(state, actor, "workflow", "siteReview.decided", "siteReview", reviewId),
    message: `${reviewId} was ${status}.`,
    metadata: { candidateId: review.candidateId, reason: actor.reason },
  });
}

function appendAuditEvent(
  state: OperatorState,
  event: Omit<AuditEvent, "id" | "occurredAt"> & Partial<Pick<AuditEvent, "id" | "occurredAt">>,
): OperatorState {
  const auditEvent: AuditEvent = {
    ...event,
    id: event.id ?? nextAuditId(state),
    occurredAt: event.occurredAt ?? nextAuditTimestamp(state),
  };

  return {
    ...state,
    auditEvents: [auditEvent, ...state.auditEvents],
  };
}

function auditBase(
  state: OperatorState,
  actor: TransitionAuditInput,
  category: AuditEvent["category"],
  action: string,
  targetType: TargetType,
  targetId: string,
): Omit<AuditEvent, "id" | "occurredAt" | "message"> {
  return {
    actorRoleId: actor.actorRoleId,
    actorName: resolveActorName(state, actor.actorRoleId, actor.actorName),
    category,
    action,
    targetType,
    targetId,
  };
}

function resolveActorName(state: OperatorState, actorRoleId: OperatorRoleId, actorName?: string): string {
  return actorName ?? state.roles.find((role) => role.id === actorRoleId)?.label ?? actorRoleId;
}

function nextAuditId(state: OperatorState): string {
  return `AUD-${String(7000 + state.auditEvents.length + 1).padStart(4, "0")}`;
}

function nextDecisionId(state: OperatorState): string {
  return `DEC-${String(500 + state.decisions.length + 1).padStart(3, "0")}`;
}

function nextAuditTimestamp(state: OperatorState): string {
  return new Date(Date.parse(DEMO_NOW) + state.auditEvents.length * 60_000).toISOString();
}
