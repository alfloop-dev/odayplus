import type { StatusTone } from "@oday-plus/domain-types";

/**
 * DealRoomAVM view model. Vocabulary is authoritative to
 * modules/avm/domain/valuation.py; see docs/design/
 * ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md Part A. The frontend never invents
 * states — these mirror ValuationCaseStatus (7 values) and the report fields.
 */

export type ValuationCaseStatus =
  | "DRAFT"
  | "DATA_READY"
  | "NORMALIZING"
  | "VALUING"
  | "REVIEW_REQUIRED"
  | "APPROVED"
  | "DATAROOM_READY";

export type Confidence = "high" | "medium" | "low";

export type Lens = "income" | "asset" | "market" | "blended";

export type AvmRouteKey = "overview" | "cases" | "caseDetail";

export type SensitivePricePermission = "visible" | "masked";

export type PriceBand = {
  p10: number;
  p50: number;
  p90: number;
};

export type LensValuation = {
  lens: Lens;
  p10: number;
  p50: number;
  p90: number;
  method: string;
  evidence: string[];
};

export type NormalizedMargin = {
  gmTtm: number;
  gmFwd: number;
  normalizedGm: number;
  adjustmentReasons: string[];
  confidence: Confidence;
};

export type StatusTransition = {
  from: ValuationCaseStatus | "—";
  to: ValuationCaseStatus;
  actor: string;
  reason: string;
  at: string;
  correlationId: string;
};

export type FinanceApproval = {
  decisionId: string;
  actorId: string;
  approvedAt: string;
  decisionReason: string;
  reservePrice: number;
  reserveOverridden: boolean;
  policyVersion: string;
  correlationId: string;
};

export type DataRoomChecklistItem = {
  key: "financials" | "assets" | "lease" | "comparables" | "valuation_card";
  label: string;
  status: "ready" | "missing";
  note: string;
};

export type ExportAudit = {
  actor: string;
  reason: string;
  exportedAt: string;
  correlationId: string;
};

export type DataRoom = {
  dataroomId: string;
  completeness: number;
  checklist: DataRoomChecklistItem[];
  exportAudit: ExportAudit[];
};

export type ValuationCase = {
  caseId: string;
  storeId: string;
  status: ValuationCaseStatus;
  fairPrice: PriceBand;
  reservePrice: number;
  askingPrice: number;
  sensitivePricePermission: SensitivePricePermission;
  confidence: Confidence;
  liquidityScore: number;
  normalizedMargin: NormalizedMargin;
  lenses: LensValuation[];
  financeApproval: FinanceApproval | null;
  dataRoom: DataRoom | null;
  statusHistory: StatusTransition[];
  createdBy: string;
  modelVersion: string;
  featureVersion: string;
  policyVersion: string;
  predictionOriginTime: string;
  valuedAt: string;
  valuationVersion: string;
  correlationId: string;
};

export const AVM_MODEL_VERSION = "dealroom-avm-baseline-v1";
export const AVM_FEATURE_VERSION = "valuation-view-v1";
export const AVM_POLICY_VERSION = "avm-finance-approval-policy-v1";

export const freshness = {
  updatedAt: "2026-06-28 09:20",
  modelVersion: AVM_MODEL_VERSION,
  featureVersion: AVM_FEATURE_VERSION,
  sourceSnapshotId: "snap-avm-20260628-0100",
};

export const valuationCases: ValuationCase[] = [
  {
    caseId: "vc-5101",
    storeId: "store-021",
    status: "DATAROOM_READY",
    fairPrice: { p10: 18200, p50: 24600, p90: 31800 },
    reservePrice: 17654,
    askingPrice: 33390,
    sensitivePricePermission: "masked",
    confidence: "high",
    liquidityScore: 0.82,
    normalizedMargin: {
      gmTtm: 9200,
      gmFwd: 9850,
      normalizedGm: 9558,
      adjustmentReasons: ["quality_score 0.91 ≥ 0.8，無折讓", "forecast 權重 0.55"],
      confidence: "high",
    },
    lenses: [
      {
        lens: "income",
        p10: 19400,
        p50: 26760,
        p90: 33200,
        method: "normalized_gm × 2.8",
        evidence: ["normalized_gm 9558", "income multiple 2.8"],
      },
      {
        lens: "asset",
        p10: 16800,
        p50: 21400,
        p90: 26200,
        method: "book + equipment + working_capital − lease_liability",
        evidence: ["book 14200", "equipment 6800", "working_capital 2400", "lease_liability 2000"],
      },
      {
        lens: "market",
        p10: 18000,
        p50: 24200,
        p90: 31600,
        method: "normalized_gm × comparable_multiple × (1 − liquidity_discount)",
        evidence: ["comparable_multiple 2.9", "liquidity_discount 0.10"],
      },
      {
        lens: "blended",
        p10: 18200,
        p50: 24600,
        p90: 31800,
        method: "三鏡彙整為 fair_price 區間",
        evidence: ["income/asset/market 加權", "fair_price band p10/p50/p90"],
      },
    ],
    financeApproval: {
      decisionId: "dec-avm-5101",
      actorId: "finance-lead-02",
      approvedAt: "2026-06-28T03:40:00Z",
      decisionReason: "三鏡一致、流動性佳，採系統 reserve 核准。",
      reservePrice: 17654,
      reserveOverridden: false,
      policyVersion: AVM_POLICY_VERSION,
      correlationId: "corr-avm-5101",
    },
    dataRoom: {
      dataroomId: "dr-5101",
      completeness: 1,
      checklist: [
        { key: "financials", label: "Financials", status: "ready", note: "TTM 與預測毛利已備" },
        { key: "assets", label: "Assets", status: "ready", note: "資產與設備估值附證" },
        { key: "lease", label: "Lease", status: "ready", note: "租約負債與條件" },
        { key: "comparables", label: "Comparables", status: "ready", note: "可比門市倍數" },
        { key: "valuation_card", label: "Valuation card", status: "ready", note: "fair/reserve/asking 摘要" },
      ],
      exportAudit: [
        {
          actor: "finance-lead-02",
          reason: "交付財務委員會審閱",
          exportedAt: "2026-06-28T04:05:00Z",
          correlationId: "corr-avm-5101-exp1",
        },
      ],
    },
    statusHistory: [
      { from: "—", to: "DATA_READY", actor: "analyst-07", reason: "建立案件", at: "2026-06-28T02:00:00Z", correlationId: "corr-avm-5101-1" },
      { from: "DATA_READY", to: "VALUING", actor: "system/avm", reason: "正規化後估值", at: "2026-06-28T02:40:00Z", correlationId: "corr-avm-5101-2" },
      { from: "VALUING", to: "REVIEW_REQUIRED", actor: "system/avm", reason: "估值完成待核准", at: "2026-06-28T02:55:00Z", correlationId: "corr-avm-5101-3" },
      { from: "REVIEW_REQUIRED", to: "APPROVED", actor: "finance-lead-02", reason: "財務核准", at: "2026-06-28T03:40:00Z", correlationId: "corr-avm-5101-4" },
      { from: "APPROVED", to: "DATAROOM_READY", actor: "finance-lead-02", reason: "建立資料室", at: "2026-06-28T03:55:00Z", correlationId: "corr-avm-5101-5" },
    ],
    createdBy: "analyst-07",
    modelVersion: AVM_MODEL_VERSION,
    featureVersion: AVM_FEATURE_VERSION,
    policyVersion: AVM_POLICY_VERSION,
    predictionOriginTime: "2026-06-28T01:00:00Z",
    valuedAt: "2026-06-28T02:55:00Z",
    valuationVersion: "v3",
    correlationId: "corr-avm-5101",
  },
  {
    caseId: "vc-5102",
    storeId: "store-077",
    status: "REVIEW_REQUIRED",
    fairPrice: { p10: 12400, p50: 16900, p90: 22600 },
    reservePrice: 12028,
    askingPrice: 23730,
    sensitivePricePermission: "masked",
    confidence: "medium",
    liquidityScore: 0.64,
    normalizedMargin: {
      gmTtm: 6100,
      gmFwd: 6600,
      normalizedGm: 6375,
      adjustmentReasons: ["forecast 權重 0.55", "quality_score 0.82，無折讓"],
      confidence: "medium",
    },
    lenses: [
      {
        lens: "income",
        p10: 13200,
        p50: 17850,
        p90: 23800,
        method: "normalized_gm × 2.8",
        evidence: ["normalized_gm 6375", "income multiple 2.8"],
      },
      {
        lens: "asset",
        p10: 9800,
        p50: 12600,
        p90: 15400,
        method: "book + equipment + working_capital − lease_liability",
        evidence: ["book 9200", "equipment 3800", "working_capital 1600", "lease_liability 2000"],
      },
      {
        lens: "market",
        p10: 12000,
        p50: 16400,
        p90: 22000,
        method: "normalized_gm × comparable_multiple × (1 − liquidity_discount)",
        evidence: ["comparable_multiple 3.1", "liquidity_discount 0.18"],
      },
      {
        lens: "blended",
        p10: 12400,
        p50: 16900,
        p90: 22600,
        method: "三鏡彙整為 fair_price 區間",
        evidence: ["asset 鏡明顯偏低，分歧來自資產基礎薄", "income 與 market 接近"],
      },
    ],
    financeApproval: null,
    dataRoom: null,
    statusHistory: [
      { from: "—", to: "DATA_READY", actor: "analyst-04", reason: "建立案件", at: "2026-06-28T05:00:00Z", correlationId: "corr-avm-5102-1" },
      { from: "DATA_READY", to: "VALUING", actor: "system/avm", reason: "正規化後估值", at: "2026-06-28T05:30:00Z", correlationId: "corr-avm-5102-2" },
      { from: "VALUING", to: "REVIEW_REQUIRED", actor: "system/avm", reason: "估值完成待核准", at: "2026-06-28T05:45:00Z", correlationId: "corr-avm-5102-3" },
    ],
    createdBy: "analyst-04",
    modelVersion: AVM_MODEL_VERSION,
    featureVersion: AVM_FEATURE_VERSION,
    policyVersion: AVM_POLICY_VERSION,
    predictionOriginTime: "2026-06-28T01:00:00Z",
    valuedAt: "2026-06-28T05:45:00Z",
    valuationVersion: "v1",
    correlationId: "corr-avm-5102",
  },
  {
    caseId: "vc-5103",
    storeId: "store-145",
    status: "DATA_READY",
    fairPrice: { p10: 8200, p50: 11400, p90: 15800 },
    reservePrice: 7954,
    askingPrice: 16590,
    sensitivePricePermission: "masked",
    confidence: "low",
    liquidityScore: 0.41,
    normalizedMargin: {
      gmTtm: 3900,
      gmFwd: 4200,
      normalizedGm: 3744,
      adjustmentReasons: ["quality_score 0.71 < 0.8 → 正規化 ×0.92", "資料新鮮度偏舊"],
      confidence: "low",
    },
    lenses: [
      {
        lens: "income",
        p10: 8600,
        p50: 11600,
        p90: 15600,
        method: "normalized_gm × 2.8",
        evidence: ["normalized_gm 3744", "income multiple 2.8"],
      },
      {
        lens: "asset",
        p10: 6400,
        p50: 8800,
        p90: 11200,
        method: "book + equipment + working_capital − lease_liability",
        evidence: ["book 7400", "equipment 2200", "working_capital 900", "lease_liability 1700"],
      },
      {
        lens: "market",
        p10: 8000,
        p50: 11200,
        p90: 15400,
        method: "normalized_gm × comparable_multiple × (1 − liquidity_discount)",
        evidence: ["comparable_multiple 3.4", "liquidity_discount 0.28", "可比樣本少"],
      },
      {
        lens: "blended",
        p10: 8200,
        p50: 11400,
        p90: 15800,
        method: "三鏡彙整為 fair_price 區間",
        evidence: ["confidence=low，區間寬", "需補件後重估"],
      },
    ],
    financeApproval: null,
    dataRoom: null,
    statusHistory: [
      { from: "—", to: "DATA_READY", actor: "analyst-09", reason: "建立案件，待估值", at: "2026-06-28T06:10:00Z", correlationId: "corr-avm-5103-1" },
    ],
    createdBy: "analyst-09",
    modelVersion: AVM_MODEL_VERSION,
    featureVersion: AVM_FEATURE_VERSION,
    policyVersion: AVM_POLICY_VERSION,
    predictionOriginTime: "2026-06-28T01:00:00Z",
    valuedAt: "2026-06-28T06:10:00Z",
    valuationVersion: "v0",
    correlationId: "corr-avm-5103",
  },
];

export function caseStatusTone(status: ValuationCaseStatus): StatusTone {
  if (status === "DATAROOM_READY" || status === "APPROVED") return "green";
  if (status === "REVIEW_REQUIRED") return "blue";
  if (status === "VALUING" || status === "NORMALIZING") return "yellow";
  return "gray";
}

export function confidenceTone(confidence: Confidence): StatusTone {
  if (confidence === "high") return "green";
  if (confidence === "medium") return "yellow";
  return "orange";
}

export function financeApprovalLabel(c: ValuationCase): string {
  return c.financeApproval
    ? `已核准 · ${c.financeApproval.actorId} · ${c.financeApproval.approvedAt}`
    : "未核准";
}

export function dataRoomLabel(c: ValuationCase): string {
  if (!c.dataRoom) return "未建立";
  const exports = c.dataRoom.exportAudit.length;
  return exports > 0 ? `已匯出 ×${exports}` : "就緒";
}

export function selectedFromQuery(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
