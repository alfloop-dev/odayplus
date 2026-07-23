import { useState } from "react";
import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import type { IntakeApiError } from "./intakeClient";

export interface TransferTargetOption {
  id: string;
  name: string;
  role: string;
}

export const DEFAULT_TRANSFER_TARGETS: TransferTargetOption[] = [];

const TRANSFER_ROLES = [
  ["expansion-staff", "展店專員"],
  ["expansion-manager", "展店經理"],
  ["data-steward", "資料管理員"],
  ["governance-reviewer", "治理審查員"],
] as const;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface TransferIntakeDialogProps {
  busy: boolean;
  error: IntakeApiError | null;
  onClose: () => void;
  onSubmit: (payload: {
    target_owner_subject_id: string;
    target_owner_role: string;
    handoff_note: string;
    riskSummary: string;
    riskAcknowledged: boolean;
  }) => void;
  record: AssistedIntake;
  onConflictRefresh?: () => void;
  targetOptions?: TransferTargetOption[];
}

/**
 * TransferIntakeDialog satisfies VDC-001:
 * Transfer contains target and handoff note only.
 * No separate reason field or resume time.
 */
export function TransferIntakeDialog({
  busy,
  error,
  onClose,
  onSubmit,
  record,
  onConflictRefresh,
  targetOptions = DEFAULT_TRANSFER_TARGETS,
}: TransferIntakeDialogProps) {
  const [targetId, setTargetId] = useState(targetOptions[0]?.id || "");
  const [targetRole, setTargetRole] = useState(
    targetOptions[0]?.role || "data-steward",
  );
  const [handoffNote, setHandoffNote] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const selectedTarget = targetOptions.find((option) => option.id === targetId);
  const targetLabel =
    selectedTarget?.name ??
    (targetId ? `${targetRole} (${targetId})` : "尚未指定 canonical subject");

  const title = "轉交收件（Transfer）";
  const riskSummary =
    `將收件 ${record.id} 轉交給 ${targetLabel}。` +
    `此操作會變更指派的處理者與責任。前後值與交接說明會寫入 Audit 歷程。`;

  function safeClose() {
    if (!busy) onClose();
  }

  function handleSubmit() {
    if (busy) return;
    setLocalError(null);

    if (!handoffNote.trim()) {
      setLocalError("請輸入 Handoff note（工作交接說明）。");
      return;
    }

    if (!UUID_PATTERN.test(targetId)) {
      setLocalError("請輸入 canonical subject UUID；展示名稱或本機假 ID 不可送出。");
      return;
    }

    if (!riskAcknowledged) {
      setLocalError("請先勾選確認你已閱讀並了解上述風險。");
      return;
    }

    onSubmit({
      target_owner_subject_id: targetId,
      target_owner_role: selectedTarget?.role ?? targetRole,
      handoff_note: handoffNote.trim(),
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
      screenLabel="Dialog 轉交收件"
      stacked
      testId="transfer-intake-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="transfer-dialog-title">
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
          data-testid="transfer-record-info"
        >
          收件編號：<strong>{record.id}</strong> · 目前負責人：
          <strong data-testid="transfer-record-owner">{record.owner || "未指派"}</strong> · 版本：
          <span data-testid="transfer-record-version">v{record.version || 1}</span>
        </div>

        {targetOptions.length > 0 ? (
          <div>
            <label className={styles.fieldLabel} htmlFor="transfer-target-select">
              轉交對象
            </label>
            <select
              className={styles.select}
              data-testid="transfer-target-select"
              id="transfer-target-select"
              onChange={(event) => {
                const option = targetOptions.find(
                  (target) => target.id === event.target.value,
                );
                setTargetId(event.target.value);
                if (option) setTargetRole(option.role);
              }}
              value={targetId}
            >
              {targetOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <>
            <div>
              <label
                className={styles.fieldLabel}
                htmlFor="transfer-target-subject"
              >
                轉交對象 Subject UUID
              </label>
              <input
                className={styles.input}
                data-testid="transfer-target-subject"
                id="transfer-target-subject"
                onChange={(event) => setTargetId(event.target.value.trim())}
                placeholder="00000000-0000-0000-0000-000000000000"
                value={targetId}
              />
            </div>
            <div>
              <label
                className={styles.fieldLabel}
                htmlFor="transfer-target-select"
              >
                轉交角色
              </label>
              <select
                className={styles.select}
                data-testid="transfer-target-select"
                id="transfer-target-select"
                onChange={(event) => setTargetRole(event.target.value)}
                value={targetRole}
              >
                {TRANSFER_ROLES.map(([role, label]) => (
                  <option key={role} value={role}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

        <div>
          <label className={styles.fieldLabel} htmlFor="transfer-handoff-note">
            Handoff note（必填 — 寫入 Audit 與指派歷程）
          </label>
          <textarea
            className={styles.textarea}
            data-testid="transfer-handoff-note"
            id="transfer-handoff-note"
            onChange={(e) => setHandoffNote(e.target.value)}
            placeholder="請輸入轉交工作交接說明..."
            rows={3}
            value={handoffNote}
          />
        </div>

        {isConflict && onConflictRefresh ? (
          <div className={styles.errorPanel} data-testid="transfer-conflict-panel" role="alert">
            <span className={styles.errorSummary}>
              409 OWNER_CONFLICT — 此收件的 owner 在你開啟後已變更
            </span>
            <span className={styles.errorMeta}>
              目前 owner：{record.owner || "未指定"} · 版本 v{record.version || 1}
            </span>
            <span className={styles.errorNext}>
              重新整理套用最新狀態後再送出 — 你的交接說明與選項已保留。
            </span>
            <button
              className={styles.secondaryButton}
              data-testid="transfer-conflict-refresh-btn"
              onClick={onConflictRefresh}
              style={{ marginTop: "6px", color: "#b3261e", borderColor: "#f3cbc7" }}
              type="button"
            >
              重新整理並套用最新 owner／版本
            </button>
          </div>
        ) : shownError ? (
          <div className={styles.errorPanel} data-testid="transfer-error-panel" role="alert">
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
          <div className={styles.riskSummaryText} data-testid="transfer-risk-summary">
            {riskSummary}
          </div>
          <label className={styles.checkboxRow} htmlFor="transfer-risk-ack">
            <input
              checked={riskAcknowledged}
              data-testid="transfer-risk-ack"
              id="transfer-risk-ack"
              onChange={(e) => setRiskAcknowledged(e.target.checked)}
              type="checkbox"
            />
            <span>我已閱讀並了解上述風險，確認執行轉交操作（寫入 Audit 歷程）</span>
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
          data-testid="transfer-submit-btn"
          disabled={busy}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "寫入中…" : "確認轉交"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}
