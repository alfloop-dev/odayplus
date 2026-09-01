import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PlanGanttChart,
  type NetworkPlanActionType,
  type PlanGanttActionItem,
  type PlanGanttDependency,
} from "../PlanGanttChart";
import type { NetPlanDiagnostic } from "../../types";

afterEach(cleanup);

describe("NetPlan Quarterly Gantt Chart Component (PlanGanttChart)", () => {
  const sampleActions: PlanGanttActionItem[] = [
    {
      entity_id: "STORE-101",
      entity_name: "台北信義店",
      quarter: "2026Q1",
      action: "KEEP",
      budget_cost: 0,
      expected_gross_margin: 850000,
      risk_score: 0.15,
      capacity_delta: 0,
    },
    {
      entity_id: "STORE-101",
      entity_name: "台北信義店",
      quarter: "2026Q2",
      action: "IMPROVE",
      budget_cost: 450000,
      expected_gross_margin: 980000,
      risk_score: 0.3,
      capacity_delta: 20,
      is_binding: true,
      binding_reasons: ["max_budget (超過區域改善預算上限)"],
    },
    {
      entity_id: "STORE-202",
      entity_name: "新北板橋店",
      quarter: "2026Q1",
      action: "EXIT",
      budget_cost: 180000,
      expected_gross_margin: -50000,
      risk_score: 0.1,
      capacity_delta: -50,
    },
    {
      entity_id: "STORE-202",
      entity_name: "新北板橋店",
      quarter: "2026Q2",
      action: "TRANSFER",
      budget_cost: 250000,
      expected_gross_margin: 400000,
      risk_score: 0.45,
      capacity_delta: 30,
      depends_on: ["STORE-202"],
    },
    {
      entity_id: "SITE-C01",
      entity_name: "台中逢甲候選新址",
      quarter: "2026Q3",
      action: "MOVE",
      budget_cost: 1900000,
      expected_gross_margin: 1200000,
      risk_score: 0.55,
      capacity_delta: 60,
      depends_on: ["STORE-202"],
    },
    {
      entity_id: "SITE-C02",
      entity_name: "高雄巨蛋旗艦新址",
      quarter: "2026Q4",
      action: "OPEN",
      budget_cost: 3200000,
      expected_gross_margin: 1650000,
      risk_score: 0.4,
      capacity_delta: 100,
    },
  ];

  const sampleDiagnostics: NetPlanDiagnostic[] = [
    {
      violated_constraint: "max_budget",
      affected_stores: ["STORE-101"],
      required_relaxation: "放寬 2026Q2 改善預算至 50 萬元",
      business_impact: "solver cannot produce a complete quarter action list",
      suggested_action: "核准預算例外或延後改裝計畫",
    },
  ];

  const sampleDependencies: PlanGanttDependency[] = [
    {
      id: "dep-1",
      fromEntityId: "STORE-202",
      fromQuarter: "2026Q1",
      toEntityId: "STORE-202",
      toQuarter: "2026Q2",
      type: "temporal_hard_constraint",
      label: "EXIT → TRANSFER 承接時序",
      description: "ODP-FR-NET-002: STORE-202 於 2026Q1 關店止損後始得於 2026Q2 轉移營運",
    },
    {
      id: "dep-2",
      fromEntityId: "STORE-202",
      fromQuarter: "2026Q2",
      toEntityId: "SITE-C01",
      toQuarter: "2026Q3",
      type: "move_dependency",
      label: "舊址釋出 → 新址 MOVE",
      description: "ODP-FR-NET-002: 舊址轉移完成後方可執行 SITE-C01 搬遷裝修",
    },
  ];

  it("1. Renders horizontal quarter axis and rows for each planning entity", () => {
    render(
      <PlanGanttChart
        scenarioId="NP-2026-Q1-Q4"
        scenarioName="2026 全年店網重配優化方案"
        policyId="netplan-network-policy"
        policyVersion="netplan-network-policy-v1"
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        actions={sampleActions}
      />
    );

    // Verify main container rendered
    expect(screen.getByTestId("netplan-gantt-chart")).toBeInTheDocument();

    // Verify horizontal quarters header
    const quartersHeader = screen.getByTestId("gantt-quarters-header");
    expect(quartersHeader).toBeInTheDocument();
    expect(screen.getByTestId("gantt-quarter-2026Q1")).toHaveTextContent("2026Q1");
    expect(screen.getByTestId("gantt-quarter-2026Q2")).toHaveTextContent("2026Q2");
    expect(screen.getByTestId("gantt-quarter-2026Q3")).toHaveTextContent("2026Q3");
    expect(screen.getByTestId("gantt-quarter-2026Q4")).toHaveTextContent("2026Q4");

    // Verify rows for each distinct planning entity
    expect(screen.getByTestId("gantt-row-STORE-101")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-row-header-STORE-101")).toHaveTextContent("STORE-101");
    expect(screen.getByTestId("gantt-row-STORE-202")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-row-header-STORE-202")).toHaveTextContent("STORE-202");
    expect(screen.getByTestId("gantt-row-SITE-C01")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-row-SITE-C02")).toBeInTheDocument();
  });

  it("2. Color-codes action bars according to OPEN / KEEP / IMPROVE / MOVE / EXIT / TRANSFER", () => {
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
      />
    );

    // Verify Legend items for all 6 actions
    const legendBar = screen.getByTestId("gantt-legend-bar");
    expect(legendBar).toBeInTheDocument();
    expect(screen.getByTestId("legend-item-OPEN")).toHaveTextContent("OPEN 新設");
    expect(screen.getByTestId("legend-item-KEEP")).toHaveTextContent("KEEP 維持");
    expect(screen.getByTestId("legend-item-IMPROVE")).toHaveTextContent("IMPROVE 改善");
    expect(screen.getByTestId("legend-item-MOVE")).toHaveTextContent("MOVE 搬遷");
    expect(screen.getByTestId("legend-item-EXIT")).toHaveTextContent("EXIT 關店");
    expect(screen.getByTestId("legend-item-TRANSFER")).toHaveTextContent("TRANSFER 轉移");

    // Check individual action bars on the Gantt chart
    const keepBar = screen.getByTestId("gantt-bar-STORE-101-2026Q1");
    expect(keepBar).toHaveAttribute("data-action", "KEEP");
    expect(keepBar).toHaveTextContent("KEEP");

    const improveBar = screen.getByTestId("gantt-bar-STORE-101-2026Q2");
    expect(improveBar).toHaveAttribute("data-action", "IMPROVE");
    expect(improveBar).toHaveTextContent("IMPROVE");

    const exitBar = screen.getByTestId("gantt-bar-STORE-202-2026Q1");
    expect(exitBar).toHaveAttribute("data-action", "EXIT");
    expect(exitBar).toHaveTextContent("EXIT");

    const transferBar = screen.getByTestId("gantt-bar-STORE-202-2026Q2");
    expect(transferBar).toHaveAttribute("data-action", "TRANSFER");
    expect(transferBar).toHaveTextContent("TRANSFER");

    const moveBar = screen.getByTestId("gantt-bar-SITE-C01-2026Q3");
    expect(moveBar).toHaveAttribute("data-action", "MOVE");
    expect(moveBar).toHaveTextContent("MOVE");

    const openBar = screen.getByTestId("gantt-bar-SITE-C02-2026Q4");
    expect(openBar).toHaveAttribute("data-action", "OPEN");
    expect(openBar).toHaveTextContent("OPEN");
  });

  it("3. Renders dependency lines expressing ODP-FR-NET-002 temporal hard constraints", () => {
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        dependencies={sampleDependencies}
      />
    );

    // Verify SVG overlay layer is rendered
    const svgLayer = screen.getByTestId("gantt-dependencies-layer");
    expect(svgLayer).toBeInTheDocument();

    // Verify dependency lines are drawn
    const lines = screen.getAllByTestId("gantt-dependency-line");
    expect(lines.length).toBeGreaterThanOrEqual(2);

    // Verify accessible textual summary list of ODP-FR-NET-002 temporal constraints
    const depList = screen.getByTestId("gantt-dependencies-list");
    expect(depList).toBeInTheDocument();
    expect(depList).toHaveTextContent("ODP-FR-NET-002");
    expect(depList).toHaveTextContent("STORE-202");
    expect(depList).toHaveTextContent("SITE-C01");
  });

  it("4. Highlights Binding Constraints and conflicts with warning alert styling", () => {
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        bindingConstraints={["max_budget: STORE-101 改善預算受限"]}
        diagnostics={sampleDiagnostics}
      />
    );

    // Verify summary alert box
    const summaryBox = screen.getByTestId("gantt-binding-constraints-summary");
    expect(summaryBox).toBeInTheDocument();
    expect(summaryBox).toHaveTextContent("Binding Constraints");
    expect(summaryBox).toHaveTextContent("max_budget");
    expect(summaryBox).toHaveTextContent("solver cannot produce a complete quarter action list");

    // Verify warning badge on the affected action bar
    const bindingBadge = screen.getByTestId("gantt-binding-badge-STORE-101-2026Q2");
    expect(bindingBadge).toBeInTheDocument();
    expect(bindingBadge).toHaveTextContent("⚠️ Binding");

    const bar = screen.getByTestId("gantt-bar-STORE-101-2026Q2");
    expect(bar).toHaveAttribute("data-is-binding", "true");
  });

  it("5. Provides equivalent table view with complete information parity and view toggle", () => {
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        bindingConstraints={["max_budget"]}
        defaultView="gantt"
      />
    );

    // Initially in Gantt view
    expect(screen.getByTestId("gantt-view-container")).toBeInTheDocument();
    expect(screen.queryByTestId("gantt-equivalent-table")).not.toBeInTheDocument();

    // Toggle to Table view
    const tableToggleBtn = screen.getByTestId("view-toggle-table");
    fireEvent.click(tableToggleBtn);

    // Verify Table view is rendered
    const tableView = screen.getByTestId("gantt-equivalent-table");
    expect(tableView).toBeInTheDocument();

    // Verify all rows and columns are present in the table
    expect(screen.getByTestId("table-row-STORE-101-2026Q1")).toHaveTextContent("STORE-101");
    expect(screen.getByTestId("table-row-STORE-101-2026Q1")).toHaveTextContent("KEEP");
    expect(screen.getByTestId("table-row-STORE-101-2026Q2")).toHaveTextContent("IMPROVE");
    expect(screen.getByTestId("table-row-STORE-202-2026Q1")).toHaveTextContent("EXIT");
    expect(screen.getByTestId("table-row-STORE-202-2026Q2")).toHaveTextContent("TRANSFER");
    expect(screen.getByTestId("table-row-SITE-C01-2026Q3")).toHaveTextContent("MOVE");
    expect(screen.getByTestId("table-row-SITE-C02-2026Q4")).toHaveTextContent("OPEN");

    // Verify binding constraint tag in table
    expect(screen.getByTestId("table-binding-tag-STORE-101")).toBeInTheDocument();

    // Toggle back to Gantt view
    const ganttToggleBtn = screen.getByTestId("view-toggle-gantt");
    fireEvent.click(ganttToggleBtn);
    expect(screen.getByTestId("gantt-view-container")).toBeInTheDocument();
  });

  it("6. Renders both policy_id and policy_version on approval / scenario header", () => {
    render(
      <PlanGanttChart
        scenarioId="NP-SCENARIO-AUDIT-001"
        scenarioName="旗艦店網核准方案"
        policyId="netplan-network-policy"
        policyVersion="netplan-network-policy-v1"
        solverVersion="netplan-ortools-mip-v1"
        objectiveScore={1840000.5}
        actions={sampleActions}
      />
    );

    // Verify policy metadata container
    const metaContainer = screen.getByTestId("gantt-policy-metadata");
    expect(metaContainer).toBeInTheDocument();

    // Verify policy_id is explicitly presented
    const policyIdElem = screen.getByTestId("gantt-policy-id");
    expect(policyIdElem).toBeInTheDocument();
    expect(policyIdElem).toHaveTextContent("netplan-network-policy");

    // Verify policy_version is explicitly presented
    const policyVersionElem = screen.getByTestId("gantt-policy-version");
    expect(policyVersionElem).toBeInTheDocument();
    expect(policyVersionElem).toHaveTextContent("netplan-network-policy-v1");

    // Verify solver version and score
    expect(screen.getByTestId("gantt-solver-version")).toHaveTextContent("netplan-ortools-mip-v1");
    expect(screen.getByTestId("gantt-objective-score")).toHaveTextContent("1840000.5");
  });

  it("7. Handles empty actions gracefully without errors", () => {
    render(
      <PlanGanttChart
        scenarioId="NP-EMPTY"
        scenarioName="空白規劃方案"
        policyId="netplan-network-policy"
        policyVersion="netplan-network-policy-v1"
        actions={[]}
      />
    );

    expect(screen.getByTestId("gantt-empty-state")).toHaveTextContent("尚無規劃實體或行動資料");
  });

  it("8. Fires onActionClick handler when clicking an action bar", () => {
    const handleClick = vi.fn();
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        onActionClick={handleClick}
      />
    );

    const openBar = screen.getByTestId("gantt-bar-SITE-C02-2026Q4");
    fireEvent.click(openBar);
    expect(handleClick).toHaveBeenCalledTimes(1);
    expect(handleClick).toHaveBeenCalledWith(
      expect.objectContaining({
        entity_id: "SITE-C02",
        action: "OPEN",
      })
    );
  });
});
