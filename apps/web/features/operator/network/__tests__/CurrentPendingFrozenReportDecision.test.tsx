import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewPanel } from "../ReviewPanel";
import { projectCandidateNativeWorkflow } from "../candidateNativeWorkflowProjection";
import type { CandidateReviewReadTarget, NetworkScoringSnapshot } from "../networkScoringTypes";
import type { NetworkReviewsSnapshot, ReviewItem } from "../networkReviewTypes";
import samples from "./fixtures/canonicalReviewSubmission.json";

// Actual saved-report/RETURN/resubmit producer output: current pending pointer
// retains V1 while the separately saved latest report is V2. This DOM control
// establishes no API permission, signed user, model result, or production grant.
const value = samples.RETURN_V1_LATEST_V2;
const graph = projectCandidateNativeWorkflow({ currentGeneration: 8, tenantId: value.resubmitContext.tenantId,
  network: { snapshot: value.afterResubmit.network as any, status: "ready", generation: 8 },
  scoring: { snapshot: value.afterResubmit.scoring as unknown as NetworkScoringSnapshot, status: "ready", generation: 8 },
  reviews: { snapshot: value.afterResubmit.reviews as unknown as NetworkReviewsSnapshot, status: "ready", generation: 8 } });
const current = graph[value.candidateId];
const latest = value.afterResubmit.scoring.scorecards.find((row) => row.id === value.candidateId)!;
const reviews = value.afterResubmit.reviews.reviews as unknown as ReviewItem[];
function props(target: CandidateReviewReadTarget | null = current.review) {
  return { reviews, fallbackRows: [], canDecide: true, workflowCandidateId: value.candidateId,
    explicitReadTarget: current.review, canonicalCurrentReviewTarget: target,
    expectedReport: { reportId: latest.reportId, reportVersion: latest.reportVersion } };
}
afterEach(cleanup);

it("offers only the native current pending Decision on frozen V1 while latest V2 remains separate", async () => {
  expect(current.stage).toBe("pendingreview"); expect(current.availability).toBe("recorded");
  expect(current.review).toMatchObject({ reportVersion: 1, latestReportVersion: 2, relation: "historical" });
  const request = vi.fn().mockResolvedValue(true);
  render(<ReviewPanel {...props()} onRequestDecision={request} />);
  expect(screen.getByTestId("review-historical-target-context")).toHaveTextContent("v1");
  expect(screen.getByTestId("review-historical-target-context")).toHaveTextContent("目前待審");
  fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
  await screen.findByTestId("review-decision-dialog");
  expect(request).toHaveBeenCalledWith(current.review!.reviewId, "GO");
});

it("keeps the dialog and its reason when only a newer latest-report identity changes", async () => {
  const request = vi.fn().mockResolvedValue(true);
  const view = render(<ReviewPanel {...props()} onRequestDecision={request} />);
  fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
  await screen.findByTestId("review-decision-dialog");
  fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: "Review this frozen current pending report using its existing evidence" } });
  const target = { ...current.review!, latestReportId: "newer-separate-report", latestReportVersion: 3 };
  view.rerender(<ReviewPanel {...props(target)} explicitReadTarget={target}
    expectedReport={{ reportId: "newer-separate-report", reportVersion: 3 }} onRequestDecision={request} />);
  expect(screen.getByTestId("review-decision-reason")).toHaveValue("Review this frozen current pending report using its existing evidence");
});

it("requires server context acceptance before a frozen pending decision dialog opens", async () => {
  const request = vi.fn().mockResolvedValue(false);
  render(<ReviewPanel {...props()} onRequestDecision={request} />);
  fireEvent.click(screen.getByRole("button", { name: "核准 GO" }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
  expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
});

it.each(["no-current-pointer", "foreign-review", "foreign-report", "no-server-guard", "read-only-role", "returned-terminal"])("keeps %s historical/foreign target read-only", (failure) => {
  let target: CandidateReviewReadTarget | null = current.review;
  if (failure === "no-current-pointer") target = null;
  if (failure === "foreign-review") target = { ...current.review!, reviewId: "foreign-current-owner" };
  if (failure === "foreign-report") target = { ...current.review!, reportId: "foreign-report" };
  render(<ReviewPanel {...props(target)} canDecide={failure !== "read-only-role"}
    reviews={failure === "returned-terminal" ? value.beforeResubmit.reviews.reviews as unknown as ReviewItem[] : reviews}
    onRequestDecision={failure === "no-server-guard" ? undefined : vi.fn().mockResolvedValue(true)} />);
  expect(screen.queryByRole("button", { name: "核准 GO" })).not.toBeInTheDocument();
  expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();
});
