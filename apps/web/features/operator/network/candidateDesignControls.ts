import type { CandidateNativeWorkflowReads, CandidateReadField, CandidateWorkflowStage, ScoringCandidate } from "./networkScoringTypes";
import { pendingScoreJob } from "./candidateScoreJobs";

// R7 Package10: the binding standalone artifact (1aefb806...) and canonical
// source (cc4e6ae9...) share this original order. Supplemental reads do not
// extend the server's six data-completeness admission checks.
export const CANDIDATE_PIPELINE = [
  { id: "all", label: "全部" }, { id: "needdata", label: "缺資料" },
  { id: "ready", label: "可評分" }, { id: "scoring", label: "評分中" },
  { id: "evaluated", label: "已評分" }, { id: "shortlisted", label: "短名單" },
  { id: "pendingreview", label: "審核中" }, { id: "approved", label: "已核准" },
  { id: "rejected", label: "未通過" }, { id: "onhold", label: "暫停" },
] as const;
export type CandidatePipelineFilter = typeof CANDIDATE_PIPELINE[number]["id"] | "compare";

const PRODUCER_REASONS: Record<CandidateWorkflowStage, string> = {
  scoring: "評分工作狀態未接通；操作中的提示不能代替工作紀錄。",
  shortlisted: "短名單紀錄未接通；比較清單不代表短名單。",
  pendingreview: "審核工作狀態未接通；尚無可核驗的審核紀錄。",
  approved: "人工核准紀錄未接通；模型建議不代表核准。",
  rejected: "人工未通過紀錄未接通；模型建議不代表決策。",
  onhold: "暫停紀錄未接通；尚無可核驗的暫停狀態。",
};

function workflowMatch(candidate: ScoringCandidate, stage: CandidateWorkflowStage): boolean | null {
  const read = candidate.pipelineEvidence?.[stage];
  if (!read || read.source !== "canonical" || read.candidateId !== candidate.id
    || read.availability !== "recorded" || typeof read.matches !== "boolean"
    || typeof read.producer !== "string" || !read.producer.trim()
    || (read.matches && (typeof read.recordId !== "string" || !read.recordId.trim()
      || typeof read.status !== "string" || !read.status.trim()))) return null;
  return read.matches;
}

function workflowUnavailableReason(candidate: ScoringCandidate, stage: CandidateWorkflowStage) {
  const read = candidate.pipelineEvidence?.[stage];
  if (read?.availability === "denied") return "流程紀錄不可得：存取受限。";
  if (read && (read.candidateId !== candidate.id || read.availability === "conflict")) return "流程紀錄不一致，尚無法確認此候選點的狀態。";
  return read?.reason || PRODUCER_REASONS[stage];
}

export function candidateMatchesPipeline(candidate: ScoringCandidate, filter: CandidatePipelineFilter, native?: CandidateNativeWorkflowReads) {
  if (filter === "all") return true;
  if (filter === "compare") return candidate.inCompare;
  if (native) return filter === "scoring" ? Boolean(native[candidate.id]?.scoreJob && pendingScoreJob(native[candidate.id].scoreJob!))
    : native[candidate.id]?.availability === "recorded" && native[candidate.id].stage === filter;
  if (filter === "needdata") return !candidate.gate.passed;
  if (filter === "ready") return !candidate.scored && candidate.gate.passed;
  if (filter === "evaluated") return candidate.scored;
  return workflowMatch(candidate, filter) === true;
}

export function candidatePipelineControls(candidates: ScoringCandidate[], native?: CandidateNativeWorkflowReads) {
  return CANDIDATE_PIPELINE.map((stage) => {
    if (native) {
      const unsupported = stage.id === "shortlisted";
      const unknown = stage.id !== "all" && (unsupported || candidates.some((candidate) => stage.id === "scoring"
        ? native[candidate.id]?.scoreJob?.availability !== "recorded" : native[candidate.id]?.availability !== "recorded"));
      return { ...stage, count: unknown ? null : candidates.filter((candidate) => candidateMatchesPipeline(candidate, stage.id, native)).length,
        available: !unknown, reason: unsupported ? PRODUCER_REASONS[stage.id as CandidateWorkflowStage]
          : unknown ? [...new Set(candidates.map((candidate) => native[candidate.id]?.reason ?? "流程關聯尚未核驗。"))].join(" ")
            : stage.id === "ready" ? "資料齊備；評分資格仍需核驗模型、來源與特徵時點。" : null };
    }
    const unknown = stage.id in PRODUCER_REASONS
      && (candidates.length === 0 || candidates.some((candidate) => workflowMatch(candidate, stage.id as CandidateWorkflowStage) === null));
    return {
      ...stage,
      count: unknown ? null : candidates.filter((candidate) => candidateMatchesPipeline(candidate, stage.id)).length,
      available: !unknown,
      reason: unknown ? [...new Set(candidates.length ? candidates
        .filter((candidate) => workflowMatch(candidate, stage.id as CandidateWorkflowStage) === null)
        .map((candidate) => workflowUnavailableReason(candidate, stage.id as CandidateWorkflowStage))
        : [PRODUCER_REASONS[stage.id as CandidateWorkflowStage]])].join(" ")
        : stage.id === "ready" ? "資料齊備；評分資格仍需核驗模型、來源與特徵時點。" : null,
    };
  });
}

export type CandidateDisplayRow = {
  key: string;
  label: string;
  state: "ok" | "warn" | "fail" | "unknown" | "recorded";
  text: string;
  provenance: string;
};
const GATE_ROWS = [
  ["address", "地址定位"], ["rent", "租金"], ["area", "坪數"], ["floor", "樓層"],
  ["use", "用途"], ["geocode", "Geocode ≥ 0.80"], ["hardRule", "Hard rule"],
  ["sourceConfidence", "來源可信度"], ["visit", "現勘"], ["contact", "仲介／房東聯絡"], ["photos", "照片／附件"],
] as const;
const UNAVAILABLE: Record<string, string> = {
  use: "用途紀錄未接通；尚未取得可核驗的用途資料。",
  visit: "現勘紀錄未接通；尚未取得實際完成紀錄。",
  contact: "聯絡紀錄未接通；尚未取得實際聯絡紀錄。",
  photos: "照片／附件未接通；尚未取得可存取的附件清單。",
};

function formatField(field: CandidateReadField): string {
  if (field.availability === "denied") return "不可得：欄位存取受限";
  if (field.availability === "invalid") return "不可得：來源數值無效";
  if (field.availability !== "recorded" || field.value === null) return "不可得：尚未量測或未記錄";
  if (Array.isArray(field.value)) return field.value.length ? field.value.join("、") : "已記錄：無硬規則失敗項目";
  const value = typeof field.value === "number" ? field.value.toLocaleString("en-US", { maximumFractionDigits: 20 }) : field.value;
  return field.unit === "ping" ? `${value} 坪` : field.unit === "ratio" ? `${value}（比例）`
    : field.unit ? `${value} ${field.unit}` : value;
}

export function candidateGateDisplayRows(candidate: ScoringCandidate): CandidateDisplayRow[] {
  const evidence = candidate.readEvidence;
  const exact = evidence?.source === "canonical" && evidence.candidateId === candidate.id
    && typeof candidate.listingId === "string" && candidate.listingId.trim().length > 0
    && evidence.listingId === candidate.listingId;
  return GATE_ROWS.map(([key, label]) => {
    if (UNAVAILABLE[key]) return { key, label, state: "unknown", text: UNAVAILABLE[key], provenance: "資料來源尚未接通" };
    const field = exact ? evidence.fields?.[key as keyof typeof evidence.fields] : undefined;
    const fromAddress = key === "address" || key === "geocode";
    const related = field?.provenance && field.provenance.recordId === (fromAddress ? evidence!.addressId : evidence!.listingId)
      && field.provenance.recordType === (fromAddress ? "AddressLocation" : "Listing")
      && (fromAddress || field.provenance.snapshotId === evidence!.datasetSnapshotId);
    if (!field || !related) return { key, label, state: "unknown", text: "不可得：尚無對應候選點的來源紀錄", provenance: "尚無可核驗的來源" };
    const state = field.availability !== "recorded" || field.value === null ? "unknown"
      : key === "sourceConfidence" ? "recorded"
        : key === "hardRule" && Array.isArray(field.value) ? field.value.length ? "fail" : "ok"
          : candidate.gate.checks.find((check) => check.key === key)?.state ?? "recorded";
    const provenance = `來源 ${field.provenance.recordType} ${field.provenance.recordId} · ${field.provenance.fieldPath}`
      + (field.provenance.snapshotId ? ` · 快照 ${field.provenance.snapshotId}` : " · 快照未提供")
      + (field.reason ? ` · ${field.reason}` : "");
    return { key, label, state, text: formatField(field), provenance };
  });
}
