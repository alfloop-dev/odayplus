import { describe, expect, it } from "vitest";
import { projectCandidateWorkflow } from "../networkWorkflowProjection";
function input(): any {
  const read = (snapshot: unknown) => ({ snapshot, status: "ready", generation: 7 });
  return { selection: { id: "candidate", invalid: false }, currentGeneration: 7,
    network: read({ source: "api", heatZones: [{ id: "zone" }], listings: [{ id: "listing" }],
      candidates: [{ id: "candidate", listingId: "listing", heatZoneId: "zone" }] }),
    scoring: read({ source: "canonical", candidates: [{ id: "candidate", listingId: "listing" }], compareSet: [],
      scorecards: [{ id: "candidate", reportId: "report", reportVersion: 1, sitescoreRunId: "run", modelVersion: "model" }] }),
    reviews: read({ source: "canonical", reviews: [{ id: "review", candidateId: "candidate", reportId: "report", reportVersion: 1, canonicalDecisionStatus: "PENDING_REVIEW" }] }) };
}
describe("workflow malformed identity reads", () => {
  it("retains the existing minimal exact identity contract and all six steps", () => {
    const result = projectCandidateWorkflow(input());
    expect(result.report?.reportId).toBe("report"); expect(result.reviewId).toBe("review");
    expect(result.steps).toHaveLength(6);
  });
  it.each([
    ["network candidates null", (x: any) => { x.network.snapshot.candidates.push(null); }],
    ["network listing null", (x: any) => { x.network.snapshot.listings.push(null); }],
    ["network zone null", (x: any) => { x.network.snapshot.heatZones.push(null); }],
    ["network candidates missing ID", (x: any) => { x.network.snapshot.candidates.push({}); }],
    ["network candidate ID numeric", (x: any) => { x.network.snapshot.candidates[0].id = 0; }],
    ["network candidate relation object", (x: any) => { x.network.snapshot.candidates[0].listingId = {}; }],
    ["scoring candidate null", (x: any) => { x.scoring.snapshot.candidates.push(null); }],
    ["scoring report null", (x: any) => { x.scoring.snapshot.scorecards.push(null); }],
    ["scoring report missing candidate ID", (x: any) => { x.scoring.snapshot.scorecards.push({ reportId: "report-2" }); }],
    ["compare membership object", (x: any) => { x.scoring.snapshot.compareSet.push({}); }],
  ])("returns an unverified whole read instead of throwing for %s", (_name, damage) => {
    const value = input(); damage(value);
    expect(() => projectCandidateWorkflow(value)).not.toThrow();
    const result = projectCandidateWorkflow(value);
    expect(result.reason).toBe("read_shape_unverified"); expect(result.report).toBeNull();
    expect(result.steps).toHaveLength(6); expect(result.steps.every((row) => row.navigationAvailable === true)).toBe(true);
  });
  it.each([
    ["null review", (x: any) => { x.reviews.snapshot.reviews.push(null); }],
    ["missing review ID", (x: any) => { x.reviews.snapshot.reviews[0].id = undefined; }],
    ["missing candidate ID", (x: any) => { x.reviews.snapshot.reviews[0].candidateId = undefined; }],
    ["object report ID", (x: any) => { x.reviews.snapshot.reviews[0].reportId = {}; }],
  ])("retains committed report/history availability but refuses malformed %s", (_name, damage) => {
    const value = input(); damage(value);
    expect(() => projectCandidateWorkflow(value)).not.toThrow();
    const result = projectCandidateWorkflow(value);
    expect(result.reviewReason).toBe("reviews_shape_unverified"); expect(result.reviewId).toBeNull();
    expect(result.report?.reportId).toBe("report"); expect(result.steps).toHaveLength(6);
  });
});
