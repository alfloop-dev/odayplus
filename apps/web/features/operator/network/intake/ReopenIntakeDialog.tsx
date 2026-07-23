"use client";

import { useState } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";

export type ReopenIntakeDialogProps = {
  busy: boolean;
  error: IntakeApiError | null;
  independentReviewRequired: boolean;
  onClose: () => void;
  onSubmit: (payload: { reason: string; riskAcknowledged: true }) => void;
  record: AssistedIntake;
};

export function ReopenIntakeDialog({
  busy,
  error,
  independentReviewRequired,
  onClose,
  onSubmit,
  record,
}: ReopenIntakeDialogProps) {
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const title = independentReviewRequired
    ? "覆核解除隔離"
    : "提出解除隔離";

  function submit() {
    const normalizedReason = reason.trim();
    if (busy) return;
    if (normalizedReason.length < 3) {
      setLocalError("請輸入至少 3 個字的解除隔離理由。");
      return;
    }
    if (!riskAcknowledged) {
      setLocalError("請先確認風險與證據影響。");
      return;
    }
    setLocalError(null);
    onSubmit({ reason: normalizedReason, riskAcknowledged: true });
  }

  return (
    <IntakeDialogShell
      ariaLabel={title}
      className={styles.panelNarrow}
      dismissible={!busy}
      onClose={onClose}
      screenLabel="Dialog 解除隔離"
      stacked
      testId="reopen-intake-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle}>{title}</span>
        <button
          aria-label="關閉"
          className={styles.dialogClose}
          disabled={busy}
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>
      <div className={styles.dialogBody}>
        <p className={styles.help} data-testid="reopen-workflow-summary">
          {independentReviewRequired
            ? "已有解除提案；本次送出將由後端驗證你不是提案者，通過後才會解除隔離。"
            : "本次只建立解除提案。另一位具權限的人員覆核前，收件仍維持 QUARANTINED。"}
        </p>
        <dl className={styles.receiptGrid}>
          <div className={styles.receiptValue}>
            <dt>Intake</dt>
            <dd>{record.id}</dd>
          </div>
          <div className={styles.receiptValue}>
            <dt>Current state</dt>
            <dd>QUARANTINED · v{record.version}</dd>
          </div>
        </dl>
        <label className={styles.field} htmlFor="reopen-intake-reason">
          <span>解除理由（寫入 durable receipt）</span>
          <textarea
            className={styles.textarea}
            data-testid="reopen-intake-reason"
            disabled={busy}
            id="reopen-intake-reason"
            onChange={(event) => setReason(event.target.value)}
            rows={4}
            value={reason}
          />
        </label>
        <label className={styles.checkboxRow}>
          <input
            checked={riskAcknowledged}
            data-testid="reopen-intake-risk"
            disabled={busy}
            onChange={(event) => setRiskAcknowledged(event.target.checked)}
            type="checkbox"
          />
          <span>我已檢查來源政策與證據，理解解除隔離會留下提案／覆核紀錄。</span>
        </label>
        {localError || error ? (
          <div className={styles.warnNote} data-testid="reopen-intake-error" role="alert">
            {localError ?? `${error?.code}: ${error?.summary}`}
          </div>
        ) : null}
      </div>
      <div className={styles.dialogFoot}>
        <button
          className={styles.secondaryButton}
          disabled={busy}
          onClick={onClose}
          type="button"
        >
          返回
        </button>
        <button
          className={styles.primaryButton}
          data-testid="reopen-intake-submit"
          disabled={busy || reason.trim().length < 3 || !riskAcknowledged}
          onClick={submit}
          type="button"
        >
          {busy
            ? "提交中…"
            : independentReviewRequired
              ? "覆核並解除隔離"
              : "提出解除隔離"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}
