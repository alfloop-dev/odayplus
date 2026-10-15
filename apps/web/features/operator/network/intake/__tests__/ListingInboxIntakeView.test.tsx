import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("provides separate native open and action controls without nesting interactive rows", () => {
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready" onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} records={mockRecords} />);
    const row = screen.getByTestId("intake-inbox-row-IN-101");
    expect(row).not.toHaveAttribute("role", "button");
    expect(screen.getByRole("button", { name: "開啟收件 IN-101" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("intake-row-open-IN-101"));
    expect(mockOnOpenDetail).toHaveBeenCalledExactlyOnceWith("IN-101");
    expect(screen.getByTestId("intake-row-action-IN-101")).toBeInTheDocument();
  });

  it("uses the server total for the all view and never invents missing counts from page length", () => {
    const props = { activeRoleId: "expansion-manager" as const, busy: false, loadState: "ready" as const,
      onAddSubmit: mockOnAddSubmit, onOpenDetail: mockOnOpenDetail, records: mockRecords };
    const { rerender } = render(<ListingInboxIntakeView {...props} />);
    expect(screen.getByTestId("intake-tab-all")).toHaveTextContent(/^全部物件$/);
    expect(screen.getByTestId("intake-tab-needsReview")).toHaveTextContent(/^需覆核$/);
    rerender(<ListingInboxIntakeView {...props} pageData={{ items: mockRecords, total: 37, page: 1, pageSize: 10,
      counts: { needsReview: 20, awaitingEntry: 0, processing: 7, blocked: 0, ready: 10 }, evidenceState: "complete" }} />);
    expect(screen.getByTestId("intake-tab-all")).toHaveTextContent("全部物件 (37)");
    expect(screen.getByTestId("intake-tab-needsReview")).toHaveTextContent("需覆核 (20)");
    fireEvent.change(screen.getByTestId("intake-filter-source"), { target: { value: "source-specific" } });
    expect(screen.getByTestId("intake-tab-all")).toHaveTextContent(/^全部物件$/);
    fireEvent.change(screen.getByTestId("intake-filter-source"), { target: { value: "" } });
    expect(screen.getByTestId("intake-tab-all")).toHaveTextContent("全部物件 (37)");
    fireEvent.click(screen.getByTestId("intake-tab-needsReview"));
    expect(screen.getByTestId("intake-tab-all")).toHaveTextContent(/^全部物件$/);
  });

  it("offers exactly the canonical API assignment and SLA states", () => {
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} records={mockRecords} />);
    const options = (id: string) => within(screen.getByTestId(id)).getAllByRole("option").map((option) => (option as HTMLOptionElement).value);
    expect(options("intake-filter-assignment")).toEqual(["", "UNASSIGNED", "ASSIGNED", "CLAIMED", "TRANSFERRED", "ESCALATED", "COMPLETED"]);
    expect(options("intake-filter-sla")).toEqual(["", "ON_TRACK", "DUE_SOON", "OVERDUE", "BREACHED", "PAUSED", "COMPLETED"]);
  });

  it("sends saved-view filtering to the server contract", async () => {
    const onQueryChange = vi.fn();
    render(
      <ListingInboxIntakeView
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

  it.each([
    ["intake-filter-source", "sourceId", "source-authority", "source-authority"],
    ["intake-filter-submitted-by", "submittedBy", "10000000-0000-4000-8000-000000000002", "10000000-0000-4000-8000-000000000002"],
    ["intake-filter-owner", "ownerId", "10000000-0000-4000-8000-000000000003", "10000000-0000-4000-8000-000000000003"],
    ["intake-filter-heat-zone", "heatZoneId", "HZ-EXACT", "HZ-EXACT"],
    ["intake-filter-area", "assignedAreaId", "AREA-EXACT", "AREA-EXACT"],
    ["intake-filter-assignment", "assignmentStatus", "CLAIMED", "CLAIMED"],
    ["intake-filter-assignment", "assignmentStatus", "UNASSIGNED", "UNASSIGNED"],
    ["intake-filter-sla", "slaState", "PAUSED", "PAUSED"],
    ["intake-filter-masked", "masked", "true", true],
    ["intake-filter-restricted", "restricted", "false", false],
    ["intake-filter-retryable", "retryable", "false", false],
  ])("sends the explicit %s condition and restarts server pagination", async (testId, field, value, apiValue) => {
    window.history.replaceState(null, "", "/operator/network?page=8&selected=IN-101&section=identity");
    const onQueryChange = vi.fn();
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} onQueryChange={onQueryChange} records={mockRecords} />);
    const control = screen.getByTestId(String(testId));
    expect(control).toHaveAccessibleName();
    fireEvent.change(control, { target: { value } });
    const apiField = field === "heatZoneId" ? "selectedHeatZoneId" : String(field);
    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ [apiField]: apiValue, page: 1 })));
    const query = new URLSearchParams(window.location.search);
    expect(query.get(String(field))).toBe(String(value));
    expect(query.get("selected")).toBe("IN-101");
    expect(query.get("section")).toBe("identity");
    // The caller supplies a server page; client-side filtering must not hide
    // records while the authoritative response is pending.
    expect(screen.getByTestId("intake-inbox-row-IN-102")).toBeInTheDocument();
  });

  it.each([
    ["intake-filter-observed-from", "observedFrom"], ["intake-filter-observed-to", "observedTo"],
    ["intake-filter-updated-from", "updatedFrom"], ["intake-filter-updated-to", "updatedTo"],
  ])("encodes local %s as a timezone-bearing ISO bound and clears it without inventing a timestamp", async (testId, field) => {
    const onQueryChange = vi.fn();
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} onQueryChange={onQueryChange} records={mockRecords} />);
    const control = screen.getByTestId(testId!);
    expect(control).toHaveValue("");
    const local = "2026-10-13T08:15:30.250";
    fireEvent.change(control, { target: { value: local } });
    const iso = new Date(local).toISOString();
    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ [field!]: iso })));
    expect(new URLSearchParams(window.location.search).get(field!)).toBe(iso);
    fireEvent.change(control, { target: { value: "" } });
    expect(new URLSearchParams(window.location.search).has(field!)).toBe(false);
    expect(control).toHaveValue("");
  });

  it.each(["submittedAt", "lastObservedAt", "dueAt"])("sorts %s through the server contract and URL", async (field) => {
    const onQueryChange = vi.fn();
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} onQueryChange={onQueryChange} records={mockRecords} />);
    fireEvent.click(screen.getByTestId(`intake-sort-${field}`));
    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ sortBy: field, sortOrder: "asc", page: 1 })));
    fireEvent.click(screen.getByTestId(`intake-sort-${field}`));
    await waitFor(() => expect(onQueryChange).toHaveBeenLastCalledWith(expect.objectContaining({ sortBy: field, sortOrder: "desc", page: 1 })));
    expect(new URLSearchParams(window.location.search).get("sortBy")).toBe(field);
  });

  it("shows canonical inbox identity and timestamps without substituting capture or audit dates", () => {
    const record = intake({ id: "IN-AUTHORITATIVE", submittedBy: "SUBJECT-SUBMITTED", ownerSubjectId: "SUBJECT-OWNER",
      submittedAt: "2026-10-13T01:00:00Z", lastObservedAt: "2026-10-13T02:00:00Z", updatedAt: "2026-10-13T03:00:00Z",
      dueAt: "2026-10-14T09:00:00Z", assignedAreaId: "AREA-AUTHORITATIVE", assignmentStatus: "CLAIMED",
      intakeMethod: "APPROVED_FEED", masked: true, restricted: true,
      capturedAt: "1999-01-01T00:00:00Z", auditEvents: [{ occurredAt: "2030-01-01T00:00:00Z" }] as AssistedIntake["auditEvents"],
    });
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} records={[record]} />);
    const row = screen.getByTestId(`intake-inbox-row-${record.id}`);
    expect(within(row).getByTestId(`intake-row-submitted-by-${record.id}`)).toHaveTextContent("SUBJECT-SUBMITTED");
    expect(within(row).getByTestId(`intake-row-owner-${record.id}`)).toHaveTextContent("SUBJECT-OWNER");
    for (const [suffix, date] of [["submitted-at", record.submittedAt], ["observed", record.lastObservedAt], ["updated", record.updatedAt], ["due", record.dueAt]]) {
      expect(within(row).getByTestId(`intake-row-${suffix}-${record.id}`)).toHaveTextContent(date!);
    }
    expect(row).toHaveTextContent("AREA-AUTHORITATIVE");
    expect(row).toHaveTextContent("欄位已遮蔽");
    expect(row).toHaveTextContent("資料受限制");
    expect(row).not.toHaveTextContent("1999-01-01");
    expect(row).not.toHaveTextContent("2030-01-01");
    expect(row).not.toHaveTextContent("測試擁有者");
  });

  it("marks absent projection fields unknown instead of inventing method, owner, update time or restriction", () => {
    const record = intake({ id: "IN-UNKNOWN", sourceId: null, submitter: null, owner: null, capturedAt: "1999-01-01T00:00:00Z" });
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail} records={[record]} />);
    const row = screen.getByTestId(`intake-inbox-row-${record.id}`);
    expect(row).toHaveTextContent("收件方式 尚未提供");
    expect(row).toHaveTextContent("負責人 尚未提供");
    expect(row).toHaveTextContent("重試狀態尚未提供");
    expect(row).toHaveTextContent("資料限制尚未提供");
    expect(within(row).getByText("尚未提供")).toBeInTheDocument();
    expect(within(row).getByTestId(`intake-row-updated-${record.id}`)).toHaveTextContent("更新 尚未提供");
    expect(row).not.toHaveTextContent("1999-01-01");
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

  it("passes the current native HeatZone options through the normal add opener", () => {
    render(<ListingInboxIntakeView activeRoleId="expansion-manager" busy={false} loadState="ready"
      records={[]} selectedHeatZoneId="native-inbox-zone" onAddSubmit={mockOnAddSubmit} onOpenDetail={mockOnOpenDetail}
      heatZoneOptions={{ state: "ready", options: [{ id: "native-inbox-zone", label: "原生收件區域" }] }} />);
    fireEvent.click(screen.getByTestId("intake-add-button"));
    expect(screen.getByTestId("intake-area-select")).toHaveValue("native-inbox-zone");
    expect(screen.getByRole("option", { name: "原生收件區域" })).toHaveValue("native-inbox-zone");
  });

});
