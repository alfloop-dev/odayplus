import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import {
  AVM_POLICY_VERSION,
  caseStatusTone,
  confidenceTone,
  dataRoomLabel,
  financeApprovalLabel,
  freshness,
  selectedFromQuery,
  valuationCases,
  type AvmRouteKey,
  type LensValuation,
  type ValuationCase,
} from "./data.ts";
import styles from "./avm.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type AvmWorkspaceProps = {
  view?: AvmRouteKey;
  caseId?: string;
  searchParams?: SearchParams;
};

export function AvmWorkspace({ view = "overview", caseId, searchParams = {} }: AvmWorkspaceProps) {
  if (view === "cases") return <CasesListPage searchParams={searchParams} />;
  if (view === "caseDetail") return <CaseDetailPage caseId={caseId} />;
  return <AvmOverview />;
}

function Header({
  title,
  summary,
  caseId,
}: {
  title: string;
  summary: string;
  caseId?: string;
}) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      breadcrumb={[
        { label: "財務／交易 DealRoom", href: "/avm" },
        ...(caseId ? [{ label: "估值案件", href: "/w/dealroom/cases" }] : []),
        { label: caseId ?? title },
      ]}
      status={{
        label: "AVM model dealroom-avm-baseline-v1",
        tone: "purple",
        marker: "◇",
        "data-testid": "avm-data-status",
      }}
      lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion} · source ${freshness.sourceSnapshotId}`}
      actions={
        <div className={styles.headerActions}>
          <a className={styles.secondaryButton} href="#audit">
            View audit
          </a>
          <a className={styles.primaryButton} href="#primary-action">
            建立估值案件
          </a>
        </div>
      }
    />
  );
}

function WorkspaceNav({ active }: { active: AvmRouteKey }) {
  return (
    <nav className={styles.workspaceNav} aria-label="DealRoomAVM navigation">
      <Link aria-current={active === "overview" ? "page" : undefined} href="/avm">
        Overview
      </Link>
      <Link
        aria-current={active === "cases" || active === "caseDetail" ? "page" : undefined}
        data-testid="avm-nav-cases"
        href="/w/dealroom/cases"
      >
        估值案件
      </Link>
    </nav>
  );
}

function AvmOverview() {
  return (
    <>
      <Header
        title="DealRoomAVM 估值"
        summary="對門市做三鏡估值、財務核准並備妥交易資料室。"
      />
      <main className="odp-content" data-testid="avm-overview-page">
        <WorkspaceNav active="overview" />
        <section className={styles.flowGrid} aria-label="AVM decision flow">
          <Link className={styles.flowCard} href="/w/dealroom/cases">
            <span className={styles.step}>1</span>
            <h2>估值案件列表</h2>
            <p>掃描案件狀態、信心、待核准與 DataRoom 就緒。</p>
          </Link>
          <Link className={styles.flowCard} href={`/w/dealroom/cases/${valuationCases[0].caseId}`}>
            <span className={styles.step}>2</span>
            <h2>估值案件詳情</h2>
            <p>三鏡估值區間、財務核准（含 reserve override）、建立並匯出 DataRoom。</p>
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

function CasesListPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedFromQuery(searchParams.selected) ?? valuationCases[0].caseId;
  const drawerCase = valuationCases.find((c) => c.caseId === selected) ?? valuationCases[0];
  return (
    <>
      <Header
        title="估值案件"
        summary="對門市做三鏡估值、財務核准並備妥交易資料室。"
      />
      <main className="odp-content" data-testid="avm-cases-page">
        <WorkspaceNav active="cases" />
        <FilterBar />
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption>估值案件列表（reserve / asking 為敏感欄位，依權限遮罩）</caption>
            <thead>
              <tr>
                {["Case", "Status", "Fair (P50)", "Reserve / Asking", "Confidence", "Finance approval", "DataRoom", "Action"].map(
                  (header, index) => (
                    <th aria-sort={index === 0 ? "ascending" : undefined} key={header}>
                      {header}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {valuationCases.map((c) => (
                <tr tabIndex={0} key={c.caseId}>
                  <td>
                    <a href={`/w/dealroom/cases?selected=${c.caseId}&drawer=case`}>
                      {c.caseId}
                      <br />
                      {c.storeId}
                    </a>
                  </td>
                  <td>
                    <Badge label={c.status} tone={caseStatusTone(c.status)} marker="◆" />
                  </td>
                  <td>{c.fairPrice.p50.toLocaleString()}</td>
                  <td>
                    <span title="敏感欄位，依權限遮罩">
                      {c.reservePrice.toLocaleString()} / {c.askingPrice.toLocaleString()}
                    </span>
                  </td>
                  <td>
                    <Badge label={c.confidence} tone={confidenceTone(c.confidence)} marker="▧" />
                  </td>
                  <td>{financeApprovalLabel(c)}</td>
                  <td>{dataRoomLabel(c)}</td>
                  <td>
                    <Link href={`/w/dealroom/cases/${c.caseId}`}>開啟案件詳情</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Drawer title={`${drawerCase.caseId} · ${drawerCase.storeId}`} testId="avm-case-drawer">
          <div className={styles.cardStack}>
            <div className={styles.metricRow}>
              <Metric label="Status" value={drawerCase.status} />
              <Metric label="Fair P50" value={drawerCase.fairPrice.p50.toLocaleString()} />
              <Metric label="Confidence" value={drawerCase.confidence} />
            </div>
            <p>
              Reserve / Asking（敏感，依權限遮罩）：{drawerCase.reservePrice.toLocaleString()} /{" "}
              {drawerCase.askingPrice.toLocaleString()}
            </p>
            <p>Finance approval：{financeApprovalLabel(drawerCase)} · DataRoom：{dataRoomLabel(drawerCase)}</p>
            <p className={styles.auditLine}>correlation_id {drawerCase.correlationId}</p>
            <Link className={styles.primaryButton} href={`/w/dealroom/cases/${drawerCase.caseId}`}>
              開啟案件詳情
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
        store_id
        <input name="store_id" defaultValue="" />
      </label>
      <label>
        status
        <select name="status" defaultValue="all">
          <option value="all">全部</option>
          <option>DATA_READY</option>
          <option>REVIEW_REQUIRED</option>
          <option>APPROVED</option>
          <option>DATAROOM_READY</option>
        </select>
      </label>
      <label>
        confidence
        <select name="confidence" defaultValue="all">
          <option value="all">全部</option>
          <option>high</option>
          <option>medium</option>
          <option>low</option>
        </select>
      </label>
      <label className={styles.checkboxLine}>
        <input type="checkbox" name="pendingApproval" /> 待核准
      </label>
      <label className={styles.checkboxLine}>
        <input type="checkbox" name="dataRoomReady" /> DataRoom 就緒
      </label>
    </form>
  );
}

function CaseDetailPage({ caseId }: { caseId?: string }) {
  const c = valuationCases.find((item) => item.caseId === caseId) ?? valuationCases[0];
  return (
    <>
      <Header
        title={`${c.caseId} · ${c.storeId}`}
        summary={`目前狀態 ${c.status}，fair P50 ${c.fairPrice.p50.toLocaleString()}，confidence ${c.confidence}。系統估值與人工核准分離呈現。`}
        caseId={c.caseId}
      />
      <main className="odp-content" data-testid="avm-case-detail-page">
        <WorkspaceNav active="caseDetail" />
        <nav className={styles.anchorTabs} aria-label="Case anchors">
          {["summary", "status", "normalized", "valuation", "approval", "dataroom", "audit"].map((id) => (
            <a href={`#${id}`} key={id}>
              {id}
            </a>
          ))}
        </nav>
        <section className={styles.reportGrid}>
          <article className={styles.reportMain}>
            <SummarySection caseData={c} />
            <StatusSection caseData={c} />
            <NormalizedMarginSection caseData={c} />
            <ValuationSection caseData={c} />
            <DataRoomSection caseData={c} />
            <AuditSection caseData={c} />
          </article>
          <aside className={styles.stickyPanel}>
            <ApprovalPanel caseData={c} />
          </aside>
        </section>
      </main>
    </>
  );
}

function SummarySection({ caseData: c }: { caseData: ValuationCase }) {
  return (
    <section className={styles.reportSection} id="summary" data-testid="avm-summary">
      <h2>Summary</h2>
      <div className={styles.metricRow}>
        <Metric label="Store" value={c.storeId} />
        <Metric label="Status" value={c.status} />
        <Metric label="Fair P50" value={c.fairPrice.p50.toLocaleString()} />
        <Metric label="Confidence" value={c.confidence} />
      </div>
      <p>
        財務核准：{financeApprovalLabel(c)} · DataRoom：{dataRoomLabel(c)} · liquidityScore {c.liquidityScore.toFixed(2)}
      </p>
    </section>
  );
}

function StatusSection({ caseData: c }: { caseData: ValuationCase }) {
  return (
    <section className={styles.reportSection} id="status">
      <h2>Status &amp; History</h2>
      <div className={styles.badgeRow}>
        <Badge label={c.status} tone={caseStatusTone(c.status)} marker="◆" />
        <Badge label={`confidence ${c.confidence}`} tone={confidenceTone(c.confidence)} marker="▧" />
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
          {c.statusHistory.map((t) => (
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

function NormalizedMarginSection({ caseData: c }: { caseData: ValuationCase }) {
  const m = c.normalizedMargin;
  return (
    <section className={styles.reportSection} id="normalized" data-testid="avm-normalized-margin">
      <h2>Normalized Margin</h2>
      <div className={styles.metricRow}>
        <Metric label="gm_ttm" value={m.gmTtm.toLocaleString()} />
        <Metric label="gm_fwd" value={m.gmFwd.toLocaleString()} />
        <Metric label="normalized_gm" value={m.normalizedGm.toLocaleString()} />
        <Metric label="confidence" value={m.confidence} />
      </div>
      <SplitList title="Adjustment reasons" items={m.adjustmentReasons} />
    </section>
  );
}

function ValuationSection({ caseData: c }: { caseData: ValuationCase }) {
  const all = c.lenses.flatMap((l) => [l.p10, l.p90]);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = Math.max(1, max - min);
  const pct = (v: number) => ((v - min) / span) * 100;
  return (
    <section className={styles.reportSection} id="valuation" data-testid="valuation-range-chart">
      <h2>Three-Lens Valuation</h2>
      <p>
        系統估值（model {c.modelVersion}），永不只顯示 P50。reserve {c.reservePrice.toLocaleString()}（P10·0.97）／
        asking {c.askingPrice.toLocaleString()}（P90·1.05）為敏感欄位，依權限遮罩。
      </p>
      <div className={styles.rangeChart} role="img" aria-label="估值三鏡 P10/P50/P90 區間比較">
        {c.lenses.map((lens) => (
          <div className={styles.rangeRow} key={lens.lens}>
            <span className={styles.rangeLabel}>{lens.lens}</span>
            <div className={styles.rangeTrack} title={`${lens.p10} / ${lens.p50} / ${lens.p90}`}>
              <span
                className={styles.rangeBand}
                style={{ left: `${pct(lens.p10)}%`, right: `${100 - pct(lens.p90)}%` }}
              />
              <span className={styles.rangeMid} style={{ left: `${pct(lens.p50)}%` }} />
              <span className={styles.rangeReserve} style={{ left: `${pct(c.reservePrice)}%` }} aria-hidden="true" />
              <span className={styles.rangeAsking} style={{ left: `${pct(c.askingPrice)}%` }} aria-hidden="true" />
            </div>
          </div>
        ))}
      </div>
      <table className={styles.intervalTable} aria-label="Valuation lenses data table">
        <thead>
          <tr>
            <th>Lens</th>
            <th>P10</th>
            <th>P50</th>
            <th>P90</th>
            <th>Method</th>
          </tr>
        </thead>
        <tbody>
          {c.lenses.map((lens) => (
            <tr key={lens.lens}>
              <td>{lens.lens}</td>
              <td>{lens.p10.toLocaleString()}</td>
              <td>{lens.p50.toLocaleString()}</td>
              <td>{lens.p90.toLocaleString()}</td>
              <td>{lens.method}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.lenses.map((lens) => (
        <LensEvidence key={lens.lens} lens={lens} />
      ))}
    </section>
  );
}

function LensEvidence({ lens }: { lens: LensValuation }) {
  return (
    <details className={styles.softBlock}>
      <summary>
        {lens.lens} · method {lens.method}
      </summary>
      <ul>
        {lens.evidence.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </details>
  );
}

function ApprovalPanel({ caseData: c }: { caseData: ValuationCase }) {
  const canApprove = c.status === "REVIEW_REQUIRED";
  const approved = c.financeApproval;
  return (
    <section className={styles.approvalPanel} id="approval" data-testid="avm-approval-panel">
      <h2>Approval (Finance)</h2>
      <p>
        系統 fair/reserve/asking 由 AVM 模型產生（{c.modelVersion}），人工核准獨立記錄、never optimistic。建立者不得核准自己的案件（segregation）。
      </p>
      {c.confidence === "low" ? (
        <p className={styles.riskNotice}>confidence=low：核准鈕仍可用，但須於 reason 說明風險。</p>
      ) : null}
      {approved ? (
        <p className={styles.auditLine}>
          success：decision_id {approved.decisionId} · actor {approved.actorId} · {approved.approvedAt} · policy{" "}
          {approved.policyVersion} · correlation_id {approved.correlationId}
        </p>
      ) : (
        <form>
          <label>
            系統 reserve_price（P10·0.97）
            <input defaultValue={c.reservePrice.toLocaleString()} readOnly />
          </label>
          <label className={styles.checkboxLine}>
            <input type="checkbox" name="reserveOverride" /> reserve override（覆寫須填 reason，標示與原值差）
          </label>
          <label>
            decision_reason（必填）
            <textarea name="reason" minLength={10} defaultValue="" placeholder="核准理由，至少 10 字" />
          </label>
          <button
            className={styles.primaryButton}
            type="button"
            id="primary-action"
            disabled={!canApprove}
          >
            {canApprove ? "財務核准此案" : "需先到 REVIEW_REQUIRED"}
          </button>
        </form>
      )}
      <p className={styles.auditLine}>policy {AVM_POLICY_VERSION}</p>
    </section>
  );
}

function DataRoomSection({ caseData: c }: { caseData: ValuationCase }) {
  const dr = c.dataRoom;
  return (
    <section className={styles.reportSection} id="dataroom" data-testid="avm-dataroom">
      <h2>DataRoom &amp; Export</h2>
      {!c.financeApproval ? (
        <p className={styles.riskNotice}>未財務核准（REVIEW_REQUIRED 前）不得建立 DataRoom 或匯出。</p>
      ) : null}
      {dr ? (
        <>
          <p>
            dataroom_id {dr.dataroomId} · 完整度 {(dr.completeness * 100).toFixed(0)}%
          </p>
          <table className={styles.intervalTable} aria-label="DataRoom checklist">
            <thead>
              <tr>
                <th>Item</th>
                <th>Status</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {dr.checklist.map((item) => (
                <tr key={item.key}>
                  <td>{item.label}</td>
                  <td>
                    <Badge
                      label={item.status === "ready" ? "ready" : "缺件"}
                      tone={item.status === "ready" ? "green" : "orange"}
                      marker="▣"
                    />
                  </td>
                  <td>{item.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={styles.softBlock}>
            <h3>Export audit</h3>
            <p>匯出為高風險審計動作：必填 reason、寫後端 Audit（avm.dataroom_exported.v1）、追加 export_audit。</p>
            <ul>
              {dr.exportAudit.map((e) => (
                <li key={e.correlationId}>
                  {e.exportedAt} · {e.actor} · {e.reason} · correlation_id {e.correlationId}
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : (
        <p>DataRoom 未建立。僅 APPROVED 後可 build_dataroom。</p>
      )}
    </section>
  );
}

function AuditSection({ caseData: c }: { caseData: ValuationCase }) {
  return (
    <section className={styles.reportSection} id="audit">
      <h2>Version / Audit</h2>
      <dl className={styles.auditGrid}>
        <dt>case id</dt>
        <dd>{c.caseId}</dd>
        <dt>model version</dt>
        <dd>{c.modelVersion}</dd>
        <dt>feature version</dt>
        <dd>{c.featureVersion}</dd>
        <dt>policy version</dt>
        <dd>{c.policyVersion}</dd>
        <dt>valuation version</dt>
        <dd>{c.valuationVersion}</dd>
        <dt>prediction origin time</dt>
        <dd>{c.predictionOriginTime}</dd>
        <dt>valued at</dt>
        <dd>{c.valuedAt}</dd>
        <dt>created by</dt>
        <dd>{c.createdBy}</dd>
        <dt>correlation id</dt>
        <dd>{c.correlationId}</dd>
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

function SplitList({ title, items, tone }: { title: string; items: string[]; tone?: "warning" }) {
  return (
    <section className={tone === "warning" ? styles.warningBlock : styles.softBlock}>
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function DecisionSeparation() {
  return (
    <section className={styles.reportSection}>
      <h2>Decision separation</h2>
      <ol className={styles.timeline}>
        <li>Prediction：三鏡 P10/P50/P90</li>
        <li>Recommendation：fair/reserve/asking，標示 model_version</li>
        <li>Human decision：財務核准（reserve override + reason）</li>
        <li>Execution：建立 DataRoom 並匯出</li>
        <li>Outcome：交易結果（未觀察前不宣稱成效）</li>
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
        <Badge label="read-only：reserve/asking 遮罩" tone="blue" marker="▣" />
      </div>
      <p>Filter、selected entity 與 drawer state 皆以 URL query 還原。</p>
    </section>
  );
}
