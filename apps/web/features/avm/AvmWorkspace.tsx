"use client";

import Link from "next/link";
import { useState, useEffect, useRef, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { AvmCase } from "@oday-plus/openapi-client";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { DataSourceBadge } from "../../src/components/DataSourceBadge.tsx";
import {
  AVM_POLICY_VERSION,
  AVM_MODEL_VERSION,
  AVM_FEATURE_VERSION,
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
  type Confidence,
} from "./data.ts";
import styles from "./avm.module.css";
import { ClientApprovalForm } from "../../src/components/ClientApprovalForm.tsx";
import { ClientCreateCaseButton } from "../../src/components/ClientCreateCaseButton.tsx";
const SENSITIVE_PRICE_MASK = "MASKED_BY_PERMISSION";


type SearchParams = Record<string, string | string[] | undefined>;

type AvmWorkspaceProps = {
  view?: AvmRouteKey;
  caseId?: string;
  searchParams?: SearchParams;
  /** Live `GET /avm/cases` binding; omitted on fixture-only routes. */
  liveCases?: ApiBinding<AvmCase>;
  isProduction?: boolean;
  currentUser?: {
    subjectId: string;
    roles: string;
    tenantId?: string;
  };
};

function mapLiveCaseToValuationCase(
  liveCase: any,
  report?: any,
  dataroom?: any
): ValuationCase {
  const fairPrice = report?.fair_price
    ? { p10: report.fair_price.p10, p50: report.fair_price.p50, p90: report.fair_price.p90 }
    : { p10: 0, p50: 0, p90: 0 };

  const normalizedMargin = report?.normalized_margin
    ? {
        gmTtm: report.normalized_margin.gm_ttm,
        gmFwd: report.normalized_margin.gm_fwd,
        normalizedGm: report.normalized_margin.normalized_gm,
        adjustmentReasons: report.normalized_margin.adjustment_reasons || [],
        confidence: report.normalized_margin.confidence,
      }
    : {
        gmTtm: 0,
        gmFwd: 0,
        normalizedGm: 0,
        adjustmentReasons: [],
        confidence: "low" as const,
      };

  const lenses = report?.lenses
    ? report.lenses.map((l: any) => ({
        lens: l.lens,
        p10: l.p10,
        p50: l.p50,
        p90: l.p90,
        method: l.method,
        evidence: Array.isArray(l.evidence)
          ? l.evidence
          : typeof l.evidence === "object" && l.evidence !== null
          ? Object.entries(l.evidence).map(([key, val]) => {
              if (Array.isArray(val)) return `${key}: ${val.join(", ")}`;
              if (typeof val === "object" && val !== null) return `${key}: ${JSON.stringify(val)}`;
              return `${key}: ${val}`;
            })
          : [],
      }))
    : [];

  const financeApproval = report?.finance_approval
    ? {
        decisionId: report.finance_approval.decision_id,
        actorId: report.finance_approval.actor_id,
        approvedAt: report.finance_approval.approved_at,
        decisionReason: report.finance_approval.decision_reason,
        reservePrice: report.finance_approval.reserve_price,
        reserveOverridden: report.finance_approval.reserve_overridden || false,
        policyVersion: report.finance_approval.policy_version || AVM_POLICY_VERSION,
        correlationId: report.finance_approval.correlation_id || "",
      }
    : null;

  const dataRoom = dataroom
    ? {
        dataroomId: dataroom.dataroom_id,
        completeness: dataroom.checklist ? dataroom.checklist.filter((item: any) => item.status === "ready").length / dataroom.checklist.length : 0,
        checklist: dataroom.checklist ? dataroom.checklist.map((item: any) => ({
          key: item.document_id,
          label: item.name,
          status: item.status,
          note: item.source_snapshot_id || "",
        })) : [],
        exportAudit: dataroom.export_audit ? dataroom.export_audit.map((item: any) => ({
          actor: item.actor,
          reason: item.reason,
          exportedAt: item.exported_at,
          correlationId: item.correlation_id,
        })) : [],
      }
    : null;

  const statusHistory = liveCase.status_history
    ? liveCase.status_history.map((h: any) => ({
        from: h.from_status || "—",
        to: h.to_status,
        actor: h.actor,
        reason: h.reason,
        at: h.timestamp,
        correlationId: h.correlation_id || "",
      }))
    : [];

  return {
    caseId: liveCase.case_id,
    storeId: liveCase.store_id,
    status: liveCase.status,
    fairPrice,
    reservePrice: report?.reserve_price || 0,
    askingPrice: report?.asking_price || 0,
    sensitivePricePermission: "masked",
    confidence: (report?.confidence || "low") as Confidence,
    liquidityScore: liveCase.valuation_input?.liquidity_discount ? 1 - liveCase.valuation_input.liquidity_discount : 0.9,
    normalizedMargin,
    lenses,
    financeApproval,
    dataRoom,
    statusHistory,
    createdBy: liveCase.created_by,
    modelVersion: report?.model_version || AVM_MODEL_VERSION,
    featureVersion: report?.feature_version || AVM_FEATURE_VERSION,
    policyVersion: report?.policy_version || AVM_POLICY_VERSION,
    predictionOriginTime: liveCase.prediction_origin_time || liveCase.created_at,
    valuedAt: report?.valued_at || liveCase.created_at,
    valuationVersion: String(report?.valuation_version || 1),
    correlationId: report?.correlation_id || "",
  };
}

export function AvmWorkspace({
  view = "overview",
  caseId,
  searchParams = {},
  liveCases,
  isProduction: isProductionProp,
  currentUser,
}: AvmWorkspaceProps) {
  const isProduction = isProductionProp !== undefined ? isProductionProp : (
    liveCases ? liveCases.source === "api" : false
  );

  const [liveCaseDetail, setLiveCaseDetail] = useState<ValuationCase | null>(null);
  const [loading, setLoading] = useState(false);

  const activeCaseId = view === "caseDetail" ? caseId : selectedFromQuery(searchParams?.selected);

  useEffect(() => {
    if (!isProduction || !activeCaseId) {
      setLiveCaseDetail(null);
      return;
    }

    let active = true;
    const fetchDetails = async () => {
      setLoading(true);
      const headers: Record<string, string> = {};
      if (currentUser?.subjectId) headers["x-subject-id"] = currentUser.subjectId;
      if (currentUser?.roles) headers["x-roles"] = currentUser.roles;
      if (currentUser?.tenantId) headers["x-tenant-id"] = currentUser.tenantId;

      try {
        const caseRes = await fetch(`/avm/cases/${activeCaseId}`, { headers });
        if (!caseRes.ok) throw new Error("Failed to fetch case");
        const liveCase = await caseRes.json();

        let report: any = null;
        try {
          const reportRes = await fetch(`/avm/cases/${activeCaseId}/report`, { headers });
          if (reportRes.ok) report = await reportRes.json();
        } catch (e) {
          // ignore
        }

        let dataroom: any = null;
        try {
          const drRes = await fetch(`/avm/cases/${activeCaseId}/dataroom`, { headers });
          if (drRes.ok) dataroom = await drRes.json();
        } catch (e) {
          // ignore
        }

        if (active) {
          setLiveCaseDetail(mapLiveCaseToValuationCase(liveCase, report, dataroom));
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (active) setLoading(false);
      }
    };

    fetchDetails();
    return () => {
      active = false;
    };
  }, [activeCaseId, isProduction, currentUser]);

  if (view === "cases") {
    return (
      <CasesListPage
        searchParams={searchParams}
        liveCases={liveCases}
        isProduction={isProduction}
        drawerCase={liveCaseDetail || undefined}
        currentUser={currentUser}
      />
    );
  }
  if (view === "caseDetail") {
    return (
      <CaseDetailPage
        caseId={caseId}
        isProduction={isProduction}
        liveCaseDetail={liveCaseDetail}
        currentUser={currentUser}
      />
    );
  }
  return <AvmOverview />;
}

// Client-side Offline Indicator
function OfflineIndicator() {
  const [isOffline, setIsOffline] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined") {
      setIsOffline(!window.navigator.onLine);
      const onOnline = () => setIsOffline(false);
      const onOffline = () => setIsOffline(true);
      window.addEventListener("online", onOnline);
      window.addEventListener("offline", onOffline);
      return () => {
        window.removeEventListener("online", onOnline);
        window.removeEventListener("offline", onOffline);
      };
    }
  }, []);

  if (!isOffline) return null;

  return (
    <div
      data-testid="offline-indicator"
      style={{
        padding: "8px 12px",
        backgroundColor: "#fff0f0",
        color: "#d93838",
        border: "1px solid #f8c2c2",
        borderRadius: "4px",
        marginBottom: "12px",
        fontSize: "14px",
        display: "flex",
        alignItems: "center",
        gap: "8px",
      }}
    >
      <span>⚠️</span>
      <span>[OFFLINE] 網路連線已中斷，改用離線模式。</span>
    </div>
  );
}

// Client-side Retry Button
function ClientRetryButton() {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <button
      onClick={() => {
        startTransition(() => {
          router.refresh();
        });
      }}
      disabled={isPending}
      className="retry-button"
      style={{
        marginLeft: "10px",
        padding: "2px 8px",
        fontSize: "12px",
        cursor: "pointer",
        border: "1px solid #ccc",
        borderRadius: "4px",
        background: isPending ? "#eee" : "#fff",
        color: "#333",
      }}
      type="button"
      data-testid="client-retry-button"
    >
      {isPending ? "Loading..." : "重試 (Retry)"}
    </button>
  );
}



function LiveCasesPanel({ binding, isProduction }: { binding: ApiBinding<AvmCase>; isProduction: boolean }) {
  // Stale detection: if loaded at > 5 minutes ago, we flag it as stale warning.
  const [isStale, setIsStale] = useState(false);

  useEffect(() => {
    const checkStale = () => {
      const diffMs = Date.now() - new Date(binding.fetchedAt).getTime();
      if (diffMs > 5 * 60 * 1000) {
        setIsStale(true);
      }
    };
    checkStale();
    const interval = setInterval(checkStale, 30000);
    return () => clearInterval(interval);
  }, [binding.fetchedAt]);

  return (
    <section
      className={styles.reportSection}
      data-testid="avm-live-cases"
      aria-label="API-bound valuation cases"
    >
      <div className={styles.badgeRow}>
        <h2>估值案件（API live）</h2>
        <DataSourceBadge binding={binding} testId="avm-data-source" />
        <ClientRetryButton />
      </div>

      <OfflineIndicator />

      {isStale && (
        <div
          data-testid="stale-warning-banner"
          style={{
            padding: "8px 12px",
            backgroundColor: "#fffdeb",
            color: "#856404",
            border: "1px solid #ffeeba",
            borderRadius: "4px",
            marginBottom: "12px",
            fontSize: "14px",
          }}
        >
          ⚠️ [STALE] 估值案件數據已過期，請點擊重試進行同步。
        </div>
      )}

      <p>
        本區直接讀取後端 <code>GET /avm/cases</code>，證明後端狀態變更會出現在 UI；
        {!isProduction && " 下方固定樣本為 documented non-product fallback。"}
      </p>

      {binding.state === "ready" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="avm-live-cases-table">
            <caption>
              Live valuation cases served by the backend ({binding.items.length})
            </caption>
            <thead>
              <tr>
                <th>case_id</th>
                <th>store_id</th>
                <th>status</th>
                <th>created_by</th>
                <th>created_at</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {binding.items.map((item) => (
                <tr key={item.case_id} data-testid="avm-live-case-row">
                  <td>{item.case_id}</td>
                  <td>{item.store_id}</td>
                  <td>
                    <Badge
                      label={item.status}
                      tone={caseStatusTone(item.status as ValuationCase["status"])}
                      marker="◆"
                    />
                  </td>
                  <td>{item.created_by}</td>
                  <td>{item.created_at}</td>
                  <td>
                    <a
                      href={`/w/dealroom/cases?selected=${item.case_id}&drawer=case`}
                      data-testid={`live-drawer-trigger-${item.case_id}`}
                      style={{
                        marginRight: "8px",
                        textDecoration: "underline",
                        color: "#0066cc",
                      }}
                    >
                      Drawer
                    </a>
                    <Link
                      href={`/w/dealroom/cases/${item.case_id}`}
                      style={{ textDecoration: "underline", color: "#0066cc" }}
                    >
                      開啟詳情
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p data-testid="avm-live-cases-empty" className={styles.riskNotice}>
          {liveCasesFallbackMessage(binding, isProduction)}
        </p>
      )}
    </section>
  );
}

function liveCasesFallbackMessage(binding: ApiBinding<AvmCase>, isProduction: boolean): string {
  if (binding.state === "empty") {
    return isProduction
      ? "後端可連線但尚無估值案件（cold store）。"
      : "後端可連線但尚無估值案件（cold store）；顯示固定樣本作為非產品 fallback。";
  }
  if (binding.state === "error") {
    return isProduction
      ? `後端讀取失敗（${binding.error ?? "unknown"}）。`
      : `後端讀取失敗（${binding.error ?? "unknown"}）；改用固定樣本 fallback。`;
  }
  return isProduction
    ? "未設定 API base URL（ODP_API_BASE_URL）。"
    : "未設定 API base URL（ODP_API_BASE_URL）；以固定樣本渲染。";
}

function canViewSensitivePrice(c: ValuationCase): boolean {
  return c.sensitivePricePermission === "visible";
}

function formatSensitivePrice(
  c: ValuationCase,
  field: "reservePrice" | "askingPrice"
): string {
  return canViewSensitivePrice(c)
    ? c[field].toLocaleString()
    : SENSITIVE_PRICE_MASK;
}

function formatReserveAsking(c: ValuationCase): string {
  return `${formatSensitivePrice(c, "reservePrice")} / ${formatSensitivePrice(c, "askingPrice")}`;
}

function formatReserveAskingLive(status: string): string {
  // Live values default to masked in UI presentation
  return `${SENSITIVE_PRICE_MASK} / ${SENSITIVE_PRICE_MASK}`;
}

function Header({
  title,
  summary,
  caseId,
  currentUser,
}: {
  title: string;
  summary: string;
  caseId?: string;
  currentUser?: { subjectId: string; roles: string };
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
          <ClientCreateCaseButton currentUser={currentUser} />
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
            <p>
              三鏡估值區間、財務核准（含 reserve override）、建立並匯出 DataRoom。
            </p>
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

function CasesListPage({
  searchParams,
  liveCases,
  isProduction,
  drawerCase: liveDrawerCase,
  currentUser,
}: {
  searchParams: SearchParams;
  liveCases?: ApiBinding<AvmCase>;
  isProduction: boolean;
  drawerCase?: ValuationCase;
  currentUser?: { subjectId: string; roles: string };
}) {
  const selected = selectedFromQuery(searchParams.selected);

  let drawerCase: ValuationCase | undefined;
  let hasDrawer = false;

  if (isProduction) {
    drawerCase = liveDrawerCase;
    hasDrawer = !!drawerCase && searchParams.drawer === "case";
  } else {
    const defaultSelected = selected ?? valuationCases[0].caseId;
    drawerCase =
      valuationCases.find((c) => c.caseId === defaultSelected) ??
      valuationCases[0];
    hasDrawer = searchParams.drawer === "case";
  }

  // Determine if we should show the fixture table
  const showFixtureTable = !isProduction;

  return (
    <>
      <Header
        title="估值案件"
        summary="對門市做三鏡估值、財務核准並備妥交易資料室。"
        currentUser={currentUser}
      />
      <main className="odp-content" data-testid="avm-cases-page">
        <WorkspaceNav active="cases" />

        {liveCases && <LiveCasesPanel binding={liveCases} isProduction={isProduction} />}

        {showFixtureTable && (
          <>
            <FilterBar />
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption>
                  估值案件列表（reserve / asking 為敏感欄位，依權限遮罩）
                </caption>
                <thead>
                  <tr>
                    {[
                      "Case",
                      "Status",
                      "Fair (P50)",
                      "Reserve / Asking",
                      "Confidence",
                      "Finance approval",
                      "DataRoom",
                      "Action",
                    ].map((header, index) => (
                      <th
                        aria-sort={index === 0 ? "ascending" : undefined}
                        key={header}
                      >
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {valuationCases.map((c) => (
                    <tr tabIndex={0} key={c.caseId}>
                      <td>
                        <a
                          href={`/w/dealroom/cases?selected=${c.caseId}&drawer=case`}
                          data-testid={`drawer-trigger-${c.caseId}`}
                        >
                          {c.caseId}
                          <br />
                          {c.storeId}
                        </a>
                      </td>
                      <td>
                        <Badge
                          label={c.status}
                          tone={caseStatusTone(c.status)}
                          marker="◆"
                        />
                      </td>
                      <td>{c.fairPrice.p50.toLocaleString()}</td>
                      <td>
                        <span title="敏感欄位，依權限遮罩">
                          {formatReserveAsking(c)}
                        </span>
                      </td>
                      <td>
                        <Badge
                          label={c.confidence}
                          tone={confidenceTone(c.confidence)}
                          marker="▧"
                        />
                      </td>
                      <td>{financeApprovalLabel(c)}</td>
                      <td>{dataRoomLabel(c)}</td>
                      <td>
                        <Link href={`/w/dealroom/cases/${c.caseId}`}>
                          開啟案件詳情
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {drawerCase && hasDrawer && (
          <Drawer
            title={`${drawerCase.caseId} · ${drawerCase.storeId}`}
            testId="avm-case-drawer"
            caseId={drawerCase.caseId}
          >
            <div className={styles.cardStack}>
              <div className={styles.metricRow}>
                <Metric label="Status" value={drawerCase.status} />
                <Metric
                  label="Fair P50"
                  value={drawerCase.fairPrice.p50.toLocaleString()}
                />
                <Metric label="Confidence" value={drawerCase.confidence} />
              </div>
              <p>
                Reserve / Asking（敏感，依權限遮罩）：
                {formatReserveAsking(drawerCase)}
              </p>
              <p>
                Finance approval：{financeApprovalLabel(drawerCase)} · DataRoom：
                {dataRoomLabel(drawerCase)}
              </p>
              <p className={styles.auditLine}>
                correlation_id {drawerCase.correlationId}
              </p>
              <Link
                className={styles.primaryButton}
                href={`/w/dealroom/cases/${drawerCase.caseId}`}
              >
                開啟案件詳情
              </Link>
            </div>
          </Drawer>
        )}
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

function CaseDetailPage({
  caseId,
  isProduction,
  liveCaseDetail,
  currentUser,
}: {
  caseId?: string;
  isProduction: boolean;
  liveCaseDetail?: ValuationCase | null;
  currentUser?: { subjectId: string; roles: string };
}) {
  const c =
    isProduction && liveCaseDetail
      ? liveCaseDetail
      : (valuationCases.find((item) => item.caseId === caseId) ?? valuationCases[0]);
  return (
    <>
      <Header
        title={`${c.caseId} · ${c.storeId}`}
        summary={`目前狀態 ${c.status}，fair P50 ${c.fairPrice.p50.toLocaleString()}，confidence ${c.confidence}。系統估值與人工核准分離呈現。`}
        caseId={c.caseId}
        currentUser={currentUser}
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
            <ApprovalPanel caseData={c} currentUser={currentUser} />
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
        財務核准：{financeApprovalLabel(c)} · DataRoom：{dataRoomLabel(c)} ·
        liquidityScore {c.liquidityScore.toFixed(2)}
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
        <Badge
          label={`confidence ${c.confidence}`}
          tone={confidenceTone(c.confidence)}
          marker="▧"
        />
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
    <section
      className={styles.reportSection}
      id="normalized"
      data-testid="avm-normalized-margin"
    >
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
    <section
      className={styles.reportSection}
      id="valuation"
      data-testid="valuation-range-chart"
    >
      <h2>Three-Lens Valuation</h2>
      <p>
        系統估值（model {c.modelVersion}），永不只顯示 P50。reserve{" "}
        {formatSensitivePrice(c, "reservePrice")}
        （P10·0.97）／asking {formatSensitivePrice(c, "askingPrice")}
        （P90·1.05）為敏感欄位，依權限遮罩。
      </p>
      {!canViewSensitivePrice(c) ? (
        <p
          className={styles.riskNotice}
          data-testid="avm-sensitive-price-mask-notice"
        >
          reserve/asking price 欄位目前為 MASKED_BY_PERMISSION；range chart
          不渲染 reserve/asking marker。
        </p>
      ) : null}
      <div
        className={styles.rangeChart}
        role="img"
        aria-label="估值三鏡 P10/P50/P90 區間比較"
      >
        {c.lenses.map((lens) => (
          <div className={styles.rangeRow} key={lens.lens}>
            <span className={styles.rangeLabel}>{lens.lens}</span>
            <div
              className={styles.rangeTrack}
              title={`${lens.p10} / ${lens.p50} / ${lens.p90}`}
            >
              <span
                className={styles.rangeBand}
                style={{ left: `${pct(lens.p10)}%`, right: `${100 - pct(lens.p90)}%` }}
              />
              <span className={styles.rangeMid} style={{ left: `${pct(lens.p50)}%` }} />
              {canViewSensitivePrice(c) ? (
                <>
                  <span
                    className={styles.rangeReserve}
                    data-testid="avm-reserve-marker"
                    style={{ left: `${pct(c.reservePrice)}%` }}
                    aria-hidden="true"
                  />
                  <span
                    className={styles.rangeAsking}
                    data-testid="avm-asking-marker"
                    style={{ left: `${pct(c.askingPrice)}%` }}
                    aria-hidden="true"
                  />
                </>
              ) : null}
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
  const evidenceArray = Array.isArray(lens.evidence)
    ? lens.evidence
    : typeof lens.evidence === "object" && lens.evidence !== null
    ? Object.entries(lens.evidence).map(([key, val]) => {
        if (Array.isArray(val)) return `${key}: ${val.join(", ")}`;
        if (typeof val === "object" && val !== null) return `${key}: ${JSON.stringify(val)}`;
        return `${key}: ${val}`;
      })
    : [];

  return (
    <details className={styles.softBlock}>
      <summary>
        {lens.lens} · method {lens.method}
      </summary>
      <ul>
        {evidenceArray.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </details>
  );
}

function ApprovalPanel({
  caseData: c,
  currentUser,
}: {
  caseData: ValuationCase;
  currentUser?: { subjectId: string; roles: string };
}) {
  const canApprove = c.status === "REVIEW_REQUIRED";
  const approved = c.financeApproval;

  return (
    <section
      className={styles.approvalPanel}
      id="approval"
      data-testid="avm-approval-panel"
    >
      <h2>Approval (Finance)</h2>
      <p>
        系統 fair/reserve/asking 由 AVM 模型產生（{c.modelVersion}
        ），人工核准獨立記錄、never optimistic。建立者不得核准自己的案件（segregation）。
      </p>
      {c.confidence === "low" && (
        <p className={styles.riskNotice}>
          confidence=low：核准鈕仍可用，但須於 reason 說明風險。
        </p>
      )}

      {approved ? (
        <p className={styles.auditLine}>
          success：decision_id {approved.decisionId} · actor {approved.actorId} ·{" "}
          {approved.approvedAt} · policy {approved.policyVersion} · correlation_id{" "}
          {approved.correlationId}
        </p>
      ) : (
        <ClientApprovalForm
          caseId={c.caseId}
          canApprove={canApprove}
          formattedReservePrice={formatSensitivePrice(c, "reservePrice")}
          currentUser={currentUser}
        />
      )}
      <p className={styles.auditLine} style={{ marginTop: "12px" }}>policy {AVM_POLICY_VERSION}</p>
    </section>
  );
}

function DataRoomSection({ caseData: c }: { caseData: ValuationCase }) {
  const dr = c.dataRoom;
  return (
    <section className={styles.reportSection} id="dataroom" data-testid="avm-dataroom">
      <h2>DataRoom &amp; Export</h2>
      {!c.financeApproval ? (
        <p className={styles.riskNotice}>
          未財務核准（REVIEW_REQUIRED 前）不得建立 DataRoom 或匯出。
        </p>
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
            <p>
              匯出為高風險審計動作：必填 reason、寫後端
              Audit（avm.dataroom_exported.v1）、追加 export_audit。
            </p>
            <ul>
              {dr.exportAudit.map((e) => (
                <li key={e.correlationId}>
                  {e.exportedAt} · {e.actor} · {e.reason} · correlation_id{" "}
                  {e.correlationId}
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

// Client-side Interactive Drawer with Accessibility support
function Drawer({
  title,
  children,
  testId,
  caseId,
}: {
  title: string;
  children: React.ReactNode;
  testId: string;
  caseId: string;
}) {
  const router = useRouter();
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // Escape key close handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [caseId]);

  // Autofocus close button on mount
  useEffect(() => {
    let attempts = 0;
    let timer: NodeJS.Timeout;
    const tryFocus = () => {
      const el = closeBtnRef.current;
      if (el) {
        el.focus();
        if (document.activeElement === el || attempts >= 10) {
          return;
        }
      }
      attempts++;
      timer = setTimeout(tryFocus, 50);
    };
    timer = setTimeout(tryFocus, 50);
    return () => clearTimeout(timer);
  }, [caseId]);

  const handleClose = () => {
    router.push("/w/dealroom/cases");
    setTimeout(() => {
      // Deterministic Focus Return
      const trigger =
        document.querySelector<HTMLElement>(`[data-testid="live-drawer-trigger-${caseId}"]`) ||
        document.querySelector<HTMLElement>(`[data-testid="drawer-trigger-${caseId}"]`);
      trigger?.focus();
    }, 100);
  };

  return (
    <aside className={styles.drawer} aria-label={title} data-testid={testId}>
      <div className={styles.drawerHeader}>
        <h2>{title}</h2>
        <button
          ref={closeBtnRef}
          onClick={handleClose}
          id="drawer-close-btn"
          aria-label={`Close ${title}`}
          type="button"
          style={{
            background: "none",
            border: "1px solid #ccc",
            borderRadius: "4px",
            padding: "2px 8px",
            cursor: "pointer",
            fontSize: "13px",
          }}
        >
          Esc
        </button>
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

function SplitList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone?: "warning";
}) {
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
