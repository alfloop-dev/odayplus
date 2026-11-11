import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Header counts are business facts only after the admitted shell envelope or a
// validated current-scope read; cleared emptyEnvelope zeros stay unknown.
const headerCount = (testId: string) => screen.getByTestId(testId).querySelector("[data-count-state]") as HTMLElement;
const headerTestIds = ["operator-notification-button", "operator-task-center-count", "operator-approval-count"];
function expectUnknownHeader() {
  for (const testId of headerTestIds) {
    expect(headerCount(testId)).toHaveAttribute("data-count-state", "unknown");
    expect(headerCount(testId)).toHaveTextContent("—");
    expect(headerCount(testId)).not.toHaveTextContent("0");
  }
}
function controlledProductionReads(subject = { current: "unit-shell-account" }, rejectedBootstrap?: () => Response) {
  const bootstraps: Array<(response: Response) => void> = [];
  const taskReads: string[] = [];
  let taskResponse = () => new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/auth/session") return Promise.resolve(new Response(JSON.stringify({ subject: subject.current, expiresAt: Math.floor(Date.now() / 1000) + 3600 }), {
      status: 200, headers: { "Content-Type": "application/json" } }));
    if (url === "/api/v1/operator/bootstrap" && rejectedBootstrap) return Promise.resolve(rejectedBootstrap());
    if (url === "/api/v1/operator/bootstrap") return new Promise<Response>((resolve) => { bootstraps.push(resolve); });
    if (url === "/api/v1/operator/shell/tasks") { taskReads.push(subject.current); return Promise.resolve(taskResponse()); }
    return Promise.resolve(new Response(null, { status: 404 }));
  }));
  const release = async (body: unknown, status = 200) => {
    await waitFor(() => expect(bootstraps.length).toBeGreaterThan(0));
    await act(async () => { bootstraps.shift()!(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })); });
  };
  return { release, taskReads, pendingBootstraps: () => bootstraps.length, setTaskResponse: (next: () => Response) => { taskResponse = next; } };
}
const taskSourceChip = (popover: HTMLElement) => within(popover.querySelector("[class*='taskCenterHeader']") as HTMLElement);
const withCounts = (counts: Partial<typeof liveEnvelope.header.counts>) => ({ ...liveEnvelope,
  meta: { ...liveEnvelope.meta, counts: { ...liveEnvelope.meta.counts, ...counts } },
  header: { counts: { ...liveEnvelope.header.counts, ...counts } } });

describe("Operator header counts never present unverified shells as zero", () => {
  beforeEach(() => { vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });

  it("keeps notification, task and approval counts unknown while production bootstrap is loading", async () => {
    const control = controlledProductionReads();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(control.pendingBootstraps()).toBe(1));
    expectUnknownHeader();
    await control.release(withCounts({ notifications: 3, taskCenter: 2, approvals: 4 }));
    await screen.findByText("Live unresolved");
    expect(headerCount("operator-notification-button")).toHaveTextContent("3");
    expect(headerCount("operator-task-center-count")).toHaveTextContent("2");
    expect(headerCount("operator-approval-count")).toHaveTextContent("4");
  });

  it.each([
    ["error", { detail: "unit refusal" }, 503],
    ["seed", seedEnvelope, 200],
  ] as const)("keeps header counts unknown after a rejected %s bootstrap", async (_kind, body, status) => {
    controlledProductionReads(undefined, () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).not.toHaveAttribute("data-status", "loading"));
    expect(screen.queryByText("Live unresolved")).not.toBeInTheDocument();
    expectUnknownHeader();
  });

  it("does not claim API status or zero counts in a task popover whose read is blocked", async () => {
    const control = controlledProductionReads();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(control.pendingBootstraps()).toBe(1));
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    const popover = await screen.findByTestId("operator-task-center");
    expect(within(popover).getByTestId("operator-task-center-not-loaded")).toBeVisible();
    expect(taskSourceChip(popover).queryByText("API", { exact: true })).not.toBeInTheDocument();
    expect(taskSourceChip(popover).getByText("NOT LOADED", { exact: true })).toBeVisible();
    const summary = within(popover).getByLabelText("Task center summary");
    expect(summary).not.toHaveTextContent(/0\s*(API|Urgent|Visible)/);
    expect(summary).toHaveTextContent(/—\s*API/);
    expect(summary).toHaveTextContent(/—\s*Visible/);
    expect(control.taskReads).toEqual([]);
  });

  it("does not claim an API task total after the task read fails", async () => {
    const control = controlledProductionReads();
    control.setTaskResponse(() => new Response(JSON.stringify({ detail: "unit task refusal" }), { status: 503, headers: { "Content-Type": "application/json" } }));
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await control.release(withCounts({ taskCenter: 5 }));
    await screen.findByText("Live unresolved");
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    const popover = await screen.findByTestId("operator-task-center");
    await within(popover).findByTestId("operator-task-center-unavailable");
    expect(taskSourceChip(popover).getByText("UNAVAILABLE", { exact: true })).toBeVisible();
    const summary = within(popover).getByLabelText("Task center summary");
    expect(summary).toHaveTextContent(/—\s*API/);
    expect(summary).not.toHaveTextContent(/5\s*API/);
    expect(summary).toHaveTextContent(/0\s*Visible/);
  });

  it("keeps a genuine zero from the validated current API envelope and task read", async () => {
    const control = controlledProductionReads();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await control.release(withCounts({ notifications: 0, taskCenter: 0, approvals: 0 }));
    await screen.findByText("Live unresolved");
    for (const testId of headerTestIds) {
      expect(headerCount(testId)).toHaveAttribute("data-count-state", "known");
      expect(headerCount(testId)).toHaveTextContent("0");
    }
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    const popover = await screen.findByTestId("operator-task-center");
    await within(popover).findByText("Task API 已回應，但目前沒有可用任務。");
    expect(taskSourceChip(popover).getByText("API", { exact: true })).toBeVisible();
    expect(within(popover).getByLabelText("Task center summary")).toHaveTextContent(/0\s*API/);
    expect(control.taskReads).toEqual(["unit-shell-account"]);
  });

  it("clears the prior identity's counts and keeps them unknown until the new identity is admitted", async () => {
    const subject = { current: "unit-shell-account" };
    const control = controlledProductionReads(subject);
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await control.release(withCounts({ notifications: 3, taskCenter: 2, approvals: 4 }));
    await screen.findByText("Live unresolved");
    expect(headerCount("operator-task-center-count")).toHaveTextContent("2");
    subject.current = "unit-other-account";
    fireEvent.click(screen.getByTestId("operator-task-center-button"));
    await waitFor(() => expect(control.pendingBootstraps()).toBe(1));
    expectUnknownHeader();
    expect(control.taskReads).toEqual([]);
    await control.release(withCounts({ notifications: 0, taskCenter: 7, approvals: 1 }));
    await waitFor(() => expect(headerCount("operator-task-center-count")).toHaveTextContent("7"));
    expect(headerCount("operator-notification-button")).toHaveTextContent("0");
    expect(headerCount("operator-approval-count")).toHaveTextContent("1");
  });
});

describe("Operator ready payload counts are known only when the API provides a valid count", () => {
  beforeEach(() => { vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
  const withRawCounts = (value: unknown) => {
    const raw = { notifications: value, taskCenter: value, approvals: value, critical: value, search: value };
    return { ...liveEnvelope, meta: { ...liveEnvelope.meta, counts: raw }, header: { counts: raw } };
  };
  const withoutCounts = () => {
    const { counts: _metaCounts, ...meta } = liveEnvelope.meta;
    const { header: _header, ...rest } = liveEnvelope;
    return { ...rest, meta };
  };
  // The original palette lists actions only for a matching query.
  async function paletteCaptions() {
    fireEvent.click(screen.getByRole("button", { name: "Open command palette" }));
    const palette = await screen.findByTestId("operator-command-palette");
    const query = await within(palette).findByRole("combobox", { name: /Command palette search/ });
    const caption = async (keyword: string, title: string) => {
      fireEvent.change(query, { target: { value: keyword } });
      return (await within(palette).findByRole("option", { name: new RegExp(title) })).textContent ?? "";
    };
    const captions = { tasks: await caption("inbox", "開啟任務中心"), notifications: await caption("alerts", "開啟通知") };
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("operator-command-palette")).toBeNull());
    return captions;
  }

  it.each([
    ["missing", undefined],
    ["null", null],
    ["numeric string", "3"],
    ["empty string", ""],
    ["false", false],
    ["empty array", []],
    ["negative", -1],
    ["fractional", 1.5],
    ["non-numeric string", "abc"],
  ])("renders a ready payload's %s count as unknown, not 0, NaN or a row total", async (_kind, value) => {
    const control = controlledProductionReads();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await control.release(value === undefined ? withoutCounts() : withRawCounts(value));
    await screen.findByText("Live unresolved");
    expectUnknownHeader();
    for (const testId of headerTestIds) expect(headerCount(testId)).not.toHaveTextContent(/NaN|1/);
    const palette = await paletteCaptions();
    expect(palette.tasks).toContain("— API tasks");
    expect(palette.notifications).toContain("— notifications");
    expect(palette.tasks).not.toMatch(/(0|1|NaN) API tasks/);
  });

  it("keeps valid numeric meta counts when the header omits them, including 0", async () => {
    const control = controlledProductionReads();
    const { header: _header, ...rest } = liveEnvelope;
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await control.release({ ...rest, meta: { ...liveEnvelope.meta, counts: { notifications: 0, taskCenter: 4, approvals: 0, critical: 0, search: 0 } } });
    await screen.findByText("Live unresolved");
    expect(headerCount("operator-notification-button")).toHaveTextContent("0");
    expect(headerCount("operator-task-center-count")).toHaveTextContent("4");
    expect(headerCount("operator-approval-count")).toHaveTextContent("0");
    for (const testId of headerTestIds) expect(headerCount(testId)).toHaveAttribute("data-count-state", "known");
  });

  it("keeps command palette counts unknown while loading and shows the admitted counts once ready", async () => {
    const control = controlledProductionReads();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(control.pendingBootstraps()).toBe(1));
    const loadingPalette = await paletteCaptions();
    expect(loadingPalette.tasks).toContain("— API tasks");
    expect(loadingPalette.notifications).toContain("— notifications");
    expect(`${loadingPalette.tasks} ${loadingPalette.notifications}`).not.toMatch(/0 (API tasks|notifications)/);
    await control.release(withCounts({ notifications: 3, taskCenter: 2 }));
    await screen.findByText("Live unresolved");
    const readyPalette = await paletteCaptions();
    expect(readyPalette.tasks).toContain("2 API tasks");
    expect(readyPalette.notifications).toContain("3 notifications");
  });
});
