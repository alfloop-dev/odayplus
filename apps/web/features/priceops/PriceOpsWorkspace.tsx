import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import type { ApiBinding } from "../../src/lib/api/binding.ts";
import {
  ProductionDataBadge,
  ProductionDataState,
  productionBindingState,
  resolveProductionMode,
} from "../operations/ProductionDataState.tsx";
import { constraintTone, freshness, lifecycleTone, pricePlans, type PricePlan } from "./data.ts";
import styles from "../intervention/intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

export type LivePricePlan = {
  plan_id?: string;
  tenant_id?: string;
  status?: string;
  items?: unknown[];
  created_at?: string;
  correlation_id?: string;
};

type PriceOpsWorkspaceProps = {
  searchParams?: SearchParams;
  livePlans?: ApiBinding<LivePricePlan>;
  isProduction?: boolean;
};

export function PriceOpsWorkspace({
  searchParams = {},
  livePlans,
  isProduction: isProductionProp,
}: PriceOpsWorkspaceProps) {
  if (resolveProductionMode(isProductionProp)) {
    return <ProductionPriceOpsWorkspace binding={livePlans} searchParams={searchParams} />;
  }
  const selectedId = readParam(searchParams.selected) ?? pricePlans[0].id;
  const selected = pricePlans.find((item) => item.id === selectedId) ?? pricePlans[0];

  return (
    <>
      <PageHeader
        title="定價"
        summary="PricingPlanComparison：現行價與候選價並陳，硬限制違反不可核准，rollback 計畫必須可見。"
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "定價 Pricing" }]}
        status={{ label: freshness.status, tone: dataStatusTone[freshness.status], marker: "◆", "data-testid": "priceops-data-status" }}
        lastUpdated={`${freshness.updatedAt} · model ${freshness.modelVersion}`}
        actions={
          <div className={styles.actions}>
            <a className={styles.secondaryButton} href="#rollback">Rollback plans</a>
            <a className={styles.primaryButton} href="#approval">Open price approval</a>
          </div>
        }
      />
      <main className="odp-content" data-testid="module-pricing">
        <div data-testid="priceops-page">
          <nav className={styles.workspaceNav} aria-label="Pricing module navigation">
            <Link aria-current="page" href="/pricing">PriceOps Plans</Link>
            <Link href="/adlift">AdLift Reports</Link>
            <Link href="/interventions">Intervention handoff</Link>
          </nav>
          <section className={styles.overviewGrid} aria-label="PriceOps overview">
            <Summary title="Pending approval" value="2" copy="manual approval only; no auto execution" />
            <Summary title="Hard constraint failures" value="1" copy="blocked plan cannot be approved" />
            <Summary title="Rollback ready" value="2" copy="publish requires rollback plan, label, and audit" />
          </section>
          <FilterBar />
          <section className={styles.grid}>
            <PlanTable selected={selected.id} />
            <PlanDrawer plan={selected} />
          </section>
        </div>
      </main>
    </>
  );
}

function ProductionPriceOpsWorkspace({
  binding,
  searchParams,
}: {
  binding?: ApiBinding<LivePricePlan>;
  searchParams: SearchParams;
}) {
  const state = productionBindingState(binding);
  const selectedId = readParam(searchParams.selected);
  const selected = binding?.items.find((plan) => plan.plan_id === selectedId);

  return (
    <>
      <PageHeader
        title="定價"
        summary="Production PriceOps plans. Only persisted API plans are rendered."
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "定價 Pricing" }]}
        status={{
          label: state === "ready" ? "API live" : "DATA_UNAVAILABLE",
          marker: state === "ready" ? "◆" : "!",
          tone: state === "ready" ? "green" : state === "error" ? "red" : "gray",
        }}
        lastUpdated={binding?.fetchedAt ? `API checked ${binding.fetchedAt}` : "Live source not available"}
      />
      <main className="odp-content" data-testid="priceops-production-page">
        <nav className={styles.workspaceNav} aria-label="Pricing module navigation">
          <Link aria-current="page" href="/pricing">PriceOps Plans</Link>
          <Link href="/adlift">AdLift Reports</Link>
          <Link href="/interventions">Intervention handoff</Link>
        </nav>
        <ProductionDataState binding={binding} resource="PriceOps plans" testId="priceops-production-data-state">
          {binding ? (
            <section className={styles.panel} data-testid="priceops-live-plans">
              <div className={styles.badgeRow}>
                <h2>PriceOps plans（API live）</h2>
                <ProductionDataBadge binding={binding} testId="priceops-data-source" />
              </div>
              <section className={styles.overviewGrid} aria-label="Live PriceOps overview">
                <Summary title="Plans" value={String(binding.items.length)} copy="API plan count" />
                <Summary
                  title="Pending approval"
                  value={String(binding.items.filter((plan) => plan.status === "PENDING_APPROVAL").length)}
                  copy="API lifecycle state"
                />
                <Summary
                  title="Rollback"
                  value={String(binding.items.filter((plan) => plan.status === "ROLLBACK").length)}
                  copy="API lifecycle state"
                />
              </section>
              <div className={styles.tableWrap}>
                <table className={styles.table} data-testid="priceops-live-table">
                  <caption>Persisted PriceOps plans served by GET /priceops/plans.</caption>
                  <thead>
                    <tr>
                      <th>Plan</th>
                      <th>Tenant</th>
                      <th>Status</th>
                      <th>Items</th>
                      <th>Created</th>
                      <th>Correlation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {binding.items.map((plan) => {
                      const planId = plan.plan_id || "unknown-plan";
                      return (
                        <tr key={planId} aria-selected={planId === selectedId} data-testid="priceops-live-row">
                          <td><Link href={`/pricing?selected=${encodeURIComponent(planId)}`}>{planId}</Link></td>
                          <td>{plan.tenant_id || "—"}</td>
                          <td>{plan.status || "—"}</td>
                          <td>{Array.isArray(plan.items) ? plan.items.length : 0}</td>
                          <td>{plan.created_at || "—"}</td>
                          <td>{plan.correlation_id || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {selectedId && !selected ? (
                <p data-testid="priceops-plan-not-found">
                  API 回傳資料中沒有 {selectedId}；未以固定方案替代。
                </p>
              ) : null}
              {selected ? (
                <aside className={styles.drawer} data-testid="priceops-live-plan-detail">
                  <h2>{selected.plan_id}</h2>
                  <p>status: {selected.status || "—"}</p>
                  <p>created at: {selected.created_at || "—"}</p>
                  <p>correlation: {selected.correlation_id || "—"}</p>
                </aside>
              ) : null}
            </section>
          ) : null}
        </ProductionDataState>
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
    <form className={styles.filterBar} aria-label="URL synced pricing filters">
      <label>
        Constraint
        <select defaultValue="all" name="constraint">
          <option value="all">全部</option>
          <option>PASS</option>
          <option>HARD_CONSTRAINT_FAILED</option>
        </select>
      </label>
      <label>
        Risk
        <select defaultValue="all" name="risk">
          <option value="all">全部</option>
          <option>low</option>
          <option>medium</option>
          <option>high</option>
        </select>
      </label>
      <a className={styles.secondaryButton} href="/pricing?selected=price-5102&drawer=plan">Show blocked plan</a>
    </form>
  );
}

function PlanTable({ selected }: { selected: string }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} data-testid="priceops-table">
        <caption>PricingPlanComparison with current and candidate price, expected impact, constraints, rollback, and approval status.</caption>
        <thead>
          <tr>
            <th>Plan</th>
            <th>Price change</th>
            <th>Demand</th>
            <th>Revenue</th>
            <th>Gross margin</th>
            <th>Constraint</th>
            <th>Loop state</th>
            <th>Approval</th>
          </tr>
        </thead>
        <tbody>
          {pricePlans.map((plan) => (
            <tr key={plan.id} aria-selected={plan.id === selected}>
              <td><Link href={`/pricing?selected=${plan.id}&drawer=plan`}>{plan.id}</Link><br />{plan.plan}</td>
              <td>{plan.priceChange}</td>
              <td>{plan.expectedDemand}</td>
              <td>{plan.expectedRevenue}</td>
              <td>{plan.expectedGrossMargin}</td>
              <td><Badge label={plan.constraintStatus} tone={constraintTone[plan.constraintStatus]} marker="!" /></td>
              <td>
                <Badge label={plan.lifecycleStatus} tone={lifecycleTone[plan.lifecycleStatus]} marker="●" />
                <br />
                {plan.outcomeStatus}
              </td>
              <td>{plan.approvalStatus}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlanDrawer({ plan }: { plan: PricePlan }) {
  const blocked = plan.constraintStatus === "HARD_CONSTRAINT_FAILED";
  return (
    <aside className={styles.drawer} data-testid="priceops-drawer" aria-label={`${plan.id} detail`}>
      <div className={styles.badgeRow}>
        <Badge label={plan.constraintStatus} tone={constraintTone[plan.constraintStatus]} marker="!" />
        <Badge label={`risk ${plan.risk}`} tone={plan.risk === "high" ? "red" : plan.risk === "medium" ? "orange" : "green"} marker="●" />
        <Badge label={`Evidence ${plan.evidenceLevel}`} tone={plan.evidenceLevel === "low" ? "orange" : "purple"} marker="▧" />
      </div>
      <h2>{plan.id} · {plan.storeGroup}</h2>
      <section className={styles.twoColumn} data-testid="pricing-plan-comparison">
        <div className={styles.softBlock}>
          <h3>Current price</h3>
          <p>{plan.currentPrice}</p>
        </div>
        <div className={styles.softBlock}>
          <h3>Candidate price</h3>
          <p>{plan.candidatePrice}</p>
        </div>
      </section>
      <section className={blocked ? styles.warningBlock : styles.softBlock} data-testid="priceops-constraint">
        <h3>Constraint status</h3>
        <p>{plan.constraintDetail}</p>
        {blocked ? <p>Hard constraint failures cannot be approved.</p> : null}
      </section>
      <section id="rollback" className={styles.softBlock} data-testid="priceops-rollback">
        <h3>Rollback plan</h3>
        <p>{plan.rollbackPlan}</p>
        <p>{plan.rollbackTrigger}</p>
      </section>
      <ClosedLoopPanel plan={plan} />
      <ApprovalPanel plan={plan} blocked={blocked} />
    </aside>
  );
}

function ClosedLoopPanel({ plan }: { plan: PricePlan }) {
  return (
    <section
      className={plan.rollbackRecommended ? styles.warningBlock : styles.softBlock}
      data-testid="priceops-closed-loop"
    >
      <h3>Apply, monitor, outcome</h3>
      <ol className={styles.timeline}>
        <li>
          <strong>Apply</strong>
          <span>{plan.applyStatus}</span>
        </li>
        <li>
          <strong>Monitor</strong>
          <span>{plan.monitoringStatus}</span>
        </li>
        <li>
          <strong>Outcome</strong>
          <span>{plan.outcomeStatus}</span>
        </li>
      </ol>
      <p className={styles.auditLine}>
        publish_job {plan.publishJobId} · label_entry {plan.labelEntryId}
      </p>
    </section>
  );
}

function ApprovalPanel({ plan, blocked }: { plan: PricePlan; blocked: boolean }) {
  return (
    <section id="approval" className={styles.approvalPanel} data-testid="priceops-approval-panel">
      <h2>Price approval</h2>
      <p>{plan.reason}</p>
      <form>
        <label>
          Decision
          <select defaultValue={blocked ? "REQUEST_REVISION" : "APPROVE"}>
            <option>APPROVE</option>
            <option>REJECT</option>
            <option>REQUEST_REVISION</option>
          </select>
        </label>
        <label>
          Reason
          <textarea defaultValue="確認需求、毛利、硬限制與 rollback 後才提交；提交等待後端 decision_id，不做 optimistic update。" />
        </label>
        <label>
          <input defaultChecked type="checkbox" /> Risk acknowledged
        </label>
        <button className={blocked ? styles.secondaryButton : styles.primaryButton} disabled={blocked} type="button">
          核准此調價方案
        </button>
      </form>
      <p className={styles.auditLine}>
        decision_id {plan.decisionId} · correlation_id {plan.audit.correlationId} · model {plan.audit.modelVersion} · policy {plan.audit.policyVersion} · feature snapshot {plan.audit.featureSnapshotTime}
      </p>
    </section>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
