import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, safeOperatorTaskHref } from "../../../../features/operator/OperatorConsole";

// Separate F6 owning cohort: no change to the original Operator 300 members.
const envelope = {
  meta: {
    source: "operator-shell-production", dataMode: "live",
    role: { id: "ops-lead", label: "Verified operations", subtitle: "Scoped", allowedWorkspaces: ["today"] },
  },
  navigation: { roles: [], workspaces: [], allowedWorkspaces: ["today"] },
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
      await screen.findByText("Verified operator");
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
      await screen.findByText("Verified operator");
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
