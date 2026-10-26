/**
 * The console's half of the ODP-FR-NET-002 partition gate.
 *
 * The previous reading only refused a payload that named *nothing*. Everything
 * here is about the shape that got through: a payload naming one of the eight
 * classes and going silent about the other seven. It reached the console as a
 * plan with one verified constraint and -- because the unmodelled list was
 * empty -- no outstanding exposure at all, which is the strongest claim the
 * screen can make, made on behalf of a solve that never made it.
 *
 * These are console tests, not gate tests. The server refuses such a submission
 * independently (`tests/integration/test_netplan_disclosure_ui_e2e.py`). What is
 * asserted here is that the screen does not first tell the operator the plan is
 * clean, because an operator who has been shown "全部已建模" has already been
 * misinformed whether or not the submit button later fails.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanGanttChart } from "../PlanGanttChart";
import { RebalancePanel } from "../RebalancePanel";
import {
  NETPLAN_CONSTRAINT_CLASSES,
  readConstraintDisclosure,
} from "../constraintDisclosure";
import type { RebalanceQueueRow } from "../../networkFindAreasViewModel";
import type { ConstraintClass, NetPlanScenarioDetail } from "../../types";

afterEach(cleanup);

const ALL_CLASSES = [...NETPLAN_CONSTRAINT_CLASSES] as ConstraintClass[];
const MODELLED_ONLY_CAPITAL: ConstraintClass[] = ["CAPITAL"];
const UNMODELLED_REST: ConstraintClass[] = [
  "LEASE",
  "CONSTRUCTION",
  "EQUIPMENT",
  "LABOUR",
  "COVERAGE",
  "DILUTION",
  "SEQUENCING",
];

function scenario(
  overrides: Partial<NetPlanScenarioDetail> = {},
): NetPlanScenarioDetail {
  return {
    id: "move",
    name: "Move (移轉新址)",
    roi: "ROI 18%",
    score: 71,
    inv: "NT$2.4M",
    payback: "26 個月",
    risk: "中高",
    time: "120 天",
    modelVersion: "netplan-v3",
    snapshotId: "FS-1",
    modelledConstraintClasses: MODELLED_ONLY_CAPITAL,
    modelled_constraint_classes: MODELLED_ONLY_CAPITAL,
    unmodelledConstraintClasses: UNMODELLED_REST,
    unmodelled_constraint_classes: UNMODELLED_REST,
    ...overrides,
  };
}

function row(scenarios: NetPlanScenarioDetail[]): RebalanceQueueRow {
  return {
    id: "RB-801",
    storeId: "RB-801",
    storeName: "南港三重店",
    status: "netplanreview",
    statusLabel: "NetPlan Review",
    summary: "低效門市重配",
    tone: "watch",
    selectedScenarioId: scenarios[0]?.id,
    netPlanScenarios: scenarios,
  };
}

function renderPanel(scenarios: NetPlanScenarioDetail[]) {
  return render(
    <RebalancePanel
      onCompleteAvm={vi.fn()}
      onRequestAvm={vi.fn()}
      onSelectScenario={vi.fn()}
      onSolveNetPlan={vi.fn()}
      onSubmitReview={vi.fn()}
      rows={[row(scenarios)]}
    />,
  );
}

describe("readConstraintDisclosure", () => {
  it("accepts a disclosure that accounts for all eight classes exactly once", () => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: MODELLED_ONLY_CAPITAL,
      unmodelledConstraintClasses: UNMODELLED_REST,
    });

    expect(reading.undeclared).toBe(false);
    expect(reading.defect).toBeNull();
    expect(reading.modelled).toEqual(["CAPITAL"]);
    expect([...reading.modelled, ...reading.unmodelled].sort()).toEqual(
      [...ALL_CLASSES].sort(),
    );
  });

  /**
   * The defect the reopen names. `unmodelled: []` next to a populated modelled
   * list is not "no exposure" -- it is seven questions nobody answered.
   */
  it("refuses a partial partition rather than reading the gap as no exposure", () => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: MODELLED_ONLY_CAPITAL,
      unmodelledConstraintClasses: [],
    });

    expect(reading.undeclared).toBe(true);
    expect(reading.defect).toBe("incomplete");
    expect(reading.defectClasses.sort()).toEqual([...UNMODELLED_REST].sort());
    // Emptied, not preserved. A subset the console will not vouch for must not
    // be handed to a badge that reads as a verification claim.
    expect(reading.modelled).toEqual([]);
    expect(reading.unmodelled).toEqual([]);
  });

  it.each([
    {
      label: "a class claimed on both sides",
      modelled: ["CAPITAL", "LEASE"],
      unmodelled: [
        "LEASE",
        "CONSTRUCTION",
        "EQUIPMENT",
        "LABOUR",
        "COVERAGE",
        "DILUTION",
        "SEQUENCING",
      ],
      defect: "overlapping",
      defectClasses: ["LEASE"],
    },
    {
      label: "a class named twice on one side",
      modelled: ["CAPITAL", "CAPITAL"],
      unmodelled: UNMODELLED_REST,
      defect: "repeated",
      defectClasses: ["CAPITAL"],
    },
    {
      label: "a class the requirement does not define",
      modelled: ["CAPITAL", "WEATHER"],
      unmodelled: UNMODELLED_REST,
      defect: "unknown-class",
      defectClasses: ["WEATHER"],
    },
    {
      label: "both halves empty",
      modelled: [],
      unmodelled: [],
      defect: "absent",
      defectClasses: [],
    },
  ])("refuses $label", ({ modelled, unmodelled, defect, defectClasses }) => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: modelled,
      unmodelledConstraintClasses: unmodelled,
    });

    expect(reading.undeclared).toBe(true);
    expect(reading.defect).toBe(defect);
    expect(reading.defectClasses.sort()).toEqual([...defectClasses].sort());
    expect(reading.modelled).toEqual([]);
    expect(reading.unmodelled).toEqual([]);
  });

  it("refuses a half that is not a list at all", () => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: "CAPITAL" as unknown as string[],
      unmodelledConstraintClasses: UNMODELLED_REST,
    });

    expect(reading.undeclared).toBe(true);
    expect(reading.defect).toBe("malformed");
  });

  /**
   * The server sends this flag when it could not reconcile the row against the
   * canonical solve -- a reason the payload itself cannot show. Honouring it is
   * how the console stays at least as closed as the gate.
   */
  it("honours the server's own undeclared verdict over a well-formed payload", () => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: MODELLED_ONLY_CAPITAL,
      unmodelledConstraintClasses: UNMODELLED_REST,
      disclosureUndeclared: true,
    });

    expect(reading.undeclared).toBe(true);
    expect(reading.defect).toBe("unverified");
    expect(reading.modelled).toEqual([]);
  });

  /**
   * When the payload is visibly broken *and* the server flagged it, the payload
   * is what the operator can act on. Reporting "the server said no" would send
   * them to ask the server why, about a row that names four of eight classes.
   */
  it("names the payload's own defect ahead of the server's verdict", () => {
    const reading = readConstraintDisclosure({
      modelledConstraintClasses: MODELLED_ONLY_CAPITAL,
      unmodelledConstraintClasses: [],
      disclosureUndeclared: true,
    });

    expect(reading.undeclared).toBe(true);
    expect(reading.defect).toBe("incomplete");
  });
});

describe("PlanGanttChart constraint disclosure", () => {
  it("renders a partial disclosure as undeclared, never as fully modelled", () => {
    render(
      <PlanGanttChart
        scenarioId="SCENARIO-1"
        scenarioName="Move"
        modelledConstraintClasses={MODELLED_ONLY_CAPITAL}
        unmodelledConstraintClasses={[]}
        actions={[]}
      />,
    );

    const section = screen.getByTestId("gantt-constraint-disclosure");
    expect(section).toHaveAttribute("data-disclosure-undeclared", "true");
    expect(section).toHaveAttribute("data-disclosure-defect", "incomplete");
    expect(screen.getByTestId("gantt-disclosure-undeclared")).toBeInTheDocument();
    // The exact sentence the partial payload used to produce.
    expect(section).not.toHaveTextContent("無未建模限制 (全部已建模)");
    // And the one class it did name is not shown as verified.
    expect(screen.queryByTestId("gantt-modelled-CAPITAL")).not.toBeInTheDocument();
    expect(screen.getByTestId("gantt-modelled-undeclared")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-disclosure-defect")).toHaveTextContent("LEASE");
  });

  it("still renders a complete disclosure as the measurement it is", () => {
    render(
      <PlanGanttChart
        scenarioId="SCENARIO-1"
        scenarioName="Move"
        modelledConstraintClasses={MODELLED_ONLY_CAPITAL}
        unmodelledConstraintClasses={UNMODELLED_REST}
        actions={[]}
      />,
    );

    const section = screen.getByTestId("gantt-constraint-disclosure");
    expect(section).toHaveAttribute("data-disclosure-undeclared", "false");
    expect(section).toHaveAttribute("data-disclosure-defect", "none");
    expect(screen.getByTestId("gantt-modelled-CAPITAL")).toBeInTheDocument();
    expect(screen.getByTestId("gantt-unmodelled-SEQUENCING")).toBeInTheDocument();
  });
});

describe("RebalancePanel constraint disclosure", () => {
  it("does not badge a partial disclosure as fully modelled", () => {
    renderPanel([
      scenario({
        unmodelledConstraintClasses: [],
        unmodelled_constraint_classes: [],
      }),
    ]);

    expect(
      screen.queryByTestId("scenario-fully-modelled-badge-move"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("scenario-blocked-badge-move")).toHaveTextContent(
      "揭露不完整",
    );
    // The class it named is not restated as a verification claim.
    expect(
      screen.getByTestId("scenario-modelled-classes-move"),
    ).toHaveTextContent("（未申報）");
  });

  /**
   * The chart is handed the payload rather than the panel's reading of it, so
   * that it can name which classes are missing -- and `disclosureUndeclared`
   * carries the panel's verdict alongside, so it can never end up the more
   * permissive of the two.
   */
  it("passes the panel's verdict down to the chart with the payload", () => {
    renderPanel([
      scenario({
        unmodelledConstraintClasses: [],
        unmodelled_constraint_classes: [],
      }),
    ]);

    const section = screen.getByTestId("gantt-constraint-disclosure");
    expect(section).toHaveAttribute("data-disclosure-undeclared", "true");
    expect(section).toHaveAttribute("data-disclosure-defect", "incomplete");
    expect(screen.getByTestId("gantt-disclosure-defect")).toHaveTextContent(
      "SEQUENCING",
    );
    expect(screen.queryByTestId("gantt-modelled-CAPITAL")).not.toBeInTheDocument();
  });

  it("blocks submission on a partial disclosure and says why", () => {
    renderPanel([
      scenario({
        unmodelledConstraintClasses: [],
        unmodelled_constraint_classes: [],
      }),
    ]);

    const alert = screen.getByTestId("rebalance-blocked-alert");
    expect(alert).toBeInTheDocument();
    expect(screen.getByTestId("rebalance-disclosure-defect")).toHaveTextContent(
      "SEQUENCING",
    );
    const submit = screen.getByRole("button", { name: /送審（無法送審）/ });
    expect(submit).toBeDisabled();
    // Not "blocked by ()". An undeclared plan is blocked by the absence.
    expect(alert.textContent).not.toContain("Blocked: )");
  });

  /**
   * The acknowledgement form is the one route by which an unmodelled class
   * reaches Govern. Offering it against a payload whose classes are unknown
   * would collect a signature for an exposure nobody can enumerate.
   */
  it("offers no acknowledgement form while the disclosure cannot be read", () => {
    renderPanel([
      scenario({
        unmodelledConstraintClasses: [],
        unmodelled_constraint_classes: [],
      }),
    ]);

    expect(
      screen.queryByTestId("rebalance-acknowledgement-section"),
    ).not.toBeInTheDocument();
  });

  it("still offers the acknowledgement form on a complete disclosure", () => {
    renderPanel([
      scenario({
        blockedConstraintClasses: [],
        acknowledgeableConstraintClasses: ["LEASE", "SEQUENCING"],
        modelledConstraintClasses: [
          "CAPITAL",
          "CONSTRUCTION",
          "EQUIPMENT",
          "LABOUR",
          "COVERAGE",
          "DILUTION",
        ],
        modelled_constraint_classes: [
          "CAPITAL",
          "CONSTRUCTION",
          "EQUIPMENT",
          "LABOUR",
          "COVERAGE",
          "DILUTION",
        ],
        unmodelledConstraintClasses: ["LEASE", "SEQUENCING"],
        unmodelled_constraint_classes: ["LEASE", "SEQUENCING"],
      }),
    ]);

    expect(
      screen.getByTestId("rebalance-acknowledgement-section"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("ack-class-LEASE")).toBeInTheDocument();
  });
});
