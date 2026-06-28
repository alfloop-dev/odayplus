import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import {
  auditDecisions,
  matrixColumns,
  selectedDecision,
  subsidyMatrix,
  type AuditDecision,
  type AuditOutcome,
  type EvidenceStatus,
} from "./data.ts";
import styles from "./audit.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type AuditWorkspaceProps = {
  view?: "overview" | "decisions" | "decisionDetail" | "evidence" | "admin";
  decisionId?: string;
  searchParams?: SearchParams;
};

export function AuditWorkspace({ view = "overview", decisionId, searchParams = {} }: AuditWorkspaceProps) {
  if (view === "decisions") return <DecisionsPage searchParams={searchParams} />;
  if (view === "decisionDetail") return <DecisionDetailPage decision={selectedDecision(decisionId)} />;
  if (view === "evidence") return <EvidencePage />;
  if (view === "admin") return <AdminAuditPage />;
  return <AuditOverview />;
}

function AuditOverview() {
  return (
    <>
      <Header title="稽核軌跡" summary="決策稽核、Evidence 匯出、Decision Card 與補貼證據矩陣。" />
      <main className="odp-content" data-testid="audit-overview-page">
        <WorkspaceNav active="overview" />
        <section className={styles.flowGrid}>
          <Link className={styles.flowCard} href="/w/audit/decisions">
            <h2>決策稽核</h2>
            <p>掃描跨模組高風險決策、結果、覆寫與待補證據。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/audit/decisions/decision-lh-240">
            <h2>Decision detail</h2>
            <p>固定七節點時間軸、Decision Card 與 AuditMetadata。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/audit/evidence">
            <h2>Evidence matrix</h2>
            <p>以補貼方案 × 證據型別呈現齊備、待補與不適用。</p>
          </Link>
        </section>
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Audit queue</h2>
            <div className={styles.metricRow}>
              <Metric label="High-risk decisions" value={auditDecisions.length} />
              <Metric label="Overrides" value={auditDecisions.filter((decision) => decision.overrideReason).length} />
              <Metric label="Evidence gaps" value="5 cells" />
            </div>
          </div>
          <ExportPanel decision={auditDecisions[0]} />
        </section>
      </main>
    </>
  );
}

function DecisionsPage({ searchParams }: { searchParams: SearchParams }) {
  const selectedId = selectedFromQuery(searchParams.selected) ?? auditDecisions[0].decisionId;
  const selected = selectedDecision(selectedId);
  return (
    <>
      <Header title="決策稽核" summary="追溯跨模組高風險決策的模型、核准、執行與結果鏈。" statusLabel="compact · URL state" />
      <main className="odp-content" data-testid="audit-decisions-page">
        <WorkspaceNav active="decisions" />
        <FilterBar />
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Decision audit log</h2>
            <DecisionTable />
          </div>
          <aside className={styles.stickyPanel} data-testid="audit-decision-drawer">
            <DecisionSummary decision={selected} />
            <div className={styles.actionRow}>
              <Link className={styles.primaryButton} href={`/w/audit/decisions/${selected.decisionId}`}>開啟稽核詳情</Link>
              <Link className={styles.secondaryButton} href="/w/audit/evidence">Evidence matrix</Link>
            </div>
          </aside>
        </section>
      </main>
    </>
  );
}

function DecisionDetailPage({ decision }: { decision: AuditDecision }) {
  return (
    <>
      <Header title={decision.decisionId} summary="決策稽核詳情：Summary、Decision Card、Audit Timeline、AuditMetadata、Evidence Export。" statusLabel={`${decision.module} · ${decision.outcome}`} />
      <main className="odp-content" data-testid="audit-decision-detail-page">
        <WorkspaceNav active="decisions" />
        <section className={styles.summaryBand} data-testid="audit-summary">
          <div className={styles.metricRow}>
            <Metric label="event_type" value={decision.eventType} />
            <Metric label="action" value={decision.action} />
            <Metric label="actor" value={`${decision.actor} · ${decision.role}`} />
            <Metric label="outcome" value={decision.outcome} />
          </div>
        </section>
        <section className={styles.detailGrid}>
          <div className={styles.sectionStack}>
            <DecisionCard decision={decision} />
            <DecisionAuditTimeline decision={decision} />
            <AuditMetadata decision={decision} />
            <ExportPanel decision={decision} />
          </div>
          <aside className={styles.stickyPanel}>
            <DecisionSummary decision={decision} />
          </aside>
        </section>
      </main>
    </>
  );
}

function EvidencePage() {
  return (
    <>
      <Header title="Evidence / 補貼證據" summary="依條件彙整證據、組補貼證據矩陣、批次匯出。" statusLabel="compact matrix" />
      <main className="odp-content" data-testid="audit-evidence-page">
        <WorkspaceNav active="evidence" />
        <section className={styles.detailGrid}>
          <div className={styles.sectionStack}>
            <EvidenceMatrix />
          </div>
          <aside className={styles.stickyPanel}>
            <BatchExportPanel />
          </aside>
        </section>
      </main>
    </>
  );
}

function AdminAuditPage() {
  return (
    <>
      <Header title="Audit & Evidence（管理段）" summary="全租戶高風險決策稽核與 Evidence 匯出；role-gated for audit/admin." statusLabel="admin · role-gated" />
      <main className="odp-content" data-testid="admin-audit-page">
        <WorkspaceNav active="admin" />
        <div className={styles.panel}>
          <h2>Role gate</h2>
          <p>Deep links without audit/admin permission return 403. Restricted fields remain masked unless policy permits reveal.</p>
        </div>
        <DecisionTable />
      </main>
    </>
  );
}

function Header({ title, summary, statusLabel = "Audit Evidence" }: { title: string; summary: string; statusLabel?: string }) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      status={{ label: statusLabel, tone: "blue", marker: "●" }}
      breadcrumb={[{ label: "稽核", href: "/audit" }, { label: title }]}
      lastUpdated="2026-06-28"
      actions={<Link className={styles.secondaryButton} href="/w/audit/decisions">決策稽核</Link>}
    />
  );
}

function WorkspaceNav({ active }: { active: "overview" | "decisions" | "evidence" | "admin" }) {
  const items = [
    { key: "overview", label: "總覽", href: "/audit" },
    { key: "decisions", label: "決策稽核", href: "/w/audit/decisions" },
    { key: "evidence", label: "Evidence", href: "/w/audit/evidence" },
    { key: "admin", label: "Admin Audit", href: "/admin/audit" },
  ] as const;
  return (
    <nav className={styles.workspaceNav} aria-label="Audit sections">
      {items.map((item) => (
        <Link key={item.key} href={item.href} aria-current={active === item.key ? "page" : undefined}>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

function FilterBar() {
  return (
    <form className={styles.toolbar} aria-label="Decision audit filters">
      <label>event_type<input name="event_type" defaultValue="" /></label>
      <label>actor<input name="actor" defaultValue="" /></label>
      <label>action<select name="action" defaultValue=""><option value="">all</option><option>approve</option><option>publish</option><option>override</option><option>export</option></select></label>
      <label>outcome<select name="outcome" defaultValue=""><option value="">all</option><option>allow</option><option>deny</option><option>success</option><option>failure</option></select></label>
      <label>module<input name="module" defaultValue="" /></label>
      <button className={styles.secondaryButton} type="submit">Apply</button>
    </form>
  );
}

function DecisionTable() {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="decision-audit-table">
        <thead>
          <tr>
            <th>Decision</th>
            <th>Type</th>
            <th>Action</th>
            <th>Actor</th>
            <th>Outcome</th>
            <th>When</th>
            <th>Override</th>
            <th>Evidence</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {auditDecisions.map((decision) => (
            <tr key={decision.decisionId}>
              <td><Link className={styles.link} href={`/w/audit/decisions/${decision.decisionId}`}>{decision.decisionId}</Link><br /><span className={styles.subtle}>{decision.eventId} · {decision.module}</span></td>
              <td>{decision.eventType}</td>
              <td><Badge label={decision.action} tone={highRiskTone(decision.action)} marker={decision.action === "override" ? "!" : "◆"} /></td>
              <td>{decision.actor}<br /><span className={styles.subtle}>{decision.role}</span></td>
              <td><OutcomeBadge outcome={decision.outcome} /></td>
              <td>{decision.occurredAt}</td>
              <td>{decision.overrideReason ? <span className={styles.danger}>! override_reason present</span> : "none"}</td>
              <td>{decision.evidenceCompleteness}</td>
              <td><Link className={styles.link} href={`/w/audit/decisions?selected=${decision.decisionId}`}>Drawer</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DecisionSummary({ decision }: { decision: AuditDecision }) {
  return (
    <div className={styles.panel} data-testid="decision-summary-drawer">
      <h2>{decision.decisionId}</h2>
      <dl className={styles.auditGrid}>
        <dt>event_id</dt><dd className={styles.mono}>{decision.eventId}</dd>
        <dt>resource</dt><dd>{decision.resource}</dd>
        <dt>actor/outcome</dt><dd>{decision.actor} · {decision.outcome}</dd>
        <dt>next action</dt><dd>{decision.outcomeStatus === "pending" ? "補結果觀察" : "可匯出證據"}</dd>
        <dt>correlation_id</dt><dd className={styles.mono}>{decision.correlationId}</dd>
      </dl>
    </div>
  );
}

function DecisionCard({ decision }: { decision: AuditDecision }) {
  return (
    <section className={styles.panel} data-testid="decision-card">
      <h2>Decision Card</h2>
      <div className={styles.cardGrid}>
        <Metric label="Decision Title" value={`${decision.module} · ${decision.decisionId}`} />
        <Metric label="System Recommendation" value={decision.systemRecommendation} />
        <Metric label="Human Decision Status" value={decision.humanDecisionStatus} />
        <Metric label="Evidence Summary" value={decision.evidenceCompleteness} />
        <Metric label="Risk/Confidence" value={decision.riskConfidence} />
        <Metric label="Required Approval" value={decision.requiredApproval} />
        <Metric label="Primary Action" value={decision.primaryAction} />
        <Metric label="Audit Metadata" value={`${decision.modelVersion} · ${decision.policyVersion}`} />
      </div>
      {decision.overrideReason ? (
        <div className={[styles.panel, styles.warning].join(" ")} data-testid="override-comparison">
          <h3>Override before / after</h3>
          <dl className={styles.auditGrid}>
            <dt>before</dt><dd>{decision.before}</dd>
            <dt>after</dt><dd>{decision.after}</dd>
            <dt>override_reason</dt><dd>{decision.overrideReason}</dd>
          </dl>
        </div>
      ) : null}
    </section>
  );
}

function DecisionAuditTimeline({ decision }: { decision: AuditDecision }) {
  const nodes = [
    "Prediction generated",
    "Recommendation generated",
    "Human review requested",
    "Human decision submitted",
    "Execution started",
    "Outcome observed",
    "Feedback written to label registry",
  ];
  const completeCount = decision.evidenceCompleteness.startsWith("7/7") ? 7 : decision.evidenceCompleteness.startsWith("6/7") ? 6 : 5;
  return (
    <section className={styles.panel} data-testid="decision-audit-timeline">
      <h2>DecisionAuditTimeline</h2>
      <ol className={styles.timeline}>
        {nodes.map((node, index) => {
          const complete = index < completeCount;
          return (
            <li key={node} className={complete ? "" : styles.pending}>
              <strong>{complete ? "✓" : "○"} {node}</strong>
              <div className={styles.timelineMeta}>
                <span>actor {index < 2 ? "system" : decision.actor}</span>
                <span>outcome {complete ? decision.outcome : "待發生"}</span>
                <span>reason {complete ? decision.reason : "not yet recorded"}</span>
                <span className={styles.mono}>correlation_id {decision.correlationId}</span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function AuditMetadata({ decision }: { decision: AuditDecision }) {
  return (
    <section className={styles.panel} data-testid="audit-metadata-panel">
      <h2>Audit Metadata</h2>
      <dl className={styles.auditGrid}>
        <dt>feature_snapshot_time</dt><dd>{decision.featureSnapshotTime}</dd>
        <dt>model_version</dt><dd>{decision.modelVersion}</dd>
        <dt>policy_version</dt><dd>{decision.policyVersion}</dd>
        <dt>actor</dt><dd>{decision.actor}</dd>
        <dt>decision_time</dt><dd>{decision.decisionTime}</dd>
        <dt>reason</dt><dd>{decision.reason}</dd>
        <dt>override_reason</dt><dd>{decision.overrideReason ?? "none"}</dd>
        <dt>outcome_time</dt><dd>{decision.outcomeStatus === "pending" ? "待發生" : decision.occurredAt}</dd>
        <dt>before/after</dt><dd>{decision.before ? `${decision.before} → ${decision.after}` : "none"}</dd>
      </dl>
    </section>
  );
}

function ExportPanel({ decision }: { decision: AuditDecision }) {
  return (
    <section className={styles.panel} data-testid="evidence-export-panel">
      <h2>Evidence Export</h2>
      <p>Fields: decision_id / entity / model_version / feature_snapshot_time / actor / decision_time / execution_status / outcome_status / audit_status.</p>
      <dl className={styles.auditGrid}>
        <dt>data classification</dt><dd>RESTRICTED · secondary confirmation required</dd>
        <dt>PII masking</dt><dd>email j***@oday.example, phone *****123, free text masked unless policy permits reveal</dd>
        <dt>last export</dt><dd>actor auditor-a · reason subsidy audit · correlation_id {decision.correlationId}</dd>
      </dl>
      <label className={styles.fieldLabel}>export reason<textarea className={styles.textarea} defaultValue="Subsidy audit evidence package; no optimistic export state." /></label>
      <button className={styles.primaryButton} type="button">匯出證據包</button>
    </section>
  );
}

function EvidenceMatrix() {
  return (
    <section className={styles.panel} data-testid="subsidy-evidence-matrix">
      <h2>Subsidy Evidence Matrix</h2>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Program / Claim</th>
              {matrixColumns.map((column) => <th key={column}>{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {subsidyMatrix.map((row) => (
              <tr key={`${row.program}-${row.claimItem}`}>
                <td><strong>{row.program}</strong><br /><span className={styles.subtle}>{row.claimItem}</span></td>
                {matrixColumns.map((column) => {
                  const cell = row.cells[column];
                  return (
                    <td key={column} className={styles.matrixCell}>
                      <EvidenceStatusBadge status={cell.status} />
                      <br />
                      <span className={styles.subtle}>{cell.ref ?? cell.missing ?? "policy N/A"}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BatchExportPanel() {
  return (
    <section className={styles.panel} data-testid="batch-export-panel">
      <h2>Batch export</h2>
      <dl className={styles.auditGrid}>
        <dt>rows</dt><dd>{subsidyMatrix.length}</dd>
        <dt>classification</dt><dd>RESTRICTED 2 · CONFIDENTIAL 1</dd>
        <dt>masked fields</dt><dd>actor email, source phone, free-text notes</dd>
        <dt>excluded range</dt><dd>none; no silent truncation</dd>
      </dl>
      <button className={styles.secondaryButton} type="button">整列匯出證據包</button>
      <button className={styles.secondaryButton} type="button">缺口清單匯出</button>
    </section>
  );
}

function OutcomeBadge({ outcome }: { outcome: AuditOutcome }) {
  const tone = outcome === "success" || outcome === "allow" ? "green" : "red";
  return <Badge label={outcome} tone={tone} marker={outcome === "failure" || outcome === "deny" ? "!" : "✓"} />;
}

function EvidenceStatusBadge({ status }: { status: EvidenceStatus }) {
  const tone = status === "齊備" ? "green" : status === "待補" ? "orange" : "gray";
  const marker = status === "齊備" ? "✓" : status === "待補" ? "▲" : "○";
  return <Badge label={status} tone={tone} marker={marker} />;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className={styles.metric}><span>{label}</span><strong>{value}</strong></div>;
}

function highRiskTone(action: AuditDecision["action"]) {
  return action === "override" || action === "rollback" ? "red" : action === "export" ? "orange" : "purple";
}

function selectedFromQuery(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}
