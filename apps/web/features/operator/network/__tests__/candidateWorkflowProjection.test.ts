import { describe, expect, it } from "vitest";
import { projectCandidateWorkflow, type WorkflowNetworkSnapshot, type WorkflowRead, type WorkflowReviewSnapshot, type WorkflowScoringSnapshot } from "../networkWorkflowProjection";

// Synthetic current-read identities; no production scoring rules are asserted.
function input() {
  const current = <T>(snapshot: T): WorkflowRead<T> => ({ snapshot, status: "ready", generation: 4 });
  return {
    selection: { id: "second" as string | null, invalid: false }, currentGeneration: 4,
    network: current<WorkflowNetworkSnapshot>({ source: "api", heatZones: [{ id: "zone" }],
      listings: [{ id: "listing-first" }, { id: "listing-second" }], candidates: [
        { id: "first", listingId: "listing-first", status: "scored" },
        { id: "second", listingId: "listing-second", heatZoneId: "zone", status: "scored", datasetSnapshotId: "snapshot" },
      ] }),
    scoring: current<WorkflowScoringSnapshot>({ source: "canonical", compareSet: [],
      candidates: [{ id: "first", listingId: "listing-first" }, { id: "second", listingId: "listing-second" }],
      scorecards: [{ id: "first", reportId: "report-first", reportVersion: 2, sitescoreRunId: "run-first", modelVersion: "model-first" },
        { id: "second", reportId: "report-second", reportVersion: 3, sitescoreRunId: "run-second", modelVersion: "model-second", datasetSnapshotId: "snapshot" }],
    }),
    reviews: current<WorkflowReviewSnapshot>({ source: "canonical", reviews: [] }),
  };
}
const step = (result: ReturnType<typeof projectCandidateWorkflow>, id: string) => result.steps.find((row) => row.id === id)!;

describe("exact authorized workflow read projection", () => {
  it("joins the selected second record and its recorded report without needing a scalar score", () => {
    const result = projectCandidateWorkflow(input());
    expect(result.report).toEqual({ candidateId: "second", reportId: "report-second", reportVersion: 3,
      sitescoreRunId: "run-second", modelVersion: "model-second", datasetSnapshotId: "snapshot" });
    expect(step(result, "sitescore").state).toBe("completed");
    expect(result.steps).toHaveLength(6);
    expect(result.currentJob).toBeNull(); expect(result.executionAdmission).toBe("unknown");
    expect(result.nextAction).not.toContain("執行");
  });

  it.each([null, "missing"])("does not substitute the first record for selection %s", (id) => {
    const value = input(); value.selection.id = id;
    const result = projectCandidateWorkflow(value);
    expect(result.candidateId).toBeNull(); expect(result.report).toBeNull();
    expect(result.steps.every((row) => row.entityId === null)).toBe(true);
    expect(result.steps.every((row) => row.navigationAvailable === true)).toBe(true);
  });

  it("refuses an ambiguous URL without treating it as an unselected authorized collection", () => {
    const value = input(); value.selection = { id: null, invalid: true };
    expect(projectCandidateWorkflow(value).reason).toBe("invalid_selection");
  });

  it.each(["network", "scoring"] as const)("keeps a denied/error %s read unknown rather than empty or scored", (key) => {
    const value = input(); value[key].status = "error";
    const result = projectCandidateWorkflow(value);
    expect(result.reason).toBe(`${key}_read_error`); expect(result.report).toBeNull();
  });

  it.each(["network", "scoring"] as const)("discards old-generation %s even if its data says scored", (key) => {
    const value = input(); value[key].generation = 3;
    expect(projectCandidateWorkflow(value).reason).toBe(`${key}_stale_generation`);
  });

  it("distinguishes a successful authorized empty read from a refused read", () => {
    const value = input(); value.scoring = { snapshot: null, status: "empty", generation: 4 };
    expect(projectCandidateWorkflow(value).reason).toBe("scoring_empty");
  });

  it.each(["fixture", "seed", undefined])("does not accept scoring source %s as a live record", (source) => {
    const value = input(); value.scoring.snapshot!.source = source;
    expect(projectCandidateWorkflow(value).reason).toBe("scoring_unverified_source");
  });

  it.each(["network", "scoring"] as const)("rejects duplicate selected identity in %s", (key) => {
    const value = input(); const snapshot = value[key].snapshot!;
    snapshot.candidates = [...snapshot.candidates!, snapshot.candidates![1]];
    expect(projectCandidateWorkflow(value).reason).toBe("candidate_identity_conflict");
  });

  it("does not substitute a listing when the candidate reads disagree", () => {
    const value = input(); value.scoring.snapshot!.candidates = [{ id: "second", listingId: "listing-first" }];
    expect(projectCandidateWorkflow(value).reason).toBe("listing_relation_unverified");
  });

  it("rejects duplicate listing identity", () => {
    const value = input(); value.network.snapshot!.listings = [{ id: "listing-second" }, { id: "listing-second" }];
    expect(projectCandidateWorkflow(value).reason).toBe("listing_identity_conflict");
  });

  it("does not mark evaluated as a report when the saved report is missing", () => {
    const value = input(); value.scoring.snapshot!.scorecards = [];
    const result = projectCandidateWorkflow(value);
    expect(result.reason).toBe("report_missing"); expect(step(result, "sitescore").state).toBe("blocked");
  });

  it.each(["reportId", "reportVersion", "sitescoreRunId", "modelVersion"] as const)("keeps an incomplete %s unknown", (key) => {
    const value = input(); delete value.scoring.snapshot!.scorecards[1][key];
    expect(projectCandidateWorkflow(value).reason).toBe("report_identity_incomplete");
  });

  it("does not choose the first duplicate report as the current report", () => {
    const value = input(); const snapshot = value.scoring.snapshot!;
    snapshot.scorecards = [...snapshot.scorecards, { ...snapshot.scorecards[1], reportVersion: 4 }];
    expect(projectCandidateWorkflow(value).reason).toBe("report_identity_conflict");
  });

  it("refuses a report ID reused by another candidate instead of claiming exact committed lineage", () => {
    const value = input(); value.scoring.snapshot!.scorecards[0].reportId = "report-second";
    expect(projectCandidateWorkflow(value).reason).toBe("report_identity_conflict");
  });

  it("retains historical report availability for a failed candidate without claiming current job success or retry permission", () => {
    const value = input(); value.network.snapshot!.candidates![1].status = "SCORING_FAILED";
    const result = projectCandidateWorkflow(value);
    expect(result.reason).toBe("failed_candidate_with_historical_report");
    expect(result.report?.reportId).toBe("report-second");
    expect(result.notice).toContain("評分失敗"); expect(result.notice).toContain("先前已保存報告");
    expect(result.currentJob).toBeNull(); expect(result.executionAdmission).toBe("unknown");
  });

  it("keeps a different snapshot explicit instead of fabricating a fresh score", () => {
    const value = input(); value.network.snapshot!.candidates![1].datasetSnapshotId = "new-snapshot";
    expect(projectCandidateWorkflow(value).notice).toContain("資料快照不同");
  });

  it("reports only actual unique compare membership without filtering the comparison basket", () => {
    const value = input(); value.scoring.snapshot!.compareSet = ["first", "second"];
    expect(step(projectCandidateWorkflow(value), "compare").stateLabel).toBe("已加入比較");
    value.scoring.snapshot!.compareSet = ["first"];
    expect(step(projectCandidateWorkflow(value), "compare").stateLabel).toBe("尚未加入比較");
  });

  it("matches human progress to the selected candidate AND exact report identity/version", () => {
    const value = input(); value.reviews.snapshot!.reviews = [
      { id: "first-review", candidateId: "first", reportId: "report-first", reportVersion: 2, canonicalDecisionStatus: "APPROVED" },
      { id: "old-review", candidateId: "second", reportId: "report-second", reportVersion: 2, canonicalDecisionStatus: "APPROVED" },
      { id: "current-review", candidateId: "second", reportId: "report-second", reportVersion: 3, canonicalDecisionStatus: "PENDING_REVIEW" },
    ];
    const result = projectCandidateWorkflow(value);
    expect(result.reviewId).toBe("current-review"); expect(step(result, "review").state).toBe("current");
  });

  it.each(["DRAFT", "SYSTEM_RECOMMENDED", "PENDING_REVIEW", "APPROVED", "REJECTED"])("preserves documented canonical decision %s", (status) => {
    const value = input(); value.reviews.snapshot!.reviews = [{ id: "current", candidateId: "second", reportId: "report-second", reportVersion: 3, canonicalDecisionStatus: status }];
    const result = projectCandidateWorkflow(value);
    expect(result.reviewId).toBe("current");
    expect(step(result, "review").summary).toContain(status);
    expect(result.reviewReason).toBe("exact_report_decision_available");
  });

  it.each(["onhold", "approved", "WAIT", "UNKNOWN"])("does not invent a canonical transition from %s", (status) => {
    const value = input(); value.reviews.snapshot!.reviews = [{ id: "current", candidateId: "second", reportId: "report-second", reportVersion: 3, canonicalDecisionStatus: status }];
    expect(projectCandidateWorkflow(value).reviewReason).toBe("review_status_unverified");
  });

  it.each(["stale", "error", "older-report", "duplicate"])("keeps %s review read from advancing the workflow", (mode) => {
    const value = input(); const review = { id: "current", candidateId: "second", reportId: "report-second", reportVersion: 3, canonicalDecisionStatus: "APPROVED" };
    value.reviews.snapshot!.reviews = [review];
    if (mode === "stale") value.reviews.generation = 3;
    if (mode === "error") value.reviews.status = "error";
    if (mode === "older-report") review.reportVersion = 2;
    if (mode === "duplicate") value.reviews.snapshot!.reviews = [review, { ...review, id: "duplicate" }];
    const result = projectCandidateWorkflow(value);
    expect(result.reviewId).toBeNull(); expect(step(result, "review").state).toBe("blocked");
  });
});
