import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import { AccessibleDrawer } from "./AccessibleDrawer.tsx";
import { HeatZoneMap } from "../map/HeatZoneMap.tsx";
import {
  candidates,
  decisionTone,
  freshness,
  heatZones,
  listings,
  recommendationTone,
  selectedFromQuery,
  siteScoreReports,
  type ExpansionRouteKey,
  type SiteScoreReport,
} from "./data.ts";
import styles from "./expansion.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type ExpansionWorkspaceProps = {
  view?: ExpansionRouteKey;
  reportId?: string;
  searchParams?: SearchParams;
};

const pages = [
  { key: "heatzone", label: "HeatZone Radar", href: "/w/expansion/heatzone" },
  { key: "listings", label: "Listing 收件匣", href: "/w/expansion/listings" },
  { key: "candidates", label: "Candidate Sites", href: "/w/expansion/candidates" },
  { key: "sitescore", label: "SiteScore Reports", href: "/w/expansion/sitescore" },
];

export function ExpansionWorkspace({
  view = "overview",
  reportId,
  searchParams = {},
}: ExpansionWorkspaceProps) {
  if (view === "heatzone") return <HeatZonePage searchParams={searchParams} />;
  if (view === "listings") return <ListingsPage searchParams={searchParams} />;
  if (view === "candidates") return <CandidatesPage searchParams={searchParams} />;
  if (view === "sitescore") return <SiteScoreListPage searchParams={searchParams} />;
  if (view === "sitescoreDetail") return <SiteScoreDetailPage reportId={reportId} />;
  return <ExpansionOverview />;
}

function ExpansionOverview() {
  return (
    <>
      <Header
        title="展店選址"
        summary="HeatZone 探勘、Listing 去重、候選點比較與 SiteScore 核准流程。"
        status="FRESH"
      />
      <section aria-label="Expansion overview" className="odp-content">
        <WorkspaceNav active="overview" />
        <section className={styles.flowGrid} aria-label="Expansion decision flow">
          {pages.map((page, index) => (
            <Link className={styles.flowCard} href={page.href} key={page.key}>
              <span className={styles.step}>{index + 1}</span>
              <h2>{page.label}</h2>
              <p>{flowCopy[page.key]}</p>
            </Link>
          ))}
        </section>
        <section className={styles.twoColumn}>
          <StatusCard />
          <DecisionSeparation />
        </section>
      </section>
    </>
  );
}

const flowCopy: Record<string, string> = {
  heatzone: "地圖與排名列表並行，低信心熱區只允許補件或人工查核。",
  listings: "檢查匯入、解析、去重、硬規則失敗與候選點轉換。",
  candidates: "比較可行性、HeatZone context、SiteScore readiness 與實勘任務。",
  sitescore: "掃描報告、M1/M3/M6/M12 區間、審查狀態與模型新鮮度。",
};

function Header({
  title,
  summary,
  status,
  reportId,
}: {
  title: string;
  summary: string;
  status: string;
  reportId?: string;
}) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      breadcrumb={[
        { label: "展店 Expansion", href: "/expansion" },
        ...(reportId ? [{ label: "SiteScore Reports", href: "/w/expansion/sitescore" }] : []),
        { label: reportId ?? title },
      ]}
      status={{
        label: status,
        tone: dataStatusTone[freshness.status],
        marker: "◆",
        "data-testid": "expansion-data-status",
      }}
      lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion}`}
      actions={
        <div className={styles.headerActions}>
          <a className={styles.secondaryButton} href="#audit">
            View audit
          </a>
          <a className={styles.primaryButton} href="#primary-action">
            {title === "HeatZone Radar" ? "重新計算 HeatZone" : "Export visible rows"}
          </a>
        </div>
      }
    />
  );
}

function WorkspaceNav({ active }: { active: ExpansionRouteKey | "overview" }) {
  return (
    <nav className={styles.workspaceNav} aria-label="Expansion module navigation">
      <Link aria-current={active === "overview" ? "page" : undefined} href="/expansion">
        Overview
      </Link>
      {pages.map((page) => (
        <Link
          aria-current={active === page.key ? "page" : undefined}
          data-testid={`exp-nav-${page.key}`}
          href={page.href}
          key={page.key}
        >
          {page.label}
        </Link>
      ))}
    </nav>
  );
}

function FilterBar({ children }: { children: React.ReactNode }) {
  return (
    <form className={styles.filterBar} aria-label="URL synced filters">
      {children}
      <a className={styles.secondaryButton} href="?district=all&state=STILL_EXPANDABLE&drawer=zone&selected=hz-1049">
        Saved view
      </a>
      <a className={styles.secondaryButton} href="#export">
        Export
      </a>
    </form>
  );
}

function HeatZonePage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? heatZones[0].id;
  const layerQuery = selectedFromQuery(searchParams.layers);
  const selectedZone = heatZones.find((zone) => zone.id === selected) ?? heatZones[0];

  return (
    <>
      <Header
        title="HeatZone Radar"
        summary="依需求缺口、ODay G2 Fit、租金可行性與 cannibalization risk 排序展店熱區。"
        status="FRESH"
      />
      <section aria-label="HeatZone Radar workspace" className="odp-content" data-testid="exp-heatzone-page">
        <WorkspaceNav active="heatzone" />
        <FilterBar>
          <label>
            District
            <select name="district" defaultValue="all">
              <option value="all">全部</option>
              <option value="taipei">台北市</option>
            </select>
          </label>
          <label>
            State
            <select name="state" defaultValue="STILL_EXPANDABLE">
              <option>STILL_EXPANDABLE</option>
              <option>SUPPRESSED_LOW_CONFIDENCE</option>
              <option>UNDER_REALIZED</option>
            </select>
          </label>
          <label>
            scoreMin
            <input name="scoreMin" defaultValue="65" />
          </label>
          <label>
            confidenceMin
            <input name="confidenceMin" defaultValue="0.70" />
          </label>
        </FilterBar>
        <section className={styles.mapLayout}>
          <HeatZoneMap
            candidates={candidates}
            freshness={freshness}
            layerQuery={layerQuery}
            listings={listings}
            selectedZoneId={selectedZone.id}
            zones={heatZones}
          />
          <aside className={styles.sidePanel} aria-label="Ranked HeatZone list">
            <h2>Top zones</h2>
            <DenseTable
              headers={["Rank", "Zone", "Score", "Confidence", "State"]}
              rows={heatZones.map((zone) => [
                `#${zone.rank}`,
                <a
                  aria-current={zone.id === selectedZone.id ? "true" : undefined}
                  data-testid={`heatzone-row-${zone.id}`}
                  href={`/w/expansion/heatzone?selected=${zone.id}&drawer=zone`}
                  key={zone.id}
                >
                  {zone.id}
                </a>,
                zone.score,
                zone.confidence.toFixed(2),
                zone.state,
              ])}
            />
          </aside>
        </section>
        <Drawer
          returnFocusTestId={`heatzone-row-${selectedZone.id}`}
          title={`${selectedZone.id} · ${selectedZone.district}`}
          testId="heatzone-drawer"
        >
          <HeatZoneScoreCard zone={selectedZone} />
        </Drawer>
      </section>
    </>
  );
}

function HeatZoneScoreCard({ zone }: { zone: (typeof heatZones)[number] }) {
  const isSuppressed = zone.confidence < 0.7 || zone.state === "SUPPRESSED_LOW_CONFIDENCE";
  return (
    <div className={styles.cardStack} data-testid="heatzone-score-card">
      <div className={styles.metricRow}>
        <Metric label="HeatZone score" value={zone.score} />
        <Metric label="Confidence" value={zone.confidence.toFixed(2)} />
        <Metric label="Listings" value={zone.listings} />
      </div>
      <Badge label={zone.state} tone={isSuppressed ? "orange" : "green"} marker="▧" />
      <SplitList title="Score reasons" items={zone.reasons} />
      <SplitList title="Warnings" items={zone.warnings} tone="warning" />
      <p className={styles.auditLine}>
        Snapshot {zone.featureSnapshotTime} · model {zone.modelVersion} · source {freshness.sourceSnapshotId}
      </p>
      {isSuppressed ? (
        <p className={styles.blockedAction}>低信心 guard：禁止直接送 SiteScore，只能建立資料補件或人工查核任務。</p>
      ) : (
        <a className={styles.primaryButton} href={`/w/expansion/listings?heatZone=${zone.id}`}>
          查看 Listing
        </a>
      )}
    </div>
  );
}

function ListingsPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? listings[0].id;
  const listing = listings.find((item) => item.id === selected) ?? listings[0];
  return (
    <>
      <Header
        title="Listing 收件匣"
        summary="處理外部房源匯入、解析、去重、硬規則與候選點轉換。"
        status="FRESH"
      />
      <section aria-label="Listing inbox workspace" className="odp-content" data-testid="exp-listings-page">
        <WorkspaceNav active="listings" />
        <ImportSummary />
        <FilterBar>
          <label>
            Status
            <select name="status" defaultValue="all">
              <option value="all">全部</option>
              <option>DUPLICATE</option>
              <option>FAILED_HARD_RULE</option>
              <option>CANDIDATE</option>
            </select>
          </label>
          <label>
            heatZone
            <input name="heatZone" defaultValue={selectedFromQuery(searchParams.heatZone) ?? ""} />
          </label>
        </FilterBar>
        <DenseTable
          caption="Listing Inbox table"
          headers={["Listing", "Status", "Issue", "Rent / Area", "Geocode", "Duplicate", "HeatZone", "Updated", "Action"]}
          rows={listings.map((item) => [
            <a href={`/w/expansion/listings?selected=${item.id}&drawer=listing`} key={item.id}>{item.source}<br />{item.address}</a>,
            item.status,
            item.issue,
            `${item.rent} / ${item.area}`,
            item.geocode,
            item.duplicate,
            item.heatZoneId,
            item.updatedAt,
            item.action,
          ])}
        />
        <Drawer title={`${listing.id} · ${listing.address}`} testId="listing-drawer">
          <DrawerSection title="Source record" body={`${listing.source} · 原始地址、租金與坪數保留 field lineage。`} />
          <DrawerSection title="Parsed canonical" body={`${listing.address} · ${listing.geocode}`} />
          <DrawerSection title="Issues" body={`${listing.status} · ${listing.issue}`} />
          <DrawerSection title="Candidate conversion" body={listing.action === "建立候選點" ? "顯示 CandidateSiteCard preview，成功後回傳 job_id。" : "需先處理阻擋問題，不做 optimistic update。"} />
          <DrawerSection title="Audit" body={`source snapshot ${freshness.sourceSnapshotId} · correlation_id corr-${listing.id}`} />
        </Drawer>
      </section>
    </>
  );
}

function CandidatesPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? candidates[0].id;
  const candidate = candidates.find((item) => item.id === selected) ?? candidates[0];
  return (
    <>
      <Header
        title="Candidate Sites"
        summary="比較候選點可行性並送出 SiteScore，缺必要資料時明確 disabled reason。"
        status="FRESH"
      />
      <section aria-label="Candidate sites workspace" className="odp-content" data-testid="exp-candidates-page">
        <WorkspaceNav active="candidates" />
        <FilterBar>
          <label>
            Pipeline status
            <select name="status" defaultValue="all">
              <option value="all">全部</option>
              <option>new</option>
              <option>screened</option>
              <option>rejected</option>
            </select>
          </label>
          <label>
            HeatZone
            <input name="heatZone" defaultValue="hz-1049" />
          </label>
        </FilterBar>
        <DenseTable
          caption="Candidate Sites table"
          headers={["Candidate", "Status", "HeatZone", "Rent / Area / Frontage", "Geocode", "Feasibility", "Listing Source", "SiteScore", "Action"]}
          rows={candidates.map((item) => [
            <a href={`/w/expansion/candidates?selected=${item.id}&drawer=candidate`} key={item.id}>{item.id}<br />{item.address}</a>,
            item.status,
            `${item.heatZoneId} / ${item.heatZoneScore}`,
            item.rentArea,
            item.geocode,
            item.feasibility,
            item.listingSource,
            item.siteScore,
            item.readiness === "ready" ? "執行 SiteScore" : `Disabled: ${item.disabledReason}`,
          ])}
        />
        <Drawer title={`${candidate.id} · ${candidate.address}`} testId="candidate-drawer">
          <CandidateSiteCard candidate={candidate} />
        </Drawer>
      </section>
    </>
  );
}

function SiteScoreListPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? siteScoreReports[0].id;
  const report = siteScoreReports.find((item) => item.id === selected) ?? siteScoreReports[0];
  return (
    <>
      <Header
        title="SiteScore Reports"
        summary="掃描評分報告、待審狀態、模型新鮮度與 M1/M3/M6/M12 預測區間。"
        status="FRESH"
      />
      <section aria-label="SiteScore reports workspace" className="odp-content" data-testid="exp-sitescore-page">
        <WorkspaceNav active="sitescore" />
        <FilterBar>
          <label>
            Decision
            <select name="decision" defaultValue="PENDING_REVIEW">
              <option>PENDING_REVIEW</option>
              <option>SYSTEM_RECOMMENDED</option>
              <option>APPROVED</option>
            </select>
          </label>
          <label>
            Model version
            <input name="modelVersion" defaultValue="sitescore-v1.4.2" />
          </label>
        </FilterBar>
        <DenseTable
          caption="SiteScore Reports table"
          headers={["Candidate", "Recommendation", "M1/M3/M6/M12", "Payback", "Confidence", "Data freshness", "Decision", "Owner / SLA", "Action"]}
          rows={siteScoreReports.map((item) => [
            <a href={`/w/expansion/sitescore?selected=${item.id}&drawer=report`} key={item.id}>{item.address}<br />{item.targetFormat}</a>,
            `${item.recommendation} · ${item.reason}`,
            item.intervals.map((i) => `${i.month} ${i.p10}/${i.p50}/${i.p90}`).join(" · "),
            item.payback,
            `${item.confidence} · ${item.confidenceReasons.join(", ")}`,
            `${item.dataStatus} · ${item.featureSnapshotTime}`,
            item.decisionStatus,
            `${item.owner} · ${item.sla}`,
            <Link href={`/w/expansion/sitescore/${item.id}`} key={item.id}>開啟完整報告</Link>,
          ])}
        />
        <Drawer title={`${report.id} preview`} testId="sitescore-preview-drawer">
          <ReportPreview report={report} />
        </Drawer>
      </section>
    </>
  );
}

function SiteScoreDetailPage({ reportId }: { reportId?: string }) {
  const report = siteScoreReports.find((item) => item.id === reportId) ?? siteScoreReports[0];
  return (
    <>
      <Header
        title={report.address}
        summary={`系統建議 ${report.recommendation}，M12 P50 ${report.intervals[3].p50}，confidence ${report.confidence}，需展店審查核准。`}
        status={report.dataStatus}
        reportId={report.id}
      />
      <section aria-label="SiteScore report detail workspace" className="odp-content" data-testid="exp-sitescore-detail-page">
        <WorkspaceNav active="sitescoreDetail" />
        <nav className={styles.anchorTabs} aria-label="Report anchors">
          {["summary", "status", "evidence", "recommendation", "decision", "execution", "audit"].map((id) => (
            <a href={`#${id}`} key={id}>{id}</a>
          ))}
        </nav>
        <section className={styles.reportGrid}>
          <article className={styles.reportMain}>
            <ReportSummary report={report} />
            <StatusSection report={report} />
            <EvidencePanel report={report} />
            <RecommendationSection report={report} />
            <ExecutionSection report={report} />
            <AuditSection report={report} />
          </article>
          <aside className={styles.stickyPanel} id="decision">
            <ApprovalPanel report={report} />
          </aside>
        </section>
      </section>
    </>
  );
}

function DenseTable({
  headers,
  rows,
  caption,
}: {
  headers: string[];
  rows: React.ReactNode[][];
  caption?: string;
}) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        {caption ? <caption>{caption}</caption> : null}
        <thead>
          <tr>
            {headers.map((header, index) => (
              <th aria-sort={index === 0 ? "ascending" : undefined} key={header}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr tabIndex={0} key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Drawer({
  title,
  children,
  testId,
  returnFocusTestId,
}: {
  title: string;
  children: React.ReactNode;
  testId: string;
  returnFocusTestId?: string;
}) {
  return (
    <AccessibleDrawer returnFocusTestId={returnFocusTestId} title={title} testId={testId}>
      {children}
    </AccessibleDrawer>
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

function SplitList({ title, items, tone }: { title: string; items: string[]; tone?: "warning" }) {
  return (
    <section className={tone === "warning" ? styles.warningBlock : styles.softBlock}>
      <h3>{title}</h3>
      <ul>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </section>
  );
}

function ImportSummary() {
  return (
    <section className={styles.summaryBand} aria-label="Import summary">
      <Metric label="Accepted" value="42" />
      <Metric label="Duplicate" value="7" />
      <Metric label="Rejected" value="3" />
      <Metric label="Source" value="591 + broker" />
    </section>
  );
}

function DrawerSection({ title, body }: { title: string; body: string }) {
  return (
    <section className={styles.softBlock}>
      <h3>{title}</h3>
      <p>{body}</p>
    </section>
  );
}

function CandidateSiteCard({ candidate }: { candidate: (typeof candidates)[number] }) {
  return (
    <div className={styles.cardStack} data-testid="candidate-site-card">
      <div className={styles.metricRow}>
        <Metric label="HeatZone" value={`${candidate.heatZoneId} / ${candidate.heatZoneScore}`} />
        <Metric label="Geocode" value={candidate.geocode} />
      </div>
      <DrawerSection title="Nearby evidence" body="competitor count 4 · POI density high · active listings 8 · existing store count 1" />
      <DrawerSection title="Feasibility checklist" body={candidate.feasibility} />
      {candidate.readiness === "ready" ? (
        <a className={styles.primaryButton} href="/w/expansion/sitescore/ssr-7001">執行 SiteScore</a>
      ) : (
        <p className={styles.blockedAction}>{candidate.disabledReason}</p>
      )}
    </div>
  );
}

function ReportPreview({ report }: { report: SiteScoreReport }) {
  return (
    <div className={styles.cardStack}>
      <Badge label={report.recommendation} tone={recommendationTone(report.recommendation)} marker="◆" />
      <p>{report.reason}</p>
      <SplitList title="Top positive factors" items={report.positiveFactors} />
      <SplitList title="Top negative factors" items={report.negativeFactors} tone="warning" />
      <Badge label={report.decisionStatus} tone={decisionTone(report.decisionStatus)} marker="▧" />
      <Link className={styles.primaryButton} href={`/w/expansion/sitescore/${report.id}`}>
        開啟完整報告
      </Link>
    </div>
  );
}

function ReportSummary({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="summary" data-testid="sitescore-summary">
      <h2>Summary</h2>
      {report.confidence === "low" ? <p className={styles.blockedAction}>Low confidence warning visible at top.</p> : null}
      <div className={styles.metricRow}>
        <Metric label="Recommendation" value={report.recommendation} />
        <Metric label="Payback" value={report.payback} />
        <Metric label="Model" value={report.modelVersion} />
      </div>
      <ForecastTable report={report} />
      <p>Rent reasonableness: within policy with warning · Cannibalization risk: medium · Comparable stores: {report.comparables.length}</p>
    </section>
  );
}

function ForecastTable({ report }: { report: SiteScoreReport }) {
  return (
    <table className={styles.intervalTable} aria-label="Forecast intervals P10 P50 P90">
      <thead>
        <tr><th>Month</th><th>P10</th><th>P50</th><th>P90</th></tr>
      </thead>
      <tbody>
        {report.intervals.map((interval) => (
          <tr key={interval.month}>
            <td>{interval.month}</td><td>{interval.p10}</td><td>{interval.p50}</td><td>{interval.p90}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StatusSection({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="status">
      <h2>Status</h2>
      <div className={styles.badgeRow}>
        <Badge label={report.decisionStatus} tone={decisionTone(report.decisionStatus)} marker="▧" />
        <Badge label={report.dataStatus} tone={dataStatusTone[report.dataStatus]} marker="◆" />
        <Badge label={`PRODUCTION ${report.modelVersion}`} tone="purple" marker="◇" />
        <Badge label={`SLA ${report.sla}`} tone="blue" marker="◫" />
        <Badge label="permission approve" tone="gray" marker="▣" />
      </div>
    </section>
  );
}

function EvidencePanel({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="evidence" data-testid="evidence-panel">
      <h2>Evidence</h2>
      <SplitList title="Positive factors" items={report.positiveFactors} />
      <SplitList title="Negative factors" items={report.negativeFactors} tone="warning" />
      <SplitList title="Comparable stores" items={report.comparables} />
      <SplitList title="Limitations" items={report.limitations} tone="warning" />
      <ForecastTable report={report} />
    </section>
  );
}

function RecommendationSection({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="recommendation">
      <h2>Recommendation</h2>
      <p>
        Generated by system, not a human decision. Model {report.modelVersion}, policy {report.policyVersion},
        feature snapshot {report.featureSnapshotTime}, generated {report.generatedAt}. Required approval role: 展店審查。
      </p>
      <Badge label={report.recommendation} tone={recommendationTone(report.recommendation)} marker="◆" />
    </section>
  );
}

function ApprovalPanel({ report }: { report: SiteScoreReport }) {
  const disabled = report.dataStatus === "FAILED_QA" || report.dataStatus === "BLOCKED";
  return (
    <section className={styles.approvalPanel} data-testid="approval-panel">
      <h2>Decision</h2>
      <p>System recommendation: {report.recommendation}. Human decision is recorded separately and never optimistic.</p>
      <form>
        <label>
          Decision
          <select defaultValue="APPROVE" disabled={disabled}>
            <option>APPROVE</option>
            <option>REJECT</option>
            <option>REQUEST_REVISION</option>
            <option>OVERRIDE</option>
          </select>
        </label>
        <label>
          Reason required
          <textarea defaultValue="同意系統建議，需確認租金條件與現場動線後核准。" minLength={10} />
        </label>
        <label className={styles.checkboxLine}>
          <input type="checkbox" defaultChecked /> riskAcknowledged=true
        </label>
        <button className={styles.primaryButton} disabled={disabled} type="button" id="primary-action">
          核准此 SiteScore
        </button>
      </form>
      <p className={styles.auditLine}>
        success displays decision_id {report.audit.decisionId} · approval_id {report.audit.approvalId}
      </p>
    </section>
  );
}

function ExecutionSection({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="execution">
      <h2>Execution / Result</h2>
      <p>{report.recommendation === "GO" ? "After approval: create opening project, assign owner, target open date, required tasks." : "Create site visit/data task before execution."}</p>
      <Badge label="OBSERVING after opening; no mature outcome claimed" tone="gray" marker="◫" />
    </section>
  );
}

function AuditSection({ report }: { report: SiteScoreReport }) {
  return (
    <section className={styles.reportSection} id="audit">
      <h2>Version / Audit</h2>
      <dl className={styles.auditGrid}>
        <dt>report id</dt><dd>{report.id}</dd>
        <dt>candidate site id</dt><dd>{report.candidateId}</dd>
        <dt>model version</dt><dd>{report.modelVersion}</dd>
        <dt>policy version</dt><dd>{report.policyVersion}</dd>
        <dt>feature snapshot</dt><dd>{report.featureSnapshotTime}</dd>
        <dt>actor</dt><dd>{report.audit.actor}</dd>
        <dt>timestamp</dt><dd>{report.audit.timestamp}</dd>
        <dt>decision id</dt><dd>{report.audit.decisionId}</dd>
        <dt>approval id</dt><dd>{report.audit.approvalId}</dd>
        <dt>correlation id</dt><dd>{report.audit.correlationId}</dd>
      </dl>
    </section>
  );
}

function StatusCard() {
  return (
    <section className={styles.reportSection}>
      <h2>Shared page contract</h2>
      <div className={styles.badgeRow}>
        <Badge label="loading" tone="gray" marker="◫" />
        <Badge label="empty" tone="gray" marker="◫" />
        <Badge label="error + correlation_id" tone="red" marker="▧" />
        <Badge label="read-only permission" tone="blue" marker="▣" />
      </div>
      <p>Filter、sort、page、selected entity 與 drawer state 皆以 URL query 還原。</p>
      <dl className={styles.metaGrid} data-testid="external-freshness-lineage">
        <dt>source snapshot</dt>
        <dd className={styles.mono}>{freshness.sourceSnapshotId}</dd>
        <dt>provider observed</dt>
        <dd className={styles.mono}>{freshness.providerObservedAt}</dd>
        <dt>ingested at</dt>
        <dd className={styles.mono}>{freshness.ingestedAt}</dd>
        <dt>correlation_id</dt>
        <dd className={styles.mono}>{freshness.correlationId}</dd>
      </dl>
    </section>
  );
}

function DecisionSeparation() {
  return (
    <section className={styles.reportSection}>
      <h2>Decision separation</h2>
      <ol className={styles.timeline}>
        <li>Prediction: HeatZone score, P10/P50/P90, payback interval</li>
        <li>Recommendation: GO / WAIT / REJECT / INVESTIGATE with model and policy version</li>
        <li>Human decision: ApprovalPanel reason, risk acknowledgement, audit ids</li>
        <li>Execution: opening tasks, visit tasks, revisit dates</li>
        <li>Outcome: observing until mature result exists</li>
      </ol>
    </section>
  );
}
