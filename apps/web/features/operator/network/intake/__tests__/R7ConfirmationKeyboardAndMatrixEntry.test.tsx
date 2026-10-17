import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake, PromotionDecisionReceipt } from "@oday-plus/openapi-client";
import type { ReviewItem } from "../../networkReviewTypes";
import { ReviewDecisionDialog } from "../../ReviewDecisionDialog";
import { IntakeDecisionDialog } from "../IntakeDecisionDialog";
import { IntakeDialogShell } from "../IntakeDialogShell";
import { ListingInboxIntakeView } from "../ListingInboxIntakeView";
import { PromotionReviewPanel } from "../PromotionReviewPanel";

// Synthetic mounted component controls; no authentication or business writes.
const record: AssistedIntake = {
  id: "synthetic-intake", sourceId: "synthetic-source", canonicalUrl: "https://example.com/listing",
  originalUrl: "https://example.com/listing", stage: "READY", policy: "ASSISTED_ENTRY_ONLY",
  policyLabel: "人工送件", policyReason: "Synthetic", submitter: "author-a", owner: "author-a",
  heatZoneId: null, rawSnapshot: null, snapshotId: null, capturedAt: null, parserVersion: "synthetic-parser",
  correlationId: null, parsedFields: {}, matchResult: null, auditEvents: [], version: 4,
};
const review: ReviewItem = {
  id: "synthetic-review", candidateId: "synthetic-candidate", candidateTitle: "Synthetic candidate",
  zoneLabel: "Synthetic zone", recommendation: "GO", score: 75, risk: "Synthetic risk", status: "pending",
  statusLabel: "Pending", requestedBy: "author-a", reviewerRole: "site_reviewer", submittedAt: "", dueAt: "",
  payback: "", m12P50: "", rentReasonableness: "", cannibalization: "", sourceListingId: "",
  fieldVisit: "", brokerContact: "", notes: "", modelVersion: "synthetic-model", datasetSnapshotId: "synthetic-snapshot",
  compareText: "", eventChips: [], history: [], decision: null,
};
const promotion: PromotionDecisionReceipt = {
  promotion_decision_id: "synthetic-promotion", intake_id: record.id, listing_id: "synthetic-listing",
  target_format_code: "Synthetic-Reviewed-Format", status: "PENDING_REVIEW", decision_type: "STANDARD",
  proposer_subject_id: "author-a", reviewer_subject_id: null, candidate_site_id: null,
  site_score_job_id: null, version: 7, audit_event_id: "synthetic-audit", correlation_id: "synthetic-correlation",
};
afterEach(cleanup);

describe("original R7 destructive confirmation and read-only reference entry", () => {
  it("retains intake decision inputs after Escape and backdrop, and allows explicit Cancel without a write", () => {
    const onClose = vi.fn(), onSubmit = vi.fn();
    render(<IntakeDecisionDialog busy={false} error={null} kind="create" onClose={onClose} onSubmit={onSubmit} record={record} />);
    fireEvent.change(screen.getByTestId("intake-decide-reason"), { target: { value: "Synthetic entered reason" } });
    fireEvent.click(screen.getByTestId("intake-decide-risk-ack"));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.mouseDown(screen.getByTestId("intake-decide-dialog"));
    expect(onClose).not.toHaveBeenCalled();
    expect(within(screen.getByRole("dialog")).getByRole("status")).toHaveTextContent("決策確認視窗需以「取消」按鈕關閉（防誤觸 Esc）");
    expect(screen.getByTestId("intake-decide-reason")).toHaveValue("Synthetic entered reason");
    expect(screen.getByTestId("intake-decide-risk-ack")).toBeChecked();
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps every intake dismissal and input disabled while its real outcome is pending", () => {
    const onClose = vi.fn(), onSubmit = vi.fn();
    render(<IntakeDecisionDialog busy error={null} kind="create" onClose={onClose} onSubmit={onSubmit} record={record} />);
    expect(screen.getByTestId("intake-decide-reason")).toBeDisabled();
    expect(screen.getByTestId("intake-decide-risk-ack")).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.mouseDown(screen.getByTestId("intake-decide-dialog"));
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it.each(["GO", "WAIT", "RETURN", "REJECT"] as const)("retains %s review reason after Escape and still permits explicit Cancel", (action) => {
    const onClose = vi.fn(), onSubmit = vi.fn();
    render(<ReviewDecisionDialog action={action} review={review} onClose={onClose} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("review-decision-reason"), { target: { value: "Synthetic reviewer entered reason" } });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(within(screen.getByRole("dialog")).getByRole("status")).toHaveTextContent("決策確認視窗需以「取消」按鈕關閉（防誤觸 Esc）");
    expect(screen.getByTestId("review-decision-reason")).toHaveValue("Synthetic reviewer entered reason");
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("retains promotion confirmation and review draft after Escape/backdrop, and cancels without approval", () => {
    const onReviewPromotion = vi.fn();
    render(<PromotionReviewPanel record={record} promotion={promotion} currentOperator={{ id: "reviewer-b", name: "Synthetic B", role: "expansion-manager" }} gateSnapshotSha256={"a".repeat(64)} onReviewPromotion={onReviewPromotion} />);
    fireEvent.change(screen.getByTestId("promotion-review-reason"), { target: { value: "Synthetic promotion reviewer reason" } });
    fireEvent.click(screen.getByTestId("promotion-review-ack"));
    fireEvent.click(screen.getByTestId("promotion-approve-btn"));
    const dialog = screen.getByTestId("promotion-confirmation-dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.mouseDown(dialog);
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByRole("status")).toHaveTextContent("決策確認視窗需以「取消」按鈕關閉（防誤觸 Esc）");
    expect(onReviewPromotion).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByTestId("promotion-confirmation-dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("promotion-review-reason")).toHaveValue("Synthetic promotion reviewer reason");
    expect(onReviewPromotion).not.toHaveBeenCalled();
  });

  it("preserves ordinary intake modal Escape and backdrop dismissal", () => {
    const onClose = vi.fn();
    render(<IntakeDialogShell ariaLabel="Synthetic ordinary dialog" onClose={onClose} screenLabel="Synthetic ordinary" testId="ordinary-shell"><button type="button">Ordinary action</button></IntakeDialogShell>);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.mouseDown(screen.getByTestId("ordinary-shell"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("opens the existing state matrix from the mounted inbox and returns focus with no business callback", () => {
    const onAddSubmit = vi.fn(), onOpenDetail = vi.fn();
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" records={[]} loadState="ready" busy={false} onAddSubmit={onAddSubmit} onOpenDetail={onOpenDetail} onRetryLoad={vi.fn()} onRetryIntake={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "收件狀態矩陣" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Intake Canonical State Matrix" });
    expect(dialog).toHaveTextContent("NEEDS_REVIEW");
    expect(dialog).toHaveTextContent("SOURCE_BLOCKED");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onAddSubmit).not.toHaveBeenCalled();
    expect(onOpenDetail).not.toHaveBeenCalled();
  });

  it("keeps the matrix reference accessible for an existing read-only viewer and respects the inbox access boundary", () => {
    const props = { records: [], loadState: "ready" as const, busy: false, onAddSubmit: vi.fn(), onOpenDetail: vi.fn(), onRetryLoad: vi.fn(), onRetryIntake: vi.fn() };
    const view = render(<ListingInboxIntakeView {...props} activeRoleId="pm-audit" />);
    fireEvent.click(screen.getByRole("button", { name: "收件狀態矩陣" }));
    expect(screen.getByRole("dialog", { name: "Intake Canonical State Matrix" })).toBeInTheDocument();
    expect(screen.queryByTestId("intake-add-button")).not.toBeInTheDocument();
    view.rerender(<ListingInboxIntakeView {...props} activeRoleId="ops-lead" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "收件狀態矩陣" })).not.toBeInTheDocument();
    expect(props.onAddSubmit).not.toHaveBeenCalled();
    expect(props.onOpenDetail).not.toHaveBeenCalled();
  });
});
