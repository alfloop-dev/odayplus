export const OPERATOR_ROLE_IDS = [
  "opsLead",
  "supportLead",
  "facilitiesLead",
  "marketingManager",
  "expansionManager",
  "auditPm",
] as const;

export type OperatorRoleId = (typeof OPERATOR_ROLE_IDS)[number];

export const WORKSPACE_KEYS = ["today", "storeOps", "growth", "network", "govern"] as const;

export type WorkspaceKey = (typeof WORKSPACE_KEYS)[number];

export const ISSUE_STATUSES = [
  "new",
  "triaged",
  "assigned",
  "inprogress",
  "executed",
  "observing",
  "outcomeready",
  "closed",
  "waitingevidence",
  "waitingapproval",
  "escalated",
] as const;

export type IssueStatus = (typeof ISSUE_STATUSES)[number];

export const GROWTH_STATUSES = [
  "candidate",
  "draft",
  "pending",
  "approved",
  "scheduled",
  "running",
  "observing",
  "outcomeready",
  "effective",
  "ineffective",
  "closed",
] as const;

export type GrowthStatus = (typeof GROWTH_STATUSES)[number];

export const LISTING_STATUSES = [
  "new",
  "parsed",
  "geocoded",
  "watching",
  "contacted",
  "visit",
  "candidate",
  "scored",
  "duplicate",
  "hardfail",
  "archived",
  "expired",
] as const;

export type ListingStatus = (typeof LISTING_STATUSES)[number];

export const CANDIDATE_STATUSES = [
  "missingdata",
  "scoring",
  "wait",
  "ready",
  "pendingreview",
  "approved",
  "rejected",
  "blocked",
] as const;

export type CandidateStatus = (typeof CANDIDATE_STATUSES)[number];

export const SITE_REVIEW_STATUSES = ["pending", "approved", "returned", "rejected"] as const;

export type SiteReviewStatus = (typeof SITE_REVIEW_STATUSES)[number];

export const REBALANCE_STATUSES = [
  "watching",
  "avmrequested",
  "avmready",
  "netplanreview",
  "pendingapproval",
  "approved",
  "closed",
] as const;

export type RebalanceStatus = (typeof REBALANCE_STATUSES)[number];

export const APPROVAL_STATUSES = ["pending", "approved", "returned", "rejected", "cancelled"] as const;

export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];
export type ApprovalDecisionStatus = Extract<ApprovalStatus, "approved" | "returned" | "rejected">;

export type OperatorCapability =
  | "workspace:today"
  | "workspace:storeOps"
  | "workspace:growth"
  | "workspace:network"
  | "workspace:govern"
  | "issue:triage"
  | "issue:assign"
  | "issue:execute"
  | "issue:close"
  | "evidence:camera"
  | "review:reply"
  | "growth:draft"
  | "growth:submitApproval"
  | "network:sourceListings"
  | "network:scoreCandidate"
  | "network:submitReview"
  | "rebalance:submit"
  | "approval:decide"
  | "audit:read";

export type OperatorActionKey =
  | "issue.triage"
  | "issue.assign"
  | "issue.execute"
  | "issue.close"
  | "evidence.camera.open"
  | "review.reply"
  | "growth.draft"
  | "growth.submitApproval"
  | "network.sourceListings"
  | "network.scoreCandidate"
  | "network.submitReview"
  | "rebalance.submit"
  | "approval.decide"
  | "audit.read";

export type Severity = "low" | "medium" | "high" | "critical";
export type RiskLevel = "low" | "medium" | "high" | "critical";
export type EvidenceKind = "googleReview" | "csCase" | "camera" | "iot" | "payment" | "forecastOps" | "cleaning";
export type EvidencePolarity = "supporting" | "contrary" | "neutral";
export type ApprovalModule = "storeOps" | "growth" | "network" | "govern";
export type TargetType =
  | "issue"
  | "growthItem"
  | "listing"
  | "candidate"
  | "siteReview"
  | "rebalanceStore"
  | "approval";
export type AuditCategory = "workflow" | "approval" | "policy" | "evidence" | "adapter" | "system";

export type AuditMetadata = Record<string, string | number | boolean | null | undefined>;

export type OperatorRole = {
  id: OperatorRoleId;
  label: string;
  description: string;
  defaultWorkspace: WorkspaceKey;
  workspaces: WorkspaceKey[];
  capabilities: OperatorCapability[];
};

export type NavWorkspace = {
  id: WorkspaceKey;
  label: string;
  shortLabel: string;
  description: string;
  order: number;
  requiredCapability: OperatorCapability;
};

export type StoreLightStatus = "green" | "yellow" | "red";

export type Store = {
  id: string;
  name: string;
  district: string;
  city: string;
  manager: string;
  lights: {
    demand: StoreLightStatus;
    operations: StoreLightStatus;
    staffing: StoreLightStatus;
    margin: StoreLightStatus;
  };
  riskScore: number;
};

export type Issue = {
  id: string;
  title: string;
  storeId: string;
  storeName: string;
  status: IssueStatus;
  severity: Severity;
  source: EvidenceKind | "multiSignal";
  ownerRoleId: OperatorRoleId;
  ownerName: string;
  slaDueAt: string;
  createdAt: string;
  updatedAt: string;
  evidenceIds: string[];
  relatedApprovalId?: string;
  relatedGrowthId?: string;
  summary: string;
};

export type EvidenceItem = {
  id: string;
  issueId: string;
  kind: EvidenceKind;
  title: string;
  sourceLabel: string;
  summary: string;
  polarity: EvidencePolarity;
  confidence: number;
  occurredAt: string;
  lockedReason?: string;
};

export type Segment = {
  id: string;
  name: string;
  size: number;
  churnRisk: RiskLevel;
  primaryNeed: string;
  suggestedGrowthItemId?: string;
};

export type PriceOpsRecommendation = {
  id: string;
  title: string;
  storeIds: string[];
  expectedGrossMarginDelta: number;
  status: "draftable" | "inReview" | "applied" | "rejected";
  suggestedGrowthItemId?: string;
};

export type GrowthItem = {
  id: string;
  title: string;
  type: "opportunity" | "campaign" | "segmentDraft" | "priceOps";
  status: GrowthStatus;
  ownerRoleId: OperatorRoleId;
  storeIds: string[];
  segmentId?: string;
  priceOpsRecommendationId?: string;
  startAt?: string;
  endAt?: string;
  expectedImpact: string;
  conflictIds: string[];
  relatedApprovalId?: string;
};

export type HeatZoneLens = "demand" | "fit" | "competition" | "cannibalization" | "rent" | "traffic" | "unmet" | "confidence";

export type OperatorHeatZone = {
  id: string;
  label: string;
  rank: number;
  centroid: [number, number];
  demandGap: number;
  competitionIndex: number;
  cannibalizationRisk: RiskLevel;
  rentBand: string;
  confidence: number;
  recommendedLens: HeatZoneLens;
  reasons: string[];
  risks: string[];
  nextStep: string;
};

export type ListingSource = {
  id: string;
  name: string;
  status: "connected" | "manualOnly" | "paused";
  complianceNote: string;
  lastSyncedAt?: string;
};

export type Listing = {
  id: string;
  sourceId: string;
  heatZoneId: string;
  address: string;
  status: ListingStatus;
  rentPerMonth: number;
  areaPing: number;
  geocodeConfidence: number;
  duplicateOfId?: string;
  hardRuleFailures: string[];
  candidateId?: string;
};

export type Candidate = {
  id: string;
  listingId?: string;
  heatZoneId: string;
  title: string;
  address: string;
  status: CandidateStatus;
  score: number;
  recommendation: "GO" | "WAIT" | "REJECT";
  modelVersion: string;
  datasetSnapshotId: string;
  missingData: string[];
  reviewId?: string;
};

export type SiteReview = {
  id: string;
  candidateId: string;
  status: SiteReviewStatus;
  requestedByRoleId: OperatorRoleId;
  reviewerRoleIds: OperatorRoleId[];
  requestedAt: string;
  decidedAt?: string;
  reasonRequired: boolean;
  reason?: string;
};

export type RebalanceStore = {
  id: string;
  storeId: string;
  storeName: string;
  status: RebalanceStatus;
  avmRequestId?: string;
  netPlanOptionId?: string;
  relatedApprovalId?: string;
  summary: string;
};

export type Approval = {
  id: string;
  module: ApprovalModule;
  targetType: TargetType;
  targetId: string;
  title: string;
  status: ApprovalStatus;
  risk: RiskLevel;
  requestedByRoleId: OperatorRoleId;
  requiredRoleIds: OperatorRoleId[];
  requestedAt: string;
  decidedAt?: string;
  decidedByRoleId?: OperatorRoleId;
  decisionId?: string;
  reason?: string;
  systemRecommendation: string;
  modelVersion: string;
  datasetSnapshotId: string;
};

export type Decision = {
  id: string;
  module: ApprovalModule;
  targetType: TargetType;
  targetId: string;
  approvalId?: string;
  systemRecommendation: string;
  finalDecision: ApprovalDecisionStatus | "wait" | "go" | "override";
  reason: string;
  actorRoleId: OperatorRoleId;
  actorName: string;
  modelVersion: string;
  datasetSnapshotId: string;
  decidedAt: string;
};

export type AuditEvent = {
  id: string;
  occurredAt: string;
  actorRoleId: OperatorRoleId;
  actorName: string;
  category: AuditCategory;
  action: string;
  targetType: TargetType;
  targetId: string;
  message: string;
  metadata?: AuditMetadata;
};

export type OperatorState = {
  roleId: OperatorRoleId;
  selectedWorkspace: WorkspaceKey;
  selectedIssueId: string;
  selectedGrowthItemId: string;
  selectedHeatZoneId: string;
  selectedCandidateId: string;
  roles: OperatorRole[];
  navWorkspaces: NavWorkspace[];
  stores: Store[];
  issues: Issue[];
  evidence: EvidenceItem[];
  approvals: Approval[];
  decisions: Decision[];
  auditEvents: AuditEvent[];
  segments: Segment[];
  priceOpsRecommendations: PriceOpsRecommendation[];
  growthItems: GrowthItem[];
  heatZones: OperatorHeatZone[];
  listingSources: ListingSource[];
  listings: Listing[];
  candidates: Candidate[];
  siteReviews: SiteReview[];
  rebalanceStores: RebalanceStore[];
};

export type TransitionAuditInput = {
  actorRoleId: OperatorRoleId;
  actorName?: string;
  note?: string;
};

export type OperatorAction =
  | { type: "state/reset"; roleId?: OperatorRoleId }
  | { type: "role/switch"; roleId: OperatorRoleId }
  | { type: "workspace/select"; workspaceId: WorkspaceKey }
  | ({ type: "issue/transition"; issueId: string; status: IssueStatus } & TransitionAuditInput)
  | ({ type: "growth/transition"; growthItemId: string; status: GrowthStatus } & TransitionAuditInput)
  | ({ type: "listing/transition"; listingId: string; status: ListingStatus } & TransitionAuditInput)
  | ({ type: "candidate/transition"; candidateId: string; status: CandidateStatus } & TransitionAuditInput)
  | ({ type: "rebalance/transition"; rebalanceStoreId: string; status: RebalanceStatus } & TransitionAuditInput)
  | ({ type: "approval/decide"; approvalId: string; status: ApprovalDecisionStatus; reason?: string } & TransitionAuditInput)
  | ({ type: "siteReview/decide"; reviewId: string; status: SiteReviewStatus; reason?: string } & TransitionAuditInput)
  | ({ type: "audit/append"; event: Omit<AuditEvent, "id" | "occurredAt" | "actorName"> } & TransitionAuditInput);

export type OperatorStateListener = (state: OperatorState) => void;

export type OperatorConsoleAdapter = {
  loadState(roleId?: OperatorRoleId): Promise<OperatorState>;
  dispatch(action: OperatorAction): Promise<OperatorState>;
  resetState(roleId?: OperatorRoleId): Promise<OperatorState>;
  saveState(state: OperatorState): Promise<OperatorState>;
  subscribe(listener: OperatorStateListener): () => void;
};

export type ActionAvailability = {
  allowed: boolean;
  reason?: string;
  requiredCapability?: OperatorCapability;
};
