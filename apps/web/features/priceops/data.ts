import type { DataStatus, DecisionStatus, StatusTone } from "@oday-plus/domain-types";

export type PricePlan = {
  id: string;
  storeGroup: string;
  plan: string;
  lifecycleStatus: "OPTIMIZED" | "PENDING_APPROVAL" | "ACTIVE" | "OBSERVING" | "ROLLBACK";
  currentPrice: string;
  candidatePrice: string;
  priceChange: string;
  expectedDemand: string;
  expectedRevenue: string;
  expectedGrossMargin: string;
  risk: "low" | "medium" | "high";
  constraintStatus: "PASS" | "HARD_CONSTRAINT_FAILED" | "SOFT_WARNING";
  constraintDetail: string;
  rollbackPlan: string;
  rollbackTrigger: string;
  rollbackRecommended: boolean;
  approvalStatus: DecisionStatus;
  applyStatus: string;
  monitoringStatus: string;
  outcomeStatus: string;
  labelEntryId: string;
  publishJobId: string;
  evidenceLevel: "high" | "medium" | "low";
  reason: string;
  decisionId: string;
  audit: {
    modelVersion: string;
    policyVersion: string;
    featureSnapshotTime: string;
    correlationId: string;
  };
};

export const freshness = {
  status: "FRESH" as DataStatus,
  updatedAt: "2026-06-28 09:34",
  modelVersion: "price-optimizer-v2.2.0",
  featureSnapshotTime: "2026-06-28T01:00:00Z",
  sourceSnapshotId: "snap-priceops-20260628-0100",
};

export const pricePlans: PricePlan[] = [
  {
    id: "price-5101",
    storeGroup: "台北都會晚餐組",
    plan: "Dinner combo +3% candidate",
    lifecycleStatus: "PENDING_APPROVAL",
    currentPrice: "現行 NT$ 168 / 198 / 238",
    candidatePrice: "候選 NT$ 173 / 204 / 245",
    priceChange: "+3.0% weighted average",
    expectedDemand: "P50 -1.2% transactions",
    expectedRevenue: "P50 +2.1% revenue",
    expectedGrossMargin: "P50 +2.8% gross margin",
    risk: "medium",
    constraintStatus: "PASS",
    constraintDetail: "hard constraints pass; competitor gap within policy",
    rollbackPlan: "Rollback to price book pb-2026.06.20 within 30 minutes; monitor 48h.",
    rollbackTrigger: "trigger when gross margin drops more than 5% or sample < 30 after 14 days",
    rollbackRecommended: false,
    approvalStatus: "PENDING_REVIEW",
    applyStatus: "Waiting for manual approval; no publish job created",
    monitoringStatus: "Observation window opens after apply; stop conditions preloaded",
    outcomeStatus: "Outcome not mature",
    labelEntryId: "pricing-label-price-5101",
    publishJobId: "pending-approval",
    evidenceLevel: "medium",
    reason: "人工核准後才建立 price publish job；不自動執行。",
    decisionId: "dec-price-5101",
    audit: {
      modelVersion: "price-optimizer-v2.2.0",
      policyVersion: "pricing-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-price-5101",
    },
  },
  {
    id: "price-5102",
    storeGroup: "桃園中壢午餐組",
    plan: "Lunch hero item +9%",
    lifecycleStatus: "PENDING_APPROVAL",
    currentPrice: "現行 NT$ 129 / 149",
    candidatePrice: "候選 NT$ 141 / 163",
    priceChange: "+9.4% weighted average",
    expectedDemand: "P50 -5.9% transactions",
    expectedRevenue: "P50 +1.1% revenue",
    expectedGrossMargin: "P50 +1.4% gross margin",
    risk: "high",
    constraintStatus: "HARD_CONSTRAINT_FAILED",
    constraintDetail: "HARD_CONSTRAINT_FAILED: max_delta_pct 6%, competitor_gap > policy limit",
    rollbackPlan: "Rollback unavailable until candidate revised; blocked before publish.",
    rollbackTrigger: "not armed; hard constraints must pass before a rollback plan can be accepted",
    rollbackRecommended: false,
    approvalStatus: "PENDING_REVIEW",
    applyStatus: "Blocked before approval; publish disabled",
    monitoringStatus: "No pilot observation until candidate is revised",
    outcomeStatus: "No outcome; plan is blocked by hard constraints",
    labelEntryId: "not-registered",
    publishJobId: "blocked",
    evidenceLevel: "low",
    reason: "Hard constraint failures cannot be approved.",
    decisionId: "dec-price-5102-blocked",
    audit: {
      modelVersion: "price-optimizer-v2.2.0",
      policyVersion: "pricing-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-price-5102",
    },
  },
  {
    id: "price-5103",
    storeGroup: "新北板橋宵夜組",
    plan: "Late-night delivery fee -2%",
    lifecycleStatus: "ROLLBACK",
    currentPrice: "現行 fee NT$ 39",
    candidatePrice: "候選 fee NT$ 38",
    priceChange: "-2.6%",
    expectedDemand: "P50 +3.0% orders",
    expectedRevenue: "P50 +0.8% revenue",
    expectedGrossMargin: "P50 +1.0% gross margin",
    risk: "low",
    constraintStatus: "SOFT_WARNING",
    constraintDetail: "soft warning: narrow comparable sample",
    rollbackPlan: "Rollback fee schedule fs-2026.06.18; canary first 12h.",
    rollbackTrigger: "fired: realised gross margin -7.4% vs baseline after canary",
    rollbackRecommended: true,
    approvalStatus: "APPROVED",
    applyStatus: "Publish job price-publish-5103 succeeded at 2026-06-28 22:10",
    monitoringStatus: "Observation closed; 12h canary breached stop condition",
    outcomeStatus: "Rollback recommended and queued for fee schedule fs-2026.06.18",
    labelEntryId: "pricing-label-price-5103",
    publishJobId: "price-publish-5103",
    evidenceLevel: "medium",
    reason: "Approved with canary and rollback plan.",
    decisionId: "dec-price-5103",
    audit: {
      modelVersion: "price-optimizer-v2.2.0",
      policyVersion: "pricing-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-price-5103",
    },
  },
];

export const constraintTone: Record<PricePlan["constraintStatus"], StatusTone> = {
  PASS: "green",
  HARD_CONSTRAINT_FAILED: "red",
  SOFT_WARNING: "orange",
};

export const lifecycleTone: Record<PricePlan["lifecycleStatus"], StatusTone> = {
  OPTIMIZED: "purple",
  PENDING_APPROVAL: "orange",
  ACTIVE: "blue",
  OBSERVING: "blue",
  ROLLBACK: "red",
};
