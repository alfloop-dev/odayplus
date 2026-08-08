/**
 * Learning Hub API loader — ODP-CAP-MODEL-RELEASE-UI-001.
 *
 * Provides API client calls and typed interfaces for Learning Hub backend endpoints:
 *   - GET  /api/v1/learninghub/models
 *   - GET  /api/v1/learninghub/models/{model_name}
 *   - GET  /api/v1/learninghub/models/{model_name}/evidence
 *   - POST /api/v1/learninghub/models/{model_name}/versions/{version}/approval
 *   - GET  /api/v1/learninghub/releases
 *   - POST /api/v1/learninghub/releases
 *   - POST /api/v1/learninghub/releases/{release_id}/monitor
 *   - GET  /api/v1/learninghub/oss-capabilities
 *
 * Follows non-optimistic audit rules (FR-GOV-009) and pre-release gate checks.
 */
import { operatorSecurityHeaders } from "../operatorSecurityHeaders";

const LEARNINGHUB_API_BASE = "/api/v1/learninghub";

export type MetricThreshold = {
  metric_name: string;
  min_value?: number | null;
  max_value?: number | null;
  warning_min_value?: number | null;
  warning_max_value?: number | null;
};

export type SegmentMetric = {
  segment_name: string;
  segment_value: string;
  metrics: Record<string, number>;
  record_count: number;
};

export type ModelCardApproval = {
  approver: string;
  role: string;
  decision: "approved" | "rejected";
  approved_at?: string;
};

export type ModelCard = {
  model_name: string;
  model_version: string;
  owner: string;
  risk_level: "R1" | "R2" | "R3" | "R4";
  intended_use: string;
  not_intended_use: string;
  dataset_snapshot_id: string;
  validation_run_id: string;
  feature_set_id: string;
  label_set_id: string;
  training_period: string;
  validation_period: string;
  algorithm: string;
  baseline: string;
  metrics_summary: Record<string, number>;
  segment_metrics: SegmentMetric[];
  calibration_summary: Record<string, unknown>;
  explainability_method: string;
  limitations: string[];
  known_biases: string[];
  privacy_review: "PASSED" | "WARNING" | "FAILED";
  security_review: "PASSED" | "WARNING" | "FAILED";
  release_status: string;
  rollback_conditions: string[];
  approvals: ModelCardApproval[];
  is_complete?: boolean;
  is_approved?: boolean;
};

export type ModelVersionItem = {
  model_name: string;
  version: string;
  artifact_uri: string;
  dataset_snapshot_id: string;
  feature_schema_version: string;
  label_version: string;
  metrics: Record<string, number>;
  stage: "dev" | "shadow" | "canary" | "production" | "retired" | "rolled_back" | "blocked";
  aliases?: string[];
  run_id?: string;
  git_sha?: string;
  rollback_target?: string | null;
  monitoring_config?: Record<string, unknown>;
  created_at?: string;
  model_card?: ModelCard;
  validation_run?: {
    validation_run_id: string;
    dataset_snapshot_id: string;
    passed: boolean;
    metrics: Record<string, number>;
    baseline_metrics: Record<string, number>;
    thresholds: MetricThreshold[];
    segment_metrics: SegmentMetric[];
  };
};

export type ModelReleaseDecisionItem = {
  release_id: string;
  model_name: string;
  from_version?: string;
  to_version: string;
  version: string;
  release_type: "SHADOW" | "CANARY" | "FULL" | "ROLLBACK";
  reason: string;
  approval_id: string;
  rollback_target?: string | null;
  monitoring_window: string;
  success_criteria: string[];
  fail_criteria: string[];
  affected_modules: string[];
  requested_by: string;
  approved_by: string;
  created_at: string;
  audit_event_id: string;
  correlation_id?: string;
  revision?: number;
};

export type ReleaseMonitorAssessment = {
  assessment_id: string;
  release_id: string;
  status: "HEALTHY" | "WARNING" | "FAIL";
  observed_metrics: Record<string, number>;
  violations: Array<{
    metric_name: string;
    observed_value: number;
    threshold: MetricThreshold;
    severity: "warning" | "error";
  }>;
  evaluated_by: string;
  evaluated_at: string;
  audit_event_id: string;
};

export type OssCapabilityItem = {
  name: string;
  available: boolean;
  version: string;
  reason?: string;
};

function newCorrelationId(): string {
  return `corr-lh-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

async function apiFetch<T>(
  path: string,
  options: RequestInit & { correlationId?: string; roleId?: string } = {},
): Promise<{ ok: true; status: number; data: T } | { ok: false; status: number; data: null; errorDetail?: string }> {
  const { correlationId, roleId, ...fetchOptions } = options;
  try {
    const res = await fetch(`${LEARNINGHUB_API_BASE}${path}`, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        "X-Correlation-Id": correlationId ?? newCorrelationId(),
        ...operatorSecurityHeaders(roleId),
        ...(fetchOptions.headers ?? {}),
      },
    });
    if (!res.ok) {
      let detail = "";
      try {
        const json = await res.json();
        detail = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail);
      } catch {
        detail = `HTTP ${res.status}`;
      }
      return { ok: false, status: res.status, data: null, errorDetail: detail };
    }
    return { ok: true, status: res.status, data: (await res.json()) as T };
  } catch (err) {
    return { ok: false, status: 0, data: null, errorDetail: String(err) };
  }
}

/** Fetch all models registered in Learning Hub */
export async function fetchModels(roleId?: string): Promise<ModelVersionItem[] | null> {
  const res = await apiFetch<{ items: ModelVersionItem[]; count: number }>("/models", { method: "GET", roleId });
  return res.ok ? res.data.items : null;
}

/** Fetch details and release decisions for a single model */
export async function fetchModelDetail(
  modelName: string,
  roleId?: string,
): Promise<{ model_name: string; versions: ModelVersionItem[]; release_decisions: ModelReleaseDecisionItem[] } | null> {
  const res = await apiFetch<{ model_name: string; versions: ModelVersionItem[]; release_decisions: ModelReleaseDecisionItem[] }>(
    `/models/${encodeURIComponent(modelName)}`,
    { method: "GET", roleId },
  );
  return res.ok ? res.data : null;
}

/** Fetch release decision history */
export async function fetchReleases(
  modelName?: string,
  roleId?: string,
): Promise<ModelReleaseDecisionItem[] | null> {
  const path = modelName ? `/releases?model_name=${encodeURIComponent(modelName)}` : "/releases";
  const res = await apiFetch<{ items: ModelReleaseDecisionItem[]; count: number }>(path, { method: "GET", roleId });
  return res.ok ? res.data.items : null;
}

/** Approve or reject a Model Card */
export async function approveModelCard(params: {
  modelName: string;
  version: string;
  decision: "approved" | "rejected";
  roleId?: string;
}): Promise<{ ok: true; card: ModelCard } | { ok: false; status: number; detail: string }> {
  const res = await apiFetch<ModelCard>(
    `/models/${encodeURIComponent(params.modelName)}/versions/${encodeURIComponent(params.version)}/approval`,
    {
      method: "POST",
      body: JSON.stringify({ decision: params.decision }),
      roleId: params.roleId,
    },
  );
  if (res.ok) {
    return { ok: true, card: res.data };
  }
  return { ok: false, status: res.status, detail: res.errorDetail ?? "Approval failed" };
}

/** Submit a model release request */
export async function requestRelease(params: {
  model_name: string;
  version: string;
  release_type: "SHADOW" | "CANARY" | "FULL" | "ROLLBACK";
  reason: string;
  approval_id: string;
  approved_by: string;
  rollback_target?: string | null;
  monitoring_window: string;
  success_criteria: string[];
  fail_criteria: string[];
  affected_modules: string[];
  expected_release_revision: number;
  idempotency_key: string;
  requested_by?: string;
  release_scope?: string;
  roleId?: string;
}): Promise<{ ok: true; decision: ModelReleaseDecisionItem } | { ok: false; status: number; detail: string }> {
  const res = await apiFetch<ModelReleaseDecisionItem>("/releases", {
    method: "POST",
    body: JSON.stringify(params),
    roleId: params.roleId,
  });
  if (res.ok) {
    return { ok: true, decision: res.data };
  }
  return { ok: false, status: res.status, detail: res.errorDetail ?? "Release request failed" };
}

/** Submit release monitoring metrics & evaluation */
export async function monitorRelease(params: {
  release_id: string;
  observed_metrics: Record<string, number>;
  guardrails: MetricThreshold[];
  evaluated_by?: string;
  roleId?: string;
}): Promise<{ ok: true; assessment: ReleaseMonitorAssessment } | { ok: false; status: number; detail: string }> {
  const res = await apiFetch<ReleaseMonitorAssessment>(`/releases/${encodeURIComponent(params.release_id)}/monitor`, {
    method: "POST",
    body: JSON.stringify({
      observed_metrics: params.observed_metrics,
      guardrails: params.guardrails,
      evaluated_by: params.evaluated_by,
    }),
    roleId: params.roleId,
  });
  if (res.ok) {
    return { ok: true, assessment: res.data };
  }
  return { ok: false, status: res.status, detail: res.errorDetail ?? "Release monitoring failed" };
}

/** Fallback mock data for dev/testing when backend is unreachable */
export const fallbackLearningHubModels: ModelVersionItem[] = [
  {
    model_name: "forecast_revenue_interval",
    version: "v2.1.0",
    artifact_uri: "gs://oday-plus-models/forecast_revenue_interval/v2.1.0.tar.gz",
    dataset_snapshot_id: "ds-forecast-2026-w31",
    feature_schema_version: "fs-v1.4",
    label_version: "lbl-v2.0",
    metrics: { mape: 0.082, rmse: 1420.5, r2: 0.941 },
    stage: "production",
    aliases: ["champion", "production"],
    run_id: "run-forecast-8812",
    git_sha: "git-a1b2c3d4",
    rollback_target: "v2.0.4",
    created_at: "2026-07-28 10:00:00",
    model_card: {
      model_name: "forecast_revenue_interval",
      model_version: "v2.1.0",
      owner: "ForecastOps Owner",
      risk_level: "R3",
      intended_use: "Predict 7/14/28-day store revenue interval for inventory and staffing optimization.",
      not_intended_use: "Individual store credit risk or loan underwriting.",
      dataset_snapshot_id: "ds-forecast-2026-w31",
      validation_run_id: "val-run-001",
      feature_set_id: "feat-store-daily-v2",
      label_set_id: "lbl-rev-daily-v2",
      training_period: "2025-01-01 to 2026-06-30",
      validation_period: "2026-07-01 to 2026-07-25",
      algorithm: "LightGBM + Conformal Quantile Regression",
      baseline: "v2.0.4 Baseline (MAPE 0.105)",
      metrics_summary: { mape: 0.082, rmse: 1420.5, r2: 0.941 },
      segment_metrics: [
        { segment_name: "region", segment_value: "North", metrics: { mape: 0.075 }, record_count: 450 },
        { segment_name: "region", segment_value: "South", metrics: { mape: 0.089 }, record_count: 380 },
      ],
      calibration_summary: { coverage_90: 0.898, coverage_50: 0.505 },
      explainability_method: "SHAP tree explainer",
      limitations: ["High variance during typhoon/holiday extreme events"],
      known_biases: ["Slight under-prediction for newly opened stores < 3 months"],
      privacy_review: "PASSED",
      security_review: "PASSED",
      release_status: "production",
      rollback_conditions: ["MAPE exceeds 0.12 for 3 consecutive days", "Data drift score > 0.35"],
      approvals: [{ approver: "ModelReviewBoard", role: "model-review-board", decision: "approved", approved_at: "2026-07-28" }],
      is_complete: true,
      is_approved: true,
    },
    validation_run: {
      validation_run_id: "val-run-001",
      dataset_snapshot_id: "ds-forecast-2026-w31",
      passed: true,
      metrics: { mape: 0.082, rmse: 1420.5 },
      baseline_metrics: { mape: 0.105, rmse: 1680.0 },
      thresholds: [
        { metric_name: "mape", max_value: 0.10, warning_max_value: 0.09 },
        { metric_name: "rmse", max_value: 1500.0, warning_max_value: 1450.0 },
      ],
      segment_metrics: [
        { segment_name: "region", segment_value: "North", metrics: { mape: 0.075 }, record_count: 450 },
      ],
    },
  },
  {
    model_name: "forecast_revenue_interval",
    version: "v2.2.0-candidate",
    artifact_uri: "gs://oday-plus-models/forecast_revenue_interval/v2.2.0-candidate.tar.gz",
    dataset_snapshot_id: "ds-forecast-2026-w31",
    feature_schema_version: "fs-v1.4",
    label_version: "lbl-v2.0",
    metrics: { mape: 0.076, rmse: 1310.0, r2: 0.952 },
    stage: "canary",
    aliases: ["challenger", "canary"],
    run_id: "run-forecast-8945",
    git_sha: "git-b2c3d4e5",
    rollback_target: "v2.1.0",
    created_at: "2026-08-01 14:30:00",
    model_card: {
      model_name: "forecast_revenue_interval",
      model_version: "v2.2.0-candidate",
      owner: "ForecastOps Owner",
      risk_level: "R3",
      intended_use: "Predict 7/14/28-day store revenue interval with weather feature enhancements.",
      not_intended_use: "Individual store credit risk or loan underwriting.",
      dataset_snapshot_id: "ds-forecast-2026-w31",
      validation_run_id: "val-run-002",
      feature_set_id: "feat-store-daily-v3",
      label_set_id: "lbl-rev-daily-v2",
      training_period: "2025-01-01 to 2026-07-20",
      validation_period: "2026-07-21 to 2026-07-31",
      algorithm: "XGBoost + Conformal Quantile Regression",
      baseline: "v2.1.0 Champion (MAPE 0.082)",
      metrics_summary: { mape: 0.076, rmse: 1310.0, r2: 0.952 },
      segment_metrics: [
        { segment_name: "region", segment_value: "North", metrics: { mape: 0.071 }, record_count: 450 },
        { segment_name: "region", segment_value: "South", metrics: { mape: 0.081 }, record_count: 380 },
      ],
      calibration_summary: { coverage_90: 0.902, coverage_50: 0.501 },
      explainability_method: "SHAP tree explainer",
      limitations: ["Requires real-time weather API integration"],
      known_biases: ["Slight sensitivity to extreme rainfall events"],
      privacy_review: "PASSED",
      security_review: "PASSED",
      release_status: "canary",
      rollback_conditions: ["MAPE exceeds 0.10 in canary window", "API timeout > 500ms"],
      approvals: [{ approver: "ModelReviewBoard", role: "model-review-board", decision: "approved", approved_at: "2026-08-02" }],
      is_complete: true,
      is_approved: true,
    },
    validation_run: {
      validation_run_id: "val-run-002",
      dataset_snapshot_id: "ds-forecast-2026-w31",
      passed: true,
      metrics: { mape: 0.076, rmse: 1310.0 },
      baseline_metrics: { mape: 0.082, rmse: 1420.5 },
      thresholds: [
        { metric_name: "mape", max_value: 0.09, warning_max_value: 0.08 },
      ],
      segment_metrics: [],
    },
  },
  {
    model_name: "sitescore-v4.8",
    version: "v4.8.1",
    artifact_uri: "gs://oday-plus-models/sitescore/v4.8.1.tar.gz",
    dataset_snapshot_id: "ds-network-2026-w30",
    feature_schema_version: "fs-net-v2",
    label_version: "lbl-site-v1",
    metrics: { accuracy: 0.884, f1: 0.865 },
    stage: "production",
    aliases: ["champion", "production"],
    run_id: "run-site-4412",
    git_sha: "git-c3d4e5f6",
    rollback_target: "v4.8.0",
    created_at: "2026-07-20 09:15:00",
    model_card: {
      model_name: "sitescore-v4.8",
      model_version: "v4.8.1",
      owner: "Expansion Science Team",
      risk_level: "R4",
      intended_use: "Evaluate site selection candidates and recommend GO / WAIT / REJECT decisions.",
      not_intended_use: "Automated lease signing without Expansion Manager signoff.",
      dataset_snapshot_id: "ds-network-2026-w30",
      validation_run_id: "val-run-003",
      feature_set_id: "feat-site-geo-v2",
      label_set_id: "lbl-site-outcome-v1",
      training_period: "2024-01-01 to 2026-05-31",
      validation_period: "2026-06-01 to 2026-07-15",
      algorithm: "CatBoost Classifier",
      baseline: "v4.8.0 Baseline (Accuracy 0.860)",
      metrics_summary: { accuracy: 0.884, f1: 0.865 },
      segment_metrics: [],
      calibration_summary: { brier_score: 0.042 },
      explainability_method: "SHAP value breakdown",
      limitations: ["Requires foot traffic sensor data within 200m radius"],
      known_biases: ["Higher false-negative rate in rural highway locations"],
      privacy_review: "PASSED",
      security_review: "PASSED",
      release_status: "production",
      rollback_conditions: ["Accuracy drops below 0.82", "Override rate exceeds 25%"],
      approvals: [{ approver: "ModelReviewBoard", role: "model-review-board", decision: "approved", approved_at: "2026-07-21" }],
      is_complete: true,
      is_approved: true,
    },
    validation_run: {
      validation_run_id: "val-run-003",
      dataset_snapshot_id: "ds-network-2026-w30",
      passed: true,
      metrics: { accuracy: 0.884, f1: 0.865 },
      baseline_metrics: { accuracy: 0.860, f1: 0.840 },
      thresholds: [
        { metric_name: "accuracy", min_value: 0.85, warning_min_value: 0.87 },
      ],
      segment_metrics: [],
    },
  },
];

export const fallbackLearningHubReleases: ModelReleaseDecisionItem[] = [
  {
    release_id: "rel-fc-20260728-01",
    model_name: "forecast_revenue_interval",
    from_version: "v2.0.4",
    to_version: "v2.1.0",
    version: "v2.1.0",
    release_type: "FULL",
    reason: "Promote v2.1.0 to champion after 7-day shadow run showing 22% lower MAPE.",
    approval_id: "ap-model-fc-001",
    rollback_target: "v2.0.4",
    monitoring_window: "7d",
    success_criteria: ["MAPE <= 0.09", "Zero service errors"],
    fail_criteria: ["MAPE > 0.12", "Latency > 300ms"],
    affected_modules: ["Store Ops", "Growth"],
    requested_by: "ForecastOps Owner",
    approved_by: "ModelReviewBoard",
    created_at: "2026-07-28 10:30:00",
    audit_event_id: "aud-rel-8801",
    correlation_id: "corr-rel-fc-001",
    revision: 1,
  },
];
