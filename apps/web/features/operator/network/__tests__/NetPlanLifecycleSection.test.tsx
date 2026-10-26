import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NetPlanLifecycleSection } from "../NetPlanLifecycleSection";
import { createNetPlanBuilderApi, type CanonicalBuilder, type NetPlanBuilderApi, type BuilderResult } from "../netplanBuilderClient";

function builder(status = "executed"): CanonicalBuilder {
  return { scenarioId: "owned", scenarioName: "Scoped plan", planningHorizon: "2026-H2", status,
    problemHash: "a".repeat(64), editable: false, canSolve: true, policyAvailable: true, disclosurePolicyVersionId: "policy-owned",
    requiresNewDraft: false, isStale: false, solve: null, statusHistory: [], modelVersion: "model-owned", featureVersion: "features-owned",
    solverVersion: "solver-owned", policyVersion: "policy-owned", constraints: { max_budget: 100, min_expected_gross_margin: null,
      min_capacity_delta: null, max_average_risk: null, min_action_counts: {}, max_action_counts: {} },
    optionsByEntity: { "store-owned": [{ entity_id: "store-owned", action: "KEEP", expected_gross_margin: 100,
      budget_cost: 0, risk_score: 0.1, capacity_delta: 0, source_snapshot_ids: ["actual-input"], notes: [] }] },
    lifecycle: { canExecute: true, execution: status === "approved" ? null : { execution_id: "execution-owned", scenario_id: "owned",
      executed_by: "recorder-owned", executed_at: "2026-10-15T08:00:00Z", actions: [{ entity_id: "store-owned", action: "KEEP" }] }, outcome: null } };
}
function api(lifecycle: NonNullable<NetPlanBuilderApi["lifecycle"]>): NetPlanBuilderApi {
  return { read: async () => ({ ok: true, builder: builder() }), command: async () => ({ ok: false, message: "unused" }), lifecycle };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shows original recorded execution while outcomes remain pending, without claiming performance", () => {
  const call = vi.fn(); render(<NetPlanLifecycleSection storeId="store-owned" builder={builder()} api={api(call)} onRecorded={vi.fn()} />);
  expect(screen.getByText("待觀察結果")).toBeVisible();
  expect(screen.getByText("execution-owned")).toBeVisible(); expect(screen.getByText("recorder-owned")).toBeVisible();
  expect(screen.queryByText("預期與實際比較")).not.toBeInTheDocument(); expect(call).not.toHaveBeenCalled();
});

it("confirms all affected IDs, preserves zero and the same failed intent, then adopts only a server receipt", async () => {
  const call = vi.fn<NonNullable<NetPlanBuilderApi["lifecycle"]>>().mockResolvedValueOnce({ ok: false, message: "409 current source changed" })
    .mockResolvedValue({ ok: true, builder: builder("outcome_observed"), auditEventId: "audit-recorded" });
  const recorded = vi.fn(); render(<NetPlanLifecycleSection storeId="store-owned" builder={builder()} api={api(call)} onRecorded={recorded} />);
  fireEvent.click(screen.getByRole("button", { name: "登錄實際結果" })); const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("store-owned")).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "確認送出" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("實際毛利"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("填報來源識別（逗號或換行分隔）"), { target: { value: "manual-ledger" } });
  fireEvent.change(screen.getByLabelText("登錄理由"), { target: { value: "Observed actual ledger record" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "確認送出" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("409"); expect(recorded).not.toHaveBeenCalled();
  expect(screen.getByLabelText("實際毛利")).toHaveValue("0");
  const key = call.mock.calls[0][4]; expect(call.mock.calls[0][5]).toEqual({ actualGrossMargin: 0, sourceSnapshotIds: ["manual-ledger"] });
  fireEvent.click(within(dialog).getByRole("button", { name: "確認送出" }));
  await waitFor(() => expect(recorded).toHaveBeenCalledWith(expect.objectContaining({ status: "outcome_observed" }), "audit-recorded"));
  expect(call.mock.calls[1][4]).toBe(key);
});

it("discards a stale response when the same scenario receives a new API context", async () => {
  let finish!: (value: BuilderResult) => void; const call = vi.fn(() => new Promise<BuilderResult>(resolve => { finish = resolve; }));
  const onRecorded = vi.fn(); const value = builder("approved"); const view = render(<NetPlanLifecycleSection storeId="store-owned" builder={value} api={api(call)} onRecorded={onRecorded} />);
  fireEvent.click(screen.getByRole("button", { name: "登錄方案執行" })); fireEvent.change(screen.getByLabelText("登錄理由"), { target: { value: "Confirm execution record" } });
  fireEvent.click(screen.getByRole("button", { name: "確認送出" })); await waitFor(() => expect(call).toHaveBeenCalledOnce());
  view.rerender(<NetPlanLifecycleSection storeId="store-owned" builder={value} api={api(vi.fn())} onRecorded={onRecorded} />);
  await act(async () => finish({ ok: true, builder: builder(), auditEventId: "old-audit" })); expect(onRecorded).not.toHaveBeenCalled();
});

it("sends the canonical scenario/hash and manual facts through the captured guard without body actors", async () => {
  const value = builder(); const payload = { actualGrossMargin: 0, sourceSnapshotIds: [] };
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ builder: { ...value, status: "outcome_observed" }, audit: { event_id: "actual-audit" } }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher); const changed = vi.fn(async () => undefined);
  const client = createNetPlanBuilderApi({}, () => async () => true, changed);
  expect((await client.lifecycle!("store-owned", "outcome", value, "Actual ledger source", "same-key", payload)).ok).toBe(true);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/operator/network-rebalance/stores/store-owned/netplan/builder/outcome");
  const init = fetcher.mock.calls[0][1]; expect(init.method).toBe("POST"); expect(init.headers["Idempotency-Key"]).toBe("same-key");
  expect(JSON.parse(init.body)).toEqual({ scenarioId: "owned", expectedProblemHash: "a".repeat(64), reason: "Actual ledger source", outcome: payload });
  expect(changed).toHaveBeenCalledOnce();
});
