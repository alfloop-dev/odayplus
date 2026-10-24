import { payloadContainsSeedData } from "./operatorDataMode";

export type ForecastBand = { horizonWeeks: 4 | 8 | 12 | 24; p10: number; p50: number; p90: number };
export type RecordedModelBinding = {
  model_service: "forecastops"; model_id: string; model_name: string; model_version: string;
  model_stage: string; dataset_snapshot_id: string; feature_schema_version: string;
  label_version: string; artifact_sha256: string; model_engine: string;
  registry_version: string | null; source_run_id: string | null; mlflow_run_id: string | null;
  model_approved_by: string | null; model_approved_at: string | null; binding_resolved_at: string | null;
};
export type RecordedQualityGuard = {
  alertId: string; forecastOutputId: string; predictionRunId: string;
  policy: { id: string; version: string; versionId: string };
  evaluatedAt: string; featureSnapshotTime: string; maxStalenessDays: number;
  stalenessDays: number | null; violated: boolean;
  onViolation: "SUPPRESS_HIGH_CONFIDENCE" | "SUPPRESS_ALERT";
  actualAlertLevel: "red" | "orange" | "yellow" | "green";
  thresholdAlertLevel: "red" | "orange" | "yellow" | "green"; alertReasonCode: string;
};
export type QualityAtRun = { state: "recorded"; reason: null; data: RecordedQualityGuard }
  | { state: "unavailable"; reason: "RECORDED_QUALITY_GUARD_UNAVAILABLE"; data: null };
export type SavedForecastAction = "inspect_machine_uptime" | "review_staffing" | "open_recovery_plan"
  | "launch_local_promotion" | "review_price_packaging" | "review_local_demand" | "create_intervention_candidate";
export type AlertAtRun = { state: "recorded"; reason: null; data: {
  alertId: string; level: "red" | "orange" | "yellow" | "green"; reasonCode: string; openedAt: string;
} } | { state: "unavailable"; reason: "RECORDED_ALERT_EVIDENCE_UNAVAILABLE"; data: null };
export type RecommendationAtAlert = { state: "recorded"; reason: null; data: {
  handoffId: string; alertId: string; forecastOutputId: string; predictionRunId: string;
  policy: { id: string; version: string; versionId: string }; createdAt: string;
  handoffStatus: "proposed" | "dispatched"; actionIds: SavedForecastAction[];
} } | { state: "unavailable"; reason: "RECORDED_RECOMMENDATION_UNAVAILABLE"; data: null };
export type RecordedForecast = {
  alertId: string; forecastOutputId: string; predictionRunId: string; forecastVersion: number;
  targetMetric: string; bands: ForecastBand[]; actualRevenue: number; siteScoreBaselineP50: number | null;
  siteScoreGapRatio: number; trajectoryClass: string; turningPointProbability: number;
  predictionOriginTime: string; scoredAt: string; featureVersion: string; sourceSnapshotIds: string[];
  dataQualityScore: number | null; dataStalenessDays: number | null;
  featureSnapshotTime?: string; qualityAtRun?: QualityAtRun;
  alertAtRun?: AlertAtRun; recommendationAtAlert?: RecommendationAtAlert;
  rootCause?: { state: "unavailable"; reason: "ROOT_CAUSE_CANDIDATE_UNPRODUCED";
    causeCandidate: null; evidenceStrength: null; dataConfidence: null };
  modelBinding: RecordedModelBinding; policy: { id: string; version: string; versionId: string };
};
export type ForecastReadResult = { state: "ready"; forecast: RecordedForecast }
  | { state: "unavailable"; reason: string }
  | { state: "superseded" }
  | { state: "error"; message: string };
export type ForecastReadGuardFactory = (current: () => boolean) => () => Promise<boolean>;

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullableText = (value: unknown) => value === null || text(value);
const nullableFinite = (value: unknown) => value === null || finite(value);

function awareTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  return year > 0 && month > 0 && month <= 12 && day > 0
    && day <= new Date(Date.UTC(year, month, 0)).getUTCDate()
    && hour < 24 && minute < 60 && second < 60;
}

function isBand(value: unknown): value is ForecastBand {
  return object(value) && [4, 8, 12, 24].some(weeks => value.horizonWeeks === weeks)
    && finite(value.p10) && finite(value.p50) && finite(value.p90)
    && value.p10 <= value.p50 && value.p50 <= value.p90;
}

function isModelBinding(value: unknown): value is RecordedModelBinding {
  return object(value) && value.model_service === "forecastops"
    && ["model_id", "model_name", "model_version", "model_stage", "dataset_snapshot_id",
      "feature_schema_version", "label_version", "model_engine"].every(key => text(value[key]))
    && value.model_engine !== "baseline"
    && typeof value.artifact_sha256 === "string" && /^[0-9a-f]{64}$/.test(value.artifact_sha256)
    && ["registry_version", "source_run_id", "mlflow_run_id", "model_approved_by"].every(key => nullableText(value[key]))
    && ["model_approved_at", "binding_resolved_at"].every(key => value[key] === null || awareTime(value[key]));
}

function isRecordedForecast(value: unknown): value is RecordedForecast {
  if (!object(value)) return false;
  const policy = value.policy;
  return value.targetMetric === "revenue"
    && ["alertId", "forecastOutputId", "predictionRunId", "trajectoryClass", "featureVersion"].every(key => text(value[key]))
    && finite(value.forecastVersion) && Number.isInteger(value.forecastVersion) && value.forecastVersion > 0
    && Array.isArray(value.bands) && value.bands.length === 4
    && value.bands.every((band, index) => isBand(band) && band.horizonWeeks === [4, 8, 12, 24][index])
    && ["actualRevenue", "siteScoreGapRatio", "turningPointProbability"].every(key => finite(value[key]))
    && nullableFinite(value.siteScoreBaselineP50) && nullableFinite(value.dataQualityScore)
    && (value.dataStalenessDays === null || finite(value.dataStalenessDays)
      && Number.isInteger(value.dataStalenessDays) && value.dataStalenessDays >= 0)
    && awareTime(value.predictionOriginTime) && awareTime(value.scoredAt)
    && Array.isArray(value.sourceSnapshotIds) && value.sourceSnapshotIds.length > 0 && value.sourceSnapshotIds.every(text)
    && isModelBinding(value.modelBinding) && object(policy)
    && ["id", "version", "versionId"].every(key => text(policy[key]));
}

/** Optional execution-time evidence cannot grant current freshness or invent a policy. */
export function recordedQualityAtRun(forecast: RecordedForecast): QualityAtRun {
  const unavailable: QualityAtRun = { state: "unavailable", reason: "RECORDED_QUALITY_GUARD_UNAVAILABLE", data: null };
  const quality: unknown = forecast.qualityAtRun;
  if (!object(quality) || quality.state !== "recorded" || quality.reason !== null || !object(quality.data)) return unavailable;
  const saved = quality.data;
  if (saved.alertId !== forecast.alertId || saved.forecastOutputId !== forecast.forecastOutputId
    || saved.predictionRunId !== forecast.predictionRunId || !object(saved.policy)
    || saved.policy.id !== forecast.policy.id || saved.policy.version !== forecast.policy.version
    || saved.policy.versionId !== forecast.policy.versionId || !awareTime(saved.evaluatedAt)
    || !awareTime(forecast.featureSnapshotTime) || saved.featureSnapshotTime !== forecast.featureSnapshotTime
    || !awareTime(saved.featureSnapshotTime) || !finite(saved.maxStalenessDays)
    || !Number.isInteger(saved.maxStalenessDays) || saved.maxStalenessDays < 0
    || !(saved.stalenessDays === null || finite(saved.stalenessDays)
      && Number.isInteger(saved.stalenessDays) && saved.stalenessDays >= 0)
    || saved.stalenessDays !== forecast.dataStalenessDays || typeof saved.violated !== "boolean"
    || !(saved.onViolation === "SUPPRESS_HIGH_CONFIDENCE" || saved.onViolation === "SUPPRESS_ALERT")
    || !(saved.actualAlertLevel === "red" || saved.actualAlertLevel === "orange" || saved.actualAlertLevel === "yellow" || saved.actualAlertLevel === "green")
    || !(saved.thresholdAlertLevel === "red" || saved.thresholdAlertLevel === "orange" || saved.thresholdAlertLevel === "yellow" || saved.thresholdAlertLevel === "green")
    || !text(saved.alertReasonCode)) return unavailable;
  // Reject contradictory retained evidence; do not manufacture a new decision.
  if (saved.violated !== (saved.stalenessDays === null || saved.stalenessDays > saved.maxStalenessDays)
    || Date.parse(saved.featureSnapshotTime) > Date.parse(forecast.predictionOriginTime)) return unavailable;
  const expectedLevel = saved.violated && saved.onViolation === "SUPPRESS_ALERT" ? "green"
    : saved.violated && saved.onViolation === "SUPPRESS_HIGH_CONFIDENCE" && saved.thresholdAlertLevel === "red"
      ? "orange" : saved.thresholdAlertLevel;
  const expectedReason = saved.violated && (saved.onViolation === "SUPPRESS_ALERT" || saved.thresholdAlertLevel === "red")
    ? "data_quality_stale" : saved.thresholdAlertLevel === "green" ? "within_expected_band" : "sitescore_gap";
  if (saved.actualAlertLevel !== expectedLevel || saved.alertReasonCode !== expectedReason) return unavailable;
  return { state: "recorded", reason: null, data: {
    alertId: forecast.alertId, forecastOutputId: forecast.forecastOutputId, predictionRunId: forecast.predictionRunId,
    policy: forecast.policy, evaluatedAt: saved.evaluatedAt, featureSnapshotTime: saved.featureSnapshotTime,
    maxStalenessDays: saved.maxStalenessDays, stalenessDays: saved.stalenessDays, violated: saved.violated,
    onViolation: saved.onViolation, actualAlertLevel: saved.actualAlertLevel,
    thresholdAlertLevel: saved.thresholdAlertLevel, alertReasonCode: saved.alertReasonCode,
  } };
}

export function recordedAlertAtRun(forecast: RecordedForecast): AlertAtRun {
  const unavailable: AlertAtRun = { state: "unavailable", reason: "RECORDED_ALERT_EVIDENCE_UNAVAILABLE", data: null };
  const wire: unknown = forecast.alertAtRun;
  if (!object(wire) || wire.state !== "recorded" || wire.reason !== null || !object(wire.data)) return unavailable;
  const data = wire.data;
  if (data.alertId !== forecast.alertId || !text(data.reasonCode) || !awareTime(data.openedAt)
    || !(data.level === "red" || data.level === "orange" || data.level === "yellow" || data.level === "green")) return unavailable;
  return { state: "recorded", reason: null, data: {
    alertId: forecast.alertId, level: data.level, reasonCode: data.reasonCode, openedAt: data.openedAt,
  } };
}

const savedActionLabels: Record<SavedForecastAction, string> = {
  inspect_machine_uptime: "檢查設備可用率", review_staffing: "檢查現場人力", open_recovery_plan: "建立恢復計畫",
  launch_local_promotion: "評估在地促銷", review_price_packaging: "檢查價格與方案",
  review_local_demand: "檢查在地需求", create_intervention_candidate: "建立干預候選",
};
export const savedForecastActionLabel = (action: SavedForecastAction) => savedActionLabels[action];
const isSavedAction = (value: unknown): value is SavedForecastAction =>
  typeof value === "string" && Object.hasOwn(savedActionLabels, value);

export function recordedRecommendationAtAlert(forecast: RecordedForecast): RecommendationAtAlert {
  const unavailable: RecommendationAtAlert = { state: "unavailable", reason: "RECORDED_RECOMMENDATION_UNAVAILABLE", data: null };
  const alert = recordedAlertAtRun(forecast);
  const wire: unknown = forecast.recommendationAtAlert;
  if (alert.state !== "recorded" || !object(wire) || wire.state !== "recorded"
    || wire.reason !== null || !object(wire.data)) return unavailable;
  const data = wire.data;
  if (!text(data.handoffId) || data.alertId !== forecast.alertId
    || data.forecastOutputId !== forecast.forecastOutputId || data.predictionRunId !== forecast.predictionRunId
    || !object(data.policy) || data.policy.id !== forecast.policy.id || data.policy.version !== forecast.policy.version
    || data.policy.versionId !== forecast.policy.versionId || !awareTime(data.createdAt)
    || !(data.handoffStatus === "proposed" || data.handoffStatus === "dispatched")
    || !Array.isArray(data.actionIds) || data.actionIds.length === 0 || !data.actionIds.every(isSavedAction)
    || new Set(data.actionIds).size !== data.actionIds.length) return unavailable;
  return { state: "recorded", reason: null, data: {
    handoffId: data.handoffId, alertId: forecast.alertId, forecastOutputId: forecast.forecastOutputId,
    predictionRunId: forecast.predictionRunId, policy: forecast.policy, createdAt: data.createdAt,
    handoffStatus: data.handoffStatus, actionIds: data.actionIds,
  } };
}

export function parseCurrentForecast(
  value: unknown, issueId: string, storeId: string,
): ForecastReadResult {
  if (!object(value) || payloadContainsSeedData(value) || !object(value.issue)
    || value.issue.id !== issueId || value.issue.storeId !== storeId || !object(value.forecast)
    || value.forecast.issueId !== issueId || value.forecast.storeId !== storeId) {
    return { state: "error", message: "預測回應與目前事件、門市或來源無法核對。" };
  }
  const envelope = value.forecast;
  if (envelope.status === "unavailable" && envelope.data === null && text(envelope.reason)) {
    return { state: "unavailable", reason: envelope.reason };
  }
  if (envelope.status !== "recorded" || envelope.reason !== null || !isRecordedForecast(envelope.data)
    || value.issue.source !== "forecastOps" || !object(value.issue.sourceRef)
    || value.issue.sourceRef.kind !== "forecastops.alert" || value.issue.sourceRef.id !== envelope.data.alertId
    || issueId !== `ISS-FC-${envelope.data.alertId}`) {
    return { state: "error", message: "預測資料缺少完整週期、執行來源或目前事件關聯。" };
  }
  return { state: "ready", forecast: envelope.data };
}

export async function readCurrentForecast({
  issueId, storeId, headers, guard, signal,
}: {
  issueId: string; storeId: string; headers: Record<string, string>;
  guard?: () => Promise<boolean>; signal: AbortSignal;
}): Promise<ForecastReadResult> {
  if (!guard) return { state: "unavailable", reason: "CURRENT_AUTHENTICATED_READ_REQUIRED" };
  const current = async () => {
    try { return !signal.aborted && await guard() && !signal.aborted; }
    catch { return false; }
  };
  const superseded = (): ForecastReadResult => ({ state: "superseded" });
  try {
    if (!await current()) return superseded();
    const response = await fetch(`/api/v1/operator/store-ops/issues/${encodeURIComponent(issueId)}/evidence`, {
      method: "GET", cache: "no-store", credentials: "same-origin", headers, signal,
    });
    if (!await current()) return superseded();
    const payload: unknown = await response.json();
    if (!await current()) return superseded();
    if (!response.ok) return { state: "error", message: "目前無法讀取這個事件的預測，請確認權限或重試。" };
    return parseCurrentForecast(payload, issueId, storeId);
  } catch {
    if (!await current()) return superseded();
    return { state: "error", message: "預測資料暫時無法取得，請稍後重試。" };
  }
}

function csvCell(value: string | number | null): string {
  const raw = value === null ? "" : String(value);
  const safe = typeof value === "string" && /^(?:[\t\r\n]|[\s\p{Cc}\p{Cf}]*[=+\-@])/u.test(raw) ? "'" + raw : raw;
  return '"' + safe.replaceAll('"', '""') + '"';
}

/** Only the four displayed rows and their displayed source identities. */
export function visibleForecastCsv(forecast: RecordedForecast, storeId: string): string {
  const quality = recordedQualityAtRun(forecast);
  const saved = quality.state === "recorded" ? quality.data : null;
  const rows: (string | number | null)[][] = [[
    "store_id", "horizon_weeks", "p10", "p50", "p90", "actual_revenue", "sitescore_baseline_p50",
    "forecast_output_id", "prediction_run_id", "model_id", "model_version", "feature_version",
    "policy_version_id", "prediction_origin_time", "scored_at", "source_snapshot_ids",
    "trajectory_class", "sitescore_gap_ratio", "turning_point_probability", "data_quality_score", "data_staleness_days",
    "model_name", "model_stage", "model_engine", "artifact_sha256", "dataset_snapshot_id", "feature_schema_version", "label_version", "registry_version",
    "feature_snapshot_time", "quality_guard_state_at_run", "quality_evaluated_at", "quality_max_staleness_days",
    "quality_violated_at_run", "quality_on_violation", "actual_alert_level_at_run", "threshold_alert_level_at_run", "alert_reason_code",
  ], ...forecast.bands.map(band => [
    storeId, band.horizonWeeks, band.p10, band.p50, band.p90, forecast.actualRevenue, forecast.siteScoreBaselineP50,
    forecast.forecastOutputId, forecast.predictionRunId, forecast.modelBinding.model_id, forecast.modelBinding.model_version,
    forecast.featureVersion, forecast.policy.versionId, forecast.predictionOriginTime, forecast.scoredAt,
    forecast.sourceSnapshotIds.join(";"),
    forecast.trajectoryClass, forecast.siteScoreGapRatio, forecast.turningPointProbability, forecast.dataQualityScore, forecast.dataStalenessDays,
    forecast.modelBinding.model_name, forecast.modelBinding.model_stage, forecast.modelBinding.model_engine,
    forecast.modelBinding.artifact_sha256, forecast.modelBinding.dataset_snapshot_id, forecast.modelBinding.feature_schema_version,
    forecast.modelBinding.label_version, forecast.modelBinding.registry_version,
    forecast.featureSnapshotTime && awareTime(forecast.featureSnapshotTime) ? forecast.featureSnapshotTime : null,
    quality.state, saved?.evaluatedAt ?? null, saved?.maxStalenessDays ?? null,
    saved ? String(saved.violated) : null, saved?.onViolation ?? null, saved?.actualAlertLevel ?? null,
    saved?.thresholdAlertLevel ?? null, saved?.alertReasonCode ?? null,
  ])];
  return "\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
