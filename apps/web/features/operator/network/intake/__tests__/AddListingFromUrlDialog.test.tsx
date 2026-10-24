import React from "react";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AddListingFromUrlDialog } from "../AddListingFromUrlDialog";
import type { IntakeApiError } from "../intakeClient";

describe("AddListingFromUrlDialog", () => {
  const mockOnClose = vi.fn();
  const mockOnSubmit = vi.fn();

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders dialog shell and input elements", () => {
    render(
      <AddListingFromUrlDialog
        busy={false}
        error={null}
        onClose={mockOnClose}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    expect(screen.getByTestId("intake-add-dialog")).toBeDefined();
    expect(screen.getByTestId("intake-url-input")).toBeDefined();
    expect(screen.getByTestId("intake-area-select")).toBeDefined();
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent("展店主管");
    expect(screen.getByTestId("intake-submit-button")).toBeDefined();
  });

  it("shows local error on invalid URL submission", () => {
    render(
      <AddListingFromUrlDialog
        busy={false}
        error={null}
        onClose={mockOnClose}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    const input = screen.getByTestId("intake-url-input");
    const submitBtn = screen.getByTestId("intake-submit-button");

    fireEvent.change(input, { target: { value: "invalid-url-string" } });
    fireEvent.click(submitBtn);

    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent("請確認網址格式");
  });

  it("triggers onSubmit with valid URL and selected heat zone", async () => {
    render(
      <AddListingFromUrlDialog
        busy={false}
        defaultHeatZoneId="native-zone-a"
        heatZoneOptions={{ state: "ready", options: [{ id: "native-zone-a", label: "原生已授權區域 A" }] }}
        error={null}
        onClose={mockOnClose}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    const input = screen.getByTestId("intake-url-input");
    const submitBtn = screen.getByTestId("intake-submit-button");

    fireEvent.change(input, { target: { value: "https://www.591.com.tw/rent-detail-1234567.html" } });
    await act(async () => { fireEvent.click(submitBtn); });

    expect(mockOnSubmit).toHaveBeenCalledWith({
      url: "https://www.591.com.tw/rent-detail-1234567.html",
      heatZoneId: "native-zone-a",
    });
  });

  it("prevents double submission when busy is true", () => {
    render(
      <AddListingFromUrlDialog
        busy={true}
        error={null}
        onClose={mockOnClose}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    const input = screen.getByTestId("intake-url-input");
    const submitBtn = screen.getByTestId("intake-submit-button") as HTMLButtonElement;

    fireEvent.change(input, { target: { value: "https://example.com/listing" } });
    fireEvent.click(submitBtn);

    expect(submitBtn.disabled).toBe(true);
    expect(mockOnSubmit).not.toHaveBeenCalled();
    expect(submitBtn).toHaveTextContent("送出中…（防止重複送出）");
  });

  it("locks rapid repeated submissions before the parent busy state updates", async () => {
    let resolveSubmit: (() => void) | undefined;
    const pendingSubmit = vi.fn(
      () => new Promise<void>((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    render(
      <AddListingFromUrlDialog
        busy={false}
        error={null}
        onClose={mockOnClose}
        onSubmit={pendingSubmit}
        submitterLabel="展店主管"
      />
    );

    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: { value: "https://example.com/listing" },
    });
    const submitButton = screen.getByTestId("intake-submit-button");
    fireEvent.click(submitButton);
    fireEvent.click(submitButton);

    expect(pendingSubmit).toHaveBeenCalledTimes(1);
    expect(submitButton).toBeDisabled();
    await act(async () => resolveSubmit?.());
  });

  it("leaves source policy authority with the server", () => {
    render(
      <AddListingFromUrlDialog
        busy={false}
        error={null}
        onClose={mockOnClose}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    const input = screen.getByTestId("intake-url-input");
    fireEvent.change(input, { target: { value: "https://www.sinyi.com.tw/buy/house/1234" } });

    expect(screen.getByTestId("intake-source-preview")).toHaveTextContent("送出後由伺服器判定");
    expect(screen.getByTestId("intake-source-preview")).toHaveTextContent("www.sinyi.com.tw");
    expect(screen.getByTestId("intake-source-preview")).not.toHaveTextContent("可自動處理擷取");
  });

  it("displays exact duplicate intercept alert when conflict error is passed", () => {
    const openExisting = vi.fn();
    const conflictError: IntakeApiError = {
      status: 409,
      code: "ODP-INTAKE-CONFLICT",
      summary: "此 URL 已在處理中，已存在紀錄（IN-999）。",
      nextAction: "請開啟既有紀錄",
      correlationId: "corr-dup-123",
      occurredAt: "2026-07-21T05:00:00Z",
      retryable: false,
    };

    render(
      <AddListingFromUrlDialog
        busy={false}
        error={conflictError}
        onClose={mockOnClose}
        onOpenExisting={openExisting}
        onSubmit={mockOnSubmit}
        submitterLabel="展店主管"
      />
    );

    expect(screen.getByTestId("intake-exact-duplicate-intercept")).toBeDefined();
    expect(screen.getByTestId("intake-add-error")).toHaveTextContent("此 URL 已在處理中");
    fireEvent.click(screen.getByTestId("intake-open-existing"));
    expect(openExisting).toHaveBeenCalledWith("IN-999");
  });

  it("shows only the supplied native zones and submits the actual selected ID", async () => {
    render(<AddListingFromUrlDialog busy={false} error={null} onClose={mockOnClose} onSubmit={mockOnSubmit}
      submitterLabel="展店經理" heatZoneOptions={{ state: "ready", options: [
        { id: "native-zone-b", label: "伺服器區域 B" }, { id: "native-zone-c", label: "伺服器區域 C" },
      ] }} />);
    const select = screen.getByTestId("intake-area-select");
    expect(within(select).getAllByRole("option").map(option => option.getAttribute("value"))).toEqual(["", "native-zone-b", "native-zone-c"]);
    expect(screen.queryByText(/信義松仁生活圈|板橋府中商圈/)).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "native-zone-c" } });
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/native-listing" } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).toHaveBeenCalledWith({ url: "https://example.com/native-listing", heatZoneId: "native-zone-c" });
  });

  it.each(["loading", "error", "ready"] as const)("keeps the optional form usable with %s empty options and no stale zone", async state => {
    render(<AddListingFromUrlDialog busy={false} defaultHeatZoneId="stale-not-authorized" error={null}
      heatZoneOptions={{ state, options: [] }} onClose={mockOnClose} onSubmit={mockOnSubmit} submitterLabel="展店經理" />);
    const select = screen.getByTestId("intake-area-select");
    expect(select).toBeDisabled();
    expect(select).toHaveValue("");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(screen.getByTestId("intake-area-note")).toHaveTextContent(state === "loading" ? "載入中" : state === "error" ? "無法取得" : "沒有可選區域");
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://example.com/native-listing" } });
    await act(async () => { fireEvent.click(screen.getByTestId("intake-submit-button")); });
    expect(mockOnSubmit).toHaveBeenCalledWith({ url: "https://example.com/native-listing", heatZoneId: "" });
  });

});
