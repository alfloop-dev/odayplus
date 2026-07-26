"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { ReviewQueueRow } from "../networkFindAreasViewModel";
import { recommendationTone, type ScoreRecommendation } from "./networkScoringTypes";
import { ReviewDecisionDialog } from "./ReviewDecisionDialog";
import {
  DECISION_BUTTON_LABEL,
  reviewStatusTone,
  type ReviewDecisionAction,
  type ReviewDecisionForm,
  type ReviewItem,
} from "./networkReviewTypes";

// ReviewPanel owns the "選址審核" tab (data-screen-label "Network 選址審核").
// It is the reviewer's decision surface: a queue on the left, the selected
// review detail on the right, and GO / WAIT / 退回 / 駁回 actions that open the
// ReviewDecisionDialog. The decision syncs Candidate + Review + Approval +
// Decision + Audit atomically server-side. When the scoring API is
// unavailable, it falls back to the fixture review queue and applies decisions
// optimistically so the surface stays operable.

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
  submitting,
  decideError,
  onDecide,
}: {
  reviews: ReviewItem[];
  fallbackRows: ReviewQueueRow[];
  canDecide?: boolean;
  submitting?: boolean;
  decideError?: string | null;
  onDecide?: (reviewId: string, action: ReviewDecisionAction, form: ReviewDecisionForm) => Promise<boolean> | boolean | void;
}) {
  const seeded = useMemo(
    () => (reviews.length ? reviews : fallbackRows.map(fallbackToReview)),
    [reviews, fallbackRows],
  );
  const [items, setItems] = useState<ReviewItem[]>(seeded);
  const [selectedId, setSelectedId] = useState(seeded[0]?.id ?? "");
  const [dialogAction, setDialogAction] = useState<ReviewDecisionAction | null>(null);

  useEffect(() => {
    setItems(seeded);
  }, [seeded]);

  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const pendingCount = items.filter((item) => item.status === "pending").length;

  async function submitDecision(form: ReviewDecisionForm) {
    if (!selected || !dialogAction) return;
    const result = await onDecide?.(selected.id, dialogAction, form);
    if (result === false) return; // keep the dialog open; server rejected
    applyOptimisticDecision(selected.id, dialogAction, form);
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
        <span className={styles.muted}>{pendingCount} 待審核 · 決策同步 Candidate／Approval／Decision／Audit</span>
      </div>

      {items.length ? (
        <div className={styles.reviewLayout}>
          <div className={styles.reviewQueue} data-testid="review-queue">
            {items.map((item) => (
              <button
                className={styles.reviewQueueCard}
                data-active={selected?.id === item.id ? "true" : undefined}
                data-testid={`review-card-${item.id}`}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
                type="button"
              >
                <div className={styles.reviewQueueTop}>
                  <span className={styles.reviewQueueId}>{item.id}</span>
                  <ToneBadge tone={recommendationTone(item.recommendation)}>
                    {item.recommendation} {item.score}
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
            {selected ? (
              <ReviewDetail
                canDecide={canDecide}
                review={selected}
                onOpenDecision={(action) => setDialogAction(action)}
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
  onOpenDecision,
}: {
  review: ReviewItem;
  canDecide: boolean;
  onOpenDecision: (action: ReviewDecisionAction) => void;
}) {
  const isPending = review.status === "pending";
  return (
    <>
      <div className={styles.reviewDetailHead}>
        <span className={styles.reviewQueueId}>{review.id}</span>
        <ToneBadge tone={recommendationTone(review.recommendation)}>
          SiteScore {review.recommendation} {review.score}
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
        <Fact label="模型／快照" value={`${review.modelVersion} · ${review.datasetSnapshotId}`} />
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
          {canDecide ? (
            <div className={styles.reviewDecisionBar}>
              <button
                className={styles.btnGo}
                data-testid={`review-btn-go-${review.id}`}
                onClick={() => onOpenDecision("GO")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.GO}
              </button>
              <button
                className={styles.btnWait}
                data-testid={`review-btn-wait-${review.id}`}
                onClick={() => onOpenDecision("WAIT")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.WAIT}
              </button>
              <button
                className={styles.btnReturn}
                data-testid={`review-btn-return-${review.id}`}
                onClick={() => onOpenDecision("RETURN")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.RETURN}
              </button>
              <button
                className={styles.btnReject}
                data-testid={`review-btn-reject-${review.id}`}
                onClick={() => onOpenDecision("REJECT")}
                type="button"
              >
                {DECISION_BUTTON_LABEL.REJECT}
              </button>
            </div>
          ) : (
            <div className={styles.reviewRoleNote} data-testid={`review-role-note-${review.id}`}>
              目前角色可準備／送審，但不能決策。請切換至授權審核角色（Site Reviewer）後再進行核決。
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className={styles.reviewMetaLabel}>{label}</div>
      <div className={styles.reviewMetaValue}>{value}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
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
    score: row.score ?? 0,
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
