import type {
  AdLiftReportCardContract,
  CandidateSiteCardContract,
  DataQuality,
  DecisionAuditTimelineContract,
  ForecastBandChartContract,
  FourLightBadgeContract,
  HeatZoneScore,
  InterventionTimelineContract,
  ModelReleaseCardContract,
  NetPlanScenarioCardContract,
  PricingPlanComparisonContract,
  RootCauseEvidenceCardContract,
  SiteScoreReportSummaryContract,
  ValuationRangeChartContract,
} from "@oday-plus/domain-types";

export const domainDataQualityFixture: DataQuality = {
  status: "FRESH",
  snapshotTime: "2026-06-29T00:00:00Z",
  sources: ["pos_daily", "listing_ingest", "weather"],
  warnings: [],
};

export const heatZoneScoreFixture: HeatZoneScore = {
  heat_zone_id: "hz-tpe-001",
  h3_index: "884f199a2bfffff",
  h3_resolution: 8,
  score: 87.4,
  priority_rank: 3,
  unmet_demand_score: 91,
  format_fit_score: 84,
  cannibalization_risk_score: 21,
  rent_feasibility_score: 78,
  listing_availability_score: 66,
  confidence: 0.82,
  state: "STILL_EXPANDABLE",
  feature_snapshot_time: "2026-06-28T16:00:00Z",
  prediction_origin_time: "2026-06-28T17:00:00Z",
  last_scored_at: "2026-06-28T17:10:00Z",
  model_version: "heatzone-r3.4.1",
  feature_version: "features-2026-06-28",
  source_snapshot_ids: ["snapshot-001"],
  reasons: ["High unmet demand", "Comparable formats ramped quickly"],
  warnings: ["One listing source is partial"],
  admin_city: "Taipei",
  admin_district: "Da'an",
};

export const candidateSiteFixture: CandidateSiteCardContract = {
  candidateSiteId: "cand-101",
  address: "No. 88, Demo Rd, Taipei",
  geocodeConfidence: { level: "medium", reasons: ["Rooftop match unavailable", "Parcel centroid verified"] },
  rent: 260000,
  area: 54,
  frontage: 8.2,
  floor: "1F",
  parkingOrTemporaryStop: "Temporary stop allowed after 20:00",
  feasibilityFlags: ["Frontage pass", "Rent requires review"],
  heatZone: { entityType: "heat_zone", entityId: "hz-tpe-001", label: "Da'an East" },
  listingSource: "broker_feed",
  status: "CANDIDATE",
  fieldPermissions: [{ field: "rent", visibility: "masked", reason: "finance-only" }],
  dataQuality: domainDataQualityFixture,
};

export const siteScoreReportFixture: SiteScoreReportSummaryContract = {
  reportId: "ssr-9001",
  candidateSite: { entityType: "candidate_site", entityId: "cand-101", label: "Demo Rd 88" },
  recommendation: "INVESTIGATE",
  m1: { p10: 720000, p50: 840000, p90: 980000, unit: "TWD" },
  m3: { p10: 2400000, p50: 2760000, p90: 3180000, unit: "TWD" },
  m6: { p10: 5100000, p50: 5900000, p90: 6800000, unit: "TWD" },
  m12: { p10: 10600000, p50: 12400000, p90: 14100000, unit: "TWD" },
  mature: { p10: 12000000, p50: 14800000, p90: 16900000, unit: "TWD" },
  paybackPeriod: { p10: 18, p50: 24, p90: 33, unit: "months" },
  rentReasonableness: "medium",
  cannibalizationRisk: "high",
  comparableStores: [
    { storeId: "store-a", distance: 1.2, locationType: "street", similarityScore: 0.82, revenueM6: 6200000 },
    { storeId: "store-b", distance: 1.8, locationType: "corner", similarityScore: 0.76, revenueM6: 5800000 },
  ],
  keyPositiveFactors: [{ label: "Unmet demand", value: 91, impact: "positive", evidenceStrength: 0.86 }],
  keyNegativeFactors: [{ label: "Cannibalization risk", value: 0.38, impact: "negative", evidenceStrength: 0.71 }],
  confidence: { level: "medium", reasons: ["Comparable sample is moderate", "Rent terms still provisional"] },
  modelVersion: "sitescore-r2.8.0",
  policyVersion: "site-policy-2026-06",
  featureSnapshotTime: "2026-06-28T16:00:00Z",
  decisionStatus: "PENDING_REVIEW",
  dataQuality: domainDataQualityFixture,
  audit: { actor: "planner@example.com", timestamp: "2026-06-29T01:00:00Z", reason: "Initial review", modelVersion: "sitescore-r2.8.0" },
};

export const forecastBandFixture: ForecastBandChartContract = {
  store: { entityType: "store", entityId: "store-a", label: "Store A" },
  metric: "revenue",
  horizon: "12w",
  granularity: "weekly",
  points: [
    { date: "2026-W24", actual: 940000, forecastP10: 880000, forecastP50: 960000, forecastP90: 1040000, modelVersion: "forecast-r6" },
    { date: "2026-W25", actual: 980000, forecastP10: 900000, forecastP50: 990000, forecastP90: 1100000, siteScoreBaseline: 930000, modelVersion: "forecast-r6" },
    {
      date: "2026-W26",
      forecastP10: 910000,
      forecastP50: 1010000,
      forecastP90: 1160000,
      interventionMarker: { entityType: "intervention", entityId: "int-1", label: "Price test" },
      anomaly: true,
      modelVersion: "forecast-r6",
    },
  ],
  confidence: { level: "medium", reasons: ["Holiday calendar uncertainty"] },
  dataQuality: domainDataQualityFixture,
};

export const fourLightBadgeFixture: FourLightBadgeContract = {
  light: "ORANGE",
  triggerConditions: ["Revenue residual below p10", "Data freshness is partial"],
  alertHref: "/w/alerts/al-1",
};

export const rootCauseEvidenceFixture: RootCauseEvidenceCardContract = {
  causeCandidate: "Price",
  evidenceStrength: 0.67,
  supportingSignals: [{ label: "Elasticity residual", value: "-8.2%", impact: "positive", evidenceStrength: 0.72 }],
  contradictingSignals: [{ label: "Competitor price index", value: "flat", impact: "neutral", evidenceStrength: 0.42 }],
  dataConfidence: { level: "medium", reasons: ["POS complete", "Competitor scrape partial"] },
  recommendedNextCheck: "Review price ladder and nearest competitor changes.",
  dataQuality: domainDataQualityFixture,
};

export const interventionTimelineFixture: InterventionTimelineContract = {
  interventionId: "int-1",
  store: { entityType: "store", entityId: "store-a", label: "Store A" },
  interventionType: "Price test",
  eligibilityStatus: "eligible",
  conflictStatus: "no conflict",
  approvalStatus: "APPROVED",
  executionStatus: "scheduled",
  observationWindow: { startsAt: "2026-07-01", endsAt: "2026-07-28" },
  outcomeStatus: "OBSERVING",
  evidenceLevel: "medium",
  nodes: [
    { step: "Triggered", timestamp: "2026-06-29T01:00:00Z", actor: "system", eventType: "trigger", status: "done", description: "Residual crossed alert policy." },
    { step: "Eligibility checked", timestamp: "2026-06-29T01:05:00Z", actor: "system", eventType: "eligibility", status: "done", description: "Store is eligible." },
    { step: "Approved", timestamp: "2026-06-29T02:00:00Z", actor: "manager@example.com", eventType: "approval", status: "approved", description: "Human approval submitted." },
  ],
  audit: { actor: "manager@example.com", timestamp: "2026-06-29T02:00:00Z", reason: "Controlled test" },
};

export const pricingPlanComparisonFixture: PricingPlanComparisonContract = {
  plan: { entityType: "pricing_plan", entityId: "price-1", label: "Weekday wash bundle" },
  currentPrice: 120,
  candidatePrice: 135,
  priceChange: 15,
  expectedDemand: { p10: 8800, p50: 9400, p90: 9900, unit: "transactions" },
  expectedRevenue: { p10: 1188000, p50: 1269000, p90: 1336500, unit: "TWD" },
  expectedGrossMargin: { p10: 420000, p50: 470000, p90: 520000, unit: "TWD" },
  risk: "medium",
  constraintStatus: "WARNING",
  hardConstraintViolations: [],
  rollbackPlan: "Return to current price after 14 days if p50 GM is below baseline.",
  approvalStatus: "PENDING_REVIEW",
  dataQuality: domainDataQualityFixture,
};

export const adLiftReportFixture: AdLiftReportCardContract = {
  campaign: { entityType: "campaign", entityId: "ad-44", label: "Summer wash" },
  treatmentStores: [{ entityType: "store", entityId: "store-a", label: "Store A" }],
  controlStores: [{ entityType: "store", entityId: "store-b", label: "Store B" }],
  preTrendStatus: "PASS",
  incrementalRevenue: { p10: 70000, p50: 120000, p90: 180000, unit: "TWD" },
  incrementalGrossMargin: { p10: 21000, p50: 38000, p90: 62000, unit: "TWD" },
  iromi: { p10: 1.2, p50: 1.9, p90: 2.7, unit: "x" },
  evidenceLevel: "medium",
  continueStopRecommendation: "CONTINUE",
  contaminationWarnings: ["One treatment store also has a local promotion"],
  dataQuality: domainDataQualityFixture,
};

export const valuationRangeFixture: ValuationRangeChartContract = {
  valuation: { entityType: "asset", entityId: "asset-21", label: "North cluster" },
  fairValue: { p10: 32000000, p50: 39000000, p90: 45500000, unit: "TWD" },
  reservePrice: 36000000,
  askingPrice: 43000000,
  lensRanges: {
    income: { p10: 33000000, p50: 39500000, p90: 44800000, unit: "TWD" },
    market: { p10: 31000000, p50: 38000000, p90: 45000000, unit: "TWD" },
    blended: { p10: 32000000, p50: 39000000, p90: 45500000, unit: "TWD" },
  },
  comparableTransactionMarkers: [35000000, 40500000, 44000000],
  liquidityScore: 0.58,
  dataRoomCompleteness: { lease: "complete", maintenance: "partial", tax: "blocked" },
  financeApprovalStatus: "PENDING_REVIEW",
  fieldPermissions: [{ field: "reservePrice", visibility: "masked", reason: "finance-only" }],
  dataQuality: domainDataQualityFixture,
};

export const netPlanScenarioFixture: NetPlanScenarioCardContract = {
  scenarioName: "North region rebalance",
  objectiveValue: 9820000,
  actionCounts: { OPEN: 2, KEEP: 31, IMPROVE: 4, MOVE: 1, EXIT: 1, HOLD: 7 },
  budgetUsage: { used: 8200000, limit: 10000000, unit: "TWD" },
  expectedGrossMargin: { p10: 7400000, p50: 8900000, p90: 10300000, unit: "TWD" },
  risk: "medium",
  bindingConstraints: ["Capex limit", "Minimum service radius"],
  solverStatus: "SUCCEEDED",
  alternativePlanAvailable: true,
  approvalStatus: "PENDING_REVIEW",
  dataQuality: domainDataQualityFixture,
};

export const modelReleaseFixture: ModelReleaseCardContract = {
  modelId: "sitescore",
  version: "r2.8.0",
  championOrChallenger: "CHALLENGER",
  metricSummary: { auc: 0.82, mape: "8.4%", calibration: "pass" },
  segmentRegression: ["Suburban small-format MAPE +2.1pp"],
  dataQualityStatus: "FRESH",
  driftStatus: "PARTIAL",
  releaseStage: "CANARY",
  rollbackTarget: { modelId: "sitescore", version: "r2.7.3" },
  approvalStatus: "PENDING_REVIEW",
  audit: { actor: "mlops@example.com", timestamp: "2026-06-29T03:00:00Z", reason: "Canary request", modelVersion: "r2.8.0" },
};

export const decisionAuditTimelineFixture: DecisionAuditTimelineContract = {
  decisionId: "decision-1",
  entity: { entityType: "candidate_site", entityId: "cand-101", label: "Demo Rd 88" },
  modelVersion: "sitescore-r2.8.0",
  featureSnapshotTime: "2026-06-28T16:00:00Z",
  actor: "manager@example.com",
  decisionTime: "2026-06-29T02:00:00Z",
  executionStatus: "APPROVED",
  outcomeStatus: "OBSERVING",
  auditStatus: "PARTIAL",
  nodes: [
    { step: "Prediction generated", timestamp: "2026-06-29T01:00:00Z", actor: "system", eventType: "prediction", status: "done", description: "Prediction generated from feature snapshot." },
    { step: "Recommendation generated", timestamp: "2026-06-29T01:01:00Z", actor: "system", eventType: "recommendation", status: "done", description: "Recommendation generated." },
    { step: "Human review requested", timestamp: "2026-06-29T01:10:00Z", actor: "planner@example.com", eventType: "review", status: "requested", description: "Review requested." },
    { step: "Human decision submitted", timestamp: "2026-06-29T02:00:00Z", actor: "manager@example.com", eventType: "approval", status: "approved", description: "Decision approved." },
  ],
};
