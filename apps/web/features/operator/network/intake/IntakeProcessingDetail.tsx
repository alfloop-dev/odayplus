"use client";

import { useMemo, useState } from "react";
import type {
  AssignmentReceipt,
  AssistedIntake,
  AuditReference,
  CorrectionReceipt,
  DecisionReceipt,
  FieldValue,
  IntakeSubmissionReceipt,
  JobReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
  TransitionReceipt,
} from "@oday-plus/openapi-client";
import { AssignmentSlaSummary } from "./AssignmentSlaSummary";
import { DurableReceiptPanel } from "./DurableReceiptPanel";
import { EvidencePanel } from "./EvidencePanel";
import {
  IdentityDecisionPanel,
  type IdentityDecisionResultReceipt,
  type IdentityGraphMode,
} from "./IdentityDecisionPanel";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { IntakeErrorRecovery } from "./IntakeErrorRecovery";
import type { IntakeApiError } from "./intakeClient";
import { IntakeStageTimeline } from "./IntakeStageTimeline";
import {
  PromotionReviewPanel,
  type PromotionActor,
  type PromotionRequestInput,
  type PromotionReviewInput,
} from "./PromotionReviewPanel";
import type { ScoreReplayInput } from "./SiteScoreJobStatus";
import {
  decisionOptions,
  matchLabel,
  matchTone,
  policyLabel,
  policyTone,
  stageLabel,
  stageTone,
  type IntakeDecisionKind,
} from "./intakeTypes";

export type IntakeDetailTab =
  | "timeline"
  | "evidence"
  | "identity"
  | "assignment"
  | "receipts"
  | "promotion"
  | "error";

export type IntakeProcessingDetailProps = {
  record: AssistedIntake;
  busy?: boolean;
  canCorrect?: boolean;
  canDecide?: boolean;
  canRetry?: boolean;
  canReplay?: boolean;
  error?: IntakeApiError | null;
  history?: TransitionReceipt[];
  jobs?: JobReceipt[];
  sla?: SlaReceipt;
  fields?: FieldValue[];
  auditReferences?: AuditReference[];
  submissionReceipt?: IntakeSubmissionReceipt;
  assignmentReceipt?: AssignmentReceipt;
  decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt;
  slaReceipt?: SlaReceipt;
  correctionReceipts?: CorrectionReceipt[];
  onClose: () => void;
  presentation?: "dialog" | "page";
  activeTab?: IntakeDetailTab;
  onActiveTabChange?: (tab: IntakeDetailTab) => void;
  compareTargetId?: string | null;
  onDecide?: (kind: IntakeDecisionKind) => void;
  onIdentityDecision?: (input: {
    kind: IntakeDecisionKind;
    graphMode: IdentityGraphMode;
    reason: string;
    riskSummary: string;
    riskAcknowledged: boolean;
    proposerId: string;
    reviewerId: string;
    ifMatchVersion?: string;
  }) => Promise<IdentityDecisionResultReceipt | void> | void;
  onOpenFix?: (fieldKey: string) => void;
  onRetry?: (overrides?: { overrideRetryBudget?: boolean; riskAcknowledged?: boolean }) => void;
  onReplayDlq?: (jobId?: string) => void;
  onCancel?: () => void;
  onOverride?: (reason: string) => void;
  onClaimAssignment?: () => void;
  onOpenTransfer?: () => void;
  onOpenPause?: () => void;
  onResumeSla?: () => void;
  onRefresh?: () => void;
  testId?: string;
  // ---- Candidate promotion saga slice (ODP-INTAKE-UX-PROMOTION-001) -------
  // All optional: when `currentOperator` + `gateSnapshotSha256` are provided
  // the detail exposes the promotion tab; existing callers are unchanged.
  promotion?: PromotionDecisionReceipt | null;
  scoreJob?: JobReceipt | null;
  currentOperator?: PromotionActor;
  gateSnapshotSha256?: string;
  promotionBusy?: boolean;
  promotionError?: IntakeApiError | null;
  promotionHydrated?: boolean;
  promotionIdempotencyReplayed?: boolean;
  canRequestPromotion?: boolean;
  canReviewPromotion?: boolean;
  canReplayScore?: boolean;
  onRequestPromotion?: (input: PromotionRequestInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReviewPromotion?: (input: PromotionReviewInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReplayScore?: (input: ScoreReplayInput) => Promise<JobReceipt | void> | void;
  onLookupPromotionDecision?: () => void;
};

export function IntakeProcessingDetail({
  record,
  busy = false,
  canCorrect = true,
  canDecide = true,
  canRetry = true,
  canReplay = true,
  error = null,
  history = [],
  jobs = [],
  sla,
  fields,
  auditReferences = [],
  submissionReceipt,
  assignmentReceipt,
  decisionReceipt,
  slaReceipt,
  correctionReceipts = [],
  onClose,
  presentation = "dialog",
  activeTab: controlledActiveTab,
  onActiveTabChange,
  compareTargetId,
  onDecide,
  onIdentityDecision,
  onOpenFix,
  onRetry,
  onReplayDlq,
  onCancel,
  onOverride,
  onClaimAssignment,
  onOpenTransfer,
  onOpenPause,
  onResumeSla,
  onRefresh,
  testId = "intake-processing-detail",
  promotion = null,
  scoreJob = null,
  currentOperator,
  gateSnapshotSha256,
  promotionBusy = false,
  promotionError = null,
  promotionHydrated = true,
  promotionIdempotencyReplayed = false,
  canRequestPromotion = false,
  canReviewPromotion = false,
  canReplayScore = false,
  onRequestPromotion,
  onReviewPromotion,
  onReplayScore,
  onLookupPromotionDecision,
}: IntakeProcessingDetailProps) {
  const isFailedOrQuarantined = record.stage === "FAILED" || record.stage === "QUARANTINED" || Boolean(error);
  const [internalActiveTab, setInternalActiveTab] = useState<IntakeDetailTab>(
    isFailedOrQuarantined ? "error" : "timeline",
  );
  const [maskedView, setMaskedView] = useState(false);
  const activeTab = controlledActiveTab ?? internalActiveTab;
  const setActiveTab = (tab: IntakeDetailTab) => {
    setInternalActiveTab(tab);
    onActiveTabChange?.(tab);
  };

  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;

  // The promotion tab exists only when the container wired the saga slice —
  // and only once the intake is READY (or a decision receipt already exists),
  // mirroring the server's WORKFLOW_STATE_DENIED gate.
  const promotionMounted = Boolean(
    promotionHydrated && currentOperator && gateSnapshotSha256 !== undefined,
  );
  const promotionAvailable = promotionMounted && (record.stage === "READY" || Boolean(promotion));

  return (
    <IntakeDialogShell
      ariaLabel={`收件處理詳情 ${record.id}`}
      className={styles.panelWide}
      dismissible={!busy && !promotionBusy}
      onClose={onClose}
      presentation={presentation}
      screenLabel={presentation === "page" ? "Page 收件處理詳情" : "Dialog 收件處理詳情"}
      testId={testId}
    >
      {/* Header */}
      <div className={styles.dialogHead}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <span className={styles.dialogTitle}>收件處理詳情 Intake Processing Detail</span>
          <span className={styles.rowId} data-testid="intake-detail-id">
            {record.id}
          </span>
          <span className={styles.chip} data-testid="intake-detail-stage" data-tone={stageTone(record.stage)}>
            {stageLabel(record.stage)}
          </span>
          {outcome && (
            <span className={styles.chip} data-testid="intake-detail-match" data-tone={matchTone(outcome)}>
              {matchLabel(outcome)}
            </span>
          )}
          <span className={styles.chip} data-tone={policyTone(record.policy)}>
            {policyLabel(record.policy)}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginLeft: "auto" }}>
          <span className={styles.deepLink} data-testid="intake-detail-deeplink">
            /w/expansion/listings/intake/{record.id}
          </span>
          <a
            className={styles.secondaryButton}
            data-testid="intake-open-source-link"
            href={record.originalUrl}
            rel="noopener noreferrer"
            target="_blank"
            title="在新視窗開啟來源；目前收件頁面與操作狀態會保留"
          >
            開啟來源
          </a>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className={styles.secondaryButton}
              style={{ padding: "3px 8px", fontSize: "11px" }}
              data-testid="intake-refresh-button"
            >
              🔄 重新整理
            </button>
          )}
          <button
            aria-label={presentation === "page" ? "返回 Listing 收件匣" : "關閉"}
            className={styles.dialogClose}
            disabled={busy || promotionBusy}
            onClick={onClose}
            type="button"
          >
            {presentation === "page" ? "返回收件匣" : "×"}
          </button>
        </div>
      </div>

      {/* Sub-nav Tabs */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          padding: "8px 16px",
          background: "#f8fafc",
          borderBottom: "1px solid #e2e8f0",
        }}
        data-testid="intake-detail-tabs"
      >
        <button
          type="button"
          onClick={() => setActiveTab("timeline")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "timeline" ? 700 : 500,
            background: activeTab === "timeline" ? "#ffffff" : "transparent",
            color: activeTab === "timeline" ? "#2e3a97" : "#64748b",
            border: activeTab === "timeline" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-timeline"
        >
          ⏱️ 階段與時序 (Timeline)
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("evidence")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "evidence" ? 700 : 500,
            background: activeTab === "evidence" ? "#ffffff" : "transparent",
            color: activeTab === "evidence" ? "#2e3a97" : "#64748b",
            border: activeTab === "evidence" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-evidence"
        >
          📋 證據與比對 (Evidence)
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("identity")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "identity" ? 700 : 500,
            background: activeTab === "identity" ? "#ffffff" : "transparent",
            color: activeTab === "identity" ? "#2e3a97" : "#64748b",
            border: activeTab === "identity" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-identity"
        >
          身分比對與決策 (Identity)
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("assignment")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "assignment" ? 700 : 500,
            background: activeTab === "assignment" ? "#ffffff" : "transparent",
            color: activeTab === "assignment" ? "#2e3a97" : "#64748b",
            border: activeTab === "assignment" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-assignment"
        >
          指派與 SLA (Assignment)
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("receipts")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "receipts" ? 700 : 500,
            background: activeTab === "receipts" ? "#ffffff" : "transparent",
            color: activeTab === "receipts" ? "#2e3a97" : "#64748b",
            border: activeTab === "receipts" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-receipts"
        >
          📜 持久化收據 (Receipts)
        </button>

        {promotionAvailable && (
          <button
            type="button"
            onClick={() => setActiveTab("promotion")}
            style={{
              padding: "6px 12px",
              borderRadius: "6px",
              fontSize: "11.5px",
              fontWeight: activeTab === "promotion" ? 700 : 500,
              background: activeTab === "promotion" ? "#ffffff" : "transparent",
              color: activeTab === "promotion" ? "#2e3a97" : "#64748b",
              border: activeTab === "promotion" ? "1px solid #cbd5e1" : "none",
              cursor: "pointer",
            }}
            data-testid="tab-promotion"
          >
            🚀 晉升審查 (Promotion)
          </button>
        )}

        <button
          type="button"
          onClick={() => setActiveTab("error")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontSize: "11.5px",
            fontWeight: activeTab === "error" ? 700 : 500,
            background: activeTab === "error" ? "#fff5f5" : "transparent",
            color: activeTab === "error" ? "#b91c1c" : "#64748b",
            border: activeTab === "error" ? "1px solid #fca5a5" : "none",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "4px",
          }}
          data-testid="tab-error"
        >
          <span>⚠️ 異常恢復 (Error Recovery)</span>
          {isFailedOrQuarantined && (
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#dc2626" }} />
          )}
        </button>

        {/* Data Purpose & Masking Control */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", color: "#64748b" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "4px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={maskedView}
              onChange={(e) => setMaskedView(e.target.checked)}
              data-testid="intake-masking-checkbox"
            />
            <span>資安遮蔽 (Purpose Binding Masking)</span>
          </label>
        </div>
      </div>

      {/* Main Body */}
      <div className={styles.dialogBody} style={{ padding: "16px" }}>
        {/* Prominent Error Recovery Banner if failed */}
        {isFailedOrQuarantined && activeTab !== "error" && (
          <div
            style={{
              padding: "8px 12px",
              borderRadius: "8px",
              background: "#fef2f2",
              border: "1px solid #fca5a5",
              color: "#991b1b",
              fontSize: "11.5px",
              marginBottom: "12px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
            data-testid="intake-error-banner"
          >
            <span>⚠️ 此收件目前處於異常或隔離狀態 ({record.stage})，請至異常恢復分頁進行處置。</span>
            <button
              type="button"
              onClick={() => setActiveTab("error")}
              className={styles.secondaryButton}
              style={{ padding: "2px 8px", fontSize: "10.5px", color: "#b91c1c", borderColor: "#fca5a5" }}
            >
              前往異常處置 →
            </button>
          </div>
        )}

        {/* Tab 1: Timeline */}
        {activeTab === "timeline" && (
          <IntakeStageTimeline
            record={record}
            history={history}
            jobs={jobs}
            sla={sla}
            canReplay={canReplay}
            onReplayJob={onReplayDlq}
            onCancel={onCancel}
          />
        )}

        {/* Tab 2: Evidence */}
        {activeTab === "evidence" && (
          <EvidencePanel
            record={record}
            fields={fields}
            auditReferences={auditReferences}
            onOpenFix={onOpenFix}
            maskedView={maskedView}
          />
        )}

        {/* Full desktop compare and reversible identity decision. */}
        {activeTab === "identity" && (
          <IdentityDecisionPanel
            busy={busy}
            currentOperator={currentOperator}
            error={error}
            onRefresh={onRefresh}
            onSubmitDecision={
              onIdentityDecision ??
              (async () => {
                throw new Error("目前無可用的身分決策命令；請重新整理或聯絡資料管理員。");
              })
            }
            record={record}
            targetListing={{
              id: compareTargetId ?? record.matchResult?.targetListingId ?? undefined,
            }}
          />
        )}

        {/* Assignment/SLA is part of the durable production composition. */}
        {activeTab === "assignment" && (
          <AssignmentSlaSummary
            busy={busy}
            currentUserId={currentOperator?.id}
            onClaim={onClaimAssignment}
            onOpenPause={onOpenPause}
            onOpenTransfer={onOpenTransfer}
            onResume={onResumeSla}
            record={record}
            userRole={currentOperator?.role}
          />
        )}

        {/* Tab: Receipts */}
        {activeTab === "receipts" && (
          <DurableReceiptPanel
            record={record}
            submissionReceipt={submissionReceipt}
            assignmentReceipt={assignmentReceipt}
            decisionReceipt={decisionReceipt}
            slaReceipt={slaReceipt}
            correctionReceipts={correctionReceipts}
          />
        )}

        {/* Tab: Candidate promotion saga (UX-SCR-EXP-003F) */}
        {activeTab === "promotion" && !promotionHydrated && (
          <div
            aria-live="polite"
            className={styles.noteBox}
            data-testid="promotion-hydration-status"
            role="status"
          >
            正在載入既有晉升決策與工作收據…
          </div>
        )}

        {activeTab === "promotion" && promotionHydrated && !promotionAvailable && (
          <div className={styles.noteBox} data-testid="promotion-unavailable-state" role="status">
            此收件目前尚未符合晉升審查的工作流程條件。請先完成必要的人工作業。
          </div>
        )}

        {activeTab === "promotion" && promotionAvailable && currentOperator && (
          <PromotionReviewPanel
            busy={promotionBusy}
            canReplayScore={canReplayScore}
            canRequest={canRequestPromotion}
            canReview={canReviewPromotion}
            currentOperator={currentOperator}
            error={promotionError}
            gateSnapshotSha256={gateSnapshotSha256 ?? ""}
            idempotencyReplayed={promotionIdempotencyReplayed}
            onLookupDecision={onLookupPromotionDecision}
            onRefresh={onRefresh}
            onReplayScore={onReplayScore}
            onRequestPromotion={onRequestPromotion}
            onReviewPromotion={onReviewPromotion}
            promotion={promotion}
            record={record}
            scoreJob={scoreJob}
          />
        )}

        {/* Tab 4: Error Recovery */}
        {activeTab === "error" && (
          <IntakeErrorRecovery
            error={error}
            stage={record.stage}
            correlationId={record.correlationId}
            preservedInput={record.parsedFields ? Object.fromEntries(Object.values(record.parsedFields).map(f => [f.key, (f as any).value ?? f.normalizedValue ?? f.sourceValue])) : null}
            onRetry={onRetry}
            onReplayDlq={onReplayDlq}
            onCancel={onCancel}
            onOverride={onOverride}
            onCorrectInput={onOpenFix ? () => onOpenFix(Object.keys(record.parsedFields ?? {})[0] ?? "address_raw") : undefined}
          />
        )}
      </div>

      {/* Decision Actions Footer */}
      {options.length > 0 && onDecide && (
        <div
          style={{
            padding: "12px 16px",
            borderTop: "1px solid #e2e8f0",
            background: "#f8fafc",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
          data-testid="intake-detail-actions"
        >
          <div style={{ fontSize: "11px", fontWeight: 600, color: "#475569" }}>
            人工覆核決策 AFFORDANCES ({options.length} 可選動作)
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            {options.map((opt) => (
              <button
                key={opt.kind}
                type="button"
                onClick={() => onDecide(opt.kind)}
                disabled={busy || !canDecide}
                className={opt.kind === "create" ? styles.primaryButton : styles.secondaryButton}
                style={{ padding: "6px 14px", fontSize: "11.5px" }}
                data-testid={`decide-action-${opt.kind}`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </IntakeDialogShell>
  );
}
