import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";

// Existing business assertions keep their transport premise: a current
// canonical session precedes protected shell reads. No principal/grant is
// derived from this unit-test response.
function stubAuthenticatedFetch(read: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => String(input) === "/auth/session"
    ? Promise.resolve(new Response(JSON.stringify({ subject: "unit-shell-account", expiresAt: Math.floor(Date.now() / 1000) + 3600 }), {
      status: 200, headers: { "Content-Type": "application/json" },
    })) : read(input, init)));
}

const seedEnvelope = {
  meta: {
    source: "operator-shell-api-envelope",
    role: {
      id: "ops-lead",
      label: "營運主管",
      subtitle: "Operations",
      allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
      heroName: "Seed Operator",
    },
    counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 1 },
  },
  navigation: {
    roles: [],
    workspaces: [
      { id: "today", label: "Today", shortLabel: "Today", description: "Today", allowed: true },
    ],
    allowedWorkspaces: ["today"],
  },
  header: {
    counts: { approvals: 0, critical: 0, notifications: 0, search: 0, taskCenter: 1 },
  },
  today: {
    hero: { name: "Seed Operator", roleLabel: "營運主管", scope: "Seed scope", dateLabel: "Seed date" },
    kpis: [{ label: "Seed KPI", value: "99" }],
    queue: [],
    decisions: [],
    riskRows: [],
    auditFeed: [],
  },
  approvals: [],
  auditFeed: [],
  decisions: [],
  kpis: [{ label: "Seed KPI", value: "99" }],
  notifications: [],
  riskRows: [],
  search: { count: 0, items: [] },
  workQueue: [],
};

const liveEnvelope = {
  meta: {
    source: "operator-shell-production",
    dataMode: "live",
    role: {
      id: "ops-lead",
      label: "Live Operations",
      subtitle: "Production tenant",
      allowedWorkspaces: ["today"],
      heroName: "Live Operator",
    },
    counts: { approvals: 0, critical: 1, notifications: 0, search: 0, taskCenter: 1 },
  },
  navigation: {
    roles: [{
      id: "ops-lead",
      label: "Live Operations",
      subtitle: "Production tenant",
      allowedWorkspaces: ["today"],
    }],
    workspaces: [
      { id: "today", label: "Today", shortLabel: "Today", description: "Live queue", allowed: true },
    ],
    allowedWorkspaces: ["today"],
  },
  header: {
    counts: { approvals: 0, critical: 1, notifications: 0, search: 0, taskCenter: 1 },
  },
  today: {
    hero: {
      name: "Live Operator",
      roleLabel: "Live Operations",
      scope: "Tenant production scope",
      dateLabel: "2026-07-24",
    },
    kpis: [{ label: "Live unresolved", value: "1", tone: "danger" }],
    queue: [{
      id: "LIVE-ISSUE-1",
      title: "Production issue",
      workspace: "today",
      owner: "Live owner",
      status: "OPEN",
      meta: "SLA 30m",
      time: "30m",
      tone: "danger",
      target: { workspace: "today" },
    }],
    decisions: [],
    riskRows: [],
    auditFeed: [],
  },
  notifications: [],
  search: { count: 0, items: [] },
};

const envelopes = { live: liveEnvelope, seed: seedEnvelope };
const retryDelays = [...operatorBootstrapRetryPolicy.delaysMs];
beforeEach(() => {
  window.sessionStorage.clear(); operatorBootstrapRetryPolicy.delaysMs = retryDelays.map(() => 0);
  vi.stubEnv("ODP_PRODUCT_MODE", "poc"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
});
afterEach(() => { operatorBootstrapRetryPolicy.delaysMs = [...retryDelays]; cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function reads(body: unknown, status = 200) { stubAuthenticatedFetch( vi.fn().mockImplementation(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }))); }
describe("Operator API banner accurately labels the permitted data lane", () => {
  it("labels an admitted API payload as development/acceptance data in POC", async () => {
    reads(envelopes.live); render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByText("Live unresolved");
    expect(screen.getByText("開發／驗收 API 資料")).toBeInTheDocument();
    expect(screen.queryByText("目前顯示的是經驗證的正式營運資料。")).not.toBeInTheDocument();
  });
  it("labels admitted API data as development/acceptance in the explicit isolated E2E lane", async () => {
    vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("ODP_DEPLOY_ENV", "e2e"); vi.stubEnv("ODP_E2E_MODE", "true"); vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "false");
    reads(envelopes.live); render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByText("Live unresolved");
    expect(screen.getByText("開發／驗收 API 資料")).toBeInTheDocument();
    expect(screen.queryByText("目前顯示的是經驗證的正式營運資料。")).not.toBeInTheDocument();
  });
  it("retains the production live-data copy after the production data admission", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); reads(envelopes.live);
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByText("Live unresolved");
    expect(screen.getByText("目前顯示的是經驗證的正式營運資料。")).toBeInTheDocument();
  });
  it("keeps actual seed data explicitly labelled as a local fixture", async () => {
    reads(envelopes.seed); render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByText("Local fixture mode");
    expect(screen.getByText("本機示範資料，僅供開發與測試使用。")).toBeInTheDocument();
    expect(screen.queryByText("開發／驗收 API 資料")).not.toBeInTheDocument();
  });
  it("retains production auth-denied admission and cannot label a rejected payload as live", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); reads({ detail: "denied" }, 403);
    const transport = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (...args: Parameters<typeof fetch>) => {
      await new Promise((resolve) => setTimeout(resolve, 25));
      return transport(...args);
    }));
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"));
    expect(screen.queryByText("目前顯示的是經驗證的正式營運資料。")).not.toBeInTheDocument();
    expect(screen.queryByText("Live unresolved")).not.toBeInTheDocument();
  });
});
