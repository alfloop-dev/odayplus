import React from "react";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AddListingFromUrlDialog } from "../AddListingFromUrlDialog";
import type { IntakeApiError } from "../intakeClient";

const scope = { brand_id: "brand-a", region_id: "region-a", assigned_area_id: "area-a", heat_zone_id: "native-zone-a" };
const scopes = [{ label: "已讀取的正式範圍 A", scope }];
const chooseScopeAndReason = () => {
  fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "人工核對後提交此來源頁" } });
};

describe("AddListingFromUrlDialog — sole native form", () => {
  const mockOnClose = vi.fn();
  const mockOnSubmit = vi.fn();
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("renders the existing shell with explicit scope and reason, not a legacy HeatZone form", () => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    expect(screen.getByTestId("intake-add-dialog")).toBeDefined();
    expect(screen.getByTestId("intake-url-input")).toBeDefined();
    expect(screen.getByLabelText("正式收件範圍")).toHaveValue("");
    expect(screen.getByLabelText("送件原因")).toHaveValue("");
    expect(screen.queryByTestId("intake-area-select")).not.toBeInTheDocument();
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent("展店主管");
    expect(screen.getByTestId("intake-submit-button")).toBeDefined();
  });

  it("shows local error on invalid URL submission", () => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "invalid-url-string" } });
    fireEvent.click(screen.getByTestId("intake-submit-button"));
    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent("請確認網址格式");
  });

  it("submits the original valid URL with the explicitly chosen full scope and trimmed human reason", async () => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: " https://www.591.com.tw/rent-detail-1234567.html?utm_source=original " } });
    chooseScopeAndReason();
    fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "  人工核對後提交此來源頁  " } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).toHaveBeenCalledExactlyOnceWith({ url: "https://www.591.com.tw/rent-detail-1234567.html?utm_source=original",
      reason: "人工核對後提交此來源頁", scope });
    expect(screen.getByTestId("intake-process-note")).toHaveTextContent("提交回執僅表示已收件");
    expect(screen.getByTestId("intake-canonical-preview")).not.toHaveTextContent("utm_source=original");
  });

  it.each(["scope", "reason", "short reason"])("never infers missing %s from a valid URL or account label", async (missing) => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/listing" } });
    chooseScopeAndReason();
    if (missing === "scope") fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "" } });
    else fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: missing === "reason" ? "" : " 短 " } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent("請選取正式收件範圍");
  });

  it("prevents submission and dismissal when busy", () => {
    render(<AddListingFromUrlDialog busy error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    const submit = screen.getByTestId("intake-submit-button");
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(screen.getByTestId("intake-add-dialog"));
    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(mockOnClose).not.toHaveBeenCalled();
    expect(submit).toHaveTextContent("送出中…（防止重複送出）");
  });

  it("locks rapid repeated submissions before the parent busy state updates", async () => {
    let resolveSubmit: (() => void) | undefined;
    const pendingSubmit = vi.fn(() => new Promise<void>((resolve) => { resolveSubmit = resolve; }));
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={pendingSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/listing" } });
    chooseScopeAndReason();
    const submit = screen.getByTestId("intake-submit-button");
    fireEvent.click(submit); fireEvent.click(submit);
    expect(pendingSubmit).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
    await act(async () => resolveSubmit?.());
  });

  it("leaves source policy authority with the server", () => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://www.sinyi.com.tw/buy/house/1234" } });
    expect(screen.getByTestId("intake-source-preview")).toHaveTextContent("送出後由伺服器判定");
    expect(screen.getByTestId("intake-source-preview")).toHaveTextContent("www.sinyi.com.tw");
    expect(screen.getByTestId("intake-source-preview")).not.toHaveTextContent("可自動處理擷取");
  });

  it("renders a real conflict without inventing an exact duplicate or extracting a legacy record ID", () => {
    const conflict: IntakeApiError = { status: 409, code: "ODP-INTAKE-CONFLICT", summary: "原請求內容衝突（IN-999）。",
      nextAction: "請核對原命令", correlationId: "corr-conflict-123", occurredAt: "2026-07-21T05:00:00Z", retryable: false };
    render(<AddListingFromUrlDialog busy={false} error={conflict} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店主管" canonicalScopes={scopes} />);
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent(conflict.summary);
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent(conflict.correlationId!);
    expect(screen.queryByTestId("intake-exact-duplicate-intercept")).not.toBeInTheDocument();
    expect(screen.queryByTestId("intake-open-existing")).not.toBeInTheDocument();
  });

  it("shows only supplied native scopes and sends the actual selected full scope", async () => {
    const otherScope = { ...scope, brand_id: "brand-b", heat_zone_id: "native-zone-b" };
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店經理" canonicalScopes={[...scopes, { label: "伺服器範圍 B", scope: otherScope }]} />);
    const select = screen.getByLabelText("正式收件範圍");
    expect(within(select).getAllByRole("option").map(option => option.getAttribute("value"))).toEqual(["", "0", "1"]);
    expect(screen.queryByText(/信義松仁生活圈|板橋府中商圈/)).not.toBeInTheDocument();
    chooseScopeAndReason();
    fireEvent.change(select, { target: { value: "1" } });
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/native-listing" } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).toHaveBeenCalledWith({ url: "https://example.com/native-listing", reason: "人工核對後提交此來源頁", scope: otherScope });
  });

  it("does not submit a URL if no canonical scope options were supplied", async () => {
    render(<AddListingFromUrlDialog busy={false} error={null} canonicalScopes={[]}
      onClose={mockOnClose} onSubmit={mockOnSubmit} submitterLabel="展店經理" />);
    expect(screen.getByLabelText("正式收件範圍")).toHaveValue("");
    expect(within(screen.getByLabelText("正式收件範圍")).getAllByRole("option")).toHaveLength(1);
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/native-listing" } });
    fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "使用者明確填寫的送件原因" } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent("請選取正式收件範圍");
  });
});
