"use client";

import { useState } from "react";
import type {
  AssignmentReceipt,
  AssistedIntake,
  CorrectionReceipt,
  DecisionReceipt,
  IntakeSubmissionReceipt,
  PromotionDecisionReceipt,
  SlaReceipt,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";

export type DurableReceiptVerification = {
  status: "VERIFIED" | "UNVERIFIED" | "TAMPERED";
  checksum?: string | null;
  verifiedAt?: string | null;
  wormState?: string | null;
};

export type DurableReceiptPanelProps = {
  record: AssistedIntake;
  submissionReceipt?: IntakeSubmissionReceipt;
  assignmentReceipt?: AssignmentReceipt;
  decisionReceipt?: DecisionReceipt | PromotionDecisionReceipt;
  slaReceipt?: SlaReceipt;
  correctionReceipts?: CorrectionReceipt[];
  verification?: DurableReceiptVerification;
  testId?: string;
};

const unavailable = "UNAVAILABLE";

function present(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? unavailable : String(value);
}

export function DurableReceiptPanel({
  record,
  submissionReceipt,
  assignmentReceipt,
  decisionReceipt,
  slaReceipt,
  correctionReceipts = [],
  verification,
  testId = "intake-durable-receipt-panel",
}: DurableReceiptPanelProps) {
  const [copied, setCopied] = useState(false);
  const verificationStatus = verification?.status ?? "UNVERIFIED";
  const isPromotionReceipt =
    decisionReceipt !== undefined && "promotion_decision_id" in decisionReceipt;
  const promotionReceipt = isPromotionReceipt
    ? (decisionReceipt as PromotionDecisionReceipt)
    : undefined;
  const hasAuthoritativeReceipt = Boolean(
    submissionReceipt ||
      assignmentReceipt ||
      decisionReceipt ||
      slaReceipt ||
      correctionReceipts.length,
  );
  const canExport = verificationStatus === "VERIFIED" && hasAuthoritativeReceipt;

  // Export only verbatim server receipts and backend verification metadata.
  // The intake read model is intentionally excluded: it is not a receipt.
  const receiptPayload = {
    ...(submissionReceipt ? { submission: submissionReceipt } : {}),
    ...(assignmentReceipt ? { assignment: assignmentReceipt } : {}),
    ...(decisionReceipt ? { decision: decisionReceipt } : {}),
    ...(slaReceipt ? { sla: slaReceipt } : {}),
    ...(correctionReceipts.length ? { corrections: correctionReceipts } : {}),
    ...(verification ? { verification } : {}),
  };
  const jsonString = JSON.stringify(receiptPayload, null, 2);

  const handleCopy = () => {
    if (!canExport) return;
    void navigator.clipboard?.writeText(jsonString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleExport = () => {
    if (!canExport) return;
    const blob = new Blob([jsonString], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `receipt-${record.id}-v${record.version}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const verificationCopy =
    verificationStatus === "VERIFIED"
      ? "VERIFIED"
      : verificationStatus === "TAMPERED"
        ? "TAMPERED"
        : "UNVERIFIED";
  const verificationTone =
    verificationStatus === "VERIFIED"
      ? { background: "#dcfce7", color: "#15803d" }
      : verificationStatus === "TAMPERED"
        ? { background: "#fee2e2", color: "#b91c1c" }
        : { background: "#f1f5f9", color: "#475569" };

  return (
    <div
      className={styles.sectionBox}
      data-testid={testId}
      style={{
        border: "1px solid #eef1f6",
        borderRadius: "8px",
        padding: "14px",
        background: "#ffffff",
        marginBottom: "16px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "12px",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <h4
          style={{
            margin: 0,
            fontSize: "13px",
            fontWeight: 700,
            color: "#1e293b",
            display: "flex",
            alignItems: "center",
            gap: "8px",
          }}
        >
          <span>持久化收據 DURABLE RECEIPTS</span>
          <span
            style={{
              fontSize: "10.5px",
              fontWeight: 700,
              padding: "2px 8px",
              borderRadius: "999px",
              ...verificationTone,
            }}
            data-testid="receipt-verification-status"
          >
            {verificationCopy}
          </span>
        </h4>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            onClick={handleCopy}
            disabled={!canExport}
            className={styles.secondaryButton}
            style={{ padding: "4px 10px", fontSize: "11px" }}
            data-testid="receipt-copy-button"
            title={!canExport ? "後端尚未提供已驗證的 durable receipt" : undefined}
          >
            {copied ? "已複製 JSON" : "複製收據"}
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={!canExport}
            className={styles.secondaryButton}
            style={{ padding: "4px 10px", fontSize: "11px" }}
            data-testid="receipt-export-button"
            title={!canExport ? "後端尚未提供已驗證的 durable receipt" : undefined}
          >
            下載收據
          </button>
        </div>
      </div>

      {!hasAuthoritativeReceipt ? (
        <div className={styles.emptyState} data-testid="receipt-unavailable-state" role="status">
          UNAVAILABLE - 後端尚未回傳 durable receipt；本頁不會建立替代識別碼或匯出檔案。
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "10px",
          marginBottom: "14px",
        }}
      >
        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "10px",
            fontSize: "11px",
          }}
        >
          <div style={{ fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
            收件提交收據 Submission Receipt
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px", color: "#475569" }}>
            <div>Intake ID: <code>{present(submissionReceipt?.intake_id)}</code></div>
            <div>Version: <span>{submissionReceipt ? `v${submissionReceipt.version}` : unavailable}</span></div>
            <div>State: <strong>{present(submissionReceipt?.state)}</strong></div>
            <div>Job ID: <code>{present(submissionReceipt?.job_id)}</code></div>
            <div>Correlation ID: <code>{present(submissionReceipt?.correlation_id)}</code></div>
            <div>Submitted At: <span>{present(submissionReceipt?.submitted_at)}</span></div>
          </div>
        </div>

        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "10px",
            fontSize: "11px",
          }}
          data-testid="durable-receipt-asg-sla"
        >
          <div style={{ fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
            指派與 SLA 收據 Assignment & SLA Receipt
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px", color: "#475569" }}>
            <div>Owner: <strong data-testid="receipt-owner-id">{present(assignmentReceipt?.owner_subject_id)}</strong></div>
            <div>Assignment Status: <strong data-testid="receipt-asg-status">{present(assignmentReceipt?.status)}</strong></div>
            <div>SLA State: <strong data-testid="receipt-sla-state">{present(slaReceipt?.state)}</strong></div>
            <div>Assignment ID: <code data-testid="receipt-asg-id">{present(assignmentReceipt?.assignment_id)}</code></div>
            <div>Assignment Version: <span data-testid="receipt-asg-version">{assignmentReceipt ? `v${assignmentReceipt.version}` : unavailable}</span></div>
            <div>Assignment Due At: <span data-testid="receipt-asg-due">{present(assignmentReceipt?.due_at)}</span></div>
            <div>SLA Instance ID: <code data-testid="receipt-sla-id">{present(slaReceipt?.sla_instance_id)}</code></div>
            <div>SLA Version: <span data-testid="receipt-sla-version">{slaReceipt ? `v${slaReceipt.version}` : unavailable}</span></div>
            <div>Paused Duration: <span data-testid="receipt-sla-paused-sec">{slaReceipt ? `${slaReceipt.paused_duration_seconds}s` : unavailable}</span></div>
            <div>SLA Correlation: <code data-testid="receipt-sla-correlation">{present(slaReceipt?.correlation_id)}</code></div>
            <div>Assignment Audit: <code data-testid="receipt-audit-event-id">{present(assignmentReceipt?.audit_event_id)}</code></div>
            <div>SLA Audit: <code>{present(slaReceipt?.audit_event_id)}</code></div>
          </div>
        </div>

        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "10px",
            fontSize: "11px",
          }}
        >
          <div style={{ fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
            決策與晉升收據 Decision Receipt
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px", color: "#475569" }}>
            <div>Decision ID: <code>{present(
              isPromotionReceipt
                ? promotionReceipt?.promotion_decision_id
                : (decisionReceipt as DecisionReceipt | undefined)?.decision_id,
            )}</code></div>
            <div>Decision State: <strong>{present(decisionReceipt?.status)}</strong></div>
            <div>Audit Event: <code>{present(decisionReceipt?.audit_event_id)}</code></div>
            <div>Correlation ID: <code>{present(
              decisionReceipt && "correlation_id" in decisionReceipt
                ? decisionReceipt.correlation_id
                : undefined,
            )}</code></div>
            <div>Listing ID: <code>{present(promotionReceipt?.listing_id)}</code></div>
            <div>Candidate Site ID: <code>{present(promotionReceipt?.candidate_site_id)}</code></div>
            <div>SiteScore Job ID: <code>{present(promotionReceipt?.site_score_job_id)}</code></div>
          </div>
        </div>
      </div>

      <div
        style={{
          background: "#1e293b",
          color: "#f8fafc",
          borderRadius: "8px",
          padding: "12px",
          fontFamily: "monospace",
          fontSize: "10.5px",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginBottom: "6px",
            color: "#94a3b8",
          }}
        >
          <span>BACKEND VERIFICATION</span>
          <span data-testid="receipt-worm-state">WORM: {present(verification?.wormState)}</span>
        </div>
        <div
          style={{ color: "#38bdf8", wordBreak: "break-all", marginBottom: "8px" }}
          data-testid="receipt-checksum"
        >
          Checksum: {present(verification?.checksum)}
        </div>
        <div style={{ color: "#cbd5e1" }}>
          Verified At: {present(verification?.verifiedAt)}
        </div>
      </div>
    </div>
  );
}
