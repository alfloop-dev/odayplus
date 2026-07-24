import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { StatusTone } from "@oday-plus/domain-types";
import type {
  ModelReleaseSummary,
  ModelVersionSummary,
} from "@oday-plus/openapi-client";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import { DataSourceBadge } from "../../src/components/DataSourceBadge.tsx";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "../operations/ProductionDataState.tsx";
import {
  models,
  releases,
  selectedModel,
  selectedRelease,
  type ModelVersionRecord,
  type ReleaseDecision,
  type ReviewStatus,
} from "./data.ts";
import styles from "./learninghub.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

type LearningHubWorkspaceProps = {
  view?: "overview" | "models" | "modelHistory" | "modelDetail" | "releases" | "releaseDetail";
  modelName?: string;
  version?: string;
  releaseId?: string;
  searchParams?: SearchParams;
  /** Live `GET /learninghub/releases` binding; supplied by the server route. */
  liveReleases?: ApiBinding<ModelReleaseSummary>;
  /** Live `GET /learninghub/models` binding; supplied by the server route. */
  liveModels?: ApiBinding<ModelVersionSummary>;
  isProduction?: boolean;
};

export function LearningHubWorkspace({
  view = "overview",
  modelName,
  version,
  releaseId,
  searchParams = {},
  liveReleases,
  liveModels,
  isProduction: isProductionProp,
}: LearningHubWorkspaceProps) {
  if (resolveProductionMode(isProductionProp)) {
    return (
      <ProductionLearningHubWorkspace
        modelBinding={liveModels}
        modelName={modelName}
        releaseBinding={liveReleases}
        releaseId={releaseId}
        version={version}
        view={view}
      />
    );
  }
  if (view === "models") return <ModelsPage searchParams={searchParams} />;
  if (view === "modelHistory") return <ModelHistoryPage model={selectedModel(modelName)} />;
  if (view === "modelDetail") return <ModelDetailPage model={selectedModel(modelName, version)} />;
  if (view === "releases") return <ReleasesPage liveReleases={liveReleases} />;
  if (view === "releaseDetail") return <ReleaseDetailPage release={selectedRelease(releaseId)} />;
  return <LearningOverview liveReleases={liveReleases} />;
}

function ProductionLearningHubWorkspace({
  modelBinding,
  modelName,
  releaseBinding,
  releaseId,
  version,
  view,
}: {
  modelBinding?: ApiBinding<ModelVersionSummary>;
  modelName?: string;
  releaseBinding?: ApiBinding<ModelReleaseSummary>;
  releaseId?: string;
  version?: string;
  view: NonNullable<LearningHubWorkspaceProps["view"]>;
}) {
  const supportsReleaseRows = view === "overview" || view === "releases" || view === "releaseDetail";
  const productionBinding = supportsReleaseRows ? releaseBinding : modelBinding;
  const state = productionBindingState(productionBinding);
  return (
    <>
      <PageHeader
        breadcrumb={[{ label: "模型與學習", href: "/learning" }, { label: releaseId ?? view }]}
        lastUpdated={productionBinding?.fetchedAt ? `API checked ${productionBinding.fetchedAt}` : "Live source not available"}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        summary="Production Learning Hub. Model governance data is never substituted with bundled samples."
        title={releaseId ? `模型發布 ${releaseId}` : view === "models" ? "模型登錄" : "模型與學習"}
      />
      <main className="odp-content" data-testid={`learning-${view}-production-page`}>
        <WorkspaceNav active={view === "overview" ? "overview" : view.startsWith("model") ? "models" : "releases"} />
        {supportsReleaseRows ? (
          <ProductionDataState
            binding={releaseBinding}
            resource="Learning Hub releases"
            testId="learning-production-data-state"
          >
            {releaseBinding ? <LiveReleases binding={releaseBinding} productionMode /> : null}
          </ProductionDataState>
        ) : (
          <ProductionDataState
            binding={modelBinding}
            resource="Model registry"
            testId="learning-production-data-state"
          >
            {modelBinding ? (
              <LiveModels
                binding={modelBinding}
                selectedModelName={modelName}
                selectedVersion={version}
              />
            ) : null}
          </ProductionDataState>
        )}
        {releaseBinding?.state === "ready" && releaseId && !releaseBinding.items.some((item) => item.release_id === releaseId) ? (
          <section className={styles.panel} data-testid="learning-release-not-found" role="status">
            <h2>Release not found</h2>
            <p>API 回傳資料中沒有 {releaseId}；未以固定發布紀錄替代。</p>
          </section>
        ) : null}
      </main>
    </>
  );
}

function LiveModels({
  binding,
  selectedModelName,
  selectedVersion,
}: {
  binding: ApiBinding<ModelVersionSummary>;
  selectedModelName?: string;
  selectedVersion?: string;
}) {
  const rows = binding.items.filter(
    (item) =>
      (!selectedModelName || item.model_name === selectedModelName) &&
      (!selectedVersion || item.version === selectedVersion),
  );

  return (
    <section className={styles.panel} data-testid="learning-live-models">
      <div className={styles.badgeRow}>
        <h2>模型登錄（API live）</h2>
        <ProductionDataBadge binding={binding} testId="learning-model-data-source" />
      </div>
      {(selectedModelName || selectedVersion) && rows.length === 0 ? (
        <p data-testid="learning-live-model-not-found">
          API 回傳資料中沒有 {selectedModelName}
          {selectedVersion ? `:${selectedVersion}` : ""}；未以固定模型替代。
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="learning-live-models-table">
            <caption>Persisted model versions served by GET /learninghub/models.</caption>
            <thead>
              <tr>
                <th>Model</th>
                <th>Version</th>
                <th>Stage / aliases</th>
                <th>Dataset / schema</th>
                <th>Artifact</th>
                <th>Approval</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr
                  key={`${item.model_name}:${item.version}`}
                  data-testid="learning-live-model-row"
                >
                  <td>
                    <Link href={`/w/ai/models/${encodeURIComponent(item.model_name)}`}>
                      {item.model_name}
                    </Link>
                  </td>
                  <td>
                    <Link
                      href={`/w/ai/models/${encodeURIComponent(item.model_name)}/${encodeURIComponent(item.version)}`}
                    >
                      {item.version}
                    </Link>
                  </td>
                  <td>
                    {item.stage}
                    <span className={styles.subtle}>{item.aliases.join(", ") || "no alias"}</span>
                  </td>
                  <td>
                    <span className={styles.mono}>{item.dataset_snapshot_id}</span>
                    <span className={styles.subtle}>{item.feature_schema_version}</span>
                  </td>
                  <td className={styles.mono}>{item.artifact_uri}</td>
                  <td>
                    {item.approved_by || "not approved"}
                    <span className={styles.subtle}>{item.approved_at || "—"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function LearningOverview({ liveReleases }: { liveReleases?: ApiBinding<ModelReleaseSummary> }) {
  return (
    <>
      <Header title="模型與學習" summary="Learning Hub：模型登錄、驗證、模型卡、發布控制與 rollback console。" />
      <main className="odp-content" data-testid="learning-overview-page">
        <WorkspaceNav active="overview" />
        {liveReleases ? <LiveReleases binding={liveReleases} /> : null}
        <section className={styles.flowGrid}>
          <Link className={styles.flowCard} href="/w/ai/models">
            <h2>模型登錄</h2>
            <p>掃描 stage、alias、validation、model card 完整度與待核准狀態。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/ai/models/sitescore-propensity/2.4.0">
            <h2>Release gate</h2>
            <p>逐條檢查 validation、model card、approval 與 rollback target。</p>
          </Link>
          <Link className={styles.flowCard} href="/w/ai/releases">
            <h2>發布與回滾</h2>
            <p>追蹤 release decision、監控窗、success/fail criteria 與 audit_event_id。</p>
          </Link>
        </section>
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Governance queue</h2>
            <div className={styles.metricRow}>
              <Metric label="Blocked models" value={models.filter((model) => model.stage === "blocked").length} />
              <Metric label="Canary releases" value={models.filter((model) => model.stage === "canary").length} />
              <Metric label="R3/R4 risk" value={models.filter((model) => model.riskLevel === "R3" || model.riskLevel === "R4").length} />
            </div>
          </div>
          <ReleaseGate model={models[0]} />
        </section>
      </main>
    </>
  );
}

function ModelsPage({ searchParams }: { searchParams: SearchParams }) {
  const selectedName = selectedFromQuery(searchParams.selected) ?? models[0].modelName;
  const selected = selectedModel(selectedName);

  return (
    <>
      <Header title="模型登錄" summary="管理各模型版本的驗證、模型卡與上線階段。" statusLabel="compact · URL state" />
      <main className="odp-content" data-testid="learning-models-page">
        <WorkspaceNav active="models" />
        <FilterBar />
        <section className={styles.overviewGrid}>
          <div className={styles.panel}>
            <h2>Model registry</h2>
            <ModelsTable />
          </div>
          <aside className={styles.stickyPanel} data-testid="model-drawer">
            <ModelReleaseCard model={selected} />
            <ReleaseGate model={selected} />
            <div className={styles.actionRow}>
              <Link className={styles.primaryButton} href={`/w/ai/models/${selected.modelName}/${selected.version}`}>
                開啟版本詳情
              </Link>
              <Link className={styles.secondaryButton} href="/w/ai/releases">
                查看發布
              </Link>
            </div>
          </aside>
        </section>
      </main>
    </>
  );
}

function ModelHistoryPage({ model }: { model: ModelVersionRecord }) {
  const versions = models.filter((candidate) => candidate.modelName === model.modelName);
  return (
    <>
      <Header title={`${model.modelName} 版本歷史`} summary="比較 champion / challenger / shadow / canary 的 stage、alias 與治理狀態。" />
      <main className="odp-content" data-testid="learning-model-history-page">
        <WorkspaceNav active="models" />
        <div className={styles.panel}>
          <h2>Version comparison</h2>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Stage / Alias</th>
                  <th>Validation</th>
                  <th>Card</th>
                  <th>Rollback target</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((item) => (
                  <tr key={item.version}>
                    <td className={styles.mono}>{item.version}</td>
                    <td><StageBadge model={item} /></td>
                    <td>{item.validationPassed ? "✓ passed" : "✕ failed"}</td>
                    <td>{item.cardComplete && item.cardApproved ? "complete + approved" : "needs work"}</td>
                    <td className={styles.mono}>{item.rollbackTarget ?? "missing"}</td>
                    <td><Link className={styles.link} href={`/w/ai/models/${item.modelName}/${item.version}`}>Open</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </>
  );
}

function ModelDetailPage({ model }: { model: ModelVersionRecord }) {
  return (
    <>
      <Header title={`${model.modelName}:${model.version}`} summary="模型版本詳情：model card、validation、release controller、rollback console 與版本稽核。" statusLabel={`${model.stage} · ${model.riskLevel}`} />
      <main className="odp-content" data-testid="learning-model-detail-page">
        <WorkspaceNav active="models" />
        <section className={styles.summaryBand} data-testid="model-summary">
          <div className={styles.metricRow}>
            <Metric label="Stage" value={model.stage} />
            <Metric label="Aliases" value={model.aliases.join(", ")} />
            <Metric label="Risk" value={model.riskLevel} />
            <Metric label="Rollback target" value={model.rollbackTarget ?? "missing"} />
          </div>
        </section>
        <section className={styles.detailGrid}>
          <div className={styles.sectionStack}>
            <ModelCard model={model} />
            <ValidationPanel model={model} />
            <ReleaseController model={model} />
            <RollbackConsole model={model} />
          </div>
          <aside className={styles.stickyPanel}>
            <ReleaseGate model={model} />
            <AuditMetadata model={model} />
          </aside>
        </section>
      </main>
    </>
  );
}

function ReleasesPage({ liveReleases }: { liveReleases?: ApiBinding<ModelReleaseSummary> }) {
  return (
    <>
      <Header title="模型發布" summary="掃描發布 / 回滾事件、監控窗、success/fail criteria 與 audit trail。" statusLabel="compact" />
      <main className="odp-content" data-testid="learning-releases-page">
        <WorkspaceNav active="releases" />
        {liveReleases ? <LiveReleases binding={liveReleases} /> : null}
        <div className={styles.panel}>
          <h2>Release decisions</h2>
          <ReleasesTable />
        </div>
      </main>
    </>
  );
}

function LiveReleases({
  binding,
  productionMode = false,
}: {
  binding: ApiBinding<ModelReleaseSummary>;
  productionMode?: boolean;
}) {
  return (
    <section
      className={styles.panel}
      data-testid="learning-live-releases"
      aria-label="API-bound model release and rollback log"
    >
      <div className={styles.badgeRow}>
        <h2>發布 / 回滾（API live）</h2>
        {productionMode ? (
          <ProductionDataBadge binding={binding} testId="learning-data-source" />
        ) : (
          <DataSourceBadge binding={binding} testId="learning-data-source" />
        )}
      </div>
      <p>
        本區直接讀取 <code>GET /learninghub/releases</code> 的治理決策（shadow / canary / full /
        rollback）。
        {!productionMode ? " 下方固定發布為 documented non-product fixture。" : null}
      </p>
      {binding.state === "ready" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table} data-testid="learning-live-releases-table">
            <caption>Live releases served by the backend ({binding.items.length})</caption>
            <thead>
              <tr>
                <th>release_id</th>
                <th>Model</th>
                <th>Type</th>
                <th>Version</th>
                <th>Monitoring</th>
                <th>audit_event_id</th>
              </tr>
            </thead>
            <tbody>
              {binding.items.map((item) => (
                <tr key={item.release_id} data-testid="learning-live-release-row">
                  <td className={styles.mono}>{item.release_id}</td>
                  <td>{stringField(item.model_name) || "—"}</td>
                  <td>
                    <Badge
                      label={stringField(item.release_type) || "—"}
                      tone={liveReleaseTone(item.release_type)}
                      marker="◆"
                    />
                  </td>
                  <td className={styles.mono}>
                    {stringField(item.from_version) || "—"} → {stringField(item.to_version) || "—"}
                  </td>
                  <td>{stringField(item.monitoring_window) || "—"}</td>
                  <td className={styles.mono}>{stringField(item.audit_event_id) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p data-testid="learning-live-releases-empty" className={styles.subtle}>
          {liveReleasesFallbackMessage(binding)}
        </p>
      )}
    </section>
  );
}

function liveReleasesFallbackMessage(binding: ApiBinding<ModelReleaseSummary>): string {
  if (binding.state === "empty") {
    return "後端可連線但尚無發布決策（cold store）；顯示固定發布作為非產品 fallback。";
  }
  if (binding.state === "error") {
    return `後端讀取失敗（${binding.error ?? "unknown"}）；改用固定發布 fallback。`;
  }
  return "未設定 API base URL（ODP_API_BASE_URL）；以固定發布渲染。";
}

function liveReleaseTone(releaseType: unknown): StatusTone {
  if (releaseType === "ROLLBACK") return "red";
  if (releaseType === "FULL") return "green";
  if (releaseType === "CANARY") return "orange";
  return "blue";
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function ReleaseDetailPage({ release }: { release: ReleaseDecision }) {
  return (
    <>
      <Header title={release.releaseId} summary="單次發布條件、核准、影響模組與 DecisionAuditTimeline。" statusLabel={`${release.releaseType} · ${release.monitoringWindow}`} />
      <main className="odp-content" data-testid="learning-release-detail-page">
        <WorkspaceNav active="releases" />
        <section className={styles.detailGrid}>
          <div className={styles.sectionStack}>
            <div className={styles.panel}>
              <h2>Release decision</h2>
              <dl className={styles.auditGrid}>
                <dt>Model</dt><dd>{release.modelName}</dd>
                <dt>Version</dt><dd>{release.fromVersion} → {release.toVersion}</dd>
                <dt>Reason</dt><dd>{release.reason}</dd>
                <dt>Approval</dt><dd>{release.approvalId} · {release.approvedBy}</dd>
                <dt>Success criteria</dt><dd>{release.successCriteria}</dd>
                <dt>Fail criteria</dt><dd>{release.failCriteria}</dd>
                <dt>Affected modules</dt><dd>{release.affectedModules.join(", ")}</dd>
              </dl>
            </div>
            <DecisionAuditTimeline correlationId={release.correlationId} auditEventId={release.auditEventId} />
          </div>
          <aside className={styles.stickyPanel}>
            <div className={styles.panel}>
              <h2>Audit metadata</h2>
              <dl className={styles.auditGrid}>
                <dt>audit_event_id</dt><dd className={styles.mono}>{release.auditEventId}</dd>
                <dt>correlation_id</dt><dd className={styles.mono}>{release.correlationId}</dd>
                <dt>created_at</dt><dd>{release.createdAt}</dd>
                <dt>requested_by</dt><dd>{release.requestedBy}</dd>
              </dl>
            </div>
          </aside>
        </section>
      </main>
    </>
  );
}

function Header({ title, summary, statusLabel = "Learning Hub" }: { title: string; summary: string; statusLabel?: string }) {
  return (
    <PageHeader
      title={title}
      summary={summary}
      status={{ label: statusLabel, tone: "purple", marker: "◆" }}
      breadcrumb={[{ label: "AI／資料", href: "/learning" }, { label: title }]}
      lastUpdated="2026-06-28"
      actions={<Link className={styles.secondaryButton} href="/w/ai/models">模型登錄</Link>}
    />
  );
}

function WorkspaceNav({ active }: { active: "overview" | "models" | "releases" }) {
  const items = [
    { key: "overview", label: "總覽", href: "/learning" },
    { key: "models", label: "模型登錄", href: "/w/ai/models" },
    { key: "releases", label: "發布", href: "/w/ai/releases" },
  ] as const;
  return (
    <nav className={styles.workspaceNav} aria-label="Learning Hub sections">
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
    <form className={styles.toolbar} aria-label="Model registry filters">
      <label>model_name<input name="model_name" defaultValue="" /></label>
      <label>stage<select name="stage" defaultValue=""><option value="">all</option><option>canary</option><option>production</option><option>blocked</option></select></label>
      <label>risk_level<select name="risk_level" defaultValue=""><option value="">all</option><option>R3/R4</option></select></label>
      <label>validation<select name="validation" defaultValue=""><option value="">all</option><option>passed</option><option>failed</option></select></label>
      <button className={styles.secondaryButton} type="submit">Apply</button>
    </form>
  );
}

function ModelsTable() {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="model-registry-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Version</th>
            <th>Stage / Alias</th>
            <th>Risk</th>
            <th>Validation</th>
            <th>Card</th>
            <th>Data quality / Drift</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => (
            <tr key={`${model.modelName}-${model.version}`}>
              <td><Link className={styles.link} href={`/w/ai/models/${model.modelName}`}>{model.modelName}</Link></td>
              <td><Link className={styles.link} href={`/w/ai/models/${model.modelName}/${model.version}`}>{model.version}</Link></td>
              <td><StageBadge model={model} /></td>
              <td><Badge label={model.riskLevel} tone={model.riskLevel === "R4" ? "red" : model.riskLevel === "R3" ? "orange" : "green"} marker={model.riskLevel === "R4" ? "!" : "◆"} /></td>
              <td>{model.validationPassed ? <span className={styles.passText}>✓ passed</span> : <span className={styles.failText}>✕ failed</span>}</td>
              <td>{model.cardComplete ? "complete" : "incomplete"} · {model.cardApproved ? "approved" : "not approved"}</td>
              <td>{statusCopy(model.dataQualityStatus)} / {statusCopy(model.driftStatus)}</td>
              <td><Link className={styles.link} href={`/w/ai/models?selected=${model.modelName}`}>Open drawer</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReleasesTable() {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="release-table">
        <thead>
          <tr>
            <th>release_id</th>
            <th>Model</th>
            <th>Type</th>
            <th>Approval</th>
            <th>Monitoring</th>
            <th>approved_by</th>
            <th>created_at</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {releases.map((release) => (
            <tr key={release.releaseId}>
              <td className={styles.mono}>{release.releaseId}</td>
              <td>{release.modelName} {release.fromVersion} → {release.toVersion}</td>
              <td>{release.releaseType}</td>
              <td className={styles.mono}>{release.approvalId}</td>
              <td>{release.monitoringWindow}</td>
              <td>{release.approvedBy}</td>
              <td>{release.createdAt}</td>
              <td><Link className={styles.link} href={`/w/ai/releases/${release.releaseId}`}>Open</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StageBadge({ model }: { model: ModelVersionRecord }) {
  return (
    <span className={styles.stagePill} title={`aliases: ${model.aliases.join(", ")}`}>
      {stageMarker(model.stage)} {model.stage} · {model.aliases.join(", ")}
    </span>
  );
}

function ModelReleaseCard({ model }: { model: ModelVersionRecord }) {
  return (
    <div className={styles.panel} data-testid="model-release-card">
      <h2>ModelReleaseCard</h2>
      <dl className={styles.auditGrid}>
        <dt>modelId</dt><dd>{model.modelName}</dd>
        <dt>version</dt><dd>{model.version}</dd>
        <dt>champion/challenger</dt><dd>{model.aliases.includes("champion") ? "champion" : "challenger"}</dd>
        <dt>metricSummary</dt><dd>{model.metricsSummary}</dd>
        <dt>segmentRegression</dt><dd>{model.segments.filter((segment) => segment.status !== "PASSED").map((segment) => segment.segment).join(", ") || "none"}</dd>
        <dt>dataQualityStatus</dt><dd>{statusCopy(model.dataQualityStatus)}</dd>
        <dt>driftStatus</dt><dd>{statusCopy(model.driftStatus)}</dd>
        <dt>releaseStage</dt><dd>{model.stage}</dd>
        <dt>rollbackTarget</dt><dd>{model.rollbackTarget ?? "missing"}</dd>
        <dt>approvalStatus</dt><dd>{model.cardApproved ? "approved" : "not approved"}</dd>
      </dl>
    </div>
  );
}

function ReleaseGate({ model }: { model: ModelVersionRecord }) {
  const checks = releaseGateChecks(model);
  const canRelease = checks.every((check) => check.pass);
  return (
    <div className={styles.panel} data-testid="release-gate-checklist">
      <h2>發布前置 checklist</h2>
      <ul className={styles.checklist}>
        {checks.map((check) => (
          <li key={check.label} className={[styles.checkRow, check.pass ? "" : styles.danger].filter(Boolean).join(" ")}>
            <span>{check.pass ? "✓" : "✕"} {check.label}</span>
            <span className={styles.subtle}>{check.target}</span>
          </li>
        ))}
      </ul>
      <p className={styles.subtle}>{canRelease ? "All gates green; request release may be enabled after reason and approval_id are entered." : "Release blocked until every gate is green. No optimistic stage changes."}</p>
    </div>
  );
}

function ModelCard({ model }: { model: ModelVersionRecord }) {
  return (
    <section className={styles.panel} data-testid="model-card-section">
      <h2>Model Card</h2>
      <div className={styles.cardGrid}>
        <Metric label="Owner / Risk" value={`${model.owner} · ${model.riskLevel}`} />
        <Metric label="Data / Feature" value={`${model.datasetSnapshotId} · ${model.featureSetId}`} />
        <Metric label="Label / Period" value={`${model.labelSetId} · ${model.trainingPeriod}`} />
        <Metric label="Algorithm / Baseline" value={`${model.algorithm} · ${model.baseline}`} />
      </div>
      <dl className={styles.auditGrid}>
        <dt>intended_use</dt><dd>{model.intendedUse}</dd>
        <dt>not_intended_use</dt><dd>{model.notIntendedUse}</dd>
        <dt>metrics_summary</dt><dd>{model.metricsSummary}</dd>
        <dt>calibration_summary</dt><dd>{model.calibrationSummary}</dd>
        <dt>explainability_method</dt><dd>{model.explainabilityMethod}</dd>
        <dt>limitations</dt><dd>{model.limitations}</dd>
        <dt>known_biases</dt><dd>{model.knownBiases}</dd>
        <dt>privacy/security</dt><dd>{statusCopy(model.privacyReview)} / {statusCopy(model.securityReview)}</dd>
        <dt>rollback_conditions</dt><dd>{model.rollbackConditions.length ? model.rollbackConditions.join("; ") : "missing"}</dd>
        <dt>approvals</dt><dd>{model.approvals.length ? model.approvals.join("; ") : "missing"}</dd>
      </dl>
    </section>
  );
}

function ValidationPanel({ model }: { model: ModelVersionRecord }) {
  return (
    <section className={styles.panel} data-testid="validation-panel">
      <h2>Validation</h2>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Threshold</th>
              <th>Actual</th>
              <th>Baseline</th>
              <th>Δ</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {model.validation.map((metric) => {
              const pass = metric.thresholdType === ">=" ? metric.actual >= metric.threshold : metric.actual <= metric.threshold;
              return (
                <tr key={metric.name}>
                  <td>{metric.name}</td>
                  <td>{metric.thresholdType} {metric.threshold}</td>
                  <td>{metric.actual}</td>
                  <td>{metric.baseline}</td>
                  <td>{(metric.actual - metric.baseline).toFixed(3)}</td>
                  <td className={pass ? styles.passText : styles.failText}>{pass ? "✓ pass" : "✕ fail"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h3>Segment metrics</h3>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th>Segment</th><th>Lift</th><th>Calibration</th><th>Status</th></tr></thead>
          <tbody>{model.segments.map((segment) => <tr key={segment.segment}><td>{segment.segment}</td><td>{segment.conversionLift}</td><td>{segment.calibration}</td><td>{statusCopy(segment.status)}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

function ReleaseController({ model }: { model: ModelVersionRecord }) {
  const canRelease = releaseGateChecks(model).every((check) => check.pass);
  return (
    <section className={styles.panel} data-testid="release-controller">
      <h2>Release Controller</h2>
      <p>SHADOW → shadow alias, CANARY → canary alias, FULL → production + champion with previous_production retained.</p>
      <div className={styles.cardGrid}>
        <label>release_type<select className={styles.field} defaultValue="CANARY"><option>SHADOW</option><option>CANARY</option><option>FULL</option><option>ROLLBACK</option></select></label>
        <label>approval_id<input className={styles.field} defaultValue={model.cardApproved ? "approval-lh-draft" : ""} /></label>
        <label>monitoring_window<input className={styles.field} defaultValue="24h" /></label>
        <label>rollback_target<input className={styles.field} defaultValue={model.rollbackTarget ?? ""} /></label>
      </div>
      <label>reason<textarea className={styles.textarea} defaultValue="Release request requires human approval and backend Audit before stage changes." /></label>
      <p className={styles.subtle}>Affected modules: {model.affectedModules.join(", ")}. Segregation applies for {model.riskLevel} releases.</p>
      <button className={styles.primaryButton} type="button" disabled={!canRelease}>申請發布</button>
    </section>
  );
}

function RollbackConsole({ model }: { model: ModelVersionRecord }) {
  const canRollback = Boolean(model.rollbackTarget);
  return (
    <section className={styles.panel} data-testid="rollback-console">
      <h2>Rollback Console</h2>
      <div className={styles.cardGrid}>
        <Metric label="Current" value={`${model.version} · ${model.stage}`} />
        <Metric label="Target" value={model.rollbackTarget ?? "missing rollback_target"} />
        <Metric label="Rollback conditions" value={model.rollbackConditions.length ? model.rollbackConditions.join("; ") : "missing"} />
      </div>
      <label>rollback reason<textarea className={styles.textarea} defaultValue="Rollback is disabled until a reason is provided and backend Audit succeeds." /></label>
      <button className={styles.secondaryButton} type="button" disabled={!canRollback}>執行回滾</button>
    </section>
  );
}

function AuditMetadata({ model }: { model: ModelVersionRecord }) {
  return (
    <div className={styles.panel} data-testid="learning-audit-metadata">
      <h2>Version / Audit</h2>
      <dl className={styles.auditGrid}>
        <dt>model_version</dt><dd>{model.version}</dd>
        <dt>feature_schema_version</dt><dd>{model.featureSchemaVersion}</dd>
        <dt>label_version</dt><dd>{model.labelVersion}</dd>
        <dt>run_id</dt><dd className={styles.mono}>{model.runId}</dd>
        <dt>git_sha</dt><dd className={styles.mono}>{model.gitSha}</dd>
        <dt>approved_by</dt><dd>{model.approvedBy ?? "pending"}</dd>
        <dt>approved_at</dt><dd>{model.approvedAt ?? "pending"}</dd>
        <dt>policy</dt><dd>learninghub-release-policy-v1</dd>
        <dt>monitoring_config</dt><dd>{model.monitoringConfig}</dd>
        <dt>correlation_id</dt><dd className={styles.mono}>{model.correlationId}</dd>
      </dl>
    </div>
  );
}

function DecisionAuditTimeline({ correlationId, auditEventId }: { correlationId: string; auditEventId: string }) {
  const nodes = ["Prediction generated", "Recommendation generated", "Human review requested", "Human decision submitted", "Execution started", "Outcome observed", "Feedback written to label registry"];
  return (
    <section className={styles.panel} data-testid="learning-decision-audit-timeline">
      <h2>DecisionAuditTimeline</h2>
      <ol className={styles.timeline}>
        {nodes.map((node, index) => (
          <li key={node}>
            <strong>{index < 5 ? "✓" : "○"} {node}</strong>
            <div className={styles.timelineMeta}>
              <span>actor {index < 3 ? "system" : "model-review-board"}</span>
              <span>outcome {index < 5 ? "success" : "待發生"}</span>
              <span className={styles.mono}>correlation_id {correlationId}</span>
              {index === 4 ? <span className={styles.mono}>audit_event_id {auditEventId}</span> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className={styles.metric}><span>{label}</span><strong>{value}</strong></div>;
}

function releaseGateChecks(model: ModelVersionRecord) {
  return [
    { label: "Validation passed", pass: model.validationPassed, target: "ValidationRun.passed" },
    { label: "Model card complete", pass: model.cardComplete && model.privacyReview !== "FAILED" && model.securityReview !== "FAILED", target: "is_complete + reviews" },
    { label: "Model card approved", pass: model.cardApproved, target: "ModelCardApproval" },
    { label: "Rollback target present", pass: Boolean(model.rollbackTarget), target: "FULL/CANARY/ROLLBACK" },
  ];
}

function statusCopy(status: ReviewStatus) {
  return status === "PASSED" ? "✓ PASSED" : status === "WARNING" ? "▲ WARNING" : "✕ FAILED";
}

function stageMarker(stage: ModelVersionRecord["stage"]) {
  if (stage === "production") return "◆";
  if (stage === "canary") return "◧";
  if (stage === "shadow") return "◇";
  if (stage === "rolled_back" || stage === "blocked") return "!";
  return "○";
}

function selectedFromQuery(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}
