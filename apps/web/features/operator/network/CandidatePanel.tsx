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
  onScoreAll?: (candidateIds: string[]) => void;
  onToggleCompare?: (candidateId: string) => void;
}) {
  const rows = candidates.length ? candidates : fallbackRows.map(fallbackToCandidate);
  const [selectedId, setSelectedId] = useState(rows[0]?.id ?? "");
  const [pipelineFilter, setPipelineFilter] = useState<PipelineFilter>("all");
  const [viewMode, setViewMode] = useState<"board" | "map">("board");
  const filteredRows = useMemo(
    () => rows.filter((row) => matchesPipelineFilter(row, pipelineFilter)),
    [pipelineFilter, rows],
  );
  const selected = filteredRows.find((row) => row.id === selectedId) ?? filteredRows[0] ?? rows[0];
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
              <button
                aria-pressed={viewMode === "board"}
                onClick={() => setViewMode("board")}
                type="button"
              >
                看板
              </button>
              <button
                aria-pressed={viewMode === "map"}
                onClick={() => setViewMode("map")}
                type="button"
              >
                地圖
              </button>
            </div>
            <button
              className={styles.secondaryButton}
              data-testid="candidate-score-all"
              disabled={!!busyCandidateId || !onScoreAll || !rows.some((row) => row.gate.passed)}
              onClick={() => onScoreAll?.(rows.filter((row) => row.gate.passed).map((row) => row.id))}
              type="button"
            >
              {busyCandidateId === "batch" ? "批次評分中…" : "批次執行 SiteScore"}
            </button>
          </aside>

          {viewMode === "board" ? (
            <section
              className={styles.candidateBoard}
              data-testid="network-candidate-table"
              aria-label="Candidate board"
            >
              {filteredRows.length ? (
                filteredRows.map((row) => {
                  const isBusy = !!busyCandidateId;
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
                        <span className={styles.candidateId}>{row.id}</span>
                        <ToneBadge tone={row.gate.passed ? (row.scored ? tone : "watch") : "risk"}>
                          {row.scored
                            ? `${row.recommendation} ${row.score}`
                            : row.gate.passed
                              ? "可評分"
                              : "缺資料"}
                        </ToneBadge>
                      </div>
                      <h4>{row.title}</h4>
                      <p className={styles.candidateSource}>
                        {row.listingId ? `↳ 來源 ${row.listingId}` : row.address}
                      </p>
                      <div className={styles.candidateCardMeta}>
                        <span>{row.zoneLabel || row.heatZoneId}</span>
                        <span>{row.modelVersion}</span>
                      </div>
                      <div data-testid={`candidate-score-value-${row.id}`}>
                        <GateBadge gate={row.gate} candidateId={row.id} />
                        {row.scored ? (
                          <div className={styles.candidateScoreLine}>
                            SiteScore {row.recommendation} {row.score}
                          </div>
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
                })
              ) : (
                <div className={styles.emptyState}>此階段沒有候選點。</div>
              )}
            </section>
          ) : (
            <div className={styles.candidateMapPlaceholder} data-testid="candidate-map-view">
              <div className={styles.miniMapCanvas}>地圖投影檢視模式</div>
            </div>
          )}

          <aside
            className={`${styles.listingDetailPanel} ${styles.candidateDetailPanel}`}
            aria-label="候選點詳情"
          >
            {selected ? (
              <CandidateDetailPane
                candidate={selected}
                onScore={onScore}
                onToggleCompare={onToggleCompare}
                busy={!!busyCandidateId}
              />
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

function CandidateDetailPane({
  candidate,
  onScore,
  onToggleCompare,
  busy,
}: {
  candidate: ScoringCandidate;
  onScore?: (candidateId: string) => void;
  onToggleCompare?: (candidateId: string) => void;
  busy: boolean;
}) {
  const isScored = candidate.scored;
  const isPassed = candidate.gate.passed;
  const tone = isScored ? recommendationTone(candidate.recommendation) : isPassed ? "watch" : "risk";
  const checks = candidate.gate.checks;
  // Gate completeness is not evidence of a measured value or a passed hard
  // rule. Reuse the authoritative per-dimension notes, never prototype facts.
  const checkNote = (key: string) => {
    const check = checks.find((item) => item.key === key);
    if (!check) return "未提供";
    const note = check.note || "未提供";
    return check.state === "fail" ? `${note}（未通過）` : check.state === "warn" ? `${note}（待確認）` : note;
  };

  return (
    <div className={styles.candidateDetailContent}>
      <div className={styles.candidateDetailHead}>
        <div className={styles.detailIdLine}>
          <span className={styles.monoId}>{candidate.id}</span>
          <ToneBadge tone={tone}>
            {isScored
              ? `${candidate.recommendation} ${candidate.score}`
              : isPassed
                ? "可評分"
                : "缺資料"}
          </ToneBadge>
        </div>
        <h3>{candidate.title}</h3>
        <p className={styles.candidateSource}>↳ 來源 {candidate.listingId || "未提供"}</p>
      </div>

      {isScored ? (
        <div className={styles.candidateScoreBox}>
          SiteScore {candidate.recommendation} {candidate.score}
        </div>
      ) : null}

      <div className={styles.candidateGateCard}>
        <div className={styles.candidateGateHead}>
          <span>資料完整度 GATE</span>
          <span className={styles.candidateGateCount}>
            {candidate.gate.okCount}/{candidate.gate.totalCount}
          </span>
        </div>
        <ul className={styles.gateGrid} data-testid={`candidate-gate-checks-${candidate.id}`}>
          {!checks.length ? <li className={styles.muted}>未提供逐項檢查記錄</li> : null}
          {checks.map((check) => (
            <li className={styles.gateRowItem} data-state={check.state} key={check.key}>
              <span className={styles.gateMark} aria-hidden="true">
                {check.state === "ok" ? "✓" : check.state === "warn" ? "⚠" : "✕"}
              </span>
              <span>{check.label}</span>
              <small className={styles.muted}>{check.note}</small>
            </li>
          ))}
        </ul>
      </div>

      {candidate.gate.blockNote || !isPassed ? (
        <div
          className={styles.gateWarningBox}
          data-testid={`candidate-gate-note-${candidate.id}`}
        >
          Gate：{candidate.gate.blockNote || `缺必要資料 — ${candidate.gate.missing.join("、") || "待人工確認"}`}
        </div>
      ) : null}

      <div className={styles.candidateKeyValueTable} aria-label="候選點鍵值資訊">
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>位置</span>
          <span className={styles.kvVal}>{candidate.address}</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>Geocode</span>
          <span className={styles.kvVal}>{checkNote("geocode")}</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>租金／坪數</span>
          <span className={styles.kvVal}>{checkNote("rent")} · {checkNote("area")}</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>樓層</span>
          <span className={styles.kvVal}>{checkNote("floor")}</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>硬規則</span>
          <span className={styles.kvVal}>{checkNote("hardRule")}</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>HeatZone</span>
          <span className={styles.kvVal} style={{ color: "#0e7c8c", fontWeight: 600 }}>
            {candidate.zoneLabel || candidate.heatZoneId || "未提供"}
          </span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>距既有店</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>距競店</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>POI</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>仲介聯絡</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>現勘</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>Owner／期限</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
        <div className={styles.kvRow}>
          <span className={styles.kvKey}>備註</span>
          <span className={styles.kvVal}>未提供</span>
        </div>
      </div>

      <div
        className={styles.candidatePhotoPlaceholder}
        data-testid="candidate-photo-placeholder"
      >
        現勘照片／附件 placeholder
      </div>

      <div className={styles.candidateActionSection}>
        <button
          className={styles.detailPrimaryButton}
          data-testid={`candidate-detail-score-${candidate.id}`}
          disabled={busy || isScored || !isPassed || !onScore}
          onClick={() => onScore?.(candidate.id)}
          type="button"
        >
          {isScored
            ? `已評分 ${candidate.recommendation} ${candidate.score}`
            : isPassed
              ? "執行 SiteScore 評分"
              : "無法評分 — 資料不完整"}
        </button>

        <div className={styles.candidateSecondaryButtons}>
          <button
            className={styles.secondaryButton}
            data-testid={`candidate-detail-compare-${candidate.id}`}
            disabled={busy || !isScored || !onToggleCompare}
            onClick={() => onToggleCompare?.(candidate.id)}
            type="button"
          >
            {candidate.inCompare ? "移出比較" : "加入比較"}
          </button>
          <button className={styles.secondaryButton} disabled title="尚未提供候選點編輯服務" type="button">
            編輯候選點
          </button>
          <button className={styles.secondaryButton} disabled title="尚未提供候選點封存服務" type="button">
            封存候選點
          </button>
        </div>
      </div>

      <div className={styles.candidateAuditSection} aria-label="Audit records">
        <div className={styles.auditTitle}>AUDIT</div>
        <div className={styles.auditList}>
          <div className={styles.auditItem}>
            <span className={styles.auditBody}>未提供候選點 audit 記錄</span>
          </div>
        </div>
      </div>
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
