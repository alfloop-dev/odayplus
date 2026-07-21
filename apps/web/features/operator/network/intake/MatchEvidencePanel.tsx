"use client";

import type { AssistedIntake, MatchOutcome } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { matchLabel, matchTone } from "./intakeTypes";

export function MatchEvidencePanel({
  record,
  onOpenCompare,
  className,
}: {
  record: AssistedIntake;
  onOpenCompare?: () => void;
  className?: string;
}) {
  const match = record.matchResult;
  if (!match) {
    return (
      <div className={`${styles.sectionBox} ${className || ""}`} data-testid="match-evidence-panel-empty">
        <div className={styles.sectionHead}>比對證據 MATCH EVIDENCE</div>
        <div className={styles.emptyState}>本收件尚未產生比對證據與結果訊號。</div>
      </div>
    );
  }

  const outcome: MatchOutcome = match.outcome;
  const targetId = match.targetListingId || "無指定標的";
  const confidencePercent = (match.confidence * 100).toFixed(0);

  return (
    <div
      className={`${styles.sectionBox} ${className || ""}`}
      data-testid="match-evidence-panel"
      role="region"
      aria-label="比對證據與訊號面板"
    >
      <div className={styles.sectionHead}>
        <span>比對證據與結果 MATCH EVIDENCE & SIGNALS</span>
        <span className={styles.chip} data-testid="match-outcome-canonical-badge" data-tone={matchTone(outcome)}>
          {outcome} · {matchLabel(outcome)}
        </span>
        <span className={styles.sectionHeadHint}>
          信心分數 {match.confidence.toFixed(2)} ({confidencePercent}%)
        </span>
      </div>

      {/* Screen reader readable summary */}
      <div className={styles.srSummary} data-testid="match-evidence-sr-summary" role="region" aria-live="polite">
        比對結果 canonical code：{outcome}（{matchLabel(outcome)}），對應既有物件 ID：{targetId}，信心度 {match.confidence.toFixed(2)}。
        變更與差異摘要：{match.summary}
      </div>

      {/* Auto-merge prohibition warning for POSSIBLE_MATCH */}
      {outcome === "POSSIBLE_MATCH" ? (
        <div className={styles.warnNote} data-testid="no-auto-merge-warning">
          <strong>⚠ 系統安全防禦規則：</strong> 系統絕不自動合併疑似重複物件 (POSSIBLE_MATCH)。必須由具備權限的人員進行人工審查、填寫決策原因並選擇建立、修訂、重複標記或送交治理。
        </div>
      ) : null}

      {/* Quick summary grid */}
      <div className={styles.metaGrid}>
        <div>
          <span className={styles.metaCaption}>Canonical Code</span>
          <div className={styles.metaValue} data-testid="evidence-code-val">
            <code>{outcome}</code>
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>目標物件 Target ID</span>
          <div className={styles.metaValue} data-testid="evidence-target-id">
            {targetId}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>快照與 Parser</span>
          <div className={styles.metaValue} data-testid="evidence-snapshot-parser">
            {record.snapshotId ?? "—"} · {record.parserVersion}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>Correlation ID</span>
          <div className={styles.metaValue} data-testid="evidence-correlation-id">
            {record.correlationId ?? "—"}
          </div>
        </div>
      </div>

      {/* Signals Breakdown */}
      <div className={styles.signals} style={{ marginTop: "12px" }}>
        <div className={styles.signalCol} data-testid="agreeing-signals-list">
          <div className={styles.signalHeadAgree}>
            ✓ 一致訊號 AGREEING SIGNALS ({match.agreeingSignals?.length ?? 0})
          </div>
          {(match.agreeingSignals ?? []).length === 0 ? (
            <div className={styles.signalItem}>— 無一致訊號 —</div>
          ) : (
            (match.agreeingSignals ?? []).map((signal) => (
              <div className={styles.signalItem} key={`agree-${signal.key}`} data-testid={`signal-agree-${signal.key}`}>
                <span className={styles.chip} data-tone="good" style={{ padding: "1px 4px", fontSize: "9px" }}>
                  ✓ Match
                </span>{" "}
                <strong>{signal.label}</strong>：{signal.detail}
              </div>
            ))
          )}
        </div>

        <div className={styles.signalCol} data-testid="contradicting-signals-list">
          <div className={styles.signalHeadCon}>
            ✕ 矛盾訊號 CONTRADICTING SIGNALS ({(match.contradictingSignals ?? []).length})
          </div>
          {(match.contradictingSignals ?? []).length === 0 ? (
            <div className={styles.signalItem} style={{ color: "#1e7f4f" }}>
              ✓ 無矛盾訊號
            </div>
          ) : (
            (match.contradictingSignals ?? []).map((signal) => (
              <div className={styles.signalItem} key={`con-${signal.key}`} data-testid={`signal-con-${signal.key}`}>
                <span className={styles.changeChip} style={{ padding: "1px 4px", fontSize: "9px" }}>
                  ▲ 矛盾
                </span>{" "}
                <strong>{signal.label}</strong>：{signal.detail}
              </div>
            ))
          )}
        </div>
      </div>

      {onOpenCompare ? (
        <div style={{ marginTop: "12px", textAlign: "right" }}>
          <button
            className={styles.secondaryButton}
            data-testid="open-full-compare-btn"
            onClick={onOpenCompare}
            type="button"
          >
            開啟完整欄位比較表 (Listing Compare Table) →
          </button>
        </div>
      ) : null}
    </div>
  );
}
