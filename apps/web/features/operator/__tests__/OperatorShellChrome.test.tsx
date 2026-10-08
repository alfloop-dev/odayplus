import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole } from "../OperatorConsole";

const liveEnvelope = {
  meta: {
    source: "operator-shell-production",
    dataMode: "live",
    role: {
      id: "ops-lead",
      label: "Live Operations",
      subtitle: "Production tenant",
      allowedWorkspaces: ["today", "store"],
      heroName: "Live Operator",
    },
    counts: { approvals: 2, critical: 1, notifications: 3, search: 0, taskCenter: 1 },
  },
  navigation: {
    roles: [{
      id: "ops-lead",
      label: "Live Operations",
      subtitle: "Production tenant",
      allowedWorkspaces: ["today", "store"],
    }],
    workspaces: [
      { id: "today", label: "Today", shortLabel: "Today", description: "Live queue", allowed: true },
      { id: "store", label: "Store", shortLabel: "Store", description: "Store ops", allowed: true },
    ],
    allowedWorkspaces: ["today", "store"],
  },
  header: {
    counts: { approvals: 2, critical: 1, notifications: 3, search: 0, taskCenter: 1 },
  },
  today: {
    hero: {
      name: "Live Operator",
      roleLabel: "Live Operations",
      scope: "Tenant production scope",
      dateLabel: "2026-10-08",
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

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Class structure of the shared header, independent of counts and labels. */
function headerSignature() {
  const inner = screen.getByTestId("operator-topbar-inner");
  const header = inner.parentElement as HTMLElement;
  return {
    consoleClass: screen.getByTestId("operator-console").className,
    headerClass: header.className,
    innerClass: inner.className,
    regions: Array.from(inner.children).map((child) => child.className),
    badge: within(header).getByTestId("operator-environment-badge").className,
  };
}

describe("Operator shared header chrome", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("keeps the same header structure for ready Today, other workspaces and the data gate", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(liveEnvelope)));
    const ready = render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);
    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    const readyToday = headerSignature();
    expect(screen.getByRole("main").className).toMatch(/shell_today/);
    ready.unmount();

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "store" }} />);
    await waitFor(() =>
      expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("data-status", "error"),
    );
    const gated = headerSignature();
    expect(screen.getByRole("main").className).not.toMatch(/shell_today/);

    expect(gated).toEqual(readyToday);
  });

  it("labels the deployment environment from deploy config, not from the fixture policy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(liveEnvelope)));
    const dev = render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);
    const devBadge = screen.getByTestId("operator-environment-badge");
    expect(devBadge).toHaveTextContent("DEV");
    expect(devBadge).toHaveAttribute("data-environment", "dev");
    expect(screen.queryByText("PRODUCTION")).not.toBeInTheDocument();
    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    expect(screen.getByTestId("operator-environment-badge")).toHaveTextContent("DEV");
    dev.unmount();

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const staging = render(<OperatorConsole deploymentEnvironment="staging" searchParams={{ ws: "today" }} />);
    await screen.findByTestId("operator-data-unavailable");
    expect(screen.getByTestId("operator-environment-badge")).toHaveTextContent("STAGING");
    staging.unmount();

    render(<OperatorConsole deploymentEnvironment="production" searchParams={{ ws: "today" }} />);
    expect(screen.getByTestId("operator-environment-badge")).toHaveTextContent("PRODUCTION");
  });

  it("explains a bootstrap timeout in operator terms and keeps the raw error as traceable detail", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValue(new DOMException("signal timed out", "TimeoutError"));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "timeout"));
    expect(within(gate).getByRole("heading", { level: 1 })).toHaveTextContent("營運資料回應逾時");
    expect(within(gate).getByRole("button", { name: "重新載入" })).toBeInTheDocument();

    const technical = within(gate).getByTestId("operator-data-unavailable-technical");
    expect(technical).toHaveTextContent("signal timed out");
    expect(technical).toHaveTextContent("OPERATOR_DATA_TIMEOUT");
    // The raw exception text is never the headline or the explanation.
    for (const visible of within(gate).getAllByText(/./, { selector: "h1, p" })) {
      expect(visible).not.toHaveTextContent(/signal timed out|fail closed|OPERATOR_DATA/);
    }

    const sent = fetchMock.mock.calls[0][1] as { headers: Record<string, string> };
    const correlationId = sent.headers["X-Correlation-Id"];
    expect(correlationId).toMatch(/^corr-operator-bootstrap-/);
    expect(within(gate).getByTestId("operator-data-unavailable-correlation")).toHaveTextContent(correlationId);
  });

  it("treats an upstream 504 as a timeout and reports the server correlation id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "WEB_API_UPSTREAM_TIMEOUT", retryable: true } }), {
          status: 504,
          headers: { "Content-Type": "application/json", "X-Correlation-Id": "corr-from-server" },
        }),
      ),
    );

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "timeout"));
    expect(within(gate).getByTestId("operator-data-unavailable-correlation")).toHaveTextContent(
      "corr-from-server",
    );
    expect(within(gate).getByTestId("operator-data-unavailable-technical")).toHaveTextContent("504");
  });

  it("does not show implementation jargon in the data-mode banner", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);
    await screen.findByTestId("operator-data-unavailable");

    const banner = screen.getByText("正式資料未就緒").parentElement as HTMLElement;
    expect(banner).not.toHaveTextContent(/fail closed|seed|API required|loading/i);
  });
});
