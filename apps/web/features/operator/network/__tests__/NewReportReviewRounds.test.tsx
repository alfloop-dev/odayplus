import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace } from "../../NetworkFindAreasWorkspace";
import { operatorFixturesAllowed } from "../../operatorDataMode";
import { projectCandidateNativeWorkflow } from "../candidateNativeWorkflowProjection";
import { inspectReviewsReadProjection } from "../networkReviewProjection";
import { hasCurrentPendingSubmissionHistory } from "../networkPendingReviewHistory";
import { hasApprovedReviewHistory } from "../networkReviewRounds";
import { hasReviewSubmissionContext, reviewSubmissionOffers, reviewSubmissionReadbackMatches, reviewSubmissionReceiptMatches } from "../networkReviewSubmission";
import type { ReviewSubmissionBody, ReviewSubmissionContext, ReviewSubmissionReceipt } from "../networkReviewSubmissionTypes";
import type { NetworkReviewsSnapshot } from "../networkReviewTypes";
import type { NetworkScoringSnapshot } from "../networkScoringTypes";
import samples from "./fixtures/canonicalReviewSubmission.json";

// Synthetic protocol variations of the unchanged older producer fixture.
// These tests establish no signed SQL grants, real opening or browser result.
const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }) }));
vi.mock("../HeatZoneMap", () => ({ HeatZoneMap: () => null }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));
const id = samples.INITIAL.candidateId;

function roundData(state: "approved" | "opened" = "opened") {
  const beforeReviews = structuredClone(samples.RETURN_V1_LATEST_V2.beforeResubmit.reviews) as NetworkReviewsSnapshot;
  const previous = beforeReviews.decisions![0], row = beforeReviews.reviews[0], approval = beforeReviews.approvals![0];
  const last = previous.history!.at(-1)!;
  Object.assign(previous, { decision_status: "APPROVED", network_action: "GO", conditions: "", required_data: [],
    override_ack: true, actor_role_id: "site_reviewer" });
  Object.assign(last, { to_status: "APPROVED", action: "APPROVE", network_action: "GO", conditions: "", required_data: [],
    override_ack: true, actor_role_id: "site_reviewer" });
  Object.assign(row, { canonicalDecisionStatus: "APPROVED", status: "approved", statusLabel: "已核准 GO",
    pending: false, decided: true, decision: { ...row.decision!, decision: "GO", finalLabel: "Approved", mappedStatus: "approved",
      conditions: "", requiredData: [], overrideAck: true, actorRoleId: "site_reviewer" } });
  Object.assign(approval, { approvalStatus: "approved", status: "approved" });
  beforeReviews.counts = { reviews: 1, pending: 0, decided: 1 };
  const beforeNetwork = structuredClone(samples.INITIAL.after.network);
  const raw = beforeNetwork.candidates[0];
  Object.assign(raw, { status: state, canonicalSiteStatus: state, reviewId: previous.decision_id,
    reviewWorkflow: { decisionId: previous.decision_id, approvalId: previous.approval_id, reportId: previous.report_id,
      reportVersion: previous.report_version, decisionVersion: previous.version, tenantId: previous.tenant_id,
      networkAction: "GO", status: "approved", conditions: "", requiredData: [] } });
  Reflect.deleteProperty(raw, "reviewSubmission");
  const scoring = structuredClone(samples.INITIAL.after.scoring) as NetworkScoringSnapshot;
  const saved = scoring.scorecards[0];
  saved.reportId = "unit-new-report-v2"; saved.reportVersion = 2;
  const context = structuredClone(samples.INITIAL.beforeContext) as ReviewSubmissionContext;
  Object.assign(context.target, { expectedReportId: saved.reportId, expectedReportVersion: 2,
    expectedLatestReportId: saved.reportId, expectedLatestReportVersion: 2,
    expectedCandidateReviewId: previous.decision_id, expectedCandidateState: state });
  context.previousReview = { decisionId: previous.decision_id, decisionVersion: previous.version!,
    approvalId: previous.approval_id!, approvalStatus: "approved", reportId: previous.report_id,
    reportVersion: previous.report_version, modelVersion: previous.model_version!, tenantId: previous.tenant_id! };
  const body = { ...samples.INITIAL.request, ...context.target } as ReviewSubmissionBody;
  const receipt = structuredClone(samples.INITIAL.postReceipt) as ReviewSubmissionReceipt;
  Object.assign(receipt.records, { decisionId: "unit-new-round", reviewId: "unit-new-round", approvalId: "unit-new-approval",
    reportId: saved.reportId, reportVersion: 2 });
  Object.assign(receipt.commandReceipt, { decisionId: receipt.records.decisionId, previousDecisionId: previous.decision_id,
    candidateState: state });
  Object.assign(receipt.review, { decision_id: receipt.records.decisionId, approval_id: receipt.records.approvalId,
    report_id: saved.reportId, report_version: 2, previous_decision_id: previous.decision_id });
  for (const entry of receipt.review.history as Record<string, unknown>[]) Object.assign(entry, {
    approval_id: receipt.records.approvalId, report_id: saved.reportId, report_version: 2,
    previous_decision_id: previous.decision_id });
  const afterNetwork = structuredClone(samples.INITIAL.after.network);
  Object.assign(afterNetwork.candidates[0], { status: state, reviewId: receipt.records.decisionId,
    reviewSubmission: { ...afterNetwork.candidates[0].reviewSubmission, decisionId: receipt.records.decisionId,
      approvalId: receipt.records.approvalId, reportId: saved.reportId, reportVersion: 2,
      latestReportId: saved.reportId, latestReportVersion: 2, previousDecisionId: previous.decision_id } });
  const afterReviews = structuredClone(samples.INITIAL.after.reviews) as NetworkReviewsSnapshot;
  Object.assign(afterReviews.decisions![0], receipt.review);
  Object.assign(afterReviews.reviews[0], { id: receipt.records.decisionId, reportId: saved.reportId, reportVersion: 2 });
  afterReviews.candidates![0].reviewId = receipt.records.decisionId;
  Object.assign(afterReviews.approvals![0], { id: receipt.records.approvalId, reviewId: receipt.records.decisionId });
  afterReviews.decisions!.push(...structuredClone(beforeReviews.decisions!));
  afterReviews.reviews.push(...structuredClone(beforeReviews.reviews));
  afterReviews.candidates!.push(...structuredClone(beforeReviews.candidates!));
  afterReviews.approvals!.push(...structuredClone(beforeReviews.approvals!));
  afterReviews.counts = { reviews: 2, pending: 1, decided: 1 };
  return { state, previous, beforeReviews, beforeNetwork, scoring, context, body, receipt, afterNetwork, afterReviews };
}
function native(data: ReturnType<typeof roundData>, after = false) {
  return projectCandidateNativeWorkflow({ currentGeneration: 3, tenantId: data.context.tenantId,
    network: { snapshot: after ? data.afterNetwork : data.beforeNetwork, status: "ready", generation: 3 },
    scoring: { snapshot: data.scoring, status: "ready", generation: 3 },
    reviews: { snapshot: after ? data.afterReviews : data.beforeReviews, status: "ready", generation: 3 } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("separate new report review rounds", () => {
  it("keeps an authorized canonical empty review queue eligible for first submit in local POC", async () => {
    const initial = samples.INITIAL;
    nav.search = `ws=network&tab=candidates&candidate=${initial.candidateId}`;
    window.history.replaceState(null, "", `/operator?${nav.search}`);
    window.sessionStorage.setItem("oday.operator.tenant", initial.beforeContext.tenantId);
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
    vi.stubEnv("ODP_E2E_MODE", "local-poc");
    vi.stubEnv("NEXT_PUBLIC_ODP_E2E_MODE", "local-poc");
    expect(operatorFixturesAllowed()).toBe(true);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
    const empty = { ...structuredClone(initial.before.reviews), source: "canonical" as const };
    expect(inspectReviewsReadProjection(empty)).toBe("empty");
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json({ authenticated: true, subject: initial.beforeContext.actorSubjectId, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
      if (url.includes("submission-context")) return json(initial.beforeContext);
      if (url.includes("network-listings")) return json(initial.before.network);
      if (url.includes("network-scoring")) return json(initial.before.scoring);
      if (url.includes("network-reviews")) return json(empty);
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    fireEvent.click(await screen.findByTestId(`candidate-detail-submit-review-${initial.candidateId}`));
    await waitFor(() => expect(screen.getByTestId("review-submission-submit")).toBeEnabled());
    expect(fetch.mock.calls.some(([input]) => String(input).includes("submission-context"))).toBe(true);
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });
  it.each(["approved", "opened"] as const)("admits a bound current context and preserves %s during actual graph readback", (state) => {
    const data = roundData(state);
    expect(inspectReviewsReadProjection(data.beforeReviews)).toBe("ready");
    expect(inspectReviewsReadProjection(data.afterReviews)).toBe("ready");
    expect(hasReviewSubmissionContext(data.context)).toBe(true);
    expect(reviewSubmissionReceiptMatches(data.receipt, data.context, data.body,
      data.receipt.commandReceipt.idempotencyKey, data.receipt.commandReceipt.correlationId!)).toBe(true);
    expect(reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt)).toBe(true);
    expect(native(data, true)[id]).toMatchObject({ stage: "pendingreview", lifecycle: state, availability: "recorded" });
    expect(reviewSubmissionOffers(data.beforeNetwork, data.scoring, data.beforeReviews, native(data))[id])
      .toMatchObject({ decisionId: null, newRound: true, previousDecisionId: data.previous.decision_id });
  });
  it.each(["missing-previous", "foreign-pointer", "same-report", "wrong-tenant", "wrong-state", "old-version"])("rejects incomplete new context %s", (bad) => {
    const data = roundData();
    if (bad === "missing-previous") delete data.context.previousReview;
    if (bad === "foreign-pointer") data.context.target.expectedCandidateReviewId = "foreign";
    if (bad === "same-report") data.context.previousReview!.reportId = data.context.target.expectedReportId;
    if (bad === "wrong-tenant") data.context.previousReview!.tenantId = "foreign";
    if (bad === "wrong-state") data.context.target.expectedCandidateState = "rejected";
    if (bad === "old-version") data.context.previousReview!.reportVersion = 2;
    expect(hasReviewSubmissionContext(data.context)).toBe(false);
  });
  it.each(["decision", "command", "history"])("rejects a receipt with wrong predecessor in %s", (part) => {
    const data = roundData();
    if (part === "decision") data.receipt.review.previous_decision_id = "foreign";
    if (part === "command") data.receipt.commandReceipt.previousDecisionId = "foreign";
    if (part === "history") (data.receipt.review.history as Record<string, unknown>[])[0].previous_decision_id = "foreign";
    expect(reviewSubmissionReceiptMatches(data.receipt, data.context, data.body,
      data.receipt.commandReceipt.idempotencyKey, data.receipt.commandReceipt.correlationId!)).toBe(false);
  });
  it.each(["appended-approved", "appended-foreign-submit"])("refuses the complete malformed current pending graph %s", (corruption) => {
    const data = roundData();
    expect(inspectReviewsReadProjection(data.afterReviews)).toBe("ready");
    expect(reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt)).toBe(true);
    const current = data.afterReviews.decisions![0];
    const last = { ...current.history!.at(-1)!, version: 3,
      action: corruption === "appended-approved" ? "APPROVE" : "submit",
      to_status: corruption === "appended-approved" ? "APPROVED" : "PENDING_REVIEW",
      actor: "foreign-history-actor", submitted_by: "foreign-history-actor" };
    current.history!.push(last);
    data.receipt.review = structuredClone(current) as unknown as ReviewSubmissionReceipt["review"];
    const workflow = native(data, true)[id];
    const observed = { inspection: inspectReviewsReadProjection(data.afterReviews),
      receipt: reviewSubmissionReceiptMatches(data.receipt, data.context, data.body,
        data.receipt.commandReceipt.idempotencyKey, data.receipt.commandReceipt.correlationId!),
      readback: reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt),
      nativeRecordedPending: workflow.availability === "recorded" && workflow.stage === "pendingreview" };
    expect(observed).toEqual({ inspection: "error", receipt: false, readback: false, nativeRecordedPending: false });
  });
  it.each(["status", "action", "version", "report-id", "report-version", "model", "tenant", "approval",
    "actor", "summary", "group", "previous", "verified-sender"])("requires each actual current submit transition field: %s", (field) => {
    const data = roundData();
    const current = data.afterReviews.decisions![0];
    const last = current.history!.at(-1)!;
    const change: Record<string, Record<string, unknown>> = {
      status: { to_status: "APPROVED" }, action: { action: "APPROVE" }, version: { version: 3 },
      "report-id": { report_id: "foreign-report" }, "report-version": { report_version: 3 },
      model: { model_version: "foreign-model" }, tenant: { tenant_id: "foreign-tenant" },
      approval: { approval_id: "foreign-approval" }, actor: { actor: "foreign-actor" },
      summary: { submission_summary: "foreign-summary" }, group: { reviewer_group_id: "foreign-group" },
      previous: { previous_decision_id: "foreign-round" }, "verified-sender": { submission_actor_verified: false },
    };
    Object.assign(last, change[field]);
    data.receipt.review = structuredClone(current) as unknown as ReviewSubmissionReceipt["review"];
    expect(hasCurrentPendingSubmissionHistory(current)).toBe(false);
    expect(inspectReviewsReadProjection(data.afterReviews)).toBe("error");
    expect(reviewSubmissionReceiptMatches(data.receipt, data.context, data.body,
      data.receipt.commandReceipt.idempotencyKey, data.receipt.commandReceipt.correlationId!)).toBe(false);
    expect(reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt)).toBe(false);
  });
  it("uses the last actual RETURN resubmit while preserving its frozen report and later latest report", () => {
    const value = samples.RETURN_V1_LATEST_V2;
    const receipt = value.resubmitReceipt as ReviewSubmissionReceipt;
    const context = value.resubmitContext as ReviewSubmissionContext;
    expect(hasCurrentPendingSubmissionHistory(receipt.review)).toBe(true);
    expect(receipt.review.report_version).toBe(1);
    expect(context.target.expectedLatestReportVersion).toBe(2);
    expect(reviewSubmissionReceiptMatches(receipt, context, value.resubmitRequest as ReviewSubmissionBody,
      receipt.commandReceipt.idempotencyKey, receipt.commandReceipt.correlationId!)).toBe(true);
    expect(reviewSubmissionReadbackMatches(value.afterResubmit.network, value.afterResubmit.reviews, receipt)).toBe(true);
  });
  it.each(["no-version", "unverified"])("keeps %s legacy graph readable without submission confirmation", (kind) => {
    const data = roundData();
    const current = data.afterReviews.decisions![0];
    if (kind === "no-version") delete current.version;
    else current.submission_actor_verified = false;
    expect(inspectReviewsReadProjection(data.afterReviews)).toBe("ready");
    expect(hasCurrentPendingSubmissionHistory(current)).toBe(false);
    expect(reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt)).toBe(false);
    expect(native(data, true)[id]).not.toMatchObject({ availability: "recorded", stage: "pendingreview" });
  });
  it.each(["missing-approval", "wrong-tenant", "wrong-pointer", "regressed-opened", "cycle"])("requires the complete preserved prior graph before pending UI: %s", (bad) => {
    const data = roundData();
    if (bad === "missing-approval") data.afterReviews.approvals = data.afterReviews.approvals!.filter((row) => row.reviewId !== data.previous.decision_id);
    if (bad === "wrong-tenant") data.afterReviews.decisions!.find((row) => row.decision_id === data.previous.decision_id)!.tenant_id = "foreign";
    if (bad === "wrong-pointer") Reflect.set(data.afterNetwork.candidates[0].reviewSubmission, "previousDecisionId", "foreign");
    if (bad === "regressed-opened") data.afterNetwork.candidates[0].status = "approved";
    if (bad === "cycle") data.afterReviews.decisions!.find((row) => row.decision_id === data.previous.decision_id)!.previous_decision_id = data.previous.decision_id;
    expect(reviewSubmissionReadbackMatches(data.afterNetwork, data.afterReviews, data.receipt)).toBe(false);
  });
  it("offers no reopening of the same saved terminal report", () => {
    const data = roundData();
    data.scoring.scorecards[0].reportId = data.previous.report_id;
    data.scoring.scorecards[0].reportVersion = data.previous.report_version;
    expect(reviewSubmissionOffers(data.beforeNetwork, data.scoring, data.beforeReviews, native(data))).toEqual({});
  });
  it.each(["GO", "WAIT", "REJECT"] as const)("offers a third report after second-round %s while keeping the opened lifecycle", (action) => {
    const data = roundData();
    const decision = data.afterReviews.decisions![0], row = data.afterReviews.reviews[0], approval = data.afterReviews.approvals![0];
    const status = action === "REJECT" ? "REJECTED" : "APPROVED";
    const shown = action === "WAIT" ? "onhold" : action === "REJECT" ? "rejected" : "approved";
    const conditions = action === "WAIT" ? "Recorded second-round lease condition" : "";
    const last = { ...decision.history!.at(-1)!, to_status: status, action: action === "REJECT" ? "REJECT" : "APPROVE",
      actor: "unit-independent-checker", reason: "Recorded independent second-round decision", at: "2026-10-14T00:00:00+00:00",
      network_action: action, conditions, required_data: [], override_ack: true, actor_role_id: "site_reviewer",
      report_id: decision.report_id, report_version: decision.report_version, model_version: decision.model_version,
      tenant_id: decision.tenant_id, approval_id: decision.approval_id, version: 3 };
    decision.history!.push(last);
    Object.assign(decision, { decision_status: status, network_action: action, conditions, required_data: [],
      override_ack: true, actor_role_id: "site_reviewer", version: 3 });
    Object.assign(row, { canonicalDecisionStatus: status, status: shown, pending: false, decided: true, decision: {
      decision: action, finalLabel: action === "WAIT" ? "On Hold" : action === "REJECT" ? "Rejected" : "Approved",
      mappedStatus: shown, reason: last.reason, decidedAt: last.at, decidedBy: last.actor, override: true,
      decisionId: decision.decision_id, conditions, requiredData: [], overrideAck: true, actorRoleId: "site_reviewer",
      approvalId: decision.approval_id } });
    Object.assign(approval, { approvalStatus: action === "REJECT" ? "rejected" : "approved",
      status: action === "WAIT" ? "on_hold" : shown, decidedBy: last.actor, decidedAt: last.at });
    Reflect.deleteProperty(data.afterNetwork.candidates[0], "reviewSubmission");
    Object.assign(data.afterNetwork.candidates[0], { canonicalSiteStatus: "opened", reviewWorkflow: {
      decisionId: decision.decision_id, approvalId: decision.approval_id, reportId: decision.report_id,
      reportVersion: decision.report_version, decisionVersion: 3, tenantId: decision.tenant_id,
      previousDecisionId: data.previous.decision_id, networkAction: action, status: shown, conditions, requiredData: [] } });
    data.scoring.scorecards[0].reportId = "unit-third-report-v3";
    data.scoring.scorecards[0].reportVersion = 3;
    const reads = native(data, true);
    expect(inspectReviewsReadProjection(data.afterReviews)).toBe("ready");
    expect(reads[id]).toMatchObject({ availability: "recorded", lifecycle: "opened", stage: shown });
    expect(reviewSubmissionOffers(data.afterNetwork, data.scoring, data.afterReviews, reads)[id])
      .toMatchObject({ decisionId: null, newRound: true, previousDecisionId: decision.decision_id });
  });
  it("rejects a missing actual original Approval rather than inferring approval from terminal state", () => {
    const data = roundData(); data.beforeReviews.approvals![0].recordSource = "unavailable";
    expect(hasApprovedReviewHistory(data.beforeReviews, id, data.context.tenantId, data.previous.decision_id, 2)).toBe(false);
    expect(reviewSubmissionOffers(data.beforeNetwork, data.scoring, data.beforeReviews, native(data))).toEqual({});
  });
  it("shows the new-round notice and confirms only after fresh full pending graph", async () => {
    const data = roundData(); let accepted = false;
    nav.search = `ws=network&tab=candidates&candidate=${id}`;
    window.history.replaceState(null, "", `/operator?${nav.search}`);
    window.sessionStorage.setItem("oday.operator.tenant", data.context.tenantId);
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValueOnce(data.receipt.commandReceipt.idempotencyKey as `${string}-${string}-${string}-${string}-${string}`)
      .mockReturnValueOnce(data.receipt.commandReceipt.correlationId as `${string}-${string}-${string}-${string}-${string}`);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/auth/session") return json({ authenticated: true, subject: data.context.actorSubjectId, expiresAt: Math.floor(Date.now() / 1000) + 3600 });
      if (url.includes("submission-context")) return json(data.context);
      if (url.endsWith("/submit")) { accepted = true; return json(data.receipt); }
      if (url.includes("network-listings")) return json(accepted ? data.afterNetwork : data.beforeNetwork);
      if (url.includes("network-scoring")) return json(data.scoring);
      if (url.includes("network-reviews")) return json(accepted ? data.afterReviews : data.beforeReviews);
      return new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    const entry = await screen.findByTestId(`candidate-detail-submit-review-${id}`);
    expect(entry).toHaveTextContent("新版報告另開審查"); fireEvent.click(entry);
    await waitFor(() => expect(screen.getByTestId("review-submission-submit")).toBeEnabled());
    expect(screen.getByText(/新版報告另開一輪審查/)).toBeVisible();
    fireEvent.change(screen.getByTestId("review-submission-summary"), { target: { value: data.body.submissionSummary } });
    fireEvent.change(screen.getByTestId("review-submission-group"), { target: { value: data.body.reviewerGroupId } });
    fireEvent.click(screen.getByTestId("review-submission-submit"));
    expect(await screen.findByTestId("review-submission-confirmed")).toHaveTextContent(data.receipt.records.decisionId);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
});
