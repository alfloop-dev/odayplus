export const INTERVENTION_STATUSES = ["CANDIDATE", "ELIGIBILITY_CHECKING", "ELIGIBLE", "INELIGIBLE", "ACTION_PROPOSED", "CONFLICT_CHECKING", "PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTING", "OBSERVING", "EVALUATING", "COMPLETED", "CLOSED", "STOPPED", "ROLLED_BACK"] as const;
export const INTERVENTION_KINDS = ["PRICE_CHANGE", "AD_CAMPAIGN", "PROMOTION", "CRM_RECALL", "MAINTENANCE", "CLEANING", "OPENING_CAMPAIGN", "EXTERNAL_SHOCK"] as const;
export type DecisionAction = "APPROVE" | "REJECT";
export type ActorScope = { actorSubjectId: string; tenantId: string; expiresAt: number };
export type ReadContext = { schema_version: 1; intervention_id: string; store_id: string; tenant_id: string; actor_subject_id: string; version: number; policy_version: string; snapshot_sha256: string };
export type Transition = { from_status: string; to_status: string; actor: string; action: string; reason: string; at: string; correlation_id: string };
export type Approval = { approved: boolean; actor_id: string; decision_reason: string; approved_at: string; policy_version: string };
export type ApprovalAudit = { event_id: string; actor: string; action: string; reason: string; policy_version: string; correlation_id: string; occurred_at: string };
export type Intervention = {
  intervention_id: string; store_id: string; kind: string; status: string; trigger_ref: string; expected_outcome: string;
  created_by: string; created_at: string; planned_start: string; planned_end: string; policy_version: string; version: number;
  action_spec: Record<string, unknown>; history: Transition[]; approval: Approval | null;
  eligibility: { eligible: boolean; checked_at: string; reasons: string[] } | null;
  conflict: { has_conflict: boolean; resolved: boolean; blocks_approval: boolean; conflicting_ids: string[]; conflicting_kinds: string[]; checked_at: string; resolution_reason: string } | null;
  window_spec: { outcome_window_days: number; maturity_buffer_days: number };
  observation_window: { opened_at: string; outcome_window_days: number; maturity_buffer_days: number; outcome_window_end: string; label_maturity_time: string } | null;
  execution: { execution_id: string; executor: string; executed_at: string; status: string; correlation_id: string } | null;
  effect: { evaluated_at: string; evidence_level: string; can_claim_effect: boolean; can_claim_causal: boolean; observation_mature: boolean; limitations: string[]; incremental_revenue: number; incremental_gross_margin: number; iromi: number | null; evaluation_method: string; pretrend_status: string; recommendation: string } | null;
  outcome: Record<string, unknown> | null;
  review_context?: ReadContext; creator_verification?: string; feature_version?: string; read_at?: string;
  approval_permissions?: { approve: boolean; reject: boolean; approve_refusal: string | null; reject_refusal: string | null };
  approval_audit?: ApprovalAudit | null;
};
export type DecisionBody = { action: DecisionAction; reason: string; expected_version: number; expectedActorSubjectId: string; expectedTenantId: string; review_context: ReadContext };
export type Attempt = { body: DecisionBody; serialized: string; key: string; correlation: string; scope: ActorScope };
export const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const id = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;
const text = (v: unknown): v is string => typeof v === "string";
type NativeInstant = { seconds: bigint; fraction: string };
// Preserve every decimal digit, including fractional timezone offsets. Native
// datetime.isoformat emits microseconds; no millisecond truncation is applied.
function nativeInstant(value: unknown): NativeInstant | null {
  if (typeof value !== "string") return null;
  const p = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|([+-])(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)$/.exec(value);
  if (!p) return null;
  const [year, month, day, hour, minute, second] = p.slice(1, 7).map(Number);
  const oh = Number(p[10] ?? 0), om = Number(p[11] ?? 0), os = Number(p[12] ?? 0);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59
    || oh > 23 || om > 59 || os > 59) return null;
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day); calendar.setUTCHours(hour, minute, second, 0);
  if (!Number.isFinite(calendar.getTime()) || calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1
    || calendar.getUTCDate() !== day) return null;
  try {
    const fraction = p[7] ?? "", offsetFraction = p[13] ?? "", precision = Math.max(fraction.length, offsetFraction.length);
    const scale = 10n ** BigInt(precision);
    const local = BigInt(calendar.getTime() / 1000) * scale + BigInt(fraction.padEnd(precision, "0") || "0");
    const offset = BigInt(oh * 3600 + om * 60 + os) * scale + BigInt(offsetFraction.padEnd(precision, "0") || "0");
    const total = local - (p[9] === "-" ? -offset : offset);
    let seconds = total / scale, remainder = total % scale;
    if (remainder < 0n) { seconds -= 1n; remainder += scale; }
    return { seconds, fraction: precision ? remainder.toString().padStart(precision, "0").replace(/0+$/, "") : "" };
  } catch { return null; }
}
function compareInstants(a: NativeInstant, b: NativeInstant): -1 | 0 | 1 {
  if (a.seconds !== b.seconds) return a.seconds < b.seconds ? -1 : 1;
  const precision = Math.max(a.fraction.length, b.fraction.length), af = a.fraction.padEnd(precision, "0"), bf = b.fraction.padEnd(precision, "0");
  return af === bf ? 0 : af < bf ? -1 : 1;
}
export function compareNativeInstants(a: unknown, b: unknown): -1 | 0 | 1 | null {
  const first = nativeInstant(a), second = nativeInstant(b);
  return first && second ? compareInstants(first, second) : null;
}
function clockLowerBound(milliseconds: number): NativeInstant | null {
  if (!Number.isSafeInteger(milliseconds)) return null;
  const ms = BigInt(milliseconds); let seconds = ms / 1000n, remainder = ms % 1000n;
  if (remainder < 0n) { seconds -= 1n; remainder += 1000n; }
  return { seconds, fraction: remainder.toString().padStart(3, "0").replace(/0+$/, "") };
}
const instant = (v: unknown): v is string => nativeInstant(v) !== null;
type WindowConsistency = "verified_endpoints" | "timezone_unverified";
function observationWindowConsistency(w: unknown): WindowConsistency | null {
  if (!object(w) || !instant(w.opened_at) || !integer(w.outcome_window_days) || !integer(w.maturity_buffer_days)
    || !instant(w.outcome_window_end) || !instant(w.label_maturity_time)) return null;
  const values = [w.opened_at, w.outcome_window_end, w.label_maturity_time];
  const suffix = /(Z|[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;
  // Python aware datetime + timedelta(days) preserves its timezone's local
  // calendar/time. Do not turn a ZoneInfo day across DST into UTC 86400 seconds.
  const walls = values.map(value => nativeInstant(value.replace(suffix, "Z"))!);
  const expectedEnd = { seconds: walls[0].seconds + BigInt(w.outcome_window_days) * 86400n, fraction: walls[0].fraction };
  const expectedMaturity = { seconds: walls[1].seconds + BigInt(w.maturity_buffer_days) * 86400n, fraction: walls[1].fraction };
  if (compareInstants(expectedEnd, walls[1]) !== 0 || compareInstants(expectedMaturity, walls[2]) !== 0) return null;
  const offsets = values.map(value => nativeInstant("2000-01-01T00:00:00" + value.match(suffix)![0])!);
  // Wire ISO timestamps omit ZoneInfo identity/fold. Changed offsets can be a
  // legitimate native DST record; preserve its wall-calendar-consistent data,
  // while withholding current maturity/claims that need timezone authority.
  if (compareInstants(offsets[0], offsets[1]) !== 0 || compareInstants(offsets[1], offsets[2]) !== 0) return "timezone_unverified";
  const instants = values.map(value => nativeInstant(value)!);
  return compareInstants(instants[0], instants[1]) === -1 && compareInstants(instants[1], instants[2]) === -1 ? "verified_endpoints" : null;
}
function observationWindowValid(w: unknown): boolean {
  return observationWindowConsistency(w) !== null;
}
function observationWindowRecordConsistency(row: Intervention): WindowConsistency | null {
  const w = row.observation_window;
  if (!w || !row.execution || !row.window_spec
    || w.outcome_window_days !== row.window_spec.outcome_window_days || w.maturity_buffer_days !== row.window_spec.maturity_buffer_days
    || compareNativeInstants(w.opened_at, row.execution.executed_at) !== 0) return null;
  return observationWindowConsistency(w);
}
export function observationWindowMatureNow(row: Intervention, nowMs = Date.now()): boolean {
  const clock = clockLowerBound(nowMs), w = row.observation_window;
  const maturity = w ? nativeInstant(w.label_maturity_time) : null;
  return Boolean(row.execution && observationWindowRecordConsistency(row) === "verified_endpoints" && clock && maturity && compareInstants(maturity, clock) <= 0);
}
function currentEvidenceClock(row: Intervention, nowMs: number): NativeInstant | null {
  const clock = clockLowerBound(nowMs);
  if (!clock) return null;
  if (row.read_at === undefined) return clock;
  const observed = nativeInstant(row.read_at);
  // A recorded future as-of evaluation does not become current proof merely
  // because the browser clock advances beyond a stale server read observation.
  return observed ? compareInstants(observed, clock) < 0 ? observed : clock : null;
}
type RecordedEvaluationConsistency = "verified_absolute" | "timezone_unverified";
function recordedEvaluationConsistency(row: Intervention): RecordedEvaluationConsistency | null {
  const e = row.effect, w = row.observation_window;
  if (!e || !w || !observationWindowRecordConsistency(row)) return null;
  const evaluated = nativeInstant(e.evaluated_at), maturity = nativeInstant(w.label_maturity_time);
  if (!evaluated || !maturity) return null;
  const suffix = /(Z|[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;
  const evaluatedWall = nativeInstant(e.evaluated_at.replace(suffix, "Z"))!, maturityWall = nativeInstant(w.label_maturity_time.replace(suffix, "Z"))!;
  // Native aware datetimes with distinct timezone objects compare as UTC;
  // an identical ZoneInfo object compares local wall time, ignoring fold.
  // ISO wire omits that identity. Preserve a recorded flag supported by either
  // actual native interpretation; conflicting interpretations cannot establish
  // current effect authority. No flag is accepted when both reject it.
  const absoluteMature = compareInstants(evaluated, maturity) >= 0, wallMature = compareInstants(evaluatedWall, maturityWall) >= 0;
  if (e.observation_mature !== absoluteMature && e.observation_mature !== wallMature) return null;
  return absoluteMature === wallMature ? "verified_absolute" : "timezone_unverified";
}
export function evaluationTimeUnverifiedNow(row: Intervention, nowMs = Date.now()): boolean {
  const evaluated = row.effect ? nativeInstant(row.effect.evaluated_at) : null, clock = currentEvidenceClock(row, nowMs);
  return Boolean(row.effect && (observationWindowRecordConsistency(row) !== "verified_endpoints" || recordedEvaluationConsistency(row) !== "verified_absolute" || !evaluated || !clock || compareInstants(evaluated, clock) > 0));
}
const integer = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v > 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text);
const optional = (v: unknown, check: (v: unknown) => boolean) => v === null || check(v);
const status = (v: unknown) => text(v) && (INTERVENTION_STATUSES as readonly string[]).includes(v);
export function parseActorScope(v: unknown): ActorScope | null {
  return object(v) && id(v.actorSubjectId) && id(v.tenantId) && integer(v.expiresAt) && v.expiresAt > Date.now() / 1000 ? v as ActorScope : null;
}
export function sameScope(a: ActorScope | null, b: ActorScope | null): boolean {
  return Boolean(a && b && a.actorSubjectId === b.actorSubjectId && a.tenantId === b.tenantId && a.expiresAt === b.expiresAt);
}
function approval(v: unknown): v is Approval {
  return object(v) && typeof v.approved === "boolean" && id(v.actor_id) && id(v.decision_reason) && instant(v.approved_at) && id(v.policy_version);
}
function context(v: unknown): v is ReadContext {
  return object(v) && v.schema_version === 1 && id(v.intervention_id) && id(v.store_id) && id(v.tenant_id) && id(v.actor_subject_id)
    && integer(v.version) && id(v.policy_version) && text(v.snapshot_sha256) && /^[a-f0-9]{64}$/.test(v.snapshot_sha256);
}
function audit(v: unknown): v is ApprovalAudit {
  return object(v) && id(v.event_id) && id(v.actor) && ["approve", "reject"].includes(String(v.action)) && id(v.reason)
    && id(v.policy_version) && id(v.correlation_id) && instant(v.occurred_at);
}
// The native resolver's observable inputs must agree with the displayed grade.
// Its DTO does not persist replicated. An L5 consistent with either L3/L4 base
// is retained; this check does not attest to independent replication evidence.
function hasObservableEvidenceConsistency(row: Intervention): boolean {
  const e = row.effect, o = row.outcome;
  if (!e || !o || typeof o.has_control_group !== "boolean" || typeof o.randomized !== "boolean") return false;
  const rank = Number(e.evidence_level.charAt(1));
  const baseRank = !e.observation_mature ? 0 : !o.has_control_group ? 1 : e.pretrend_status !== "PASS" ? 2 : o.randomized ? 4 : 3;
  return rank === baseRank || rank === 5 && (baseRank === 3 || baseRank === 4);
}
export function parseIntervention(v: unknown): Intervention | null {
  if (!object(v) || !id(v.intervention_id) || !id(v.store_id) || !status(v.status)
    || !(INTERVENTION_KINDS as readonly unknown[]).includes(v.kind) || !id(v.expected_outcome) || !text(v.trigger_ref)
    || !id(v.created_by) || !instant(v.created_at) || !instant(v.planned_start) || !instant(v.planned_end)
    || !id(v.policy_version) || !integer(v.version) || !object(v.action_spec) || !object(v.window_spec)
    || !integer(v.window_spec.outcome_window_days) || !integer(v.window_spec.maturity_buffer_days)
    || !Array.isArray(v.history) || !v.history.every(t => object(t) && status(t.from_status) && status(t.to_status)
      && id(t.actor) && id(t.action) && text(t.reason) && instant(t.at) && text(t.correlation_id))
    || !optional(v.approval, approval)
    || !optional(v.eligibility, e => object(e) && typeof e.eligible === "boolean" && instant(e.checked_at) && strings(e.reasons))
    || !optional(v.conflict, c => object(c) && typeof c.has_conflict === "boolean" && typeof c.resolved === "boolean"
      && c.blocks_approval === (c.has_conflict && !c.resolved) && Array.isArray(c.conflicting_ids) && c.conflicting_ids.every(id)
      && strings(c.conflicting_kinds) && instant(c.checked_at) && text(c.resolution_reason))
    || !optional(v.execution, e => object(e) && id(e.execution_id) && id(e.executor) && instant(e.executed_at) && id(e.status) && text(e.correlation_id))
    || !optional(v.observation_window, observationWindowValid)
    || !optional(v.outcome, o => object(o) && instant(o.collected_at) && finite(o.incremental_revenue) && finite(o.incremental_gross_margin)
      && typeof o.has_control_group === "boolean" && ["PASS", "FAIL", "INCONCLUSIVE"].includes(String(o.pretrend_status))
      && typeof o.treatment_store_count === "number" && Number.isSafeInteger(o.treatment_store_count) && o.treatment_store_count >= 0
      && typeof o.control_store_count === "number" && Number.isSafeInteger(o.control_store_count) && o.control_store_count >= 0
      && id(o.evaluation_method) && typeof o.randomized === "boolean" && finite(o.ad_spend) && (o.iromi === null || finite(o.iromi)))
    || !optional(v.effect, e => object(e) && instant(e.evaluated_at) && id(e.evidence_level) && /^L[0-5]$/.test(e.evidence_level)
      && typeof e.can_claim_effect === "boolean" && typeof e.can_claim_causal === "boolean" && typeof e.observation_mature === "boolean"
      && strings(e.limitations) && finite(e.incremental_revenue) && finite(e.incremental_gross_margin)
      && (e.iromi === null || finite(e.iromi)) && id(e.evaluation_method) && ["PASS", "FAIL", "INCONCLUSIVE"].includes(String(e.pretrend_status))
      && ["CONTINUE", "SCALE", "STOP", "CHANGE_CHANNEL", "ADJUST", "INCONCLUSIVE"].includes(String(e.recommendation)))) return null;
  const result = v as Intervention;
  if (result.observation_window && !observationWindowRecordConsistency(result)) return null;
  if (result.outcome && result.outcome.iromi !== (Number(result.outcome.ad_spend) > 0 ? Number(result.outcome.incremental_gross_margin) / Number(result.outcome.ad_spend) : null)) return null;
  if (result.effect) {
    const e = result.effect, w = result.observation_window, o = result.outcome;
    const rank = Number(e.evidence_level.charAt(1));
    if (!w || !result.execution || !o || !hasObservableEvidenceConsistency(result) || recordedEvaluationConsistency(result) === null
      || !e.observation_mature && rank !== 0 || e.can_claim_effect !== (rank >= 1) || e.can_claim_causal !== (rank >= 3)
      || e.pretrend_status !== o.pretrend_status || e.evaluation_method !== o.evaluation_method
      || rank >= 3 && (o.has_control_group !== true || e.pretrend_status !== "PASS")
      || e.incremental_revenue !== (e.can_claim_effect ? o.incremental_revenue : 0)
      || e.incremental_gross_margin !== (e.can_claim_effect ? o.incremental_gross_margin : 0)
      || e.iromi !== (e.can_claim_effect ? o.iromi : null)) return null;
  }
  return result;
}
export function parseOwnedRead(v: unknown, scope: ActorScope, expectedId: string): Intervention | null {
  const row = parseIntervention(v);
  if (!row || !object(v) || row.intervention_id !== expectedId || !context(v.review_context) || !object(v.approval_permissions)
    || typeof v.approval_permissions.approve !== "boolean" || typeof v.approval_permissions.reject !== "boolean"
    || !optional(v.approval_permissions.approve_refusal, id) || !optional(v.approval_permissions.reject_refusal, id)
    || v.approval_permissions.approve !== (v.approval_permissions.approve_refusal === null)
    || v.approval_permissions.reject !== (v.approval_permissions.reject_refusal === null)
    || !["authenticated_principal", "unverified_legacy_record"].includes(String(v.creator_verification))
    || !id(v.feature_version) || !instant(v.read_at) || !optional(v.approval_audit, audit)) return null;
  const c = v.review_context;
  if (c.intervention_id !== row.intervention_id || c.store_id !== row.store_id || c.version !== row.version || c.policy_version !== row.policy_version
    || c.actor_subject_id !== scope.actorSubjectId || c.tenant_id !== scope.tenantId) return null;
  // A permissive malformed projection never creates a browser command grant.
  const p = v.approval_permissions;
  if ((p.approve || p.reject) && row.status !== "PENDING_APPROVAL") return null;
  if (p.approve && (v.creator_verification !== "authenticated_principal" || row.created_by === scope.actorSubjectId || row.conflict?.blocks_approval)) return null;
  return row;
}
export function makeAttempt(row: Intervention, scope: ActorScope, action: DecisionAction, reason: string): Attempt {
  if (!row.review_context || !reason.trim() || !row.approval_permissions?.[action === "APPROVE" ? "approve" : "reject"]) throw new Error("APPROVAL_CONTEXT_REQUIRED");
  const body: DecisionBody = { action, reason: reason.trim(), expected_version: row.version,
    expectedActorSubjectId: scope.actorSubjectId, expectedTenantId: scope.tenantId, review_context: { ...row.review_context } };
  return { body, serialized: JSON.stringify(body), key: crypto.randomUUID(), correlation: crypto.randomUUID(), scope: { ...scope } };
}
export function receiptMatches(v: unknown, row: Intervention, attempt: Attempt): boolean {
  const result = parseIntervention(v);
  if (!result || !object(v) || !result.approval || !id(v.audit_event_id) || v.correlation_id !== attempt.correlation) return false;
  const a = result.approval, b = attempt.body;
  const transition = result.history.filter(t => t.action === b.action.toLowerCase() && t.actor === b.expectedActorSubjectId && t.reason === b.reason && t.correlation_id === attempt.correlation);
  return result.intervention_id === row.intervention_id && result.store_id === row.store_id && result.version === b.expected_version + 1
    && result.status === (b.action === "APPROVE" ? "APPROVED" : "REJECTED") && a.approved === (b.action === "APPROVE")
    && a.actor_id === b.expectedActorSubjectId && a.decision_reason === b.reason && a.policy_version === row.policy_version
    && transition.length === 1 && transition[0].from_status === "PENDING_APPROVAL" && transition[0].to_status === result.status;
}
export function readbackMatches(read: Intervention, receipt: unknown, attempt: Attempt): boolean {
  if (!object(receipt) || !read.approval || !read.approval_audit || !object(receipt.approval)) return false;
  const a = read.approval, proof = read.approval_audit, b = attempt.body;
  return read.version === b.expected_version + 1 && read.status === (b.action === "APPROVE" ? "APPROVED" : "REJECTED")
    && a.approved === (b.action === "APPROVE") && a.actor_id === b.expectedActorSubjectId && a.decision_reason === b.reason
    && a.approved_at === receipt.approval.approved_at && a.policy_version === b.review_context.policy_version
    && proof.event_id === receipt.audit_event_id && proof.actor === b.expectedActorSubjectId && proof.action === b.action.toLowerCase()
    && proof.reason === b.reason && proof.policy_version === a.policy_version && proof.correlation_id === attempt.correlation;
}
export function mayClaimEffect(row: Intervention, nowMs = Date.now()): boolean {
  // Raw typed callers get the same shape, grade and precise recorded-time
  // validation as the read parser; caller-side parsing is not a prerequisite.
  const valid = parseIntervention(row), clock = valid ? currentEvidenceClock(valid, nowMs) : null;
  const e = valid?.effect, w = valid?.observation_window;
  const evaluated = e ? nativeInstant(e.evaluated_at) : null, maturity = w ? nativeInstant(w.label_maturity_time) : null;
  return Boolean(e && w && observationWindowRecordConsistency(valid!) === "verified_endpoints" && recordedEvaluationConsistency(valid!) === "verified_absolute" && valid?.execution && clock && evaluated && maturity
    && compareInstants(evaluated, clock) <= 0 && compareInstants(maturity, clock) <= 0
    && e.observation_mature && Number(e.evidence_level.charAt(1)) >= 1 && e.can_claim_effect);
}
export function mayClaimCausal(row: Intervention, nowMs = Date.now()): boolean {
  return mayClaimEffect(row, nowMs) && Boolean(row.effect?.can_claim_causal && Number(row.effect.evidence_level.charAt(1)) >= 3
    && row.outcome?.has_control_group === true && row.effect.pretrend_status === "PASS");
}
export function currentEvidenceGrade(row: Intervention, nowMs = Date.now()): string | null {
  return mayClaimEffect(row, nowMs) ? row.effect!.evidence_level : null;
}
