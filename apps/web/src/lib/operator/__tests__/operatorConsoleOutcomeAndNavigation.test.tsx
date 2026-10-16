import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, safeOperatorTaskHref } from "../../../../features/operator/OperatorConsole";

// Separate F6/F8 owning cohorts: no change to the original Operator 300 members.
const envelope = {
  meta: {
    source: "operator-shell-production", dataMode: "live",
    role: { id: "ops-lead", label: "Verified operations", subtitle: "Scoped", allowedWorkspaces: ["today"] },
  },
  navigation: { roles: [], workspaces: [{ id: "today", label: "Today", shortLabel: "Today", description: "Live work", allowed: true }], allowedWorkspaces: ["today"] },
  today: {
    hero: { name: "Verified operator", roleLabel: "Operations", scope: "Tenant A", dateLabel: "2026-10-16" },
    kpis: [{ label: "Open", value: "1" }], queue: [], decisions: [], riskRows: [], auditFeed: [],
  },
  notifications: [], search: { count: 0, items: [] },
};

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.localStorage.clear();
});

describe("F6 task destination contract", () => {
  it.each([
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)",
    "//evil.invalid/operator?ws=today", "\\\\evil.invalid/operator", "/\\evil.invalid/",
    "https://evil.invalid/operator?ws=today", "https://user:pass@app.invalid/operator",
    "https://app.invalid@evil.invalid/operator", "https://[invalid", "", "\u0000/route",
  ])("refuses unsafe or foreign destination %j", (href) => {
    expect(safeOperatorTaskHref(href, "https://app.invalid")).toBeNull();
  });

  it.each(["/tasks", "/operator?ws=today&entity=APR-1", "tasks?view=open#queue", "https://app.invalid/tasks"])(
    "retains valid same-origin application destination %s", (href) => {
      expect(safeOperatorTaskHref(href, "https://app.invalid")).toBe(new URL(href, "https://app.invalid").href);
    },
  );

  it.each(["javascript:alert(1)", "https://evil.invalid/operator?ws=today", "//evil.invalid/tasks"])(
    "the real Task Center refuses %s without reaching navigation", async (href) => {
      const assign = vi.spyOn(window.location, "assign").mockImplementation(() => undefined);
      vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
        url.includes("/shell/tasks")
          ? { items: [{ id: "T-1", title: "Unclassified task", description: "Review the item", href }] }
          : envelope,
      ), { status: 200 })));
      render(<OperatorConsole searchParams={{ ws: "today" }} />);
      await screen.findByRole("heading", { name: /Verified operator/ });
      fireEvent.click(screen.getByTestId("operator-task-center-button"));
      fireEvent.click(await screen.findByRole("button", { name: /Unclassified task/ }));
      expect(screen.getByText(/任務連結無效或不屬於本站/)).toBeInTheDocument();
      expect(assign).not.toHaveBeenCalled();
    },
  );

  it.each(["/tasks?view=open", "/operator?ws=today&entity=T-1&tab=overview"])(
    "the real Task Center preserves safe navigation %s", async (href) => {
      const assign = vi.spyOn(window.location, "assign").mockImplementation(() => undefined);
      vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
        url.includes("/shell/tasks")
          ? { items: [{ id: "T-1", title: "Unclassified task", description: "Review the item", href }] }
          : envelope,
      ), { status: 200 })));
      render(<OperatorConsole searchParams={{ ws: "today" }} />);
      await screen.findByRole("heading", { name: /Verified operator/ });
      fireEvent.click(screen.getByTestId("operator-task-center-button"));
      fireEvent.click(await screen.findByRole("button", { name: /Unclassified task/ }));
      if (href.startsWith("/operator")) {
        expect(assign).not.toHaveBeenCalled();
        expect(window.location.search).toContain("ws=today");
        expect(window.location.search).toContain("entity=T-1");
      } else {
        expect(assign).toHaveBeenCalledWith(new URL(href, window.location.origin).href);
      }
    },
  );
});

// Transport doubles establish component non-execution only, not real SELF/PG
// authority, backend idempotency or reconciliation of a historical operation.
const holdText = "HOLD｜此檢視僅供唯讀：權威操作生命週期尚未提供，本次請求未提交。請使用既有授權的業務流程；先前請求結果仍未查證，本提示不代表已解決。";
const decisionEnvelope = (actor = "Verified operator", tenant = "Tenant A") => ({
  ...envelope,
  today: {
    ...envelope.today,
    hero: { ...envelope.today.hero, name: actor, scope: tenant },
    decisions: [{
      id: "APR-501", title: "Owning approval", meta: "Review required", status: "待核准",
      cta: "進行核准", tone: "info", target: { workspace: "govern", entityId: "APR-501", tab: "approvals" },
    }],
  },
});
const selfContext = {
  subject: "account-a", accountId: "account-a", tenantId: "tenant-a",
  expiresAt: "2099-01-01T00:00:00.000Z",
};

function installHoldTransport(context: unknown = selfContext, contextStatus = 200) {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.startsWith("/auth/session")) {
      return new Response(JSON.stringify(context), { status: contextStatus });
    }
    if (options?.method && options.method !== "GET") {
      throw new Error("An approval intent must never reach mutation transport");
    }
    return new Response(JSON.stringify(decisionEnvelope()), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function expectReadOnlyTransport(fetcher: ReturnType<typeof installHoldTransport>) {
  for (const [url, options] of fetcher.mock.calls) {
    expect(options?.method ?? "GET").toBe("GET");
    expect(url).not.toContain("/decision");
    expect(new Headers(options?.headers).has("Idempotency-Key")).toBe(false);
  }
  expect(screen.queryByText("決策已送出")).not.toBeInTheDocument();
  expect(screen.queryByText(/請重新整理後再試/)).not.toBeInTheDocument();
  expect(screen.getByTestId("operator-decision-rail")).toHaveTextContent("待核准");
}

async function attemptApproval() {
  fireEvent.click(await screen.findByRole("button", { name: "核准" }));
  expect(screen.getByText(holdText)).toBeInTheDocument();
}

describe("F8 conservative operational approval HOLD in every browser context", () => {
  it.each([
    ["positive durable SELF representation", selfContext, 200],
    ["missing context", null, 401],
    ["spoofed display identity", { subject: "Admin", tenant: "guessed" }, 200],
    ["malformed context", { ...selfContext, tenantId: [] }, 200],
    ["expired context", { ...selfContext, expiresAt: "2000-01-01T00:00:00Z" }, 401],
    ["unavailable storage", { error: "unavailable" }, 503],
  ] as const)("%s cannot enable even a first submission", async (_label, context, status) => {
    const fetcher = installHoldTransport(context, status);
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    const callsBeforeIntent = fetcher.mock.calls.length;
    await attemptApproval();
    expect(fetcher).toHaveBeenCalledTimes(callsBeforeIntent);
    expectReadOnlyTransport(fetcher);
  });

  it("local fixture permission alone cannot bypass operational HOLD or simulate a server acknowledgement", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    const fetcher = installHoldTransport();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    await attemptApproval();
    expectReadOnlyTransport(fetcher);
  });

  it("repeated and different approval intents create no key, mutation, audit or refresh-as-resolution", async () => {
    const response = decisionEnvelope();
    response.today.decisions.push({
      ...response.today.decisions[0], id: "APR-502", title: "Other owning approval",
      target: { workspace: "govern", entityId: "APR-502", tab: "approvals" },
    });
    const fetcher = installHoldTransport();
    fetcher.mockImplementation(async () => new Response(JSON.stringify(response), { status: 200 }));
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    const callsBeforeIntent = fetcher.mock.calls.length;
    for (const button of screen.getAllByRole("button", { name: "核准" })) {
      fireEvent.click(button);
      fireEvent.click(button);
    }
    expect(screen.getByText(holdText)).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(callsBeforeIntent);
    expect(screen.getByTestId("operator-decision-rail").querySelectorAll("article")).toHaveLength(2);
    expect(fetcher.mock.calls.every(([, options]) => !options?.body)).toBe(true);
    expect(screen.queryByText("決策已送出")).not.toBeInTheDocument();
  });

  it.each(["missing", "UNKNOWN", "locally cleared", "locally acknowledged"])(
    "reload and %s browser custody metadata never authorize or resolve historical work", async (state) => {
      const fetcher = installHoldTransport();
      // Arbitrary untrusted data, deliberately not a supported authority schema.
      window.localStorage.clear();
      window.sessionStorage.setItem("operator.approval.context", JSON.stringify(selfContext));
      if (state !== "missing") {
        window.localStorage.setItem("operator.approval.operation", JSON.stringify({
          approvalId: "APR-501", outcome: state, idempotencyKey: "untrusted-prior-key",
        }));
      }
      const before = window.localStorage.getItem("operator.approval.operation");
      const first = render(<OperatorConsole searchParams={{ ws: "today" }} />);
      await screen.findByRole("heading", { name: /Verified operator/ });
      await attemptApproval();
      first.unmount();
      render(<OperatorConsole searchParams={{ ws: "today" }} />);
      await screen.findByRole("heading", { name: /Verified operator/ });
      await attemptApproval();
      expectReadOnlyTransport(fetcher);
      expect(window.localStorage.getItem("operator.approval.operation")).toBe(before);
      expect(screen.getByText(holdText)).toHaveTextContent("先前請求結果仍未查證");
      window.localStorage.clear();
    },
  );

  it("new actor/tenant and a late superseded bootstrap cannot create a decision result", async () => {
    let releaseOld: (response: Response) => void = () => { throw new Error("old read not started"); };
    const oldRead = new Promise<Response>((resolve) => { releaseOld = resolve; });
    const fetcher = installHoldTransport();
    fetcher.mockImplementationOnce(() => oldRead);
    const first = render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    first.unmount();
    fetcher.mockImplementation(async () => new Response(JSON.stringify(
      decisionEnvelope("Current operator", "Tenant B"),
    ), { status: 200 }));
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Current operator/ });
    await attemptApproval();
    releaseOld(new Response(JSON.stringify(decisionEnvelope("Old operator", "Tenant A")), { status: 200 }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /Current operator/ })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: /Old operator/ })).not.toBeInTheDocument();
    expectReadOnlyTransport(fetcher);
    expect(screen.getByText(holdText)).toHaveTextContent("本提示不代表已解決");
  });
});

const demoEnvelope = () => ({
  ...decisionEnvelope(),
  meta: { ...envelope.meta, correlationId: "initial-read", source: "operator-shell-api-envelope", dataMode: "fixture",
    dataOrigin: { kind: "fixture" }, liveReadiness: { required: false, ready: false, complete: false } },
});
function apiResponse(value: unknown, path = "/api/v1/operator/bootstrap", status = 200) {
  const response = new Response(JSON.stringify(value), { status });
  Object.defineProperty(response, "url", { value: new URL(path, window.location.origin).href, configurable: true });
  return response;
}
function allowDemoCoordination() {
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
  const scopedNavigator = Object.create(navigator);
  Object.defineProperty(scopedNavigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, callback: (lock: object) => Promise<void>) => callback({}),
  } });
  vi.stubGlobal("navigator", scopedNavigator);
}
function installDemoTransport(fault = "none") {
  let reads = 0;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      if (fault === "loss") throw new Error("response lost after possible dispatch");
      const body = JSON.parse(String(options.body));
      return apiResponse({ approvalId: fault === "mismatch" ? "OTHER" : "APR-501",
        newStatus: body.status, auditEventId: "demo-not-durable-audit",
        correlationId: new Headers(options.headers).get("X-Correlation-Id") }, url);
    }
    reads += 1;
    if (reads === 3 && fault === "refresh") throw new Error("read refresh failed after acknowledgement");
    const data = demoEnvelope();
    data.meta.correlationId = new Headers(options?.headers).get("X-Correlation-Id") ?? "missing";
    if (reads >= 3) data.today.decisions = [];
    return apiResponse(data);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

describe("F8 positively server-declared NONOPERATIONAL demo", () => {
  it.each(["stale-correlation", "changed-role", "changed-mode", "changed-readiness", "no-locks", "storage-error"])(
    "%s refuses before creating a demo request", async (fault) => {
      allowDemoCoordination();
      if (fault === "no-locks") Object.defineProperty(navigator, "locks", { value: undefined });
      if (fault === "storage-error") {
        const storage = window.localStorage;
        // Happy DOM's Window storage is not the global Storage prototype.
        // Inject failure at the actual browser-owned interface, not an unused
        // prototype (which would silently let the negative dispatch a POST).
        vi.spyOn(window, "localStorage", "get").mockReturnValue({
          get length() { return storage.length; },
          clear: () => storage.clear(), key: (index) => storage.key(index),
          removeItem: (key) => storage.removeItem(key),
          setItem: (key, value) => storage.setItem(key, value),
          getItem: (key) => {
            if (key === "oday.operator.fixture-approval.unresolved") throw new Error("storage unavailable");
            return storage.getItem(key);
          },
        });
      }
      let reads = 0;
      const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
        const data = demoEnvelope();
        data.meta.correlationId = new Headers(options?.headers).get("X-Correlation-Id") ?? "missing";
        if (++reads > 1) {
          if (fault === "stale-correlation") data.meta.correlationId = "stale-other-read";
          if (fault === "changed-role") data.meta.role = { ...data.meta.role, id: "other-role" };
          if (fault === "changed-mode") data.meta.dataMode = "live";
          if (fault === "changed-readiness") data.meta.liveReadiness.required = true;
        }
        return apiResponse(data);
      });
      vi.stubGlobal("fetch", fetcher);
      render(<OperatorConsole searchParams={{ ws: "today" }} />);
      await screen.findByRole("heading", { name: /Verified operator/ });
      fireEvent.click(await screen.findByRole("button", { name: "核准" }));
      await screen.findByText(holdText);
      expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
    },
  );

  it("performs the actual fixture POST then real envelope refresh, not local count simulation", async () => {
    allowDemoCoordination();
    const fetcher = installDemoTransport();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    fireEvent.click(await screen.findByRole("button", { name: "核准" }));
    await screen.findByText(/示範 API 已確認且狀態已讀回/);
    expect(screen.getByTestId("operator-decisions-empty")).toBeInTheDocument();
    const posts = fetcher.mock.calls.filter(([, options]) => options?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/api/v1/operator/approvals/APR-501/decision");
    expect(JSON.parse(String(posts[0][1]?.body))).toMatchObject({ status: "approved", actorRoleId: "ops-lead" });
    expect(new Headers(posts[0][1]?.headers).get("Idempotency-Key")).toBeTruthy();
    expect(fetcher.mock.calls.filter(([url]) => url === "/api/v1/operator/bootstrap")).toHaveLength(3);
    expect(screen.getByText(/非真實業務核准或持久稽核/)).toBeInTheDocument();
  });

  it.each(["loss", "mismatch", "refresh"])("retains %s custody across click and reload, with no new request/key", async (fault) => {
    allowDemoCoordination();
    const fetcher = installDemoTransport(fault);
    const first = render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    fireEvent.click(await screen.findByRole("button", { name: "核准" }));
    await screen.findByText(fault === "refresh" ? /示範 API 已確認，但刷新失敗/ : /示範請求結果未知/);
    const custody = window.localStorage.getItem("oday.operator.fixture-approval.unresolved");
    expect(custody).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    await screen.findByText(/先前示範請求尚未查證/);
    first.unmount();
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    await screen.findByRole("heading", { name: /Verified operator/ });
    // Loss/mismatch leaves the item; refresh may later read an absent card.
    // Neither read can clear the UNKNOWN/ACK-refresh custody record.
    expect(window.localStorage.getItem("oday.operator.fixture-approval.unresolved")).toBe(custody);
    expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
    expect(screen.queryByText(/示範 API 已確認且狀態已讀回/)).not.toBeInTheDocument();
  });

  it.each(["missing", "live", "required", "foreign", "production-runtime"])("%s metadata/runtime cannot enable a demo mutation", async (fault) => {
    allowDemoCoordination();
    if (fault === "production-runtime") vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const data = demoEnvelope();
    if (fault === "missing") delete (data.meta as Partial<typeof data.meta>).dataOrigin;
    if (fault === "live") data.meta.dataMode = "live";
    if (fault === "required") data.meta.liveReadiness.required = true;
    const fetcher = vi.fn(async (_url: string, _options?: RequestInit) => {
      const response = apiResponse(data);
      if (fault === "foreign") Object.defineProperty(response, "url", { value: "https://foreign.invalid/api/v1/operator/bootstrap" });
      return response;
    });
    vi.stubGlobal("fetch", fetcher);
    render(<OperatorConsole searchParams={{ ws: "today" }} />);
    if (fault === "production-runtime") {
      await waitFor(() => expect(fetcher).toHaveBeenCalled());
      expect(screen.queryByRole("button", { name: "核准" })).not.toBeInTheDocument();
    } else {
      await screen.findByRole("heading", { name: /Verified operator/ });
      await attemptApproval();
    }
    expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
    expect(window.localStorage.getItem("oday.operator.fixture-approval.unresolved")).toBeNull();
  });
});
