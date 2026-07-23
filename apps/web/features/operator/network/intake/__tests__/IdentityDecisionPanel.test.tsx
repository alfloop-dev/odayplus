import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MatchOutcome } from "@oday-plus/openapi-client";
import { IdentityDecisionBoundary } from "../IdentityDecisionBoundary";
import { ListingCompareTable } from "../ListingCompareTable";
import {
  IDENTITY_OUTCOME_ACTIONS,
  type IdentityActor,
  type IdentityComparableValue,
  type IdentityComparisonContract,
  type IdentityDecisionCommand,
  type IdentityDecisionDraft,
  type IdentityDraftPersistenceReceipt,
  type IdentityDraftScope,
  type IdentityDecisionReceipt,
  type IdentityGraphOperation,
  type IdentityGraphPlan,
  type IdentityReviewWorkflow,
} from "../identityTypes";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

const proposer: IdentityActor = {
  subjectId: "subject-proposer",
  displayName: "王提案",
  role: "expansion_staff",
};

const reviewer: IdentityActor = {
  subjectId: "subject-reviewer",
  displayName: "林覆核",
  role: "expansion_manager",
};

const record = {
  id: "intake-3011",
  correlationId: "corr-authoritative-3011",
  snapshotId: "snapshot-authoritative-3011",
  parserVersion: "parser-2.4.0",
};

const draftIdentity: IdentityDraftScope = {
  tenantId: "tenant-tw-001",
  intakeId: record.id,
  matchCaseId: "match-case-3011",
  actorId: proposer.subjectId,
};

function draftStorageKey(scope: IdentityDraftScope = draftIdentity): string {
  return [
    "odp",
    "intake",
    "identity-draft",
    "v2",
    scope.tenantId,
    scope.intakeId,
    scope.matchCaseId,
    scope.actorId,
  ]
    .map(encodeURIComponent)
    .join(":");
}

function value(displayValue: string): IdentityComparableValue {
  return { value: displayValue, displayValue };
}

function comparison(outcome: MatchOutcome = "POSSIBLE_MATCH"): IdentityComparisonContract {
  return {
    matchCaseId: "match-case-3011",
    matchCaseVersion: 7,
    outcome,
    confidence: outcome === "EXACT_DUPLICATE" ? 1 : 0.78,
    summary: "地址一致，但租金與樓層存在矛盾。",
    currentListingId: outcome === "NEW" ? null : "listing-current-1002",
    currentPropertyId: outcome === "NEW" ? null : "property-current-2002",
    submittedIntakeId: "intake-3011",
    submittedSnapshotId: "snapshot-authoritative-3011",
    submittedParserRunId: "parser-run-authoritative-42",
    fields: {
      sourceId: {
        current: outcome === "NEW" ? null : value("SRC-591-OLD"),
        submitted: value("SRC-591-NEW"),
        state: outcome === "NEW" ? "MISSING" : "CHANGED",
        detail: outcome === "NEW" ? "沒有既有來源 identity。" : "來源刊登 ID 已更新。",
      },
      canonicalUrl: {
        current: outcome === "NEW" ? null : value("https://example.com/listing/old"),
        submitted: value("https://example.com/listing/new"),
        state: outcome === "NEW" ? "MISSING" : "CHANGED",
        detail: "Canonical URL 由 server comparison response 提供。",
      },
      address: {
        current: outcome === "NEW" ? null : value("台北市信義區松高路 12 號"),
        submitted: value("台北市信義區松高路 12 號"),
        state: outcome === "NEW" ? "MISSING" : "MATCH",
        detail: "正規化地址一致。",
      },
      area: {
        current: outcome === "NEW" ? null : value("45 坪"),
        submitted: value("45 坪"),
        state: outcome === "NEW" ? "MISSING" : "MATCH",
        detail: "面積一致。",
      },
      floor: {
        current: outcome === "NEW" ? null : value("8 樓"),
        submitted: value("9 樓"),
        state: outcome === "NEW" ? "MISSING" : "CONTRADICTION",
        detail: "樓層互相矛盾。",
      },
      listingType: {
        current: outcome === "NEW" ? null : value("店面"),
        submitted: value("店面"),
        state: outcome === "NEW" ? "MISSING" : "MATCH",
        detail: "物件類型一致。",
      },
      rentOrPrice: {
        current: outcome === "NEW" ? null : value("NT$35,000"),
        submitted: value("NT$38,000"),
        state: outcome === "NEW" ? "MISSING" : "CHANGED",
        detail: "租金由 35,000 變更為 38,000。",
      },
      status: {
        current: outcome === "NEW" ? null : value("ACTIVE"),
        submitted: value("AVAILABLE"),
        state: outcome === "NEW" ? "MISSING" : "CHANGED",
        detail: "來源狀態變更。",
      },
    },
    agreeingSignals: [
      { key: "address", label: "地址", detail: "正規化地址一致" },
      { key: "area", label: "面積", detail: "45 坪一致" },
    ],
    contradictingSignals: [
      { key: "floor", label: "樓層", detail: "8 樓與 9 樓矛盾" },
      { key: "rentOrPrice", label: "租金", detail: "NT$35,000 與 NT$38,000 不同" },
    ],
  };
}

function graphPlan(operation: IdentityGraphOperation): IdentityGraphPlan {
  return {
    planId: `plan-${operation.toLowerCase()}`,
    operation,
    state: operation === "UNMERGE" || operation === "REVERSAL" ? "REVERSAL_PENDING" : "DRAFT",
    expectedGraphVersion: 14,
    originalDecisionId:
      operation === "UNMERGE" || operation === "REVERSAL" ? "decision-original-88" : null,
    proposer,
    requestedReviewer: reviewer,
    before: {
      nodes: [
        {
          nodeId: "property-before",
          nodeType: "PROPERTY",
          label: "原 Property",
          effective: true,
          version: 14,
        },
        {
          nodeId: "edge-source",
          nodeType: "SOURCE_IDENTITY",
          label: "來源 identity",
          effective: true,
          version: 3,
        },
      ],
      edges: [
        {
          edgeId: "edge-before-1",
          fromNodeId: "edge-source",
          toNodeId: "property-before",
          relation: "RESOLVES_TO",
          effectiveFrom: "2026-07-01T00:00:00Z",
          effectiveTo: null,
          supersedesEdgeId: null,
        },
      ],
    },
    after: {
      nodes: [
        {
          nodeId: "property-after",
          nodeType: "PROPERTY",
          label: "目標 Property",
          effective: true,
          version: 15,
        },
        {
          nodeId: "edge-source",
          nodeType: "SOURCE_IDENTITY",
          label: "來源 identity",
          effective: true,
          version: 4,
        },
      ],
      edges: [
        {
          edgeId: "edge-after-1",
          fromNodeId: "edge-source",
          toNodeId: "property-after",
          relation: "RESOLVES_TO",
          effectiveFrom: "2026-07-23T00:00:00Z",
          effectiveTo: null,
          supersedesEdgeId: "edge-before-1",
        },
      ],
    },
    redirects: [
      {
        fromPropertyId: "property-before",
        toPropertyId: "property-after",
        disposition: operation === "UNMERGE" || operation === "REVERSAL" ? "REVERSE" : "CREATE",
      },
    ],
    candidateImpacts: [
      {
        candidateSiteId: "candidate-77",
        disposition: "REQUIRE_REVIEW",
        targetPropertyId: "property-after",
      },
    ],
    lineageImpact: [
      "保留 edge-before-1 作為 immutable historical edge。",
      "建立 edge-after-1 並以 supersedes_edge_id 指向 edge-before-1。",
    ],
    riskSummary: `${operation} 將改變 effective property resolution，但不覆寫歷史 edge。`,
  };
}

const graphPlans = (["MERGE", "SPLIT", "UNMERGE", "REVERSAL"] as const).map(graphPlan);

function workflow(overrides: Partial<IdentityReviewWorkflow> = {}): IdentityReviewWorkflow {
  return {
    status: "DRAFT",
    currentActor: proposer,
    proposer,
    reviewer,
    decisionId: null,
    requiresIndependentReview: true,
    canPropose: true,
    canReview: false,
    denialReasonCode: null,
    proposal: null,
    ...overrides,
  };
}

function receipt(
  overrides: Partial<IdentityDecisionReceipt> = {},
): IdentityDecisionReceipt {
  return {
    decisionId: "decision-authoritative-9001",
    status: "PENDING_REVIEW",
    outcomeAction: "APPEND_REVISION",
    graphOperation: null,
    graphPlanId: null,
    originalDecisionId: null,
    matchCaseId: "match-case-3011",
    proposer,
    reviewer: null,
    reason: "地址一致，租金變更應建立 immutable ListingRevision。",
    riskAcknowledged: true,
    occurredAt: "2026-07-23T10:30:00Z",
    resourceVersions: { "match-case-3011": 8 },
    listingId: "listing-current-1002",
    listingRevisionId: "listing-revision-authoritative-2",
    effectiveEdgeIds: ["edge-authoritative-2"],
    supersededEdgeIds: ["edge-before-1"],
    redirectIds: [],
    auditEventId: "audit-authoritative-555",
    correlationId: "corr-authoritative-3011",
    lineageImpact: ["Listing revision 2 appended; revision 1 remains immutable."],
    ...overrides,
  };
}

function renderBoundary({
  outcome = "POSSIBLE_MATCH",
  reviewWorkflow = workflow(),
  onSubmit = vi.fn().mockResolvedValue(receipt()),
  conflict = null,
}: {
  outcome?: MatchOutcome;
  reviewWorkflow?: IdentityReviewWorkflow;
  onSubmit?: (command: IdentityDecisionCommand) => Promise<IdentityDecisionReceipt>;
  conflict?: Parameters<typeof IdentityDecisionBoundary>[0]["conflict"];
} = {}) {
  return {
    onSubmit,
    ...render(
      <IdentityDecisionBoundary
        comparison={comparison(outcome)}
        conflict={conflict}
        draftIdentity={{
          ...draftIdentity,
          actorId: reviewWorkflow.currentActor.subjectId,
        }}
        durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
        graphPlans={graphPlans}
        onRefreshConflict={vi.fn()}
        onSubmit={onSubmit}
        record={record}
        workflow={reviewWorkflow}
      />,
    ),
  };
}

describe("ODP-INTAKE-FCL-IDENTITY-001 production integration boundary", () => {
  it("renders a semantic current-versus-submitted table with authoritative values", () => {
    render(<ListingCompareTable comparison={comparison()} />);

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByTestId("compare-current-sourceId").textContent).toBe("SRC-591-OLD");
    expect(screen.getByTestId("compare-submitted-sourceId").textContent).toBe("SRC-591-NEW");
    expect(screen.getByTestId("compare-current-rentOrPrice").textContent).toBe("NT$35,000");
    expect(screen.getByTestId("compare-submitted-rentOrPrice").textContent).toBe("NT$38,000");
    expect(screen.getByTestId("compare-row-floor").getAttribute("data-state")).toBe("CONTRADICTION");
    expect(screen.getByTestId("intake-change-summary").textContent).toContain("矛盾欄位：樓層");
    expect(document.body.textContent).not.toContain("SRC-listing");
  });

  it.each<MatchOutcome>([
    "NEW",
    "EXACT_DUPLICATE",
    "REVISION",
    "POSSIBLE_MATCH",
    "QUARANTINED",
  ])("keeps %s visually and behaviorally distinct", (outcome) => {
    renderBoundary({ outcome });

    const badge = screen.getByTestId("identity-match-badge");
    expect(badge.textContent).toBe(outcome);
    expect(badge.getAttribute("data-outcome")).toBe(outcome);
    for (const action of IDENTITY_OUTCOME_ACTIONS[outcome]) {
      expect(screen.getByTestId(`identity-action-${action}`)).toBeTruthy();
    }
  });

  it("offers all six explicit human outcomes for POSSIBLE_MATCH", () => {
    renderBoundary();

    expect(screen.getByTestId("identity-action-CREATE")).toBeTruthy();
    expect(screen.getByTestId("identity-action-APPEND_REVISION")).toBeTruthy();
    expect(screen.getByTestId("identity-action-MARK_DUPLICATE")).toBeTruthy();
    expect(screen.getByTestId("identity-action-SEND_TO_STEWARD")).toBeTruthy();
    expect(screen.getByTestId("identity-action-REJECT")).toBeTruthy();
    expect(screen.getByTestId("identity-action-QUARANTINE")).toBeTruthy();
    expect(screen.getByTestId("identity-no-auto-merge-note").textContent).toContain("不會自動合併");
  });

  it.each<IdentityGraphOperation>(["MERGE", "SPLIT", "UNMERGE", "REVERSAL"])(
    "renders authoritative %s before/after graph and lineage plan",
    (operation) => {
      renderBoundary();

      fireEvent.click(screen.getByTestId(`identity-graph-${operation}`));

      expect(screen.getByTestId("identity-graph-plan").getAttribute("data-operation")).toBe(operation);
      expect(screen.getByTestId("graph-before-edges").textContent).toContain("edge-before-1");
      expect(screen.getByTestId("graph-after-edges").textContent).toContain("edge-after-1");
      expect(screen.getByTestId("graph-lineage-impact").textContent).toContain("immutable historical edge");
      expect(screen.getByTestId("graph-candidate-impacts").textContent).toContain("REQUIRE_REVIEW");
      expect(screen.getByTestId("graph-redirects").textContent).toContain("property-before");
    },
  );

  it("submits a POSSIBLE_MATCH proposal for independent review and renders only its returned receipt", async () => {
    const onSubmit = vi.fn().mockResolvedValue(receipt());
    renderBoundary({ onSubmit });

    fireEvent.click(screen.getByTestId("identity-action-APPEND_REVISION"));
    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "地址一致，租金變更應建立 immutable ListingRevision。" },
    });
    fireEvent.click(screen.getByTestId("identity-risk-ack"));
    fireEvent.click(screen.getByTestId("identity-submit-proposal"));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        phase: "PROPOSE",
        matchCaseId: "match-case-3011",
        matchCaseVersion: 7,
        outcomeAction: "APPEND_REVISION",
        proposerId: "subject-proposer",
        reviewerId: "subject-reviewer",
        requiresIndependentReview: true,
      }),
    );
    expect(await screen.findByTestId("identity-durable-receipt")).toBeTruthy();
    expect(screen.getByTestId("identity-durable-receipt").textContent).toContain(
      "listing-revision-authoritative-2",
    );
    expect(screen.getByTestId("identity-durable-receipt").textContent).not.toContain("LST-AUTO");
    expect(screen.getByTestId("identity-durable-receipt").textContent).not.toContain("RCPT-MATCH");
  });

  it("denies self-review while preserving the pending proposal", () => {
    renderBoundary({
      reviewWorkflow: workflow({
        status: "PENDING_REVIEW",
        currentActor: proposer,
        proposer,
        reviewer: proposer,
        decisionId: "decision-pending-1",
        canPropose: false,
        canReview: true,
        proposal: {
          outcomeAction: null,
          graphOperation: "MERGE",
          graphPlanId: "plan-merge",
          reason: "提案者要求合併並保留完整 lineage。",
          riskAcknowledged: true,
        },
      }),
    });

    expect(screen.getByTestId("self-review-denied").textContent).toContain("SELF_REVIEW_DENIED");
    expect((screen.getByTestId("identity-review-approve") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("identity-review-reject") as HTMLButtonElement).disabled).toBe(true);
  });

  it("allows a distinct reviewer to approve the exact pending decision", async () => {
    const approvedReceipt = receipt({
      status: "APPROVED",
      reviewer,
      graphOperation: "MERGE",
      outcomeAction: null,
    });
    const onSubmit = vi.fn().mockResolvedValue(approvedReceipt);
    renderBoundary({
      onSubmit,
      reviewWorkflow: workflow({
        status: "PENDING_REVIEW",
        currentActor: reviewer,
        proposer,
        reviewer,
        decisionId: "decision-pending-1",
        canPropose: false,
        canReview: true,
        proposal: {
          outcomeAction: null,
          graphOperation: "MERGE",
          graphPlanId: "plan-merge",
          reason: "提案者要求合併並保留完整 lineage。",
          riskAcknowledged: true,
        },
      }),
    });

    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "已逐欄確認比對與 lineage impact，同意執行。" },
    });
    fireEvent.click(screen.getByTestId("identity-risk-ack"));
    fireEvent.click(screen.getByTestId("identity-review-approve"));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        phase: "REVIEW",
        reviewDisposition: "APPROVE",
        decisionId: "decision-pending-1",
        proposerId: "subject-proposer",
        reviewerId: "subject-reviewer",
        graphOperation: "MERGE",
        graphPlanId: "plan-merge",
      }),
    );
  });

  it("lets a distinct reviewer reject without falsely executing the graph", async () => {
    const rejectedReceipt = receipt({
      status: "REJECTED",
      reviewer,
      graphOperation: "MERGE",
      graphPlanId: "plan-merge",
      outcomeAction: null,
      effectiveEdgeIds: [],
      supersededEdgeIds: [],
      redirectIds: [],
    });
    const onSubmit = vi.fn().mockResolvedValue(rejectedReceipt);
    renderBoundary({
      onSubmit,
      reviewWorkflow: workflow({
        status: "PENDING_REVIEW",
        currentActor: reviewer,
        proposer,
        reviewer,
        decisionId: "decision-pending-1",
        canPropose: false,
        canReview: true,
        proposal: {
          outcomeAction: null,
          graphOperation: "MERGE",
          graphPlanId: "plan-merge",
          reason: "提案者要求合併並保留完整 lineage。",
          riskAcknowledged: true,
        },
      }),
    });

    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "比對證據不足，拒絕此 graph proposal。" },
    });
    fireEvent.click(screen.getByTestId("identity-review-reject"));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        phase: "REVIEW",
        reviewDisposition: "REJECT",
        graphOperation: "MERGE",
        riskAcknowledged: false,
      }),
    );
  });

  it.each<IdentityGraphOperation>(["MERGE", "SPLIT", "UNMERGE", "REVERSAL"])(
    "renders the authoritative %s execution or reversal receipt without constructing IDs",
    (operation) => {
      const operationReceipt = receipt({
        status: operation === "UNMERGE" || operation === "REVERSAL" ? "REVERSED" : "EXECUTED",
        outcomeAction: null,
        graphOperation: operation,
        graphPlanId: `plan-${operation.toLowerCase()}`,
        originalDecisionId:
          operation === "UNMERGE" || operation === "REVERSAL"
            ? "decision-original-88"
            : null,
        effectiveEdgeIds: [`edge-effective-${operation.toLowerCase()}`],
        supersededEdgeIds: [`edge-superseded-${operation.toLowerCase()}`],
        redirectIds: [`redirect-${operation.toLowerCase()}`],
      });

      render(
        <IdentityDecisionBoundary
          comparison={comparison()}
          draftIdentity={draftIdentity}
          durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
          graphPlans={graphPlans}
          onSubmit={vi.fn().mockResolvedValue(operationReceipt)}
          receipt={operationReceipt}
          record={record}
          workflow={workflow()}
        />,
      );

      const receiptPanel = screen.getByTestId("identity-durable-receipt");
      expect(receiptPanel.textContent).toContain(`plan-${operation.toLowerCase()}`);
      expect(receiptPanel.textContent).toContain(`edge-effective-${operation.toLowerCase()}`);
      expect(receiptPanel.textContent).toContain(`redirect-${operation.toLowerCase()}`);
      if (operation === "UNMERGE" || operation === "REVERSAL") {
        expect(receiptPanel.textContent).toContain("decision-original-88");
      }
    },
  );

  it("shows an authoritative conflict and preserves reason/risk draft", () => {
    const conflict = {
      code: "REVIEW_CONFLICT" as const,
      summary: "Match case was updated by another reviewer.",
      currentVersion: 9,
      currentState: "PENDING_REVIEW",
      currentOwner: "subject-other-reviewer",
      correlationId: "corr-conflict-44",
      occurredAt: "2026-07-23T11:00:00Z",
      nextAction: "Reload match case version 9 and review the changed plan.",
    };
    renderBoundary({ conflict });

    const reason = screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement;
    fireEvent.change(reason, { target: { value: "我的決策草稿不得因 conflict 消失。" } });
    fireEvent.click(screen.getByTestId("identity-risk-ack"));

    expect(screen.getByTestId("identity-conflict-banner").textContent).toContain("version 9");
    expect(screen.getByTestId("identity-conflict-banner").textContent).toContain("corr-conflict-44");
    expect(reason.value).toBe("我的決策草稿不得因 conflict 消失。");
    expect((screen.getByTestId("identity-risk-ack") as HTMLInputElement).checked).toBe(true);
  });

  it("restores the draft after unmount and exposes the same durable desktop link on mobile", async () => {
    const first = renderBoundary();
    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "跨裝置前先保存這份 identity 決策草稿。" },
    });
    fireEvent.click(screen.getByTestId("identity-risk-ack"));

    await waitFor(() =>
      expect(window.sessionStorage.getItem(draftStorageKey())).toContain(
        "跨裝置前先保存",
      ),
    );
    first.unmount();

    renderBoundary();
    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe(
      "跨裝置前先保存這份 identity 決策草稿。",
    );
    expect((screen.getByTestId("identity-risk-ack") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("identity-desktop-link").getAttribute("href")).toBe(
      "/w/expansion/listings/intake/intake-3011?section=identity",
    );
    expect(screen.getByTestId("identity-desktop-required").textContent).toContain("草稿已保留");
  });

  it("reports pending and success only after the authoritative server save receipt", async () => {
    const persistedDraft: IdentityDecisionDraft = {
      commandType: "GRAPH",
      outcomeAction: null,
      graphOperation: "SPLIT",
      graphPlanId: "plan-split",
      reason: "這份草稿已由 server draft API 保存。",
      riskAcknowledged: true,
    };
    let resolveSave!: (receipt: IdentityDraftPersistenceReceipt) => void;
    const onDraftSave = vi.fn(
      () =>
        new Promise<IdentityDraftPersistenceReceipt>((resolve) => {
          resolveSave = resolve;
        }),
    );

    render(
      <IdentityDecisionBoundary
        comparison={comparison()}
        draftPersistence="SERVER"
        draftIdentity={draftIdentity}
        durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
        graphPlans={graphPlans}
        onDraftSave={onDraftSave}
        onSubmit={vi.fn().mockResolvedValue(receipt())}
        persistedDraft={persistedDraft}
        record={record}
        workflow={workflow()}
      />,
    );

    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe(
      "這份草稿已由 server draft API 保存。",
    );
    expect(screen.getByTestId("identity-server-draft-status").getAttribute("data-status")).toBe(
      "LOADED",
    );

    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "更新後的 server draft。" },
    });
    expect(onDraftSave).toHaveBeenCalledWith(
      expect.objectContaining({ reason: "更新後的 server draft。" }),
      draftIdentity,
    );
    expect(screen.getByTestId("identity-server-draft-status").getAttribute("data-status")).toBe(
      "PENDING",
    );
    expect(screen.getByTestId("identity-server-draft-status").textContent).not.toContain(
      "已確認保存",
    );

    resolveSave({
      draft: {
        ...persistedDraft,
        reason: "Server normalized and saved draft.",
      },
      draftVersion: 12,
      persistedAt: "2026-07-23T12:00:00Z",
    });

    await waitFor(() =>
      expect(screen.getByTestId("identity-server-draft-status").getAttribute("data-status")).toBe(
        "SAVED",
      ),
    );
    expect(screen.getByTestId("identity-server-draft-status").textContent).toContain("version 12");
    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe(
      "Server normalized and saved draft.",
    );
  });

  it("keeps the local draft visible and reports an authoritative server save failure", async () => {
    const onDraftSave = vi.fn().mockRejectedValue(new Error("DRAFT_VERSION_CONFLICT"));
    render(
      <IdentityDecisionBoundary
        comparison={comparison()}
        draftPersistence="SERVER"
        draftIdentity={draftIdentity}
        durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
        graphPlans={graphPlans}
        onDraftSave={onDraftSave}
        onSubmit={vi.fn().mockResolvedValue(receipt())}
        persistedDraft={null}
        record={record}
        workflow={workflow()}
      />,
    );

    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "保存失敗時仍保留這段草稿。" },
    });

    await waitFor(() =>
      expect(screen.getByTestId("identity-server-draft-status").getAttribute("data-status")).toBe(
        "FAILED",
      ),
    );
    expect(screen.getByTestId("identity-server-draft-status").textContent).toContain(
      "DRAFT_VERSION_CONFLICT",
    );
    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe(
      "保存失敗時仍保留這段草稿。",
    );
  });

  it("clears a stale scoped session draft when the server returns an authoritative null", async () => {
    window.sessionStorage.setItem(
      draftStorageKey(),
      JSON.stringify({
        commandType: "OUTCOME",
        outcomeAction: "CREATE",
        graphOperation: null,
        graphPlanId: null,
        reason: "stale browser draft",
        riskAcknowledged: false,
      }),
    );
    const onDraftSave = vi.fn().mockResolvedValue({
      draft: null,
      draftVersion: 13,
      persistedAt: "2026-07-23T12:05:00Z",
    } satisfies IdentityDraftPersistenceReceipt);

    render(
      <IdentityDecisionBoundary
        comparison={comparison()}
        draftPersistence="SERVER"
        draftIdentity={draftIdentity}
        durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
        graphPlans={graphPlans}
        onDraftSave={onDraftSave}
        onSubmit={vi.fn().mockResolvedValue(receipt())}
        persistedDraft={null}
        record={record}
        workflow={workflow()}
      />,
    );

    expect(window.sessionStorage.getItem(draftStorageKey())).toBeNull();
    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "server will clear this edit" },
    });

    await waitFor(() =>
      expect(screen.getByTestId("identity-server-draft-status").getAttribute("data-status")).toBe(
        "CLEARED",
      ),
    );
    expect(window.sessionStorage.getItem(draftStorageKey())).toBeNull();
    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe("");
  });

  it("isolates session drafts by tenant, intake, match case, and actor", async () => {
    const first = renderBoundary();
    fireEvent.change(screen.getByTestId("identity-decision-reason"), {
      target: { value: "tenant A actor A only" },
    });
    await waitFor(() =>
      expect(window.sessionStorage.getItem(draftStorageKey())).toContain("tenant A actor A only"),
    );
    first.unmount();

    const isolatedScope: IdentityDraftScope = {
      ...draftIdentity,
      tenantId: "tenant-tw-002",
      actorId: "subject-reviewer",
    };
    render(
      <IdentityDecisionBoundary
        comparison={comparison()}
        draftIdentity={isolatedScope}
        durableDesktopHref="/w/expansion/listings/intake/intake-3011?section=identity"
        graphPlans={graphPlans}
        onSubmit={vi.fn().mockResolvedValue(receipt())}
        record={record}
        workflow={workflow({
          currentActor: reviewer,
          proposer: reviewer,
        })}
      />,
    );

    expect((screen.getByTestId("identity-decision-reason") as HTMLTextAreaElement).value).toBe("");
    expect(window.sessionStorage.getItem(draftStorageKey())).toContain("tenant A actor A only");
    expect(window.sessionStorage.getItem(draftStorageKey(isolatedScope))).not.toContain(
      "tenant A actor A only",
    );
  });
});
