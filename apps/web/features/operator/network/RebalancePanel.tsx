"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "../networkFindAreas.module.css";
import type { RebalanceQueueRow } from "../networkFindAreasViewModel";

type RebalanceAction = "request-avm" | "complete-avm" | "solve-netplan" | "select-scenario" | "submit-review";

export type RebalancePanelProps = {
  apiError?: string | null;
  busyAction?: string | null;
  onCompleteAvm: (storeId: string) => void;
  onRequestAvm: (storeId: string) => void;
  onSelectScenario: (storeId: string, scenarioId: string) => void;
  onSolveNetPlan: (storeId: string) => void;
  onSubmitReview: (storeId: string) => void;
  rows: RebalanceQueueRow[];
};

const stepLabels = ["低效確認", "AVM 估值", "NetPlan 三案", "送審", "核准"];

const lightTone: Record<string, string> = {
  G: "#2e9e63",
  A: "#d08700",
  R: "#c4342c",
};

export function RebalancePanel({
  apiError,
  busyAction,
  onCompleteAvm,
  onRequestAvm,
  onSelectScenario,
  onSolveNetPlan,
  onSubmitReview,
  rows,
}: RebalancePanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(rows[0]?.id ?? null);

  useEffect(() => {
    if (rows.length > 0 && !rows.some((row) => row.id === selectedId)) {
      setSelectedId(rows[0].id);
    }
  }, [rows, selectedId]);

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) ?? rows[0],
    [rows, selectedId],
  );

  if (!rows.length || !selected) {
    return (
      <div className={styles.tabPanel} data-screen-label="Network 低效重配" data-testid="network-panel-rebalance" role="tabpanel">
        <div className={styles.panelHeader}>
          <h3>低效重配 / Rebalance</h3>
          <span>0 stores</span>
        </div>
        <div className={styles.emptyState}>No rebalance candidates</div>
      </div>
    );
  }

  const cta = primaryCta(selected);
  const selectedScenario = selected.netPlanScenarios?.find((scenario) => scenario.id === selected.selectedScenarioId);
  const actionBusy = busyAction?.startsWith(`${selected.id}:`) ?? false;

  function handlePrimary() {
    if (cta.disabled) return;
    if (cta.action === "request-avm") onRequestAvm(selected.id);
    if (cta.action === "complete-avm") onCompleteAvm(selected.id);
    if (cta.action === "solve-netplan") onSolveNetPlan(selected.id);
    if (cta.action === "submit-review") onSubmitReview(selected.id);
  }

  return (
    <div className={styles.tabPanel} data-screen-label="Network 低效重配" data-testid="network-panel-rebalance" role="tabpanel">
      <div className={styles.panelHeader}>
        <div>
          <h3>低效重配 / Rebalance</h3>
          <p>API-backed AVM job → NetPlan 三案 → Govern approval</p>
        </div>
        <span>{rows.length} stores</span>
      </div>
      {apiError ? (
        <div className={styles.rebalanceError} data-testid="rebalance-api-error">
          {apiError}
        </div>
      ) : null}

      <section className={styles.rebalanceWorkflowGrid}>
        <aside className={styles.rebalanceStoreList} aria-label="Rebalance candidates">
          {rows.map((row) => (
            <button
              aria-current={row.id === selected.id ? "true" : undefined}
              className={styles.rebalanceStoreButton}
              data-testid={`rebalance-card-${row.id}`}
              data-tone={row.runtimeState ? "risk" : row.status === "approved" ? "good" : "watch"}
              key={row.id}
              onClick={() => setSelectedId(row.id)}
              type="button"
            >
              <span className={styles.rebalanceStoreTopline}>
                <i aria-hidden="true" />
                <strong>{row.storeName}</strong>
                <small>{row.statusLabel}</small>
              </span>
              <span>{row.healthNote ?? row.summary}</span>
              <span className={styles.rebalanceStoreMeta}>
                <b>{row.monthlyRevenueLabel ?? "—"}</b>
                <b>利用率 {row.utilizationLabel ?? "—"}</b>
              </span>
              <span className={styles.rebalanceLights} aria-label="eight-week light history">
                {(row.lightHistory ?? []).map((light, index) => (
                  <i key={`${light}-${index}`} style={{ background: lightTone[light] ?? "#98a1b3" }} />
                ))}
              </span>
            </button>
          ))}
          <small className={styles.muted}>右側圓點為近 8 週四燈歷史（左舊右新）。</small>
        </aside>

        <article className={styles.rebalanceDetail} data-testid={`rebalance-detail-${selected.id}`}>
          <header className={styles.rebalanceDetailHeader}>
            <div>
              <span className={styles.kicker}>{selected.id}</span>
              <h4>{selected.storeName}</h4>
              <p>{selected.summary}</p>
            </div>
            <span className={styles.rebalanceStatusPill}>{selected.statusLabel}</span>
          </header>

          <div className={styles.rebalanceStepper} aria-label="Rebalance workflow">
            {stepLabels.map((label, index) => {
              const current = workflowStep(selected.status);
              return (
                <span
                  className={styles.rebalanceStep}
                  data-active={index === current}
                  data-done={index < current}
                  key={label}
                >
                  <i>{index < current ? "✓" : index === current ? "•" : ""}</i>
                  <b>{label}</b>
                </span>
              );
            })}
          </div>

          <div className={styles.rebalanceSignalGrid}>
            <div>
              <span>月營收</span>
              <strong>{selected.monthlyRevenueLabel ?? "—"}</strong>
            </div>
            <div>
              <span>利用率</span>
              <strong>{selected.utilizationLabel ?? "—"}</strong>
            </div>
            <div>
              <span>來源 Issue</span>
              <strong>{selected.sourceIssueId ?? "—"}</strong>
            </div>
          </div>

          <div className={styles.rebalanceTrend} aria-label="90 day revenue trend">
            {(selected.trend ?? []).map((value, index, values) => (
              <i
                key={`${value}-${index}`}
                style={{
                  height: `${Math.max(6, value)}%`,
                  background: index === values.length - 1 ? "#c4342c" : "#c6cfea",
                }}
              />
            ))}
          </div>

          {selected.runtimeState ? (
            <div className={styles.rebalanceRuntimeState} data-testid={`rebalance-runtime-${selected.id}`}>
              <strong>{selected.runtimeState.model} runtime unavailable</strong>
              <span>retryable · retry after {selected.runtimeState.retryAfterSeconds ?? 300}s</span>
            </div>
          ) : null}

          {selected.avmP50 !== undefined ? (
            <section className={styles.rebalanceAvmBlock} data-testid={`rebalance-avm-${selected.id}`}>
              <div className={styles.rebalanceAvmHeader}>
                <span>AVM 估值（service output）</span>
                <span>{selected.avmConf ?? "—"}</span>
              </div>
              <div className={styles.avmValueP50}>{formatCurrency(selected.avmP50)}</div>
              <div className={styles.avmBands}>
                <span>P10: {selected.avmP10 ? formatCurrency(selected.avmP10) : "—"}</span>
                <span>P90: {selected.avmP90 ? formatCurrency(selected.avmP90) : "—"}</span>
              </div>
              <div className={styles.rebalanceMetadata}>
                <span>{selected.avmModelVersion}</span>
                <span>{selected.avmSnapshotId}</span>
                <span>{selected.avmEvidenceId}</span>
              </div>
              {selected.avmReserve ? <div className={styles.avmReserveNote}>{selected.avmReserve}</div> : null}
            </section>
          ) : null}

          {selected.netPlanScenarios && selected.netPlanScenarios.length > 0 ? (
            <section className={styles.rebalanceNetPlanBlock} data-testid={`rebalance-netplan-${selected.id}`}>
              <div className={styles.rebalanceNetPlanHeader}>NETPLAN 三案 · 點擊選擇</div>
              <div className={styles.netPlanScenarioList}>
                {selected.netPlanScenarios.map((scenario) => {
                  const scenarioId = scenario.id ?? scenario.name;
                  const scenarioBusy = busyAction === `${selected.id}:select-scenario:${scenarioId}`;
                  return (
                    <button
                      aria-pressed={scenario.selected || selected.selectedScenarioId === scenarioId}
                      className={classNames(
                        styles.netPlanScenarioCard,
                        (scenario.selected || selected.selectedScenarioId === scenarioId) && styles.netPlanScenarioCardSelected,
                        scenario.isSystemRecommendation && styles.netPlanScenarioCardRec,
                      )}
                      data-testid={`rebalance-scenario-${scenarioId}`}
                      disabled={actionBusy || selected.status !== "netplanreview" || scenarioBusy}
                      key={scenarioId}
                      onClick={() => onSelectScenario(selected.id, scenarioId)}
                      type="button"
                    >
                      <span className={styles.scenarioTitleRow}>
                        <strong>{scenario.name}</strong>
                        {scenario.isSystemRecommendation ? <span className={styles.recBadge}>系統建議</span> : null}
                        <span className={styles.roiValue}>{scenario.roi}</span>
                      </span>
                      <span className={styles.scenarioDetails}>
                        投資 {scenario.inv} · 回本 {scenario.payback} · 風險 {scenario.risk} · 時程 {scenario.time}
                      </span>
                      <span className={styles.rebalanceMetadata}>
                        <span>{scenario.modelVersion}</span>
                        <span>{scenario.snapshotId}</span>
                        <span>score {scenario.score ?? "—"}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          ) : null}

          {selected.selectedScenarioId ? (
            <section className={styles.rebalanceSelection} data-testid={`rebalance-selection-${selected.id}`}>
              <strong>Selected: {selectedScenario?.name ?? selected.selectedScenarioId}</strong>
              <span>
                Owner {selected.selectedScenarioOwner?.actorName ?? "—"} · Evidence{" "}
                {selected.selectedScenarioEvidenceId ?? "—"}
              </span>
            </section>
          ) : null}

          <section className={styles.rebalanceBoundary} data-testid={`rebalance-boundary-${selected.id}`}>
            <strong>Execution boundary</strong>
            <span>
              relocationExecuted={String(Boolean(selected.relocationExecuted))} ·{" "}
              {selected.executionBoundary ?? "Govern approval required before relocation execution."}
            </span>
            {selected.relatedApprovalId ? <b>Govern approval {selected.relatedApprovalId}</b> : null}
          </section>

          <button
            className={styles.rebalancePrimary}
            data-testid="rebalance-primary-action"
            disabled={cta.disabled || actionBusy}
            onClick={handlePrimary}
            type="button"
          >
            {actionBusy ? "Working..." : cta.label}
          </button>
          {cta.note ? <small className={styles.muted}>{cta.note}</small> : null}
        </article>
      </section>
    </div>
  );
}

function primaryCta(row: RebalanceQueueRow): {
  action: Exclude<RebalanceAction, "select-scenario"> | null;
  disabled: boolean;
  label: string;
  note?: string;
} {
  if (row.status === "watching") {
    return { action: "request-avm", disabled: false, label: "建立 AVM 估值請求", note: "先估值，再進 NetPlan 三案。" };
  }
  if (row.status === "avmrequested") {
    return { action: "complete-avm", disabled: false, label: "完成 AVM job", note: "AVM result comes from service metadata, not UI constants." };
  }
  if (row.status === "avmready") {
    return { action: "solve-netplan", disabled: false, label: "建立 NetPlan Review（三案）" };
  }
  if (row.status === "netplanreview") {
    return {
      action: "submit-review",
      disabled: !row.selectedScenarioId,
      label: "送審（Rebalance Review）",
      note: row.selectedScenarioId ? "送審後由 Govern 核准中心決策。" : "請先選擇 Keep / Move / Exit 其中一案。",
    };
  }
  if (row.status === "pendingapproval") {
    return { action: null, disabled: true, label: "等待 Govern 核准中", note: "送審不代表 relocation 已執行。" };
  }
  if (row.status === "approved") {
    return { action: null, disabled: true, label: "已核准 — 等待後續執行計畫", note: "本 task 不標記 relocation executed。" };
  }
  return { action: null, disabled: true, label: "已結案" };
}

function workflowStep(status: RebalanceQueueRow["status"]) {
  if (status === "watching") return 0;
  if (status === "avmrequested") return 1;
  if (status === "avmready") return 2;
  if (status === "netplanreview" || status === "pendingapproval") return 3;
  return 4;
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("zh-TW", {
    currency: "TWD",
    maximumFractionDigits: 0,
    style: "currency",
  }).format(value);
}

function classNames(...names: Array<string | false | null | undefined>) {
  return names.filter(Boolean).join(" ");
}
