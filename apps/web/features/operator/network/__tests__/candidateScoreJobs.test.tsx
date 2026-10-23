import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { candidateScoreJob, readCandidateScoreJobs, type CandidateScoreJobsRead } from "../candidateScoreJobs";
import { useCandidateScoreJobs } from "../useCandidateScoreJobs";
import { candidatePipelineControls, candidateMatchesPipeline } from "../candidateDesignControls";
import type { CandidateNativeWorkflowReads, NetworkScoringSnapshot, ScoringCandidate } from "../networkScoringTypes";

// Explicit HTTP controls: these do not claim a production principal or model admission.
const headers = { "x-tenant-id": "tenant" };
function candidate(id = "second"): ScoringCandidate {
  const fact = { state: "verified" as const, reasonCode: null };
  return { id, listingId: `listing-${id}`, currentAdmission: { candidateId: id, listingId: `listing-${id}`,
    addressId: `address-${id}`, checkedAt: "2026-10-15T00:00:00Z", currentInputs: fact, pit: fact, model: fact,
    source: { ...fact, jobId: `job-${id}`, intakeId: `intake-${id}`, promotionDecisionId: `promotion-${id}` } } } as ScoringCandidate;
}
const report = { id: "second", reportId: "report-second", reportVersion: 2, sitescoreRunId: "run-second", modelVersion: "model-second" };
const snapshot = (candidates = [candidate()], scorecards: unknown[] = []): NetworkScoringSnapshot =>
  ({ source: "canonical", candidates, scorecards } as NetworkScoringSnapshot);
const original = (status: string, id = "second") => ({ job_id: `job-${id}`, status, attempt: 1, version: 3 });
const context = (activeExecutionRequest: unknown = null) => ({ tenantId: "tenant", candidateSiteId: "second",
  originalJobId: "job-second", predecessorJobId: "job-second", jobVersion: 3,
  reportId: report.reportId, reportVersion: report.reportVersion, activeExecutionRequest });
const execution = (status: string) => ({ source: "canonical", created: false, executionRequestId: "request-second",
  candidateSiteId: "second", jobId: "fresh-second", status, actualVersion: 3, jobVersion: 4,
  baseReportId: report.reportId, baseReportVersion: report.reportVersion, result: null, lastError: null, correlationId: "correlation" });
function transport(responses: Record<string, unknown>) {
  return vi.fn<typeof fetch>(async (url) => {
    const path = String(url);
    if (!(path in responses)) throw new Error(`Unexpected request ${path}`);
    const value = responses[path];
    return value instanceof Response ? value : Response.json(value);
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("independent authorized Candidate job observations", () => {
  it.each(["QUEUED", "RUNNING", "FAILED", "CANCELLED"])("reads initial %s without requiring a saved report or execution context", async (status) => {
    const get = transport({ "/api/v1/jobs/job-second/receipt": original(status) });
    const value = await readCandidateScoreJobs(snapshot(), headers, undefined, get);
    expect(value.jobs[0]).toMatchObject({ candidateId: "second", listingId: "listing-second", availability: "recorded", status, jobId: "job-second", version: 3 });
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0][1]).toMatchObject({ headers, cache: "no-store" });
    expect(get.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
  it("preserves original outcome and delivery state without writing a prototype domain status", async () => {
    const get = transport({ "/api/v1/jobs/job-second/receipt": { ...original("FAILED"), delivery_state: "DEAD_LETTER" } });
    expect((await readCandidateScoreJobs(snapshot(), headers, undefined, get)).jobs[0]).toMatchObject({ status: "FAILED", deliveryState: "DEAD_LETTER" });
  });
  it("does not request a guessed job when source identity is unverified, while a blocked model is independent of job visibility", async () => {
    const unknown = candidate("first"); unknown.currentAdmission!.source.state = "unverified";
    const known = candidate(); known.currentAdmission!.model = { state: "blocked", reasonCode: "MODEL_REVOKED" };
    const get = transport({ "/api/v1/jobs/job-second/receipt": original("RUNNING") });
    const value = await readCandidateScoreJobs(snapshot([unknown, known]), headers, undefined, get);
    expect(value.jobs.map((row) => [row.candidateId, row.availability])).toEqual([["first", "unverified"], ["second", "recorded"]]);
    expect(get).toHaveBeenCalledTimes(1);
  });
  it("keeps a current failed rescore independent of its exact historical report", async () => {
    const get = transport({ "/api/v1/jobs/job-second/receipt": original("SUCCEEDED"),
      "/api/v1/operator/network-scoring/candidates/second/execution-context": context(execution("FAILED")) });
    const value = await readCandidateScoreJobs(snapshot(undefined, [report]), headers, undefined, get);
    expect(value.jobs[0]).toMatchObject({ availability: "recorded", jobId: "fresh-second", executionRequestId: "request-second",
      status: "FAILED", reportId: report.reportId, reportVersion: 2 });
  });
  it("restores the latest successful rescore when there is no active request", async () => {
    const completed = { ...execution("SUCCEEDED"), result: { report_id: report.reportId, report_version: 2,
      sitescore_run_id: report.sitescoreRunId, prediction_run_id: "prediction", model_version: report.modelVersion } };
    const get = transport({ "/api/v1/jobs/job-second/receipt": original("SUCCEEDED"),
      "/api/v1/operator/network-scoring/candidates/second/execution-context": { ...context(), predecessorJobId: "fresh-second" },
      "/api/v1/jobs/fresh-second": completed });
    expect((await readCandidateScoreJobs(snapshot(undefined, [report]), headers, undefined, get)).jobs[0]).toMatchObject({ jobId: "fresh-second", status: "SUCCEEDED", reportVersion: 2 });
  });
  it("retains denial rather than falling back to a successful original job or borrowing another report", async () => {
    const get = transport({ "/api/v1/jobs/job-second/receipt": original("SUCCEEDED"),
      "/api/v1/operator/network-scoring/candidates/second/execution-context": new Response("", { status: 403 }) });
    expect((await readCandidateScoreJobs(snapshot(undefined, [report]), headers, undefined, get)).jobs[0].availability).toBe("denied");
    const crossed = transport({ "/api/v1/jobs/job-second/receipt": original("SUCCEEDED"),
      "/api/v1/operator/network-scoring/candidates/second/execution-context": { ...context(), tenantId: "other" } });
    expect((await readCandidateScoreJobs(snapshot(undefined, [report]), headers, undefined, crossed)).jobs[0].availability).toBe("conflict");
  });
  it("does not infer report availability from SUCCEEDED, accept the wrong job id, or use an old generation", async () => {
    const get = transport({ "/api/v1/jobs/job-second/receipt": original("SUCCEEDED") });
    const value = await readCandidateScoreJobs(snapshot(), headers, undefined, get);
    expect(value.jobs[0]).toMatchObject({ availability: "conflict", status: "SUCCEEDED" });
    const read: CandidateScoreJobsRead = { snapshot: value, generation: 4, status: "ready" };
    expect(candidateScoreJob(read, 5, "second", "listing-second")).toBeNull();
    const wrong = transport({ "/api/v1/jobs/job-second/receipt": original("RUNNING", "first") });
    expect((await readCandidateScoreJobs(snapshot(), headers, undefined, wrong)).jobs[0].availability).toBe("conflict");
  });
  it("counts scoring from complete job observations independently of the existing approved lifecycle", async () => {
    const value = await readCandidateScoreJobs(snapshot(), headers, undefined, transport({ "/api/v1/jobs/job-second/receipt": original("RUNNING") }));
    const reads: CandidateNativeWorkflowReads = { second: { candidateId: "second", stage: "approved", lifecycle: "approved",
      availability: "recorded", reason: "persisted approval", review: null, scoreJob: value.jobs[0] } };
    expect(candidateMatchesPipeline(candidate(), "scoring", reads)).toBe(true);
    expect(candidatePipelineControls([candidate()], reads).find((row) => row.id === "scoring")).toMatchObject({ available: true, count: 1 });
    expect(candidatePipelineControls([candidate(), candidate("first")], reads).find((row) => row.id === "scoring")).toMatchObject({ available: false, count: null });
    expect(candidatePipelineControls([candidate()], reads).find((row) => row.id === "shortlisted")?.available).toBe(false);
  });
  it("masks the old collection synchronously and rejects a delayed read after an access generation changes", async () => {
    let resolveOld!: (response: Response) => void;
    const old = snapshot([candidate("first")]); const current = snapshot();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("job-first/receipt")
      ? new Promise<Response>((resolve) => { resolveOld = resolve; }) : Response.json(original("RUNNING"))));
    const terminal = vi.fn(async () => {});
    const { result, rerender, unmount } = renderHook(({ source, generation }) =>
      useCandidateScoreJobs(source, headers, String(generation), generation, "", terminal),
    { initialProps: { source: old, generation: 4 } });
    rerender({ source: current, generation: 5 });
    expect(result.current.snapshot).toBeNull();
    await waitFor(() => expect(result.current.snapshot?.jobs[0].candidateId).toBe("second"));
    await act(async () => { resolveOld(Response.json(original("RUNNING", "first"))); });
    expect(result.current.snapshot?.jobs.map((job) => job.candidateId)).toEqual(["second"]);
    unmount();
  });
});
