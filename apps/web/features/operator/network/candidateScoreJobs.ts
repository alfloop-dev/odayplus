import { hasScoreExecutionContext, hasScoreExecutionReceipt, readId, readObject } from "./networkReadValidation";
import type { NetworkScoringSnapshot, ScoreExecutionReceipt, ScoringCandidate } from "./networkScoringTypes";
import type { WorkflowRead } from "./networkWorkflowProjection";

export type CandidateScoreJobRead = {
  candidateId: string; listingId: string | null;
  availability: "recorded" | "unverified" | "unavailable" | "denied" | "conflict";
  jobId: string | null; executionRequestId: string | null;
  status: ScoreExecutionReceipt["status"] | null;
  deliveryState: "RETRYING" | "DEAD_LETTER" | null;
  version: number | null; attempt: number | null;
  reportId: string | null; reportVersion: number | null;
  reason: string;
};
export type CandidateScoreJobsSnapshot = { source: "canonical"; jobs: CandidateScoreJobRead[] };
export type CandidateScoreJobsRead = WorkflowRead<CandidateScoreJobsSnapshot>;
export const pendingScoreJob = (job: CandidateScoreJobRead) => job.availability === "recorded"
  && (["QUEUED", "RUNNING", "RETRYING"].includes(job.status ?? "") || job.deliveryState === "RETRYING");
const positive = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const initial = (candidate: ScoringCandidate): CandidateScoreJobRead => ({ candidateId: candidate.id,
  listingId: candidate.listingId ?? null, availability: "unverified", jobId: null, executionRequestId: null,
  status: null, deliveryState: null, version: null, attempt: null, reportId: null, reportVersion: null,
  reason: "尚無可核驗的工作身分；未將報告或資料資格當成工作成功。" });

/** Only existing authorized GETs. Source metadata discovers an id; it grants no job permission. */
export async function readCandidateScoreJobs(snapshot: NetworkScoringSnapshot, headers: HeadersInit,
  signal?: AbortSignal, transport: typeof fetch = fetch): Promise<CandidateScoreJobsSnapshot> {
  async function load(candidate: ScoringCandidate): Promise<CandidateScoreJobRead> {
    const job = initial(candidate), admission = candidate.currentAdmission, source = admission?.source;
    if (snapshot.candidates.filter((row) => row.id === candidate.id).length !== 1) {
      return { ...job, availability: "conflict", reason: "候選點工作身分重複。" };
    }
    if (!readId(candidate.listingId) || !admission || admission.candidateId !== candidate.id
      || admission.listingId !== candidate.listingId || source?.state !== "verified" || !readId(source.jobId)
      || !readId(source.intakeId) || !readId(source.promotionDecisionId)) {
      return { ...job, reason: "目前來源與工作父層身分尚未核驗；未猜測工作紀錄。" };
    }
    job.jobId = source.jobId;
    async function get(path: string) {
      const response = await transport(path, { headers, signal, cache: "no-store" });
      if (!response.ok) {
        job.availability = response.status === 401 || response.status === 403 ? "denied" : "unavailable";
        job.reason = `工作紀錄不可得（HTTP ${response.status}）；已保存報告另列。`;
        return null;
      }
      return response.json() as Promise<unknown>;
    }
    try {
      const receipt = await get(`/api/v1/jobs/${encodeURIComponent(source.jobId)}/receipt`);
      if (receipt === null) return job;
      if (!readObject(receipt) || receipt.job_id !== source.jobId || !positive(receipt.version)
        || !Number.isSafeInteger(receipt.attempt) || (receipt.attempt as number) < 0
        || !["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"].includes(String(receipt.status))
        || receipt.delivery_state != null && !["RETRYING", "DEAD_LETTER"].includes(String(receipt.delivery_state))) {
        return { ...job, availability: "conflict", reason: "原始工作回條與已核驗身分或結構不符。" };
      }
      Object.assign(job, { availability: "recorded", status: receipt.status, version: receipt.version,
        attempt: receipt.attempt, deliveryState: receipt.delivery_state ?? null, reason: "原始評分工作的授權回條。" });
      const reports = snapshot.scorecards.filter((card) => card.id === candidate.id);
      const report = reports[0];
      if (reports.length > 1) return { ...job, availability: "conflict", reason: "候選點報告身分重複。" };
      if (!report) return job.status === "SUCCEEDED"
        ? { ...job, availability: "conflict", reason: "工作已記錄成功，但目前讀取尚無對應報告；請重新讀取。" } : job;
      // A failed initial job cannot own a fresh execution; keep its actual failure visible.
      if (job.status !== "SUCCEEDED") return job;
      const context = await get(`/api/v1/operator/network-scoring/candidates/${encodeURIComponent(candidate.id)}/execution-context`);
      if (context === null) return job;
      const tenant = new Headers(headers).get("x-tenant-id");
      if (!hasScoreExecutionContext(context) || context.candidateSiteId !== candidate.id
        || tenant !== null && context.tenantId !== tenant || context.originalJobId !== source.jobId
        || context.reportId !== report.reportId || context.reportVersion !== report.reportVersion) {
        return { ...job, availability: "conflict", reason: "目前工作上下文與候選點、租戶或報告版本不符。" };
      }
      let execution = context.activeExecutionRequest;
      if (!execution && context.predecessorJobId !== source.jobId) {
        const value = await get(`/api/v1/jobs/${encodeURIComponent(context.predecessorJobId)}`);
        if (value === null) return job;
        if (!hasScoreExecutionReceipt(value) || value.jobId !== context.predecessorJobId) {
          return { ...job, availability: "conflict", reason: "已完成的再評分工作回條不符。" };
        }
        execution = value;
      }
      if (execution) {
        const identityMatches = execution.candidateSiteId === candidate.id && (execution.status === "SUCCEEDED"
          ? execution.result?.report_id === report.reportId && execution.result.report_version === report.reportVersion
            && execution.result.sitescore_run_id === report.sitescoreRunId && execution.result.model_version === report.modelVersion
          : execution.baseReportId === report.reportId && execution.baseReportVersion === report.reportVersion);
        if (!identityMatches || execution.jobVersion === null) {
          return { ...job, availability: "conflict", reason: "再評分回條與此候選點的確切報告不符。" };
        }
        Object.assign(job, { jobId: execution.jobId, executionRequestId: execution.executionRequestId,
          status: execution.status, version: execution.jobVersion, attempt: null, deliveryState: null,
          reportId: execution.result?.report_id ?? execution.baseReportId,
          reportVersion: execution.result?.report_version ?? execution.baseReportVersion, reason: "目前再評分工作的授權回條；保存報告另列。" });
      } else Object.assign(job, { reportId: context.reportId, reportVersion: context.reportVersion });
      return job;
    } catch {
      return { ...job, availability: "unavailable", reason: "工作紀錄讀取未完成；未推算工作成功或重試資格。" };
    }
  }
  return { source: "canonical", jobs: await Promise.all(snapshot.candidates.map(load)) };
}

export function candidateScoreJob(read: CandidateScoreJobsRead | undefined, generation: number,
  candidateId: string, listingId: string): CandidateScoreJobRead | null {
  if (!read || read.status !== "ready" || read.generation !== generation || read.snapshot?.source !== "canonical") return null;
  const jobs = read.snapshot.jobs.filter((job) => job.candidateId === candidateId);
  return jobs.length === 1 && jobs[0].listingId === listingId ? jobs[0] : null;
}
