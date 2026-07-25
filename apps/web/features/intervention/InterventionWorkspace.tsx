import Link from "next/link";
import type { InterventionSummary } from "@oday-plus/openapi-client";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { DataSourceBadge } from "../../src/components/DataSourceBadge.tsx";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "../operations/ProductionDataState.tsx";
import { freshness, interventionCases, statusTone, type InterventionCase } from "./data.ts";
import styles from "./intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type InterventionWorkspaceProps = {
  searchParams?: SearchParams;
  /** Live `GET /interventions` binding; supplied by the server route. */
  liveInterventions?: ApiBinding<InterventionSummary>;
  isProduction?: boolean;
};

export function InterventionWorkspace({
  searchParams = {},
  liveInterventions,
  isProduction: isProductionProp,
}: InterventionWorkspaceProps) {
  if (resolveProductionMode(isProductionProp)) {
    return <ProductionInterventionWorkspace binding={liveInterventions} />;
  }
  const selectedId = readParam(searchParams.selected) ?? interventionCases[0].id;
  const selected = interventionCases.find((item) => item.id === selectedId) ?? interventionCases[0];

  return (
    <>
      <PageHeader
        title="干預決策"
        summary="Alerts → Root Cause Evidence → InterventionTimeline，核准、停止與觀察窗分離。"
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "營運 Operations", href: "/operations" }, { label: "干預決策" }]}
        status={{ label: freshness.status, tone: dataStatusTone[freshness.status], marker: "◆", "data-testid": "intervention-data-status" }}
        lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion}`}
        actions={
          <div className={styles.actions}>
            <a className={styles.secondaryButton} href="#audit">View audit</a>
            <a className={styles.primaryButton} href="#approval">Open approval panel</a>
          </div>
        }
      />
      <main className="odp-content" data-testid="intervention-page">
        <WorkspaceNav />
        {liveInterventions ? <LiveInterventionCases binding={liveInterventions} /> : null}
        <section className={styles.overviewGrid} aria-label="Intervention workflow overview">
          <SummaryCard title="待核准" value="1" copy="conflict blocked item requires resolution before execute" />
          <SummaryCard title="觀察中" value="1" copy="outcome maturity guard blocks effect claims" />
          <SummaryCard title="Evidence level visible" value="immature" copy="outcome window must mature before closed" />
        </section>
        <FilterBar />
        <section className={styles.grid}>
          <CaseTable selected={selected.id} />
          <InterventionDrawer intervention={selected} />
        </section>
      </main>
    </>
  );
}

function ProductionInterventionWorkspace({
  binding,
}: {
  binding?: ApiBinding<InterventionSummary>;
}) {
  const state = productionBindingState(binding);
  return (
    <>
      <PageHeader
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "營運 Operations", href: "/operations" }, { label: "干預決策" }]}
        lastUpdated={binding?.fetchedAt ? `API checked ${binding.fetchedAt}` : "Live source not available"}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        summary="Production intervention lifecycle. Only persisted backend cases are rendered."
        title="干預決策"
      />
      <main className="odp-content" data-testid="intervention-production-page">
        <WorkspaceNav />
        <ProductionDataState binding={binding} resource="Intervention cases" testId="intervention-production-data-state">
          {binding ? <LiveInterventionCases binding={binding} productionMode /> : null}
        </ProductionDataState>
      </main>
    </>
  );
}

function WorkspaceNav() {
  return (
    <nav className={styles.workspaceNav} aria-label="Intervention module navigation">
      <Link aria-current="page" href="/interventions">InterventionTimeline</Link>
      <Link href="/pricing">PriceOps handoff</Link>
      <Link href="/adlift">AdLift contamination</Link>
    </nav>
  );
}

function LiveInterventionCases({
  binding,
  productionMode = false,
}: {
  binding: ApiBinding<InterventionSummary>;
  productionMode?: boolean;
}) {
  return (
    <section className={styles.panel} data-testid="intervention-live-cases" aria-label="API-bound intervention cases">
      <div className={styles.badgeRow}>
        <h2>Intervention cases（API live）</h2>
        {productionMode ? (
          <ProductionDataBadge binding={binding} testId="intervention-data-source" />
        ) : (
          <DataSourceBadge binding={binding} testId="intervention-data-source" />
        )}
      </div>
      <p>
        本區直接讀取 <code>GET /interventions</code> 的完整生命週期狀態（含 CLOSED 收尾）。
        {!productionMode ? " 下方固定案例為 documented non-product fixture。" : null}
      </p>
      {binding.state === "ready" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="intervention-live-cases-table">
            <caption>Live intervention cases served by the backend ({binding.items.length})</caption>
            <thead>
              <tr>
                <th>intervention_id</th>
                <th>store</th>
                <th>kind</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {binding.items.map((item) => (
                <tr key={item.intervention_id} data-testid="intervention-live-case-row">
                  <td>{item.intervention_id}</td>
                  <td>{stringField(item.store_id)}</td>
                  <td>{stringField(item.kind)}</td>
                  <td><Badge label={stringField(item.status) || "—"} tone={liveStatusTone(item.status)} marker="●" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p data-testid="intervention-live-cases-empty" className={styles.auditLine}>
          {liveCasesFallbackMessage(binding)}
        </p>
      )}
    </section>
  );
}

function liveCasesFallbackMessage(binding: ApiBinding<InterventionSummary>): string {
  if (binding.state === "empty") {
    return "後端可連線但尚無 intervention（cold store）；顯示固定案例作為非產品 fallback。";
  }
  if (binding.state === "error") {
    return `後端讀取失敗（${binding.error ?? "unknown"}）；改用固定案例 fallback。`;
  }
  return "未設定 API base URL（ODP_API_BASE_URL）；以固定案例渲染。";
}

function liveStatusTone(status: unknown) {
  if (status === "CLOSED" || status === "COMPLETED") return "green" as const;
  if (status === "STOPPED" || status === "ROLLED_BACK" || status === "REJECTED") return "red" as const;
  if (status === "OBSERVING" || status === "EXECUTING") return "blue" as const;
  return "orange" as const;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function SummaryCard({ title, value, copy }: { title: string; value: string; copy: string }) {
  return (
    <article className={styles.card}>
      <h2>{title}</h2>
      <div className={styles.metricRow}>
        <span className={styles.metric}><span>Status</span><strong>{value}</strong></span>
      </div>
      <p>{copy}</p>
    </article>
  );
}

function FilterBar() {
  return (
    <form className={styles.filterBar} aria-label="URL synced intervention filters">
      <label>
        Alert
        <select name="alert" defaultValue="all">
          <option value="all">全部</option>
          <option>ORANGE</option>
          <option>RED</option>
        </select>
      </label>
      <label>
        Status
        <select name="status" defaultValue="PENDING_REVIEW">
          <option>PENDING_REVIEW</option>
          <option>OBSERVING</option>
          <option>CLOSED</option>
        </select>
      </label>
      <a className={styles.secondaryButton} href="/interventions?selected=int-3002&drawer=case">Saved view</a>
    </form>
  );
}

function CaseTable({ selected }: { selected: string }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="intervention-table">
        <caption>Intervention candidates with eligibility, conflict, approval, observation, and evidence level.</caption>
        <thead>
          <tr>
            <th>Case</th>
            <th>Store</th>
            <th>Root cause</th>
            <th>Status</th>
            <th>Conflict</th>
            <th>Evidence</th>
            <th>Primary action</th>
          </tr>
        </thead>
        <tbody>
          {interventionCases.map((item) => (
            <tr key={item.id} aria-selected={item.id === selected}>
              <td><Link href={`/interventions?selected=${item.id}&drawer=case`}>{item.id}</Link></td>
              <td>{item.store}</td>
              <td>{item.cause}</td>
              <td><Badge label={item.decisionStatus} tone={item.decisionStatus === "PENDING_REVIEW" ? "orange" : "blue"} marker="●" /></td>
              <td>{item.conflict}</td>
              <td>{item.evidenceLevel}</td>
              <td>{item.status === "CONFLICT_CHECKED" ? "Resolve conflict" : "Review outcome"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InterventionDrawer({ intervention }: { intervention: InterventionCase }) {
  return (
    <aside className={styles.drawer} data-testid="intervention-drawer" aria-label={`${intervention.id} detail`}>
      <div className={styles.badgeRow}>
        <Badge label={intervention.alert} tone={intervention.alert === "RED" ? "red" : "orange"} marker="!" />
        <Badge label={intervention.decisionStatus} tone="blue" marker="●" />
        <Badge label={`Evidence ${intervention.evidenceLevel}`} tone={intervention.evidenceLevel === "low" ? "orange" : "purple"} marker="▧" />
      </div>
      <h2>{intervention.id} · {intervention.store}</h2>
      <p>{intervention.action}</p>
      <section className={styles.softBlock}>
        <h3>Eligibility / Conflict / Observation</h3>
        <p>{intervention.eligibility}</p>
        <p>{intervention.conflict}</p>
        <p>{intervention.observationWindow}</p>
        <p>{intervention.outcome}</p>
      </section>
      {intervention.conflict.includes("BLOCKED") ? (
        <section className={styles.warningBlock} data-testid="intervention-conflict-block">
          <h3>Conflict blocks approval execution</h3>
          <p>Resolve overlapping intervention before execute; no optimistic state change.</p>
        </section>
      ) : null}
      <Timeline intervention={intervention} />
      <ApprovalPanel intervention={intervention} />
    </aside>
  );
}

function Timeline({ intervention }: { intervention: InterventionCase }) {
  return (
    <section>
      <h2>InterventionTimeline</h2>
      <ol className={styles.timeline} data-testid="intervention-timeline">
        {intervention.timeline.map((node) => (
          <li key={node.label}>
            <div className={styles.badgeRow}>
              <strong>{node.label}</strong>
              <Badge label={node.status} tone={statusTone[node.status]} marker="●" />
            </div>
            <p>{node.description}</p>
            <p className={styles.auditLine}>{node.timestamp} · {node.actor} · artifact {node.artifact}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ApprovalPanel({ intervention }: { intervention: InterventionCase }) {
  const disabled = intervention.conflict.includes("BLOCKED");
  return (
    <section id="approval" className={styles.approvalPanel} data-testid="intervention-approval-panel">
      <h2>Approval / Stop panel</h2>
      <p>{intervention.reasonRequired}</p>
      <form>
        <label>
          Decision
          <select defaultValue={disabled ? "REQUEST_REVISION" : "APPROVE"}>
            <option>APPROVE</option>
            <option>STOP</option>
            <option>REQUEST_REVISION</option>
          </select>
        </label>
        <label>
          Reason
          <textarea defaultValue="原因：確認根因證據與執行風險，提交後等待後端 decision_id，不做 optimistic update。" />
        </label>
        <label>
          <input defaultChecked type="checkbox" /> Risk acknowledged
        </label>
        <div className={styles.actions}>
          <button className={disabled ? styles.secondaryButton : styles.primaryButton} disabled={disabled} type="button">
            核准此干預
          </button>
          <button className={styles.dangerButton} type="button">停止此干預</button>
        </div>
      </form>
      <p className={styles.auditLine}>
        decision_id {intervention.decisionId} · correlation_id {intervention.audit.correlationId} · model {intervention.audit.modelVersion} · policy {intervention.audit.policyVersion} · feature snapshot {intervention.audit.featureSnapshotTime}
      </p>
    </section>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
