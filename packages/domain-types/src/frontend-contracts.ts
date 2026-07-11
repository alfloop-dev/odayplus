import type { AuditMeta, Confidence, DataQuality, FieldVisibility, Interval } from "./common.ts";
import type { DecisionStatus, FourLight, JobStatus, ModelStatus, RiskLevel } from "./status.ts";

/**
 * Frontend domain contracts for the shared UI and ui-domain packages.
 *
 * Source of truth:
 * - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md §5
 * - docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md
 *
 * These types intentionally model UI-facing evidence and decision surfaces, not
 * backend persistence models. They keep the frontend workers aligned while the
 * shared component packages are completed.
 */

export type EntityRef = {
  entityType: string;
  entityId: string;
  label: string;
};

export type PermissionScope = {
  tenantId?: string;
  brandId?: string;
  regionId?: string;
  storeId?: string;
  module?: string;
};

export type FieldPermission = {
  field: string;
  visibility: FieldVisibility;
  reason?: string;
};

export type Factor = {
  label: string;
  value?: string | number;
  impact?: "positive" | "negative" | "neutral";
  evidenceStrength?: number;
};

export type ComparableStore = {
  storeId: string;
  distance?: number;
  locationType?: string;
  formatSimilarity?: number;
  machineMixSimilarity?: number;
  storeAge?: number;
  revenueM3?: number;
  revenueM6?: number;
  revenueM12?: number;
  similarityScore: number;
};

export type CandidateListingStatus =
  | "RAW"
  | "PARSED"
  | "GEOCODED"
  | "DUPLICATE"
  | "FAILED_HARD_RULE"
  | "CANDIDATE"
  | "SCORED"
  | "REJECTED";

export type CandidateSiteCardContract = {
  candidateSiteId: string;
  address: string;
  geocodeConfidence: Confidence;
  rent?: number;
  area?: number;
  frontage?: number;
  floor?: string;
  parkingOrTemporaryStop?: string;
  feasibilityFlags: string[];
  heatZone: EntityRef;
  listingSource: string;
  status: CandidateListingStatus;
  fieldPermissions?: FieldPermission[];
  dataQuality?: DataQuality;
};

export type SiteScoreRecommendation = "GO" | "WAIT" | "REJECT" | "INVESTIGATE";

export type SiteScoreReportSummaryContract = {
  reportId: string;
  candidateSite: EntityRef;
  recommendation: SiteScoreRecommendation;
  m1: Interval;
  m3: Interval;
  m6: Interval;
  m12: Interval;
  mature?: Interval;
  paybackPeriod: Interval;
  rentReasonableness: RiskLevel;
  cannibalizationRisk: RiskLevel;
  comparableStores: ComparableStore[];
  keyPositiveFactors: Factor[];
  keyNegativeFactors: Factor[];
  confidence: Confidence;
  modelVersion: string;
  policyVersion?: string;
  featureSnapshotTime: string;
  decisionStatus: DecisionStatus;
  dataQuality?: DataQuality;
  audit?: AuditMeta;
};

export type ForecastMetric = "revenue" | "gross_margin" | "transactions" | "utilization";
export type ForecastHorizon = "4w" | "8w" | "12w" | "24w";
export type ForecastGranularity = "daily" | "weekly";

export type ForecastBandPoint = {
  date: string;
  actual?: number;
  forecastP10: number;
  forecastP50: number;
  forecastP90: number;
  siteScoreBaseline?: number;
  interventionMarker?: EntityRef;
  anomaly?: boolean;
  modelVersion?: string;
};

export type ForecastBandChartContract = {
  store: EntityRef;
  metric: ForecastMetric;
  horizon: ForecastHorizon;
  granularity: ForecastGranularity;
  points: ForecastBandPoint[];
  confidence: Confidence;
  dataQuality?: DataQuality;
};

export type FourLightBadgeContract = {
  light: FourLight;
  triggerConditions: string[];
  alertHref?: string;
};

export type RootCauseCategory =
  | "Revenue Residual"
  | "Store-age Ramp"
  | "Seasonality"
  | "Equipment Availability"
  | "Cost Unit"
  | "Customer Experience"
  | "Price"
  | "Ad"
  | "Promotion"
  | "Competitor"
  | "External Shock";

export type RootCauseEvidenceCardContract = {
  causeCandidate: RootCauseCategory;
  evidenceStrength: number;
  supportingSignals: Factor[];
  contradictingSignals: Factor[];
  dataConfidence: Confidence;
  recommendedNextCheck: string;
  dataQuality?: DataQuality;
};

export type InterventionTimelineStep =
  | "Triggered"
  | "Eligibility checked"
  | "Action built"
  | "Conflict checked"
  | "Approved"
  | "Executed"
  | "Observation started"
  | "Outcome collected"
  | "Effect evaluated"
  | "Closed";

export type TimelineEvent = {
  timestamp: string;
  actor: string;
  eventType: string;
  status: string;
  description: string;
  relatedArtifact?: EntityRef;
};

export type InterventionTimelineContract = {
  interventionId: string;
  store: EntityRef;
  interventionType: string;
  eligibilityStatus: string;
  conflictStatus: string;
  approvalStatus: DecisionStatus;
  executionStatus: string;
  observationWindow: { startsAt: string; endsAt: string };
  outcomeStatus: DecisionStatus;
  evidenceLevel: "high" | "medium" | "low" | "insufficient";
  nodes: Array<TimelineEvent & { step: InterventionTimelineStep }>;
  audit?: AuditMeta;
};

export type PricingPlanComparisonContract = {
  plan: EntityRef;
  currentPrice: number;
  candidatePrice: number;
  priceChange: number;
  expectedDemand: Interval;
  expectedRevenue: Interval;
  expectedGrossMargin: Interval;
  risk: RiskLevel;
  constraintStatus: "PASS" | "WARNING" | "VIOLATION";
  hardConstraintViolations: string[];
  rollbackPlan: string;
  approvalStatus: DecisionStatus;
  dataQuality?: DataQuality;
};

export type AdLiftReportCardContract = {
  campaign: EntityRef;
  treatmentStores: EntityRef[];
  controlStores: EntityRef[];
  preTrendStatus: "PASS" | "FAILED" | "INSUFFICIENT";
  incrementalRevenue: Interval;
  incrementalGrossMargin: Interval;
  iromi: Interval;
  evidenceLevel: "high" | "medium" | "low" | "insufficient";
  continueStopRecommendation: "CONTINUE" | "STOP" | "INVESTIGATE";
  contaminationWarnings: string[];
  dataQuality?: DataQuality;
};

export type ValuationLens = "income" | "asset" | "market" | "blended";

export type ValuationRangeChartContract = {
  valuation: EntityRef;
  fairValue: Interval;
  reservePrice?: number;
  askingPrice?: number;
  lensRanges: Partial<Record<ValuationLens, Interval>>;
  comparableTransactionMarkers: number[];
  liquidityScore: number;
  dataRoomCompleteness: Record<string, "complete" | "partial" | "missing" | "blocked">;
  financeApprovalStatus: DecisionStatus;
  fieldPermissions?: FieldPermission[];
  dataQuality?: DataQuality;
};

export type NetPlanAction = "OPEN" | "KEEP" | "IMPROVE" | "MOVE" | "EXIT" | "HOLD";

export type InfeasibilityDiagnosis = {
  violatedConstraint: string;
  affectedStores: EntityRef[];
  requiredRelaxation: string;
  businessImpact: string;
  suggestedAction: string;
};

export type NetPlanScenarioCardContract = {
  scenarioName: string;
  objectiveValue: number;
  actionCounts: Record<NetPlanAction, number>;
  budgetUsage: { used: number; limit: number; unit: string };
  expectedGrossMargin: Interval;
  risk: RiskLevel;
  bindingConstraints: string[];
  solverStatus: JobStatus;
  alternativePlanAvailable: boolean;
  approvalStatus: DecisionStatus;
  infeasibilityDiagnosis?: InfeasibilityDiagnosis[];
  dataQuality?: DataQuality;
};

export type ModelReleaseCardContract = {
  modelId: string;
  version: string;
  championOrChallenger: "CHAMPION" | "CHALLENGER";
  metricSummary: Record<string, number | string>;
  segmentRegression: string[];
  dataQualityStatus: DataQuality["status"];
  driftStatus: DataQuality["status"];
  releaseStage: ModelStatus;
  rollbackTarget?: { modelId: string; version: string };
  approvalStatus: DecisionStatus;
  audit?: AuditMeta;
};

export type DecisionAuditStep =
  | "Prediction generated"
  | "Recommendation generated"
  | "Human review requested"
  | "Human decision submitted"
  | "Execution started"
  | "Outcome observed"
  | "Feedback written to label registry";

export type DecisionAuditTimelineContract = {
  decisionId: string;
  entity: EntityRef;
  modelVersion?: string;
  featureSnapshotTime?: string;
  actor: string;
  decisionTime: string;
  executionStatus?: DecisionStatus;
  outcomeStatus?: DecisionStatus;
  auditStatus: "READY" | "PARTIAL" | "MISSING" | "EXPORTING";
  nodes: Array<TimelineEvent & { step: DecisionAuditStep }>;
};

export type JobProgressContract = {
  jobId: string;
  jobType: string;
  status: JobStatus;
  submittedAt: string;
  startedAt?: string;
  elapsedTime?: string;
  estimatedStage?: string;
  progressMessage?: string;
  retryCount: number;
  correlationId: string;
};

export type FrontendDomainComponentKey =
  | "HeatZoneScoreCard"
  | "CandidateSiteCard"
  | "SiteScoreReportSummary"
  | "ForecastBandChart"
  | "FourLightBadge"
  | "RootCauseEvidenceCard"
  | "InterventionTimeline"
  | "PricingPlanComparison"
  | "AdLiftReportCard"
  | "ValuationRangeChart"
  | "NetPlanScenarioCard"
  | "ModelReleaseCard"
  | "DecisionAuditTimeline";

export const FRONTEND_DOMAIN_COMPONENT_KEYS: readonly FrontendDomainComponentKey[] = [
  "HeatZoneScoreCard",
  "CandidateSiteCard",
  "SiteScoreReportSummary",
  "ForecastBandChart",
  "FourLightBadge",
  "RootCauseEvidenceCard",
  "InterventionTimeline",
  "PricingPlanComparison",
  "AdLiftReportCard",
  "ValuationRangeChart",
  "NetPlanScenarioCard",
  "ModelReleaseCard",
  "DecisionAuditTimeline",
] as const;

export const FRONTEND_DOMAIN_TYPE_COVERAGE = {
  HeatZoneScoreCard: ["HeatZoneScore"],
  CandidateSiteCard: ["CandidateSiteCardContract"],
  SiteScoreReportSummary: ["SiteScoreReportSummaryContract"],
  ForecastBandChart: ["ForecastBandChartContract", "ForecastBandPoint"],
  FourLightBadge: ["FourLightBadgeContract"],
  RootCauseEvidenceCard: ["RootCauseEvidenceCardContract"],
  InterventionTimeline: ["InterventionTimelineContract"],
  PricingPlanComparison: ["PricingPlanComparisonContract"],
  AdLiftReportCard: ["AdLiftReportCardContract"],
  ValuationRangeChart: ["ValuationRangeChartContract"],
  NetPlanScenarioCard: ["NetPlanScenarioCardContract"],
  ModelReleaseCard: ["ModelReleaseCardContract"],
  DecisionAuditTimeline: ["DecisionAuditTimelineContract"],
} satisfies Record<FrontendDomainComponentKey, readonly string[]>;
