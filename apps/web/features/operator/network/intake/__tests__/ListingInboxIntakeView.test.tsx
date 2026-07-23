import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    version: 1,
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

const managerPermissionContext = {
  resourceInScope: true,
  isOwner: true,
  isAssigned: true,
  sourceInScope: true,
  purposeDeclared: true,
  fieldClassification: "INTERNAL" as const,
  workflowState: "NEEDS_REVIEW",
};

const managerPermissionProps = {
  permissionContext: managerPermissionContext,
  submitPermissionContext: managerPermissionContext,
  permissionContextForRecord: (record: AssistedIntake) => ({
    ...managerPermissionContext,
    workflowState: record.stage,
  }),
};

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
        {...managerPermissionProps}
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

  it("sends saved-view filtering to the server contract", async () => {
    const onQueryChange = vi.fn();
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        onQueryChange={onQueryChange}
        records={mockRecords}
      />
    );

    const needsReviewTab = screen.getByTestId("intake-tab-needsReview");
    fireEvent.click(needsReviewTab);

    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ savedView: "needsReview", page: 1 })));
  });

  it("sends search to the server contract", async () => {
    const onQueryChange = vi.fn();
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        onQueryChange={onQueryChange}
        records={mockRecords}
      />
    );

    const searchInput = screen.getByTestId("intake-search-input");
    fireEvent.change(searchInput, { target: { value: "sinyi" } });

    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ search: "sinyi", page: 1 })));
  });

  it("toggles between list mode and map mode", () => {
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
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
    expect(screen.queryByTestId("intake-table")).not.toBeInTheDocument();
    expect(screen.getByTestId("intake-map-marker-IN-101")).toHaveTextContent("待定位");

    const listModeBtn = screen.getByTestId("intake-view-mode-list");
    fireEvent.click(listModeBtn);

    expect(screen.queryByTestId("intake-map-view-panel")).toBeNull();
  });

  it("restores server query filters across browser history navigation", async () => {
    const onQueryChange = vi.fn();
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        onQueryChange={onQueryChange}
        records={mockRecords}
      />
    );

    fireEvent.change(screen.getByTestId("intake-search-input"), { target: { value: "sinyi" } });
    expect(window.location.search).toContain("search=sinyi");

    window.history.pushState(null, "", "/operator/network?search=591");
    fireEvent.popState(window);

    expect(screen.getByTestId("intake-search-input")).toHaveValue("591");
    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ search: "591" })));
  });

  it("triggers detail modal on row action button click", () => {
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
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

  it("renders degraded evidence separately and directly retries retryable failures", () => {
    const onRetryIntake = vi.fn();
    const failed = intake({
      id: "IN-FAIL",
      stage: "FAILED",
      failure: { code: "FETCH_TIMEOUT", summary: "timeout", nextAction: "retry", retryable: true },
    });
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={mockOnAddSubmit}
        onOpenDetail={mockOnOpenDetail}
        onRetryIntake={onRetryIntake}
        pageData={{ items: [failed], total: 1, page: 1, pageSize: 10, counts: { needsReview: 0, awaitingEntry: 0, processing: 0, blocked: 1, ready: 0 }, evidenceState: "degraded" }}
        records={[failed]}
      />
    );
    expect(screen.getByTestId("intake-evidence-degraded")).toHaveTextContent("證據降級");
    expect(screen.getAllByText(/可重試/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("intake-row-action-IN-FAIL"));
    expect(onRetryIntake).toHaveBeenCalledWith("IN-FAIL");
    expect(mockOnOpenDetail).not.toHaveBeenCalled();
  });

  it("renders permission denied note for unauthorized role", () => {
    render(
      <ListingInboxIntakeView
        {...managerPermissionProps}
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
        {...managerPermissionProps}
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
        {...managerPermissionProps}
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
