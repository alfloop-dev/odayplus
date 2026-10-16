import { payloadContainsSeedData } from "../operatorDataMode";
import type { ForecastReadGuardFactory } from "../storeOpsForecastClient";

export type CanonicalMonitoringItem = {
  evaluationId: string; releaseId: string; releaseRevision: number;
  modelName: string; modelVersion: string; modelDatasetSnapshotId: string;
  storeId: string; horizonDays: number; monitoringWindow: string;
  executedAt: string; windowEnd: string; assessedAt: string;
  referenceIds: string[]; currentIds: string[];
  referenceCoverage: [string, string]; currentCoverage: [string, string];
  policyVersionId: string; sourceDigest: string; driftShare: number;
  driftDetected: boolean; auditEventId: string;
  recommendedAction: "REVIEW_ROLLBACK" | "CONTINUE_OBSERVING";
};
export type CanonicalMonitoringRead = { state: "ready"; items: CanonicalMonitoringItem[]; unavailableCount: number }
  | { state: "unavailable"; reason: string } | { state: "error"; message: string } | { state: "superseded" };
export type CanonicalMonitoringSelection = {
  store_id: string; horizon_days: 28 | 56 | 84 | 168; reference_ids: string[]; current_ids: string[];
};
export type CanonicalMonitoringCommand = { state: "saved"; item: CanonicalMonitoringItem }
  | { state: "unavailable"; reason: string } | { state: "error"; message: string } | { state: "superseded" };

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value);
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.every(text) && new Set(value).size === value.length;
function aware(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  return year > 0 && month > 0 && month <= 12 && day > 0
    && day <= new Date(Date.UTC(year, month, 0)).getUTCDate() && hour < 24 && minute < 60 && second < 60;
}
function intersect(left: string[], right: string[]) { return left.some(id => right.includes(id)); }
const pair = (value: unknown): value is [string, string] => Array.isArray(value) && value.length === 2
  && aware(value[0]) && aware(value[1]) && Date.parse(value[0]) <= Date.parse(value[1]);

function binding(value: unknown, modelName: unknown, modelVersion: unknown, dataset: unknown): boolean {
  return object(value) && value.model_service === "forecastops" && value.model_stage === "production"
    && value.model_name === modelName && value.model_version === modelVersion && value.dataset_snapshot_id === dataset
    && value.model_id === `${modelName}:${modelVersion}`
    && ["feature_schema_version", "label_version", "model_engine"].every(key => text(value[key]))
    && value.model_engine !== "baseline" && hash(value.artifact_sha256)
    && ["registry_version", "source_run_id", "mlflow_run_id", "model_approved_by"].every(key => value[key] === null || text(value[key]))
    && ["model_approved_at", "binding_resolved_at"].every(key => value[key] === null || aware(value[key]));
}

export function parseCanonicalMonitoringItem(value: unknown, releaseId: string): CanonicalMonitoringItem | null {
  if (!object(value) || !object(value.source_provenance) || !object(value.observed_metrics)) return null;
  const p = value.source_provenance;
  if (p.schema !== "forecast.canonical-monitoring.v1" || p.release_id !== releaseId
    || !integer(p.release_revision) || p.release_revision < 1
    || !["model_name", "model_version", "model_dataset_snapshot_id", "store_id", "tenant_id", "monitoring_window"].every(key => text(p[key]))
    || !text(value.evaluation_id) || !text(value.audit_event_id) || !text(value.requested_by)
    || value.model_name !== p.model_name || value.model_version !== p.model_version
    || value.dataset_snapshot_id !== p.model_dataset_snapshot_id || value.created_at !== p.assessed_at
    || value.signal_type !== "PREDICTION_DRIFT" || typeof value.drift_detected !== "boolean"
    || typeof value.observed_metrics.prediction_drift_share !== "number"
    || !Number.isFinite(value.observed_metrics.prediction_drift_share)
    || value.observed_metrics.prediction_drift_share < 0 || value.observed_metrics.prediction_drift_share > 1
    || !integer(p.horizon_days) || ![28, 56, 84, 168].includes(p.horizon_days)
    || !aware(p.release_executed_at) || !aware(p.release_window_end) || !aware(p.assessed_at)
    || Date.parse(p.release_executed_at) >= Date.parse(p.release_window_end)
    || Date.parse(p.assessed_at) < Date.parse(p.release_executed_at)
    || !ids(p.reference_ids) || !ids(p.current_ids) || intersect(p.reference_ids, p.current_ids)
    || !pair(p.reference_coverage) || !pair(p.current_coverage)
    || Date.parse(p.reference_coverage[1]) >= Date.parse(p.current_coverage[0])
    || Date.parse(p.reference_coverage[0]) < Date.parse(p.release_executed_at)
    || Date.parse(p.current_coverage[1]) > Math.min(Date.parse(p.release_window_end), Date.parse(p.assessed_at))
    || !hash(p.source_digest) || !object(p.policy) || !text(p.policy.policy_version_id)
    || p.policy.policy_kind !== "model_performance_drift" || !text(p.policy.policy_label) || !text(p.policy.tenant_id)
    || p.policy.policy_version_id !== `${p.policy.policy_label}:${p.policy.tenant_id}`
    || !object(p.policy.parameters) || !Array.isArray(p.policy.declared_inputs)
    || !p.policy.declared_inputs.every(text) || !p.policy.declared_inputs.includes("prediction_outputs")
    || value.decision_policy_version_id !== p.policy.policy_version_id || !hash(p.policy_digest)
    || !text(p.reference_record_set_id) || !text(p.current_record_set_id)
    || p.reference_record_set_id === p.current_record_set_id
    || value.reference_snapshot_id !== p.reference_record_set_id || value.current_snapshot_id !== p.current_record_set_id
    || value.cohort_key !== `forecastops:${p.tenant_id}:${p.store_id}:revenue:w${p.horizon_days / 7}`
    || p.signal !== "prediction_distribution_drift" || p.mature_error_available !== false || p.full_window_observed !== false
    || !Array.isArray(p.source_manifest) || p.source_manifest.length !== p.reference_ids.length + p.current_ids.length
    || value.audit_delivery !== "pending_or_delivered_separately"
    || value.recommended_action !== (value.drift_detected ? "REVIEW_ROLLBACK" : "CONTINUE_OBSERVING")) return null;
  // Narrow the actual fields explicitly; parsing returns a small UI projection.
  if (!text(p.model_name) || !text(p.model_version) || !text(p.model_dataset_snapshot_id)
    || !text(p.store_id) || !text(p.monitoring_window)) return null;
  const expected = [...p.reference_ids, ...p.current_ids];
  if (!p.source_manifest.every((row: unknown, index: number) => object(row)
    && row.forecast_output_id === expected[index] && hash(row.sha256) && text(row.prediction_run_id)
    && typeof row.prediction_value === "number" && Number.isFinite(row.prediction_value)
    && aware(row.scored_at) && binding(row.binding, p.model_name, p.model_version, p.model_dataset_snapshot_id))) return null;
  const referenceTimes = p.source_manifest.slice(0, p.reference_ids.length).map((row: unknown) => object(row) && aware(row.scored_at) ? Date.parse(row.scored_at) : Number.NaN);
  const currentTimes = p.source_manifest.slice(p.reference_ids.length).map((row: unknown) => object(row) && aware(row.scored_at) ? Date.parse(row.scored_at) : Number.NaN);
  if (Math.min(...referenceTimes) !== Date.parse(p.reference_coverage[0])
    || Math.max(...referenceTimes) !== Date.parse(p.reference_coverage[1])
    || Math.min(...currentTimes) !== Date.parse(p.current_coverage[0])
    || Math.max(...currentTimes) !== Date.parse(p.current_coverage[1])) return null;
  return { evaluationId: value.evaluation_id, releaseId, releaseRevision: p.release_revision,
    modelName: p.model_name, modelVersion: p.model_version, modelDatasetSnapshotId: p.model_dataset_snapshot_id,
    storeId: p.store_id, horizonDays: p.horizon_days, monitoringWindow: p.monitoring_window,
    executedAt: p.release_executed_at, windowEnd: p.release_window_end, assessedAt: p.assessed_at,
    referenceIds: p.reference_ids, currentIds: p.current_ids, referenceCoverage: p.reference_coverage,
    currentCoverage: p.current_coverage, policyVersionId: p.policy.policy_version_id, sourceDigest: p.source_digest,
    driftShare: value.observed_metrics.prediction_drift_share, driftDetected: value.drift_detected,
    auditEventId: value.audit_event_id, recommendedAction: value.drift_detected ? "REVIEW_ROLLBACK" : "CONTINUE_OBSERVING" };
}

async function request(releaseId: string, roleId: string, guard: (() => Promise<boolean>) | undefined,
  signal: AbortSignal, selection?: CanonicalMonitoringSelection): Promise<unknown> {
  if (!guard) return null;
  const current = async () => !signal.aborted && await guard() && !signal.aborted;
  if (!await current()) return null;
  const response = await fetch(`/api/v1/learninghub/releases/${encodeURIComponent(releaseId)}/monitoring`, {
    method: selection ? "POST" : "GET", credentials: "same-origin", cache: "no-store", signal,
    headers: { Accept: "application/json", "X-Operator-Role": roleId,
      ...(selection ? { "Content-Type": "application/json" } : {}) },
    ...(selection ? { body: JSON.stringify(selection) } : {}),
  });
  if (!await current()) return null;
  if (!response.ok) throw new Error(`監測資料讀取或評估未完成（HTTP ${response.status}）`);
  const payload: unknown = await response.json();
  if (!await current()) return null;
  if (!object(payload) || payloadContainsSeedData(payload)) throw new Error("監測資料來源格式無法驗證");
  return payload;
}

export async function readCanonicalMonitoring(releaseId: string, roleId: string,
  guard: (() => Promise<boolean>) | undefined, signal: AbortSignal): Promise<CanonicalMonitoringRead> {
  if (!guard) return { state: "unavailable", reason: "尚未取得可信登入與監測資料讀取範圍" };
  try {
    const payload = await request(releaseId, roleId, guard, signal);
    if (payload === null || signal.aborted) return { state: "superseded" };
    if (!object(payload) || payload.release_id !== releaseId || !Array.isArray(payload.items)
      || !integer(payload.unavailable_count) || payload.unavailable_count < 0) throw new Error("監測資料格式無法驗證");
    const items: CanonicalMonitoringItem[] = [];
    for (const row of payload.items) {
      const item = parseCanonicalMonitoringItem(row, releaseId);
      if (!item || items.some(existing => existing.evaluationId === item.evaluationId)) throw new Error("監測資料關聯無法驗證");
      items.push(item);
    }
    if (payload.state === "unavailable" && items.length === 0 && text(payload.reason)) return { state: "unavailable", reason: "尚無本發布可驗證的預測分布漂移評估；來源或期間缺漏時仍不可評估" };
    if (payload.state !== "available" || !items.length || payload.reason !== null) throw new Error("監測資料狀態無法驗證");
    return { state: "ready", items, unavailableCount: payload.unavailable_count };
  } catch (error) {
    if (signal.aborted) return { state: "superseded" };
    return { state: "error", message: error instanceof Error ? error.message : "監測資料讀取失敗" };
  }
}

export async function evaluateCanonicalMonitoring(releaseId: string, roleId: string,
  selection: CanonicalMonitoringSelection, guard: (() => Promise<boolean>) | undefined,
  signal: AbortSignal): Promise<CanonicalMonitoringCommand> {
  if (!guard) return { state: "unavailable", reason: "尚未取得可信登入與監測資料範圍" };
  try {
    const payload = await request(releaseId, roleId, guard, signal, selection);
    if (payload === null || signal.aborted) return { state: "superseded" };
    if (!object(payload)) throw new Error("監測評估回條格式無法驗證");
    const item = parseCanonicalMonitoringItem(payload.item, releaseId);
    if (!item || item.storeId !== selection.store_id || item.horizonDays !== selection.horizon_days
      || JSON.stringify(item.referenceIds) !== JSON.stringify(selection.reference_ids)
      || JSON.stringify(item.currentIds) !== JSON.stringify(selection.current_ids)) throw new Error("監測評估來源回條不符");
    return { state: "saved", item };
  } catch (error) {
    if (signal.aborted) return { state: "superseded" };
    return { state: "error", message: error instanceof Error ? error.message : "監測評估未完成" };
  }
}

export type CanonicalMonitoringGuardFactory = ForecastReadGuardFactory;
