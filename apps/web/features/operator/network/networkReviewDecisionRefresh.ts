import { projectCandidateNativeWorkflow } from "./candidateNativeWorkflowProjection";
import { reviewDecisionReadbackMatches, type BoundReviewDecisionBody } from "./networkReviewDecision";
import { hasNetworkListingsRenderShape, hasNetworkScoringRenderShape, readObject } from "./networkReadValidation";
import type { NetworkScoringSnapshot } from "./networkScoringTypes";

/** A POST receipt never supplies the candidate view or grants a fresh read.
 * Both guarded GETs must join the current saved scoring read before publication. */
export function reviewDecisionFreshGraphMatches(network: unknown, scoring: NetworkScoringSnapshot | null,
  reviews: unknown, reviewId: string, body: BoundReviewDecisionBody, generation: number): boolean {
  if (!readObject(network) || network.source !== "api" || !hasNetworkListingsRenderShape(network)
    || !scoring || scoring.source !== "canonical" || !hasNetworkScoringRenderShape(scoring)
    || !reviewDecisionReadbackMatches(reviews, reviewId, body)) return false;
  const candidates = (network.candidates as Record<string, unknown>[]).filter((row) => row.id === body.expectedCandidateId);
  const candidate = candidates[0];
  const listings = (network.listings as Record<string, unknown>[]).filter((row) => row.id === candidate?.listingId);
  if (candidates.length !== 1 || listings.length !== 1 || listings[0].tenantId !== body.expectedTenantId) return false;
  const graph = projectCandidateNativeWorkflow({ currentGeneration: generation, tenantId: body.expectedTenantId,
    network: { snapshot: network, status: "ready", generation },
    scoring: { snapshot: scoring, status: "ready", generation },
    reviews: { snapshot: reviews, status: "ready", generation } });
  const target = graph[body.expectedCandidateId];
  const stage = { GO: "approved", WAIT: "onhold", RETURN: "needdata", REJECT: "rejected" }[body.decision];
  return target?.availability === "recorded" && target.stage === stage && target.review?.reviewId === reviewId
    && target.review.reportId === body.expectedReportId && target.review.reportVersion === body.expectedReportVersion
    && target.review.generation === generation;
}
