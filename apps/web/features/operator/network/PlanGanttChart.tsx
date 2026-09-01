"use client";

import React, { useId, useMemo, useState } from "react";
import styles from "./planGanttChart.module.css";
import type { NetPlanDiagnostic } from "../types";

export type NetworkPlanActionType = "OPEN" | "KEEP" | "IMPROVE" | "MOVE" | "EXIT" | "TRANSFER";

export interface PlanGanttActionItem {
  id?: string;
  entity_id: string;
  entityId?: string;
  entity_name?: string;
  entityName?: string;
  quarter?: string;
  planning_quarter?: string;
  planningQuarter?: string;
  action: NetworkPlanActionType | string;
  expected_gross_margin?: number;
  expectedGrossMargin?: number;
  budget_cost?: number;
  budgetCost?: number;
  risk_score?: number;
  riskScore?: number;
  capacity_delta?: number;
  capacityDelta?: number;
  source_snapshot_ids?: string[];
  sourceSnapshotIds?: string[];
  notes?: string[];
  depends_on?: string[];
  dependsOn?: string[];
  is_binding?: boolean;
  isBinding?: boolean;
  binding_reasons?: string[];
  bindingReasons?: string[];
  entity_type?: "existing_store" | "candidate_site" | string;
  entityType?: "existing_store" | "candidate_site" | string;
}

export interface PlanGanttDependency {
  id?: string;
  fromEntityId: string;
  fromQuarter?: string;
  toEntityId: string;
  toQuarter?: string;
  type?: "precedence" | "temporal_hard_constraint" | "move_dependency" | "relocation";
  label?: string;
  description?: string;
}

export interface PlanGanttProps {
  scenarioId?: string;
  scenarioName?: string;
  policyId?: string;
  policy_id?: string;
  policyVersion?: string;
  policy_version?: string;
  solverVersion?: string;
  solver_version?: string;
  objectiveScore?: number;
  actions?: PlanGanttActionItem[];
  selected_actions?: PlanGanttActionItem[];
  bindingConstraints?: string[];
  binding_constraints?: string[];
  dependencies?: PlanGanttDependency[];
  diagnostics?: NetPlanDiagnostic[];
  quarters?: string[];
  defaultView?: "gantt" | "table";
  onActionClick?: (action: PlanGanttActionItem) => void;
  className?: string;
  isApprovalView?: boolean;
}

export const ACTION_COLOR_CLASSES: Record<string, string> = {
  OPEN: styles.actionOpen,
  KEEP: styles.actionKeep,
  IMPROVE: styles.actionImprove,
  MOVE: styles.actionMove,
  EXIT: styles.actionExit,
  TRANSFER: styles.actionTransfer,
};

export const ACTION_LABELS: Record<string, string> = {
  OPEN: "OPEN / 新設",
  KEEP: "KEEP / 維持",
  IMPROVE: "IMPROVE / 改善",
  MOVE: "MOVE / 搬遷",
  EXIT: "EXIT / 關店",
  TRANSFER: "TRANSFER / 轉移",
};

export const ACTION_LEGEND = [
  { type: "OPEN", label: "OPEN 新設", className: styles.actionOpen },
  { type: "KEEP", label: "KEEP 維持", className: styles.actionKeep },
  { type: "IMPROVE", label: "IMPROVE 改善", className: styles.actionImprove },
  { type: "MOVE", label: "MOVE 搬遷", className: styles.actionMove },
  { type: "EXIT", label: "EXIT 關店", className: styles.actionExit },
  { type: "TRANSFER", label: "TRANSFER 轉移", className: styles.actionTransfer },
];

function normalizeAction(raw: string | undefined): NetworkPlanActionType {
  const upper = String(raw || "KEEP").toUpperCase().trim();
  if (upper in ACTION_COLOR_CLASSES) {
    return upper as NetworkPlanActionType;
  }
  return "KEEP";
}

function parseEntityAndQuarter(actionItem: PlanGanttActionItem): {
  entityId: string;
  entityName: string;
  quarter: string;
} {
  const rawEntityId = actionItem.entity_id || actionItem.entityId || "UNKNOWN";
  const rawQuarter = actionItem.quarter || actionItem.planning_quarter || actionItem.planningQuarter;
  const rawName = actionItem.entity_name || actionItem.entityName;

  if (rawQuarter) {
    return {
      entityId: rawEntityId,
      entityName: rawName || rawEntityId,
      quarter: rawQuarter.toUpperCase().trim(),
    };
  }

  // If entity_id is formatted like "STORE-101:2026Q1" or "STORE-101:Q1"
  const colonMatch = rawEntityId.match(/^(.+):((?:20\d{2})?Q[1-4])$/i);
  if (colonMatch) {
    return {
      entityId: colonMatch[1],
      entityName: rawName || colonMatch[1],
      quarter: colonMatch[2].toUpperCase().trim(),
    };
  }

  return {
    entityId: rawEntityId,
    entityName: rawName || rawEntityId,
    quarter: "",
  };
}

function formatCurrency(val?: number): string {
  if (val === undefined || val === null || isNaN(val)) return "—";
  if (Math.abs(val) >= 1_000_000) {
    return `NT$${(val / 1_000_000).toFixed(1)}M`;
  }
  if (Math.abs(val) >= 1_000) {
    return `NT$${(val / 1_000).toFixed(0)}K`;
  }
  return `NT$${val.toLocaleString()}`;
}

function checkActionBinding(
  actionItem: PlanGanttActionItem,
  entityId: string,
  diagnostics?: NetPlanDiagnostic[],
  bindingConstraints?: string[]
): { isBinding: boolean; bindingReasons: string[] } {
  const isExplicitBinding = Boolean(actionItem.is_binding || actionItem.isBinding);
  const isDiagnosed = Boolean(
    diagnostics?.some((d) => d.affected_stores.includes(entityId))
  );
  const matchingConstraints = (bindingConstraints || []).filter((c) =>
    c.toLowerCase().includes(entityId.toLowerCase())
  );
  const explicitReasons = actionItem.binding_reasons || actionItem.bindingReasons || [];
  const allReasons = Array.from(new Set([...explicitReasons, ...matchingConstraints]));
  const isBinding = isExplicitBinding || isDiagnosed || matchingConstraints.length > 0 || explicitReasons.length > 0;
  return { isBinding, bindingReasons: allReasons };
}

export function PlanGanttChart({
  scenarioId = "NP-SCENARIO",
  scenarioName = "季度規劃甘特圖",
  policyId,
  policy_id,
  policyVersion,
  policy_version,
  solverVersion,
  solver_version,
  objectiveScore,
  actions,
  selected_actions,
  bindingConstraints,
  binding_constraints,
  dependencies,
  diagnostics,
  quarters: customQuarters,
  defaultView = "gantt",
  onActionClick,
  className,
  isApprovalView = true,
}: PlanGanttProps) {
  const [activeView, setActiveView] = useState<"gantt" | "table">(defaultView);
  const arrowMarkerId = useId();

  const effectivePolicyId = policyId || policy_id || null;
  const effectivePolicyVersion = policyVersion || policy_version || null;
  const effectiveSolverVersion = solverVersion || solver_version || null;
  const rawActions = useMemo(() => actions || selected_actions || [], [actions, selected_actions]);
  const rawBindingConstraints = useMemo(
    () => bindingConstraints || binding_constraints || [],
    [bindingConstraints, binding_constraints]
  );

  // Discover and sort all backend-provided quarters
  const quarters = useMemo(() => {
    if (customQuarters && customQuarters.length > 0) {
      return customQuarters;
    }
    const foundQuarters = new Set<string>();
    rawActions.forEach((item) => {
      const parsed = parseEntityAndQuarter(item);
      if (parsed.quarter) {
        foundQuarters.add(parsed.quarter);
      }
    });
    return Array.from(foundQuarters).sort();
  }, [customQuarters, rawActions]);

  // Group actions by planning entity
  const { entities, entityActionMap, parsedActionList } = useMemo(() => {
    const entitySet = new Set<string>();
    const entityNames: Record<string, string> = {};
    const entityTypes: Record<string, string> = {};
    const map: Record<string, Record<string, PlanGanttActionItem>> = {};
    const flatList: Array<{
      entityId: string;
      entityName: string;
      quarter: string;
      action: NetworkPlanActionType;
      raw: PlanGanttActionItem;
      isBinding: boolean;
      bindingReasons: string[];
      dependencies: string[];
    }> = [];

    rawActions.forEach((item) => {
      const parsed = parseEntityAndQuarter(item);
      const actionType = normalizeAction(item.action);

      entitySet.add(parsed.entityId);
      entityNames[parsed.entityId] = parsed.entityName;
      if (item.entity_type || item.entityType) {
        entityTypes[parsed.entityId] = (item.entity_type || item.entityType)!;
      }

      if (parsed.quarter) {
        if (!map[parsed.entityId]) {
          map[parsed.entityId] = {};
        }
        map[parsed.entityId][parsed.quarter] = item;
      }

      // Check binding constraint status strictly scoped to this entity
      const { isBinding, bindingReasons } = checkActionBinding(
        item,
        parsed.entityId,
        diagnostics,
        rawBindingConstraints
      );

      const itemDependencies = item.depends_on || item.dependsOn || [];

      flatList.push({
        entityId: parsed.entityId,
        entityName: parsed.entityName,
        quarter: parsed.quarter,
        action: actionType,
        raw: item,
        isBinding,
        bindingReasons,
        dependencies: itemDependencies,
      });
    });

    const sortedEntities = Array.from(entitySet).map((id) => ({
      id,
      name: entityNames[id] || id,
      type: entityTypes[id] || (id.startsWith("CS-") || id.startsWith("SITE-") ? "candidate_site" : "existing_store"),
    }));

    return {
      entities: sortedEntities,
      entityActionMap: map,
      parsedActionList: flatList,
    };
  }, [rawActions, diagnostics, rawBindingConstraints]);

  // Resolve authoritative ODP-FR-NET-002 dependencies (no synthetic cross-quarter generation)
  const resolvedDependencies = useMemo<PlanGanttDependency[]>(() => {
    if (dependencies && dependencies.length > 0) {
      return dependencies;
    }

    const result: PlanGanttDependency[] = [];

    // Explicit item dependencies only
    parsedActionList.forEach((item) => {
      item.dependencies.forEach((depEntityId, idx) => {
        result.push({
          id: `dep-explicit-${item.entityId}-${depEntityId}-${idx}`,
          fromEntityId: depEntityId,
          toEntityId: item.entityId,
          toQuarter: item.quarter || undefined,
          type: "precedence",
          label: "時序相依 (Precedence)",
          description: `ODP-FR-NET-002: ${depEntityId} 必須在 ${item.entityId}${item.quarter ? ` (${item.quarter})` : ""} 之前完成`,
        });
      });
    });

    return result;
  }, [dependencies, parsedActionList]);

  return (
    <section
      className={`${styles.ganttContainer} ${className || ""}`}
      data-screen-label="NetPlan 季度甘特圖"
      data-testid="netplan-gantt-chart"
      aria-label="NetPlan 季度甘特圖與等價表格"
    >
      {/* Header & Policy Metadata Row */}
      <header className={styles.headerRow}>
        <div className={styles.titleArea}>
          <h4>{scenarioName}</h4>
          <p>
            ODP-FR-NET-007 季度甘特圖 · ODP-FR-NET-002 時序硬限制相依 · 後端求解輸出
          </p>
        </div>

        {/* Policy Metadata Section (Always rendered for verification & approval audit) */}
        <div
          className={styles.policyBadgeGroup}
          data-testid="gantt-policy-metadata"
          aria-label="治理政策與版本識別"
        >
          <span
            className={`${styles.policyBadge} ${styles.policyBadgeStrong}`}
            data-testid="gantt-policy-id"
            title="方案政策識別碼 (policy_id)"
          >
            policy_id: <strong>{effectivePolicyId ?? "—"}</strong>
          </span>
          <span
            className={`${styles.policyBadge} ${styles.policyBadgeStrong}`}
            data-testid="gantt-policy-version"
            title="方案政策版本 (policy_version)"
          >
            policy_version: <strong>{effectivePolicyVersion ?? "—"}</strong>
          </span>
          {effectiveSolverVersion ? (
            <span className={styles.policyBadge} data-testid="gantt-solver-version">
              solver: {effectiveSolverVersion}
            </span>
          ) : null}
          {objectiveScore !== undefined ? (
            <span className={styles.policyBadge} data-testid="gantt-objective-score">
              score: {objectiveScore.toFixed(1)}
            </span>
          ) : null}
        </div>

        {/* View Toggle (Gantt vs Equivalent Table) */}
        <div className={styles.viewToggleGroup} role="group" aria-label="檢視模式切換">
          <button
            type="button"
            className={`${styles.viewToggleButton} ${activeView === "gantt" ? styles.viewToggleButtonActive : ""}`}
            data-testid="view-toggle-gantt"
            aria-pressed={activeView === "gantt"}
            onClick={() => setActiveView("gantt")}
          >
            甘特圖檢視 (Gantt)
          </button>
          <button
            type="button"
            className={`${styles.viewToggleButton} ${activeView === "table" ? styles.viewToggleButtonActive : ""}`}
            data-testid="view-toggle-table"
            aria-pressed={activeView === "table"}
            onClick={() => setActiveView("table")}
          >
            等價表格檢視 (Table)
          </button>
        </div>
      </header>

      {/* Action Color Legend */}
      <div className={styles.legendBar} aria-label="行動類型圖例" data-testid="gantt-legend-bar">
        <span className={styles.legendTitle}>行動著色圖例:</span>
        {ACTION_LEGEND.map((item) => (
          <span key={item.type} className={styles.legendItem} data-testid={`legend-item-${item.type}`}>
            <span className={`${styles.legendColorBox} ${item.className}`} />
            <span>{item.label}</span>
          </span>
        ))}
      </div>

      {/* Binding Constraints & Diagnostics Alert Callout */}
      {(rawBindingConstraints.length > 0 || (diagnostics && diagnostics.length > 0)) && (
        <div
          className={styles.bindingAlertBox}
          data-testid="gantt-binding-constraints-summary"
          role="alert"
        >
          <span aria-hidden="true">⚠️</span>
          <div>
            <strong>Binding Constraints (緊縮約束 / 衝突限制):</strong>
            {rawBindingConstraints.length > 0 && (
              <div>
                約束項目: {rawBindingConstraints.join(" · ")}
              </div>
            )}
            {diagnostics && diagnostics.length > 0 && (
              <div>
                診斷衝擊: {diagnostics.map((d) => d.business_impact).join(" · ")}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Gantt Chart View */}
      {activeView === "gantt" && (
        <div className={styles.ganttScrollWrapper} data-testid="gantt-view-container">
          {entities.length === 0 || quarters.length === 0 ? (
            <div className={styles.emptyState} data-testid="gantt-empty-state">
              尚無規劃實體或季度行動資料
            </div>
          ) : (
            <div
              className={styles.ganttTableContainer}
              style={{ "--gantt-cols": quarters.length } as React.CSSProperties}
            >
              {/* Quarters Header Row */}
              <div
                className={styles.ganttHeaderGrid}
                data-testid="gantt-quarters-header"
                role="row"
              >
                <div className={styles.entityHeaderCell}>規劃實體 (Planning Entity)</div>
                {quarters.map((q) => (
                  <div
                    key={q}
                    className={styles.quarterHeaderCell}
                    data-testid={`gantt-quarter-${q}`}
                    role="columnheader"
                  >
                    <strong>{q}</strong>
                    <small>季度規劃</small>
                  </div>
                ))}
              </div>

              {/* Rows and Actions Grid */}
              <div className={styles.ganttRowsContainer}>
                {/* SVG Overlay for ODP-FR-NET-002 Temporal Dependency Lines */}
                {resolvedDependencies.length > 0 && (
                  <svg
                    className={styles.dependencySvgLayer}
                    data-testid="gantt-dependencies-layer"
                    aria-hidden="true"
                  >
                    <defs>
                      <marker
                        id={`arrow-${arrowMarkerId}`}
                        viewBox="0 0 10 10"
                        refX="8"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" className={styles.dependencyArrow} />
                      </marker>
                    </defs>
                    {resolvedDependencies.map((dep, depIdx) => {
                      const fromEntityIdx = entities.findIndex((e) => e.id === dep.fromEntityId);
                      const toEntityIdx = entities.findIndex((e) => e.id === dep.toEntityId);
                      const fromQuarterIdx = dep.fromQuarter ? quarters.indexOf(dep.fromQuarter) : 0;
                      const toQuarterIdx = dep.toQuarter ? quarters.indexOf(dep.toQuarter) : quarters.length - 1;

                      if (fromEntityIdx === -1 || toEntityIdx === -1 || fromQuarterIdx === -1 || toQuarterIdx === -1) {
                        return null;
                      }

                      const numCols = quarters.length;
                      const numRows = entities.length;
                      const rowHeight = 58;

                      const startX = ((fromQuarterIdx + 0.6) / numCols) * 100;
                      const startY = fromEntityIdx * rowHeight + 29;
                      const endX = ((toQuarterIdx + 0.3) / numCols) * 100;
                      const endY = toEntityIdx * rowHeight + 29;

                      const deltaX = endX - startX;
                      const deltaY = endY - startY;

                      const pathD =
                        Math.abs(deltaY) < 5
                          ? `M ${startX}% ${startY} L ${endX}% ${endY}`
                          : `M ${startX}% ${startY} C ${startX + deltaX * 0.5}% ${startY}, ${startX + deltaX * 0.5}% ${endY}, ${endX}% ${endY}`;

                      return (
                        <path
                          key={dep.id || `dep-${depIdx}`}
                          d={pathD}
                          className={styles.dependencyPath}
                          markerEnd={`url(#arrow-${arrowMarkerId})`}
                          data-testid="gantt-dependency-line"
                        />
                      );
                    })}
                  </svg>
                )}

                {entities.map((entity) => (
                  <div
                    key={entity.id}
                    className={styles.ganttRow}
                    data-testid={`gantt-row-${entity.id}`}
                    role="row"
                  >
                    {/* Entity Header Cell */}
                    <div
                      className={styles.entityLabelCell}
                      data-testid={`gantt-row-header-${entity.id}`}
                    >
                      <span className={styles.entityIdText}>{entity.id}</span>
                      <span className={styles.entitySubText}>
                        {entity.name !== entity.id ? entity.name : entity.type === "candidate_site" ? "候選新址" : "既有門市"}
                      </span>
                    </div>

                    {/* Quarter Cells */}
                    {quarters.map((q) => {
                      const actionItem = entityActionMap[entity.id]?.[q];
                      if (!actionItem) {
                        return (
                          <div
                            key={q}
                            className={styles.ganttCell}
                            data-testid={`gantt-cell-${entity.id}-${q}`}
                          />
                        );
                      }

                      const actionType = normalizeAction(actionItem.action);
                      const { isBinding } = checkActionBinding(
                        actionItem,
                        entity.id,
                        diagnostics,
                        rawBindingConstraints
                      );
                      const gm = actionItem.expected_gross_margin ?? actionItem.expectedGrossMargin;
                      const cost = actionItem.budget_cost ?? actionItem.budgetCost;

                      return (
                        <div
                          key={q}
                          className={styles.ganttCell}
                          data-testid={`gantt-cell-${entity.id}-${q}`}
                        >
                          <div
                            className={`${styles.actionBar} ${ACTION_COLOR_CLASSES[actionType] || styles.actionKeep} ${isBinding ? styles.actionBarBinding : ""}`}
                            data-action={actionType}
                            data-is-binding={String(isBinding)}
                            data-testid={`gantt-bar-${entity.id}-${q}`}
                            onClick={() => onActionClick?.(actionItem)}
                            role="button"
                            tabIndex={0}
                            aria-label={`${entity.id} 在 ${q} 執行 ${actionType} 行動${isBinding ? " (Binding Constraint)" : ""}`}
                          >
                            <div className={styles.actionBarTop}>
                              <span className={styles.actionBadgeText}>{actionType}</span>
                              {isBinding && (
                                <span
                                  className={styles.bindingBadge}
                                  data-testid={`gantt-binding-badge-${entity.id}-${q}`}
                                  title="Binding Constraint 緊縮約束"
                                >
                                  ⚠️ Binding
                                </span>
                              )}
                            </div>
                            <div className={styles.actionMetaRow}>
                              {cost !== undefined && <span>{formatCurrency(cost)}</span>}
                              {gm !== undefined && <span>GM: {formatCurrency(gm)}</span>}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Equivalent Table View (Full Data Parity) */}
      {activeView === "table" && (
        <div className={styles.tableViewWrapper} data-testid="gantt-equivalent-table">
          <table className={styles.equivalentTable} role="table" aria-label="NetPlan 季度行動等價表格">
            <thead>
              <tr>
                <th scope="col">規劃實體 (Entity ID)</th>
                <th scope="col">名稱/類型</th>
                <th scope="col">季度 (Quarter)</th>
                <th scope="col">行動 (Action)</th>
                <th scope="col">預估毛利 (Expected GM)</th>
                <th scope="col">資本/預算 (Budget Cost)</th>
                <th scope="col">風險評分 (Risk)</th>
                <th scope="col">容量增量 (Capacity)</th>
                <th scope="col">ODP-FR-NET-002 時序相依</th>
                <th scope="col">Binding Constraints 狀態</th>
              </tr>
            </thead>
            <tbody>
              {parsedActionList.length === 0 ? (
                <tr>
                  <td colSpan={10} style={{ textAlign: "center", padding: "24px", color: "#64748b" }}>
                    尚無規劃行動資料
                  </td>
                </tr>
              ) : (
                parsedActionList.map((item, idx) => (
                  <tr key={`${item.entityId}-${item.quarter}-${idx}`} data-testid={`table-row-${item.entityId}-${item.quarter}`}>
                    <td className={styles.monoText}>
                      <strong>{item.entityId}</strong>
                    </td>
                    <td>{item.entityName}</td>
                    <td className={styles.monoText}>
                      <strong>{item.quarter}</strong>
                    </td>
                    <td>
                      <span
                        className={`${styles.actionTag} ${ACTION_COLOR_CLASSES[item.action] || styles.actionKeep}`}
                        data-action={item.action}
                      >
                        {ACTION_LABELS[item.action] || item.action}
                      </span>
                    </td>
                    <td>{formatCurrency(item.raw.expected_gross_margin ?? item.raw.expectedGrossMargin)}</td>
                    <td>{formatCurrency(item.raw.budget_cost ?? item.raw.budgetCost)}</td>
                    <td>
                      {item.raw.risk_score !== undefined || item.raw.riskScore !== undefined
                        ? (item.raw.risk_score ?? item.raw.riskScore)?.toFixed(2)
                        : "—"}
                    </td>
                    <td>
                      {item.raw.capacity_delta !== undefined || item.raw.capacityDelta !== undefined
                        ? `${(item.raw.capacity_delta ?? item.raw.capacityDelta)! > 0 ? "+" : ""}${item.raw.capacity_delta ?? item.raw.capacityDelta}`
                        : "—"}
                    </td>
                    <td>
                      {item.dependencies.length > 0 ? (
                        <span className={styles.dependencyText}>
                          前置: {item.dependencies.join(", ")}
                        </span>
                      ) : (
                        <span style={{ color: "#94a3b8" }}>—</span>
                      )}
                    </td>
                    <td>
                      {item.isBinding ? (
                        <span className={styles.constraintTag} data-testid={`table-binding-tag-${item.entityId}-${item.quarter || 'noq'}`}>
                          ⚠️ {item.bindingReasons.length > 0 ? item.bindingReasons.join(", ") : "Binding Constraint"}
                        </span>
                      ) : (
                        <span style={{ color: "#10b981", fontSize: "11px" }}>✓ 無衝突</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ODP-FR-NET-002 Dependencies Explanation Section */}
      {resolvedDependencies.length > 0 && (
        <section
          className={styles.dependenciesSection}
          data-testid="gantt-dependencies-list"
          aria-label="ODP-FR-NET-002 時序相依限制清單"
        >
          <h5>ODP-FR-NET-002 時序硬限制相依 (Temporal Hard Constraints):</h5>
          <ul className={styles.dependenciesList}>
            {resolvedDependencies.map((dep, idx) => (
              <li key={dep.id || idx}>
                <strong>{dep.fromEntityId}</strong> {dep.fromQuarter ? `(${dep.fromQuarter})` : ""} →{" "}
                <strong>{dep.toEntityId}</strong> {dep.toQuarter ? `(${dep.toQuarter})` : ""}:{" "}
                <span>{dep.description || dep.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
