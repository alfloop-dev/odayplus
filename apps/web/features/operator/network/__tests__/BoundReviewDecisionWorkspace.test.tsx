import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import { boundReviewDecisionBody, hasReviewDecisionContext, reviewDecisionContextMatches, reviewDecisionReceiptMatches, reviewDecisionReadbackMatches, type ReviewDecisionContext, type BoundReviewDecisionBody } from "../networkReviewDecision";
import type { NetworkReviewsSnapshot, ReviewDecisionAction } from "../networkReviewTypes";
import samples from "./fixtures/canonicalReviewDecision.json";
import freshListings from "./fixtures/canonicalReviewDecisionFreshListings.json";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push }) }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({ ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div /> }));
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const sample = samples.GO;
const actions = ["GO", "WAIT", "RETURN", "REJECT"] as const;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function context(value: unknown): ReviewDecisionContext { if (!hasReviewDecisionContext(value)) throw new Error("Actual producer context did not pass"); return value; }
function mount(options: { returned?: boolean; explicitReview?: boolean; post?: (attempt: number) => Response | Promise<Response>; rotate?: "context-json" | "post-json" | "readback-json"; tenantChange?: "context-json" | "post-json" | "readback-json"; readback?: unknown } = {}) {
  const sample = options.returned ? samples.RETURN_RESUBMITTED_GO : samples.GO;
  const c = context(sample.context);
  nav.search = `ws=network&tab=review&candidate=${sample.candidateId}${options.explicitReview === false ? "" : `&review=${sample.decisionId}`}`;
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  window.sessionStorage.setItem("oday.operator.tenant", c.tenantId);
  vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(sample.idempotencyKey as ReturnType<Crypto["randomUUID"]>)
    .mockReturnValueOnce(sample.correlationId as ReturnType<Crypto["randomUUID"]>);
  let subject = c.actorSubjectId; let accepted = false; let attempts = 0;
  const response = (value: unknown, phase?: string) => {
    const r = json(value); if (options.rotate === phase || options.tenantChange === phase) {
      const read = r.json.bind(r); r.json = async () => {
        const body = await read();
        if (options.rotate === phase) subject = "other-session-subject";
        if (options.tenantChange === phase) window.sessionStorage.setItem("oday.operator.tenant", "other-tenant-with-same-subject");
        return body;
      };
    } return r;
  };
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json({ subject, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url === "/auth/operator-context") return json({ actorSubjectId: subject, tenantId: c.tenantId, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url.includes("/decision-context?")) return response(c, "context-json");
    if (url.endsWith("/decision") && init?.method === "POST") { attempts += 1; const result = options.post ? await options.post(attempts) : response(sample.receipt, "post-json"); accepted = result.ok; return result; }
    if (url.includes("network-reviews")) return accepted ? response(options.readback ?? sample.after, "readback-json") : json(sample.before);
    if (url.includes("network-scoring")) return json(sample.scoring);
    // The original native producer projects terminal metadata after a decision.
    if (url.includes("network-listings")) return json(accepted
      ? freshListings[options.returned ? "RETURN_RESUBMITTED_GO" : "GO"] : sample.network);
    if (url.includes("network-rebalance")) return json({ stores: [], source: "api" });
    if (url.includes("heatzone-proposals")) return json({ proposals: [], source: "api" });
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetch); const confirmed = vi.fn();
  const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" initialTabId="review" onReviewDecisionConfirmed={confirmed} />);
  return { fetch, confirmed, view, selectedSample: sample, rotate: () => { subject = "other-session-subject"; },
    changeTenant: () => { window.sessionStorage.setItem("oday.operator.tenant", "other-tenant-with-same-subject"); }, writes: () => fetch.mock.calls.filter(([, init]) => init?.method === "POST") };
}
async function openAndSubmit(sample: { decisionId: string; request: { reason: string } } = samples.GO) {
  await waitFor(() => expect(screen.getByTestId("review-card-" + sample.decisionId)).toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
  await waitFor(() => expect(screen.getByTestId("review-decision-reason")).toBeInTheDocument());
  fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: sample.request.reason } });
  fireEvent.click(screen.getByTestId("review-decision-submit"));
}
beforeEach(() => { window.sessionStorage.clear(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); nav.push.mockReset(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("current native review owner", () => {
  it.each(actions)("admits actual %s physical context, receipt and terminal readback", (action) => {
    const v = samples[action]; const c = context(v.context); const before = v.before as NetworkReviewsSnapshot;
    expect(reviewDecisionContextMatches(c, before, before.reviews[0])).toBe(true);
    const b = boundReviewDecisionBody(c, action, { reason: v.request.reason, conditions: v.request.conditions, requiredData: v.request.requiredData.join("、"), overrideAck: v.request.overrideAck });
    expect(b).toEqual(v.request);
    expect(reviewDecisionReceiptMatches(v.receipt, v.decisionId, b, v.idempotencyKey, v.correlationId)).toBe(true);
    expect(reviewDecisionReadbackMatches(v.after, v.decisionId, b)).toBe(true);
  });
  it.each(actions)("admits actual resubmitted frozen V1 %s while latest V2 and prior returned approval remain separate", (action) => {
    const v = samples[`RETURN_RESUBMITTED_${action}`];
    const c = context(v.context); const before = v.before as NetworkReviewsSnapshot;
    expect(v.frozenV1ReportPreserved && v.latestV2ReportPreserved && v.priorReturnedApprovalPreserved).toBe(true);
    expect(c.target.expectedReportVersion).toBe(1); expect(v.scoring.scorecards[0].reportVersion).toBe(2);
    expect(reviewDecisionContextMatches(c, before, before.reviews[0])).toBe(true);
    const b = boundReviewDecisionBody(c, action, { reason: v.request.reason, conditions: v.request.conditions,
      requiredData: v.request.requiredData.join("、"), overrideAck: v.request.overrideAck });
    expect(reviewDecisionReceiptMatches(v.receipt, v.decisionId, b, v.idempotencyKey, v.correlationId)).toBe(true);
    expect(reviewDecisionReadbackMatches(v.after, v.decisionId, b)).toBe(true);
  });
  it.each([true, false])("completes current frozenV1 owner with latestV2, explicit current-review URL=%s", async (explicitReview) => {
    const run = mount({ returned: true, explicitReview });
    await openAndSubmit(run.selectedSample);
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(1);
    const request = JSON.parse(run.writes()[0][1]!.body as string);
    expect(request.expectedReportVersion).toBe(1); expect(request.expectedDecisionVersion).toBe(4);
    expect(request.expectedApprovalId).not.toBe(samples.RETURN_RESUBMITTED_GO.originalReturnedApprovalId);
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
  });
  it.each(["actor", "approval", "report", "version", "action", "reason", "key", "correlation"])("refuses inconsistent %s receipt", (change) => {
    const r = structuredClone(sample.receipt);
    if (change === "actor") r.commandContext.actorSubjectId = "other";
    if (change === "approval") r.records.approvalId = sample.decisionId;
    if (change === "report") r.review.report_id = "other";
    if (change === "version") r.review.version += 1;
    if (change === "action") r.review.network_action = "WAIT";
    if (change === "reason") r.commandContext.request.reason = "different reason";
    if (change === "key") r.commandContext.idempotencyKey = "other";
    if (change === "correlation") r.commandContext.correlationId = "other";
    expect(reviewDecisionReceiptMatches(r, sample.decisionId, sample.request as BoundReviewDecisionBody, sample.idempotencyKey, sample.correlationId)).toBe(false);
  });
  it("submits only after current context and confirms persisted readback before Today refresh", async () => {
    const run = mount(); await openAndSubmit(); await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(1);
    const [url, request] = run.writes()[0]; expect(url).toBe(`/api/v1/operator/network-reviews/${sample.decisionId}/decision`);
    expect(request?.headers).toMatchObject({ "If-Match": `W/"${sample.request.expectedDecisionVersion}"`, "If-Actor-Match": sample.context.actorSubjectId,
      "Idempotency-Key": sample.idempotencyKey, "X-Correlation-Id": sample.correlationId });
    expect(JSON.parse(request!.body as string)).toEqual(sample.request);
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
  });
  it.each(["context-json", "post-json", "readback-json"] as const)("rejects rotated subject during %s without publishing stale success", async (rotate) => {
    const run = mount({ rotate });
    if (rotate === "context-json") {
      await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
    } else await openAndSubmit();
    await waitFor(() => expect(screen.queryByTestId("review-card-" + sample.decisionId)).not.toBeInTheDocument());
    expect(run.confirmed).not.toHaveBeenCalled();
    expect(run.writes()).toHaveLength(rotate === "context-json" ? 0 : 1);
  });
  it.each(["context-json", "post-json", "readback-json"] as const)("clears its same-actor tenant scope during %s without leaving the decision busy", async (tenantChange) => {
    const run = mount({ tenantChange });
    if (tenantChange === "context-json") {
      await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
    } else await openAndSubmit();
    await waitFor(() => expect(screen.queryByTestId("review-card-" + sample.decisionId)).not.toBeInTheDocument());
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同步中…" })).not.toBeInTheDocument();
    expect(run.confirmed).not.toHaveBeenCalled(); expect(run.writes()).toHaveLength(tenantChange === "context-json" ? 0 : 1);
  });
  it("rejects an observed tenant changed before the canonical decision dialog", async () => {
    const run = mount();
    await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
    run.changeTenant(); fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
    await waitFor(() => expect(screen.queryByTestId("review-card-" + sample.decisionId)).not.toBeInTheDocument());
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
    expect(run.writes()).toHaveLength(0); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("pins the actor before the dialog and rejects a subject rotated while entering the reason", async () => {
    const run = mount();
    await waitFor(() => expect(screen.getByRole("button", { name: "核准 GO" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
    await waitFor(() => expect(screen.getByTestId("review-decision-reason")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: sample.request.reason } });
    run.rotate(); fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(screen.queryByTestId("review-card-" + sample.decisionId)).not.toBeInTheDocument());
    expect(run.writes()).toHaveLength(0); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("keeps the original unknown request after the user changes its reason", async () => {
    const run = mount({ post: () => { throw new TypeError("synthetic lost response"); } });
    await openAndSubmit(); await waitFor(() => expect(screen.getByTestId("review-decision-error")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: "Different reason cannot replace an unconfirmed request" } });
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(screen.getByTestId("review-decision-error")).toHaveTextContent("原核決結果尚未確認"));
    expect(run.writes()).toHaveLength(1); expect(run.confirmed).not.toHaveBeenCalled();
  });
  it("retains the original form/version/body/key after an unknown POST outcome", async () => {
    const run = mount({ post: (attempt) => { if (attempt === 1) throw new TypeError("synthetic lost response"); return json(sample.receipt); } });
    await openAndSubmit(); await waitFor(() => expect(screen.getByTestId("review-decision-error")).toBeInTheDocument());
    expect(screen.getByTestId("review-decision-reason")).toHaveValue(sample.request.reason);
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(() => expect(run.confirmed).toHaveBeenCalledTimes(1));
    expect(run.writes()).toHaveLength(2); expect(run.writes()[1][1]?.body).toBe(run.writes()[0][1]?.body);
    expect(run.writes()[1][1]?.headers).toEqual(run.writes()[0][1]?.headers);
    expect(run.writes()[1][1]?.method).toBe(run.writes()[0][1]?.method);
  });
  it("does not refresh Today from a 200 response whose persisted decision remains pending", async () => {
    const run = mount({ readback: sample.before }); await openAndSubmit();
    await waitFor(() => expect(screen.getByTestId("review-decision-error")).toHaveTextContent("正式 Decision"));
    expect(run.confirmed).not.toHaveBeenCalled();
  });
});
