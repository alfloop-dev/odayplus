"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiError, ConflictError } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type { AuthoritativeRecoveryContext } from "./evidenceContracts";
import type { IntakeApiError } from "./intakeClient";

type RecoveryError = IntakeApiError | ApiError | ConflictError;

export type IntakeErrorRecoveryProps = {
  error?: RecoveryError | null;
  /** Legacy server-state prop retained for callers that have not built context. */
  stage?: string | null;
  /** Legacy authoritative correlation value from the containing record. */
  correlationId?: string | null;
  /** Legacy preserved draft prop. Prefer recovery.preserved_input. */
  preservedInput?: Record<string, unknown> | null;
  recovery?: AuthoritativeRecoveryContext | null;
  onRetry?: (overrides?: {
    overrideRetryBudget?: boolean;
    riskAcknowledged?: boolean;
  }) => void;
  onReplayDlq?: (jobId?: string) => void;
  onCancel?: () => void;
  onOverride?: (reason: string) => void;
  onCorrectInput?: (fieldKey?: string) => void;
  testId?: string;
};

function readString(
  object: Record<string, unknown>,
  snake: string,
  camel?: string,
): string | undefined {
  const value = object[snake] ?? (camel ? object[camel] : undefined);
  return typeof value === "string" && value !== "" ? value : undefined;
}

function readNumber(
  object: Record<string, unknown>,
  snake: string,
  camel?: string,
): number | undefined {
  const value = object[snake] ?? (camel ? object[camel] : undefined);
  return typeof value === "number" ? value : undefined;
}

function redactSensitiveInput(value: unknown, key = ""): unknown {
  const normalizedKey = key.toLowerCase();
  if (
    normalizedKey.includes("token")
    || normalizedKey.includes("password")
    || normalizedKey.includes("secret")
    || normalizedKey.includes("credential")
    || normalizedKey.includes("cookie")
    || normalizedKey.includes("authorization")
  ) {
    return "[REDACTED]";
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactSensitiveInput(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([childKey, childValue]) => [
        childKey,
        redactSensitiveInput(childValue, childKey),
      ]),
    );
  }
  return value;
}

function MetadataValue({
  label,
  value,
  testId,
}: {
  label: string;
  value: unknown;
  testId?: string;
}) {
  const absent = value === null || value === undefined || value === "";
  return (
    <div className={styles.receiptValue}>
      <dt>{label}</dt>
      <dd data-testid={testId} data-authoritative={absent ? "missing" : "present"}>
        {absent
          ? "API 未回傳"
          : typeof value === "object"
            ? JSON.stringify(value)
            : String(value)}
      </dd>
    </div>
  );
}

export function IntakeErrorRecovery({
  error,
  stage,
  correlationId,
  preservedInput,
  recovery,
  onRetry,
  onReplayDlq,
  onCancel,
  onOverride,
  onCorrectInput,
  testId = "intake-error-recovery",
}: IntakeErrorRecoveryProps) {
  const [showPreservedInput, setShowPreservedInput] = useState(false);
  const [overrideModalOpen, setOverrideModalOpen] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const summaryRef = useRef<HTMLDivElement>(null);

  const source = (error ?? {}) as Record<string, unknown>;
  const errorCode = readString(source, "code");
  const errorMessage = readString(source, "message", "summary");
  const errorCorrelation = readString(source, "correlation_id", "correlationId")
    ?? correlationId
    ?? undefined;
  const occurredAt = readString(source, "occurred_at", "occurredAt");
  const nextAction = readString(source, "next_action", "nextAction");
  const reasonCode = readString(source, "reason_code", "reasonCode");
  const currentState = readString(source, "current_state", "currentState")
    ?? recovery?.current_state
    ?? stage
    ?? undefined;
  const currentVersion = readNumber(source, "current_version", "currentVersion")
    ?? recovery?.current_version
    ?? undefined;
  const currentOwner = readString(
    source,
    "current_owner_subject_id",
    "currentOwnerSubjectId",
  );
  const retryWithEtag = readString(source, "retry_with_etag", "retryWithEtag");
  const retryAfterSeconds = readNumber(
    source,
    "retry_after_seconds",
    "retryAfterSeconds",
  );
  const isRetryable = typeof source.retryable === "boolean"
    ? source.retryable
    : undefined;
  const httpStatus = typeof source.status === "number" ? source.status : undefined;
  const fieldErrors = Array.isArray(source.field_errors)
    ? source.field_errors as Array<{ field?: string; code?: string; message?: string }>
    : [];
  const authoritativeInput = recovery?.preserved_input ?? preservedInput;
  const safeInput = useMemo(
    () => authoritativeInput
      ? redactSensitiveInput(authoritativeInput) as Record<string, unknown>
      : null,
    [authoritativeInput],
  );

  useEffect(() => {
    if (error) summaryRef.current?.focus();
  }, [error, errorCode]);

  if (!error) return null;

  const handleConfirmOverride = () => {
    const reason = overrideReason.trim();
    if (!reason || !riskAcknowledged) return;
    onOverride?.(reason);
    setOverrideModalOpen(false);
    setOverrideReason("");
    setRiskAcknowledged(false);
  };

  return (
    <section
      className={styles.errorRecovery}
      data-testid={testId}
      aria-labelledby={`${testId}-title`}
    >
      <div
        ref={summaryRef}
        role="alert"
        tabIndex={-1}
        className={styles.errorSummary}
        data-testid="error-summary"
      >
        <div>
          <h4 id={`${testId}-title`}>異常與恢復 ERROR RECOVERY</h4>
          <p data-testid="error-message">{errorMessage ?? "API 未回傳錯誤摘要"}</p>
        </div>
        <div className={styles.errorCodeBlock}>
          <code
            data-testid="error-code"
            data-authoritative={errorCode ? "present" : "missing"}
          >
            {errorCode ?? "API 未回傳"}
          </code>
          {isRetryable !== undefined ? (
            <span data-testid="error-retryable-badge">
              {isRetryable ? "可重試 RETRYABLE" : "不可重試 NON_RETRYABLE"}
            </span>
          ) : null}
        </div>
      </div>

      <dl className={styles.errorMetadata}>
        <MetadataValue label="HTTP status" value={httpStatus} />
        <MetadataValue label="Reason code" value={reasonCode} />
        <MetadataValue
          label="Correlation ID"
          value={errorCorrelation}
          testId="error-correlation-id"
        />
        <MetadataValue
          label="Occurred at"
          value={occurredAt}
          testId="error-occurred-at"
        />
        <MetadataValue label="Retryable" value={isRetryable} />
        <MetadataValue
          label="Current state"
          value={currentState}
          testId="error-current-state"
        />
        <MetadataValue
          label="Current version"
          value={currentVersion}
          testId="error-current-version"
        />
        <MetadataValue
          label="Affected operation"
          value={recovery?.operation}
          testId="error-operation"
        />
        <MetadataValue label="Current owner" value={currentOwner} />
        <MetadataValue label="Retry with ETag" value={retryWithEtag} />
        <MetadataValue label="Retry after seconds" value={retryAfterSeconds} />
        <MetadataValue
          label="Server current value"
          value={recovery?.server_value}
          testId="error-server-value"
        />
        <MetadataValue
          label="Next action"
          value={nextAction}
          testId="error-next-action"
        />
      </dl>

      {fieldErrors.length > 0 ? (
        <section className={styles.fieldErrors} aria-label="欄位錯誤">
          <h5>欄位錯誤 FIELD ERRORS</h5>
          <ul>
            {fieldErrors.map((fieldError, index) => (
              <li key={`${fieldError.field ?? "unknown"}-${index}`}>
                {fieldError.field ? <code>{fieldError.field}</code> : null}
                {fieldError.code ? <code>{fieldError.code}</code> : null}
                {fieldError.message ? <span>{fieldError.message}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {safeInput ? (
        <section className={styles.preservedInput}>
          <button
            type="button"
            onClick={() => setShowPreservedInput((visible) => !visible)}
            className={styles.secondaryButton}
            aria-expanded={showPreservedInput}
            data-testid="error-toggle-preserved-input"
          >
            {showPreservedInput ? "隱藏保留輸入" : "顯示保留輸入"}
          </button>
          {showPreservedInput ? (
            <pre data-testid="error-preserved-input-box">
              {JSON.stringify(safeInput, null, 2)}
            </pre>
          ) : null}
        </section>
      ) : null}

      <div className={styles.actionRow}>
        {onRetry && isRetryable ? (
          <button
            type="button"
            onClick={() => onRetry()}
            className={styles.primaryButton}
            data-testid="error-action-retry"
          >
            立即重試
          </button>
        ) : null}
        {onReplayDlq ? (
          <button
            type="button"
            onClick={() => onReplayDlq()}
            className={styles.secondaryButton}
            data-testid="error-action-replay-dlq"
          >
            重播 DLQ
          </button>
        ) : null}
        {onCorrectInput ? (
          <button
            type="button"
            onClick={() => onCorrectInput()}
            className={styles.secondaryButton}
            data-testid="error-action-correct"
          >
            修正欄位
          </button>
        ) : null}
        {onOverride ? (
          <button
            type="button"
            onClick={() => setOverrideModalOpen(true)}
            className={styles.secondaryButton}
            data-testid="error-action-override"
          >
            提出覆寫
          </button>
        ) : null}
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className={styles.secondaryButton}
            data-testid="error-action-cancel"
          >
            取消處理
          </button>
        ) : null}
      </div>

      {overrideModalOpen ? (
        <div
          className={styles.modalBackdrop}
          role="dialog"
          aria-modal="true"
          aria-labelledby={`${testId}-override-title`}
          data-testid="error-override-dialog"
        >
          <div className={styles.panel}>
            <h4 id={`${testId}-override-title`}>提出例外覆寫</h4>
            <p className={styles.help}>
              覆寫必須由後端重新授權並產生收據；此畫面不會預先顯示成功。
            </p>
            <label className={styles.field}>
              <span>覆寫理由</span>
              <textarea
                value={overrideReason}
                onChange={(event) => setOverrideReason(event.target.value)}
                data-testid="error-override-reason"
              />
            </label>
            <label className={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={riskAcknowledged}
                onChange={(event) => setRiskAcknowledged(event.target.checked)}
                data-testid="error-override-risk"
              />
              <span>我理解覆寫會留下獨立稽核紀錄，且後端仍可能拒絕。</span>
            </label>
            <div className={styles.actionRow}>
              <button
                type="button"
                className={styles.secondaryButton}
                onClick={() => setOverrideModalOpen(false)}
              >
                返回
              </button>
              <button
                type="button"
                className={styles.primaryButton}
                disabled={!overrideReason.trim() || !riskAcknowledged}
                onClick={handleConfirmOverride}
                data-testid="error-override-confirm"
              >
                送出覆寫請求
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
