import { afterEach, describe, expect, it, vi } from "vitest";
import { compareNativeInstants, type ActorScope } from "../interventionContract";
import { commandEligible, hasVerifiedDefaultChildDuration, LIFECYCLE_COMMANDS, lifecycleChildMatches, lifecycleLabelMatches, lifecycleReadbackMatches, makeLifecycleAttempt, nativeEqual, parseCommandContext, parseLifecycleRead, parseLifecycleReceipt, type LifecycleAttempt, type LifecycleCommand } from "../lifecycleCommandContract";
import fixture from "./fixtures/nativeInterventionLifecycle.json";

const copy = <T,>(value: T): T => structuredClone(value);
const samples = fixture.samples;
function attemptFor(sample: typeof samples[number]): LifecycleAttempt {
  const scope = sample.scope as ActorScope, row = parseLifecycleRead(sample.before, scope, sample.before.intervention_id)!;
  const context = parseCommandContext(sample.context, row, scope, sample.command as LifecycleCommand)!;
  vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(sample.key as ReturnType<Crypto["randomUUID"]>).mockReturnValueOnce(sample.correlation as ReturnType<Crypto["randomUUID"]>);
  return makeLifecycleAttempt(row, scope, context, sample.business);
}
afterEach(() => vi.restoreAllMocks());
describe("actual native lifecycle complete attempts", () => {
  it("covers all thirteen commands and real maturity, follow-up, override and refusal branches", () => {
    expect(new Set(samples.map(s => s.command))).toEqual(new Set(LIFECYCLE_COMMANDS));
    expect(fixture.provenance.originalBeforeGETAndCommandContextCaptured).toBe(true);
    expect(fixture.provenance.originalSerializedBodyHeadersKeyCIDCaptured).toBe(true);
    expect(samples.some(s => s.name === "evaluate_immature")).toBe(true);
    expect(samples.some(s => s.name === "close_follow_up")).toBe(true);
    expect(samples.some(s => s.name === "eligibility_ineligible")).toBe(true);
    expect(samples.some(s => s.name === "conflict_overlap")).toBe(true);
  });
  it.each(samples)("$name uses the genuine native request, receipt and scoped audit", sample => {
    const attempt = attemptFor(sample);
    expect(nativeEqual(attempt.body, sample.request)).toBe(true);
    expect(JSON.parse(attempt.serialized)).toEqual(sample.request);
    expect(attempt.headers).toMatchObject({ "If-Match": sample.beforeETag, "If-Actor-Match": sample.scope.actorSubjectId, "Idempotency-Key": sample.key, "X-Correlation-Id": sample.correlation });
    const receipt = parseLifecycleReceipt(sample.receipt, attempt); expect(receipt).not.toBeNull();
    const after = parseLifecycleRead(sample.after, sample.scope, sample.before.intervention_id)!;
    expect(lifecycleReadbackMatches(after, receipt!, attempt)).toBe(true);
    if (sample.label) expect(lifecycleLabelMatches(sample.label, receipt!)).toBe(true);
    if (sample.child) expect(lifecycleChildMatches(parseLifecycleRead(sample.child, sample.scope, sample.child.intervention_id)!, receipt!, attempt)).toBe(true);
    expect(sample.request).not.toHaveProperty("executed_at"); expect(sample.request).not.toHaveProperty("now");
  });
  it.each(["execute", "evaluate", "evaluate_immature"])("%s validates its actual one/two transition path", name => {
    const sample = samples.find(s => s.name === name)!, attempt = attemptFor(sample), bad = copy(sample.receipt) as unknown as Record<string, unknown>;
    bad.version = Number(bad.version) + 1; expect(parseLifecycleReceipt(bad, attempt)).toBeNull();
  });
  it.each(["command", "previous_version", "version", "correlation_id", "lifecycle_audit_event_id"])("refuses a corrupted shared receipt %s", field => {
    const sample = samples.find(s => s.command === "assign")!, attempt = attemptFor(sample), bad = copy(sample.receipt) as unknown as Record<string, unknown>;
    bad[field] = field.includes("version") ? true : field === "lifecycle_audit_event_id" ? "" : "wrong"; expect(parseLifecycleReceipt(bad, attempt)).toBeNull();
  });
  it.each(["actor", "correlation_id", "from_status", "to_status", "action", "reason"])("refuses a corrupted native history %s", field => {
    const sample = samples.find(s => s.command === "execute")!, attempt = attemptFor(sample), bad = copy(sample.receipt) as unknown as { history: Record<string, unknown>[] };
    bad.history[bad.history.length - 1][field] = "wrong"; expect(parseLifecycleReceipt(bad, attempt)).toBeNull();
  });
  it.each(["intervention_id", "resource", "tenant_id", "actor", "actor_subject_id", "actor_evidence", "event_id", "command", "action", "reason", "status", "policy_version", "correlation_id"])("requires exact owned audit %s", field => {
    const sample = samples.find(s => s.command === "assign")!, attempt = attemptFor(sample), receipt = parseLifecycleReceipt(sample.receipt, attempt)!;
    const bad = copy(sample.after) as unknown as { lifecycle_audit: Record<string, unknown> };
    bad.lifecycle_audit[field] = "foreign";
    const parsed = parseLifecycleRead(bad, sample.scope, sample.before.intervention_id)!;
    expect(lifecycleReadbackMatches(parsed, receipt, attempt)).toBe(false);
  });
  it.each(["actor_subject_id", "tenant_id", "store_id", "intervention_id", "command", "snapshot_sha256", "policy_version"])("refuses a malformed original command context %s", field => {
    const sample = samples[0], row = parseLifecycleRead(sample.before, sample.scope, sample.before.intervention_id)!;
    const bad = copy(sample.context) as unknown as Record<string, unknown>; bad[field] = "foreign";
    expect(parseCommandContext(bad, row, sample.scope, sample.command as LifecycleCommand)).toBeNull();
  });
  it.each([true, 1.5, "1", null, 0, -1])("refuses command context version %s", version => {
    const sample = samples[0], row = parseLifecycleRead(sample.before, sample.scope, sample.before.intervention_id)!;
    expect(parseCommandContext({ ...sample.context, version }, row, sample.scope, sample.command as LifecycleCommand)).toBeNull();
  });
  it("preserves original action and unknown keys without manufacturing a treatment schema", () => {
    const sample = copy(samples.find(s => s.command === "action")!), attempt = attemptFor(sample);
    const spec = attempt.body.action_spec as Record<string, unknown>;
    expect(spec.saved_extra).toEqual(sample.before.action_spec.saved_extra);
    expect(spec.campaign).toEqual(sample.before.action_spec.campaign);
    (sample.business.action_spec as Record<string, unknown>).saved_extra = ["changed draft"];
    expect(JSON.parse(attempt.serialized).action_spec.saved_extra).not.toEqual(["changed draft"]);
  });
  it("refuses technical transport edits through the business form and nonfinite parameters", () => {
    const sample = samples.find(s => s.command === "action")!, row = parseLifecycleRead(sample.before, sample.scope, sample.before.intervention_id)!, context = parseCommandContext(sample.context, row, sample.scope, "action")!;
    expect(() => makeLifecycleAttempt(row, sample.scope, context, { action_spec: {}, expected_version: 99 })).toThrow();
    expect(() => makeLifecycleAttempt(row, sample.scope, context, { action_spec: { bad: NaN } })).toThrow();
  });
  it("permits genuine later target history without reminting the original attempt", () => {
    const sample = samples.find(s => s.command === "assign")!, attempt = attemptFor(sample), receipt = parseLifecycleReceipt(sample.receipt, attempt)!;
    const row = parseLifecycleRead(sample.advancedAfter, sample.scope, sample.before.intervention_id)!;
    expect(parseLifecycleReceipt(sample.replayAfterAdvanceReceipt, attempt)).not.toBeNull();
    expect(lifecycleReadbackMatches(row, receipt, attempt)).toBe(true);
    expect(attempt.context.version).toBe(sample.before.version);
  });
  it("withholds a superseded label and wrong linked native creation", () => {
    const evaluation = samples.find(s => s.command === "evaluate")!, ea = attemptFor(evaluation), er = parseLifecycleReceipt(evaluation.receipt, ea)!;
    expect(lifecycleLabelMatches({ ...evaluation.label, written_at: "2025-01-01T00:00:00Z" }, er)).toBe(false);
    const adjustment = samples.find(s => s.command === "adjust")!, aa = attemptFor(adjustment), ar = parseLifecycleReceipt(adjustment.receipt, aa)!;
    const child = parseLifecycleRead(adjustment.child, adjustment.scope, adjustment.child!.intervention_id)!;
    expect(lifecycleChildMatches({ ...child, predecessor_id: "foreign" }, ar, aa)).toBe(false);
  });
  it("requires authoritative readback for a changed opaque audit ID", () => {
    const sample = samples.find(s => s.command === "assign")!, attempt = attemptFor(sample);
    const receipt = parseLifecycleReceipt({ ...sample.receipt, lifecycle_audit_event_id: "another-opaque-event" }, attempt)!;
    expect(receipt).not.toBeNull();
    expect(lifecycleReadbackMatches(parseLifecycleRead(sample.after, sample.scope, sample.before.intervention_id)!, receipt, attempt)).toBe(false);
  });
  it("retains full native timestamp precision", () => {
    expect(compareNativeInstants("2026-11-11T08:39:37.000800Z", "2026-11-11T08:39:37.000900Z")).toBe(-1);
  });
  it.each(["INELIGIBLE", "REJECTED", "CLOSED", "STOPPED", "ROLLED_BACK"])("has no assign/unassign actions on terminal %s", status => {
    const sample = samples[0], row = parseLifecycleRead(sample.before, sample.scope, sample.before.intervention_id)!;
    expect(commandEligible({ ...row, status }, "assign")).toBe(false); expect(commandEligible({ ...row, status }, "unassign")).toBe(false);
  });
});

// These bounded arithmetic projections use unchanged workflow AST expressions,
// not new API/saved records. Oracle: actual-native-default-child-duration-source-oracle-v1.json
// SHA256 d0f0bdf9766483ae2539f6cc52d56e29be87a3b33b99bf34904de0d9e3e68a99.
const nativeDurationCases = [
  {
    "name": "utc",
    "parentStart": "2026-03-01T00:00:00.000800+00:00",
    "parentEnd": "2026-03-02T00:00:00.000900+00:00",
    "childStart": "2026-04-01T00:00:00.000700+00:00",
    "childEnd": "2026-04-02T00:00:00.000800+00:00",
    "expectedConservativeWireGuard": true
  },
  {
    "name": "fixed_offset",
    "parentStart": "2026-03-01T00:00:00+08:00",
    "parentEnd": "2026-03-02T00:00:00+08:00",
    "childStart": "2026-04-01T00:00:00+08:00",
    "childEnd": "2026-04-02T00:00:00+08:00",
    "expectedConservativeWireGuard": true
  },
  {
    "name": "same_zone_original_spring",
    "parentStart": "2026-03-07T12:00:00-05:00",
    "parentEnd": "2026-03-09T12:00:00-04:00",
    "childStart": "2026-04-01T00:00:00+00:00",
    "childEnd": "2026-04-03T00:00:00+00:00",
    "expectedConservativeWireGuard": false
  },
  {
    "name": "distinct_no_cache_original_spring",
    "parentStart": "2026-03-07T12:00:00-05:00",
    "parentEnd": "2026-03-09T12:00:00-04:00",
    "childStart": "2026-04-01T00:00:00+00:00",
    "childEnd": "2026-04-02T23:00:00+00:00",
    "expectedConservativeWireGuard": false
  },
  {
    "name": "known_original_duration_child_spring",
    "parentStart": "2026-03-01T00:00:00+00:00",
    "parentEnd": "2026-03-02T00:00:00+00:00",
    "childStart": "2026-03-07T12:00:00-05:00",
    "childEnd": "2026-03-08T12:00:00-04:00",
    "expectedConservativeWireGuard": true
  },
  {
    "name": "known_original_duration_child_autumn",
    "parentStart": "2026-03-01T00:00:00+00:00",
    "parentEnd": "2026-03-02T00:00:00+00:00",
    "childStart": "2026-10-31T12:00:00-04:00",
    "childEnd": "2026-11-01T12:00:00-05:00",
    "expectedConservativeWireGuard": true
  }
] as const;
describe("native source default child duration authority", () => {
  it.each(nativeDurationCases)("$name preserves sourced arithmetic and withholds missing timezone identity", source => {
    const sample = copy(samples.find(s => s.name === "close_follow_up")!), attempt = attemptFor(sample), receipt = parseLifecycleReceipt(sample.receipt, attempt)!;
    receipt.target.planned_start = source.parentStart; receipt.target.planned_end = source.parentEnd;
    receipt.target.observation_window = { ...receipt.target.observation_window!, label_maturity_time: source.childStart };
    const child = parseLifecycleRead(sample.child, sample.scope, sample.child!.intervention_id)!;
    child.planned_start = source.childStart; child.planned_end = source.childEnd;
    expect(hasVerifiedDefaultChildDuration(receipt.target)).toBe(source.expectedConservativeWireGuard);
    expect(lifecycleChildMatches(child, receipt, attempt)).toBe(source.expectedConservativeWireGuard);
    if (source.expectedConservativeWireGuard) {
      expect(lifecycleChildMatches({ ...child, planned_end: "2026-06-01T00:00:00Z" }, receipt, attempt)).toBe(false);
    }
  });
  it.each(["adjust", "close"] as const)("%s blocks an unverified default duration before building a mutating request", command => {
    const sample = copy(samples.find(s => s.command === command)!), scope = sample.scope, row = parseLifecycleRead(sample.before, scope, sample.before.intervention_id)!;
    row.planned_start = nativeDurationCases[2].parentStart; row.planned_end = nativeDurationCases[2].parentEnd;
    const context = parseCommandContext(sample.context, row, scope, command)!;
    const business = { ...sample.business, ...(command === "close" ? { follow_up: true } : {}) };
    expect(() => makeLifecycleAttempt(row, scope, context, business)).toThrow(command === "adjust" ? "請填寫替代案計畫結束時間" : "不足以核對追蹤案");
    const explicit = command === "adjust" ? { ...business, planned_end: "2026-06-01T12:00:00.000900Z" } : { ...business, follow_up: false };
    expect(makeLifecycleAttempt(row, scope, context, explicit).body).toMatchObject(explicit);
  });
});
