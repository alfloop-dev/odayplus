"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { IntakeFieldCell } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import {
  fromLegacyIntakeField,
  materialCorrectionRequirement,
  type FieldLineageField,
} from "./FieldLineageRow";
import {
  useCorrectionDraft,
  type CorrectionDraftIdentity,
  type CorrectionDraftStatus,
} from "./useCorrectionDraft";

// "Dialog 欄位修正" (part of UX-SCR-EXP-003C).
//
// Owned layer  : single-field manual correction with the identity-field reason
//                gate enforced client-side BEFORE the request. The server also
//                enforces it (422); blocking here means the operator gets the
//                requirement as guidance rather than as a rejection.
//
// A correction is a high-impact write, so it also carries the risk disclosure
// the operator acknowledged. The summary sent is the string rendered above the
// checkbox, so the audit stores what was actually read.

export function IntakeFieldFixDialog({
  busy,
  baseVersion = null,
  draftIdentity = null,
  error,
  field,
  onClose,
  onSubmit,
  submissionState = "IDLE",
}: {
  busy: boolean;
  baseVersion?: number | null;
  draftIdentity?: Omit<CorrectionDraftIdentity, "purpose" | "fieldPath"> | null;
  error: IntakeApiError | null;
  field: IntakeFieldCell | FieldLineageField;
  onClose: () => void;
  onSubmit: (input: {
    value: string;
    reason: string;
    riskSummary: string;
    riskAcknowledged: boolean;
    operationId: string;
    ifMatchVersion: number | null;
    requiresIndependentReview: boolean;
  }) => void;
  submissionState?:
    | "IDLE"
    | {
        status: "COMMITTED";
        operationId: string;
        submittedBaseVersion: number | null;
      };
}) {
  const lineageField = isLineageField(field) ? field : fromLegacyIntakeField(field);
  const review = materialCorrectionRequirement(lineageField);
  const initialValue = String(
    lineageField.correctedValue ?? lineageField.effectiveValue ?? lineageField.normalizedValue ?? "",
  );
  const initialFields = useMemo(
    () => ({ value: initialValue }),
    [initialValue],
  );
  const controller = useCorrectionDraft({
    identity: draftIdentity
      ? {
          ...draftIdentity,
          purpose: "correction",
          fieldPath: lineageField.fieldPath,
        }
      : null,
    initialFields,
    baseVersion,
  });
  const [localError, setLocalError] = useState<string | null>(null);
  const submittedSnapshotRef = useRef<{
    operationId: string;
    baseVersion: number | null;
  } | null>(null);

  useEffect(() => {
    if (!error) return;
    controller.markFailure(
      {
        code: error.code,
        summary: error.summary,
        occurredAt: error.occurredAt,
        retryable: error.retryable,
        correlationId: error.correlationId,
      },
      error.status === 409,
    );
    submittedSnapshotRef.current = null;
    // The error object is the server response boundary for this update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error]);

  useEffect(() => {
    if (submissionState === "IDLE") return;
    const submitted = submittedSnapshotRef.current;
    if (
      !submitted ||
      submitted.operationId !== submissionState.operationId ||
      submitted.baseVersion !== submissionState.submittedBaseVersion
    ) {
      return;
    }
    submittedSnapshotRef.current = null;
    controller.clearAfterCommit();
    // Clear only after authoritative readback identifies the exact operation
    // and base version submitted by this mounted dialog.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submissionState]);

  const before = formatCell(lineageField.effectiveValue);
  const value = String(controller.draft.fields.value ?? "");
  const submitting = controller.draft.status === "SUBMITTING";
  const controlsLocked = busy || submitting;
  const riskSummary = review.material
    ? `修正重要欄位「${lineageField.label}」：${before} → ${value.trim() || "（空白）"}。` +
      `此變更會先成為修正提案，提案者不得自行核准；前後值、理由、快照、Parser 與 supersession lineage 會寫入 Audit。`
    : `修正欄位「${lineageField.label}」：${before} → ${value.trim() || "（空白）"}。` +
      `修正值會取代來源正規化值作為後續評估依據。前後值會寫入 Audit。`;

  function handleSubmit() {
    if (controlsLocked || submittedSnapshotRef.current) return;
    if (!value.trim()) {
      setLocalError("請輸入修正後的值");
      return;
    }
    if (review.reasonRequired && !controller.draft.reason.trim()) {
      setLocalError("重要欄位修正必須填寫原因（前後值會寫入 Audit）。");
      return;
    }
    if (review.riskAcknowledgementRequired && !controller.draft.riskAcknowledged) {
      setLocalError("請先確認你已了解此修正的影響。");
      return;
    }
    setLocalError(null);
    submittedSnapshotRef.current = {
      operationId: controller.draft.operationId,
      baseVersion: controller.draft.baseVersion,
    };
    controller.markSubmitting();
    onSubmit({
      value: value.trim(),
      reason: controller.draft.reason.trim(),
      riskSummary,
      riskAcknowledged: controller.draft.riskAcknowledged,
      operationId: controller.draft.operationId,
      ifMatchVersion: controller.draft.baseVersion,
      requiresIndependentReview: review.independentReviewRequired,
    });
  }

  const shownError = localError ?? error?.summary ?? controller.draft.lastFailure?.summary ?? null;

  return (
    <IntakeDialogShell
      ariaLabel={`修正欄位：${lineageField.label}`}
      className={styles.panelNarrow}
      onClose={controlsLocked ? () => {} : onClose}
      screenLabel="Dialog 欄位修正"
      stacked
      testId="intake-fix-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="intake-fix-title">
          修正欄位：{lineageField.label}
        </span>
        <button
          aria-label="關閉"
          className={styles.dialogClose}
          disabled={controlsLocked}
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        <div className={styles.noteBox} data-testid="intake-fix-context">
          解析值：{formatCell(lineageField.parsedValue)}　·　正規化值：
          {formatCell(lineageField.normalizedValue)}　·　人工修正值：
          {formatCell(lineageField.correctedValue)}　·　有效值：
          {formatCell(lineageField.effectiveValue)}
          <br />
          Snapshot {lineageField.sourceSnapshotId ?? "未提供"} · Parser run{" "}
          {lineageField.parserRunId ?? "未提供"} · Draft {controller.draft.status} · Operation{" "}
          {controller.draft.operationId}
        </div>

        {lineageField.corrections.length ? (
          <div className={styles.noteBox} data-testid="intake-fix-lineage">
            {lineageField.corrections.map((correction) => (
              <div key={correction.correctionId}>
                {correction.correctionId} · {correction.status} ·{" "}
                {correction.actorName ?? correction.actorSubjectId} · {correction.correctedAt} ·{" "}
                {correction.reason}
                {correction.supersedesCorrectionId
                  ? ` · supersedes ${correction.supersedesCorrectionId}`
                  : ""}
              </div>
            ))}
          </div>
        ) : null}

        <div>
          <label className={styles.fieldLabel} htmlFor="intake-fix-value">
            修正後的值
          </label>
          <input
            className={styles.input}
            data-autofocus
            data-testid="intake-fix-value"
            disabled={controlsLocked}
            id="intake-fix-value"
            onChange={(event) => controller.setField("value", event.target.value)}
            value={value}
          />
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="intake-fix-reason">
            {review.material
              ? "修正原因（必填 — 此欄位影響識別／地址／租金／坪數或比對結果）"
              : "修正原因（必填）"}
          </label>
          <textarea
            className={styles.textarea}
            data-testid="intake-fix-reason"
            disabled={controlsLocked}
            id="intake-fix-reason"
            onChange={(event) => controller.setReason(event.target.value)}
            placeholder="例：與房東電話確認門牌為 26 號"
            rows={2}
            value={controller.draft.reason}
          />
        </div>

        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>風險摘要 RISK SUMMARY</div>
          <div className={styles.riskSummaryText} data-testid="intake-fix-risk-summary">
            {riskSummary}
          </div>
          {review.riskAcknowledgementRequired ? (
            <label className={styles.checkboxRow} htmlFor="intake-fix-risk-ack">
              <input
                checked={controller.draft.riskAcknowledged}
                data-testid="intake-fix-risk-ack"
                disabled={controlsLocked}
                id="intake-fix-risk-ack"
                onChange={(event) => controller.setRiskAcknowledged(event.target.checked)}
                type="checkbox"
              />
              <span>我已閱讀並了解上述風險，確認送出修正提案（將連同此摘要寫入 Audit）</span>
            </label>
          ) : null}
          {review.independentReviewRequired ? (
            <div className={styles.warnNote} data-testid="intake-fix-independent-review">
              需要獨立覆核：提案者不得自行核准；送出不會立即改變 authoritative effective value。
            </div>
          ) : null}
        </div>

        {shownError ? (
          <div className={styles.errorPanel} data-testid="intake-fix-error" role="alert">
            <span className={styles.errorSummary}>{shownError}</span>
            {error ? (
              <>
                <span className={styles.errorMeta}>
                  錯誤碼 {error.code}
                  {error.correlationId ? ` · correlation ${error.correlationId}` : ""} · 發生於{" "}
                  {error.occurredAt}
                </span>
                <span className={styles.errorNext}>下一步：{error.nextAction}</span>
              </>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className={styles.dialogFooter}>
        <button
          className={styles.secondaryButton}
          disabled={controlsLocked}
          onClick={onClose}
          type="button"
        >
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-fix-submit"
          disabled={controlsLocked}
          onClick={handleSubmit}
          type="button"
        >
          {controlsLocked ? "送出中…" : retryLabel(controller.draft.status)}
        </button>
      </div>
    </IntakeDialogShell>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function isLineageField(field: IntakeFieldCell | FieldLineageField): field is FieldLineageField {
  return "fieldPath" in field;
}

function retryLabel(status: CorrectionDraftStatus): string {
  if (status === "FAILED" || status === "CONFLICT") return "以相同 operation 重試";
  return "送出修正提案";
}
