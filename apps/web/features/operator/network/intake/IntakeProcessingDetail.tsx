"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
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
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { IntakeErrorRecovery } from "./IntakeErrorRecovery";
import type { AuthoritativeRecoveryContext } from "./evidenceContracts";
import type { IntakeApiError } from "./intakeClient";
import { IntakeStageTimeline } from "./IntakeStageTimeline";
import {
  ParsedDataReview,
  buildLegacyFieldReview,
} from "./ParsedDataReview";
import {
  fieldLineageDomId,
  type FieldCorrectionLineage,
} from "./FieldLineageRow";
import {
  PromotionReviewPanel,
  type PromotionActor,
  type PromotionRequestInput,
  type PromotionReviewInput,
} from "./PromotionReviewPanel";
import type { ScoreReplayInput } from "./SiteScoreJobStatus";
import type {
  IntakeLifecycleSnapshot,
  JobLifecycleReceipt,
  SlaLifecycleReceipt,
} from "./useIntakeLifecycle";
import type {
  AuthoritativeEvidenceVerification,
  AuthoritativeExportReceipt,
  AuthoritativeHumanDecisionEvidence,
  AuthoritativeSensitiveEvidenceAccess,
  AuthoritativeSourceEvidence,
} from "./evidenceContracts";
import {
  formatIntakeDateTime,
  type IntakeDetailPresentationFacts,
} from "./types";
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
  | "review"
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
  canReopen?: boolean;
  canReplay?: boolean;
  canCancelJob?: boolean;
  reopenDeniedReason?: string | null;
  error?: IntakeApiError | null;
  recovery?: AuthoritativeRecoveryContext | null;
  history?: TransitionReceipt[];
  jobs?: Array<JobReceipt | JobLifecycleReceipt>;
  sla?: SlaReceipt | SlaLifecycleReceipt;
  fields?: FieldValue[];
  auditReferences?: AuditReference[];
  submissionReceipt?: IntakeSubmissionReceipt;
  assignmentReceipt?: AssignmentReceipt;
  decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt;
  slaReceipt?: SlaReceipt;
  correctionReceipts?: CorrectionReceipt[];
  correctionsByField?: Readonly<
    Record<string, readonly FieldCorrectionLineage[]>
  >;
  detailFacts?: IntakeDetailPresentationFacts | null;
  sourceEvidence?: AuthoritativeSourceEvidence | null;
  evidenceAccess?: AuthoritativeSensitiveEvidenceAccess | null;
  humanDecisionEvidence?: AuthoritativeHumanDecisionEvidence | null;
  evidenceVerification?: AuthoritativeEvidenceVerification | null;
  exportReceipt?: AuthoritativeExportReceipt | null;
  lifecycle?: IntakeLifecycleSnapshot | null;
  onClose: () => void;
  presentation?: "dialog" | "page";
  activeTab?: IntakeDetailTab;
  onActiveTabChange?: (tab: IntakeDetailTab) => void;
  compareTargetId?: string | null;
  onDecide?: (kind: IntakeDecisionKind) => void;
  reviewSection?: ReactNode;
  identitySection?: ReactNode;
  auditSection?: ReactNode;
  onOpenFix?: (fieldKey: string) => void;
  onRetry?: (overrides?: { overrideRetryBudget?: boolean; riskAcknowledged?: boolean }) => void;
  onReopen?: () => void;
  onReplayDlq?: (jobId?: string) => void;
  onCancelJob?: (jobId: string) => void;
  onCancel?: () => void;
  onOverride?: (reason: string) => void;
  onClaimAssignment?: () => void;
  onOpenTransfer?: () => void;
  onOpenPause?: () => void;
  onResumeSla?: () => void;
  onEscalateAssignment?: () => void;
  onCompleteAssignment?: () => void;
  onRefresh?: () => void;
  testId?: string;
  // ---- Candidate promotion saga slice (ODP-INTAKE-UX-PROMOTION-001) -------
  // All optional: when `currentOperator` + `gateSnapshotSha256` are provided
  // the detail exposes the promotion tab; existing callers are unchanged.
  promotion?: PromotionDecisionReceipt | null;
  scoreJob?: JobReceipt | JobLifecycleReceipt | null;
  currentOperator?: PromotionActor;
  gateSnapshotSha256?: string;
  promotionBusy?: boolean;
  promotionError?: IntakeApiError | null;
  promotionHydrated?: boolean;
  promotionIdempotencyReplayed?: boolean;
  canRequestPromotion?: boolean;
  canReviewPromotion?: boolean;
  canExecutePromotion?: boolean;
  canReplayScore?: boolean;
  promotionRequestDeniedReason?: string | null;
  promotionReviewDeniedReason?: string | null;
  promotionExecuteDeniedReason?: string | null;
  promotionReplayDeniedReason?: string | null;
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
  canReopen = false,
  canReplay = true,
  canCancelJob = false,
  reopenDeniedReason = null,
  error = null,
  recovery = null,
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
  correctionsByField = {},
  detailFacts = null,
  sourceEvidence = null,
  evidenceAccess = null,
  humanDecisionEvidence = null,
  evidenceVerification = null,
  exportReceipt = null,
  lifecycle = null,
  onClose,
  presentation = "dialog",
  activeTab: controlledActiveTab,
  onActiveTabChange,
  compareTargetId,
  onDecide,
  reviewSection,
  identitySection,
  auditSection,
  onOpenFix,
  onRetry,
  onReopen,
  onReplayDlq,
  onCancel,
  onCancelJob,
  onOverride,
  onClaimAssignment,
  onOpenTransfer,
  onOpenPause,
  onResumeSla,
  onEscalateAssignment,
  onCompleteAssignment,
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
  canExecutePromotion = false,
  canReplayScore = false,
  promotionRequestDeniedReason = null,
  promotionReviewDeniedReason = null,
  promotionExecuteDeniedReason = null,
  promotionReplayDeniedReason = null,
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
  const [pendingFieldFocus, setPendingFieldFocus] = useState<string | null>(null);
  const activeTab = controlledActiveTab ?? internalActiveTab;
  const setActiveTab = (tab: IntakeDetailTab) => {
    setInternalActiveTab(tab);
    onActiveTabChange?.(tab);
  };

  useEffect(() => {
    if (activeTab !== "review" || !pendingFieldFocus) return;
    const target =
      document.getElementById(fieldLineageDomId(pendingFieldFocus)) ??
      [...document.querySelectorAll<HTMLElement>("[data-field-path]")].find(
        (element) => {
          const path = element.dataset.fieldPath;
          return path === pendingFieldFocus || path?.endsWith(`.${pendingFieldFocus}`);
        },
      );
    if (!target) return;
    target.focus();
    target.scrollIntoView?.({ block: "center" });
    setPendingFieldFocus(null);
  }, [activeTab, pendingFieldFocus]);

  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;
  const parsedReviewFields = useMemo(
    () =>
      buildLegacyFieldReview(record.parsedFields ?? {}, {
        sourceSnapshotId: record.snapshotId,
        correctionsByField,
      }),
    [correctionsByField, record.parsedFields, record.snapshotId],
  );

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
          {record.originalUrl ? (
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
          ) : (
            <span className={styles.metaSub} data-testid="intake-source-link-unavailable">
              來源網址已遮罩或不可用
            </span>
          )}
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

      <section
        aria-label="收件摘要"
        className={styles.sectionBox}
        data-testid="intake-detail-summary"
      >
        <div className={styles.sectionHead}>收件摘要 SUBMISSION SUMMARY</div>
        <dl className={styles.metaGrid}>
          <DetailValue
            label="Source"
            testId="intake-summary-source"
            value={detailFacts ? detailFacts.sourceId : record.sourceId}
          />
          <DetailValue
            label="Original URL"
            testId="intake-summary-original-url"
            value={detailFacts ? detailFacts.originalUrl : record.originalUrl}
          />
          <DetailValue
            label="Canonical URL"
            testId="intake-summary-canonical-url"
            value={detailFacts ? detailFacts.canonicalUrl : record.canonicalUrl}
          />
          <DetailValue
            label="Submitter"
            testId="intake-summary-submitter"
            value={detailFacts ? detailFacts.submitter : record.submitter}
          />
          <DetailValue
            label="Owner"
            testId="intake-summary-owner"
            value={detailFacts ? detailFacts.owner : record.owner}
          />
          <DetailTime
            label="Submitted at"
            testId="intake-summary-submitted-at"
            value={detailFacts?.submittedAt}
          />
          <DetailTime
            label="Updated at"
            testId="intake-summary-updated-at"
            value={detailFacts?.updatedAt}
          />
          <DetailValue
            label="Scope"
            testId="intake-summary-scope"
            value={detailFacts ? JSON.stringify(detailFacts.scope) : null}
          />
          <DetailValue
            label="Policy state"
            testId="intake-summary-policy-state"
            value={detailFacts ? detailFacts.policyState : record.policy}
          />
          <DetailValue
            label="Policy reason"
            testId="intake-summary-policy-reason"
            value={detailFacts ? detailFacts.policyReason : record.policyReason || null}
          />
          <DetailValue
            label="Policy version"
            testId="intake-summary-policy-version"
            value={detailFacts?.policyVersion}
          />
          <DetailTime
            label="Policy expires at"
            testId="intake-summary-policy-expires-at"
            value={detailFacts?.policyExpiresAt}
          />
          <DetailValue
            label="ETag"
            testId="intake-summary-etag"
            value={detailFacts?.etag}
          />
          <DetailValue
            label="Version"
            testId="intake-summary-version"
            value={detailFacts ? detailFacts.version : record.version}
          />
        </dl>
      </section>

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
          onClick={() => setActiveTab("review")}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            fontWeight: activeTab === "review" ? 700 : 500,
            background: activeTab === "review" ? "#ffffff" : "transparent",
            color: activeTab === "review" ? "#2e3a97" : "#64748b",
            border: activeTab === "review" ? "1px solid #cbd5e1" : "none",
            cursor: "pointer",
          }}
          data-testid="tab-review"
        >
          資料覆核
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
            jobHistory={lifecycle?.job_history}
            sla={sla}
            canReplay={canReplay}
            canRetry={canRetry}
            canReopen={canReopen}
            reopenDeniedReason={reopenDeniedReason}
            canCancelJob={canCancelJob}
            onReplayJob={onReplayDlq}
            onCancelJob={onCancelJob}
            onRetry={() => onRetry?.()}
            onReopen={onReopen}
            onCancel={onCancel}
          />
        )}

        {activeTab === "review" &&
          (reviewSection ??
            (record.stage === "AWAITING_ASSISTED_ENTRY" ? (
              <UnavailableContractState
                code="AUTHORITATIVE_ASSISTED_ENTRY_UNAVAILABLE"
                message="此收件需要人工補錄，但伺服器尚未提供可提交的 assisted-entry command contract。"
              />
            ) : (
              <ParsedDataReview
                canCorrect={canCorrect}
                fields={parsedReviewFields}
                onCorrect={onOpenFix ? (field) => onOpenFix(field.fieldPath) : undefined}
              />
            )))}

        {/* Tab 2: Evidence */}
        {activeTab === "evidence" && (
          <>
            <EvidencePanel
              record={record}
              fields={fields}
              sourceEvidence={sourceEvidence}
              access={evidenceAccess}
              humanDecision={humanDecisionEvidence}
              verification={evidenceVerification}
              exportReceipt={exportReceipt}
              auditReferences={auditReferences}
              etag={detailFacts?.etag}
              onOpenFix={onOpenFix}
              maskedView={maskedView}
            />
            {auditSection}
          </>
        )}

        {/* Full desktop compare and reversible identity decision. */}
        {activeTab === "identity" &&
          (identitySection ?? (
            <UnavailableContractState
              code="AUTHORITATIVE_IDENTITY_UNAVAILABLE"
              message="伺服器尚未提供 comparison、identity graph plan 與 review workflow；本頁不會由舊 match signals 推導決策資料。"
            />
          ))}

        {/* Assignment/SLA is part of the durable production composition. */}
        {activeTab === "assignment" && (
          <AssignmentSlaSummary
            allowedActions={lifecycle?.allowed_actions}
            assignment={lifecycle?.assignment}
            busy={busy}
            currentUserId={currentOperator?.id}
            history={[
              ...(lifecycle?.assignment_history ?? []),
              ...(lifecycle?.sla_history ?? []),
            ]}
            onClaim={onClaimAssignment}
            onOpenPause={onOpenPause}
            onOpenTransfer={onOpenTransfer}
            onResume={onResumeSla}
            onEscalate={onEscalateAssignment}
            onComplete={onCompleteAssignment}
            sla={lifecycle?.sla}
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
            canExecute={canExecutePromotion}
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
            replayDeniedReason={promotionReplayDeniedReason}
            requestDeniedReason={promotionRequestDeniedReason}
            reviewDeniedReason={promotionReviewDeniedReason}
            executeDeniedReason={promotionExecuteDeniedReason}
            scoreJob={scoreJob}
          />
        )}

        {/* Tab 4: Error Recovery */}
        {activeTab === "error" && (
          <IntakeErrorRecovery
            error={error}
            recovery={recovery}
            stage={record.stage}
            correlationId={record.correlationId}
            preservedInput={record.parsedFields ? Object.fromEntries(Object.values(record.parsedFields).map(f => [f.key, (f as any).value ?? f.normalizedValue ?? f.sourceValue])) : null}
            onRetry={onRetry}
            onReplayDlq={onReplayDlq}
            onCancel={onCancel}
            onOverride={onOverride}
            onCorrectInput={(fieldKey) => {
              const target =
                fieldKey ??
                Object.keys(record.parsedFields ?? {})[0] ??
                null;
              if (!target) return;
              setPendingFieldFocus(target);
              setActiveTab("review");
            }}
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

function DetailValue({
  label,
  value,
  testId,
}: {
  label: string;
  value: unknown;
  testId: string;
}) {
  const unavailable = value === null || value === undefined || value === "";
  return (
    <div>
      <dt className={styles.metaCaption}>{label}</dt>
      <dd
        className={styles.metaValue}
        data-authoritative={unavailable ? "missing" : "present"}
        data-testid={testId}
      >
        {unavailable ? "API 未回傳" : String(value)}
      </dd>
    </div>
  );
}

function DetailTime({
  label,
  value,
  testId,
}: {
  label: string;
  value?: string | null;
  testId: string;
}) {
  const formatted = formatIntakeDateTime(value);
  return (
    <div>
      <dt className={styles.metaCaption}>{label}</dt>
      <dd
        className={styles.metaValue}
        data-authoritative={formatted ? "present" : "missing"}
        data-testid={testId}
      >
        {formatted && value ? (
          <time dateTime={value} title={formatted.title}>
            {formatted.text}
          </time>
        ) : (
          "API 未回傳"
        )}
      </dd>
    </div>
  );
}

function UnavailableContractState({
  code,
  message,
}: {
  code: string;
  message: string;
}) {
  return (
    <section className={styles.noteBox} data-testid={code.toLowerCase()} role="status">
      <strong>資料尚未提供</strong>
      <div>{message}</div>
      <code>{code}</code>
    </section>
  );
}
