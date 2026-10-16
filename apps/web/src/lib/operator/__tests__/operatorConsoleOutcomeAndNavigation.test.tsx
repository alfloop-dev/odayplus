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
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
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

describe("F8 conservative approval HOLD in every browser context", () => {
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

  it("fixture/demo mode cannot bypass HOLD or simulate a server acknowledgement", async () => {
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
