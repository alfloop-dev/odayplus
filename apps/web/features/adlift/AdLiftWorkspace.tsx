import Link from "next/link";
import type { AdliftReport as LiveAdLiftReport } from "@oday-plus/openapi-client";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "../operations/ProductionDataState.tsx";
import { freshness, preTrendTone, recommendationTone, reports, type AdLiftReport } from "./data.ts";
import styles from "../intervention/intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type AdLiftWorkspaceProps = {
  searchParams?: SearchParams;
  liveReports?: ApiBinding<LiveAdLiftReport>;
  isProduction?: boolean;
};

export function AdLiftWorkspace({
  searchParams = {},
  liveReports,
  isProduction: isProductionProp,
}: AdLiftWorkspaceProps) {
  if (resolveProductionMode(isProductionProp)) {
    return <ProductionAdLiftWorkspace binding={liveReports} searchParams={searchParams} />;
  }
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

function ProductionAdLiftWorkspace({
  binding,
  searchParams,
}: {
  binding?: ApiBinding<LiveAdLiftReport>;
  searchParams: SearchParams;
}) {
  const state = productionBindingState(binding);
  const selectedId = readParam(searchParams.selected);
  const selected = binding?.items.find((report) => liveReportId(report) === selectedId);

  return (
    <>
      <PageHeader
        title="廣告增益"
        summary="Production AdLift reports. Only persisted API results are rendered."
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "定價 Pricing", href: "/pricing" }, { label: "廣告增益" }]}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        lastUpdated={binding?.fetchedAt ? `API checked ${binding.fetchedAt}` : "Live source not available"}
      />
      <main className="odp-content" data-testid="adlift-production-page">
        <nav className={styles.workspaceNav} aria-label="AdLift module navigation">
          <Link href="/pricing">PriceOps Plans</Link>
          <Link aria-current="page" href="/adlift">AdLift Reports</Link>
          <Link href="/interventions">Intervention overlaps</Link>
        </nav>
        <ProductionDataState binding={binding} resource="AdLift reports" testId="adlift-production-data-state">
          {binding ? (
            <section className={styles.panel} data-testid="adlift-live-reports">
              <div className={styles.badgeRow}>
                <h2>AdLift reports（API live）</h2>
                <ProductionDataBadge binding={binding} testId="adlift-data-source" />
              </div>
              <LiveReportSummary reports={binding.items} />
              <LiveReportTable reports={binding.items} selectedId={selectedId} />
              {selectedId && !selected ? (
                <p data-testid="adlift-report-not-found">
                  API 回傳資料中沒有 {selectedId}；未以固定報告替代。
                </p>
              ) : null}
              {selected ? <LiveReportDetail report={selected} /> : null}
            </section>
          ) : null}
        </ProductionDataState>
      </main>
    </>
  );
}

function LiveReportSummary({ reports: liveReports }: { reports: LiveAdLiftReport[] }) {
  const blocked = liveReports.filter((report) => !liveCausalClaimAllowed(report)).length;
  const contaminated = liveReports.filter((report) => liveContaminationCount(report) > 0).length;
  return (
    <section className={styles.overviewGrid} aria-label="Live AdLift overview">
      <Summary title="Reports ready" value={String(liveReports.length)} copy="API report count" />
      <Summary title="Blocked claims" value={String(blocked)} copy="API causal claim guard" />
      <Summary title="Contamination" value={String(contaminated)} copy="API contamination findings" />
    </section>
  );
}

function LiveReportTable({
  reports: liveReports,
  selectedId,
}: {
  reports: LiveAdLiftReport[];
  selectedId?: string;
}) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="adlift-live-table">
        <caption>Persisted AdLift reports served by GET /adlift/reports.</caption>
        <thead>
          <tr>
            <th>Report</th>
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
          {liveReports.map((report) => {
            const reportId = liveReportId(report);
            return (
              <tr key={reportId} aria-selected={reportId === selectedId} data-testid="adlift-live-row">
                <td><Link href={`/adlift?selected=${encodeURIComponent(reportId)}`}>{reportId}</Link></td>
                <td>{liveString(report.campaign_name) || liveString(report.campaign_id) || "—"}</td>
                <td>{liveStringList(report.treatment_store_ids).length}</td>
                <td>{liveStringList(report.control_store_ids).length}</td>
                <td>{liveString(report.pre_trend_status) || "—"}</td>
                <td>{liveNumber(report.iromi)}</td>
                <td>{liveString(report.evidence_level) || "—"}</td>
                <td>{liveString(report.recommendation) || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LiveReportDetail({ report }: { report: LiveAdLiftReport }) {
  return (
    <aside className={styles.drawer} data-testid="adlift-live-report-detail">
      <h2>{liveReportId(report)} · {liveString(report.campaign_name) || "Campaign"}</h2>
      <p>incremental revenue: {liveNumber(report.incremental_revenue)}</p>
      <p>incremental gross margin: {liveNumber(report.incremental_gross_margin)}</p>
      <p>model: {liveString(report.model_version) || "—"}</p>
      <p>policy: {liveString(report.policy_version) || "—"}</p>
      <p>generated at: {liveString(report.generated_at) || "—"}</p>
      <p>source snapshots: {liveStringList(report.source_snapshot_ids).join(", ") || "—"}</p>
    </aside>
  );
}

function liveReportId(report: LiveAdLiftReport): string {
  return liveString(report.report_id) || liveString(report.campaign_id) || "unknown-report";
}

function liveString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function liveStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function liveNumber(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
}

function liveCausalClaimAllowed(report: LiveAdLiftReport): boolean {
  return report.causal_claim_allowed === true;
}

function liveContaminationCount(report: LiveAdLiftReport): number {
  return Array.isArray(report.contamination) ? report.contamination.length : 0;
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
