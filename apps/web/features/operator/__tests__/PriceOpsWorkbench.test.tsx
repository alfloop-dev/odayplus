import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PriceOpsWorkbench } from "../PriceOpsWorkbench";
import { PRICEOPS_RECOMMENDATIONS, normalizePriceRecommendation } from "../growthViewModel";

const rec = { ...PRICEOPS_RECOMMENDATIONS[0], planId: "plan-a", itemId: "item-a" };
const sim = (price: number) => ({ price, demand: { p10: 111, p50: 222, p90: 333 }, revenue: { p10: 444, p50: 555, p90: 666 }, gross_margin: { p10: 77, p50: 88, p90: 99 } });
const comparison = { plan_id: "plan-a", plan_status: "pending_approval", is_feasible: true, is_approvable: true, rollback_ready: true,
  actor_id: "verified-pricer", policy_version: "actual-policy", solver_version: "actual-solver", command_permissions: { simulate: true, writeback: true },
  items: [{ item_id: "item-a", current_price: 80, candidate_price: 85, baseline_simulation: sim(80), candidate_simulation: sim(85), constraint_status: "PASS", expected_gross_margin_change: 17 }] };
function reply(value: unknown) { return { ok: true, json: async () => value }; }
function setup(options: { deny?: boolean; mismatch?: string; uncertainOnce?: boolean; pauseWrite?: boolean; ownPlan?: boolean; malformedBlockers?: boolean } = {}) {
  const requests: { url: string; init: RequestInit; body: Record<string, unknown> }[] = [];
  let release: (() => void) | undefined;
  let writes = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : {};
    requests.push({ url, init, body });
    if (url.endsWith("/comparison")) return reply({ ...comparison,
      ...(options.ownPlan ? { created_by: "verified-pricer", is_approvable: false, approval_actor_blockers: ["plan creator cannot approve their own pricing plan"] } : {}),
      ...(options.malformedBlockers ? { approval_actor_blockers: "malformed" } : {}),
      command_permissions: options.deny ? { simulate: false, writeback: false } : comparison.command_permissions });
    const headers = init.headers as Record<string, string>;
    if (url.endsWith("/simulate-scenario")) return reply({ scenario_id: "retained-scenario", plan_id: "plan-a", is_feasible: true,
      items: [{ item_id: "item-a", candidate_price: body.candidate_prices["item-a"], baseline_simulation: sim(80), candidate_simulation: sim(body.candidate_prices["item-a"]), expected_gross_margin_change: 17, is_feasible: true }],
      audit_event_id: "actual-simulation-audit", correlation_id: headers["X-Correlation-Id"] });
    writes++;
    if (options.uncertainOnce && writes === 1) throw new Error("response lost");
    if (options.pauseWrite) await new Promise<void>(resolve => { release = resolve; });
    return reply({ decision_id: "persisted-decision", plan_id: "plan-a", actor: "verified-pricer", ...body,
      written_back_at: "2026-10-12T12:00:00Z", policy_version: "actual-policy", solver_version: "actual-solver",
      idempotency_key: headers["Idempotency-Key"], audit_event_id: "actual-decision-audit", correlation_id: headers["X-Correlation-Id"],
      ...(options.mismatch ? { [options.mismatch]: options.mismatch === "audit_event_id" ? "" : "wrong" } : {}) });
  }));
  const view = render(<PriceOpsWorkbench recommendation={rec} segmentName="synthetic segment" />);
  return { requests, view, release: () => release?.() };
}
async function prepare() {
  await waitFor(() => expect(screen.getByTestId("priceops-simulate-btn")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("調整情境價:"), { target: { value: "85" } });
  fireEvent.click(screen.getByTestId("priceops-simulate-btn"));
  await waitFor(() => expect(within(screen.getByTestId("priceops-alternative-band")).getByText("111 / 222 / 333")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("決策原因"), { target: { value: "authored reason" } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("PriceOps real command acknowledgments", () => {
  it("shows API bands, real versions, and audited server identity only after confirmed writeback", async () => {
    const { requests } = setup();
    await prepare();
    expect(screen.getByTestId("priceops-scenario-workbench")).toHaveTextContent("actual-policy");
    expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    await waitFor(() => expect(screen.getByTestId("priceops-audit-status")).toHaveTextContent("persisted-decision"));
    expect(screen.getByTestId("priceops-audit-status")).toHaveTextContent("verified-pricer");
    expect(screen.getByTestId("priceops-audit-status")).toHaveTextContent("actual-decision-audit");
    expect(requests.at(-1)?.body).toMatchObject({ decision: "scenario_selected", reason: "authored reason", selected_scenario_id: "retained-scenario" });
  });
  it("retains the command key and correlation when a response is lost", async () => {
    const { requests } = setup({ uncertainOnce: true }); await prepare();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    await screen.findByText(/尚未確認決策寫回/);
    expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    await screen.findByTestId("priceops-audit-status");
    const writes = requests.filter(row => row.url.endsWith("decision-writeback"));
    expect(writes).toHaveLength(2); expect(writes[1].body).toEqual(writes[0].body);
    expect(writes[1].init.headers).toEqual(writes[0].init.headers);
  });
  it("locks parameters and refuses double submits while the server response is pending", async () => {
    const { requests, release } = setup({ pauseWrite: true }); await prepare();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn")); fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    expect(screen.getByLabelText("決策原因")).toBeDisabled();
    expect(screen.getByLabelText("調整情境價:")).toBeDisabled();
    expect(requests.filter(row => row.url.endsWith("decision-writeback"))).toHaveLength(1);
    expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    release(); await screen.findByTestId("priceops-audit-status");
  });
  it.each(["plan_id", "actor", "selected_scenario_id", "correlation_id", "audit_event_id", "idempotency_key"])("refuses mismatched %s acknowledgments", async field => {
    const { view } = setup({ mismatch: field }); await prepare();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    await screen.findByText(/尚未確認決策寫回/); expect(screen.queryByTestId("priceops-audit-status")).not.toBeInTheDocument();
    view.unmount();
  });
  it("shows independent-review guidance and prevents own-plan approval without preventing scenario selection", async () => {
    const { requests } = setup({ ownPlan: true }); await prepare();
    fireEvent.change(screen.getByLabelText("決策"), { target: { value: "approved" } });
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
    expect(screen.getByTestId("priceops-scenario-workbench")).toHaveTextContent("建立者不能核准自己的調價計畫");
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    expect(requests.filter(row => row.url.endsWith("decision-writeback"))).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("決策"), { target: { value: "scenario_selected" } });
    expect(screen.getByTestId("priceops-writeback-btn")).toBeEnabled();
  });
  it("refuses malformed approval-context data instead of rendering an enabled approval", async () => {
    setup({ malformedBlockers: true });
    await screen.findByText(/定價計畫或模擬資料未取得/);
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
    expect(screen.getByTestId("priceops-simulate-btn")).toBeDisabled();
  });
  it("renders a read-only account without enabled business commands", async () => {
    const { requests } = setup({ deny: true });
    await screen.findByText("目前帳號沒有定價決策寫回權限。");
    expect(screen.getByTestId("priceops-simulate-btn")).toBeDisabled();
    expect(screen.getByTestId("priceops-writeback-btn")).toBeDisabled();
    fireEvent.click(screen.getByTestId("priceops-writeback-btn"));
    expect(requests.filter(row => row.init.method === "POST")).toHaveLength(0);
  });
  it("preserves canonical plan/item bindings and does not invent percentages from absolute margin", () => {
    const projected = normalizePriceRecommendation({ id: "canonical", segmentId: "s", planId: "p", itemId: "i", currentPrice: 80, recommendedPrice: 85, expectedIncrementalGrossMargin: 17, constraintStatus: "SAFE" } as never);
    expect(projected).toMatchObject({ planId: "p", itemId: "i", currentPrice: "80", candidatePrice: "85", expectedIncrementalGrossMargin: 17, expectedRevenueLift: null, expectedMarginLift: null });
  });
});
