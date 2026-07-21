import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import { ListingCompareTable } from "../ListingCompareTable";
import { MatchEvidencePanel } from "../MatchEvidencePanel";
import { IdentityDecisionPanel, type IdentityDecisionResultReceipt } from "../IdentityDecisionPanel";

// Sample AssistedIntake fixtures covering all canonical outcomes
const sampleRecordPossibleMatch: AssistedIntake = {
  id: "IN-3011",
  sourceId: "591_123456",
  originalUrl: "https://rent.591.com.tw/123456",
  canonicalUrl: "https://rent.591.com.tw/123456",
  submitter: "OP-100 (John Proposer)",
  capturedAt: "2026-07-20T10:00:00Z",
  owner: "OP-100",
  policy: "APPROVED_RETRIEVAL",
  policyLabel: "核准單頁讀取",
  policyReason: "核准領域白名單",
  stage: "NEEDS_REVIEW",
  parserVersion: "v2.1.0",
  snapshotId: "SNAP-9001",
  correlationId: "CORR-778899",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "POSSIBLE_MATCH",
    confidence: 0.78,
    summary: "地址高度比對成功，但租金由 35,000 變更為 38,000，樓層登記有些許差異。",
    agreeingSignals: [
      { key: "address", label: "地址", detail: "台北市信義區松高路12號 (100% 吻合)" },
      { key: "area", label: "面積", detail: "45 坪 (吻合)" },
    ],
    contradictingSignals: [
      { key: "rent", label: "租金", detail: "目標 $35,000 vs 本次 $38,000" },
      { key: "floor", label: "樓層", detail: "目標 5F vs 本次 5F-2" },
    ],
  },
  parsedFields: {
    address: { key: "address", label: "地址", sourceValue: "台北市信義區松高路12號", normalizedValue: "台北市信義區松高路12號", correctedValue: null, lowConfidence: false },
    rent: { key: "rent", label: "租金", sourceValue: "38000", normalizedValue: "38000", correctedValue: null, lowConfidence: true },
    area: { key: "area", label: "坪數", sourceValue: "45", normalizedValue: "45", correctedValue: null, lowConfidence: false },
    floor: { key: "floor", label: "樓層", sourceValue: "5F-2", normalizedValue: "5F-2", correctedValue: null, lowConfidence: false },
  },
  auditEvents: [
    { id: "EV-1", occurredAt: "2026-07-20T10:00:00Z", actorName: "System", actorRoleId: "system", message: "Intake created" },
  ],
};

const sampleRecordExactDuplicate: AssistedIntake = {
  ...sampleRecordPossibleMatch,
  id: "IN-3012",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "EXACT_DUPLICATE",
    confidence: 0.99,
    summary: "全欄位與網址完全相同，判定為完全重複。",
    agreeingSignals: [
      { key: "address", label: "地址", detail: "完全相同" },
      { key: "url", label: "網址", detail: "完全相同" },
    ],
    contradictingSignals: [],
  },
};

const sampleRecordRevision: AssistedIntake = {
  ...sampleRecordPossibleMatch,
  id: "IN-3013",
  matchResult: {
    targetListingId: "LST-1002",
    outcome: "REVISION",
    confidence: 0.92,
    summary: "同一物件更新價格與圖片，判定為物件新版本。",
    agreeingSignals: [
      { key: "address", label: "地址", detail: "完全相同" },
      { key: "sourceId", label: "來源 ID", detail: "同物件號" },
    ],
    contradictingSignals: [
      { key: "rent", label: "租金", detail: "價格調降 2,000" },
    ],
  },
};

const sampleRecordNew: AssistedIntake = {
  ...sampleRecordPossibleMatch,
  id: "IN-3014",
  stage: "READY",
  matchResult: {
    targetListingId: "",
    outcome: "NEW",
    confidence: 0.95,
    summary: "未於既有網絡庫中比對到相關物件，判定為全新物件。",
    agreeingSignals: [],
    contradictingSignals: [],
  },
};

const sampleRecordQuarantined: AssistedIntake = {
  ...sampleRecordPossibleMatch,
  id: "IN-3015",
  stage: "QUARANTINED",
  matchResult: {
    targetListingId: "",
    outcome: "QUARANTINED",
    confidence: 0.40,
    summary: "來源內容存在衝突且遭安全隔離。",
    agreeingSignals: [],
    contradictingSignals: [
      { key: "identity", label: "身份權限", detail: "屬受保護刊登" },
    ],
  },
};

describe("Assisted Intake UI — Identity & Match Components Suite (ODP-INTAKE-UX-MATCH-001)", () => {
  describe("ListingCompareTable Component", () => {
    it("renders canonical match outcome badge and comparison table headers", () => {
      render(<ListingCompareTable record={sampleRecordPossibleMatch} />);

      expect(screen.getByTestId("listing-compare-table")).toBeInTheDocument();
      expect(screen.getByTestId("compare-outcome-badge")).toHaveTextContent("POSSIBLE_MATCH");
      expect(screen.getByTestId("intake-change-summary")).toBeInTheDocument();
      expect(screen.getByTestId("intake-change-summary")).toHaveTextContent("比對結果為 疑似重複 (POSSIBLE_MATCH)");
    });

    it("renders required comparison fields (sourceId, url, address, area, floor, rent, status, confidence, contradictions)", () => {
      render(<ListingCompareTable record={sampleRecordPossibleMatch} />);

      expect(screen.getByTestId("compare-row-sourceId")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-canonicalUrl")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-address")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-area")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-floor")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-rent")).toBeInTheDocument();
      expect(screen.getByTestId("compare-row-status")).toBeInTheDocument();

      // Check signal markers
      expect(screen.getByTestId("signal-con-rent")).toHaveTextContent("▲ 矛盾");
      expect(screen.getByTestId("signal-con-floor")).toHaveTextContent("▲ 矛盾");
      expect(screen.getByTestId("signal-match-address")).toHaveTextContent("✓ 一致");

      // Check summary metrics
      expect(screen.getByTestId("compare-confidence-val")).toHaveTextContent("78.0%");
      expect(screen.getByTestId("compare-agree-count")).toHaveTextContent("2 項");
      expect(screen.getByTestId("compare-con-count")).toHaveTextContent("2 項");
    });
  });

  describe("MatchEvidencePanel Component", () => {
    it("renders canonical codes: NEW, EXACT_DUPLICATE, REVISION, POSSIBLE_MATCH, QUARANTINED", () => {
      const { rerender } = render(<MatchEvidencePanel record={sampleRecordNew} />);
      expect(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("NEW");

      rerender(<MatchEvidencePanel record={sampleRecordExactDuplicate} />);
      expect(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("EXACT_DUPLICATE");

      rerender(<MatchEvidencePanel record={sampleRecordRevision} />);
      expect(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("REVISION");

      rerender(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expect(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("POSSIBLE_MATCH");

      rerender(<MatchEvidencePanel record={sampleRecordQuarantined} />);
      expect(screen.getByTestId("match-outcome-canonical-badge")).toHaveTextContent("QUARANTINED");
    });

    it("displays strict no-auto-merge warning banner for POSSIBLE_MATCH", () => {
      render(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expect(screen.getByTestId("no-auto-merge-warning")).toBeInTheDocument();
      expect(screen.getByTestId("no-auto-merge-warning")).toHaveTextContent("系統絕不自動合併疑似重複物件 (POSSIBLE_MATCH)");
    });

    it("renders agreeing and contradicting signals with accessible labels", () => {
      render(<MatchEvidencePanel record={sampleRecordPossibleMatch} />);
      expect(screen.getByTestId("agreeing-signals-list")).toHaveTextContent("地址");
      expect(screen.getByTestId("contradicting-signals-list")).toHaveTextContent("租金");
      expect(screen.getByTestId("match-evidence-sr-summary")).toBeInTheDocument();
    });
  });

  describe("IdentityDecisionPanel Component", () => {
    it("renders main identity decision panel with summary, compare, and graph tabs", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} />);

      expect(screen.getByTestId("identity-decision-panel")).toBeInTheDocument();
      expect(screen.getByTestId("identity-match-badge")).toHaveTextContent("POSSIBLE_MATCH");
      expect(screen.getByTestId("tab-summary-btn")).toBeInTheDocument();
      expect(screen.getByTestId("tab-compare-btn")).toBeInTheDocument();
      expect(screen.getByTestId("tab-graph-btn")).toBeInTheDocument();
    });

    it("strictly prohibits auto-merge on POSSIBLE_MATCH and requires manual decision options", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} />);

      expect(screen.getByTestId("identity-no-auto-merge-note")).toBeInTheDocument();
      expect(screen.getByTestId("btn-decision-create")).toBeInTheDocument();
      expect(screen.getByTestId("btn-decision-revise")).toBeInTheDocument();
      expect(screen.getByTestId("btn-decision-dup")).toBeInTheDocument();
      expect(screen.getByTestId("btn-decision-steward")).toBeInTheDocument();
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

      expect(screen.getByTestId("self-review-denied")).toBeInTheDocument();
      expect(screen.getByTestId("self-review-denied")).toHaveTextContent("SELF_REVIEW_DENIED");
      expect(screen.getByTestId("self-review-denied-notice")).toHaveTextContent("案件提案者與最終審查者不能為同一人 (OP-100)");

      // Submit button should be disabled
      expect(screen.getByTestId("identity-submit-btn")).toBeDisabled();
    });

    it("allows graph mode switching (merge, split, unmerge, reversal) and displays lineage impact", () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} proposerId="OP-100" reviewerId="OP-200" />);

      // Switch to split
      fireEvent.click(screen.getByTestId("graph-mode-split"));
      expect(screen.getByTestId("graph-lineage-impact")).toHaveTextContent("拆分模式");

      // Switch to unmerge
      fireEvent.click(screen.getByTestId("graph-mode-unmerge"));
      expect(screen.getByTestId("graph-lineage-impact")).toHaveTextContent("解鎖並撤銷");

      // Switch to reversal
      fireEvent.click(screen.getByTestId("graph-mode-reversal"));
      expect(screen.getByTestId("graph-lineage-impact")).toHaveTextContent("回滾");
    });

    it("requires reason and risk acknowledgement checkbox before submit", async () => {
      const handleSubmit = jest.fn();
      render(
        <IdentityDecisionPanel
          record={sampleRecordPossibleMatch}
          proposerId="OP-100"
          reviewerId="OP-200"
          onSubmitDecision={handleSubmit}
        />
      );

      const submitBtn = screen.getByTestId("identity-submit-btn");
      expect(submitBtn).toBeDisabled();

      // Enter reason
      const reasonInput = screen.getByTestId("identity-decision-reason");
      fireEvent.change(reasonInput, { target: { value: "實地核對無誤，確定合併為同物件。" } });

      // Tick risk ack
      const riskAck = screen.getByTestId("identity-risk-ack");
      fireEvent.click(riskAck);

      expect(submitBtn).not.toBeDisabled();
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(handleSubmit).toHaveBeenCalledWith(
          expect.objectContaining({
            kind: "revise",
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

      const handleRefresh = jest.fn();

      render(
        <IdentityDecisionPanel
          record={sampleRecordPossibleMatch}
          proposerId="OP-100"
          reviewerId="OP-200"
          error={apiError}
          onRefresh={handleRefresh}
        />
      );

      expect(screen.getByTestId("identity-conflict-banner")).toBeInTheDocument();
      expect(screen.getByTestId("identity-conflict-banner")).toHaveTextContent("409 OWNER_CONFLICT");

      const refreshBtn = screen.getByTestId("identity-conflict-refresh-btn");
      fireEvent.click(refreshBtn);

      expect(handleRefresh).toHaveBeenCalled();
    });

    it("renders durable receipt when decision succeeds", async () => {
      render(<IdentityDecisionPanel record={sampleRecordPossibleMatch} proposerId="OP-100" reviewerId="OP-200" />);

      // Fill reason and tick risk
      fireEvent.change(screen.getByTestId("identity-decision-reason"), { target: { value: "確認修訂" } });
      fireEvent.click(screen.getByTestId("identity-risk-ack"));

      fireEvent.click(screen.getByTestId("identity-submit-btn"));

      await waitFor(() => {
        expect(screen.getByTestId("identity-durable-receipt")).toBeInTheDocument();
        expect(screen.getByTestId("receipt-id-val")).toHaveTextContent("RCPT-MATCH-");
        expect(screen.getByTestId("receipt-action-val")).toBeInTheDocument();
      });
    });
  });
});
