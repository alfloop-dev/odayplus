import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NetPlanNativeApprovalSection } from "../NetPlanNativeApprovalSection";
import { NetPlanOutcomeTrackingSection } from "../NetPlanOutcomeTrackingSection";
import { createNetPlanBuilderApi, isOutcomeTracking, type BuilderResult, type CanonicalBuilder, type NetPlanBuilderApi, type OutcomeTracking } from "../netplanBuilderClient";

// Explicit structural engineering fixtures; no management/model/UAT claim.
function tracking(): Extract<OutcomeTracking, { state: "recorded" }> {
  return { state: "recorded", reasonCode: null, tasks: ([90, 180, 365] as const).map(days => ({
    task_id: `task-${days}`, scenario_id: "owned", approval_id: "actual-source-approval", horizon_days: days,
    anchor_rule: "actual_execution_calendar_days", execution_id: null, executed_at: null, due_at: null,
    status: "PENDING_EXECUTION", collected_outcome_id: null, measured_value: null, mature: false, training_eligible: false })),
    labels: [], receipt: { receipt_id: "receipt-owned", audit_event_id: "audit-owned", delivery_status: "pending" },
    automatic_collection: { available: false, reasonCode: "NETPLAN_REALIZED_MARGIN_PROVIDER_NOT_BOUND" },
    maturity: { available: false, reasonCode: "NETPLAN_LABEL_MATURITY_DEFINITION_NOT_BOUND" } };
}
function builder(status = "pending_approval"): CanonicalBuilder {
  return { scenarioId: "owned", scenarioName: "Scoped plan", planningHorizon: "2026-H2", status,
    problemHash: "a".repeat(64), editable: false, canSolve: true, policyAvailable: true, disclosurePolicyVersionId: "policy-owned",
    requiresNewDraft: false, isStale: false, solve: null, statusHistory: [], modelVersion: "model-owned", featureVersion: "features-owned",
    solverVersion: "solver-owned", policyVersion: "policy-owned", constraints: { max_budget: 100, min_expected_gross_margin: null,
      min_capacity_delta: null, max_average_risk: null, min_action_counts: {}, max_action_counts: {} },
    optionsByEntity: { "store-owned": [{ entity_id: "store-owned", action: "KEEP", expected_gross_margin: 100,
      budget_cost: 0, risk_score: 0.1, capacity_delta: 0, source_snapshot_ids: ["actual-input"], notes: [] }] },
    lifecycle: { canExecute: true, canApprove: true, execution: null, outcome: null } };
}
function api(decide: NonNullable<NetPlanBuilderApi["decide"]>): NetPlanBuilderApi {
  return { read: async () => ({ ok: true, builder: builder() }), command: async () => ({ ok: false, message: "unused" }), decide };
}
function complete() {
  fireEvent.change(screen.getByLabelText("原始管理核准回執識別"), { target: { value: "receipt-from-existing-authority" } });
  fireEvent.change(screen.getByLabelText("決定理由"), { target: { value: "Original native decision reason" } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shows exactly three actual pending IDs and preserves a single observed manual zero without horizon maturity", () => {
  const value = tracking(); value.labels.push({ label_id: "label-zero", scenario_id: "owned", outcome_id: "native-zero",
    actual_gross_margin: 0, measurement_method: "manual_recorded", status: "OBSERVED", horizon_days: null, mature: false, training_eligible: false });
  render(<NetPlanOutcomeTrackingSection tracking={value} />);
  for (const days of [90, 180, 365]) expect(screen.getByText(`task-${days}`)).toBeVisible();
  expect(screen.getAllByText("待實際執行")).toHaveLength(3);
  expect(screen.getByText("label-zero")).toBeVisible(); expect(screen.getByText("0")).toBeVisible();
  expect(screen.getByText(/單次填報不會複製/)).toBeVisible();
  expect(screen.getByText(/OBSERVED · 手動填報/)).toBeVisible();
});

it("opens the original Modal without POST, preserves failure input/key and adopts only a successful server receipt", async () => {
  const call = vi.fn<NonNullable<NetPlanBuilderApi["decide"]>>().mockResolvedValueOnce({ ok: false, message: "409 authority changed" })
    .mockResolvedValue({ ok: true, builder: { ...builder("approved"), outcomeTracking: tracking() }, auditEventId: "new-audit" });
  const recorded = vi.fn(); render(<NetPlanNativeApprovalSection storeId="store-owned" builder={builder()} api={api(call)} disabled={false} onRecorded={recorded} />);
  fireEvent.click(screen.getByRole("button", { name: "登錄方案決定" })); expect(call).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog"); expect(within(dialog).getByText("store-owned")).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "確認送出" })).toBeDisabled(); complete();
  fireEvent.click(within(dialog).getByRole("button", { name: "確認送出" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("409"); expect(recorded).not.toHaveBeenCalled();
  expect(screen.getByLabelText("原始管理核准回執識別")).toHaveValue("receipt-from-existing-authority");
  expect(call.mock.calls[0][4]).toEqual({ decision: "approved", approvalReceiptId: "receipt-from-existing-authority" });
  const key = call.mock.calls[0][3]; fireEvent.click(within(dialog).getByRole("button", { name: "確認送出" }));
  await waitFor(() => expect(recorded).toHaveBeenCalledWith(expect.objectContaining({ status: "approved" }), "new-audit"));
  expect(call.mock.calls[1][3]).toBe(key);
});

it("cannot adopt an old approval response after an API context change and return to the same scenario", async () => {
  let resolve!: (result: BuilderResult) => void; const call = vi.fn(() => new Promise<BuilderResult>(done => { resolve = done; }));
  const oldApi = api(call); const value = builder(); const recorded = vi.fn();
  const view = render(<NetPlanNativeApprovalSection storeId="store-owned" builder={value} api={oldApi} disabled={false} onRecorded={recorded} />);
  fireEvent.click(screen.getByRole("button", { name: "登錄方案決定" })); complete();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "確認送出" }));
  await waitFor(() => expect(call).toHaveBeenCalledOnce());
  view.rerender(<NetPlanNativeApprovalSection storeId="other-store" builder={value} api={api(vi.fn())} disabled={false} onRecorded={recorded} />);
  view.rerender(<NetPlanNativeApprovalSection storeId="store-owned" builder={value} api={oldApi} disabled={false} onRecorded={recorded} />);
  await act(async () => resolve({ ok: true, builder: builder("approved"), auditEventId: "old-audit" }));
  expect(recorded).not.toHaveBeenCalled();
});

it("sends original receipt/scenario/hash through the actual scoped client guard with no body actor or clock", async () => {
  const value = builder(); const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    builder: { ...builder("approved"), outcomeTracking: tracking() }, audit: { event_id: "verified-native-audit" } }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher); const changed = vi.fn(async () => undefined);
  const client = createNetPlanBuilderApi({}, () => async () => true, changed);
  expect((await client.decide!("store-owned", value, "Original reason", "same-native-intent",
    { decision: "approved", approvalReceiptId: "native-receipt" })).ok).toBe(true);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/operator/network-rebalance/stores/store-owned/netplan/builder/decide");
  const init = fetcher.mock.calls[0][1]; expect(init.method).toBe("POST"); expect(init.headers["Idempotency-Key"]).toBe("same-native-intent");
  expect(JSON.parse(init.body)).toEqual({ scenarioId: "owned", expectedProblemHash: "a".repeat(64), reason: "Original reason",
    nativeDecision: { decision: "approved", approvalReceiptId: "native-receipt" } });
  expect(changed).toHaveBeenCalledOnce();
});

it("does not send a new decision when current approval authority or the parent dirty guard is absent", () => {
  const call = vi.fn(); const value = builder(); value.lifecycle!.canApprove = false;
  const view = render(<NetPlanNativeApprovalSection storeId="store-owned" builder={value} api={api(call)} disabled={false} onRecorded={vi.fn()} />);
  expect(screen.getByRole("button", { name: "登錄方案決定" })).toBeDisabled();
  view.rerender(<NetPlanNativeApprovalSection storeId="store-owned" builder={builder()} api={api(call)} disabled onRecorded={vi.fn()} />);
  expect(screen.getByRole("button", { name: "登錄方案決定" })).toBeDisabled(); expect(call).not.toHaveBeenCalled();
});

it("rejects invented mature labels, duplicate horizons, mixed scenario IDs and missing tasks", () => {
  expect(isOutcomeTracking(tracking(), "owned")).toBe(true);
  const duplicate = tracking(); duplicate.tasks[1] = { ...duplicate.tasks[0] }; expect(isOutcomeTracking(duplicate, "owned")).toBe(false);
  const foreign = tracking(); foreign.tasks[1].scenario_id = "foreign"; expect(isOutcomeTracking(foreign, "owned")).toBe(false);
  const missing = tracking(); missing.tasks.pop(); expect(isOutcomeTracking(missing, "owned")).toBe(false);
  const invented = { ...tracking(), labels: [{ label_id: "invented", scenario_id: "owned", outcome_id: "x", actual_gross_margin: 0,
    measurement_method: "manual_recorded", status: "MATURE", horizon_days: 180, mature: true, training_eligible: true }] };
  expect(isOutcomeTracking(invented, "owned")).toBe(false);
});

function scheduledTracking() {
  const anchor = "2026-10-16T10:00:00+00:00";
  return { ...tracking(), tasks: tracking().tasks.map(row => ({ ...row, status: "SCHEDULED" as const,
    execution_id: "actual-execution", executed_at: anchor,
    due_at: new Date(Date.parse(anchor) + row.horizon_days * 86_400_000).toISOString() })) };
}
function executedLifecycle(): NonNullable<CanonicalBuilder["lifecycle"]> {
  return { canExecute: true, canApprove: true, outcome: null,
    execution: { execution_id: "actual-execution", scenario_id: "owned", executed_by: "actual-recorder",
      executed_at: "2026-10-16T10:00:00+00:00", actions: [{ entity_id: "store-owned", action: "KEEP" }] } };
}

it("rejects mixed stages, different executions or anchors, wrong calendar due times and naive timestamps", () => {
  expect(isOutcomeTracking(scheduledTracking(), "owned", executedLifecycle())).toBe(true);
  const mixed = { ...scheduledTracking(), tasks: [scheduledTracking().tasks[0], tracking().tasks[1], tracking().tasks[2]] };
  expect(isOutcomeTracking(mixed, "owned")).toBe(false);
  const executions = scheduledTracking(); executions.tasks[1].execution_id = "another-execution";
  expect(isOutcomeTracking(executions, "owned")).toBe(false);
  const anchor = scheduledTracking(); anchor.tasks[1].executed_at = "2026-10-17T10:00:00+00:00";
  expect(isOutcomeTracking(anchor, "owned")).toBe(false);
  const wrong = scheduledTracking(); wrong.tasks[0].due_at = wrong.tasks[0].executed_at;
  expect(isOutcomeTracking(wrong, "owned")).toBe(false);
  const naive = scheduledTracking(); naive.tasks[0].executed_at = "2026-10-16T10:00:00";
  expect(isOutcomeTracking(naive, "owned")).toBe(false);
  const naiveDue = scheduledTracking(); naiveDue.tasks[0].due_at = "2027-01-14T10:00:00";
  expect(isOutcomeTracking(naiveDue, "owned")).toBe(false);
});

it("joins all scheduled tasks and observed labels to the same response's actual execution and native outcome", () => {
  const lifecycle = executedLifecycle();
  const foreignExecution = { ...lifecycle, execution: { ...lifecycle.execution!, execution_id: "other" } };
  expect(isOutcomeTracking(scheduledTracking(), "owned", foreignExecution)).toBe(false);
  const wrongTime = { ...lifecycle, execution: { ...lifecycle.execution!, executed_at: "2026-10-17T10:00:00+00:00" } };
  expect(isOutcomeTracking(scheduledTracking(), "owned", wrongTime)).toBe(false);
  expect(isOutcomeTracking(tracking(), "owned", lifecycle)).toBe(false);
  lifecycle.outcome = { outcome_id: "actual-manual-zero", scenario_id: "owned", expected_gross_margin: 100,
    actual_gross_margin: 0, variance: -100, variance_pct: -1, observed_at: "2026-10-17T10:00:00+00:00", source_snapshot_ids: ["actual-ledger"] };
  const label = { label_id: "actual-label", scenario_id: "owned", outcome_id: "actual-manual-zero",
    execution_id: "actual-execution", actual_gross_margin: 0, measurement_method: "manual_recorded", status: "OBSERVED",
    horizon_days: null, mature: false, training_eligible: false };
  const valid = { ...scheduledTracking(), labels: [label] };
  expect(isOutcomeTracking(valid, "owned", lifecycle)).toBe(true);
  expect(isOutcomeTracking({ ...valid, labels: [{ ...label, execution_id: "other" }] }, "owned", lifecycle)).toBe(false);
  expect(isOutcomeTracking({ ...valid, labels: [{ ...label, outcome_id: "other" }] }, "owned", lifecycle)).toBe(false);
  expect(isOutcomeTracking({ ...valid, labels: [{ ...label, actual_gross_margin: 1 }] }, "owned", lifecycle)).toBe(false);
});

it("shows only a source-matched actual due progress and never converts a notification into a collected measurement", () => {
  const tasks = scheduledTracking().tasks.map(row => ({ ...row, tenant_id: "owned-tenant", collection_job_id: `job-${row.horizon_days}` }));
  const first = tasks[0];
  const progress = { tenant_id: "owned-tenant", scenario_id: "owned", task_id: first.task_id, job_id: first.collection_job_id,
    execution_id: first.execution_id, horizon_days: first.horizon_days, due_at: first.due_at, due_observed_at: first.due_at,
    audit_event_id: "actual-due-audit", task_source_sha256: "b".repeat(64), state: "DUE_SOURCE_UNAVAILABLE" as const,
    collected_outcome_id: null, measured_value: null, mature: false as const, training_eligible: false as const };
  const value = { ...scheduledTracking(), tasks, collectionProgress: [progress] };
  expect(isOutcomeTracking(value, "owned", executedLifecycle())).toBe(true);
  render(<NetPlanOutcomeTrackingSection tracking={value} />);
  expect(screen.getByText("actual-due-audit")).toBeVisible();
  expect(screen.getByText("已到期，待資料來源；尚無此期間量測結果")).toBeVisible();
  expect(isOutcomeTracking({ ...value, collectionProgress: [{ ...progress, job_id: "another-job" }] }, "owned", executedLifecycle())).toBe(false);
  expect(isOutcomeTracking({ ...value, collectionProgress: [{ ...progress, tenant_id: "foreign" }] }, "owned", executedLifecycle())).toBe(false);
  expect(isOutcomeTracking({ ...value, collectionProgress: [{ ...progress, task_id: "unrelated-task" }] }, "owned", executedLifecycle())).toBe(false);
  expect(isOutcomeTracking({ ...value, collectionProgress: [{ ...progress, mature: true, measured_value: 99 }] }, "owned", executedLifecycle())).toBe(false);
});
