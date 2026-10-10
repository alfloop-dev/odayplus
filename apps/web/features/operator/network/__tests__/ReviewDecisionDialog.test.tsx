import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewDecisionDialog } from "../ReviewDecisionDialog";
import type { ReviewItem } from "../networkReviewTypes";

afterEach(cleanup);
const review: ReviewItem = {
  id: "RV-701", candidateId: "CS-1002", candidateTitle: "板橋府中候選點", zoneLabel: "板橋",
  recommendation: "WAIT", score: 76, risk: "中", status: "pending", statusLabel: "待審核",
  requestedBy: "提案者", reviewerRole: "site_reviewer", submittedAt: "2026-07-13", dueAt: "2026-07-17",
  payback: "27 個月", m12P50: "NT$372K", rentReasonableness: "偏高", cannibalization: "中",
  sourceListingId: "L-2025", fieldVisit: "已完成", brokerContact: "已聯絡", notes: "施工",
  modelVersion: "SiteScore v2.3", datasetSnapshotId: "FS-1", compareText: "備選",
  eventChips: [], history: [], decision: null,
};
describe("Review Decision keyboard and write gates", () => {
  it("labels required fields and exposes override acknowledgement state", () => {
    const onSubmit = vi.fn();
    render(<ReviewDecisionDialog action="GO" review={review} onClose={vi.fn()} onSubmit={onSubmit} />);
    const reason = screen.getByRole("textbox", { name: /決策原因/ });
    expect(reason).toHaveAttribute("aria-required", "true");
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(screen.getByRole("alert")).toHaveTextContent("原因");
    fireEvent.change(reason, { target: { value: "已核對晚間人流並評估施工期間的風險。" } });
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(screen.getByRole("alert")).toHaveTextContent("風險確認");
    expect(onSubmit).not.toHaveBeenCalled();
    const ack = screen.getByTestId("review-decision-ack");
    expect(ack).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(ack);
    expect(ack).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ overrideAck: true }));
  });
  it("keeps drafts and disallows dismissal or duplicate submits during a write", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const props = { action: "WAIT" as const, review, onClose, onSubmit };
    const view = render(<ReviewDecisionDialog {...props} />);
    const reason = screen.getByRole("textbox", { name: /決策原因/ });
    const conditions = screen.getByRole("textbox", { name: /通過條件/ });
    fireEvent.change(reason, { target: { value: "已評估站前施工風險，附條件保留此候選點。" } });
    fireEvent.change(conditions, { target: { value: "補齊現勘與晚間人流" } });
    view.rerender(<ReviewDecisionDialog {...props} submitting />);
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-busy", "true");
    for (const control of screen.getAllByRole("button")) expect(control).toBeDisabled();
    expect(reason).toBeDisabled();
    expect(conditions).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(screen.getByTestId("review-decision-dialog"));
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    view.rerender(<ReviewDecisionDialog {...props} error="寫入未確認，請重試" />);
    expect(reason).toHaveValue("已評估站前施工風險，附條件保留此候選點。");
    expect(conditions).toHaveValue("補齊現勘與晚間人流");
    expect(screen.getByRole("alert")).toHaveTextContent("寫入未確認");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
  it("labels the Return data list without weakening its required gate", () => {
    const onSubmit = vi.fn();
    render(<ReviewDecisionDialog action="RETURN" review={review} onClose={vi.fn()} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByRole("textbox", { name: /決策原因/ }), { target: { value: "需補齊現勘與晚間人流樣本後再決策。" } });
    fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(screen.getByRole("alert")).toHaveTextContent("需補資料");
    expect(screen.getByRole("textbox", { name: /需補資料/ })).toHaveAttribute("aria-required", "true");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
