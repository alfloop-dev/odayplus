/**
 * Growth workspace (營收成長) view model.
 *
 * Dual-mode: runtime API client + embedded fixture fallback.
 *
 * Read path  — fetchGrowthViewModel({ segmentId, itemId, draftId })
 *   1. Calls /api/v1/operator/growth/{freshness,segments,recommendations,actions}
 *      with Idempotency-Key and X-Correlation-Id.
 *   2. On any network/parse error, silently falls back to embedded fixtures so
 *      the workspace never breaks.
 *
 * Write path — apiClient.createGrowthDraft() / apiClient.writeGrowthOutcome()
 *   • POST /api/v1/operator/growth/actions           (create draft)
 *   • POST /api/v1/operator/growth/actions/{id}/outcome  (effectiveness writeback)
 *   Both carry Idempotency-Key + X-Correlation-Id headers and are idempotent.
 *
 * Synchronous buildGrowthViewModel() still works for SSR/testing contexts that
 * pass pre-fetched data directly.
 *
 * Fixture entities (task ODP-OC-FE-03, wire-up ODP-FIN-FE-001):
 *   - SEGMENTS                 分群 (GrowthSegment)
 *   - PRICEOPS_RECOMMENDATIONS PriceOps 建議 (PriceOpsRecommendation)
 *   - GROWTH_ITEMS             Growth Actions (GrowthItem)
 *
 * State vocabularies stay in the canonical English codes (domain-types §status);
 * the effectiveness gate below encodes the product rule that an ineffective
 * campaign must not be closed directly.
 */
import type {
  Confidence,
  DataStatus,
  DecisionStatus,
  StatusTone,
} from "@oday-plus/domain-types";
import { operatorSecurityHeaders } from "./operatorSecurityHeaders";

export type ConfidenceLevel = Confidence["level"];

/** The three canonical create-entry draft types (package 6 entry cards). */
export type GrowthKind = "offpeak" | "winback" | "priceops";

/**
 * Growth lifecycle status.  Extends the canonical DecisionStatus with the
 * package 6 R4 states that the Growth workspace adds on top of the shared
 * decision vocabulary.
 */
export type GrowthStatus =
  | DecisionStatus
  | "PENDING_APPROVAL"
  | "SCHEDULED"
  | "RUNNING"
  | "INEFFECTIVE";

/** 分群 — a store/customer segment used to target growth actions. */
export type GrowthSegment = {
  id: string;
  name: string;
  /** human-readable membership rule for the segment. */
  definition: string;
  storeCount: number;
  /** share of total revenue this segment represents. */
  revenueShare: string;
  trend: "up" | "flat" | "down";
  /** one-line growth opportunity for scan-reading the table. */
  opportunity: string;
  dataStatus: DataStatus;
};

/** PriceOps 建議 — a pricing recommendation that can seed a growth draft. */
export type PriceOpsRecommendation = {
  id: string;
  segmentId: string;
  title: string;
  currentPrice: string;
  candidatePrice: string;
  /** expected P50 revenue lift, percent. */
  expectedRevenueLift: number;
  /** expected P50 gross-margin lift, percent. */
  expectedMarginLift: number;
  constraintStatus: "PASS" | "SOFT_WARNING" | "HARD_CONSTRAINT_FAILED";
  constraintDetail: string;
  confidence: ConfidenceLevel;
  /** PriceOps only ever hands over a recommendation, never an approved decision. */
  decisionStatus: Extract<DecisionStatus, "SYSTEM_RECOMMENDED" | "DRAFT">;
};

/** Effectiveness verdict for an observed growth action. */
export type GrowthOutcome =
  | "PENDING" // observation window not matured yet
  | "EFFECTIVE" // met target lift with sufficient evidence
  | "INEFFECTIVE" // non-positive lift — cannot be closed directly
  | "INCONCLUSIVE"; // positive but under target, or evidence too weak

/** Growth Action — a campaign/action with an intervention-style lifecycle. */
export type GrowthItem = {
  id: string;
  name: string;
  /** draft type / entry-card kind this action was created from. */
  kind?: GrowthKind;
  segmentId: string;
  /** PriceOps recommendation that seeded this draft, if any. */
  sourceRecommendationId?: string;
  objective: string;
  status: GrowthStatus;
  observationWindow: string;
  /** target lift the action was approved against, percent. */
  targetLift: number;
  /** observed lift once the window matures; null while still observing. */
  observedLift: number | null;
  evidenceLevel: ConfidenceLevel;
  rationale: string;
  rollbackPlan: string;
  audit: {
    decisionId: string;
    correlationId: string;
    modelVersion: string;
    policyVersion: string;
    featureSnapshotTime: string;
  };
};

/** Freshness / model-version metadata returned by /growth/freshness. */
export type GrowthFreshness = {
  status: DataStatus;
  updatedAt: string;
  modelVersion: string;
  policyVersion: string;
  featureSnapshotTime: string;
  sourceSnapshotId: string;
};

// ---------------------------------------------------------------------------
// Embedded fixture data (fallback when API is unreachable)
// ---------------------------------------------------------------------------

export const FIXTURE_FRESHNESS: GrowthFreshness = {
  status: "FRESH" as DataStatus,
  updatedAt: "2026-07-09 14:20",
  modelVersion: "growth-uplift-v1.4.0",
  policyVersion: "growth-policy-2026.07",
  featureSnapshotTime: "2026-07-09T06:00:00Z",
  sourceSnapshotId: "snap-growth-20260709-0600",
};

/**
 * @deprecated Use FIXTURE_FRESHNESS. The bare `freshness` export is kept for
 * existing imports that reference it directly.
 */
export const freshness = FIXTURE_FRESHNESS;

export const SEGMENTS: GrowthSegment[] = [
  {
    id: "seg-metro-dinner",
    name: "都會晚餐高潛力組",
    definition: "六都 · 晚餐時段營收占比 > 45% · 近 8 週交易量成長",
    storeCount: 42,
    revenueShare: "31.4%",
    trend: "up",
    opportunity: "晚餐客單價仍低於同商圈基準，具備定價上調空間。",
    dataStatus: "FRESH",
  },
  {
    id: "seg-suburb-lunch",
    name: "郊區午餐守成組",
    definition: "非六都 · 午餐時段營收占比 > 50% · 交易量持平",
    storeCount: 28,
    revenueShare: "18.7%",
    trend: "flat",
    opportunity: "午餐主力商品需求彈性高，調價風險大，優先觀察。",
    dataStatus: "FRESH",
  },
  {
    id: "seg-latenight-delivery",
    name: "宵夜外送流失組",
    definition: "外送占比 > 60% · 近 12 週宵夜時段營收下滑",
    storeCount: 17,
    revenueShare: "9.2%",
    trend: "down",
    opportunity: "外送費結構偏高，可測試小幅下調搭配廣告增量。",
    dataStatus: "LOW_CONFIDENCE",
  },
];

export const PRICEOPS_RECOMMENDATIONS: PriceOpsRecommendation[] = [
  {
    id: "rec-9001",
    segmentId: "seg-metro-dinner",
    title: "晚餐套餐 +3% 加權調價",
    currentPrice: "現行 NT$ 168 / 198 / 238",
    candidatePrice: "候選 NT$ 173 / 204 / 245",
    expectedRevenueLift: 2.1,
    expectedMarginLift: 2.8,
    constraintStatus: "PASS",
    constraintDetail: "硬限制通過；競品價差在政策範圍內。",
    confidence: "medium",
    decisionStatus: "SYSTEM_RECOMMENDED",
  },
  {
    id: "rec-9002",
    segmentId: "seg-latenight-delivery",
    title: "宵夜外送費 -2% 試點",
    currentPrice: "現行外送費 NT$ 39",
    candidatePrice: "候選外送費 NT$ 38",
    expectedRevenueLift: 0.8,
    expectedMarginLift: 1.0,
    constraintStatus: "SOFT_WARNING",
    constraintDetail: "軟警告：可比樣本偏少，建議搭配對照組。",
    confidence: "low",
    decisionStatus: "SYSTEM_RECOMMENDED",
  },
  {
    id: "rec-9003",
    segmentId: "seg-suburb-lunch",
    title: "午餐主力商品 +9% 調價",
    currentPrice: "現行 NT$ 129 / 149",
    candidatePrice: "候選 NT$ 141 / 163",
    expectedRevenueLift: 1.1,
    expectedMarginLift: 1.4,
    constraintStatus: "HARD_CONSTRAINT_FAILED",
    constraintDetail: "HARD_CONSTRAINT_FAILED：max_delta_pct 6%、競品價差超出政策上限。",
    confidence: "low",
    decisionStatus: "SYSTEM_RECOMMENDED",
  },
];

export const GROWTH_ITEMS: GrowthItem[] = [
  {
    id: "growth-7001",
    name: "都會晚餐套餐調價活動",
    segmentId: "seg-metro-dinner",
    sourceRecommendationId: "rec-9001",
    objective: "晚餐時段營收 P50 +2.0%，毛利不低於現況。",
    status: "OUTCOME_READY",
    observationWindow: "2026-06-20 ~ 2026-07-04（14 天）",
    targetLift: 2.0,
    observedLift: 2.6,
    evidenceLevel: "high",
    rationale: "對照組配對通過 pre-trend 檢定；調價後晚餐營收顯著高於基準。",
    rollbackPlan: "回復價目表 pb-2026.06.19，30 分鐘內生效，觀察 48 小時。",
    audit: {
      decisionId: "dec-growth-7001",
      correlationId: "corr-growth-7001",
      modelVersion: "growth-uplift-v1.4.0",
      policyVersion: "growth-policy-2026.07",
      featureSnapshotTime: "2026-07-04T06:00:00Z",
    },
  },
  {
    id: "growth-7002",
    name: "宵夜外送費試點活動",
    segmentId: "seg-latenight-delivery",
    sourceRecommendationId: "rec-9002",
    objective: "宵夜外送訂單量 P50 +3.0%，營收不低於現況。",
    status: "OUTCOME_READY",
    observationWindow: "2026-06-18 ~ 2026-07-02（14 天）",
    targetLift: 3.0,
    observedLift: -1.4,
    evidenceLevel: "medium",
    rationale: "下調外送費後訂單量未提升，宵夜營收較基準下滑。",
    rollbackPlan: "回復外送費結構 fs-2026.06.17，先 canary 12 小時再全量。",
    audit: {
      decisionId: "dec-growth-7002",
      correlationId: "corr-growth-7002",
      modelVersion: "growth-uplift-v1.4.0",
      policyVersion: "growth-policy-2026.07",
      featureSnapshotTime: "2026-07-02T06:00:00Z",
    },
  },
  {
    id: "growth-7003",
    name: "郊區午餐加價包觀察活動",
    segmentId: "seg-suburb-lunch",
    objective: "午餐加價包滲透率 P50 +1.5%，需求彈性可控。",
    status: "OUTCOME_READY",
    observationWindow: "2026-06-25 ~ 2026-07-09（14 天）",
    targetLift: 1.5,
    observedLift: 0.6,
    evidenceLevel: "low",
    rationale: "觀察期營收微幅上升但未達標，對照組樣本不足以判定因果。",
    rollbackPlan: "回復加價包設定 cfg-2026.06.24；維持既有午餐主力價。",
    audit: {
      decisionId: "dec-growth-7003",
      correlationId: "corr-growth-7003",
      modelVersion: "growth-uplift-v1.4.0",
      policyVersion: "growth-policy-2026.07",
      featureSnapshotTime: "2026-07-09T06:00:00Z",
    },
  },
  {
    id: "growth-7004",
    name: "都會晚餐加點推薦活動",
    segmentId: "seg-metro-dinner",
    objective: "晚餐加點附加營收 P50 +2.5%。",
    status: "OBSERVING",
    observationWindow: "2026-07-05 ~ 2026-07-19（觀察中）",
    targetLift: 2.5,
    observedLift: null,
    evidenceLevel: "medium",
    rationale: "活動執行中，觀察窗尚未成熟，暫無成效判定。",
    rollbackPlan: "停用加點推薦模組設定 cfg-2026.07.05。",
    audit: {
      decisionId: "dec-growth-7004",
      correlationId: "corr-growth-7004",
      modelVersion: "growth-uplift-v1.4.0",
      policyVersion: "growth-policy-2026.07",
      featureSnapshotTime: "2026-07-09T06:00:00Z",
    },
  },
  {
    id: "growth-7005",
    name: "宵夜外送廣告增量草稿",
    segmentId: "seg-latenight-delivery",
    objective: "宵夜外送營收 P50 +2.0%，iROMI 為正。",
    status: "DRAFT",
    observationWindow: "尚未排程",
    targetLift: 2.0,
    observedLift: null,
    evidenceLevel: "low",
    rationale: "草稿：待補齊對照組與 pre-trend 檢定後送審。",
    rollbackPlan: "草稿階段無執行，無需 rollback。",
    audit: {
      decisionId: "draft-growth-7005",
      correlationId: "corr-growth-7005",
      modelVersion: "growth-uplift-v1.4.0",
      policyVersion: "growth-policy-2026.07",
      featureSnapshotTime: "2026-07-09T06:00:00Z",
    },
  },
];

// ---------------------------------------------------------------------------
// Runtime API client
// ---------------------------------------------------------------------------

const GROWTH_API_BASE = "/api/v1/operator/growth";

/** Generate a short idempotency key for browser-side requests. */
function newIdempotencyKey(): string {
  return `ik-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/** Generate a correlation ID for browser-side requests. */
function newCorrelationId(): string {
  return `corr-web-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

async function apiFetch<T>(
  path: string,
  options: RequestInit & { correlationId?: string } = {},
): Promise<T | null> {
  const { correlationId, ...fetchOptions } = options;
  try {
    const res = await fetch(`${GROWTH_API_BASE}${path}`, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        "X-Correlation-Id": correlationId ?? newCorrelationId(),
        ...operatorSecurityHeaders(),
        ...(fetchOptions.headers ?? {}),
      },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** Create a Growth Action draft from one of the three entry cards. */
export async function createGrowthDraft(params: {
  name: string;
  segmentId: string;
  sourceRecommendationId?: string;
  objective: string;
  targetLift: number;
  kind?: GrowthKind;
  store?: string;
  channel?: string;
  budget?: number;
  observationWindow?: string;
  observationWindowDays?: number;
  rationale?: string;
  rollbackPlan?: string;
}): Promise<{ id: string; status: string; kind: string; correlationId: string } | null> {
  const idempotencyKey = newIdempotencyKey();
  const correlationId = newCorrelationId();
  const result = await apiFetch<{
    id: string;
    status: string;
    kind: string;
    correlation_id: string;
  }>("/actions", {
    method: "POST",
    correlationId,
    headers: {
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      name: params.name,
      segmentId: params.segmentId,
      sourceRecommendationId: params.sourceRecommendationId,
      objective: params.objective,
      targetLift: params.targetLift,
      kind: params.kind ?? "offpeak",
      store: params.store ?? "全品牌",
      channel: params.channel ?? "LINE 推播",
      budget: params.budget ?? 0,
      observationWindow: params.observationWindow,
      observationWindowDays: params.observationWindowDays ?? 14,
      rationale: params.rationale ?? "",
      rollbackPlan: params.rollbackPlan ?? "",
    }),
  });
  if (!result) return null;
  return {
    id: result.id,
    status: result.status,
    kind: result.kind,
    correlationId: result.correlation_id,
  };
}

/** A single conflict-gate check returned by the server. */
export type ConflictCheck = {
  id: "overlap" | "priceops" | "budget" | "fatigue" | "approval";
  label: string;
  level: "ok" | "warn" | "fail";
  note: string;
};

/** Server conflict-gate result for a draft payload. */
export type ConflictResult = {
  checks: ConflictCheck[];
  /** true when any check is `fail` — the builder must not submit. */
  blocked: boolean;
  reasons: string[];
};

/** A Govern approval item created when a Growth draft is submitted. */
export type GrowthApproval = {
  id: string;
  module: string;
  kind: string;
  ref: string;
  title: string;
  requester: string;
  approver: string;
  risk: string;
  status: "pending" | "approved" | "rejected";
  evidence: string[];
  reason: string;
  decidedBy: string;
  decidedAt: string;
};

/** Run the five server-side conflict checks for a draft payload. */
export async function checkGrowthConflicts(params: {
  kind: GrowthKind;
  store: string;
  observationWindow: string;
  channel: string;
  budget: number;
  excludeActionId?: string;
}): Promise<ConflictResult | null> {
  return apiFetch<ConflictResult>("/conflicts/check", {
    method: "POST",
    correlationId: newCorrelationId(),
    body: JSON.stringify({
      kind: params.kind,
      store: params.store,
      observationWindow: params.observationWindow,
      channel: params.channel,
      budget: params.budget,
      excludeActionId: params.excludeActionId,
    }),
  });
}

/** Submit a DRAFT Growth Action for approval; creates a Govern item. */
export async function submitGrowthForApproval(params: {
  actionId: string;
}): Promise<{ actionId: string; status: string; approval: GrowthApproval; correlationId: string } | null> {
  const correlationId = newCorrelationId();
  const result = await apiFetch<{
    id: string;
    status: string;
    approval: GrowthApproval;
    correlation_id: string;
  }>(`/actions/${params.actionId}/submit`, {
    method: "POST",
    correlationId,
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({}),
  });
  if (!result) return null;
  return {
    actionId: result.id,
    status: result.status,
    approval: result.approval,
    correlationId: result.correlation_id,
  };
}

/** Approve or reject a Growth approval; advances the linked Growth state. */
export async function resolveGrowthApproval(params: {
  approvalId: string;
  decision: "approved" | "rejected";
  reason?: string;
}): Promise<{ approvalId: string; growthStatus: string; correlationId: string } | null> {
  const correlationId = newCorrelationId();
  const result = await apiFetch<{
    id: string;
    growthStatus: string;
    correlation_id: string;
  }>(`/approvals/${params.approvalId}/decision`, {
    method: "POST",
    correlationId,
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ decision: params.decision, reason: params.reason ?? "" }),
  });
  if (!result) return null;
  return {
    approvalId: result.id,
    growthStatus: result.growthStatus,
    correlationId: result.correlation_id,
  };
}

/** Write back effectiveness verdict for a Growth Action. */
export async function writeGrowthOutcome(params: {
  actionId: string;
  outcome: GrowthOutcome;
  requiredAction: CloseoutRequiredAction;
  observedLift?: number | null;
  evidenceLevel?: ConfidenceLevel;
  rationale?: string;
}): Promise<{ actionId: string; outcome: string; correlationId: string } | null> {
  const idempotencyKey = newIdempotencyKey();
  const correlationId = newCorrelationId();
  const result = await apiFetch<{
    id: string;
    growth_outcome: string;
    correlation_id: string;
  }>(`/actions/${params.actionId}/outcome`, {
    method: "POST",
    correlationId,
    headers: {
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      outcome: params.outcome,
      requiredAction: params.requiredAction,
      observedLift: params.observedLift ?? null,
      evidenceLevel: params.evidenceLevel ?? "medium",
      rationale: params.rationale ?? "",
    }),
  });
  if (!result) return null;
  return {
    actionId: result.id,
    outcome: result.growth_outcome,
    correlationId: result.correlation_id,
  };
}

/** Bundled API client for Growth workspace write operations. */
export const growthApiClient = {
  createGrowthDraft,
  writeGrowthOutcome,
  checkGrowthConflicts,
  submitGrowthForApproval,
  resolveGrowthApproval,
};

// ---------------------------------------------------------------------------
// Create-entry cards + five-step builder model (package 6)
// ---------------------------------------------------------------------------

/** The three create-entry cards shown at the top of the Growth workspace. */
export type GrowthEntryCard = {
  kind: GrowthKind;
  title: string;
  en: string;
  desc: string;
  /** accent dot color (design token). */
  dot: string;
};

export const GROWTH_ENTRY_CARDS: GrowthEntryCard[] = [
  {
    kind: "offpeak",
    title: "建立離峰促銷",
    en: "Off-peak Promotion",
    desc: "平日 10:00–14:00 · 離峰使用者／附近會員 · 洗烘組合 9 折",
    dot: "#12909F",
  },
  {
    kind: "winback",
    title: "建立會員召回",
    en: "Member Winback",
    desc: "60 天未回訪會員 · LINE 推播＋折抵券",
    dot: "#2E3A97",
  },
  {
    kind: "priceops",
    title: "建立 PriceOps 測試",
    en: "PriceOps Test",
    desc: "尖峰／離峰價格彈性測試 · 含回滾條件",
    dot: "#8A6BC7",
  },
];

/** The five builder steps: setup → audience/time → impact → risk/conflict → approval. */
export const BUILDER_STEPS: string[] = [
  "基本設定",
  "客群／時段",
  "預估效益",
  "風險／衝突",
  "送核准",
];

export const growthKindLabel: Record<GrowthKind, string> = {
  offpeak: "離峰促銷 Off-peak Promotion",
  winback: "會員召回 Member Winback",
  priceops: "PriceOps 測試",
};

/** Draft form the five-step builder collects and submits. */
export type GrowthBuilderForm = {
  kind: GrowthKind;
  name: string;
  segmentId: string;
  objective: string;
  store: string;
  observationWindow: string;
  channel: string;
  targetLift: string;
  budget: string;
  rationale: string;
  rollbackPlan: string;
  sourceRecommendationId?: string;
};

/** Per-kind builder presets that prefill each entry card's draft. */
export const GROWTH_KIND_PRESETS: Record<GrowthKind, GrowthBuilderForm> = {
  offpeak: {
    kind: "offpeak",
    name: "平日離峰洗烘組合促銷",
    segmentId: "seg-metro-dinner",
    objective: "提升離峰設備利用率",
    store: "Oday 信義松仁店",
    observationWindow: "平日 10:00–14:00",
    channel: "LINE 推播",
    targetLift: "2.0",
    budget: "10000",
    rationale: "洗烘組合 9 折／點數加倍，鎖定離峰使用者與附近會員。",
    rollbackPlan: "14 天未達標即回復原價目表。",
  },
  winback: {
    kind: "winback",
    name: "60 天未回訪會員召回",
    segmentId: "seg-latenight-delivery",
    objective: "提升沉睡會員回訪率",
    store: "全品牌",
    observationWindow: "核准後 3 日內推播",
    channel: "LINE 推播",
    targetLift: "3.0",
    budget: "18000",
    rationale: "對 60 天未回訪會員推播 NT$50 折抵券，30 天觀察回訪。",
    rollbackPlan: "核銷率低於門檻即停止推播。",
  },
  priceops: {
    kind: "priceops",
    name: "尖峰／離峰價格測試",
    segmentId: "seg-metro-dinner",
    objective: "尖峰／離峰價格彈性測試",
    store: "Oday 信義松仁店",
    observationWindow: "平日 10:00–14:00",
    channel: "店內告示＋App 價格頁",
    targetLift: "2.0",
    budget: "0",
    rationale: "離峰折扣 -15% 測試需求彈性，含回滾條件。",
    rollbackPlan: "14 天未達標即回滾至原價。",
  },
};

/** Tone for a conflict-check level. */
export const conflictLevelTone: Record<ConflictCheck["level"], StatusTone> = {
  ok: "green",
  warn: "orange",
  fail: "red",
};

// ---------------------------------------------------------------------------
// Runtime data fetch with fixture fallback
// ---------------------------------------------------------------------------

export type GrowthApiData = {
  freshness: GrowthFreshness;
  segments: GrowthSegment[];
  recommendations: PriceOpsRecommendation[];
  items: GrowthItem[];
  /** true when data came from the live API; false when falling back to fixtures */
  fromApi: boolean;
};

/**
 * Fetch all Growth workspace data from /api/v1/operator/growth/*.
 * Falls back to embedded fixtures on any error so the workspace never breaks.
 *
 * Called from server components (Next.js server-side fetch) or from
 * client-side hooks when live refresh is needed.
 */
export async function fetchGrowthApiData(params: {
  segmentId?: string;
} = {}): Promise<GrowthApiData> {
  const correlationId = newCorrelationId();
  const baseHeaders = { "X-Correlation-Id": correlationId };

  try {
    const [freshnessRes, segmentsRes, recommendationsRes, actionsRes] = await Promise.all([
      apiFetch<GrowthFreshness>("/freshness", { headers: baseHeaders }),
      apiFetch<{ items: GrowthSegment[] }>("/segments", { headers: baseHeaders }),
      apiFetch<{ items: PriceOpsRecommendation[] }>(
        params.segmentId
          ? `/recommendations?segment_id=${encodeURIComponent(params.segmentId)}`
          : "/recommendations",
        { headers: baseHeaders },
      ),
      apiFetch<{ items: GrowthItem[] }>(
        params.segmentId
          ? `/actions?segment_id=${encodeURIComponent(params.segmentId)}`
          : "/actions",
        { headers: baseHeaders },
      ),
    ]);

    if (segmentsRes && recommendationsRes && actionsRes) {
      return {
        freshness: freshnessRes ?? FIXTURE_FRESHNESS,
        segments: segmentsRes.items,
        recommendations: recommendationsRes.items,
        items: actionsRes.items,
        fromApi: true,
      };
    }
  } catch {
    // fall through to fixture fallback
  }

  // Fixture fallback
  const recommendations = params.segmentId
    ? PRICEOPS_RECOMMENDATIONS.filter((r) => r.segmentId === params.segmentId)
    : PRICEOPS_RECOMMENDATIONS;
  const items = params.segmentId
    ? GROWTH_ITEMS.filter((i) => i.segmentId === params.segmentId)
    : GROWTH_ITEMS;

  return {
    freshness: FIXTURE_FRESHNESS,
    segments: SEGMENTS,
    recommendations,
    items,
    fromApi: false,
  };
}

// ---------------------------------------------------------------------------
// Domain logic (pure, unchanged from ODP-OC-FE-03)
// ---------------------------------------------------------------------------

/** Statuses whose observation window has matured enough to judge an outcome. */
const OUTCOME_STAGES: GrowthStatus[] = ["OUTCOME_READY", "CLOSED"];

/**
 * 成效判斷 — classify the observed effectiveness of a growth action.
 *
 * Rules (deterministic, evidence-aware):
 *   - window not matured / no observation → PENDING
 *   - non-positive observed lift          → INEFFECTIVE
 *   - low evidence, or positive but below target → INCONCLUSIVE
 *   - met target with adequate evidence   → EFFECTIVE
 */
export function judgeEffectiveness(item: GrowthItem): GrowthOutcome {
  if (!OUTCOME_STAGES.includes(item.status) || item.observedLift === null) {
    return "PENDING";
  }
  if (item.observedLift <= 0) {
    return "INEFFECTIVE";
  }
  if (item.evidenceLevel === "low" || item.observedLift < item.targetLift) {
    return "INCONCLUSIVE";
  }
  return "EFFECTIVE";
}

export type CloseoutRequiredAction =
  | "CLOSE"
  | "ROLLBACK"
  | "CONTINUE_OBSERVATION"
  | "STRENGTHEN_EVIDENCE";

export type CloseoutGate = {
  outcome: GrowthOutcome;
  canClose: boolean;
  requiredAction: CloseoutRequiredAction;
  reason: string;
};

/**
 * Closeout gate — encodes the product rule 「無效活動不可直接結案」.
 *
 * Only an EFFECTIVE, matured action may be closed directly. Ineffective actions
 * must be rolled back / revised first; pending or inconclusive actions must keep
 * observing or strengthen evidence before any closeout is allowed.
 */
export function closeoutGate(item: GrowthItem): CloseoutGate {
  const outcome = judgeEffectiveness(item);
  switch (outcome) {
    case "EFFECTIVE":
      return {
        outcome,
        canClose: true,
        requiredAction: "CLOSE",
        reason: "達標且證據充足，可結案並回寫 Label Registry。",
      };
    case "INEFFECTIVE":
      return {
        outcome,
        canClose: false,
        requiredAction: "ROLLBACK",
        reason: "活動無效：必須先執行 rollback 或修正方案，無效活動不可直接結案。",
      };
    case "INCONCLUSIVE":
      return {
        outcome,
        canClose: false,
        requiredAction: "STRENGTHEN_EVIDENCE",
        reason: "未達標或證據不足，需補強對照組/延長觀察後再判定，不可直接結案。",
      };
    default:
      return {
        outcome,
        canClose: false,
        requiredAction: "CONTINUE_OBSERVATION",
        reason: "觀察窗尚未成熟，無法判定成效，不可結案。",
      };
  }
}

export const outcomeLabel: Record<GrowthOutcome, string> = {
  PENDING: "觀察中",
  EFFECTIVE: "有效",
  INEFFECTIVE: "無效",
  INCONCLUSIVE: "待判定",
};

export const outcomeTone: Record<GrowthOutcome, StatusTone> = {
  PENDING: "blue",
  EFFECTIVE: "green",
  INEFFECTIVE: "red",
  INCONCLUSIVE: "orange",
};

export const constraintTone: Record<
  PriceOpsRecommendation["constraintStatus"],
  StatusTone
> = {
  PASS: "green",
  SOFT_WARNING: "orange",
  HARD_CONSTRAINT_FAILED: "red",
};

export const trendLabel: Record<GrowthSegment["trend"], string> = {
  up: "↑ 成長",
  flat: "→ 持平",
  down: "↓ 下滑",
};

export const trendTone: Record<GrowthSegment["trend"], StatusTone> = {
  up: "green",
  flat: "gray",
  down: "red",
};

export const confidenceTone: Record<ConfidenceLevel, StatusTone> = {
  high: "green",
  medium: "blue",
  low: "orange",
};

/** Format a signed percent for lift display (P50 convention). */
export function formatLift(value: number | null): string {
  if (value === null) {
    return "—（觀察中）";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export type GrowthViewModel = {
  segments: GrowthSegment[];
  recommendations: PriceOpsRecommendation[];
  items: GrowthItem[];
  selectedSegment: GrowthSegment | null;
  selectedItem: GrowthItem;
  selectedItemGate: CloseoutGate;
  draftRecommendation: PriceOpsRecommendation | null;
  summary: {
    segmentCount: number;
    activeCount: number;
    effectiveCount: number;
    blockedCloseoutCount: number;
  };
  /** Metadata about whether data came from the live API or fixtures. */
  dataSource: "api" | "fixture";
};

/**
 * Build the workspace view model from URL-synced selection params and
 * pre-fetched API data.
 *
 * When called without `apiData`, falls back to embedded fixtures (backwards
 * compatible with existing synchronous callers and tests).
 */
export function buildGrowthViewModel(
  params: {
    segmentId?: string;
    itemId?: string;
    draftId?: string;
  },
  apiData?: GrowthApiData,
): GrowthViewModel {
  const allSegments = apiData?.segments ?? SEGMENTS;
  const allItems = apiData?.items ?? GROWTH_ITEMS;
  const allRecommendations = apiData?.recommendations ?? PRICEOPS_RECOMMENDATIONS;

  const selectedSegment =
    allSegments.find((segment) => segment.id === params.segmentId) ?? null;

  const recommendations = selectedSegment
    ? allRecommendations.filter((rec) => rec.segmentId === selectedSegment.id)
    : allRecommendations;

  const items = selectedSegment
    ? allItems.filter((item) => item.segmentId === selectedSegment.id)
    : allItems;

  const fallbackItem = items[0] ?? allItems[0];
  const selectedItem =
    items.find((item) => item.id === params.itemId) ?? fallbackItem;

  const draftRecommendation = params.draftId
    ? allRecommendations.find((rec) => rec.id === params.draftId) ?? null
    : null;

  const activeStatuses: GrowthStatus[] = [
    "APPROVED",
    "EXECUTED",
    "OBSERVING",
    "OUTCOME_READY",
  ];

  return {
    segments: allSegments,
    recommendations,
    items,
    selectedSegment,
    selectedItem,
    selectedItemGate: closeoutGate(selectedItem),
    draftRecommendation,
    dataSource: apiData?.fromApi ? "api" : "fixture",
    summary: {
      segmentCount: allSegments.length,
      activeCount: allItems.filter((item) =>
        activeStatuses.includes(item.status),
      ).length,
      effectiveCount: allItems.filter(
        (item) => judgeEffectiveness(item) === "EFFECTIVE",
      ).length,
      blockedCloseoutCount: allItems.filter((item) => {
        const gate = closeoutGate(item);
        return item.status === "OUTCOME_READY" && !gate.canClose;
      }).length,
    },
  };
}
