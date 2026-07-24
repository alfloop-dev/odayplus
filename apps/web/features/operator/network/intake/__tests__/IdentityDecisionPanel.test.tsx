import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ListingCompareTable } from "../ListingCompareTable";
import { MatchEvidencePanel } from "../MatchEvidencePanel";
import { IdentityDecisionPanel } from "../IdentityDecisionPanel";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLElement | null = null;
let root: Root | null = null;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  if (root) {
    act(() => {
      root!.unmount();
    });
    root = null;
  }
  if (container) {
    container.remove();
    container = null;
  }
});

function render(ui: React.ReactNode) {
  act(() => {
    root!.render(ui);
  });
  return {
    rerender(newUi: React.ReactNode) {
      act(() => {
        root!.render(newUi);
      });
    },
  };
}

const screen = {
  getByTestId(testId: string): HTMLElement {
    const el = document.body.querySelector(`[data-testid="${testId}"]`);
    if (!el) {
      throw new Error(`Element with data-testid="${testId}" not found`);
    }
    return el as HTMLElement;
  },
};

const fireEvent = {
  click(element: HTMLElement) {
    act(() => {
      if (element instanceof HTMLInputElement && element.type === "checkbox") {
        const checkedSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "checked")?.set;
        if (checkedSetter) {
          checkedSetter.call(element, !element.checked);
        } else {
          element.checked = !element.checked;
        }
        element.dispatchEvent(new Event("click", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        element.click();
      }
    });
  },
  change(element: HTMLElement, { target: { value } }: { target: { value: string } }) {
    act(() => {
      if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
        const valueSetter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
        if (valueSetter) {
          valueSetter.call(element, value);
        } else {
          element.value = value;
        }
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  },
};

async function waitFor(fn: () => void | Promise<void>, timeout = 2000) {
  const start = Date.now();
  let lastError: any = null;
  while (Date.now() - start < timeout) {
    try {
      await act(async () => {
        await fn();
      });
      return;
    } catch (err) {
      lastError = err;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw lastError;
}

const expectAny = (val: any): any => {
  if (typeof val === "function" || (val && typeof val === "object" && "mock" in val)) {
    return expect(val);
  }
  return {
    toBeInTheDocument: () => expect(val).not.toBeNull(),
    toHaveTextContent: (text: string) => expect(val?.textContent).toContain(text),
    toBeDisabled: () => expect((val as HTMLButtonElement)?.disabled).toBe(true),
    not: {
      toBeDisabled: () => expect((val as HTMLButtonElement)?.disabled).toBe(false),
    },
  };
};

// Sample AssistedIntake fixtures covering all canonical outcomes
const sampleRecordPossibleMatch: any = {
  id: "IN-3011",
  sourceId: "591_123456",
  originalUrl: "https://rent.591.com.tw/123456",
  canonicalUrl: "https://rent.591.com.tw/123456",
  submitter: "OP-100 (John Proposer)",
  capturedAt: "2026-07-20T10:00:00Z",
  owner: "OP-100",
  heatZoneId: "HZ-TPE-XINYI",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "核准單頁讀取",
  policyReason: "核准領域白名單",
  stage: "NEEDS_REVIEW",
  parserVersion: "v2.1.0",
  snapshotId: "SNAP-9001",
  rawSnapshot: null,
  correlationId: "CORR-778899",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "POSSIBLE_MATCH",
    outcomeLabel: "疑似重複",
    confidence: 0.78,
    summary: "地址高度比對成功，但租金由 35,000 變更為 38,000，樓層登記有些許差異。",
    agreeingSignals: [
      { key: "address", label: "地址", agrees: true, detail: "台北市信義區松高路12號 (100% 吻合)" },
      { key: "area", label: "面積", agrees: true, detail: "45 坪 (吻合)" },
    ],
    contradictingSignals: [
      { key: "rent", label: "租金", agrees: false, detail: "目標 $35,000 vs 本次 $38,000" },
      { key: "floor", label: "樓層", agrees: false, detail: "目標 5F vs 本次 5F-2" },
    ],
  },
  parsedFields: {
    address: { key: "address", label: "地址", sourceValue: "台北市信義區松高路12號", normalizedValue: "台北市信義區松高路12號", correctedValue: null, correctionReason: null, identity: true, lowConfidence: false },
    rent: { key: "rent", label: "租金", sourceValue: "38000", normalizedValue: "38000", correctedValue: null, correctionReason: null, identity: false, lowConfidence: true },
    area: { key: "area", label: "坪數", sourceValue: "45", normalizedValue: "45", correctedValue: null, correctionReason: null, identity: true, lowConfidence: false },
    floor: { key: "floor", label: "樓層", sourceValue: "5F-2", normalizedValue: "5F-2", correctedValue: null, correctionReason: null, identity: true, lowConfidence: false },
  },
  auditEvents: [
    { id: "EV-1", occurredAt: "2026-07-20T10:00:00Z", actorName: "System", actorRoleId: "system", action: "INTAKE_CREATED", targetId: "IN-3011", message: "Intake created", correlationId: "CORR-778899" },
  ],
};

const sampleRecordExactDuplicate: any = {
  ...sampleRecordPossibleMatch,
  id: "IN-3012",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "EXACT_DUPLICATE",
    outcomeLabel: "完全重複",
    confidence: 0.99,
    summary: "全欄位與網址完全相同，判定為完全重複。",
    agreeingSignals: [
      { key: "address", label: "地址", agrees: true, detail: "完全相同" },
      { key: "url", label: "網址", agrees: true, detail: "完全相同" },
    ],
    contradictingSignals: [],
  },
};

const sampleRecordRevision: any = {
  ...sampleRecordPossibleMatch,
  id: "IN-3013",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "REVISION",
    outcomeLabel: "物件版本更新",
    confidence: 0.92,
    summary: "同一物件更新價格與圖片，判定為物件新版本。",
    agreeingSignals: [
      { key: "address", label: "地址", agrees: true, detail: "完全相同" },
      { key: "sourceId", label: "來源 ID", agrees: true, detail: "同物件號" },
    ],
    contradictingSignals: [
      { key: "rent", label: "租金", agrees: false, detail: "價格調降 2,000" },
    ],
  },
};

const sampleRecordNew: any = {
  ...sampleRecordPossibleMatch,
  id: "IN-3014",
  stage: "READY",
  matchResult: {
    targetListingId: "",
    outcome: "NEW",
    outcomeLabel: "新物件",
    confidence: 0.95,
    summary: "未於既有網絡庫中比對到相關物件，判定為全新物件。",
    agreeingSignals: [],
    contradictingSignals: [],
  },
};

const sampleRecordQuarantined: any = {
  ...sampleRecordPossibleMatch,
  id: "IN-3015",
  stage: "QUARANTINED",
  matchResult: {
    targetListingId: "",
    outcome: "QUARANTINED",
    outcomeLabel: "已隔離",
    confidence: 0.40,
    summary: "來源內容存在衝突且遭安全隔離。",
    agreeingSignals: [],
    contradictingSignals: [
      { key: "identity", label: "身份權限", agrees: false, detail: "屬受保護刊登" },
    ],
  },
};

const targetListing = {
  id: "LST-1002",
  sourceId: "591_123456",
  canonicalUrl: "https://rent.591.com.tw/123456",
  address: "台北市信義區松高路12號",
  area: "45",
  floor: "5F",
  listingType: "—",
  rent: "35000",
  status: "NEEDS_REVIEW",
};

describe("Assisted Intake UI — Identity & Match Components Suite (ODP-INTAKE-UX-MATCH-001)", () => {
  describe("ListingCompareTable Component", () => {
    it("renders canonical match outcome badge and comparison table headers", () => {
      render(<ListingCompareTable record={sampleRecordPossibleMatch} targetListing={targetListing} />);

      expectAny(screen.getByTestId("listing-compare-table")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-outcome-badge")).toHaveTextContent("POSSIBLE_MATCH");
      expectAny(screen.getByTestId("intake-change-summary")).toBeInTheDocument();
      expectAny(screen.getByTestId("intake-change-summary")).toHaveTextContent("比對結果為 疑似重複 (POSSIBLE_MATCH)");
    });

    it("renders required comparison fields (sourceId, url, address, area, floor, rent, status, confidence, contradictions)", () => {
      render(<ListingCompareTable record={sampleRecordPossibleMatch} targetListing={targetListing} />);

      expectAny(screen.getByTestId("compare-row-sourceId")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-canonicalUrl")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-address")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-area")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-floor")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-rent")).toBeInTheDocument();
      expectAny(screen.getByTestId("compare-row-status")).toBeInTheDocument();

      // Check signal markers
      expectAny(screen.getByTestId("signal-con-rent")).toHaveTextContent("▲ 矛盾");
      expectAny(screen.getByTestId("signal-con-floor")).toHaveTextContent("▲ 矛盾");
      expectAny(screen.getByTestId("signal-match-address")).toHaveTextContent("✓ 一致");

      // Check summary metrics
      expectAny(screen.getByTestId("compare-confidence-val")).toHaveTextContent("78.0%");
      expectAny(screen.getByTestId("compare-agree-count")).toHaveTextContent("2 項");
      expectAny(screen.getByTestId("compare-con-count")).toHaveTextContent("2 項");
    });
  });

  describe("MatchEvidencePanel Component", () => {
    it("renders canonical codes: NEW, EXACT_DUPLICATE, REVISION, POSSIBLE_MATCH, QUARANTINED", () => {
      const { rerender } = render(<MatchEvidencePanel record={sampleRecordNew} />);
      expectAny(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("NEW");

      rerender(<MatchEvidencePanel record={sampleRecordExactDuplicate} />);
      expectAny(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("EXACT_DUPLICATE");

      rerender(<MatchEvidencePanel record={sampleRecordRevision} />);
      expectAny(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("REVISION");

      rerender(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expectAny(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("POSSIBLE_MATCH");

      rerender(<MatchEvidencePanel record={sampleRecordQuarantined} />);
      expectAny(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("QUARANTINED");
    });

    it("displays strict no-auto-merge warning banner for POSSIBLE_MATCH", () => {
      render(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expectAny(screen.getByTestId("no-auto-merge-warning")).toBeInTheDocument();
      expectAny(screen.getByTestId("no-auto-merge-warning")).toHaveTextContent("系統絕不自動合併疑似重複物件 (POSSIBLE_MATCH)");
    });

    it("renders agreeing and contradicting signals with accessible labels", () => {
      render(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expectAny(screen.getByTestId("agreeing-signals-list")).toHaveTextContent("地址");
      expectAny(screen.getByTestId("contradicting-signals-list")).toHaveTextContent("租金");
      expectAny(screen.getByTestId("match-evidence-sr-summary")).toBeInTheDocument();
    });
  });

  describe("IdentityDecisionPanel Component", () => {
    it("renders main identity decision panel with summary, compare, and graph tabs", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} />);

      expectAny(screen.getByTestId("identity-decision-panel")).toBeInTheDocument();
      expectAny(screen.getByTestId("identity-match-badge")).toHaveTextContent("POSSIBLE_MATCH");
      expectAny(screen.getByTestId("tab-summary-btn")).toBeInTheDocument();
      expectAny(screen.getByTestId("tab-compare-btn")).toBeInTheDocument();
      expectAny(screen.getByTestId("tab-graph-btn")).toBeInTheDocument();
    });

    it("strictly prohibits auto-merge on POSSIBLE_MATCH and requires manual decision options", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} />);

      expectAny(screen.getByTestId("identity-no-auto-merge-note")).toBeInTheDocument();
      expectAny(screen.getByTestId("btn-decision-create")).toBeInTheDocument();
      expectAny(screen.getByTestId("btn-decision-revise")).toBeInTheDocument();
      expectAny(screen.getByTestId("btn-decision-dup")).toBeInTheDocument();
      expectAny(screen.getByTestId("btn-decision-steward")).toBeInTheDocument();
    });

    it("enforces dual-actor authorization and renders SELF_REVIEW_DENIED when proposer === reviewer", () => {
      render(
        <IdentityDecisionPanel
          record={sampleRecordPossibleMatch}
          proposerId="OP-100"
          reviewerId="OP-100"
          requireSecondActor={true}
        />
      );

      expectAny(screen.getByTestId("self-review-denied")).toBeInTheDocument();
      expectAny(screen.getByTestId("self-review-denied")).toHaveTextContent("SELF_REVIEW_DENIED");
      expectAny(screen.getByTestId("self-review-denied-notice")).toHaveTextContent("案件提案者與最終審查者不能為同一人 (OP-100)");

      // Submit button should be disabled
      expectAny(screen.getByTestId("identity-submit-btn")).toBeDisabled();
    });

    it("allows graph mode switching (merge, split, unmerge, reversal) and displays lineage impact", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} proposerId="OP-100" reviewerId="OP-200" />);

      // Switch to split
      fireEvent.click(screen.getByTestId("graph-mode-split"));
      expectAny(screen.getByTestId("identity-risk-summary")).toHaveTextContent("拆分模式");

      // Switch to unmerge
      fireEvent.click(screen.getByTestId("graph-mode-unmerge"));
      expectAny(screen.getByTestId("identity-risk-summary")).toHaveTextContent("反轉合併");

      // Switch to reversal
      fireEvent.click(screen.getByTestId("graph-mode-reversal"));
      expectAny(screen.getByTestId("identity-risk-summary")).toHaveTextContent("歷程回滾");
    });

    it("requires reason and risk acknowledgement checkbox before submit", async () => {
      const handleSubmit = vi.fn();
      render(
        <IdentityDecisionPanel
          record={sampleRecordPossibleMatch}
          proposerId="OP-100"
          reviewerId="OP-200"
          onSubmitDecision={handleSubmit}
        />
      );

      const submitBtn = screen.getByTestId("identity-submit-btn");
      expectAny(submitBtn).toBeDisabled();

      // Enter reason
      const reasonInput = screen.getByTestId("identity-decision-reason");
      fireEvent.change(reasonInput, { target: { value: "實地核對無誤，確定合併為同物件。" } });

      // Tick risk ack
      const riskAck = screen.getByTestId("identity-risk-ack");
      fireEvent.click(riskAck);

      expectAny(submitBtn).not.toBeDisabled();
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expectAny(handleSubmit).toHaveBeenCalledWith(
          expect.objectContaining({
            kind: "create",
            reason: "實地核對無誤，確定合併為同物件。",
            proposerId: "OP-100",
            reviewerId: "OP-200",
            riskAcknowledged: true,
          })
        );
      });
    });

    it("handles concurrency conflict (409 OWNER_CONFLICT), preserves inputs, and allows refresh retry", () => {
      const apiError = {
        status: 409,
        code: "ODP-INTAKE-CONFLICT",
        summary: "版本衝突 (409 OWNER_CONFLICT)",
        nextAction: "請重新整理",
        correlationId: "CORR-999",
        occurredAt: "2026-07-20T10:05:00Z",
        retryable: false,
      };

      const handleRefresh = vi.fn();

      render(
        <IdentityDecisionPanel
          record={sampleRecordPossibleMatch}
          proposerId="OP-100"
          reviewerId="OP-200"
          error={apiError}
          onRefresh={handleRefresh}
        />
      );

      expectAny(screen.getByTestId("identity-conflict-banner")).toBeInTheDocument();
      expectAny(screen.getByTestId("identity-conflict-banner")).toHaveTextContent("409 OWNER_CONFLICT");

      const refreshBtn = screen.getByTestId("identity-conflict-refresh-btn");
      fireEvent.click(refreshBtn);

      expectAny(handleRefresh).toHaveBeenCalled();
    });

    it("renders durable receipt when decision succeeds", async () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} proposerId="OP-100" reviewerId="OP-200" />);

      // Fill reason and tick risk
      fireEvent.change(screen.getByTestId("identity-decision-reason"), { target: { value: "確認修訂" } });
      fireEvent.click(screen.getByTestId("identity-risk-ack"));

      fireEvent.click(screen.getByTestId("identity-submit-btn"));

      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      await waitFor(() => {
        expectAny(screen.getByTestId("identity-durable-receipt")).toBeInTheDocument();
        expectAny(screen.getByTestId("receipt-id-val")).toHaveTextContent("RCPT-MATCH-");
        expectAny(screen.getByTestId("receipt-actor-val")).toBeInTheDocument();
      });
    });
  });
});
