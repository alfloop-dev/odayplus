import "@testing-library/jest-dom/vitest";
import { Profiler } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";

vi.mock("../GrowthWorkspace", () => ({
  GrowthWorkspace: () => <div data-testid="granted-growth-workspace" />,
}));
vi.mock("../DesignAlignedWorkspaces", () => ({
  DesignStoreOpsWorkspace: () => <div data-testid="granted-store-workspace" />,
}));

const productionRetryDelays = [...operatorBootstrapRetryPolicy.delaysMs];

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
    // Cold-start retries pause for seconds in production; keep tests fast.
    operatorBootstrapRetryPolicy.delaysMs = productionRetryDelays.map(() => 0);
  });

  afterEach(() => {
    operatorBootstrapRetryPolicy.delaysMs = [...productionRetryDelays];
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it.each([
    ["pm-audit", "growth", "query"],
    ["pm-audit", "growth", "stored"],
    ["expansion-manager", "store", "query"],
    ["expansion-manager", "store", "stored"],
  ] as const)("restores %s's principal-granted %s workspace from %s on refresh", async (roleId, workspace, source) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    window.sessionStorage.setItem("oday.operator.role", roleId);
    window.sessionStorage.setItem("oday.operator.workspace", source === "stored" ? workspace : "today");
    const projectedRole = { ...liveEnvelope.meta.role, id: roleId, allowedWorkspaces: ["today", workspace] };
    let resolveBootstrap!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveBootstrap = resolve; })));
    render(<OperatorConsole searchParams={source === "query" ? { ws: workspace } : {}} />);
    expect(screen.queryByTestId(`granted-${workspace}-workspace`)).toBeNull();
    // No static-persona decision is allowed to overwrite the saved preference
    // while waiting for the principal's verified bootstrap.
    expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe(source === "stored" ? workspace : "today");
    resolveBootstrap(new Response(JSON.stringify({
      ...liveEnvelope,
      meta: { ...liveEnvelope.meta, role: projectedRole },
      navigation: { ...liveEnvelope.navigation, roles: [projectedRole], allowedWorkspaces: ["today", workspace] },
    }), { status: 200 }));
    expect(await screen.findByTestId(`granted-${workspace}-workspace`)).toBeInTheDocument();
    await waitFor(() => expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe(workspace));
  });

  it.each([
    ["pm-audit", "growth"],
    ["expansion-manager", "store"],
  ] as const)("keeps principal-granted %s %s when switching persona", async (roleId, workspace) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const granted = ["today", workspace];
    const nextRole = { ...liveEnvelope.meta.role, id: roleId, label: `Verified ${roleId}`, allowedWorkspaces: granted };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...liveEnvelope,
      navigation: { ...liveEnvelope.navigation, roles: [liveEnvelope.meta.role, nextRole], allowedWorkspaces: granted },
    }), { status: 200 })));
    render(<OperatorConsole searchParams={{ ws: workspace }} />);
    expect(await screen.findByTestId(`granted-${workspace}-workspace`)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Live Operations"));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`Verified ${roleId} Production tenant`) }));
    await waitFor(() => expect(screen.getByTitle(`Verified ${roleId}`)).toBeInTheDocument());
    expect(await screen.findByTestId(`granted-${workspace}-workspace`)).toBeInTheDocument();
    expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe(workspace);
    expect(window.sessionStorage.getItem("oday.operator.role")).toBe(roleId);
  });

  it.each([
    ["the first commit", ["today", "store"], "store"],
    ["the bootstrap commit", ["today", "store"], "store"],
    ["the first commit", ["today"], "today"],
  ] as const)("keeps Store Ops selected at %s only while bootstrap grants %j", async (moment, granted, expected) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    window.history.replaceState({}, "", "/operator");
    const navigation = {
      ...liveEnvelope.navigation,
      roles: [{ ...liveEnvelope.navigation.roles[0], allowedWorkspaces: granted }],
      workspaces: [
        ...liveEnvelope.navigation.workspaces,
        { id: "store", label: "門市營運", shortLabel: "Store", description: "Store Ops", allowed: true },
      ],
      allowedWorkspaces: granted,
    };
    let resolveBootstrap!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveBootstrap = resolve; })));
    const workspaceWrites: string[] = [];
    const setItem = window.sessionStorage.setItem.bind(window.sessionStorage);
    vi.spyOn(window.sessionStorage, "setItem").mockImplementation((key, value) => {
      if (key === "oday.operator.workspace") workspaceWrites.push(value);
      setItem(key, value);
    });
    // Click at the first moment a browser can deliver input after the commit
    // (the static persona offers Store Ops before bootstrap), as the
    // production-built E2E did when the verified bootstrap committed.
    let writesBeforeClick = -1;
    const clickStoreOpsAfterCommit = () => {
      if (writesBeforeClick >= 0) return;
      if (moment === "the bootstrap commit" && !screen.queryByText("Live unresolved")) return;
      const storeOps = screen.queryByRole("button", { name: /門市營運/ });
      if (!storeOps) return;
      writesBeforeClick = workspaceWrites.length;
      storeOps.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    };
    render(
      <Profiler id="operator-console" onRender={clickStoreOpsAfterCommit}>
        <OperatorConsole searchParams={{}} />
      </Profiler>,
    );
    resolveBootstrap(new Response(JSON.stringify({ ...liveEnvelope, navigation }), { status: 200 }));
    if (expected === "store") {
      expect(await screen.findByTestId("granted-store-workspace")).toBeInTheDocument();
      expect(screen.queryByText("Live unresolved")).not.toBeInTheDocument();
      expect(window.location.search).toBe("?ws=store&tab=overview");
      expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe("store");
      // No synchronization may write back the workspace it saw before the click.
      expect(workspaceWrites.slice(writesBeforeClick)).not.toContain("today");
    } else {
      // A selection the verified grants do not include still fails closed.
      expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
      expect(screen.queryByTestId("granted-store-workspace")).toBeNull();
      await waitFor(() => expect(window.sessionStorage.getItem("oday.operator.workspace")).toBe("today"));
    }
    expect(writesBeforeClick).toBeGreaterThanOrEqual(0);
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
    await waitFor(() => {
      expect(gate).toHaveAttribute("data-status", "seed");
    });
    expect(screen.queryByText("Seed KPI")).not.toBeInTheDocument();
  });

  it("shows an error gate when bootstrap cannot be loaded", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole searchParams={{ ws: "store" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => {
      expect(gate).toHaveAttribute("data-status", "error");
    });
    // The shared header is unconditional: the data gate keeps the same chrome.
    expect(screen.getByTestId("operator-topbar-inner")).toBeInTheDocument();
    expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-failure-kind", "network");
    expect(screen.getByRole("button", { name: /今日工作/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /門市營運/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /營運主管/ })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/operator/bootstrap",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(document.querySelector('[data-screen-label="Store Ops 門市營運"]')).not.toBeInTheDocument();
  });

  it("leaves loading when the bootstrap BFF returns an upstream timeout", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "WEB_API_UPSTREAM_TIMEOUT",
              retryable: true,
            },
          }),
          {
            status: 504,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    render(<OperatorConsole searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => {
      expect(gate).toHaveAttribute("data-status", "error");
    });
    expect(screen.queryByText("營運資料載入中")).not.toBeInTheDocument();
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
    await waitFor(() => {
      expect(gate).toHaveAttribute("data-status", "empty");
    });
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
