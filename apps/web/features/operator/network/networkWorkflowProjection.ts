import type { ExpansionStep } from "./ExpansionStepper";
import { readId, readObject } from "./networkReadValidation";

// These are read identities, not new domain states or scoring/admission rules.
export type WorkflowRead<T> = {
  snapshot: T | null;
  status: string;
  generation: number | null;
};
type CandidateIdentity = { id: string; listingId?: string | null; heatZoneId?: string; status?: string; datasetSnapshotId?: string | null };
type ScoringIdentity = { id: string; listingId?: string | null };
type ReportIdentity = {
  id: string; reportId?: string; reportVersion?: number; sitescoreRunId?: string;
  modelVersion?: string; datasetSnapshotId?: string;
};
export type WorkflowNetworkSnapshot = {
  source?: string; candidates?: readonly CandidateIdentity[];
  listings?: readonly { id: string }[]; heatZones?: readonly { id: string }[];
};
export type WorkflowScoringSnapshot = {
  source?: string; candidates: readonly ScoringIdentity[];
  scorecards: readonly ReportIdentity[]; compareSet: readonly string[];
};
export type WorkflowReviewSnapshot = {
  source?: string;
  reviews: readonly {
    id: string; candidateId: string; reportId?: string; reportVersion?: number;
    canonicalDecisionStatus?: string;
  }[];
};
export type CommittedWorkflowReport = Required<Pick<ReportIdentity, "reportId" | "reportVersion" | "sitescoreRunId" | "modelVersion">> & {
  candidateId: string; datasetSnapshotId?: string;
};
export type CandidateWorkflowProjection = {
  steps: ExpansionStep[];
  notice: string;
  nextAction: string;
  reason: string;
  candidateId: string | null;
  listingId: string | null;
  report: CommittedWorkflowReport | null;
  reviewId: string | null;
  reviewReason: string;
  currentJob: "unknown";
  executionAdmission: "unknown";
};

const stepDefinitions = [
  ["find", "Find Area", 0], ["radar", "Listing Radar", 1], ["candidate", "Candidate", 2],
  ["sitescore", "SiteScore", 3], ["compare", "Compare", 4], ["review", "Review", 5],
] as const;
function neutralSteps(reason: string): ExpansionStep[] {
  return stepDefinitions.map(([id, label, tabIndex]) => ({
    id, label, tabIndex, state: "blocked", entityId: null, summary: reason,
    stateLabel: "未確認", navigationAvailable: true,
  }));
}
function readProblem<T extends { source?: string }>(read: WorkflowRead<T>, generation: number, name: string): string | null {
  if (read.status !== "ready" && read.status !== "empty") return `${name}_read_${read.status}`;
  if (read.generation !== generation) return `${name}_stale_generation`;
  if (!read.snapshot) return `${name}_empty`;
  if (read.snapshot.source !== "api" && read.snapshot.source !== "canonical") return `${name}_unverified_source`;
  return null;
}
const nonblank = readId;
const identityRows = (value: unknown, valid: (row: Record<string, unknown>) => boolean = () => true): boolean =>
  Array.isArray(value) && value.every((row) => readObject(row) && readId(row.id) && valid(row));
const optionalIdentityText = (value: unknown): boolean => value === undefined || value === null || typeof value === "string";
const candidateIdentity = (row: Record<string, unknown>): boolean => optionalIdentityText(row.listingId)
  && optionalIdentityText(row.heatZoneId) && optionalIdentityText(row.status) && optionalIdentityText(row.datasetSnapshotId);
const reportIdentity = (row: Record<string, unknown>): boolean => optionalIdentityText(row.reportId)
  && optionalIdentityText(row.sitescoreRunId) && optionalIdentityText(row.modelVersion) && optionalIdentityText(row.datasetSnapshotId)
  && (row.reportVersion === undefined || typeof row.reportVersion === "number" && Number.isFinite(row.reportVersion));
const reviewIdentity = (row: Record<string, unknown>): boolean => readId(row.candidateId)
  && optionalIdentityText(row.reportId) && optionalIdentityText(row.canonicalDecisionStatus)
  && (row.reportVersion === undefined || typeof row.reportVersion === "number" && Number.isFinite(row.reportVersion));
function readReasonLabel(reason: string): string {
  const resource = reason.startsWith("network_") ? "物件與候選點資料" : reason.startsWith("scoring_") ? "評分資料" : "人審資料";
  if (reason.endsWith("_read_loading")) return `${resource}仍在載入`;
  if (reason.endsWith("_read_error")) return `${resource}尚未成功讀取`;
  if (reason.endsWith("_stale_generation")) return `${resource}屬先前的存取上下文`;
  if (reason.endsWith("_empty")) return `目前授權的${resource}為空`;
  return `${resource}的來源或內容尚未確認`;
}

/** Joins only exact identities from current authorized reads; never infers a score, job or permission. */
export function projectCandidateWorkflow(input: {
  selection: { id: string | null; invalid: boolean };
  currentGeneration: number;
  network: WorkflowRead<WorkflowNetworkSnapshot>;
  scoring: WorkflowRead<WorkflowScoringSnapshot>;
  reviews: WorkflowRead<WorkflowReviewSnapshot>;
}): CandidateWorkflowProjection {
  const initialNotice = input.selection.invalid ? "候選點連結包含不一致或空白的選取值，請重新選取。" : "請選取候選點以查看對應物件與已保存報告；區域與物件頁籤仍可瀏覽。";
  const result: CandidateWorkflowProjection = {
    steps: neutralSteps(initialNotice), notice: initialNotice, nextAction: "請選取候選點以查看流程",
    reason: input.selection.invalid ? "invalid_selection" : "selection_required",
    candidateId: null, listingId: null, report: null, reviewId: null, reviewReason: "report_unavailable",
    currentJob: "unknown", executionAdmission: "unknown",
  };
  if (input.selection.invalid || !input.selection.id) return result;
  const unavailable = readProblem(input.network, input.currentGeneration, "network")
    ?? readProblem(input.scoring, input.currentGeneration, "scoring");
  if (unavailable) {
    return { ...result, reason: unavailable, notice: `${readReasonLabel(unavailable)}；指定候選點的流程進度尚未確認。`, nextAction: "重新讀取指定候選點資料" };
  }
  const id = input.selection.id;
  const network = input.network.snapshot!;
  const scoring = input.scoring.snapshot!;
  if (!identityRows(network.candidates, candidateIdentity) || !identityRows(network.listings)
    || network.heatZones !== undefined && !identityRows(network.heatZones)
    || !identityRows(scoring.candidates, candidateIdentity) || !identityRows(scoring.scorecards, reportIdentity)
    || !Array.isArray(scoring.compareSet) || !scoring.compareSet.every(readId)) {
    return { ...result, reason: "read_shape_unverified", notice: "指定候選點的讀取資料結構尚未確認，未推算流程進度。", nextAction: "重新讀取指定候選點資料" };
  }
  const candidateRows = (network.candidates ?? []).filter((row) => row.id === id);
  const scoringRows = scoring.candidates.filter((row) => row.id === id);
  if (candidateRows.length !== 1 || scoringRows.length !== 1) {
    return { ...result, reason: candidateRows.length > 1 || scoringRows.length > 1 ? "candidate_identity_conflict" : "candidate_missing",
      notice: "指定候選點目前不存在、不可存取或有重複身分；未改用其他候選點。", nextAction: "重新選取候選點" };
  }
  const candidate = candidateRows[0];
  const listingId = candidate.listingId;
  const listings = (network.listings ?? []).filter((row) => row.id === listingId);
  if (!nonblank(listingId) || listingId !== scoringRows[0].listingId || listings.length !== 1) {
    return { ...result, reason: listings.length > 1 ? "listing_identity_conflict" : "listing_relation_unverified",
      notice: "指定候選點與物件關聯尚未確認；未使用第一筆物件或候選點。", nextAction: "重新讀取候選點與物件關聯" };
  }
  result.candidateId = id;
  result.listingId = listingId;
  result.steps[1] = { ...result.steps[1], state: "completed", entityId: listingId, stateLabel: `已關聯 · ${listingId}`, summary: "指定候選點記錄與目前授權物件身分相符。" };
  result.steps[2] = { ...result.steps[2], state: "completed", entityId: id, stateLabel: `已選取 · ${id}`, summary: "已精確選取候選點；不代表資料 Gate、工作或人審通過。" };
  const zones = (Array.isArray(network.heatZones) ? network.heatZones : []).filter((zone) => zone.id === candidate.heatZoneId);
  if (nonblank(candidate.heatZoneId) && zones.length === 1) {
    result.steps[0] = { ...result.steps[0], state: "completed", entityId: candidate.heatZoneId, stateLabel: `已關聯 · ${candidate.heatZoneId}`, summary: "候選點記錄的區域與目前授權區域相符。" };
  } else {
    result.steps[0].summary = "此候選點尚無可核對的 HeatZone 關聯；不以目前瀏覽區域代替。";
  }
  const reports = scoring.scorecards.filter((card) => card.id === id);
  const report = reports[0];
  const validReport = reports.length === 1 && nonblank(report.reportId) && Number.isInteger(report.reportVersion)
    && (report.reportVersion ?? 0) > 0 && nonblank(report.sitescoreRunId) && nonblank(report.modelVersion);
  const duplicateReportIdentity = validReport && scoring.scorecards.filter((card) => card.reportId === report.reportId).length !== 1;
  if (!validReport || duplicateReportIdentity) {
    result.reason = reports.length > 1 || duplicateReportIdentity ? "report_identity_conflict" : reports.length ? "report_identity_incomplete" : "report_missing";
    result.notice = "此候選點的已保存報告尚未確認；目前工作與模型受理資格也未提供，不能推算應執行評分。";
    result.nextAction = "查閱指定候選點的資料與報告狀態";
    result.steps[3].entityId = id;
    result.steps[3].summary = result.notice;
    return result;
  }
  result.report = { candidateId: id, reportId: report.reportId!, reportVersion: report.reportVersion!,
    sitescoreRunId: report.sitescoreRunId!, modelVersion: report.modelVersion!, datasetSnapshotId: report.datasetSnapshotId };
  result.reason = candidate.status === "SCORING_FAILED" ? "failed_candidate_with_historical_report" : "committed_report_available";
  const snapshotDiffers = nonblank(candidate.datasetSnapshotId) && nonblank(report.datasetSnapshotId)
    && candidate.datasetSnapshotId !== report.datasetSnapshotId;
  result.notice = candidate.status === "SCORING_FAILED"
    ? "候選點目前記錄為評分失敗；先前已保存報告仍可查閱，不代表目前工作成功或可以重試。"
    : "已保存的 SiteScore 報告可查閱；目前工作狀態、模型受理與再評分資格尚未提供。";
  if (snapshotDiffers) result.notice += " 報告與候選點的資料快照不同，保留歷史報告，不推算目前資料已完成評分。";
  result.nextAction = "查閱已保存的 SiteScore 報告";
  result.steps[3] = { ...result.steps[3], state: "completed", entityId: id, stateLabel: `已保存報告 · ${id}`,
    summary: `已核對報告 ${report.reportId} v${report.reportVersion}，run ${report.sitescoreRunId}，模型 ${report.modelVersion}；不需要數值分數。` };
  const memberships = scoring.compareSet.filter((candidateId) => candidateId === id);
  result.steps[4] = { ...result.steps[4], entityId: id, state: memberships.length === 1 ? "completed" : "blocked",
    stateLabel: memberships.length === 1 ? "已加入比較" : memberships.length > 1 ? "比較關聯未確認" : "尚未加入比較",
    summary: memberships.length === 1 ? "目前授權比較集合包含此候選點；保留完整比較集合，不推算排名或推薦。" : "此候選點目前沒有唯一的比較集合關聯。" };
  const reviewProblem = readProblem(input.reviews, input.currentGeneration, "reviews")
    ?? (!identityRows(input.reviews.snapshot?.reviews, reviewIdentity) ? "reviews_shape_unverified" : null);
  result.reviewReason = reviewProblem ?? "review_missing";
  if (!reviewProblem) {
    const exact = input.reviews.snapshot!.reviews.filter((row) => row.candidateId === id
      && row.reportId === report.reportId && row.reportVersion === report.reportVersion);
    if (exact.length > 1) result.reviewReason = "review_identity_conflict";
    else if (exact.length === 1) {
      const review = exact[0];
      const status = review.canonicalDecisionStatus;
      if (["DRAFT", "SYSTEM_RECOMMENDED", "PENDING_REVIEW", "APPROVED", "REJECTED"].includes(status ?? "")) {
        result.reviewId = review.id;
        result.reviewReason = "exact_report_decision_available";
        result.steps[5] = { ...result.steps[5], entityId: review.id,
          state: status === "APPROVED" || status === "REJECTED" ? "completed" : status === "PENDING_REVIEW" ? "current" : "blocked",
          stateLabel: status === "APPROVED" ? "此報告已核准" : status === "REJECTED" ? "此報告已駁回" : `此報告決策：${status}`,
          summary: `人審 ${review.id} 對應目前報告 ${report.reportId} v${report.reportVersion}，記錄狀態 ${status}；不代表其他版本或模型受理資格。` };
      } else result.reviewReason = "review_status_unverified";
    } else if (input.reviews.snapshot!.reviews.some((row) => row.candidateId === id)) result.reviewReason = "review_report_mismatch";
  }
  if (!result.reviewId) result.steps[5].summary = "此報告尚無可唯一核對的當代人審關聯；歷史人審仍可查閱。";
  return result;
}
