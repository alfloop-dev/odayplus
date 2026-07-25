import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { StatusTone } from "@oday-plus/domain-types";
import type {
  ForecastAlert,
  ForecastOutputSummary,
} from "@oday-plus/openapi-client";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { DataSourceBadge } from "../../src/components/DataSourceBadge.tsx";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "./ProductionDataState.tsx";
import {
  alerts,
  formatMoney,
  formatPercent,
  jobStatus,
  selectedStore,
  stores,
  type AlertLevel,
  type OperationsView,
  type StoreForecast,
} from "./data.ts";
import styles from "./operations.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type OperationsWorkspaceProps = {
  view?: OperationsView;
  storeId?: string;
  searchParams?: SearchParams;
  /** Live `GET /forecastops/alerts` binding; supplied by the server route. */
  liveAlerts?: ApiBinding<ForecastAlert>;
  /** Live `GET /forecastops/forecasts` binding; supplied by the server route. */
  liveForecasts?: ApiBinding<ForecastOutputSummary>;
  isProduction?: boolean;
};

const navItems = [
  { key: "forecast", label: "Forecast 總覽", href: "/w/operations/forecast" },
  { key: "alerts", label: "四燈預警佇列", href: "/w/operations/alerts" },
];

const lightTone: Record<AlertLevel, StatusTone> = {
  green: "green",
  yellow: "yellow",
  orange: "orange",
  red: "red",
};

const lightMarker: Record<AlertLevel, string> = {
  green: "●",
  yellow: "▲",
  orange: "◆",
  red: "■",
};

const thresholdCopy: Record<AlertLevel, string> = {
  red: "gap <= -35%; immediate intervention handoff",
  orange: "-35% < gap <= -20%; promotion handoff eligible",
  yellow: "-20% < gap <= -10%; alert-only",
  green: "gap > -10%; within expected band",
};

export function OperationsWorkspace({
  view = "overview",
  storeId,
  searchParams = {},
  liveAlerts,
  liveForecasts,
  isProduction: isProductionProp,
}: OperationsWorkspaceProps) {
  const isProduction = resolveProductionMode(isProductionProp);
  if (isProduction) {
    return (
      <ProductionOperationsWorkspace
        alertBinding={liveAlerts}
        forecastBinding={liveForecasts}
        storeId={storeId}
        view={view}
      />
    );
  }
  if (view === "forecast") return <ForecastPage searchParams={searchParams} />;
  if (view === "alerts") return <AlertsPage searchParams={searchParams} liveAlerts={liveAlerts} />;
  if (view === "storeDetail") return <StoreDetailPage store={selectedStore(storeId)} />;
  return <OperationsOverview liveAlerts={liveAlerts} />;
}

function ProductionOperationsWorkspace({
  alertBinding,
  forecastBinding,
  storeId,
  view,
}: {
  alertBinding?: ApiBinding<ForecastAlert>;
  forecastBinding?: ApiBinding<ForecastOutputSummary>;
  storeId?: string;
  view: OperationsView;
}) {
  const usesForecasts = view === "forecast" || view === "storeDetail";
  const binding = usesForecasts ? forecastBinding : alertBinding;
  const resource = usesForecasts ? "ForecastOps forecasts" : "ForecastOps alerts";
  const state = productionBindingState(binding);

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: "營運 Operations", href: "/operations" }, { label: view }]}
        lastUpdated={binding?.fetchedAt ? `API checked ${binding.fetchedAt}` : "Live source not available"}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        summary="ForecastOps production workspace. Only persisted API data is rendered."
        title={view === "alerts" ? "四燈預警佇列" : view === "forecast" ? "Forecast 總覽" : "營運 Operations"}
      />
      <main className="odp-content" data-testid={`ops-${view}-production-page`}>
        <WorkspaceNav active={view} />
        {usesForecasts ? (
          <ProductionDataState
            binding={forecastBinding}
            resource={resource}
            testId="ops-production-data-state"
          >
            {forecastBinding ? (
              <LiveForecastTable
                binding={forecastBinding}
                selectedStoreId={view === "storeDetail" ? storeId : undefined}
              />
            ) : null}
          </ProductionDataState>
        ) : (
          <ProductionDataState
            binding={alertBinding}
            resource={resource}
            testId="ops-production-data-state"
          >
            {alertBinding ? <LiveAlertQueue binding={alertBinding} productionMode /> : null}
          </ProductionDataState>
        )}
      </main>
    </>
  );
}

function LiveForecastTable({
  binding,
  selectedStoreId,
}: {
  binding: ApiBinding<ForecastOutputSummary>;
  selectedStoreId?: string;
}) {
  const rows = selectedStoreId
    ? binding.items.filter((item) => item.store_id === selectedStoreId)
    : binding.items;

  return (
    <section
      className={styles.panel}
      data-testid="ops-live-forecasts"
      aria-label="API-bound ForecastOps outputs"
    >
      <div className={styles.badgeRow}>
        <h2>ForecastOps forecasts（API live）</h2>
        <ProductionDataBadge binding={binding} testId="ops-forecast-data-source" />
      </div>
      {selectedStoreId && rows.length === 0 ? (
        <p data-testid="ops-live-forecast-not-found">
          API 回傳資料中沒有 {selectedStoreId}；未以固定預測替代。
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="ops-live-forecasts-table">
            <caption>Persisted forecasts served by GET /forecastops/forecasts.</caption>
            <thead>
              <tr>
                <th scope="col">Store</th>
                <th scope="col">P10 / P50 / P90</th>
                <th scope="col">Trajectory</th>
                <th scope="col">Gap</th>
                <th scope="col">Model</th>
                <th scope="col">Snapshot lineage</th>
                <th scope="col">Scored at</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((forecast) => (
                <tr key={forecast.forecast_output_id} data-testid="ops-live-forecast-row">
                  <td>
                    <Link href={`/w/operations/forecast/${encodeURIComponent(forecast.store_id)}`}>
                      {forecast.store_id}
                    </Link>
                    <span className={styles.subtle}>
                      v{forecast.forecast_version} · {forecast.prediction_run_id}
                    </span>
                  </td>
                  <td>
                    {formatMoney(forecast.p10)} / {formatMoney(forecast.p50)} /{" "}
                    {formatMoney(forecast.p90)}
                  </td>
                  <td>
                    {forecast.trajectory_class}
                    <span className={styles.subtle}>
                      turning {formatPercent(forecast.turning_point_probability)}
                    </span>
                  </td>
                  <td>{formatPercent(forecast.sitescore_gap_ratio)}</td>
                  <td>
                    {forecast.model_name} · {forecast.model_version}
                    <span className={styles.subtle}>
                      {forecast.engine_name} · {forecast.feature_version}
                    </span>
                  </td>
                  <td className={styles.mono}>{forecast.source_snapshot_ids.join(", ")}</td>
                  <td>{forecast.scored_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function OperationsOverview({ liveAlerts }: { liveAlerts?: ApiBinding<ForecastAlert> }) {
  const redOpen = alerts.filter((alert) => alert.light === "red" && alert.status === "open").length;
  const orangeOpen = alerts.filter((alert) => alert.light === "orange" && alert.status === "open").length;

  return (
    <>
      <Header
        title="營運 Operations"
        summary="ForecastOps 預測、四燈預警、根因證據與干預交接工作台。"
        statusLabel={`${jobStatus.status} · ${jobStatus.dataStatus}`}
      />
      <main className="odp-content" data-testid="ops-overview-page">
        <WorkspaceNav active="overview" />
        <section className={styles.flowGrid} aria-label="Operations workflow">
          <Link className={styles.flowCard} href="/w/operations/forecast">
            <h2>Forecast 總覽</h2>
            <p>掃描各店 w4/w8/w12/w24 預測帶、四燈分佈與模型新鮮度。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/operations/alerts">
            <h2>四燈預警佇列</h2>
            <p>分流 RED/ORANGE/YELLOW open alerts，確認交接狀態。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/operations/forecast/store-001">
            <h2>單店詳情</h2>
            <p>看預測帶、Root-cause Evidence、Recommendation、Handoff 與 Audit。</p>
          </Link>
        </section>
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Open alert mix</h2>
            <div className={styles.metricRow}>
              <Metric label="RED open" value={redOpen} />
              <Metric label="ORANGE open" value={orangeOpen} />
              <Metric label="Policy" value="four-light-policy-v1" />
            </div>
          </div>
          <DecisionSeparation />
        </section>
        {liveAlerts ? <LiveAlertQueue binding={liveAlerts} /> : null}
      </main>
    </>
  );
}

function ForecastPage({ searchParams }: { searchParams: SearchParams }) {
  const selected = selectedStore(selectedFromQuery(searchParams.selected));

  return (
    <>
      <Header
        title="Forecast 總覽"
        summary="依四燈等級與軌跡分類掃描各店營收預測與模型新鮮度。"
        statusLabel={`${jobStatus.status} · ${jobStatus.dataStatus}`}
      />
      <main className="odp-content" data-testid="ops-forecast-page">
        <WorkspaceNav active="forecast" />
        <FilterBar />
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Store forecasts</h2>
            <ForecastTable />
          </div>
          <Drawer title={`${selected.storeId} · ${selected.storeName}`} testId="forecast-row-drawer">
            <ForecastSummary store={selected} />
            <ForecastBandChart store={selected} compact />
            <div className={styles.actionRow}>
              <Link className={styles.primaryButton} href={`/w/operations/forecast/${selected.storeId}`}>
                開啟單店詳情
              </Link>
              {selected.alertId ? (
                <Link className={styles.secondaryButton} href={`/w/operations/alerts?selected=${selected.alertId}`}>
                  查看預警
                </Link>
              ) : null}
            </div>
          </Drawer>
        </section>
      </main>
    </>
  );
}

function AlertsPage({
  searchParams,
  liveAlerts,
}: {
  searchParams: SearchParams;
  liveAlerts?: ApiBinding<ForecastAlert>;
}) {
  const selectedAlertId = selectedFromQuery(searchParams.selected) ?? alerts[0].alertId;
  const selectedAlert = alerts.find((alert) => alert.alertId === selectedAlertId) ?? alerts[0];
  const store = selectedStore(selectedAlert.storeId);

  return (
    <>
      <Header
        title="四燈預警佇列"
        summary="分流 RED/ORANGE/YELLOW 營運預警、確認狀態並交接干預。"
        statusLabel={`RED ${openCount("red")} · ORANGE ${openCount("orange")} · YELLOW ${openCount("yellow")}`}
      />
      <main className="odp-content" data-testid="ops-alerts-page">
        <WorkspaceNav active="alerts" />
        <FilterBar />
        {liveAlerts ? <LiveAlertQueue binding={liveAlerts} /> : null}
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Alert center</h2>
            <AlertsTable />
          </div>
          <Drawer title={`${selectedAlert.alertId} · ${selectedAlert.storeId}`} testId="alert-drawer">
            <FourLightBadge level={selectedAlert.light} gap={store.gapRatio} />
            <p>{selectedAlert.evidenceSummary}</p>
            <dl className={styles.auditGrid}>
              <dt>Status</dt>
              <dd>{selectedAlert.status}</dd>
              <dt>Handoff</dt>
              <dd>
                {selectedAlert.handoffId
                  ? `${selectedAlert.handoffId} · ${selectedAlert.interventionType} · ${selectedAlert.eligibilityStatus}`
                  : "No handoff; alert-only"}
              </dd>
              <dt>correlation_id</dt>
              <dd className={styles.mono}>{selectedAlert.correlationId}</dd>
            </dl>
            <div className={styles.actionRow}>
              <button className={styles.secondaryButton} type="button">
                確認預警
              </button>
              {selectedAlert.light === "red" || selectedAlert.light === "orange" ? (
                <Link className={styles.primaryButton} href={`/w/operations/forecast/${selectedAlert.storeId}#handoff`}>
                  建立干預
                </Link>
              ) : (
                <button className={styles.secondaryButton} type="button">
                  建立資料查核任務
                </button>
              )}
            </div>
            <p className={styles.subtle}>High-risk actions are not optimistic; list updates wait for backend success.</p>
          </Drawer>
        </section>
      </main>
    </>
  );
}

function StoreDetailPage({ store }: { store: StoreForecast }) {
  return (
    <>
      <Header
        title={`${store.storeId} · ${store.storeName}`}
        summary="單店預測 / 預警詳情：預測帶、根因證據、推薦、交接與版本稽核。"
        statusLabel={`${store.freshness} · forecast v${store.forecastVersion}`}
        storeId={store.storeId}
      />
      <main className="odp-content" data-testid="ops-store-detail-page">
        <WorkspaceNav active="forecast" />
        <nav className={styles.anchorTabs} aria-label="Store detail sections">
          {["summary", "status", "forecast", "root-cause", "recommendation", "handoff", "audit"].map((id) => (
            <a href={`#${id}`} key={id}>
              {id}
            </a>
          ))}
        </nav>
        <section className={styles.summaryBand} id="summary" data-testid="store-summary">
          <div className={styles.metricRow}>
            <Metric label="Store" value={store.storeId} />
            <Metric label="Gap vs baseline" value={formatPercent(store.gapRatio)} />
            <Metric label="Trajectory" value={store.trajectory} />
            <Metric label="Turning point" value={formatPercent(store.turningPointProbability)} />
          </div>
          <FourLightBadge level={store.light} gap={store.gapRatio} />
        </section>
        <section className={styles.detailGrid}>
          <div className={styles.chartPanel} id="forecast" data-testid="forecast-band-chart">
            <h2>Forecast</h2>
            <p>
              Actual revenue, forecast P50, P10-P90 band, and SiteScore baseline P50 for w4/w8/w12/w24.
            </p>
            <ForecastBandChart store={store} />
          </div>
          <aside className={styles.stickyPanel}>
            <StatusPanel store={store} />
            <HandoffPanel store={store} />
          </aside>
        </section>
        <section className={styles.detailGrid}>
          <RootCauseEvidenceCard store={store} />
          <div className={styles.stickyPanel}>
            <RecommendationPanel store={store} />
            <AuditMetadata store={store} />
          </div>
        </section>
      </main>
    </>
  );
}

function Header({
  title,
  summary,
  statusLabel,
  storeId,
}: {
  title: string;
  summary: string;
  statusLabel: string;
  storeId?: string;
}) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      breadcrumb={[
        { label: "營運 Operations", href: "/operations" },
        ...(storeId ? [{ label: "Forecast 總覽", href: "/w/operations/forecast" }] : []),
        { label: storeId ?? title },
      ]}
      status={{
        label: statusLabel,
        tone: statusLabel.includes("FAILED") ? "red" : statusLabel.includes("STALE") ? "yellow" : "green",
        marker: "◆",
        "data-testid": "operations-data-status",
      }}
      lastUpdated={`${jobStatus.updatedAt} · source ${jobStatus.sourceSnapshot}`}
      actions={
        <div className={styles.headerActions}>
          <button className={styles.secondaryButton} type="button">
            Saved view
          </button>
          <button className={styles.secondaryButton} type="button">
            Export visible rows
          </button>
          <button className={styles.primaryButton} type="button">
            重新計算預測
          </button>
        </div>
      }
    />
  );
}

function WorkspaceNav({ active }: { active: OperationsView }) {
  return (
    <nav className={styles.workspaceNav} aria-label="Operations module navigation">
      <Link aria-current={active === "overview" ? "page" : undefined} href="/operations">
        Overview
      </Link>
      {navItems.map((item) => (
        <Link
          aria-current={active === item.key ? "page" : undefined}
          data-testid={`ops-nav-${item.key}`}
          href={item.href}
          key={item.key}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

function FilterBar() {
  return (
    <form className={styles.filterBar} aria-label="URL synced Operations filters">
      <label>
        level
        <select name="level" defaultValue="all">
          <option value="all">all</option>
          <option value="red">red</option>
          <option value="orange">orange</option>
          <option value="yellow">yellow</option>
          <option value="green">green</option>
        </select>
      </label>
      <label>
        trajectory_class
        <select name="trajectory_class" defaultValue="all">
          <option value="all">all</option>
          <option value="declining">declining</option>
          <option value="plateau">plateau</option>
          <option value="ramping">ramping</option>
          <option value="growing">growing</option>
        </select>
      </label>
      <label>
        district
        <input name="district" defaultValue="all" />
      </label>
      <label>
        modelVersion
        <input name="modelVersion" defaultValue="forecastops-r3-20260627" />
      </label>
      <label>
        snapshot
        <input name="snapshot" defaultValue={jobStatus.sourceSnapshot} />
      </label>
      <Link className={styles.secondaryButton} href="?level=orange&trajectory_class=plateau&selected=store-002">
        Saved view
      </Link>
    </form>
  );
}

function ForecastTable() {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col">Store</th>
            <th scope="col">Light</th>
            <th scope="col">Trajectory</th>
            <th scope="col">Forecast w4</th>
            <th scope="col">Gap vs baseline</th>
            <th scope="col">Turning point</th>
            <th scope="col">Freshness</th>
            <th scope="col">Model</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {stores.map((store) => (
            <tr key={store.storeId} tabIndex={0}>
              <td>
                <Link className={styles.storeLink} href={`/w/operations/forecast?selected=${store.storeId}`}>
                  {store.storeId}
                </Link>
                <span className={styles.subtle}>{store.storeName}</span>
              </td>
              <td>
                <FourLightBadge level={store.light} gap={store.gapRatio} />
              </td>
              <td>{store.trajectory}</td>
              <td>
                {formatMoney(store.bands[0].p50)}
                <span className={styles.subtle}>
                  P10-P90 {formatMoney(store.bands[0].p10)} - {formatMoney(store.bands[0].p90)}
                </span>
              </td>
              <td>
                {formatPercent(store.gapRatio)}
                <span className={styles.subtle}>baseline {formatMoney(store.baselineP50)}</span>
              </td>
              <td>{formatPercent(store.turningPointProbability)}</td>
              <td>
                {store.freshness}
                <span className={styles.subtle}>{store.scoredAt}</span>
              </td>
              <td>
                {store.modelVersion}
                <span className={styles.subtle}>{store.policyVersion}</span>
              </td>
              <td>
                <Link className={styles.secondaryButton} href={`/w/operations/forecast/${store.storeId}`}>
                  詳情
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AlertsTable() {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col">Alert</th>
            <th scope="col">Light</th>
            <th scope="col">Evidence summary</th>
            <th scope="col">Opened</th>
            <th scope="col">Status</th>
            <th scope="col">Handoff</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <tr key={alert.alertId} tabIndex={0}>
              <td>
                <Link className={styles.storeLink} href={`/w/operations/alerts?selected=${alert.alertId}`}>
                  {alert.alertId}
                </Link>
                <span className={styles.subtle}>{alert.storeId}</span>
              </td>
              <td>
                <FourLightBadge level={alert.light} gap={selectedStore(alert.storeId).gapRatio} />
                <span className={styles.subtle}>{alert.reasonCode}</span>
              </td>
              <td>{alert.evidenceSummary}</td>
              <td>
                {alert.openedAt}
                <span className={styles.subtle}>{alert.waitTime}</span>
              </td>
              <td>{alert.status}</td>
              <td>
                {alert.handoffId ? (
                  <>
                    {alert.interventionType}
                    <span className={styles.subtle}>
                      {alert.eligibilityStatus} · {alert.handoffId}
                    </span>
                  </>
                ) : (
                  "alert-only"
                )}
              </td>
              <td>
                <Link className={styles.secondaryButton} href={`/w/operations/forecast/${alert.storeId}`}>
                  單店詳情
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LiveAlertQueue({
  binding,
  productionMode = false,
}: {
  binding: ApiBinding<ForecastAlert>;
  productionMode?: boolean;
}) {
  return (
    <section className={styles.panel} data-testid="ops-live-alerts" aria-label="API-bound four-light alerts">
      <div className={styles.badgeRow}>
        <h2>四燈預警（API live）</h2>
        {productionMode ? (
          <ProductionDataBadge binding={binding} testId="ops-alert-data-source" />
        ) : (
          <DataSourceBadge binding={binding} testId="ops-alert-data-source" />
        )}
      </div>
      <p>
        本區直接讀取 <code>GET /forecastops/alerts</code> 的持久化狀態（含 acknowledged 確認軌跡）。
        {!productionMode ? " 下方固定佇列為 documented non-product fixture。" : null}
      </p>
      {binding.state === "ready" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="ops-live-alerts-table">
            <caption className={styles.subtle}>
              Live alerts served by the backend ({binding.items.length})
            </caption>
            <thead>
              <tr>
                <th scope="col">alert_id</th>
                <th scope="col">store</th>
                <th scope="col">light</th>
                <th scope="col">status</th>
                <th scope="col">acknowledged_by</th>
              </tr>
            </thead>
            <tbody>
              {binding.items.map((alert) => (
                <tr key={alert.alert_id} data-testid="ops-live-alert-row">
                  <td className={styles.mono}>{alert.alert_id}</td>
                  <td>{stringField(alert.store_id)}</td>
                  <td>
                    <Badge
                      label={String(alert.alert_level ?? "—").toUpperCase()}
                      marker={lightMarker[normalizeLevel(alert.alert_level)]}
                      tone={lightTone[normalizeLevel(alert.alert_level)]}
                    />
                  </td>
                  <td data-testid="ops-live-alert-status">{stringField(alert.status)}</td>
                  <td>{stringField(alert.acknowledged_by) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p data-testid="ops-live-alerts-empty" className={styles.subtle}>
          {liveAlertsFallbackMessage(binding)}
        </p>
      )}
    </section>
  );
}

function liveAlertsFallbackMessage(binding: ApiBinding<ForecastAlert>): string {
  if (binding.state === "empty") {
    return "後端可連線但尚無 alert（cold store）；顯示固定佇列作為非產品 fallback。";
  }
  if (binding.state === "error") {
    return `後端讀取失敗（${binding.error ?? "unknown"}）；改用固定佇列 fallback。`;
  }
  return "未設定 API base URL（ODP_API_BASE_URL）；以固定佇列渲染。";
}

function normalizeLevel(value: unknown): AlertLevel {
  if (value === "red" || value === "orange" || value === "yellow" || value === "green") {
    return value;
  }
  return "green";
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function FourLightBadge({ level, gap }: { level: AlertLevel; gap: number }) {
  return (
    <span title={`${thresholdCopy[level]} · policy four-light-policy-v1`}>
      <Badge
        className={styles.lightBadge}
        label={`${level.toUpperCase()} · gap ${formatPercent(gap)}`}
        marker={lightMarker[level]}
        tone={lightTone[level]}
        data-testid={`four-light-${level}`}
      />
    </span>
  );
}

function ForecastBandChart({ store, compact = false }: { store: StoreForecast; compact?: boolean }) {
  const max = Math.max(store.baselineP50, ...store.bands.flatMap((band) => [band.p90, band.actual]));
  return (
    <div className={styles.barChart}>
      {store.bands.map((band) => {
        const rangeLeft = Math.max(2, (band.p10 / max) * 100);
        const rangeWidth = Math.max(6, ((band.p90 - band.p10) / max) * 100);
        const p50 = (band.p50 / max) * 100;
        const actual = (band.actual / max) * 100;
        const baseline = (store.baselineP50 / max) * 100;
        return (
          <div className={styles.barRow} key={band.horizon}>
            <span>{band.horizon}</span>
            <div className={styles.bandTrack} aria-label={`${band.horizon} forecast band`}>
              <span className={styles.bandRange} style={{ left: `${rangeLeft}%`, width: `${rangeWidth}%` }} />
              <span className={styles.bandP50} style={{ left: `${p50}%` }} title="forecast P50" />
              <span className={styles.actualMarker} style={{ left: `${actual}%` }} title="actual_revenue" />
              <span className={styles.baselineMarker} style={{ left: `${baseline}%` }} title="sitescore_baseline_p50" />
            </div>
            <strong>
              P50 {formatMoney(band.p50)}
              {!compact ? (
                <span className={styles.subtle}>
                  P10 {formatMoney(band.p10)} · P90 {formatMoney(band.p90)}
                </span>
              ) : null}
            </strong>
          </div>
        );
      })}
      <ForecastBandTable store={store} />
    </div>
  );
}

function ForecastBandTable({ store }: { store: StoreForecast }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.subtle}>ForecastBandChart data export preview</caption>
        <thead>
          <tr>
            <th>Horizon</th>
            <th>Actual</th>
            <th>P10</th>
            <th>P50</th>
            <th>P90</th>
            <th>SiteScore baseline P50</th>
          </tr>
        </thead>
        <tbody>
          {store.bands.map((band) => (
            <tr key={band.horizon}>
              <td>{band.horizon}</td>
              <td>{formatMoney(band.actual)}</td>
              <td>{formatMoney(band.p10)}</td>
              <td>{formatMoney(band.p50)}</td>
              <td>{formatMoney(band.p90)}</td>
              <td>{formatMoney(store.baselineP50)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RootCauseEvidenceCard({ store }: { store: StoreForecast }) {
  return (
    <section className={styles.panel} id="root-cause" data-testid="root-cause-evidence-card">
      <h2>Root-cause Evidence</h2>
      {store.qualityWarning ? <div className={styles.warningBlock}>{store.qualityWarning}</div> : null}
      <div className={styles.metricRow}>
        <Metric label="actual_revenue" value={formatMoney(store.actualRevenue)} />
        <Metric label="forecast_p50 w4" value={formatMoney(store.bands[0].p50)} />
        <Metric label="gap" value={formatPercent(store.gapRatio)} />
        <Metric label="trajectory" value={store.trajectory} />
      </div>
      <div className={styles.evidenceGrid}>
        <EvidenceList title="Positive signals" items={store.positiveSignals} />
        <EvidenceList title="Negative signals" items={store.negativeSignals} />
      </div>
      <EvidenceList title="recommended_actions" items={store.recommendedActions} />
    </section>
  );
}

function RecommendationPanel({ store }: { store: StoreForecast }) {
  return (
    <section className={styles.panel} id="recommendation" data-testid="recommendation-panel">
      <h2>Recommendation</h2>
      <FourLightBadge level={store.light} gap={store.gapRatio} />
      <p>
        由系統依 <span className={styles.mono}>{store.policyVersion}</span> 產生；model {store.modelVersion},
        feature {store.featureVersion}, origin {store.predictionOriginTime}. Requires ops_manager approval.
      </p>
    </section>
  );
}

function HandoffPanel({ store }: { store: StoreForecast }) {
  return (
    <section className={styles.panel} id="handoff" data-testid="handoff-panel">
      <h2>Handoff / Execution</h2>
      {store.handoffId ? (
        <>
          <Badge
            label={`${store.interventionType} · ${store.eligibilityStatus}`}
            marker="↗"
            tone={store.eligibilityStatus === "manual_review" ? "red" : "orange"}
          />
          <dl className={styles.auditGrid}>
            <dt>handoff_id</dt>
            <dd className={styles.mono}>{store.handoffId}</dd>
            <dt>action_set_json</dt>
            <dd>{store.recommendedActions.join(", ")}</dd>
          </dl>
          <Link className={styles.primaryButton} href={`/w/operations/interventions/${store.handoffId}`}>
            開啟 InterventionOps
          </Link>
        </>
      ) : store.light === "red" || store.light === "orange" ? (
        <button className={styles.primaryButton} type="button">
          建立干預
        </button>
      ) : (
        <p>Alert-only: this light level can be acknowledged or sent to data quality review.</p>
      )}
      <p className={styles.subtle}>Success must return handoff_id before this panel changes state.</p>
    </section>
  );
}

function StatusPanel({ store }: { store: StoreForecast }) {
  return (
    <section className={styles.panel} id="status" data-testid="status-panel">
      <h2>Status</h2>
      <div className={styles.badgeRow}>
        <Badge label="forecast job SUCCEEDED" marker="◆" tone="green" />
        <Badge label={store.freshness} marker="●" tone={store.freshness === "STALE" ? "yellow" : "green"} />
      </div>
      <p>
        SLA checked at {store.scoredAt}; model {store.modelVersion}; policy {store.policyVersion}.
      </p>
    </section>
  );
}

function AuditMetadata({ store }: { store: StoreForecast }) {
  return (
    <section className={styles.panel} id="audit" data-testid="audit-metadata">
      <h2>Version / Audit</h2>
      <dl className={styles.auditGrid}>
        <dt>prediction_run_id</dt>
        <dd className={styles.mono}>{store.predictionRunId}</dd>
        <dt>model_version</dt>
        <dd>{store.modelVersion}</dd>
        <dt>feature_version</dt>
        <dd>{store.featureVersion}</dd>
        <dt>policy_version</dt>
        <dd>{store.policyVersion}</dd>
        <dt>prediction_origin_time</dt>
        <dd>{store.predictionOriginTime}</dd>
        <dt>scored_at</dt>
        <dd>{store.scoredAt}</dd>
        <dt>source_snapshot_ids</dt>
        <dd>{store.sourceSnapshotIds.join(", ")}</dd>
        <dt>actor</dt>
        <dd>system</dd>
        <dt>correlation_id</dt>
        <dd className={styles.mono}>{store.correlationId}</dd>
      </dl>
    </section>
  );
}

function DecisionSeparation() {
  return (
    <section className={styles.panel} data-testid="decision-separation">
      <h2>Decision separation</h2>
      <ul className={styles.list}>
        <li>Prediction: ForecastBandChart and interval table.</li>
        <li>Recommendation: FourLightBadge and recommended_actions.</li>
        <li>Human decision: acknowledge or create intervention.</li>
        <li>Execution: InterventionHandoff status and link.</li>
        <li>Outcome: reserved for InterventionOps, no premature impact claim.</li>
      </ul>
    </section>
  );
}

function Drawer({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  return (
    <aside className={styles.drawer} data-testid={testId}>
      <h2>{title}</h2>
      <div className={styles.actionRow} aria-label="Drawer navigation">
        <button className={styles.iconButton} type="button" aria-label="Previous item">
          ‹
        </button>
        <button className={styles.iconButton} type="button" aria-label="Next item">
          ›
        </button>
      </div>
      {children}
    </aside>
  );
}

function ForecastSummary({ store }: { store: StoreForecast }) {
  return (
    <div className={styles.metricRow}>
      <Metric label="actual" value={formatMoney(store.actualRevenue)} />
      <Metric label="baseline" value={formatMoney(store.baselineP50)} />
      <Metric label="turning point" value={formatPercent(store.turningPointProbability)} />
    </div>
  );
}

function EvidenceList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className={styles.evidenceBlock}>
      <h3>{title}</h3>
      <ul className={styles.list}>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function selectedFromQuery(value: string | string[] | undefined) {
  if (Array.isArray(value)) return value[0];
  return value;
}

function openCount(level: AlertLevel) {
  return alerts.filter((alert) => alert.light === level && alert.status === "open").length;
}
