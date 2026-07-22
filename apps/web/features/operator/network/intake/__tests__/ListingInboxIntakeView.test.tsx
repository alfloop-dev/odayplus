import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { ListingInboxIntakeView } from "../ListingInboxIntakeView";

function intake(overrides: Partial<AssistedIntake>): AssistedIntake {
  return {
    id: "IN-000",
    sourceId: "src-default",
    canonicalUrl: "https://example.com/listing",
    originalUrl: "https://example.com/listing",
    stage: "NEEDS_REVIEW",
    policy: "ASSISTED_ENTRY_ONLY",
    policyLabel: "網址單頁送件",
    policyReason: "使用者提交單頁",
    submitter: "測試送件人",
    owner: "測試擁有者",
    heatZoneId: null,
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: null,
    parserVersion: "parser-v1",
    correlationId: null,
    parsedFields: {},
    matchResult: null,
    auditEvents: [],
    ...overrides,
  };
}

const mockRecords: AssistedIntake[] = [
  intake({
    id: "IN-101",
    sourceId: "src-591",
    canonicalUrl: "https://www.591.com.tw/rent-101.html",
    originalUrl: "https://www.591.com.tw/rent-101.html",
    stage: "NEEDS_REVIEW",
    submitter: "張專員",
    owner: "李主管",
    matchResult: {
      outcome: "POSSIBLE_MATCH",
      outcomeLabel: "疑似重複",
      targetListingId: "L-201",
      confidence: 0.85,
      agreeingSignals: [],
      contradictingSignals: [],
      summary: "需人工覆核",
    },
  }),
  intake({
    id: "IN-102",
    sourceId: "src-sinyi",
    canonicalUrl: "https://www.sinyi.com.tw/buy/102",
    originalUrl: "https://www.sinyi.com.tw/buy/102",
    stage: "READY",
    policy: "APPROVED_RETRIEVAL",
    policyLabel: "核准推送來源",
    submitter: "陳專員",
    owner: "李主管",
    matchResult: {
      outcome: "NEW",
      outcomeLabel: "新物件",
      targetListingId: null,
      confidence: 0.98,
      agreeingSignals: [],
      contradictingSignals: [],
      summary: "未找到重複物件",
    },
  }),
  intake({
    id: "IN-103",
    sourceId: "src-rakuya",
    canonicalUrl: "https://www.rakuya.com.tw/item/103",
    originalUrl: "https://www.rakuya.com.tw/item/103",
    stage: "QUARANTINED",
    policy: "SOURCE_BLOCKED",
    policyLabel: "網址單頁送件",
    submitter: "王專員",
    owner: "張主管",
    matchResult: {
      outcome: "QUARANTINED",
      outcomeLabel: "隔離",
      targetListingId: null,
      confidence: 0,
      agreeingSignals: [],
      contradictingSignals: [],
      summary: "來源遭隔離",
    },
  }),
];

describe("ListingInboxIntakeView", () => {
  const mockOnAddSubmit = vi.fn().mockResolvedValue(undefined);
  const mockOnOpenDetail = vi.fn();
  const mockOnRetryLoad = vi.fn();

  beforeEach(() => {
    window.history.replaceState(null, "", "/operator/network");
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("renders header, saved view tabs, and data table rows", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    expect(screen.getByTestId("intake-inbox-view")).toBeDefined();
    expect(screen.getByText("Listing Inbox 收件匣")).toBeDefined();
    expect(screen.getByTestId("intake-add-button")).toBeDefined();
    expect(screen.getByTestId("intake-table")).toBeDefined();

    expect(screen.getByTestId("intake-inbox-row-IN-101")).toBeDefined();
    expect(screen.getByTestId("intake-inbox-row-IN-102")).toBeDefined();
    expect(screen.getByTestId("intake-inbox-row-IN-103")).toBeDefined();
  });

  it("filters records by saved view tab", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    const needsReviewTab = screen.getByTestId("intake-tab-needsReview");
    fireEvent.click(needsReviewTab);

    expect(screen.getByTestId("intake-inbox-row-IN-101")).toBeDefined();
    expect(screen.queryByTestId("intake-inbox-row-IN-102")).toBeNull();
    expect(screen.queryByTestId("intake-inbox-row-IN-103")).toBeNull();
  });

  it("filters records by search input", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    const searchInput = screen.getByTestId("intake-search-input");
    fireEvent.change(searchInput, { target: { value: "sinyi" } });

    expect(screen.queryByTestId("intake-inbox-row-IN-101")).toBeNull();
    expect(screen.getByTestId("intake-inbox-row-IN-102")).toBeDefined();
    expect(screen.queryByTestId("intake-inbox-row-IN-103")).toBeNull();
  });

  it("toggles between list mode and map mode", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    const mapModeBtn = screen.getByTestId("intake-view-mode-map");
    fireEvent.click(mapModeBtn);

    expect(screen.getByTestId("intake-map-view-panel")).toBeDefined();

    const listModeBtn = screen.getByTestId("intake-view-mode-list");
    fireEvent.click(listModeBtn);

    expect(screen.queryByTestId("intake-map-view-panel")).toBeNull();
  });

  it("restores filters across browser history navigation", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    fireEvent.change(screen.getByTestId("intake-search-input"), { target: { value: "sinyi" } });
    expect(window.location.search).toContain("search=sinyi");

    window.history.pushState(null, "", "/operator/network?search=591");
    fireEvent.popState(window);

    expect(screen.getByTestId("intake-search-input")).toHaveValue("591");
    expect(screen.getByTestId("intake-inbox-row-IN-101")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-inbox-row-IN-102")).not.toBeInTheDocument();
  });

  it("triggers detail modal on row action button click", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    const actionBtn = screen.getByTestId("intake-row-action-IN-101");
    fireEvent.click(actionBtn);

    expect(mockOnOpenDetail).toHaveBeenCalledWith("IN-101");
  });

  it("renders permission denied note for unauthorized role", () => {
    render(
      <ListingInboxIntakeView
        activeRoleId="ops-lead"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={mockRecords}
      />
    );

    expect(screen.getByTestId("intake-no-access")).toBeDefined();
    expect(screen.queryByTestId("intake-table")).toBeNull();
  });

  it("renders loading and error states correctly", () => {
    const { rerender } = render(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="loading"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        records={[]}
      />
    );

    expect(screen.getByTestId("intake-inbox-loading")).toBeDefined();

    rerender(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadError={{
          status: 500,
          code: "ODP-INTAKE-500",
          summary: "無法載入收件列表",
          nextAction: "請重試",
          correlationId: null,
          occurredAt: "2026-07-21T05:00:00Z",
          retryable: true,
        }}
        loadState="error"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        onRetryLoad={mockOnRetryLoad}
        records={[]}
      />
    );

    expect(screen.getByTestId("intake-inbox-error")).toBeDefined();
  });
});
