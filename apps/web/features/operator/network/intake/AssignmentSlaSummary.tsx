import type { AssistedIntake } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";

export type SlaStatusState = "ON_TRACK" | "DUE_SOON" | "OVERDUE" | "BREACHED" | "PAUSED" | "COMPLETED" | "UNAVAILABLE";

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
  assignmentResourceVersion?: number | null;
  slaResourceVersion?: number | null;
  className?: string;
}

/** Present the read model, never a browser-clock approximation of SLA policy. */
export function computeSlaState(record: AssistedIntake): SlaStatusState {
  switch (record.slaState) {
    case "ON_TRACK":
    case "DUE_SOON":
    case "OVERDUE":
    case "BREACHED":
    case "PAUSED":
    case "COMPLETED":
      return record.slaState;
    default:
      return "UNAVAILABLE";
  }
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
  COMPLETED: {
    label: "已完成 (Completed)",
    icon: "✓",
    pattern: "[✓ COMPLETED]",
    toneClass: "neutral",
  },
  UNAVAILABLE: {
    label: "UNAVAILABLE",
    icon: "?",
    pattern: "[? UNAVAILABLE]",
    toneClass: "neutral",
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
  assignmentResourceVersion = null,
  slaResourceVersion = null,
  className,
}: AssignmentSlaSummaryProps) {
  const slaState = computeSlaState(record);
  const slaInfo = SLA_STATE_MAP[slaState];

  const currentOwner = record.owner || (record as any).assignedOwner || "UNAVAILABLE";
  const assignedQueue = (record as any).assignedQueue || (record as any).target_owner_role || "UNAVAILABLE";
  const assignmentId = record.assignmentId || "UNAVAILABLE";
  const assignmentStatus = record.assignmentStatus || "UNAVAILABLE";
  const assignmentStatusKnown = [
    "UNASSIGNED", "ASSIGNED", "TRANSFERRED", "ESCALATED", "CLAIMED", "COMPLETED",
  ].includes(assignmentStatus);
  const slaInstanceId = record.slaInstanceId || "UNAVAILABLE";
  const raw = record as AssistedIntake & { dueAt?: unknown; slaDueAt?: unknown };
  const dueAtString = raw.dueAt ?? raw.slaDueAt;
  const dueTime = typeof dueAtString === "string" && dueAtString.trim() ? Date.parse(dueAtString) : NaN;
  const formattedDueAt = Number.isFinite(dueTime) ? new Date(dueTime).toLocaleString("zh-TW") : "UNAVAILABLE";

  const isPaused = slaState === "PAUSED";
  const canPauseState = slaState === "ON_TRACK" || slaState === "DUE_SOON" || slaState === "OVERDUE";
  const historyItems: any[] = (record as any).assignmentHistory || (record as any).slaHistory || [];

  return (
    <div
      className={`${styles.sectionBox} ${styles.assignmentSummary} ${className || ""}`}
      data-testid="assignment-sla-summary"
    >
      <div className={styles.sectionHead}>
        指派與 SLA 狀態 (ASSIGNMENT & SLA SUMMARY)
      </div>

      <div className={styles.assignmentCards}>
        {/* Owner Card */}
        <div className={styles.assignmentCard}>
          <div style={{ fontSize: "11px", color: "#64748b", fontWeight: 600 }}>目前 Owner / 負責人</div>
          <div style={{ fontSize: "14px", fontWeight: 700, marginTop: "2px" }} data-testid="asg-owner">
            {currentOwner}
          </div>
          <div style={{ fontSize: "10.5px", color: "#475569" }}>佇列：{assignedQueue}</div>
          <div style={{ fontSize: "10.5px", color: "#475569" }}>Assignment ID：{assignmentId}</div>
          <div style={{ fontSize: "10.5px", color: "#475569" }}>Assignment Status：{assignmentStatus}</div>
        </div>

        {/* SLA Status Card with Text + Icon/Pattern */}
        <div className={styles.assignmentCard}>
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
          <div style={{ fontSize: "10.5px", color: "#475569" }}>到期時間：{formattedDueAt}</div>
          <div style={{ fontSize: "10.5px", color: "#475569" }}>SLA Instance ID：{slaInstanceId}</div>
        </div>
      </div>

      {assignmentId === "UNAVAILABLE" || !assignmentStatusKnown ? (
        <div className={styles.emptyState} data-testid="assignment-action-unavailable" role="status">
          ASSIGNMENT ACTIONS: UNAVAILABLE — 後端未提供可操作的 assignment ID 與狀態。
        </div>
      ) : null}
      {slaInstanceId === "UNAVAILABLE" || slaState === "UNAVAILABLE" ? (
        <div className={styles.emptyState} data-testid="sla-action-unavailable" role="status">
          SLA ACTIONS: UNAVAILABLE — 後端未提供可操作的 SLA instance ID 與明確狀態。
        </div>
      ) : null}
      {assignmentResourceVersion === null ? (
        <div className={styles.emptyState} data-testid="assignment-resource-version-unavailable" role="status">
          RESOURCE_VERSION_UNAVAILABLE — assignment resource version 未由權威收據或 read model 提供。
        </div>
      ) : null}
      {slaResourceVersion === null ? (
        <div className={styles.emptyState} data-testid="sla-resource-version-unavailable" role="status">
          RESOURCE_VERSION_UNAVAILABLE — SLA resource version 未由權威收據或 read model 提供。
        </div>
      ) : null}

      {/* SLA History / Log if present */}
      {historyItems.length > 0 && (
        <div style={{ marginTop: "10px", padding: "8px", background: "#ffffff", borderRadius: "4px", border: "1px dashed #cbd5e1" }}>
          <div style={{ fontSize: "11px", fontWeight: 600, color: "#475569" }}>指派與 SLA 異動歷程 ({historyItems.length})</div>
          <ul style={{ margin: "4px 0 0 0", paddingLeft: "18px", fontSize: "11px", color: "#334155" }}>
            {historyItems.map((item, idx) => (
              <li key={idx} style={{ marginBottom: "2px" }}>
                <span>[{item.timestamp || item.occurredAt || "UNAVAILABLE"}] </span>
                <strong>{item.action || item.type || "UNAVAILABLE"}: </strong>
                <span>{item.note || item.reason || item.description || "UNAVAILABLE"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action Buttons */}
      <div className={styles.assignmentActions}>
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

        {canPauseState && onOpenPause && (
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
