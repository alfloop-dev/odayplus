/**
 * Growth workspace (營收成長) view model.
 *
 * Pure fixtures + selectors for the Operator Console Growth workspace. No
 * runtime/data dependencies — the workspace renders from these fixtures until
 * the backend Growth/PriceOps endpoints are wired.
 *
 * Fixture entities (task ODP-OC-FE-03):
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

export type ConfidenceLevel = Confidence["level"];

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
  segmentId: string;
  /** PriceOps recommendation that seeded this draft, if any. */
  sourceRecommendationId?: string;
  objective: string;
  status: DecisionStatus;
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

export const freshness = {
  status: "FRESH" as DataStatus,
  updatedAt: "2026-07-09 14:20",
  modelVersion: "growth-uplift-v1.4.0",
  policyVersion: "growth-policy-2026.07",
  featureSnapshotTime: "2026-07-09T06:00:00Z",
  sourceSnapshotId: "snap-growth-20260709-0600",
};

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

/** Statuses whose observation window has matured enough to judge an outcome. */
const OUTCOME_STAGES: DecisionStatus[] = ["OUTCOME_READY", "CLOSED"];

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
};

/**
 * Build the workspace view model from URL-synced selection params. Filtering by
 * segment narrows both the recommendation table and the growth-action list so
 * the console reads as one coherent workspace.
 */
export function buildGrowthViewModel(params: {
  segmentId?: string;
  itemId?: string;
  draftId?: string;
}): GrowthViewModel {
  const selectedSegment =
    SEGMENTS.find((segment) => segment.id === params.segmentId) ?? null;

  const recommendations = selectedSegment
    ? PRICEOPS_RECOMMENDATIONS.filter((rec) => rec.segmentId === selectedSegment.id)
    : PRICEOPS_RECOMMENDATIONS;

  const items = selectedSegment
    ? GROWTH_ITEMS.filter((item) => item.segmentId === selectedSegment.id)
    : GROWTH_ITEMS;

  const fallbackItem = items[0] ?? GROWTH_ITEMS[0];
  const selectedItem =
    items.find((item) => item.id === params.itemId) ?? fallbackItem;

  const draftRecommendation = params.draftId
    ? PRICEOPS_RECOMMENDATIONS.find((rec) => rec.id === params.draftId) ?? null
    : null;

  const activeStatuses: DecisionStatus[] = [
    "APPROVED",
    "EXECUTED",
    "OBSERVING",
    "OUTCOME_READY",
  ];

  return {
    segments: SEGMENTS,
    recommendations,
    items,
    selectedSegment,
    selectedItem,
    selectedItemGate: closeoutGate(selectedItem),
    draftRecommendation,
    summary: {
      segmentCount: SEGMENTS.length,
      activeCount: GROWTH_ITEMS.filter((item) =>
        activeStatuses.includes(item.status),
      ).length,
      effectiveCount: GROWTH_ITEMS.filter(
        (item) => judgeEffectiveness(item) === "EFFECTIVE",
      ).length,
      blockedCloseoutCount: GROWTH_ITEMS.filter((item) => {
        const gate = closeoutGate(item);
        return item.status === "OUTCOME_READY" && !gate.canClose;
      }).length,
    },
  };
}
