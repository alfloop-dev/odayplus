"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { CandidatePipelineRow } from "../networkFindAreasViewModel";
import {
  recommendationTone,
  type ScoringCandidate,
  type ScoringGate,
} from "./networkScoringTypes";

// CandidatePanel owns the "候選點工作台" tab. It surfaces the R4 data
// completeness Gate (address / geocode / rent / area / floor / hard-rule) and
// exposes the SiteScore run action per candidate. Candidates whose gate is
// blocked (e.g. CS-1003 low geocode) are shown as "缺資料 — 無法評分" and their
// run action is disabled — scoring is refused server-side as well.

export function CandidatePanel({
  busyCandidateId,
  candidates,
  fallbackRows,
  onScore,
  onScoreAll,
  onToggleCompare,
}: {
  busyCandidateId?: string | null;
  candidates: ScoringCandidate[];
  fallbackRows: CandidatePipelineRow[];
  onScore?: (candidateId: string) => void;
  onScoreAll?: () => void;
  onToggleCompare?: (candidateId: string) => void;
}) {
  const rows = candidates.length ? candidates : fallbackRows.map(fallbackToCandidate);
  const [selectedId, setSelectedId] = useState(rows[0]?.id ?? "");
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0];
  const scoreable = rows.filter((row) => !row.scored && row.gate.passed);

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network 候選點工作台"
      data-testid="network-panel-candidates"
      role="tabpanel"
    >
      <div className={styles.panelHeader}>
        <h3>候選點 / Candidates</h3>
        <div className={styles.detailActions}>
          <span className={styles.muted}>{rows.length} candidates · 資料完整度 Gate 鎖評分</span>
          <button
            data-testid="candidate-score-all"
            disabled={!onScoreAll || scoreable.length === 0}
            onClick={() => onScoreAll?.()}
            type="button"
          >
            執行批次評分{scoreable.length ? `（${scoreable.length}）` : ""}
          </button>
        </div>
      </div>

      {rows.length ? (
        <div className={styles.radarLayout}>
          <section className={styles.tableWrap}>
            <table className={styles.dataTable} data-testid="network-candidate-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>HeatZone</th>
                  <th>資料完整度 Gate</th>
                  <th>SiteScore</th>
                  <th>Model / snapshot</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const isBusy = busyCandidateId === row.id;
                  const tone = recommendationTone(row.recommendation);
                  return (
                    <tr
                      key={row.id}
                      data-active={selected?.id === row.id ? "true" : undefined}
                      data-testid={`candidate-row-${row.id}`}
                      data-tone={row.scored ? tone : undefined}
                      onClick={() => setSelectedId(row.id)}
                    >
                      <td>
                        <strong>{row.id}</strong>
                        <small>{row.title}</small>
                      </td>
                      <td>{row.zoneLabel}</td>
                      <td>
                        <GateBadge gate={row.gate} candidateId={row.id} />
                      </td>
                      <td data-testid={`candidate-score-value-${row.id}`}>
                        {row.scored ? (
                          <ToneBadge tone={tone}>
                            {row.recommendation} {row.score}
                          </ToneBadge>
                        ) : row.gate.passed ? (
                          <span className={styles.muted}>待評分</span>
                        ) : (
                          <span className={styles.muted}>缺資料 — 無法評分</span>
                        )}
                      </td>
                      <td>
                        {row.modelVersion}
                        <small>{row.datasetSnapshotId}</small>
                      </td>
                      <td>
                        <div className={styles.rowActions}>
                          {!row.scored && row.gate.passed ? (
                            <button
                              data-testid={`candidate-score-${row.id}`}
                              disabled={isBusy}
                              onClick={(event) => {
                                event.stopPropagation();
                                onScore?.(row.id);
                              }}
                              type="button"
                            >
                              {isBusy ? "Scoring..." : "執行 SiteScore"}
                            </button>
                          ) : !row.gate.passed ? (
                            <button data-testid={`candidate-blocked-${row.id}`} disabled type="button">
                              補資料後評分
                            </button>
                          ) : (
                            <button
                              data-testid={`candidate-compare-${row.id}`}
                              disabled={isBusy || !onToggleCompare}
                              onClick={(event) => {
                                event.stopPropagation();
                                onToggleCompare?.(row.id);
                              }}
                              type="button"
                            >
                              {row.inCompare ? "移出比較" : "加入比較"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <aside className={styles.listingDetailPanel} aria-label="資料完整度 Gate detail">
            {selected ? (
              <GateDetail candidate={selected} onScore={onScore} busy={busyCandidateId === selected.id} />
            ) : (
              <div className={styles.emptyState}>No candidate selected</div>
            )}
          </aside>
        </div>
      ) : (
        <div className={styles.emptyState}>No candidates yet</div>
      )}
    </div>
  );
}

function GateBadge({ gate, candidateId }: { gate: ScoringGate; candidateId: string }) {
  const tone = gate.passed ? (gate.state === "warn" ? "watch" : "good") : "risk";
  return (
    <span data-testid={`candidate-gate-${candidateId}`}>
      <ToneBadge tone={tone}>
        {gate.okCount}/{gate.totalCount}
      </ToneBadge>
      {!gate.passed ? (
        <small className={styles.flagRisk} data-testid={`candidate-gate-block-${candidateId}`}>
          缺資料 — 無法評分：{gate.missing.join("、")}
        </small>
      ) : gate.state === "warn" ? (
        <small className={styles.muted}>{gate.blockNote}</small>
      ) : (
        <small className={styles.muted}>資料齊備</small>
      )}
    </span>
  );
}

function GateDetail({
  candidate,
  onScore,
  busy,
}: {
  candidate: ScoringCandidate;
  onScore?: (candidateId: string) => void;
  busy: boolean;
}) {
  return (
    <>
      <div>
        <div className={styles.detailIdLine}>
          <span>{candidate.id}</span>
          <ToneBadge tone={candidate.gate.passed ? "good" : "risk"}>資料完整度 GATE</ToneBadge>
        </div>
        <h3>{candidate.title}</h3>
        <p>{candidate.address}</p>
      </div>
      <ul className={styles.gateGrid} data-testid={`candidate-gate-checks-${candidate.id}`}>
        {candidate.gate.checks.map((check) => (
          <li className={styles.gateRowItem} data-state={check.state} key={check.key}>
            <span className={styles.gateMark} aria-hidden="true">
              {check.state === "ok" ? "✓" : check.state === "warn" ? "⚠" : "✕"}
            </span>
            <span>{check.label}</span>
            <small className={styles.muted}>{check.note}</small>
          </li>
        ))}
      </ul>
      {candidate.gate.blockNote ? (
        <p
          className={candidate.gate.passed ? styles.reasonNote : styles.errorText}
          data-testid={`candidate-gate-note-${candidate.id}`}
        >
          Gate：{candidate.gate.blockNote}
        </p>
      ) : null}
      <button
        className={styles.detailPrimaryButton}
        data-testid={`candidate-detail-score-${candidate.id}`}
        disabled={busy || candidate.scored || !candidate.gate.passed || !onScore}
        onClick={() => onScore?.(candidate.id)}
        type="button"
      >
        {candidate.scored
          ? `已評分 ${candidate.recommendation} ${candidate.score}`
          : candidate.gate.passed
            ? "執行 SiteScore"
            : "要求人工確認地址（鎖評分）"}
      </button>
    </>
  );
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}

// Fixture fallback: adapt a viewModel CandidatePipelineRow into the minimal
// ScoringCandidate shape when the scoring API is unavailable.
function fallbackToCandidate(row: CandidatePipelineRow): ScoringCandidate {
  const passed = row.missingData.length === 0;
  return {
    id: row.id,
    heatZoneId: row.heatZoneId,
    title: row.title,
    zoneLabel: row.zoneLabel,
    address: row.address,
    modelVersion: row.modelVersion,
    datasetSnapshotId: row.datasetSnapshotId,
    stage: row.status,
    scored: passed,
    score: passed ? row.score : null,
    recommendation: passed ? row.recommendation : null,
    reviewId: row.reviewId,
    inCompare: false,
    gate: buildFallbackGate(row, passed),
  };
}

function buildFallbackGate(row: CandidatePipelineRow, passed: boolean): ScoringGate {
  return {
    state: passed ? "ready" : "needdata",
    passed,
    missing: row.missingData,
    otherMissing: [],
    blockNote: passed ? "" : `缺必要資料：${row.missingData.join("、")}`,
    okCount: passed ? 6 : Math.max(0, 6 - row.missingData.length),
    totalCount: 6,
    checks: [],
  };
}
