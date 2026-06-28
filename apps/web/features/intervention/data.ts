import type { DataStatus, DecisionStatus, StatusTone } from "@oday-plus/domain-types";

export type InterventionStatus =
  | "TRIGGERED"
  | "ELIGIBILITY_CHECKED"
  | "ACTION_BUILT"
  | "CONFLICT_CHECKED"
  | "APPROVED"
  | "EXECUTED"
  | "OBSERVING"
  | "OUTCOME_COLLECTED"
  | "EFFECT_EVALUATED"
  | "CLOSED";

export type TimelineNode = {
  label: string;
  timestamp: string;
  actor: string;
  status: "done" | "active" | "blocked" | "pending";
  description: string;
  artifact: string;
};

export type InterventionCase = {
  id: string;
  store: string;
  alert: "ORANGE" | "RED";
  cause: string;
  action: string;
  status: InterventionStatus;
  decisionStatus: DecisionStatus;
  eligibility: string;
  conflict: string;
  approval: string;
  execution: string;
  observationWindow: string;
  outcome: string;
  evidenceLevel: "high" | "medium" | "low" | "immature";
  reasonRequired: string;
  decisionId: string;
  audit: {
    actor: string;
    timestamp: string;
    modelVersion: string;
    policyVersion: string;
    featureSnapshotTime: string;
    correlationId: string;
  };
  timeline: TimelineNode[];
};

export const freshness = {
  status: "FRESH" as DataStatus,
  updatedAt: "2026-06-28 09:30",
  modelVersion: "intervention-policy-v1.3.0",
  featureSnapshotTime: "2026-06-28T01:00:00Z",
  sourceSnapshotId: "snap-intervention-20260628-0100",
};

export const interventionCases: InterventionCase[] = [
  {
    id: "int-3001",
    store: "台北信義店",
    alert: "ORANGE",
    cause: "Revenue Residual · 晚餐交易低於 forecast band",
    action: "晚餐時段外送曝光加權 + 店內備餐節奏調整",
    status: "OBSERVING",
    decisionStatus: "OBSERVING",
    eligibility: "ELIGIBLE · feature snapshot fresh · store open 180d",
    conflict: "CLEARED · no overlapping price change",
    approval: "APPROVED · reason captured · risk acknowledged",
    execution: "EXECUTED · job job-int-3001",
    observationWindow: "2026-06-27 18:00 → 2026-07-04 18:00 · not mature",
    outcome: "Outcome not mature; UI must not claim effect.",
    evidenceLevel: "immature",
    reasonRequired: "核准、停止或提前關閉都需填寫 10 字以上 reason，提交後以後端 decision_id 為準。",
    decisionId: "dec-int-3001",
    audit: {
      actor: "ops_manager@oday.test",
      timestamp: "2026-06-27T17:45:00Z",
      modelVersion: "intervention-policy-v1.3.0",
      policyVersion: "ops-intervention-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-int-3001",
    },
    timeline: [
      node("Triggered", "done", "forecastops", "四燈 ORANGE 觸發干預候選。", "alert-4401"),
      node("Eligibility checked", "done", "policy-engine", "資格通過，資料新鮮度 FRESH。", "elig-3001"),
      node("Action built", "done", "intervention-builder", "建立外送曝光與備餐節奏組合。", "action-3001"),
      node("Conflict checked", "done", "conflict-engine", "未與調價或廣告活動衝突。", "conflict-3001"),
      node("Approved", "done", "ops_manager@oday.test", "人工核准完成，reason 已留痕。", "dec-int-3001"),
      node("Executed", "done", "job-runner", "執行完成並寫入 Label Registry。", "job-int-3001"),
      node("Observation started", "active", "intervention-workflow", "觀察窗進行中，尚未成熟。", "obs-3001"),
      node("Outcome collected", "pending", "causal-eval", "等待觀察窗成熟。", "outcome-3001"),
      node("Effect evaluated", "pending", "causal-eval", "尚不可宣稱效果。", "effect-3001"),
      node("Closed", "pending", "ops_manager", "等待 outcome maturity。", "close-3001"),
    ],
  },
  {
    id: "int-3002",
    store: "桃園中壢店",
    alert: "RED",
    cause: "Equipment · 出餐延遲與退款率同步升高",
    action: "停止促銷流量並建立設備檢修任務",
    status: "CONFLICT_CHECKED",
    decisionStatus: "PENDING_REVIEW",
    eligibility: "ELIGIBLE · incident linked",
    conflict: "BLOCKED · overlaps adlift-cmp-8802; resolve before execute",
    approval: "PENDING_REVIEW · cannot execute until conflict resolved",
    execution: "NOT_STARTED",
    observationWindow: "Not started",
    outcome: "No outcome; no causal claim.",
    evidenceLevel: "low",
    reasonRequired: "停止高風險動作必須填寫原因，並顯示後端 decision_id。",
    decisionId: "dec-int-3002-pending",
    audit: {
      actor: "system",
      timestamp: "2026-06-28T02:10:00Z",
      modelVersion: "root-cause-v0.9.4",
      policyVersion: "ops-intervention-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-int-3002",
    },
    timeline: [
      node("Triggered", "done", "forecastops", "四燈 RED 觸發。", "alert-4479"),
      node("Eligibility checked", "done", "policy-engine", "資格通過。", "elig-3002"),
      node("Action built", "done", "intervention-builder", "建立停止促銷與檢修動作。", "action-3002"),
      node("Conflict checked", "blocked", "conflict-engine", "與 adlift-cmp-8802 重疊。", "conflict-3002"),
      node("Approved", "pending", "ops_manager", "等待衝突解除與人工核准。", "dec-int-3002"),
      node("Executed", "pending", "job-runner", "尚未執行。", "job-int-3002"),
      node("Observation started", "pending", "intervention-workflow", "尚未開始。", "obs-3002"),
      node("Outcome collected", "pending", "causal-eval", "尚未收集。", "outcome-3002"),
      node("Effect evaluated", "pending", "causal-eval", "尚未評估。", "effect-3002"),
      node("Closed", "pending", "ops_manager", "尚未關閉。", "close-3002"),
    ],
  },
];

export const statusTone: Record<TimelineNode["status"], StatusTone> = {
  done: "green",
  active: "blue",
  blocked: "red",
  pending: "gray",
};

function node(
  label: string,
  status: TimelineNode["status"],
  actor: string,
  description: string,
  artifact: string,
): TimelineNode {
  return {
    label,
    status,
    actor,
    description,
    artifact,
    timestamp: status === "pending" ? "—" : "2026-06-28T02:00:00Z",
  };
}
