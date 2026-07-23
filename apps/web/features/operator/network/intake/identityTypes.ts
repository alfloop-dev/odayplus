import type { MatchOutcome } from "@oday-plus/openapi-client";

export const IDENTITY_COMPARISON_FIELD_ORDER = [
  "sourceId",
  "canonicalUrl",
  "address",
  "area",
  "floor",
  "listingType",
  "rentOrPrice",
  "status",
] as const;

export type IdentityComparisonFieldKey = (typeof IDENTITY_COMPARISON_FIELD_ORDER)[number];
export type IdentityComparisonState = "MATCH" | "CHANGED" | "CONTRADICTION" | "MISSING" | "MASKED";

export type IdentityComparableValue = {
  value: string | number | boolean | null;
  displayValue: string;
  masked?: boolean;
};

export type IdentityComparisonField = {
  current: IdentityComparableValue | null;
  submitted: IdentityComparableValue | null;
  state: IdentityComparisonState;
  detail: string;
};

export type IdentitySignal = {
  key: string;
  label: string;
  detail: string;
};

export type IdentityComparisonContract = {
  matchCaseId: string;
  matchCaseVersion: number;
  outcome: MatchOutcome;
  confidence: number;
  summary: string;
  currentListingId: string | null;
  currentPropertyId: string | null;
  submittedIntakeId: string;
  submittedSnapshotId: string | null;
  submittedParserRunId: string | null;
  fields: Record<IdentityComparisonFieldKey, IdentityComparisonField>;
  agreeingSignals: IdentitySignal[];
  contradictingSignals: IdentitySignal[];
};

export type IdentityOutcomeAction =
  | "CREATE"
  | "APPEND_REVISION"
  | "MARK_DUPLICATE"
  | "SEND_TO_STEWARD"
  | "REJECT"
  | "QUARANTINE";

export const IDENTITY_OUTCOME_ACTIONS: Record<MatchOutcome, readonly IdentityOutcomeAction[]> = {
  NEW: ["CREATE", "SEND_TO_STEWARD", "REJECT", "QUARANTINE"],
  EXACT_DUPLICATE: ["MARK_DUPLICATE", "SEND_TO_STEWARD", "REJECT", "QUARANTINE"],
  REVISION: ["APPEND_REVISION", "SEND_TO_STEWARD", "REJECT", "QUARANTINE"],
  POSSIBLE_MATCH: [
    "CREATE",
    "APPEND_REVISION",
    "MARK_DUPLICATE",
    "SEND_TO_STEWARD",
    "REJECT",
    "QUARANTINE",
  ],
  QUARANTINED: ["SEND_TO_STEWARD", "REJECT", "QUARANTINE"],
};

export const IDENTITY_ACTION_LABEL: Record<IdentityOutcomeAction, string> = {
  CREATE: "建立新物件",
  APPEND_REVISION: "加入既有物件版本",
  MARK_DUPLICATE: "標記重複",
  SEND_TO_STEWARD: "送交資料管理員",
  REJECT: "拒絕收件",
  QUARANTINE: "隔離收件",
};

export type IdentityGraphOperation = "MERGE" | "SPLIT" | "UNMERGE" | "REVERSAL";

export type IdentityGraphNode = {
  nodeId: string;
  nodeType: "PROPERTY" | "LISTING" | "SOURCE_IDENTITY" | "CANDIDATE_SITE";
  label: string;
  effective: boolean;
  version: number | null;
};

export type IdentityGraphEdge = {
  edgeId: string;
  fromNodeId: string;
  toNodeId: string;
  relation: string;
  effectiveFrom: string;
  effectiveTo: string | null;
  supersedesEdgeId: string | null;
};

export type IdentityGraphSnapshot = {
  nodes: IdentityGraphNode[];
  edges: IdentityGraphEdge[];
};

export type IdentityActor = {
  subjectId: string;
  displayName: string;
  role: string;
};

export type IdentityGraphPlan = {
  planId: string;
  operation: IdentityGraphOperation;
  state: "DRAFT" | "PENDING_REVIEW" | "APPROVED" | "EXECUTING" | "EXECUTED" | "REVERSAL_PENDING";
  expectedGraphVersion: number;
  originalDecisionId: string | null;
  proposer: IdentityActor;
  requestedReviewer: IdentityActor | null;
  before: IdentityGraphSnapshot;
  after: IdentityGraphSnapshot;
  redirects: Array<{
    fromPropertyId: string;
    toPropertyId: string;
    disposition: "CREATE" | "CLOSE" | "REVERSE";
  }>;
  candidateImpacts: Array<{
    candidateSiteId: string;
    disposition: "KEEP_HISTORICAL" | "REASSIGN" | "REQUIRE_REVIEW";
    targetPropertyId: string | null;
  }>;
  lineageImpact: string[];
  riskSummary: string;
};

export type IdentityDecisionStatus =
  | "DRAFT"
  | "PENDING_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "EXECUTING"
  | "EXECUTED"
  | "FAILED"
  | "REVERSAL_PENDING"
  | "REVERSED"
  | "SUPERSEDED";

export type IdentityReviewWorkflow = {
  status: IdentityDecisionStatus;
  currentActor: IdentityActor;
  proposer: IdentityActor;
  reviewer: IdentityActor | null;
  decisionId: string | null;
  requiresIndependentReview: boolean;
  canPropose: boolean;
  canReview: boolean;
  denialReasonCode: string | null;
  proposal: {
    outcomeAction: IdentityOutcomeAction | null;
    graphOperation: IdentityGraphOperation | null;
    graphPlanId: string | null;
    reason: string;
    riskAcknowledged: boolean;
  } | null;
};

export type IdentityDecisionDraft = {
  commandType: "OUTCOME" | "GRAPH";
  outcomeAction: IdentityOutcomeAction | null;
  graphOperation: IdentityGraphOperation | null;
  graphPlanId: string | null;
  reason: string;
  riskAcknowledged: boolean;
};

export type IdentityDecisionCommand = {
  phase: "PROPOSE" | "REVIEW";
  reviewDisposition: "APPROVE" | "REJECT" | null;
  matchCaseId: string;
  matchCaseVersion: number;
  decisionId: string | null;
  outcomeAction: IdentityOutcomeAction | null;
  graphOperation: IdentityGraphOperation | null;
  graphPlanId: string | null;
  expectedGraphVersion: number | null;
  reason: string;
  riskAcknowledged: boolean;
  proposerId: string;
  reviewerId: string | null;
  requiresIndependentReview: boolean;
};

export type IdentityDecisionReceipt = {
  decisionId: string;
  status: IdentityDecisionStatus;
  outcomeAction: IdentityOutcomeAction | null;
  graphOperation: IdentityGraphOperation | null;
  graphPlanId: string | null;
  originalDecisionId: string | null;
  matchCaseId: string;
  proposer: IdentityActor;
  reviewer: IdentityActor | null;
  reason: string;
  riskAcknowledged: boolean;
  occurredAt: string;
  resourceVersions: Record<string, number>;
  listingId: string | null;
  listingRevisionId: string | null;
  effectiveEdgeIds: string[];
  supersededEdgeIds: string[];
  redirectIds: string[];
  auditEventId: string;
  correlationId: string;
  lineageImpact: string[];
};

export type IdentityConflict = {
  code: "VERSION_CONFLICT" | "REVIEW_CONFLICT" | "DEPENDENCY_CONFLICT" | string;
  summary: string;
  currentVersion: number;
  currentState: string;
  currentOwner: string | null;
  correlationId: string;
  occurredAt: string;
  nextAction: string;
};

export const IDENTITY_FIELD_LABEL: Record<IdentityComparisonFieldKey, string> = {
  sourceId: "來源 ID",
  canonicalUrl: "規範網址",
  address: "地址",
  area: "坪數／面積",
  floor: "樓層",
  listingType: "物件類型",
  rentOrPrice: "租金／價格",
  status: "物件狀態",
};

export function defaultOutcomeAction(outcome: MatchOutcome): IdentityOutcomeAction {
  return IDENTITY_OUTCOME_ACTIONS[outcome][0];
}

export function commandRequiresIndependentReview(
  outcome: MatchOutcome,
  draft: IdentityDecisionDraft,
): boolean {
  if (draft.commandType === "GRAPH") return true;
  return outcome === "POSSIBLE_MATCH" || draft.outcomeAction === "QUARANTINE";
}
