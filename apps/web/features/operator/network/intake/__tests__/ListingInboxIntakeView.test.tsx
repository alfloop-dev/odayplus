import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssignmentReceipt } from "@oday-plus/openapi-client";
import type {
  InboxIntakeRecord,
  IntakeInboxBootstrapContext,
  IntakeInboxPageContract,
  IntakeInboxSavedView,
} from "../inboxContracts";

vi.mock("maplibre-gl", () => {
  class MockMap {
    container: HTMLElement;
    setCenter = vi.fn();
    setZoom = vi.fn();
    fitBounds = vi.fn();
    on = vi.fn();
    remove = vi.fn();

    constructor(options: { container: HTMLElement }) {
      this.container = options.container;
    }
  }

  class MockMarker {
    element: HTMLElement;
    setLngLat = vi.fn(() => this);
    addTo = vi.fn((map: MockMap) => {
      map.container.appendChild(this.element);
      return this;
    });
    remove = vi.fn(() => this.element.remove());

    constructor(options: { element: HTMLElement }) {
      this.element = options.element;
    }
  }

  class MockLngLatBounds {
    extend = vi.fn(() => this);
  }

  return {
    default: {
      LngLatBounds: MockLngLatBounds,
      Map: MockMap,
      Marker: MockMarker,
    },
  };
});

import { ListingInboxIntakeView } from "../ListingInboxIntakeView";

function intake(
  overrides: Partial<InboxIntakeRecord> = {},
): InboxIntakeRecord {
  return {
    id: "IN-000",
    sourceId: "source-example",
    intakeMethod: "URL",
    canonicalUrl: "https://listings.example.com/property/000",
    originalUrl: "https://listings.example.com/property/000",
    stage: "NEEDS_REVIEW",
    policy: "ASSISTED_ENTRY_ONLY",
    policyLabel: "人工補錄",
    policyReason: "Policy requires assisted entry.",
    submitter: "actor-submitter",
    owner: "actor-owner",
    heatZoneId: "HZ-01",
    assignedAreaId: "AREA-TAIPEI-01",
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: "2026-07-22T03:00:00Z",
    lastObservedAt: "2026-07-22T03:00:00Z",
    lastUpdatedAt: "2026-07-23T04:00:00Z",
    parserVersion: "parser-v1",
    correlationId: "corr-in-000",
    parsedFields: {},
    matchResult: null,
    auditEvents: [],
    version: 1,
    assignmentStatus: "ASSIGNED",
    slaState: "DUE_SOON",
    dueAt: "2026-07-24T03:00:00Z",
    needsReview: true,
    restrictedData: false,
    retryable: false,
    ...overrides,
  };
}

const records: InboxIntakeRecord[] = [
  intake({
    id: "IN-101",
    sourceId: "source-591",
    canonicalUrl: "https://listings.example.com/property/101",
    matchResult: {
      outcome: "POSSIBLE_MATCH",
      outcomeLabel: "疑似重複",
      targetListingId: "LISTING-201",
      confidence: 0.85,
      agreeingSignals: [
        {
          key: "normalizedAddress",
          label: "Normalized address",
          agrees: true,
          detail: "Address agrees.",
        },
      ],
      contradictingSignals: [
        {
          key: "rent",
          label: "Rent",
          agrees: false,
          detail: "Rent differs.",
        },
      ],
      summary: "需人工覆核",
    },
    location: {
      latitude: 25.033,
      longitude: 121.5654,
      source: "parsed-field-or-source-snapshot",
    },
  }),
  intake({
    id: "IN-EXACT",
    matchResult: {
      outcome: "EXACT_DUPLICATE",
      outcomeLabel: "完全重複",
      targetListingId: "LISTING-202",
      confidence: 1,
      agreeingSignals: [],
      contradictingSignals: [],
      summary: "Canonical identity matched.",
    },
  }),
  intake({
    id: "IN-REVISION",
    matchResult: {
      outcome: "REVISION",
      outcomeLabel: "版本更新",
      targetListingId: "LISTING-203",
      confidence: 0.99,
      agreeingSignals: [],
      contradictingSignals: [],
      summary: "Existing listing revision.",
    },
  }),
  intake({
    id: "IN-102",
    sourceId: "source-manual",
    intakeMethod: "MANUAL",
    owner: "",
    assignmentId: null,
    assignmentStatus: null,
    stage: "AWAITING_ASSISTED_ENTRY",
    location: null,
  }),
  intake({
    id: "IN-FAIL",
    sourceId: "source-retry",
    stage: "FAILED",
    owner: "actor-owner",
    failure: {
      code: "FETCH_TIMEOUT",
      summary: "Retrieval timed out.",
      nextAction: "Retry retrieval.",
      retryable: true,
    },
    needsReview: false,
    retryable: true,
    location: null,
  }),
];

const pageData: IntakeInboxPageContract = {
  items: records,
  total: 23,
  page: 1,
  pageSize: 10,
  evidenceState: "degraded",
  nextCursor: "cursor-next",
  previousCursor: null,
};

const bootstrapContext: IntakeInboxBootstrapContext = {
  tenantId: "tenant-authoritative",
  scopeLabel: "TW-NORTH / AREA-TAIPEI-01",
  ownerLabel: "queue-expansion-north",
  submitterLabel: "Manager Lin (actor-manager)",
  heatZones: [
    { id: "HZ-01", label: "HZ-01 authoritative zone" },
    { id: "HZ-AUTH", label: "HZ-AUTH bootstrap only" },
  ],
};

const savedViews: IntakeInboxSavedView[] = [
  { id: "review-queue", label: "北區待覆核", count: 7 },
  { id: "my-work", label: "我的工作" },
];

function claimReceipt(): AssignmentReceipt {
  return {
    assignment_id: "ASG-IN-102",
    audit_event_id: "AUD-CLAIM-102",
    due_at: "2026-07-28T00:00:00Z",
    owner_subject_id: "actor-manager",
    status: "CLAIMED",
    version: 2,
  };
}

function renderView(
  overrides: Partial<React.ComponentProps<typeof ListingInboxIntakeView>> = {},
) {
  const props: React.ComponentProps<typeof ListingInboxIntakeView> = {
    activeRoleId: "expansion-manager",
    activeSubjectId: "actor-manager",
    busy: false,
    bootstrapContext,
    loadState: "ready",
    onClaimIntake: vi.fn().mockResolvedValue({
      ok: true,
      value: claimReceipt(),
    }),
    onAddSubmit: vi.fn().mockResolvedValue(undefined),
    onOpenDetail: vi.fn(),
    pageData,
    records,
    savedViews,
    ...overrides,
  };
  return { ...render(<ListingInboxIntakeView {...props} />), props };
}

describe("ListingInboxIntakeView", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/w/expansion/listings");
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("renders a semantic sortable table with every required Inbox column", () => {
    renderView();

    expect(screen.getByRole("table", {
      name: "Listing Inbox 收件、處理、比對、責任與 SLA",
    })).toBeVisible();
    for (const header of [
      "Listing / Intake",
      "來源",
      "方式",
      "階段",
      "比對結果",
      "問題／下一步",
      "Owner / Assignment",
      "Due / SLA",
      "送件人",
      "HeatZone / Area",
      "Observed",
      "Updated",
      "資料限制",
      "直接動作",
    ]) {
      expect(screen.getByRole("columnheader", { name: new RegExp(header) })).toBeVisible();
    }
    expect(screen.getByRole("columnheader", { name: /Updated/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(screen.getByTestId("intake-inbox-row-IN-101")).toHaveTextContent(
      "尚無 Listing",
    );
    expect(
      screen.getByTestId("intake-inbox-row-IN-101").querySelector(
        'a[href*="LISTING-201"]',
      ),
    ).toBeNull();
    expect(screen.getByTestId("intake-inbox-row-IN-EXACT")).toHaveTextContent(
      "Listing LISTING-202",
    );
    expect(
      screen.getByTestId("intake-inbox-row-IN-REVISION"),
    ).toHaveTextContent("Listing LISTING-203");
    expect(screen.getByTestId("intake-inbox-row-IN-FAIL")).toHaveTextContent(
      "可重試",
    );
  });

  it("renders only authoritative saved views and HeatZones from bootstrap props", () => {
    renderView();

    expect(screen.getByTestId("intake-tab-review-queue")).toHaveTextContent(
      "北區待覆核 (7)",
    );
    expect(screen.getByTestId("intake-tab-my-work")).toHaveTextContent(
      "我的工作",
    );
    expect(screen.queryByText("全部物件")).not.toBeInTheDocument();
    expect(screen.queryByText("隔離／失敗")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("intake-add-button"));
    const heatZoneSelect = screen.getByTestId("intake-area-select");
    expect(heatZoneSelect).toHaveTextContent("HZ-AUTH bootstrap only");
    expect(heatZoneSelect).not.toHaveTextContent("信義松仁生活圈");
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent(
      "Tenant tenant-authoritative",
    );
    expect(screen.getByTestId("intake-submitter")).toHaveTextContent(
      "初始 owner queue-expansion-north",
    );
  });

  it("fails closed when saved views or bootstrap context are unavailable", () => {
    renderView({ bootstrapContext: undefined, savedViews: undefined });

    expect(
      screen.getByTestId("intake-saved-views-unavailable"),
    ).toBeVisible();
    expect(
      screen.getByTestId("intake-bootstrap-context-unavailable"),
    ).toBeVisible();
    expect(screen.getByTestId("intake-add-button")).toBeDisabled();
    expect(screen.queryByRole("navigation", { name: "收件 saved views" }))
      .not.toBeInTheDocument();
  });

  it("does not send an unknown URL saved view to the query adapter", async () => {
    window.history.replaceState(
      null,
      "",
      "/w/expansion/listings?savedView=legacy-fake-view",
    );
    const onQueryChange = vi.fn();
    renderView({ onQueryChange });

    expect(
      screen.getByTestId("intake-saved-view-selection-unavailable"),
    ).toBeVisible();
    await waitFor(() => expect(onQueryChange).toHaveBeenCalled());
    expect(onQueryChange.mock.calls.at(-1)?.[0]).not.toHaveProperty(
      "savedView",
    );
  });

  it("sends every required filter, stable sort and cursor field to the server query contract", async () => {
    const onQueryChange = vi.fn();
    renderView({ onQueryChange });

    fireEvent.click(screen.getByTestId("intake-tab-review-queue"));
    fireEvent.change(screen.getByTestId("intake-search-input"), {
      target: { value: "信義路" },
    });
    const changes: Array<[string, string]> = [
      ["intake-filter-method", "URL"],
      ["intake-filter-stage", "NEEDS_REVIEW"],
      ["intake-filter-outcome", "POSSIBLE_MATCH"],
      ["intake-filter-source", "source-591"],
      ["intake-filter-submitter", "actor-submitter"],
      ["intake-filter-owner", "actor-owner"],
      ["intake-filter-assignment", "ASSIGNED"],
      ["intake-filter-needs-review", "true"],
      ["intake-filter-sla", "DUE_SOON"],
      ["intake-filter-heatzone", "HZ-01"],
      ["intake-filter-area", "AREA-TAIPEI-01"],
      ["intake-filter-observed-from", "2026-07-01T00:00"],
      ["intake-filter-observed-to", "2026-07-31T23:59"],
      ["intake-filter-updated-from", "2026-07-02T00:00"],
      ["intake-filter-updated-to", "2026-07-30T23:59"],
      ["intake-filter-restricted", "false"],
      ["intake-filter-quarantined", "false"],
      ["intake-filter-failed", "false"],
      ["intake-filter-retryable", "true"],
    ];
    for (const [testId, value] of changes) {
      fireEvent.change(screen.getByTestId(testId), { target: { value } });
    }
    fireEvent.click(screen.getByRole("button", { name: /^來源/ }));
    fireEvent.click(screen.getByTestId("intake-next-page"));

    await waitFor(() =>
      expect(onQueryChange).toHaveBeenLastCalledWith({
        selectedHeatZoneId: "HZ-01",
        page: 2,
        pageSize: 10,
        cursor: "cursor-next",
        search: "信義路",
        savedView: "review-queue",
        intakeMethod: "URL",
        intakeStage: "NEEDS_REVIEW",
        matchOutcome: "POSSIBLE_MATCH",
        sourceId: "source-591",
        submittedBy: "actor-submitter",
        owner: "actor-owner",
        assignmentStatus: "ASSIGNED",
        needsReview: "true",
        slaState: "DUE_SOON",
        heatZoneId: "HZ-01",
        areaId: "AREA-TAIPEI-01",
        observedFrom: "2026-07-01T00:00:00.000Z",
        observedTo: "2026-07-31T23:59:00.000Z",
        updatedFrom: "2026-07-02T00:00:00.000Z",
        updatedTo: "2026-07-30T23:59:00.000Z",
        restrictedData: "false",
        quarantined: "false",
        failed: "false",
        retryable: "true",
        sortBy: "sourceId",
        sortOrder: "asc",
      }),
    );
  });

  it("restores filters, map mode and selection from the URL and browser history", async () => {
    window.history.replaceState(
      null,
      "",
      "/w/expansion/listings?search=initial&sourceId=source-591&viewMode=map&selected=IN-101",
    );
    const onQueryChange = vi.fn();
    renderView({ onQueryChange });

    expect(screen.getByTestId("intake-view-mode-map")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("intake-map-view-panel")).toBeVisible();

    window.history.pushState(
      null,
      "",
      "/w/expansion/listings?search=restored&owner=actor-owner&viewMode=list&selected=IN-FAIL",
    );
    fireEvent.popState(window);

    expect(screen.getByTestId("intake-search-input")).toHaveValue("restored");
    expect(screen.getByTestId("intake-filter-owner")).toHaveValue("actor-owner");
    expect(screen.getByLabelText("選取收件 IN-FAIL")).toBeChecked();
    await waitFor(() =>
      expect(onQueryChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "restored", owner: "actor-owner" }),
      ),
    );
  });

  it("uses MapLibre for authoritative coordinates and a separate unlocated list", () => {
    renderView();
    fireEvent.click(screen.getByTestId("intake-view-mode-map"));

    expect(screen.getByTestId("intake-map-view-panel")).toHaveAttribute(
      "data-map-engine",
      "maplibre",
    );
    expect(screen.getByTestId("intake-map-marker-IN-101")).toHaveAttribute(
      "href",
      "/w/expansion/listings/intake/IN-101",
    );
    expect(screen.getByTestId("intake-unlocated-list")).toHaveTextContent("IN-102");
    expect(screen.getByTestId("intake-unlocated-list")).toHaveTextContent("IN-FAIL");
    expect(screen.queryByTestId("intake-map-marker-IN-102")).not.toBeInTheDocument();
  });

  it("hands a parent-applied authoritative submission record back to the Add URL receipt", async () => {
    const onAddSubmit = vi.fn().mockResolvedValue(undefined);
    const { rerender, props } = renderView({ onAddSubmit });
    fireEvent.click(screen.getByTestId("intake-add-button"));
    fireEvent.change(screen.getByTestId("intake-url-input"), {
      target: {
        value:
          "https://listings.example.com/property/new?utm_source=operator",
      },
    });
    fireEvent.click(screen.getByTestId("intake-submit-button"));
    await waitFor(() => expect(onAddSubmit).toHaveBeenCalledTimes(1));

    const submitted = intake({
      id: "IN-NEW",
      originalUrl: "https://listings.example.com/property/new",
      canonicalUrl: "https://listings.example.com/property/new",
      stage: "CHECKING_IDENTITY",
      correlationId: "corr-new",
    });
    rerender(
      <ListingInboxIntakeView
        {...props}
        pageData={{ ...pageData, items: [submitted, ...records], total: 24 }}
        records={[submitted, ...records]}
      />,
    );

    expect(
      await screen.findByTestId("intake-inbox-submission-receipt"),
    ).toHaveTextContent("Intake IN-NEW");
    expect(screen.queryByTestId("intake-add-dialog")).not.toBeInTheDocument();
  });

  it("provides direct open, claim, review, retry and correction actions", async () => {
    const onRetryIntake = vi.fn();
    const onOpenDetail = vi.fn();
    const onClaimIntake = vi.fn().mockResolvedValue({
      ok: true,
      value: claimReceipt(),
    });
    renderView({ onClaimIntake, onOpenDetail, onRetryIntake });

    expect(screen.getByTestId("intake-open-IN-101")).toHaveAttribute(
      "href",
      "/w/expansion/listings/intake/IN-101",
    );
    expect(screen.getByTestId("intake-review-IN-101")).toHaveAttribute(
      "href",
      "/w/expansion/listings/intake/IN-101?section=identity&compare=true",
    );
    expect(screen.getByTestId("intake-correction-IN-101")).toHaveAttribute(
      "href",
      "/w/expansion/listings/intake/IN-101?section=fields&action=correction",
    );

    fireEvent.click(screen.getByTestId("intake-claim-IN-102"));
    await waitFor(() => expect(onClaimIntake).toHaveBeenCalledTimes(1));
    expect(onClaimIntake).toHaveBeenCalledWith("IN-102");
    expect(onClaimIntake.mock.calls[0]).toHaveLength(1);
    expect(await screen.findByTestId("intake-claim-receipt")).toHaveTextContent(
      "Assignment ASG-IN-102",
    );

    fireEvent.click(screen.getByTestId("intake-retry-IN-FAIL"));
    expect(onRetryIntake).toHaveBeenCalledWith("IN-FAIL");
    expect(onOpenDetail).not.toHaveBeenCalled();
  });

  it("shows permission, loading, error and degraded evidence states", () => {
    const { rerender } = renderView({
      activeRoleId: "ops-lead",
      pageData: undefined,
    });
    expect(screen.getByTestId("intake-no-access")).toBeVisible();

    rerender(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="loading"
        onAddSubmit={vi.fn()}
        records={[]}
      />,
    );
    expect(screen.getByTestId("intake-inbox-loading")).toBeVisible();

    rerender(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadError={{
          status: 500,
          code: "ODP-INTAKE-500",
          summary: "無法載入收件列表",
          nextAction: "請重試",
          correlationId: "corr-load",
          occurredAt: "2026-07-23T05:00:00Z",
          retryable: true,
          currentVersion: 12,
          currentState: "NEEDS_REVIEW",
        }}
        loadState="error"
        onAddSubmit={vi.fn()}
        records={[]}
      />,
    );
    expect(screen.getByTestId("intake-inbox-error")).toHaveTextContent(
      "無法載入收件列表",
    );
    expect(screen.getByTestId("intake-inbox-error-code")).toHaveTextContent(
      "ODP-INTAKE-500",
    );
    expect(
      screen.getByTestId("intake-inbox-error-correlation"),
    ).toHaveTextContent("corr-load");
    expect(
      screen.getByTestId("intake-inbox-error-occurred-at"),
    ).toHaveTextContent("2026-07-23T05:00:00Z");
    expect(
      screen.getByTestId("intake-inbox-error-retryable"),
    ).toHaveTextContent("true");
    expect(
      screen.getByTestId("intake-inbox-error-current-version"),
    ).toHaveTextContent("12");
    expect(
      screen.getByTestId("intake-inbox-error-current-state"),
    ).toHaveTextContent("NEEDS_REVIEW");
    expect(screen.getByTestId("intake-inbox-error")).toHaveTextContent(
      "下一步：請重試",
    );

    rerender(
      <ListingInboxIntakeView
        activeRoleId="expansion-manager"
        busy={false}
        loadState="ready"
        onAddSubmit={vi.fn()}
        pageData={pageData}
        records={records}
      />,
    );
    expect(screen.getByTestId("intake-evidence-degraded")).toHaveTextContent(
      "證據降級",
    );
  });
});
