"use client";

import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { NetworkCompareViewModel } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type NetworkScoringCompare,
} from "./networkScoringTypes";

// ComparePanel owns the "候選點比較" tab (Package 10 / R7 design parity).
// It renders the 3 summary chips at top, the comparison table with map below,
// and the system recommendation card (primary / alternate / avoid / priority ranking / action buttons).

export function ComparePanel({
  busyCandidateId,
  compare,
  fallback,
  onRemoveCandidate,
  onSubmitReview,
}: {
  busyCandidateId?: string | null;
  compare: NetworkScoringCompare | null;
  fallback: NetworkCompareViewModel;
  onRemoveCandidate?: (candidateId: string) => void;
  onSubmitReview?: (candidateId: string) => void;
}) {
  const hasScoringCompare = compare != null && !compare.empty;
  const primaryRec = compare?.recommendation?.primary;
  const alternateRec = compare?.recommendation?.alternate;
  const avoidRec = compare?.recommendation?.avoid;

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network 候選點比較"
      data-testid="network-panel-compare"
      role="tabpanel"
    >
      {hasScoringCompare ? (
        <>
          <div className={styles.compareSummaryChips}>
            <div className={styles.compareSummaryChip}>
              <span className={styles.compareChipKey}>已選候選點</span>
              <strong className={styles.compareChipVal}>
                {compare.columns.length} 個候選點
              </strong>
            </div>
            <div className={styles.compareSummaryChip}>
              <span className={styles.compareChipKey}>首選</span>
              <strong className={styles.compareChipVal}>
                {primaryRec ? `${primaryRec.title}（${primaryRec.score}）` : "—"}
              </strong>
            </div>
            <div className={styles.compareSummaryChip}>
              <span className={styles.compareChipKey}>次選</span>
              <strong className={styles.compareChipVal}>
                {alternateRec ? `${alternateRec.title}（${alternateRec.score}）` : "—"}
              </strong>
            </div>
          </div>

          <div className={styles.compareWorkspace}>
            <section className={styles.compareMain}>
              <div className={styles.tableWrap}>
                <table className={styles.dataTable} data-testid="network-compare-table">
                  <thead>
                    <tr>
                      <th>比較欄位</th>
                      {compare.columns.map((column) => (
                        <th
                          key={column.id}
                          className={column.isBest ? styles.leaderCell : undefined}
                        >
                          <div className={styles.compareColHeader}>
                            <div className={styles.compareColTop}>
                              <span
                                className={styles.priorityPill}
                                data-best={column.isBest ? "true" : undefined}
                              >
                                {column.priority}
                              </span>
                              <strong>{column.title}</strong>
                              {onRemoveCandidate ? (
                                <button
                                  aria-label={`移除 ${column.title}`}
                                  className={styles.compareRemoveBtn}
                                  disabled={!!busyCandidateId}
                                  onClick={() => onRemoveCandidate(column.id)}
                                  type="button"
                                >
                                  ×
                                </button>
                              ) : null}
                            </div>
                            <span className={styles.compareColId}>{column.id}</span>
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {compare.metrics.map((metric) => (
                      <tr key={metric.key}>
                        <th scope="row">{metric.label}</th>
                        {metric.values.map((value) => (
                          <td
                            key={value.id}
                            className={value.isBest ? styles.leaderCell : undefined}
                          >
                            <span
                              className={
                                value.isBest ? styles.compareValBest : styles.compareValNormal
                              }
                            >
                              {value.text}
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className={styles.compareMapWrap} data-testid="compare-map">
                <div className={styles.miniMapCanvas}>
                  <div className={styles.miniMapGrid} />
                  {compare.columns.map((col, idx) => (
                    <span
                      className={styles.miniMapPin}
                      key={col.id}
                      style={{
                        left: `${25 + idx * 30}%`,
                        top: `${35 + (idx % 2) * 20}%`,
                      }}
                    >
                      <i className={col.isBest ? styles.pinSquareBest : styles.pinSquare} />
                      <span>{col.title}</span>
                    </span>
                  ))}
                  <span className={styles.miniMapStorePin} style={{ left: "15%", top: "70%" }}>
                    <i className={styles.pinCircle} />
                    <span>既有店（示意）</span>
                  </span>
                  <span className={styles.miniMapStorePin} style={{ left: "85%", top: "40%" }}>
                    <i className={styles.pinCircle} />
                    <span>既有店（示意）</span>
                  </span>
                </div>
                <div className={styles.miniMapFooter}>
                  <span>位置示意 · 非實際座標／距離</span>
                </div>
              </div>
            </section>

            {compare.recommendation ? (
              <aside
                className={styles.compareRecommendation}
                data-testid="compare-recommendation"
              >
                <div className={styles.filterTitle}>RECOMMENDATION · 系統推薦</div>
                <RecCard
                  badge={`${primaryRec?.recommendation ?? "GO"} ${primaryRec?.score ?? ""}`}
                  testid="compare-primary"
                  text={primaryRec?.text ?? ""}
                  title={primaryRec?.title ?? "首選候選點"}
                  variant="primary"
                  why={primaryRec?.why}
                />
                {alternateRec ? (
                  <RecCard
                    badge={`${alternateRec.recommendation} ${alternateRec.score}`}
                    testid="compare-alternate"
                    text={alternateRec.text}
                    title={alternateRec.title}
                    variant="alternate"
                  />
                ) : null}
                {avoidRec ? (
                  <RecCard
                    badge={`${avoidRec.recommendation} ${avoidRec.score}`}
                    testid="compare-avoid"
                    text={avoidRec.text}
                    title={avoidRec.title}
                    variant="avoid"
                  />
                ) : null}
                <div className={styles.comparePriorityList} aria-label="Candidate priority">
                  {compare.recommendation.priorityList.map((item) => (
                    <div key={item.id}>
                      <span>{item.priority}</span>
                      <strong>{item.title}</strong>
                      <b>{item.score}</b>
                    </div>
                  ))}
                </div>

                <button
                  className={styles.detailPrimaryButton}
                  disabled={!!busyCandidateId || !primaryRec || !onSubmitReview}
                  title={!onSubmitReview ? "尚未提供送審服務" : undefined}
                  onClick={() => { if (primaryRec) onSubmitReview?.(primaryRec.id); }}
                  type="button"
                >
                  送審首選（{primaryRec?.title ?? "最佳候選點"}）
                </button>

                <div className={styles.compareSecondaryActions}>
                  {alternateRec ? (
                    <button
                      className={styles.secondaryButton}
                      disabled
                      title="尚未提供備選狀態變更服務"
                      type="button"
                    >
                      保留 {alternateRec.title} 為備選
                    </button>
                  ) : null}
                  {avoidRec ? (
                    <button
                      className={styles.secondaryButton}
                      disabled
                      title="尚未提供候選點封存服務"
                      type="button"
                    >
                      封存 {avoidRec.title}
                    </button>
                  ) : null}
                  <button
                    className={styles.secondaryButton}
                    disabled
                    title="尚未提供報告產生服務"
                    type="button"
                  >
                    產生比較報告 preview
                  </button>
                </div>
              </aside>
            ) : null}
          </div>
        </>
      ) : fallback.columns.length ? (
        <div className={styles.compareWorkspace}>
          <section className={styles.compareMain}>
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
                        <td
                          key={value.zoneId}
                          className={value.isLeader ? styles.leaderCell : undefined}
                        >
                          {value.label}
                          {value.isLeader ? <span className={styles.leaderMark}>▲</span> : null}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      ) : (
        <div className={styles.emptyState}>尚未加入比較 — 於候選點或 SiteScore Lab 點「加入比較」。</div>
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
      {text ? <p className={styles.recCardText}>{text}</p> : null}
      {why && why.length ? (
        <ul className={styles.recCardWhyList}>
          {why.map((item) => (
            <li key={item}>
              <span>▸</span> {item}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
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
