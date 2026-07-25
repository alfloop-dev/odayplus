import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExpansionWorkspace } from "../ExpansionWorkspace";

vi.mock("../../operator/network/intake/AssistedIntakeSection.tsx", () => ({
  AssistedIntakeSection: ({
    activeRoleId,
    selectedHeatZoneId,
  }: {
    activeRoleId: string;
    selectedHeatZoneId?: string;
  }) => (
    <section
      data-role={activeRoleId}
      data-heat-zone={selectedHeatZoneId}
      data-testid="production-assisted-intake"
    />
  ),
}));

afterEach(cleanup);

describe("production Expansion composition", () => {
  it("mounts Assisted Intake independently of the listing radar binding", () => {
    render(
      <ExpansionWorkspace
        isProduction
        searchParams={{ heatZone: "heat-zone-live-1", role: "expansion-manager" }}
        view="listings"
      />,
    );

    expect(screen.getByTestId("production-assisted-intake")).toHaveAttribute(
      "data-role",
      "expansion-manager",
    );
    expect(screen.getByTestId("production-assisted-intake")).toHaveAttribute(
      "data-heat-zone",
      "heat-zone-live-1",
    );
    expect(screen.getByTestId("exp-production-data-state")).toHaveAttribute(
      "data-state",
      "unconfigured",
    );
  });
});
