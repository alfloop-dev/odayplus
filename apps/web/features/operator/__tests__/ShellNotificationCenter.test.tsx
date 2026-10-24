import "@testing-library/jest-dom/vitest";
import { StrictMode, useState } from "react";
import { DesignStoreOpsWorkspace } from "../DesignAlignedWorkspaces";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeNotificationInbox, ShellNotificationCenter, type ShellNotificationCenterProps } from "../ShellNotificationCenter";

// Isolated transport contract controls, not real authenticated-session or UAT evidence.
function notification(id = "owner-1", overrides: Record<string, unknown> = {}) {
  return { notificationId: id, title: `Notice ${id}`, detail: "Owner-provided detail", tone: "warning", severity: "warning",
    category: "alert", occurredAt: "2026-10-15T08:00:00+00:00", acknowledged: false,
    sourceEntity: { kind: "forecastops.alert", id: `alert-${id}` }, sourceDetail: { state: "available" },
    target: { workspace: "store", entityId: id, tab: "forecastOps" }, ...overrides };
}
function inbox(items = [notification()], allowed = true) {
  return { items, unacknowledged: items.filter((item) => !item.acknowledged).length,
    actions: { acknowledge: { allowed } } };
}
function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function props(overrides: Partial<ShellNotificationCenterProps> = {}): ShellNotificationCenterProps {
  return { scopeKey: "A-epoch1", headers: { "X-Operator-Role": "ops-lead" },
    createAuthenticatedReadGuard: (current) => async () => current(),
    onConfirmedUnreadCount: vi.fn(), onTargetSelect: vi.fn(), ...overrides };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("Notification header canonical inbox", () => {
  it("reads owner event time and exact source target; categories and read filter do not classify legacy unknowns", async () => {
    const rows = [notification(), notification("job-1", { category: "job", acknowledged: true, sourceDetail: { state: "unavailable", reasonCode: "INGESTION_RUN_DETAIL_NOT_IMPLEMENTED" }, sourceEntity: { kind: "external_data.ingestion_run", id: "job-1" } }),
      notification("legacy-1", { category: null, occurredAt: null, target: null, sourceDetail: undefined, sourceEntity: undefined })];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox(rows)))));
    const current = props(); render(<ShellNotificationCenter {...current} />);
    expect(await screen.findByText("Notice owner-1")).toBeVisible();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    expect(document.querySelector("time")).toHaveAttribute("datetime", "2026-10-15T08:00:00+00:00");
    expect(screen.getByText("事件時間未提供")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Job" }));
    expect(screen.getByText("Notice job-1")).toBeVisible();
    expect(screen.queryByText("Notice legacy-1")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("讀取狀態"), { target: { value: "unread" } });
    expect(screen.getByText("目前沒有新通知。")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "全部" }));
    fireEvent.click(screen.getByRole("button", { name: "查看來源 owner-1" }));
    await waitFor(() => expect(current.onTargetSelect).toHaveBeenCalledWith(
      { workspace: "store", entityId: "owner-1", tab: "forecastOps" }, "Notice owner-1"));
    expect(current.onConfirmedUnreadCount).toHaveBeenCalledWith(2);
  });

  it("changes neither read state nor Bell count before the server confirms the post-command re-read", async () => {
    const post = deferred<Response>(); const reread = deferred<Response>(); let reads = 0;
    const fetcher = vi.fn((_url: string, init?: RequestInit) => init?.method === "POST"
      ? post.promise : ++reads === 1 ? Promise.resolve(response(inbox())) : reread.promise);
    vi.stubGlobal("fetch", fetcher); const current = props(); render(<ShellNotificationCenter {...current} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "選取通知 owner-1" }));
    fireEvent.click(screen.getByRole("button", { name: "將選取通知標記已讀" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    expect(current.onConfirmedUnreadCount).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("checkbox", { name: "選取通知 owner-1" })).toBeChecked();
    await act(async () => post.resolve(response({ notification: { notificationId: "owner-1", acknowledged: true } })));
    expect(current.onConfirmedUnreadCount).toHaveBeenCalledTimes(1);
    await act(async () => reread.resolve(response(inbox([notification("owner-1", { acknowledged: true })]))));
    await waitFor(() => expect(current.onConfirmedUnreadCount).toHaveBeenLastCalledWith(0));
    expect(await screen.findByText("已向後端重新核對通知狀態。")).toBeVisible();
  });

  it("retains a failed selection and its exact idempotency key across a denied retry", async () => {
    const keys: string[] = [];
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") { keys.push((init.headers as Record<string, string>)["Idempotency-Key"]); return Promise.resolve(response({}, 403)); }
      return Promise.resolve(response(inbox()));
    }));
    render(<ShellNotificationCenter {...props()} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "選取通知 owner-1" }));
    fireEvent.click(screen.getByRole("button", { name: "將選取通知標記已讀" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 403");
    expect(screen.getByRole("checkbox", { name: "選取通知 owner-1" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "將選取通知標記已讀" }));
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
    expect(screen.queryByText("已向後端重新核對通知狀態。")).not.toBeInTheDocument();
  });

  it("re-reads partial batch results without claiming the denied remainder succeeded", async () => {
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "POST") return Promise.resolve(url.includes("owner-1/")
        ? response({ notification: { notificationId: "owner-1", acknowledged: true } }) : response({}, 403));
      return Promise.resolve(response(inbox([notification("owner-1", { acknowledged: ++reads > 1 }), notification("owner-2")])));
    }));
    const current = props(); render(<ShellNotificationCenter {...current} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "選取通知 owner-1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "選取通知 owner-2" }));
    fireEvent.click(screen.getByRole("button", { name: "將選取通知標記已讀" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 403");
    expect(current.onConfirmedUnreadCount).toHaveBeenLastCalledWith(1);
    expect(screen.queryByRole("checkbox", { name: "選取通知 owner-1" })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "選取通知 owner-2" })).toBeChecked();
    expect(screen.queryByText("已向後端重新核對通知狀態。")).not.toBeInTheDocument();
  });

  it("hides write controls on a canonical VIEW-only response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox([notification()], false)))));
    render(<ShellNotificationCenter {...props()} />);
    expect(await screen.findByText("Notice owner-1")).toBeVisible();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "將選取通知標記已讀" })).not.toBeInTheDocument();
  });

  it.each([
    { kind: "root provenance", value: { ...inbox(), source: "seed-r4" } },
    { kind: "nested provenance", value: { ...inbox(), metadata: { fixture: true } } },
    { kind: "malformed read state", value: inbox([notification("bad", { acknowledged: "false" })]) },
    { kind: "count mismatch", value: { ...inbox(), unacknowledged: 0 } },
  ])("fails closed on production $kind without seed rows or a fabricated Bell count", async ({ value }) => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(value))));
    const current = props({ fixtureRows: [{ title: "POC-only-row" }] }); render(<ShellNotificationCenter {...current} />);
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("POC-only-row")).not.toBeInTheDocument();
    expect(screen.queryByText("Notice owner-1")).not.toBeInTheDocument();
    expect(current.onConfirmedUnreadCount).not.toHaveBeenCalled();
  });

  it("keeps explicit POC title-only fixtures labelled and readonly", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response({}, 503))));
    render(<ShellNotificationCenter {...props({ allowFixtureFallback: true, fixtureRows: [{ title: "POC-only-row", detail: "fixture" }] })} />);
    expect(screen.getByText("POC-only-row")).toBeVisible();
    expect(await screen.findByText("POC-only-row")).toBeVisible();
    expect(screen.getByText("FIXTURE · 唯讀")).toBeVisible();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it.each(["HTTP", "JSON", "failure"])("ignores A's late %s result after B becomes ready", async (phase) => {
    const pending = deferred<Response>(); const body = deferred<unknown>(); let calls = 0;
    const jsonStarted = vi.fn(() => body.promise);
    vi.stubGlobal("fetch", vi.fn(() => {
      if (++calls !== 1) return Promise.resolve(response(inbox([notification("B-row")])));
      return phase === "JSON" ? Promise.resolve({ ok: true, json: jsonStarted } as unknown as Response) : pending.promise;
    }));
    const current = props(); const view = render(<ShellNotificationCenter {...current} />);
    await waitFor(() => expect(phase === "JSON" ? jsonStarted.mock.calls.length : calls).toBe(1));
    view.rerender(<ShellNotificationCenter {...current} scopeKey="B-epoch2" />);
    expect(await screen.findByText("Notice B-row")).toBeVisible();
    await act(async () => { if (phase === "JSON") body.resolve(inbox([notification("A-row")]));
      else pending.resolve(phase === "failure" ? response({}, 503) : response(inbox([notification("A-row")]))); });
    expect(screen.getByText("Notice B-row")).toBeVisible();
    expect(screen.queryByText("Notice A-row")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["B", "A-B-A"])("does not let an old POST result overwrite a ready %s context", async (destination) => {
    const pending = deferred<Response>(); let calls = 0;
    const fetcher = vi.fn((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return pending.promise;
      return Promise.resolve(response(inbox([notification("same-id", { title: `Ready ${++calls}` })])));
    });
    vi.stubGlobal("fetch", fetcher); const current = props(); const view = render(<ShellNotificationCenter {...current} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "選取通知 same-id" }));
    fireEvent.click(screen.getByRole("button", { name: "將選取通知標記已讀" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    view.rerender(<ShellNotificationCenter {...current} scopeKey="B-epoch2" />);
    expect(await screen.findByText("Ready 2")).toBeVisible();
    if (destination === "A-B-A") {
      view.rerender(<ShellNotificationCenter {...current} scopeKey="A-epoch3" />);
      expect(await screen.findByText("Ready 3")).toBeVisible();
    }
    const expected = destination === "B" ? 2 : 3;
    await act(async () => pending.resolve(response({ notification: { notificationId: "same-id", acknowledged: true } })));
    expect(screen.getByText(`Ready ${expected}`)).toBeVisible(); expect(calls).toBe(expected);
    expect(screen.queryByText("已向後端重新核對通知狀態。")).not.toBeInTheDocument();
  });

  it("sends no data request and updates no count when the current session pin refuses", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const current = props({ createAuthenticatedReadGuard: () => async () => false });
    render(<ShellNotificationCenter {...current} />); await act(async () => {});
    expect(fetcher).not.toHaveBeenCalled(); expect(current.onConfirmedUnreadCount).not.toHaveBeenCalled();
  });

  it("supports StrictMode replay without reviving the cancelled effect", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox()))));
    render(<StrictMode><ShellNotificationCenter {...props()} /></StrictMode>);
    expect(await screen.findByText("Notice owner-1")).toBeVisible();
  });

  it("does not invent event time, category or mutation eligibility on valid legacy metadata", () => {
    const result = normalizeNotificationInbox({ items: [notification("legacy", { category: undefined, occurredAt: undefined, sourceDetail: undefined, sourceEntity: undefined })], unacknowledged: 1 });
    expect(result.rows[0].category).toBeNull(); expect(result.rows[0].occurredAt).toBeNull();
    expect(result.canAcknowledge).toBe(false);
  });
});


// Explicit HTTP integration controls render the real Store Ops consumer; these
// fixtures are transport contracts, not measured health or native source proof.
function storeState() {
  const issue = (id: string, title: string) => ({ id, title, storeId: "transport-store", storeName: "Source store",
    status: "new", severity: "medium", source: "forecastOps", ownerRoleId: "opsLead", ownerName: "Provided owner",
    slaDueAt: "2026-10-15T12:00:00Z", createdAt: "2026-10-15T08:00:00Z", updatedAt: "2026-10-15T08:00:00Z",
    evidenceIds: [], summary: "Provided issue source" });
  return { source: "operator-store-ops-production", stores: [{ id: "transport-store", name: "Source store",
    district: "Provided district", city: "Provided city", manager: "Provided owner",
    lights: { demand: null, operations: null, staffing: null, margin: null }, riskScore: null }],
    issues: [issue("unrelated-first", "Unrelated issue"), issue("resolved-materializer-issue", "Resolved source issue")],
    evidence: [], auditEvents: [], fourLightSummary: [], count: 2 };
}
function NotificationConsumer() {
  const [issue, setIssue] = useState<string | undefined>();
  return <><ShellNotificationCenter {...props()} onTargetSelect={(next) => setIssue(next.entityId)} />
    <DesignStoreOpsWorkspace initialIssueId={issue} onOpenWorkflow={vi.fn()} /></>;
}

it.each(["resolved-materializer-issue", "missing-or-denied-issue"])("notification source renders the real explicit %s consumer without first-row substitution", async (destination) => {
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(response(url.includes("/shell/notifications")
    ? inbox([notification(destination)]) : storeState()))));
  render(<NotificationConsumer />);
  await screen.findByLabelText("unrelated-first detail");
  fireEvent.click(await screen.findByRole("button", { name: `查看來源 ${destination}` }));
  if (destination === "resolved-materializer-issue") {
    const detail = await screen.findByLabelText("resolved-materializer-issue detail");
    expect(detail).toHaveTextContent("Resolved source issue");
    expect(screen.queryByLabelText("unrelated-first detail")).not.toBeInTheDocument();
  } else {
    expect(await screen.findByText("指定事件不存在、無權限或不在目前篩選範圍；未開啟其他事件。")).toBeVisible();
    expect(screen.queryByLabelText("unrelated-first detail")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Action rail")).not.toBeInTheDocument();
  }
});

it("shows an ingestion-run source id without routing it into unrelated Governance approvals", async () => {
  const current = props();
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox([notification("run-1", {
    category: "job", sourceEntity: { kind: "external_data.ingestion_run", id: "run-1" },
    sourceDetail: { state: "unavailable", reasonCode: "INGESTION_RUN_DETAIL_NOT_IMPLEMENTED" },
    target: { workspace: "govern", entityId: "run-1", tab: "data-quality" },
  })])))));
  render(<ShellNotificationCenter {...current} />);
  expect(await screen.findByText("匯入執行詳情入口尚未提供")).toBeVisible();
  expect(screen.getByText("來源：run-1")).toBeVisible();
  expect(screen.queryByRole("button", { name: /查看來源/ })).not.toBeInTheDocument();
  expect(current.onTargetSelect).not.toHaveBeenCalled();
});
