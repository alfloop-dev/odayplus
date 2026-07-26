import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";

export type SlaStatusState = "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED";

export interface AssignmentSlaSummaryProps {
  record: AssistedIntake;
  busy?: boolean;
  onClaim?: () => void;
  onOpenTransfer?: () => void;
  onOpenPause?: () => void;
  onResume?: () => void;
  onEscalate?: () => void;
  onComplete?: () => void;
  userRole?: string;
  currentUserId?: string;
  className?: string;
}

export function computeSlaState(record: AssistedIntake): SlaStatusState {
  if ((record as any).isSlaPaused || (record as any).slaState === "PAUSED") {
    return "PAUSED";
  }
  if ((record as any).slaState === "BREACHED" || (record as any).isBreached) {
    return "BREACHED";
  }

  const dueAt = (record as any).dueAt || (record as any).slaDueAt;
  if (!dueAt) return "ON_TRACK";

  const dueTime = new Date(dueAt).getTime();
  const now = Date.now();
  const diffMinutes = Math.floor((dueTime - now) / (1000 * 60));

  if (diffMinutes < 0) return "OVERDUE";
  if (diffMinutes <= 60) return "DUE_SOON";
  return "ON_TRACK";
}

/** SLA status display details with text AND icon/pattern for WCAG compliance */
export const SLA_STATE_MAP: Record<
  SlaStatusState,
  { label: string; icon: string; pattern: string; toneClass: string }
> = {
  ON_TRACK: {
    label: "正常 (On Track)",
    icon: "✓",
    pattern: "[✓ ON TRACK]",
    toneClass: "good",
  },
  DUE_SOON: {
    label: "即將到期 (Due Soon)",
    icon: "⚠",
    pattern: "[⚠ DUE SOON]",
    toneClass: "watch",
  },
  OVERDUE: {
    label: "已逾期 (Overdue)",
    icon: "‼",
    pattern: "[‼ OVERDUE]",
    toneClass: "risk",
  },
  BREACHED: {
    label: "違約 (Breached)",
    icon: "🔥",
    pattern: "[🔥 BREACHED]",
    toneClass: "risk",
  },
  PAUSED: {
    label: "已暫停 (Paused)",
    icon: "⏸",
    pattern: "[⏸ PAUSED]",
    toneClass: "info",
  },
};

/**
 * AssignmentSlaSummary component
 * Renders assignment state, SLA timer, owner/queue/due-time/history, and action triggers.
 * SLA presentation uses text + icon/pattern to satisfy WCAG AA (non-color dependent).
 * No optimistic mutations: all operations trigger async callbacks to backend API.
 */
export function AssignmentSlaSummary({
  record,
  busy = false,
  onClaim,
  onOpenTransfer,
  onOpenPause,
  onResume,
  onEscalate,
  onComplete,
  userRole,
  currentUserId,
  className,
}: AssignmentSlaSummaryProps) {
  const slaState = computeSlaState(record);
  const slaInfo = SLA_STATE_MAP[slaState];

  const currentOwner = record.owner || (record as any).assignedOwner || "未指派 (Unassigned)";
  const assignedQueue = (record as any).assignedQueue || (record as any).target_owner_role || "治理覆核佇列";
  const dueAtString = (record as any).dueAt || (record as any).slaDueAt || null;
  const formattedDueAt = dueAtString ? new Date(dueAtString).toLocaleString("zh-TW") : "無時限 (No SLA Limit)";

  const isPaused = slaState === "PAUSED";
  const historyItems: any[] = (record as any).assignmentHistory || (record as any).slaHistory || [];

  return (
    <div
      className={`${styles.sectionBox} ${className || ""}`}
      data-testid="assignment-sla-summary"
    >
      <div className={styles.sectionHead}>
        指派與 SLA 狀態 (ASSIGNMENT & SLA SUMMARY)
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px", margin: "10px 0" }}>
        {/* Owner Card */}
        <div style={{ padding: "8px 12px", background: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: "11px", color: "#64748b", fontWeight: 600 }}>目前 Owner / 負責人</div>
          <div style={{ fontSize: "14px", fontWeight: 700, marginTop: "2px" }} data-testid="asg-owner">
            {currentOwner}
          </div>
          <div style={{ fontSize: "10.5px", color: "#94a3b8" }}>佇列：{assignedQueue}</div>
        </div>

        {/* SLA Status Card with Text + Icon/Pattern */}
        <div style={{ padding: "8px 12px", background: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: "11px", color: "#64748b", fontWeight: 600 }}>SLA 處理狀態</div>
          <div
            style={{ fontSize: "14px", fontWeight: 700, marginTop: "2px", display: "flex", alignItems: "center", gap: "6px" }}
            data-testid="asg-sla-status"
          >
            <span style={{ fontSize: "16px" }} aria-hidden="true">{slaInfo.icon}</span>
            <span>{slaInfo.label}</span>
            <span style={{ fontSize: "10px", padding: "1px 4px", background: "#e2e8f0", borderRadius: "3px", fontFamily: "monospace" }}>
              {slaInfo.pattern}
            </span>
          </div>
          <div style={{ fontSize: "10.5px", color: "#94a3b8" }}>到期時間：{formattedDueAt}</div>
        </div>
      </div>

      {/* SLA History / Log if present */}
      {historyItems.length > 0 && (
        <div style={{ marginTop: "10px", padding: "8px", background: "#ffffff", borderRadius: "4px", border: "1px dashed #cbd5e1" }}>
          <div style={{ fontSize: "11px", fontWeight: 600, color: "#475569" }}>指派與 SLA 異動歷程 ({historyItems.length})</div>
          <ul style={{ margin: "4px 0 0 0", paddingLeft: "18px", fontSize: "11px", color: "#334155" }}>
            {historyItems.map((item, idx) => (
              <li key={idx} style={{ marginBottom: "2px" }}>
                <span>[{item.timestamp || item.occurredAt || "時間未知"}] </span>
                <strong>{item.action || item.type || "變更"}: </strong>
                <span>{item.note || item.reason || item.description || "無說明"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action Buttons */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
        {onClaim && (
          <button
            className={styles.primaryButton}
            data-testid="asg-btn-claim"
            disabled={busy}
            onClick={onClaim}
            type="button"
          >
            {busy ? "處理中…" : "認領 (Claim)"}
          </button>
        )}

        {onOpenTransfer && (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-transfer"
            disabled={busy}
            onClick={onOpenTransfer}
            type="button"
          >
            轉交 (Transfer)
          </button>
        )}

        {!isPaused && onOpenPause && (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-pause"
            disabled={busy}
            onClick={onOpenPause}
            type="button"
          >
            暫停 SLA (Pause)
          </button>
        )}

        {isPaused && onResume && (
          <button
            className={styles.primaryButton}
            data-testid="asg-btn-resume"
            disabled={busy}
            onClick={onResume}
            type="button"
          >
            {busy ? "處理中…" : "恢復 SLA (Resume)"}
          </button>
        )}

        {onEscalate && (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-escalate"
            disabled={busy}
            onClick={onEscalate}
            style={{ color: "#b3261e", borderColor: "#f3cbc7" }}
            type="button"
          >
            升級 (Escalate)
          </button>
        )}

        {onComplete && (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-complete"
            disabled={busy}
            onClick={onComplete}
            type="button"
          >
            標記完成 (Complete)
          </button>
        )}
      </div>
    </div>
  );
}
