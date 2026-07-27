"use client";

import { useEffect, useMemo, useState } from "react";
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
  const [pipelineFilter, setPipelineFilter] = useState<PipelineFilter>("all");
  const filteredRows = useMemo(
    () => rows.filter((row) => matchesPipelineFilter(row, pipelineFilter)),
    [pipelineFilter, rows],
  );
  const selected = filteredRows.find((row) => row.id === selectedId) ?? filteredRows[0] ?? rows[0];
  const scoreable = rows.filter((row) => !row.scored && row.gate.passed);
  const pipelineFilters: Array<{ id: PipelineFilter; label: string; count: number }> = [
    { id: "all", label: "全部候選點", count: rows.length },
    { id: "ready", label: "可執行評分", count: rows.filter((row) => !row.scored && row.gate.passed).length },
    { id: "blocked", label: "缺資料", count: rows.filter((row) => !row.gate.passed).length },
    { id: "scored", label: "已評分", count: rows.filter((row) => row.scored).length },
    { id: "compare", label: "比較中", count: rows.filter((row) => row.inCompare).length },
  ];

  useEffect(() => {
    if (filteredRows.length && !filteredRows.some((row) => row.id === selectedId)) {
      setSelectedId(filteredRows[0].id);
    }
  }, [filteredRows, selectedId]);

  return (
    <div
      className={styles.tabPanel}
      data-screen-label="Network 候選點工作台"
      data-testid="network-panel-candidates"
      role="tabpanel"
    >
      <div className={styles.panelHeader}>
        <div>
          <h3>候選點 / Candidates</h3>
          <p>從 Listing 建立候選點，確認資料完整度後送 SiteScore。</p>
        </div>
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
        <div className={styles.candidateWorkspace}>
          <aside className={styles.pipelinePanel} aria-label="Candidate pipeline">
            <div className={styles.filterTitle}>PIPELINE</div>
            <div className={styles.pipelineFilterList}>
              {pipelineFilters.map((filter) => (
                <button
                  aria-pressed={pipelineFilter === filter.id}
                  key={filter.id}
                  onClick={() => setPipelineFilter(filter.id)}
                  type="button"
                >
                  <span>{filter.label}</span>
                  <b>{filter.count}</b>
                </button>
              ))}
            </div>
            <div className={styles.candidateViewToggle} aria-label="Candidate view">
              <button aria-pressed="true" type="button">看板</button>
              <button disabled title="目前 API 未提供地圖投影資料" type="button">地圖</button>
            </div>
          </aside>

          <section
            className={styles.candidateBoard}
            data-testid="network-candidate-table"
            aria-label="Candidate board"
          >
            {filteredRows.length ? filteredRows.map((row) => {
              const isBusy = busyCandidateId === row.id;
              const tone = recommendationTone(row.recommendation);
              return (
                <article
                  className={styles.candidateCard}
                  data-active={selected?.id === row.id ? "true" : undefined}
                  data-testid={`candidate-row-${row.id}`}
                  data-tone={row.scored ? tone : row.gate.passed ? "watch" : "risk"}
                  key={row.id}
                  onClick={() => setSelectedId(row.id)}
                >
                  <div className={styles.candidateCardHead}>
                    <span>{row.id}</span>
                    <ToneBadge tone={row.gate.passed ? (row.scored ? tone : "watch") : "risk"}>
                      {row.scored ? `${row.recommendation} ${row.score}` : row.gate.passed ? "可評分" : "缺資料"}
                    </ToneBadge>
                  </div>
                  <h4>{row.title}</h4>
                  <p>{row.listingId ? `來源 ${row.listingId}` : row.address}</p>
                  <div className={styles.candidateCardMeta}>
                    <span>{row.zoneLabel}</span>
                    <span>{row.modelVersion}</span>
                  </div>
                  <div data-testid={`candidate-score-value-${row.id}`}>
                    <GateBadge gate={row.gate} candidateId={row.id} />
                    {row.scored ? (
                      <span className={styles.candidateScoreLine}>
                        SiteScore {row.recommendation} {row.score}
                      </span>
                    ) : null}
                  </div>
                  <small className={styles.candidateSnapshot}>{row.datasetSnapshotId}</small>
                  <div className={styles.rowActions}>
                    {!row.scored && row.gate.passed ? (
                      <button
                        data-testid={`candidate-score-${row.id}`}
                        disabled={isBusy || !onScore}
                        onClick={(event) => {
                          event.stopPropagation();
                          onScore?.(row.id);
                        }}
                        type="button"
                      >
                        {isBusy ? "評分中…" : "執行 SiteScore"}
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
                </article>
              );
            }) : (
              <div className={styles.emptyState}>此階段沒有候選點。</div>
            )}
          </section>

          <aside className={`${styles.listingDetailPanel} ${styles.candidateDetailPanel}`} aria-label="資料完整度 Gate detail">
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

type PipelineFilter = "all" | "ready" | "blocked" | "scored" | "compare";

function matchesPipelineFilter(candidate: ScoringCandidate, filter: PipelineFilter) {
  if (filter === "ready") return !candidate.scored && candidate.gate.passed;
  if (filter === "blocked") return !candidate.gate.passed;
  if (filter === "scored") return candidate.scored;
  if (filter === "compare") return candidate.inCompare;
  return true;
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
