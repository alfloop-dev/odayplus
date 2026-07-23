"use client";

// SiteScore job status (ODP-INTAKE-UX-PROMOTION-001) — UX-SCR-EXP-003F.
//
// Owned layer  : the SiteScore job slice of the promotion saga — every
//                JobReceipt state rendered distinctly, replay affordance for
//                authorized users with a *stable* idempotency key, and the
//                commit gate that refuses to display a job ID before the
//                server's transaction receipt proves it exists.
// Not changing : the promotion decision flow itself (PromotionReviewPanel),
//                the /v1/jobs/{id}/retry transport, or the intake detail
//                composition.
// Composes with: PromotionReviewPanel (embeds this per handoff §8.8) and the
//                intake detail's promotion section.

import { useRef, useState } from "react";
import type { JobReceipt, JobStatus, PromotionStatus } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type { IntakeTone } from "./intakeTypes";
import type {
  JobLifecycleReceipt,
  PersistedLifecycleTransition,
} from "./useIntakeLifecycle";

/** Wire payload for the authorized replay of a failed score job. */
export type ScoreReplayInput = {
  jobId: string;
  /** Replay resumes from the durable checkpoint, never from scratch. */
  checkpoint: "SCORE_QUEUED";
  reason: string;
  riskAcknowledged: boolean;
  /**
   * Stable per (job, attempt): repeated clicks and lost-response retries reuse
   * the SAME key, so the server dedups instead of double-queueing (§8.8).
   */
  idempotencyKey: string;
  /** `W/"<job version>"` concurrency token for the retry endpoint. */
  ifMatch: string;
};

/** zh-TW label per canonical job state — never collapsed into one spinner. */
export const SITE_SCORE_JOB_LABEL: Record<JobStatus, string> = {
  QUEUED: "已排入佇列",
  RUNNING: "評分執行中",
  RETRYING: "自動重試中",
  SUCCEEDED: "評分完成",
  FAILED: "評分失敗",
  CANCELLED: "已取消",
  DEAD_LETTER: "進入死信佇列",
};

export function siteScoreJobTone(status: JobStatus): IntakeTone {
  if (status === "SUCCEEDED") return "good";
  if (status === "FAILED" || status === "DEAD_LETTER") return "risk";
  if (status === "RETRYING" || status === "CANCELLED") return "watch";
  return "info";
}

/** Text marker so job state never depends on colour alone (VDC-003). */
export function siteScoreJobMark(status: JobStatus): string {
  if (status === "SUCCEEDED") return "✓";
  if (status === "FAILED" || status === "DEAD_LETTER") return "✕";
  if (status === "CANCELLED") return "⊘";
  return "…";
}

/** Job states a steward/manager may replay from the durable checkpoint. */
const REPLAYABLE: readonly JobStatus[] = ["FAILED", "DEAD_LETTER"];

export type SiteScoreJobStatusProps = {
  /**
   * The authoritative job receipt, or null before the server has committed
   * one. The job ID is rendered ONLY from this receipt — the UI never invents
   * or predicts an ID (§8.8: display after transaction commit only).
   */
  job?: JobReceipt | JobLifecycleReceipt | null;
  /** Persisted server transitions for this job. */
  history?: PersistedLifecycleTransition[];
  /** Saga status, so SCORE_FAILED keeps the candidate visibly alive. */
  promotionStatus?: PromotionStatus | null;
  /** Committed candidate ID; retained (and shown) through SCORE_FAILED. */
  candidateSiteId?: string | null;
  /** Authorization gate for replay — absent permission removes the control. */
  canReplay?: boolean;
  canCancel?: boolean;
  busy?: boolean;
  onReplay?: (input: ScoreReplayInput) => Promise<JobReceipt | void> | void;
  onCancel?: (input: { jobId: string; ifMatch: string }) => Promise<JobReceipt | void> | void;
  testId?: string;
};

export function SiteScoreJobStatus({
  job = null,
  history = [],
  promotionStatus = null,
  candidateSiteId = null,
  canReplay = false,
  canCancel = false,
  busy = false,
  onReplay,
  onCancel,
  testId = "sitescore-job-status",
}: SiteScoreJobStatusProps) {
  const [replayReason, setReplayReason] = useState("");
  const [replayAck, setReplayAck] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // One key per (job, attempt): every click for the same failed attempt sends
  // the same key, so a lost 202 can be replayed without double-queueing. The
  // server bumping `attempt` is the only thing that rotates the key.
  const replayKeys = useRef(new Map<string, string>());
  const replayScope = job ? `${job.job_id}:a${job.attempt}` : null;
  let replayKey: string | null = null;
  if (replayScope) {
    const existing = replayKeys.current.get(replayScope);
    if (existing) {
      replayKey = existing;
    } else {
      replayKey = `sitescore-replay-${replayScope}-${Math.random().toString(36).slice(2, 10)}`;
      replayKeys.current.set(replayScope, replayKey);
    }
  }

  const scoreFailed = promotionStatus === "SCORE_FAILED";
  const replayable = Boolean(job && (REPLAYABLE.includes(job.status) || scoreFailed));
  const showReplayControls = replayable && canReplay && Boolean(onReplay);
  const lifecycleJob = job as JobLifecycleReceipt | null;
  const cancellable = Boolean(
    job &&
      (job.status === "QUEUED" || job.status === "RUNNING" || job.status === "RETRYING") &&
      canCancel &&
      onCancel,
  );

  async function handleReplay() {
    if (!job || !onReplay || !replayKey || busy) return;
    if (!replayReason.trim()) {
      setLocalError("請填寫重放原因（寫入 job replay 收據與 Audit）。");
      return;
    }
    if (!replayAck) {
      setLocalError("請先確認風險聲明：重放使用同一 Idempotency-Key，不會建立第二筆 Candidate。");
      return;
    }
    setLocalError(null);
    try {
      await onReplay({
        jobId: job.job_id,
        checkpoint: "SCORE_QUEUED",
        reason: replayReason.trim(),
        riskAcknowledged: replayAck,
        idempotencyKey: replayKey,
        ifMatch: `W/"${job.version}"`,
      });
    } catch (err: unknown) {
      setLocalError((err as Error)?.message || "重放請求失敗，輸入已保留，可以同一鍵重試。");
    }
  }

  return (
    <section
      aria-label="SiteScore 評分工作狀態"
      className={styles.sectionBox}
      data-testid={testId}
    >
      <div className={styles.sectionHead}>
        SiteScore 評分工作 SITE SCORE JOB
        {job ? (
          <span
            className={styles.chip}
            data-testid="sitescore-job-state"
            data-tone={siteScoreJobTone(job.status)}
          >
            {siteScoreJobMark(job.status)} {job.status} · {SITE_SCORE_JOB_LABEL[job.status]}
          </span>
        ) : null}
      </div>

      {/* Live status summary for screen readers — announces state changes. */}
      <div aria-live="polite" className={styles.srSummary} data-testid="sitescore-sr-summary">
        {job
          ? `評分工作 ${job.job_id} 狀態 ${job.status}（第 ${job.attempt} 次嘗試）。`
          : "評分工作尚未由伺服器交易確認，尚無工作編號。"}
      </div>

      {!job ? (
        // Pre-commit: no fabricated ID, no fake progress — an explicit
        // placeholder that says why there is nothing to show yet.
        <div className={styles.noteBox} data-testid="sitescore-job-placeholder">
          尚未產生評分工作編號 — SiteScore job ID 只會在 candidate 交易 commit
          並排入評分佇列後，由伺服器收據提供。
        </div>
      ) : (
        <div className={styles.metaGrid}>
          <div>
            <span className={styles.metaCaption}>工作編號 Job ID</span>
            <div className={styles.metaValue} data-testid="sitescore-job-id">
              <code>{job.job_id}</code>
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>檢查點 Checkpoint</span>
            <div className={styles.metaValue} data-testid="sitescore-job-checkpoint">
              <code>{job.checkpoint}</code>
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>嘗試次數 Attempt</span>
            <div className={styles.metaValue} data-testid="sitescore-job-attempt">
              第 {job.attempt} 次
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>版本 Version</span>
            <div className={styles.metaValue} data-testid="sitescore-job-version">
              W/&quot;{job.version}&quot;
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>Correlation ID</span>
            <div className={styles.metaValue} data-testid="sitescore-job-correlation">
              <code>{job.correlation_id}</code>
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>Queue</span>
            <div className={styles.metaValue} data-testid="sitescore-job-queue">
              {lifecycleJob?.queue_name ?? "伺服器未提供"}
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>Timeout</span>
            <div className={styles.metaValue} data-testid="sitescore-job-timeout">
              {lifecycleJob?.timeout_at
                ? new Date(lifecycleJob.timeout_at).toLocaleString("zh-TW", {
                    timeZoneName: "short",
                  })
                : "伺服器未提供"}
            </div>
          </div>
          <div>
            <span className={styles.metaCaption}>Next retry</span>
            <div className={styles.metaValue} data-testid="sitescore-job-next-retry">
              {lifecycleJob?.next_retry_at
                ? new Date(lifecycleJob.next_retry_at).toLocaleString("zh-TW", {
                    timeZoneName: "short",
                  })
                : "無"}
            </div>
          </div>
        </div>
      )}

      {history.length ? (
        <ol className={styles.timeline} data-testid="sitescore-job-history">
          {[...history]
            .sort(
              (left, right) =>
                new Date(left.occurred_at).getTime() -
                new Date(right.occurred_at).getTime(),
            )
            .map((entry) => (
              <li className={styles.timelineItem} key={entry.transition_id}>
                <span className={styles.timelineMark} aria-hidden="true">
                  {entry.to_state === "FAILED" || entry.to_state === "DEAD_LETTER"
                    ? "!"
                    : "✓"}
                </span>
                <div className={styles.timelineContent}>
                  <div className={styles.timelineTitle}>
                    {entry.from_state ?? "—"} → {entry.to_state}
                  </div>
                  <div className={styles.timelineMeta}>
                    {new Date(entry.occurred_at).toLocaleString("zh-TW", {
                      timeZoneName: "short",
                    })}{" "}
                    · Attempt {entry.attempt ?? job?.attempt ?? "—"} ·{" "}
                    {entry.checkpoint ?? job?.checkpoint ?? "—"}
                  </div>
                </div>
              </li>
            ))}
        </ol>
      ) : job ? (
        <div className={styles.noteBox} data-testid="sitescore-job-history-unavailable">
          伺服器尚未回傳 job history；不從目前 job 狀態推算先前步驟。
        </div>
      ) : null}

      {/* SCORE_FAILED keeps the candidate — say so, and keep showing its ID. */}
      {scoreFailed ? (
        <div className={styles.warnNote} data-testid="candidate-retained-note" role="status">
          評分失敗（SCORE_FAILED），但 Candidate Site
          {candidateSiteId ? (
            <>
              {" "}
              <code data-testid="candidate-retained-id">{candidateSiteId}</code>{" "}
            </>
          ) : (
            " "
          )}
          仍然存在，不會被刪除。可由授權人員自 SCORE_QUEUED 檢查點以同一
          Idempotency-Key 重放評分。
        </div>
      ) : null}

      {replayable && !canReplay ? (
        <div className={styles.noteBox} data-testid="sitescore-replay-denied">
          重放評分需要展店主管或資料管理員授權（403 ROLE_DENIED 會被拒絕）。
        </div>
      ) : null}

      {cancellable && job && onCancel ? (
        <div className={styles.actionRow}>
          <button
            className={styles.secondaryButton}
            data-testid="sitescore-cancel-btn"
            disabled={busy}
            onClick={() => onCancel({ jobId: job.job_id, ifMatch: `W/"${job.version}"` })}
            type="button"
          >
            {busy ? "取消請求送出中…" : "取消 SiteScore job"}
          </button>
        </div>
      ) : null}

      {showReplayControls && job ? (
        <div data-testid="sitescore-replay-controls" style={{ marginTop: "10px" }}>
          <label className={styles.fieldLabel} htmlFor="sitescore-replay-reason">
            重放原因（必填，寫入 replay 收據）
          </label>
          <textarea
            className={styles.textarea}
            data-testid="sitescore-replay-reason"
            id="sitescore-replay-reason"
            onChange={(e) => setReplayReason(e.target.value)}
            placeholder="例如：外部評分服務逾時已排除，授權自 SCORE_QUEUED 檢查點重放。"
            rows={2}
            value={replayReason}
          />
          <label className={styles.checkboxRow} htmlFor="sitescore-replay-ack">
            <input
              checked={replayAck}
              data-testid="sitescore-replay-ack"
              disabled={busy}
              id="sitescore-replay-ack"
              onChange={(e) => setReplayAck(e.target.checked)}
              type="checkbox"
            />
            <span>
              我確認以同一 Idempotency-Key 重放：只會恢復原評分請求，不會建立第二筆
              Candidate 或重複扣打重試預算。
            </span>
          </label>
          <div className={styles.metaSub} data-testid="sitescore-replay-key">
            Idempotency-Key（本次嘗試固定）：<code>{replayKey}</code>
          </div>
          {localError ? (
            <div className={styles.errorPanel} data-testid="sitescore-replay-error" role="alert">
              <span className={styles.errorSummary}>{localError}</span>
            </div>
          ) : null}
          <div style={{ marginTop: "8px", textAlign: "right" }}>
            <button
              className={styles.primaryButton}
              data-testid="sitescore-replay-btn"
              disabled={busy || !replayReason.trim() || !replayAck}
              onClick={handleReplay}
              type="button"
            >
              {busy ? "重放請求中…" : "自 SCORE_QUEUED 檢查點重放（同 Idempotency-Key）"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
