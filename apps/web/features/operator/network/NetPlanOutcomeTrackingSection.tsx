"use client";
import { Fragment } from "react";
import type { OutcomeTracking } from "./netplanBuilderClient";
import styles from "../networkFindAreas.module.css";

export function NetPlanOutcomeTrackingSection({ tracking }: { tracking?: OutcomeTracking }) {
  return <section className={styles.rebalanceNetPlanBlock} aria-label="90／180／365 日結果追蹤">
    <h4>90／180／365 日結果追蹤</h4>
    {!tracking || tracking.state !== "recorded" ? <p>尚無可核實的核准追蹤紀錄。</p> : <>
      <p>各期間從實際執行日起算日曆日；到期表示待回收資料，不代表已量測或成效成熟。</p>
      <ul>{tracking.tasks.map(task => <li key={task.task_id}><strong>{task.horizon_days} 日</strong>
        <dl><dt>追蹤識別</dt><dd>{task.task_id}</dd><dt>原核准識別</dt><dd>{task.approval_id}</dd>
          <dt>啟動狀態</dt><dd>{task.status === "PENDING_EXECUTION" ? "待實際執行" : "已依執行日建立追蹤期間"}</dd>
          <dt>實際執行時間</dt><dd>{task.executed_at ?? "尚未執行"}</dd>
          <dt>資料回收到期時間</dt><dd>{task.due_at ?? "待實際執行後起算"}</dd>
          {tracking.collectionProgress?.filter(progress => progress.task_id === task.task_id).map(progress => <Fragment key={progress.audit_event_id}>
            <dt>資料回收狀態</dt><dd>已到期，待資料來源；尚無此期間量測結果</dd>
            <dt>到期紀錄時間</dt><dd>{progress.due_observed_at}</dd><dt>到期稽核</dt><dd>{progress.audit_event_id}</dd>
          </Fragment>)}</dl></li>)}</ul>
      <h5>已保存的手動觀察標籤</h5>
      {tracking.labels.length === 0 ? <p>尚無已登錄的實際觀察標籤。</p> : tracking.labels.map(label => <dl key={label.label_id}>
        <dt>標籤識別</dt><dd>{label.label_id}</dd><dt>原結果識別</dt><dd>{label.outcome_id}</dd>
        <dt>已填報實際毛利</dt><dd>{label.actual_gross_margin}</dd><dt>資料狀態</dt><dd>OBSERVED · 手動填報，未宣告成熟或可用於訓練</dd></dl>)}
      <p>自動實現毛利資料來源與標籤成熟規則尚未接入；單次填報不會複製為三個期間的量測值。</p>
      <p>紀錄回執 {tracking.receipt.receipt_id} · 稽核 {tracking.receipt.audit_event_id} · 傳遞狀態 {tracking.receipt.delivery_status}</p>
    </>}
  </section>;
}
