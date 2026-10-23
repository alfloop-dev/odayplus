import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InterventionWorkspace } from "../InterventionWorkspace";
import { COMMAND_LABELS, type LifecycleCommand } from "../lifecycleCommandContract";
import type { ActorScope } from "../interventionContract";
import fixture from "./fixtures/nativeInterventionLifecycle.json";
const samples = fixture.samples;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const copy = <T,>(v: T): T => structuredClone(v);
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
type Options = { lost?: boolean; advanced?: boolean; missingProof?: boolean; deny?: boolean; pending?: boolean; held?: Promise<Response>; rotateJSON?: "post" | "proof" | "label" | "child"; rotation?: "actor" | "tenant" | "expiry" };
function mount(sample: typeof samples[number], options: Options = {}) {
  let current = { ...sample.scope } as ActorScope, accepted = false, writes = 0, contextRequests = 0;
  const contextsAtPost: number[] = [];
  const confirmed = vi.fn(), selected = vi.fn();
  const rotate = () => { current = { ...current, ...(options.rotation === "actor" ? { actorSubjectId: "changed-actor" } : options.rotation === "expiry" ? { expiresAt: current.expiresAt + 1 } : { tenantId: "changed-tenant" }) }; };
  const response = (body: unknown, stage: string) => {
    const r = json(body); if (stage === options.rotateJSON) { const read = r.json.bind(r); r.json = async () => { const result = await read(); rotate(); return result; }; } return r;
  };
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), parsed = new URL(url, "http://localhost");
    if (url === "/auth/operator-context") return json(current);
    if (parsed.pathname.endsWith("/command-context")) {
      contextRequests += 1;
      if (options.deny) return json(sample.nativeCapabilityDenial?.body ?? { detail: "forbidden" }, 403);
      const key = parsed.searchParams.get("command")!;
      const caps = options.pending ? sample.afterCapabilities : sample.capabilities;
      return json((caps as unknown as Record<string, unknown>)?.[key] ?? { detail: "capability unavailable" }, (caps as unknown as Record<string, unknown>)?.[key] ? 200 : 403);
    }
    if (init?.method === "POST") {
      contextsAtPost.push(contextRequests);
      writes += 1; accepted = true;
      if (options.held) return options.held;
      if (options.lost && writes === 1) throw new TypeError("Simulated response loss for the actual native committed fixture");
      return response(options.advanced ? sample.replayAfterAdvanceReceipt : sample.receipt, "post");
    }
    if (parsed.pathname.endsWith("/label")) return response(sample.label, "label");
    if (sample.child && parsed.pathname.endsWith("/" + sample.child.intervention_id)) return response(sample.child, "child");
    if (parsed.searchParams.has("lifecycle_correlation_id")) {
      const body = copy(options.advanced ? sample.advancedAfter : sample.after);
      if (options.missingProof && body) (body as unknown as Record<string, unknown>).lifecycle_audit = null;
      return response(body, "proof");
    }
    return json(options.pending ? sample.after : accepted ? options.advanced ? sample.advancedAfter : sample.after : sample.before);
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(sample.key as ReturnType<Crypto["randomUUID"]>).mockReturnValueOnce(sample.correlation as ReturnType<Crypto["randomUUID"]>);
  window.history.replaceState(null, "", "/operator?ws=today&tab=interventions&entity=" + sample.before.intervention_id);
  const view = render(<InterventionWorkspace selectedId={sample.before.intervention_id} onSelect={selected} onDecisionConfirmed={confirmed} />);
  return { fetch, confirmed, selected, view, rotate, contextsAtPost, posts: () => fetch.mock.calls.filter(([, init]) => init?.method === "POST"), contexts: () => fetch.mock.calls.filter(([url]) => String(url).includes("/command-context")) };
}
async function open(command: LifecycleCommand) {
  const button = await screen.findByRole("button", { name: COMMAND_LABELS[command] }); await waitFor(() => expect(button).toBeEnabled()); button.focus(); fireEvent.click(button);
  await waitFor(() => expect(screen.getByTestId("lifecycle-command-dialog")).toBeInTheDocument());
}
function fill(sample: typeof samples[number]) {
  const b = sample.business as unknown as Record<string, unknown>;
  const change = (name: RegExp, value: unknown) => fireEvent.change(screen.getByLabelText(name), { target: { value: String(value) } });
  if (sample.command === "assign") { change(/^受指派者/, b.assignee); change(/^指派標籤/, b.role); }
  if (sample.command === "eligibility") { change(/^資格結果/, b.eligible ? "eligible" : "ineligible"); change(/^人工檢查理由/, (b.reasons as string[]).join("\n")); }
  if (sample.command === "action") change(/^價格調整百分比$/, (b.action_spec as Record<string, unknown>).price_change_pct);
  if (sample.command === "conflict-check") { if (b.allow_overlap) fireEvent.click(screen.getByLabelText("允許重疊衝突")); change(/^衝突處理理由/, b.reason); }
  if (["close", "adjust", "stop", "rollback"].includes(sample.command)) change(/^操作理由/, b.reason);
  if (sample.command === "close" && b.follow_up) fireEvent.click(screen.getByLabelText("建立追蹤干預案"));
  if (sample.command === "outcomes") {
    for (const [label, key] of [["量測增量營收", "incremental_revenue"], ["量測增量毛利", "incremental_gross_margin"], ["廣告支出", "ad_spend"], ["Treatment 店數", "treatment_store_count"], ["Control 店數", "control_store_count"]]) change(new RegExp("^" + label), b[key]);
    if (b.has_control_group) fireEvent.click(screen.getByLabelText("有 control 組")); if (b.randomized) fireEvent.click(screen.getByLabelText("採隨機分派"));
    change(/^預趨勢/, b.pretrend_status); change(/^評估方法/, b.evaluation_method); change(/^量測方法/, b.measurement_method);
  }
}
async function review(sample: typeof samples[number]) { await open(sample.command as LifecycleCommand); fill(sample); fireEvent.click(screen.getByTestId("lifecycle-submit")); await waitFor(() => expect(screen.getByTestId("lifecycle-submit")).toHaveTextContent("確認並提交操作")); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.sessionStorage.clear(); });
describe("native thirteen command forms in the existing nine sections", () => {
  it.each(samples)("$name confirms only after its actual receipt, CID proof and related native reads", async sample => {
    const run = mount(sample); await review(sample); expect(run.posts()).toHaveLength(0);
    fireEvent.click(screen.getByTestId("lifecycle-submit")); await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("lifecycle-confirmed")).toBeInTheDocument());
    expect(run.posts()).toHaveLength(1); expect(JSON.parse(String(run.posts()[0][1]?.body))).toEqual(sample.request);
    expect(run.posts()[0][1]?.headers).toMatchObject({ "If-Match": sample.beforeETag, "If-Actor-Match": sample.scope.actorSubjectId, "Idempotency-Key": sample.key, "X-Correlation-Id": sample.correlation });
    expect(run.fetch.mock.calls.some(([url]) => String(url).includes("lifecycle_correlation_id=" + sample.correlation))).toBe(true);
    if (sample.label) expect(run.fetch.mock.calls.some(([url]) => String(url).endsWith("/label"))).toBe(true);
    if (sample.child) expect(run.fetch.mock.calls.some(([url]) => String(url).includes(sample.child!.intervention_id + "?lifecycle_correlation_id="))).toBe(true);
    expect(run.fetch.mock.calls.every(([url]) => !String(url).includes("/audit/events"))).toBe(true);
  });
  it("does not turn a browser persona or manual role label into a command grant", async () => {
    const sample = samples.find(s => s.command === "assign")!;
    window.sessionStorage.setItem("oday.operator.role", "OPERATIONS_MANAGER");
    const run = mount(sample, { deny: true }); await screen.findByRole("heading", { name: "9. Version / Audit" });
    await waitFor(() => expect(screen.getAllByText("部分操作權限或資料尚未通過核對。").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: COMMAND_LABELS.assign })).not.toBeInTheDocument(); expect(run.posts()).toHaveLength(0);
  });
  it("withholds confirmation when the backend audit projection is absent", async () => {
    const sample = samples.find(s => s.command === "assign")!, run = mount(sample, { missingProof: true }); await review(sample); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(screen.getByTestId("lifecycle-error")).toHaveTextContent("LIFECYCLE_READBACK_MISMATCH"));
    expect(run.confirmed).not.toHaveBeenCalled(); expect(screen.getByText(/原請求結果尚未確認/)).toBeInTheDocument();
  });
  it("replays the original attempt after a genuine later native version without a new context GET", async () => {
    const sample = samples.find(s => s.command === "assign")!, run = mount(sample, { lost: true, advanced: true }); await review(sample); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(screen.getByText(/原請求結果尚未確認/)).toBeInTheDocument());
    const original = run.posts()[0][1];
    fireEvent.click(screen.getByTestId("lifecycle-submit")); await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.posts()).toHaveLength(2); expect(run.posts()[1][1]?.body).toBe(original?.body); expect(run.posts()[1][1]?.headers).toEqual(original?.headers);
    expect(run.contextsAtPost[1]).toBe(run.contextsAtPost[0]);
    await waitFor(() => expect(screen.getByTestId("lifecycle-confirmed")).toHaveTextContent("案件後續已有更新"));
  });
  it.each(["actor", "tenant", "expiry"] as const)("invalidates %s rotation before any POST", async rotation => {
    const sample = samples.find(s => s.command === "assign")!, run = mount(sample, { rotation }); await review(sample); run.rotate(); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(screen.queryByTestId("lifecycle-command-dialog")).not.toBeInTheDocument()); expect(run.posts()).toHaveLength(0); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it.each(["post", "proof"] as const)("owns the actor and tenant again after %s JSON parsing", async rotateJSON => {
    const sample = samples.find(s => s.command === "assign")!, run = mount(sample, { rotateJSON }); await review(sample); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(screen.queryByTestId("lifecycle-command-dialog")).not.toBeInTheDocument()); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("does not let a late result own a changed location", async () => {
    const sample = samples.find(s => s.command === "assign")!, held = deferred<Response>(), run = mount(sample, { held: held.promise }); await review(sample); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(run.posts()).toHaveLength(1)); window.history.replaceState(null, "", "/operator?ws=today&tab=interventions&entity=another-case"); held.resolve(json(sample.receipt));
    await waitFor(() => expect(screen.queryByTestId("lifecycle-command-dialog")).not.toBeInTheDocument()); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("prevents duplicate writes and traps selects with cancel and focus restoration", async () => {
    const sample = samples.find(s => s.command === "eligibility")!, run = mount(sample);
    await open("eligibility"); const first = screen.getByLabelText("資格結果"); expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true }); expect(screen.getByTestId("lifecycle-submit")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("lifecycle-submit"), { key: "Tab" }); expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Escape" }); await waitFor(() => expect(screen.queryByTestId("lifecycle-command-dialog")).not.toBeInTheDocument()); expect(run.posts()).toHaveLength(0);
    expect(screen.getByRole("button", { name: COMMAND_LABELS.eligibility })).toHaveFocus();
    const assign = samples.find(s => s.command === "assign")!, held = deferred<Response>(); cleanup();
    vi.restoreAllMocks(); vi.unstubAllGlobals();
    const next = mount(assign, { held: held.promise }); await review(assign); fireEvent.click(screen.getByTestId("lifecycle-submit")); await waitFor(() => expect(next.posts()).toHaveLength(1));
    fireEvent.click(screen.getByTestId("lifecycle-submit")); expect(next.posts()).toHaveLength(1); held.resolve(json(assign.receipt)); await waitFor(() => expect(next.confirmed).toHaveBeenCalledTimes(1));
  });
  it("holds one operational lock across the old approval and new lifecycle dialogs", async () => {
    const sample = samples.find(s => s.command === "submit")!, run = mount(sample, { pending: true });
    await open("assign"); expect(screen.getByRole("button", { name: "核准干預" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "核准干預" })); expect(screen.queryByTestId("intervention-decision-dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" })); await waitFor(() => expect(screen.getByRole("button", { name: "核准干預" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "核准干預" })); await screen.findByTestId("intervention-decision-dialog");
    expect(screen.getByRole("button", { name: COMMAND_LABELS.assign })).toBeDisabled(); expect(screen.queryByTestId("lifecycle-command-dialog")).not.toBeInTheDocument(); expect(run.posts()).toHaveLength(0);
  });
  it("requires native reasons and real measurement inputs before creating an attempt", async () => {
    const sample = samples.find(s => s.command === "stop")!, run = mount(sample); await open("stop"); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    expect(screen.getByTestId("lifecycle-error")).toHaveTextContent("操作理由為必填"); expect(run.posts()).toHaveLength(0);
  });
});

// Bounded source-arithmetic projections; the original17 native API captures above
// remain byte exact. These two controls stop predictable ambiguity before POST.
describe("default child duration preflight", () => {
  it("requires an existing explicit replacement end before allowing adjustment confirmation", async () => {
    const sample = copy(samples.find(s => s.command === "adjust")!);
    sample.before.planned_start = "2026-03-07T12:00:00-05:00"; sample.before.planned_end = "2026-03-09T12:00:00-04:00";
    const run = mount(sample); await open("adjust"); fill(sample);
    expect(screen.getByTestId("lifecycle-duration-insufficient")).toHaveTextContent("請填寫替代案計畫結束時間");
    expect(screen.getByTestId("lifecycle-submit")).toBeDisabled(); fireEvent.click(screen.getByTestId("lifecycle-submit")); expect(run.posts()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText(/^替代案計畫結束/), { target: { value: "2026-06-01T12:00" } });
    expect(screen.getByTestId("lifecycle-submit")).toBeEnabled(); fireEvent.click(screen.getByTestId("lifecycle-submit"));
    await waitFor(() => expect(screen.getByTestId("lifecycle-submit")).toHaveTextContent("確認並提交操作")); expect(run.posts()).toHaveLength(0);
  });
  it("withholds a follow-up write with missing duration authority while allowing ordinary close", async () => {
    const sample = copy(samples.find(s => s.name === "close_follow_up")!);
    sample.before.planned_start = "2026-03-07T12:00:00-05:00"; sample.before.planned_end = "2026-03-09T12:00:00-04:00";
    const run = mount(sample); await open("close"); fill(sample);
    expect(screen.getByTestId("lifecycle-duration-insufficient")).toHaveTextContent("不足以核對追蹤案的預設期限");
    expect(screen.getByTestId("lifecycle-submit")).toBeDisabled(); fireEvent.click(screen.getByTestId("lifecycle-submit")); expect(run.posts()).toHaveLength(0);
    fireEvent.click(screen.getByLabelText("建立追蹤干預案")); expect(screen.getByTestId("lifecycle-submit")).toBeEnabled();
    fireEvent.click(screen.getByTestId("lifecycle-submit")); await waitFor(() => expect(screen.getByTestId("lifecycle-submit")).toHaveTextContent("確認並提交操作")); expect(run.posts()).toHaveLength(0);
  });
});
