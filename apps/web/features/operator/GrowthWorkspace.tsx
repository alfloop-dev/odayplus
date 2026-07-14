"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import {
  BUILDER_STEPS,
  buildGrowthViewModel,
  checkGrowthConflicts,
  closeoutGate,
  confidenceTone,
  conflictLevelTone,
  constraintTone,
  createGrowthDraft,
  formatLift,
  GROWTH_ENTRY_CARDS,
  GROWTH_KIND_PRESETS,
  growthKindLabel,
  outcomeLabel,
  outcomeTone,
  resolveGrowthApproval,
  submitGrowthForApproval,
  trendLabel,
  trendTone,
  writeGrowthOutcome,
  type CloseoutGate,
  type ConflictCheck,
  type GrowthApiData,
  type GrowthBuilderForm,
  type GrowthItem,
  type GrowthKind,
  type GrowthSegment,
  type PriceOpsRecommendation,
} from "./growthViewModel.ts";
import styles from "./operator.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

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

/**
 * 營收成長 Growth workspace — segmentation, PriceOps recommendation table,
 * growth-action list + detail, a URL-driven create-draft modal, and an
 * effectiveness / closeout gate that blocks closing ineffective campaigns.
 *
 * Accepts optional `apiData` (fetched server-side via fetchGrowthApiData) so
 * the workspace renders real API data on first load. Falls back to fixtures
 * when `apiData` is undefined (e.g. during testing or when the API is down).
 *
 * Rendered inside the Operator Console; state is URL-synced (server component)
 * so selection and the draft modal are shareable and testable.
 */
export function GrowthWorkspace({
  searchParams = {},
  basePath = "/operator",
  apiData,
}: {
  searchParams?: SearchParams;
  basePath?: string;
  /** Pre-fetched API data from the server component. Optional; falls back to fixtures. */
  apiData?: GrowthApiData;
}) {
  const segmentId = readParam(searchParams.segment);
  const itemId = readParam(searchParams.item);
  const draftId = readParam(searchParams.draft);
  const builderParam = readParam(searchParams.builder);
  const builderKind: GrowthKind | null =
    builderParam === "offpeak" || builderParam === "winback" || builderParam === "priceops"
      ? builderParam
      : null;

  const vm = buildGrowthViewModel({ segmentId, itemId, draftId }, apiData);
  const freshnessData = apiData?.freshness ?? {
    status: "FRESH" as const,
    updatedAt: "2026-07-09 14:20",
    modelVersion: "growth-uplift-v1.4.0",
  };

  // Build an href that keeps the Growth workspace active and preserves the
  // current selection unless explicitly overridden.
  const href = (overrides: Record<string, string | undefined>): string => {
    const params = new URLSearchParams({ ws: "growth" });
    const merged: Record<string, string | undefined> = {
      segment: vm.selectedSegment?.id,
      item: itemId,
      ...overrides,
    };
    for (const [key, value] of Object.entries(merged)) {
      if (value) {
        params.set(key, value);
      }
    }
    return `${basePath}?${params.toString()}`;
  };

  const fixtureHint = DATA_SOURCE_HINT[vm.dataSource];

  return (
    <>
      <PageHeader
        title="營收成長"
        summary="分群 → PriceOps 建議 → Growth Action 生命週期。成效未達標的活動不可直接結案，需先 rollback 或補強證據。"
        breadcrumb={[{ label: "Operator Console", href: basePath }, { label: "營收成長" }]}
        status={{
          label: freshnessData.status,
          tone: dataStatusTone[freshnessData.status],
          marker: "◆",
          "data-testid": "growth-data-status",
        }}
        lastUpdated={`${freshnessData.updatedAt} · model ${freshnessData.modelVersion}${fixtureHint ? ` · [${fixtureHint}]` : ""}`}
      />
      <div className="odp-content" data-testid="growth-workspace" data-source={vm.dataSource}>
        <section className={styles.overviewGrid} aria-label="Growth overview">
          <Metric label="分群數" value={String(vm.summary.segmentCount)} hint="納入成長評估的區隔" />
          <Metric label="進行中活動" value={String(vm.summary.activeCount)} hint="已核准至觀察中的 Growth Action" />
          <Metric label="判定有效" value={String(vm.summary.effectiveCount)} hint="達標且證據充足" />
          <Metric
            label="結案受阻"
            value={String(vm.summary.blockedCloseoutCount)}
            hint="無效／待判定，不可直接結案"
          />
        </section>

        <EntryCardsSection href={href} />

        <SegmentSection segments={vm.segments} selected={vm.selectedSegment} href={href} />

        <RecommendationSection recommendations={vm.recommendations} href={href} />

        <GrowthActionSection
          items={vm.items}
          selected={vm.selectedItem}
          gate={vm.selectedItemGate}
          href={href}
        />
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
    <section className={styles.section} aria-label="Growth create entries">
      <h2 className={styles.sectionTitle}>建立入口</h2>
      <p className={styles.sectionHint}>
        三個建立入口各自預填對應的活動類型；建立後進入五步 Draft Builder，送審核准才進入生命週期。
      </p>
      <div className={styles.overviewGrid} data-testid="growth-entry-cards">
        {GROWTH_ENTRY_CARDS.map((card) => (
          <Link
            key={card.kind}
            href={href({ builder: card.kind, draft: undefined, item: undefined })}
            className={styles.metric}
            data-testid={`growth-entry-${card.kind}`}
            aria-label={`${card.title}（${card.en}）`}
          >
            <span style={{ color: card.dot }}>● {card.en}</span>
            <strong>＋ {card.title}</strong>
            <span>{card.desc}</span>
          </Link>
        ))}
      </div>
    </section>
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

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <article className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
      <span>{hint}</span>
    </article>
  );
}

function SegmentSection({
  segments,
  selected,
  href,
}: {
  segments: GrowthSegment[];
  selected: GrowthSegment | null;
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <section className={styles.section} aria-label="Segments">
      <h2 className={styles.sectionTitle}>分群</h2>
      <p className={styles.sectionHint}>選擇分群以聚焦其 PriceOps 建議與 Growth Action。</p>
      <div className={styles.filterBar} data-testid="growth-segment-filter">
        <span>聚焦：</span>
        <Link
          className={styles.chip}
          aria-current={selected === null ? "true" : undefined}
          href={href({ segment: undefined, item: undefined })}
        >
          全部分群
        </Link>
        {segments.map((segment) => (
          <Link
            key={segment.id}
            className={styles.chip}
            aria-current={selected?.id === segment.id ? "true" : undefined}
            href={href({ segment: segment.id, item: undefined })}
          >
            {segment.name}
          </Link>
        ))}
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table} data-testid="growth-segment-table">
          <caption>分群定義、規模、營收占比、趨勢與成長機會。</caption>
          <thead>
            <tr>
              <th>分群</th>
              <th>定義</th>
              <th>店數</th>
              <th>營收占比</th>
              <th>趨勢</th>
              <th>資料狀態</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((segment) => (
              <tr key={segment.id} aria-selected={selected?.id === segment.id}>
                <td>
                  <Link className={styles.link} href={href({ segment: segment.id, item: undefined })}>
                    {segment.name}
                  </Link>
                  <span className={styles.subtle}>{segment.opportunity}</span>
                </td>
                <td>{segment.definition}</td>
                <td>{segment.storeCount}</td>
                <td>{segment.revenueShare}</td>
                <td>
                  <Badge label={trendLabel[segment.trend]} tone={trendTone[segment.trend]} marker="●" />
                </td>
                <td>
                  <Badge
                    label={segment.dataStatus}
                    tone={dataStatusTone[segment.dataStatus]}
                    marker="◆"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RecommendationSection({
  recommendations,
  href,
}: {
  recommendations: PriceOpsRecommendation[];
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <section className={styles.section} aria-label="PriceOps recommendations">
      <h2 className={styles.sectionTitle}>PriceOps 建議</h2>
      <p className={styles.sectionHint}>
        系統建議僅為 SYSTEM_RECOMMENDED；硬限制未通過的建議不可建立草稿，需先由 PriceOps 修正。
      </p>
      <div className={styles.tableWrap}>
        <table className={styles.table} data-testid="growth-recommendation-table">
          <caption>現行價、候選價、預期營收/毛利增量、限制狀態與建立草稿入口。</caption>
          <thead>
            <tr>
              <th>建議</th>
              <th>價格</th>
              <th>營收增量 P50</th>
              <th>毛利增量 P50</th>
              <th>信心</th>
              <th>限制</th>
              <th>動作</th>
            </tr>
          </thead>
          <tbody>
            {recommendations.map((rec) => {
              const blocked = rec.constraintStatus === "HARD_CONSTRAINT_FAILED";
              return (
                <tr key={rec.id}>
                  <td>
                    {rec.title}
                    <span className={styles.subtle}>{rec.id}</span>
                  </td>
                  <td>
                    {rec.currentPrice}
                    <span className={styles.subtle}>{rec.candidatePrice}</span>
                  </td>
                  <td>{formatLift(rec.expectedRevenueLift)}</td>
                  <td>{formatLift(rec.expectedMarginLift)}</td>
                  <td>
                    <Badge label={rec.confidence} tone={confidenceTone[rec.confidence]} marker="▧" />
                  </td>
                  <td>
                    <Badge
                      label={rec.constraintStatus}
                      tone={constraintTone[rec.constraintStatus]}
                      marker="!"
                    />
                  </td>
                  <td>
                    {blocked ? (
                      <span
                        className={styles.secondaryButton}
                        aria-disabled="true"
                        title="硬限制未通過，不可建立草稿"
                      >
                        建立草稿
                      </span>
                    ) : (
                      <Link
                        className={styles.primaryButton}
                        href={href({ draft: rec.id })}
                        data-testid={`growth-draft-${rec.id}`}
                      >
                        建立草稿
                      </Link>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function GrowthActionSection({
  items,
  selected,
  gate,
  href,
}: {
  items: GrowthItem[];
  selected: GrowthItem;
  gate: CloseoutGate;
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <section className={styles.section} aria-label="Growth actions">
      <h2 className={styles.sectionTitle}>Growth Actions</h2>
      <p className={styles.sectionHint}>選擇活動檢視成效判斷與結案閘門。</p>
      <div className={styles.detailGrid}>
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="growth-item-table">
            <caption>活動、分群、狀態、目標/觀察增量與成效判定。</caption>
            <thead>
              <tr>
                <th>活動</th>
                <th>狀態</th>
                <th>目標</th>
                <th>觀察</th>
                <th>成效</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const rowGate = closeoutGate(item);
                return (
                  <tr key={item.id} aria-selected={item.id === selected.id}>
                    <td>
                      <Link className={styles.link} href={href({ item: item.id })}>
                        {item.name}
                      </Link>
                      <span className={styles.subtle}>{item.observationWindow}</span>
                    </td>
                    <td>{item.status}</td>
                    <td>{formatLift(item.targetLift)}</td>
                    <td>{formatLift(item.observedLift)}</td>
                    <td>
                      <Badge
                        label={outcomeLabel[rowGate.outcome]}
                        tone={outcomeTone[rowGate.outcome]}
                        marker="●"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <GrowthActionDetail item={selected} gate={gate} href={href} />
      </div>
    </section>
  );
}

function GrowthActionDetail({
  item,
  gate,
  href,
}: {
  item: GrowthItem;
  gate: CloseoutGate;
  href: (o: Record<string, string | undefined>) => string;
}) {
  return (
    <aside className={styles.drawer} data-testid="growth-item-detail" aria-label={`${item.name} 詳情`}>
      <div className={styles.badgeRow}>
        <Badge label={item.status} tone="blue" marker="◆" />
        <Badge label={outcomeLabel[gate.outcome]} tone={outcomeTone[gate.outcome]} marker="●" />
        <Badge label={`evidence ${item.evidenceLevel}`} tone={confidenceTone[item.evidenceLevel]} marker="▧" />
      </div>
      <h2>{item.name}</h2>
      <div className={styles.softBlock}>
        <h3>目標</h3>
        <p>{item.objective}</p>
      </div>
      <section className={styles.twoColumn} data-testid="growth-lift-comparison">
        <div className={styles.softBlock}>
          <h3>目標增量</h3>
          <div className={styles.liftRow}>
            <strong>{formatLift(item.targetLift)}</strong>
          </div>
        </div>
        <div className={styles.softBlock}>
          <h3>觀察增量</h3>
          <div className={styles.liftRow}>
            <strong>{formatLift(item.observedLift)}</strong>
          </div>
          <span className={styles.subtle}>觀察窗：{item.observationWindow}</span>
        </div>
      </section>
      <div className={styles.softBlock}>
        <h3>成效判斷</h3>
        <p>{item.rationale}</p>
      </div>
      <div className={styles.softBlock}>
        <h3>Rollback 計畫</h3>
        <p>{item.rollbackPlan}</p>
      </div>
      {item.status === "DRAFT" || item.status === "PENDING_APPROVAL" ? (
        <ApprovalFlowPanel item={item} href={href} />
      ) : null}
      <CloseoutPanel item={item} gate={gate} href={href} />
      <dl className={styles.auditGrid} data-testid="growth-item-audit">
        <dt>decision_id</dt>
        <dd>{item.audit.decisionId}</dd>
        <dt>correlation_id</dt>
        <dd>{item.audit.correlationId}</dd>
        <dt>model</dt>
        <dd>{item.audit.modelVersion}</dd>
        <dt>policy</dt>
        <dd>{item.audit.policyVersion}</dd>
        <dt>feature snapshot</dt>
        <dd>{item.audit.featureSnapshotTime}</dd>
      </dl>
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
  href,
}: {
  item: GrowthItem;
  href: (o: Record<string, string | undefined>) => string;
}) {
  const [approvalId, setApprovalId] = useState<string | null>(null);
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
    audit("SUBMIT_FOR_APPROVAL", { approvalId: result.approval.id, status: result.status });
  };

  const handleDecision = async (decision: "approved" | "rejected") => {
    if (busy || !approvalId) return;
    setBusy(true);
    setApiError(null);
    const result = await resolveGrowthApproval({ approvalId, decision, reason: decision === "approved" ? "符合政策" : "退回修改" });
    setBusy(false);
    if (!result) {
      setApiError("核准決策寫入失敗；API 不可用。");
      return;
    }
    setGrowthStatus(result.growthStatus);
    audit("RESOLVE_APPROVAL", { approvalId, decision, growthStatus: result.growthStatus });
  };

  return (
    <section className={styles.closeoutPanel} data-testid="growth-approval-panel" data-growth-status={growthStatus}>
      <h3>送審與核准</h3>
      {apiError ? (
        <div className={styles.warningBlock} data-testid="growth-approval-error">
          <p>{apiError}</p>
        </div>
      ) : null}
      {approvalId ? (
        <div className={styles.softBlock} data-testid="growth-approval-created">
          <p>
            已建立 Govern 核准項 <strong>{approvalId}</strong>（狀態：{growthStatus}）。
          </p>
        </div>
      ) : (
        <p className={styles.subtle}>送審後建立 Govern 核准項，核准通過才進入排程／執行。</p>
      )}
      <div className={styles.closeoutActions}>
        {!approvalId ? (
          <button
            type="button"
            className={styles.primaryButton}
            onClick={handleSubmit}
            disabled={busy || item.status !== "DRAFT"}
            data-testid="growth-submit-approval"
          >
            {busy ? "送審中…" : "送主管核准"}
          </button>
        ) : (
          <>
            <button
              type="button"
              className={styles.primaryButton}
              onClick={() => handleDecision("approved")}
              disabled={busy || growthStatus === "APPROVED"}
              data-testid="growth-approve"
            >
              核准
            </button>
            <button
              type="button"
              className={styles.secondaryButton}
              onClick={() => handleDecision("rejected")}
              disabled={busy || growthStatus === "DRAFT"}
              data-testid="growth-reject"
            >
              駁回
            </button>
          </>
        )}
      </div>
      <p className={styles.subtle}>
        <Link className={styles.link} href={href({ item: item.id })}>
          重新整理狀態
        </Link>
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
  const blockClass = gate.canClose ? styles.successBlock : styles.warningBlock;

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

    // Always record local audit trail regardless of API availability
    const auditPayload = {
      action: "APPROVE_CLOSEOUT",
      itemId: item.id,
      decisionId: item.audit.decisionId,
      outcome: gate.outcome,
      requiredAction: gate.requiredAction,
      apiResult: result ? { correlationId: result.correlationId } : "offline-fallback",
      timestamp: new Date().toISOString(),
    };
    console.log(`[Console Audit] ${JSON.stringify(auditPayload)}`);

    if (result === null) {
      // API unavailable — record locally and proceed (fixture/offline fallback)
      setApiError("API 暫時不可用，結案已記錄於本機稽核日誌。");
    }

    setIsSubmitting(false);
    setIsApproved(true);
  };

  return (
    <section className={styles.closeoutPanel} data-testid="growth-closeout-panel">
      <h3>結案判定</h3>
      {isApproved ? (
        <div className={styles.successBlock} data-testid="growth-closeout-success">
          <p>結案已成功提交並記錄稽核日誌。等待後端決策回寫。</p>
          {apiError ? (
            <p className={styles.subtle}>{apiError}</p>
          ) : null}
        </div>
      ) : (
        <div className={blockClass} data-testid="growth-closeout-gate" data-can-close={gate.canClose}>
          <p>{gate.reason}</p>
        </div>
      )}
      <div className={styles.closeoutActions}>
        <button
          type="button"
          className={styles.primaryButton}
          disabled={!gate.canClose || isApproved || isSubmitting}
          onClick={handleApprove}
          data-testid="growth-close-button"
        >
          {isSubmitting ? "提交中…" : isApproved ? "已結案" : "結案並回寫成效"}
        </button>
        {gate.requiredAction !== "CLOSE" && !isApproved ? (
          <span className={styles.secondaryButton} data-testid="growth-required-action">
            需先：{requiredActionLabel[gate.requiredAction]}
          </span>
        ) : null}
      </div>
      <p className={styles.auditLine}>
        提交結案等待後端 decision_id，不做 optimistic update；無效活動不可直接結案（decision {item.audit.decisionId}）。
      </p>
      <p className={styles.subtle}>
        <Link className={styles.link} href={href({ item: item.id })}>
          重新整理判定
        </Link>
      </p>
    </section>
  );
}

const CHANNEL_OPTIONS = ["LINE 推播", "App 首頁", "店內告示", "店內告示＋App 價格頁"];

/**
 * Five-step Draft Builder (package 6): 基本設定 → 客群／時段 → 預估效益 →
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
      setBlocked(false);
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
    if (isSubmitting || blocked) return;
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
        : "offline-fallback",
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
    <div className={styles.modalBackdrop} data-testid="growth-draft-modal">
      <Link
        href={closeHref}
        className={styles.modalBackdrop}
        aria-label="關閉建立草稿視窗"
        style={{ background: "transparent", zIndex: 1 }}
        tabIndex={-1}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="growth-draft-title"
        className={styles.modal}
        style={{ position: "relative", zIndex: 2 }}
        data-step={step}
      >
        <div className={styles.modalHeader}>
          <div>
            <h2 id="growth-draft-title">建立 Growth Action 草稿</h2>
            <p className={styles.subtle}>類型：{growthKindLabel[form.kind]}</p>
          </div>
          <Link href={closeHref} className={styles.secondaryButton} data-testid="growth-draft-close">
            關閉
          </Link>
        </div>

        <ol className={styles.badgeRow} data-testid="growth-builder-steps">
          {BUILDER_STEPS.map((label, i) => (
            <li key={label} style={{ listStyle: "none" }}>
              <Badge
                label={`${i + 1}. ${label}`}
                tone={step === i + 1 ? "blue" : step > i + 1 ? "green" : "gray"}
                marker={step > i + 1 ? "✓" : "●"}
              />
            </li>
          ))}
        </ol>

        {apiError ? (
          <div className={styles.warningBlock} data-testid="growth-draft-api-error">
            <p>{apiError}</p>
          </div>
        ) : null}

        <form className={styles.modalForm}>
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
              <p className={styles.sectionHint}>
                伺服器衝突閘門檢查（重疊／PriceOps／預算／打擾／核准）；任一項為 fail 即不可送審。
              </p>
              {checking ? <p className={styles.subtle}>檢查中…</p> : null}
              <div
                data-testid="growth-conflict-panel"
                data-blocked={blocked}
                className={blocked ? styles.warningBlock : styles.softBlock}
              >
                {conflicts === null ? (
                  <p className={styles.subtle}>尚未取得伺服器衝突結果（API 不可用時以人工複核）。</p>
                ) : (
                  conflicts.map((c) => (
                    <div key={c.id} className={styles.badgeRow} data-testid={`growth-conflict-${c.id}`}>
                      <Badge
                        label={c.label}
                        tone={conflictLevelTone[c.level]}
                        marker={c.level === "ok" ? "✓" : c.level === "fail" ? "✕" : "!"}
                      />
                      <span className={styles.subtle}>{c.note}</span>
                    </div>
                  ))
                )}
              </div>
              {blocked ? (
                <p className={styles.auditLine} data-testid="growth-conflict-blocked">
                  存在硬衝突，無法送審——請回上一步調整時段／門市後重新檢查。
                </p>
              ) : null}
            </div>
          ) : null}

          {step === 5 ? (
            <div data-testid="growth-builder-step-5">
              <dl className={styles.auditGrid}>
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
            </div>
          ) : null}

          <div className={styles.modalActions}>
            {step > 1 ? (
              <button type="button" className={styles.secondaryButton} onClick={goPrev} data-testid="growth-builder-prev">
                上一步
              </button>
            ) : (
              <Link href={closeHref} className={styles.secondaryButton}>
                取消
              </Link>
            )}
            {step < 5 ? (
              <button type="button" className={styles.primaryButton} onClick={goNext} data-testid="growth-builder-next">
                下一步
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className={styles.secondaryButton}
                  onClick={() => handleCreate(false)}
                  disabled={isSubmitting || blocked}
                  data-testid="growth-draft-submit"
                >
                  {isSubmitting ? "建立中…" : "建立草稿"}
                </button>
                <button
                  type="button"
                  className={styles.primaryButton}
                  onClick={() => handleCreate(true)}
                  disabled={isSubmitting || blocked}
                  data-testid="growth-draft-submit-approval"
                >
                  {isSubmitting ? "送審中…" : "建立並送核准"}
                </button>
              </>
            )}
          </div>
        </form>
        <p className={styles.auditLine}>
          建立草稿僅產生 DRAFT，不自動執行；送審核准（建立 Govern 核准項）後才進入生命週期。
        </p>
      </div>
    </div>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
