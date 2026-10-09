import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoreOpsWorkflowDialogs } from "../StoreOpsWorkflowDialogs";
import type { StoreOpsWorkflowIssue } from "../storeOpsWorkflowTypes";

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
  beforeEach(() => { vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })); });
  it("keeps triage payload enums and advanced inputs editable", () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    const onTriage = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="triage" issue={liveIssue} callbacks={{ onTriage }} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("根因分類"), { target: { value: "payment" } });
    fireEvent.change(screen.getByLabelText("信心度"), { target: { value: "strong" } });
    fireEvent.click(screen.getByText("進階研判與補證據"));
    fireEvent.change(screen.getByLabelText("嚴重度"), { target: { value: "critical" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /需補證據/ }));
    fireEvent.submit(screen.getByLabelText("根因分類").closest("form")!);
    expect(onTriage).toHaveBeenCalledWith(expect.objectContaining({ category: "payment", evidenceStrength: "strong", severity: "critical", needEvidence: true, demoFastForward: false }));
  });

  it("requires an audit note only for remote restart", () => {
    const onCreateAction = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="action" issue={liveIssue} callbacks={{ onCreateAction }} onClose={vi.fn()} />);
    expect(screen.queryByLabelText(/遠端重啟稽核備註/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("處置類型"), { target: { value: "remoteRestart" } });
    fireEvent.submit(screen.getByLabelText("處置類型").closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("遠端重啟必須填寫稽核備註");
    expect(onCreateAction).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/遠端重啟稽核備註/), { target: { value: "現場確認可安全重啟" } });
    fireEvent.submit(screen.getByLabelText("處置類型").closest("form")!);
    expect(onCreateAction).toHaveBeenCalledWith(expect.objectContaining({ actionType: "remoteRestart", remoteRestartAuditNote: "現場確認可安全重啟" }));
  });

  it("renders follow-up only for ineffective outcomes and forbids direct closure", () => {
    const onOutcome = vi.fn();
    render(<StoreOpsWorkflowDialogs activeDialog="outcome" issue={liveIssue} callbacks={{ onOutcome }} onClose={vi.fn()} />);
    expect(screen.queryByLabelText("後續工作台")).not.toBeInTheDocument();
    const outcome = screen.getByRole("combobox", { name: "成效判斷" });
    fireEvent.change(outcome, { target: { value: "ineffective" } });
    expect(screen.queryByRole("checkbox", { name: /審查後結案/ })).not.toBeInTheDocument();
    fireEvent.submit(outcome.closest("form")!);
    expect(onOutcome).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/後續行動/), { target: { value: "延長觀察" } });
    fireEvent.submit(outcome.closest("form")!);
    expect(onOutcome).toHaveBeenCalledWith(expect.objectContaining({ outcome: "ineffective", closeIssue: false, followUpAction: "延長觀察" }));
  });

  afterEach(() => {
    cleanup();
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
