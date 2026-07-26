import { useState, useEffect } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";

const TARGET_OPTIONS = [
  { id: "actor-mgr", name: "吳孟哲（展店主管）", role: "expansion-manager" },
  { id: "actor-steward", name: "周育安（資料管理員）", role: "data-steward" },
  { id: "actor-staff", name: "許庭瑜（展店）", role: "expansion-staff" },
  { id: "gov-queue", name: "治理覆核佇列", role: "site-reviewer" },
];

export function IntakeAssignmentSlaDialog({
  busy,
  error,
  kind,
  onClose,
  onSubmit,
  record,
  onConflictRefresh,
}: {
  busy: boolean;
  error: IntakeApiError | null;
  kind: "transfer" | "pause";
  onClose: () => void;
  onSubmit: (payload: any) => void;
  record: AssistedIntake;
  onConflictRefresh?: () => void;
}) {
  const [targetId, setTargetId] = useState(TARGET_OPTIONS[0].id);
  const [handoffNote, setHandoffNote] = useState("");
  const [reason, setReason] = useState("");

  // Default expected resume time: tomorrow at 09:00 local time
  const getTomorrowMorning = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    // Format as YYYY-MM-DDTHH:MM
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const hours = String(d.getHours()).padStart(2, "0");
    const minutes = String(d.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day}T${hours}:${minutes}`;
  };

  const [resumeTime, setResumeTime] = useState(getTomorrowMorning());
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

  const selectedTarget = TARGET_OPTIONS.find((o) => o.id === targetId) || TARGET_OPTIONS[0];

  const title = kind === "transfer" ? "轉交收件（Transfer）" : "暫停 SLA（Pause）";
  const noteLabel =
    kind === "transfer"
      ? "Handoff note（必填 — 寫入 Audit 與指派歷程）"
      : "核准原因（必填 — 顯示於 SLA 歷程）";

  const riskSummary =
    kind === "transfer"
      ? `將收件 ${record.id} 轉交給 ${selectedTarget.name}。` +
        `此操作會變更指派的處理者與責任。前後值會寫入 Audit。`
      : `將收件 ${record.id} 的 SLA 暫停，預計於 ${resumeTime.replace("T", " ")} 恢復。` +
        `此操作會暫停處理時限計時。前後值會寫入 Audit。`;

  function handleSubmit() {
    if (busy) return;
    setLocalError(null);

    if (kind === "transfer") {
      if (!handoffNote.trim()) {
        setLocalError("請輸入 Handoff note。");
        return;
      }
      if (!reason.trim()) {
        setLocalError("請輸入轉交原因。");
        return;
      }
    } else {
      if (!reason.trim()) {
        setLocalError("請輸入暫停 SLA 的原因。");
        return;
      }
      if (!resumeTime) {
        setLocalError("請輸入預計恢復時間。");
        return;
      }
    }

    if (!riskAcknowledged) {
      setLocalError("請先勾選確認你已閱讀並了解上述風險。");
      return;
    }

    if (kind === "transfer") {
      onSubmit({
        target_owner_subject_id: selectedTarget.id,
        target_owner_role: selectedTarget.role,
        handoff_note: handoffNote.trim(),
        reason: reason.trim(),
        riskSummary,
        riskAcknowledged,
      });
    } else {
      // Convert local time string to ISO string with timezone
      const isoResumeAt = new Date(resumeTime).toISOString();
      onSubmit({
        expected_resume_at: isoResumeAt,
        reason: reason.trim(),
        riskSummary,
        riskAcknowledged,
      });
    }
  }

  const shownError = localError || error?.summary || null;
  const isConflict = error?.code === "ODP-INTAKE-CONFLICT" || error?.status === 409;

  return (
    <IntakeDialogShell
      ariaLabel={title}
      className={styles.panelNarrow}
      onClose={onClose}
      screenLabel="Dialog 轉交／暫停"
      stacked
      testId="intake-assignment-sla-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="intake-asg-title">
          {title}
        </span>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
          ×
        </button>
      </div>

      <div className={styles.dialogBody}>
        {kind === "transfer" ? (
          <>
            <div>
              <label className={styles.fieldLabel} htmlFor="intake-asg-to">
                轉交對象
              </label>
              <select
                className={styles.select}
                data-testid="intake-asg-to"
                id="intake-asg-to"
                onChange={(e) => setTargetId(e.target.value)}
                value={targetId}
              >
                {TARGET_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className={styles.fieldLabel} htmlFor="intake-asg-note">
                {noteLabel}
              </label>
              <textarea
                className={styles.textarea}
                data-testid="intake-asg-note"
                id="intake-asg-note"
                onChange={(e) => setHandoffNote(e.target.value)}
                placeholder="請輸入轉交工作交接說明..."
                rows={2}
                value={handoffNote}
              />
            </div>

            <div>
              <label className={styles.fieldLabel} htmlFor="intake-asg-reason">
                轉交原因
              </label>
              <textarea
                className={styles.textarea}
                data-testid="intake-asg-reason"
                id="intake-asg-reason"
                onChange={(e) => setReason(e.target.value)}
                placeholder="例：此物件為連鎖超商，轉交給展店主管覆核..."
                rows={2}
                value={reason}
              />
            </div>
          </>
        ) : (
          <>
            <div>
              <label className={styles.fieldLabel} htmlFor="intake-asg-resume">
                預計恢復時間
              </label>
              <input
                className={styles.input}
                data-testid="intake-asg-resume"
                id="intake-asg-resume"
                onChange={(e) => setResumeTime(e.target.value)}
                type="datetime-local"
                value={resumeTime}
              />
            </div>

            <div>
              <label className={styles.fieldLabel} htmlFor="intake-asg-reason">
                {noteLabel}
              </label>
              <textarea
                className={styles.textarea}
                data-testid="intake-asg-reason"
                id="intake-asg-reason"
                onChange={(e) => setReason(e.target.value)}
                placeholder="例：等待房東提供完整租約證明，暫停時效計時..."
                rows={3}
                value={reason}
              />
            </div>
          </>
        )}

        {isConflict && onConflictRefresh ? (
          <div className={styles.errorPanel} data-testid="intake-asg-conflict" role="alert">
            <span className={styles.errorSummary}>
              409 OWNER_CONFLICT — 此收件的 owner 在你開啟後已變更（升級處理中）
            </span>
            <span className={styles.errorMeta}>
              目前 owner：{record.owner || "未指定"}　·　版本 v{record.version || 1}
            </span>
            <span className={styles.errorNext}>
              重新整理套用最新狀態後再送出 — 你的輸入已保留。
            </span>
            <button
              className={styles.secondaryButton}
              onClick={onConflictRefresh}
              style={{ marginTop: "6px", color: "#b3261e", borderColor: "#f3cbc7" }}
              type="button"
            >
              重新整理並套用最新 owner／版本
            </button>
          </div>
        ) : shownError ? (
          <div className={styles.errorPanel} data-testid="intake-asg-error" role="alert">
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
          <div className={styles.riskSummaryText} data-testid="intake-asg-risk-summary">
            {riskSummary}
          </div>
          <label className={styles.checkboxRow} htmlFor="intake-asg-risk-ack">
            <input
              checked={riskAcknowledged}
              data-testid="intake-asg-risk-ack"
              id="intake-asg-risk-ack"
              onChange={(e) => setRiskAcknowledged(e.target.checked)}
              type="checkbox"
            />
            <span>我已閱讀並了解上述風險，確認執行此操作（將連同此摘要寫入 Audit）</span>
          </label>
        </div>
      </div>

      <div className={styles.dialogFooter}>
        <button className={styles.secondaryButton} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="intake-asg-submit"
          disabled={busy}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "寫入中…" : title}
        </button>
      </div>
    </IntakeDialogShell>
  );
}
