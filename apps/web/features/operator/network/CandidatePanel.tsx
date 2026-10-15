"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import styles from "../networkFindAreas.module.css";
import type { CandidatePipelineRow } from "../networkFindAreasViewModel";
import {
  candidateGateDisplayRows,
  candidateMatchesPipeline,
  candidatePipelineControls,
  type CandidatePipelineFilter,
} from "./candidateDesignControls";
import {
  recommendationTone,
  type CandidateNativeWorkflowRead,
  type CandidateNativeWorkflowReads,
  type CandidateReviewReadTarget,
  type ScoringCandidate,
  type ScoringGate,
} from "./networkScoringTypes";

// CandidatePanel owns the "候選點工作台" tab. It surfaces the R4 data
// completeness Gate (address / geocode / rent / area / floor / hard-rule) and
// exposes the SiteScore run action per candidate. Candidates whose gate is
// blocked (e.g. CS-1003 low geocode) are shown as "缺資料 — 無法評分" and their
// run action is disabled — scoring is refused server-side as well.
// With a zoneScope (Find Areas 「查看本區候選點」) every count, the board, the
// selection and the batch run cover only that HeatZone's candidates.

export function CandidatePanel({
  busyCandidateId,
  candidates,
  fallbackRows,
  onClearZoneScope,
  onScore,
  onScoreAll,
  onToggleCompare,
  zoneScope,
  selectedCandidateId,
  selectionError,
  onSelectCandidate,
  nativeWorkflow,
  onViewReview,
}: {
  nativeWorkflow?: CandidateNativeWorkflowReads;
  onViewReview?: (target: CandidateReviewReadTarget) => void;
  selectedCandidateId?: string | null;
  selectionError?: string | null;
  onSelectCandidate?: (candidateId: string) => void;
  busyCandidateId?: string | null;
  candidates: ScoringCandidate[];
  fallbackRows: CandidatePipelineRow[];
  onClearZoneScope?: () => void;
  onScore?: (candidateId: string) => void;
  onScoreAll?: (candidateIds?: string[]) => void;
  onToggleCompare?: (candidateId: string) => void;
  zoneScope?: { id: string; label: string } | null;
}) {
  const zoneId = zoneScope?.id;
  const rows = useMemo(() => {
    const allRows = candidates.length ? candidates : fallbackRows.map(fallbackToCandidate);
    return zoneId ? allRows.filter((row) => row.heatZoneId === zoneId) : allRows;
  }, [candidates, fallbackRows, zoneId]);
  const [selectedId, setSelectedId] = useState(rows[0]?.id ?? "");
  const [pipelineFilter, setPipelineFilter] = useState<CandidatePipelineFilter>("all");
  const filteredRows = useMemo(
    () => rows.filter((row) => candidateMatchesPipeline(row, pipelineFilter, nativeWorkflow)),
    [pipelineFilter, rows, nativeWorkflow],
  );
  const exactSelection = selectedCandidateId !== undefined && selectedCandidateId !== null;
  const duplicateSelection = exactSelection && candidates.filter((row) => row.id === selectedCandidateId).length > 1;
  const selected = selectionError || duplicateSelection ? undefined : exactSelection
    ? filteredRows.find((row) => row.id === selectedCandidateId)
    : filteredRows.find((row) => row.id === selectedId) ?? filteredRows[0] ?? rows[0];
  const missingSelection = selectionError ?? (duplicateSelection
    ? "指定候選點的資料不一致，請重新確認。"
    : exactSelection && !selected ? "指定候選點目前不在可檢視的清單中；請確認權限或篩選條件。" : null);
  const scoreable = rows.filter((row) => !row.scored && row.gate.passed);
  const pipelineFilters = candidatePipelineControls(rows, nativeWorkflow);

  useEffect(() => {
    if (!exactSelection && !selectionError && filteredRows.length && !filteredRows.some((row) => row.id === selectedId)) {
      setSelectedId(filteredRows[0].id);
    }
  }, [filteredRows, selectedId, exactSelection, selectionError]);

  useEffect(() => {
    setPipelineFilter("all");
  }, [selectedCandidateId]);

  if (duplicateSelection) {
    return (
      <div className={styles.tabPanel} data-testid="network-panel-candidates" role="tabpanel">
        <p role="alert" data-testid="candidate-selection-unavailable">{missingSelection}</p>
      </div>
    );
  }

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
          {zoneScope ? (
            <>
              <span className={styles.toneBadge} data-testid="candidate-zone-scope" data-tone="watch">
                本區：{zoneScope.label}（{rows.length}）
              </span>
              {onClearZoneScope ? (
                <button data-testid="candidate-zone-scope-clear" onClick={onClearZoneScope} type="button">
                  顯示全部候選點
                </button>
              ) : null}
            </>
          ) : null}
          <span className={styles.muted}>{rows.length} candidates · 資料完整度 Gate 鎖評分</span>
          <button
            data-testid="candidate-score-all"
            disabled={nativeWorkflow !== undefined || !onScoreAll || scoreable.length === 0}
            title={nativeWorkflow !== undefined ? "批次評分受理與完整工作狀態尚未接通；資料 Gate 不代表可執行。" : undefined}
            onClick={() => onScoreAll?.(zoneScope ? scoreable.map((row) => row.id) : undefined)}
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
            <div className={styles.pipelineFilterList} data-testid="candidate-original-pipeline">
              {pipelineFilters.map((filter) => (
                <button
                  aria-pressed={pipelineFilter === filter.id}
                  data-testid={`candidate-pipeline-${filter.id}`}
                  disabled={!filter.available}
                  key={filter.id}
                  onClick={() => setPipelineFilter(filter.id)}
                  title={filter.reason ?? undefined}
                  type="button"
                >
                  <span>{filter.label}</span>
                  <b>{filter.count ?? "—"}</b>
                </button>
              ))}
            </div>
            {pipelineFilters.some((filter) => !filter.available) ? <p className={styles.candidatePipelineNote}>
              「—」表示流程紀錄不可得，尚無法確認數量。
            </p> : null}
            <div className={styles.pipelineFilterList}>
              <button aria-pressed={pipelineFilter === "compare"} data-testid="candidate-compare-filter"
                onClick={() => setPipelineFilter("compare")} type="button">
                <span>比較中</span><b>{rows.filter((row) => row.inCompare).length}</b>
              </button>
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
                  onClick={() => onSelectCandidate ? onSelectCandidate(row.id) : setSelectedId(row.id)}
                >
                  <div className={styles.candidateCardHead}>
                    <span>{row.id}</span>
                    <ToneBadge tone={row.gate.passed ? (row.scored ? tone : "watch") : "risk"}>
                      {nativeWorkflow ? nativeStageLabel(nativeWorkflow[row.id]) : row.scored ? [row.recommendation, row.score].filter((value) => value !== null).join(" ") : row.gate.passed ? "可評分" : "缺資料"}
                    </ToneBadge>
                  </div>
                  <h4>{row.title}</h4>
                  <p>{row.listingId ? `來源 ${row.listingId}` : row.address}</p>
                  <div className={styles.candidateCardMeta}>
                    <span>{row.zoneLabel}</span>
                    <span>{row.modelVersion ?? (row.scored ? "模型版本未提供" : "尚無評分報告")}</span>
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
                    {nativeWorkflow?.[row.id]?.review ? <button
                      data-testid={`candidate-row-view-review-${row.id}`} disabled={!onViewReview || isBusy}
                      onClick={(event) => { event.stopPropagation(); const target = nativeWorkflow[row.id].review; if (target) onViewReview?.(target); }} type="button">
                      {nativeWorkflow[row.id].review?.relation === "historical" ? "查看歷史審核" : "查看審核"}
                    </button> : nativeWorkflow && nativeWorkflow[row.id]?.availability !== "recorded" ? <button
                      disabled title={nativeWorkflow[row.id]?.reason} type="button">流程尚未核驗</button>
                      : nativeWorkflow && !row.scored && row.gate.passed ? <button
                        data-testid={`candidate-score-${row.id}`} disabled
                        title="初次評分受理與原始工作狀態尚未接通；資料 Gate 不代表可執行。" type="button">初次評分受理未接通</button>
                        : !row.scored && row.gate.passed ? (
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
              <GateDetail candidate={selected} nativeWorkflow={nativeWorkflow?.[selected.id]} canonical={nativeWorkflow !== undefined}
                onViewReview={onViewReview} onScore={onScore} onToggleCompare={onToggleCompare} busy={busyCandidateId === selected.id} />
            ) : (
              <div className={styles.emptyState} data-testid="candidate-selection-unavailable">{missingSelection ?? "No candidate selected"}</div>
            )}
          </aside>
        </div>
      ) : (
        <div className={styles.emptyState} data-testid="candidate-empty">
          {missingSelection ?? (zoneScope ? `${zoneScope.label} 尚無候選點。` : "No candidates yet")}
        </div>
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
  onToggleCompare,
  busy,
  nativeWorkflow,
  canonical,
  onViewReview,
}: {
  nativeWorkflow?: CandidateNativeWorkflowRead;
  canonical?: boolean;
  onViewReview?: (target: CandidateReviewReadTarget) => void;
  candidate: ScoringCandidate;
  onScore?: (candidateId: string) => void;
  onToggleCompare?: (candidateId: string) => void;
  busy: boolean;
}) {
  const displayRows = candidateGateDisplayRows(candidate);
  const fieldText = (key: string) => displayRows.find((row) => row.key === key)?.text ?? "不可得：資料未接通";
  const activeStage = canonical ? nativeWorkflow?.stage : (["onhold", "approved", "rejected", "pendingreview", "scoring"] as const)
    .find((stage) => candidateMatchesPipeline(candidate, stage));
  const reviewTarget = nativeWorkflow?.review;
  const scoreAction = !canonical && !activeStage && !candidate.scored && candidate.gate.passed;
  const viewReviewAction = activeStage === "pendingreview" && reviewTarget && onViewReview;
  const primaryLabel = activeStage === "onhold" ? "恢復處理"
    : activeStage === "approved" ? "已核准 — 待納入展店計畫"
      : activeStage === "rejected" ? "未通過 — 已結案"
        : activeStage === "pendingreview" ? `查看審核 ${reviewTarget?.reviewId ?? candidate.pipelineEvidence?.pendingreview?.recordId ?? ""}`
          : canonical && activeStage === "needdata" ? "退回／缺資料 — 補資料流程未接通"
            : canonical && !activeStage ? "流程狀態尚未核驗"
              : canonical && activeStage === "ready" ? "資料齊備 — 初次評分受理未接通"
          : activeStage === "scoring" ? "SiteScore 評分中…"
            : candidate.scored ? "送審（SiteScore Review）"
              : candidate.gate.passed ? "執行 SiteScore"
                : candidate.gate.state === "geo" ? "要求人工確認地址（鎖評分）"
                  : candidate.gate.state === "hard" ? "標記不適合／退回物件"
                    : "補資料（鎖評分）";
  const primaryReason = viewReviewAction ? "查看既有審核紀錄；核決仍由伺服器核驗實際權限與目前報告。"
    : canonical && activeStage === "ready" ? "初次評分受理與原始工作狀態尚未接通；資料齊備不代表可執行。再評分請查閱已保存報告的 SiteScore。"
      : canonical && nativeWorkflow ? nativeWorkflow.reason
      : scoreAction ? "資料完整度通過；評分服務仍會核驗模型、來源與特徵時點。"
    : "此工作流程尚未接通可核驗的操作服務。";
  const facts = [
    ["位置", fieldText("address")], ["Geocode", fieldText("geocode")],
    ["租金／坪數", `${fieldText("rent")} · ${fieldText("area")}`], ["樓層", fieldText("floor")],
    ["硬規則", fieldText("hardRule")], ["HeatZone", `${candidate.zoneLabel || "區域未提供"} · 適配量測不可得`],
    ["距既有店", "不可得：距離量測未接通"], ["距競店", "不可得：距離量測未接通"],
    ["POI", "不可得：POI 量測未接通"], ["仲介聯絡", fieldText("contact")], ["現勘", fieldText("visit")],
    ["Owner／期限", "不可得：負責人與期限未接通"], ["備註", "不可得：候選點備註未接通"],
  ];
  return (
    <>
      <div>
        <div className={styles.detailIdLine}>
          <span>{candidate.id}</span>
          <ToneBadge tone={candidate.gate.passed ? "watch" : "risk"}>{canonical ? nativeStageLabel(nativeWorkflow) : candidate.scored ? "已評分" : candidate.gate.passed ? "可評分" : "缺資料"}</ToneBadge>
        </div>
        <h3>{candidate.title}</h3>
        <p className={styles.candidateSourceLine}>{candidate.listingId ? `↳ 來源 ${candidate.listingId}` : "來源紀錄不可得"}</p>
      </div>
      {candidate.scored ? <div className={styles.candidateDetailScore}>SiteScore {candidate.recommendation ?? "建議未提供"}
        {candidate.score === null ? " · 尚無數值總分" : ` ${candidate.score}`}</div> : null}
      {canonical ? <p className={styles.muted} data-testid={`candidate-lifecycle-${candidate.id}`}>
        候選點記錄狀態：{nativeWorkflow?.lifecycle ?? "未提供"} · {nativeWorkflow?.reason ?? "流程紀錄未核驗"}
      </p> : null}
      {reviewTarget ? <p className={styles.reasonNote} data-testid={`candidate-review-provenance-${candidate.id}`}>
        人工審核 {reviewTarget.reviewId} · 凍結報告 {reviewTarget.reportId} v{reviewTarget.reportVersion}。
        {reviewTarget.latestReportVersion !== null ? ` 最新報告 ${reviewTarget.latestReportId} v${reviewTarget.latestReportVersion}。` : " 最新報告尚未核驗。"}
        {reviewTarget.relation === "historical" ? "保留原候選點人工狀態；這筆歷史決策未套用於最新版報告。" : "此紀錄只對應上述報告版本。"}
      </p> : null}
      <div className={styles.candidateGateContainer}>
        <div className={styles.candidateGateHeader}><span>資料完整度 GATE</span><span>{candidate.gate.okCount}/{candidate.gate.totalCount} 評分資料</span></div>
      <ul className={styles.gateGrid} data-testid={`candidate-gate-checks-${candidate.id}`}>
        {displayRows.map((check) => (
          <li className={styles.gateRowItem} data-state={check.state} data-testid={`candidate-evidence-${check.key}`} key={check.key} title={check.provenance}>
            <span className={styles.gateMark} aria-hidden="true">
              {check.state === "ok" ? "✓" : check.state === "warn" ? "⚠" : check.state === "fail" ? "✕" : check.state === "recorded" ? "●" : "—"}
            </span>
            <span data-gate-label>{check.label}</span>
            <small className={styles.muted}>{check.text}</small>
          </li>
        ))}
      </ul>
      </div>
      {candidate.gate.blockNote ? (
        <p
          className={candidate.gate.passed ? styles.reasonNote : styles.errorText}
          data-testid={`candidate-gate-note-${candidate.id}`}
        >
          Gate：{candidate.gate.blockNote}
        </p>
      ) : null}
      <dl className={styles.candidateDetailFacts}>
        {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <div className={styles.candidateAttachmentSlot}>現勘照片／附件未接通</div>
      <div className={styles.candidateWorkflowActions}>
      <button
        className={styles.detailPrimaryButton}
        data-testid={viewReviewAction ? `candidate-view-review-${candidate.id}` : `candidate-detail-score-${candidate.id}`}
        disabled={busy || !(scoreAction && onScore || viewReviewAction)}
        onClick={() => { if (viewReviewAction && reviewTarget) onViewReview?.(reviewTarget); else if (scoreAction) onScore?.(candidate.id); }}
        title={primaryReason}
        type="button"
      >
        {busy && scoreAction ? "評分中…" : primaryLabel}
      </button>
      <p className={styles.muted}>{primaryReason}</p>
      <div className={styles.candidateSecondaryActions}>
        {reviewTarget && activeStage !== "pendingreview" ? <button data-testid={`candidate-view-review-${candidate.id}`}
          disabled={!onViewReview || busy} onClick={() => onViewReview?.(reviewTarget)} type="button">
          {reviewTarget.relation === "historical" ? "查看歷史審核" : "查看審核"}
        </button> : null}
        <button disabled title="現勘指派服務未接通" type="button">指派現勘</button>
        <button disabled title="仲介聯絡服務未接通" type="button">聯絡仲介</button>
        <button disabled={busy || !onToggleCompare} onClick={() => onToggleCompare?.(candidate.id)} type="button">
          {candidate.inCompare ? "移出比較" : "加入比較"}
        </button>
        <button disabled title="暫停與恢復服務未接通" type="button">封存（On Hold）</button>
      </div>
      </div>
      <div data-testid="candidate-audit-readback"><div className={styles.filterTitle}>AUDIT</div>
        <p className={styles.muted}>稽核紀錄未接通；尚無可核驗的操作歷程。</p>
      </div>
    </>
  );
}

function nativeStageLabel(read?: CandidateNativeWorkflowRead): string {
  const labels = { needdata: "缺資料／退回", ready: "資料齊備", evaluated: "已保存報告", pendingreview: "審核中",
    approved: "已核准", rejected: "未通過", onhold: "暫停（WAIT）" };
  return read?.stage && read.availability === "recorded" ? labels[read.stage] : "流程未核驗";
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
  const scored = row.recommendation !== null && Boolean(row.modelVersion)
    && (row.status === "scored" || row.score !== null);
  return {
    id: row.id,
    heatZoneId: row.heatZoneId,
    title: row.title,
    zoneLabel: row.zoneLabel,
    address: row.address,
    modelVersion: row.modelVersion,
    datasetSnapshotId: row.datasetSnapshotId ?? "",
    stage: row.status,
    scored,
    score: scored ? row.score : null,
    recommendation: scored ? row.recommendation : null,
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
