"use client";

import { useId, useState } from "react";
import { useModalDialogBehavior } from "./useModalDialogBehavior";
import styles from "../networkFindAreas.module.css";
import type { ReviewSubmissionContext, ReviewSubmissionForm } from "./networkReviewSubmissionTypes";

export function ReviewSubmissionDialog({ candidateId, context, busy, error, correlationId, onClose, onSubmit }: {
  candidateId: string; context: ReviewSubmissionContext | null; busy: boolean;
  error: string | null; correlationId: string | null; onClose: () => void;
  onSubmit: (form: ReviewSubmissionForm) => void;
}) {
  const [summary, setSummary] = useState("");
  const [group, setGroup] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const id = useId();
  const close = () => { if (!busy) onClose(); };
  const panel = useModalDialogBehavior({ dismissible: !busy, onClose: close });
  function submit() {
    if (busy || !context?.canSubmit) return;
    if (!summary.trim()) { setLocalError("請填寫送審摘要。"); return; }
    if (!context.reviewerGroups.some((item) => item.id === group)) { setLocalError("請選擇審核群組。"); return; }
    setLocalError(null);
    onSubmit({ submissionSummary: summary.trim(), reviewerGroupId: group });
  }
  return <div className={styles.reviewDialogOverlay} data-testid="review-submission-dialog">
    <div className={styles.reviewDialog} ref={panel} role="dialog" aria-modal="true" aria-label={`送審 — ${candidateId}`} aria-busy={busy}>
      <div className={styles.reviewDialogHead}><div className={styles.reviewDialogTitle}>送審 · {candidateId}</div>
        <button type="button" aria-label="關閉送審" disabled={busy} onClick={close} className={styles.reviewDialogClose}>×</button></div>
      <div className={styles.reviewDialogBody}>
        {context ? <p data-testid="review-submission-report-target">
          本次凍結報告 {context.target.expectedReportId} v{context.target.expectedReportVersion}。<br />
          最新報告 {context.target.expectedLatestReportId} v{context.target.expectedLatestReportVersion}。
          {context.target.decisionId ? ` 保留同一 Decision ${context.target.decisionId}；送審不會將舊版決策套用到新版報告。` : " 建立這筆已保存報告的審核紀錄。"}
        </p> : <p role="status">正在確認送審資格與報告版本…</p>}
        <div className={styles.reviewDialogField}><label className={styles.reviewDialogFieldLabel} htmlFor={`${id}-summary`}>送審摘要（必填）</label>
          <textarea id={`${id}-summary`} data-autofocus data-testid="review-submission-summary" className={styles.reviewDialogInput}
            rows={3} readOnly={busy} value={summary} onChange={(event) => setSummary(event.target.value)} /></div>
        <div className={styles.reviewDialogField}><label className={styles.reviewDialogFieldLabel} htmlFor={`${id}-group`}>審核群組（必填）</label>
          <select id={`${id}-group`} data-testid="review-submission-group" className={styles.reviewDialogInput} disabled={busy || !context?.canSubmit}
            value={group} onChange={(event) => setGroup(event.target.value)}>
            <option value="">請選擇審核群組</option>{context?.reviewerGroups.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select></div>
        {context && !context.canSubmit ? <p>這筆 Decision 已在審核中；確認現有紀錄後即可查閱。</p> : null}
        {localError || error ? <p role="alert" className={styles.errorText}>{localError ?? error}</p> : null}
        {correlationId ? <p data-testid="review-submission-correlation">Correlation ID：{correlationId}</p> : null}
        <p className={styles.muted}>送審結果須重新核對正式 Decision 與 Approval 紀錄；人工核決由審核流程另行處理。</p>
      </div>
      <div className={styles.reviewDialogActions}>
        <button type="button" disabled={busy} onClick={close}>取消</button>
        <button type="button" data-testid="review-submission-submit" disabled={busy || !context?.canSubmit} onClick={submit}>
          {busy ? "確認送審中…" : "送出審核"}</button>
      </div>
    </div>
  </div>;
}
