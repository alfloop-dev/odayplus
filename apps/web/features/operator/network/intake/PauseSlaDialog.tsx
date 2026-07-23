import { useState, useEffect } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";

export interface PauseSlaDialogProps {
  busy: boolean;
  error: IntakeApiError | null;
  onClose: () => void;
  onSubmit: (payload: {
    reason: string;
    expected_resume_at: string;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) => void;
  record: AssistedIntake;
  onConflictRefresh?: () => void;
}

/**
 * PauseSlaDialog satisfies VDC-001:
 * Pause contains reason and required editable resume time only.
 * No hidden default resume time or target/handoff note fields.
 */
export function PauseSlaDialog({
  busy,
  error,
  onClose,
  onSubmit,
  record,
  onConflictRefresh,
}: PauseSlaDialogProps) {
  const [reason, setReason] = useState("");
  const [resumeTime, setResumeTime] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    const activeBeforeOpen = document.activeElement as HTMLElement | null;
    return () => {
      if (activeBeforeOpen && typeof activeBeforeOpen.focus === "function") {
        activeBeforeOpen.focus();
      }
    };
  }, []);

  const title = "暫停 SLA（Pause SLA）";
  const formattedResumeText = resumeTime ? resumeTime.replace("T", " ") : "未設定";
  const riskSummary =
    `將收件 ${record.id} 的 SLA 暫停，預計恢復時間：${formattedResumeText}。` +
    `此操作會暫停處理時效計時。前後狀態與暫停原因將寫入 Audit 歷程。`;

  function safeClose() {
    if (!busy) onClose();
  }

  function handleSubmit() {
    if (busy) return;
    setLocalError(null);

    if (!reason.trim()) {
      setLocalError("請輸入暫停 SLA 的原因（必填）。");
      return;
    }

    if (!resumeTime) {
      setLocalError("請選擇預計恢復時間（必填，可編輯）。");
      return;
    }

    // Convert local time string to ISO 8601 string
    let isoResumeAt: string;
    try {
      isoResumeAt = new Date(resumeTime).toISOString();
    } catch {
      setLocalError("預計恢復時間格式無效，請重新選擇。");
      return;
    }

    if (!riskAcknowledged) {
      setLocalError("請先勾選確認你已閱讀並了解上述風險。");
      return;
    }

    onSubmit({
      reason: reason.trim(),
      expected_resume_at: isoResumeAt,
      riskSummary,
      riskAcknowledged,
    });
  }

  const shownError = localError || error?.summary || null;
  const isConflict = error?.code === "ODP-INTAKE-CONFLICT" || error?.status === 409;

  return (
    <IntakeDialogShell
      ariaLabel={title}
      className={styles.panelNarrow}
      onClose={safeClose}
      screenLabel="Dialog 暫停 SLA"
      stacked
      testId="pause-sla-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="pause-dialog-title">
          {title}
        </span>
        <button
          aria-label="關閉"
          className={styles.dialogClose}
          disabled={busy}
          onClick={safeClose}
          type="button"
        >
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        <div
          style={{
            fontSize: "11px",
            color: "#64748b",
            background: "#f8fafc",
            padding: "6px 10px",
            borderRadius: "4px",
            border: "1px solid #e2e8f0",
            marginBottom: "10px",
          }}
          data-testid="pause-record-info"
        >
          收件編號：<strong>{record.id}</strong> · 目前負責人：
          <strong data-testid="pause-record-owner">{record.owner || "未指派"}</strong> · 版本：
          <span data-testid="pause-record-version">v{record.version || 1}</span>
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="pause-reason-input">
            核准暫停原因（必填 — 寫入 SLA 歷程與 Audit）
          </label>
          <textarea
            className={styles.textarea}
            data-testid="pause-reason-input"
            id="pause-reason-input"
            onChange={(e) => setReason(e.target.value)}
            placeholder="例：等待房東提供完整租約證明，暫停時效計時..."
            rows={3}
            value={reason}
          />
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="pause-resume-time-input">
            預計恢復時間（必填 — 可編輯）
          </label>
          <input
            className={styles.input}
            data-testid="pause-resume-time-input"
            id="pause-resume-time-input"
            onChange={(e) => setResumeTime(e.target.value)}
            type="datetime-local"
            value={resumeTime}
          />
        </div>

        {isConflict && onConflictRefresh ? (
          <div className={styles.errorPanel} data-testid="pause-conflict-panel" role="alert">
            <span className={styles.errorSummary}>
              409 OWNER_CONFLICT — 此收件的 owner 在你開啟後已變更
            </span>
            <span className={styles.errorMeta}>
              目前 owner：{record.owner || "未指定"} · 版本 v{record.version || 1}
            </span>
            <span className={styles.errorNext}>
              重新整理套用最新狀態後再送出 — 你的暫停原因與預計恢復時間已保留。
            </span>
            <button
              className={styles.secondaryButton}
              data-testid="pause-conflict-refresh-btn"
              onClick={onConflictRefresh}
              style={{ marginTop: "6px", color: "#b3261e", borderColor: "#f3cbc7" }}
              type="button"
            >
              重新整理並套用最新 owner／版本
            </button>
          </div>
        ) : shownError ? (
          <div className={styles.errorPanel} data-testid="pause-error-panel" role="alert">
            <span className={styles.errorSummary}>{shownError}</span>
            {error && (
              <>
                <span className={styles.errorMeta}>
                  錯誤碼 {error.code}
                  {error.correlationId ? ` · correlation ${error.correlationId}` : ""} · 發生於{" "}
                  {error.occurredAt}
                </span>
                <span className={styles.errorNext}>下一步：{error.nextAction}</span>
              </>
            )}
          </div>
        ) : null}

        <div className={styles.sectionBox}>
          <div className={styles.sectionHead}>風險摘要 RISK SUMMARY</div>
          <div className={styles.riskSummaryText} data-testid="pause-risk-summary">
            {riskSummary}
          </div>
          <label className={styles.checkboxRow} htmlFor="pause-risk-ack">
            <input
              checked={riskAcknowledged}
              data-testid="pause-risk-ack"
              id="pause-risk-ack"
              onChange={(e) => setRiskAcknowledged(e.target.checked)}
              type="checkbox"
            />
            <span>我已閱讀並了解上述風險，確認執行暫停 SLA 操作（寫入 Audit 歷程）</span>
          </label>
        </div>
      </div>

      <div className={styles.dialogFooter}>
        <button
          className={styles.secondaryButton}
          disabled={busy}
          onClick={safeClose}
          type="button"
        >
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="pause-submit-btn"
          disabled={busy}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "寫入中…" : "確認暫停 SLA"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}
