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
    priority: "high",
    owner: "展店經理",
    sla: "18m",
    entityRef: "SITE-LIVE-1",
    summary: "Review the submitted site override.",
    systemRecommendation: "WAIT",
    evidence: [
      { id: "EV-LIVE-1", label: "SiteScore v4.8", type: "model", state: "ready" },
      { id: "EV-LIVE-2", label: "Dataset 2026-W30", type: "dataset", state: "stale" },
    ],
  }],
  decisions: [{
    id: "DEC-LIVE-1",
    module: "Network",
    item: "SITE-LIVE-0 GO override",
    systemRecommendation: "WAIT",
    finalDecision: "Approved",
    reason: "Lease evidence was independently reviewed.",
    actor: "展店經理",
    decidedAt: "2026-07-23T09:00:00Z",
    model: "sitescore-v4.8",
    datasetSnapshot: "network-2026-W30",
    approvalId: "APR-LIVE-0",
  }],
  auditRows: [{
    id: "AUD-LIVE-1",
    category: "camera",
    timestamp: "2026-07-24T00:10:00Z",
    actor: "Evidence service",
    action: "Evidence opened",
    module: "Network",
    entityRef: "SITE-LIVE-1",
    summary: "Restricted evidence opened for review.",
    correlationId: "corr-live-1",
  }],
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

    fireEvent.click(screen.getByRole("button", { name: "核准" }));
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

  it("matches the Package 10 approval center and switches selected evidence", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);

    expect(await screen.findByText("治理稽核")).toBeInTheDocument();
    expect(screen.getByText("核准、決策、稽核與證據 — 所有處置的可追溯層")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "核准佇列" })).toBeInTheDocument();
    expect(screen.getAllByText("風險 高")).toHaveLength(2);
    expect(screen.getAllByText("待核准")).toHaveLength(2);
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("SiteScore v4.8");

    fireEvent.click(screen.getByRole("button", { name: /Dataset 2026-W30/ }));

    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("EV-LIVE-2");
    expect(screen.getByTestId("governance-selected-evidence")).toHaveTextContent("stale");
  });

  it("requires a durable reason before return or reject", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByRole("button", { name: "退回修改" }));

    expect(screen.getByRole("alert")).toHaveTextContent("退回或駁回理由需至少 10 個字");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders dense decision, audit, evidence package and status surfaces", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<GovernanceWorkspace roleId="ops-lead" />);
    await screen.findByTestId("governance-workspace");

    fireEvent.click(screen.getByTestId("governance-tab-decisions"));
    expect(screen.getByText("系統建議、最終決策與採用證據的不可分割紀錄")).toBeInTheDocument();
    expect(screen.getByText("APR-LIVE-0")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-audit"));
    expect(screen.getByText("隱私敏感")).toBeInTheDocument();
    expect(screen.getByText("corr-live-1")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-evidencePackage"));
    expect(screen.getByRole("button", { name: "產生 Evidence Package" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("governance-tab-statusBoard"));
    expect(screen.getByText("Data Quality 監控")).toBeInTheDocument();
    expect(screen.getByTestId("governance-sla-card")).toBeInTheDocument();
    expect(screen.getByTestId("governance-users-card")).toBeInTheDocument();
  });
});
