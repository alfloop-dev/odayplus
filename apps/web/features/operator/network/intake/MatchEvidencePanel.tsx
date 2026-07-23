"use client";

import styles from "./identity.module.css";
import type { IdentityComparisonContract } from "./identityTypes";

export function MatchEvidencePanel({
  comparison,
  correlationId,
  className,
}: {
  comparison: IdentityComparisonContract;
  correlationId: string | null;
  className?: string;
}) {
  return (
    <section
      aria-labelledby="identity-evidence-title"
      className={`${styles.section} ${className ?? ""}`}
      data-testid="match-evidence-panel"
    >
      <div className={styles.headingRow}>
        <h3 className={styles.title} id="identity-evidence-title">
          比對證據
        </h3>
        <span
          className={styles.badge}
          data-outcome={comparison.outcome}
          data-testid="match-outcome-canonical-badge"
        >
          {comparison.outcome}
        </span>
      </div>

      <p className={styles.subtitle} data-testid="match-evidence-sr-summary" role="status">
        {comparison.summary} 信心度 {comparison.confidence.toFixed(2)}。既有物件{" "}
        {comparison.currentListingId ?? "無"}；既有 Property {comparison.currentPropertyId ?? "無"}。
      </p>

      {comparison.outcome === "POSSIBLE_MATCH" ? (
        <p className={styles.notice} data-testid="no-auto-merge-warning">
          <code>POSSIBLE_MATCH</code> 必須由人員明確選擇處置；本元件不會觸發自動合併。
        </p>
      ) : null}

      <div className={styles.metaRow}>
        <span className={styles.meta}>
          Snapshot: <code className={styles.code}>{comparison.submittedSnapshotId ?? "未提供"}</code>
        </span>
        <span className={styles.meta}>
          Parser run: <code className={styles.code}>{comparison.submittedParserRunId ?? "未提供"}</code>
        </span>
        <span className={styles.meta}>
          Correlation: <code className={styles.code}>{correlationId ?? "未提供"}</code>
        </span>
      </div>

      <div className={styles.signalColumns}>
        <div>
          <h4>一致訊號 ({comparison.agreeingSignals.length})</h4>
          {comparison.agreeingSignals.length > 0 ? (
            <ul className={styles.signalList} data-testid="agreeing-signals-list">
              {comparison.agreeingSignals.map((signal) => (
                <li key={`${signal.key}-${signal.detail}`}>
                  <strong>{signal.label}</strong>：{signal.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>沒有一致訊號。</p>
          )}
        </div>

        <div>
          <h4>矛盾訊號 ({comparison.contradictingSignals.length})</h4>
          {comparison.contradictingSignals.length > 0 ? (
            <ul className={styles.signalList} data-testid="contradicting-signals-list">
              {comparison.contradictingSignals.map((signal) => (
                <li key={`${signal.key}-${signal.detail}`}>
                  <strong>{signal.label}</strong>：{signal.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.hint}>沒有矛盾訊號。</p>
          )}
        </div>
      </div>
    </section>
  );
}
