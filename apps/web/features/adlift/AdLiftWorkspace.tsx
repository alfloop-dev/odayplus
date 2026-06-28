import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import { freshness, preTrendTone, recommendationTone, reports, type AdLiftReport } from "./data.ts";
import styles from "../intervention/intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

export function AdLiftWorkspace({ searchParams = {} }: { searchParams?: SearchParams }) {
  const selectedId = readParam(searchParams.selected) ?? reports[0].id;
  const selected = reports.find((item) => item.id === selectedId) ?? reports[0];

  return (
    <>
      <PageHeader
        title="廣告增益"
        summary="AdLiftReportCard：treatment/control、pre-trend、incrementality、iROMI 與 contamination guard。"
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "定價 Pricing", href: "/pricing" }, { label: "廣告增益" }]}
        status={{ label: freshness.status, tone: dataStatusTone[freshness.status], marker: "◆", "data-testid": "adlift-data-status" }}
        lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion}`}
        actions={
          <div className={styles.actions}>
            <a className={styles.secondaryButton} href="#evidence">Evidence</a>
            <a className={styles.primaryButton} href="#decision">Continue / Stop</a>
          </div>
        }
      />
      <main className="odp-content" data-testid="adlift-page">
        <nav className={styles.workspaceNav} aria-label="AdLift module navigation">
          <Link href="/pricing">PriceOps Plans</Link>
          <Link aria-current="page" href="/adlift">AdLift Reports</Link>
          <Link href="/interventions">Intervention overlaps</Link>
        </nav>
        <section className={styles.overviewGrid} aria-label="AdLift overview">
          <Summary title="Reports ready" value="3" copy="treatment/control and pre-trend status visible" />
          <Summary title="Blocked claims" value="2" copy="no controls or failed pre-trend prevents causality claim" />
          <Summary title="Contamination" value="1" copy="overlapping intervention shown before decision" />
        </section>
        <FilterBar />
        <section className={styles.grid}>
          <ReportTable selected={selected.id} />
          <ReportDrawer report={selected} />
        </section>
      </main>
    </>
  );
}

function Summary({ title, value, copy }: { title: string; value: string; copy: string }) {
  return (
    <article className={styles.card}>
      <h2>{title}</h2>
      <span className={styles.metric}><span>Status</span><strong>{value}</strong></span>
      <p>{copy}</p>
    </article>
  );
}

function FilterBar() {
  return (
    <form className={styles.filterBar} aria-label="URL synced adlift filters">
      <label>
        Evidence
        <select defaultValue="all" name="evidence">
          <option value="all">全部</option>
          <option>medium</option>
          <option>low</option>
          <option>blocked</option>
        </select>
      </label>
      <label>
        Recommendation
        <select defaultValue="all" name="recommendation">
          <option value="all">全部</option>
          <option>CONTINUE</option>
          <option>STOP</option>
          <option>REVIEW_ONLY</option>
        </select>
      </label>
      <a className={styles.secondaryButton} href="/adlift?selected=adlift-8802&drawer=report">Show contamination</a>
    </form>
  );
}

function ReportTable({ selected }: { selected: string }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="adlift-table">
        <caption>AdLiftReportCard list with controls, pre-trend, incrementality, evidence, and continue/stop recommendation.</caption>
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Treatment</th>
            <th>Control</th>
            <th>Pre-trend</th>
            <th>iROMI</th>
            <th>Evidence</th>
            <th>Recommendation</th>
          </tr>
        </thead>
        <tbody>
          {reports.map((report) => (
            <tr key={report.id} aria-selected={report.id === selected}>
              <td><Link href={`/adlift?selected=${report.id}&drawer=report`}>{report.id}</Link><br />{report.campaign}</td>
              <td>{report.treatmentStores}</td>
              <td>{report.controlStores}</td>
              <td><Badge label={report.preTrendStatus} tone={preTrendTone[report.preTrendStatus]} marker="!" /></td>
              <td>{report.iromi}</td>
              <td>{report.evidenceLevel}</td>
              <td>{report.continueStopRecommendation}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReportDrawer({ report }: { report: AdLiftReport }) {
  const blocked = report.evidenceLevel === "blocked" || report.preTrendStatus !== "PASS";
  return (
    <aside className={styles.drawer} data-testid="adlift-report-card" aria-label={`${report.id} report`}>
      <div className={styles.badgeRow}>
        <Badge label={report.preTrendStatus} tone={preTrendTone[report.preTrendStatus]} marker="!" />
        <Badge label={`Evidence ${report.evidenceLevel}`} tone={blocked ? "orange" : "purple"} marker="▧" />
        <Badge label={report.continueStopRecommendation} tone={recommendationTone[report.continueStopRecommendation]} marker="●" />
      </div>
      <h2>{report.id} · {report.campaign}</h2>
      <section className={styles.metricRow} id="evidence">
        <span className={styles.metric}><span>Treatment stores</span><strong>{report.treatmentStores}</strong></span>
        <span className={styles.metric}><span>Control stores</span><strong>{report.controlStores}</strong></span>
        <span className={styles.metric}><span>iROMI</span><strong>{report.iromi}</strong></span>
      </section>
      <section className={blocked ? styles.warningBlock : styles.softBlock} data-testid="adlift-claim-guard">
        <h3>Causal claim guard</h3>
        <p>{report.claimGuard}</p>
        <p>{report.contamination}</p>
      </section>
      <section className={styles.softBlock}>
        <h3>Incrementality</h3>
        <p>incremental revenue: {report.incrementalRevenue}</p>
        <p>incremental gross margin: {report.incrementalGrossMargin}</p>
      </section>
      <DecisionPanel report={report} blocked={blocked} />
    </aside>
  );
}

function DecisionPanel({ report, blocked }: { report: AdLiftReport; blocked: boolean }) {
  return (
    <section id="decision" className={styles.approvalPanel} data-testid="adlift-decision-panel">
      <h2>Continue / Stop decision</h2>
      <p>Continue/stop decisions are high-risk marketing actions; submit reason and wait for backend audit.</p>
      <form>
        <label>
          Decision
          <select defaultValue={report.continueStopRecommendation}>
            <option>CONTINUE</option>
            <option>STOP</option>
            <option>REVIEW_ONLY</option>
          </select>
        </label>
        <label>
          Reason
          <textarea defaultValue="確認 treatment/control、pre-trend、contamination 與 evidence level 後提交，不做 optimistic update。" />
        </label>
        <button className={blocked ? styles.dangerButton : styles.primaryButton} type="button">
          {blocked ? "停止或轉人工審查" : "延續此活動"}
        </button>
      </form>
      <p className={styles.auditLine}>
        decision_id {report.decisionId} · correlation_id {report.audit.correlationId} · model {report.audit.modelVersion} · policy {report.audit.policyVersion} · feature snapshot {report.audit.featureSnapshotTime}
      </p>
    </section>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
