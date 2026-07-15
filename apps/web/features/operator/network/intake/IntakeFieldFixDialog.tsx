"use client";

import { useState } from "react";
import type { IntakeFieldCell } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";
import { isIdentityField } from "./intakeTypes";

// "Dialog 欄位修正" (part of UX-SCR-EXP-003C).
//
// Owned layer  : single-field manual correction with the identity-field reason
//                gate enforced client-side BEFORE the request. The server also
//                enforces it (422); blocking here means the operator gets the
//                requirement as guidance rather than as a rejection.

export function IntakeFieldFixDialog({
  busy,
  error,
  field,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  error: IntakeApiError | null;
  field: IntakeFieldCell;
  onClose: () => void;
  onSubmit: (input: { value: string; reason: string }) => void;
}) {
  const identity = isIdentityField(field.key);
  const [value, setValue] = useState(
    String(field.correctedValue ?? field.normalizedValue ?? ""),
  );
  const [reason, setReason] = useState(field.correctionReason ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  function handleSubmit() {
    if (busy) return;
    if (!value.trim()) {
      setLocalError("請輸入修正後的值");
      return;
    }
    if (identity && !reason.trim()) {
      setLocalError("識別欄位修正必須填寫原因（前後值會寫入 Audit）。");
      return;
    }
    setLocalError(null);
    onSubmit({ value: value.trim(), reason: reason.trim() });
  }

  const shownError = localError ?? error?.summary ?? null;

  return (
    <IntakeDialogShell
      ariaLabel={`修正欄位：${field.label}`}
      className={styles.panelNarrow}
      onClose={onClose}
      screenLabel="Dialog 欄位修正"
      stacked
      testId="intake-fix-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="intake-fix-title">
          修正欄位：{field.label}
        </span>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        <div className={styles.noteBox} data-testid="intake-fix-context">
          來源值：{formatCell(field.sourceValue)}　·　正規化值：{formatCell(field.normalizedValue)}
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="intake-fix-value">
            修正後的值
          </label>
          <input
            className={styles.input}
            data-testid="intake-fix-value"
            id="intake-fix-value"
            onChange={(event) => setValue(event.target.value)}
            value={value}
          />
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="intake-fix-reason">
            {identity
              ? "修正原因（必填 — 此欄位影響識別／地址／租金／坪數或比對結果）"
              : "修正原因（選填）"}
          </label>
          <textarea
            className={styles.textarea}
            data-testid="intake-fix-reason"
            id="intake-fix-reason"
            onChange={(event) => setReason(event.target.value)}
            placeholder="例：與房東電話確認門牌為 26 號"
            rows={2}
            value={reason}
          />
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
        <button className={styles.secondaryButton} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-fix-submit"
          disabled={busy}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "儲存中…" : "儲存修正"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}
