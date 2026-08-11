import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RebalancePanel } from "../../../features/operator/network/RebalancePanel";
import type { RebalanceQueueRow } from "../../../features/operator/networkFindAreasViewModel";
import type { NetPlanDiagnostic, NetPlanScenarioDetail } from "../../../features/operator/types";

afterEach(cleanup);

describe("NetPlan structured diagnostic & stale state UX component rendering", () => {
  it("renders RebalancePanel with all 5 structured diagnostic fields, stale badge, and infeasible badge", () => {
    const diagnostic: NetPlanDiagnostic = {
      violated_constraint: "max_budget",
      affected_stores: ["STORE-101", "STORE-102"],
      required_relaxation: "budget_cost <= 2500000.0 (relax by 500000.0)",
      business_impact: "投資預算超出上限，可能影響現金流",
      suggested_action: "增加預算或調整搬遷規模",
    };

    const scenario: NetPlanScenarioDetail = {
      id: "SCENARIO-INFEASIBLE",
      name: "不可行重配方案 A",
      roi: "0.0%",
      inv: "NT$2.5M",
      payback: "N/A",
      risk: "High",
      time: "6 個月",
      isStale: true,
      isInfeasible: true,
      score: 45,
      diagnostics: [diagnostic],
      modelVersion: "NetPlan v1.0",
      snapshotId: "SNAP-20260810",
    };

    const row: RebalanceQueueRow = {
      id: "REB-101",
      storeId: "STORE-101",
      storeName: "台北信義店",
      status: "netplanreview",
      statusLabel: "店網評估",
      summary: "低效重配測試",
      tone: "watch",
      selectedScenarioId: "SCENARIO-INFEASIBLE",
      netPlanScenarios: [scenario],
    };

    render(
      <RebalancePanel
        apiError={null}
        busyAction={null}
        onCompleteAvm={vi.fn()}
        onRequestAvm={vi.fn()}
        onSelectScenario={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSubmitReview={vi.fn()}
        rows={[row]}
      />,
    );

    // 1. Verify scenario card rendering in DOM
    const card = screen.getByTestId("rebalance-scenario-SCENARIO-INFEASIBLE");
    expect(card).toBeInTheDocument();

    // 2. Verify stale and infeasible badges rendered in DOM
    const staleBadge = screen.getByTestId("scenario-stale-SCENARIO-INFEASIBLE");
    expect(staleBadge).toHaveTextContent("過期 / Stale");

    const infeasibleBadge = screen.getByTestId("scenario-infeasible-SCENARIO-INFEASIBLE");
    expect(infeasibleBadge).toHaveTextContent("不可行");

    // 3. Verify all 5 structured diagnostic fields rendered in DOM
    const diagBox = screen.getByTestId("scenario-diagnostics-SCENARIO-INFEASIBLE");
    expect(diagBox).toBeInTheDocument();
    expect(diagBox).toHaveTextContent("不可行性診斷 (Infeasibility Diagnostics)");

    const item = screen.getByTestId("diagnostic-item-0");
    const container = item;

    const violatedConstraint = container.querySelector('[data-field="violated_constraint"]');
    expect(violatedConstraint).toHaveTextContent("max_budget");

    const affectedStores = container.querySelector('[data-field="affected_stores"]');
    expect(affectedStores).toHaveTextContent("STORE-101, STORE-102");

    const requiredRelaxation = container.querySelector('[data-field="required_relaxation"]');
    expect(requiredRelaxation).toHaveTextContent("budget_cost <= 2500000.0 (relax by 500000.0)");

    const businessImpact = container.querySelector('[data-field="business_impact"]');
    expect(businessImpact).toHaveTextContent("投資預算超出上限，可能影響現金流");

    const suggestedAction = container.querySelector('[data-field="suggested_action"]');
    expect(suggestedAction).toHaveTextContent("增加預算或調整搬遷規模");
  });
});
