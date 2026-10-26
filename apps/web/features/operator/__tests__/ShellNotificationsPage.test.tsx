import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShellTaskCenter } from "../ShellTaskCenter";
import { ShellNotificationPage } from "../ShellNotificationPage";
import type { ShellNotificationCenterProps } from "../ShellNotificationCenter";

const originalLocation = `${window.location.pathname}${window.location.search}${window.location.hash}`;
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function notification(id: string, changes: Record<string, unknown> = {}) {
  return { notificationId: id, title: `Notice ${id}`, detail: `Actual detail ${id}`, tone: "warning", severity: "warning",
    category: "alert", occurredAt: "2026-10-15T08:00:00+00:00", acknowledged: false,
    sourceEntity: { kind: "forecastops.alert", id: `alert-${id}` }, sourceDetail: { state: "available" },
    target: { workspace: "store", tab: "forecastOps", entityId: `issue-${id}` }, ...changes };
}
function inbox(items: Record<string, unknown>[] = [notification("A")]) { return { items, unacknowledged: items.filter((item) => !item.acknowledged).length, actions: { acknowledge: { allowed: true } } }; }
function props(overrides: Partial<ShellNotificationCenterProps> = {}) {
  return { scopeKey: "A-epoch1", expectedSubjectId: "subject-A", headers: { "X-Operator-Role": "ops-lead" },
    createAuthenticatedReadGuard: (current: () => boolean) => async () => current(),
    onConfirmedUnreadCount: vi.fn(), onTargetSelect: vi.fn(), ...overrides };
}
function navigate(url: string) { window.history.replaceState(null, "", url); window.dispatchEvent(new PopStateEvent("popstate")); }
afterEach(() => { cleanup(); window.history.replaceState(null, "", originalLocation); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("complete notification route", () => {
  it("restores category/read/detail URL state and opens only its exact resolved source", async () => {
    window.history.replaceState(null, "", "/notifications?category=alert&read=unread&notification=A");
    const rows = [notification("A"), notification("B", { category: "job", acknowledged: true,
      sourceDetail: { state: "unavailable", reasonCode: "INGESTION_RUN_DETAIL_NOT_IMPLEMENTED" }, sourceEntity: { kind: "external_data.ingestion_run", id: "run-B" } })];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox(rows)))));
    const current = props(); render(<ShellNotificationPage {...current} />);
    const detail = await screen.findByRole("dialog", { name: "Notice A" });
    expect(within(detail).getByText("Actual detail A")).toBeVisible();
    expect(screen.getByRole("tab", { name: "警示" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("讀取狀態")).toHaveValue("unread");
    expect(screen.getByText("未讀通知：1")).toBeVisible();
    fireEvent.click(within(detail).getByRole("button", { name: "查看來源 issue-A" }));
    await waitFor(() => expect(current.onTargetSelect).toHaveBeenCalledWith({ workspace: "store", tab: "forecastOps", entityId: "issue-A" }, "Notice A"));
    fireEvent.click(within(detail).getByRole("button", { name: "關閉" }));
    expect(new URL(window.location.href).searchParams.has("notification")).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "Job" }));
    fireEvent.change(screen.getByLabelText("讀取狀態"), { target: { value: "read" } });
    expect(new URL(window.location.href).searchParams.get("category")).toBe("job");
    expect(new URL(window.location.href).searchParams.get("read")).toBe("read");
    expect(screen.getByText("Notice B")).toBeVisible();
    act(() => navigate("/notifications?category=alert&read=unread&notification=A"));
    expect(await screen.findByRole("dialog", { name: "Notice A" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "警示" })).toHaveAttribute("aria-selected", "true");
  });

  it("does not substitute the first notification or source when an explicit detail is missing", async () => {
    window.history.replaceState(null, "", "/notifications?notification=missing-or-denied");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox()))));
    const current = props(); render(<ShellNotificationPage {...current} />);
    const detail = await screen.findByRole("dialog", { name: "通知詳情" });
    expect(await within(detail).findByText("指定通知不存在或不在目前可讀範圍；未改開其他通知。")).toBeVisible();
    expect(within(detail).queryByText("Actual detail A")).not.toBeInTheDocument();
    expect(within(detail).queryByRole("button", { name: /查看來源/ })).not.toBeInTheDocument();
    expect(current.onTargetSelect).not.toHaveBeenCalled();
  });

  it("uses the original Drawer keyboard/focus flow and exact next/previous record IDs", async () => {
    window.history.replaceState(null, "", "/notifications");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox([notification("A"), notification("B")])))));
    render(<ShellNotificationPage {...props()} />);
    const opener = await screen.findByRole("button", { name: "查看通知詳情 A" }); opener.focus(); fireEvent.click(opener);
    const first = await screen.findByRole("dialog", { name: "Notice A" });
    expect(within(first).getByRole("button", { name: "關閉" })).toHaveFocus();
    fireEvent.click(within(first).getByRole("button", { name: "下一筆" }));
    const next = await screen.findByRole("dialog", { name: "Notice B" });
    expect(new URL(window.location.href).searchParams.get("notification")).toBe("B");
    fireEvent.click(within(next).getByRole("button", { name: "上一筆" }));
    expect(await screen.findByRole("dialog", { name: "Notice A" })).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(opener).toHaveFocus();
  });

  it("provides the personal-settings route without fetching the inbox or fabricating approval/system producers", async () => {
    window.history.replaceState(null, "", "/settings/notifications");
    const fetcher = vi.fn((_url: string) => Promise.resolve(response({ preferences: { channels: { inApp: true }, severityFloor: "warning", digest: "owner-value" },
      isDefault: false, severityLevels: ["info", "warning", "critical"], actions: { update: { allowed: false } } })));
    vi.stubGlobal("fetch", fetcher); render(<ShellNotificationPage {...props()} preferencesPage />);
    expect(await screen.findByRole("heading", { level: 1, name: "通知偏好" })).toBeVisible();
    expect(await screen.findByLabelText("inApp")).toBeDisabled();
    expect(fetcher.mock.calls.every(([url]) => url === "/api/v1/operator/shell/notifications/preferences")).toBe(true);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    act(() => navigate("/notifications"));
    expect(screen.getByText(/核准通知來源尚未取得/)).toBeVisible();
    expect(screen.getByRole("link", { name: "查看目前待核准任務" })).toHaveAttribute("href", "/tasks?view=approvals");
  });

  it("rechecks the current session before an exact source command and never navigates on refusal", async () => {
    window.history.replaceState(null, "", "/notifications"); let allowed = true;
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(inbox()))));
    const current = props({ createAuthenticatedReadGuard: (isCurrent) => async () => allowed && isCurrent() });
    render(<ShellNotificationPage {...current} />);
    const source = await screen.findByRole("button", { name: "查看來源 issue-A" });
    allowed = false; fireEvent.click(source); await act(async () => {});
    expect(current.onTargetSelect).not.toHaveBeenCalled();
  });
});


it.each(["current request", "replaced request"])("approval notification opens the real exact Task consumer for a %s", async (scenario) => {
  const key = "a".repeat(64); const taskId = "sitescore.decision:actual-round-2";
  const notice = { ...notification(`approval-request:${key}`), category: "approval", target: null,
    sourceEntity: { kind: "sitescore.decision", id: "actual-round-2" }, sourceTaskId: taskId, sourceRequestKey: key,
    sourceDetail: { state: "available", consumer: "task-center" } };
  const task = { taskId, sourceId: "actual-round-2", sourceKind: "sitescore.decision", taskType: "sitescore.decision", title: "Actual round two",
    status: "PENDING_REVIEW", priority: null, owner: null, createdBy: "actual-author", createdAt: "2026-10-15T08:00:00Z", updatedAt: "2026-10-15T09:00:00Z",
    completed: false, requiresApproval: true, slaState: "none", sourceDetailAvailable: true, reportId: "frozen-report-2", reportVersion: 2,
    previousDecisionId: "actual-round-1", notificationRequest: { key },
    target: { workspace: "network", tab: "review", entityId: "actual-candidate", candidateId: "actual-candidate", reviewId: "actual-round-2" } };
  const fetcher = vi.fn((input: string) => {
    if (input.includes("/shell/notifications")) return Promise.resolve(response({ ...inbox([notice]), meta: { approvalCoverage: { complete: false, state: "partial" } } }));
    const url = new URL(input, window.location.origin);
    expect(url.searchParams.get("taskId")).toBe(taskId); expect(url.searchParams.get("requestKey")).toBe(key);
    const selected = scenario === "current request" ? task : null;
    return Promise.resolve(response({ items: [task], selected, selectionUnavailable: !selected, count: 1, total: 1, activeCount: 1,
      viewCounts: { assigned: 0, approvals: 1, initiated: 0, completed: 0 },
      meta: { coverage: { complete: false, retentionComplete: false, domains: [] } } }));
  });
  vi.stubGlobal("fetch", fetcher); window.history.replaceState(null, "", "/notifications");
  function Consumer() {
    const [taskOpen, setTaskOpen] = useState(false);
    return taskOpen ? <ShellTaskCenter scopeKey="same-current-reviewer" headers={{}} createAuthenticatedReadGuard={(current) => async () => current()}
      onConfirmedActiveCount={vi.fn()} /> : <ShellNotificationPage {...props()} onTaskSelect={(id, requestKey) => {
        window.history.replaceState(null, "", `/tasks/${encodeURIComponent(id)}?${new URLSearchParams({ view: "approvals", requestKey })}`); setTaskOpen(true);
      }} />;
  }
  render(<Consumer />);
  fireEvent.click(await screen.findByRole("button", { name: `查看核准任務 ${taskId}` }));
  const dialog = await screen.findByRole("dialog");
  if (scenario === "current request") {
    expect(await within(dialog).findByText("frozen-report-2／2")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "開啟完整審查" })).toHaveAttribute("data-source-href", "/operator?ws=network&tab=review&entity=actual-candidate&candidate=actual-candidate&review=actual-round-2");
  } else {
    expect(await within(dialog).findByText(/指定任務不存在、無權限/)).toBeVisible();
    expect(within(dialog).queryByRole("button", { name: "開啟完整審查" })).not.toBeInTheDocument();
  }
});


it("re-reads an exact Task when browser history changes only its request key and rejects a mismatched round", async () => {
  const first = "a".repeat(64); const second = "b".repeat(64); const id = "sitescore.decision:same-record";
  const row = { taskId: id, sourceId: "same-record", sourceKind: "sitescore.decision", taskType: "sitescore.decision", title: "Current exact record",
    status: "PENDING_REVIEW", completed: false, requiresApproval: true, slaState: "none", sourceDetailAvailable: false, notificationRequest: { key: first } };
  const requested: Array<string | null> = [];
  vi.stubGlobal("fetch", vi.fn((input: string) => {
    requested.push(new URL(input, window.location.origin).searchParams.get("requestKey"));
    return Promise.resolve(response({ items: [row], selected: row, count: 1, total: 1, activeCount: 1, selectionUnavailable: false,
      viewCounts: { assigned: 0, approvals: 1, initiated: 0, completed: 0 }, meta: { coverage: { complete: false, retentionComplete: false, domains: [] } } }));
  }));
  window.history.replaceState(null, "", `/tasks/${encodeURIComponent(id)}?view=approvals&requestKey=${first}`);
  render(<ShellTaskCenter scopeKey="same-authorized-account" headers={{}} createAuthenticatedReadGuard={(current) => async () => current()} onConfirmedActiveCount={vi.fn()} />);
  expect(await screen.findByRole("dialog", { name: "Current exact record" })).toBeVisible();
  act(() => navigate(`/tasks/${encodeURIComponent(id)}?view=approvals&requestKey=${second}`));
  const detail = screen.getByRole("dialog");
  expect(await within(detail).findByRole("alert")).toHaveTextContent("指定核准請求已更換");
  expect(requested).toEqual([first, second]);
  expect(within(detail).queryByRole("button", { name: "開啟完整審查" })).not.toBeInTheDocument();
});
