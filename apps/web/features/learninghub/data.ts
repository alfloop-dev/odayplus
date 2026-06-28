export type ModelStage = "dev" | "shadow" | "canary" | "production" | "retired" | "rolled_back" | "blocked";
export type ReleaseType = "SHADOW" | "CANARY" | "FULL" | "ROLLBACK";
export type ReviewStatus = "PASSED" | "WARNING" | "FAILED";

export type ValidationMetric = {
  name: string;
  actual: number;
  baseline: number;
  thresholdType: ">=" | "<=";
  threshold: number;
  unit?: string;
};

export type SegmentMetric = {
  segment: string;
  conversionLift: string;
  calibration: string;
  status: ReviewStatus;
};

export type ModelVersionRecord = {
  modelName: string;
  version: string;
  stage: ModelStage;
  aliases: string[];
  riskLevel: "R1" | "R2" | "R3" | "R4";
  validationPassed: boolean;
  cardComplete: boolean;
  cardApproved: boolean;
  privacyReview: ReviewStatus;
  securityReview: ReviewStatus;
  dataQualityStatus: ReviewStatus;
  driftStatus: ReviewStatus;
  rollbackTarget?: string;
  owner: string;
  artifactUri: string;
  datasetSnapshotId: string;
  featureSchemaVersion: string;
  featureSetId: string;
  labelVersion: string;
  labelSetId: string;
  runId: string;
  gitSha: string;
  createdAt: string;
  approvedBy?: string;
  approvedAt?: string;
  intendedUse: string;
  notIntendedUse: string;
  trainingPeriod: string;
  validationPeriod: string;
  algorithm: string;
  baseline: string;
  metricsSummary: string;
  calibrationSummary: string;
  explainabilityMethod: string;
  limitations: string;
  knownBiases: string;
  rollbackConditions: string[];
  approvals: string[];
  monitoringConfig: string;
  correlationId: string;
  validation: ValidationMetric[];
  segments: SegmentMetric[];
  affectedModules: string[];
};

export type ReleaseDecision = {
  releaseId: string;
  modelName: string;
  fromVersion: string;
  toVersion: string;
  releaseType: ReleaseType;
  reason: string;
  approvalId: string;
  rollbackTarget?: string;
  monitoringWindow: string;
  successCriteria: string;
  failCriteria: string;
  affectedModules: string[];
  requestedBy: string;
  approvedBy: string;
  createdAt: string;
  auditEventId: string;
  correlationId: string;
};

export const models: ModelVersionRecord[] = [
  {
    modelName: "sitescore-propensity",
    version: "2.4.0",
    stage: "canary",
    aliases: ["challenger", "canary"],
    riskLevel: "R3",
    validationPassed: true,
    cardComplete: true,
    cardApproved: true,
    privacyReview: "WARNING",
    securityReview: "PASSED",
    dataQualityStatus: "PASSED",
    driftStatus: "WARNING",
    rollbackTarget: "2.3.1",
    owner: "ai-data-risk",
    artifactUri: "mlflow://models/sitescore-propensity/2.4.0",
    datasetSnapshotId: "ds-sitescore-2026w25",
    featureSchemaVersion: "site-features-v7",
    featureSetId: "fs-site-demand-v7",
    labelVersion: "label-open-success-v3",
    labelSetId: "ls-site-2026q2",
    runId: "mlflow-run-8842",
    gitSha: "8f41c22",
    createdAt: "2026-06-25 09:14",
    approvedBy: "model-review-board",
    approvedAt: "2026-06-26 10:40",
    intendedUse: "Rank candidate stores for expansion review and subsidy-ready decision packages.",
    notIntendedUse: "Autonomous approval, franchisee credit scoring, or replacing finance review.",
    trainingPeriod: "2025-07-01 to 2026-03-31",
    validationPeriod: "2026-04-01 to 2026-06-15",
    algorithm: "Gradient boosted decision trees with calibrated probability output.",
    baseline: "sitescore-propensity 2.3.1 champion",
    metricsSummary: "AUC 0.842, precision@50 0.71, calibration error 0.031.",
    calibrationSummary: "Expected calibration error improved by 0.008 vs champion; west-region tail requires watch.",
    explainabilityMethod: "SHAP summary with segment-level partial dependence review.",
    limitations: "Cold-start urban micro-markets remain under-represented.",
    knownBiases: "Lower confidence in markets with fewer than 12 comparable stores.",
    rollbackConditions: ["canary conversion lift < -2%", "drift status FAILED", "audit denial on release approval"],
    approvals: ["model-review-board approved", "privacy steward warning accepted"],
    monitoringConfig: "24h canary watch, 5% traffic, hourly drift sample.",
    correlationId: "corr-lh-sitescore-240",
    validation: [
      { name: "auc", actual: 0.842, baseline: 0.831, thresholdType: ">=", threshold: 0.83 },
      { name: "precision_at_50", actual: 0.71, baseline: 0.68, thresholdType: ">=", threshold: 0.69 },
      { name: "calibration_error", actual: 0.031, baseline: 0.039, thresholdType: "<=", threshold: 0.035 },
    ],
    segments: [
      { segment: "North urban", conversionLift: "+3.8%", calibration: "0.026", status: "PASSED" },
      { segment: "West suburban", conversionLift: "-0.7%", calibration: "0.044", status: "WARNING" },
      { segment: "New franchisee", conversionLift: "+1.9%", calibration: "0.033", status: "PASSED" },
    ],
    affectedModules: ["SiteScore", "NetPlan", "Audit Evidence"],
  },
  {
    modelName: "forecastops-demand",
    version: "1.9.3",
    stage: "production",
    aliases: ["champion", "production"],
    riskLevel: "R2",
    validationPassed: true,
    cardComplete: true,
    cardApproved: true,
    privacyReview: "PASSED",
    securityReview: "PASSED",
    dataQualityStatus: "PASSED",
    driftStatus: "PASSED",
    rollbackTarget: "1.9.0",
    owner: "forecast-ml",
    artifactUri: "mlflow://models/forecastops-demand/1.9.3",
    datasetSnapshotId: "ds-forecast-2026w25",
    featureSchemaVersion: "forecast-features-v5",
    featureSetId: "fs-demand-v5",
    labelVersion: "revenue-label-v4",
    labelSetId: "ls-revenue-2026q2",
    runId: "mlflow-run-8760",
    gitSha: "51aa09d",
    createdAt: "2026-06-20 16:12",
    approvedBy: "ops-ml-lead",
    approvedAt: "2026-06-21 11:20",
    intendedUse: "Produce w4/w8/w12/w24 revenue forecasts for four-light alerting.",
    notIntendedUse: "Direct promotion execution without intervention approval.",
    trainingPeriod: "2024-06-01 to 2026-03-31",
    validationPeriod: "2026-04-01 to 2026-06-10",
    algorithm: "Temporal fusion transformer with store embeddings.",
    baseline: "forecastops-demand 1.9.0",
    metricsSummary: "WAPE 8.7%, P90 coverage 91.2%.",
    calibrationSummary: "Prediction band coverage within policy.",
    explainabilityMethod: "Feature attribution by horizon and store cluster.",
    limitations: "Holiday uplift requires external calendar freshness.",
    knownBiases: "Sparse stores have wider confidence bands.",
    rollbackConditions: ["wape > 10%", "P90 coverage < 88%"],
    approvals: ["ops-ml-lead approved"],
    monitoringConfig: "72h watch, all production stores.",
    correlationId: "corr-lh-forecast-193",
    validation: [
      { name: "wape", actual: 0.087, baseline: 0.093, thresholdType: "<=", threshold: 0.095 },
      { name: "p90_coverage", actual: 0.912, baseline: 0.904, thresholdType: ">=", threshold: 0.9 },
    ],
    segments: [
      { segment: "High volume", conversionLift: "+0.6%", calibration: "0.018", status: "PASSED" },
      { segment: "Sparse stores", conversionLift: "-0.2%", calibration: "0.036", status: "PASSED" },
    ],
    affectedModules: ["ForecastOps", "InterventionOps"],
  },
  {
    modelName: "price-elasticity",
    version: "0.8.0",
    stage: "blocked",
    aliases: ["challenger"],
    riskLevel: "R4",
    validationPassed: false,
    cardComplete: false,
    cardApproved: false,
    privacyReview: "PASSED",
    securityReview: "FAILED",
    dataQualityStatus: "WARNING",
    driftStatus: "FAILED",
    owner: "pricing-science",
    artifactUri: "mlflow://models/price-elasticity/0.8.0",
    datasetSnapshotId: "ds-price-2026w24",
    featureSchemaVersion: "price-features-v3",
    featureSetId: "fs-price-v3",
    labelVersion: "margin-label-v2",
    labelSetId: "ls-margin-2026q2",
    runId: "mlflow-run-8810",
    gitSha: "d712b70",
    createdAt: "2026-06-24 14:03",
    intendedUse: "Recommend constrained price test candidates.",
    notIntendedUse: "Autonomous price publication.",
    trainingPeriod: "2025-01-01 to 2026-03-31",
    validationPeriod: "2026-04-01 to 2026-06-01",
    algorithm: "Causal forest elasticity model.",
    baseline: "price-elasticity 0.7.2",
    metricsSummary: "MAPE 11.9%, guardrail violation rate 4.2%.",
    calibrationSummary: "Segment regression detected in late-night menu mix.",
    explainabilityMethod: "Treatment effect intervals by category.",
    limitations: "Needs security review remediation and rollback target.",
    knownBiases: "Underestimates bundled-item substitution.",
    rollbackConditions: [],
    approvals: [],
    monitoringConfig: "Not eligible for release.",
    correlationId: "corr-lh-price-080",
    validation: [
      { name: "mape", actual: 0.119, baseline: 0.101, thresholdType: "<=", threshold: 0.105 },
      { name: "guardrail_violation_rate", actual: 0.042, baseline: 0.019, thresholdType: "<=", threshold: 0.02 },
    ],
    segments: [
      { segment: "Late-night menu", conversionLift: "-5.2%", calibration: "0.071", status: "FAILED" },
      { segment: "Lunch bundle", conversionLift: "+1.1%", calibration: "0.038", status: "WARNING" },
    ],
    affectedModules: ["PriceOps", "Audit Evidence"],
  },
];

export const releases: ReleaseDecision[] = [
  {
    releaseId: "rel-lh-240-canary",
    modelName: "sitescore-propensity",
    fromVersion: "2.3.1",
    toVersion: "2.4.0",
    releaseType: "CANARY",
    reason: "Canary release after validation pass; monitor west suburban calibration.",
    approvalId: "approval-lh-7721",
    rollbackTarget: "2.3.1",
    monitoringWindow: "24h",
    successCriteria: "conversion lift >= 0%, calibration_error <= 0.04",
    failCriteria: "drift FAILED or canary lift < -2%",
    affectedModules: ["SiteScore", "NetPlan", "Audit Evidence"],
    requestedBy: "ai-data-risk",
    approvedBy: "model-review-board",
    createdAt: "2026-06-26 10:44",
    auditEventId: "audit-lh-9001",
    correlationId: "corr-lh-sitescore-240",
  },
  {
    releaseId: "rel-lh-193-full",
    modelName: "forecastops-demand",
    fromVersion: "1.9.0",
    toVersion: "1.9.3",
    releaseType: "FULL",
    reason: "Full production promotion after 72h watch window passed.",
    approvalId: "approval-lh-7640",
    rollbackTarget: "1.9.0",
    monitoringWindow: "72h",
    successCriteria: "WAPE <= 9.5%, P90 coverage >= 90%",
    failCriteria: "WAPE > 10% for 2 windows",
    affectedModules: ["ForecastOps", "InterventionOps"],
    requestedBy: "forecast-ml",
    approvedBy: "ops-ml-lead",
    createdAt: "2026-06-21 11:24",
    auditEventId: "audit-lh-8840",
    correlationId: "corr-lh-forecast-193",
  },
];

export function selectedModel(modelName?: string, version?: string): ModelVersionRecord {
  return (
    models.find((model) => model.modelName === modelName && (!version || model.version === version)) ??
    models.find((model) => model.modelName === modelName) ??
    models[0]
  );
}

export function selectedRelease(releaseId?: string): ReleaseDecision {
  return releases.find((release) => release.releaseId === releaseId) ?? releases[0];
}
