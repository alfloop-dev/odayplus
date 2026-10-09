import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "../ReviewPanel";
import { ExpansionStepper } from "../ExpansionStepper";
import type { ReviewItem } from "../networkReviewTypes";

afterEach(cleanup);
const review: ReviewItem = {
  id: "RV-701", candidateId: "CS-1002", candidateTitle: "板橋府中候選點", zoneLabel: "板橋",
  recommendation: "WAIT", score: 76, risk: "站前施工至 12 月", status: "pending", statusLabel: "待審核",
  requestedBy: "提案者", reviewerRole: "site_reviewer", submittedAt: "2026-07-13", dueAt: "2026-07-17",
  payback: "27 個月", m12P50: "NT$372K", rentReasonableness: "偏高", cannibalization: "中",
  sourceListingId: "L-2025", fieldVisit: "已完成", brokerContact: "已聯絡", notes: "施工影響需管理",
  modelVersion: "SiteScore v2.3", datasetSnapshotId: "FS-1", compareText: "備選",
  eventChips: [], history: [], decision: null,
};
describe("Package 10 Review surface", () => {
  it("keeps source-backed semantic facts, missing values and selected state", () => {
    render(<ReviewPanel reviews={[{ ...review, brokerContact: "" }]} fallbackRows={[]} onDecide={vi.fn()} />);
    expect(screen.getByTestId("review-card-RV-701")).toHaveAttribute("aria-pressed", "true");
    const facts = screen.getByLabelText("審核候選點資料");
    expect(facts.tagName).toBe("DL");
    expect(within(facts).getAllByRole("term")).toHaveLength(7);
    expect(within(facts).getAllByRole("definition")).toHaveLength(7);
    expect(within(facts).getByText("仲介聯絡").nextElementSibling).toHaveTextContent("—");
    expect(facts).toHaveTextContent("L-2025");
    expect(facts).toHaveTextContent("施工影響需管理");
    expect(screen.getByTestId("review-recommendation-note-RV-701")).toHaveTextContent("系統建議為 WAIT");
    expect(screen.getByRole("button", { name: "要求現勘（審核前補件）" })).toBeDisabled();
    expect(screen.getByText("現勘補件服務尚未提供；此操作不可執行。")).toBeVisible();
  });
  it("preserves denied decision controls and makes no writes from preparation", () => {
    const onDecide = vi.fn();
    render(<ReviewPanel reviews={[review]} fallbackRows={[]} canDecide={false} onDecide={onDecide} />);
    expect(screen.getByTestId("review-role-note-RV-701")).toBeVisible();
    expect(screen.queryByRole("button", { name: "核准 GO" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "要求現勘（審核前補件）" })).toBeDisabled();
    fireEvent.click(screen.getByTestId("review-card-RV-701"));
    expect(onDecide).not.toHaveBeenCalled();
  });
  it("keeps all decisions behind the existing confirmation dialog", () => {
    const onDecide = vi.fn();
    render(<ReviewPanel reviews={[review]} fallbackRows={[]} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "退回修改" }));
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("textbox", { name: /需補資料/ })).toHaveAttribute("aria-required", "true");
    expect(onDecide).not.toHaveBeenCalled();
  });
  it("localizes the scoped review packet gate without enabling it", () => {
    const onStepSelect = vi.fn();
    render(<ExpansionStepper activeTab={5} onStepSelect={onStepSelect} steps={[
      { id: "review", label: "Review", state: "blocked", tabIndex: 5, summary: "No candidate review packet yet." },
    ]} />);
    expect(screen.getByRole("status")).toHaveTextContent("此流程尚無候選點審核資料");
    expect(screen.getByTestId("network-step-review")).toBeDisabled();
    expect(screen.getByTestId("network-step-review")).toHaveAttribute("title", "此流程尚無候選點審核資料");
    expect(onStepSelect).not.toHaveBeenCalled();
  });
});
