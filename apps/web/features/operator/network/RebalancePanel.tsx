"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "../networkFindAreas.module.css";
import type { RebalanceQueueRow } from "../networkFindAreasViewModel";
import {
  describeDisclosureDefect,
  readConstraintDisclosure,
} from "./constraintDisclosure";
import { PlanGanttChart } from "./PlanGanttChart";

type RebalanceAction = "request-avm" | "complete-avm" | "solve-netplan" | "select-scenario" | "submit-review";

export type RebalanceReviewSubmission = {
  reason: string;
  actorRoleId?: string;
  actorName?: string;
  acknowledgedClasses?: string[];
  acknowledgementReason?: string;
  acknowledgementActorId?: string;
  approvalReceiptId?: string;
};

export type RebalancePanelProps = {
  apiError?: string | null;
  busyAction?: string | null;
  onCompleteAvm: (storeId: string) => void;
  onRequestAvm: (storeId: string) => void;
  onSelectScenario: (storeId: string, scenarioId: string) => void;
  onSolveNetPlan: (storeId: string) => void;
  onSubmitReview: (storeId: string, submission?: RebalanceReviewSubmission) => void;
  rows: RebalanceQueueRow[];
};

const stepLabels = ["低效確認", "AVM 估值", "NetPlan 三案", "送審", "核准"];

const lightTone: Record<string, string> = {
  G: "#2e9e63",
  A: "#d08700",
  R: "#c4342c",
};

/**
 * Which unmodelled classes a named authority may sign for is versioned
 * governance data the API resolves per tenant, so the console reads the split
 * off the scenario rather than holding a copy of it. When the API sends no
 * classification -- an older payload, or a surface with no policy registered --
 * every unmodelled class is treated as blocking: offering an acknowledgement
 * the server will refuse is worse than offering none.
 *
 * The policy split is only consulted once the disclosure itself has been read
 * as a complete ODP-FR-NET-002 partition. A payload that does not account for
 * all eight classes has not said which of them this solve is exposed on, so
 * there is nothing for the policy to classify -- and a blocked/acknowledgeable
 * split computed over a subset would describe a smaller exposure than the plan
 * actually carries. `readConstraintDisclosure` makes that call; everything
 * here is downstream of it.
 */
function classifyDisclosure(scenario: {
  modelledConstraintClasses?: unknown[] | null;
  modelled_constraint_classes?: unknown[] | null;
  unmodelledConstraintClasses?: unknown[] | null;
  unmodelled_constraint_classes?: unknown[] | null;
  blockedConstraintClasses?: unknown[] | null;
  acknowledgeableConstraintClasses?: unknown[] | null;
  disclosureUndeclared?: boolean;
} | undefined): {
  blocked: string[];
  acknowledgeable: string[];
  modelled: string[];
  unmodelled: string[];
  undeclared: boolean;
  defectDescription: string;
} {
  const reading = readConstraintDisclosure(scenario);
  if (reading.undeclared) {
    return {
      blocked: [],
      acknowledgeable: [],
      modelled: [],
      unmodelled: [],
      undeclared: true,
      defectDescription: describeDisclosureDefect(reading),
    };
  }
  const rawBlocked = scenario?.blockedConstraintClasses;
  const rawAcknowledgeable = scenario?.acknowledgeableConstraintClasses;
  if (rawBlocked == null || rawAcknowledgeable == null) {
    return {
      blocked: reading.unmodelled,
      acknowledgeable: [],
      modelled: reading.modelled,
      unmodelled: reading.unmodelled,
      undeclared: false,
      defectDescription: "",
    };
  }
  return {
    blocked: rawBlocked.map((item) => String(item)),
    acknowledgeable: rawAcknowledgeable.map((item) => String(item)),
    modelled: reading.modelled,
    unmodelled: reading.unmodelled,
    undeclared: false,
    defectDescription: "",
  };
}

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
  const [ackReason, setAckReason] = useState<string>("");
  const [ackActorName, setAckActorName] = useState<string>("");
  const [ackActorId, setAckActorId] = useState<string>("");
  const [ackReceiptId, setAckReceiptId] = useState<string>("");
  const [acknowledgedClasses, setAcknowledgedClasses] = useState<string[]>([]);

  useEffect(() => {
    if (rows.length > 0 && !rows.some((row) => row.id === selectedId)) {
      setSelectedId(rows[0].id);
    }
  }, [rows, selectedId]);

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) ?? rows[0],
    [rows, selectedId],
  );

  const selectedScenario = selected?.netPlanScenarios?.find(
    (scenario) => scenario.id === selected.selectedScenarioId
  );

  const selectedDisclosure = useMemo(
    () => classifyDisclosure(selectedScenario),
    [selectedScenario]
  );
  const selectedBlocked = selectedDisclosure.blocked;
  const selectedHasBlocked = selectedBlocked.length > 0 || selectedDisclosure.undeclared;
  const selectedAcknowledgeable = selectedDisclosure.acknowledgeable;
  const selectedNeedsAck = selectedAcknowledgeable.length > 0;
  const selectedPolicyVersionId = selectedScenario?.disclosurePolicyVersionId ?? null;

  // Every acknowledgeable class starts unticked. Pre-ticking them would produce
  // exactly the "acknowledge whatever is outstanding" signature the server
  // refuses, with the signer's name on it.
  useEffect(() => {
    setAcknowledgedClasses([]);
  }, [selected?.id, selected?.selectedScenarioId, selectedScenario?.id]);

  const isAckReasonValid = ackReason.trim().length > 0;
  const isAckActorValid = ackActorId.trim().length > 0;
  const isAckReceiptValid = ackReceiptId.trim().length > 0;
  const isAckClassesCovered =
    selectedAcknowledgeable.length === 0 ||
    selectedAcknowledgeable.every((c) => acknowledgedClasses.includes(c));
  const isAcknowledgementSatisfied =
    !selectedNeedsAck ||
    (isAckReasonValid && isAckClassesCovered && isAckActorValid && isAckReceiptValid);

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

  const cta = primaryCta(
    selected,
    isAcknowledgementSatisfied,
    selectedHasBlocked,
    selectedBlocked,
    selectedDisclosure.undeclared
  );
  const actionBusy = busyAction?.startsWith(`${selected.id}:`) ?? false;
  const avmP50 = typeof selected.avmP50 === "number" ? selected.avmP50 : null;

  function handlePrimary() {
    if (cta.disabled) return;
    if (cta.action === "request-avm") onRequestAvm(selected.id);
    if (cta.action === "complete-avm") onCompleteAvm(selected.id);
    if (cta.action === "solve-netplan") onSolveNetPlan(selected.id);
    if (cta.action === "submit-review") {
      onSubmitReview(selected.id, {
        reason: "Move scenario selected for Govern approval; relocation remains unexecuted.",
        actorName: ackActorName.trim() || undefined,
        acknowledgedClasses: selectedNeedsAck ? acknowledgedClasses : undefined,
        acknowledgementReason: selectedNeedsAck ? ackReason.trim() : undefined,
        acknowledgementActorId: selectedNeedsAck ? ackActorId.trim() : undefined,
        approvalReceiptId: selectedNeedsAck ? ackReceiptId.trim() : undefined,
      });
    }
  }

  return (
    <div className={styles.tabPanel} data-screen-label="Network 低效重配" data-testid="network-panel-rebalance" role="tabpanel">
      <div className={styles.panelHeader}>
        <div>
          <h3>低效重配 / Rebalance</h3>
          <p>AVM 服務估值 → NetPlan 三案 → Govern 核准；送審不代表搬遷已執行。</p>
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
              <strong>{selected.runtimeState.model} 暫時無法使用</strong>
              <span>可重試 · {selected.runtimeState.retryAfterSeconds ?? 300} 秒後再試</span>
            </div>
          ) : null}

          {avmP50 !== null ? (
            <section className={styles.rebalanceAvmBlock} data-testid={`rebalance-avm-${selected.id}`}>
              <div className={styles.rebalanceAvmHeader}>
                <span>AVM 估值（service output）</span>
                <span>{selected.avmConf ?? "—"}</span>
              </div>
              <div className={styles.avmValueP50}>{formatCurrency(avmP50)}</div>
              <div className={styles.avmBands}>
                <span>P10: {typeof selected.avmP10 === "number" ? formatCurrency(selected.avmP10) : "—"}</span>
                <span>P90: {typeof selected.avmP90 === "number" ? formatCurrency(selected.avmP90) : "—"}</span>
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
                  const cardDisclosure = classifyDisclosure(scenario);
                  const cardModelled = cardDisclosure.modelled;
                  const cardUnmodelled = cardDisclosure.unmodelled;
                  const cardBlocked = cardDisclosure.blocked;
                  const cardHasBlocked = cardBlocked.length > 0 || cardDisclosure.undeclared;
                  const cardNeedsAck = cardDisclosure.acknowledgeable.length > 0;

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
                        {scenario.isStale ? <span className={styles.staleBadge} data-testid={`scenario-stale-${scenarioId}`}>過期 / Stale</span> : null}
                        {scenario.isInfeasible ? <span className={styles.infeasibleBadge} data-testid={`scenario-infeasible-${scenarioId}`}>不可行</span> : null}
                        <span className={styles.roiValue}>{scenario.roi}</span>
                      </span>
                      <span className={styles.scenarioDetails}>
                        投資 {scenario.inv} · 回本 {scenario.payback} · 風險 {scenario.risk} · 時程 {scenario.time}
                      </span>

                      {/* Constraint disclosure badges for each scenario */}
                      <div className={styles.scenarioDisclosureRow} data-testid={`scenario-disclosure-${scenarioId}`}>
                        <span className={styles.scenarioModelledBadge} data-testid={`scenario-modelled-classes-${scenarioId}`}>
                          已建模: {cardModelled.length > 0 ? cardModelled.join(", ") : "（未申報）"}
                        </span>
                        {cardUnmodelled.length > 0 ? (
                          <span className={styles.scenarioUnmodelledBadge} data-testid={`scenario-unmodelled-classes-${scenarioId}`}>
                            未建模: {cardUnmodelled.join(", ")}
                          </span>
                        ) : null}
                        {cardHasBlocked ? (
                          <span className={styles.scenarioBlockedBadge} data-testid={`scenario-blocked-badge-${scenarioId}`}>
                            {cardDisclosure.undeclared
                              ? "揭露不完整 / 未申報建模範圍"
                              : `不可豁免阻擋: ${cardBlocked.join(", ")}`}
                          </span>
                        ) : cardNeedsAck ? (
                          <span className={styles.scenarioAckBadge} data-testid={`scenario-ack-required-badge-${scenarioId}`}>
                            需具名確認
                          </span>
                        ) : (
                          <span className={styles.scenarioFullyModelledBadge} data-testid={`scenario-fully-modelled-badge-${scenarioId}`}>
                            全部已建模
                          </span>
                        )}
                      </div>

                      {scenario.diagnostics && scenario.diagnostics.length > 0 ? (
                        <div className={styles.infeasibilityDiagnostics} data-testid={`scenario-diagnostics-${scenarioId}`}>
                          <strong>不可行性診斷 (Infeasibility Diagnostics)</strong>
                          {scenario.diagnostics.map((diag, idx) => (
                            <div key={idx} className={styles.diagnosticItem} data-testid={`diagnostic-item-${idx}`}>
                              <div><span>違反約束 (violated_constraint):</span> <code data-field="violated_constraint">{diag.violated_constraint}</code></div>
                              <div><span>受影響門市 (affected_stores):</span> <span data-field="affected_stores">{diag.affected_stores.join(", ")}</span></div>
                              <div><span>需放寬條件 (required_relaxation):</span> <span data-field="required_relaxation">{diag.required_relaxation}</span></div>
                              <div><span>商業影響 (business_impact):</span> <span data-field="business_impact">{diag.business_impact}</span></div>
                              <div><span>建議行動 (suggested_action):</span> <span data-field="suggested_action">{diag.suggested_action}</span></div>
                            </div>
                          ))}
                        </div>
                      ) : null}
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
              <PlanGanttChart
                scenarioId={selectedScenario?.id ?? selected.selectedScenarioId}
                scenarioName={selectedScenario?.name ?? `NetPlan: ${selected.storeName}`}
                policyId={selectedScenario?.policy_id || selectedScenario?.policyId}
                policyVersion={selectedScenario?.policy_version || selectedScenario?.policyVersion}
                solverVersion={selectedScenario?.solverVersion}
                objectiveScore={selectedScenario?.score}
                actions={selectedScenario?.actions || selectedScenario?.selected_actions}
                bindingConstraints={selectedScenario?.bindingConstraints || selectedScenario?.binding_constraints || (selectedScenario?.diagnostics?.map((d) => d.violated_constraint) ?? [])}
                // The payload as it arrived, not this component's reading of
                // it. The chart partitions the eight classes itself, so handing
                // it the emptied lists would only cost it the ability to say
                // *which* part of the disclosure is missing -- while
                // `disclosureUndeclared` still guarantees it cannot end up more
                // permissive than the panel around it.
                modelledConstraintClasses={selectedScenario?.modelledConstraintClasses || selectedScenario?.modelled_constraint_classes}
                unmodelledConstraintClasses={selectedScenario?.unmodelledConstraintClasses || selectedScenario?.unmodelled_constraint_classes}
                disclosureUndeclared={selectedDisclosure.undeclared}
                dependencies={selectedScenario?.dependencies}
                diagnostics={selectedScenario?.diagnostics}
              />
            </section>
          ) : null}

          {/* Blocked constraint classes alert */}
          {selected.status === "netplanreview" && selectedHasBlocked ? (
            <div className={styles.rebalanceBlockedAlert} data-testid="rebalance-blocked-alert" role="alert">
              <span className={styles.blockedIcon}>⚠️</span>
              <div>
                <strong>
                  {selectedDisclosure.undeclared
                    ? "本方案未完整申報硬限制建模範圍 (Disclosure undeclared)"
                    : `存在未建模且不可豁免之硬限制 (Blocked: ${selectedBlocked.join(", ")})`}
                </strong>
                {selectedDisclosure.undeclared ? (
                  <p data-testid="rebalance-disclosure-defect">
                    求解結果未完整說明 ODP-FR-NET-002 八類硬限制的建模狀態
                    {selectedDisclosure.defectDescription
                      ? `（${selectedDisclosure.defectDescription}）`
                      : ""}
                    。未申報不等於全部已驗證，因此無法送審；請重新求解並取得完整的硬限制揭露。
                  </p>
                ) : (
                  <p>
                    {selectedBlocked.join(", ")} 未在此次求解中被約束，依治理政策
                    {selectedPolicyVersionId
                      ? `（${selectedPolicyVersionId}）`
                      : "（本介面未註冊揭露政策，未建模類別一律視為阻擋）"}
                    不可由具名簽核豁免，無法進行送審。請補上對應上限後重新求解。
                  </p>
                )}
              </div>
            </div>
          ) : null}

          {/* Acknowledgeable constraint classes form */}
          {selected.status === "netplanreview" && selectedNeedsAck && !selectedHasBlocked ? (
            <section
              className={styles.rebalanceAcknowledgementSection}
              data-testid="rebalance-acknowledgement-section"
              aria-label="未建模限制具名風險確認表單"
            >
              <div className={styles.acknowledgementHeader}>
                <strong>🛡️ 未建模限制具名風險確認 (Constraint Disclosure Acknowledgement)</strong>
                <p>
                  本方案未在求解模型中建模以下限制類別，送審前須由授權主管具名確認線下風險與因應措施。
                  簽核權限來自管理核准收據，不由本表單自行宣告。
                </p>
                {selectedPolicyVersionId ? (
                  <small className={styles.muted} data-testid="acknowledgement-policy-version">
                    適用政策版本：{selectedPolicyVersionId}
                  </small>
                ) : null}
              </div>

              <div className={styles.acknowledgementClassList} data-testid="acknowledgement-class-list">
                {selectedAcknowledgeable.map((c) => {
                  const isChecked = acknowledgedClasses.includes(c);
                  const impactDesc =
                    c === "LEASE"
                      ? "租約可行性、檔期條件與解約金未於求解器內驗證，需線下商務確認。"
                      : c === "SEQUENCING"
                      ? "多期排程與工程工期先後次序未於模型內限制，需施工團隊排程確認。"
                      : "未於求解器內建模，需線下確認。";
                  return (
                    <label key={c} className={styles.acknowledgementClassItem} data-testid={`ack-class-item-${c}`}>
                      <input
                        type="checkbox"
                        checked={isChecked}
                        data-testid={`ack-class-${c}`}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setAcknowledgedClasses((prev) => Array.from(new Set([...prev, c])));
                          } else {
                            setAcknowledgedClasses((prev) => prev.filter((item) => item !== c));
                          }
                        }}
                      />
                      <div>
                        <strong>{c}</strong>
                        <p>{impactDesc}</p>
                      </div>
                    </label>
                  );
                })}
              </div>

              <div className={styles.acknowledgementFieldsGrid}>
                {/* No "authorized role" field. A submitter who could type their
                    own authorising role would be authorising themselves; the
                    server reads the role off the management approval receipt
                    named below and refuses if it carries no such authority. */}
                <div className={styles.ackFieldGroup}>
                  <label htmlFor={`ack-receipt-${selected.id}`}>
                    管理核准收據編號 (Approval Receipt) <span className={styles.requiredMark}>*必填</span>
                  </label>
                  <input
                    id={`ack-receipt-${selected.id}`}
                    type="text"
                    className={styles.inputField}
                    data-testid="acknowledgement-receipt-input"
                    value={ackReceiptId}
                    onChange={(e) => setAckReceiptId(e.target.value)}
                  />
                  <small className={styles.muted}>簽核權限由此收據認定，本欄不可留空。</small>
                </div>

                <div className={styles.ackFieldGroup}>
                  <label htmlFor={`ack-actor-id-${selected.id}`}>
                    簽核人主體識別 (Principal Id) <span className={styles.requiredMark}>*必填</span>
                  </label>
                  <input
                    id={`ack-actor-id-${selected.id}`}
                    type="text"
                    className={styles.inputField}
                    data-testid="acknowledgement-actor-id-input"
                    value={ackActorId}
                    onChange={(e) => setAckActorId(e.target.value)}
                  />
                  <small className={styles.muted}>須與收據上的核准主體一致，否則送審會被退回。</small>
                </div>

                <div className={styles.ackFieldGroup}>
                  <label htmlFor={`ack-actor-${selected.id}`}>簽核人姓名 (Actor Name)</label>
                  <input
                    id={`ack-actor-${selected.id}`}
                    type="text"
                    className={styles.inputField}
                    data-testid="acknowledgement-actor-input"
                    value={ackActorName}
                    onChange={(e) => setAckActorName(e.target.value)}
                  />
                </div>

                <div className={styles.ackFieldGroupFull}>
                  <label htmlFor={`ack-reason-${selected.id}`}>
                    具名風險確認理由 (Acknowledgement Reason) <span className={styles.requiredMark}>*必填</span>
                  </label>
                  <textarea
                    id={`ack-reason-${selected.id}`}
                    className={styles.textareaField}
                    data-testid="acknowledgement-reason-input"
                    placeholder="請詳細填寫未建模限制線下審查與風險確認理由 (不可為空白)..."
                    rows={3}
                    value={ackReason}
                    onChange={(e) => setAckReason(e.target.value)}
                  />
                  {!isAckReasonValid && ackReason !== "" ? (
                    <span className={styles.fieldError}>確認理由不可僅包含空白字元</span>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          <section className={styles.rebalanceBoundary} data-testid={`rebalance-boundary-${selected.id}`}>
            <strong>執行邊界 / Execution boundary</strong>
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

function primaryCta(
  row: RebalanceQueueRow,
  isAckSatisfied: boolean = true,
  hasBlocked: boolean = false,
  blockedClasses: string[] = [],
  undeclared: boolean = false
): {
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
    if (!row.selectedScenarioId) {
      return {
        action: "submit-review",
        disabled: true,
        label: "送審（Rebalance Review）",
        note: "請先選擇 Keep / Move / Exit 其中一案。",
      };
    }
    if (hasBlocked) {
      return {
        action: "submit-review",
        disabled: true,
        label: "送審（無法送審）",
        // An undeclared disclosure has no blocked class list to name -- it is
        // blocked precisely because nothing established which classes are at
        // stake. Printing an empty parenthesis would read as "blocked by
        // nothing", which is the sentence this branch exists to avoid.
        note: undeclared
          ? "方案未完整申報 ODP-FR-NET-002 八類硬限制的建模狀態，符合性無法判定，無法送審。"
          : `方案包含未建模且不可豁免之限制 (${blockedClasses.join(", ")})，無法送審。`,
      };
    }
    if (!isAckSatisfied) {
      return {
        action: "submit-review",
        disabled: true,
        label: "送審（需具名確認）",
        note: "送審前需勾選每一項未建模限制，並填寫確認理由、簽核人主體與管理核准收據。",
      };
    }
    return {
      action: "submit-review",
      disabled: false,
      label: "送審（Rebalance Review）",
      note: "送審後由 Govern 核准中心決策，將攜帶具名未建模限制確認收據。",
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
