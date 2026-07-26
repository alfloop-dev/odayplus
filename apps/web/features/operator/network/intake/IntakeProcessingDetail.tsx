"use client";

import { useMemo, useState } from "react";
import type {
  AssignmentReceipt, AssistedIntake, AuditReference, CorrectionReceipt, DecisionReceipt,
  FieldValue, IntakeSubmissionReceipt, JobReceipt, PromotionDecisionReceipt, SlaReceipt, TransitionReceipt,
} from "@oday-plus/openapi-client";
import { AssistedEntryForm } from "./AssistedEntryForm";
import { AssignmentSlaSummary } from "./AssignmentSlaSummary";
import { DurableReceiptPanel } from "./DurableReceiptPanel";
import { EvidencePanel } from "./EvidencePanel";
import { IntakeErrorRecovery } from "./IntakeErrorRecovery";
import type { IntakeApiError } from "./intakeClient";
import { IntakeStageTimeline } from "./IntakeStageTimeline";
import { ListingCompareTable, type TargetListingData } from "./ListingCompareTable";
import { MatchEvidencePanel } from "./MatchEvidencePanel";
import {
  PromotionReviewPanel, type PromotionActor, type PromotionRequestInput, type PromotionReviewInput,
} from "./PromotionReviewPanel";
import type { ScoreReplayInput } from "./SiteScoreJobStatus";
import {
  decisionOptions, matchLabel, matchTone, policyLabel, policyTone, stageLabel, stageTone, type IntakeDecisionKind,
} from "./intakeTypes";
import styles from "./intake.module.css";

export type IntakeProcessingDetailProps = {
  record: AssistedIntake;
  targetListing?: TargetListingData | null;
  detailHref?: string;
  busy?: boolean; canCorrect?: boolean; canDecide?: boolean; canRetry?: boolean; canReplay?: boolean;
  error?: IntakeApiError | null; history?: TransitionReceipt[]; jobs?: JobReceipt[]; sla?: SlaReceipt;
  fields?: FieldValue[]; auditReferences?: AuditReference[]; submissionReceipt?: IntakeSubmissionReceipt;
  assignmentReceipt?: AssignmentReceipt; decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt;
  slaReceipt?: SlaReceipt; correctionReceipts?: CorrectionReceipt[];
  assignmentResourceVersion?: number | null; slaResourceVersion?: number | null;
  onClose: () => void;
  onAssistedEntrySave?: (input: { fields: Record<string, string>; riskSummary: string; riskAcknowledged: boolean }) => void;
  onDecide?: (kind: IntakeDecisionKind) => void; onOpenFix?: (fieldKey: string) => void;
  onRetry?: (overrides?: { overrideRetryBudget?: boolean; riskAcknowledged?: boolean }) => void;
  onReplayDlq?: (jobId?: string) => void; onCancel?: () => void; onOverride?: (reason: string) => void;
  onClaimAssignment?: () => void; onOpenTransfer?: () => void; onOpenPause?: () => void;
  onResumeSla?: () => void; onRefresh?: () => void; testId?: string;
  promotion?: PromotionDecisionReceipt | null; scoreJob?: JobReceipt | null; currentOperator?: PromotionActor;
  gateSnapshotSha256?: string; promotionBusy?: boolean; promotionError?: IntakeApiError | null;
  promotionIdempotencyReplayed?: boolean; canRequestPromotion?: boolean; canReviewPromotion?: boolean;
  canReplayScore?: boolean;
  onRequestPromotion?: (input: PromotionRequestInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReviewPromotion?: (input: PromotionReviewInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReplayScore?: (input: ScoreReplayInput) => Promise<JobReceipt | void> | void;
  onLookupPromotionDecision?: () => void;
};

export function IntakeProcessingDetail(props: IntakeProcessingDetailProps) {
  const {
    record, targetListing, detailHref, busy = false, canCorrect = true, canDecide = true, canRetry = true, canReplay = true,
    error = null, history = [], jobs = [], sla, fields, auditReferences = [], submissionReceipt,
    assignmentReceipt, decisionReceipt, slaReceipt, correctionReceipts = [],
    assignmentResourceVersion = null, slaResourceVersion = null, onClose, onAssistedEntrySave,
    onDecide, onOpenFix, onRetry, onReplayDlq, onCancel, onOverride, onClaimAssignment, onOpenTransfer,
    onOpenPause, onResumeSla, onRefresh, testId = "intake-detail-dialog", promotion = null,
    scoreJob = null, currentOperator, gateSnapshotSha256, promotionBusy = false, promotionError = null,
    promotionIdempotencyReplayed = false, canRequestPromotion = false, canReviewPromotion = false,
    canReplayScore = false, onRequestPromotion, onReviewPromotion, onReplayScore, onLookupPromotionDecision,
  } = props;
  const [maskedView, setMaskedView] = useState(false);
  const options = decisionOptions(record);
  const outcome = record.matchResult?.outcome;
  const targetDecisionReady = hasAuthoritativeTargetEvidence(record, targetListing);
  const targetDependentOutcome =
    outcome === "POSSIBLE_MATCH" || outcome === "REVISION" || outcome === "EXACT_DUPLICATE";
  const decisionLocked = targetDependentOutcome && !targetDecisionReady;
  const evidenceFields = useMemo(() => fields, [fields]);
  const promotionMounted = Boolean(currentOperator && gateSnapshotSha256 !== undefined);
  const promotionAvailable = promotionMounted && (record.stage === "READY" || Boolean(promotion));
  const recordFailure =
    record.stage === "FAILED" || record.stage === "QUARANTINED"
      ? record.failure ?? null
      : null;
  const recoveryError = error ?? recordFailure;
  const hasRecoveryState =
    Boolean(recoveryError) ||
    record.stage === "FAILED" ||
    record.stage === "QUARANTINED" ||
    jobs.some((job) => job.status === "DEAD_LETTER" || job.status === "FAILED");
  const preservedInput = useMemo(() => buildPreservedInput(record), [record]);
  const providerListingId = authoritativeParsedValue(
    record,
    "providerListingId",
    "provider_listing_id",
    "sourceListingId",
    "source_listing_id",
  );
  const rawRecord = record as AssistedIntake & {
    submittedAt?: string | null;
    submitted_at?: string | null;
  };
  const submittedAt = rawRecord.submittedAt ?? rawRecord.submitted_at ?? "UNAVAILABLE";

  return (
    <main className={styles.intakeFullPage} data-screen-label="Intake 收件處理詳情頁" data-testid={testId}>
      <header className={styles.intakeFullPageHeader} data-testid="intake-detail-header">
        <button className={styles.secondaryButton} onClick={onClose} type="button" data-testid="intake-return-button">← 返回收件匣</button>
        <div>
          <div className={styles.dialogTitle}>收件處理詳情 Intake Processing Detail</div>
          <div className={styles.intakeHeaderChips}>
            <span className={styles.rowId} data-testid="intake-detail-id">{record.id}</span>
            <span className={styles.chip} data-testid="intake-detail-stage" data-tone={stageTone(record.stage)}>{stageLabel(record.stage)}</span>
            {outcome ? <span className={styles.chip} data-testid="intake-detail-match" data-tone={matchTone(outcome)}>{matchLabel(outcome)}</span> : null}
            <span className={styles.chip} data-tone={policyTone(record.policy)}>{policyLabel(record.policy)}</span>
            <span>版本 v{record.version}</span>
          </div>
          <a className={styles.deepLink} data-testid="intake-detail-deeplink" href={detailHref ?? `/intake/${record.id}`}>
            {detailHref ?? `/intake/${record.id}`} · 可直接開啟、重新載入與返回
          </a>
        </div>
        {onRefresh ? <button className={styles.secondaryButton} onClick={onRefresh} type="button" data-testid="intake-refresh-button">重新整理</button> : null}
      </header>

      <section className={styles.sectionBox} data-testid="intake-submission-summary">
        <div className={styles.sectionHead}>送件摘要 SUBMISSION SUMMARY</div>
        <div className={styles.metaGrid}>
          <Meta label="原始 URL" value={record.originalUrl} />
          <Meta label="Canonical URL" value={record.canonicalUrl} />
          <Meta label="來源提供者 ID／政策" value={`${record.sourceId || "UNAVAILABLE"} · ${record.policyLabel || "UNAVAILABLE"}`} />
          <Meta label="提供者物件 ID" value={providerListingId} />
          <Meta label="送件時間 Submitted At" value={submittedAt} />
          <Meta label="擷取時間 Captured At" value={record.capturedAt ?? "UNAVAILABLE"} />
          <Meta label="提交者" value={record.submitter || "UNAVAILABLE"} />
          <Meta label="Owner" value={record.owner || "UNAVAILABLE"} />
          <Meta label="HeatZone／區域" value={record.heatZoneId ?? "UNAVAILABLE"} />
        </div>
      </section>

      <AssignmentSlaSummary assignmentResourceVersion={assignmentResourceVersion} busy={busy}
        onClaim={onClaimAssignment} onOpenPause={onOpenPause} onOpenTransfer={onOpenTransfer}
        onResume={onResumeSla} record={record} slaResourceVersion={slaResourceVersion} />

      <section data-testid="intake-processing-stages">
        <IntakeStageTimeline record={record} mode="stages" onCancel={onCancel} />
      </section>

      <section data-testid="intake-source-policy-evidence">
        <div className={styles.intakeSectionToolbar}>
          <strong>來源政策與 WORM 證據</strong>
          <label><input checked={maskedView} onChange={(event) => setMaskedView(event.target.checked)} type="checkbox" data-testid="intake-masking-checkbox" /> 資安遮蔽</label>
        </div>
        <EvidencePanel record={record} fields={evidenceFields} auditReferences={auditReferences}
          maskedView={maskedView} section="source" />
      </section>

      {hasRecoveryState ? <section data-testid="intake-error-recovery-section">
        <IntakeErrorRecovery error={recoveryError} stage={record.stage} correlationId={record.correlationId}
          preservedInput={preservedInput} onRetry={canRetry ? onRetry : undefined} onReplayDlq={onReplayDlq}
          onCancel={onCancel} onOverride={onOverride}
          onCorrectInput={onOpenFix ? () => onOpenFix(Object.keys(record.parsedFields ?? {})[0] ?? "address_raw") : undefined} />
      </section> : null}

      {record.stage === "AWAITING_ASSISTED_ENTRY" && onAssistedEntrySave
        ? <AssistedEntryForm busy={busy} canEdit={canCorrect} onSave={onAssistedEntrySave} /> : null}

      <section data-testid="intake-parsed-lineage">
        <EvidencePanel record={record} fields={evidenceFields} onOpenFix={canCorrect ? onOpenFix : undefined}
          maskedView={maskedView} section="lineage" />
      </section>

      <section data-testid="intake-match-evidence-section"><MatchEvidencePanel record={record} /></section>

      {record.matchResult ? (
        <section
          className={outcome === "POSSIBLE_MATCH" ? styles.possibleMatchOutcome : styles.unambiguousMatchOutcome}
          data-match-outcome={outcome}
          data-testid="intake-comparison-decision-section"
        >
          <ListingCompareTable detailHref={detailHref} record={record} targetListing={targetListing} />
          {decisionLocked ? (
            <div className={styles.errorPanel} data-testid="intake-target-decision-lock" role="alert">
              <strong>AUTHORITATIVE_TARGET_UNAVAILABLE</strong>
              <span>精確目標物件、可比對值或命名證據尚未由 API 提供；目標相依決策已關閉。</span>
              {onRefresh ? <button className={styles.secondaryButton} onClick={onRefresh} type="button">重新整理目標資料</button> : null}
            </div>
          ) : null}
          {options.length > 0 && onDecide ? (
            <div
              className={`${styles.intakeDecisionSection} ${outcome === "POSSIBLE_MATCH" ? styles.possibleMatchDecision : ""}`}
              data-testid="intake-detail-actions"
            >
              <div>
                <strong>人工覆核決策 HUMAN DECISION</strong>
                <p>識別決策須記錄原因、前後值、操作者、時間與版本；高風險動作由後端執行第二操作者與衝突檢查。POSSIBLE_MATCH 絕不自動合併。</p>
              </div>
              <div className={styles.intakeDecisionActions}>
                {options.map((option) => (
                  <button className={option.kind === "create" ? styles.primaryButton : styles.secondaryButton}
                    data-testid={`decide-action-${option.kind}`} disabled={busy || !canDecide || decisionLocked}
                    key={option.kind} onClick={() => onDecide(option.kind)} type="button">{option.label}</button>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {promotionAvailable && currentOperator ? (
        <section data-testid="intake-promotion-section">
          <PromotionReviewPanel busy={promotionBusy} canReplayScore={canReplayScore} canRequest={canRequestPromotion}
            canReview={canReviewPromotion} currentOperator={currentOperator} error={promotionError}
            gateSnapshotSha256={gateSnapshotSha256 ?? ""} idempotencyReplayed={promotionIdempotencyReplayed}
            onLookupDecision={onLookupPromotionDecision} onRefresh={onRefresh} onReplayScore={onReplayScore}
            onRequestPromotion={onRequestPromotion} onReviewPromotion={onReviewPromotion} promotion={promotion}
            record={record} scoreJob={scoreJob} />
        </section>
      ) : null}

      <section data-testid="intake-receipts-section">
        <DurableReceiptPanel record={record} submissionReceipt={submissionReceipt} assignmentReceipt={assignmentReceipt}
          decisionReceipt={decisionReceipt} slaReceipt={slaReceipt} correctionReceipts={correctionReceipts} />
      </section>

      <section data-testid="intake-timeline-audit-section">
        <IntakeStageTimeline record={record} history={history} jobs={jobs} sla={sla} canReplay={canReplay}
          mode="audit" onReplayJob={onReplayDlq} onCancel={onCancel} />
        <div className={styles.sectionBox} data-testid="intake-audit-references">
          <div className={styles.sectionHead}>稽核參照 AUDIT REFERENCES</div>
          {record.auditEvents?.length ? (
            <ul>
              {record.auditEvents.map((event) => (
                <li key={event.id}>
                  Time {event.occurredAt || "UNAVAILABLE"} · ID {event.id || "UNAVAILABLE"} · Action {event.action || "UNAVAILABLE"} · Actor {event.actorName || "UNAVAILABLE"} · Actor Role {event.actorRoleId || "UNAVAILABLE"} · Target {event.targetId || "UNAVAILABLE"} · Message {event.message || "UNAVAILABLE"} · Correlation {event.correlationId || "UNAVAILABLE"} · Event Version UNAVAILABLE · Before/After UNAVAILABLE — classified/masked audit detail contract required · Metadata UNAVAILABLE — classified/masked audit detail contract required
                </li>
              ))}
            </ul>
          ) : <div className={styles.emptyState} data-testid="audit-events-unavailable">AUDIT EVENTS: UNAVAILABLE</div>}
        </div>
        <div className={styles.sectionBox} data-testid="intake-authoritative-audit-references">
          {auditReferences.length ? auditReferences.map((reference) => (
            <div key={reference.audit_event_id}>{reference.audit_event_id} · {reference.action} · {reference.result}</div>
          )) : <div className={styles.emptyState} data-testid="audit-references-unavailable">AUDIT REFERENCES: UNAVAILABLE</div>}
        </div>
      </section>
    </main>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><div className={styles.metaCaption}>{label}</div><div className={styles.metaValue}>{value || "UNAVAILABLE"}</div></div>;
}

/** Same withheld-value marker the evidence panel renders for masked cells. */
const MASKED_MARKER = "•••• [MASKED]";

/**
 * Preserved input is built from the durable API-backed intake record, not from
 * parsed output alone. Retrieval can fail before parsing ever starts, so a
 * `parsedFields`-only view renders `{}` and loses the URL the operator actually
 * submitted (ADD ODP-P10-CAN-001-R3D). Submission context is therefore read
 * first, then parsed/normalized/corrected field values layered on top so an
 * operator correction still wins.
 *
 * Only values the record actually supplies are emitted: absent values stay
 * absent rather than being filled with a placeholder, and server-masked cells
 * keep the same `•••• [MASKED]` marker the evidence panel uses. No browser-only
 * copy of the submission is kept anywhere.
 */
export function buildPreservedInput(record: AssistedIntake): Record<string, unknown> | null {
  const preserved: Record<string, unknown> = {};
  const put = (key: string, value: unknown) => {
    if (value === null || value === undefined || value === "") return;
    preserved[key] = value;
  };

  // A below-CONFIDENTIAL clearance gets `originalUrl: null` plus the masking
  // flag from the API. Report it as withheld rather than silently absent.
  const masking = record as AssistedIntake & { originalUrl_masked?: boolean };
  if (masking.originalUrl_masked === true) {
    preserved.originalUrl = MASKED_MARKER;
  } else {
    put("originalUrl", record.originalUrl);
  }
  put("canonicalUrl", record.canonicalUrl);
  put("heatZoneId", record.heatZoneId);
  put("sourceId", record.sourceId);
  put("intakeMethod", record.intakeMethod);

  for (const field of Object.values(record.parsedFields ?? {})) {
    if (!field?.key) continue;
    if (field.masked === true) {
      preserved[field.key] = MASKED_MARKER;
      continue;
    }
    put(field.key, field.correctedValue ?? field.normalizedValue ?? field.sourceValue);
  }

  return Object.keys(preserved).length > 0 ? preserved : null;
}

function authoritativeParsedValue(record: AssistedIntake, ...keys: string[]): string {
  for (const key of keys) {
    const field = record.parsedFields?.[key];
    const value = field?.correctedValue ?? field?.normalizedValue ?? field?.sourceValue;
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return "UNAVAILABLE";
}

export function hasAuthoritativeTargetEvidence(
  record: AssistedIntake,
  targetListing?: TargetListingData | null,
): boolean {
  const match = record.matchResult;
  if (!match?.targetListingId || !targetListing || targetListing.id !== match.targetListingId) {
    return false;
  }
  const hasValue = [
    targetListing.sourceId,
    targetListing.sourceListingId,
    targetListing.sourceUrl,
    targetListing.canonicalUrl,
    targetListing.address,
    targetListing.area,
    targetListing.floor,
    targetListing.listingType,
    targetListing.rent,
    targetListing.status,
  ].some((value) => value !== null && value !== undefined && value !== "");
  const hasNamedEvidence =
    (match.agreeingSignals?.length ?? 0) + (match.contradictingSignals?.length ?? 0) > 0;
  return hasValue && hasNamedEvidence;
}
