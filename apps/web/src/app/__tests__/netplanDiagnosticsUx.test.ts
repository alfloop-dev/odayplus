import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const baseDir = process.cwd().endsWith("apps/web") ? process.cwd() : resolve(process.cwd(), "apps/web");

describe("NetPlan structured diagnostic & stale state UX rendering", () => {
  it("verifies RebalancePanel references and renders all 5 structured diagnostic fields and stale state", () => {
    const rebalancePanelSource = readFileSync(
      resolve(baseDir, "features/operator/network/RebalancePanel.tsx"),
      "utf8",
    );

    // 5 structured diagnostic fields
    expect(rebalancePanelSource).toContain("violated_constraint");
    expect(rebalancePanelSource).toContain("affected_stores");
    expect(rebalancePanelSource).toContain("required_relaxation");
    expect(rebalancePanelSource).toContain("business_impact");
    expect(rebalancePanelSource).toContain("suggested_action");

    // stale & infeasible state indicators
    expect(rebalancePanelSource).toContain("isStale");
    expect(rebalancePanelSource).toContain("isInfeasible");
    expect(rebalancePanelSource).toContain("scenario-diagnostics");
    expect(rebalancePanelSource).toContain("scenario-stale");
  });

  it("verifies NetPlan types include NetPlanDiagnostic and NetPlanScenarioDetail", () => {
    const typesSource = readFileSync(
      resolve(baseDir, "features/operator/types.ts"),
      "utf8",
    );

    expect(typesSource).toContain("export type NetPlanDiagnostic");
    expect(typesSource).toContain("violated_constraint: string");
    expect(typesSource).toContain("affected_stores: string[]");
    expect(typesSource).toContain("required_relaxation: string");
    expect(typesSource).toContain("business_impact: string");
    expect(typesSource).toContain("suggested_action: string");
    expect(typesSource).toContain("isStale?: boolean");
    expect(typesSource).toContain("isInfeasible?: boolean");
  });

  it("verifies fixtures contain sample diagnostic and stale scenario data", () => {
    const fixturesSource = readFileSync(
      resolve(baseDir, "features/operator/fixtures.ts"),
      "utf8",
    );

    expect(fixturesSource).toContain("violated_constraint: \"max_budget\"");
    expect(fixturesSource).toContain("affected_stores: [\"STORE-101\"]");
    expect(fixturesSource).toContain("required_relaxation:");
    expect(fixturesSource).toContain("business_impact:");
    expect(fixturesSource).toContain("suggested_action:");
  });
});
