import type { DataStatus, DecisionStatus, StatusTone } from "@oday-plus/domain-types";

export type Recommendation = "GO" | "WAIT" | "REJECT" | "INVESTIGATE";

export type ForecastInterval = {
  month: "M1" | "M3" | "M6" | "M12";
  p10: number;
  p50: number;
  p90: number;
};

export type ExpansionRouteKey =
  | "overview"
  | "heatzone"
  | "listings"
  | "candidates"
  | "sitescore"
  | "sitescoreDetail";

export type HeatZone = {
  id: string;
  district: string;
  h3: string;
  score: number;
  confidence: number;
  state:
    | "UNTOUCHED"
    | "PARTIALLY_ABSORBED"
    | "SATURATED"
    | "UNDER_REALIZED"
    | "STILL_EXPANDABLE"
    | "SUPPRESSED_LOW_CONFIDENCE";
  rank: number;
  listings: number;
  warnings: string[];
  reasons: string[];
  modelVersion: string;
  featureSnapshotTime: string;
};

export type Listing = {
  id: string;
  source: string;
  address: string;
  status: "RAW" | "PARSED" | "GEOCODED" | "DUPLICATE" | "FAILED_HARD_RULE" | "CANDIDATE";
  issue: string;
  rent: string;
  area: string;
  geocode: string;
  duplicate: string;
  heatZoneId: string;
  updatedAt: string;
  action: string;
};

export type CandidateSite = {
  id: string;
  address: string;
  status: "new" | "screened" | "scored" | "visited" | "rejected" | "approved" | "opened";
  heatZoneId: string;
  heatZoneScore: number;
  rentArea: string;
  geocode: string;
  feasibility: string;
  listingSource: string;
  siteScore: string;
  readiness: "ready" | "blocked";
  disabledReason?: string;
};

export type SiteScoreReport = {
  id: string;
  candidateId: string;
  address: string;
  targetFormat: string;
  recommendation: Recommendation;
  reason: string;
  decisionStatus: DecisionStatus;
  dataStatus: DataStatus;
  confidence: "high" | "medium" | "low";
  confidenceReasons: string[];
  payback: string;
  modelVersion: string;
  policyVersion: string;
  featureSnapshotTime: string;
  generatedAt: string;
  owner: string;
  sla: string;
  intervals: ForecastInterval[];
  positiveFactors: string[];
  negativeFactors: string[];
  comparables: string[];
  limitations: string[];
  audit: {
    decisionId: string;
    approvalId: string;
    actor: string;
    timestamp: string;
    correlationId: string;
  };
};

export const freshness = {
  status: "FRESH" as DataStatus,
  updatedAt: "2026-06-28 09:12",
  modelVersion: "hz-score-v2.1.0",
  featureSnapshotTime: "2026-06-28T01:00:00Z",
  sourceSnapshotId: "snap-expansion-20260628-0100",
};

export const heatZones: HeatZone[] = [
  {
    id: "hz-1049",
    district: "台北市信義區",
    h3: "8930e1d8b0fffff",
    score: 91,
    confidence: 0.86,
    state: "STILL_EXPANDABLE",
    rank: 1,
    listings: 8,
    warnings: ["租金接近政策上限", "同商圈 1 家成熟門市"],
    reasons: ["需求缺口高", "ODay G2 format fit 高", "步行人流與晚餐 POI 密度佳"],
    modelVersion: "hz-score-v2.1.0",
    featureSnapshotTime: "2026-06-28T01:00:00Z",
  },
  {
    id: "hz-0881",
    district: "新北市板橋區",
    h3: "8930e1d9157ffff",
    score: 84,
    confidence: 0.74,
    state: "UNDER_REALIZED",
    rank: 2,
    listings: 5,
    warnings: ["公車站點資料 PARTIAL"],
    reasons: ["既有門市覆蓋不足", "候選房源坪數集中於目標區間"],
    modelVersion: "hz-score-v2.1.0",
    featureSnapshotTime: "2026-06-28T01:00:00Z",
  },
  {
    id: "hz-0773",
    district: "桃園市中壢區",
    h3: "8930e36a237ffff",
    score: 69,
    confidence: 0.62,
    state: "SUPPRESSED_LOW_CONFIDENCE",
    rank: 3,
    listings: 3,
    warnings: ["geocode confidence 低於 0.7", "comparable sample size 低"],
    reasons: ["租金可行性佳", "晚間外送需求有缺口"],
    modelVersion: "hz-score-v2.1.0",
    featureSnapshotTime: "2026-06-28T01:00:00Z",
  },
];

export const listings: Listing[] = [
  {
    id: "lst-9001",
    source: "591 / A-8831",
    address: "信義路五段 22 號 1F",
    status: "GEOCODED",
    issue: "無阻擋；geocode 精度 rooftop",
    rent: "NT$ *** / 月",
    area: "34 坪",
    geocode: "0.94 / rooftop / 8930e1d8b0fffff",
    duplicate: "無高信心重複",
    heatZoneId: "hz-1049",
    updatedAt: "2026-06-28 08:44",
    action: "建立候選點",
  },
  {
    id: "lst-9002",
    source: "internal-broker / B-120",
    address: "縣民大道二段 91 號",
    status: "DUPLICATE",
    issue: "duplicate_group dg-77；地址標準化差異",
    rent: "NT$ *** / 月",
    area: "29 坪",
    geocode: "0.88 / parcel / 8930e1d9157ffff",
    duplicate: "dg-77 / address+phone / 0.91",
    heatZoneId: "hz-0881",
    updatedAt: "2026-06-28 08:20",
    action: "解重複",
  },
  {
    id: "lst-9003",
    source: "web-crawler / W-771",
    address: "中壢區中正路 188 號 B1",
    status: "FAILED_HARD_RULE",
    issue: "floor_not_allowed；B1 不符合格式政策",
    rent: "NT$ *** / 月",
    area: "18 坪",
    geocode: "0.64 / street / 8930e36a237ffff",
    duplicate: "無",
    heatZoneId: "hz-0773",
    updatedAt: "2026-06-28 07:58",
    action: "請求修正",
  },
];

export const candidates: CandidateSite[] = [
  {
    id: "cs-4107",
    address: "台北市信義區信義路五段 22 號 1F",
    status: "screened",
    heatZoneId: "hz-1049",
    heatZoneScore: 91,
    rentArea: "租金遮罩 / 34 坪 / frontage 8m",
    geocode: "0.94 rooftop",
    feasibility: "租金警示；坪數通過；1F 通過",
    listingSource: "591 / 2026-06-28 08:44",
    siteScore: "ssr-7001 / GO / PENDING_REVIEW",
    readiness: "ready",
  },
  {
    id: "cs-4108",
    address: "新北市板橋區縣民大道二段 91 號",
    status: "new",
    heatZoneId: "hz-0881",
    heatZoneScore: 84,
    rentArea: "租金遮罩 / 29 坪 / frontage 6m",
    geocode: "0.88 parcel",
    feasibility: "需補臨停照片",
    listingSource: "internal-broker / 2026-06-28 08:20",
    siteScore: "尚未執行",
    readiness: "ready",
  },
  {
    id: "cs-4109",
    address: "桃園市中壢區中正路 188 號 B1",
    status: "rejected",
    heatZoneId: "hz-0773",
    heatZoneScore: 69,
    rentArea: "租金遮罩 / 18 坪 / frontage unknown",
    geocode: "0.64 street",
    feasibility: "FAILED_HARD_RULE: floor_not_allowed",
    listingSource: "web-crawler / 2026-06-28 07:58",
    siteScore: "不可執行",
    readiness: "blocked",
    disabledReason: "缺 address_id、h3_res_9 信心不足，且樓層不符硬規則。",
  },
];

export const siteScoreReports: SiteScoreReport[] = [
  {
    id: "ssr-7001",
    candidateId: "cs-4107",
    address: "台北市信義區信義路五段 22 號 1F",
    targetFormat: "ODAY_G2",
    recommendation: "GO",
    reason: "需求缺口與格式 fit 高，租金雖接近上限但仍在政策容忍區間。",
    decisionStatus: "PENDING_REVIEW",
    dataStatus: "FRESH",
    confidence: "high",
    confidenceReasons: ["comparable stores 6", "feature snapshot fresh", "geocode rooftop"],
    payback: "P50 14.8 月；P10-P90 11.2-21.6 月",
    modelVersion: "sitescore-v1.4.2",
    policyVersion: "expansion-policy-2026.06",
    featureSnapshotTime: "2026-06-28T01:00:00Z",
    generatedAt: "2026-06-28T02:20:00Z",
    owner: "展店審查 A",
    sla: "2026-06-29 18:00",
    intervals: [
      { month: "M1", p10: 48, p50: 72, p90: 96 },
      { month: "M3", p10: 164, p50: 224, p90: 292 },
      { month: "M6", p10: 362, p50: 508, p90: 682 },
      { month: "M12", p10: 812, p50: 1148, p90: 1512 },
    ],
    positiveFactors: ["需求缺口 0.82", "晚餐 POI 高密度", "G2 format fit 0.89"],
    negativeFactors: ["租金接近上限", "一公里內既有門市 1 家"],
    comparables: ["store-021 / 420m / 成熟 / high band", "store-077 / 1.2km / G2 / medium band"],
    limitations: ["租金敏感欄位已遮罩", "需人工確認外帶動線"],
    audit: {
      decisionId: "dec-20260628-7001",
      approvalId: "apv-pending-7001",
      actor: "system/sitescore",
      timestamp: "2026-06-28T02:21:00Z",
      correlationId: "corr-exp-7001",
    },
  },
  {
    id: "ssr-7002",
    candidateId: "cs-4108",
    address: "新北市板橋區縣民大道二段 91 號",
    targetFormat: "ODAY_G1",
    recommendation: "INVESTIGATE",
    reason: "熱區分數高，但臨停與人流證據不足，需實勘補件。",
    decisionStatus: "SYSTEM_RECOMMENDED",
    dataStatus: "PARTIAL",
    confidence: "medium",
    confidenceReasons: ["traffic evidence partial", "geocode parcel", "rent inside policy"],
    payback: "P50 19.5 月；P10-P90 14.8-29.4 月",
    modelVersion: "sitescore-v1.4.2",
    policyVersion: "expansion-policy-2026.06",
    featureSnapshotTime: "2026-06-28T01:00:00Z",
    generatedAt: "2026-06-28T02:12:00Z",
    owner: "展店審查 B",
    sla: "2026-06-30 12:00",
    intervals: [
      { month: "M1", p10: 32, p50: 55, p90: 81 },
      { month: "M3", p10: 132, p50: 190, p90: 254 },
      { month: "M6", p10: 288, p50: 430, p90: 610 },
      { month: "M12", p10: 690, p50: 984, p90: 1280 },
    ],
    positiveFactors: ["租金可行", "商圈需求缺口穩定"],
    negativeFactors: ["臨停證據缺", "競品密度上升"],
    comparables: ["store-045 / 860m / G1 / medium band"],
    limitations: ["交通資料 PARTIAL", "需要補現場照片"],
    audit: {
      decisionId: "dec-20260628-7002",
      approvalId: "apv-draft-7002",
      actor: "system/sitescore",
      timestamp: "2026-06-28T02:13:00Z",
      correlationId: "corr-exp-7002",
    },
  },
];

export function recommendationTone(recommendation: Recommendation): StatusTone {
  if (recommendation === "GO") return "green";
  if (recommendation === "WAIT") return "yellow";
  if (recommendation === "INVESTIGATE") return "orange";
  return "red";
}

export function decisionTone(status: DecisionStatus): StatusTone {
  if (status === "APPROVED" || status === "EXECUTED" || status === "OUTCOME_READY") return "green";
  if (status === "PENDING_REVIEW" || status === "SYSTEM_RECOMMENDED") return "blue";
  if (status === "OVERRIDDEN") return "orange";
  if (status === "REJECTED") return "red";
  return "gray";
}

export function selectedFromQuery(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
