import type { CandidateNativeWorkflowRead } from "./networkScoringTypes";
import styles from "../networkFindAreas.module.css";

/** Render only the shared, authorized native projection; no second state derivation. */
export function CandidateWorkflowFacts({ read }: { read?: CandidateNativeWorkflowRead }) {
  if (!read) return null;
  const candidateId = read.candidateId;
  const data = read?.dataReadiness, job = read?.scoreJob, report = read?.scoreReport, review = read?.reviewDecision;
  const unavailable = (state?: string) => state === "conflict" ? "讀取不一致" : "尚未核驗";
  const reviewAction = !review?.networkAction ? "候選點人工結果另行核驗"
    : read?.review?.reviewId === review.decisionId
      ? `${read.review.relation === "historical" ? "歷史" : ""}人工 ${review.networkAction}`
      : `紀錄動作 ${review.networkAction}（候選點關聯尚未核驗）`;
  const values = [
    ["data", "資料完整度", data?.availability === "recorded" ? data.passed ? "資料齊備" : "需補資料" : unavailable(data?.availability), data?.reason],
    ["job", "評分工作", job?.availability === "recorded"
      ? `${job.status} ${job.deliveryState ?? ""} · ${job.jobId} · 版本 ${job.version ?? "未提供"}`
      : job?.availability === "denied" ? "無權查看工作" : unavailable(job?.availability), job?.reason],
    ["report", "保存報告", report?.availability === "recorded" ? `${report.reportId} v${report.reportVersion} · ${report.sitescoreRunId} · ${report.modelVersion}`
      : report?.availability === "absent" ? "本次讀取無保存報告" : unavailable(report?.availability), report?.reason],
    ["review", "人工審核", review?.availability === "recorded"
      ? `${review.status} · ${reviewAction} · ${review.decisionId} v${review.decisionVersion} · 凍結報告 ${review.reportId} v${review.reportVersion}`
      : review?.availability === "absent" ? "本次讀取無審核紀錄" : unavailable(review?.availability), review?.reason],
  ];
  return <dl className={styles.candidateWorkflowFacts} aria-label="候選點流程紀錄" data-testid="candidate-workflow-facts" data-candidate-id={candidateId}>
    {values.map(([key, label, value, reason]) => <div key={key} data-testid={`candidate-state-${key}-${candidateId}`}>
      <dt>{label}</dt><dd title={reason}>{value}</dd>
    </div>)}
  </dl>;
}
