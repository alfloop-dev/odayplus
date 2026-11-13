import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { responseForRequest } from "./storeOpsPersistedReviews.fixtures";
import { StoreOpsWorkflowDialogs } from "../StoreOpsWorkflowDialogs";
import { STORE_OPS_WORKFLOW_DIALOG_TYPES, type StoreOpsWorkflowIssue } from "../storeOpsWorkflowTypes";

const liveIssue: StoreOpsWorkflowIssue = {
  createdAt: "2026-07-24T08:00:00Z",
  evidenceIds: [],
  id: "ISS-LIVE-001",
  ownerName: "Live Operator",
  ownerRoleId: "opsLead",
  severity: "high",
  slaDueAt: "2026-07-24T10:00:00Z",
  source: "multiSignal",
  status: "new",
  storeId: "store-live-1",
  storeName: "Live Store",
  summary: "Persisted production issue",
  title: "Live issue",
  updatedAt: "2026-07-24T08:05:00Z",
};

describe("StoreOpsWorkflowDialogs production guards", () => {
  beforeEach(() => { vi.stubGlobal("crypto", webcrypto); vi.stubGlobal("fetch", vi.fn((url, init) => Promise.resolve(responseForRequest(liveIssue, url, init)))); });
  it("pins the captured verified principal and retains the request across a failed before or after session check", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const guard = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true).mockResolvedValueOnce(false).mockResolvedValue(true);
    const onClose = vi.fn(), onTriage = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onTriage }} onClose={onClose}
      commandScope="verified-A/tenant/persona" commandPrincipal={{ subject: "verified-A", tenantId: "verified-tenant" }}
      createAuthenticatedReadGuard={(current) => async () => current() && await guard()} />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    window.sessionStorage.setItem("oday.operator.subject", "untrusted-browser-B");
    window.sessionStorage.setItem("oday.operator.tenant", "untrusted-browser-tenant-B");
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("登入範圍尚未重新核對"));
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認送出結果"));
    expect(fetch).toHaveBeenCalledOnce(); expect(onClose).not.toHaveBeenCalled(); expect(onTriage).not.toHaveBeenCalled();
    const first = new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers);
    expect(first.get("If-Actor-Match")).toBe("verified-A");
    expect(first.get("If-Tenant-Match")).toBe("verified-tenant");
    expect(first.has("X-Subject-Id")).toBe(false);
    fireEvent.submit(form);
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledTimes(2); expect(onTriage).toHaveBeenCalledOnce();
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe(first.get("Idempotency-Key"));
  });
  it("does not POST after the mounted owner is removed while the real guard callback waits", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    let finish!: (verified: boolean) => void;
    let started = false;
    const view = render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} onClose={vi.fn()}
      commandScope="verified-A/tenant/persona" commandPrincipal={{ subject: "verified-A", tenantId: "verified-tenant" }}
      createAuthenticatedReadGuard={(current) => async () => {
        if (!current()) return false;
        started = true;
        const verified = await new Promise<boolean>((resolve) => { finish = resolve; });
        return current() && verified;
      }} />);
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    await waitFor(() => expect(started).toBe(true));
    view.unmount();
    await act(async () => { finish(true); });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("keeps an unsent draft owned by the original account and hides it from a different account", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const onClose = vi.fn(), onTriage = vi.fn();
    const props = { activeDialog: "triage" as const, issue: liveIssue, callbacks: { onTriage }, onClose };
    const view = render(<StoreOpsWorkflowDialogs {...props} commandScope="verified-A/tenant/persona" />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "payment" } });
    view.rerender(<StoreOpsWorkflowDialogs {...props} commandScope="verified-B/tenant/persona" />);
    expect(screen.queryByText(liveIssue.id)).not.toBeInTheDocument();
    expect(form).not.toBeVisible();
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("登入範圍已變更"));
    expect(fetch).not.toHaveBeenCalled(); expect(onTriage).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled();
    view.rerender(<StoreOpsWorkflowDialogs {...props} commandScope="verified-A/tenant/persona" />);
    expect(screen.getByRole("dialog").querySelector("form")).toBe(form);
    expect(screen.getByLabelText("根因分類")).toHaveValue("payment");
    fireEvent.submit(form);
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledOnce(); expect(onTriage).toHaveBeenCalledOnce();
  });
  it("reports actual storage denial without a POST, success callback, dismissal or stuck pending form", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const onClose = vi.fn(), onTriage = vi.fn(), onStorageFailure = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onTriage }} onClose={onClose} onStorageFailure={onStorageFailure} />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    const denied = vi.spyOn(window.sessionStorage, "getItem").mockImplementation(() => { throw new DOMException("Denied", "SecurityError"); });
    try {
      fireEvent.submit(form);
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("瀏覽器無法核對"));
      expect(fetch).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled(); expect(onTriage).not.toHaveBeenCalled();
      expect(onStorageFailure).toHaveBeenCalledOnce();
      expect(screen.getByRole("dialog")).toHaveAttribute("aria-busy", "false");
      expect(screen.getByLabelText("根因分類")).not.toBeDisabled();
      denied.mockRestore();
      onStorageFailure.mock.calls[0][0]();
      fireEvent.submit(form);
      await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
      expect(fetch).toHaveBeenCalledOnce(); expect(onTriage).toHaveBeenCalledOnce();
    } finally { denied.mockRestore(); }
  });

  it("retains the original unknown intent across storage blocking and refuses a different recovered account scope", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error("connection lost"))
      .mockImplementation((url, init) => Promise.resolve(responseForRequest(liveIssue, url, init)));
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn(), onTriage = vi.fn(), onStorageFailure = vi.fn();
    const props = { activeDialog: "triage" as const, issue: liveIssue, callbacks: { onTriage }, onClose, onStorageFailure };
    const view = render(<StoreOpsWorkflowDialogs {...props} commandScope="verified-account-A/tenant/persona" />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "payment" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認"));
    const originalKey = fetchMock.mock.calls[0][1].headers["Idempotency-Key"];
    const denied = vi.spyOn(window.sessionStorage, "getItem").mockImplementation(() => { throw new DOMException("Denied", "SecurityError"); });
    try {
      fireEvent.submit(form);
      await waitFor(() => expect(onStorageFailure).toHaveBeenCalledOnce());
      expect(fetchMock).toHaveBeenCalledOnce();
      view.rerender(<StoreOpsWorkflowDialogs {...props} browserStorageBlocked commandScope="verified-account-A/tenant/persona" />);
      expect(screen.getByLabelText("根因分類")).toHaveValue("payment");
      expect(screen.getByLabelText("根因分類")).toBeDisabled();
      denied.mockRestore();
      view.rerender(<StoreOpsWorkflowDialogs {...props} commandScope="verified-account-B/tenant/persona" />);
      fireEvent.submit(form);
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("登入範圍已變更"));
      expect(fetchMock).toHaveBeenCalledOnce(); expect(onClose).not.toHaveBeenCalled(); expect(onTriage).not.toHaveBeenCalled();
      view.rerender(<StoreOpsWorkflowDialogs {...props} commandScope="verified-account-A/tenant/persona" />);
      expect(screen.getByRole("dialog").querySelector("form")).toBe(form);
      expect(screen.getByLabelText("根因分類")).toHaveValue("payment");
      fireEvent.submit(form);
      await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]).toBe(originalKey);
      expect(onTriage).toHaveBeenCalledOnce();
    } finally { denied.mockRestore(); }
  });
  it("keeps escalation metadata and target enums behind the compact choice cards", async () => {
    const onEscalate = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="escalate" issue={liveIssue} callbacks={{ onEscalate }} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("radio", { name: /^展店與店網/ }));
    fireEvent.click(screen.getByText("緊急程度與通知設定"));
    fireEvent.change(screen.getByLabelText("緊急程度"), { target: { value: "critical" } });
    fireEvent.change(screen.getByLabelText("升級理由"), { target: { value: "需結構性方案" } });
    fireEvent.submit(screen.getByLabelText("升級理由").closest("form")!);
    await waitFor(() => expect(onEscalate).toHaveBeenCalledWith(expect.objectContaining({ target: "network", urgency: "critical", notifyOwner: true, reason: "需結構性方案" })));
  });

  it("preserves the reply rejection reason guard and publish flag in advanced settings", async () => {
    const onReplyReview = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="replyReview" issue={liveIssue} callbacks={{ onReplyReview }} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("回覆管道與審查設定"));
    fireEvent.change(screen.getByLabelText("審查決策"), { target: { value: "reject" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    expect(onReplyReview).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("退回或拒絕必須填寫審查備註");
    fireEvent.change(screen.getByLabelText(/審查備註/), { target: { value: "需核對現場證據" } });
    fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "已核對的操作人員回覆" } });
    fireEvent.submit(screen.getByLabelText(/回覆內容/).closest("form")!);
    await waitFor(() => expect(onReplyReview).toHaveBeenCalledWith(expect.objectContaining({ decision: "reject", channel: "google", publishAfterApproval: false, reviewerNote: "需核對現場證據" })));
  });

  it("keeps triage payload enums and advanced inputs editable", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const onTriage = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onTriage }} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "payment" } });
    fireEvent.change(screen.getByLabelText("信心度"), { target: { value: "strong" } });
    fireEvent.click(screen.getByText("進階研判與補證據"));
    fireEvent.change(screen.getByLabelText("嚴重度"), { target: { value: "critical" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /需補證據/ }));
    fireEvent.submit(screen.getByLabelText("根因分類").closest("form")!);
    await waitFor(() => expect(onTriage).toHaveBeenCalledWith(expect.objectContaining({ category: "payment", evidenceStrength: "strong", severity: "critical", needEvidence: true, demoFastForward: false })));
  });

  it("requires an audit note only for remote restart", async () => {
    const onCreateAction = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="action" issue={liveIssue} callbacks={{ onCreateAction }} onClose={vi.fn()} />);
    expect(screen.queryByLabelText(/遠端重啟稽核備註/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("處置類型"), { target: { value: "remoteRestart" } });
    fireEvent.submit(screen.getByLabelText("處置類型").closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("遠端重啟必須填寫稽核備註");
    expect(onCreateAction).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/遠端重啟稽核備註/), { target: { value: "現場確認可安全重啟" } });
    fireEvent.submit(screen.getByLabelText("處置類型").closest("form")!);
    await waitFor(() => expect(onCreateAction).toHaveBeenCalledWith(expect.objectContaining({ actionType: "remoteRestart", remoteRestartAuditNote: "現場確認可安全重啟" })));
  });

  it("renders follow-up only for ineffective outcomes and forbids direct closure", async () => {
    const onOutcome = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="outcome" issue={liveIssue} callbacks={{ onOutcome }} onClose={vi.fn()} />);
    expect(screen.queryByLabelText("後續工作台")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("判斷理由"), { target: { value: "現場複查尚未改善" } });
    const outcome = screen.getByRole("radio", { name: /^無效/ });
    fireEvent.click(outcome);
    expect(screen.queryByRole("checkbox", { name: /審查後結案/ })).not.toBeInTheDocument();
    fireEvent.submit(outcome.closest("form")!);
    expect(onOutcome).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/後續行動/), { target: { value: "延長觀察" } });
    fireEvent.submit(outcome.closest("form")!);
    await waitFor(() => expect(onOutcome).toHaveBeenCalledWith(expect.objectContaining({ outcome: "ineffective", closeIssue: false, followUpAction: "延長觀察" })));
  });

  it.each(STORE_OPS_WORKFLOW_DIALOG_TYPES)("keeps %s visible and prevents duplicate or dismissed pending writes", async (type) => {
    let finish!: (response: unknown) => void;
    const fetchMock = vi.fn().mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog={type} issue={liveIssue} callbacks={{ onSubmit }} onClose={onClose} />);
    if (type === "cameraPurpose") {
      fireEvent.change(screen.getByLabelText(/調閱目的/), { target: { value: "核對現場證據" } });
      fireEvent.click(screen.getByRole("checkbox", { name: /確認隱私/ }));
    }
    if (type === "replyReview") fireEvent.change(screen.getByLabelText(/回覆內容/), { target: { value: "操作人員填寫的待審回覆" } });
    if (type === "outcome") fireEvent.change(screen.getByLabelText("判斷理由"), { target: { value: "人工複查結果" } });
    if (type === "transfer") fireEvent.change(screen.getByLabelText("轉交理由"), { target: { value: "現場設備檢查" } });
    const panel = screen.getByRole("dialog");
    const form = panel.querySelector("form")!;
    fireEvent.submit(form);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(panel).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveFocus();
    expect(screen.getByRole("button", { name: "關閉對話框" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(form.querySelector("input, select, textarea")).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.submit(form);
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    finish(responseForRequest(liveIssue, fetchMock.mock.calls[0][0], fetchMock.mock.calls[0][1]));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("retains input and the intent key after lost acknowledgement, changing the key for edited intent", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockImplementationOnce((url, init) => Promise.resolve(responseForRequest(liveIssue, url, init)));
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    const onTriage = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onTriage }} onClose={onClose} />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "payment" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("尚未確認"));
    expect(screen.getByLabelText("根因分類")).toHaveValue("payment");
    expect(onClose).not.toHaveBeenCalled();
    expect(onTriage).not.toHaveBeenCalled();
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503"));
    expect(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]).toBe(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]);
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "device" } });
    fireEvent.submit(form);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]);
    expect(onTriage).toHaveBeenCalledTimes(1);
    expect(onTriage).toHaveBeenCalledWith(expect.objectContaining({ category: "device" }));
  });

  it("keeps the dialog open when a successful HTTP response names a different issue", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ issue: { ...liveIssue, id: "OTHER" } }) });
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onSubmit }} onClose={onClose} />);
    const form = screen.getByRole("dialog").querySelector("form")!;
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("未確認此事件"));
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.submit(form);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]).toBe(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]);
  });

  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("does not substitute the fallback issue when the API record is absent", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(
      <StoreOpsWorkflowDialogs
        activeDialog="triage"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByTestId("store-ops-workflow-unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Local fallback store ops issue")).not.toBeInTheDocument();
    expect(screen.queryByText("Fallback Store")).not.toBeInTheDocument();
  });

  it("removes demo fast-forward controls from a live production issue", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");

    render(
      <StoreOpsWorkflowDialogs
        activeDialog="triage"
        issue={liveIssue}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("ISS-LIVE-001")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /示範快轉/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "示範快轉" })).not.toBeInTheDocument();
  });
});
