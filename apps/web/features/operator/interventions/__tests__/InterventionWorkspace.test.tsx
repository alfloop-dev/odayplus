import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { InterventionWorkspace } from "../InterventionWorkspace";
import { INTERVENTION_KINDS, INTERVENTION_STATUSES, makeAttempt, mayClaimEffect, mayClaimCausal, parseActorScope, parseOwnedRead, readbackMatches, receiptMatches, type ActorScope, type Intervention } from "../interventionContract";
import samples from "./fixtures/nativeInterventionApproval.json";
import effects from "./fixtures/nativeInterventionEffect.json";
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });
const copy = <T,>(v: T): T => structuredClone(v);
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; }
const scope = (): ActorScope => ({ actorSubjectId: samples.APPROVE.before.review_context.actor_subject_id, tenantId: samples.APPROVE.before.review_context.tenant_id, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
function mount(options: { action?: "APPROVE" | "REJECT"; readonly?: boolean; post?: (n: number) => Response | Promise<Response>; malformedRead?: (body: Record<string, unknown>) => void;
  lostAfterAccepted?: boolean; rotate?: "read-json" | "post-json" | "readback-json"; rotation?: "actor" | "tenant"; readback?: unknown; onConfirmed?: () => void | Promise<void> } = {}) {
  const sample = samples[options.action ?? "APPROVE"]; const actor = scope(); let current = { ...actor }, accepted = false, attempts = 0;
  const base = copy(sample.before) as unknown as Record<string, unknown>;
  if (options.readonly) base.approval_permissions = { approve: false, reject: false, approve_refusal: "APPROVAL_PERMISSION_REQUIRED", reject_refusal: "APPROVAL_PERMISSION_REQUIRED" };
  options.malformedRead?.(base);
  vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(sample.key as ReturnType<Crypto["randomUUID"]>).mockReturnValueOnce(sample.correlation as ReturnType<Crypto["randomUUID"]>);
  window.history.replaceState(null, "", `/operator?ws=today&tab=interventions&entity=${sample.before.intervention_id}`);
  const rotate = () => { current = { ...current, ...(options.rotation === "actor" ? { actorSubjectId: "other-actor" } : { tenantId: "other-tenant" }) }; };
  const response = (body: unknown, stage: string) => {
    const r = json(body); if (options.rotate === stage) { const read = r.json.bind(r); r.json = async () => { const value = await read(); rotate(); return value; }; } return r;
  };
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/operator-context") return json(current);
    if (init?.method === "POST") { attempts += 1; if (options.lostAfterAccepted) accepted = true; const r = options.post ? await options.post(attempts) : response(sample.receipt, "post-json"); accepted = r.ok; return r; }
    return accepted ? response(options.readback ?? sample.after, "readback-json") : response(base, "read-json");
  });
  vi.stubGlobal("fetch", fetch); const confirmed = vi.fn(options.onConfirmed), select = vi.fn();
  const view = render(<InterventionWorkspace selectedId={sample.before.intervention_id} onSelect={select} onDecisionConfirmed={confirmed} />);
  return { fetch, sample, confirmed, view, select, actor, rotate, setScope: (next: ActorScope) => { current = next; }, writes: () => fetch.mock.calls.filter(([, init]) => init?.method === "POST") };
}
async function open(action: "APPROVE" | "REJECT" = "APPROVE") {
  const label = action === "APPROVE" ? "核准干預" : "退回干預";
  await waitFor(() => expect(screen.getByRole("button", { name: label })).toBeEnabled()); fireEvent.click(screen.getByRole("button", { name: label }));
  await waitFor(() => expect(screen.getByTestId("intervention-reason")).toBeInTheDocument());
}
async function submit(action: "APPROVE" | "REJECT" = "APPROVE") { await open(action); fireEvent.change(screen.getByTestId("intervention-reason"), { target: { value: samples[action].request.reason } }); fireEvent.click(screen.getByTestId("intervention-submit")); }
beforeEach(() => { window.sessionStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
describe("full native Intervention ApprovalPanel", () => {
  it.each(["APPROVE", "REJECT"] as const)("uses actual native %s context/body/audit and readback before Today refresh", async action => {
    const run = mount({ action }); await submit(action); await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(1); const [url, init] = run.writes()[0]; expect(url).toBe(`/api/v1/interventions/${run.sample.before.intervention_id}/approve`);
    expect(JSON.parse(String(init?.body))).toEqual(run.sample.request);
    expect(init?.headers).toMatchObject({ "If-Match": 'W/"5"', "If-Actor-Match": run.actor.actorSubjectId, "Idempotency-Key": run.sample.key, "X-Correlation-Id": run.sample.correlation });
    expect(screen.getByTestId("intervention-confirmed")).toHaveTextContent(run.sample.correlation);
    expect(screen.queryByTestId("intervention-decision-dialog")).not.toBeInTheDocument();
  });
  it("keeps nine fixed sections, ten timeline nodes, native policy and complete audit", async () => {
    mount(); await waitFor(() => expect(screen.getByRole("heading", { name: "9. Version / Audit" })).toBeInTheDocument());
    expect(screen.getAllByRole("heading", { level: 2 }).map(n => n.textContent)).toEqual(["1. Summary", "2. Status & Timeline", "3. Eligibility", "4. Action Proposed", "5. Conflict Control", "6. Approval", "7. Execution & Observation Window", "8. Outcome & Effect", "9. Version / Audit"]);
    expect(screen.getByText("◇ Triggered")).toBeInTheDocument(); expect(screen.getByText("◇ Closed")).toBeInTheDocument();
    expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument(); expect(screen.getAllByText("intervention-feature-v1")).toHaveLength(1);
  });
  it("shows a read-only principal without command buttons", async () => {
    const run = mount({ readonly: true }); await waitFor(() => expect(screen.getByRole("heading", { name: "9. Version / Audit" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "核准干預" })).not.toBeInTheDocument(); expect(screen.queryByRole("button", { name: "退回干預" })).not.toBeInTheDocument(); expect(run.writes()).toHaveLength(0);
  });
  it("requires a reason and uses keyboard focus trap and Escape", async () => {
    const run = mount(); await open(); const input = screen.getByTestId("intervention-reason"); expect(input).toHaveFocus();
    fireEvent.click(screen.getByTestId("intervention-submit")); expect(run.writes()).toHaveLength(0);
    expect(screen.getAllByText("決策理由為必填。").length).toBeGreaterThan(0);
    fireEvent.keyDown(input, { key: "Tab", shiftKey: true }); expect(screen.getByTestId("intervention-submit")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("intervention-submit"), { key: "Tab" }); expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: "Escape" }); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("prevents duplicate submit while the actual request is held", async () => {
    const hold = deferred<Response>(); const run = mount({ post: () => hold.promise }); await submit();
    await waitFor(() => expect(run.writes()).toHaveLength(1)); fireEvent.click(screen.getByTestId("intervention-submit")); expect(run.writes()).toHaveLength(1);
    hold.resolve(json(run.sample.receipt)); await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
  });
  it("replays exactly the original unknown request after form edits", async () => {
    const run = mount({ post: n => { if (n === 1) throw new TypeError("synthetic response loss"); return json(samples.APPROVE.receipt); } });
    await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("原請求結果尚未確認"));
    fireEvent.change(screen.getByTestId("intervention-reason"), { target: { value: "Changed reason cannot replace the original request" } }); fireEvent.click(screen.getByTestId("intervention-submit"));
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1)); expect(run.writes()).toHaveLength(2);
    expect(run.writes()[1][1]?.body).toBe(run.writes()[0][1]?.body); expect(run.writes()[1][1]?.headers).toEqual(run.writes()[0][1]?.headers);
  });
  it("reopens a committed response-loss recovery with terminal native context and replays the original pending command", async () => {
    const run = mount({ lostAfterAccepted: true, post: n => { if (n === 1) throw new TypeError("synthetic response lost after commit"); return json(samples.APPROVE.receipt); } });
    await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("原請求結果尚未確認"));
    fireEvent.keyDown(screen.getByTestId("intervention-reason"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "核對原核決結果" })); await waitFor(() => expect(screen.getByTestId("intervention-reason")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("intervention-reason"), { target: { value: "Changed UI form" } }); fireEvent.click(screen.getByTestId("intervention-submit"));
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1)); expect(run.writes()[1][1]?.body).toBe(run.writes()[0][1]?.body); expect(run.writes()[1][1]?.headers).toEqual(run.writes()[0][1]?.headers);
  });
  it.each(["actor", "tenant"] as const)("refuses %s rotation before POST without trusting browser storage", async rotation => {
    const run = mount({ rotation }); await open(); fireEvent.change(screen.getByTestId("intervention-reason"), { target: { value: run.sample.request.reason } });
    window.sessionStorage.setItem("oday.operator.tenant", run.actor.tenantId); run.rotate(); fireEvent.click(screen.getByTestId("intervention-submit"));
    await waitFor(() => expect(screen.queryByTestId("intervention-decision-dialog")).not.toBeInTheDocument()); expect(run.writes()).toHaveLength(0); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it.each(["read-json", "post-json", "readback-json"] as const)("clears same-actor tenant rotation during %s and cannot publish success", async rotate => {
    const run = mount({ rotate });
    if (rotate !== "read-json") await submit();
    await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("登入身分或門市範圍已變更"));
    expect(screen.queryByRole("heading", { name: "1. Summary" })).not.toBeInTheDocument(); expect(screen.queryByTestId("intervention-decision-dialog")).not.toBeInTheDocument(); expect(screen.queryByText("核對中…")).not.toBeInTheDocument(); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("invalidates observed ABA tenant rotation while a response is held", async () => {
    const hold = deferred<Response>(); const run = mount({ post: () => hold.promise }); await submit(); await waitFor(() => expect(run.writes()).toHaveLength(1));
    run.rotate(); window.dispatchEvent(new Event("focus")); await waitFor(() => expect(screen.queryByRole("heading", { name: "1. Summary" })).not.toBeInTheDocument());
    run.setScope(run.actor); hold.resolve(json(run.sample.receipt)); await Promise.resolve(); expect(run.confirmed).not.toHaveBeenCalled(); expect(screen.queryByTestId("intervention-confirmed")).not.toBeInTheDocument();
  });
  it("cannot promote a late receipt after selection has changed", async () => {
    const hold = deferred<Response>(); const run = mount({ post: () => hold.promise }); await submit(); await waitFor(() => expect(run.writes()).toHaveLength(1));
    window.history.pushState(null, "", "/operator?ws=today&tab=interventions&entity=other-case"); hold.resolve(json(run.sample.receipt));
    await waitFor(() => expect(screen.queryByTestId("intervention-decision-dialog")).not.toBeInTheDocument()); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it.each([403, 409, 500])("never confirms an HTTP %s response", async status => {
    const run = mount({ post: () => json({ detail: "native refused" }, status) }); await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toBeInTheDocument()); expect(run.confirmed).not.toHaveBeenCalled(); expect(screen.getByTestId("intervention-reason")).toHaveValue(run.sample.request.reason);
  });
  it("requires authoritative readback and matching audit event before Today refresh", async () => {
    const changed = copy(samples.APPROVE.after); changed.approval_audit!.event_id = "other-event";
    const run = mount({ readback: changed }); await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("APPROVAL_READBACK_MISMATCH")); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("keeps a pending readback from producing confirmation", async () => {
    const run = mount({ readback: samples.APPROVE.before }); await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("APPROVAL_READBACK_MISMATCH")); expect(run.confirmed).not.toHaveBeenCalled();
  });
});
describe("native contract refusal controls", () => {
  it.each(["APPROVE", "REJECT"] as const)("validates exact native %s receipt and durable audit readback", action => {
    const s = samples[action], actor = scope(), row = parseOwnedRead(s.before, actor, s.before.intervention_id)!; const attempt = makeAttempt(row, actor, action, s.request.reason); attempt.key = s.key; attempt.correlation = s.correlation;
    expect(receiptMatches(s.receipt, row, attempt)).toBe(true); expect(readbackMatches(parseOwnedRead(s.after, actor, s.after.intervention_id)!, s.receipt, attempt)).toBe(true);
  });
  it.each([true, 5.0 + .1, "5", 0, -1, null])("refuses a malformed native version %s", version => {
    const before = copy(samples.APPROVE.before) as unknown as Record<string, unknown>; before.version = version; expect(parseOwnedRead(before, scope(), samples.APPROVE.before.intervention_id)).toBeNull();
  });
  it.each(["actor_subject_id", "tenant_id", "store_id", "intervention_id", "policy_version", "snapshot_sha256"])("refuses a malformed read context %s", key => {
    const before = copy(samples.APPROVE.before); (before.review_context as unknown as Record<string, unknown>)[key] = "foreign"; expect(parseOwnedRead(before, scope(), before.intervention_id)).toBeNull();
  });
  it.each(["actor", "reason", "version", "status", "policy", "audit", "correlation", "transition"])("refuses a mismatched %s native receipt", key => {
    const actor = scope(), row = parseOwnedRead(samples.APPROVE.before, actor, samples.APPROVE.before.intervention_id)!, attempt = makeAttempt(row, actor, "APPROVE", samples.APPROVE.request.reason); attempt.correlation = samples.APPROVE.correlation;
    const v = copy(samples.APPROVE.receipt);
    if (key === "actor") v.approval.actor_id = "foreign"; if (key === "reason") v.approval.decision_reason = "different"; if (key === "version") v.version += 1; if (key === "status") v.status = "REJECTED"; if (key === "policy") v.approval.policy_version = "other"; if (key === "audit") v.audit_event_id = ""; if (key === "correlation") v.correlation_id = "other"; if (key === "transition") v.history[v.history.length - 1].actor = "foreign";
    expect(receiptMatches(v, row, attempt)).toBe(false);
  });
  it("preserves all native statuses including CLOSED and all treatment kinds", () => { expect(INTERVENTION_STATUSES).toHaveLength(16); expect(INTERVENTION_STATUSES).toContain("CLOSED"); expect(INTERVENTION_KINDS).toHaveLength(8); });
  it("refuses browser-supplied absent/expired actor or tenant context", () => { expect(parseActorScope({ ...scope(), tenantId: " " })).toBeNull(); expect(parseActorScope({ ...scope(), expiresAt: 1 })).toBeNull(); expect(parseActorScope({ subject: "display-role", tenantId: "browser-preference" })).toBeNull(); });
  it("never treats a raw unobserved outcome as mature effect evidence", () => { const row = parseOwnedRead(samples.APPROVE.before, scope(), samples.APPROVE.before.intervention_id)!; expect(mayClaimEffect({ ...row, outcome: { incremental_revenue: 123456 } })).toBe(false); });
});
describe("native intervention queue and URL-owned preview", () => {
  it("uses canonical context and native tenant collection, reflects filters in URL, and opens full detail from a trapped preview", async () => {
    window.history.replaceState(null, "", "/operator?ws=today&tab=interventions");
    const actor = scope(), select = vi.fn(); const fetch = vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json({ items: [samples.APPROVE.before, samples.REJECT.before], count: 2 }));
    vi.stubGlobal("fetch", fetch); render(<InterventionWorkspace onSelect={select} />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "快速查看" })).toHaveLength(2));
    fireEvent.change(screen.getByRole("combobox", { name: "類型篩選" }), { target: { value: "PRICE_CHANGE" } });
    await waitFor(() => expect(screen.getAllByRole("button", { name: "快速查看" })).toHaveLength(2)); expect(window.location.search).toContain("kind=PRICE_CHANGE");
    expect(fetch.mock.calls.some(([url]) => String(url).includes("/api/v1/interventions?kind=PRICE_CHANGE"))).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "快速查看" })[0]);
    await waitFor(() => expect(screen.getByRole("dialog", { name: "干預快速查看" })).toBeInTheDocument());
    expect(window.location.search).toContain("preview="); expect(screen.getByRole("button", { name: "關閉" })).toHaveFocus();
    expect(screen.queryByRole("button", { name: "核准干預" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一筆" })); await waitFor(() => expect(screen.getByRole("button", { name: "上一筆" })).toBeEnabled());
    const dialog = screen.getByRole("dialog"); fireEvent.keyDown(dialog, { key: "Escape" }); await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(window.location.search).not.toContain("preview="); expect(select).not.toHaveBeenCalled();
  });
  it("shows native empty, error and permission states without fixture fallback", async () => {
    window.history.replaceState(null, "", "/operator?ws=today&tab=interventions"); const actor = scope(); let failed = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : failed ? json({ detail: "forbidden" }, 403) : json({ items: [], count: 0 })));
    render(<InterventionWorkspace onSelect={vi.fn()} />); await waitFor(() => expect(screen.getByText("目前範圍尚無干預紀錄。")).toBeInTheDocument());
    failed = true; fireEvent.click(screen.getByRole("button", { name: "重新載入" })); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("403"));
    expect(screen.queryByRole("button", { name: "核准干預" })).not.toBeInTheDocument();
  });
  it("does not start a protected collection request without canonical actor and tenant", async () => {
    window.history.replaceState(null, "", "/operator?ws=today&tab=interventions"); const fetch = vi.fn(async (_input: RequestInfo | URL) => json({ actorSubjectId: "actor-a", expiresAt: Math.floor(Date.now()/1000)+3600 })); vi.stubGlobal("fetch", fetch);
    render(<InterventionWorkspace onSelect={vi.fn()} />); await waitFor(() => expect(screen.getByTestId("intervention-error")).toBeInTheDocument());
    expect(fetch.mock.calls.every(([url]) => String(url) === "/auth/operator-context")).toBe(true);
  });
});

describe("actual native maturity and evidence producers", () => {
  it.each(["IMMATURE", "MATURE"] as const)("uses native %s gates and withholds raw estimates before maturity", name => {
    const source = effects[name], row = parseOwnedRead(source, scope(), source.intervention_id);
    expect(row).not.toBeNull(); expect(mayClaimEffect(row!)).toBe(name === "MATURE"); expect(mayClaimCausal(row!)).toBe(name === "MATURE");
    expect(source.outcome.incremental_revenue).toBe(123); expect(source.effect.incremental_revenue).toBe(name === "MATURE" ? 123 : 0);
  });
  it.each(["effect_flag", "causal_flag", "evidence", "mature_flag", "raw_immature_increment"])("refuses inconsistent native %s projection", key => {
    const source = copy(effects.IMMATURE);
    if (key === "effect_flag") source.effect.can_claim_effect = true;
    if (key === "causal_flag") source.effect.can_claim_causal = true;
    if (key === "evidence") source.effect.evidence_level = "L3";
    if (key === "mature_flag") source.effect.observation_mature = true;
    if (key === "raw_immature_increment") source.effect.incremental_revenue = 123;
    expect(parseOwnedRead(source, scope(), source.intervention_id)).toBeNull();
  });
});
describe("current authority on exact receipt recovery", () => {
  it("refuses a response-loss replay after current native command permission is revoked", async () => {
    const revoked = copy(samples.APPROVE.after);
    revoked.approval_permissions = { approve: false, reject: false, approve_refusal: "APPROVAL_PERMISSION_REQUIRED", reject_refusal: "APPROVAL_PERMISSION_REQUIRED" } as typeof revoked.approval_permissions;
    const run = mount({ lostAfterAccepted: true, readback: revoked, post: () => { throw new TypeError("synthetic accepted response loss"); } });
    await submit(); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("原請求結果尚未確認"));
    fireEvent.click(screen.getByTestId("intervention-submit")); await waitFor(() => expect(screen.getByTestId("intervention-error")).toHaveTextContent("目前登入者無法重送"));
    expect(run.writes()).toHaveLength(1); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it.each(["MATURE", "IMMATURE"] as const)("renders actual %s evidence with the correct financial visibility", async name => {
    const value = effects[name], actor = scope(); window.history.replaceState(null, "", `/operator?ws=today&tab=interventions&entity=${value.intervention_id}`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input) === "/auth/operator-context" ? json(actor) : json(value)));
    render(<InterventionWorkspace selectedId={value.intervention_id} onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "8. Outcome & Effect" })).toBeInTheDocument());
    if (name === "IMMATURE") { expect(screen.getByTestId("intervention-effect-withheld")).toBeInTheDocument(); expect(screen.queryByText("123")).not.toBeInTheDocument(); }
    else { expect(screen.queryByTestId("intervention-effect-withheld")).not.toBeInTheDocument(); expect(screen.getByText("123")).toBeInTheDocument(); }
  });
});
