import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";

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

describe("OperatorConsole production data gate", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("blocks seed shell data instead of rendering a workspace fixture", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(seedEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "seed");
    expect(screen.queryByText("Seed KPI")).not.toBeInTheDocument();
  });

  it("shows an error gate when bootstrap cannot be loaded", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<OperatorConsole searchParams={{ ws: "store" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "error");
    expect(document.querySelector('[data-screen-label="Store Ops 門市營運"]')).not.toBeInTheDocument();
  });

  it("builds the production Today state only from a complete live API payload", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(liveEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    expect(screen.getByText("Production issue")).toBeInTheDocument();
    expect(screen.getByText(/Live Operator/)).toBeInTheDocument();
    expect(screen.queryByText("Seed KPI")).not.toBeInTheDocument();
    expect(screen.queryByText(/林承翰/)).not.toBeInTheDocument();
    expect(screen.queryByText("TASK-401")).not.toBeInTheDocument();
  });

  it("blocks a nominally live but incomplete API payload", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        ...liveEnvelope,
        today: {
          kpis: [{ label: "Partial live KPI", value: "1" }],
        },
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    expect(gate).toHaveAttribute("data-status", "empty");
    expect(screen.queryByText("Partial live KPI")).not.toBeInTheDocument();
    expect(screen.queryByText(/林承翰/)).not.toBeInTheDocument();
  });

  it("blocks seed Task Center payloads in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operator/bootstrap") {
        return new Response(JSON.stringify(liveEnvelope), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/v1/operator/shell/tasks") {
        return new Response(JSON.stringify({
          source: "fixture-task-center",
          items: [{
            id: "TASK-SEED",
            title: "Synthetic production task",
            status: "open",
          }],
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    expect(await screen.findByText("Production issue")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("operator-task-center-button"));

    expect(await screen.findByTestId("operator-task-center-unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Synthetic production task")).not.toBeInTheDocument();
    expect(screen.queryByText(/TASK-401/)).not.toBeInTheDocument();
  });

  it("uses the existing shell task endpoint and retains local fixture mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/operator/bootstrap") {
        return new Response(JSON.stringify(seedEnvelope), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/v1/operator/shell/tasks") {
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/operator/bootstrap",
      expect.any(Object),
    ));

    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/operator/shell/tasks")).toBe(true),
    );
    expect(screen.getByText(/TASK-401/)).toBeInTheDocument();
  });
});
