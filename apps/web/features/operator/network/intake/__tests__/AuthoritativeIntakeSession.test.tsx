import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistedIntakeSection } from "../AssistedIntakeSection";
import { unavailableIntakeOperatorSession } from "../intakeOperatorSession";
import { ExpansionWorkspace } from "../../../../expansion/ExpansionWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("durable intake authoritative session boundary", () => {
  it("fails closed without bootstrap authorization context", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <AssistedIntakeSection
        operatorSession={unavailableIntakeOperatorSession()}
      />,
    );

    expect(
      screen.getByTestId("intake-authoritative-session-denied"),
    ).toHaveTextContent("AUTHORIZATION_CONTEXT_UNAVAILABLE");
    expect(screen.queryByTestId("intake-add-button")).not.toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces backend denial and masking reason codes", () => {
    render(
      <AssistedIntakeSection
        operatorSession={{
          ...unavailableIntakeOperatorSession(),
          status: "denied",
          denialReasonCode: "ASSIGNMENT_SCOPE_DENIED",
          maskingReasonCode: "FIELD_MASKED",
          source: "operator-bootstrap",
        }}
      />,
    );

    const boundary = screen.getByTestId("intake-authoritative-session-denied");
    expect(boundary).toHaveTextContent("ASSIGNMENT_SCOPE_DENIED");
    expect(boundary).toHaveTextContent("FIELD_MASKED");
  });

  it("does not infer a manager when no authoritative session or explicit legacy actor exists", () => {
    render(<AssistedIntakeSection />);

    expect(
      screen.getByTestId("intake-authoritative-session-unavailable"),
    ).toHaveTextContent("AUTHORIZATION_CONTEXT_UNAVAILABLE");
    expect(screen.queryByText("展店經理")).not.toBeInTheDocument();
  });

  it("ignores a role query parameter on the durable Expansion route", () => {
    render(
      <ExpansionWorkspace
        operatorSession={unavailableIntakeOperatorSession()}
        searchParams={{ role: "expansion-manager" }}
        view="listings"
      />,
    );

    expect(
      screen.getByTestId("intake-authoritative-session-denied"),
    ).toHaveTextContent("AUTHORIZATION_CONTEXT_UNAVAILABLE");
    expect(screen.queryByTestId("intake-add-button")).not.toBeInTheDocument();
  });
});
