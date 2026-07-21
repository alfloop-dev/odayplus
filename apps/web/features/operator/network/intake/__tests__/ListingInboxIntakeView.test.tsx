import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { ListingInboxIntakeView } from "../ListingInboxIntakeView";

const mockRecords: AssistedIntake[] = [
  {
    id: "IN-101",
    tenantId: "tenant-a",
    sourceId: "src-591",
    canonicalUrl: "https://www.591.com.tw/rent-101.html",
    originalUrl: "https://www.591.com.tw/rent-101.html",
    stage: "NEEDS_REVIEW",
    policyState: "ASSISTED_ENTRY_ONLY",
    policyLabel: "網址單頁送件",
    submitter: "張專員",
    owner: "李主管",
    submittedAt: "2026-07-21T04:00:00Z",
    updatedAt: "2026-07-21T04:10:00Z",
    matchResult: {
      outcome: "POSSIBLE_MATCH",
      targetListingId: "L-201",
      confidence: 0.85,
    },
    version: 1,
  },
  {
    id: "IN-102",
    tenantId: "tenant-a",
    sourceId: "src-sinyi",
    canonicalUrl: "https://www.sinyi.com.tw/buy/102",
    originalUrl: "https://www.sinyi.com.tw/buy/102",
    stage: "READY",
    policyState: "APPROVED_RETRIEVAL",
    policyLabel: "核准推送來源",
    submitter: "陳專員",
    owner: "李主管",
    submittedAt: "2026-07-21T03:00:00Z",
    updatedAt: "2026-07-21T04:20:00Z",
    matchResult: {
      outcome: "NEW",
      confidence: 0.98,
    },
    version: 1,
  },
  {
    id: "IN-103",
    tenantId: "tenant-a",
    sourceId: "src-rakuya",
    canonicalUrl: "https://www.rakuya.com.tw/item/103",
    originalUrl: "https://www.rakuya.com.tw/item/103",
    stage: "QUARANTINED",
    policyState: "SOURCE_BLOCKED",
    policyLabel: "網址單頁送件",
    submitter: "王專員",
    owner: "張主管",
    submittedAt: "2026-07-21T02:00:00Z",
    updatedAt: "2026-07-21T02:30:00Z",
    matchResult: {
      outcome: "QUARANTINED",
      confidence: 0,
    },
    version: 1,
  },
];

describe("ListingInboxIntakeView", () => {
  const mockOnAddSubmit = vi.fn().mockResolvedValue(undefined);
  const mockOnOpenDetail = vi.fn();
  const mockOnRetryLoad = vi.fn();

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
        activeRoleId="operations_manager"
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
