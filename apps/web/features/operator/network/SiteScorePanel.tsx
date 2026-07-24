"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { SiteScoreLabRow } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type ScoreCard,
  type ScoringCandidate,
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
}: {
  busyCandidateId?: string | null;
  candidates: ScoringCandidate[];
  fallbackRows: SiteScoreLabRow[];
  modelVersion?: string;
  onRescore?: (candidateId: string) => void;
  scorecards: ScoreCard[];
}) {
  const cards = scorecards.length ? scorecards : fallbackRows.map(fallbackToCard);
  const blocked = candidates.filter((candidate) => !candidate.gate.passed);

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network SiteScore Lab"
      data-testid="network-panel-sitescore"
      role="tabpanel"
    >
      <div className={styles.panelHeader}>
        <h3>SiteScore / Score Lab</h3>
        <span className={styles.muted} data-testid="sitescore-model-meta">
          {modelVersion ?? "SiteScore v2.3"} · 特徵快照每日 06:00 · 需求／租金／侵蝕／回本四構面
        </span>
      </div>

      {blocked.length ? (
        <div className={styles.complianceBanner} data-testid="sitescore-gate-banner">
          <span>GATE</span>
          {blocked.map((candidate) => (
            <span key={candidate.id} data-testid={`sitescore-blocked-${candidate.id}`}>
              {candidate.id} {candidate.title}：缺資料 — 無法評分（{candidate.gate.missing.join("、")}）
            </span>
          ))}
        </div>
      ) : null}

      {cards.length ? (
        <div className={styles.cardGrid}>
          {cards.map((card) => {
            const tone = recommendationTone(card.recommendation);
            const isBusy = busyCandidateId === card.id;
            return (
              <article
                className={styles.scoreCard}
                data-testid={`sitescore-card-${card.id}`}
                data-tone={tone}
                key={card.id}
              >
                <header className={styles.scoreCardHead}>
                  <div>
                    <span className={styles.kicker}>{card.id}</span>
                    <strong>{card.title}</strong>
                    <small>{card.zoneLabel}</small>
                  </div>
                  <ToneBadge tone={tone}>
                    {card.recommendation} {card.score}
                  </ToneBadge>
                </header>

                <p className={styles.scoreBand}>
                  {card.modelVersion} · 快照 {card.datasetSnapshotId}
                  {card.confidence ? ` · 信心 ${card.confidence}` : ""}
                </p>

                <RevenuePath card={card} />

                <div className={styles.bandTiles}>
                  <BandTile label="P10 保守" value={card.band.p10} />
                  <BandTile label="P50 基準" value={card.band.p50} />
                  <BandTile label="P90 樂觀" value={card.band.p90} />
                </div>

                <dl className={styles.scoreMeta}>
                  <div>
                    <dt>回本期</dt>
                    <dd>{card.payback}</dd>
                  </div>
                  <div>
                    <dt>CAPEX 假設</dt>
                    <dd>{card.capex}</dd>
                  </div>
                  <div>
                    <dt>租金假設</dt>
                    <dd>{card.rentAssumption}</dd>
                  </div>
                </dl>

                <div className={styles.subScoreGrid} aria-label="Risk breakdown">
                  {SUB_SCORE_LABELS.map(([key, label]) => (
                    <div className={styles.gateRowItem} key={key}>
                      <span>{label}</span>
                      <small className={styles.muted}>{card.subScores[key] ?? "—"}</small>
                    </div>
                  ))}
                </div>

                {card.drivers.length ? (
                  <p className={styles.muted}>需求驅動：{card.drivers.join("・")}</p>
                ) : null}

                <div className={styles.reasonCols}>
                  <section>
                    <h4>支持原因</h4>
                    <ul className={styles.missingList}>
                      {card.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </section>
                  <section>
                    <h4>主要風險</h4>
                    <ul className={styles.missingList}>
                      {card.risks.map((risk) => (
                        <li key={risk}>{risk}</li>
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
                    <strong>{card.conditionTitle}</strong>
                    <ul className={styles.missingList}>
                      {card.conditions.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <button
                  className={styles.detailPrimaryButton}
                  data-testid={`sitescore-rescore-${card.id}`}
                  disabled={isBusy || !onRescore}
                  onClick={() => onRescore?.(card.id)}
                  type="button"
                >
                  {isBusy ? "評分中…" : "重新評分（Re-run SiteScore）"}
                </button>
              </article>
            );
          })}
        </div>
      ) : (
        <div className={styles.emptyState}>No SiteScore runs</div>
      )}
    </div>
  );
}

function RevenuePath({ card }: { card: ScoreCard }) {
  const points: Array<[string, number]> = [
    ["M1", card.revenuePath.m1],
    ["M3", card.revenuePath.m3],
    ["M6", card.revenuePath.m6],
    ["M12", card.revenuePath.m12],
  ];
  const max = Math.max(1, ...points.map(([, value]) => value));
  return (
    <div className={styles.revenuePath} aria-label="月營收路徑（P50）">
      {points.map(([label, value]) => (
        <div className={styles.revenueBar} key={label}>
          <i aria-hidden="true">
            <b style={{ height: `${Math.max(6, Math.round((value / max) * 100))}%` }} />
          </i>
          <span>{label}</span>
          <small>NT${value}K</small>
        </div>
      ))}
    </div>
  );
}

function BandTile({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.bandTile}>
      <span>{label}</span>
      <strong>{value}</strong>
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
    generatedAt: "",
    confidence: "",
    payback: "",
    revenuePath: { m1: 0, m3: 0, m6: 0, m12: 0 },
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
