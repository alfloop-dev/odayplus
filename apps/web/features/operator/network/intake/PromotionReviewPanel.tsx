"use client";

// Candidate Site promotion review (ODP-INTAKE-UX-PROMOTION-001) — the
// UX-SCR-EXP-003F promotion section of the intake detail (handoff §8.7/§8.8,
// state contracts §7, Review 003 engineering gate).
//
// Owned layer  : the promotion saga UI — explicit request, independent
//                second-actor review, every saga state rendered distinctly
//                (never one compressed loading state), commit-gated Candidate
//                / SiteScore IDs, lost-response recovery on a stable
//                idempotency key, and the durable promotion receipt.
// Not changing : the /v1 promotion endpoints, PromotionService, the intake
//                detail shell, or the assignment/SLA surfaces.
// Composes with: IntakeProcessingDetail (renders this as the promotion
//                section) and SiteScoreJobStatus (embedded below for the
//                score-job slice of the saga).

import { useRef, useState } from "react";
import type {
  AssistedIntake,
  JobReceipt,
  PromotionDecisionReceipt,
  PromotionStatus,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type { IntakeApiError } from "./intakeClient";
import type { IntakeTone } from "./intakeTypes";
import { SiteScoreJobStatus, type ScoreReplayInput } from "./SiteScoreJobStatus";
import type {
  JobLifecycleReceipt,
  PersistedLifecycleTransition,
} from "./useIntakeLifecycle";

/** zh-TW label per canonical promotion saga state (state contracts §7). */
export const PROMOTION_STATUS_LABEL: Record<PromotionStatus, string> = {
  REQUESTED: "已提出申請",
  VALIDATING: "驗證前置條件中",
  PENDING_REVIEW: "等待第二人審查",
  REJECTED: "已駁回",
  APPROVED: "已核准",
  CANDIDATE_CREATING: "Candidate 建立交易中",
  CANDIDATE_CREATED: "Candidate 已建立",
  SCORE_QUEUED: "評分已排入佇列",
  COMPLETED: "晉升完成",
  FAILED: "Candidate 建立失敗",
  SCORE_FAILED: "評分失敗（Candidate 保留）",
};

export function promotionTone(status: PromotionStatus): IntakeTone {
  if (status === "COMPLETED" || status === "CANDIDATE_CREATED" || status === "APPROVED") return "good";
  if (status === "REJECTED" || status === "FAILED" || status === "SCORE_FAILED") return "risk";
  if (status === "PENDING_REVIEW") return "watch";
  return "info";
}

/** Text marker so saga state never depends on colour alone (VDC-003). */
export function promotionMark(status: PromotionStatus): string {
  if (status === "COMPLETED") return "✓";
  if (status === "REJECTED" || status === "FAILED" || status === "SCORE_FAILED") return "✕";
  return "→";
}

/**
 * The actual saga path taken, so the stepper shows the real branch (REJECTED /
 * FAILED / SCORE_FAILED) instead of a fabricated straight line — mirroring the
 * intake stagePath idiom and §8.8's "no single compressed loading state".
 */
export function promotionStagePath(status: PromotionStatus): PromotionStatus[] {
  const head: PromotionStatus[] = ["REQUESTED", "VALIDATING", "PENDING_REVIEW"];
  if (status === "REQUESTED" || status === "VALIDATING" || status === "PENDING_REVIEW") return head;
  if (status === "REJECTED") return [...head, "REJECTED"];
  const executing: PromotionStatus[] = [...head, "APPROVED", "CANDIDATE_CREATING"];
  if (status === "APPROVED" || status === "CANDIDATE_CREATING") return executing;
  if (status === "FAILED") return [...executing, "FAILED"];
  const scored: PromotionStatus[] = [...executing, "CANDIDATE_CREATED", "SCORE_QUEUED"];
  if (status === "CANDIDATE_CREATED" || status === "SCORE_QUEUED") return scored;
  if (status === "SCORE_FAILED") return [...scored, "SCORE_FAILED"];
  return [...scored, "COMPLETED"];
}

const BAD_STATES: readonly PromotionStatus[] = ["REJECTED", "FAILED", "SCORE_FAILED"];

/**
 * Commit gates (§8.8): the Candidate ID is displayed only once the creation
 * transaction committed; the score-job ID only once the job was durably
 * queued. Even if a receipt carried an ID early, the UI refuses to show it.
 */
const CANDIDATE_COMMITTED: readonly PromotionStatus[] = [
  "CANDIDATE_CREATED",
  "SCORE_QUEUED",
  "COMPLETED",
  "SCORE_FAILED",
];
const SCORE_JOB_COMMITTED: readonly PromotionStatus[] = ["SCORE_QUEUED", "COMPLETED", "SCORE_FAILED"];

export function committedCandidateId(promotion: PromotionDecisionReceipt | null | undefined): string | null {
  if (!promotion?.candidate_site_id) return null;
  return CANDIDATE_COMMITTED.includes(promotion.status) ? promotion.candidate_site_id : null;
}

export function committedScoreJobId(promotion: PromotionDecisionReceipt | null | undefined): string | null {
  if (!promotion?.site_score_job_id) return null;
  return SCORE_JOB_COMMITTED.includes(promotion.status) ? promotion.site_score_job_id : null;
}

/** Wire payload for the explicit promotion request (POST …/promotion-requests). */
export type PromotionRequestInput = {
  targetFormatCode: string;
  reason: string;
  gateSnapshotSha256: string;
  riskAcknowledged: boolean;
  requestedReviewerId?: string | null;
  /** Stable per draft — a retried submit reuses the SAME key (§8.8). */
  idempotencyKey: string;
  /** `W/"<intake version>"` — the request mutates the intake aggregate. */
  ifMatch: string;
};

/** Wire payload for the second-actor review (POST …/actions/review). */
export type PromotionReviewInput = {
  decision: "APPROVE" | "REJECT";
  reason: string;
  riskAcknowledged: boolean;
  requestedChanges?: string[];
  idempotencyKey: string;
  /** `W/"<promotion version>"`. */
  ifMatch: string;
};

export type PromotionActor = { id: string; name: string; role: string };

export type PromotionReviewPanelProps = {
  record: AssistedIntake;
  /** Authoritative decision receipt from the server; null before any request. */
  promotion?: PromotionDecisionReceipt | null;
  /** Authoritative score-job receipt, once one exists. */
  scoreJob?: JobReceipt | JobLifecycleReceipt | null;
  /** Persisted histories returned by the lifecycle read boundary. */
  promotionHistory?: PersistedLifecycleTransition[];
  decisionHistory?: PersistedLifecycleTransition[];
  scoreJobHistory?: PersistedLifecycleTransition[];
  currentOperator: PromotionActor;
  /** Proposer subject — defaults to the promotion receipt, then the submitter. */
  proposerId?: string;
  /** Gate evaluation snapshot hash the request must bind to. */
  gateSnapshotSha256: string;
  targetFormatOptions?: readonly string[];
  /** Role gates. Absent permission removes/disables the control (§8.7). */
  canRequest?: boolean;
  canReview?: boolean;
  canExecute?: boolean;
  canReplayScore?: boolean;
  canCancelScore?: boolean;
  requestDeniedReason?: string | null;
  reviewDeniedReason?: string | null;
  executeDeniedReason?: string | null;
  replayDeniedReason?: string | null;
  busy?: boolean;
  error?: IntakeApiError | null;
  /** True when the server answered from a prior durable receipt. */
  idempotencyReplayed?: boolean;
  onRequestPromotion?: (input: PromotionRequestInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReviewPromotion?: (input: PromotionReviewInput) => Promise<PromotionDecisionReceipt | void> | void;
  onReplayScore?: (input: ScoreReplayInput) => Promise<JobReceipt | void> | void;
  onCancelScore?: (input: {
    jobId: string;
    ifMatch: string;
  }) => Promise<JobReceipt | void> | void;
  /** Lost-response recovery via decision lookup (GET promotion-decisions/:id). */
  onLookupDecision?: () => void;
  onRefresh?: () => void;
  refreshing?: boolean;
  lastRefreshedAt?: string | null;
  nextRefreshAt?: string | null;
  testId?: string;
};

export function PromotionReviewPanel({
  record,
  promotion = null,
  scoreJob = null,
  promotionHistory = [],
  decisionHistory = [],
  scoreJobHistory = [],
  currentOperator,
  proposerId: proposerIdProp,
  gateSnapshotSha256,
  targetFormatOptions = ["FMT-STANDARD-STORE", "FMT-MICRO-STORE", "FMT-SMART-VENDING"],
  canRequest = true,
  canReview = true,
  canExecute = false,
  canReplayScore = false,
  canCancelScore = false,
  requestDeniedReason = null,
  reviewDeniedReason = null,
  executeDeniedReason = null,
  replayDeniedReason = null,
  busy = false,
  error = null,
  idempotencyReplayed = false,
  onRequestPromotion,
  onReviewPromotion,
  onReplayScore,
  onCancelScore,
  onLookupDecision,
  onRefresh,
  refreshing = false,
  lastRefreshedAt = null,
  nextRefreshAt = null,
  testId = "promotion-review-panel",
}: PromotionReviewPanelProps) {
  // ---- request draft state (preserved across conflicts and lost responses) --
  const [targetFormatCode, setTargetFormatCode] = useState<string>(targetFormatOptions[0] ?? "");
  const [requestReason, setRequestReason] = useState("");
  const [requestAck, setRequestAck] = useState(false);
  // ---- review draft state ---------------------------------------------------
  const [reviewReason, setReviewReason] = useState("");
  const [reviewAck, setReviewAck] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);

  // Stable idempotency keys. The request key survives retries for the same
  // draft; a fresh request after a REJECTED decision gets a new scope (the
  // rejected decision's ID), so it cannot replay the rejected receipt.
  const keyStore = useRef(new Map<string, string>());
  function stableKey(scope: string, prefix: string): string {
    const existing = keyStore.current.get(scope);
    if (existing) return existing;
    const created = `${prefix}-${Math.random().toString(36).slice(2, 12)}`;
    keyStore.current.set(scope, created);
    return created;
  }
  const requestKey = stableKey(
    `request:${record.id}:${promotion?.promotion_decision_id ?? "new"}`,
    `promotion-request-${record.id}`,
  );
  const reviewKey = promotion
    ? stableKey(
        `review:${promotion.promotion_decision_id}:v${promotion.version}`,
        `promotion-review-${promotion.promotion_decision_id}`,
      )
    : null;

  const status: PromotionStatus | null = promotion?.status ?? null;
  const proposerId = proposerIdProp ?? promotion?.proposer_subject_id ?? record.submitter ?? "";
  const isSelfReview = Boolean(promotion) && proposerId === currentOperator.id;

  const requestOpen =
    record.stage === "READY" && (!promotion || status === "REJECTED");
  const reviewOpen = status === "PENDING_REVIEW";

  const candidateId = committedCandidateId(promotion);
  const scoreJobId = committedScoreJobId(promotion);

  const conflict = error?.status === 409;
  const preconditionRequired = error?.status === 428;
  const transportLost = attempted && Boolean(error) && error?.status === 0;

  // ---- handlers (non-optimistic: state changes only via server receipts) ----
  async function handleRequest() {
    if (busy || !onRequestPromotion) return;
    if (!targetFormatCode) {
      setLocalError("請選擇晉升目標型態（target_format_code）。");
      return;
    }
    if (requestReason.trim().length < 3) {
      setLocalError("請填寫晉升申請原因（至少 3 個字，寫入決策與 Audit）。");
      return;
    }
    if (!requestAck) {
      setLocalError("請勾選風險確認後再提出晉升申請。");
      return;
    }
    setLocalError(null);
    setAttempted(true);
    try {
      await onRequestPromotion({
        targetFormatCode,
        reason: requestReason.trim(),
        gateSnapshotSha256,
        riskAcknowledged: requestAck,
        idempotencyKey: requestKey,
        ifMatch: `W/"${record.version}"`,
      });
    } catch (err: unknown) {
      setLocalError(
        (err as Error)?.message ||
          "晉升申請未確認寫入。你的輸入已保留，可以同一 Idempotency-Key 重試或查詢決策狀態。",
      );
    }
  }

  async function handleReview(decision: "APPROVE" | "REJECT") {
    if (busy || !onReviewPromotion || !promotion || !reviewKey) return;
    if (isSelfReview) {
      setLocalError("提案者不得審查自己的晉升申請（SELF_REVIEW_DENIED）。");
      return;
    }
    if (decision === "APPROVE" && !canExecute) {
      setLocalError(
        `審查者可駁回，但沒有執行 Candidate promotion 的權限（${executeDeniedReason ?? "ROLE_DENIED"}）。`,
      );
      return;
    }
    if (reviewReason.trim().length < 3) {
      setLocalError("請填寫審查理由（至少 3 個字）。");
      return;
    }
    if (!reviewAck) {
      setLocalError("請勾選風險確認：核准將建立 Candidate 並排入 SiteScore 評分。");
      return;
    }
    setLocalError(null);
    setAttempted(true);
    try {
      await onReviewPromotion({
        decision,
        reason: reviewReason.trim(),
        riskAcknowledged: reviewAck,
        idempotencyKey: reviewKey,
        ifMatch: `W/"${promotion.version}"`,
      });
    } catch (err: unknown) {
      setLocalError(
        (err as Error)?.message ||
          "審查未確認寫入。你的輸入已保留，可以同一 Idempotency-Key 重試。",
      );
    }
  }

  const shownError = localError || error?.summary || null;
  const persistedPromotionHistory = [...promotionHistory].sort(
    (left, right) =>
      new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime(),
  );
  const persistedStates = persistedPromotionHistory
    .map((entry) => entry.to_state as PromotionStatus)
    .filter((entry) => entry in PROMOTION_STATUS_LABEL);
  const steps =
    status && persistedStates[persistedStates.length - 1] !== status
      ? [...persistedStates, status]
      : persistedStates;
  const currentIndex = steps.length - 1;

  return (
    <section
      aria-label={`Candidate 晉升審查 ${record.id}`}
      className={styles.sectionBox}
      data-testid={testId}
    >
      {/* ------------------------------------------------ header + status --- */}
      <div className={styles.sectionHead}>
        Candidate Site 晉升審查 PROMOTION &amp; SITESCORE
        {status ? (
          <span
            className={styles.chip}
            data-testid="promotion-status-badge"
            data-tone={promotionTone(status)}
          >
            {promotionMark(status)} {status} · {PROMOTION_STATUS_LABEL[status]}
          </span>
        ) : (
          <span className={styles.chip} data-testid="promotion-status-badge" data-tone="neutral">
            尚未提出晉升申請
          </span>
        )}
        {onRefresh ? (
          <button
            className={styles.secondaryButton}
            data-testid="promotion-refresh-btn"
            disabled={busy || refreshing}
            onClick={onRefresh}
            style={{ marginLeft: "auto", padding: "3px 8px", fontSize: "11px" }}
            type="button"
          >
            {refreshing ? "更新中…" : "重新整理"}
          </button>
        ) : null}
      </div>

      <div className={styles.metaSub} data-testid="promotion-refresh-status">
        最近伺服器更新：
        {lastRefreshedAt
          ? new Date(lastRefreshedAt).toLocaleString("zh-TW", { timeZoneName: "short" })
          : "尚未讀取"}
        {nextRefreshAt
          ? ` · 下次輪詢 ${new Date(nextRefreshAt).toLocaleString("zh-TW", {
              timeZoneName: "short",
            })}`
          : ""}
      </div>

      {/* Screen-reader live summary (VDC-003). */}
      <div aria-live="polite" className={styles.srSummary} data-testid="promotion-sr-summary">
        {status
          ? `晉升決策狀態 ${status}（${PROMOTION_STATUS_LABEL[status]}）。`
          : "此收件尚未提出 Candidate 晉升申請。"}
        {isSelfReview && reviewOpen ? "警告：提案者與審查者相同，審查已封鎖。" : ""}
      </div>

      {/* Promotion review is complex-desk work (Review 003 / VDC-002). */}
      <div className={styles.desktopOnlyNote} data-testid="promotion-desktop-note">
        DESKTOP_REQUIRED — 晉升審查屬複雜審查作業，請在桌面寬度完成；窄螢幕僅供唯讀檢視。
      </div>

      {/* --------------------------------------------------- saga stepper --- */}
      {status ? (
        <div aria-label="晉升流程狀態" className={styles.stepper} data-testid="promotion-saga-stepper" role="list">
          {steps.map((code, index) => {
            const isCurrent = index === currentIndex;
            const failed = isCurrent && BAD_STATES.includes(code);
            const done = index < currentIndex || (isCurrent && code === "COMPLETED");
            const state = failed ? "failed" : done ? "done" : isCurrent ? "current" : "upcoming";
            return (
              <span
                className={styles.step}
                data-state={state}
                data-testid={isCurrent ? `promo-step-${code}` : `promo-history-step-${index}`}
                key={`${code}-${index}`}
                role="listitem"
              >
                <span className={styles.stepMark}>{failed ? "✕" : done && !isCurrent ? "✓" : String(index + 1)}</span>
                <span className={styles.stepText}>
                  <span className={styles.stepName}>{PROMOTION_STATUS_LABEL[code]}</span>
                  <span className={styles.stepCode}>{code}</span>
                </span>
                {index < steps.length - 1 ? <span aria-hidden="true" className={styles.stepArrow}>→</span> : null}
              </span>
            );
          })}
        </div>
      ) : null}
      {status && persistedPromotionHistory.length === 0 ? (
        <div className={styles.noteBox} data-testid="promotion-history-unavailable">
          伺服器尚未回傳 promotion history；只顯示目前 authoritative receipt
          狀態，不推算中間步驟。
        </div>
      ) : null}
      {persistedPromotionHistory.length ? (
        <ol className={styles.timeline} data-testid="promotion-history">
          {persistedPromotionHistory.map((entry) => (
            <li className={styles.timelineItem} key={entry.transition_id}>
              <span className={styles.timelineMark} aria-hidden="true">
                {entry.to_state === "REJECTED" ||
                entry.to_state === "FAILED" ||
                entry.to_state === "SCORE_FAILED"
                  ? "!"
                  : "✓"}
              </span>
              <div className={styles.timelineContent}>
                <div className={styles.timelineTitle}>
                  Promotion · {entry.from_state ?? "—"} → {entry.to_state}
                </div>
                <div className={styles.timelineMeta}>
                  {new Date(entry.occurred_at).toLocaleString("zh-TW", {
                    timeZoneName: "short",
                  })}{" "}
                  · {entry.actor} · v{entry.version_after}
                  {entry.reason_code ? ` · ${entry.reason_code}` : ""}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : null}

      {decisionHistory.length ? (
        <ol className={styles.timeline} data-testid="promotion-decision-history">
          {[...decisionHistory]
            .sort(
              (left, right) =>
                new Date(left.occurred_at).getTime() -
                new Date(right.occurred_at).getTime(),
            )
            .map((entry) => (
              <li className={styles.timelineItem} key={entry.transition_id}>
                <span className={styles.timelineMark} aria-hidden="true">
                  {entry.to_state === "REJECTED" || entry.to_state === "FAILED"
                    ? "!"
                    : "✓"}
                </span>
                <div className={styles.timelineContent}>
                  <div className={styles.timelineTitle}>
                    Decision · {entry.from_state ?? "—"} → {entry.to_state}
                  </div>
                  <div className={styles.timelineMeta}>
                    {new Date(entry.occurred_at).toLocaleString("zh-TW", {
                      timeZoneName: "short",
                    })}{" "}
                    · {entry.actor} · v{entry.version_after}
                  </div>
                </div>
              </li>
            ))}
        </ol>
      ) : null}

      {/* ------------------------------------------- idempotent replay tag --- */}
      {idempotencyReplayed ? (
        <div className={styles.noteBox} data-testid="idempotency-replayed-indicator" role="status">
          Idempotency-Replayed — 伺服器以原持久化收據回應此請求：未建立第二筆
          Candidate，也未重複排入評分。
        </div>
      ) : null}

      {/* ------------------------------------------------- error surfaces --- */}
      {conflict ? (
        <div className={styles.errorPanel} data-testid="promotion-conflict-banner" role="alert">
          <span className={styles.errorSummary}>⚠ 版本衝突（409 VERSION_CONFLICT）</span>
          <span className={styles.errorMeta}>
            目前伺服器狀態：{status ?? record.stage} · 最新版本 W/&quot;
            {promotion?.version ?? record.version}&quot;。你的輸入已完整保留；請重新整理取得最新
            If-Match 後重新提交（同一 Idempotency-Key）。
          </span>
          {onRefresh ? (
            <div style={{ marginTop: "6px" }}>
              <button
                className={styles.primaryButton}
                data-testid="promotion-conflict-refresh-btn"
                onClick={onRefresh}
                type="button"
              >
                重新整理並保留輸入
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {preconditionRequired ? (
        <div className={styles.errorPanel} data-testid="promotion-precondition-banner" role="alert">
          <span className={styles.errorSummary}>⚠ 缺少併發版本（428 PRECONDITION_REQUIRED）</span>
          <span className={styles.errorMeta}>
            請重新整理取得最新版本後重試；此操作必須攜帶 If-Match。
          </span>
        </div>
      ) : null}

      {transportLost ? (
        <div className={styles.errorPanel} data-testid="promotion-lost-response" role="alert">
          <span className={styles.errorSummary}>⚠ 回應遺失 — 操作結果未確認</span>
          <span className={styles.errorMeta}>
            請求可能已寫入但回應未送達。以同一 Idempotency-Key 重試只會取回原收據，
            不會建立第二筆 Candidate；也可直接查詢決策狀態。
          </span>
          <div className={styles.actionRow} style={{ marginTop: "6px" }}>
            {requestOpen ? (
              <button
                className={styles.primaryButton}
                data-testid="promotion-lost-retry-btn"
                disabled={busy}
                onClick={handleRequest}
                type="button"
              >
                以同一 Idempotency-Key 重試
              </button>
            ) : null}
            {onLookupDecision ? (
              <button
                className={styles.secondaryButton}
                data-testid="promotion-lookup-btn"
                disabled={busy}
                onClick={onLookupDecision}
                type="button"
              >
                查詢決策狀態（不重送）
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {shownError && !conflict && !preconditionRequired && !transportLost ? (
        <div className={styles.errorPanel} data-testid="promotion-error" role="alert">
          <span className={styles.errorSummary}>{shownError}</span>
          {error?.nextAction ? <span className={styles.errorNext}>{error.nextAction}</span> : null}
        </div>
      ) : null}
      {shownError && (conflict || preconditionRequired || transportLost) && localError ? (
        <div className={styles.errorPanel} data-testid="promotion-local-error" role="alert">
          <span className={styles.errorSummary}>{localError}</span>
        </div>
      ) : null}

      {/* ------------------------------------------------- request form ----- */}
      {requestOpen ? (
        canRequest ? (
          <div className={styles.sectionBox} data-testid="promotion-request-form" style={{ marginTop: "10px" }}>
            <div className={styles.sectionHead}>
              提出晉升申請 EXPLICIT PROMOTION REQUEST
              {status === "REJECTED" ? (
                <span className={styles.chip} data-testid="promotion-rerequest-chip" data-tone="watch">
                  前次申請已駁回 — 這是新的一次申請（新 Idempotency-Key）
                </span>
              ) : null}
            </div>

            <div className={styles.noteBox} data-testid="promotion-request-preconditions">
              前置條件：收件 READY、Listing ACTIVE、無重複 Candidate。核准需由另一位
              展店主管完成（second-actor），系統不會自動晉升。
            </div>

            <label className={styles.fieldLabel} htmlFor="promotion-target-format">
              目標型態 target_format_code（必填）
            </label>
            <select
              className={styles.select}
              data-testid="promotion-target-format"
              disabled={busy}
              id="promotion-target-format"
              onChange={(e) => setTargetFormatCode(e.target.value)}
              value={targetFormatCode}
            >
              {targetFormatOptions.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>

            <label className={styles.fieldLabel} htmlFor="promotion-request-reason" style={{ marginTop: "8px" }}>
              申請原因（必填，寫入決策與 Audit）
            </label>
            <textarea
              className={styles.textarea}
              data-testid="promotion-request-reason"
              disabled={busy}
              id="promotion-request-reason"
              onChange={(e) => setRequestReason(e.target.value)}
              placeholder="例如：熱區缺口與租金符合展店門檻，申請晉升為 Candidate Site 並排入評分。"
              rows={3}
              value={requestReason}
            />

            <div className={styles.metaSub} data-testid="promotion-gate-snapshot">
              Gate snapshot SHA-256：<code>{gateSnapshotSha256}</code>
            </div>
            <div className={styles.metaSub} data-testid="promotion-request-key">
              Idempotency-Key（本次申請固定，重試沿用）：<code>{requestKey}</code>
            </div>
            <div className={styles.metaSub} data-testid="promotion-request-ifmatch">
              If-Match：W/&quot;{record.version}&quot;
            </div>

            <div className={styles.sectionBox} style={{ marginTop: "8px", background: "#fefce8" }}>
              <div className={styles.sectionHead} style={{ color: "#854d0e" }}>
                風險宣告 RISK
              </div>
              <div className={styles.riskSummaryText} data-testid="promotion-risk-summary">
                晉升為高風險決策：核准後將以單一交易建立 Candidate Site 並排入
                SiteScore 評分（提案者 {proposerId || "—"} 不得自行核准）。決策、原因與
                風險確認會寫入 WORM audit。
              </div>
              <label className={styles.checkboxRow} htmlFor="promotion-request-ack">
                <input
                  checked={requestAck}
                  data-testid="promotion-request-ack"
                  disabled={busy}
                  id="promotion-request-ack"
                  onChange={(e) => setRequestAck(e.target.checked)}
                  type="checkbox"
                />
                <span>我已閱讀上述風險，確認提出晉升申請（等待第二人審查，不會立即建立 Candidate）。</span>
              </label>
            </div>

            <div style={{ marginTop: "10px", textAlign: "right" }}>
              <button
                className={styles.primaryButton}
                data-testid="promotion-request-submit"
                disabled={busy || !requestAck || requestReason.trim().length < 3}
                onClick={handleRequest}
                type="button"
              >
                {busy ? "提交中…（等待伺服器確認，不做樂觀更新）" : "提出晉升申請"}
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.noteBox} data-testid="promotion-request-denied">
            你目前的角色（{currentOperator.role}）無權提出晉升申請 — 需由負責此收件的
            展店人員提出。
            {requestDeniedReason ? (
              <> 後端拒絕代碼：<code>{requestDeniedReason}</code></>
            ) : null}
          </div>
        )
      ) : null}

      {record.stage !== "READY" && !promotion ? (
        <div className={styles.noteBox} data-testid="promotion-not-ready-note">
          收件狀態 {record.stage} 尚未 READY — 完成身分決策後才能提出 Candidate 晉升。
        </div>
      ) : null}

      {/* -------------------------------------------- second-actor review --- */}
      {reviewOpen && promotion ? (
        <div className={styles.sectionBox} data-testid="promotion-review-section" style={{ marginTop: "10px" }}>
          <div className={styles.sectionHead}>
            第二人審查 SECOND-ACTOR REVIEW
            {isSelfReview ? (
              <span className={styles.chip} data-testid="promotion-self-review-denied" data-tone="risk">
                ✕ SELF_REVIEW_DENIED
              </span>
            ) : (
              <span className={styles.chip} data-testid="promotion-second-actor-ok" data-tone="good">
                ✓ 提案者與審查者不同
              </span>
            )}
          </div>

          <div className={styles.metaGrid}>
            <div>
              <span className={styles.metaCaption}>提案者 Proposer</span>
              <div className={styles.metaValue} data-testid="promotion-proposer-id">
                <code>{proposerId || "—"}</code>
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>審查者 Reviewer</span>
              <div className={styles.metaValue} data-testid="promotion-reviewer-id">
                <code>{currentOperator.id}</code>（{currentOperator.role}）
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>審查 If-Match</span>
              <div className={styles.metaValue} data-testid="promotion-review-ifmatch">
                W/&quot;{promotion.version}&quot;
              </div>
            </div>
          </div>

          {isSelfReview ? (
            <div className={styles.errorPanel} data-testid="promotion-self-review-notice" role="alert">
              <span className={styles.errorSummary}>✕ 自我審查已封鎖（SELF_REVIEW_DENIED）</span>
              <span className={styles.errorMeta}>
                晉升申請的提案者（{proposerId}）不得核准或駁回自己的申請，請由另一位
                展店主管執行審查。
              </span>
            </div>
          ) : !canReview ? (
            <div className={styles.noteBox} data-testid="promotion-review-denied">
              你目前的角色（{currentOperator.role}）無審查權限 — 需要展店主管或選址審核
              角色（403 ROLE_DENIED 會被拒絕）。
              {reviewDeniedReason ? (
                <> 後端拒絕代碼：<code>{reviewDeniedReason}</code></>
              ) : null}
            </div>
          ) : (
            <>
              <label className={styles.fieldLabel} htmlFor="promotion-review-reason">
                審查理由（必填，寫入決策與 Audit）
              </label>
              <textarea
                className={styles.textarea}
                data-testid="promotion-review-reason"
                disabled={busy}
                id="promotion-review-reason"
                onChange={(e) => setReviewReason(e.target.value)}
                placeholder="例如：已核對 gate snapshot 與租金/面積門檻，核准建立 Candidate 並排入評分。"
                rows={3}
                value={reviewReason}
              />
              <label className={styles.checkboxRow} htmlFor="promotion-review-ack">
                <input
                  checked={reviewAck}
                  data-testid="promotion-review-ack"
                  disabled={busy}
                  id="promotion-review-ack"
                  onChange={(e) => setReviewAck(e.target.checked)}
                  type="checkbox"
                />
                <span>
                  我確認此審查為獨立第二人決策：核准將以單一交易建立 Candidate 並排入
                  SiteScore；駁回不建立任何 Candidate。
                </span>
              </label>
              <div className={styles.metaSub} data-testid="promotion-review-key">
                Idempotency-Key（本次審查固定）：<code>{reviewKey}</code>
              </div>
              <div className={styles.actionRow} style={{ marginTop: "10px", justifyContent: "flex-end" }}>
                <button
                  className={styles.secondaryButton}
                  data-testid="promotion-reject-btn"
                  disabled={busy || !reviewAck || reviewReason.trim().length < 3}
                  onClick={() => handleReview("REJECT")}
                  type="button"
                >
                  駁回申請（REJECT）
                </button>
                <button
                  aria-describedby={!canExecute ? "promotion-execute-denied" : undefined}
                  className={styles.primaryButton}
                  data-testid="promotion-approve-btn"
                  disabled={busy || !canExecute || !reviewAck || reviewReason.trim().length < 3}
                  onClick={() => handleReview("APPROVE")}
                  type="button"
                >
                  {busy ? "提交中…（不做樂觀更新）" : "核准晉升（APPROVE）"}
                </button>
              </div>
              {!canExecute ? (
                <div
                  className={styles.noteBox}
                  data-testid="promotion-execute-denied"
                  id="promotion-execute-denied"
                >
                  審查與執行權限分離：你可以駁回，但不可執行 Candidate promotion。
                  後端拒絕代碼：<code>{executeDeniedReason ?? "ROLE_DENIED"}</code>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      {/* ------------------------------------------------ durable receipt --- */}
      {promotion ? (
        <div className={styles.sectionBox} data-testid="promotion-receipt" style={{ marginTop: "10px" }}>
          <div className={styles.sectionHead}>持久化晉升收據 DURABLE PROMOTION RECEIPT</div>
          <div className={styles.metaGrid}>
            <div>
              <span className={styles.metaCaption}>決策編號 Decision ID</span>
              <div className={styles.metaValue} data-testid="promotion-decision-id">
                <code>{promotion.promotion_decision_id}</code>
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>決策型別 / 狀態</span>
              <div className={styles.metaValue} data-testid="promotion-receipt-status">
                {promotion.decision_type} · {promotion.status}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>版本 Version</span>
              <div className={styles.metaValue} data-testid="promotion-version">
                W/&quot;{promotion.version}&quot;
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>Candidate Site ID</span>
              <div className={styles.metaValue}>
                {candidateId ? (
                  <code data-testid="promotion-candidate-id">{candidateId}</code>
                ) : (
                  <span className={styles.correctedEmpty} data-testid="promotion-candidate-pending">
                    尚未產生（等待建立交易 commit）
                  </span>
                )}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>SiteScore Job ID</span>
              <div className={styles.metaValue}>
                {scoreJobId ? (
                  <code data-testid="promotion-score-job-id">{scoreJobId}</code>
                ) : (
                  <span className={styles.correctedEmpty} data-testid="promotion-score-job-pending">
                    尚未產生（等待評分排程 commit）
                  </span>
                )}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>審查者 Reviewer</span>
              <div className={styles.metaValue} data-testid="promotion-receipt-reviewer">
                {promotion.reviewer_subject_id ? <code>{promotion.reviewer_subject_id}</code> : "—（待審查）"}
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>Audit Event ID</span>
              <div className={styles.metaValue} data-testid="promotion-audit-event-id">
                <code>{promotion.audit_event_id}</code>
              </div>
            </div>
            <div>
              <span className={styles.metaCaption}>Correlation ID</span>
              <div className={styles.metaValue} data-testid="promotion-correlation-id">
                <code>{promotion.correlation_id}</code>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* --------------------------------------------------- score job ------ */}
      {promotion && (scoreJob || SCORE_JOB_COMMITTED.includes(promotion.status) || promotion.status === "CANDIDATE_CREATED") ? (
        <div style={{ marginTop: "10px" }}>
          <SiteScoreJobStatus
            busy={busy}
            candidateSiteId={candidateId}
            canCancel={canCancelScore}
            canReplay={canReplayScore}
            deniedReason={replayDeniedReason}
            history={scoreJobHistory}
            job={scoreJob as JobLifecycleReceipt | null}
            onCancel={onCancelScore}
            onReplay={onReplayScore}
            promotionStatus={promotion.status}
          />
        </div>
      ) : null}
    </section>
  );
}
