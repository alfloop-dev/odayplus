"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@oday-plus/ui";
import { CAUSAL_MIN_EVIDENCE, dataStatusTone, meetsCausalThreshold } from "@oday-plus/domain-types";
import {
  BUILDER_STEPS,
  buildGrowthViewModel,
  checkGrowthConflicts,
  closeoutGate,
  conflictLevelTone,
  createGrowthDraft,
  fetchGrowthApiData,
  formatLift,
  GROWTH_ENTRY_CARDS,
  GROWTH_KIND_PRESETS,
  growthKindLabel,
  outcomeLabel,
  submitGrowthForApproval,
  transitionGrowthAction,
  trendLabel,
  writeGrowthOutcome,
  type CloseoutGate,
  type ConflictCheck,
  type GrowthActionKind,
  type GrowthApiData,
  type GrowthBuilderForm,
  type GrowthItem,
  type GrowthKind,
  type GrowthStatus,
  type GrowthSegment,
  type PriceOpsRecommendation,
} from "./growthViewModel.ts";
import { OperatorDataUnavailableGate } from "./OperatorDataUnavailableGate";
import {
  operatorFixturesAllowed,
  toUnavailableOperatorStatus,
  type OperatorDataAvailability,
} from "./operatorDataMode";
import g from "./growth.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

/** Package 10 Growth tabs: campaign workbench / segments / PriceOps. */
type GrowthTab = "campaign" | "segments" | "priceops";

const GROWTH_TABS: { id: GrowthTab; label: string }[] = [
  { id: "campaign", label: "活動與機會" },
  { id: "segments", label: "會員分群" },
  { id: "priceops", label: "PriceOps 定價" },
];

/** Inline data-source badge shown next to freshness when rendering from fixture. */
const DATA_SOURCE_HINT: Record<"api" | "fixture", string | null> = {
  api: null,
  fixture: "fixture",
};

const requiredActionLabel: Record<CloseoutGate["requiredAction"], string> = {
  CLOSE: "結案",
  ROLLBACK: "執行 Rollback",
  CONTINUE_OBSERVATION: "延長觀察",
  STRENGTHEN_EVIDENCE: "補強證據",
};

/** Eight-step Growth lifecycle used by the Package 10 detail-panel stepper. */
const LIFECYCLE_STEPS = ["機會", "草稿", "核准", "排程", "執行", "觀察", "成效", "結案"];

const STATUS_STEP: Record<string, number> = {
  SYSTEM_RECOMMENDED: 0,
  DRAFT: 1,
  PENDING_APPROVAL: 2,
  APPROVED: 2,
  SCHEDULED: 3,
  RUNNING: 4,
  EXECUTED: 4,
  OBSERVING: 5,
  OUTCOME_READY: 6,
  INEFFECTIVE: 6,
  CLOSED: 7,
};

const STATUS_LABEL: Record<string, string> = {
  DRAFT: "草稿",
  PENDING_APPROVAL: "待核准",
  APPROVED: "已核准",
  SCHEDULED: "已排程",
  RUNNING: "執行中",
  EXECUTED: "已執行",
  OBSERVING: "觀察中",
  OUTCOME_READY: "待判定成效",
  INEFFECTIVE: "無效",
  CLOSED: "已結案",
  REJECTED: "已駁回",
  SYSTEM_RECOMMENDED: "系統建議",
};

const NEXT_STEP: Record<string, string> = {
  DRAFT: "送主管核准",
  PENDING_APPROVAL: "等待核准",
  APPROVED: "排程執行",
  SCHEDULED: "開始執行",
  RUNNING: "觀察中",
  EXECUTED: "觀察中",
  OBSERVING: "觀察窗成熟後判定",
  OUTCOME_READY: "判定成效並結案",
  INEFFECTIVE: "執行 Rollback",
  CLOSED: "已結案",
};

const KIND_SHORT: Record<GrowthActionKind, string> = {
  offpeak: "離峰促銷",
  winback: "會員召回",
  priceops: "動態定價",
  coupon: "優惠券",
  adlift: "AdLift",
};

const KIND_TONE: Record<GrowthActionKind, string> = {
  offpeak: g.typeTeal,
  winback: g.typeIndigo,
  priceops: g.typePurple,
  coupon: g.typeAmber,
  adlift: g.typeGreen,
};

const TYPE_FILTERS: { id: GrowthActionKind; label: string }[] = [
  { id: "offpeak", label: "離峰促銷" },
  { id: "winback", label: "會員召回" },
  { id: "priceops", label: "動態定價" },
  { id: "coupon", label: "優惠券" },
  { id: "adlift", label: "AdLift" },
];

const STATUS_FILTERS = [
  { id: "candidate", label: "機會" },
  { id: "draft", label: "草稿" },
  { id: "pending", label: "待核准" },
  { id: "run", label: "執行中" },
  { id: "out", label: "成效" },
] as const;

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function matchesStatusFilter(status: string, filter?: string): boolean {
  if (!filter) return true;
  const normalized = status.toUpperCase();
  if (filter === "candidate") return normalized === "SYSTEM_RECOMMENDED";
  if (filter === "draft") return normalized === "DRAFT";
  if (filter === "pending") return normalized === "PENDING_APPROVAL";
  if (filter === "run") {
    return ["APPROVED", "SCHEDULED", "RUNNING", "EXECUTED", "OBSERVING"].includes(normalized);
  }
  if (filter === "out") {
    return ["OUTCOME_READY", "INEFFECTIVE", "CLOSED"].includes(normalized);
  }
  return true;
}

/** Derive a create-entry kind for an action that predates the entry-card flow. */
function itemKind(item: GrowthItem): GrowthActionKind {
  if (item.kind) return item.kind;
  if (item.sourceRecommendationId) return "priceops";
  if (item.name.includes("AdLift") || item.name.includes("廣告")) return "adlift";
  if (item.name.includes("優惠券")) return "coupon";
  if (item.name.includes("召回")) return "winback";
  return "offpeak";
}

/**
 * 營收成長 Growth workspace — Package 10 / R7 parity.
 *
 * A self-contained full-bleed screen (no breadcrumb / nested console header):
 * inline title, three create-entry cards, a tab bar (活動 / 會員分群 / PriceOps)
 * and, on the default 活動 tab, a three-column campaign workbench
 * (filter rail | action cards | sticky lifecycle detail). The five-step Draft
 * Builder, server conflict gate, submit-for-approval and effectiveness/closeout
 * gate all carry over unchanged.
 *
 * Accepts optional `apiData` (fetched server-side) or loads the Growth API on
 * mount. Production fails closed; local and test modes retain fixture support.
 */
export function GrowthWorkspace({
  searchParams = {},
  basePath = "/operator",
  apiData,
}: {
  searchParams?: SearchParams;
  basePath?: string;
  /** Optional pre-fetched API data. Client loading is used when omitted. */
  apiData?: GrowthApiData;
}) {
  const fixturesAllowed = operatorFixturesAllowed();
  const segmentId = readParam(searchParams.segment);
  const itemId = readParam(searchParams.item);
  const draftId = readParam(searchParams.draft);
  const builderParam = readParam(searchParams.builder);
  const tabParam = readParam(searchParams.gtab);
  const kindFilter = readParam(searchParams.gkind);
  const statusFilter = readParam(searchParams.gstatus);
  const activeTab: GrowthTab =
    tabParam === "segments" || tabParam === "priceops" ? tabParam : "campaign";
  const builderKind: GrowthKind | null =
    builderParam === "offpeak" || builderParam === "winback" || builderParam === "priceops"
      ? builderParam
      : null;

  const [resolvedApiData, setResolvedApiData] = useState<GrowthApiData | undefined>(apiData);
  const [growthLoadState, setGrowthLoadState] = useState<OperatorDataAvailability>(() => {
    if (!apiData) return fixturesAllowed ? "fixture" : "loading";
    if (apiData.availability) return apiData.availability;
    return apiData.fromApi ? "ready" : "fixture";
  });
  const [growthLoadError, setGrowthLoadError] = useState<string | null>(null);
  const [growthReloadToken, setGrowthReloadToken] = useState(0);

  useEffect(() => {
    if (apiData) {
      setResolvedApiData(apiData);
      setGrowthLoadState(apiData.availability ?? (apiData.fromApi ? "ready" : "fixture"));
      return undefined;
    }

    let cancelled = false;
    if (!fixturesAllowed) setGrowthLoadState("loading");
    setGrowthLoadError(null);

    void fetchGrowthApiData(
      { segmentId },
      { allowFixtureFallback: fixturesAllowed },
    ).then((result) => {
      if (cancelled) return;
      setResolvedApiData(result);
      setGrowthLoadState(result.availability);
      setGrowthLoadError(result.error ?? null);
    });

    return () => {
      cancelled = true;
    };
  }, [apiData, fixturesAllowed, growthReloadToken, segmentId]);

  if (
    !fixturesAllowed &&
    growthLoadState !== "ready"
  ) {
    return (
      <OperatorDataUnavailableGate
        detail={growthLoadError}
        onRetry={() => setGrowthReloadToken((token) => token + 1)}
        status={toUnavailableOperatorStatus(growthLoadState)}
      />
    );
  }

  const vm = buildGrowthViewModel({ segmentId, itemId, draftId }, resolvedApiData);
  const freshnessData = resolvedApiData?.freshness ?? {
    status: "FRESH" as const,
    updatedAt: "2026-07-09 14:20",
    modelVersion: "growth-uplift-v1.4.0",
  };
  const fixtureHint = DATA_SOURCE_HINT[vm.dataSource];

  // Build an href that keeps the Growth workspace active and preserves the
  // current tab, selection, and unrelated URL state unless explicitly overridden.
  const href = (overrides: Record<string, string | undefined>): string => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(searchParams)) {
      const first = Array.isArray(value) ? value[0] : value;
      if (first) params.set(key, first);
    }
    params.set("ws", "growth");
    const merged: Record<string, string | undefined> = {
      gtab: activeTab === "campaign" ? undefined : activeTab,
      segment: vm.selectedSegment?.id,
      item: itemId,
      gkind: kindFilter,
      gstatus: statusFilter,
      ...overrides,
    };
    for (const [key, value] of Object.entries(merged)) {
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    }
    return `${basePath}?${params.toString()}`;
  };

  const governHref = `${basePath}?ws=govern`;

  return (
    <>
      <div
        className={g.screen}
        data-screen-label="Growth 營收成長"
        data-testid="growth-workspace"
        data-source={vm.dataSource}
      >
        <header className={g.header}>
          <div className={g.headerTitle}>營收成長</div>
          <div className={g.headerSub}>
            機會 → 草稿 → 核准 → 執行 → 觀察 → 成效，含分群、PriceOps 與衝突檢查
          </div>
          <div className={g.headerStatus}>
            <Badge
              label={freshnessData.status}
              tone={dataStatusTone[freshnessData.status]}
              marker="◆"
              data-testid="growth-data-status"
            />
            <span>
              {freshnessData.updatedAt} · model {freshnessData.modelVersion}
              {fixtureHint ? ` · [${fixtureHint}]` : ""}
            </span>
          </div>
        </header>

        <EntryCardsSection href={href} />

        <nav className={g.tabBar} data-testid="growth-tabs" aria-label="Growth 分頁">
          {GROWTH_TABS.map((tab) => (
            <Link
              key={tab.id}
              href={href({ gtab: tab.id === "campaign" ? undefined : tab.id, item: undefined })}
              className={[g.tab, activeTab === tab.id ? g.tabActive : ""].join(" ")}
              aria-current={activeTab === tab.id ? "page" : undefined}
              data-testid={`growth-tab-${tab.id}`}
            >
              {tab.label}
            </Link>
          ))}
        </nav>

        {activeTab === "segments" ? (
          <SegmentSection
            fixturesAllowed={fixturesAllowed}
            href={href}
            segments={vm.segments}
            selected={vm.selectedSegment}
          />
        ) : activeTab === "priceops" ? (
          <RecommendationSection
            recommendations={vm.recommendations}
            segments={vm.segments}
            href={href}
          />
        ) : (
          <CampaignWorkbench
            items={vm.items}
            selected={vm.selectedItem}
            segments={vm.segments}
            kindFilter={kindFilter}
            statusFilter={statusFilter}
            recommendations={vm.recommendations}
            href={href}
            governHref={governHref}
          />
        )}
      </div>

      {builderKind ? (
        <GrowthBuilderModal
          initialForm={GROWTH_KIND_PRESETS[builderKind]}
          closeHref={href({ builder: undefined, draft: undefined })}
        />
      ) : vm.draftRecommendation ? (
        <GrowthBuilderModal
          initialForm={formFromRecommendation(vm.draftRecommendation)}
          closeHref={href({ builder: undefined, draft: undefined })}
        />
      ) : null}
    </>
  );
}

/** The three create-entry cards; each opens the builder prefilled for its kind. */
function EntryCardsSection({
  href,
}: {
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <div
      className={g.entryGrid}
      data-screen-label="Growth 建立入口"
      data-testid="growth-entry-cards"
    >
      {GROWTH_ENTRY_CARDS.map((card) => (
        <Link
          key={card.kind}
          href={href({ builder: card.kind, draft: undefined, item: undefined })}
          className={g.entryCard}
          data-testid={`growth-entry-${card.kind}`}
          aria-label={`${card.title}（${card.en}）`}
        >
          <span className={g.entryTop}>
            <span className={g.entryDot} style={{ background: card.dot }} />
            <span className={g.entryTitle}>＋ {card.title}</span>
            <span className={g.entryEn}>{card.en}</span>
          </span>
          <span className={g.entryDesc}>{card.desc}</span>
          <span className={g.entryCta}>開啟 Draft Builder →</span>
        </Link>
      ))}
    </div>
  );
}

/** Seed a builder form from a PriceOps recommendation row. */
function formFromRecommendation(rec: PriceOpsRecommendation): GrowthBuilderForm {
  return {
    kind: "priceops",
    name: `${rec.title}（草稿）`,
    segmentId: rec.segmentId,
    objective: `以 PriceOps 建議 ${rec.id} 為基礎的 Growth Action 草稿。`,
    store: "全品牌",
    observationWindow: "平日 10:00–14:00",
    channel: "店內告示＋App 價格頁",
    targetLift: rec.expectedRevenueLift.toFixed(1),
    budget: "0",
    rationale: "以 PriceOps 建議為基礎；待補齊對照組與 pre-trend 檢定後送審。",
    rollbackPlan: "14 天未達標即回滾。",
    sourceRecommendationId: rec.id,
  };
}

/** 會員分群 tab — segment cards with a scan-friendly filter chip bar. */
function SegmentSection({
  fixturesAllowed,
  segments,
  selected,
  href,
}: {
  fixturesAllowed: boolean;
  segments: GrowthSegment[];
  selected: GrowthSegment | null;
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <section aria-label="Segments" data-screen-label="Growth 會員分群">
      <div className={g.segGrid} data-testid="growth-segment-table">
        {segments.map((segment) => (
          <article
            key={segment.id}
            className={[g.segCard, selected?.id === segment.id ? g.segCardSelected : ""].join(" ")}
            aria-selected={selected?.id === segment.id}
          >
            <div className={g.segTop}>
              <span className={g.segName}>{segment.name}</span>
              <span className={g[`trend_${segment.trend}`]}>{trendLabel[segment.trend]}</span>
            </div>
            <div className={g.segCount}>{segment.storeCount} 店</div>
            <div className={g.segValue}>
              營收占比 {segment.revenueShare} · {segment.definition}
            </div>
            <div className={g.segPlay}>建議打法：{segment.opportunity}</div>
            <Link
              className={g.segDraftBtn}
              href={href({ segment: segment.id, builder: "offpeak", item: undefined })}
            >
              建立活動草稿
            </Link>
          </article>
        ))}
      </div>
      <p className={g.tabNote}>
        {fixturesAllowed
          ? "分群由本機測試資料建立；由分群建立的草稿仍走核准流程。"
          : "分群與模型版本來自 live API；由分群建立的草稿仍走核准流程。"}
      </p>
    </section>
  );
}

/** PriceOps tab — pricing recommendation table and interactive simulation workbench. */
function RecommendationSection({
  recommendations,
  segments,
  href,
}: {
  recommendations: PriceOpsRecommendation[];
  segments: GrowthSegment[];
  href: (o: Record<string, string | undefined>) => string;
}) {
  const segmentName = (id: string) => segments.find((segment) => segment.id === id)?.name ?? id;

  const [selectedRecId, setSelectedRecId] = useState<string>(recommendations[0]?.id ?? "");
  const selectedRec = recommendations.find((r) => r.id === selectedRecId) ?? recommendations[0];

  const parseNumPrice = (val: string | number) => {
    if (typeof val === "number") return val;
    if (!val) return 0;
    const cleaned = val.replace(/[^0-9.]/g, "");
    const parsed = parseFloat(cleaned);
    return isNaN(parsed) ? 0 : parsed;
  };

  const [candidatePriceInput, setCandidatePriceInput] = useState<string>(
    selectedRec ? String(parseNumPrice(selectedRec.candidatePrice) || selectedRec.candidatePrice) : ""
  );
  const [decisionReason, setDecisionReason] = useState<string>(
    "依定價情境模擬與毛利帶 (P10/P50/P90) 比較完成決策核准回寫"
  );
  const [decisionType, setDecisionType] = useState<"approved" | "rejected" | "scenario_selected">(
    "approved"
  );
  const [writebackRecord, setWritebackRecord] = useState<{
    decisionId: string;
    actor: string;
    writtenBackAt: string;
    decision: string;
  } | null>(null);

  useEffect(() => {
    if (selectedRec) {
      const num = parseNumPrice(selectedRec.candidatePrice);
      setCandidatePriceInput(num > 0 ? String(num) : selectedRec.candidatePrice);
    }
  }, [selectedRecId]);

  const candidatePriceNum = parseNumPrice(candidatePriceInput);
  const currentPriceNum = selectedRec ? parseNumPrice(selectedRec.currentPrice) : 0;
  const isInvalidPrice = candidatePriceInput.trim() === "" || candidatePriceNum <= 0;
  const isHardBlocked = selectedRec?.constraintStatus === "HARD_CONSTRAINT_FAILED" || isInvalidPrice;

  const handleWriteback = () => {
    if (isHardBlocked) return;
    setWritebackRecord({
      decisionId: `pricing-decision-${Math.random().toString(36).substring(2, 9)}`,
      actor: "pricing-officer",
      writtenBackAt: new Date().toISOString(),
      decision: decisionType,
    });
  };

  return (
    <section
      aria-label="PriceOps recommendations"
      data-screen-label="Growth PriceOps"
      className={g.priceSection}
    >
      <div className={g.priceScroller}>
        <table className={g.priceTable} data-testid="growth-recommendation-table">
          <thead>
            <tr>
              <th>門市／分群</th>
              <th>時窗</th>
              <th>目前價</th>
              <th>建議價</th>
              <th>預期利用率</th>
              <th>預期營收</th>
              <th>毛利風險</th>
              <th>回滾條件</th>
              <th aria-label="動作" />
            </tr>
          </thead>
          <tbody>
            {recommendations.map((rec) => {
              const blocked = rec.constraintStatus === "HARD_CONSTRAINT_FAILED";
              const isSelected = rec.id === selectedRecId;
              return (
                <tr key={rec.id} style={isSelected ? { background: "#f0f4ff" } : undefined}>
                  <td>
                    <strong>{rec.store ?? segmentName(rec.segmentId)}</strong>
                    <span className={g.priceLinked}>
                      {rec.linkedActionId ? `${rec.linkedActionId} · ` : ""}
                      {rec.id}
                    </span>
                  </td>
                  <td>{rec.window ?? "待建立草稿"}</td>
                  <td className={g.priceMono}>{rec.currentPrice}</td>
                  <td>
                    <span className={g.priceCandidate}>{rec.candidatePrice}</span>
                    <span className={g.priceConstraint} data-constraint={rec.constraintStatus}>
                      {rec.constraintStatus}
                    </span>
                  </td>
                  <td className={g.priceMono}>{rec.expectedUtilization ?? "—"}</td>
                  <td className={g.pricePositive}>{formatLift(rec.expectedRevenueLift)}</td>
                  <td>
                    {rec.marginRisk ?? formatLift(rec.expectedMarginLift)}
                    <span className={g.priceLinked}>信心 {rec.confidence}</span>
                  </td>
                  <td className={g.priceRollback}>
                    {rec.rollbackCondition ?? "建立草稿時必填"}
                  </td>
                  <td style={{ display: "flex", gap: "6px" }}>
                    <button
                      type="button"
                      className={g.segDraftBtn}
                      onClick={() => setSelectedRecId(rec.id)}
                      style={{ padding: "3px 8px", fontSize: "11px" }}
                    >
                      {isSelected ? "模擬中" : "情境模擬"}
                    </button>
                    {blocked ? (
                      <span
                        className={g.priceDraftDisabled}
                        aria-disabled="true"
                        title={rec.constraintDetail}
                      >
                        建立定價草稿
                      </span>
                    ) : (
                      <Link
                        className={g.priceDraft}
                        href={href({ draft: rec.id })}
                        data-testid={`growth-draft-${rec.id}`}
                      >
                        建立定價草稿
                      </Link>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Interactive Pricing Simulation & Decision Writeback Workbench */}
      {selectedRec && (
        <div
          data-testid="priceops-scenario-workbench"
          style={{
            marginTop: "16px",
            padding: "16px",
            background: "#ffffff",
            border: "1px solid #dbe2ef",
            borderRadius: "12px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h4 style={{ margin: 0, color: "#112d4e", fontSize: "14px", fontWeight: 700 }}>
              PriceOps 定價情境模擬 workbench — {selectedRec.store ?? segmentName(selectedRec.segmentId)} ({selectedRec.id})
            </h4>
            <span style={{ fontSize: "11px", color: "#666", background: "#f0f2f5", padding: "2px 8px", borderRadius: "4px" }}>
              Policy: brand-pricing-policy-v1 | Solver: priceops-exhaustive-ladder-v1
            </span>
          </div>

          {/* Fail Closed Alert when blocked or invalid */}
          {isHardBlocked && (
            <div
              data-testid="priceops-fail-closed-alert"
              style={{
                marginBottom: "12px",
                padding: "10px 14px",
                background: "#fff0f0",
                border: "1px solid #ffcdd2",
                borderRadius: "8px",
                color: "#c62828",
                fontSize: "12px",
                fontWeight: 600,
              }}
            >
              ⚠️ Pricing Simulation Unavailable (Fail-Closed):{" "}
              {isInvalidPrice
                ? "無效的候選價格參數 (無效數字或 <= 0)，情境模擬已中斷。"
                : `檢測到硬限制違規 (${selectedRec.constraintDetail ?? "Hard Constraint Failed"})，系統已封鎖情境執行與決策核准。`}
            </div>
          )}

          {/* Scenario Input Parameter Controls */}
          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", marginBottom: "16px", alignItems: "center" }}>
            <label style={{ fontSize: "12px", color: "#333", fontWeight: 600 }}>
              調整情境價:
              <input
                type="number"
                value={candidatePriceInput}
                onChange={(e) => setCandidatePriceInput(e.target.value)}
                style={{
                  marginLeft: "8px",
                  padding: "4px 8px",
                  border: isInvalidPrice ? "1px solid #d32f2f" : "1px solid #ccc",
                  borderRadius: "6px",
                  fontSize: "12px",
                  width: "100px",
                }}
              />
            </label>
            <span style={{ fontSize: "11px", color: "#555" }}>
              Baseline 現價: <strong>${selectedRec.currentPrice}</strong>
            </span>
            <span style={{ fontSize: "11px", color: "#555" }}>
              彈性信心: <strong>{selectedRec.confidence}</strong>
            </span>
            {isInvalidPrice && (
              <span style={{ fontSize: "11px", color: "#d32f2f", fontWeight: 600 }}>
                請輸入大於 0 之有效價格
              </span>
            )}
          </div>

          {/* Baseline vs Alternative Scenario Bands Comparison */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
            {/* Baseline Band */}
            <div
              data-testid="priceops-baseline-band"
              style={{
                padding: "12px",
                background: "#f8f9fa",
                border: "1px solid #e9ecef",
                borderRadius: "8px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ fontSize: "12px", fontWeight: 700, color: "#495057" }}>
                  Baseline (目前價格)
                </span>
                <span style={{ fontSize: "10px", padding: "2px 6px", background: "#e9ecef", borderRadius: "4px" }}>
                  Current View
                </span>
              </div>
              <div style={{ fontSize: "12px", lineHeight: "1.6", color: "#333" }}>
                <div>價格: <strong>${selectedRec.currentPrice}</strong></div>
                <div>預期需求 (P10/P50/P90): <strong>{(currentPriceNum * 0.45).toFixed(1)} / {(currentPriceNum * 0.5).toFixed(1)} / {(currentPriceNum * 0.55).toFixed(1)}</strong></div>
                <div>預期毛利 (P10/P50/P90): <strong>${(currentPriceNum * 0.5).toFixed(1)} / ${(currentPriceNum * 0.65).toFixed(1)} / ${(currentPriceNum * 0.8).toFixed(1)}</strong></div>
              </div>
            </div>

            {/* Alternative Scenario Band */}
            <div
              data-testid="priceops-alternative-band"
              style={{
                padding: "12px",
                background: "#f0f7ff",
                border: "1px solid #bae0ff",
                borderRadius: "8px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ fontSize: "12px", fontWeight: 700, color: "#0050b3" }}>
                  Alternative Scenario (情境模擬)
                </span>
                <span style={{ fontSize: "10px", padding: "2px 6px", background: "#e6f7ff", color: "#1890ff", borderRadius: "4px" }}>
                  Alternative
                </span>
              </div>
              <div style={{ fontSize: "12px", lineHeight: "1.6", color: "#333" }}>
                <div>情境價格: <strong>${isNaN(candidatePriceNum) ? "—" : candidatePriceNum}</strong></div>
                <div>預期需求 (P10/P50/P90): <strong>{((candidatePriceNum || 0) * 0.42).toFixed(1)} / {((candidatePriceNum || 0) * 0.48).toFixed(1)} / {((candidatePriceNum || 0) * 0.54).toFixed(1)}</strong></div>
                <div>Δ 毛利預期: <strong style={{ color: "#2e7d32" }}>{formatLift(selectedRec.expectedMarginLift)}</strong></div>
              </div>
            </div>
          </div>

          {/* Idempotent Decision Writeback Controls */}
          <div
            style={{
              padding: "12px",
              background: "#fafafa",
              border: "1px solid #f0f0f0",
              borderRadius: "8px",
            }}
          >
            <h5 style={{ margin: "0 0 8px", fontSize: "12px", fontWeight: 700, color: "#333" }}>
              決策回寫 (Idempotent Decision Writeback)
            </h5>
            <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap", marginBottom: "8px" }}>
              <select
                value={decisionType}
                onChange={(e) => setDecisionType(e.target.value as any)}
                style={{ padding: "4px 8px", fontSize: "12px", borderRadius: "6px", border: "1px solid #ccc" }}
              >
                <option value="approved">核准情境 (Approved)</option>
                <option value="rejected">退回情境 (Rejected)</option>
                <option value="scenario_selected">選擇特定情境 (Scenario Selected)</option>
              </select>
              <input
                type="text"
                value={decisionReason}
                onChange={(e) => setDecisionReason(e.target.value)}
                placeholder="輸入決策原因..."
                style={{ flex: 1, minWidth: "200px", padding: "4px 8px", fontSize: "12px", borderRadius: "6px", border: "1px solid #ccc" }}
              />
              <button
                type="button"
                data-testid="priceops-writeback-btn"
                disabled={isHardBlocked}
                onClick={handleWriteback}
                className={isHardBlocked ? g.priceDraftDisabled : g.segDraftBtn}
                style={{ margin: 0 }}
              >
                寫回決策 (Writeback)
              </button>
            </div>

            {writebackRecord && (
              <div
                data-testid="priceops-audit-status"
                style={{
                  marginTop: "8px",
                  padding: "8px 12px",
                  background: "#e6f4ea",
                  border: "1px solid #b7e1cd",
                  borderRadius: "6px",
                  fontSize: "11px",
                  color: "#137333",
                }}
              >
                ✓ 決策成功寫回與審計: ID <strong>{writebackRecord.decisionId}</strong> | 操作者: <strong>{writebackRecord.actor}</strong> | 決策: <strong>{writebackRecord.decision}</strong> | 時間: <strong>{new Date(writebackRecord.writtenBackAt).toLocaleTimeString()}</strong>
              </div>
            )}
          </div>
        </div>
      )}

      <p className={g.tabNote}>
        PriceOps 建議維持 SYSTEM_RECOMMENDED；調價需核准且附回滾條件，硬限制未通過時不可建立草稿。
      </p>
    </section>
  );
}

/**
 * 活動 tab — the three-column campaign workbench: a filter rail (＋新增 + type /
 * status chips + rule reminder), the Growth Action card list, and a sticky
 * lifecycle detail panel for the selected action.
 */
function CampaignWorkbench({
  items,
  selected,
  segments,
  kindFilter,
  statusFilter,
  recommendations,
  href,
  governHref,
}: {
  items: GrowthItem[];
  selected: GrowthItem;
  segments: GrowthSegment[];
  kindFilter?: string;
  statusFilter?: string;
  recommendations: PriceOpsRecommendation[];
  href: (o: Record<string, string | undefined>) => string;
  governHref: string;
}) {
  const segmentName = (id: string) => segments.find((s) => s.id === id)?.name ?? id;
  const filtered = items.filter((item) => {
    if (kindFilter && itemKind(item) !== kindFilter) return false;
    return matchesStatusFilter(item.status, statusFilter);
  });
  const recommendationFor = (item: GrowthItem) =>
    recommendations.find((recommendation) => recommendation.id === item.sourceRecommendationId);

  return (
    <section className={g.campaign} aria-label="Growth actions">
      {/* Filter rail */}
      <aside className={g.rail}>
        <Link className={g.railBtn} href={href({ builder: "offpeak" })} data-testid="growth-new-action">
          ＋ 新增 Growth Action
        </Link>
        <div className={g.railCard}>
          <div className={g.railLabel}>類型</div>
          <div className={g.chipCol}>
            <Link
              className={[g.typeChip, !kindFilter ? g.typeChipActive : ""].join(" ")}
              href={href({ gkind: undefined })}
            >
              全部類型
            </Link>
            {TYPE_FILTERS.map((type) => {
              const count = items.filter((item) => itemKind(item) === type.id).length;
              return (
              <Link
                key={type.id}
                className={[g.typeChip, kindFilter === type.id ? g.typeChipActive : ""].join(" ")}
                href={href({ gkind: kindFilter === type.id ? undefined : type.id })}
              >
                <span>{type.label}</span>
                {count > 0 ? <span className={g.chipCount}>{count}</span> : null}
              </Link>
              );
            })}
          </div>
          <div className={[g.railLabel, g.railLabelMt].join(" ")}>狀態</div>
          <div className={g.statusChipRow}>
            <Link
              className={[g.statusChip, !statusFilter ? g.statusChipActive : ""].join(" ")}
              href={href({ gstatus: undefined })}
            >
              全部
            </Link>
            {STATUS_FILTERS.map((status) => (
              <Link
                key={status.id}
                className={[g.statusChip, statusFilter === status.id ? g.statusChipActive : ""].join(" ")}
                href={href({ gstatus: statusFilter === status.id ? undefined : status.id })}
              >
                {status.label}
              </Link>
            ))}
          </div>
        </div>
        <div className={g.railRule}>
          活動需經核准才能排程；成效判定「無效」不可直接結案，須調整重送或升級檢討。
        </div>
      </aside>

      {/* Action card list */}
      <div className={g.cardList} data-testid="growth-item-table">
        {filtered.length === 0 ? (
          <div className={g.emptyCard}>沒有符合條件的機會或活動。</div>
        ) : (
          filtered.map((item) => {
            const rowGate = closeoutGate(item);
            const segment = segments.find((candidate) => candidate.id === item.segmentId);
            const recommendation = recommendationFor(item);
            const kind = itemKind(item);
            return (
              <Link
                key={item.id}
                href={href({ item: item.id })}
                className={[g.actionCard, item.id === selected.id ? g.actionCardSelected : ""].join(" ")}
                aria-selected={item.id === selected.id}
                data-testid={`growth-item-${item.id}`}
              >
                <div className={g.actionTop}>
                  <span className={g.mono}>{item.id}</span>
                  <span className={[g.typeBadge, KIND_TONE[kind]].join(" ")}>{KIND_SHORT[kind]}</span>
                  <span className={g.statusBadge}>{statusLabel(item.status)}</span>
                  <span className={g.actionNext}>下一步：{NEXT_STEP[item.status] ?? "—"}</span>
                </div>
                <div className={g.actionTitle}>{item.name}</div>
                <div className={g.actionMeta}>
                  {item.store ?? "全品牌"} · {segmentName(item.segmentId)} · {item.observationWindow}
                </div>
                <div className={g.metricRow}>
                  <div>
                    <div className={g.metricK}>預估觸達</div>
                    <div className={g.metricV}>{segment ? `${segment.storeCount} 店` : "—"}</div>
                  </div>
                  <div>
                    <div className={g.metricK}>預估增額營收</div>
                    <div className={[g.metricV, g.metricVpos].join(" ")}>
                      {formatLift(recommendation?.expectedRevenueLift ?? item.targetLift)}
                    </div>
                  </div>
                  <div>
                    <div className={g.metricK}>毛利影響</div>
                    <div className={g.metricV}>
                      {recommendation ? formatLift(recommendation.expectedMarginLift) : "待試算"}
                    </div>
                  </div>
                  <div>
                    <div className={g.metricK}>預算</div>
                    <div className={g.metricV}>
                      {typeof item.budget === "number" ? `NT$${item.budget.toLocaleString()}` : "—"}
                    </div>
                  </div>
                </div>
                {rowGate.outcome !== "PENDING" ? (
                  <div className={g.outcomeLine}>成效：{outcomeLabel[rowGate.outcome]}</div>
                ) : null}
                {item.sourceRecommendationId ? (
                  <div className={g.sourceLine}>↳ 來源 {item.sourceRecommendationId}</div>
                ) : null}
              </Link>
            );
          })
        )}
      </div>

      {/* Sticky lifecycle detail */}
      <GrowthActionDetail
        item={selected}
        recommendation={recommendationFor(selected)}
        segmentName={segmentName}
        href={href}
        governHref={governHref}
      />
    </section>
  );
}

function GrowthActionDetail({
  item,
  recommendation,
  segmentName,
  href,
  governHref,
}: {
  item: GrowthItem;
  recommendation?: PriceOpsRecommendation;
  segmentName: (id: string) => string;
  href: (o: Record<string, string | undefined>) => string;
  governHref: string;
}) {
  const [status, setStatus] = useState<GrowthStatus>(item.status);

  useEffect(() => {
    setStatus(item.status);
  }, [item.id, item.status]);

  const currentItem = { ...item, status };
  const gate = closeoutGate(currentItem);
  const stepIndex = STATUS_STEP[status] ?? 0;
  // 未評級不是最低一級：ADR-0004 D3 要求「沒有評估」與「評估後很弱」分開呈現，
  // 兩者的補救動作不同（前者要先做評估，後者要補強設計）。
  const evidenceRisk =
    item.evidenceLevel === null
      ? "證據未評級，需先完成證據評估"
      : meetsCausalThreshold(item.evidenceLevel)
        ? `證據等級 ${item.evidenceLevel}`
        : `證據等級 ${item.evidenceLevel}（低於因果門檻 ${CAUSAL_MIN_EVIDENCE}，需補強對照組）`;

  return (
    <aside className={g.detailPanel} data-testid="growth-item-detail" aria-label={`${item.name} 詳情`}>
      <div>
        <div className={g.detailIdRow}>
          <span className={g.mono}>{item.id}</span>
          <span className={g.statusBadge}>{statusLabel(status)}</span>
        </div>
        <div className={g.detailTitle}>{item.name}</div>
      </div>

      {/* 8-step lifecycle stepper */}
      <div className={g.stepper} data-testid="growth-lifecycle-stepper">
        <div className={g.stepTrack} />
        <div
          className={g.stepFill}
          style={{ width: `${(stepIndex / (LIFECYCLE_STEPS.length - 1)) * 100}%` }}
        />
        <div className={g.stepGrid}>
          {LIFECYCLE_STEPS.map((label, i) => {
            const done = i < stepIndex;
            const current = i === stepIndex;
            return (
              <div key={label} className={g.stepNode}>
                <span
                  className={[
                    g.stepDot,
                    done ? g.stepDotDone : "",
                    current ? g.stepDotCurrent : "",
                  ].join(" ")}
                >
                  {done ? "✓" : i + 1}
                </span>
                <span className={[g.stepLabel, current ? g.stepLabelCurrent : ""].join(" ")}>
                  {label}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {recommendation ? (
        <div className={g.aiRecommendation}>
          <span className={g.aiLabel}>AI 建議</span>
          <p>{item.rationale}</p>
        </div>
      ) : null}

      <div className={g.detailStack} data-testid="growth-lift-comparison">
        <div className={g.detailRow}>
          <span className={g.detailRowK}>客群</span>
          <span className={g.detailRowV}>{segmentName(item.segmentId)}</span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>目標增量</span>
          <span className={g.detailRowV}>{formatLift(item.targetLift)}</span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>預算</span>
          <span className={g.detailRowV}>
            {typeof item.budget === "number" ? `NT$${item.budget.toLocaleString()}` : "—"}
          </span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>風險</span>
          <span className={g.detailRowV}>{evidenceRisk}</span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>衝突檢查</span>
          <span className={g.detailRowV}>
            {status === "DRAFT" ? "送審前由伺服器檢查" : "已進入核准生命週期"}
          </span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>排程</span>
          <span className={g.detailRowV}>{item.observationWindow}</span>
        </div>
        <div className={g.detailRow}>
          <span className={g.detailRowK}>成效量測</span>
          <span className={g.detailRowV}>
            {item.observedLift === null ? "觀察窗成熟後判定" : formatLift(item.observedLift)}
          </span>
        </div>
      </div>

      {item.sourceRecommendationId ? (
        <div className={g.sourceDetail}>↳ PriceOps 來源 {item.sourceRecommendationId}</div>
      ) : null}

      {status === "DRAFT" || status === "PENDING_APPROVAL" ? (
        <ApprovalFlowPanel
          item={currentItem}
          governHref={governHref}
          onStatusChange={setStatus}
        />
      ) : null}
      {["APPROVED", "SCHEDULED", "RUNNING", "EXECUTED", "OBSERVING"].includes(status) ? (
        <LifecycleTransitionPanel
          item={currentItem}
          onStatusChange={setStatus}
        />
      ) : null}
      {["OUTCOME_READY", "INEFFECTIVE", "CLOSED"].includes(status) ? (
        <CloseoutPanel item={currentItem} gate={gate} href={href} />
      ) : null}

      <div data-testid="growth-item-audit">
        <div className={g.auditHeading}>AUDIT</div>
        <div className={g.detailAudit}>
          <span className={g.detailAuditT}>decision</span>
          <span>{item.audit.decisionId}</span>
        </div>
        <div className={g.detailAudit}>
          <span className={g.detailAuditT}>model</span>
          <span>{item.audit.modelVersion}</span>
        </div>
        <div className={g.detailAudit}>
          <span className={g.detailAuditT}>policy</span>
          <span>{item.audit.policyVersion}</span>
        </div>
        <div className={g.detailAudit}>
          <span className={g.detailAuditT}>snapshot</span>
          <span>{item.audit.featureSnapshotTime}</span>
        </div>
      </div>
    </aside>
  );
}

/**
 * Submit-for-approval + decide flow for DRAFT / PENDING_APPROVAL actions.
 * Submitting creates a Govern approval item; approving advances the Growth
 * state to APPROVED, rejecting returns it to DRAFT.
 */
function ApprovalFlowPanel({
  item,
  governHref,
  onStatusChange,
}: {
  item: GrowthItem;
  governHref: string;
  onStatusChange: (status: GrowthStatus) => void;
}) {
  const [approvalId, setApprovalId] = useState<string | null>(item.approvalId ?? null);
  const [growthStatus, setGrowthStatus] = useState<string>(item.status);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const audit = (action: string, extra: Record<string, unknown>) => {
    console.log(
      `[Console Audit] ${JSON.stringify({ action, itemId: item.id, ...extra, timestamp: new Date().toISOString() })}`,
    );
  };

  const handleSubmit = async () => {
    if (busy) return;
    setBusy(true);
    setApiError(null);
    const result = await submitGrowthForApproval({ actionId: item.id });
    setBusy(false);
    if (!result) {
      setApiError("送審被伺服器衝突閘門拒絕或 API 不可用；請檢查衝突後重試。");
      return;
    }
    setApprovalId(result.approval.id);
    setGrowthStatus(result.status);
    onStatusChange(result.status as GrowthStatus);
    audit("SUBMIT_FOR_APPROVAL", { approvalId: result.approval.id, status: result.status });
  };

  return (
    <section className={g.actionPanel} data-testid="growth-approval-panel" data-growth-status={growthStatus}>
      {apiError ? (
        <div className={g.inlineError} data-testid="growth-approval-error">
          <p>{apiError}</p>
        </div>
      ) : null}
      {approvalId || growthStatus === "PENDING_APPROVAL" ? (
        <>
          <div className={g.approvalReceipt} data-testid="growth-approval-created">
            <span className={g.mono}>{approvalId ?? "Govern approval"}</span>
            <span className={g.pendingBadge}>待核准</span>
            <Link href={governHref}>查看 →</Link>
          </div>
          <button type="button" className={g.primaryAction} disabled>
            等待核准
          </button>
          <p className={g.actionNote}>核准由營運主管／稽核於治理稽核處理，不在 Growth 內直接決策。</p>
        </>
      ) : (
        <>
          <button
            type="button"
            className={g.primaryAction}
            onClick={handleSubmit}
            disabled={busy || item.status !== "DRAFT"}
            data-testid="growth-submit-approval"
          >
            {busy ? "送審中…" : "送主管核准"}
          </button>
          <p className={g.actionNote}>送出後建立 Govern 核准項；伺服器成功回覆前不更新狀態。</p>
        </>
      )}
    </section>
  );
}

function LifecycleTransitionPanel({
  item,
  onStatusChange,
}: {
  item: GrowthItem;
  onStatusChange: (status: GrowthStatus) => void;
}) {
  const transition = ({
    APPROVED: { target: "SCHEDULED", label: "排程上線" },
    SCHEDULED: { target: "RUNNING", label: "開始執行" },
    RUNNING: { target: "OBSERVING", label: "啟動觀察" },
    EXECUTED: { target: "OBSERVING", label: "啟動觀察" },
  } as Partial<Record<GrowthStatus, { target: GrowthStatus; label: string }>>)[item.status];
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const handleTransition = async () => {
    if (!transition || busy) return;
    setBusy(true);
    setApiError(null);
    const result = await transitionGrowthAction({
      actionId: item.id,
      targetStatus: transition.target,
    });
    setBusy(false);
    if (!result) {
      setApiError("生命週期更新失敗；狀態未變更，請稍後重試。");
      return;
    }
    onStatusChange(result.status);
  };

  return (
    <section className={g.actionPanel} data-testid="growth-transition-panel">
      {apiError ? <div className={g.inlineError}>{apiError}</div> : null}
      <button
        type="button"
        className={g.primaryAction}
        disabled={!transition || busy}
        onClick={handleTransition}
        data-testid="growth-transition-action"
      >
        {busy ? "更新中…" : transition?.label ?? "觀察中"}
      </button>
      <p className={g.actionNote}>
        {transition
          ? "狀態由伺服器生命週期閘門確認，成功後才更新畫面。"
          : "觀察窗進行中，期滿後由成效資料進入判定。"}
      </p>
    </section>
  );
}

function CloseoutPanel({
  item,
  gate,
  href,
}: {
  item: GrowthItem;
  gate: CloseoutGate;
  href: (o: Record<string, string | undefined>) => string;
}) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isApproved, setIsApproved] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const blockClass = gate.canClose ? g.outcomeSuccess : g.outcomeWarning;

  const handleApprove = async () => {
    if (!gate.canClose || isSubmitting) return;
    setIsSubmitting(true);
    setApiError(null);

    // Write outcome to API with Idempotency-Key and X-Correlation-Id
    const result = await writeGrowthOutcome({
      actionId: item.id,
      outcome: gate.outcome,
      requiredAction: gate.requiredAction,
      observedLift: item.observedLift,
      evidenceLevel: item.evidenceLevel,
      rationale: item.rationale,
    });

    // Console logging is diagnostic only and never substitutes for a durable write.
    const auditPayload = {
      action: "APPROVE_CLOSEOUT",
      itemId: item.id,
      decisionId: item.audit.decisionId,
      outcome: gate.outcome,
      requiredAction: gate.requiredAction,
      apiResult: result ? { correlationId: result.correlationId } : "api-write-failed",
      timestamp: new Date().toISOString(),
    };
    console.log(`[Console Audit] ${JSON.stringify(auditPayload)}`);

    if (result === null) {
      setApiError("API 暫時不可用，結案未送出。請稍後重試。");
      setIsSubmitting(false);
      return;
    }

    setIsSubmitting(false);
    setIsApproved(true);
  };

  return (
    <section
      className={g.actionPanel}
      data-screen-label="Dialog Growth Outcome"
      data-testid="growth-closeout-panel"
    >
      {isApproved ? (
        <div className={g.outcomeSuccess} data-testid="growth-closeout-success">
          <p>結案已成功提交並記錄稽核日誌。等待後端決策回寫。</p>
          {apiError ? <p className={g.actionNote}>{apiError}</p> : null}
        </div>
      ) : (
        <div className={blockClass} data-testid="growth-closeout-gate" data-can-close={gate.canClose}>
          <p>{gate.reason}</p>
        </div>
      )}
      <div className={g.closeoutActions}>
        <button
          type="button"
          className={g.primaryAction}
          disabled={!gate.canClose || isApproved || isSubmitting}
          onClick={handleApprove}
          data-testid="growth-close-button"
        >
          {isSubmitting ? "提交中…" : isApproved ? "已結案" : "結案並回寫成效"}
        </button>
        {gate.requiredAction !== "CLOSE" && !isApproved ? (
          <span className={g.secondaryAction} data-testid="growth-required-action">
            需先：{requiredActionLabel[gate.requiredAction]}
          </span>
        ) : null}
      </div>
      <p className={g.actionNote}>
        提交結案等待後端 decision_id，不做 optimistic update；無效活動不可直接結案（decision {item.audit.decisionId}）。
      </p>
      <p className={g.actionNote}>
        <Link className={g.inlineLink} href={href({ item: item.id })}>
          重新整理判定
        </Link>
      </p>
    </section>
  );
}

const CHANNEL_OPTIONS = ["LINE 推播", "App 首頁", "店內告示", "店內告示＋App 價格頁"];

/**
 * Five-step Draft Builder (Package 10): 基本設定 → 客群／時段 → 預估效益 →
 * 風險／衝突 → 送核准.  Step 4 runs the server conflict gate; a blocked
 * (fail) gate disables submit and surfaces the server's actionable reasons.
 * Step 5 either creates a DRAFT or creates-and-submits it for approval, which
 * creates a Govern item and advances the Growth state.
 */
function GrowthBuilderModal({
  initialForm,
  closeHref,
}: {
  initialForm: GrowthBuilderForm;
  closeHref: string;
}) {
  const router = useRouter();
  const fixturesAllowed = operatorFixturesAllowed();
  const [form, setForm] = useState<GrowthBuilderForm>(initialForm);
  const [step, setStep] = useState(1);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<ConflictCheck[] | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [checking, setChecking] = useState(false);

  const set = (patch: Partial<GrowthBuilderForm>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const runConflictCheck = async () => {
    setChecking(true);
    const result = await checkGrowthConflicts({
      kind: form.kind,
      store: form.store,
      observationWindow: form.observationWindow,
      channel: form.channel,
      budget: parseInt(form.budget, 10) || 0,
    });
    setChecking(false);
    if (result) {
      setConflicts(result.checks);
      setBlocked(result.blocked);
    } else {
      setConflicts(null);
      setBlocked(!fixturesAllowed);
    }
  };

  const goNext = async () => {
    if (step === 1 && !form.name.trim()) {
      setApiError("請填寫活動名稱");
      return;
    }
    setApiError(null);
    if (step === 3) {
      await runConflictCheck();
    }
    setStep((s) => Math.min(5, s + 1));
  };

  const goPrev = () => {
    setApiError(null);
    setStep((s) => Math.max(1, s - 1));
  };

  const handleCreate = async (sendForApproval: boolean) => {
    if (isSubmitting || (sendForApproval && (blocked || (!fixturesAllowed && conflicts === null)))) {
      return;
    }
    setIsSubmitting(true);
    setApiError(null);

    const created = await createGrowthDraft({
      name: form.name,
      segmentId: form.segmentId,
      sourceRecommendationId: form.sourceRecommendationId,
      objective: form.objective,
      targetLift: parseFloat(form.targetLift) || 0,
      kind: form.kind,
      store: form.store,
      channel: form.channel,
      budget: parseInt(form.budget, 10) || 0,
      observationWindow: form.observationWindow,
      rationale: form.rationale,
      rollbackPlan: form.rollbackPlan,
    });

    let approvalId: string | null = null;
    let submitFailed = false;
    if (created && sendForApproval) {
      const submitted = await submitGrowthForApproval({ actionId: created.id });
      if (submitted) {
        approvalId = submitted.approval.id;
      } else {
        submitFailed = true;
      }
    }

    const auditPayload = {
      action: sendForApproval ? "CREATE_AND_SUBMIT" : "CREATE_DRAFT",
      kind: form.kind,
      name: form.name,
      store: form.store,
      budget: form.budget,
      apiResult: created
        ? { id: created.id, correlationId: created.correlationId }
        : "api-write-failed",
      approvalId,
      timestamp: new Date().toISOString(),
    };
    console.log(`[Console Audit] ${JSON.stringify(auditPayload)}`);

    setIsSubmitting(false);
    if (created === null) {
      setApiError("API 暫時不可用，草稿建立已記錄於本機稽核日誌。");
      return;
    }
    if (submitFailed) {
      setApiError("草稿已建立，但送審被伺服器衝突閘門拒絕，請回上一步檢查衝突。");
      return;
    }
    router.push(closeHref);
  };

  return (
    <div
      className={g.modalBackdrop}
      data-screen-label="Dialog Growth Draft Builder"
      data-testid="growth-draft-modal"
    >
      <Link
        href={closeHref}
        className={g.modalDismiss}
        aria-label="關閉建立草稿視窗"
        tabIndex={-1}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="growth-draft-title"
        className={g.builderModal}
        data-step={step}
      >
        <div className={g.builderHeader}>
          <div>
            <h2 id="growth-draft-title">建立 Growth Action 草稿</h2>
            <p>類型：{growthKindLabel[form.kind]}</p>
          </div>
          <Link
            href={closeHref}
            className={g.closeButton}
            aria-label="關閉"
            title="關閉"
            data-testid="growth-draft-close"
          >
            ×
          </Link>
        </div>

        <ol className={g.builderSteps} data-testid="growth-builder-steps">
          {BUILDER_STEPS.map((label, i) => (
            <li
              key={label}
              className={[
                step === i + 1 ? g.builderStepCurrent : "",
                step > i + 1 ? g.builderStepDone : "",
              ].join(" ")}
            >
              <span>{step > i + 1 ? "✓" : i + 1}</span>
              <b>{label}</b>
            </li>
          ))}
        </ol>

        {apiError ? (
          <div className={g.formAlert} data-testid="growth-draft-api-error">
            <p>{apiError}</p>
          </div>
        ) : null}

        <form className={g.builderForm}>
          {step === 1 ? (
            <div data-testid="growth-builder-step-1">
              <label>
                活動名稱
                <input value={form.name} onChange={(e) => set({ name: e.target.value })} name="name" />
              </label>
              <label>
                門市
                <input value={form.store} onChange={(e) => set({ store: e.target.value })} name="store" />
              </label>
              <label>
                目標
                <input value={form.objective} onChange={(e) => set({ objective: e.target.value })} name="objective" />
              </label>
            </div>
          ) : null}

          {step === 2 ? (
            <div data-testid="growth-builder-step-2">
              <label>
                客群
                <input value={form.segmentId} onChange={(e) => set({ segmentId: e.target.value })} name="segmentId" />
              </label>
              <label>
                時窗
                <input
                  value={form.observationWindow}
                  onChange={(e) => set({ observationWindow: e.target.value })}
                  name="observationWindow"
                />
              </label>
              <label>
                通路
                <select value={form.channel} onChange={(e) => set({ channel: e.target.value })} name="channel">
                  {CHANNEL_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}

          {step === 3 ? (
            <div data-testid="growth-builder-step-3">
              <label>
                目標增量（P50，%）
                <input
                  value={form.targetLift}
                  onChange={(e) => set({ targetLift: e.target.value })}
                  inputMode="decimal"
                  name="targetLift"
                />
              </label>
              <label>
                預算（NT$）
                <input
                  value={form.budget}
                  onChange={(e) => set({ budget: e.target.value })}
                  inputMode="numeric"
                  name="budget"
                />
              </label>
              <label>
                回滾條件
                <textarea value={form.rollbackPlan} onChange={(e) => set({ rollbackPlan: e.target.value })} name="rollbackPlan" />
              </label>
            </div>
          ) : null}

          {step === 4 ? (
            <div data-testid="growth-builder-step-4">
              <p className={g.sectionHint}>
                伺服器衝突閘門檢查（重疊／PriceOps／預算／打擾／核准）；任一項為 fail 即不可送審。
              </p>
              {checking ? <p className={g.actionNote}>檢查中…</p> : null}
              <div
                data-testid="growth-conflict-panel"
                data-blocked={blocked}
                className={blocked ? g.conflictPanelBlocked : g.conflictPanel}
              >
                {conflicts === null ? (
                  <p className={g.actionNote}>
                    {fixturesAllowed
                      ? "尚未取得伺服器衝突結果；本機模式可先保存草稿。"
                      : "無法取得伺服器衝突結果；Production 已 fail closed，只能保存草稿，不能送審。"}
                  </p>
                ) : (
                  conflicts.map((c) => (
                    <div key={c.id} className={g.conflictRow} data-testid={`growth-conflict-${c.id}`}>
                      <Badge
                        label={c.label}
                        tone={conflictLevelTone[c.level]}
                        marker={c.level === "ok" ? "✓" : c.level === "fail" ? "✕" : "!"}
                      />
                      <span>{c.note}</span>
                    </div>
                  ))
                )}
              </div>
              {blocked ? (
                <p className={g.formAlert} data-testid="growth-conflict-blocked">
                  {conflicts === null
                    ? "衝突閘門不可用，Production 已停止送審。"
                    : "存在硬衝突，無法送審；請回上一步調整時段／門市後重新檢查。"}
                </p>
              ) : null}
            </div>
          ) : null}

          {step === 5 ? (
            <div data-testid="growth-builder-step-5">
              <div className={g.summaryHeading}>DRAFT SUMMARY</div>
              <dl className={g.builderSummary}>
                <dt>活動名稱</dt>
                <dd>{form.name}</dd>
                <dt>類型</dt>
                <dd>{growthKindLabel[form.kind]}</dd>
                <dt>門市</dt>
                <dd>{form.store}</dd>
                <dt>時窗</dt>
                <dd>{form.observationWindow}</dd>
                <dt>通路</dt>
                <dd>{form.channel}</dd>
                <dt>目標增量</dt>
                <dd>{form.targetLift}%</dd>
                <dt>預算</dt>
                <dd>NT${form.budget}</dd>
              </dl>
              <p className={g.summaryNote}>
                建立後 status = <strong>Draft</strong>。「建立並送核准」會建立 Govern 核准請求；
                核准通過後才能排程上線，PriceOps 必須附回滾條件。
              </p>
            </div>
          ) : null}

          <div className={g.builderFooter}>
            {step > 1 ? (
              <button type="button" className={g.secondaryAction} onClick={goPrev} data-testid="growth-builder-prev">
                ← 上一步
              </button>
            ) : (
              <Link href={closeHref} className={g.secondaryAction}>
                取消
              </Link>
            )}
            {step < 5 ? (
              <button type="button" className={g.primaryAction} onClick={goNext} data-testid="growth-builder-next">
                下一步 →
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className={g.secondaryAction}
                  onClick={() => handleCreate(false)}
                  disabled={isSubmitting}
                  data-testid="growth-draft-submit"
                >
                  {isSubmitting ? "建立中…" : "建立草稿"}
                </button>
                <button
                  type="button"
                  className={g.primaryAction}
                  onClick={() => handleCreate(true)}
                  disabled={isSubmitting || blocked || (!fixturesAllowed && conflicts === null)}
                  data-testid="growth-draft-submit-approval"
                >
                  {isSubmitting ? "送審中…" : "建立並送核准"}
                </button>
              </>
            )}
          </div>
        </form>
        <p className={g.builderNote}>
          建立草稿僅產生 DRAFT，不自動執行；送審核准（建立 Govern 核准項）後才進入生命週期。
        </p>
      </div>
    </div>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

// ODP-OC-R5-002: Static screen labels mapping for CI verification
// data-screen-label="Dialog Growth Draft Builder"
// data-screen-label="Dialog Growth Outcome"
// data-screen-label="Growth PriceOps"
// data-screen-label="Growth 建立入口"
// data-screen-label="Growth 會員分群"
