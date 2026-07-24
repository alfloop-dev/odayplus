import Link from "next/link";
import type { NetPlanScenarioSummary } from "@oday-plus/openapi-client";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { DataSourceBadge } from "../../src/components/DataSourceBadge.tsx";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "../operations/ProductionDataState.tsx";
import {
  approvalLabel,
  formatActionCounts,
  freshness,
  NETPLAN_POLICY_VERSION,
  scenarioStatusTone,
  scenarios,
  selectedFromQuery,
  solverStatusTone,
  TERMINAL_STATUSES,
  VALID_TRANSITIONS,
  type NetPlanRouteKey,
  type NetPlanScenario,
} from "./data.ts";
import styles from "./netplan.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type NetPlanWorkspaceProps = {
  view?: NetPlanRouteKey;
  scenarioId?: string;
  searchParams?: SearchParams;
  /** Live `GET /netplan/scenarios` binding; supplied by the server route. */
  liveScenarios?: ApiBinding<NetPlanScenarioSummary>;
  isProduction?: boolean;
};

export function NetPlanWorkspace({
  view = "overview",
  scenarioId,
  searchParams = {},
  liveScenarios,
  isProduction: isProductionProp,
}: NetPlanWorkspaceProps) {
  if (resolveProductionMode(isProductionProp)) {
    return <ProductionNetPlanWorkspace binding={liveScenarios} scenarioId={scenarioId} view={view} />;
  }
  if (view === "scenarios") return <ScenariosListPage searchParams={searchParams} />;
  if (view === "scenarioDetail") return <ScenarioDetailPage scenarioId={scenarioId} />;
  return <NetPlanOverview liveScenarios={liveScenarios} />;
}

function ProductionNetPlanWorkspace({
  binding,
  scenarioId,
  view,
}: {
  binding?: ApiBinding<NetPlanScenarioSummary>;
  scenarioId?: string;
  view: NetPlanRouteKey;
}) {
  const state = productionBindingState(binding);
  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: "網絡規劃 NetPlan", href: "/netplan" },
          { label: scenarioId ?? (view === "overview" ? "Overview" : "情境") },
        ]}
        lastUpdated={binding?.fetchedAt ? `API checked ${binding.fetchedAt}` : "Live source not available"}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        summary="Production NetPlan workspace. Scenario rows come only from the NetPlan API."
        title={scenarioId ? `NetPlan 情境 ${scenarioId}` : view === "overview" ? "NetPlan 店網規劃" : "NetPlan 情境"}
      />
      <main className="odp-content" data-testid={`netplan-${view}-production-page`}>
        <WorkspaceNav active={view} />
        <ProductionDataState binding={binding} resource="NetPlan scenarios" testId="netplan-production-data-state">
          {binding ? <LiveNetPlanScenarios binding={binding} productionMode /> : null}
        </ProductionDataState>
        {binding?.state === "ready" && scenarioId && !binding.items.some((item) => item.scenario_id === scenarioId) ? (
          <section className={styles.reportSection} data-testid="netplan-scenario-not-found" role="status">
            <h2>Scenario not found</h2>
            <p>API 回傳的情境中沒有 {scenarioId}；未以固定情境替代。</p>
          </section>
        ) : null}
      </main>
    </>
  );
}

function Header({
  title,
  summary,
  scenarioId,
}: {
  title: string;
  summary: string;
  scenarioId?: string;
}) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      breadcrumb={[
        { label: "網絡規劃 NetPlan", href: "/netplan" },
        ...(scenarioId ? [{ label: "情境", href: "/w/network/scenarios" }] : []),
        { label: scenarioId ?? title },
      ]}
      status={{
        label: "solver netplan-exhaustive-cpsat-compatible-v1",
        tone: "purple",
        marker: "◇",
        "data-testid": "netplan-data-status",
      }}
      lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion} · source ${freshness.sourceSnapshotId}`}
      actions={
        <div className={styles.headerActions}>
          <a className={styles.secondaryButton} href="#audit">
            View audit
          </a>
          <a className={styles.primaryButton} href="#primary-action">
            建立情境
          </a>
        </div>
      }
    />
  );
}

function WorkspaceNav({ active }: { active: NetPlanRouteKey }) {
  return (
    <nav className={styles.workspaceNav} aria-label="NetPlan navigation">
      <Link aria-current={active === "overview" ? "page" : undefined} href="/netplan">
        Overview
      </Link>
      <Link
        aria-current={active === "scenarios" || active === "scenarioDetail" ? "page" : undefined}
        data-testid="netplan-nav-scenarios"
        href="/w/network/scenarios"
      >
        情境
      </Link>
    </nav>
  );
}

function NetPlanOverview({ liveScenarios }: { liveScenarios?: ApiBinding<NetPlanScenarioSummary> }) {
  return (
    <>
      <Header
        title="NetPlan 店網規劃"
        summary="設定限制、解算最佳計畫與 alternatives、核准、執行並觀察結果。"
      />
      <main className="odp-content" data-testid="netplan-overview-page">
        <WorkspaceNav active="overview" />
        {liveScenarios ? <LiveNetPlanScenarios binding={liveScenarios} /> : null}
        <section className={styles.flowGrid} aria-label="NetPlan decision flow">
          <Link className={styles.flowCard} href="/w/network/scenarios">
            <span className={styles.step}>1</span>
            <h2>情境列表</h2>
            <p>掃描情境狀態、solver 結果、待核准與不可行。</p>
          </Link>
          <Link className={styles.flowCard} href={`/w/network/scenarios/${scenarios[0].scenarioId}`}>
            <span className={styles.step}>2</span>
            <h2>情境詳情</h2>
            <p>最佳計畫 + alternatives / infeasibility、核准、執行、結果。</p>
          </Link>
        </section>
        <section className={styles.twoColumn}>
          <DecisionSeparation />
          <SharedContract />
        </section>
      </main>
    </>
  );
}

function LiveNetPlanScenarios({
  binding,
  productionMode = false,
}: {
  binding: ApiBinding<NetPlanScenarioSummary>;
  productionMode?: boolean;
}) {
  return (
    <section
      className={styles.reportSection}
      data-testid="netplan-live-scenarios"
      aria-label="API-bound NetPlan scenario comparison"
    >
      <div className={styles.badgeRow}>
        <h2>情境比較（API live）</h2>
        {productionMode ? (
          <ProductionDataBadge binding={binding} testId="netplan-data-source" />
        ) : (
          <DataSourceBadge binding={binding} testId="netplan-data-source" />
        )}
      </div>
      <p>
        本區直接讀取 <code>GET /netplan/scenarios</code> 的完整生命週期狀態（solved / infeasible /
        approved / executed / outcome_observed）。
        {!productionMode ? " 下方固定情境為 documented non-product fixture。" : null}
      </p>
      {binding.state === "ready" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="netplan-live-scenarios-table">
            <caption>Live scenarios served by the backend ({binding.items.length})</caption>
            <thead>
              <tr>
                <th>scenario_id</th>
                <th>scenario</th>
                <th>horizon</th>
                <th>status</th>
                <th>solver</th>
              </tr>
            </thead>
            <tbody>
              {binding.items.map((item) => (
                <tr key={item.scenario_id} data-testid="netplan-live-scenario-row">
                  <td>{item.scenario_id}</td>
                  <td>{stringField(item.scenario_name)}</td>
                  <td>{stringField(item.planning_horizon) || "—"}</td>
                  <td>
                    <Badge
                      label={stringField(item.status) || "—"}
                      tone={liveScenarioTone(item.status)}
                      marker="◆"
                    />
                  </td>
                  <td>{stringField(item.solver_version) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p data-testid="netplan-live-scenarios-empty" className={styles.auditLine}>
          {liveScenariosFallbackMessage(binding)}
        </p>
      )}
    </section>
  );
}

function liveScenariosFallbackMessage(binding: ApiBinding<NetPlanScenarioSummary>): string {
  if (binding.state === "empty") {
    return "後端可連線但尚無情境（cold store）；顯示固定情境作為非產品 fallback。";
  }
  if (binding.state === "error") {
    return `後端讀取失敗（${binding.error ?? "unknown"}）；改用固定情境 fallback。`;
  }
  return "未設定 API base URL（ODP_API_BASE_URL）；以固定情境渲染。";
}

function liveScenarioTone(status: unknown) {
  if (status === "approved" || status === "executed" || status === "outcome_observed" || status === "closed") {
    return "green" as const;
  }
  if (status === "infeasible" || status === "rejected") return "red" as const;
  if (status === "solved" || status === "pending_approval") return "blue" as const;
  return "orange" as const;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function ScenariosListPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? scenarios[0].scenarioId;
  const drawer = scenarios.find((s) => s.scenarioId === selected) ?? scenarios[0];
  return (
    <>
      <Header
        title="情境"
        summary="掃描情境狀態、solver 結果、待核准與不可行。"
      />
      <main className="odp-content" data-testid="netplan-scenarios-page">
        <WorkspaceNav active="scenarios" />
        <FilterBar />
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption>情境列表（infeasible / rejected / closed 為 terminal）</caption>
            <thead>
              <tr>
                {["Scenario", "Status", "Solver", "Objective", "Actions", "Budget", "Risk", "Approval", "Action"].map(
                  (header, index) => (
                    <th aria-sort={index === 0 ? "ascending" : undefined} key={header}>
                      {header}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {scenarios.map((s) => (
                <tr tabIndex={0} key={s.scenarioId}>
                  <td>
                    <a href={`/w/network/scenarios?selected=${s.scenarioId}&drawer=scenario`}>
                      {s.scenarioId}
                      <br />
                      {s.scenarioName} · {s.planningHorizon}
                    </a>
                  </td>
                  <td>
                    <Badge label={s.status} tone={scenarioStatusTone(s.status)} marker="◆" />
                  </td>
                  <td>
                    {s.solveResult ? (
                      <Badge label={s.solveResult.solverStatus} tone={solverStatusTone(s.solveResult.solverStatus)} marker="▧" />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{s.solveResult ? s.solveResult.objectiveValue.toLocaleString() : "—"}</td>
                  <td>{s.solveResult ? formatActionCounts(s.solveResult.actionCounts) : "—"}</td>
                  <td>
                    {s.solveResult
                      ? `${s.solveResult.budgetUsage.toLocaleString()} / ${s.constraints.maxBudget.toLocaleString()}`
                      : "—"}
                  </td>
                  <td>
                    {s.solveResult
                      ? `${s.solveResult.averageRisk.toFixed(2)} / ${(s.constraints.maxAverageRisk ?? 0).toFixed(2)}`
                      : "—"}
                  </td>
                  <td>{approvalLabel(s)}</td>
                  <td>
                    <Link href={`/w/network/scenarios/${s.scenarioId}`}>開啟情境詳情</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Drawer title={`${drawer.scenarioId} · ${drawer.scenarioName}`} testId="netplan-scenario-drawer">
          <div className={styles.cardStack}>
            <div className={styles.metricRow}>
              <Metric label="Status" value={drawer.status} />
              <Metric label="Solver" value={drawer.solveResult?.solverStatus ?? "—"} />
              <Metric label="Objective" value={drawer.solveResult?.objectiveValue.toLocaleString() ?? "—"} />
            </div>
            <p>Approval：{approvalLabel(drawer)}</p>
            <p className={styles.auditLine}>correlation_id {drawer.correlationId}</p>
            <Link className={styles.primaryButton} href={`/w/network/scenarios/${drawer.scenarioId}`}>
              開啟情境詳情
            </Link>
          </div>
        </Drawer>
      </main>
    </>
  );
}

function FilterBar() {
  return (
    <form className={styles.filterBar} aria-label="URL synced filters">
      <label>
        scenario_name
        <input name="scenario_name" defaultValue="" />
      </label>
      <label>
        status
        <select name="status" defaultValue="all">
          <option value="all">全部</option>
          <option>draft</option>
          <option>solved</option>
          <option>infeasible</option>
          <option>pending_approval</option>
          <option>approved</option>
          <option>outcome_observed</option>
        </select>
      </label>
      <label>
        solver
        <select name="solver" defaultValue="all">
          <option value="all">全部</option>
          <option>optimal</option>
          <option>feasible</option>
          <option>infeasible</option>
        </select>
      </label>
    </form>
  );
}

function ScenarioDetailPage({ scenarioId }: { scenarioId?: string }) {
  const s = scenarios.find((item) => item.scenarioId === scenarioId) ?? scenarios[0];
  return (
    <>
      <Header
        title={`${s.scenarioId} · ${s.scenarioName}`}
        summary={`${s.planningHorizon} · 狀態 ${s.status}${
          s.solveResult ? ` · solver ${s.solveResult.solverStatus} · objective ${s.solveResult.objectiveValue.toLocaleString()}` : ""
        }。系統最佳計畫與人工核准分離呈現。`}
        scenarioId={s.scenarioId}
      />
      <main className="odp-content" data-testid="netplan-scenario-detail-page">
        <WorkspaceNav active="scenarioDetail" />
        <nav className={styles.anchorTabs} aria-label="Scenario anchors">
          {["summary", "status", "builder", "solve", "approval", "execution", "audit"].map((id) => (
            <a href={`#${id}`} key={id}>
              {id}
            </a>
          ))}
        </nav>
        <section className={styles.reportGrid}>
          <article className={styles.reportMain}>
            <SummarySection scenario={s} />
            <StatusSection scenario={s} />
            <BuilderSection scenario={s} />
            <SolveSection scenario={s} />
            <ExecutionSection scenario={s} />
            <AuditSection scenario={s} />
          </article>
          <aside className={styles.stickyPanel}>
            <ApprovalPanel scenario={s} />
          </aside>
        </section>
      </main>
    </>
  );
}

function SummarySection({ scenario: s }: { scenario: NetPlanScenario }) {
  return (
    <section className={styles.reportSection} id="summary" data-testid="netplan-summary">
      <h2>Summary</h2>
      <div className={styles.metricRow}>
        <Metric label="Horizon" value={s.planningHorizon} />
        <Metric label="Status" value={s.status} />
        <Metric label="Solver" value={s.solveResult?.solverStatus ?? "—"} />
        <Metric label="Objective" value={s.solveResult?.objectiveValue.toLocaleString() ?? "—"} />
      </div>
      {s.solveResult ? (
        <p>
          budget_usage {s.solveResult.budgetUsage.toLocaleString()} / {s.constraints.maxBudget.toLocaleString()} · average_risk{" "}
          {s.solveResult.averageRisk.toFixed(2)}
        </p>
      ) : null}
    </section>
  );
}

function StatusSection({ scenario: s }: { scenario: NetPlanScenario }) {
  const terminal = TERMINAL_STATUSES.includes(s.status);
  return (
    <section className={styles.reportSection} id="status">
      <h2>Status &amp; History</h2>
      <div className={styles.badgeRow}>
        <Badge label={s.status} tone={scenarioStatusTone(s.status)} marker="◆" />
        {terminal ? <Badge label="terminal（無出邊）" tone="gray" marker="◫" /> : null}
        <span className={styles.auditLine}>
          可用轉移：{VALID_TRANSITIONS[s.status].length ? VALID_TRANSITIONS[s.status].join(" / ") : "—（terminal）"}
        </span>
      </div>
      <table className={styles.intervalTable} aria-label="Status history">
        <thead>
          <tr>
            <th>From → To</th>
            <th>Actor</th>
            <th>Reason</th>
            <th>At</th>
            <th>correlation_id</th>
          </tr>
        </thead>
        <tbody>
          {s.statusHistory.map((t) => (
            <tr key={t.correlationId}>
              <td>
                {t.from} → {t.to}
              </td>
              <td>{t.actor}</td>
              <td>{t.reason}</td>
              <td>{t.at}</td>
              <td>{t.correlationId}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function BuilderSection({ scenario: s }: { scenario: NetPlanScenario }) {
  const c = s.constraints;
  return (
    <section className={styles.reportSection} id="builder" data-testid="netplan-builder">
      <h2>Scenario Builder（Constraints &amp; Options）</h2>
      <div className={styles.metricRow}>
        <Metric label="max_budget" value={c.maxBudget.toLocaleString()} />
        <Metric label="min_expected_gm" value={c.minExpectedGrossMargin?.toLocaleString() ?? "—"} />
        <Metric label="min_capacity_delta" value={c.minCapacityDelta ?? "—"} />
        <Metric label="max_average_risk" value={c.maxAverageRisk?.toFixed(2) ?? "—"} />
      </div>
      <p>
        min action counts {JSON.stringify(c.minActionCounts)} · max action counts {JSON.stringify(c.maxActionCounts)}。限制編輯後須重新 solve，不得用舊解。
      </p>
      <table className={styles.intervalTable} aria-label="Options by entity">
        <thead>
          <tr>
            <th>Entity</th>
            <th>Action</th>
            <th>Expected GM</th>
            <th>Budget cost</th>
            <th>Risk</th>
            <th>Capacity Δ</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {s.optionsByEntity.map((o) => (
            <tr key={`${o.entityId}-${o.action}`}>
              <td>{o.entityId}</td>
              <td>{o.action}</td>
              <td>{o.expectedGm.toLocaleString()}</td>
              <td>{o.budgetCost.toLocaleString()}</td>
              <td>{o.riskScore.toFixed(2)}</td>
              <td>{o.capacityDelta}</td>
              <td>{o.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SolveSection({ scenario: s }: { scenario: NetPlanScenario }) {
  const r = s.solveResult;
  if (r && r.solverStatus === "infeasible") {
    return <InfeasibilitySection scenario={s} />;
  }
  if (!r) {
    return (
      <section className={styles.reportSection} id="solve" data-testid="netplan-solve">
        <h2>Solve Result</h2>
        <p>尚未解算。設定限制後 solve（大型 solver 只顯示「解算中」狀態，不顯示假進度）。</p>
      </section>
    );
  }
  return (
    <section className={styles.reportSection} id="solve" data-testid="netplan-scenario-card">
      <h2>Solve Result — Feasible（NetPlanScenarioCard）</h2>
      <p>
        系統最佳計畫，標示 solver {r.solverVersion}；不得呈現為已核准。
      </p>
      <div className={styles.metricRow}>
        <Metric label="objective" value={r.objectiveValue.toLocaleString()} />
        <Metric label="expected_gm" value={r.expectedGrossMargin.toLocaleString()} />
        <Metric label="budget_usage" value={`${r.budgetUsage.toLocaleString()} / ${s.constraints.maxBudget.toLocaleString()}`} />
        <Metric label="average_risk" value={r.averageRisk.toFixed(2)} />
        <Metric label="capacity_delta" value={r.capacityDelta} />
      </div>
      <p>action_counts：{formatActionCounts(r.actionCounts)}</p>
      <div className={styles.softBlock}>
        <h3>Binding constraints</h3>
        <div className={styles.tagRow}>
          {r.bindingConstraints.length ? (
            r.bindingConstraints.map((b) => (
              <span className={styles.tag} key={b.constraint}>
                {b.constraint} {b.usagePct}%
              </span>
            ))
          ) : (
            <span>無綁定限制</span>
          )}
        </div>
      </div>
      <table className={styles.intervalTable} aria-label="Selected actions">
        <thead>
          <tr>
            <th>Entity</th>
            <th>Action</th>
            <th>Expected GM</th>
            <th>Cost</th>
            <th>Risk</th>
            <th>Capacity Δ</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {r.selectedActions.map((a) => (
            <tr key={a.entityId}>
              <td>{a.entityId}</td>
              <td>{a.action}</td>
              <td>{a.expectedGm.toLocaleString()}</td>
              <td>{a.budgetCost.toLocaleString()}</td>
              <td>{a.riskScore.toFixed(2)}</td>
              <td>{a.capacityDelta}</td>
              <td>{a.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.softBlock}>
        <h3>Alternatives（與最佳計畫並排比較）</h3>
        {r.alternatives.length ? (
          <table className={styles.intervalTable} aria-label="Alternatives comparison">
            <thead>
              <tr>
                <th>Alt</th>
                <th>Δobjective</th>
                <th>Δbudget</th>
                <th>Δrisk</th>
                <th>動作差異</th>
              </tr>
            </thead>
            <tbody>
              {r.alternatives.map((alt) => (
                <tr key={alt.id}>
                  <td>{alt.id}</td>
                  <td>{alt.deltaObjective.toLocaleString()}</td>
                  <td>{alt.deltaBudget.toLocaleString()}</td>
                  <td>{alt.deltaRisk.toFixed(2)}</td>
                  <td>{alt.actionDiff}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>無替代計畫。</p>
        )}
      </div>
    </section>
  );
}

function InfeasibilitySection({ scenario: s }: { scenario: NetPlanScenario }) {
  const r = s.solveResult!;
  return (
    <section className={styles.reportSection} id="solve" data-testid="netplan-infeasibility">
      <h2>Solve Result — Infeasibility Diagnosis</h2>
      <p className={styles.riskNotice}>
        solver_status=infeasible（terminal）。UI 不自動放寬任何限制，只呈現診斷與「修改情境」入口。
      </p>
      {r.diagnostics.map((d) => (
        <div className={styles.diagnosisCard} key={d.violatedConstraint}>
          <h3>⚠ {d.violatedConstraint}</h3>
          <p>affected_stores：{d.affectedStores.join("、")}</p>
          <p>required_relaxation：{d.requiredRelaxation}</p>
          <p>business_impact：{d.businessImpact}</p>
          <p>suggested_action：{d.suggestedAction}</p>
        </div>
      ))}
      <a className={styles.primaryButton} href="/w/network/scenarios?drawer=new">
        修改情境（建立新 draft）
      </a>
    </section>
  );
}

function ApprovalPanel({ scenario: s }: { scenario: NetPlanScenario }) {
  const canApprove = s.status === "pending_approval";
  const showPanel = s.status !== "infeasible" && s.status !== "draft";
  if (!showPanel) {
    return (
      <section className={styles.approvalPanel} id="approval" data-testid="netplan-approval-panel">
        <h2>Approval</h2>
        <p>{s.status === "infeasible" ? "infeasible 不顯示核准動作。" : "draft 尚未解算/送審。"}</p>
      </section>
    );
  }
  return (
    <section className={styles.approvalPanel} id="approval" data-testid="netplan-approval-panel">
      <h2>Approval</h2>
      <p>
        系統最佳計畫與 alternatives 由 solver 產生（{s.solverVersion}），人工決策獨立記錄、never optimistic。建立者不得核准自己的情境（segregation）。
      </p>
      {s.approval ? (
        <p className={styles.auditLine}>
          success：approval_id {s.approval.approvalId} · actor {s.approval.actorId} · {s.approval.decision} · {s.approval.decidedAt} ·
          policy {s.approval.policyVersion} · correlation_id {s.approval.correlationId}
        </p>
      ) : (
        <form>
          <label>
            decision
            <select name="decision" defaultValue="approved" disabled={!canApprove}>
              <option value="approved">approved</option>
              <option value="rejected">rejected</option>
            </select>
          </label>
          <label>
            reason（必填，approved/rejected 皆然）
            <textarea name="reason" minLength={10} defaultValue="" placeholder="決策理由，至少 10 字" />
          </label>
          <button className={styles.primaryButton} type="button" id="primary-action" disabled={!canApprove}>
            {canApprove ? "送出決策" : "需先 submit_for_approval"}
          </button>
        </form>
      )}
      <p className={styles.auditLine}>policy {NETPLAN_POLICY_VERSION}</p>
    </section>
  );
}

function ExecutionSection({ scenario: s }: { scenario: NetPlanScenario }) {
  return (
    <section className={styles.reportSection} id="execution" data-testid="netplan-execution">
      <h2>Execution &amp; Outcome</h2>
      {s.execution ? (
        <p>
          ExecutionRecord：{s.execution.executionId} · {s.execution.actions} actions · {s.execution.executedBy} ·{" "}
          {s.execution.executedAt}（採最新 solve 的 selected_actions）。
        </p>
      ) : (
        <p>approved 後可 execute；尚未執行。</p>
      )}
      {s.outcome ? (
        <div className={styles.softBlock}>
          <h3>Outcome（expected vs actual）</h3>
          <table className={styles.intervalTable} aria-label="Expected vs actual">
            <thead>
              <tr>
                <th>Expected GM</th>
                <th>Actual GM</th>
                <th>Variance</th>
                <th>Variance %</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{s.outcome.expectedGrossMargin.toLocaleString()}</td>
                <td>{s.outcome.actualGrossMargin.toLocaleString()}</td>
                <td>
                  {s.outcome.variance >= 0 ? "▲" : "▼"} {s.outcome.variance.toLocaleString()}
                </td>
                <td>
                  {s.outcome.variancePct >= 0 ? "▲" : "▼"} {s.outcome.variancePct}%
                </td>
              </tr>
            </tbody>
          </table>
          <p className={styles.auditLine}>observed_at {s.outcome.observedAt}</p>
        </div>
      ) : s.execution ? (
        <p>待觀察結果（executed，未觀察前不宣稱成效）。</p>
      ) : null}
    </section>
  );
}

function AuditSection({ scenario: s }: { scenario: NetPlanScenario }) {
  return (
    <section className={styles.reportSection} id="audit">
      <h2>Version / Audit</h2>
      <dl className={styles.auditGrid}>
        <dt>scenario id</dt>
        <dd>{s.scenarioId}</dd>
        <dt>model version</dt>
        <dd>{s.modelVersion}</dd>
        <dt>feature version</dt>
        <dd>{s.featureVersion}</dd>
        <dt>solver version</dt>
        <dd>{s.solverVersion}</dd>
        <dt>policy version</dt>
        <dd>{s.policyVersion}</dd>
        <dt>correlation id</dt>
        <dd>{s.correlationId}</dd>
      </dl>
    </section>
  );
}

function Drawer({ title, children, testId }: { title: string; children: React.ReactNode; testId: string }) {
  return (
    <aside className={styles.drawer} aria-label={title} data-testid={testId}>
      <div className={styles.drawerHeader}>
        <h2>{title}</h2>
        <a href="?">Esc</a>
      </div>
      {children}
      <div className={styles.drawerFooter}>
        <a href="#prev">上一筆</a>
        <a href="#next">下一筆</a>
        <a href="#deep-link">Deep link</a>
      </div>
    </aside>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DecisionSeparation() {
  return (
    <section className={styles.reportSection}>
      <h2>Decision separation</h2>
      <ol className={styles.timeline}>
        <li>Prediction：候選計畫期望毛利</li>
        <li>Recommendation：solver 最佳計畫與 alternatives，標示 solver_version</li>
        <li>Human decision：核准／退回（+ reason）</li>
        <li>Execution：採最新 solve 執行店網行動</li>
        <li>Outcome：expected vs actual + variance（未觀察前不宣稱成效）</li>
      </ol>
    </section>
  );
}

function SharedContract() {
  return (
    <section className={styles.reportSection}>
      <h2>Shared page contract</h2>
      <div className={styles.badgeRow}>
        <Badge label="loading" tone="gray" marker="◫" />
        <Badge label="empty" tone="gray" marker="◫" />
        <Badge label="error + correlation_id" tone="red" marker="▧" />
        <Badge label="read-only" tone="blue" marker="▣" />
      </div>
      <p>Filter、selected entity 與 drawer state 皆以 URL query 還原。</p>
    </section>
  );
}
