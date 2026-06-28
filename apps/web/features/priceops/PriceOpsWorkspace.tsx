import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import { dataStatusTone } from "@oday-plus/domain-types";
import { constraintTone, freshness, pricePlans, type PricePlan } from "./data.ts";
import styles from "../intervention/intervention.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

export function PriceOpsWorkspace({ searchParams = {} }: { searchParams?: SearchParams }) {
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
            <Summary title="Rollback ready" value="2" copy="publish requires rollback plan and audit" />
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
      </section>
      <ApprovalPanel plan={plan} blocked={blocked} />
    </aside>
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
