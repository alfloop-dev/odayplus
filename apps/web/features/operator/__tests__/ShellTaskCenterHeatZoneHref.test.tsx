import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { normalizeTaskCenter, ShellTaskCenter, taskSourceHref } from "../ShellTaskCenter";

function responseBody(target: unknown, sourceDetailAvailable = true) {
  const row = { taskId: "market-survey.assignment:asgn-a", sourceId: "asgn-a", sourceKind: "market-survey.assignment",
    taskType: "market-survey.assignment", title: "熱區查核指定熱區", status: "ASSIGNED", priority: null, owner: null,
    createdBy: "creator-a", createdAt: "2026-10-18T08:00:00Z", updatedAt: null, completed: false,
    requiresApproval: false, assigneeId: "field-a", assigneeName: null, slaDueAt: null, slaState: "none",
    sourceDetailAvailable, target };
  return { items: [row], count: 1, total: 1, selected: row, selectionUnavailable: false, activeCount: 1,
    viewCounts: { assigned: 1, approvals: 0, initiated: 0, completed: 0 },
    meta: { source: "operator-shell-canonical-tasks", coverage: { complete: false, retentionComplete: false,
      domains: [{ domain: "market-survey.assignment", state: "available", creatorAvailable: false, retainedCompletedStatesAvailable: true }] } } };
}
function nativeTarget(id: string, heatZoneId: unknown = id) {
  return { workspace: "network", tab: "areas", entityId: id, heatZoneId };
}
const initialURL = window.location.href;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, "", initialURL); });

it.each(["zone-a", "HZ/台北?A&B#%"])("retains exact API target identity through Task Center normalization: %s", (id) => {
  const row = normalizeTaskCenter(responseBody(nativeTarget(id))).rows[0];
  expect(row.target).toHaveProperty("heatZoneId", id);
  const href = taskSourceHref(row);
  expect(href).not.toBeNull();
  const url = new URL(href!, "https://console.example");
  expect(url.pathname).toBe("/operator");
  expect(url.searchParams.get("tab")).toBe("areas");
  expect(url.searchParams.get("entity")).toBe(id);
  expect(url.searchParams.get("heatZone")).toBe(id);
});

it.each([null, "zone-b"])("does not substitute a zone when the native target cannot be matched: %s", (other) => {
  const row = normalizeTaskCenter(responseBody(nativeTarget("zone-a", other))).rows[0];
  expect(taskSourceHref(row)).toBeNull();
});

it("does not offer a source link before the server admits source availability", () => {
  const row = normalizeTaskCenter(responseBody(nativeTarget("zone-a"), false)).rows[0];
  expect(taskSourceHref(row)).toBeNull();
});

it.each([3, ""])("rejects a malformed native ID from the API instead of ignoring that field: %s", (invalid) => {
  expect(() => normalizeTaskCenter(responseBody(nativeTarget("zone-a", invalid)))).toThrow("任務欄位格式不正確");
});

it("places the exact admitted zone link on the existing Task Center drawer action", async () => {
  window.history.replaceState(null, "", "/tasks/market-survey.assignment%3Aasgn-a?view=assigned");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(responseBody(nativeTarget("zone-a"))),
    { status: 200, headers: { "Content-Type": "application/json" } })));
  render(<ShellTaskCenter scopeKey="tenant-a-current" headers={{ "X-Operator-Role": "expansion-staff" }}
    createAuthenticatedReadGuard={(current) => async () => current()} onConfirmedActiveCount={vi.fn()} />);
  const drawer = await screen.findByRole("dialog");
  const open = await within(drawer).findByRole("button", { name: "查看來源詳情" });
  expect(open).toHaveAttribute("data-source-href", "/operator?ws=network&tab=areas&entity=zone-a&heatZone=zone-a");
  expect(within(drawer).queryByRole("button", { name: "核准" })).not.toBeInTheDocument();
});
