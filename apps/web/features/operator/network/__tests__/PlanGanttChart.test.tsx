import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PlanGanttChart,
  type NetworkPlanActionType,
  type PlanGanttActionItem,
  type PlanGanttDependency,
} from "../PlanGanttChart";
import { RebalancePanel } from "../RebalancePanel";
import type { ConstraintClass, NetPlanDiagnostic } from "../../types";
import type { RebalanceQueueRow } from "../../networkFindAreasViewModel";
// Asserted against the live FastAPI response by
// tests/integration/test_netplan_disclosure_ui_e2e.py -- see fixtures/README.md.
import rawApiPayload from "./fixtures/netplanDisclosureApiPayload.json";

// Narrowed once, here, rather than at each use. JSON widens the class names to
// `string`, so each list is re-typed against the console's own union: a class
// the backend renames becomes a compile error in this file rather than an
// assertion that quietly stops matching anything. Field-by-field rather than
// one cast over the whole object, so a field disappearing from the fixture is
// caught here too.
const asConstraintClasses = (values: string[]): ConstraintClass[] =>
  values as ConstraintClass[];

const apiPayload = {
  storeId: rawApiPayload.storeId,
  status: rawApiPayload.status as RebalanceQueueRow["status"],
  netPlanScenarios: rawApiPayload.netPlanScenarios.map((scenario) => ({
    id: scenario.id,
    name: scenario.name,
    isSystemRecommendation: scenario.isSystemRecommendation,
    modelledConstraintClasses: asConstraintClasses(scenario.modelledConstraintClasses),
    unmodelledConstraintClasses: asConstraintClasses(scenario.unmodelledConstraintClasses),
    modelled_constraint_classes: asConstraintClasses(scenario.modelled_constraint_classes),
    unmodelled_constraint_classes: asConstraintClasses(
      scenario.unmodelled_constraint_classes
    ),
    blockedConstraintClasses: asConstraintClasses(scenario.blockedConstraintClasses),
    acknowledgeableConstraintClasses: asConstraintClasses(
      scenario.acknowledgeableConstraintClasses
    ),
    disclosurePolicyVersionId: scenario.disclosurePolicyVersionId,
    disclosureUndeclared: scenario.disclosureUndeclared,
  })),
};

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
      binding_reasons: ["max_budget: STORE-101 改善預算受限"],
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

  it("1. Renders horizontal quarter axis and rows for each planning entity from backend data", () => {
    render(
      <PlanGanttChart
        scenarioId="NP-2026-Q1-Q4"
        scenarioName="2026 全年店網重配優化方案"
        policyId="pol-net-001"
        policyVersion="pol-v2.1"
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

  it("3. Renders authoritative dependency lines without generating fake synthetic sequence links", () => {
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
    expect(lines.length).toBe(2);

    // Verify accessible textual summary list of ODP-FR-NET-002 temporal constraints
    const depList = screen.getByTestId("gantt-dependencies-list");
    expect(depList).toBeInTheDocument();
    expect(depList).toHaveTextContent("ODP-FR-NET-002");
    expect(depList).toHaveTextContent("STORE-202");
    expect(depList).toHaveTextContent("SITE-C01");
  });

  it("4. Highlights only affected Binding Constraints and leaves unaffected stores unflagged (Regression Test)", () => {
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

    // Affected store: STORE-101 in 2026Q2 is flagged as binding
    const bindingBadge = screen.getByTestId("gantt-binding-badge-STORE-101-2026Q2");
    expect(bindingBadge).toBeInTheDocument();
    expect(bindingBadge).toHaveTextContent("⚠️ Binding");
    const affectedBar = screen.getByTestId("gantt-bar-STORE-101-2026Q2");
    expect(affectedBar).toHaveAttribute("data-is-binding", "true");

    // Unaffected stores: MUST NOT be marked as binding (prevents blanket rawBindingConstraints bug!)
    const unaffectedBar1 = screen.getByTestId("gantt-bar-STORE-202-2026Q1");
    expect(unaffectedBar1).toHaveAttribute("data-is-binding", "false");
    expect(screen.queryByTestId("gantt-binding-badge-STORE-202-2026Q1")).not.toBeInTheDocument();

    const unaffectedBar2 = screen.getByTestId("gantt-bar-STORE-202-2026Q2");
    expect(unaffectedBar2).toHaveAttribute("data-is-binding", "false");
    expect(screen.queryByTestId("gantt-binding-badge-STORE-202-2026Q2")).not.toBeInTheDocument();

    const unaffectedBar3 = screen.getByTestId("gantt-bar-SITE-C02-2026Q4");
    expect(unaffectedBar3).toHaveAttribute("data-is-binding", "false");
    expect(screen.queryByTestId("gantt-binding-badge-SITE-C02-2026Q4")).not.toBeInTheDocument();
  });

  it("5. Provides equivalent table view with complete information parity and view toggle", () => {
    render(
      <PlanGanttChart
        actions={sampleActions}
        quarters={["2026Q1", "2026Q2", "2026Q3", "2026Q4"]}
        bindingConstraints={["max_budget: STORE-101"]}
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

    // Verify binding constraint tag in table for affected entity
    expect(screen.getByTestId("table-binding-tag-STORE-101-2026Q2")).toBeInTheDocument();

    // Toggle back to Gantt view
    const ganttToggleBtn = screen.getByTestId("view-toggle-gantt");
    fireEvent.click(ganttToggleBtn);
    expect(screen.getByTestId("gantt-view-container")).toBeInTheDocument();
  });

  it("6. Renders authoritative policy_id and policy_version without hardcoded defaults", () => {
    const { rerender } = render(
      <PlanGanttChart
        scenarioId="NP-SCENARIO-AUDIT-001"
        scenarioName="旗艦店網核准方案"
        policyId="gov-policy-alpha-001"
        policyVersion="v3.4.1"
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
    expect(policyIdElem).toHaveTextContent("gov-policy-alpha-001");

    // Verify policy_version is explicitly presented
    const policyVersionElem = screen.getByTestId("gantt-policy-version");
    expect(policyVersionElem).toBeInTheDocument();
    expect(policyVersionElem).toHaveTextContent("v3.4.1");

    // Re-render without policy_id/policy_version -> must NOT synthesize fallback strings
    rerender(
      <PlanGanttChart
        scenarioId="NP-SCENARIO-AUDIT-002"
        scenarioName="未指定政策方案"
        actions={sampleActions}
      />
    );

    expect(screen.getByTestId("gantt-policy-id")).toHaveTextContent("—");
    expect(screen.getByTestId("gantt-policy-version")).toHaveTextContent("—");
  });

  it("7. Does not synthesize fake quarters when actions lack quarter metadata", () => {
    // Actions without any quarter metadata (like pure ActionOption.to_dict output)
    const rawActionOptions: PlanGanttActionItem[] = [
      {
        entity_id: "STORE-303",
        action: "IMPROVE",
        budget_cost: 500000,
        expected_gross_margin: 700000,
        risk_score: 0.2,
      },
    ];

    render(
      <PlanGanttChart
        scenarioId="NP-NO-QUARTERS"
        actions={rawActionOptions}
      />
    );

    // In Gantt view, because no quarters exist, empty state is displayed rather than fake Q1..Q4
    expect(screen.getByTestId("gantt-empty-state")).toHaveTextContent("尚無規劃實體或季度行動資料");

    // Switch to Table view: flat list shows the action with '—' quarter
    fireEvent.click(screen.getByTestId("view-toggle-table"));
    expect(screen.getByTestId("table-row-STORE-303-")).toHaveTextContent("STORE-303");
    expect(screen.getByTestId("table-row-STORE-303-")).toHaveTextContent("IMPROVE");
  });

  it("8. Handles empty actions gracefully without errors", () => {
    render(
      <PlanGanttChart
        scenarioId="NP-EMPTY"
        scenarioName="空白規劃方案"
        actions={[]}
      />
    );

    expect(screen.getByTestId("gantt-empty-state")).toHaveTextContent("尚無規劃實體或季度行動資料");
  });

  it("9. Fires onActionClick handler when clicking an action bar", () => {
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

  it("10. Integration: RebalancePanel renders authoritative actions and policy without synthetic fabrication", () => {
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-01",
        storeId: "STORE-REB-01",
        storeName: "中壢站前店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "低效門市評估",
        tone: "watch",
        selectedScenarioId: "SCENARIO-KEEP-01",
        netPlanScenarios: [
          {
            id: "SCENARIO-KEEP-01",
            name: "方案 A: 既有門市改善",
            roi: "18.5%",
            inv: "450K",
            payback: "1.2 年",
            risk: "低",
            time: "2026Q1",
            modelledConstraintClasses: ["CAPITAL"],
            unmodelledConstraintClasses: ["LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL"],
            unmodelled_constraint_classes: ["LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            policy_id: "pol-gov-network-2026",
            policy_version: "v2.0",
            score: 88.5,
            actions: [
              {
                entity_id: "STORE-REB-01",
                entity_name: "中壢站前店",
                quarter: "2026Q1",
                action: "IMPROVE",
                budget_cost: 450000,
                expected_gross_margin: 620000,
                risk_score: 0.25,
              },
            ],
          },
          {
            id: "SCENARIO-EMPTY-02",
            name: "方案 B: 無行動空案",
            roi: "0%",
            inv: "0",
            payback: "—",
            risk: "高",
            time: "—",
            modelledConstraintClasses: ["CAPITAL"],
            unmodelledConstraintClasses: ["LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL"],
            unmodelled_constraint_classes: ["LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
            // Notice: actions is missing/empty!
          },
        ],
      },
    ];

    const { rerender } = render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={vi.fn()}
      />
    );

    // 1. Verify selection section renders PlanGanttChart with authoritative policy metadata
    expect(screen.getByTestId("rebalance-selection-STORE-REB-01")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-policy-id")).toHaveTextContent("pol-gov-network-2026");
    expect(screen.getByTestId("gantt-policy-version")).toHaveTextContent("v2.0");
    expect(screen.getByTestId("gantt-bar-STORE-REB-01-2026Q1")).toHaveTextContent("IMPROVE");

    // 2. Select scenario with NO actions -> verify RebalancePanel does NOT synthesize fake actions!
    const rowWithEmptyScenario: RebalanceQueueRow[] = [
      {
        ...mockRows[0],
        selectedScenarioId: "SCENARIO-EMPTY-02",
      },
    ];

    rerender(
      <RebalancePanel
        rows={rowWithEmptyScenario}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={vi.fn()}
      />
    );

    // Empty state should be shown, NOT synthetic EXIT/MOVE/IMPROVE actions!
    expect(screen.getByTestId("gantt-empty-state")).toHaveTextContent("尚無規劃實體或季度行動資料");
    expect(screen.queryByTestId("gantt-bar-STORE-REB-01-2026Q1")).not.toBeInTheDocument();
    expect(screen.getByTestId("gantt-policy-id")).toHaveTextContent("—");
  });

  it("11. PlanGanttChart renders constraint disclosure section with modelled and unmodelled badges", () => {
    render(
      <PlanGanttChart
        scenarioId="SCENARIO-DISCLOSURE-01"
        scenarioName="NetPlan 限制揭露測試案"
        modelledConstraintClasses={["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"]}
        unmodelledConstraintClasses={["LEASE", "SEQUENCING"]}
      />
    );

    const disclosure = screen.getByTestId("gantt-constraint-disclosure");
    expect(disclosure).toBeInTheDocument();
    expect(disclosure).toHaveTextContent("ODP-FR-NET-002 硬限制揭露");

    const modelledGroup = screen.getByTestId("gantt-modelled-classes");
    expect(modelledGroup).toBeInTheDocument();
    expect(screen.getByTestId("gantt-modelled-CAPITAL")).toHaveTextContent("✓ CAPITAL");
    expect(screen.getByTestId("gantt-modelled-CONSTRUCTION")).toHaveTextContent("✓ CONSTRUCTION");
    expect(screen.getByTestId("gantt-modelled-EQUIPMENT")).toHaveTextContent("✓ EQUIPMENT");
    expect(screen.getByTestId("gantt-modelled-LABOUR")).toHaveTextContent("✓ LABOUR");
    expect(screen.getByTestId("gantt-modelled-COVERAGE")).toHaveTextContent("✓ COVERAGE");
    expect(screen.getByTestId("gantt-modelled-DILUTION")).toHaveTextContent("✓ DILUTION");

    const unmodelledGroup = screen.getByTestId("gantt-unmodelled-classes");
    expect(unmodelledGroup).toBeInTheDocument();
    expect(screen.getByTestId("gantt-unmodelled-LEASE")).toHaveTextContent("⚠️ LEASE");
    expect(screen.getByTestId("gantt-unmodelled-SEQUENCING")).toHaveTextContent("⚠️ SEQUENCING");
  });

  it("12. RebalancePanel renders disclosure badges on all scenarios and blocks submission if blocked classes exist", () => {
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-BLOCKED",
        storeId: "STORE-REB-BLOCKED",
        storeName: "新竹巨城店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "未建模硬限制阻擋測試",
        tone: "watch",
        selectedScenarioId: "SCENARIO-BLOCKED",
        netPlanScenarios: [
          {
            id: "SCENARIO-BLOCKED",
            name: "方案 1: 缺少工程預算約束 (Blocked)",
            roi: "15.0%",
            inv: "1.2M",
            payback: "2.0 年",
            risk: "中",
            time: "2026Q2",
            modelledConstraintClasses: ["CAPITAL"],
            unmodelledConstraintClasses: ["CONSTRUCTION", "LEASE", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL"],
            unmodelled_constraint_classes: ["CONSTRUCTION", "LEASE", "SEQUENCING"],
            blockedConstraintClasses: ["CONSTRUCTION"],
            acknowledgeableConstraintClasses: ["LEASE", "SEQUENCING"],
            disclosurePolicyVersionId: "netplan-constraint-disclosure-policy-v1:tenant-demo",
            score: 75.0,
          },
          {
            id: "SCENARIO-ACK-OK",
            name: "方案 2: 僅缺少租約與時序 (Ack OK)",
            roi: "18.0%",
            inv: "1.0M",
            payback: "1.8 年",
            risk: "低",
            time: "2026Q2",
            modelledConstraintClasses: ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"],
            unmodelledConstraintClasses: ["LEASE", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"],
            unmodelled_constraint_classes: ["LEASE", "SEQUENCING"],
            blockedConstraintClasses: [],
            acknowledgeableConstraintClasses: ["LEASE", "SEQUENCING"],
            disclosurePolicyVersionId: "netplan-constraint-disclosure-policy-v1:tenant-demo",
            score: 82.0,
          },
        ],
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    // 1. Verify card badges on both primary and alternative scenarios
    expect(screen.getByTestId("scenario-modelled-classes-SCENARIO-BLOCKED")).toHaveTextContent("已建模: CAPITAL");
    expect(screen.getByTestId("scenario-unmodelled-classes-SCENARIO-BLOCKED")).toHaveTextContent("未建模: CONSTRUCTION, LEASE, SEQUENCING");
    expect(screen.getByTestId("scenario-blocked-badge-SCENARIO-BLOCKED")).toHaveTextContent("不可豁免阻擋");

    expect(screen.getByTestId("scenario-modelled-classes-SCENARIO-ACK-OK")).toHaveTextContent("已建模: CAPITAL, CONSTRUCTION, EQUIPMENT, LABOUR, COVERAGE, DILUTION");
    expect(screen.getByTestId("scenario-unmodelled-classes-SCENARIO-ACK-OK")).toHaveTextContent("未建模: LEASE, SEQUENCING");
    expect(screen.getByTestId("scenario-ack-required-badge-SCENARIO-ACK-OK")).toHaveTextContent("需具名確認");

    // 2. Selected scenario is SCENARIO-BLOCKED -> Verify blocker alert is rendered
    expect(screen.getByTestId("rebalance-blocked-alert")).toBeInTheDocument();
    expect(screen.getByTestId("rebalance-blocked-alert")).toHaveTextContent("CONSTRUCTION");

    // 3. Verify primary action button is DISABLED and cannot submit
    const primaryButton = screen.getByTestId("rebalance-primary-action");
    expect(primaryButton).toBeDisabled();
    expect(primaryButton).toHaveTextContent("送審（無法送審）");
    fireEvent.click(primaryButton);
    expect(submitReviewMock).not.toHaveBeenCalled();
  });

  it("13. RebalancePanel requires non-empty reason and actor to acknowledge unmodelled classes and submit", () => {
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-ACK",
        storeId: "STORE-REB-ACK",
        storeName: "台南成功店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "未建模具名確認送審測試",
        tone: "watch",
        selectedScenarioId: "SCENARIO-ACK-ONLY",
        netPlanScenarios: [
          {
            id: "SCENARIO-ACK-ONLY",
            name: "方案 A: 推薦遷移案 (Ack Required)",
            roi: "22.5%",
            inv: "850K",
            payback: "1.5 年",
            risk: "低",
            time: "2026Q1",
            modelledConstraintClasses: ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"],
            unmodelledConstraintClasses: ["LEASE", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"],
            unmodelled_constraint_classes: ["LEASE", "SEQUENCING"],
            blockedConstraintClasses: [],
            acknowledgeableConstraintClasses: ["LEASE", "SEQUENCING"],
            disclosurePolicyVersionId: "netplan-constraint-disclosure-policy-v1:tenant-demo",
            score: 91.0,
          },
        ],
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    // 1. Verify acknowledgement form is rendered
    expect(screen.getByTestId("rebalance-acknowledgement-section")).toBeInTheDocument();
    expect(screen.getByTestId("ack-class-item-LEASE")).toHaveTextContent("租約可行性、檔期條件與解約金未於求解器內驗證");
    expect(screen.getByTestId("ack-class-item-SEQUENCING")).toHaveTextContent("多期排程與工程工期先後次序未於模型內限制");

    const reasonInput = screen.getByTestId("acknowledgement-reason-input");
    const actorIdInput = screen.getByTestId("acknowledgement-actor-id-input");
    const receiptInput = screen.getByTestId("acknowledgement-receipt-input");
    const actorInput = screen.getByTestId("acknowledgement-actor-input");
    const primaryButton = screen.getByTestId("rebalance-primary-action");

    // The panel shows which policy version produced this classification, so a
    // reader can tell what rules the split in front of them came from.
    expect(screen.getByTestId("acknowledgement-policy-version")).toHaveTextContent(
      "netplan-constraint-disclosure-policy-v1:tenant-demo"
    );

    // There is no "authorised role" field to type into. Authority is read off
    // the management approval receipt named below, never off the submission.
    expect(screen.queryByTestId("acknowledgement-actor-role-input")).toBeNull();

    // 1. Nothing is pre-ticked: a pre-filled acknowledgement would be a
    //    signature nobody chose to give.
    expect(screen.getByTestId("ack-class-LEASE")).not.toBeChecked();
    expect(screen.getByTestId("ack-class-SEQUENCING")).not.toBeChecked();

    // 2. Initially reason is empty -> CTA is disabled
    expect(primaryButton).toBeDisabled();
    expect(primaryButton).toHaveTextContent("送審（需具名確認）");

    // 3. Typing whitespace only -> CTA remains disabled
    fireEvent.change(reasonInput, { target: { value: "    " } });
    expect(primaryButton).toBeDisabled();

    fireEvent.change(reasonInput, { target: { value: "租約條件已由商務處完成線下簽核；Q1-Q2 時序排程已與工程團隊確認。" } });
    fireEvent.change(actorInput, { target: { value: "張策略長" } });

    // 4. A reason alone is not enough: the principal and the receipt that
    //    establishes their authority are both required.
    expect(primaryButton).toBeDisabled();
    fireEvent.change(actorIdInput, { target: { value: "principal://network-planning-authority" } });
    expect(primaryButton).toBeDisabled();
    fireEvent.change(receiptInput, { target: { value: "receipt-ops-77" } });

    // 5. Still disabled while any disclosed class is unticked.
    expect(primaryButton).toBeDisabled();
    fireEvent.click(screen.getByTestId("ack-class-LEASE"));
    expect(primaryButton).toBeDisabled();
    fireEvent.click(screen.getByTestId("ack-class-SEQUENCING"));

    expect(primaryButton).not.toBeDisabled();
    expect(primaryButton).toHaveTextContent("送審（Rebalance Review）");

    // 6. Submit review -> verify full payload passed to onSubmitReview
    fireEvent.click(primaryButton);
    expect(submitReviewMock).toHaveBeenCalledTimes(1);
    expect(submitReviewMock).toHaveBeenCalledWith("STORE-REB-ACK", {
      reason: "Move scenario selected for Govern approval; relocation remains unexecuted.",
      actorName: "張策略長",
      acknowledgedClasses: ["LEASE", "SEQUENCING"],
      acknowledgementReason: "租約條件已由商務處完成線下簽核；Q1-Q2 時序排程已與工程團隊確認。",
      acknowledgementActorId: "principal://network-planning-authority",
      approvalReceiptId: "receipt-ops-77",
    });
  });

  it("14. RebalancePanel treats an unclassified disclosure as blocking rather than waivable", () => {
    // A payload from a surface with no disclosure policy registered -- or from
    // an older server -- carries the unmodelled set but no split. Guessing that
    // LEASE and SEQUENCING are the waivable ones would put a signature form in
    // front of an operator that the server is going to refuse, and would hold a
    // copy of a versioned governance rule in the console to do it.
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-UNCLASSIFIED",
        storeId: "STORE-REB-UNCLASSIFIED",
        storeName: "桃園藝文店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "未分類揭露",
        tone: "watch",
        selectedScenarioId: "SCENARIO-UNCLASSIFIED",
        netPlanScenarios: [
          {
            id: "SCENARIO-UNCLASSIFIED",
            name: "方案 A: 未附政策分類",
            roi: "12.0%",
            inv: "600K",
            payback: "2.4 年",
            risk: "中",
            time: "2026Q3",
            modelledConstraintClasses: ["CAPITAL"],
            unmodelledConstraintClasses: ["LEASE", "SEQUENCING"],
            modelled_constraint_classes: ["CAPITAL"],
            unmodelled_constraint_classes: ["LEASE", "SEQUENCING"],
            score: 61.0,
          },
        ],
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    expect(screen.getByTestId("scenario-blocked-badge-SCENARIO-UNCLASSIFIED")).toHaveTextContent(
      "LEASE, SEQUENCING"
    );
    expect(screen.queryByTestId("rebalance-acknowledgement-section")).toBeNull();
    expect(screen.getByTestId("rebalance-blocked-alert")).toHaveTextContent(
      "本介面未註冊揭露政策"
    );

    const primaryButton = screen.getByTestId("rebalance-primary-action");
    expect(primaryButton).toBeDisabled();
    fireEvent.click(primaryButton);
    expect(submitReviewMock).not.toHaveBeenCalled();
  });

  it("15. RebalancePanel refuses a scenario that declared no disclosure at all", () => {
    // An undeclared scenario has an empty unmodelled set for the same reason a
    // silent instrument reads zero: nothing was measured. Rendering it as
    // "fully modelled" is the fail-open the disclosure contract exists to stop.
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-UNDECLARED",
        storeId: "STORE-REB-UNDECLARED",
        storeName: "高雄夢時代店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "未申報揭露",
        tone: "watch",
        selectedScenarioId: "SCENARIO-UNDECLARED",
        netPlanScenarios: [
          {
            id: "SCENARIO-UNDECLARED",
            name: "方案 A: 未申報建模範圍",
            roi: "9.0%",
            inv: "400K",
            payback: "3.0 年",
            risk: "高",
            time: "2026Q4",
            modelledConstraintClasses: [],
            unmodelledConstraintClasses: [],
            modelled_constraint_classes: [],
            unmodelled_constraint_classes: [],
            blockedConstraintClasses: [],
            acknowledgeableConstraintClasses: [],
            disclosureUndeclared: true,
            score: 40.0,
          },
        ],
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    expect(screen.getByTestId("rebalance-blocked-alert")).toHaveTextContent(
      "未申報硬限制建模範圍"
    );
    expect(screen.queryByTestId("rebalance-acknowledgement-section")).toBeNull();
    expect(
      screen.getByTestId("scenario-modelled-classes-SCENARIO-UNDECLARED")
    ).toHaveTextContent("（未申報）");
    expect(
      screen.getByTestId("scenario-blocked-badge-SCENARIO-UNDECLARED")
    ).toHaveTextContent("未申報建模範圍");
    expect(
      screen.queryByTestId("scenario-fully-modelled-badge-SCENARIO-UNDECLARED")
    ).toBeNull();

    const primaryButton = screen.getByTestId("rebalance-primary-action");
    expect(primaryButton).toBeDisabled();
    fireEvent.click(primaryButton);
    expect(submitReviewMock).not.toHaveBeenCalled();
  });
  it("16. RebalancePanel stays closed on an empty disclosure even without the server flag", () => {
    // The server now sends disclosureUndeclared for this payload, but the
    // console must not depend on the flag arriving to stay shut: an older API,
    // a dropped field or a surface that never classified would otherwise turn
    // a scenario that named no class at all into an enabled submit button.
    const mockRows: RebalanceQueueRow[] = [
      {
        id: "STORE-REB-NOFLAG",
        storeId: "STORE-REB-NOFLAG",
        storeName: "台南西門店",
        status: "netplanreview",
        statusLabel: "NetPlan 評估中",
        summary: "未申報揭露（無伺服器旗標）",
        tone: "watch",
        selectedScenarioId: "SCENARIO-NOFLAG",
        netPlanScenarios: [
          {
            id: "SCENARIO-NOFLAG",
            name: "方案 A: 未申報建模範圍",
            roi: "9.0%",
            inv: "400K",
            payback: "3.0 年",
            risk: "高",
            time: "2026Q4",
            modelledConstraintClasses: [],
            unmodelledConstraintClasses: [],
            modelled_constraint_classes: [],
            unmodelled_constraint_classes: [],
            blockedConstraintClasses: [],
            acknowledgeableConstraintClasses: [],
            score: 40.0,
          },
        ],
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={mockRows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    expect(screen.getByTestId("rebalance-blocked-alert")).toHaveTextContent(
      "未申報硬限制建模範圍"
    );
    expect(
      screen.getByTestId("scenario-blocked-badge-SCENARIO-NOFLAG")
    ).toHaveTextContent("未申報建模範圍");
    expect(
      screen.queryByTestId("scenario-fully-modelled-badge-SCENARIO-NOFLAG")
    ).toBeNull();
    expect(screen.queryByTestId("rebalance-acknowledgement-section")).toBeNull();

    const primaryButton = screen.getByTestId("rebalance-primary-action");
    expect(primaryButton).toBeDisabled();
    fireEvent.click(primaryButton);
    expect(submitReviewMock).not.toHaveBeenCalled();
  });

  it("17. PlanGanttChart shows an undeclared disclosure rather than hiding it", () => {
    // The section used to be rendered only when at least one class was named,
    // so a plan that disclosed nothing showed no disclosure panel at all --
    // indistinguishable, on screen, from a plan with nothing to disclose.
    render(
      <PlanGanttChart
        scenarioId="SCENARIO-GANTT-UNDECLARED"
        scenarioName="NetPlan 未申報揭露案"
        modelledConstraintClasses={[]}
        unmodelledConstraintClasses={[]}
      />
    );

    const disclosure = screen.getByTestId("gantt-constraint-disclosure");
    expect(disclosure).toBeInTheDocument();
    expect(disclosure).toHaveAttribute("data-disclosure-undeclared", "true");
    expect(screen.getByTestId("gantt-disclosure-undeclared")).toHaveTextContent(
      "未申報硬限制建模範圍"
    );

    // The sentence that turned a missing disclosure into a verified one.
    expect(disclosure).not.toHaveTextContent("全部已建模");
    expect(screen.getByTestId("gantt-unmodelled-undeclared")).toHaveTextContent(
      "未申報 (無法判定)"
    );
    expect(screen.getByTestId("gantt-modelled-undeclared")).toHaveTextContent(
      "未申報 (無法判定)"
    );
  });

  it("18. PlanGanttChart still reads a fully modelled plan as fully modelled", () => {
    // The counterpart to 17: "no unmodelled classes" is a real result when the
    // plan did name what it bound, and must not be relabelled as undeclared.
    render(
      <PlanGanttChart
        scenarioId="SCENARIO-GANTT-COMPLETE"
        scenarioName="NetPlan 全數建模案"
        modelledConstraintClasses={["CAPITAL", "LEASE", "SEQUENCING"]}
        unmodelledConstraintClasses={[]}
      />
    );

    const disclosure = screen.getByTestId("gantt-constraint-disclosure");
    expect(disclosure).toHaveAttribute("data-disclosure-undeclared", "false");
    expect(screen.queryByTestId("gantt-disclosure-undeclared")).toBeNull();
    expect(screen.getByTestId("gantt-unmodelled-classes")).toHaveTextContent(
      "無未建模限制 (全部已建模)"
    );
  });
  it("19. RebalancePanel drives the payload a production CP-SAT solve returns over HTTP", () => {
    // The rows are built from the fixture the Python E2E asserts the FastAPI
    // response against, not from a literal typed here. That is what makes this
    // a test of the console against the real contract rather than against
    // someone's recollection of it: if the backend stops sending a field, the
    // Python test fails; if the console stops handling what is sent, this one
    // does.
    const rows: RebalanceQueueRow[] = [
      {
        id: apiPayload.storeId,
        storeId: apiPayload.storeId,
        storeName: "台北信義店",
        status: apiPayload.status,
        statusLabel: "NetPlan 三案",
        summary: "production CP-SAT solve",
        tone: "watch",
        selectedScenarioId: apiPayload.netPlanScenarios[0].id,
        // Only the presentation fields are supplied here. Everything the
        // disclosure turns on comes from the fixture untouched.
        netPlanScenarios: apiPayload.netPlanScenarios.map((scenario) => ({
          ...scenario,
          roi: "12.0%",
          inv: "420K",
          payback: "2.4 年",
          risk: "中",
          time: "2026Q3",
          score: 830000,
        })),
      },
    ];

    const submitReviewMock = vi.fn();

    render(
      <RebalancePanel
        rows={rows}
        onRequestAvm={vi.fn()}
        onCompleteAvm={vi.fn()}
        onSolveNetPlan={vi.fn()}
        onSelectScenario={vi.fn()}
        onSubmitReview={submitReviewMock}
      />
    );

    const [primary, alternative] = apiPayload.netPlanScenarios;

    // Both the recommendation and the alternative disclose. An operator reads
    // these rows side by side, and an alternative that arrived unclassified
    // would be the one that looked clean.
    for (const scenario of [primary, alternative]) {
      expect(
        screen.getByTestId(`scenario-modelled-classes-${scenario.id}`)
      ).toHaveTextContent("CAPITAL");
      expect(
        screen.getByTestId(`scenario-unmodelled-classes-${scenario.id}`)
      ).toHaveTextContent("LEASE, SEQUENCING");
      expect(
        screen.getByTestId(`scenario-ack-required-badge-${scenario.id}`)
      ).toHaveTextContent("需具名確認");
      expect(
        screen.queryByTestId(`scenario-fully-modelled-badge-${scenario.id}`)
      ).toBeNull();
      expect(screen.queryByTestId(`scenario-blocked-badge-${scenario.id}`)).toBeNull();
    }

    // The six classes the production formulation bound, shown for the selected
    // plan rather than summarised as a count.
    const disclosure = screen.getByTestId("gantt-constraint-disclosure");
    expect(disclosure).toHaveAttribute("data-disclosure-undeclared", "false");
    for (const cls of primary.modelledConstraintClasses) {
      expect(screen.getByTestId(`gantt-modelled-${cls}`)).toHaveTextContent(cls);
    }
    for (const cls of primary.unmodelledConstraintClasses) {
      expect(screen.getByTestId(`gantt-unmodelled-${cls}`)).toHaveTextContent(cls);
    }

    // The two structurally unmodellable classes are offered for signature, and
    // the policy version that permits it is named on screen.
    const ackSection = screen.getByTestId("rebalance-acknowledgement-section");
    expect(ackSection).toBeInTheDocument();
    expect(screen.getByTestId("acknowledgement-policy-version")).toHaveTextContent(
      primary.disclosurePolicyVersionId
    );

    // Nothing is pre-ticked, so the CTA starts closed.
    const primaryButton = screen.getByTestId("rebalance-primary-action");
    expect(primaryButton).toBeDisabled();
    fireEvent.click(primaryButton);
    expect(submitReviewMock).not.toHaveBeenCalled();

    for (const cls of primary.acknowledgeableConstraintClasses) {
      fireEvent.click(screen.getByTestId(`ack-class-${cls}`));
    }
    fireEvent.change(screen.getByTestId("acknowledgement-receipt-input"), {
      target: { value: "receipt-e2e-001" },
    });
    fireEvent.change(screen.getByTestId("acknowledgement-actor-id-input"), {
      target: { value: "principal://network-planning-authority" },
    });
    fireEvent.change(screen.getByTestId("acknowledgement-reason-input"), {
      target: { value: "租約條件已由商務處完成線下簽核；時序排程已與工程團隊確認。" },
    });

    expect(primaryButton).toBeEnabled();
    fireEvent.click(primaryButton);
    expect(submitReviewMock).toHaveBeenCalledTimes(1);

    const [, submitted] = submitReviewMock.mock.calls[0];
    expect(submitted.acknowledgedClasses).toEqual(
      primary.acknowledgeableConstraintClasses
    );
    expect(submitted.approvalReceiptId).toBe("receipt-e2e-001");
    expect(submitted.acknowledgementActorId).toBe(
      "principal://network-planning-authority"
    );
    expect(submitted.acknowledgementReason).toContain("線下簽核");
  });
});
