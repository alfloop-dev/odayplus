import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OperatorRoleId } from "../../navigation";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";
import { hasReviewSubmissionContext, reviewSubmissionReceiptMatches, reviewSubmissionReadbackMatches } from "../networkReviewSubmission";
import { projectCandidateNativeWorkflow } from "../candidateNativeWorkflowProjection";
import type { NetworkReviewsSnapshot } from "../networkReviewTypes";
import type { NetworkScoringSnapshot } from "../networkScoringTypes";
import type { ReviewSubmissionBody, ReviewSubmissionContext, ReviewSubmissionReceipt } from "../networkReviewSubmissionTypes";
import samples from "./fixtures/canonicalReviewSubmission.json";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}


// Exact actual persisted backend producer output, SHAf4f801... . These are
// native renderer/transport regressions, not signed API or browser acceptance.
// The fixture retains its nonSigned/nonBrowser/nonProductionGrants markers.
const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }) }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));
const initial = samples.INITIAL;
const returned = samples.RETURN_V1_LATEST_V2;
const drafted = samples.GENERIC_DRAFT_V1_LATEST_V2;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { "Content-Type": "application/json" } });
function admittedContext(value: unknown): ReviewSubmissionContext {
  if (!hasReviewSubmissionContext(value)) throw new Error("Actual producer context failed its contract");
  return value;
}
function admittedReceipt(value: unknown, context: ReviewSubmissionContext, body: ReviewSubmissionBody, key = initial.postReceipt.commandReceipt.idempotencyKey, correlation = initial.postReceipt.commandReceipt.correlationId): ReviewSubmissionReceipt {
  if (!reviewSubmissionReceiptMatches(value, context, body, key, correlation)) {
    throw new Error("Actual producer receipt failed its contract");
  }
  return value;
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
type Options = { role?: OperatorRoleId; flow?: "initial" | "returned" | "drafted"; session?: unknown; jsonPhase?: "context" | "post" | "confirmation"; phase?: "context" | "post" | "confirmation";
  post?: (count: number) => Response | Promise<Response>; deniedReadback?: boolean; delayedContext?: Promise<Response> };
function mount(options: Options = {}) {
  const isReturn = options.flow === "returned";
  const isDraft = options.flow === "drafted";
  const context = admittedContext(isReturn ? returned.resubmitContext : isDraft ? drafted.beforeContext : initial.beforeContext);
  const before = isReturn ? returned.beforeResubmit : isDraft ? drafted.before : initial.before;
  const after = isReturn ? returned.afterResubmit : isDraft ? drafted.after : initial.after;
  const receipt = isReturn ? returned.resubmitReceipt : isDraft ? drafted.postReceipt : initial.postReceipt;
  const key = receipt.commandReceipt.idempotencyKey, correlation = receipt.commandReceipt.correlationId;
  vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValueOnce(key as `${string}-${string}-${string}-${string}-${string}`)
    .mockReturnValueOnce(correlation as `${string}-${string}-${string}-${string}-${string}`);
  let accepted = false;
  let actor = context.actorSubjectId;
  let posts = 0;
  let contextReads = 0;
  function response(value: unknown, phase: "context" | "post" | "confirmation") {
    const result = json(value);
    if (options.jsonPhase === phase) {
      const parse = result.json.bind(result);
      result.json = async () => { const data: unknown = await parse(); actor = "another-authenticated-account"; return data; };
    }
    return result;
  }
  nav.search = `ws=network&tab=candidates&candidate=${context.candidateId}`;
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  window.sessionStorage.setItem("oday.operator.tenant", context.tenantId);
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/auth/session") return json(options.session ?? { authenticated: true, subject: actor,
      username: "Human display name differs from account subject", expiresAt: Math.floor(Date.now() / 1000) + 3600 });
    if (url.includes("/submission-context")) {
      contextReads += 1;
      if (options.delayedContext && contextReads === 1) return options.delayedContext;
      if (options.phase === "context") actor = "another-authenticated-account";
      return response(accepted ? isReturn ? returned.pendingContext : isDraft ? drafted.pendingContext : initial.pendingContext : context, "context");
    }
    if (url.endsWith("/submit")) {
      posts += 1; accepted = true;
      if (options.phase === "post") actor = "another-authenticated-account";
      return options.post ? options.post(posts) : response(receipt, "post");
    }
    const read = accepted ? after : before;
    if (url.includes("network-listings")) return json(read.network);
    if (url.includes("network-scoring")) return json(read.scoring);
    if (url.includes("network-reviews")) {
      if (accepted && options.phase === "confirmation") actor = "another-authenticated-account";
      if (accepted && !options.deniedReadback) return response(read.reviews, "confirmation");
      return json(options.deniedReadback && accepted ? { detail: "VIEW denied" } : read.reviews,
        options.deniedReadback && accepted ? 403 : 200);
    }
    return json({ detail: "unknown test route" }, 404);
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(<NetworkFindAreasWorkspace activeRoleId={options.role ?? "expansion-manager"} />);
  return { context, before, after, receipt, fetch, view, postCount: () => posts };
}
async function open(candidateId = initial.candidateId) {
  fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${candidateId}`));
  await waitFor(() => expect(screen.getByTestId("review-submission-submit")).toBeEnabled());
}
function fill(summary = initial.request.submissionSummary, group = initial.request.reviewerGroupId) {
  fireEvent.change(screen.getByTestId("review-submission-summary"), { target: { value: summary } });
  fireEvent.change(screen.getByTestId("review-submission-group"), { target: { value: group } });
}
function submit() { fireEvent.click(screen.getByTestId("review-submission-submit")); }
beforeEach(() => { window.sessionStorage.clear(); nav.push.mockReset(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("actual producer saved-report submission contracts", () => {
  it("keeps the fixture provenance and actual independent Approval identity", () => {
    expect(samples.nonSigned).toBe(true); expect(samples.nonBrowser).toBe(true);
    expect(samples.realProductionGrantsEstablished).toBe(false);
    const context = admittedContext(initial.beforeContext);
    const receipt = admittedReceipt(initial.postReceipt, context, initial.request);
    expect(receipt.records.approvalId).not.toBe(receipt.records.decisionId);
    expect(reviewSubmissionReadbackMatches(initial.after.network, initial.after.reviews, receipt)).toBe(true);
  });
  it("joins actual RETURN V1 to active pending V1 while latest report remains V2", () => {
    const context = admittedContext(returned.resubmitContext);
    const receipt = admittedReceipt(returned.resubmitReceipt, context, returned.resubmitRequest,
      returned.resubmitReceipt.commandReceipt.idempotencyKey, returned.resubmitReceipt.commandReceipt.correlationId);
    expect(reviewSubmissionReceiptMatches(receipt, context, returned.resubmitRequest,
      receipt.commandReceipt.idempotencyKey, receipt.commandReceipt.correlationId)).toBe(true);
    expect(reviewSubmissionReadbackMatches(returned.afterResubmit.network, returned.afterResubmit.reviews, receipt)).toBe(true);
    const reads = projectCandidateNativeWorkflow({ currentGeneration: 8, tenantId: context.tenantId,
      network: { snapshot: returned.afterResubmit.network, status: "ready", generation: 8 },
      scoring: { snapshot: returned.afterResubmit.scoring as NetworkScoringSnapshot, status: "ready", generation: 8 },
      reviews: { snapshot: returned.afterResubmit.reviews as NetworkReviewsSnapshot, status: "ready", generation: 8 } });
    expect(reads[context.candidateId].stage).toBe("pendingreview");
    expect(reads[context.candidateId].review).toMatchObject({ reportVersion: 1, latestReportVersion: 2, relation: "historical" });
  });
  it("admits the actual cached replay without changing recorded history or the immutable creator", () => {
    const context = admittedContext(initial.beforeContext);
    const receipt = admittedReceipt(initial.cachedReplayReceipt, context, initial.request);
    expect(receipt.idempotentReplay).toBe(true);
    expect(receipt.review).toEqual(initial.postReceipt.review);
    expect(receipt.records).toEqual(initial.postReceipt.records);
    expect(receipt.commandReceipt).toEqual(initial.postReceipt.commandReceipt);
  });
  it.each(["actorSubjectId", "tenantId", "idempotencyKey", "correlationId", "decisionId", "submissionSummary", "reviewerGroupId",
    "originalCreatorSubjectId", "verifiedSubmitterSubjectId"])("rejects a foreign or conflicting command %s", (field) => {
    const value = structuredClone(initial.postReceipt);
    Reflect.set(value.commandReceipt, field, "foreign-conflict");
    expect(reviewSubmissionReceiptMatches(value, admittedContext(initial.beforeContext), initial.request, initial.postReceipt.commandReceipt.idempotencyKey, initial.postReceipt.commandReceipt.correlationId)).toBe(false);
  });
  it.each(["candidateId", "listingId", "reportId", "reviewId", "decisionId", "approvalId", "auditId"])("rejects absent actual record %s", (field) => {
    const value = structuredClone(initial.postReceipt); Reflect.set(value.records, field, "");
    expect(reviewSubmissionReceiptMatches(value, admittedContext(initial.beforeContext), initial.request, initial.postReceipt.commandReceipt.idempotencyKey, initial.postReceipt.commandReceipt.correlationId)).toBe(false);
  });
  it.each(["decisionVersion", "reportVersion"])("rejects incorrect actual %s", (field) => {
    const value = structuredClone(initial.postReceipt); Reflect.set(value.records, field, 999);
    expect(reviewSubmissionReceiptMatches(value, admittedContext(initial.beforeContext), initial.request, initial.postReceipt.commandReceipt.idempotencyKey, initial.postReceipt.commandReceipt.correlationId)).toBe(false);
  });
  it("rejects receipt success without the producer's physical pending pointer", () => {
    const receipt = admittedReceipt(initial.postReceipt, admittedContext(initial.beforeContext), initial.request);
    const network = structuredClone(initial.after.network); Reflect.deleteProperty(network.candidates[0], "reviewSubmission");
    expect(reviewSubmissionReadbackMatches(network, initial.after.reviews, receipt)).toBe(false);
  });
  it("keeps duplicate, foreign and blank context fields unavailable", () => {
    const context = structuredClone(initial.beforeContext);
    expect(hasReviewSubmissionContext({ ...context, reviewerGroups: [...context.reviewerGroups, context.reviewerGroups[0]] })).toBe(false);
    expect(hasReviewSubmissionContext({ ...context, actorSubjectId: "" })).toBe(false);
    expect(hasReviewSubmissionContext({ ...context, target: { ...context.target, expectedCandidateReviewId: "foreign" } })).toBe(false);
    expect(hasReviewSubmissionContext({ ...context, target: { ...context.target, expectedCandidateDigest: "" } })).toBe(false);
  });
});

describe("native Candidate submission form and actual producer bridge", () => {
  it.each(["platform-admin", "pm-audit", "ops-lead", "cs-lead", "field-lead", "marketing-manager"] as const)("keeps %s read-only without submission affordance", async (role) => {
    const { fetch } = mount({ role }); await screen.findByTestId(`candidate-row-${initial.candidateId}`);
    expect(screen.queryByTestId(`candidate-submit-review-${initial.candidateId}`)).not.toBeInTheDocument();
    expect(screen.queryByTestId(`candidate-detail-submit-review-${initial.candidateId}`)).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([url, init]) => String(url).includes("submission-context") || init?.method === "POST")).toHaveLength(0);
  });
  it("requires user summary and API-provided group before any POST", async () => {
    const { fetch } = mount(); await open();
    expect(screen.getByTestId("review-submission-summary")).toHaveValue("");
    expect(screen.getByTestId("review-submission-group")).toHaveValue("");
    expect(within(screen.getByTestId("review-submission-group")).getByRole("option", { name: "Executive" })).toBeVisible();
    submit(); expect(await screen.findByRole("alert")).toHaveTextContent("請填寫送審摘要");
    fill(initial.request.submissionSummary, ""); submit(); expect(screen.getByRole("alert")).toHaveTextContent("請選擇審核群組");
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.queryByText(/風險確認/)).not.toBeInTheDocument();
  });
  it("confirms actual initial Decision/Approval only after fresh full canonical reads", async () => {
    const { fetch, receipt } = mount(); await open(); fill(); submit();
    expect(await screen.findByTestId("review-submission-confirmed")).toHaveTextContent(receipt.records.decisionId);
    expect(screen.getByTestId("review-submission-confirmed")).toHaveTextContent(receipt.records.approvalId);
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1); expect(JSON.parse(String(posts[0][1]?.body))).toEqual(initial.request);
    expect(posts[0][1]?.headers).toMatchObject({ "If-Actor-Match": initial.beforeContext.actorSubjectId,
      "Idempotency-Key": initial.postReceipt.commandReceipt.idempotencyKey, "X-Correlation-Id": initial.postReceipt.commandReceipt.correlationId });
    expect(within(screen.getByTestId("candidate-pipeline-pendingreview")).getByText("1")).toBeVisible();
    expect(within(screen.getByTestId("candidate-pipeline-approved")).getByText("0")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看已確認審核" }));
    expect(nav.push).toHaveBeenCalledWith(expect.stringContaining(`review=${receipt.records.decisionId}`), { scroll: false });
  });
  it("resubmits same RETURN Decision with explicit frozen V1/latest V2 labels", async () => {
    const { fetch, context, receipt } = mount({ flow: "returned" }); await open(returned.candidateId);
    expect(screen.getByTestId("review-submission-report-target")).toHaveTextContent(context.target.expectedReportId);
    expect(screen.getByTestId("review-submission-report-target")).toHaveTextContent(context.target.expectedLatestReportId);
    fill(returned.resubmitRequest.submissionSummary, returned.resubmitRequest.reviewerGroupId); submit();
    expect(await screen.findByTestId("review-submission-confirmed")).toHaveTextContent(receipt.records.decisionId);
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual(returned.resubmitRequest);
    expect(receipt.records.decisionId).toBe(returned.postReceipt.records.decisionId);
    expect(receipt.records.approvalId).not.toBe(returned.postReceipt.records.approvalId);
    expect(screen.getByTestId(`candidate-review-provenance-${context.candidateId}`)).toHaveTextContent("v1");
    expect(screen.getByTestId(`candidate-review-provenance-${context.candidateId}`)).toHaveTextContent("v2");
  });
  it("resubmits unique actual generic DRAFT V1 without labelling it RETURN or trusting the old creator", async () => {
    const { fetch, context, receipt } = mount({ flow: "drafted" });
    await open(drafted.candidateId);
    expect(screen.getByTestId("candidate-pipeline-needdata")).toBeDisabled();
    expect(screen.getByTestId("review-submission-report-target")).toHaveTextContent(context.target.expectedReportId);
    expect(screen.getByTestId("review-submission-report-target")).toHaveTextContent(context.target.expectedLatestReportId);
    fill(drafted.request.submissionSummary, drafted.request.reviewerGroupId); submit();
    expect(await screen.findByTestId("review-submission-confirmed")).toHaveTextContent(receipt.records.decisionId);
    expect(receipt.commandReceipt.originalCreatorVerified).toBe(false);
    expect(receipt.review.created_by_verified).toBe(false);
    expect(receipt.review.submission_actor_verified).toBe(true);
    expect(receipt.commandReceipt.verifiedSubmitterSubjectId).toBe(context.actorSubjectId);
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual(drafted.request);
    expect(fetch.mock.calls.some(([url]) => String(url).includes(`decisionId=${drafted.contextQueryDecisionId}`))).toBe(true);
  });
  it("prevents duplicate POST while the response is unresolved", async () => {
    const pending = deferred<Response>(); const { fetch } = mount({ post: () => pending.promise });
    await open(); fill(); submit();
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
    expect(screen.getByTestId("review-submission-submit")).toBeDisabled(); submit();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    await act(async () => { pending.resolve(json(initial.postReceipt)); });
    await screen.findByTestId("review-submission-confirmed");
  });
  it("retains values/correlation and replays exact same key/body after lost response", async () => {
    const { fetch } = mount({ post: (count) => { if (count === 1) throw new TypeError("transport response lost"); return json(initial.cachedReplayReceipt); } });
    await open(); fill(); submit(); await screen.findByRole("alert");
    expect(screen.getByTestId("review-submission-summary")).toHaveValue(initial.request.submissionSummary);
    expect(screen.getByTestId("review-submission-group")).toHaveValue(initial.request.reviewerGroupId);
    expect(screen.getByTestId("review-submission-correlation")).toHaveTextContent(initial.postReceipt.commandReceipt.correlationId);
    submit(); await screen.findByTestId("review-submission-confirmed");
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2); expect(posts[1][1]?.body).toBe(posts[0][1]?.body);
    expect(posts[1][1]?.headers).toEqual(posts[0][1]?.headers);
  });
  it.each([{}, { source: "canonical", records: { decisionId: "fake" }, counts: { pending: 1 } }])("does not confirm a bare 200 or increased count", async (value) => {
    mount({ post: () => json(value) }); await open(); fill(); submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("結果尚未確認");
    expect(screen.queryByTestId("review-submission-confirmed")).not.toBeInTheDocument();
  });
  it("receipt does not grant denied canonical VIEW and preserves the failed form", async () => {
    mount({ deniedReadback: true }); await open(); fill(); submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("403");
    expect(screen.getByTestId("review-submission-summary")).toHaveValue(initial.request.submissionSummary);
    expect(screen.queryByTestId("review-submission-confirmed")).not.toBeInTheDocument();
  });
  it.each([{}, { authenticated: false }, { authenticated: true, subject: "", expiresAt: 9999999999 },
    { authenticated: true, subject: "submit-author", expiresAt: 1 }])("does not POST without a current canonical session", async (session) => {
    const { fetch } = mount({ session });
    fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${initial.candidateId}`));
    await screen.findByRole("alert");
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(screen.getByTestId("review-submission-submit")).toBeDisabled();
  });
  it.each(["context", "post", "confirmation"] as const)("rejects A-to-B session change during %s without false confirmation", async (phase) => {
    const { fetch } = mount({ phase });
    fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${initial.candidateId}`));
    if (phase !== "context") { await waitFor(() => expect(screen.getByTestId("review-submission-submit")).toBeEnabled()); fill(); submit(); }
    expect(await screen.findByRole("alert")).toHaveTextContent("登入身分或操作上下文已變更");
    expect(screen.queryByTestId("review-submission-confirmed")).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(phase === "context" ? 0 : 1);
  });
  it.each(["context", "post", "confirmation"] as const)("rejects actor change after %s JSON is parsed", async (jsonPhase) => {
    const { fetch } = mount({ jsonPhase });
    fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${initial.candidateId}`));
    if (jsonPhase !== "context") { await waitFor(() => expect(screen.getByTestId("review-submission-submit")).toBeEnabled()); fill(); submit(); }
    expect(await screen.findByRole("alert")).toHaveTextContent("登入身分或操作上下文已變更");
    expect(screen.queryByTestId("review-submission-confirmed")).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(jsonPhase === "context" ? 0 : 1);
  });
  it("drops delayed context JSON after role/navigation generation changes", async () => {
    const response = deferred<Response>(); const { view, fetch } = mount({ delayedContext: response.promise });
    fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${initial.candidateId}`));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes("submission-context"))).toBe(true));
    nav.search = "ws=network&tab=candidates&candidate=foreign";
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="platform-admin" />);
    await act(async () => { response.resolve(json(initial.beforeContext)); });
    expect(screen.queryByTestId("review-submission-dialog")).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    expect(nav.push).not.toHaveBeenCalled();
  });
});
