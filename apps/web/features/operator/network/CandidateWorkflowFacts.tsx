import type { CandidateWorkflowProjection } from "./networkWorkflowProjection";
import styles from "../networkFindAreas.module.css";

/** Four independent observations. A saved report never overwrites a failed or denied job read. */
export function CandidateWorkflowFacts({ value }: { value: CandidateWorkflowProjection }) {
  if (!value.candidateId) return null;
  const job = value.currentJob;
  const availability = { unverified: "未核驗", unavailable: "未取得", denied: "不可讀", conflict: "紀錄不一致", recorded: "已記錄" };
  return <dl className={styles.candidateWorkflowFacts} aria-label="候選點流程紀錄" data-testid="candidate-workflow-facts">
    <div><dt>資料完整度</dt><dd data-testid="workflow-data-readiness">{value.dataReadiness
      ? `${value.dataReadiness.state} · ${value.dataReadiness.okCount}/${value.dataReadiness.totalCount}` : "尚未確認"}</dd></div>
    <div><dt>評分工作</dt><dd data-testid="workflow-score-job">{job?.availability === "recorded"
      ? `${job.jobId} · ${job.status}${job.deliveryState ? ` / ${job.deliveryState}` : ""} · v${job.version}`
      : job ? `${availability[job.availability]} · ${job.reason}` : "尚未確認目前授權的工作紀錄"}</dd></div>
    <div><dt>保存報告</dt><dd data-testid="workflow-score-report">{value.report
      ? `${value.report.reportId} v${value.report.reportVersion} · ${value.report.sitescoreRunId} · ${value.report.modelVersion}` : "尚無可核對的保存報告"}</dd></div>
    <div><dt>本報告人審</dt><dd data-testid="workflow-review-decision">{value.reviewDecision
      ? `${value.reviewDecision.id} · ${value.reviewDecision.status} · ${value.reviewDecision.reportId} v${value.reviewDecision.reportVersion}` : "尚無可核對的當代人審紀錄"}</dd></div>
  </dl>;
}
