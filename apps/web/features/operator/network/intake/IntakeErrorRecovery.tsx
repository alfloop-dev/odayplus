"use client";

import { useState } from "react";
import type { ApiError, ConflictError } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type { IntakeApiError } from "./intakeClient";

export type IntakeErrorRecoveryProps = {
  error?: IntakeApiError | ApiError | ConflictError | null;
  stage?: string;
  correlationId?: string;
  preservedInput?: Record<string, unknown> | null;
  onRetry?: (overrides?: { overrideRetryBudget?: boolean; riskAcknowledged?: boolean }) => void;
  onReplayDlq?: (jobId?: string) => void;
  onCancel?: () => void;
  onOverride?: (reason: string) => void;
  onCorrectInput?: (fieldKey?: string) => void;
  testId?: string;
};

export function IntakeErrorRecovery({
  error,
  stage = "FAILED",
  correlationId,
  preservedInput,
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

  const errorCode = error?.code ?? "ERR_PARSE_MALFORMED_HTML";
  const errorMessage = error?.message ?? "收件解析過程發生未預期的結構異常或連線中斷。";
  const isRetryable = error?.retryable ?? true;
  const corrId = error?.correlation_id ?? correlationId ?? "CORR-ERR-991204";
  const occurredAt = "occurred_at" in (error ?? {}) ? (error as ApiError).occurred_at : new Date().toISOString();
  const nextAction = error?.next_action ?? "RETRY";
  const currentVersion = "current_version" in (error ?? {}) ? (error as ConflictError).current_version : 1;

  // Mask credential/sensitive class data from preserved input (Purpose Binding enforcement)
  const sanitizePreservedInput = (obj?: Record<string, unknown> | null): Record<string, unknown> => {
    if (!obj) return { url: "https://example.com/item/10492", rawText: "<html_snapshot_data>" };
    const clean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      const lower = k.toLowerCase();
      if (lower.includes("token") || lower.includes("password") || lower.includes("secret") || lower.includes("cred")) {
        clean[k] = "•••••••• [REDACTED_PURPOSE_BINDING]";
      } else {
        clean[k] = v;
      }
    }
    return clean;
  };

  const safeInput = sanitizePreservedInput(preservedInput);

  const handleConfirmOverride = () => {
    if (!overrideReason.trim() || !riskAcknowledged) return;
    onOverride?.(overrideReason);
    setOverrideModalOpen(false);
    setOverrideReason("");
    setRiskAcknowledged(false);
  };

  return (
    <div
      className={styles.sectionBox}
      data-testid={testId}
      style={{
        border: "1px solid #fca5a5",
        borderRadius: "10px",
        padding: "14px",
        background: "#fff5f5",
        marginBottom: "16px",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "16px" }}>⚠️</span>
          <div>
            <h4 style={{ margin: 0, fontSize: "13px", fontWeight: 700, color: "#991b1b" }}>
              異常恢復與降級控制 ERROR RECOVERY & DLQ
            </h4>
            <span style={{ fontSize: "10.5px", color: "#7f1d1d" }}>
              階段: <strong data-testid="error-stage">{stage}</strong>
            </span>
          </div>
        </div>

        <span
          style={{
            fontSize: "10px",
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: "999px",
            background: isRetryable ? "#fef3c7" : "#fee2e2",
            color: isRetryable ? "#92400e" : "#b91c1c",
          }}
          data-testid="error-retryable-badge"
        >
          {isRetryable ? "↺ 可自動重試 (Retryable)" : "🚫 不可直接重試 (Non-retryable)"}
        </span>
      </div>

      {/* Main Error Box */}
      <div style={{ background: "#ffffff", border: "1px solid #fecaca", borderRadius: "8px", padding: "12px", marginBottom: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
          <span style={{ fontFamily: "monospace", fontWeight: 700, color: "#b91c1c", fontSize: "12px" }} data-testid="error-code">
            [{errorCode}]
          </span>
          <span style={{ fontSize: "10.5px", color: "#64748b" }}>
            發生時間: {new Date(occurredAt).toLocaleString()}
          </span>
        </div>

        <p style={{ margin: "0 0 8px 0", fontSize: "12px", color: "#334155", lineHeight: "1.5" }} data-testid="error-message">
          {errorMessage}
        </p>

        <div style={{ display: "flex", gap: "16px", fontSize: "10.5px", color: "#64748b", borderTop: "1px dashed #fca5a5", paddingTop: "6px" }}>
          <div>Correlation ID: <code style={{ color: "#1e293b" }} data-testid="error-correlation-id">{corrId}</code></div>
          <div>目前版本: <code>v{currentVersion}</code></div>
          <div>建議處置: <strong style={{ color: "#2563eb" }}>{nextAction}</strong></div>
        </div>
      </div>

      {/* Preserved Input Drawer */}
      <div style={{ marginBottom: "12px" }}>
        <button
          type="button"
          onClick={() => setShowPreservedInput(!showPreservedInput)}
          className={styles.secondaryButton}
          style={{ padding: "4px 10px", fontSize: "11px" }}
          data-testid="error-toggle-preserved-input"
        >
          {showPreservedInput ? "▼ 隱藏保留輸入參數 (Preserved Input)" : "▶ 展開保留輸入參數 (Preserved Input)"}
        </button>

        {showPreservedInput && (
          <div
            data-testid="error-preserved-input-box"
            style={{
              marginTop: "8px",
              padding: "10px",
              background: "#1e293b",
              color: "#f8fafc",
              borderRadius: "6px",
              fontFamily: "monospace",
              fontSize: "10.5px",
              maxHeight: "150px",
              overflowY: "auto",
            }}
          >
            <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(safeInput, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Action Toolbar */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", justifyContent: "flex-end" }}>
        {onRetry && isRetryable && (
          <button
            type="button"
            onClick={() => onRetry()}
            className={styles.primaryButton}
            style={{ padding: "5px 12px", fontSize: "11.5px" }}
            data-testid="error-action-retry"
          >
            ↺ 立即重試 (Retry)
          </button>
        )}

        {onReplayDlq && (
          <button
            type="button"
            onClick={() => onReplayDlq()}
            className={styles.secondaryButton}
            style={{ padding: "5px 12px", fontSize: "11.5px", background: "#fef3c7", borderColor: "#fde68a", color: "#92400e" }}
            data-testid="error-action-replay-dlq"
          >
            ⚡ 重播 DLQ (Replay DLQ)
          </button>
        )}

        {onCorrectInput && (
          <button
            type="button"
            onClick={() => onCorrectInput()}
            className={styles.secondaryButton}
            style={{ padding: "5px 12px", fontSize: "11.5px" }}
            data-testid="error-action-correct"
          >
            ✏️ 修正欄位 (Correct Input)
          </button>
        )}

        {onOverride && (
          <button
            type="button"
            onClick={() => setOverrideModalOpen(true)}
            className={styles.secondaryButton}
            style={{ padding: "5px 12px", fontSize: "11.5px", borderColor: "#fca5a5", color: "#b91c1c" }}
            data-testid="error-action-override"
          >
            ⚠️ 強制通過 (Override & Proceed)
          </button>
        )}

        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className={styles.secondaryButton}
            style={{ padding: "5px 12px", fontSize: "11.5px" }}
            data-testid="error-action-cancel"
          >
            ✕ 取消收件 (Cancel)
          </button>
        )}
      </div>

      {/* Override Reason Modal */}
      {overrideModalOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          data-testid="error-override-modal"
        >
          <div style={{ background: "#ffffff", padding: "18px", borderRadius: "10px", width: "420px", maxWidth: "90%" }}>
            <h4 style={{ margin: "0 0 8px 0", fontSize: "14px", color: "#b91c1c" }}>⚠️ 強制覆核與風控確認 (Override)</h4>
            <p style={{ fontSize: "11.5px", color: "#475569", margin: "0 0 10px 0" }}>
              強制繞過解析異常將記入資安稽核日誌。請輸入必要處置理由並勾選風控承諾。
            </p>

            <textarea
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="請輸入強制通過之業務理由..."
              rows={3}
              style={{ width: "100%", padding: "8px", fontSize: "11.5px", borderRadius: "6px", border: "1px solid #cbd5e1", marginBottom: "10px" }}
              data-testid="override-reason-input"
            />

            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", color: "#1e293b", marginBottom: "14px" }}>
              <input
                type="checkbox"
                checked={riskAcknowledged}
                onChange={(e) => setRiskAcknowledged(e.target.checked)}
                data-testid="override-risk-checkbox"
              />
              <span>我了解並承擔強制通過此收件之資料安全與一致性風險</span>
            </label>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                onClick={() => setOverrideModalOpen(false)}
                className={styles.secondaryButton}
                style={{ padding: "4px 12px", fontSize: "11.5px" }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmOverride}
                disabled={!overrideReason.trim() || !riskAcknowledged}
                className={styles.primaryButton}
                style={{ padding: "4px 12px", fontSize: "11.5px", background: "#b91c1c", borderColor: "#b91c1c" }}
                data-testid="override-submit-button"
              >
                確認強制通過 (Submit Override)
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
