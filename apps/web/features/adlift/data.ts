import type { DataStatus, StatusTone } from "@oday-plus/domain-types";

export type AdLiftReport = {
  id: string;
  campaign: string;
  treatmentStores: string;
  controlStores: string;
  preTrendStatus: "PASS" | "FAILED" | "INSUFFICIENT_CONTROL";
  incrementalRevenue: string;
  incrementalGrossMargin: string;
  iromi: string;
  evidenceLevel: "high" | "medium" | "low" | "blocked";
  continueStopRecommendation: "CONTINUE" | "STOP" | "REVIEW_ONLY";
  contamination: string;
  claimGuard: string;
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
  updatedAt: "2026-06-28 09:38",
  modelVersion: "adlift-incrementality-v1.1.0",
  featureSnapshotTime: "2026-06-28T01:00:00Z",
  sourceSnapshotId: "snap-adlift-20260628-0100",
};

export const reports: AdLiftReport[] = [
  {
    id: "adlift-8801",
    campaign: "晚餐新客 CPA · 台北都會",
    treatmentStores: "24 stores",
    controlStores: "24 matched controls",
    preTrendStatus: "PASS",
    incrementalRevenue: "P50 NT$ 412k",
    incrementalGrossMargin: "P50 NT$ 128k",
    iromi: "1.74",
    evidenceLevel: "medium",
    continueStopRecommendation: "CONTINUE",
    contamination: "No overlapping intervention detected",
    claimGuard: "Matched control present; causal incrementality claim allowed with medium evidence.",
    decisionId: "dec-adlift-8801",
    audit: {
      modelVersion: "adlift-incrementality-v1.1.0",
      policyVersion: "adlift-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-adlift-8801",
    },
  },
  {
    id: "adlift-8802",
    campaign: "中壢午餐折扣 · overlapping ops incident",
    treatmentStores: "12 stores",
    controlStores: "0 valid controls",
    preTrendStatus: "INSUFFICIENT_CONTROL",
    incrementalRevenue: "Not claimable",
    incrementalGrossMargin: "Not claimable",
    iromi: "N/A",
    evidenceLevel: "blocked",
    continueStopRecommendation: "REVIEW_ONLY",
    contamination: "Contamination: overlaps intervention int-3002 and price-5102 window",
    claimGuard: "No matched control; must not claim causality.",
    decisionId: "dec-adlift-8802-review",
    audit: {
      modelVersion: "adlift-incrementality-v1.1.0",
      policyVersion: "adlift-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-adlift-8802",
    },
  },
  {
    id: "adlift-8803",
    campaign: "板橋宵夜搜尋曝光",
    treatmentStores: "16 stores",
    controlStores: "16 matched controls",
    preTrendStatus: "FAILED",
    incrementalRevenue: "Not claimable",
    incrementalGrossMargin: "Not claimable",
    iromi: "N/A",
    evidenceLevel: "low",
    continueStopRecommendation: "STOP",
    contamination: "No operational overlap, but pre-trend failed",
    claimGuard: "Pre-trend failed; show warning and stop recommendation.",
    decisionId: "dec-adlift-8803",
    audit: {
      modelVersion: "adlift-incrementality-v1.1.0",
      policyVersion: "adlift-policy-2026.06",
      featureSnapshotTime: "2026-06-28T01:00:00Z",
      correlationId: "corr-adlift-8803",
    },
  },
];

export const preTrendTone: Record<AdLiftReport["preTrendStatus"], StatusTone> = {
  PASS: "green",
  FAILED: "red",
  INSUFFICIENT_CONTROL: "orange",
};

export const recommendationTone: Record<AdLiftReport["continueStopRecommendation"], StatusTone> = {
  CONTINUE: "green",
  STOP: "red",
  REVIEW_ONLY: "orange",
};
