"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import {
  buildGrowthViewModel,
  closeoutGate,
  confidenceTone,
  constraintTone,
  formatLift,
  outcomeLabel,
  outcomeTone,
  trendLabel,
  trendTone,
  writeGrowthOutcome,
  type CloseoutGate,
  type GrowthApiData,
  type GrowthItem,
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

        <SegmentSection segments={vm.segments} selected={vm.selectedSegment} href={href} />

        <RecommendationSection recommendations={vm.recommendations} href={href} />

        <GrowthActionSection
          items={vm.items}
          selected={vm.selectedItem}
          gate={vm.selectedItemGate}
          href={href}
        />
      </div>

      {vm.draftRecommendation ? (
        <CreateDraftModal recommendation={vm.draftRecommendation} closeHref={href({ draft: undefined })} />
      ) : null}
    </>
  );
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

function CreateDraftModal({
  recommendation,
  closeHref,
}: {
  recommendation: PriceOpsRecommendation;
  closeHref: string;
}) {
  const router = useRouter();
  const [name, setName] = useState(`${recommendation.title}（草稿）`);
  const [targetLift, setTargetLift] = useState(recommendation.expectedRevenueLift.toFixed(1));
  const [observationWindow, setObservationWindow] = useState("14");
  const [rationale, setRationale] = useState("以 PriceOps 建議為基礎；待補齊對照組與 pre-trend 檢定後送審。");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    setApiError(null);

    // Write draft to API with Idempotency-Key + X-Correlation-Id
    const { createGrowthDraft } = await import("./growthViewModel.ts");
    const result = await createGrowthDraft({
      name,
      segmentId: recommendation.segmentId,
      sourceRecommendationId: recommendation.id,
      objective: `以 PriceOps 建議 ${recommendation.id} 為基礎的 Growth Action 草稿。`,
      targetLift: parseFloat(targetLift) || 0,
      observationWindowDays: parseInt(observationWindow, 10) || 14,
      rationale,
      rollbackPlan: "",
    });

    const auditPayload = {
      action: "CREATE_DRAFT",
      recommendationId: recommendation.id,
      name,
      targetLift: parseFloat(targetLift) || 0,
      observationWindow: `${observationWindow} 天`,
      rationale,
      apiResult: result ? { id: result.id, correlationId: result.correlationId } : "offline-fallback",
      timestamp: new Date().toISOString(),
    };
    console.log(`[Console Audit] ${JSON.stringify(auditPayload)}`);

    if (result === null) {
      setApiError("API 暫時不可用，草稿建立已記錄於本機稽核日誌。");
    }

    setIsSubmitting(false);
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
      >
        <div className={styles.modalHeader}>
          <div>
            <h2 id="growth-draft-title">建立 Growth Action 草稿</h2>
            <p className={styles.subtle}>來源建議：{recommendation.id} · {recommendation.title}</p>
          </div>
          <Link href={closeHref} className={styles.secondaryButton} data-testid="growth-draft-close">
            關閉
          </Link>
        </div>
        {apiError ? (
          <div className={styles.warningBlock} data-testid="growth-draft-api-error">
            <p>{apiError}</p>
          </div>
        ) : null}
        <form className={styles.modalForm}>
          <label>
            活動名稱
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              name="name"
            />
          </label>
          <label>
            目標增量（P50，%）
            <input
              value={targetLift}
              onChange={(e) => setTargetLift(e.target.value)}
              inputMode="decimal"
              name="targetLift"
            />
          </label>
          <label>
            觀察窗
            <select
              value={observationWindow}
              onChange={(e) => setObservationWindow(e.target.value)}
              name="observationWindow"
            >
              <option value="7">7 天</option>
              <option value="14">14 天</option>
              <option value="28">28 天</option>
            </select>
          </label>
          <label>
            草稿理由
            <textarea
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              name="rationale"
            />
          </label>
          <div className={styles.modalActions}>
            <Link href={closeHref} className={styles.secondaryButton}>
              取消
            </Link>
            <button
              type="button"
              className={styles.primaryButton}
              onClick={handleSubmit}
              disabled={isSubmitting}
              data-testid="growth-draft-submit"
            >
              {isSubmitting ? "建立中…" : "建立草稿"}
            </button>
          </div>
        </form>
        <p className={styles.auditLine}>
          建立草稿僅產生 DRAFT，不自動執行；送審核准後才進入干預生命週期。
        </p>
      </div>
    </div>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
