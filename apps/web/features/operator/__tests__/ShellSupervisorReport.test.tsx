import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShellNotificationPage } from "../ShellNotificationPage";
import { ShellSupervisorReport } from "../ShellSupervisorReport";

const originalURL = window.location.href;
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function report(id = "actual-form") { return { source: "canonical-franchisee-report-owner", report: { reportId: id, subjectId: "actual-franchisee",
  storeId: "actual-store", category: "equipment", message: `Saved result ${id}`, status: "received", createdAt: "2026-10-15T23:00:00Z" } }; }
function props() { return { scopeKey: "current-supervisor-A", reportId: "actual-form", headers: {},
  createAuthenticatedReadGuard: (current: () => boolean) => async () => current() }; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
afterEach(() => { cleanup(); window.history.replaceState(null, "", originalURL); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("resolves the actual notification into its real exact report consumer without an unrelated first row", async () => {
  const fetcher = vi.fn((url: string) => Promise.resolve(response(url.includes("/shell/notifications")
    ? { items: [{ notificationId: "franchisee-report:actual-form", title: "Actual saved report", detail: "Persisted form available",
      category: "alert", tone: "info", severity: "info", acknowledged: false, occurredAt: "2026-10-15T23:00:00Z", target: null,
      sourceEntity: { kind: "franchisee.report", id: "actual-form" }, sourceReportId: "actual-form",
      sourceDetail: { state: "available", consumer: "supervisor-report" } }], unacknowledged: 1, actions: { acknowledge: { allowed: false } } }
    : report())));
  vi.stubGlobal("fetch", fetcher); window.history.replaceState(null, "", "/notifications");
  function Consumer() {
    const [selected, setSelected] = useState<string | undefined>();
    return <ShellNotificationPage scopeKey="current-supervisor" expectedSubjectId="actual-supervisor" headers={{}}
      createAuthenticatedReadGuard={(current) => async () => current()} onConfirmedUnreadCount={vi.fn()} onTargetSelect={vi.fn()}
      reportId={selected} onReportSelect={setSelected} />;
  }
  render(<Consumer />);
  fireEvent.click(await screen.findByRole("button", { name: "查看回報 actual-form" }));
  expect(await screen.findByText("Saved result actual-form")).toBeVisible();
  expect(screen.getByText("actual-franchisee")).toBeVisible();
  expect(fetcher).toHaveBeenLastCalledWith("/api/v1/operator/shell/franchisee/reports/actual-form", expect.objectContaining({ credentials: "include", cache: "no-store" }));
  expect(screen.getByText(/未提供 Email 或外部通道的投遞收條/)).toBeVisible();
});

it.each(["missing", "wrong id", "fixture"])("keeps %s report detail unavailable without another source", async (kind) => {
  const value = kind === "wrong id" ? report("unrelated-first") : kind === "fixture" ? { ...report(), metadata: { fixture: true } } : {};
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response(value, kind === "missing" ? 404 : 200))));
  render(<ShellSupervisorReport {...props()} />);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("Saved result unrelated-first")).not.toBeInTheDocument();
  expect(screen.queryByText("actual-franchisee")).not.toBeInTheDocument();
});

it.each(["HTTP", "JSON", "failure"])("does not let old supervisor %s replace a new scoped exact report", async (phase) => {
  const pending = deferred<Response>(); const body = deferred<unknown>(); let calls = 0;
  const json = vi.fn(() => body.promise);
  vi.stubGlobal("fetch", vi.fn(() => ++calls === 1 ? phase === "JSON"
    ? Promise.resolve({ ok: true, json } as unknown as Response) : pending.promise
    : Promise.resolve(response(report("current-B")))));
  const view = render(<ShellSupervisorReport {...props()} />);
  await waitFor(() => expect(phase === "JSON" ? json.mock.calls.length : calls).toBe(1));
  view.rerender(<ShellSupervisorReport {...props()} scopeKey="supervisor-B-epoch2" reportId="current-B" />);
  expect(await screen.findByText("Saved result current-B")).toBeVisible();
  await act(async () => { if (phase === "JSON") body.resolve(report()); else pending.resolve(response(report(), phase === "failure" ? 503 : 200)); });
  expect(screen.getByText("Saved result current-B")).toBeVisible();
  expect(screen.queryByText("Saved result actual-form")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("sends no report request on a refused current session pin", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<ShellSupervisorReport {...props()} createAuthenticatedReadGuard={() => async () => false} />);
  await act(async () => {}); expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByText("Saved result actual-form")).not.toBeInTheDocument();
});
