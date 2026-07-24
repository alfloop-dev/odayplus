"use client";

import { useState } from "react";
import styles from "../networkFindAreas.module.css";
import {
  DECISION_BUTTON_LABEL,
  DECISION_FINAL_LABEL,
  isOverride,
  type ReviewDecisionAction,
  type ReviewDecisionForm,
  type ReviewItem,
} from "./networkReviewTypes";

// ReviewDecisionDialog renders the "Dialog Review Decision" surface. Every
// decision requires a reason (written to the Decision Log); 核准 WAIT also
// requires pass conditions; 退回修改 requires the missing-data list (synced to
// the Candidate); and a decision that overrides the SiteScore recommendation
// requires an explicit risk acknowledgement. Validation is enforced here AND
// server-side — the dialog never lets an incomplete decision post.

const MIN_REASON_LEN = 10;

export function ReviewDecisionDialog({
  action,
  review,
  submitting,
  error,
  onClose,
  onSubmit,
}: {
  action: ReviewDecisionAction;
  review: ReviewItem;
  submitting?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (form: ReviewDecisionForm) => void;
}) {
  const [reason, setReason] = useState("");
  const [conditions, setConditions] = useState("");
  const [requiredData, setRequiredData] = useState("");
  const [ack, setAck] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const override = isOverride(action, review.recommendation);
  const finalLabel = DECISION_FINAL_LABEL[action];

  const syncNote =
    `送出後 Candidate、Review、Approval、Decision、Audit 於同一交易同步：` +
    `${review.candidateId} → ${finalLabel}，並寫入 Decision Log 與稽核軌跡。`;

  function handleSubmit() {
    if (reason.trim().length < MIN_REASON_LEN) {
      setLocalError("決策原因必填（至少 10 字）。");
      return;
    }
    if (action === "WAIT" && !conditions.trim()) {
      setLocalError("核准 WAIT 需填寫通過條件。");
      return;
    }
    if (action === "RETURN" && !requiredData.trim()) {
      setLocalError("退回修改需填寫需補資料。");
      return;
    }
    if (override && !ack) {
      setLocalError("本決策覆寫系統建議，需勾選風險確認。");
      return;
    }
    setLocalError(null);
    onSubmit({ reason: reason.trim(), conditions: conditions.trim(), requiredData: requiredData.trim(), overrideAck: ack });
  }

  const shownError = localError ?? error ?? null;

  return (
    <div
      className={styles.reviewDialogOverlay}
      data-screen-label="Dialog Review Decision"
      data-testid="review-decision-dialog"
      role="dialog"
      aria-modal="true"
      aria-label={`${DECISION_BUTTON_LABEL[action]} — ${review.candidateTitle}`}
    >
      <div className={styles.reviewDialog}>
        <div className={styles.reviewDialogHead}>
          <div className={styles.reviewDialogTitle} data-testid="review-decision-title">
            {DECISION_BUTTON_LABEL[action]} · {review.candidateTitle}
          </div>
          <button
            className={styles.reviewDialogClose}
            data-testid="review-decision-close"
            onClick={onClose}
            type="button"
            aria-label="關閉"
          >
            ×
          </button>
        </div>

        <div className={styles.reviewDialogBody}>
          <div className={styles.reviewDialogSub}>
            {review.id} · SiteScore {review.recommendation} {review.score} → {finalLabel}
          </div>

          {override ? (
            <div className={styles.reviewOverrideHint} data-testid="review-decision-override-warning">
              本決策與系統建議不一致（Override recommendation）— 需填寫覆寫理由並勾選風險確認。
            </div>
          ) : null}

          <div className={styles.reviewDialogField}>
            <span className={styles.reviewDialogFieldLabel}>決策原因（必填 — 寫入 Decision Log）</span>
            <textarea
              className={styles.reviewDialogInput}
              data-testid="review-decision-reason"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="例：人流量體大且回本期可接受，惟施工期影響需以條件管理…"
            />
          </div>

          {action === "WAIT" ? (
            <div className={styles.reviewDialogField}>
              <span className={`${styles.reviewDialogFieldLabel} ${styles.reviewDialogFieldLabelWarn}`}>
                通過條件（必填 — 條件達成後可重評為 GO）
              </span>
              <textarea
                className={styles.reviewDialogInput}
                data-testid="review-decision-conditions"
                rows={3}
                value={conditions}
                onChange={(event) => setConditions(event.target.value)}
                placeholder="例：租金議價至 52,000 以下；完成現勘；補充晚間人流資料"
              />
            </div>
          ) : null}

          {action === "RETURN" ? (
            <div className={styles.reviewDialogField}>
              <span className={styles.reviewDialogFieldLabel}>
                需補資料（必填 — 以「、」分隔，會同步至 Candidate 缺資料清單）
              </span>
              <input
                className={styles.reviewDialogInput}
                data-testid="review-decision-required"
                value={requiredData}
                onChange={(event) => setRequiredData(event.target.value)}
                placeholder="例：現勘紀錄、晚間人流樣本"
              />
            </div>
          ) : null}

          {override ? (
            <button
              className={styles.reviewAckButton}
              data-checked={ack ? "true" : "false"}
              data-testid="review-decision-ack"
              onClick={() => setAck((value) => !value)}
              type="button"
            >
              <span className={styles.reviewAckMark} aria-hidden="true">
                {ack ? "☑" : "☐"}
              </span>
              <span className={styles.reviewAckText}>
                我了解本決策為覆寫系統建議，已評估相關風險，並同意記錄於 Decision Log（風險確認）。
              </span>
            </button>
          ) : null}

          {shownError ? (
            <div className={styles.errorText} data-testid="review-decision-error">
              {shownError}
            </div>
          ) : null}

          <div className={styles.reviewSyncNote} data-testid="review-decision-sync-note">
            {syncNote}
          </div>
        </div>

        <div className={styles.reviewDialogActions}>
          <button className={styles.reviewDialogCancel} onClick={onClose} type="button">
            取消
          </button>
          <button
            className={styles.reviewDialogSubmit}
            data-testid="review-decision-submit"
            disabled={submitting}
            onClick={handleSubmit}
            type="button"
          >
            {submitting ? "同步中…" : `確認${DECISION_BUTTON_LABEL[action]}`}
          </button>
        </div>
      </div>
    </div>
  );
}
