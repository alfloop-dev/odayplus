import { compareNativeInstants, id, INTERVENTION_KINDS, object, parseIntervention, parseOwnedRead, sameScope, type ActorScope, type Intervention, type ReadContext } from "./interventionContract";

export const LIFECYCLE_COMMANDS = ["assign", "unassign", "eligibility", "action", "conflict-check", "submit", "execute", "outcomes", "evaluate", "close", "adjust", "stop", "rollback"] as const;
export type LifecycleCommand = typeof LIFECYCLE_COMMANDS[number];
export type CommandContext = ReadContext & { command: LifecycleCommand; cohort_sha256: string | null };
type Assignment = { predecessor_id: string; replacement_id: string; actor: string; reason: string; adjusted_at: string; policy_version: string; rollback_plan: string | Record<string, unknown> | null };
type Close = { disposition: string; actor: string; reason: string; closed_at: string; policy_version: string; recommendation: string; follow_up_intervention_id: string | null; has_follow_up: boolean };
export type LifecycleCase = Intervention & {
  assigned_to: string | null; assigned_at: string | null; assigned_by: string | null; assignment_role: string | null;
  close: Close | null; predecessor_id: string | null; replacement_id: string | null; adjustment: Assignment | null;
  lifecycle_audit?: unknown;
};
export type LifecycleAttempt = {
  command: LifecycleCommand; original: LifecycleCase; scope: ActorScope; context: CommandContext;
  body: Record<string, unknown>; serialized: string; key: string; correlation: string; headers: Record<string, string>;
};
export type LifecycleReceipt = { raw: Record<string, unknown>; target: LifecycleCase; child: LifecycleCase | null; label: Record<string, unknown> | null; auditId: string; childAuditId: string | null };
export const COMMAND_LABELS: Record<LifecycleCommand, string> = {
  assign: "指派案件", unassign: "取消指派", eligibility: "記錄人工資格檢查", action: "建立行動提案", "conflict-check": "檢查重疊衝突", submit: "送交核准",
  execute: "執行並開啟觀察窗", outcomes: "錄入量測結果", evaluate: "評估效果並寫回標籤", close: "結案", adjust: "調整並建立替代案", stop: "停止干預", rollback: "回滾干預",
};
export const COMMAND_SECTION: Record<LifecycleCommand, number> = { assign: 1, unassign: 1, eligibility: 3, action: 4, "conflict-check": 5, submit: 6, execute: 7, outcomes: 8, evaluate: 8, close: 8, adjust: 7, stop: 7, rollback: 7 };
const terminal = ["INELIGIBLE", "REJECTED", "CLOSED", "STOPPED", "ROLLED_BACK"];
const sourceStates: Record<string, string[]> = {
  eligibility: ["CANDIDATE", "ELIGIBILITY_CHECKING"], action: ["ELIGIBLE"], "conflict-check": ["ACTION_PROPOSED", "CONFLICT_CHECKING"], submit: ["CONFLICT_CHECKING"],
  execute: ["APPROVED"], outcomes: ["OBSERVING"], evaluate: ["OBSERVING", "EVALUATING"], close: ["COMPLETED"], adjust: ["APPROVED", "EXECUTING", "OBSERVING"], stop: ["APPROVED", "EXECUTING", "OBSERVING"], rollback: ["EXECUTING", "OBSERVING", "EVALUATING"],
};
const integer = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const text = (v: unknown): v is string => typeof v === "string";
const instant = (v: unknown): v is string => compareNativeInstants(v, v) === 0;
const nullableId = (v: unknown) => v === null || id(v);
const nullableTime = (v: unknown) => v === null || instant(v);
const nullableText = (v: unknown) => v === null || text(v);
function jsonValue(v: unknown): boolean {
  return v === null || typeof v === "string" || typeof v === "boolean" || finite(v) || Array.isArray(v) && v.every(jsonValue) || object(v) && Object.values(v).every(jsonValue);
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(stable).join(",") + "]";
  if (object(value)) return "{" + Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + stable(value[k])).join(",") + "}";
  return JSON.stringify(value);
}
export const nativeEqual = (a: unknown, b: unknown): boolean => stable(a) === stable(b);
const sameInstant = (a: unknown, b: unknown) => compareNativeInstants(a, b) === 0;
function serializedTimes(values: string[]): { wall: bigint; absolute: bigint }[] | null {
  const parts = values.map(v => instant(v) ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|([+-])(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)$/.exec(v) : null);
  if (parts.some(v => !v)) return null;
  const precision = Math.max(...parts.map(p => Math.max(p![7]?.length ?? 0, p![13]?.length ?? 0))), scale = 10n ** BigInt(precision);
  return parts.map(p => {
    const date = new Date(0); date.setUTCFullYear(Number(p![1]), Number(p![2]) - 1, Number(p![3])); date.setUTCHours(Number(p![4]), Number(p![5]), Number(p![6]), 0);
    const local = BigInt(date.getTime() / 1000) * scale + BigInt((p![7] ?? "").padEnd(precision, "0") || "0");
    const offset = BigInt(Number(p![10] ?? 0) * 3600 + Number(p![11] ?? 0) * 60 + Number(p![12] ?? 0)) * scale + BigInt((p![13] ?? "").padEnd(precision, "0") || "0");
    return { wall: local, absolute: local - (p![9] === "-" ? -offset : offset) };
  });
}
export function hasVerifiedDefaultChildDuration(row: Pick<Intervention, "planned_start" | "planned_end">): boolean {
  const values = serializedTimes([row.planned_start, row.planned_end]);
  return Boolean(values && values[1].wall > values[0].wall && values[1].wall - values[0].wall === values[1].absolute - values[0].absolute);
}
function equalSerializedDurations(aStart: string, aEnd: string, bStart: string, bEnd: string): boolean {
  // Pickle preserves native tzinfo identity. Subtraction may use same-zone wall
  // time or distinct-zone instants; ISO offsets alone cannot choose between them.
  // Addition of timedelta uses the child's wall clock, even across a DST change.
  const values = serializedTimes([aStart, aEnd, bStart, bEnd]);
  if (!values) return false;
  const duration = values[1].wall - values[0].wall;
  return duration > 0n && duration === values[1].absolute - values[0].absolute && duration === values[3].wall - values[2].wall;
}
export function commandEligible(row: Intervention, command: LifecycleCommand): boolean {
  if (command === "assign" || command === "unassign") return !terminal.includes(row.status);
  if (!sourceStates[command]?.includes(row.status)) return false;
  if (command === "execute") return row.approval?.approved === true;
  if (command === "evaluate") return Boolean(row.outcome && row.observation_window);
  if (command === "submit") return Boolean(row.conflict && !row.conflict.blocks_approval);
  if (command === "close") return !row.effect || row.effect.observation_mature;
  return true;
}
function extendedCase(v: unknown, core: Intervention | null): LifecycleCase | null {
  if (!core || !object(v) || !nullableId(v.assigned_to) || !nullableTime(v.assigned_at) || !nullableId(v.assigned_by) || !nullableText(v.assignment_role)
    || !nullableId(v.predecessor_id) || !nullableId(v.replacement_id)) return null;
  if (v.assigned_to === null ? v.assigned_at !== null || v.assigned_by !== null || v.assignment_role !== null : !instant(v.assigned_at) || !id(v.assigned_by)) return null;
  if (v.close !== null && (!object(v.close) || !["KEEP", "REVERT", "ITERATE", "ESCALATE"].includes(String(v.close.disposition)) || !id(v.close.actor) || !id(v.close.reason)
    || !instant(v.close.closed_at) || !id(v.close.policy_version) || !text(v.close.recommendation) || !nullableId(v.close.follow_up_intervention_id)
    || v.close.has_follow_up !== (v.close.follow_up_intervention_id !== null))) return null;
  if (v.adjustment !== null && (!object(v.adjustment) || !id(v.adjustment.predecessor_id) || !id(v.adjustment.replacement_id) || !id(v.adjustment.actor)
    || !id(v.adjustment.reason) || !instant(v.adjustment.adjusted_at) || !id(v.adjustment.policy_version)
    || !(v.adjustment.rollback_plan === null || text(v.adjustment.rollback_plan) || object(v.adjustment.rollback_plan)))) return null;
  return core as LifecycleCase;
}
export function parseLifecycleRead(v: unknown, scope: ActorScope, expectedId: string): LifecycleCase | null {
  return extendedCase(v, parseOwnedRead(v, scope, expectedId));
}
export function parseCommandContext(v: unknown, row: Intervention, scope: ActorScope, command: LifecycleCommand): CommandContext | null {
  const allowed = ["schema_version", "command", "intervention_id", "store_id", "tenant_id", "actor_subject_id", "version", "policy_version", "snapshot_sha256", "cohort_sha256"];
  if (!object(v) || Object.keys(v).some(k => !allowed.includes(k)) || v.schema_version !== 1 || v.command !== command
    || v.intervention_id !== row.intervention_id || v.store_id !== row.store_id || v.tenant_id !== scope.tenantId || v.actor_subject_id !== scope.actorSubjectId
    || !integer(v.version) || v.version !== row.version || v.policy_version !== row.policy_version || !text(v.snapshot_sha256) || !/^[a-f0-9]{64}$/.test(v.snapshot_sha256)
    || v.snapshot_sha256 !== row.review_context?.snapshot_sha256
    || (command === "conflict-check" || command === "submit" ? !text(v.cohort_sha256) || !/^[a-f0-9]{64}$/.test(v.cohort_sha256) : v.cohort_sha256 !== null)) return null;
  return v as CommandContext;
}
const businessKeys: Record<LifecycleCommand, string[]> = {
  assign: ["assignee", "role"], unassign: [], eligibility: ["eligible", "reasons"], action: ["action_spec"], "conflict-check": ["allow_overlap", "reason"], submit: [], execute: [],
  outcomes: ["incremental_revenue", "incremental_gross_margin", "ad_spend", "has_control_group", "pretrend_status", "treatment_store_count", "control_store_count", "evaluation_method", "randomized", "measurement_method"],
  evaluate: [], close: ["disposition", "reason", "follow_up", "follow_up_kind"], adjust: ["reason", "action_spec", "planned_start", "planned_end", "expected_outcome", "rollback_plan"], stop: ["reason"], rollback: ["reason"],
};
export function makeLifecycleAttempt(row: LifecycleCase, scope: ActorScope, context: CommandContext, business: Record<string, unknown>): LifecycleAttempt {
  const command = context.command;
  if (!commandEligible(row, command) || !parseCommandContext(context, row, scope, command) || !jsonValue(business) || Object.keys(business).some(k => !businessKeys[command].includes(k))) throw new Error("LIFECYCLE_CONTEXT_REQUIRED");
  if (command === "assign" && (!id(business.assignee) || business.role !== undefined && !nullableText(business.role))) throw new Error("請填寫受指派者。");
  if (command === "eligibility" && (typeof business.eligible !== "boolean" || !Array.isArray(business.reasons) || !business.reasons.every(text))) throw new Error("人工資格結果與檢查理由尚未填妥。");
  if (command === "action" && !object(business.action_spec)) throw new Error("請確認行動設定。");
  if (command === "conflict-check" && (typeof business.allow_overlap !== "boolean" || !text(business.reason) || business.allow_overlap && !id(business.reason))) throw new Error("允許重疊時必須填寫理由。");
  if (["close", "adjust", "stop", "rollback"].includes(command) && !id(business.reason)) throw new Error("操作理由為必填。");
  if (command === "close" && (!["KEEP", "REVERT", "ITERATE", "ESCALATE"].includes(String(business.disposition)) || typeof business.follow_up !== "boolean"
    || business.follow_up_kind !== undefined && !(INTERVENTION_KINDS as readonly unknown[]).includes(business.follow_up_kind))) throw new Error("請確認結案處置與追蹤案類型。");
  if (command === "adjust" && (business.action_spec !== undefined && !object(business.action_spec) || business.expected_outcome !== undefined && !id(business.expected_outcome)
    || business.planned_start !== undefined && !instant(business.planned_start) || business.planned_end !== undefined && !instant(business.planned_end)
    || business.rollback_plan !== undefined && !(text(business.rollback_plan) || object(business.rollback_plan)))) throw new Error("請確認替代案設定與計畫時間。");
  if (command === "adjust" && business.planned_end === undefined && !hasVerifiedDefaultChildDuration(row)) throw new Error("原案期間包含時區變化，請填寫替代案計畫結束時間後再確認。");
  if (command === "close" && business.follow_up === true && !hasVerifiedDefaultChildDuration(row)) throw new Error("現有紀錄不足以核對追蹤案的預設期限；請先取消建立追蹤案。");
  if (command === "outcomes") {
    const counts = ["treatment_store_count", "control_store_count"];
    if (!["incremental_revenue", "incremental_gross_margin", "ad_spend"].every(k => finite(business[k])) || !counts.every(k => typeof business[k] === "number" && Number.isSafeInteger(business[k]) && Number(business[k]) >= 0)
      || typeof business.has_control_group !== "boolean" || typeof business.randomized !== "boolean" || !["PASS", "FAIL", "INCONCLUSIVE"].includes(String(business.pretrend_status))
      || !["NONE", "BEFORE_AFTER", "MATCHED_BEFORE_AFTER", "DID", "SYNTHETIC_CONTROL", "RCT"].includes(String(business.evaluation_method))
      || business.measurement_method !== undefined && !id(business.measurement_method)) throw new Error("請填寫有效的量測數值、店數及方法。");
  }
  const body = { ...structuredClone(business), ...(command === "execute" ? { executor: scope.actorSubjectId } : { actor: scope.actorSubjectId }),
    ...(command === "evaluate" ? { replicated: false } : {}), expected_version: row.version, expectedActorSubjectId: scope.actorSubjectId, expectedTenantId: scope.tenantId, command_context: { ...context } };
  const key = crypto.randomUUID(), correlation = crypto.randomUUID();
  return { command, original: structuredClone(row), scope: { ...scope }, context: { ...context }, body, serialized: JSON.stringify(body), key, correlation,
    headers: { "Content-Type": "application/json", "If-Match": 'W/"' + row.version + '"', "If-Actor-Match": scope.actorSubjectId, "Idempotency-Key": key, "X-Correlation-Id": correlation } };
}
function pathFor(command: LifecycleCommand, original: LifecycleCase, result: LifecycleCase): [string, string, string][] {
  const reason = command === "assign" ? "assigned to " + result.assigned_to + (result.assignment_role ? " (" + result.assignment_role + ")" : "") : command === "eligibility" ? result.eligibility?.eligible ? "eligible" : "ineligible" : "";
  const paths: Record<LifecycleCommand, [string, string, string][]> = {
    assign: [["assign", original.status, reason]], unassign: [["unassign", original.status, "unassigned"]], eligibility: [["check_eligibility", result.eligibility?.eligible ? "ELIGIBLE" : "INELIGIBLE", reason]],
    action: [["propose_action", "ACTION_PROPOSED", "action candidate built"]], "conflict-check": [["check_conflict", "CONFLICT_CHECKING", result.conflict?.has_conflict ? "conflict detected" : "no conflict"]],
    submit: [["submit_for_approval", "PENDING_APPROVAL", "submitted for approval"]], execute: [["execute", "EXECUTING", "execution coordinated"], ["start_observation", "OBSERVING", "observation window opened"]],
    outcomes: [["collect_outcome", "OBSERVING", "outcome collected"]], evaluate: [["evaluate_effect", "EVALUATING", "effect evaluation started"], ...(result.effect?.observation_mature ? [["complete", "COMPLETED", "evidence " + result.effect.evidence_level] as [string, string, string]] : [])],
    close: [["close", "CLOSED", result.close?.reason ?? ""]], adjust: [["adjust", "STOPPED", "adjusted by replacement " + result.replacement_id + ": " + result.adjustment?.reason]],
    stop: [["stop", "STOPPED", result.history.at(-1)?.reason ?? ""]], rollback: [["rollback", "ROLLED_BACK", result.history.at(-1)?.reason ?? ""]],
  };
  return paths[command];
}
const caseFields = ["intervention_id", "store_id", "kind", "trigger_ref", "expected_outcome", "planned_start", "planned_end", "window_spec", "created_by", "created_at", "policy_version", "action_spec", "eligibility", "conflict", "approval", "execution", "observation_window", "outcome", "effect", "close", "assigned_to", "assigned_at", "assigned_by", "assignment_role", "predecessor_id", "replacement_id", "adjustment"];
const changedFields: Record<LifecycleCommand, string[]> = { assign: ["assigned_to", "assigned_at", "assigned_by", "assignment_role"], unassign: ["assigned_to", "assigned_at", "assigned_by", "assignment_role"], eligibility: ["eligibility"], action: ["action_spec"], "conflict-check": ["conflict"], submit: [], execute: ["execution", "observation_window"], outcomes: ["outcome"], evaluate: ["effect"], close: ["close"], adjust: ["replacement_id", "adjustment"], stop: [], rollback: [] };
function businessMatches(result: LifecycleCase, attempt: LifecycleAttempt, child: LifecycleCase | null): boolean {
  const b = attempt.body, command = attempt.command, original = attempt.original;
  if (command === "assign") return result.assigned_to === b.assignee && result.assigned_by === attempt.scope.actorSubjectId && result.assignment_role === (b.role ?? null) && instant(result.assigned_at);
  if (command === "unassign") return [result.assigned_to, result.assigned_at, result.assigned_by, result.assignment_role].every(v => v === null);
  if (command === "eligibility") return Boolean(result.eligibility && result.eligibility.eligible === b.eligible && nativeEqual(result.eligibility.reasons, b.reasons));
  if (command === "action") return nativeEqual(result.action_spec, { ...original.action_spec, ...(b.action_spec as Record<string, unknown>) });
  if (command === "conflict-check") return Boolean(result.conflict && result.conflict.resolved === (!result.conflict.has_conflict || b.allow_overlap === true)
    && result.conflict.resolution_reason === (result.conflict.has_conflict && b.allow_overlap ? b.reason : ""));
  if (command === "submit") return Boolean(result.conflict && !result.conflict.blocks_approval);
  if (command === "execute") return result.execution?.executor === attempt.scope.actorSubjectId && result.execution.correlation_id === attempt.correlation && result.execution.status === "EXECUTED";
  if (command === "outcomes") return Boolean(result.outcome && businessKeys.outcomes.filter(k => k !== "measurement_method").every(k => nativeEqual(result.outcome?.[k], b[k]))
    && result.outcome.measurement_method === (b.measurement_method || "panel_did"));
  if (command === "evaluate") return Boolean(result.effect && result.effect.evidence_level !== "L5");
  if (command === "close") return Boolean(result.close && result.close.actor === attempt.scope.actorSubjectId && result.close.reason === b.reason && result.close.disposition === b.disposition
    && result.close.has_follow_up === b.follow_up && result.close.recommendation === (original.effect?.recommendation ?? ""));
  if (command === "adjust") return Boolean(child && result.adjustment && result.replacement_id === child.intervention_id && child.predecessor_id === original.intervention_id
    && result.adjustment.actor === attempt.scope.actorSubjectId && result.adjustment.reason === b.reason && nativeEqual(result.adjustment, child.adjustment)
    && child.adjustment?.predecessor_id === original.intervention_id && child.adjustment.replacement_id === child.intervention_id
    && child.kind === original.kind && nativeEqual(child.window_spec, original.window_spec)
    && nativeEqual(child.action_spec, { ...original.action_spec, ...(object(b.action_spec) ? b.action_spec : {}), ...(b.rollback_plan !== undefined ? { rollback_plan: b.rollback_plan } : {}) })
    && child.expected_outcome === (b.expected_outcome || "adjusted from " + original.intervention_id + ": " + original.expected_outcome)
    && (b.planned_start === undefined || sameInstant(child.planned_start, b.planned_start)) && (b.planned_end === undefined ? equalSerializedDurations(original.planned_start, original.planned_end, child.planned_start, child.planned_end) : sameInstant(child.planned_end, b.planned_end)));
  return result.history.at(-1)?.reason === b.reason;
}
function nativeLabel(v: unknown, row: LifecycleCase): v is Record<string, unknown> {
  const e = row.effect, w = row.observation_window;
  return Boolean(object(v) && e && w && v.intervention_id === row.intervention_id && v.store_id === row.store_id && v.treatment_type === row.kind
    && object(v.outcome_window) && sameInstant(v.outcome_window.start, w.opened_at) && sameInstant(v.outcome_window.end, w.outcome_window_end)
    && sameInstant(v.label_maturity_time, w.label_maturity_time) && v.is_mature === e.observation_mature && v.evidence_level === e.evidence_level
    && v.can_claim_effect === e.can_claim_effect && v.can_claim_causal === e.can_claim_causal && v.incremental_revenue === e.incremental_revenue
    && v.incremental_gross_margin === e.incremental_gross_margin && v.iromi === e.iromi && v.measurement_method === row.outcome?.measurement_method
    && v.exclude_from_baseline === true && instant(v.written_at));
}
export function parseLifecycleReceipt(v: unknown, attempt: LifecycleAttempt): LifecycleReceipt | null {
  if (!object(v) || v.command !== attempt.command || v.previous_version !== attempt.original.version || !integer(v.version)
    || v.correlation_id !== attempt.correlation || !id(v.lifecycle_audit_event_id)) return null;
  const native = attempt.command === "evaluate" ? v.intervention : attempt.command === "adjust" ? v.original : v;
  const result = extendedCase(native, parseIntervention(native));
  const child = attempt.command === "adjust" ? extendedCase(v.replacement, parseIntervention(v.replacement)) : null;
  if (!result || result.intervention_id !== attempt.original.intervention_id || result.store_id !== attempt.original.store_id || v.version !== result.version
    || caseFields.filter(k => !changedFields[attempt.command].includes(k)).some(k => !nativeEqual((result as unknown as Record<string, unknown>)[k], (attempt.original as unknown as Record<string, unknown>)[k]))) return null;
  const path = pathFor(attempt.command, attempt.original, result), prefix = attempt.original.history.length, tail = result.history.slice(prefix);
  if (result.version !== attempt.original.version + path.length || tail.length !== path.length || !nativeEqual(result.history.slice(0, prefix), attempt.original.history)) return null;
  let prior = attempt.original.status;
  for (let i = 0; i < path.length; i++) {
    const t = tail[i], [action, target, reason] = path[i];
    if (t.from_status !== prior || t.to_status !== target || t.action !== action || t.reason !== reason || t.actor !== attempt.scope.actorSubjectId || t.correlation_id !== attempt.correlation) return null;
    prior = target;
  }
  if (result.status !== prior || !businessMatches(result, attempt, child)) return null;
  if (attempt.command === "evaluate" && (v.intervention_id !== result.intervention_id || v.status !== result.status || v.audit_event_id !== v.lifecycle_audit_event_id || !nativeEqual(v.effect, result.effect) || !nativeLabel(v.label, result))) return null;
  if (attempt.command === "adjust" && (!child || !id(v.audit_event_id) || v.audit_event_id === v.lifecycle_audit_event_id
    || v.original_intervention_id !== result.intervention_id || v.original_status !== result.status || v.replacement_intervention_id !== child.intervention_id || v.replacement_status !== child.status
    || child.intervention_id === result.intervention_id || child.store_id !== result.store_id || child.status !== "CANDIDATE" || child.version !== 1 || child.history.length !== 0
    || child.created_by !== attempt.scope.actorSubjectId || child.trigger_ref !== "adjust:" + result.intervention_id)) return null;
  return { raw: v, target: result, child, label: attempt.command === "evaluate" ? v.label as Record<string, unknown> : null, auditId: v.lifecycle_audit_event_id, childAuditId: attempt.command === "adjust" ? v.audit_event_id as string : null };
}
const auditAction: Record<LifecycleCommand, string> = { assign: "assign", unassign: "unassign", eligibility: "check_eligibility", action: "propose_action", "conflict-check": "check_conflict", submit: "submit_for_approval", execute: "execute", outcomes: "collect_outcome", evaluate: "evaluate_effect", close: "close", adjust: "adjust", stop: "stop", rollback: "rollback" };
function expectedAuditReason(a: LifecycleAttempt, result: LifecycleCase): string {
  const fixed: Partial<Record<LifecycleCommand, string>> = { unassign: "unassigned", action: "action candidate built", submit: "submitted for approval", execute: "execution coordinated", outcomes: "outcome collected" };
  if (a.command === "assign") return "assigned to " + a.body.assignee;
  if (a.command === "eligibility") return (a.body.reasons as string[]).join(", ");
  if (a.command === "evaluate") return "evidence " + result.effect?.evidence_level;
  return fixed[a.command] ?? String(a.body.reason ?? "");
}
function auditMatches(v: unknown, a: LifecycleAttempt, result: LifecycleCase, eventId: string | null, child = false): boolean {
  if (!object(v) || v.schema_version !== 1 || v.event_type !== "intervention.lifecycle.v1" || !id(v.event_id) || eventId !== null && v.event_id !== eventId
    || v.intervention_id !== result.intervention_id || v.resource !== "intervention/" + result.intervention_id
    || v.actor !== a.scope.actorSubjectId || v.actor_subject_id !== a.scope.actorSubjectId || v.actor_evidence !== "authenticated_principal.v1"
    || v.tenant_id !== a.scope.tenantId || v.store_id !== result.store_id || v.kind !== result.kind || v.status !== result.status
    || v.correlation_id !== a.correlation || !instant(v.occurred_at) || !id(v.policy_version) || !text(v.reason)) return false;
  if (child) return v.action === "create" && v.command === "create" && nativeEqual(v.transitions, []) && v.outcome === "candidate" && v.policy_version === result.policy_version
    && v.reason === (a.command === "adjust" ? "adjusted from " + a.original.intervention_id + ": " + a.body.reason : "intervention candidate " + result.kind);
  const outcome: Record<LifecycleCommand, string> = { assign: "assigned", unassign: "unassigned", eligibility: result.status.toLowerCase(), action: "action_proposed", "conflict-check": result.conflict?.has_conflict ? "conflict_detected" : "no_conflict", submit: "pending_approval", execute: "observing", outcomes: "outcome_collected", evaluate: result.effect?.observation_mature ? "completed" : "evaluating", close: "closed", adjust: "stopped", stop: "stopped", rollback: "rolled_back" };
  return v.action === auditAction[a.command] && v.command === a.command && nativeEqual(v.transitions, result.history.slice(a.original.history.length))
    && v.outcome === outcome[a.command] && v.reason === expectedAuditReason(a, result)
    && v.policy_version === (a.command === "adjust" ? result.adjustment?.policy_version : result.policy_version);
}
export function lifecycleReadbackMatches(read: LifecycleCase, receipt: LifecycleReceipt, attempt: LifecycleAttempt): boolean {
  const saved = receipt.target;
  if (read.intervention_id !== saved.intervention_id || read.store_id !== saved.store_id || read.kind !== saved.kind || read.version < saved.version
    || !nativeEqual(read.history.slice(0, saved.history.length), saved.history) || !auditMatches(read.lifecycle_audit, attempt, saved, receipt.auditId)) return false;
  return read.version > saved.version || caseFields.every(k => nativeEqual((read as unknown as Record<string, unknown>)[k], (saved as unknown as Record<string, unknown>)[k]));
}
export function lifecycleLabelMatches(v: unknown, receipt: LifecycleReceipt): boolean {
  return Boolean(receipt.label && nativeLabel(v, receipt.target) && nativeEqual(v, receipt.label));
}
export function lifecycleChildMatches(child: LifecycleCase, receipt: LifecycleReceipt, attempt: LifecycleAttempt): boolean {
  const saved = receipt.target, expectedId = attempt.command === "adjust" ? saved.replacement_id : saved.close?.follow_up_intervention_id;
  if (!expectedId || child.intervention_id !== expectedId || child.store_id !== saved.store_id || child.created_by !== attempt.scope.actorSubjectId
    || child.status !== "CANDIDATE" || child.version !== 1 || child.history.length || !auditMatches(child.lifecycle_audit, attempt, child, receipt.childAuditId, true)) return false;
  if (attempt.command === "adjust") return Boolean(receipt.child && caseFields.every(k => nativeEqual((child as unknown as Record<string, unknown>)[k], (receipt.child as unknown as Record<string, unknown>)[k])));
  return child.kind === (attempt.body.follow_up_kind ?? saved.kind) && child.trigger_ref === "follow-up:" + saved.intervention_id
    && sameInstant(child.planned_start, saved.observation_window?.label_maturity_time ?? saved.planned_end) && child.predecessor_id === null
    && equalSerializedDurations(saved.planned_start, saved.planned_end, child.planned_start, child.planned_end);
}
export function attemptScopeMatches(attempt: LifecycleAttempt, current: ActorScope): boolean { return sameScope(attempt.scope, current); }
