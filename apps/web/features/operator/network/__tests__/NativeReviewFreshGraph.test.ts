import { describe, expect, it } from "vitest";
import { projectCandidateNativeWorkflow } from "../candidateNativeWorkflowProjection";
import { reviewSubmissionOffers } from "../networkReviewSubmission";
import { reviewDecisionFreshGraphMatches } from "../networkReviewDecisionRefresh";
import type { BoundReviewDecisionBody } from "../networkReviewDecision";
import type { NetworkReviewsSnapshot } from "../networkReviewTypes";
import type { NetworkScoringSnapshot } from "../networkScoringTypes";
import captured from "./fixtures/h15CapturedNativeReviewViews.json";
import samples from "./fixtures/canonicalReviewDecision.json";
import freshListings from "./fixtures/canonicalReviewDecisionFreshListings.json";

// Captured native records remain unchanged. Fresh listings are exact native
// producer AST memory counterviews, not proof of a newly executed HTTP GET.
function input(v: { network: unknown; scoring: unknown; reviews: unknown }, network: unknown = v.network) {
  const reviews = v.reviews as NetworkReviewsSnapshot;
  return { currentGeneration: 7, tenantId: reviews.decisions![0].tenant_id,
    network: { snapshot: network as { source: string; candidates: unknown[]; listings: unknown[] }, status: "ready" as const, generation: 7 },
    scoring: { snapshot: v.scoring as NetworkScoringSnapshot, status: "ready" as const, generation: 7 },
    reviews: { snapshot: v.reviews as NetworkReviewsSnapshot, status: "ready" as const, generation: 7 } };
}
describe("captured native nullable Decision view bridge", () => {
  it.each(["PB-20", "PB-21"] as const)("restores %s prior approval/new-round offer without changing native records", (caseId) => {
    const v = captured[caseId], before = JSON.stringify(v);
    const reads = projectCandidateNativeWorkflow(input(v));
    expect(v.reviews.decisions[0].required_data).toBeNull();
    expect(v.network.candidates[0].reviewWorkflow.requiredData).toEqual([]);
    expect(reads[v.candidateId]).toMatchObject({ availability: "recorded", stage: "approved" });
    expect(reviewSubmissionOffers(v.network, v.scoring as NetworkScoringSnapshot, v.reviews as NetworkReviewsSnapshot, reads)[v.candidateId].newRound).toBe(true);
    expect(JSON.stringify(v)).toBe(before);
  });
  it.each([null, false, "", {}, ["unexpected"], [false], [""]])("refuses malformed or mismatched metadata %j", (requiredData) => {
    const v = structuredClone(captured["PB-20"]);
    (v.network.candidates[0].reviewWorkflow as unknown as Record<string, unknown>).requiredData = requiredData;
    expect(projectCandidateNativeWorkflow(input(v))[v.candidateId].availability).toBe("conflict");
  });
  it.each(["PB-22", "PB-23", "PB-24"] as const)("keeps %s stale pending view refused and admits actual producer terminal counterview", (caseId) => {
    const v = captured[caseId], before = JSON.stringify(v.reviews);
    expect(projectCandidateNativeWorkflow(input(v))[v.candidateId].availability).toBe("conflict");
    expect(projectCandidateNativeWorkflow(input(v, v.freshNetwork))[v.candidateId]).toMatchObject({ availability: "recorded", stage: caseId === "PB-22" ? "approved" : "needdata" });
    expect(JSON.stringify(v.reviews)).toBe(before);
  });
});
describe("both fresh guarded reads must form the same native terminal graph", () => {
  it.each(["GO", "WAIT", "RETURN", "REJECT", "RETURN_RESUBMITTED_GO"] as const)("admits unchanged actual %s receipt target and history with current scoring", (action) => {
    const v = samples[action], original = JSON.stringify(v.after);
    expect(reviewDecisionFreshGraphMatches(freshListings[action], v.scoring as NetworkScoringSnapshot, v.after, v.decisionId, v.request as BoundReviewDecisionBody, 7)).toBe(true);
    expect(reviewDecisionFreshGraphMatches(v.network, v.scoring as NetworkScoringSnapshot, v.after, v.decisionId, v.request as BoundReviewDecisionBody, 7)).toBe(false);
    expect(JSON.stringify(v.after)).toBe(original);
  });
  it.each(["version", "tenant", "source", "duplicate", "missing", "pointer", "array"])("refuses fresh candidate %s instead of confirming POST", (fault) => {
    const v = samples.GO, network = structuredClone(freshListings.GO);
    if (fault === "version") network.candidates[0].reviewWorkflow.decisionVersion += 1;
    if (fault === "tenant") network.listings[0].tenantId = "other-tenant-same-account";
    if (fault === "source") network.source = "fixture";
    if (fault === "duplicate") network.candidates.push(network.candidates[0]);
    if (fault === "missing") network.candidates = [];
    if (fault === "pointer") network.candidates[0].reviewId = "other-review";
    if (fault === "array") (network.candidates[0].reviewWorkflow as unknown as Record<string, unknown>).requiredData = null;
    expect(reviewDecisionFreshGraphMatches(network, v.scoring as NetworkScoringSnapshot, v.after, v.decisionId, v.request as BoundReviewDecisionBody, 7)).toBe(false);
  });
});
