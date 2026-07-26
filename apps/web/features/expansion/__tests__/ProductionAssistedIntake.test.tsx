import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExpansionWorkspace } from "../ExpansionWorkspace";

vi.mock("../../operator/network/intake/AssistedIntakeSection.tsx", () => ({
  AssistedIntakeSection: ({
    activeRoleId,
    initialDialog,
    initialSelectedId,
    selectedHeatZoneId,
  }: {
    activeRoleId: string;
    initialDialog?: string;
    initialSelectedId?: string;
    selectedHeatZoneId?: string;
  }) => (
    <section
      data-dialog={initialDialog}
      data-role={activeRoleId}
      data-selected={initialSelectedId}
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

  it("forwards the durable intake route state without requiring browser query params", () => {
    render(
      <ExpansionWorkspace
        isProduction
        searchParams={{
          dialog: "detail",
          selected: "IN-3001",
          role: "expansion-manager",
        }}
        view="listings"
      />,
    );

    expect(screen.getByTestId("production-assisted-intake")).toHaveAttribute(
      "data-selected",
      "IN-3001",
    );
    expect(screen.getByTestId("production-assisted-intake")).toHaveAttribute(
      "data-dialog",
      "detail",
    );
  });
});
