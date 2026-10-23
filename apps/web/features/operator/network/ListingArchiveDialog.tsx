"use client";

import { useCallback, useState } from "react";
import styles from "../networkFindAreas.module.css";
import type { ListingApiError } from "./listingsClient";
import { useModalDialogBehavior } from "./useModalDialogBehavior";

export type ListingArchiveRequest = { listingId: string; label: string; idempotencyKey: string; ifMatch?: string };

/** Reuses the approved R7 decision confirmation surface; reason is never prefilled. */
export function ListingArchiveDialog({ request, busy, error, onClose, onSubmit }: {
  request: ListingArchiveRequest; busy?: boolean; error?: ListingApiError | null;
  onClose: () => void; onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const requestClose = useCallback(() => { if (!busy) onClose(); }, [busy, onClose]);
  const panelRef = useModalDialogBehavior({ dismissible: !busy, onClose: requestClose });
  return <div className={styles.reviewDialogOverlay} data-screen-label="Dialog 收件決策確認"
    data-testid="listing-archive-dialog" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
    <div aria-busy={busy ? "true" : undefined} aria-label={`封存 — ${request.listingId}`} aria-modal="true"
      className={styles.reviewDialog} ref={panelRef} role="dialog">
      <div className={styles.reviewDialogHead}>
        <div className={styles.reviewDialogTitle}>封存 · {request.listingId}</div>
        <button aria-label="關閉" className={styles.reviewDialogClose} data-testid="listing-archive-close"
          disabled={busy} onClick={requestClose} type="button">×</button>
      </div>
      <div className={styles.reviewDialogBody}>
        <div className={styles.reviewDialogSub}>{request.label}</div>
        <div className={styles.reviewDialogField}>
          <span className={styles.reviewDialogFieldLabel}>封存原因（必填 — 寫入 Audit）</span>
          <textarea className={styles.reviewDialogInput} data-autofocus data-testid="listing-archive-reason"
            disabled={busy} onChange={(event) => setReason(event.target.value)} rows={3} value={reason} />
        </div>
        <div className={styles.reviewSyncNote}>封存後保留既有來源證據與稽核紀錄。確認送出後才寫入。</div>
        {localError || error ? <div className={styles.errorText} role="alert">
          {localError ?? error?.summary}
          {error ? <span> · 錯誤碼 {error.code} · {error.occurredAt} · 下一步：{error.nextAction}
            {error.correlationId ? ` · correlation ${error.correlationId}` : ""}</span> : null}
        </div> : null}
      </div>
      <div className={styles.reviewDialogActions}>
        <button className={styles.reviewDialogCancel} data-testid="listing-archive-cancel" disabled={busy}
          onClick={requestClose} type="button">取消</button>
        <button className={styles.reviewDialogSubmit} data-testid="listing-archive-submit" disabled={busy}
          onClick={() => { if (busy) return; if (!reason.trim()) { setLocalError("請填寫封存原因。"); return; }
            setLocalError(null); onSubmit(reason.trim()); }} type="button">{busy ? "送出中…" : "確認封存"}</button>
      </div>
    </div>
  </div>;
}
