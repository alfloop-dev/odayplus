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
    // A persistent upstream timeout is retried exactly once before the gate shows.
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("retries a cold-start 504 once with the same correlation id and renders live data", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code: "WEB_API_UPSTREAM_TIMEOUT", retryable: true } }), {
          status: 504,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(liveEnvelope));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-data-unavailable")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [first, second] = fetchMock.mock.calls.map(
      (call) => (call[1] as { headers: Record<string, string> }).headers["X-Correlation-Id"],
    );
    expect(second).toBe(first);
    // The browser budget outlasts the Web BFF's 10s upstream timeout.
    expect(Math.min(...timeoutSpy.mock.calls.map(([ms]) => ms))).toBeGreaterThan(10_000);
  });

  /** A 200 whose headers arrived but whose streamed body then fails. */
  function failingBodyResponse(error: Error) {
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: () => Promise.reject(error),
    } as unknown as Response;
  }

  it("retries once when the 200 body times out mid-stream, then renders live data", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(failingBodyResponse(new DOMException("signal timed out", "TimeoutError")))
      .mockResolvedValueOnce(jsonResponse(liveEnvelope));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const ids = fetchMock.mock.calls.map(
      (call) => (call[1] as { headers: Record<string, string> }).headers["X-Correlation-Id"],
    );
    expect(ids[1]).toBe(ids[0]);
  });

  it("makes exactly two attempts when the body keeps failing with a network error", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => failingBodyResponse(new TypeError("network error")));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "network"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  /** Attempt signals that have already timed out, as after a 20s body stall. */
  function stubTimedOutAttemptSignals() {
    return vi.spyOn(AbortSignal, "timeout").mockImplementation(() => {
      const controller = new AbortController();
      controller.abort(new DOMException("signal timed out", "TimeoutError"));
      return controller.signal;
    });
  }

  it("retries a body read that the timeout aborts with AbortError (Chromium < 154)", async () => {
    stubTimedOutAttemptSignals();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(failingBodyResponse(new DOMException("The operation was aborted.", "AbortError")))
      .mockResolvedValueOnce(jsonResponse(liveEnvelope));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    expect(await screen.findByText("Live unresolved")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("makes exactly two attempts when every body read is aborted by the timeout", async () => {
    stubTimedOutAttemptSignals();
    const fetchMock = vi
      .fn()
      .mockImplementation(async () =>
        failingBodyResponse(new DOMException("The operation was aborted.", "AbortError")),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "timeout"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry a malformed JSON body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(failingBodyResponse(new SyntaxError("Unexpected token < in JSON")));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-status", "error"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a permission denial", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "forbidden" }, 403));
    vi.stubGlobal("fetch", fetchMock);

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "forbidden"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("tells an expired session to sign in again instead of showing permission guidance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: "WEB_SESSION_REQUIRED", message: "A valid web session is required." } }, 401),
      ),
    );
    window.history.replaceState({}, "", "/operator?ws=store");

    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "store" }} />);

    const gate = await screen.findByTestId("operator-data-unavailable");
    await waitFor(() => expect(gate).toHaveAttribute("data-failure-kind", "unauthenticated"));
    expect(within(gate).getByRole("heading", { level: 1 })).toHaveTextContent("登入已過期");
    expect(within(gate).getByTestId("operator-data-unavailable-login-link")).toHaveAttribute(
      "href",
      `/login?returnTo=${encodeURIComponent("/operator?ws=store")}`,
    );
    expect(within(gate).queryByTestId("operator-data-unavailable-admin-link")).not.toBeInTheDocument();
    expect(gate).not.toHaveTextContent("沒有營運資料讀取權限");
  });

  it("does not show implementation jargon in the data-mode banner", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<OperatorConsole deploymentEnvironment="dev" searchParams={{ ws: "today" }} />);
    // The gate exists during loading too; wait for the final error banner,
    // not merely the gate's first render, before checking its wording.
    const banner = (await screen.findByText("正式資料未就緒")).parentElement as HTMLElement;
    expect(banner).not.toHaveTextContent(/fail closed|seed|API required|loading/i);
  });
});
