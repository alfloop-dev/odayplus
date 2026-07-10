"use client";

import { useId, useState } from "react";
import { Badge } from "./Badge.tsx";
import { Button } from "./Button.tsx";
import { AuditMetadata } from "./AuditMetadata.tsx";
import type {
  ApprovalDecision,
  ApprovalRecommendation,
  ApprovalSubmitPayload,
  ApprovalSubmitResult,
  AuditMeta,
  DecisionStatus,
} from "./contracts.ts";

export type ApprovalPanelProps = {
  decisionStatus: DecisionStatus;
  recommendation: ApprovalRecommendation;
  onSubmit: (payload: ApprovalSubmitPayload) => Promise<ApprovalSubmitResult> | ApprovalSubmitResult;
  audit: AuditMeta;
  disabledReason?: string;
  minReasonLength?: number;
  title?: string;
  className?: string;
};

const decisionLabels: Record<ApprovalDecision, string> = {
  APPROVE: "核准此決策",
  REJECT: "退回此決策",
  REQUEST_REVISION: "要求補件",
};

export function ApprovalPanel({
  decisionStatus,
  recommendation,
  onSubmit,
  audit,
  disabledReason,
  minReasonLength = 10,
  title = "Human approval",
  className,
}: ApprovalPanelProps) {
  const reasonId = useId();
  const [decision, setDecision] = useState<ApprovalDecision>("APPROVE");
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ApprovalSubmitResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const blocked = Boolean(disabledReason);
  const reasonValid = reason.trim().length >= minReasonLength;
  const canSubmit = !blocked && reasonValid && riskAcknowledged && !submitting;

  const submit = async () => {
    setError(null);
    if (!canSubmit) {
      setError("請填寫理由並確認風險後再提交。");
      return;
    }
    setSubmitting(true);
    try {
      const response = await onSubmit({ decision, reason, riskAcknowledged });
      setResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交失敗，請稍後再試。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className={["odp-approval", className].filter(Boolean).join(" ")} aria-labelledby="odp-approval-title">
      <header className="odp-approval__header">
        <h2 id="odp-approval-title">{title}</h2>
        <Badge label={decisionStatus} tone={decisionStatus === "APPROVED" ? "green" : decisionStatus === "REJECTED" ? "red" : "blue"} />
      </header>
      <section className="odp-approval__recommendation" aria-label="System recommendation">
        <h3>System recommendation</h3>
        <p>{recommendation.text}</p>
        <dl>
          {recommendation.modelVersion ? (
            <>
              <dt>Model</dt>
              <dd>{recommendation.modelVersion}</dd>
            </>
          ) : null}
          {recommendation.policyVersion ? (
            <>
              <dt>Policy</dt>
              <dd>{recommendation.policyVersion}</dd>
            </>
          ) : null}
          {recommendation.generatedAt ? (
            <>
              <dt>Generated</dt>
              <dd>{recommendation.generatedAt}</dd>
            </>
          ) : null}
        </dl>
      </section>

      {disabledReason ? (
        <div className="odp-inline-warning" role="note">
          {disabledReason}
        </div>
      ) : null}

      <fieldset className="odp-approval__fieldset" disabled={blocked || submitting}>
        <legend>Human decision</legend>
        {(Object.keys(decisionLabels) as ApprovalDecision[]).map((option) => (
          <label key={option} className="odp-radio">
            <input
              type="radio"
              name="approval-decision"
              value={option}
              checked={decision === option}
              onChange={() => setDecision(option)}
            />
            <span>{decisionLabels[option]}</span>
          </label>
        ))}
        <label className="odp-field" htmlFor={reasonId}>
          <span>Decision reason</span>
          <textarea
            id={reasonId}
            className="odp-textarea"
            value={reason}
            minLength={minReasonLength}
            aria-describedby={`${reasonId}-hint`}
            required
            onChange={(event) => setReason(event.currentTarget.value)}
          />
        </label>
        <p id={`${reasonId}-hint`} className="odp-muted">
          至少 {minReasonLength} 個字；此理由會寫入稽核紀錄。
        </p>
        <label className="odp-checkbox">
          <input
            type="checkbox"
            checked={riskAcknowledged}
            onChange={(event) => setRiskAcknowledged(event.currentTarget.checked)}
          />
          <span>我已檢視風險、證據與資料限制。</span>
        </label>
      </fieldset>

      {error ? (
        <div className="odp-inline-error" role="alert">
          {error}
        </div>
      ) : null}
      {result ? (
        <div className="odp-inline-success" role="status">
          Decision id: {result.decisionId}
          {result.auditEventId ? ` · Audit event: ${result.auditEventId}` : ""}
        </div>
      ) : null}
      <footer className="odp-actions">
        <Button variant={decision === "REJECT" ? "danger" : "primary"} loading={submitting} disabled={!canSubmit} onClick={submit}>
          {decisionLabels[decision]}
        </Button>
      </footer>
      <AuditMetadata meta={audit} />
    </section>
  );
}
