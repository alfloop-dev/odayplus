"use client";

import { useEffect, useState, type ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { SiteScoreLabRow } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type ScoreCard,
  type ScoringCandidate,
} from "./networkScoringTypes";

// SiteScorePanel owns the "SiteScore Lab" tab (Package 10 / R7 parity).
// It renders the single-candidate scorecard workspace (score, GO/WAIT/REJECT,
// M1/M3/M6/M12 revenue path, P10/P50/P90 band, 6 risk sub-scores, support
// reasons, primary risks, conditions, secondary actions, and mini-map) or the
// dense batch score comparison table.

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
  onScoreAll,
  onToggleCompare,
  onSubmitReview,
  scorecards,
}: {
  busyCandidateId?: string | null;
  candidates: ScoringCandidate[];
  fallbackRows: SiteScoreLabRow[];
  modelVersion?: string;
  onRescore?: (candidateId: string) => void;
  onScoreAll?: (candidateIds: string[]) => void;
  onToggleCompare?: (candidateId: string) => void;
  onSubmitReview?: (candidateId: string) => void;
  scorecards: ScoreCard[];
}) {
  const cards = scorecards.length ? scorecards : fallbackRows.map(fallbackToCard);
  const blocked = candidates.filter((candidate) => !candidate.gate.passed);
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [selectedId, setSelectedId] = useState(cards[0]?.id ?? candidates[0]?.id ?? "");
  const [batchSelection, setBatchSelection] = useState<string[]>(() =>
    cards.map((card) => card.id),
  );
  const eligibleBatchIds = candidates
    .filter((candidate) => candidate.gate.passed && batchSelection.includes(candidate.id))
    .map((candidate) => candidate.id);

  useEffect(() => {
    if (cards.length && !cards.some((card) => card.id === selectedId) && !candidates.some((candidate) => candidate.id === selectedId)) {
      setSelectedId(cards[0].id);
    }
  }, [cards, candidates, selectedId]);

  const selectedCard = cards.find((card) => card.id === selectedId);
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedId);

  function toggleBatchItem(id: string) {
    setBatchSelection((current) =>
      current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    );
  }

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network SiteScore Lab"
      data-testid="network-panel-sitescore"
      role="tabpanel"
    >
      <div className={styles.scoreLabToolbar}>
        <div className={styles.scoreLabModes} aria-label="SiteScore view" role="group">
          <button
            aria-pressed={mode === "single"}
            onClick={() => setMode("single")}
            type="button"
          >
            單點評分
          </button>
          <button
            aria-pressed={mode === "batch"}
            onClick={() => setMode("batch")}
            type="button"
          >
            批次評分
          </button>
        </div>
        <span className={styles.muted} data-testid="sitescore-model-meta">
          {modelVersion ?? "SiteScore v2.3"} · 特徵快照每日 06:00
        </span>
      </div>

      {mode === "single" ? (
        <div className={styles.scoreLabWorkspace}>
          <aside className={styles.scoreLabPicker} aria-label="選擇候選點">
            <div className={styles.scoreLabPickerChoices}>
            {cards.map((card) => {
              const cand = candidates.find((c) => c.id === card.id);
              const isSelected = (selectedCard?.id ?? selectedCandidate?.id) === card.id;
              return (
                <button
                  aria-current={isSelected ? "true" : undefined}
                  data-testid={`sitescore-pick-${card.id}`}
                  key={card.id}
                  onClick={() => setSelectedId(card.id)}
                  type="button"
                >
                  <span>
                    <strong>{card.title}</strong>
                    <small>{card.id} · {card.zoneLabel}</small>
                  </span>
                  <ToneBadge tone={recommendationTone(card.recommendation)}>
                    {cand?.stage === "evaluated" || !cand ? card.recommendation + " " + card.score : card.recommendation}
                  </ToneBadge>
                </button>
              );
            })}
            {blocked.map((candidate) => {
              const isSelected = selectedCandidate?.id === candidate.id && !cards.some((c) => c.id === candidate.id);
              return (
                <button
                  aria-current={isSelected ? "true" : undefined}
                  data-testid={`sitescore-pick-${candidate.id}`}
                  key={candidate.id}
                  onClick={() => setSelectedId(candidate.id)}
                  type="button"
                >
                  <span>
                    <strong>{candidate.title}</strong>
                    <small>{candidate.id} · 缺資料</small>
                  </span>
                  <ToneBadge tone="risk">缺資料</ToneBadge>
                </button>
              );
            })}

            </div>
            <div className={styles.scoreLabMiniMap} data-testid="sitescore-mini-map">
              <div className={styles.miniMapCanvas}>
                <div className={styles.miniMapGrid} />
                <span className={styles.miniMapPin} style={{ left: "50%", top: "42%" }}>
                  <i className={styles.pinSquare} />
                  <span>{selectedCard?.title ?? selectedCandidate?.title ?? "候選點"}</span>
                </span>
                <span className={styles.miniMapStorePin} style={{ left: "28%", top: "68%" }}>
                  <i className={styles.pinCircle} />
                  <span>周邊門市（示意）</span>
                </span>
                <span className={styles.miniMapStorePin} style={{ left: "76%", top: "30%" }}>
                  <i className={styles.pinCircle} />
                  <span>周邊門市（示意）</span>
                </span>
              </div>
              <div className={styles.miniMapFooter}>
                <span>位置示意 · 非實際座標／距離</span>
              </div>
            </div>
          </aside>

          <section className={styles.scoreLabReportStack}>
            {blocked.map((candidate) => (
              <div
                className={styles.scoreLabGateBlockedBox}
                data-testid={`sitescore-blocked-${candidate.id}`}
                hidden={selectedId !== candidate.id}
                key={candidate.id}
              >
                <h4>{candidate.title}</h4>
                <div className={styles.gateAlertBadge}>
                  Gate：缺資料 — {candidate.gate.missing.join("、")}。請先於候選點工作台補齊，執行已停用。
                </div>
                <button className={styles.detailPrimaryButton} disabled type="button">
                  執行 SiteScore
                </button>
              </div>
            ))}
            {cards.map((card) => (
              <ScoreReport
                busy={!!busyCandidateId}
                card={card}
                hidden={selectedCard?.id !== card.id}
                key={card.id}
                onRescore={onRescore}
                onSubmitReview={onSubmitReview}
                onToggleCompare={onToggleCompare}
              />
            ))}
            {!cards.length && !blocked.length ? (
              <div className={styles.emptyState}>No SiteScore runs</div>
            ) : null}
          </section>
        </div>
      ) : (
        <div className={styles.scoreLabBatchWorkspace}>
          <div className={styles.scoreLabBatchSelector}>
            <div className={styles.batchHeaderTitle}>選擇候選點（可評分／已評分）</div>
            <div className={styles.batchRowsList}>
              {candidates.map((candidate) => {
                const checked = batchSelection.includes(candidate.id);
                return (
                  <button
                    className={styles.batchRowItem}
                    key={candidate.id}
                    aria-pressed={checked}
                    disabled={!candidate.gate.passed || !!busyCandidateId}
                    onClick={() => toggleBatchItem(candidate.id)}
                    type="button"
                  >
                    <span className={styles.batchCheckmark} aria-hidden="true">{checked ? "☑" : "☐"}</span>
                    <strong className={styles.batchCandidateName}>{candidate.title}</strong>
                    <ToneBadge tone={recommendationTone(candidate.recommendation)}>
                      {candidate.gate.passed ? candidate.recommendation : "缺資料"}
                    </ToneBadge>
                    <span className={styles.batchRent}>
                      {cards.find((card) => card.id === candidate.id)?.rentAssumption || "—"}
                    </span>
                  </button>
                );
              })}
            </div>
            <button
              className={styles.batchRunButton}
              data-testid="sitescore-batch-run"
              disabled={!!busyCandidateId || !onScoreAll || !eligibleBatchIds.length}
              onClick={() => onScoreAll?.(eligibleBatchIds)}
              type="button"
            >
              {busyCandidateId === "batch" ? "批次評分中…" : "批次執行 SiteScore"}
            </button>
          </div>

          <div className={styles.scoreLabBatchResultsWrap}>
            <div className={styles.batchHeaderTitle}>批次結果 · 依分數排序</div>
            <div className={styles.tableWrap} role="region" aria-label="批次評分結果，可水平捲動" tabIndex={0}>
              <table className={styles.dataTable} data-testid="sitescore-batch-table">
                <thead>
                  <tr>
                    <th>優先</th>
                    <th>候選點</th>
                    <th>分數</th>
                    <th>建議</th>
                    <th>M12 P50</th>
                    <th>回本期</th>
                    <th>稀釋</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {cards
                    .slice()
                    .sort((left, right) => right.score - left.score)
                    .map((card, index) => (
                      <tr key={card.id} data-tone={recommendationTone(card.recommendation)}>
                        <td>
                          <span className={styles.priorityPill}>#{index + 1}</span>
                        </td>
                        <td>
                          <strong>{card.title}</strong>
                          <small>{card.id}</small>
                        </td>
                        <td className={styles.scoreMonoCell}>{card.score}</td>
                        <td>
                          <ToneBadge tone={recommendationTone(card.recommendation)}>
                            {card.recommendation}
                          </ToneBadge>
                        </td>
                        <td className={styles.scoreMonoCell}>{card.band.p50}</td>
                        <td className={styles.scoreMonoCell}>{card.payback}</td>
                        <td>{card.subScores.cannibalization || "未提供"}</td>
                        <td>
                          <button
                            className={styles.batchCompareToggleBtn}
                            disabled={!!busyCandidateId || !onToggleCompare}
                            onClick={() => onToggleCompare?.(card.id)}
                            type="button"
                          >
                            加入比較
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ScoreReport({
  busy,
  card,
  hidden,
  onRescore,
  onSubmitReview,
  onToggleCompare,
}: {
  busy: boolean;
  card: ScoreCard;
  hidden: boolean;
  onRescore?: (candidateId: string) => void;
  onSubmitReview?: (candidateId: string) => void;
  onToggleCompare?: (candidateId: string) => void;
}) {
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
          <span className={styles.scoreNumber}>
            {card.score}
            <small>/ 100</small>
          </span>
          <ToneBadge tone={tone}>{card.recommendation}</ToneBadge>
          {card.confidence ? (
            <span className={styles.confidenceBadge}>信心 {card.confidence}</span>
          ) : null}
          <div className={styles.scoreCandidateHeading}>
            <strong>{card.title}</strong>
            <small>{card.id}</small>
          </div>
        </div>
        <div className={styles.scoreReportTitle}>
          <span>{card.modelVersion} · 快照 {card.datasetSnapshotId}</span>
          <span>產生於 {card.generatedAt || "未提供"}</span>
        </div>
      </header>

      <div className={styles.scoreReportGrid}>
        <section>
          <div className={styles.filterTitle}>月營收路徑（P50）</div>
          <RevenuePath card={card} />
          <div className={styles.bandTiles}>
            <BandTile label="P10 保守" value={card.band.p10} />
            <BandTile label="P50 基準" value={card.band.p50} highlight />
            <BandTile label="P90 樂觀" value={card.band.p90} />
          </div>
        </section>
        <section>
          <dl className={styles.scoreMeta}>
            <div>
              <dt>回本期</dt>
              <dd className={styles.scoreMonoVal}>{card.payback}</dd>
            </div>
            <div>
              <dt>租金合理性</dt>
              <dd>{card.subScores.rentReasonableness || "未提供"}</dd>
            </div>
            <div>
              <dt>自家稀釋</dt>
              <dd>{card.subScores.cannibalization || "未提供"}</dd>
            </div>
            <div>
              <dt>競店壓力</dt>
              <dd>{card.subScores.competition || "未提供"}</dd>
            </div>
            <div>
              <dt>資料信心</dt>
              <dd>{card.confidence || "未提供"}</dd>
            </div>
          </dl>
          {card.drivers.length ? (
            <div className={styles.scoreDriversBlock}>
              <div className={styles.driversHeading}>需求驅動</div>
              {card.drivers.map((driver) => (
                <div className={styles.driverItem} key={driver}>
                  <span>◆</span> {driver}
                </div>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      <div className={styles.scoreAssumptionTiles}>
        <div className={styles.assumptionTile}>
          <span className={styles.assumptionLabel}>回本期</span>
          <strong className={styles.assumptionValue}>{card.payback}</strong>
        </div>
        <div className={styles.assumptionTile}>
          <span className={styles.assumptionLabel}>CAPEX 假設</span>
          <strong className={styles.assumptionValue}>{card.capex || "未提供"}</strong>
        </div>
        <div className={styles.assumptionTile}>
          <span className={styles.assumptionLabel}>租金假設</span>
          <strong className={styles.assumptionValue}>{card.rentAssumption || "未提供"}</strong>
        </div>
      </div>

      <div className={styles.riskBreakdownSection}>
        <div className={styles.filterTitle}>RISK BREAKDOWN</div>
        <dl className={styles.riskBreakdownGrid} aria-label="Risk breakdown">
          {SUB_SCORE_LABELS.map(([key, label]) => {
            const val = card.subScores[key] || "未提供";
            // Never infer a passing risk grade from recommendation or prose.
            // Older/live receipts without explicit presentation metadata are neutral.
            const declared = card.subScoreTones?.[key];
            const tone = card.subScores[key] && ["good", "watch", "risk"].includes(declared ?? "")
              ? declared : "unknown";
            return (
              <div className={styles.riskBreakdownCard} key={key}>
                <dt className={styles.riskCardKey}>{label}</dt>
                <dd className={styles.riskCardVal} data-tone={tone}>{val}</dd>
              </div>
            );
          })}
        </dl>
      </div>

      <div className={styles.reasonCols}>
        <section>
          <h4 className={styles.posHeading}>支持原因</h4>
          <ul className={styles.posList}>
            {card.reasons.map((reason) => (
              <li key={reason}>
                <span className={styles.greenBullet} />
                {reason}
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h4 className={styles.riskHeading}>主要風險</h4>
          <ul className={styles.riskList}>
            {card.risks.map((risk) => (
              <li key={risk}>
                <span className={styles.redBullet} />
                {risk}
              </li>
            ))}
          </ul>
        </section>
      </div>

      {card.conditions.length ? (
        <div
          className={styles.conditionBox}
          data-tone={tone}
          data-testid={`sitescore-conditions-${card.id}`}
        >
          <div className={styles.conditionTitle}>{card.conditionTitle || "通過條件"}</div>
          <ul className={styles.conditionList}>
            {card.conditions.map((item) => (
              <li key={item}>
                <span>▸</span> {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className={styles.scoreLabActions}>
        <button
          className={styles.secondaryButton}
          disabled={busy || !onToggleCompare}
          onClick={() => onToggleCompare?.(card.id)}
          type="button"
        >
          加入／移出比較
        </button>
        <button
          className={styles.secondaryButton}
          disabled
          title="尚未提供報告產生服務"
          type="button"
        >
          產生報告 preview
        </button>
        <button
          className={styles.secondaryButton}
          disabled={busy || !onSubmitReview}
          title={!onSubmitReview ? "尚未提供送審服務" : undefined}
          onClick={() => onSubmitReview?.(card.id)}
          type="button"
        >
          送審（SiteScore Review）
        </button>
        <button
          className={styles.secondaryButton}
          disabled
          title="尚未提供補資料通知服務"
          type="button"
        >
          要求補資料
        </button>
        <button
          className={styles.secondaryButton}
          disabled
          title="尚未提供候選點狀態變更服務"
          type="button"
        >
          標記不適合
        </button>
        {onRescore ? (
          <button
            className={styles.secondaryButton}
            data-testid={`sitescore-rescore-${card.id}`}
            disabled={busy}
            onClick={() => onRescore(card.id)}
            type="button"
          >
            {busy ? "評分中…" : "重新評分（Re-run）"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function RevenuePath({ card }: { card: ScoreCard }) {
  const points: Array<[string, number | undefined]> = [
    ["M1", card.revenuePath.m1],
    ["M3", card.revenuePath.m3],
    ["M6", card.revenuePath.m6],
    ["M12", card.revenuePath.m12],
  ];
  const max = Math.max(1, ...points.map(([, value]) => value ?? 0));
  return (
    <div className={styles.revenuePath} aria-label="月營收路徑（P50）">
      {points.map(([label, value]) => (
        <div className={styles.revenueBar} key={label}>
          <small className={styles.revenueBarVal}>{value == null ? "未提供" : `NT$${value}K`}</small>
          <i aria-hidden="true" className={styles.revenueBarTrack}>
            <b
              className={styles.revenueBarFill}
              style={{ height: `${Math.max(0, Math.round(((value ?? 0) / max) * 100))}%` }}
            />
          </i>
          <span className={styles.revenueBarLabel}>{label}</span>
        </div>
      ))}
    </div>
  );
}

function BandTile({
  highlight,
  label,
  value,
}: {
  highlight?: boolean;
  label: string;
  value: string;
}) {
  return (
    <div className={`${styles.bandTile} ${highlight ? styles.bandTileHighlight : ""}`}>
      <span className={styles.bandTileLabel}>{label}</span>
      <strong className={styles.bandTileValue}>{value}</strong>
    </div>
  );
}

function ToneBadge({
  children,
  tone,
}: {
  children: ReactNode;
  tone: "good" | "watch" | "risk";
}) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}

function fallbackToCard(row: SiteScoreLabRow): ScoreCard {
  return {
    id: row.id,
    title: row.title,
    zoneLabel: row.zoneLabel,
    heatZoneId: "",
    score: row.score,
    recommendation: row.recommendation,
    modelVersion: row.modelVersion,
    datasetSnapshotId: row.datasetSnapshotId,
    generatedAt: "今日 06:10",
    confidence: "高（現勘＋實價）",
    payback: "16 個月",
    revenuePath: { m1: 920, m3: 1180, m6: 1360, m12: 1480 },
    band: { p10: "NT$1,180K", p50: "NT$1,480K", p90: "NT$1,720K" },
    subScores: {
      rentReasonableness: "優（16.5% 營收）",
      cannibalization: "極低（<3%）",
      competition: "中等（280m 2家）",
      demand: "強（商辦＋住宅）",
      poiFit: "高（捷運站 220m）",
      access: "佳（雙向臨路＋車位）",
    },
    capex: "NT$3,800K（含裝潢／設備）",
    rentAssumption: "NT$68,000／月（押3付1）",
    drivers: ["信義商圈外溢人流", "捷運松山線出站動線", "高密度住宅聚落"],
    reasons: ["回本期最短（16 個月）", "自家門市稀釋率極低（<3%）"],
    risks: ["競店距離 280m，需注意尖峰外送價格戰"],
    conditions: row.missingData,
    conditionTitle: row.missingData.length ? "待補證據" : "",
  };
}
