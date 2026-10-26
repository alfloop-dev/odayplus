import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoreOpsWorkflowDialogs } from "../StoreOpsWorkflowDialogs";
import { outcomeRecord, replyRecord, responseForRequest, reviewIssue } from "./storeOpsPersistedReviews.fixtures";
describe("native saved review form readback", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", webcrypto); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    vi.stubGlobal("fetch", vi.fn((url, init) => Promise.resolve(responseForRequest(reviewIssue, url, init))));
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
  it("has no fabricated draft and refuses an empty manual reply", () => {
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={reviewIssue} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("");
    expect(screen.getByText(/尚無已保存回覆/)).toBeInTheDocument();
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("請填寫回覆內容");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("reopens from latest native saved draft and clearly retains not-sent/source limits", () => {
    const record = replyRecord(); record.submission.publishAfterApproval = true;
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, replyReviews: [record] }} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue(record.submission.draftReply);
    expect(screen.getByLabelText("已保存回覆審查")).toHaveTextContent("人工填報名稱（未驗證身分）");
    expect(screen.getByLabelText("已保存回覆審查")).toHaveTextContent("尚未發送");
    expect(screen.getByText(/僅保存核准後發布意圖/)).toBeInTheDocument();
  });
  it("does not display or submit a foreign-owner saved draft", () => {
    const record = { ...replyRecord(), storeId: "FOREIGN", submission: { ...replyRecord().submission, draftReply: "foreign secret text" } };
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, replyReviews: [record] }} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("");
    expect(screen.queryByText("foreign secret text")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "fresh manual text" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("來源無法核對");
  });
  it("keeps unsaved edits on same-owner refresh and resets when the issue store changes", () => {
    const onClose = vi.fn(); const record = replyRecord();
    const { rerender } = render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, replyReviews: [record] }} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "unsaved local edit" } });
    rerender(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, updatedAt: "2026-10-01T02:00:00Z", replyReviews: [record] }} onClose={onClose} />);
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("unsaved local edit");
    rerender(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, storeId: "NEW-STORE" }} onClose={onClose} />);
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("");
  });
  it("requires coherent approval intent after switching a saved approve to return", () => {
    const record = replyRecord(); record.submission.publishAfterApproval = true;
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, replyReviews: [record] }} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("審查決策"), { target: { value: "return" } });
    fireEvent.change(screen.getByLabelText(/審查備註/), { target: { value: "請重新確認" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("只有核准回覆可記錄核准後發布意圖");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("restores a manual outcome review without inventing measurement columns", () => {
    const record = outcomeRecord();
    render(<StoreOpsWorkflowDialogs activeDialog="outcome" issue={{ ...reviewIssue, outcomeReviews: [record] }} onClose={vi.fn()} />);
    expect(screen.getByLabelText("判斷理由")).toHaveValue(record.submission.impactSummary);
    expect(screen.getByLabelText("已保存成效判斷")).toHaveTextContent("不代表已取得量測數據或因果成效證據");
    expect(screen.getByText("來源未提供處置前後指標，請核對證據後記錄判斷。")).toBeInTheDocument();
  });
  it("requires manual outcome reasoning before POST", () => {
    render(<StoreOpsWorkflowDialogs activeDialog="outcome" issue={reviewIssue} onClose={vi.fn()} />);
    fireEvent.submit(screen.getByLabelText("判斷理由").closest("form")!);
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("請填寫判斷理由");
  });
  it("confirms a native saved reply before callback/dismissal", async () => {
    const onClose = vi.fn(), onReplyReview = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={reviewIssue} callbacks={{ onReplyReview }} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "  真正由操作人員填寫  " } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onReplyReview).toHaveBeenCalledWith(expect.objectContaining({ draftReply: "真正由操作人員填寫" }));
  });
  it("ignores a late accepted old-owner operation after store rotation", async () => {
    let finish!: (value: unknown) => void;
    const fetchMock = vi.fn((_url: unknown, _init?: RequestInit) => new Promise(resolve => { finish = resolve; })); vi.stubGlobal("fetch", fetchMock);
    let digestCompleted = false;
    const originalDigest = webcrypto.subtle.digest.bind(webcrypto.subtle);
    vi.spyOn(webcrypto.subtle, "digest").mockImplementation(async (...args) => { const value = await originalDigest(...args); digestCompleted = true; return value; });
    const onClose = vi.fn(), onReplyReview = vi.fn();
    const { rerender } = render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={reviewIssue} callbacks={{ onReplyReview }} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "old owner input" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    rerender(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={{ ...reviewIssue, storeId: "ROTATED-STORE" }} callbacks={{ onReplyReview }} onClose={onClose} />);
    finish(responseForRequest(reviewIssue, fetchMock.mock.calls[0][0], fetchMock.mock.calls[0][1]));
    await waitFor(() => expect(digestCompleted).toBe(true));
    expect(onClose).not.toHaveBeenCalled(); expect(onReplyReview).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("");
  });
  it("retains draft and does not acknowledge a bare issue without the saved audit/record", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ issue: reviewIssue }) }));
    const onClose = vi.fn(), onReplyReview = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={reviewIssue} callbacks={{ onReplyReview }} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "preserved input" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("未確認已保存的審查紀錄"));
    expect(screen.getByLabelText(/回覆內容/)).toHaveValue("preserved input");
    expect(onClose).not.toHaveBeenCalled(); expect(onReplyReview).not.toHaveBeenCalled();
  });
});
