"use client";

import { useCallback, useState } from "react";
import styles from "../networkFindAreas.module.css";
import type { ListingApiError } from "./listingsClient";
import { useModalDialogBehavior } from "./useModalDialogBehavior";

// "Dialog 合併重複物件" — the confirmation surface for a Listing Radar merge
// (ODP-OC-R5-011).
//
// Merge is a high-impact, irreversible write, so three rules are load-bearing
// here and are NOT negotiable:
//
//   1. The operator supplies the reason in their own words. The dialog never
//      defaults or pre-fills it — a stored reason nobody wrote is not a reason.
//   2. The exact risk summary rendered above the checkbox is the string sent to
//      the server. It is not rebuilt at submit time: the audit record must
//      store the text the operator actually read, not a re-derivation of it.
//   3. No optimistic UI. Nothing is written until the server answers, and
//      cancelling — or submitting without a reason or acknowledgement — writes
//      nothing at all. While the write IS in flight the dialog cannot be
//      dismissed (button, Escape or backdrop): hiding a high-impact write whose
//      outcome is still unknown would leave the operator guessing whether it
//      landed.

const MIN_REASON_LEN = 10;

export type ListingMergeRequest = {
  sourceListingId: string;
  targetListingId: string;
  sourceLabel?: string;
  targetLabel?: string;
  /** Stable for this merge across retries; see newMergeIdempotencyKey. */
  idempotencyKey: string;
};

export type ListingMergeForm = {
  reason: string;
  riskSummary: string;
  riskAcknowledged: boolean;
};

export function ListingMergeDialog({
  busy,
  error,
  onClose,
  onSubmit,
  request,
}: {
  busy?: boolean;
  error?: ListingApiError | null;
  onClose: () => void;
  onSubmit: (form: ListingMergeForm) => void;
  request: ListingMergeRequest;
}) {
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const { sourceListingId, targetListingId } = request;
  const riskSummary = buildMergeRiskSummary(sourceListingId, targetListingId);

  // A write in flight is not dismissable — see rule 3 above.
  const dismissible = !busy;
  const requestClose = useCallback(() => {
    if (busy) return;
    onClose();
  }, [busy, onClose]);
  const panelRef = useModalDialogBehavior({ dismissible, onClose: requestClose });

  function handleSubmit() {
    if (busy) return;
    // merge_listing ALWAYS requires a reason server-side (422 otherwise), so
    // the dialog never treats it as optional.
    if (reason.trim().length < MIN_REASON_LEN) {
      setLocalError("合併原因必填（至少 10 字 — 寫入 Audit）。");
      return;
    }
    if (!riskAcknowledged) {
      setLocalError("請先勾選確認，表示你已了解合併的影響。");
      return;
    }
    setLocalError(null);
    onSubmit({ reason: reason.trim(), riskSummary, riskAcknowledged });
  }

  const shownError = localError ?? error?.summary ?? null;
  const summaryRows = buildSummaryRows(request);

  return (
    <div
      className={styles.reviewDialogOverlay}
      data-screen-label="Dialog 合併重複物件"
      data-testid="listing-merge-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <div
        aria-busy={busy ? "true" : undefined}
        aria-label={`合併重複物件 — ${sourceListingId} → ${targetListingId}`}
        aria-modal="true"
        className={styles.reviewDialog}
        ref={panelRef}
        role="dialog"
      >
        <div className={styles.reviewDialogHead}>
          <div className={styles.reviewDialogTitle} data-testid="listing-merge-title">
            合併重複物件 · {sourceListingId} → {targetListingId}
          </div>
          <button
            aria-label="關閉"
            className={styles.reviewDialogClose}
            data-testid="listing-merge-close"
            disabled={busy}
            onClick={requestClose}
            type="button"
          >
            ×
          </button>
        </div>

        <div className={styles.reviewDialogBody}>
          <div className={styles.reviewDialogSub}>
            合併前檢視 — 送出後 {sourceListingId} 不再獨立評估
          </div>

          <div data-testid="listing-merge-summary">
            {summaryRows.map((row) => (
              <div className={styles.reviewDialogField} key={row.key}>
                <span className={styles.reviewDialogFieldLabel}>{row.key}</span>
                <span>{row.value}</span>
              </div>
            ))}
          </div>

          <div className={styles.reviewDialogField}>
            <span className={styles.reviewDialogFieldLabel}>
              合併原因（必填 — 寫入 Audit）
            </span>
            <textarea
              className={styles.reviewDialogInput}
              data-autofocus
              data-testid="listing-merge-reason"
              onChange={(event) => setReason(event.target.value)}
              placeholder="例：同地址、同租金，且來源證據確認為同一物件的重複刊登…"
              rows={3}
              value={reason}
            />
          </div>

          <div className={styles.reviewOverrideHint} data-testid="listing-merge-risk-summary">
            {riskSummary}
          </div>

          <button
            className={styles.reviewAckButton}
            data-checked={riskAcknowledged ? "true" : "false"}
            data-testid="listing-merge-risk-ack"
            onClick={() => setRiskAcknowledged((value) => !value)}
            type="button"
          >
            <span aria-hidden="true" className={styles.reviewAckMark}>
              {riskAcknowledged ? "☑" : "☐"}
            </span>
            <span className={styles.reviewAckText}>
              我已閱讀並了解上述風險，確認執行合併（將連同此摘要寫入 Audit）。
            </span>
          </button>

          {shownError ? (
            <div className={styles.errorText} data-testid="listing-merge-error" role="alert">
              <span>{shownError}</span>
              {error ? (
                <span data-testid="listing-merge-error-meta">
                  {" "}
                  錯誤碼 {error.code}
                  {error.correlationId ? ` · correlation ${error.correlationId}` : ""} · 發生於{" "}
                  {error.occurredAt} · 下一步：{error.nextAction}
                </span>
              ) : null}
            </div>
          ) : null}

          <div className={styles.reviewSyncNote} data-testid="listing-merge-note">
            此操作不使用 optimistic UI — 按下確認才會寫入，並記錄操作者、原因、風險摘要與
            correlation ID。
          </div>
        </div>

        <div className={styles.reviewDialogActions}>
          <button
            className={styles.reviewDialogCancel}
            data-testid="listing-merge-cancel"
            disabled={busy}
            onClick={requestClose}
            type="button"
          >
            取消
          </button>
          <button
            className={styles.reviewDialogSubmit}
            data-testid="listing-merge-submit"
            disabled={busy}
            onClick={handleSubmit}
            type="button"
          >
            {busy ? "寫入中…" : "確認合併"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The disclosure shown to the operator and stored on the audit event. It states
 * the durable effect and what is irreversible about it, because "確認合併" alone
 * does not tell the operator what they are about to change.
 */
export function buildMergeRiskSummary(sourceListingId: string, targetListingId: string): string {
  return (
    `合併後將把 ${sourceListingId} 標記為 ${targetListingId} 的重複，並把其來源證據移轉至 ` +
    `${targetListingId}。${sourceListingId} 將不再獨立進入網絡評估，且此操作無法自助復原。`
  );
}

function buildSummaryRows(request: ListingMergeRequest): Array<{ key: string; value: string }> {
  return [
    {
      key: "來源（將標記為重複）",
      value: `${request.sourceListingId}${request.sourceLabel ? ` · ${request.sourceLabel}` : ""}`,
    },
    {
      key: "目標（保留並接收證據）",
      value: `${request.targetListingId}${request.targetLabel ? ` · ${request.targetLabel}` : ""}`,
    },
    { key: "寫入", value: "Listing Radar 狀態 + Audit（操作者、原因、風險摘要、correlation ID）" },
  ];
}
