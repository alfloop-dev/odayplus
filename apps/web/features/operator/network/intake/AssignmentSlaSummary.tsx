import styles from "./intake.module.css";
import type {
  AssignmentLifecycleReceipt,
  IntakeLifecycleAction,
  PersistedLifecycleTransition,
  SlaLifecycleReceipt,
} from "./useIntakeLifecycle";
import { formatIntakeDateTime } from "./types";

export type SlaStatusState =
  | "ON_TRACK"
  | "DUE_SOON"
  | "OVERDUE"
  | "BREACHED"
  | "PAUSED"
  | "COMPLETED";

export interface AssignmentSlaSummaryProps {
  assignment?: AssignmentLifecycleReceipt | null;
  sla?: SlaLifecycleReceipt | null;
  history?: PersistedLifecycleTransition[];
  allowedActions?: readonly IntakeLifecycleAction[];
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

export const SLA_STATE_MAP: Record<
  SlaStatusState,
  { label: string; icon: string; pattern: string; toneClass: string }
> = {
  ON_TRACK: { label: "正常 (On Track)", icon: "✓", pattern: "[✓ ON TRACK]", toneClass: "good" },
  DUE_SOON: { label: "即將到期 (Due Soon)", icon: "⚠", pattern: "[⚠ DUE SOON]", toneClass: "watch" },
  OVERDUE: { label: "已逾期 (Overdue)", icon: "‼", pattern: "[‼ OVERDUE]", toneClass: "risk" },
  BREACHED: { label: "已違反 SLA (Breached)", icon: "🔥", pattern: "[🔥 BREACHED]", toneClass: "risk" },
  PAUSED: { label: "已暫停 (Paused)", icon: "⏸", pattern: "[⏸ PAUSED]", toneClass: "info" },
  COMPLETED: { label: "已完成 (Completed)", icon: "✓", pattern: "[✓ COMPLETED]", toneClass: "good" },
};

function actionAllowed(
  action: IntakeLifecycleAction,
  allowedActions: readonly IntakeLifecycleAction[] | undefined,
  callback: (() => void) | undefined,
): boolean {
  if (!callback) return false;
  return allowedActions?.includes(action) ?? false;
}

export function AssignmentSlaSummary({
  assignment = null,
  sla = null,
  history = [],
  allowedActions,
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
  const slaState = sla?.state as SlaStatusState | undefined;
  const slaInfo = slaState ? SLA_STATE_MAP[slaState] : null;
  const currentOwner =
    assignment?.owner_display_name ??
    assignment?.owner_subject_id ??
    "API 未回傳";
  const queue = assignment?.queue_name ?? "API 未回傳";
  const dueAt = sla?.due_at ?? assignment?.due_at ?? null;
  const persistedHistory = [...history].sort(
    (left, right) =>
      new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime(),
  );

  const canClaim = actionAllowed("CLAIM_ASSIGNMENT", allowedActions, onClaim);
  const canTransfer = actionAllowed("TRANSFER_ASSIGNMENT", allowedActions, onOpenTransfer);
  const canPause = actionAllowed("PAUSE_SLA", allowedActions, onOpenPause);
  const canResume = actionAllowed("RESUME_SLA", allowedActions, onResume);
  const canEscalate = actionAllowed("ESCALATE_ASSIGNMENT", allowedActions, onEscalate);
  const canComplete = actionAllowed("COMPLETE_ASSIGNMENT", allowedActions, onComplete);

  return (
    <section
      aria-label="指派與 SLA"
      className={`${styles.sectionBox} ${className || ""}`}
      data-current-user={currentUserId}
      data-role={userRole}
      data-testid="assignment-sla-summary"
    >
      <div className={styles.sectionHead}>
        指派與 SLA 狀態 ASSIGNMENT &amp; SLA
        <span className={styles.chip} data-tone={slaInfo?.toneClass ?? "neutral"}>
          {slaInfo?.pattern ?? "[? UNAVAILABLE]"}
        </span>
      </div>

      <div className={styles.metaGrid}>
        <div>
          <span className={styles.metaCaption}>Assignment status</span>
          <div className={styles.metaValue} data-testid="asg-status">
            {assignment?.status ?? "UNASSIGNED"}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Owner</span>
          <div className={styles.metaValue} data-testid="asg-owner">
            {currentOwner}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Queue</span>
          <div className={styles.metaValue} data-testid="asg-queue">
            {queue}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Assigned at</span>
          <div className={styles.metaValue} data-testid="asg-assigned-at">
            <AssignmentTime value={assignment?.assigned_at} />
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Claimed at</span>
          <div className={styles.metaValue} data-testid="asg-claimed-at">
            <AssignmentTime value={assignment?.claimed_at} />
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Due at</span>
          <div className={styles.metaValue} data-testid="asg-due-at">
            <AssignmentTime value={dueAt} />
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>SLA state</span>
          <div className={styles.metaValue} data-testid="asg-sla-status">
            {slaInfo && slaState ? (
              <>
                <span aria-hidden="true">{slaInfo.icon}</span> {slaState} · {slaInfo.label}
              </>
            ) : (
              "API 未回傳"
            )}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Expected resume</span>
          <div className={styles.metaValue} data-testid="asg-expected-resume">
            <AssignmentTime value={sla?.expected_resume_at} />
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Escalation level</span>
          <div className={styles.metaValue} data-testid="asg-escalation-level">
            {sla?.escalation_level ?? "無"}
          </div>
        </div>
      </div>

      {persistedHistory.length ? (
        <ol className={styles.timeline} data-testid="asg-history">
          {persistedHistory.map((entry) => (
            <li className={styles.timelineItem} key={entry.transition_id}>
              <span className={styles.timelineMark} aria-hidden="true">
                {entry.to_state === "ESCALATED" || entry.to_state === "BREACHED" ? "!" : "✓"}
              </span>
              <div className={styles.timelineContent}>
                <div className={styles.timelineTitle}>
                  {entry.stream ?? "ASSIGNMENT"} · {entry.from_state ?? "—"} → {entry.to_state}
                </div>
                <div className={styles.timelineMeta}>
                  <AssignmentTime value={entry.occurred_at} /> · {entry.actor || "API 未回傳 actor"}
                  {entry.actor_role ? ` (${entry.actor_role})` : ""} · v{entry.version_after}
                </div>
                <div className={styles.timelineMeta}>
                  {entry.reason ?? entry.reason_code ?? "API 未回傳原因"}
                  {entry.owner_subject_id ? ` · Owner ${entry.owner_subject_id}` : ""}
                  {entry.queue_name ? ` · Queue ${entry.queue_name}` : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className={styles.noteBox} data-testid="asg-history-unavailable">
          伺服器尚未回傳 assignment/SLA history；不從目前 owner 或 due time 推算歷程。
        </div>
      )}

      <div className={styles.actionRow} data-testid="asg-direct-actions">
        {canClaim ? (
          <button
            className={styles.primaryButton}
            data-testid="asg-btn-claim"
            disabled={busy}
            onClick={onClaim}
            type="button"
          >
            {busy ? "處理中…" : "認領 Claim"}
          </button>
        ) : null}
        {canTransfer ? (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-transfer"
            disabled={busy}
            onClick={onOpenTransfer}
            type="button"
          >
            轉交 Transfer
          </button>
        ) : null}
        {slaState && slaState !== "PAUSED" && canPause ? (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-pause"
            disabled={busy}
            onClick={onOpenPause}
            type="button"
          >
            暫停 SLA
          </button>
        ) : null}
        {slaState === "PAUSED" && canResume ? (
          <button
            className={styles.primaryButton}
            data-testid="asg-btn-resume"
            disabled={busy}
            onClick={onResume}
            type="button"
          >
            恢復 SLA
          </button>
        ) : null}
        {canEscalate ? (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-escalate"
            disabled={busy}
            onClick={onEscalate}
            type="button"
          >
            升級 Escalate
          </button>
        ) : null}
        {canComplete ? (
          <button
            className={styles.secondaryButton}
            data-testid="asg-btn-complete"
            disabled={busy}
            onClick={onComplete}
            type="button"
          >
            標記完成
          </button>
        ) : null}
      </div>
    </section>
  );
}

function AssignmentTime({ value }: { value?: string | null }) {
  const formatted = formatIntakeDateTime(value);
  return formatted && value ? (
    <time dateTime={value} title={formatted.title}>
      {formatted.text}
    </time>
  ) : (
    <>API 未回傳</>
  );
}
