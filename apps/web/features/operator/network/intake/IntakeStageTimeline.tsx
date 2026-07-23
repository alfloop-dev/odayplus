"use client";

import type {
  AssistedIntake,
  JobReceipt,
  SlaReceipt,
  TransitionReceipt,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { stageTone } from "./intakeTypes";
import type {
  JobLifecycleReceipt,
  PersistedLifecycleTransition,
  SlaLifecycleReceipt,
} from "./useIntakeLifecycle";

const INTAKE_LABELS: Record<string, string> = {
  SUBMITTED: "已送出",
  CHECKING_IDENTITY: "識別檢查",
  CHECKING_SOURCE_POLICY: "來源政策",
  AWAITING_ASSISTED_ENTRY: "待人工補錄",
  RETRIEVING: "擷取中",
  PARSING: "解析中",
  MATCHING: "比對中",
  NEEDS_REVIEW: "待人工覆核",
  READY: "可決策",
  QUARANTINED: "已隔離",
  FAILED: "處理失敗",
  CANCELLED: "已取消",
};

const ACTIVE_JOB_STATES = new Set(["QUEUED", "RUNNING", "RETRYING"]);
const REPLAYABLE_JOB_STATES = new Set(["FAILED", "DEAD_LETTER"]);

function labelForState(state: string): string {
  return INTAKE_LABELS[state] ?? state;
}

function toneForState(state: string) {
  if (state === "CANCELLED") return "neutral";
  return stageTone(state as Parameters<typeof stageTone>[0]);
}

function formatTime(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW", { timeZoneName: "short" });
}

function sortedHistory(
  history: Array<TransitionReceipt | PersistedLifecycleTransition>,
): PersistedLifecycleTransition[] {
  return [...history].sort(
    (left, right) =>
      new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime(),
  ) as PersistedLifecycleTransition[];
}

export type IntakeStageTimelineProps = {
  record: AssistedIntake;
  history?: Array<TransitionReceipt | PersistedLifecycleTransition>;
  jobs?: Array<JobReceipt | JobLifecycleReceipt>;
  jobHistory?: PersistedLifecycleTransition[];
  sla?: SlaReceipt | SlaLifecycleReceipt;
  busyAction?: string | null;
  canCancel?: boolean;
  canRetry?: boolean;
  canReopen?: boolean;
  reopenDeniedReason?: string | null;
  canReplay?: boolean;
  canCancelJob?: boolean;
  onCancel?: () => void;
  onRetry?: (checkpoint: string | null) => void;
  onReopen?: () => void;
  onReplayJob?: (jobId: string) => void;
  onCancelJob?: (jobId: string) => void;
  testId?: string;
};

/**
 * Renders only persisted server transitions. When history is absent, the UI
 * shows the current state and an explicit missing-history notice rather than
 * inferring a path from the final state.
 */
export function IntakeStageTimeline({
  record,
  history = [],
  jobs = [],
  jobHistory = [],
  sla,
  busyAction = null,
  canCancel,
  canRetry,
  canReopen,
  reopenDeniedReason,
  canReplay = false,
  canCancelJob = false,
  onCancel,
  onRetry,
  onReopen,
  onReplayJob,
  onCancelJob,
  testId = "intake-stage-timeline",
}: IntakeStageTimelineProps) {
  const stage = String(record.stage);
  const cancelAllowed = canCancel ?? Boolean(onCancel);
  const retryAllowed = canRetry ?? Boolean(onRetry);
  const reopenAllowed = canReopen ?? Boolean(onReopen);
  const transitions = sortedHistory(history);
  const persistedJobs = jobs as JobLifecycleReceipt[];
  const isCancelled = stage === "CANCELLED";
  const isControlledReopen = stage === "QUARANTINED";
  const activeJob =
    [...persistedJobs].reverse().find((job) => ACTIVE_JOB_STATES.has(job.status)) ??
    persistedJobs[persistedJobs.length - 1] ??
    null;
  const retryCheckpoint = activeJob?.checkpoint ?? null;

  return (
    <section
      aria-label="收件生命週期與背景工作"
      className={styles.sectionBox}
      data-testid={testId}
    >
      <div className={styles.sectionHead}>
        階段時序與執行歷程 STAGE TIMELINE
        <span
          className={styles.chip}
          data-testid="timeline-current-stage"
          data-tone={toneForState(stage)}
        >
          {stage} · {labelForState(stage)}
        </span>
      </div>

      <div aria-live="polite" className={styles.srSummary}>
        收件目前狀態 {stage}。伺服器已回傳 {transitions.length} 筆持久化狀態轉換。
      </div>

      {transitions.length ? (
        <ol className={styles.timeline} data-testid="timeline-stepper">
          {transitions.map((transition) => (
            <li
              className={styles.timelineItem}
              data-testid={`timeline-transition-${transition.transition_id}`}
              key={transition.transition_id}
            >
              <span className={styles.timelineMark} aria-hidden="true">
                {transition.to_state === "FAILED" || transition.to_state === "QUARANTINED"
                  ? "!"
                  : transition.to_state === "CANCELLED"
                    ? "×"
                    : "✓"}
              </span>
              <div className={styles.timelineContent}>
                <div className={styles.timelineTitle}>
                  {transition.from_state ? `${transition.from_state} → ` : ""}
                  {transition.to_state}
                </div>
                <div className={styles.timelineMeta}>
                  {formatTime(transition.occurred_at)} · {transition.actor}
                  {transition.actor_role ? ` (${transition.actor_role})` : ""} · v
                  {transition.version_after}
                </div>
                <div className={styles.timelineMeta}>
                  {transition.reason_code ? `Reason ${transition.reason_code}` : ""}
                  {transition.attempt != null ? ` · Attempt ${transition.attempt}` : ""}
                  {transition.checkpoint ? ` · Checkpoint ${transition.checkpoint}` : ""}
                  {transition.correlation_id
                    ? ` · Correlation ${transition.correlation_id}`
                    : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className={styles.noteBox} data-testid="timeline-history-unavailable">
          伺服器尚未回傳 persisted processing history；僅顯示目前狀態，不推算中間階段。
        </div>
      )}

      {sla ? (
        <div className={styles.metaGrid} data-testid="timeline-sla-panel">
          <div>
            <span className={styles.metaCaption}>SLA 狀態</span>
            <div className={styles.metaValue}>{sla.state}</div>
          </div>
          <div>
            <span className={styles.metaCaption}>到期時間</span>
            <div className={styles.metaValue}>{formatTime(sla.due_at)}</div>
          </div>
          <div>
            <span className={styles.metaCaption}>暫停累計</span>
            <div className={styles.metaValue}>{sla.paused_duration_seconds} 秒</div>
          </div>
          {"expected_resume_at" in sla ? (
            <div>
              <span className={styles.metaCaption}>預計恢復</span>
              <div className={styles.metaValue}>
                {formatTime((sla as SlaLifecycleReceipt).expected_resume_at)}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <div data-testid="timeline-job-list">
        {persistedJobs.map((job) => {
          const replayable = REPLAYABLE_JOB_STATES.has(job.status);
          const cancellable = ACTIVE_JOB_STATES.has(job.status);
          const transitionsForJob = jobHistory.filter(
            (entry) =>
              entry.stream === "JOB" &&
              (entry.reason === job.job_id || entry.correlation_id === job.correlation_id),
          );

          return (
            <article
              className={styles.sectionBox}
              data-testid={`timeline-job-${job.job_id}`}
              key={job.job_id}
            >
              <div className={styles.sectionHead}>
                Job <code>{job.job_id}</code>
                <span className={styles.chip} data-tone={replayable ? "risk" : "info"}>
                  {job.status}
                </span>
              </div>
              <div className={styles.metaGrid}>
                <div>
                  <span className={styles.metaCaption}>Attempt</span>
                  <div className={styles.metaValue}>
                    {job.attempt}
                    {job.max_attempts ? ` / ${job.max_attempts}` : ""}
                  </div>
                </div>
                <div>
                  <span className={styles.metaCaption}>Checkpoint</span>
                  <div className={styles.metaValue}>{job.checkpoint || "—"}</div>
                </div>
                <div>
                  <span className={styles.metaCaption}>Timeout</span>
                  <div className={styles.metaValue}>{formatTime(job.timeout_at)}</div>
                </div>
                <div>
                  <span className={styles.metaCaption}>Next retry</span>
                  <div className={styles.metaValue}>{formatTime(job.next_retry_at)}</div>
                </div>
                <div>
                  <span className={styles.metaCaption}>Queue</span>
                  <div className={styles.metaValue}>{job.queue_name ?? "伺服器未提供"}</div>
                </div>
                <div>
                  <span className={styles.metaCaption}>Correlation</span>
                  <div className={styles.metaValue}>
                    <code>{job.correlation_id}</code>
                  </div>
                </div>
              </div>
              {job.status === "DEAD_LETTER" ? (
                <div className={styles.warnNote} role="status">
                  DEAD_LETTER · {formatTime(job.dead_lettered_at)}。只有具 replay
                  權限的操作者可從 persisted checkpoint 重播。
                </div>
              ) : null}
              {job.status === "CANCELLED" ? (
                <div className={styles.noteBox} role="status">
                  Job 已取消於 {formatTime(job.cancelled_at)}；此 job 不可再執行。
                </div>
              ) : null}
              {transitionsForJob.length ? (
                <ul data-testid={`timeline-job-history-${job.job_id}`}>
                  {transitionsForJob.map((entry) => (
                    <li key={entry.transition_id}>
                      {formatTime(entry.occurred_at)} · {entry.from_state ?? "—"} →{" "}
                      {entry.to_state}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className={styles.actionRow}>
                {cancellable && canCancelJob && onCancelJob ? (
                  <button
                    className={styles.secondaryButton}
                    data-testid={`timeline-cancel-job-${job.job_id}`}
                    disabled={Boolean(busyAction)}
                    onClick={() => onCancelJob(job.job_id)}
                    type="button"
                  >
                    取消 Job
                  </button>
                ) : null}
                {replayable && canReplay && onReplayJob ? (
                  <button
                    className={styles.primaryButton}
                    data-testid={`timeline-replay-job-${job.job_id}`}
                    disabled={Boolean(busyAction)}
                    onClick={() => onReplayJob(job.job_id)}
                    type="button"
                  >
                    從 {job.checkpoint || "persisted checkpoint"} 重播
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      {isCancelled ? (
        <div className={styles.noteBox} data-testid="timeline-cancelled-terminal">
          CANCELLED 是 terminal state；此收件不可 retry 或 reopen。
        </div>
      ) : (
        <div className={styles.actionRow} data-testid="timeline-direct-actions">
          {cancelAllowed && onCancel && !isControlledReopen && stage !== "READY" ? (
            <button
              className={styles.secondaryButton}
              data-testid="timeline-cancel-button"
              disabled={Boolean(busyAction)}
              onClick={onCancel}
              type="button"
            >
              取消收件
            </button>
          ) : null}
          {stage === "FAILED" && retryAllowed && onRetry ? (
            <button
              className={styles.primaryButton}
              data-testid="timeline-retry-button"
              disabled={Boolean(busyAction)}
              onClick={() => onRetry(retryCheckpoint)}
              type="button"
            >
              從 {retryCheckpoint || "伺服器指定 checkpoint"} 重試
            </button>
          ) : null}
          {isControlledReopen && reopenAllowed && onReopen ? (
            <button
              className={styles.primaryButton}
              data-testid="timeline-reopen-button"
              disabled={Boolean(busyAction)}
              onClick={onReopen}
              type="button"
            >
              受控重新開啟 {stage}
            </button>
          ) : null}
          {isControlledReopen && !reopenAllowed && reopenDeniedReason ? (
            <div className={styles.warnNote} data-testid="timeline-reopen-denied" role="status">
              此角色目前不能解除隔離：<code>{reopenDeniedReason}</code>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
