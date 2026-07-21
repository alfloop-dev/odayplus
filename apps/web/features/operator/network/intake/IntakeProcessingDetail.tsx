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
import { DurableReceiptPanel } from "./DurableReceiptPanel";
import { EvidencePanel } from "./EvidencePanel";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { IntakeErrorRecovery } from "./IntakeErrorRecovery";
import type { IntakeApiError } from "./intakeClient";
import { IntakeStageTimeline } from "./IntakeStageTimeline";
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

export type IntakeDetailTab = "timeline" | "evidence" | "receipts" | "error";

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
  onDecide?: (kind: IntakeDecisionKind) => void;
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
  onDecide,
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
}: IntakeProcessingDetailProps) {
  const isFailedOrQuarantined = record.stage === "FAILED" || record.stage === "QUARANTINED" || Boolean(error);
  const [activeTab, setActiveTab] = useState<IntakeDetailTab>(isFailedOrQuarantined ? "error" : "timeline");
  const [maskedView, setMaskedView] = useState(false);

  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;

  return (
    <IntakeDialogShell
      ariaLabel={`收件處理詳情 ${record.id}`}
      className={styles.panelWide}
      onClose={onClose}
      screenLabel="Dialog 收件處理詳情"
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
            #intake/{record.id}
          </span>
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
          <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
            ×
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

        {/* Tab 3: Receipts */}
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

        {/* Tab 4: Error Recovery */}
        {activeTab === "error" && (
          <IntakeErrorRecovery
            error={error}
            stage={record.stage}
            correlationId={record.correlationId}
            preservedInput={record.parsedFields ? Object.fromEntries(Object.values(record.parsedFields).map(f => [f.key, f.value])) : null}
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
