import { hasCandidateReviewSubmission, hasNetworkListingsRenderShape, hasNetworkScoringRenderShape, readId, readObject } from "./networkReadValidation";
import { inspectReviewsReadProjection } from "./networkReviewProjection";
import { hasApprovedReviewHistory } from "./networkReviewRounds";
import { hasCurrentPendingSubmissionHistory } from "./networkPendingReviewHistory";
import type { NetworkReviewsSnapshot, ReviewItem } from "./networkReviewTypes";
import type { CandidateNativeWorkflowRead, CandidateNativeWorkflowReads, CandidateReviewReadTarget, NetworkScoringSnapshot } from "./networkScoringTypes";
import type { WorkflowRead } from "./networkWorkflowProjection";
import { candidateScoreJob, pendingScoreJob, type CandidateScoreJobsRead } from "./candidateScoreJobs";

type NetworkRead = { source?: string; candidates?: readonly unknown[]; listings?: readonly unknown[] };
type Input = {
  currentGeneration: number;
  tenantId?: string | null;
  network: WorkflowRead<NetworkRead>;
  scoring: WorkflowRead<NetworkScoringSnapshot>;
  reviews: WorkflowRead<NetworkReviewsSnapshot>;
  jobs?: CandidateScoreJobsRead;
};
const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const ready = (read: WorkflowRead<unknown>, generation: number) => ["ready", "empty"].includes(read.status)
  && read.generation === generation && read.snapshot !== null;
const messages = {
  read: "流程紀錄不可得：尚未完成目前存取上下文的讀取。",
  graph: "審核關聯尚未核驗；不以模型建議或通用 APPROVED 推算人工流程。",
  conflict: "候選點與人工審核紀錄不一致，尚無法確認流程狀態。",
};

/** Read only: joins the existing masked reads, including the complete strict
 * Decision/Approval graph. No extra endpoint, role, producer or write is added. */
export function projectCandidateNativeWorkflow(input: Input): CandidateNativeWorkflowReads {
  const output: Record<string, CandidateNativeWorkflowRead> = {};
  const scoring = input.scoring.snapshot;
  if (!scoring || !Array.isArray(scoring.candidates)) return output;
  const network = input.network.snapshot;
  const reviews = input.reviews.snapshot;
  const validReads = ready(input.network, input.currentGeneration) && ready(input.scoring, input.currentGeneration)
    && network?.source === "api" && hasNetworkListingsRenderShape(network) && hasNetworkScoringRenderShape(scoring) && scoring.source === "canonical";
  const validReviews = ready(input.reviews, input.currentGeneration) && reviews?.source === "canonical"
    && inspectReviewsReadProjection(reviews) !== "error";
  for (const candidate of scoring.candidates) {
    if (!readObject(candidate) || !readId(candidate.id)) continue;
    const result: CandidateNativeWorkflowRead = { candidateId: candidate.id, stage: null, availability: "unavailable",
      lifecycle: null, reason: messages.read, review: null };
    output[candidate.id] = result;
    if (!validReads || !network) continue;
    const candidates = network.candidates?.filter((row) => readObject(row) && row.id === candidate.id) ?? [];
    const raw = candidates[0];
    const listings = network.listings?.filter((row) => readObject(row) && row.id === candidate.listingId) ?? [];
    if (candidates.length !== 1 || !readObject(raw) || !readId(candidate.listingId)
      || raw.listingId !== candidate.listingId || listings.length !== 1
      || scoring.candidates.filter((row) => row.id === candidate.id).length !== 1) {
      Object.assign(result, { availability: "conflict", reason: messages.conflict }); continue;
    }
    result.lifecycle = typeof raw.canonicalSiteStatus === "string" ? raw.canonicalSiteStatus
      : typeof raw.status === "string" ? raw.status : null;
    result.scoreJob = candidateScoreJob(input.jobs, input.currentGeneration, candidate.id, candidate.listingId) ?? undefined;
    if (!validReviews || !reviews) { result.reason = messages.graph; continue; }
    const reports = scoring.scorecards.filter((row) => row.id === candidate.id);
    const latest = reports.length === 1 && readId(reports[0].reportId) && version(reports[0].reportVersion)
      && readId(reports[0].sitescoreRunId) && readId(reports[0].modelVersion) ? reports[0] : null;
    if (reports.length > 1 || reports.length === 1 && !latest
      || latest && scoring.scorecards.filter((row) => row.reportId === latest.reportId).length !== 1) {
      Object.assign(result, { availability: "conflict", reason: "最新報告身分尚未唯一核驗；保留候選點記錄，不推算流程。" }); continue;
    }
    const ownReviews = reviews.reviews.filter((row) => row.candidateId === candidate.id);
    const target = (row: ReviewItem): CandidateReviewReadTarget => ({ candidateId: candidate.id,
      listingId: candidate.listingId!, reviewId: row.id, reportId: row.reportId!, reportVersion: row.reportVersion!,
      latestReportId: latest?.reportId ?? null, latestReportVersion: latest?.reportVersion ?? null,
      generation: input.currentGeneration,
      relation: latest?.reportId === row.reportId && latest?.reportVersion === row.reportVersion ? "current" : "historical" });
    const persisted = (row: ReviewItem) => {
      const decision = reviews.decisions?.find((record) => record.decision_id === row.id);
      const approval = reviews.approvals?.find((record) => record.reviewId === row.id);
      return row.reportAvailability === "available" && decision && readId(decision.tenant_id)
        && (!input.tenantId || decision.tenant_id === input.tenantId)
        && (!readObject(listings[0]) || typeof listings[0].tenantId !== "string" || listings[0].tenantId === decision.tenant_id)
        && (latest === null || latest.reportId !== row.reportId || latest.reportVersion !== row.reportVersion
          || latest.modelVersion === row.modelVersion && latest.datasetSnapshotId === row.datasetSnapshotId)
        && approval?.recordSource === "persisted" && readId(approval.id) && approval.id !== row.id;
    };
    const submission = raw.reviewSubmission;
    if (submission !== undefined && submission !== null) {
      const row = hasCandidateReviewSubmission(submission) ? ownReviews.find((review) => review.id === submission.decisionId) : undefined;
      const decision = row && reviews.decisions?.find((record) => record.decision_id === row.id);
      const approval = row && reviews.approvals?.find((record) => record.reviewId === row.id);
      const newRound = decision && readId(decision.previous_decision_id) && readId(decision.tenant_id)
        && hasApprovedReviewHistory(reviews, candidate.id, decision.tenant_id, decision.previous_decision_id, decision.report_version);
      if (!hasCandidateReviewSubmission(submission) || !row || !decision || !readObject(decision) || !persisted(row)
        || row.canonicalDecisionStatus !== "PENDING_REVIEW" || row.status !== "pending"
        || !hasCurrentPendingSubmissionHistory(decision)
        || submission.decisionVersion !== decision.version || submission.tenantId !== decision.tenant_id
        || submission.approvalId !== decision.approval_id || submission.approvalId !== approval?.id
        || approval.approvalStatus !== "pending" || approval.status !== "pending"
        || submission.reportId !== row.reportId || submission.reportVersion !== row.reportVersion
        || submission.latestReportId !== latest?.reportId || submission.latestReportVersion !== latest?.reportVersion
        || submission.submittedBy !== decision.submitted_by || decision.submission_actor_verified !== true
        || submission.submissionSummary !== decision.submission_summary || submission.reviewerGroupId !== decision.reviewer_group_id
        || submission.originalCreatorSubjectId !== decision.created_by || submission.originalCreatorVerified !== decision.created_by_verified
        || raw.reviewId != null && raw.reviewId !== row.id || raw.reviewWorkflow != null
        || submission.previousDecisionId !== decision.previous_decision_id
          && (submission.previousDecisionId != null || decision.previous_decision_id != null)
        || ["approved", "opened", "rejected"].includes(String(result.lifecycle))
          && !(newRound && ["approved", "opened"].includes(String(result.lifecycle)))) {
        Object.assign(result, { availability: "conflict", reason: messages.conflict }); continue;
      }
      Object.assign(result, { availability: "recorded", stage: "pendingreview", review: target(row),
        reason: (newRound ? `原${result.lifecycle === "opened" ? "已開店" : "已核准"}狀態保留；新版報告本輪待審。` : "")
          + `已核對實際待審 Decision、獨立 Approval 與凍結報告 v${row.reportVersion}；最新報告 v${latest.reportVersion} 另行保存。` });
      continue;
    }
    const metadata = raw.reviewWorkflow;
    if (metadata !== undefined && metadata !== null) {
      const row = readObject(metadata) && readId(metadata.decisionId)
        ? ownReviews.find((review) => review.id === metadata.decisionId) : undefined;
      const decision = row && reviews.decisions?.find((record) => record.decision_id === row.id);
      const action = readObject(metadata) ? metadata.networkAction : null;
      const newRound = decision && readId(decision.previous_decision_id) && readId(decision.tenant_id)
        && hasApprovedReviewHistory(reviews, candidate.id, decision.tenant_id, decision.previous_decision_id, decision.report_version);
      const stage = action === "GO" ? "approved" : action === "WAIT" ? "onhold" : action === "RETURN" ? "needdata"
        : action === "REJECT" ? "rejected" : null;
      // The producer checks physical cand.review_id before emitting this nested
      // pointer. The legacy siteReviews namespace may mask the top-level copy.
      // A present top-level pointer must still agree; metadata alone is insufficient.
      const operational = ["approved", "opened"].includes(String(raw.canonicalSiteStatus));
      const lifecycleMatches = newRound && operational || (action === "GO" ? operational
        : action === "REJECT" ? raw.canonicalSiteStatus === "rejected"
          : !["approved", "opened", "rejected"].includes(String(raw.canonicalSiteStatus)));
      if (!readObject(metadata) || !row || !decision || !stage || !persisted(row)
        || !readId(raw.canonicalSiteStatus) || !lifecycleMatches
        || raw.reviewId != null && raw.reviewId !== row.id
        || !version(metadata.decisionVersion) || metadata.decisionVersion !== decision.version
        || metadata.tenantId !== decision.tenant_id || metadata.approvalId !== decision.approval_id
        || metadata.reportId !== row.reportId || metadata.reportVersion !== row.reportVersion
        || metadata.previousDecisionId !== decision.previous_decision_id
          && (metadata.previousDecisionId != null || decision.previous_decision_id != null)
        || metadata.networkAction !== decision.network_action || metadata.status !== row.status
        || raw.status !== (operational ? raw.canonicalSiteStatus : row.status)
        || metadata.conditions !== decision.conditions || !same(metadata.requiredData, decision.required_data)) {
        Object.assign(result, { availability: "conflict", reason: messages.conflict }); continue;
      }
      Object.assign(result, { availability: "recorded", stage, review: target(row),
        reason: `已核對人工 ${action} 決策與凍結報告 v${row.reportVersion}；候選點原始狀態 ${result.lifecycle}。`
          + (action === "RETURN" ? ` 需補資料：${decision.required_data!.join("、")}。` : "")
          + (action === "WAIT" ? ` 通過條件：${decision.conditions}。` : "") });
      continue;
    }
    const pending = ownReviews.filter((row) => row.canonicalDecisionStatus === "PENDING_REVIEW"
      && row.reportId === latest?.reportId && row.reportVersion === latest?.reportVersion);
    if (pending.length) {
      const row = pending[0];
      if (pending.length !== 1 || !persisted(row) || raw.reviewId != null && raw.reviewId !== row.id
        || ["approved", "opened", "rejected"].includes(String(result.lifecycle))) {
        Object.assign(result, { availability: "conflict", reason: messages.conflict }); continue;
      }
      Object.assign(result, { availability: "recorded", stage: "pendingreview", review: target(row),
        reason: "已核對唯一待審 Decision、獨立 Approval 與目前凍結報告；尚未寫入人工終局候選點指標。" }); continue;
    }
    if (ownReviews.length || raw.reviewId != null || ["approved", "opened", "rejected"].includes(String(result.lifecycle))) {
      result.reason = messages.graph; continue;
    }
    if (["SCORING_QUEUED", "SCORING_FAILED"].includes(String(result.lifecycle))) {
      if (result.scoreJob?.availability !== "recorded") {
        result.reason = `候選點記錄為 ${result.lifecycle}；${result.scoreJob?.reason ?? "目前工作回條尚未確認"}，已保存報告不代表當次成功。`; continue;
      }
    }
    if (result.scoreJob && pendingScoreJob(result.scoreJob)) {
      Object.assign(result, { availability: "recorded", stage: "scoring",
        reason: `評分工作 ${result.scoreJob.jobId}：${result.scoreJob.status}；保存報告與人工狀態另列。` }); continue;
    }
    if (result.scoreJob?.availability === "recorded" && ["FAILED", "DEAD_LETTER", "CANCELLED"].includes(result.scoreJob.status ?? "")) {
      result.reason = `評分工作 ${result.scoreJob.jobId}：${result.scoreJob.status}；保留候選點與歷史報告，不推算重試資格。`; continue;
    }
    // Actual _gate produces passed=true only with ready/warn and no required
    // missing fields. Supplementary otherMissing remains legal for warn.
    // This checks recorded Gate coherence, independently of model admission.
    if (!latest && candidate.gate.passed && (!["ready", "warn"].includes(candidate.gate.state)
      || candidate.gate.missing.length !== 0)) {
      Object.assign(result, { availability: "conflict", reason: "資料 Gate 的狀態、通過旗標與必要缺件不一致，尚未確認資料齊備。" }); continue;
    }
    Object.assign(result, { availability: "recorded", stage: latest ? "evaluated" : candidate.gate.passed ? "ready" : "needdata",
      reason: latest ? "已保存報告可查閱；未推算目前工作成功或人工核准。"
        : candidate.gate.passed ? "資料齊備；評分資格與工作受理仍需另行核驗。" : "目前資料完整度尚未通過。" });
  }
  return output;
}

/** An explicit URL is an identity request, never a fallback instruction. */
export function resolveCandidateReviewTarget(reads: CandidateNativeWorkflowReads, candidateId: string | null,
  selection: { id: string | null; invalid: boolean }): { target: CandidateReviewReadTarget | null; error: string | null } {
  if (!selection.id && !selection.invalid) return { target: null, error: null };
  const target = candidateId ? reads[candidateId]?.review : null;
  if (selection.invalid || !target || target.reviewId !== selection.id) {
    return { target: null, error: "指定審核連結尚無目前授權且一致的候選點、Decision、Approval 與凍結報告關聯；未改選其他審核。" };
  }
  return { target, error: null };
}
