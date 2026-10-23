import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Drawer } from "@oday-plus/ui";
import { normalizeTaskCenter, ShellTaskCenter, taskSourceHref, type ShellTaskCenterProps } from "../ShellTaskCenter";

// Synthetic transport/session boundaries for this unit suite; no actual login,
// complete historical coverage, approval decision or human UAT is asserted.
function task(id = "intervention:owner-a", extra: Record<string, unknown> = {}) {
  return { taskId: id, sourceId: id.split(":").slice(1).join(":"), sourceKind: "intervention", taskType: "intervention",
    title: `Task ${id}`, status: "PENDING_APPROVAL", priority: null, owner: null, createdBy: "subject-a",
    createdAt: "2026-10-15T08:00:00Z", updatedAt: null, completed: false, requiresApproval: true,
    assigneeId: "subject-a", assigneeName: null, slaDueAt: null, slaState: "none", sourceDetailAvailable: true,
    target: { workspace: "today", tab: "interventions", entityId: id.split(":").slice(1).join(":") }, ...extra };
}
function envelope(items: (ReturnType<typeof task> & { expiresAt?: string | null })[] = [task()], selected: ReturnType<typeof task> | null = null, missing = false) {
  return { items, count: items.length, total: Math.max(2, items.length), selected, selectionUnavailable: missing, activeCount: 2,
    viewCounts: { assigned: 2, approvals: 1, initiated: 1, completed: 0 },
    meta: { source: "operator-shell-canonical-tasks", coverage: { complete: false, retentionComplete: false,
      domains: [{ domain: "listing", state: "available", creatorAvailable: false, retainedCompletedStatesAvailable: false }] } } };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function props(extra: Partial<ShellTaskCenterProps> = {}): ShellTaskCenterProps {
  return { scopeKey: "A-epoch-1", headers: { "X-Operator-Role": "ops-lead" },
    createAuthenticatedReadGuard: (current) => async () => current(), onConfirmedActiveCount: vi.fn(), ...extra };
}
const originalURL = window.location.href;
beforeEach(() => { window.history.replaceState(null, "", "/tasks?view=assigned"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, "", originalURL); });

describe("Task Center original table/drawer and canonical owner contract", () => {
  it("renders all eight compact columns and incomplete creator/history coverage without fabricated dates or priority", async () => {
    const current = props(); vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(envelope()))));
    render(<ShellTaskCenter {...current} />);
    const table = await screen.findByRole("table", { name: "任務中心" });
    await within(table).findByText("Task intervention:owner-a");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(table.parentElement).toHaveAttribute("data-density", "compact");
    expect(screen.getByText(/空清單不代表全部完成/)).toBeVisible();
    expect(within(table).getByText("未提供 SLA")).toBeVisible();
    expect(within(table).getAllByText("未提供")).toHaveLength(2);
    expect(current.onConfirmedActiveCount).toHaveBeenCalledWith(2);
  });

  it("uses exact record IDs for drawer/previous/next/deep link and retains URL filters and sort", async () => {
    const items = [task(), task("intervention:owner-b")];
    vi.stubGlobal("fetch", vi.fn((input: string) => {
      const url = new URL(input, window.location.origin); const selected = url.searchParams.get("taskId");
      return Promise.resolve(response(envelope(items, items.find((row) => row.taskId === selected) ?? null)));
    }));
    render(<ShellTaskCenter {...props()} />);
    const table = screen.getByRole("table", { name: "任務中心" });
    fireEvent.click(await within(table).findByText("Task intervention:owner-a"));
    const dialog = await screen.findByRole("dialog", { name: "Task intervention:owner-a" });
    expect(within(dialog).getByText("subject-a")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "開啟完整審查" })).toHaveAttribute("data-source-href", "/operator?ws=today&tab=interventions&entity=owner-a");
    expect(within(dialog).queryByRole("button", { name: /^(核准|駁回)$/ })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "下一筆" }));
    expect(await screen.findByRole("dialog", { name: "Task intervention:owner-b" })).toBeVisible();
    expect(window.location.pathname).toBe("/tasks/intervention%3Aowner-b");
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(window.location.pathname).toBe("/tasks");
    fireEvent.click(screen.getByRole("tab", { name: /我發起/ }));
    await waitFor(() => expect(window.location.search).toContain("view=initiated"));
    fireEvent.change(screen.getByLabelText("SLA"), { target: { value: "breached" } });
    fireEvent.click(screen.getByRole("button", { name: "最後更新" }));
    expect(window.location.search).toContain("sla=breached");
    expect(window.location.search).toContain("sort=updatedAt");
  });

  it("restores URL selection on popstate and shows explicit missing/denied without selecting the first visible task", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string) => {
      const id = new URL(input, window.location.origin).searchParams.get("taskId");
      return Promise.resolve(response(envelope([task()], null, Boolean(id))));
    }));
    render(<ShellTaskCenter {...props()} />);
    await screen.findAllByText("Task intervention:owner-a");
    window.history.pushState(null, "", "/tasks/missing?view=completed&sla=none");
    fireEvent.popState(window);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/指定任務不存在、無權限/);
    expect(within(dialog).queryByText("Task intervention:owner-a")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "開啟完整審查" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /已完成/ })).toHaveAttribute("aria-selected", "true");
  });

  it.each([403, 503])("preserves truthful unavailable state and no confirmed badge on HTTP %s, then supports retry", async (status) => {
    let calls = 0; const current = props();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(++calls === 1 ? response({}, status) : response(envelope([])))));
    render(<ShellTaskCenter {...current} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(`HTTP ${status}`);
    expect(current.onConfirmedActiveCount).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重試" }));
    await waitFor(() => expect(current.onConfirmedActiveCount).toHaveBeenCalledWith(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("discards an old A HTTP failure after B is ready, including A→B→A context epochs", async () => {
    const old = deferred<Response>(); let reads = 0;
    vi.stubGlobal("fetch", vi.fn(() => ++reads === 1 ? old.promise : Promise.resolve(response(envelope([task("intervention:B")])))));
    const current = props(); const view = render(<ShellTaskCenter {...current} />);
    await waitFor(() => expect(reads).toBe(1));
    view.rerender(<ShellTaskCenter {...current} scopeKey="B-epoch-2" />);
    await screen.findAllByText("Task intervention:B");
    view.rerender(<ShellTaskCenter {...current} scopeKey="A-epoch-3" />);
    await waitFor(() => expect(reads).toBe(3));
    await act(async () => old.resolve(response({}, 503)));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getAllByText("Task intervention:B").length).toBeGreaterThan(0);
  });

  it("does not adopt HTTP/JSON after the current authenticated-read pin denies it", async () => {
    const body = deferred<unknown>(); let reads = 0; let allowed = true; let jsonStarted = false;
    vi.stubGlobal("fetch", vi.fn(() => { reads += 1; return Promise.resolve({ ok: true, json: () => { jsonStarted = true; return body.promise; } } as Response); }));
    const current = props({ createAuthenticatedReadGuard: (isCurrent) => async () => allowed && isCurrent() });
    render(<ShellTaskCenter {...current} />);
    await waitFor(() => expect(jsonStarted).toBe(true)); expect(reads).toBe(1); allowed = false;
    await act(async () => body.resolve(envelope()));
    expect(screen.queryByText("Task intervention:owner-a")).not.toBeInTheDocument();
    expect(current.onConfirmedActiveCount).not.toHaveBeenCalled();
  });

  it("discards an old JSON success after the new scope is ready without clearing its data or count", async () => {
    const old = deferred<unknown>(); let jsonStarted = false; let reads = 0;
    vi.stubGlobal("fetch", vi.fn(() => ++reads === 1
      ? Promise.resolve({ ok: true, json: () => { jsonStarted = true; return old.promise; } } as Response)
      : Promise.resolve(response(envelope([task("intervention:B-ready")])))));
    const current = props(); const view = render(<ShellTaskCenter {...current} />);
    await waitFor(() => expect(jsonStarted).toBe(true));
    view.rerender(<ShellTaskCenter {...current} scopeKey="B-ready-epoch" />);
    await screen.findAllByText("Task intervention:B-ready");
    await act(async () => old.resolve(envelope([task("intervention:old-A")])));
    expect(screen.queryByText("Task intervention:old-A")).not.toBeInTheDocument();
    expect(screen.getAllByText("Task intervention:B-ready").length).toBeGreaterThan(0);
    expect(current.onConfirmedActiveCount).toHaveBeenCalledTimes(1);
  });

  it("refuses a wrong selected-row identity instead of opening the first authorized task", async () => {
    window.history.replaceState(null, "", "/tasks/explicit-request?view=assigned");
    const current = props(); vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(envelope([task()], task())))));
    render(<ShellTaskCenter {...current} />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("識別碼無法核對");
    expect(within(dialog).queryByText("Task intervention:owner-a")).not.toBeInTheDocument();
    expect(current.onConfirmedActiveCount).not.toHaveBeenCalled();
  });

  it("rejects production fixture provenance while explicit POC keeps its label and real empty coverage", async () => {
    const payload = { ...envelope([]), source: "seed-r4" };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(payload))));
    const view = render(<ShellTaskCenter {...props()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("示範來源");
    view.rerender(<ShellTaskCenter {...props()} scopeKey="poc-epoch" allowFixtureData />);
    expect(screen.getByText(/POC：/)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText(/空清單不代表全部完成/)).toBeVisible();
  });

  it("uses the actual candidate/review query contract, never a source ID guessed as an unrelated governance tab", () => {
    const row = normalizeTaskCenter(envelope([task("approval:r1", {
      target: { workspace: "network", tab: "review", entityId: "candidate-a", candidateId: "candidate-a", reviewId: "review-2" },
    })])).rows[0];
    expect(taskSourceHref(row)).toBe("/operator?ws=network&tab=review&entity=candidate-a&candidate=candidate-a&review=review-2");
    expect(taskSourceHref({ ...row, target: { workspace: "govern", tab: "data-quality", entityId: "run-a" } })).toBeNull();
  });

  it("keeps Drawer keyboard focus inside, uses the latest close callback and restores the original trigger", () => {
    const first = vi.fn(); const latest = vi.fn(); const trigger = document.createElement("button");
    document.body.append(trigger); trigger.focus();
    const view = render(<Drawer open onClose={first} title="Focus owner"><button>last action</button></Drawer>);
    const dialog = screen.getByRole("dialog"); const close = within(dialog).getByRole("button", { name: "關閉" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(within(dialog).getByRole("button", { name: "last action" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" }); expect(close).toHaveFocus();
    view.rerender(<Drawer open onClose={latest} title="Focus owner"><button>last action</button></Drawer>);
    fireEvent.keyDown(document, { key: "Escape" }); expect(latest).toHaveBeenCalledOnce(); expect(first).not.toHaveBeenCalled();
    view.unmount(); expect(trigger).toHaveFocus(); trigger.remove();
  });
});


it("shows a native Survey task deadline separately from unprovided SLA and does not invent a HeatZone source link", async () => {
  const expiresAt = "2026-10-18T08:00:00+00:00";
  const row = task("market-survey.assignment:asgn-owned", { sourceKind: "market-survey.assignment", taskType: "market-survey.assignment",
    title: "熱區查核 actual-zone", status: "ASSIGNED", requiresApproval: false,
    expiresAt, sourceDetailAvailable: false, target: null });
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(envelope([row])))));
  render(<ShellTaskCenter {...props()} />);
  const table = await screen.findByRole("table", { name: "任務中心" });
  await within(table).findByText("熱區查核 actual-zone");
  expect(within(table).getByText(/任務期限/)).toBeVisible();
  expect(within(table).getByText("未提供 SLA")).toBeVisible();
  expect(within(table).getByText(expiresAt)).toHaveAttribute("datetime", expiresAt);
  const parsed = normalizeTaskCenter(envelope([row])).rows[0];
  expect(parsed.expiresAt).toBe(expiresAt);
  expect(taskSourceHref(parsed)).toBeNull();
  expect(() => normalizeTaskCenter(envelope([{ ...row, expiresAt: "not-a-time" }]))).toThrow(/任務欄位/);
});
