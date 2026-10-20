import "@testing-library/jest-dom/vitest";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as Workspace } from "../../NetworkFindAreasWorkspace";
import { NETWORK_TAB_IDS } from "../networkUrlState";

// Explicit synthetic bootstrap tabs for these component controls. Backend
// scope/role denials and read revalidation assertions remain authoritative.
function NetworkFindAreasWorkspace(props: Parameters<typeof Workspace>[0]) {
  return <Workspace allowedTabIds={NETWORK_TAB_IDS} {...props} />;
}
import { projectCandidateNativeWorkflow, resolveCandidateReviewTarget } from "../candidateNativeWorkflowProjection";
import { CandidatePanel } from "../CandidatePanel";
import { CandidateWorkflowFacts } from "../CandidateWorkflowFacts";
import { candidateMatchesPipeline, candidatePipelineControls } from "../candidateDesignControls";
import { inspectReviewsReadProjection } from "../networkReviewProjection";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }) }));
vi.mock("../intake/AssistedIntakeSection", async (original) => ({
  ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div />,
}));
// Actual existing durable Python producers and guarded masked GETs. The
// subprocess owns only explicit offline fixtures; it never loads or infers.
const root = resolve(process.cwd(), "../..");
const producer = resolve(root, "tests/integration/test_candidate_native_workflow_readback.py");
const facts = JSON.parse(execFileSync(process.env.ODP_TEST_PYTHON ?? resolve(root, ".venv/bin/python"), [producer], { cwd: root,
  env: { ...process.env, PYTHONPATH: root, ODP_REQUIRE_LIVE_DATA: "false", ODP_PRODUCT_MODE: "poc", ODAY_ENV: "test" }, encoding: "utf8" }));
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function mount(name: string, reviewStatus = 200) {
  const value = facts[name];
  nav.search = `ws=network&tab=candidates&candidate=${value.candidateId}`;
  window.history.replaceState(null, "", `/operator?${nav.search}`);
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    return url.includes("network-listings") ? json(value.network) : url.includes("network-scoring") ? json(value.scoring)
      : url.includes("network-reviews") ? json(reviewStatus === 200 ? value.reviews : { detail: "scope denied" }, reviewStatus) : json({}, 403);
  });
  vi.stubGlobal("fetch", fetch);
  render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
  return { value, fetch };
}
beforeEach(() => { window.sessionStorage.clear(); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("canonical candidate stages from actual durable producers", () => {
  it.each([["GO", "approved"], ["WAIT", "onhold"], ["RETURN", "needdata"], ["REJECT", "rejected"], ["PENDING", "pendingreview"]])(
    "%s has one primary stage %s instead of overlapping evaluated", async (name, stage) => {
      const { value } = mount(name);
      await screen.findByTestId(`candidate-row-${value.candidateId}`);
      await waitFor(() => expect(screen.getByTestId(`candidate-pipeline-${stage}`)).toBeEnabled());
      expect(within(screen.getByTestId(`candidate-pipeline-${stage}`)).getByText("1")).toBeVisible();
      expect(within(screen.getByTestId("candidate-pipeline-evaluated")).getByText("0")).toBeVisible();
      expect(screen.getByTestId("candidate-pipeline-shortlisted")).toBeDisabled();
      expect(screen.getByTestId("candidate-pipeline-scoring")).toBeDisabled();
      if (stage === "pendingreview") expect(screen.getByTestId("network-step-review")).toHaveAttribute("data-state", "current");
    });
  it.each(["GO_V2", "OPENED_V3"])("%s retains own human state without approving a newer report", async (name) => {
    const { value, fetch } = mount(name);
    await screen.findByTestId(`candidate-row-${value.candidateId}`);
    await waitFor(() => expect(screen.getByTestId("candidate-pipeline-approved")).toBeEnabled());
    expect(within(screen.getByTestId("candidate-pipeline-approved")).getByText("1")).toBeVisible();
    expect(screen.getByTestId(`candidate-review-provenance-${value.candidateId}`)).toHaveTextContent("v1");
    expect(screen.getByTestId(`candidate-review-provenance-${value.candidateId}`)).toHaveTextContent(`v${value.latestReportVersion}`);
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
  it("does not promote generic APPROVED or model GO into an explicit human GO", async () => {
    const { value } = mount("UNKNOWN_APPROVED");
    await screen.findByTestId(`candidate-row-${value.candidateId}`);
    expect(screen.getByTestId("candidate-pipeline-approved")).toBeDisabled();
    expect(screen.getByTestId("network-step-review")).not.toHaveAttribute("data-state", "completed");
  });
  it("review denial keeps the stage relation unknown and preserves masked candidate reads", async () => {
    const { value } = mount("GO", 403);
    await screen.findByTestId(`candidate-row-${value.candidateId}`);
    expect(screen.getByTestId("candidate-pipeline-approved")).toBeDisabled();
    expect(screen.queryByTestId(`candidate-view-review-${value.candidateId}`)).not.toBeInTheDocument();
  });
  it("actual data readiness remains visible with model unknown, while absent initial/batch execution owners remain unavailable", async () => {
    const { value, fetch } = mount("UNSCORED");
    await screen.findByTestId(`candidate-row-${value.candidateId}`);
    await waitFor(() => expect(screen.getByTestId("candidate-pipeline-ready")).toBeEnabled());
    expect(within(screen.getByTestId("candidate-pipeline-ready")).getByText("1")).toBeVisible();
    expect(value.scoring.candidates[0].currentAdmission.model.state).toBe("unverified");
    expect(screen.getByTestId(`candidate-detail-score-${value.candidateId}`)).toBeDisabled();
    expect(screen.getByTestId(`candidate-score-${value.candidateId}`)).toBeDisabled();
    expect(screen.getByTestId("candidate-score-all")).toBeDisabled();
    expect(screen.getByTestId(`candidate-detail-score-${value.candidateId}`)).toHaveTextContent("初次評分受理未接通");
    expect(fetch.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);
  });
});

function readFacts(name = "GO") {
  const value = structuredClone(facts[name]);
  return { value, input: { currentGeneration: 4, tenantId: "offline-review-metadata-tenant",
    network: { snapshot: value.network, status: "ready", generation: 4 },
    scoring: { snapshot: value.scoring, status: "ready", generation: 4 },
    reviews: { snapshot: value.reviews, status: "ready", generation: 4 } } };
}
describe("strict candidate read joins, with actual producer envelopes", () => {
  it("retains independently authorized data/report reads when review access is denied", async () => {
    const { value } = mount("GO", 403);
    await screen.findByTestId("candidate-workflow-facts");
    expect(screen.getAllByTestId(`candidate-state-data-${value.candidateId}`)).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId(`candidate-state-report-${value.candidateId}`))
      .toHaveTextContent(value.scoring.scorecards[0].reportId));
    expect(screen.getByTestId(`candidate-state-data-${value.candidateId}`)).toHaveTextContent(
      value.scoring.candidates[0].gate.passed ? "資料齊備" : "需補資料");
    expect(screen.getByTestId(`candidate-state-review-${value.candidateId}`)).toHaveTextContent("尚未核驗");
    expect(screen.queryByTestId(`candidate-view-review-${value.candidateId}`)).not.toBeInTheDocument();
  });

  it.each(["FAILED", "SUCCEEDED"] as const)("keeps a failed candidate, %s job and retained report independent", (status) => {
    const { value, input } = readFacts("PENDING");
    for (const key of ["reviews", "approvals", "decisions", "candidates"]) value.reviews[key] = [];
    input.reviews.status = "empty";
    const candidate = value.scoring.candidates[0];
    value.network.candidates[0].canonicalSiteStatus = value.network.candidates[0].status = "SCORING_FAILED";
    candidate.canonicalSiteStatus = "SCORING_FAILED";
    const job = { candidateId: value.candidateId, listingId: candidate.listingId, availability: "recorded" as const,
      jobId: "authorized-failed-job", executionRequestId: null, status, deliveryState: null,
      version: 3, attempt: 2, reportId: null, reportVersion: null, reason: "Actual authorized failure receipt" };
    const reads = projectCandidateNativeWorkflow({ ...input, jobs: { status: "ready", generation: 4,
      snapshot: { source: "canonical", jobs: [job] } } });
    const read = reads[value.candidateId];
    expect(read.lifecycle).toBe("SCORING_FAILED");
    expect(read.stage).toBeNull();
    expect(read.availability).toBe(status === "SUCCEEDED" ? "conflict" : "unavailable");
    expect(read.scoreJob).toMatchObject({ availability: "recorded", jobId: job.jobId, status });
    expect(read.scoreReport).toMatchObject({ availability: "recorded", reportId: value.scoring.scorecards[0].reportId });
    expect(read.reviewDecision).toMatchObject({ availability: "absent", decisionId: null });
    render(<><CandidateWorkflowFacts read={read} /><CandidatePanel candidates={value.scoring.candidates} fallbackRows={[]} nativeWorkflow={reads} selectedCandidateId={value.candidateId} /></>);
    expect(within(screen.getByTestId(`candidate-row-${value.candidateId}`)).getByText(status === "FAILED" ? "評分工作失敗" : "候選點評分失敗")).toBeVisible();
    expect(screen.getByTestId(`candidate-state-job-${value.candidateId}`)).toHaveTextContent(status);
    expect(screen.getByTestId(`candidate-state-report-${value.candidateId}`)).toHaveTextContent(value.scoring.scorecards[0].reportId);
  });

  it("refuses inconsistent persisted lifecycle reads while retaining independent historical report identity", () => {
    const { value, input } = readFacts("GO");
    value.scoring.candidates[0].canonicalSiteStatus = "SCORING_FAILED";
    const read = projectCandidateNativeWorkflow(input)[value.candidateId];
    expect(read.availability).toBe("conflict");
    expect(read.stage).toBeNull();
    expect(read.scoreReport).toMatchObject({ availability: "recorded", reportId: value.scoring.scorecards[0].reportId });
    expect(read.reviewDecision).toMatchObject({ availability: "unavailable", decisionId: null });
  });

  it("clears all independent identities when the access generation changes", () => {
    const { value, input } = readFacts("GO");
    input.network.generation = 3;
    const read = projectCandidateNativeWorkflow(input)[value.candidateId];
    expect(read.dataReadiness).toMatchObject({ availability: "unavailable", state: null });
    expect(read.scoreReport).toMatchObject({ availability: "unavailable", reportId: null });
    expect(read.reviewDecision).toMatchObject({ availability: "unavailable", decisionId: null });
    expect(read.scoreJob).toBeUndefined();
  });

  it.each(["hard", "geo", "needdata", "missing"])("%s contradicting passed=true does not certify data ready", (change) => {
    const { value, input } = readFacts("UNSCORED");
    const gate = value.scoring.candidates[0].gate;
    if (change === "missing") gate.missing = ["rent is missing"]; else gate.state = change;
    const reads = projectCandidateNativeWorkflow(input);
    expect(reads[value.candidateId].stage).toBeNull();
    expect(reads[value.candidateId].availability).toBe("conflict");
    expect(candidatePipelineControls(value.scoring.candidates, reads).find((control) => control.id === "ready")?.count).toBeNull();
  });
  it("warn with nonblocking supplementary evidence still certifies data readiness independently of model unknown", () => {
    const { value, input } = readFacts("UNSCORED");
    value.scoring.candidates[0].gate.state = "warn";
    value.scoring.candidates[0].gate.otherMissing = ["supplementary field visit"];
    expect(value.scoring.candidates[0].currentAdmission.model.state).toBe("unverified");
    expect(projectCandidateNativeWorkflow(input)[value.candidateId].stage).toBe("ready");
  });
  it("uses server-validated nested persisted pointer when legacy namespace masks the top copy", () => {
    const { value, input } = readFacts();
    expect(value.network.candidates[0].reviewId).toBeNull();
    const read = projectCandidateNativeWorkflow(input)[value.candidateId];
    expect(read.stage).toBe("approved"); expect(read.review?.reviewId).toBe(value.reviewId);
    expect(read.lifecycle).toBe("approved");
  });
  it.each(["top-pointer", "nested-pointer", "tenant", "listing-tenant", "decision-version", "approval", "frozen-report", "action", "conditions", "required-data", "actor", "model", "listing", "metadata-absent", "opened-conflict"])(
    "%s never substitutes a model GO for an inconsistent persisted relation", (field) => {
      const { value, input } = readFacts(field === "opened-conflict" ? "WAIT" : "GO");
      const row = value.network.candidates[0], meta = row.reviewWorkflow;
      if (field === "top-pointer") row.reviewId = "foreign-decision";
      if (field === "nested-pointer") meta.decisionId = "foreign-decision";
      if (field === "tenant") meta.tenantId = "foreign-tenant";
      if (field === "listing-tenant") value.network.listings[0].tenantId = "foreign-tenant";
      if (field === "decision-version") meta.decisionVersion += 1;
      if (field === "approval") meta.approvalId = value.reviewId;
      if (field === "frozen-report") meta.reportId = "foreign-report";
      if (field === "action") meta.networkAction = "WAIT";
      if (field === "conditions") meta.conditions = "invented condition";
      if (field === "required-data") meta.requiredData = ["invented data"];
      if (field === "actor") value.reviews.reviews[0].decision.decidedBy = "foreign-actor";
      if (field === "model") value.scoring.scorecards[0].modelVersion = "foreign-model";
      if (field === "listing") value.scoring.candidates[0].listingId = "foreign-listing";
      if (field === "metadata-absent") delete row.reviewWorkflow;
      if (field === "opened-conflict") row.canonicalSiteStatus = "opened";
      const read = projectCandidateNativeWorkflow(input)[value.candidateId];
      expect(read.stage).toBeNull(); expect(read.review).toBeNull();
      if (field !== "listing") expect(read.lifecycle).toBe(row.canonicalSiteStatus);
      expect(resolveCandidateReviewTarget({ [value.candidateId]: read }, value.candidateId, { id: value.reviewId, invalid: false }).target).toBeNull();
    });
  it.each(["denied", "stale", "malformed", "source-api", "approval-unavailable", "duplicate-graph", "foreign-observed-tenant"])(
    "%s does not turn unknown joins into authoritative empty counts", (problem) => {
      const { value, input } = readFacts();
      if (problem === "denied") input.reviews.status = "error";
      if (problem === "stale") input.reviews.generation = 3;
      if (problem === "malformed") value.reviews.reviews[0].history = [null];
      if (problem === "source-api") value.reviews.source = "api";
      if (problem === "approval-unavailable") value.reviews.approvals[0].recordSource = "unavailable";
      if (problem === "duplicate-graph") value.reviews.decisions.push(structuredClone(value.reviews.decisions[0]));
      if (problem === "foreign-observed-tenant") input.tenantId = "foreign-tenant";
      const reads = projectCandidateNativeWorkflow(input);
      expect(reads[value.candidateId].stage).toBeNull(); expect(reads[value.candidateId].review).toBeNull();
      expect(candidatePipelineControls(value.scoring.candidates, reads).find((control) => control.id === "approved")?.count).toBeNull();
    });
  it("RETURN overrides a complete six-gate and a retained model GO report", () => {
    const { value, input } = readFacts("RETURN"); const reads = projectCandidateNativeWorkflow(input);
    expect(value.scoring.candidates[0].gate.passed).toBe(true);
    expect(candidateMatchesPipeline(value.scoring.candidates[0], "needdata", reads)).toBe(true);
    expect(candidateMatchesPipeline(value.scoring.candidates[0], "evaluated", reads)).toBe(false);
    expect(candidateMatchesPipeline(value.scoring.candidates[0], "ready", reads)).toBe(false);
    expect(reads[value.candidateId].reason).toContain(value.reviews.decisions[0].required_data[0]);
  });
  it("keeps actual unmeasured confidence null without fabricating zero or blocking valid human readback", () => {
    const { value, input } = readFacts("GO_UNMEASURED");
    expect(value.reviews.reviews[0].confidence).toBeNull();
    expect(value.reviews.reviews[0].confidenceStatus).toBe("unmeasured");
    expect(projectCandidateNativeWorkflow(input)[value.candidateId].stage).toBe("approved");
  });
  it("only a unique current pending Decision with a persisted independent Approval can match without metadata", () => {
    const { value, input } = readFacts("PENDING");
    expect(value.network.candidates[0].reviewWorkflow).toBeUndefined();
    expect(projectCandidateNativeWorkflow(input)[value.candidateId].stage).toBe("pendingreview");
    value.scoring.scorecards[0].reportVersion = 2;
    expect(projectCandidateNativeWorkflow(input)[value.candidateId].stage).toBeNull();
    value.scoring.scorecards[0].reportVersion = 1;
    value.network.candidates[0].reviewId = "foreign-pointer";
    expect(projectCandidateNativeWorkflow(input)[value.candidateId].stage).toBeNull();
  });
  it("rejects two distinct valid actual pending records without choosing the first or declaring an empty stage", () => {
    const { value, input } = readFacts("AMBIGUOUS_PENDING");
    expect(inspectReviewsReadProjection(value.reviews)).toBe("ready");
    expect(value.reviews.reviews).toHaveLength(2);
    const reads = projectCandidateNativeWorkflow(input);
    expect(reads[value.candidateId].availability).toBe("conflict");
    expect(reads[value.candidateId].review).toBeNull();
    expect(candidatePipelineControls(value.scoring.candidates, reads).find((control) => control.id === "pendingreview")?.count).toBeNull();
  });
  it("a truly empty canonical review graph permits saved-report reads without manufacturing job or shortlist counts", () => {
    const { value, input } = readFacts("PENDING");
    for (const key of ["reviews", "approvals", "decisions", "candidates"]) value.reviews[key] = [];
    input.reviews.status = "empty";
    const reads = projectCandidateNativeWorkflow(input), controls = candidatePipelineControls(value.scoring.candidates, reads);
    expect(reads[value.candidateId].stage).toBe("evaluated");
    expect(controls.find((control) => control.id === "approved")?.count).toBe(0);
    expect(controls.find((control) => control.id === "scoring")?.count).toBeNull();
    expect(controls.find((control) => control.id === "shortlisted")?.count).toBeNull();
  });
});
