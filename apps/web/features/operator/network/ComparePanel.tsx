"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { NetworkCompareViewModel } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type NetworkScoringCompare,
} from "./networkScoringTypes";

// ComparePanel owns the "候選點比較" tab. It renders the candidate comparison
// table plus the system recommendation, which classifies the basket into
// primary (推薦 / GO), alternate (備選), and avoid (不建議 / REJECT) — derived
// server-side from the score-sorted results so the guidance stays consistent
// with the SiteScore Lab.

export function ComparePanel({
  compare,
  fallback,
}: {
  compare: NetworkScoringCompare | null;
  fallback: NetworkCompareViewModel;
}) {
  const hasScoringCompare = compare != null && !compare.empty;

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network 候選點比較"
      data-testid="network-panel-compare"
      role="tabpanel"
    >
      <div className={styles.panelHeader}>
        <h3>比較 / Compare</h3>
        <span>
          {hasScoringCompare ? `${compare!.columns.length} candidates · 推薦／備選／不建議` : `${fallback.columns.length} HeatZones`}
        </span>
      </div>

      {hasScoringCompare ? (
        <>
          {compare!.recommendation ? (
            <div className={styles.compareRecPanel} data-testid="compare-recommendation">
              <RecCard
                variant="primary"
                testid="compare-primary"
                title={compare!.recommendation.primary.title}
                badge={`${compare!.recommendation.primary.recommendation} ${compare!.recommendation.primary.score}`}
                text={compare!.recommendation.primary.text}
                why={compare!.recommendation.primary.why}
              />
              {compare!.recommendation.alternate ? (
                <RecCard
                  variant="alternate"
                  testid="compare-alternate"
                  title={compare!.recommendation.alternate.title}
                  badge={`${compare!.recommendation.alternate.recommendation} ${compare!.recommendation.alternate.score}`}
                  text={compare!.recommendation.alternate.text}
                />
              ) : null}
              {compare!.recommendation.avoid ? (
                <RecCard
                  variant="avoid"
                  testid="compare-avoid"
                  title={compare!.recommendation.avoid.title}
                  badge={`${compare!.recommendation.avoid.recommendation} ${compare!.recommendation.avoid.score}`}
                  text={compare!.recommendation.avoid.text}
                />
              ) : null}
            </div>
          ) : null}

          <div className={styles.tableWrap}>
            <table className={styles.dataTable} data-testid="network-compare-table">
              <thead>
                <tr>
                  <th>比較欄位</th>
                  {compare!.columns.map((column) => (
                    <th key={column.id} className={column.isBest ? styles.leaderCell : undefined}>
                      <span className={styles.priorityPill} data-best={column.isBest ? "true" : undefined}>
                        {column.priority}
                      </span>{" "}
                      {column.id} · {column.title}
                      {column.isBest ? <span className={styles.leaderMark}>▲</span> : null}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {compare!.metrics.map((metric) => (
                  <tr key={metric.key}>
                    <th scope="row">{metric.label}</th>
                    {metric.values.map((value) => (
                      <td key={value.id} className={value.isBest ? styles.leaderCell : undefined}>
                        {value.text}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : fallback.columns.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.dataTable} data-testid="network-compare-table">
            <thead>
              <tr>
                <th>Metric</th>
                {fallback.columns.map((column) => (
                  <th key={column.zoneId}>
                    {column.label}
                    <small>rank #{column.rank}</small>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {fallback.metrics.map((metric) => (
                <tr key={metric.key}>
                  <th scope="row">{metric.label}</th>
                  {metric.values.map((value) => (
                    <td key={value.zoneId} className={value.isLeader ? styles.leaderCell : undefined}>
                      {value.label}
                      {value.isLeader ? <span className={styles.leaderMark}>▲</span> : null}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className={styles.emptyState}>Nothing to compare</div>
      )}
    </div>
  );
}

function RecCard({
  badge,
  testid,
  text,
  title,
  variant,
  why,
}: {
  badge: string;
  testid: string;
  text: string;
  title: string;
  variant: "primary" | "alternate" | "avoid";
  why?: string[];
}) {
  const tone = variant === "primary" ? "good" : variant === "avoid" ? "risk" : "watch";
  const label = variant === "primary" ? "推薦" : variant === "alternate" ? "備選" : "不建議";
  return (
    <article className={styles.recCard} data-tone={tone} data-testid={testid}>
      <header className={styles.detailIdLine}>
        <span className={styles.kicker}>{label}</span>
        <ToneBadge tone={tone}>{badge}</ToneBadge>
      </header>
      <strong>{title}</strong>
      <p>{text}</p>
      {why && why.length ? (
        <ul className={styles.missingList}>
          {why.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}
