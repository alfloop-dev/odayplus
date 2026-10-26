"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { ReviewQueueRow } from "../networkFindAreasViewModel";
import { recommendationTone, type CandidateReviewReadTarget, type ScoreRecommendation } from "./networkScoringTypes";
import { ReviewDecisionDialog } from "./ReviewDecisionDialog";
import { formatReviewConfidence, formatReviewScore, hasRecordedReviewReport, isReviewPending } from "./networkReviewProjection";
import {
  DECISION_BUTTON_LABEL,
  reviewStatusTone,
  type ReviewDecisionAction,
  type ReviewDecisionForm,
  type ReviewItem,
} from "./networkReviewTypes";

// Canonical rows and decisions come from the authorized review readback.
// Fixture fallback and optimistic updates are confined to fixture callers.

const DECISION_STATUS: Record<ReviewDecisionAction, { status: string; label: string }> = {
  GO: { status: "approved", label: "已核准 GO" },
  WAIT: { status: "onhold", label: "On Hold（WAIT）" },
  RETURN: { status: "needdata", label: "退回補件（Need Data）" },
  REJECT: { status: "rejected", label: "已駁回" },
};

export function ReviewPanel({
  reviews,
  fallbackRows,
  canDecide = true,
  decisionUnavailableReason,
  submitting,
  decideError,
  onDecide,
  onRequestDecision,
  workflowCandidateId,
  expectedReport,
  onSelectCandidate,
  selectionError,
  fixtureCandidateContext = false,
  explicitReadTarget,
  explicitSelectionError,
  canonicalCurrentReviewTarget,
}: {
  explicitReadTarget?: CandidateReviewReadTarget | null;
  /** Verified native current pending pointer; report age alone never grants authority. */
  canonicalCurrentReviewTarget?: CandidateReviewReadTarget | null;
  explicitSelectionError?: string | null;
  reviews: ReviewItem[];
  fallbackRows: ReviewQueueRow[];
  canDecide?: boolean;
  /** Read-only explanation supplied separately from the role eligibility. */
  decisionUnavailableReason?: string;
  submitting?: boolean;
  decideError?: string | null;
  onRequestDecision?: (reviewId: string, action: ReviewDecisionAction) => Promise<boolean> | boolean;
  onDecide?: (reviewId: string, action: ReviewDecisionAction, form: ReviewDecisionForm) => Promise<boolean> | boolean | void;
  workflowCandidateId?: string | null;
  expectedReport?: { reportId: string; reportVersion: number } | null;
  onSelectCandidate?: (candidateId: string) => void;
  selectionError?: string | null;
  /** Local fixture compatibility only; production keeps exact committed-report matching. */
  fixtureCandidateContext?: boolean;
}) {
  const seeded = useMemo(
    () => resolveEffectiveReviews(reviews, fallbackRows),
    [reviews, fallbackRows],
  );
  const [items, setItems] = useState<ReviewItem[]>(seeded);
  const [selectedId, setSelectedId] = useState(seeded[0]?.id ?? "");
  const [historySelection, setHistorySelection] = useState<{ reviewId: string; candidateId: string } | null>(null);
  const [dialogAction, setDialogAction] = useState<ReviewDecisionAction | null>(null);

  useEffect(() => {
    setItems(seeded);
  }, [seeded]);
  const decisionReportTarget = canonicalCurrentReviewTarget && canonicalCurrentReviewTarget.candidateId === workflowCandidateId
    ? canonicalCurrentReviewTarget : expectedReport;
  useEffect(() => {
    setHistorySelection(null);
    setDialogAction(null);
  }, [workflowCandidateId, decisionReportTarget?.reportId, decisionReportTarget?.reportVersion,
    canonicalCurrentReviewTarget?.reviewId, canonicalCurrentReviewTarget?.generation,
    fixtureCandidateContext, explicitReadTarget?.reviewId, explicitSelectionError]);

  const exactReviews = items.filter((item) => {
    const identity = item as ReviewItem & { reportId?: string; reportVersion?: number };
    return item.candidateId === workflowCandidateId && expectedReport
      && identity.reportId === expectedReport.reportId && identity.reportVersion === expectedReport.reportVersion;
  });
  // Legacy fixtures have a recorded candidate/review pair but no immutable
  // report lineage. Admit only an explicit fixture opt-in and a unique exact
  // candidate; canonical or lineage-bearing rows retain report matching.
  const fixtureCandidateReviews = fixtureCandidateContext && workflowCandidateId && expectedReport == null
    ? items.filter((item) => item.candidateId === workflowCandidateId)
    : [];
  const fixtureReview = fixtureCandidateReviews.length === 1 ? fixtureCandidateReviews[0] : undefined;
  const fixtureLegacyCurrent = fixtureReview && fixtureReview.canonicalDecisionStatus === undefined
    && fixtureReview.reportId === undefined && fixtureReview.reportVersion === undefined
    && (fixtureReview as ReviewItem & { sitescoreRunId?: unknown }).sitescoreRunId === undefined;
  const currentReviews = fixtureLegacyCurrent ? fixtureCandidateReviews : exactReviews;
  const historical = historySelection?.candidateId === workflowCandidateId
    ? items.find((item) => item.id === historySelection?.reviewId && item.candidateId === workflowCandidateId) : undefined;
  const explicitRows = explicitReadTarget && explicitReadTarget.candidateId === workflowCandidateId
    ? items.filter((item) => item.id === explicitReadTarget.reviewId && item.candidateId === explicitReadTarget.candidateId
      && item.reportId === explicitReadTarget.reportId && item.reportVersion === explicitReadTarget.reportVersion)
    : [];
  const boundTarget = canonicalCurrentReviewTarget;
  const boundPendingRows = onRequestDecision && boundTarget && boundTarget.candidateId === workflowCandidateId
    ? items.filter((item) => item.id === boundTarget.reviewId
      && item.candidateId === boundTarget.candidateId
      && item.reportId === boundTarget.reportId && item.reportVersion === boundTarget.reportVersion
      && item.canonicalDecisionStatus === "PENDING_REVIEW" && isReviewPending(item) && hasRecordedReviewReport(item)) : [];
  const selected = selectionError || explicitSelectionError ? undefined : explicitReadTarget
    ? explicitRows.length === 1 ? explicitRows[0] : undefined
    : workflowCandidateId ? historical ?? (boundPendingRows.length === 1 ? boundPendingRows[0]
      : currentReviews.length === 1 ? currentReviews[0] : undefined)
    : items.find((item) => item.id === selectedId) ?? items[0];
  const selectedIsCurrentReport = explicitReadTarget?.relation !== "historical"
    && (!workflowCandidateId || currentReviews.length === 1 && selected?.id === currentReviews[0].id);
  const selectedIsBoundPending = boundPendingRows.length === 1 && selected?.id === boundPendingRows[0].id;
  const selectedIsDecisionTarget = selectedIsCurrentReport || selectedIsBoundPending;
  const pendingCount = items.filter(isReviewPending).length;

  async function requestDecision(action: ReviewDecisionAction) {
    if (!selected || submitting || !selectedIsDecisionTarget || !canDecide || !isReviewPending(selected) || !hasRecordedReviewReport(selected)) return;
    const requested = onRequestDecision?.(selected.id, action);
    if (typeof requested === "boolean" ? !requested : requested && !await requested) return;
    setDialogAction(action);
  }

  async function submitDecision(form: ReviewDecisionForm) {
    if (!selected || !dialogAction || !selectedIsDecisionTarget || !canDecide || !isReviewPending(selected) || !hasRecordedReviewReport(selected)) return;
    const result = await onDecide?.(selected.id, dialogAction, form);
    if (result === false) return; // keep the dialog open; server rejected
    // Canonical state, actors and audit come from the authoritative readback.
    // A successful POST alone must not fabricate an approval or a hold.
    if (!selected.canonicalDecisionStatus) applyOptimisticDecision(selected.id, dialogAction, form);
    setDialogAction(null);
  }

  function applyOptimisticDecision(reviewId: string, action: ReviewDecisionAction, form: ReviewDecisionForm) {
    const mapped = DECISION_STATUS[action];
    const requiredList = form.requiredData
      .split(/[、,]/)
      .map((value) => value.trim())
      .filter(Boolean);
    setItems((current) =>
      current.map((item) =>
        item.id === reviewId
          ? {
              ...item,
              status: mapped.status,
              statusLabel: mapped.label,
              pending: false,
              decided: true,
              candidateStatusLabel: mapped.label,
              candidateMissingData: action === "RETURN" ? requiredList : [],
              decision: {
                decision: action,
                finalLabel: mapped.label,
                mappedStatus: mapped.status,
                reason: form.reason,
                conditions: form.conditions || undefined,
                requiredData: requiredList,
                override: item.recommendation !== action && action !== "RETURN",
                decidedAt: "",
                decidedBy: "審核角色",
              },
            }
          : item,
      ),
    );
  }

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network 選址審核"
      data-testid="network-panel-review"
      role="tabpanel"
    >
      <div className={styles.panelHeader}>
        <h3>選址審核 / Review</h3>
        <span className={styles.muted}>{pendingCount} 待審核</span>
      </div>

      {items.length ? (
        <div className={styles.reviewLayout}>
          <div className={styles.reviewQueue} data-testid="review-queue">
            {items.map((item) => (
              <button
                className={styles.reviewQueueCard}
                data-active={selected?.id === item.id ? "true" : "false"}
                data-testid={`review-card-${item.id}`}
                key={item.id}
                onClick={() => { setSelectedId(item.id); setHistorySelection({ reviewId: item.id, candidateId: item.candidateId }); onSelectCandidate?.(item.candidateId); }}
                type="button"
              >
                <div className={styles.reviewQueueTop}>
                  <span className={styles.reviewQueueId}>{item.id}</span>
                  <ToneBadge tone={recommendationTone(item.recommendation)}>
                    {item.recommendation} {formatReviewScore(item.score)}
                  </ToneBadge>
                  {item.risk ? <span className={styles.flagRisk}>{item.risk}</span> : null}
                  <span style={{ marginLeft: "auto" }}>
                    <ToneBadge tone={reviewStatusTone(item.status)}>{item.statusLabel}</ToneBadge>
                  </span>
                </div>
                <div className={styles.reviewQueueName}>{item.candidateTitle}</div>
                <div className={styles.reviewQueueMeta}>
                  <span>{item.requestedBy}</span>
                  <span>送審 {item.submittedAt}</span>
                  {item.dueAt ? <span className={styles.reviewQueueDue}>期限 {item.dueAt}</span> : null}
                </div>
              </button>
            ))}
          </div>

          <div className={styles.reviewDetail}>
            {decideError && !dialogAction ? <p role="alert" data-testid="review-action-error">{decideError}</p> : null}
            {selectionError ? <p role="status">{selectionError}</p> : null}
            {explicitSelectionError ? <p role="status" data-testid="review-explicit-target-unavailable">{explicitSelectionError}</p> : null}
            {explicitReadTarget && selected ? <p role="status" data-testid="review-historical-target-context">
              指定審核 {explicitReadTarget.reviewId} · 凍結報告 {explicitReadTarget.reportId} v{explicitReadTarget.reportVersion}。
              {expectedReport ? ` 最新報告 ${expectedReport.reportId} v${expectedReport.reportVersion}。` : " 最新報告尚未核驗。"}
              {explicitReadTarget.relation === "historical"
                ? selectedIsBoundPending ? "目前待審核對此凍結報告核決；最新版報告另列，不取代本輪報告。"
                  : "歷史紀錄僅供查閱，未套用為最新版報告的決策。"
                : "此審核只對應上述報告版本。"}
            </p> : null}
            {workflowCandidateId && !selectedIsDecisionTarget ? <p role="status">目前報告的人審關聯尚未唯一核對；可查閱歷史項目，未套用為目前報告的決策。</p> : null}
            {selected ? (
              <ReviewDetail
                canDecide={canDecide && selectedIsDecisionTarget}
                decisionUnavailableReason={decisionUnavailableReason}
                busy={submitting}
                review={selected}
                onOpenDecision={(action) => { void requestDecision(action); }}
              />
            ) : (
              <div className={styles.emptyState}>選擇左側審核項目</div>
            )}
          </div>
        </div>
      ) : (
        <div className={styles.emptyState}>目前沒有審核項目。</div>
      )}

      {dialogAction && selected ? (
        <ReviewDecisionDialog
          action={dialogAction}
          review={selected}
          submitting={submitting}
          error={decideError}
          onClose={() => setDialogAction(null)}
          onSubmit={submitDecision}
        />
      ) : null}
    </div>
  );
}

function ReviewDetail({
  review,
  canDecide,
  decisionUnavailableReason,
  busy,
  onOpenDecision,
}: {
  review: ReviewItem;
  canDecide: boolean;
  decisionUnavailableReason?: string;
  busy?: boolean;
  onOpenDecision: (action: ReviewDecisionAction) => void;
}) {
  const isPending = isReviewPending(review);
  return (
    <>
      <div className={styles.reviewDetailHead}>
        <span className={styles.reviewQueueId}>{review.id}</span>
        <ToneBadge tone={recommendationTone(review.recommendation)}>
          SiteScore {review.recommendation} {formatReviewScore(review.score)}
        </ToneBadge>
        {review.risk ? <span className={styles.flagRisk}>{review.risk}</span> : null}
        <span style={{ marginLeft: "auto" }}>
          <ToneBadge tone={reviewStatusTone(review.status)}>{review.statusLabel}</ToneBadge>
        </span>
      </div>

      <h3 style={{ marginTop: 8 }}>
        {review.candidateTitle}
        <small className={styles.muted}>　{review.candidateId}</small>
      </h3>

      <div className={styles.reviewMetaGrid}>
        <Meta label="申請人" value={review.requestedBy} />
        <Meta label="審核角色" value={review.reviewerRole} />
        <Meta label="送審時間" value={review.submittedAt} />
        <Meta label="期限" value={review.dueAt || "—"} />
      </div>

      <div className={styles.reviewMetricGrid} data-testid={`review-metrics-${review.id}`}>
        <Meta label="回本期" value={review.payback || "—"} />
        <Meta label="M12 P50" value={review.m12P50 || "—"} />
        <Meta label="租金合理性" value={review.rentReasonableness || "—"} />
        <Meta label="自家稀釋" value={review.cannibalization || "—"} />
      </div>

      <div className={styles.reviewFacts}>
        <Fact label="來源物件" value={review.sourceListingId} />
        <Fact label="現勘" value={review.fieldVisit} />
        <Fact label="仲介聯絡" value={review.brokerContact} />
        <Fact label="候選備註" value={review.notes} />
        <Fact label="模型／快照" value={`${review.modelVersion ?? "未提供"} · ${review.datasetSnapshotId ?? "未提供"}`} />
        {review.canonicalDecisionStatus || review.confidenceStatus ? (
          <Fact label="信心值" value={formatReviewConfidence(review)} />
        ) : null}
        {review.canonicalDecisionStatus ? (
          <Fact label="正式決策狀態" value={review.canonicalDecisionStatus} />
        ) : null}
        <Fact label="比較結果" value={review.compareText} />
        <Fact label="Candidate 狀態" value={review.candidateStatusLabel ?? review.statusLabel} />
      </div>

      {review.eventChips.length ? (
        <div className={styles.reviewChips}>
          {review.eventChips.map((chip) => (
            <span className={styles.reviewChip} key={chip}>
              {chip}
            </span>
          ))}
        </div>
      ) : null}

      {review.history.length ? (
        <div className={styles.reviewHistory}>
          <div className={styles.reviewMetaLabel}>CANDIDATE 歷程</div>
          {review.history.map((entry, index) => (
            <div className={styles.reviewHistoryRow} key={`${entry.t}-${index}`}>
              <span className={styles.reviewHistoryTime}>{entry.t}</span>
              <span>{entry.v}</span>
            </div>
          ))}
        </div>
      ) : null}

      {review.decision && !isPending ? (
        <div className={styles.reviewDecided} data-testid={`review-decided-${review.id}`}>
          決策：{review.decision.finalLabel}（{review.decision.decision}）· 原因：{review.decision.reason}
          {review.decision.conditions ? ` · 通過條件：${review.decision.conditions}` : ""}
          {review.decision.requiredData && review.decision.requiredData.length
            ? ` · 需補資料：${review.decision.requiredData.join("、")}`
            : ""}
          {review.decision.override ? "（覆寫系統建議）" : ""}
        </div>
      ) : null}

      {isPending ? (
        <div className={styles.reviewPending}>
          <div className={styles.reviewSyncNote} data-testid={`review-sync-note-${review.id}`}>
            每個審核決策都會開啟確認視窗：<b>決策原因必填</b>；核准 WAIT 須填通過條件；退回修改須填需補資料；覆寫系統建議須勾選風險確認。決策後 Candidate 狀態自動同步並寫入 Decision Log。
          </div>
          {!hasRecordedReviewReport(review) ? (
            <div className={styles.reviewRoleNote} data-testid={`review-report-note-${review.id}`}>
              此審查綁定的評分報告{review.reportAvailability === "conflict" ? "資料關聯不一致" : "暫時無法取得"}，請確認報告後再核決。
            </div>
          ) : canDecide ? (
            <div className={styles.reviewDecisionBar}>
              <button
                className={styles.btnGo}
                data-testid={`review-btn-go-${review.id}`}
                disabled={busy}
                onClick={() => onOpenDecision("GO")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.GO}
              </button>
              <button
                className={styles.btnWait}
                data-testid={`review-btn-wait-${review.id}`}
                disabled={busy}
                onClick={() => onOpenDecision("WAIT")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.WAIT}
              </button>
              <button
                className={styles.btnReturn}
                data-testid={`review-btn-return-${review.id}`}
                disabled={busy}
                onClick={() => onOpenDecision("RETURN")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.RETURN}
              </button>
              <button
                className={styles.btnReject}
                data-testid={`review-btn-reject-${review.id}`}
                disabled={busy}
                onClick={() => onOpenDecision("REJECT")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.REJECT}
              </button>
            </div>
          ) : (
            <div className={styles.reviewRoleNote} data-testid={`review-role-note-${review.id}`}>
              {decisionUnavailableReason ?? "目前角色可準備／送審，但不能決策。請切換至授權審核角色（Site Reviewer）後再進行核決。"}
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}

function Meta({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className={styles.reviewMetaLabel}>{label}</div>
      <div className={styles.reviewMetaValue}>{value ?? "—"}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className={styles.reviewFactRow}>
      <span className={styles.reviewFactKey}>{label}</span>
      <span>{value || "—"}</span>
    </div>
  );
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}

// The one rule for which review queue is shown: the dedicated reviews when it
// has rows, otherwise the fallback rows (callers pass fallback rows only in
// fixture mode). The workspace header chip and tab badge count this same list,
// so a fixture-mode empty response cannot report zero while rows are listed.
export function resolveEffectiveReviews(reviews: ReviewItem[], fallbackRows: ReviewQueueRow[]): ReviewItem[] {
  return reviews.length ? reviews : fallbackRows.map(fallbackToReview);
}

// Fixture fallback: adapt a viewModel ReviewQueueRow into the minimal ReviewItem
// shape when the review API is unavailable.
const FIXTURE_STATUS: Record<string, { status: string; label: string }> = {
  pending: { status: "pending", label: "待審核" },
  approved: { status: "approved", label: "已核准 GO" },
  returned: { status: "needdata", label: "退回補件（Need Data）" },
  rejected: { status: "rejected", label: "已駁回" },
};

function fallbackToReview(row: ReviewQueueRow): ReviewItem {
  const mapped = FIXTURE_STATUS[row.status] ?? { status: row.status, label: row.statusLabel };
  const recommendation: ScoreRecommendation = row.recommendation ?? "WAIT";
  return {
    id: row.id,
    candidateId: row.candidateId,
    candidateTitle: row.candidateTitle,
    zoneLabel: row.zoneLabel,
    recommendation,
    score: row.score ?? null,
    risk: "",
    status: mapped.status,
    statusLabel: row.statusLabel || mapped.label,
    requestedBy: row.requestedByLabel,
    reviewerRole: row.reviewerLabels.join("、"),
    submittedAt: row.requestedAt,
    dueAt: "",
    payback: "—",
    m12P50: "—",
    rentReasonableness: "—",
    cannibalization: "—",
    sourceListingId: "—",
    fieldVisit: "—",
    brokerContact: "—",
    notes: "—",
    modelVersion: "SiteScore v2.3",
    datasetSnapshotId: "—",
    compareText: "—",
    candidateStatusLabel: mapped.label,
    candidateMissingData: [],
    eventChips: [],
    history: [],
    decision: row.reason
      ? {
          decision: "RETURN",
          finalLabel: mapped.label,
          mappedStatus: mapped.status,
          reason: row.reason,
          override: false,
          decidedAt: row.decidedAt ?? "",
          decidedBy: "審核角色",
        }
      : null,
    pending: row.status === "pending",
    decided: row.status !== "pending",
  };
}
