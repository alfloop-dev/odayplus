import { payloadContainsSeedData } from "../operatorDataMode";

export const NETPLAN_ACTIONS = ["OPEN", "KEEP", "IMPROVE", "MOVE", "EXIT"] as const;
export type NetPlanAction = typeof NETPLAN_ACTIONS[number];
export type BuilderConstraints = {
  max_budget: number;
  min_expected_gross_margin: number | null;
  min_capacity_delta: number | null;
  max_average_risk: number | null;
  min_action_counts: Partial<Record<NetPlanAction, number>>;
  max_action_counts: Partial<Record<NetPlanAction, number>>;
};
export type LifecycleOperation = "execute" | "outcome" | "close";
export type RecordedOutcome = { actualGrossMargin: number; sourceSnapshotIds: string[] };
export type NativeDecision = { decision: "approved" | "rejected"; approvalReceiptId: string };
export type OutcomeTrackingTask = { task_id: string; scenario_id: string; approval_id: string; horizon_days: 90 | 180 | 365;
  anchor_rule: "actual_execution_calendar_days"; execution_id: string | null; executed_at: string | null; due_at: string | null;
  status: "PENDING_EXECUTION" | "SCHEDULED"; measured_value: null; collected_outcome_id: null; mature: false; training_eligible: false };
export type ObservedNetPlanLabel = { label_id: string; scenario_id: string; outcome_id: string; actual_gross_margin: number;
  measurement_method: "manual_recorded"; status: "OBSERVED"; horizon_days: null; mature: false; training_eligible: false };
export type CollectionDueProgress = { task_id: string; scenario_id: string; job_id: string; execution_id: string; horizon_days: 90 | 180 | 365;
  due_at: string; due_observed_at: string; audit_event_id: string; task_source_sha256: string;
  state: "DUE_SOURCE_UNAVAILABLE"; collected_outcome_id: null; measured_value: null; mature: false; training_eligible: false };
export type OutcomeTracking = { state: "unavailable"; reasonCode: string; tasks: []; labels: []; receipt: null }
  | { state: "recorded"; reasonCode: null; tasks: OutcomeTrackingTask[]; labels: ObservedNetPlanLabel[];
      receipt: { receipt_id: string; audit_event_id: string; delivery_status: string };
      collectionProgress?: CollectionDueProgress[];
      automatic_collection: { available: false; reasonCode: string }; maturity: { available: false; reasonCode: string } };
export type LifecycleState = { canExecute: boolean; canApprove?: boolean; execution: { execution_id: string; scenario_id: string; executed_by: string; executed_at: string; actions: { entity_id: string; action: string }[] } | null;
  outcome: { outcome_id: string; scenario_id: string; expected_gross_margin: number; actual_gross_margin: number; variance: number; variance_pct: number; observed_at: string; source_snapshot_ids: string[] } | null };
export type CanonicalBuilder = {
  lifecycle?: LifecycleState;
  outcomeTracking?: OutcomeTracking;
  scenarioId: string; scenarioName: string; planningHorizon: string; status: string;
  problemHash: string; constraints: BuilderConstraints & Record<string, unknown>;
  optionsByEntity: Record<string, {
    entity_id: string; action: NetPlanAction; expected_gross_margin: number;
    budget_cost: number; risk_score: number; capacity_delta: number;
    source_snapshot_ids: string[]; notes: string[];
  }[]>;
  statusHistory: { from_status: string; to_status: string; actor: string; reason: string; occurred_at: string }[];
  modelVersion: string; featureVersion: string; solverVersion: string; policyVersion: string;
  policyAvailable: boolean; disclosurePolicyVersionId: string | null;
  editable: boolean; canSolve: boolean; requiresNewDraft: boolean; isStale: boolean; solve: unknown;
};
export type BuilderResult = { ok: true; builder: CanonicalBuilder; auditEventId?: string }
  | { ok: false; superseded: true } | { ok: false; message: string };
export type NetPlanBuilderApi = {
  decide?: (storeId: string, builder: CanonicalBuilder, reason: string, key: string, decision: NativeDecision) => Promise<BuilderResult>;
  lifecycle?: (storeId: string, operation: LifecycleOperation, builder: CanonicalBuilder, reason: string, key: string, outcome?: RecordedOutcome) => Promise<BuilderResult>;
  read: (storeId: string) => Promise<BuilderResult>;
  command: (storeId: string, operation: "save" | "solve", builder: CanonicalBuilder,
    reason: string, key: string, constraints?: BuilderConstraints) => Promise<BuilderResult>;
};
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const strings = (x: unknown): x is string[] => Array.isArray(x) && x.every(v => typeof v === "string");
export function isCanonicalBuilder(x: unknown): x is CanonicalBuilder {
  if (!object(x) || !object(x.constraints) || !object(x.optionsByEntity)) return false;
  if (!["scenarioId", "scenarioName", "planningHorizon", "status", "modelVersion", "featureVersion", "solverVersion", "policyVersion"].every(k => typeof x[k] === "string" && x[k])) return false;
  if (typeof x.problemHash !== "string" || !/^[0-9a-f]{64}$/.test(x.problemHash)
    || typeof x.policyAvailable !== "boolean" || !(x.disclosurePolicyVersionId === null || typeof x.disclosurePolicyVersionId === "string")
    || typeof x.editable !== "boolean" || typeof x.canSolve !== "boolean" || typeof x.requiresNewDraft !== "boolean"
    || typeof x.isStale !== "boolean" || !Array.isArray(x.statusHistory)
    || !x.statusHistory.every(h => object(h) && ["from_status", "to_status", "actor", "reason", "occurred_at"].every(k => typeof h[k] === "string"))) return false;
  if (x.lifecycle !== undefined && !isLifecycleState(x.lifecycle, x.scenarioId as string)) return false;
  if (x.outcomeTracking !== undefined && (!isLifecycleState(x.lifecycle, x.scenarioId as string)
    || !isOutcomeTracking(x.outcomeTracking, x.scenarioId as string, x.lifecycle))) return false;
  const c = x.constraints;
  if (!finite(c.max_budget) || c.max_budget < 0) return false;
  for (const key of ["min_expected_gross_margin", "min_capacity_delta", "max_average_risk"]) {
    if (c[key] !== null && !finite(c[key])) return false;
  }
  for (const key of ["min_action_counts", "max_action_counts"]) {
    const counts = c[key];
    if (!object(counts) || !Object.entries(counts).every(([action, count]) =>
      NETPLAN_ACTIONS.includes(action as NetPlanAction) && finite(count) && Number.isInteger(count) && count >= 0)) return false;
  }
  return Object.keys(x.optionsByEntity).length > 0 && Object.entries(x.optionsByEntity).every(([id, options]) => Array.isArray(options) && options.length > 0
    && options.every(o => object(o) && o.entity_id === id && NETPLAN_ACTIONS.includes(o.action as NetPlanAction)
      && ["expected_gross_margin", "budget_cost", "risk_score", "capacity_delta"].every(k => finite(o[k]))
      && strings(o.source_snapshot_ids) && strings(o.notes)));
}

export function isLifecycleState(value: unknown, scenarioId: string): value is LifecycleState {
  if (!object(value) || typeof value.canExecute !== "boolean") return false;
  if (value.canApprove !== undefined && typeof value.canApprove !== "boolean") return false;
  const execution = value.execution;
  const outcome = value.outcome;
  if (execution !== null && (!object(execution) || execution.scenario_id !== scenarioId
    || !["execution_id", "executed_by", "executed_at"].every(key => typeof execution[key] === "string" && execution[key])
    || !Number.isFinite(Date.parse(execution.executed_at as string)) || !Array.isArray(execution.actions)
    || !execution.actions.every(row => object(row) && typeof row.entity_id === "string" && row.entity_id
      && NETPLAN_ACTIONS.includes(row.action as NetPlanAction)))) return false;
  return outcome === null || object(outcome) && outcome.scenario_id === scenarioId
    && typeof outcome.outcome_id === "string" && Boolean(outcome.outcome_id)
    && typeof outcome.observed_at === "string" && Number.isFinite(Date.parse(outcome.observed_at))
    && ["expected_gross_margin", "actual_gross_margin", "variance", "variance_pct"].every(key => finite(outcome[key]))
    && strings(outcome.source_snapshot_ids);
}

export function isOutcomeTracking(value: unknown, scenarioId: string, lifecycle?: LifecycleState): value is OutcomeTracking {
  if (!object(value) || !Array.isArray(value.tasks) || !Array.isArray(value.labels)) return false;
  if (value.state === "unavailable") return typeof value.reasonCode === "string" && Boolean(value.reasonCode)
    && value.tasks.length === 0 && value.labels.length === 0 && value.receipt === null;
  const receipt = value.receipt;
  if (value.state !== "recorded" || value.reasonCode !== null || !object(receipt)
    || !["receipt_id", "audit_event_id", "delivery_status"].every(key => typeof receipt[key] === "string" && receipt[key])
    || !object(value.automatic_collection) || value.automatic_collection.available !== false || typeof value.automatic_collection.reasonCode !== "string"
    || !object(value.maturity) || value.maturity.available !== false || typeof value.maturity.reasonCode !== "string"
    || value.tasks.length !== 3 || value.labels.length > 1) return false;
  const awareTime = (value: unknown): value is string => typeof value === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  const horizons = new Set<number>(); const ids = new Set<string>(); const approvals = new Set<string>();
  const states = new Set<string>(); const executions = new Set<string | null>(); const anchors = new Set<string | null>();
  for (const row of value.tasks) {
    if (!object(row) || row.scenario_id !== scenarioId || typeof row.task_id !== "string" || !row.task_id
      || typeof row.approval_id !== "string" || !row.approval_id || ids.has(row.task_id)
      || typeof row.horizon_days !== "number" || ![90, 180, 365].includes(row.horizon_days) || horizons.has(row.horizon_days)
      || row.anchor_rule !== "actual_execution_calendar_days" || row.measured_value !== null || row.collected_outcome_id !== null
      || row.mature !== false || row.training_eligible !== false) return false;
    if (row.status === "PENDING_EXECUTION") {
      if (row.execution_id !== null || row.executed_at !== null || row.due_at !== null) return false;
    } else if (row.status === "SCHEDULED") {
      if (typeof row.execution_id !== "string" || !row.execution_id || !awareTime(row.executed_at) || !awareTime(row.due_at)
        || Date.parse(row.due_at) !== Date.parse(row.executed_at) + row.horizon_days * 86_400_000) return false;
    } else return false;
    horizons.add(row.horizon_days); ids.add(row.task_id); approvals.add(row.approval_id);
    states.add(row.status); executions.add(row.execution_id as string | null); anchors.add(row.executed_at as string | null);
  }
  if (approvals.size !== 1 || states.size !== 1 || executions.size !== 1 || anchors.size !== 1) return false;
  const executionId = executions.values().next().value;
  const anchor = anchors.values().next().value;
  if (executionId === null && value.labels.length !== 0) return false;
  if (value.collectionProgress !== undefined) {
    if (!Array.isArray(value.collectionProgress)) return false;
    const progressed = new Set<string>();
    for (const progress of value.collectionProgress) {
      if (!object(progress) || typeof progress.task_id !== "string" || progressed.has(progress.task_id)) return false;
      const task = value.tasks.find(row => object(row) && row.task_id === progress.task_id);
      if (!object(task) || task.status !== "SCHEDULED" || progress.state !== "DUE_SOURCE_UNAVAILABLE"
        || ["tenant_id", "scenario_id", "task_id", "execution_id", "horizon_days", "due_at"].some(key => progress[key] !== task[key])
        || typeof progress.job_id !== "string" || !progress.job_id || progress.job_id !== task.collection_job_id
        || typeof progress.audit_event_id !== "string" || !progress.audit_event_id
        || typeof progress.task_source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(progress.task_source_sha256)
        || !awareTime(progress.due_observed_at) || !awareTime(progress.due_at)
        || Date.parse(progress.due_observed_at) < Date.parse(progress.due_at)
        || progress.collected_outcome_id !== null || progress.measured_value !== null || progress.mature !== false || progress.training_eligible !== false) return false;
      progressed.add(progress.task_id);
    }
  }
  if (lifecycle !== undefined) {
    const execution = lifecycle.execution;
    if (executionId === null ? execution !== null : !execution || execution.execution_id !== executionId
      || !awareTime(execution.executed_at) || execution.executed_at !== anchor) return false;
    if ((lifecycle.outcome === null ? 0 : 1) !== value.labels.length) return false;
  }
  return value.labels.every(row => object(row) && row.scenario_id === scenarioId
    && typeof row.label_id === "string" && Boolean(row.label_id) && typeof row.outcome_id === "string" && Boolean(row.outcome_id)
    && finite(row.actual_gross_margin) && row.measurement_method === "manual_recorded" && row.status === "OBSERVED"
    && row.execution_id === executionId && row.horizon_days === null && row.mature === false && row.training_eligible === false
    && (lifecycle === undefined || lifecycle.outcome !== null && row.outcome_id === lifecycle.outcome.outcome_id
      && row.actual_gross_margin === lifecycle.outcome.actual_gross_margin));
}

export function createNetPlanBuilderApi(headers: Record<string, string>,
  captureGuard: () => (() => Promise<boolean>) | undefined,
  onChanged: () => Promise<void>): NetPlanBuilderApi {
  async function request(storeId: string, operation?: "save" | "solve" | "decide" | LifecycleOperation, builder?: CanonicalBuilder,
    reason?: string, key?: string, constraints?: BuilderConstraints, outcome?: RecordedOutcome, nativeDecision?: NativeDecision): Promise<BuilderResult> {
    const guard = captureGuard();
    if (!guard) return { ok: false, message: "尚無已驗證的工作階段；請重新載入。" };
    const current = async () => { try { return await guard(); } catch { return false; } };
    const superseded = (): BuilderResult => ({ ok: false, superseded: true });
    const cid = crypto.randomUUID();
    try {
      if (!await current()) return superseded();
      const response = await fetch(`/api/v1/operator/network-rebalance/stores/${encodeURIComponent(storeId)}/netplan/builder${operation && operation !== "save" ? `/${operation}` : ""}`, {
        method: operation === "save" ? "PUT" : operation ? "POST" : "GET",
        cache: "no-store", headers: { ...headers, "Content-Type": "application/json", "X-Correlation-Id": cid,
          ...(key ? { "Idempotency-Key": key } : {}) },
        ...(operation ? { body: JSON.stringify({ scenarioId: builder!.scenarioId,
          expectedProblemHash: builder!.problemHash, reason, ...(operation === "save" ? { constraints } : operation === "outcome" ? { outcome } : operation === "decide" ? { nativeDecision } : {}) }) } : {}),
      });
      if (!await current()) return superseded();
      const data: unknown = await response.json();
      if (!await current()) return superseded();
      if (!response.ok) return { ok: false, message: `NetPlan ${response.status}；請保留輸入後重試或重新讀取。追蹤：${cid}` };
      const value = operation && object(data) ? data.builder : data;
      if (payloadContainsSeedData(data) || !isCanonicalBuilder(value)) {
        return { ok: false, message: "NetPlan 未回傳完整的正式情境資料。" };
      }
      if (operation && ["execute", "outcome", "close", "decide"].includes(operation)
        && (value.scenarioId !== builder!.scenarioId || value.problemHash !== builder!.problemHash)) {
        return { ok: false, message: "執行情境與回應版本無法核對，請重新讀取。" };
      }
      if (operation) {
        if (!object(data) || !object(data.audit) || typeof data.audit.event_id !== "string" || !data.audit.event_id) {
          return { ok: false, message: `命令回應缺少稽核識別；請重新讀取確認。追蹤：${cid}` };
        }
        await onChanged();
        if (!await current()) return superseded();
        return { ok: true, builder: value, auditEventId: data.audit.event_id };
      }
      return { ok: true, builder: value };
    } catch {
      if (!await current()) return superseded();
      return { ok: false, message: `NetPlan 資料暫時無法取得；輸入已保留。追蹤：${cid}` };
    }
  }
  return { decide: (id, builder, reason, key, decision) => request(id, "decide", builder, reason, key, undefined, undefined, decision),
    lifecycle: (id, operation, builder, reason, key, outcome) => request(id, operation, builder, reason, key, undefined, outcome),
    read: id => request(id), command: (id, operation, builder, reason, key, constraints) =>
    request(id, operation, builder, reason, key, constraints) };
}
