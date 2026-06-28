import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import { freshness, interventionCases, statusTone, type InterventionCase } from "./data.ts";
import styles from "./intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

export function InterventionWorkspace({ searchParams = {} }: { searchParams?: SearchParams }) {
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

function WorkspaceNav() {
  return (
    <nav className={styles.workspaceNav} aria-label="Intervention module navigation">
      <Link aria-current="page" href="/interventions">InterventionTimeline</Link>
      <Link href="/pricing">PriceOps handoff</Link>
      <Link href="/adlift">AdLift contamination</Link>
    </nav>
  );
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
