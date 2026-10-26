"use client";

import { useEffect, useState, type ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { SiteScoreLabRow } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type NetworkTargetFormat,
  type ScoreCard,
  type ScoringCandidate,
  type ScoreExecutionReceipt,
} from "./networkScoringTypes";

// SiteScorePanel owns the "SiteScore Lab" tab. It renders the R4 scorecard for
// every scored candidate — score, GO/WAIT/REJECT, M1/M3/M6/M12 revenue path,
// P10/P50/P90 band, six risk sub-scores, support reasons, primary risks, and
// the rec-specific conditions (WAIT 通過條件) / reject reasons. Gate-blocked
// candidates surface "缺資料 — 無法評分" instead of a scorecard.

const SUB_SCORE_LABELS: Array<[keyof ScoreCard["subScores"], string]> = [
  ["rentReasonableness", "租金合理性"],
  ["cannibalization", "自家稀釋"],
  ["competition", "競店壓力"],
  ["demand", "需求強度"],
  ["poiFit", "POI 適配"],
  ["access", "可及性／停車"],
];

export function SiteScorePanel({
  busyCandidateId,
  candidates,
  fallbackRows,
  modelVersion,
  onRescore,
  scorecards,
  selectedCandidateId,
  onSelectCandidate,
  selectionError,
  rescoreUnavailableReason,
  executionRequest,
  executionError,
  requireRescoreReason,
  targetFormat,
}: {
  targetFormat?: NetworkTargetFormat | null;
  busyCandidateId?: string | null;
  candidates: ScoringCandidate[];
  fallbackRows: SiteScoreLabRow[];
  modelVersion?: string | null;
  onRescore?: (candidateId: string, reason?: string) => void;
  scorecards: ScoreCard[];
  selectedCandidateId?: string | null;
  onSelectCandidate?: (candidateId: string) => void;
  selectionError?: string | null;
  rescoreUnavailableReason?: string | null;
  executionRequest?: ScoreExecutionReceipt | null;
  executionError?: string | null;
  requireRescoreReason?: boolean;
}) {
  const cards = scorecards.length ? scorecards : fallbackRows.flatMap((row) => {
    const card = fallbackToCard(row);
    return card ? [card] : [];
  });
  const blocked = candidates.filter((candidate) => !candidate.gate.passed);
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [selectedId, setSelectedId] = useState(cards[0]?.id ?? "");
  const matchingCards = cards.filter((card) => card.id === selectedCandidateId);
  const selectedCard = selectionError ? undefined : selectedCandidateId
    ? matchingCards.length === 1 ? matchingCards[0] : undefined
    : cards.find((card) => card.id === selectedId) ?? cards[0];

  useEffect(() => {
    if (cards.length && !cards.some((card) => card.id === selectedId)) {
      setSelectedId(cards[0].id);
    }
  }, [cards, selectedId]);

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network SiteScore Lab"
      data-testid="network-panel-sitescore"
      role="tabpanel"
    >
      <div className={styles.scoreLabToolbar}>
        <div className={styles.scoreLabModes} aria-label="SiteScore view">
          <button aria-pressed={mode === "single"} onClick={() => setMode("single")} type="button">
            單點評分
          </button>
          <button aria-pressed={mode === "batch"} onClick={() => setMode("batch")} type="button">
            批次評分
          </button>
        </div>
        <span className={styles.muted} data-testid="sitescore-model-meta">
          {modelVersion ?? cards[0]?.modelVersion ?? "尚無評分模型資訊"} · 需求／租金／侵蝕／回本四構面
        </span>
      </div>

      {targetFormat ? <StandardFormatAssumptions format={targetFormat} /> : null}

      {executionRequest ? <p role="status" data-testid="sitescore-execution-status">{executionRequest.candidateSiteId} · 評分工作 {executionRequest.status}{executionRequest.status === "SUCCEEDED" ? ` · 報告版本 ${executionRequest.result?.report_version}` : " · 已保存報告仍可查閱"}</p> : null}
      {executionError ? <p role="alert">{executionError}</p> : null}

      {blocked.length ? (
        <div className={styles.complianceBanner} data-testid="sitescore-gate-banner">
          <span>GATE</span>
          {blocked.map((candidate) => (
            <b className={styles.gateIssue} key={candidate.id} data-testid={`sitescore-blocked-${candidate.id}`}>
              {candidate.id} {candidate.title}：缺資料 — 無法評分（{candidate.gate.missing.join("、")}）
            </b>
          ))}
        </div>
      ) : null}

      {cards.length ? (
        mode === "single" ? (
          <div className={styles.scoreLabWorkspace}>
            <aside className={styles.scoreLabPicker} aria-label="選擇候選點">
              {cards.map((card) => (
                <button
                  aria-current={selectedCard?.id === card.id ? "true" : undefined}
                  data-testid={`sitescore-pick-${card.id}`}
                  key={card.id}
                  onClick={() => { setSelectedId(card.id); onSelectCandidate?.(card.id); }}
                  type="button"
                >
                  <span>
                    <strong>{card.title}</strong>
                    <small>{card.id} · {card.zoneLabel}</small>
                  </span>
                  <ToneBadge tone={recommendationTone(card.recommendation)}>
                    {card.recommendation} {card.score}
                  </ToneBadge>
                </button>
              ))}
              {blocked.map((candidate) => (
                <button disabled key={candidate.id} type="button">
                  <span>
                    <strong>{candidate.title}</strong>
                    <small>{candidate.id} · 缺資料</small>
                  </span>
                  <ToneBadge tone="risk">BLOCKED</ToneBadge>
                </button>
              ))}
            </aside>
            <section className={styles.scoreLabReportStack}>
              {selectionError ? <p role="status">{selectionError}</p> : null}
              {selectedCandidateId && !selectedCard ? <p role="status">指定候選點的報告目前無法唯一核對，未選取其他報告。</p> : null}
              {cards.map((card) => (
                <ScoreReport
                  busy={busyCandidateId === card.id}
                  card={card}
                  hidden={selectedCard?.id !== card.id}
                  key={card.id}
                  onRescore={onRescore}
                  rescoreUnavailableReason={rescoreUnavailableReason}
                  requireRescoreReason={requireRescoreReason}
                  retryFailedRequest={Boolean(executionRequest && ["FAILED", "CANCELLED"].includes(executionRequest.status) && executionRequest.candidateSiteId === card.id)}
                />
              ))}
            </section>
          </div>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.dataTable} data-testid="sitescore-batch-table">
              <thead>
                <tr>
                  <th>優先</th>
                  <th>候選點</th>
                  <th>分數</th>
                  <th>建議</th>
                  <th>M12 P50</th>
                  <th>回本期</th>
                  <th>快照</th>
                </tr>
              </thead>
              <tbody>
                {cards
                  .slice()
                  .sort((left, right) => left.score === null ? right.score === null ? 0 : 1
                    : right.score === null ? -1 : right.score - left.score)
                  .map((card, index) => (
                    <tr key={card.id} data-tone={recommendationTone(card.recommendation)}>
                      <td><span className={styles.priorityPill}>{card.score === null ? "—" : `#${index + 1}`}</span></td>
                      <td><strong>{card.title}</strong><small>{card.id}</small></td>
                      <td>{card.score ?? "未提供"}</td>
                      <td><ToneBadge tone={recommendationTone(card.recommendation)}>{card.recommendation}</ToneBadge></td>
                      <td>{card.band.p50}</td>
                      <td>{card.payback}</td>
                      <td>{card.datasetSnapshotId}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <div className={styles.emptyState}>No SiteScore runs</div>
      )}
    </div>
  );
}

// Shared by the existing ready panel and the workspace's canonical empty
// assumptions area. No financial calculation or action qualification lives here.
export function StandardFormatAssumptions({ format }: { format: NetworkTargetFormat }) {
  const assetLabels: Record<NetworkTargetFormat["capital_assets"][number]["asset_kind"], string> = {
    PAYMENT_SYSTEM: "支付系統", KIOSK: "Kiosk", VENDING: "販賣機", CAMERA_SYSTEM: "監視系統", FURNITURE: "家具備品",
  };
  const amount = (value: number | null) => value === null ? "未知" : money(value);
  const measurement = (value: number | null) => value === null ? "未知" : value;
  return (
    <section className={styles.scoreCard} aria-label="目前標準配置（唯讀）" data-testid="sitescore-target-format">
      <div className={styles.filterTitle}>目前標準配置（唯讀） · {format.format_code} · {format.format_version}</div>
      <p className={styles.muted}>{format.format_name}；不是已保存報告的假設，亦不代表可評分或財務核准。</p>
      <dl className={styles.scoreMeta}>
        <div><dt>完整未稅套裝</dt><dd>{money(format.package_total_untaxed)}</dd></div>
        <div><dt>套裝內未分配金額</dt><dd>{money(format.unallocated_package_cost)}（已含於總價，非額外費用；升級／保固／保險分配未知）</dd></div>
        <div><dt>營業時數</dt><dd>{format.daily_operating_hours} 小時／日；週期僅為理論產能依據，不是預測需求或利用率。</dd></div>
        {format.machine_mix.items.map(({ quantity, machine_model: model }) => <div key={model.model_id}>
          <dt>{model.capacity_kg} kg 洗烘一體機</dt>
          <dd>{quantity} 台 × {money(model.unit_capex)}（未稅）；每次 {money(model.base_cycle_price)}／{model.cycle_minutes} 分鐘；
            使用年限 {model.useful_life_months} 月／殘值比 {model.residual_value_ratio}；基準轉數／日 {measurement(model.baseline_turns_per_day)}；
            每次水 L／電 kWh／氣 kg：{measurement(model.water_liters_per_cycle)}／{measurement(model.electricity_kwh_per_cycle)}／{measurement(model.gas_kg_per_cycle)}；
            洗劑 {amount(model.detergent_cost_per_cycle)}／每台月維護 {amount(model.monthly_maintenance_per_unit)}</dd>
        </div>)}
        {format.capital_assets.map((asset) => <div key={asset.asset_id}><dt>{assetLabels[asset.asset_kind]}</dt>
          <dd>{asset.quantity} 件 × {money(asset.unit_capex)}；{asset.useful_life_months} 月／殘值比 {asset.residual_value_ratio}；每月營運成本 {amount(asset.monthly_fixed_operating_cost)}</dd></div>)}
        <div><dt>工程裝修（未稅）</dt><dd>{money(format.fitout_spec.base_fitout_cost)}；{format.fitout_spec.fitout_useful_life_months} 月或較短租期</dd></div>
        <div><dt>財務政策與營運輸入</dt><dd>水電費率、維護、融資、稅、殘值政策、爬坡與季節性：未知。未計算 ROI、折舊或報告 CAPEX。</dd></div>
      </dl>
    </section>
  );
}

function ScoreReport({
  busy,
  card,
  hidden,
  onRescore,
  rescoreUnavailableReason,
  requireRescoreReason,
  retryFailedRequest,
}: {
  busy: boolean;
  card: ScoreCard;
  hidden: boolean;
  onRescore?: (candidateId: string, reason?: string) => void;
  rescoreUnavailableReason?: string | null;
  requireRescoreReason?: boolean;
  retryFailedRequest?: boolean;
}) {
  const [reason, setReason] = useState("");
  const tone = recommendationTone(card.recommendation);
  return (
    <article
      className={`${styles.scoreCard} ${styles.scoreReport}`}
      data-testid={`sitescore-card-${card.id}`}
      data-tone={tone}
      hidden={hidden}
    >
      <header className={styles.scoreCardHead}>
        <div className={styles.scoreIdentity}>
          <span className={card.score === null ? styles.scoreLabel : styles.scoreNumber}>{card.score === null ? "已評分" : <>{card.score}<small>/ 100</small></>}</span>
          <ToneBadge tone={tone}>{card.recommendation}</ToneBadge>
          {card.confidence !== null && card.confidence !== "" ? <span className={styles.confidenceBadge}>信心 {card.confidence}</span> : null}
        </div>
        <div className={styles.scoreReportTitle}>
          <strong>{card.title}</strong>
          <small>{card.id} · {card.zoneLabel}</small>
          <span>{card.modelVersion} · 快照 {card.datasetSnapshotId}{card.generatedAt ? ` · ${card.generatedAt}` : ""}</span>
        </div>
      </header>

      <div className={styles.scoreReportGrid}>
        <section>
          <div className={styles.filterTitle}>月營收路徑（P50）</div>
          <RevenuePath card={card} />
          <div className={styles.bandTiles}>
            <BandTile label="P10 保守" value={card.band.p10} />
            <BandTile label="P50 基準" value={card.band.p50} />
            <BandTile label="P90 樂觀" value={card.band.p90} />
          </div>
        </section>
        <section>
          <dl className={styles.scoreMeta}>
            <div><dt>回本期</dt><dd>{typeof card.payback === "number" ? `${card.payback} 個月` : card.payback || "—"}</dd></div>
            <div><dt>CAPEX 假設</dt><dd>{card.capex ?? "—"}</dd></div>
            <div><dt>租金假設</dt><dd>{card.rentAssumption ?? "—"}</dd></div>
          </dl>
          {card.drivers.length ? <p className={styles.scoreDrivers}>需求驅動：{card.drivers.join("・")}</p> : null}
        </section>
      </div>

      <div>
        <div className={styles.filterTitle}>RISK BREAKDOWN</div>
        <div className={styles.subScoreGrid} aria-label="Risk breakdown">
          {SUB_SCORE_LABELS.map(([key, label]) => (
            <div className={styles.gateRowItem} key={key}>
              <span>{label}</span>
              <small className={styles.muted}>{card.subScores[key] ?? "—"}</small>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.reasonCols}>
        <section>
          <h4>支持原因</h4>
          <ul className={styles.missingList}>{card.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </section>
        <section>
          <h4>主要風險</h4>
          <ul className={styles.missingList}>{card.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul>
        </section>
      </div>

      {card.conditions.length ? (
        <div className={styles.conditionBox} data-tone={tone} data-testid={`sitescore-conditions-${card.id}`}>
          <strong>{card.conditionTitle}</strong>
          <ul className={styles.missingList}>{card.conditions.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}

      {requireRescoreReason ? (
        <div className={styles.reasonInputGroup}>
          <label htmlFor={`sitescore-rescore-reason-${card.id}`}>重新評分原因</label>
          <input id={`sitescore-rescore-reason-${card.id}`} className={styles.inputField} aria-label={`重新評分原因 ${card.id}`} value={reason} onChange={(event) => setReason(event.target.value)} disabled={busy} />
        </div>
      ) : null}
      <button
        className={styles.detailPrimaryButton}
        data-testid={`sitescore-rescore-${card.id}`}
        disabled={busy || !onRescore || Boolean(rescoreUnavailableReason) || Boolean(requireRescoreReason && !reason.trim())}
        onClick={() => { if (!busy && !rescoreUnavailableReason) requireRescoreReason ? onRescore?.(card.id, reason) : onRescore?.(card.id); }}
        type="button"
      >
        {busy ? "評分中…" : retryFailedRequest ? "重試這次評分工作" : "重新評分（Re-run SiteScore）"}
      </button>
      {rescoreUnavailableReason || requireRescoreReason && !reason.trim() ? <p className={styles.muted} data-testid={`sitescore-rescore-qualification-${card.id}`}>{rescoreUnavailableReason ?? "目前工作與再評分資格尚未確認；已保存報告仍可查閱。"}</p> : null}
    </article>
  );
}

function RevenuePath({ card }: { card: ScoreCard }) {
  const values: Array<[string, ScoreCard["revenuePath"]["m1"]]> = [
    ["M1", card.revenuePath.m1],
    ["M3", card.revenuePath.m3],
    ["M6", card.revenuePath.m6],
    ["M12", card.revenuePath.m12],
  ];
  const points = values.map(([label, interval]) => ({ label, interval,
    value: typeof interval === "number" ? interval : interval?.p50 ?? null }));
  const max = Math.max(1, ...points.flatMap(({ value }) => value === null ? [] : [value]));
  return (
    <div className={styles.revenuePath} aria-label="月營收路徑（P50）">
      {points.map(({ label, value, interval }) => (
        <div className={styles.revenueBar} key={label}>
          <i aria-hidden="true">
            {value === null ? null : <b style={{ height: `${Math.max(6, Math.round((value / max) * 100))}%` }} />}
          </i>
          <span>{label}</span>
          <small
            title={interval !== null && typeof interval !== "number"
              ? `${label} P10 ${money(interval.p10)} · P50 ${money(interval.p50)} · P90 ${money(interval.p90)}` : undefined}
            aria-label={interval !== null && typeof interval !== "number"
              ? `${label} P10 ${money(interval.p10)} · P50 ${money(interval.p50)} · P90 ${money(interval.p90)}` : undefined}
          >{value === null ? "—" : typeof interval === "number" ? `NT$${value}K` : money(value)}</small>
        </div>
      ))}
    </div>
  );
}

function money(value: number): string {
  return `NT$${value.toLocaleString("zh-TW", { maximumFractionDigits: 2 })}`;
}

function BandTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className={styles.bandTile}>
      <span>{label}</span>
      <strong>{typeof value === "number" ? money(value) : value}</strong>
    </div>
  );
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}

// Fixture fallback: adapt a viewModel SiteScoreLabRow into a minimal ScoreCard.
function fallbackToCard(row: SiteScoreLabRow): ScoreCard | null {
  if (row.recommendation === null || !row.modelVersion || !row.datasetSnapshotId) {
    return null;
  }
  return {
    id: row.id,
    title: row.title,
    zoneLabel: row.zoneLabel,
    heatZoneId: "",
    score: row.score,
    recommendation: row.recommendation,
    modelVersion: row.modelVersion,
    datasetSnapshotId: row.datasetSnapshotId,
    generatedAt: "",
    confidence: "",
    payback: "",
    revenuePath: { m1: null, m3: null, m6: null, m12: null },
    band: { p10: "—", p50: "—", p90: "—" },
    subScores: {},
    capex: "",
    rentAssumption: "",
    drivers: [],
    reasons: [],
    risks: [],
    conditions: row.missingData,
    conditionTitle: row.missingData.length ? "待補證據" : "",
  };
}
