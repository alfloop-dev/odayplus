// Types for the R4 Network SiteScore scoring surface served by
// /api/v1/operator/network-scoring. They mirror NetworkScoringService's JSON
// so CandidatePanel / SiteScorePanel / ComparePanel can render the data-gate,
// scorecards, and Compare recommendation without re-deriving them client-side.

export type ScoreRecommendation = "GO" | "WAIT" | "REJECT" | "INVESTIGATE";

export type ScoringGateCheck = {
  key: string;
  label: string;
  state: "ok" | "warn" | "fail";
  note: string;
};

export type ScoringGate = {
  state: string;
  passed: boolean;
  missing: string[];
  otherMissing: string[];
  blockNote: string;
  checks: ScoringGateCheck[];
  okCount: number;
  totalCount: number;
};

export type CandidateReadField = {
  value: string | number | string[] | null;
  unit: string | null;
  availability: "recorded" | "unmeasured" | "denied" | "invalid";
  reason: string | null;
  provenance: {
    recordType: string;
    recordId: string;
    fieldPath: string;
    snapshotId: string | null;
    measurementStatus?: string;
  };
};

export type CandidateReadEvidence = {
  source: "canonical";
  candidateId: string;
  listingId: string;
  addressId: string;
  datasetSnapshotId: string | null;
  fields: Record<"address" | "geocode" | "rent" | "area" | "floor" | "hardRule" | "sourceConfidence", CandidateReadField>;
};

export type CurrentScoreAdmission = {
  candidateId: string;
  listingId: string;
  addressId: string;
  checkedAt: string | null;
  currentInputs: { state: "verified" | "blocked" | "unverified"; reasonCode: string | null };
  source: CurrentScoreAdmission["currentInputs"] & {
    jobId?: string; intakeId?: string; promotionDecisionId?: string;
  };
  pit: CurrentScoreAdmission["currentInputs"];
  model: CurrentScoreAdmission["currentInputs"];
};

// A future producer can supply these reads without turning model outcomes or
// comparison membership into a persisted human workflow state.
export type CandidateWorkflowStage = "scoring" | "shortlisted" | "pendingreview" | "approved" | "rejected" | "onhold";
export type CandidateWorkflowRead = {
  source: "canonical";
  candidateId: string;
  availability: "recorded" | "unavailable" | "denied" | "conflict";
  matches: boolean | null;
  producer: string;
  recordId: string | null;
  status: string | null;
  reason: string | null;
};

// Read identities derived from already authorized snapshots. A historical
// decision never replaces the latest report expectation or grants approval.
export type CandidateReviewReadTarget = {
  candidateId: string;
  listingId: string;
  reviewId: string;
  reportId: string;
  reportVersion: number;
  latestReportId: string | null;
  latestReportVersion: number | null;
  generation: number;
  relation: "current" | "historical";
};
export type CandidateNativeStage = "needdata" | "ready" | "scoring" | "evaluated" | "pendingreview" | "approved" | "rejected" | "onhold";
export type CandidateNativeWorkflowRead = {
  candidateId: string;
  stage: CandidateNativeStage | null;
  availability: "recorded" | "unavailable" | "conflict";
  lifecycle: string | null;
  reason: string;
  review: CandidateReviewReadTarget | null;
  scoreJob?: import("./candidateScoreJobs").CandidateScoreJobRead;
};
export type CandidateNativeWorkflowReads = Readonly<Record<string, CandidateNativeWorkflowRead>>;

export type ScoringCandidate = {
  id: string;
  listingId?: string | null;
  heatZoneId: string;
  title: string;
  zoneLabel: string;
  address: string | null;
  district?: string;
  modelVersion: string | null;
  datasetSnapshotId: string;
  stage: string;
  gate: ScoringGate;
  scored: boolean;
  score: number | null;
  recommendation: ScoreRecommendation | null;
  reviewId?: string | null;
  inCompare: boolean;
  readEvidence?: CandidateReadEvidence;
  currentAdmission?: CurrentScoreAdmission;
  pipelineEvidence?: Partial<Record<CandidateWorkflowStage, CandidateWorkflowRead>>;
};

export type RevenueValue = number | { p10: number; p50: number; p90: number } | null;

export type ScoreCard = {
  id: string;
  title: string;
  zoneLabel: string;
  heatZoneId: string;
  score: number | null;
  recommendation: ScoreRecommendation;
  modelVersion: string;
  // Canonical reports carry immutable lineage; legacy visual fixtures omit it.
  reportId?: string;
  reportVersion?: number;
  sitescoreRunId?: string;
  datasetSnapshotId: string;
  generatedAt: string;
  confidence: string | number | null;
  confidenceStatus?: "measured" | "unmeasured";
  payback: string | number | null;
  revenuePath: { m1: RevenueValue; m3: RevenueValue; m6: RevenueValue; m12: RevenueValue };
  band: { p10: string | number; p50: string | number; p90: string | number };
  subScores: {
    rentReasonableness?: string | number | null;
    cannibalization?: string | number | null;
    competition?: string | number | null;
    demand?: string | number | null;
    poiFit?: string | number | null;
    access?: string | number | null;
  };
  capex: string | null;
  rentAssumption: string | null;
  drivers: string[];
  reasons: string[];
  risks: string[];
  conditions: string[];
  conditionTitle: string;
  reviewId?: string | null;
};

export type BatchResultRow = {
  rank: number | null;
  priority: string | null;
  id: string;
  title: string;
  score: number | null;
  recommendation: ScoreRecommendation;
  m12P50: string | number;
  payback: string | number | null;
  cannibalization: string | number | null;
  inCompare: boolean;
};

export type CompareColumn = {
  id: string;
  title: string;
  priority: string | null;
  recommendation: ScoreRecommendation;
  score: number | null;
  isBest: boolean;
};

export type CompareMetric = {
  key: string;
  label: string;
  values: Array<{ id: string; text: string; isBest: boolean }>;
};

export type CompareRecommendationCard = {
  id: string;
  title: string;
  recommendation: ScoreRecommendation;
  score: number | null;
  text: string;
  why?: string[];
};

export type CompareRecommendation = {
  primary: CompareRecommendationCard;
  alternate: CompareRecommendationCard | null;
  avoid: CompareRecommendationCard | null;
  priorityList: Array<{
    priority: string;
    id: string;
    title: string;
    score: number | null;
    recommendation: ScoreRecommendation;
  }>;
};

export type NetworkScoringCompare = {
  columns: CompareColumn[];
  metrics: CompareMetric[];
  recommendation: CompareRecommendation | null;
  empty: boolean;
};

export type NetworkScoringSnapshot = {
  source?: "api" | "canonical" | "fixture";
  modelVersion: string | null;
  candidates: ScoringCandidate[];
  scorecards: ScoreCard[];
  batchResults: BatchResultRow[];
  compare: NetworkScoringCompare;
  compareSet: string[];
  counts?: { candidates: number; scored: number; gateBlocked: number };
};

export function recommendationTone(recommendation: ScoreRecommendation | null): "good" | "watch" | "risk" {
  if (recommendation === "GO") return "good";
  if (recommendation === "REJECT") return "risk";
  return "watch";
}

export type ScoreExecutionContext = {
  tenantId: string;
  candidateSiteId: string;
  reportId: string;
  reportVersion: number;
  jobVersion: number;
  originalJobId: string;
  predecessorJobId: string;
  activeExecutionRequest?: ScoreExecutionReceipt | null;
};

export type ScoreExecutionReceipt = {
  source: "canonical";
  created: boolean;
  executionRequestId: string;
  candidateSiteId: string;
  jobId: string;
  status: "QUEUED" | "RUNNING" | "RETRYING" | "SUCCEEDED" | "FAILED" | "DEAD_LETTER" | "CANCELLED";
  actualVersion: number;
  jobVersion: number | null;
  baseReportId: string;
  baseReportVersion: number;
  result: { report_id: string; report_version: number; sitescore_run_id: string; prediction_run_id: string; model_version: string } | null;
  lastError: { code: string; retryable: boolean } | null;
  correlationId: string;
};


/** The current accepted command; shared request GET keeps its original creator/correlation. */
export type ScoreExecutionCommandReceipt = {
  operation: "create" | "retry";
  actorSubjectId: string;
  tenantId: string;
  idempotencyKey: string;
  correlationId: string;
  executionRequestId: string;
  jobId: string;
};

export type ScoreExecutionCommandIntent = Pick<ScoreExecutionCommandReceipt,
  "operation" | "actorSubjectId" | "tenantId" | "idempotencyKey" | "correlationId">;
