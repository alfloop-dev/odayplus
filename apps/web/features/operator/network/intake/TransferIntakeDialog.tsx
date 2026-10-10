import { useState, useEffect, useCallback } from "react";
import type { AssistedIntake, OdpApiClient } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { IntakeDialogShell } from "./IntakeDialogShell";
import { intakeApi, type IntakeApiError } from "./intakeClient";

export interface TransferTargetOption {
  id: string;
  name: string;
  role: string;
}

// Shape guards are not identity/scope authorization. The caller must supply
// fresh, resource-scoped directory results; the server must revalidate on write.
export function usableTransferTargets(options: TransferTargetOption[]): TransferTargetOption[] {
  return options.filter((option) =>
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(option.id) &&
    Boolean(option.name.trim()) && Boolean(option.role.trim()) &&
    options.filter((other) => other.id.toLowerCase() === option.id.toLowerCase()).length === 1,
  );
}

// Only the exact enabled resource/client context can consume a completed read.
// Changing selection, role/client, version or closing the dialog drops authority
// immediately, even before the new effect runs. Late responses are ignored.
export function useAssignmentTransferTargets(
  client: OdpApiClient | null, assignmentId: string | null | undefined,
  version: number | null, enabled: boolean,
) {
  const key = enabled && assignmentId && version !== null ? `${assignmentId}:v${version}` : null;
  const [generation, setGeneration] = useState(0);
  const [directory, setDirectory] = useState<{
    client: OdpApiClient | null; key: string | null; generation: number;
    state: "loading" | "ready" | "error"; options: TransferTargetOption[]; error: IntakeApiError | null;
  } | null>(null);
  const refreshTargets = useCallback(() => setGeneration((value) => value + 1), []);
  useEffect(() => {
    if (!client || !key || !assignmentId) return;
    let cancelled = false;
    setDirectory({ client, key, generation, state: "loading", options: [], error: null });
    void intakeApi.transferTargets(client, assignmentId).then((result) => {
      if (cancelled) return;
      const matches = result.ok && result.value.assignment_version === version;
      setDirectory({
        client, key, generation, state: matches ? "ready" : "error",
        options: matches && result.ok ? usableTransferTargets(result.value.items) : [],
        error: !result.ok ? result.error : !matches ? {
          status: 409, code: "ODP-INTAKE-CONFLICT", summary: "指派版本已變更，請重新整理指派後再載入對象。",
          nextAction: "重新整理 owner／指派版本。", retryable: false,
          correlationId: null, occurredAt: new Date().toISOString(),
        } : null,
      });
    });
    return () => { cancelled = true; };
  }, [client, key, assignmentId, version, generation]);
  const current = key && directory?.key === key && directory.client === client && directory.generation === generation
    ? directory : null;
  return {
    options: current?.options ?? [],
    state: current?.state ?? (key && client ? "loading" as const : "idle" as const),
    error: current?.error ?? null,
    refreshTargets,
  };
}

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
  /** Assignment concurrency token, never the unrelated intake version. */
  resourceVersion?: number | null;
  onConflictRefresh?: () => void;
  /** No fixture fallback: unavailable directory authority closes submission. */
  targetOptions?: TransferTargetOption[];
  targetLoadState?: "idle" | "loading" | "ready" | "error";
  targetLoadError?: IntakeApiError | null;
  onRefreshTargets?: () => void;
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
  resourceVersion = null,
  onConflictRefresh,
  targetOptions = [],
  targetLoadState = "ready",
  targetLoadError = null,
  onRefreshTargets,
}: TransferIntakeDialogProps) {
  const targets = usableTransferTargets(targetOptions);
  const [targetId, setTargetId] = useState(targets[0]?.id ?? "");
  const [handoffNote, setHandoffNote] = useState("");
  const [acknowledgedTarget, setAcknowledgedTarget] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    const activeBeforeOpen = document.activeElement as HTMLElement | null;
    return () => {
      if (activeBeforeOpen && typeof activeBeforeOpen.focus === "function") {
        activeBeforeOpen.focus();
      }
    };
  }, []);

  // Never silently substitute another person after a directory refresh.
  const selectedTarget = targets.find((option) => option.id === targetId);
  const targetConsentKey = selectedTarget
    ? JSON.stringify([record.id, selectedTarget.id, selectedTarget.name, selectedTarget.role])
    : null;
  const riskAcknowledged = targetConsentKey !== null && acknowledgedTarget === targetConsentKey;
  useEffect(() => {
    setAcknowledgedTarget(null);
    setLocalError(null);
  }, [targetConsentKey]);

  const title = "轉交收件（Transfer）";
  const riskSummary = selectedTarget
    ? `將收件 ${record.id} 轉交給 ${selectedTarget.name}。` +
      `此操作會變更指派的處理者與責任。前後值與交接說明會寫入 Audit 歷程。`
    : "TRANSFER_TARGETS_UNAVAILABLE — 尚無可確認的轉交對象；不會變更負責人。";

  const hasAuthority = Boolean(record.assignmentId) &&
    Number.isSafeInteger(resourceVersion) && (resourceVersion ?? 0) >= 1;
  const versionLabel = hasAuthority ? `v${resourceVersion}` : "UNAVAILABLE";

  function handleSubmit() {
    if (busy || !hasAuthority || !selectedTarget || error?.status === 409 || error?.code === "ODP-INTAKE-CONFLICT") return;
    setLocalError(null);

    if (!handoffNote.trim()) {
      setLocalError("請輸入 Handoff note（工作交接說明）。");
      return;
    }

    if (!riskAcknowledged) {
      setLocalError("請先勾選確認你已閱讀並了解上述風險。");
      return;
    }

    onSubmit({
      target_owner_subject_id: selectedTarget.id,
      target_owner_role: selectedTarget.role,
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
      onClose={onClose}
      screenLabel="Dialog 轉交收件"
      stacked
      testId="transfer-intake-dialog"
    >
      <div className={styles.dialogHead}>
        <span className={styles.dialogTitle} data-testid="transfer-dialog-title">
          {title}
        </span>
        <button aria-label="關閉" className={styles.dialogClose} onClick={onClose} type="button">
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
          <span data-testid="transfer-record-version">{versionLabel}</span>
        </div>

        <div>
          <label className={styles.fieldLabel} htmlFor="transfer-target-select">
            轉交對象
          </label>
          <select
            className={styles.select}
            data-testid="transfer-target-select"
            id="transfer-target-select"
            disabled={busy || targets.length === 0}
            onChange={(e) => setTargetId(e.target.value)}
            value={selectedTarget?.id ?? ""}
            aria-describedby={!selectedTarget ? "transfer-targets-unavailable" : undefined}
          >
            <option value="" disabled>請選擇可用對象</option>
            {targets.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.name}
              </option>
            ))}
          </select>
          {!selectedTarget ? (
            <div className={styles.noteBox} data-testid="transfer-targets-unavailable"
              id="transfer-targets-unavailable" role="status">
              {targetLoadState === "loading" ? "正在載入此指派的權威轉交對象…" :
                targetLoadError?.summary || "TRANSFER_TARGETS_UNAVAILABLE — 尚無可轉交的對象，或原選擇已不可用。請重新整理權威指派與身分範圍；不使用示範人物或治理佇列代替。"}
            </div>
          ) : null}
        </div>

        {onRefreshTargets ? (
          <button className={styles.secondaryButton} type="button" onClick={onRefreshTargets}
            disabled={busy || targetLoadState === "loading"} data-testid="transfer-targets-refresh">
            重新載入轉交對象
          </button>
        ) : null}

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
              {error?.summary || "409 OWNER_CONFLICT — 此收件的 owner 在你開啟後已變更"}
            </span>
            <span className={styles.errorMeta}>
              目前 owner：{record.owner || "未指定"} · 指派版本 {versionLabel}
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
              disabled={busy || !selectedTarget}
              onChange={(e) => setAcknowledgedTarget(e.target.checked ? targetConsentKey : null)}
              type="checkbox"
            />
            <span>我已閱讀並了解上述風險，確認執行轉交操作（寫入 Audit 歷程）</span>
          </label>
        </div>
      </div>

      <div className={styles.dialogFooter}>
        <button className={styles.secondaryButton} onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primaryButton}
          data-testid="transfer-submit-btn"
          disabled={busy || !hasAuthority || !selectedTarget || isConflict}
          onClick={handleSubmit}
          type="button"
        >
          {busy ? "寫入中…" : "確認轉交"}
        </button>
      </div>
    </IntakeDialogShell>
  );
}
