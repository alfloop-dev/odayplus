import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewDecisionDialog } from "../network/ReviewDecisionDialog";
import type { ReviewItem } from "../network/networkReviewTypes";

const review: ReviewItem = {
  id:"synthetic-review", candidateId:"synthetic-candidate", candidateTitle:"Synthetic candidate",
  zoneLabel:"Synthetic zone", recommendation:"GO", score:75, risk:"Synthetic risk",
  status:"pending", statusLabel:"Pending", requestedBy:"synthetic-requester", reviewerRole:"site_reviewer",
  submittedAt:"2026-10-12T00:00:00Z", dueAt:"2026-10-13T00:00:00Z", payback:"Unavailable",
  m12P50:"Unavailable", rentReasonableness:"Unavailable", cannibalization:"Unavailable",
  sourceListingId:"synthetic-listing", fieldVisit:"Unavailable", brokerContact:"Unavailable",
  notes:"Synthetic test only", modelVersion:"synthetic-model", datasetSnapshotId:"synthetic-snapshot",
  compareText:"Synthetic comparison", eventChips:[], history:[], decision:null,
};

describe("R7 Review decision dialog", () => {
  afterEach(cleanup);

  it("focuses the labelled reason and closes with Escape without submitting", () => {
    const onClose=vi.fn(), onSubmit=vi.fn();
    render(<ReviewDecisionDialog action="GO" review={review} onClose={onClose} onSubmit={onSubmit} />);
    expect(screen.getByLabelText("決策原因（必填 — 寫入 Decision Log）")).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key:"Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("retains WAIT reason, conditions and override acknowledgement during an uncertain write", () => {
    const onClose=vi.fn(), onSubmit=vi.fn();
    const props={ action:"WAIT" as const, review, onClose, onSubmit };
    const view=render(<ReviewDecisionDialog {...props} />);
    const submit=screen.getByTestId("review-decision-submit");
    fireEvent.click(submit);
    expect(screen.getByRole("alert")).toHaveTextContent("決策原因必填");
    const reason=screen.getByLabelText("決策原因（必填 — 寫入 Decision Log）");
    fireEvent.change(reason, { target:{ value:"  Synthetic reviewer requires a conditional approval.  " } });
    fireEvent.click(submit);
    expect(screen.getByRole("alert")).toHaveTextContent("通過條件");
    const conditions=screen.getByLabelText("通過條件（必填 — 條件達成後可重評為 GO）");
    fireEvent.change(conditions, { target:{ value:"  Synthetic lease condition  " } });
    fireEvent.click(submit);
    expect(screen.getByRole("alert")).toHaveTextContent("風險確認");
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("review-decision-ack"));
    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ reason:"Synthetic reviewer requires a conditional approval.", conditions:"Synthetic lease condition", requiredData:"", overrideAck:true });
    view.rerender(<ReviewDecisionDialog {...props} submitting />);
    expect(reason).toHaveAttribute("readonly");
    expect(conditions).toHaveAttribute("readonly");
    expect(reason).toHaveFocus();
    expect(screen.getByTestId("review-decision-ack")).toBeDisabled();
    expect(screen.getByTestId("review-decision-ack")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("review-decision-close")).toBeDisabled();
    expect(screen.getByRole("button", {name:"取消"})).toBeDisabled();
    fireEvent.keyDown(reason, { key:"Escape" });
    fireEvent.click(submit);
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    view.rerender(<ReviewDecisionDialog {...props} error="Synthetic unconfirmed result" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Synthetic unconfirmed result");
    expect(reason).toHaveValue("  Synthetic reviewer requires a conditional approval.  ");
    expect(conditions).toHaveValue("  Synthetic lease condition  ");
    expect(screen.getByTestId("review-decision-ack")).toHaveAttribute("aria-pressed", "true");
  });
});
