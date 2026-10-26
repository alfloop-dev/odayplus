import "@testing-library/jest-dom/vitest";
import { webcrypto } from "node:crypto";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoreOpsWorkflowDialogs } from "../StoreOpsWorkflowDialogs";
import type { StoreOpsWorkflowDialogType } from "../storeOpsWorkflowTypes";
import { nativeWorkflowRecordCases, responseForRequest, reviewIssue, workflowReceiptForRecord } from "./storeOpsPersistedReviews.fixtures";
const dialogs: Record<string, StoreOpsWorkflowDialogType> = { triage: "triage", assign: "assign", actions: "action", "field-report": "fieldReport", escalate: "escalate", transfer: "transfer", "camera-purpose": "cameraPurpose" };
const fields: Record<string, Array<[RegExp, string]>> = {
  triage: [[/研判備註/, "保存人工研判"], [/觀察窗/, "現場複查完成後重新判斷"]],
  assign: [[/^負責人$/, "填報工務人員"], [/處理期限/, "2026-10-20 09:00:00.000900+08:00"], [/交辦備註/, "確認設備後回報"]],
  actions: [[/處置標題/, "清潔複查"], [/執行說明/, "確認入口狀況"], [/^檢查清單/, "地面\n入口"]],
  "field-report": [[/回報人/, "現場填報名稱"], [/觀察時間/, "2026-10-15T03:00:00.000800+08:00"], [/回報摘要/, "完成目視檢查"], [/附件/, "proof-declared.jpg"]],
  escalate: [[/升級理由/, "跨區域人工檢查"], [/期望結果/, "另行受理後續工作"]],
  transfer: [[/接手負責人/, "客服填報標籤"], [/轉交理由/, "需人工核對"], [/交辦備註/, "保留原始回報"]],
  "camera-purpose": [[/調閱目的/, "核對現場設備"], [/稽核備註/, "保存實際調閱目的備註"], [/攝影機位置/, "操作者提供的位置標籤"], [/調閱時段/, "操作者指定時段"]],
};
describe("seven native saved manual workflow forms", () => {
  beforeEach(() => { vi.stubGlobal("crypto", webcrypto); vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); vi.stubGlobal("fetch", vi.fn((url, init) => Promise.resolve(responseForRequest(reviewIssue, url, init)))); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
  it.each(nativeWorkflowRecordCases)("reopens $action from actual model fields with truthful status", row => {
    render(<StoreOpsWorkflowDialogs activeDialog={dialogs[row.action]} issue={{ ...reviewIssue, workflowRecords: [row.record] }} onClose={vi.fn()} />);
    for (const [label, value] of fields[row.action]) expect(screen.getByLabelText(label)).toHaveValue(value);
    expect(screen.getByLabelText("已保存工作流程紀錄")).toHaveTextContent("人工填報名稱（未驗證身分）");
    const truth: Record<string, string> = { triage: "未快轉實際觀察計時", assign: "尚未驗證受派人的實際身分", actions: "未執行設備命令", "field-report": "附件實體尚未核驗", escalate: "尚未送出通知", transfer: "未建立訂閱", "camera-purpose": "不代表影片已下載" };
    expect(screen.getByLabelText("已保存工作流程紀錄")).toHaveTextContent(truth[row.action]);
    if (row.action === "actions") expect(screen.getByRole("checkbox", { name: /^需補證據/ })).not.toBeChecked();
    if (row.action === "escalate") expect(screen.getByRole("checkbox", { name: /^通知目前負責人/ })).not.toBeChecked();
    if (row.action === "transfer") expect(screen.getByRole("checkbox", { name: /^持續追蹤/ })).not.toBeChecked();
    if (row.action === "camera-purpose") expect(screen.getByRole("checkbox", { name: /確認隱私/ })).not.toBeChecked();
  });
  it.each(nativeWorkflowRecordCases)("acknowledges $action only after matching persisted audit and hash", async row => {
    const onClose = vi.fn(), onSubmit = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog={dialogs[row.action]} issue={{ ...reviewIssue, workflowRecords: [row.record] }} callbacks={{ onSubmit }} onClose={onClose} />);
    if (row.action === "camera-purpose") fireEvent.click(screen.getByRole("checkbox", { name: /確認隱私/ }));
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1)); expect(onSubmit).toHaveBeenCalledTimes(1);
  });
  it.each(["assign", "fieldReport"] as const)("does not guess a timezone for %s input", type => {
    render(<StoreOpsWorkflowDialogs activeDialog={type} issue={reviewIssue} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(type === "assign" ? "處理期限" : "觀察時間"), { target: { value: "2026-10-20 09:00" } });
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    expect(fetch).not.toHaveBeenCalled(); expect(screen.getByRole("alert")).toHaveTextContent("不會自動推定時區");
  });
  it("keeps missing attachment entities and camera location/time input empty", () => {
    const { rerender } = render(<StoreOpsWorkflowDialogs activeDialog="fieldReport" issue={reviewIssue} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/附件/)).toHaveValue("");
    rerender(<StoreOpsWorkflowDialogs activeDialog="cameraPurpose" issue={reviewIssue} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/攝影機位置/)).toHaveValue(""); expect(screen.getByLabelText(/調閱時段/)).toHaveValue("");
  });
  it("does not reuse saved privacy acknowledgement to authorize a fresh camera purpose", () => {
    const r = nativeWorkflowRecordCases.find(row => row.action === "camera-purpose")!;
    render(<StoreOpsWorkflowDialogs activeDialog="cameraPurpose" issue={{ ...reviewIssue, workflowRecords: [r.record] }} onClose={vi.fn()} />);
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    expect(fetch).not.toHaveBeenCalled(); expect(screen.getByRole("alert")).toHaveTextContent("必須確認隱私與稽核告知");
  });
  it("keeps native nullable submission absence visible instead of claiming saved defaults", () => {
    const r = nativeWorkflowRecordCases.find(row => row.action === "actions")!;
    render(<StoreOpsWorkflowDialogs activeDialog="action" issue={{ ...reviewIssue, workflowRecords: [r.nullableRecord] }} onClose={vi.fn()} />);
    expect(screen.getByLabelText(/執行說明/)).toHaveValue(""); expect(screen.getByLabelText(/^檢查清單/)).toHaveValue("");
    expect(screen.getByLabelText("已保存工作流程紀錄")).toHaveTextContent("表單建議值不代表既有紀錄");
  });
  it("preserves unknown native manual assignee labels without treating them as authority", () => {
    const r = nativeWorkflowRecordCases.find(row => row.action === "assign")!;
    const record = { ...r.record, submission: { ...r.record.submission, ownerRoleId: "external-manual-role" } } as typeof r.record;
    render(<StoreOpsWorkflowDialogs activeDialog="assign" issue={{ ...reviewIssue, workflowRecords: [record] }} onClose={vi.fn()} />);
    expect(screen.getByLabelText("負責角色")).toHaveValue("external-manual-role");
    expect(screen.getByRole("option", { name: /原紀錄人工標籤/ })).toBeInTheDocument();
    expect(screen.getByLabelText("已保存工作流程紀錄")).toHaveTextContent("尚未驗證受派人的實際身分");
  });
  it("refuses foreign history and bare issue acknowledgement without dismissing", async () => {
    const r = nativeWorkflowRecordCases[0], onClose = vi.fn(), onSubmit = vi.fn();
    const { rerender } = render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={{ ...reviewIssue, workflowRecords: [{ ...r.record, storeId: "FOREIGN" }] }} callbacks={{ onSubmit }} onClose={onClose} />);
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!); expect(fetch).not.toHaveBeenCalled();
    rerender(<StoreOpsWorkflowDialogs activeDialog="triage" issue={{ ...reviewIssue, storeId: "NEW-STORE" }} callbacks={{ onSubmit }} onClose={onClose} />);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ issue: { ...reviewIssue, storeId: "NEW-STORE" } }) }));
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("未確認已保存的工作流程紀錄"));
    expect(onClose).not.toHaveBeenCalled(); expect(onSubmit).not.toHaveBeenCalled();
  });
});
