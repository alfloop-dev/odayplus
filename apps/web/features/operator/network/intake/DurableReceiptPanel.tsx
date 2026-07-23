"use client";

import { useMemo, useState, type ReactNode } from "react";
import type {
  AssignmentReceipt,
  AssistedIntake,
  CorrectionReceipt,
  DecisionReceipt,
  IntakeSubmissionReceipt,
  JobReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type {
  AuthoritativeEvidenceReceipt,
  AuthoritativeEvidenceVerification,
  AuthoritativeExportReceipt,
  AuthoritativeIdentityReceipt,
} from "./evidenceContracts";

export type DurableReceiptPanelProps = {
  /**
   * Compatibility only. A record is not a receipt and none of its fields are
   * used to manufacture receipt metadata.
   */
  record?: AssistedIntake;
  submissionReceipt?: IntakeSubmissionReceipt | null;
  assignmentReceipt?: AssignmentReceipt | null;
  decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt | null;
  promotionReceipt?: PromotionDecisionReceipt | null;
  slaReceipt?: SlaReceipt | null;
  correctionReceipts?: CorrectionReceipt[];
  identityReceipts?: AuthoritativeIdentityReceipt[];
  jobReceipts?: JobReceipt[];
  evidenceReceipts?: AuthoritativeEvidenceReceipt[];
  exportReceipts?: AuthoritativeExportReceipt[];
  verification?: AuthoritativeEvidenceVerification | null;
  testId?: string;
};

type ReceiptCardProps = {
  title: string;
  receiptId: string;
  receiptIdTestId?: string;
  testId: string;
  children: ReactNode;
};

function ReceiptCard({
  title,
  receiptId,
  receiptIdTestId,
  testId,
  children,
}: ReceiptCardProps) {
  return (
    <article className={styles.sectionBox} data-testid={testId}>
      <div className={styles.sectionLabel}>{title}</div>
      <dl className={styles.receiptList}>
        <ReceiptValue
          label="Receipt ID"
          testId={receiptIdTestId ?? `${testId}-id`}
          value={receiptId}
        />
        {children}
      </dl>
    </article>
  );
}

function ReceiptValue({
  label,
  value,
  testId,
}: {
  label: string;
  value: ReactNode | null | undefined;
  testId?: string;
}) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className={styles.receiptValue}>
      <dt>{label}</dt>
      <dd data-testid={testId}>{value}</dd>
    </div>
  );
}

function isPromotionReceipt(
  receipt: DecisionReceipt | PromotionDecisionReceipt,
): receipt is PromotionDecisionReceipt {
  return "promotion_decision_id" in receipt;
}

function VerificationDetails({
  verification,
}: {
  verification: AuthoritativeEvidenceVerification;
}) {
  return (
    <article
      className={styles.sectionBox}
      data-testid="receipt-verification"
      aria-label="伺服器簽章驗證結果"
    >
      <div className={styles.sectionLabel}>簽章與證據驗證 VERIFICATION</div>
      <dl className={styles.receiptList}>
        <ReceiptValue
          label="Verification status"
          testId="receipt-verification-status"
          value={verification.status}
        />
        <ReceiptValue label="Verified at" value={verification.verified_at} />
        <ReceiptValue label="Checksum algorithm" value={verification.checksum_algorithm} />
        <ReceiptValue
          label="Content checksum"
          testId="receipt-checksum"
          value={verification.content_sha256}
        />
        <ReceiptValue label="Signature" value={verification.signature} />
        <ReceiptValue label="Signer key version" value={verification.signer_key_version} />
        <ReceiptValue
          label="WORM sink receipt"
          testId="receipt-worm-sink-id"
          value={verification.worm_sink_id}
        />
        <ReceiptValue label="WORM checksum" value={verification.worm_checksum} />
        <ReceiptValue label="Evidence state" value={verification.evidence_state} />
        <ReceiptValue label="Audit event" value={verification.audit_event_id} />
        <ReceiptValue label="Correlation ID" value={verification.correlation_id} />
      </dl>
    </article>
  );
}

export function DurableReceiptPanel(props: DurableReceiptPanelProps) {
  const {
    submissionReceipt,
    assignmentReceipt,
    decisionReceipt,
    promotionReceipt,
    slaReceipt,
    correctionReceipts = [],
    identityReceipts = [],
    jobReceipts = [],
    evidenceReceipts = [],
    exportReceipts = [],
    verification,
    testId = "intake-durable-receipt-panel",
  } = props;
  const [copied, setCopied] = useState(false);

  const decision = decisionReceipt && !isPromotionReceipt(decisionReceipt)
    ? decisionReceipt
    : undefined;
  const promotion = promotionReceipt
    ?? (decisionReceipt && isPromotionReceipt(decisionReceipt) ? decisionReceipt : undefined);

  const authoritativeBundle = useMemo(
    () => ({
      submission_receipt: submissionReceipt,
      assignment_receipt: assignmentReceipt,
      sla_receipt: slaReceipt,
      correction_receipts: correctionReceipts,
      decision_receipt: decision,
      identity_receipts: identityReceipts,
      promotion_receipt: promotion,
      job_receipts: jobReceipts,
      evidence_receipts: evidenceReceipts,
      export_receipts: exportReceipts,
      verification,
    }),
    [
      assignmentReceipt,
      correctionReceipts,
      decision,
      evidenceReceipts,
      exportReceipts,
      identityReceipts,
      jobReceipts,
      promotion,
      slaReceipt,
      submissionReceipt,
      verification,
    ],
  );

  const hasAuthoritativeData = Boolean(
    submissionReceipt
      || assignmentReceipt
      || slaReceipt
      || decision
      || promotion
      || verification
      || correctionReceipts.length
      || identityReceipts.length
      || jobReceipts.length
      || evidenceReceipts.length
      || exportReceipts.length,
  );

  if (!hasAuthoritativeData) return null;

  const jsonString = JSON.stringify(authoritativeBundle, null, 2);

  const handleCopy = async () => {
    await navigator.clipboard?.writeText(jsonString);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const handleExport = () => {
    const blob = new Blob([jsonString], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = submissionReceipt
      ? `receipt-${submissionReceipt.intake_id}-v${submissionReceipt.version}.json`
      : "authoritative-receipts.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className={styles.sectionBox} data-testid={testId}>
      <div className={styles.receiptHeader}>
        <div>
          <div className={styles.sectionLabel}>持久化收據 DURABLE RECEIPTS</div>
          <p className={styles.help}>
            僅顯示 API 回傳的收據與驗證資料。未回傳的收據不會在此建立。
          </p>
        </div>
        <div className={styles.actionRow}>
          <button
            type="button"
            onClick={handleCopy}
            className={styles.secondaryButton}
            data-testid="receipt-copy-button"
          >
            {copied ? "已複製 JSON" : "複製收據 JSON"}
          </button>
          <button
            type="button"
            onClick={handleExport}
            className={styles.secondaryButton}
            data-testid="receipt-export-button"
          >
            下載收據 JSON
          </button>
        </div>
      </div>

      <div className={styles.receiptGrid}>
        {submissionReceipt ? (
          <ReceiptCard
            title="收件提交 SUBMISSION"
            receiptId={submissionReceipt.intake_id}
            testId="receipt-submission"
          >
            <ReceiptValue label="State" value={submissionReceipt.state} />
            <ReceiptValue label="Version" value={submissionReceipt.version} />
            <ReceiptValue label="Job ID" value={submissionReceipt.job_id} />
            <ReceiptValue label="Correlation ID" value={submissionReceipt.correlation_id} />
            <ReceiptValue label="Submitted at" value={submissionReceipt.submitted_at} />
            <ReceiptValue label="Duplicate hint" value={submissionReceipt.duplicate_hint} />
          </ReceiptCard>
        ) : null}

        {assignmentReceipt ? (
          <ReceiptCard
            title="指派 ASSIGNMENT"
            receiptId={assignmentReceipt.assignment_id}
            receiptIdTestId="receipt-asg-id"
            testId="receipt-assignment"
          >
            <ReceiptValue
              label="Status"
              testId="receipt-asg-status"
              value={assignmentReceipt.status}
            />
            <ReceiptValue
              label="Owner"
              testId="receipt-owner-id"
              value={assignmentReceipt.owner_subject_id}
            />
            <ReceiptValue
              label="Due at"
              testId="receipt-asg-due"
              value={assignmentReceipt.due_at}
            />
            <ReceiptValue
              label="Version"
              testId="receipt-asg-version"
              value={assignmentReceipt.version}
            />
            <ReceiptValue
              label="Audit event"
              testId="receipt-audit-event-id"
              value={assignmentReceipt.audit_event_id}
            />
          </ReceiptCard>
        ) : null}

        {slaReceipt ? (
          <ReceiptCard
            title="SLA"
            receiptId={slaReceipt.sla_instance_id}
            receiptIdTestId="receipt-sla-id"
            testId="receipt-sla"
          >
            <ReceiptValue
              label="State"
              testId="receipt-sla-state"
              value={slaReceipt.state}
            />
            <ReceiptValue
              label="Due at"
              testId="receipt-sla-due"
              value={slaReceipt.due_at}
            />
            <ReceiptValue label="Due soon at" value={slaReceipt.due_soon_at} />
            <ReceiptValue
              label="Paused duration (seconds)"
              testId="receipt-sla-paused-sec"
              value={slaReceipt.paused_duration_seconds}
            />
            <ReceiptValue
              label="Version"
              testId="receipt-sla-version"
              value={slaReceipt.version}
            />
            <ReceiptValue
              label="Audit event"
              testId="receipt-audit-event-id"
              value={slaReceipt.audit_event_id}
            />
            <ReceiptValue
              label="Correlation ID"
              testId="receipt-sla-correlation"
              value={slaReceipt.correlation_id}
            />
          </ReceiptCard>
        ) : null}

        {correctionReceipts.map((receipt) => (
          <ReceiptCard
            key={receipt.correction_id}
            title="欄位校正 CORRECTION"
            receiptId={receipt.correction_id}
            testId={`receipt-correction-${receipt.correction_id}`}
          >
            <ReceiptValue label="Status" value={receipt.status} />
            <ReceiptValue label="Intake ID" value={receipt.intake_id} />
            <ReceiptValue label="Listing revision ID" value={receipt.listing_revision_id} />
            <ReceiptValue label="Version" value={receipt.version} />
            <ReceiptValue label="Audit event" value={receipt.audit_event_id} />
            <ReceiptValue label="Correlation ID" value={receipt.correlation_id} />
          </ReceiptCard>
        ))}

        {decision ? (
          <ReceiptCard
            title="人工決策 DECISION"
            receiptId={decision.decision_id}
            testId="receipt-decision"
          >
            <ReceiptValue label="Status" value={decision.status} />
            <ReceiptValue
              label="Resource versions"
              value={JSON.stringify(decision.resource_versions)}
            />
            <ReceiptValue label="Job ID" value={decision.job_id} />
            <ReceiptValue label="Audit event" value={decision.audit_event_id} />
            <ReceiptValue label="Correlation ID" value={decision.correlation_id} />
          </ReceiptCard>
        ) : null}

        {identityReceipts.map((receipt) => (
          <ReceiptCard
            key={receipt.identity_receipt_id}
            title="身分圖決策 IDENTITY"
            receiptId={receipt.identity_receipt_id}
            testId={`receipt-identity-${receipt.identity_receipt_id}`}
          >
            <ReceiptValue label="Operation" value={receipt.operation} />
            <ReceiptValue label="Status" value={receipt.status} />
            <ReceiptValue label="Decision ID" value={receipt.decision_id} />
            <ReceiptValue
              label="Identity edge IDs"
              value={receipt.identity_edge_ids?.join(", ")}
            />
            <ReceiptValue
              label="Resource versions"
              value={receipt.resource_versions
                ? JSON.stringify(receipt.resource_versions)
                : undefined}
            />
            <ReceiptValue label="Occurred at" value={receipt.occurred_at} />
            <ReceiptValue label="Audit event" value={receipt.audit_event_id} />
            <ReceiptValue label="Correlation ID" value={receipt.correlation_id} />
          </ReceiptCard>
        ))}

        {promotion ? (
          <ReceiptCard
            title="候選點晉升 PROMOTION"
            receiptId={promotion.promotion_decision_id}
            testId="receipt-promotion"
          >
            <ReceiptValue label="Status" value={promotion.status} />
            <ReceiptValue label="Decision type" value={promotion.decision_type} />
            <ReceiptValue label="Intake ID" value={promotion.intake_id} />
            <ReceiptValue label="Listing ID" value={promotion.listing_id} />
            <ReceiptValue label="Candidate site ID" value={promotion.candidate_site_id} />
            <ReceiptValue label="SiteScore job ID" value={promotion.site_score_job_id} />
            <ReceiptValue label="Proposer" value={promotion.proposer_subject_id} />
            <ReceiptValue label="Reviewer" value={promotion.reviewer_subject_id} />
            <ReceiptValue label="Version" value={promotion.version} />
            <ReceiptValue label="Audit event" value={promotion.audit_event_id} />
            <ReceiptValue label="Correlation ID" value={promotion.correlation_id} />
          </ReceiptCard>
        ) : null}

        {jobReceipts.map((receipt) => (
          <ReceiptCard
            key={receipt.job_id}
            title="非同步工作 JOB"
            receiptId={receipt.job_id}
            testId={`receipt-job-${receipt.job_id}`}
          >
            <ReceiptValue label="Status" value={receipt.status} />
            <ReceiptValue label="Checkpoint" value={receipt.checkpoint} />
            <ReceiptValue label="Attempt" value={receipt.attempt} />
            <ReceiptValue label="Version" value={receipt.version} />
            <ReceiptValue label="Correlation ID" value={receipt.correlation_id} />
          </ReceiptCard>
        ))}

        {evidenceReceipts.map((receipt) => (
          <ReceiptCard
            key={receipt.evidence_receipt_id}
            title="來源證據 EVIDENCE"
            receiptId={receipt.evidence_receipt_id}
            testId={`receipt-evidence-${receipt.evidence_receipt_id}`}
          >
            <ReceiptValue label="Status" value={receipt.status} />
            <ReceiptValue label="Created at" value={receipt.created_at} />
            <ReceiptValue label="Snapshot ID" value={receipt.source_snapshot_id} />
            <ReceiptValue label="Parser run ID" value={receipt.parser_run_id} />
            <ReceiptValue label="Version" value={receipt.version} />
            <ReceiptValue label="Audit event" value={receipt.audit_event_id} />
            <ReceiptValue label="Correlation ID" value={receipt.correlation_id} />
          </ReceiptCard>
        ))}

        {exportReceipts.map((receipt) => (
          <ReceiptCard
            key={receipt.export_manifest_id}
            title="證據匯出 EXPORT"
            receiptId={receipt.export_manifest_id}
            testId={`receipt-export-${receipt.export_manifest_id}`}
          >
            <ReceiptValue label="Requested by" value={receipt.requested_by} />
            <ReceiptValue label="Approved by" value={receipt.approved_by} />
            <ReceiptValue label="Purpose" value={receipt.purpose} />
            <ReceiptValue label="Scope" value={JSON.stringify(receipt.scope)} />
            <ReceiptValue label="Field mask" value={JSON.stringify(receipt.field_mask)} />
            <ReceiptValue
              label="Source snapshots"
              value={receipt.source_snapshot_ids.join(", ")}
            />
            <ReceiptValue label="Audit events" value={receipt.audit_event_ids.join(", ")} />
            <ReceiptValue label="Object URI" value={receipt.object_uri} />
            <ReceiptValue label="Content SHA-256" value={receipt.content_sha256} />
            <ReceiptValue label="Watermark" value={receipt.watermark} />
            <ReceiptValue label="Expires at" value={receipt.expires_at} />
            <ReceiptValue label="Created at" value={receipt.created_at} />
            <ReceiptValue label="Download evidence ID" value={receipt.download_evidence_id} />
            <ReceiptValue label="Signer key version" value={receipt.signer_key_version} />
            <ReceiptValue label="WORM sink receipt" value={receipt.worm_sink_id} />
            <ReceiptValue label="WORM checksum" value={receipt.worm_checksum} />
          </ReceiptCard>
        ))}

        {verification ? <VerificationDetails verification={verification} /> : null}
      </div>
    </section>
  );
}
