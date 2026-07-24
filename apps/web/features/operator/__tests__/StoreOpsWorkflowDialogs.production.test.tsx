import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoreOpsWorkflowDialogs } from "../StoreOpsWorkflowDialogs";
import type { StoreOpsWorkflowIssue } from "../storeOpsWorkflowTypes";

const liveIssue: StoreOpsWorkflowIssue = {
  createdAt: "2026-07-24T08:00:00Z",
  evidenceIds: [],
  id: "ISS-LIVE-001",
  ownerName: "Live Operator",
  ownerRoleId: "opsLead",
  severity: "high",
  slaDueAt: "2026-07-24T10:00:00Z",
  source: "multiSignal",
  status: "new",
  storeId: "store-live-1",
  storeName: "Live Store",
  summary: "Persisted production issue",
  title: "Live issue",
  updatedAt: "2026-07-24T08:05:00Z",
};

describe("StoreOpsWorkflowDialogs production guards", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("does not substitute the fallback issue when the API record is absent", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(
      <StoreOpsWorkflowDialogs
        activeDialog="triage"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByTestId("store-ops-workflow-unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Local fallback store ops issue")).not.toBeInTheDocument();
    expect(screen.queryByText("Fallback Store")).not.toBeInTheDocument();
  });

  it("removes demo fast-forward controls from a live production issue", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(
      <StoreOpsWorkflowDialogs
        activeDialog="triage"
        issue={liveIssue}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Live issue")).toBeInTheDocument();
    expect(screen.queryByText("Demo fast-forward")).not.toBeInTheDocument();
  });
});
