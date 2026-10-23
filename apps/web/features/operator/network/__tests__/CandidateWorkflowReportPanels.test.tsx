import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "../ReviewPanel";
import { SiteScorePanel } from "../SiteScorePanel";
import type { ReviewItem } from "../networkReviewTypes";
import type { ScoreCard } from "../networkScoringTypes";

const review = (id: string, candidateId: string, reportVersion: number) => ({
  id, candidateId, candidateTitle: candidateId, zoneLabel: "Area", recommendation: "GO", score: 12,
  risk: "", status: "pending", statusLabel: "待審", requestedBy: "recorded", reviewerRole: "reviewer", submittedAt: "", dueAt: "",
  payback: "", m12P50: "", rentReasonableness: "", cannibalization: "", sourceListingId: "", fieldVisit: "", brokerContact: "", notes: "",
  modelVersion: "recorded", datasetSnapshotId: "recorded", compareText: "", eventChips: [], history: [], decision: null,
  reportId: `report-${candidateId}`, reportVersion,
} satisfies ReviewItem & { reportId: string; reportVersion: number });
const card = (id: string): ScoreCard => ({ id, title: id, zoneLabel: "Area", heatZoneId: "zone", score: null, recommendation: "GO",
  modelVersion: "recorded", datasetSnapshotId: "recorded", generatedAt: "", confidence: null, payback: null,
  revenuePath: { m1: null, m3: null, m6: null, m12: null }, band: { p10: "—", p50: "—", p90: "—" },
  subScores: {}, capex: null, rentAssumption: null, drivers: [], reasons: [], risks: [], conditions: [], conditionTitle: "" });
afterEach(cleanup);

describe("read panels preserve exact workflow and historical access", () => {
  it("starts on the second candidate's exact report decision while keeping the other queue records readable", () => {
    const onSelect = vi.fn();
    render(<ReviewPanel reviews={[review("first-review", "first", 1), review("old-review", "second", 1), review("current-review", "second", 2)]}
      fallbackRows={[]} workflowCandidateId="second" expectedReport={{ reportId: "report-second", reportVersion: 2 }} onSelectCandidate={onSelect} />);
    expect(screen.getByTestId("review-card-current-review")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("review-card-first-review")).not.toHaveAttribute("data-active", "true");
    fireEvent.click(screen.getByTestId("review-card-old-review"));
    expect(screen.getByTestId("review-card-old-review")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("review-metrics-old-review")).toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-go-old-review")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-btn-go-current-review")).not.toBeInTheDocument();
    expect(onSelect).toHaveBeenCalledWith("second");
    expect(screen.getByRole("status")).toHaveTextContent("歷史項目");
  });

  it.each(["missing", "duplicate"])("does not choose a different review when current report match is %s", (mode) => {
    const rows = [review("first-review", "first", 1), review("old-review", "second", 1)];
    if (mode === "duplicate") rows.push(review("match1", "second", 2), review("match2", "second", 2));
    render(<ReviewPanel reviews={rows} fallbackRows={[]} workflowCandidateId="second" expectedReport={{ reportId: "report-second", reportVersion: 2 }} />);
    expect(screen.getByRole("status")).toHaveTextContent("尚未唯一核對");
    expect(screen.queryByTestId("review-metrics-first-review")).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^review-btn-go-/)).not.toBeInTheDocument();
    expect(screen.getAllByTestId(/^review-card-/)).toHaveLength(rows.length);
  });

  it("does not keep an old decision dialog after workflow candidate or report changes", () => {
    const rows = [review("first-review", "first", 1), review("second-review", "second", 2)];
    const view = render(<ReviewPanel reviews={rows} fallbackRows={[]} workflowCandidateId="second" expectedReport={{ reportId: "report-second", reportVersion: 2 }} />);
    fireEvent.click(screen.getByTestId("review-btn-go-second-review"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    view.rerender(<ReviewPanel reviews={rows} fallbackRows={[]} workflowCandidateId="first" expectedReport={{ reportId: "report-first", reportVersion: 1 }} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("review-card-first-review")).toHaveAttribute("data-active", "true");
  });

  it("controls SiteScore's exact second selection and keeps the first saved report in the picker", () => {
    const onSelect = vi.fn();
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[card("first"), card("second")]} selectedCandidateId="second" onSelectCandidate={onSelect} />);
    expect(screen.getByTestId("sitescore-card-first")).not.toBeVisible();
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
    fireEvent.click(screen.getByTestId("sitescore-pick-first"));
    expect(onSelect).toHaveBeenCalledWith("first");
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
  });

  it("does not choose the first saved report when an exact URL report is absent", () => {
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[card("first")]} selectedCandidateId="missing" />);
    expect(screen.getByTestId("sitescore-card-first")).not.toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("未選取其他報告");
  });

  it("keeps a historical report readable while unknown admission disables its rescore action", () => {
    const onRescore = vi.fn();
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[card("second")]} selectedCandidateId="second"
      onRescore={onRescore} rescoreUnavailableReason="目前工作與再評分資格尚未確認" />);
    expect(screen.getByTestId("sitescore-card-second")).toBeVisible();
    expect(screen.getByTestId("sitescore-rescore-second")).toBeDisabled();
    fireEvent.click(screen.getByTestId("sitescore-rescore-second")); expect(onRescore).not.toHaveBeenCalled();
  });

  it("keeps the existing fixture rescore interaction when no unavailable qualifier is supplied", () => {
    const onRescore = vi.fn();
    render(<SiteScorePanel candidates={[]} fallbackRows={[]} scorecards={[card("second")]} onRescore={onRescore} />);
    fireEvent.click(screen.getByTestId("sitescore-rescore-second")); expect(onRescore).toHaveBeenCalledWith("second");
  });
});
