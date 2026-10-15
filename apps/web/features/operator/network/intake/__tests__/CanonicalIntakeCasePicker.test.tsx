import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanonicalIntakeCasePicker } from "../CanonicalIntakeCasePicker";
import type { CanonicalIntakeReadContext } from "../canonicalIntakeReadClient";

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const intake = (n: number) => ({ intake_id: id(n), tenant_id: id(10), submitter_subject_id: id(11), correlation_id: id(12),
  intake_method: "MANUAL", processing_state: "NEEDS_REVIEW", version: 3, submitted_at: "2026-10-15T08:00:00Z",
  scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } });
const matchCase = (n: number) => ({ match_case_id: id(n + 20), intake_id: id(n), intake_version: 3, version: 1,
  status: "PENDING_REVIEW", outcome: "POSSIBLE_MATCH", candidates: [{ match_candidate_id: id(n + 30), property_id: id(n + 40),
    listing_id: null, rank: 1, confidence: "0.9000", observed_property_version: 2, observed_listing_version: null }] });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
const headers = { "X-Operator-Role": "expansion-manager" };
function makeContext(handler?: (path: string) => Response | Promise<Response>, key = "A"): CanonicalIntakeReadContext {
  return { contextKey: key, headers, fetchImpl: vi.fn(async (input) => {
    const path = String(input);
    if (handler) return handler(path);
    if (path.startsWith("/api/v1/canonical-intakes?")) return json({ items: [intake(1), intake(2)], next_cursor: null });
    const selected = path.includes(id(2)) ? 2 : 1;
    return json(path.endsWith("/match-case") ? matchCase(selected) : intake(selected));
  }) };
}
const open = () => fireEvent.click(screen.getByRole("button", { name: "選取收件／比對案件" }));
const select = (n: number) => fireEvent.click(screen.getByRole("button", { name: new RegExp(id(n)) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("native Intake and case selection", () => {
  it("does no eager read, selects no default row and reads only an explicitly chosen actual context", async () => {
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("global fetch must not be used"); }));
    const context = makeContext(); render(<CanonicalIntakeCasePicker context={context} />);
    expect(context.fetchImpl).not.toHaveBeenCalled();
    open(); await screen.findByRole("button", { name: new RegExp(id(1)) });
    expect(screen.queryByRole("region", { name: "所選收件與比對案件" })).not.toBeInTheDocument();
    expect(context.fetchImpl).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: new RegExp(id(1)) })).toHaveAttribute("aria-pressed", "false");
    select(2);
    const detail = await screen.findByRole("region", { name: "所選收件與比對案件" });
    await within(detail).findByText(new RegExp(`比對案件 ${id(22)}`));
    expect(within(detail).getByText(new RegExp(`主體 ${id(42)}`))).toBeVisible();
    expect(vi.mocked(context.fetchImpl).mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/canonical-intakes?page_size=25", `/api/v1/canonical-intakes/${id(2)}`, `/api/v1/intakes/${id(2)}/match-case`,
    ]);
    select(2);
    expect(within(detail).getByText(new RegExp(`比對案件 ${id(22)}`))).toBeVisible();
    expect(context.fetchImpl).toHaveBeenCalledTimes(3);
  });

  it.each([401, 403, 503])("renders denied/unavailable %s as an error rather than an empty collection", async (status) => {
    render(<CanonicalIntakeCasePicker context={makeContext(() => json({ detail: "unavailable" }, status))} />); open();
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("目前可讀範圍沒有收件。")).not.toBeInTheDocument();
  });

  it("shows an empty collection only after an actual successful empty read", async () => {
    render(<CanonicalIntakeCasePicker context={makeContext(() => json({ items: [], next_cursor: null }))} />); open();
    expect(await screen.findByText("目前可讀範圍沒有收件。")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["CANONICAL_MATCH_CASE_NOT_FOUND", "PROPERTY_NOT_FOUND"])("distinguishes absent case from other 404: %s", async (detail) => {
    const normal = makeContext();
    const context = makeContext((path) => path.endsWith("/match-case") ? json({ detail }, 404) : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    if (detail === "CANONICAL_MATCH_CASE_NOT_FOUND") expect(await screen.findByText("這筆收件目前沒有比對案件。")).toBeVisible();
    else { expect(await screen.findByRole("alert")).toBeVisible(); expect(screen.queryByText("這筆收件目前沒有比對案件。")).not.toBeInTheDocument(); }
    expect(vi.mocked(context.fetchImpl).mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
  });

  it("discards late detail JSON when another Intake is explicitly selected", async () => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const normal = makeContext();
    const context = makeContext((path) => path === `/api/v1/canonical-intakes/${id(1)}` ? late : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1); await reading.promise;
    select(2);
    const detail = screen.getByRole("region", { name: "所選收件與比對案件" });
    await within(detail).findByText(new RegExp(`比對案件 ${id(22)}`));
    await act(async () => { body.resolve(intake(1)); });
    expect(within(detail).queryByText(new RegExp(id(1)))).not.toBeInTheDocument();
    expect(within(detail).getByText(new RegExp(`比對案件 ${id(22)}`))).toBeVisible();
    expect(vi.mocked(context.fetchImpl).mock.calls.map(([path]) => path)).not.toContain(`/api/v1/intakes/${id(1)}/match-case`);
  });

  it.each(["contextKey", "transport", "headers"])("closes and unmounts old data immediately on %s change", async (change) => {
    const body = deferred<unknown>(); const reading = deferred<boolean>(); const late = json({});
    late.json = () => { reading.resolve(true); return body.promise; };
    const context = makeContext(() => late); const view = render(<CanonicalIntakeCasePicker context={context} />);
    open(); await reading.promise;
    const replacement = change === "contextKey" ? { ...context, contextKey: "B" }
      : change === "headers" ? { ...context, headers: { "X-Operator-Role": "pm-audit" } }
      : { ...context, fetchImpl: makeContext().fetchImpl };
    view.rerender(<CanonicalIntakeCasePicker context={replacement} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => { body.resolve({ items: [intake(1)], next_cursor: null }); });
    expect(screen.queryByText(new RegExp(id(1)))).not.toBeInTheDocument();
  });

  it("clears selected details on page changes and passes only the received cursor", async () => {
    const normal = makeContext(); const context = makeContext((path) => path.startsWith("/api/v1/canonical-intakes?")
      ? json({ items: [intake(path.includes("cursor=") ? 2 : 1)], next_cursor: path.includes("cursor=") ? null : "opaque+/=" }) : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    await screen.findByText(new RegExp(`比對案件 ${id(21)}`));
    fireEvent.click(screen.getByRole("button", { name: "下一頁" }));
    expect(screen.queryByRole("region", { name: "所選收件與比對案件" })).not.toBeInTheDocument();
    await screen.findByRole("button", { name: new RegExp(id(2)) });
    expect(screen.queryByRole("button", { name: new RegExp(id(1)) })).not.toBeInTheDocument();
    const lastPath = String(vi.mocked(context.fetchImpl).mock.calls.at(-1)?.[0]);
    expect(new URL(lastPath, "https://unit.invalid").searchParams.get("cursor")).toBe("opaque+/=");
  });

  it("clears old rows immediately while refreshing and retains a failed read as an error", async () => {
    const refresh = deferred<Response>(); const normal = makeContext(); let calls = 0;
    const context = makeContext((path) => ++calls === 1 ? normal.fetchImpl(path) : refresh.promise);
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) });
    fireEvent.click(screen.getByRole("button", { name: "重新讀取" }));
    expect(screen.queryByRole("button", { name: new RegExp(id(1)) })).not.toBeInTheDocument();
    await act(async () => { refresh.resolve(json({ detail: "denied" }, 403)); });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("目前可讀範圍沒有收件。")).not.toBeInTheDocument();
  });

  it("uses the existing dialog Escape and focus restoration contract", async () => {
    render(<CanonicalIntakeCasePicker context={makeContext()} />); const trigger = screen.getByRole("button", { name: "選取收件／比對案件" });
    trigger.focus(); open(); await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});
