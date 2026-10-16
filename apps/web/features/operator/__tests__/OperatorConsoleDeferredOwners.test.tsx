import "@testing-library/jest-dom/vitest";
import { lazy, Suspense, type ComponentProps, type ComponentType } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OperatorConsole, operatorBootstrapRetryPolicy } from "../OperatorConsole";
import type { WorkspaceId } from "../navigation";
import type { Issue } from "../types";

// This exercises actual React lazy imports at the existing Console branch.
// The child owners are transport/render controls, not native authority grants.
type Trace = { loaded: string[]; rendered: string[] };
const trace = vi.hoisted((): Trace => ({ loaded: [], rendered: [] }));
vi.mock("next/dynamic", () => ({
  default: (load: () => Promise<ComponentType<Record<string, unknown>>>) => {
    const Deferred = lazy(async () => ({ default: await load() }));
    return (props: Record<string, unknown>) => <Suspense fallback={<div role="status">Deferred owner</div>}>
      <Deferred {...props} />
    </Suspense>;
  },
}));
vi.mock("../DesignAlignedWorkspaces", () => {
  trace.loaded.push("store");
  return { DesignStoreOpsWorkspace: (props: ComponentProps<typeof import("../DesignAlignedWorkspaces").DesignStoreOpsWorkspace>) => {
    trace.rendered.push("store");
    const issue = props.issues?.[0];
    return <section data-testid="deferred-store" data-role={props.activeRoleId}
      data-issue={props.initialIssueId} data-tab={props.initialTabId}>
      <button disabled={!issue} onClick={() => { if (issue) props.onOpenWorkflow("triage", issue); }}>Open controlled workflow</button>
    </section>;
  } };
});
vi.mock("../GrowthWorkspace", () => {
  trace.loaded.push("growth");
  return { GrowthWorkspace: (props: ComponentProps<typeof import("../GrowthWorkspace").GrowthWorkspace>) => {
    trace.rendered.push("growth");
    return <section data-testid="deferred-growth" data-base={props.basePath} data-selected={props.searchParams?.item} />;
  } };
});
vi.mock("../StoreOpsWorkflowDialogs", () => {
  trace.loaded.push("dialog");
  return { StoreOpsWorkflowDialogs: (props: ComponentProps<typeof import("../StoreOpsWorkflowDialogs").StoreOpsWorkflowDialogs>) => {
    trace.rendered.push("dialog");
    return <section role="dialog" aria-label="Controlled workflow" data-issue={props.issue?.id} data-dialog={props.activeDialog}>
      <button onClick={props.onClose}>Close controlled workflow</button>
    </section>;
  } };
});
vi.mock("../ShellNotificationPage", () => {
  trace.loaded.push("notification-page");
  return { ShellNotificationPage: (props: ComponentProps<typeof import("../ShellNotificationPage").ShellNotificationPage>) => {
    trace.rendered.push("notification-page");
    return <section data-testid="deferred-notification-page" data-subject={props.expectedSubjectId}
      data-preferences={String(props.preferencesPage)} data-report={props.reportId} data-scope={props.scopeKey} />;
  } };
});
vi.mock("../ShellTaskCenter", () => {
  trace.loaded.push("tasks");
  return { ShellTaskCenter: (props: ComponentProps<typeof import("../ShellTaskCenter").ShellTaskCenter>) => {
    trace.rendered.push("tasks");
    return <section data-testid="deferred-tasks" data-scope={props.scopeKey}
      data-role={new Headers(props.headers).get("X-Operator-Role")} data-fixtures={String(props.allowFixtureData)} />;
  } };
});
vi.mock("../ShellNotificationCenter", () => {
  trace.loaded.push("notification-popover");
  return { ShellNotificationCenter: () => {
    trace.rendered.push("notification-popover");
    return <section data-testid="deferred-notification-popover" />;
  } };
});
vi.mock("../NetworkFindAreasWorkspace", () => {
  trace.loaded.push("network");
  return { NetworkFindAreasWorkspace: () => {
    trace.rendered.push("network");
    return <section data-testid="deferred-network" />;
  } };
});
vi.mock("../GovernanceWorkspace", () => {
  trace.loaded.push("governance");
  return { GovernanceWorkspace: () => {
    trace.rendered.push("governance");
    return <section data-testid="deferred-governance" />;
  } };
});
vi.mock("../network/networkFindAreasLoader", () => {
  trace.loaded.push("network-bindings");
  return { loadNetworkFindAreasBindings: vi.fn() };
});

const originalLocation = window.location.href;
const originalDelays = [...operatorBootstrapRetryPolicy.delaysMs];
const issue: Issue = {
  id: "controlled-current-issue", title: "Controlled issue", storeId: "controlled-current-store",
  storeName: "Controlled store", status: "new", severity: "high", source: "payment",
  ownerRoleId: "opsLead", ownerName: "Controlled owner", slaDueAt: "2026-10-16T12:00:00Z",
  createdAt: "2026-10-16T08:00:00Z", updatedAt: "2026-10-16T08:00:00Z", evidenceIds: [],
  summary: "Declared rendering control; no actual business data or write receipt",
};
function envelope(allowed: WorkspaceId[] = ["today", "store", "growth", "network", "govern"]) {
  const role = { id: "ops-lead", label: "Controlled operator", subtitle: "Controlled scope", allowedWorkspaces: allowed };
  const counts = { approvals: 0, critical: 0, notifications: 1, search: 0, taskCenter: 1 };
  return {
    meta: { source: "operator-shell-production", dataMode: "live", role, counts },
    navigation: { roles: [role], allowedWorkspaces: allowed,
      workspaces: allowed.map((id) => ({ id, label: id, shortLabel: id, description: id, allowed: true })) },
    header: { counts },
    today: { hero: { name: "Controlled reader", roleLabel: role.label, scope: "Controlled tenant", dateLabel: "2026-10-16" },
      kpis: [{ label: "Controlled count", value: "0" }], queue: [], decisions: [], riskRows: [], auditFeed: [] },
    notifications: [], search: { count: 0, items: [] }, issues: [issue],
  };
}
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }
function controlledBootstrap() {
  let release: (response: Response) => void = () => { throw new Error("Bootstrap promise not initialized"); };
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    paths.push(path);
    if (path === "/auth/session") return json({ subject: "controlled-current-subject", expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (path === "/api/v1/operator/bootstrap") return pending.then((response) => response.clone());
    throw new Error("Unexpected owner read before its own component control: " + path);
  }));
  return { release, paths };
}
beforeEach(() => {
  window.sessionStorage.clear(); trace.rendered.length = 0;
  operatorBootstrapRetryPolicy.delaysMs = originalDelays.map(() => 0);
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "production");
  window.history.replaceState(null, "", "/operator");
});
afterEach(() => {
  cleanup(); operatorBootstrapRetryPolicy.delaysMs = [...originalDelays];
  window.history.replaceState(null, "", originalLocation);
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
});

describe("Console conditional owner import boundary", () => {
  it.each(["notifications", "tasks"] as const)("admits the %s route without loading unrelated business owners", async (shellView) => {
    const control = controlledBootstrap();
    window.history.replaceState(null, "", "/" + shellView + "?view=approvals");
    render(<OperatorConsole shellView={shellView} searchParams={{ ws: "growth", view: "approvals" }} />);
    await waitFor(() => expect(control.paths).toContain("/api/v1/operator/bootstrap"));
    expect(trace.rendered).toEqual([]);
    expect(trace.loaded.filter((name) => ["store", "growth", "dialog", "network", "network-bindings", "governance", "notification-popover"].includes(name))).toEqual([]);
    await act(async () => { control.release(json(envelope())); });
    const child = await screen.findByTestId("deferred-" + (shellView === "tasks" ? "tasks" : "notification-page"));
    expect(child.getAttribute("data-scope")).toContain("controlled-current-subject");
    expect(trace.rendered.every((name) => name === (shellView === "tasks" ? "tasks" : "notification-page"))).toBe(true);
    expect(trace.loaded.filter((name) => ["store", "growth", "dialog", "network", "network-bindings", "governance", "notification-popover"].includes(name))).toEqual([]);
    if (shellView === "tasks") {
      expect(child).toHaveAttribute("data-role", "ops-lead"); expect(child).toHaveAttribute("data-fixtures", "false");
    } else expect(child).toHaveAttribute("data-subject", "controlled-current-subject");
    expect(control.paths.every((path) => ["/auth/session", "/api/v1/operator/bootstrap"].includes(path))).toBe(true);
  });

  it("keeps report identity and preferences on the same admitted notification owner", async () => {
    const control = controlledBootstrap();
    render(<OperatorConsole shellView="notifications" searchParams={{}} notificationPreferences notificationReportId="controlled-report-exact" />);
    await act(async () => { control.release(json(envelope())); });
    const child = await screen.findByTestId("deferred-notification-page");
    expect(child).toHaveAttribute("data-preferences", "true");
    expect(child).toHaveAttribute("data-report", "controlled-report-exact");
    expect(child).toHaveAttribute("data-subject", "controlled-current-subject");
    expect(trace.rendered.every((name) => name === "notification-page")).toBe(true);
  });

  it("does not render a cached deferred owner when actual bootstrap authorization is denied", async () => {
    const control = controlledBootstrap();
    render(<OperatorConsole shellView="tasks" searchParams={{ ws: "growth" }} />);
    await act(async () => { control.release(json({ detail: "Controlled authorization denial" }, 403)); });
    await waitFor(() => expect(control.paths).toContain("/api/v1/operator/bootstrap"));
    await waitFor(() => expect(screen.getByTestId("operator-data-unavailable")).toHaveAttribute("role", "alert"));
    expect(screen.getByTestId("operator-data-unavailable")).toBeVisible();
    expect(screen.queryByTestId("deferred-tasks")).toBeNull();
    expect(trace.rendered).toEqual([]);
  });

  it("loads the requested Store owner with original context and the dialog only after an explicit action", async () => {
    const control = controlledBootstrap();
    render(<OperatorConsole searchParams={{ ws: "store", tab: "forecastOps", entity: issue.id }} />);
    await act(async () => { control.release(json(envelope())); });
    const store = await screen.findByTestId("deferred-store");
    expect(store).toHaveAttribute("data-role", "ops-lead");
    expect(store).toHaveAttribute("data-tab", "forecastOps");
    expect(store).toHaveAttribute("data-issue", issue.id);
    expect(trace.loaded).not.toContain("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Open controlled workflow" }));
    const dialog = await screen.findByRole("dialog", { name: "Controlled workflow" });
    expect(dialog).toHaveAttribute("data-issue", issue.id); expect(dialog).toHaveAttribute("data-dialog", "triage");
    fireEvent.click(screen.getByRole("button", { name: "Close controlled workflow" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Controlled workflow" })).toBeNull());
    expect(screen.getByTestId("deferred-store")).toBeVisible();
  });

  it("loads Growth on its admitted branch and preserves its exact query and base path", async () => {
    const control = controlledBootstrap();
    render(<OperatorConsole searchParams={{ ws: "growth", item: "controlled-growth-id", gtab: "priceops" }} />);
    await act(async () => { control.release(json(envelope())); });
    const growth = await screen.findByTestId("deferred-growth");
    expect(growth).toHaveAttribute("data-base", "/operator");
    expect(growth).toHaveAttribute("data-selected", "controlled-growth-id");
    expect(trace.rendered.every((name) => name === "growth")).toBe(true);
  });
});
