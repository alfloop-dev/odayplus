import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GovernanceWorkspace } from "../GovernanceWorkspace";

const snapshot = {
  approvals: [{
    id: "APR-LIVE-1",
    module: "Network",
    title: "Review candidate",
    requestor: "Expansion",
    submittedAt: "2026-07-24T00:00:00Z",
    status: "pending",
    systemRecommendation: "WAIT",
    evidence: [],
  }],
  decisions: [],
  auditRows: [],
  evidencePackages: [],
  statusBoard: {
    dataQuality: [{ source: "Listings", status: "ready", good: true, note: "live" }],
    models: [],
    connectors: [],
    sla: [],
    users: [],
    runbooks: [],
  },
  source: "operator-governance-production",
};

describe("GovernanceWorkspace high-risk failures", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("does not create a local decision or evidence package after API failure", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/snapshot") && (!init?.method || init.method === "GET")) {
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "failed" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<GovernanceWorkspace roleId="ops-lead" />);
    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("決策未送出（API 無法連線）")).toBeInTheDocument();
    expect(screen.queryByText(/已核准決策/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    fireEvent.click(screen.getByTestId("governance-export-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("evidence-package-result")).not.toBeInTheDocument(),
    );
  });

  it("blocks seed governance payloads instead of rendering local approvals", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        ...snapshot,
        source: "fixture-governance-replay",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "seed");
    expect(screen.queryByTestId("governance-workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("Close escalated service issue")).not.toBeInTheDocument();
  });

  it("retains governance fixtures in local mode when the API is unavailable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByTestId("governance-workspace")).toBeInTheDocument();
    expect(screen.getAllByText("Close escalated service issue").length).toBeGreaterThan(0);
  });
});
