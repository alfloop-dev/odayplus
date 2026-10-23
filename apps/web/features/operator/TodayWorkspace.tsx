"use client";

import type { CSSProperties } from "react";
import { StatusBadge } from "./components";
import { STORE_OPS_STATUS_LABELS } from "./storeOpsViewModel";
import type { OperatorShellEnvelope, ShellAuditEvent, ShellTarget } from "./shellEnvelope";
import styles from "./operator.module.css";

// Preserve the existing public imports while keeping Console's data boundary pure.
export { normalizeShellEnvelope } from "./shellEnvelope";
export type { ShellTarget, ShellRole, ShellWorkspace, ShellMetric, ShellQueueItem, ShellDecision, ShellRiskRow, ShellAuditEvent, ShellNotification, ShellSearchItem, ShellCounts, OperatorShellEnvelope } from "./shellEnvelope";

const auditTimeFormat = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function auditTimeLabel(value: string): string {
  // Package 10's activity rail uses a compact clock label. Live audit records
  // carry ISO instants; preserve those instants on <time> rather than allowing
  // an unbounded timestamp column to squeeze the event into a vertical strip.
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return value;
  const instant = new Date(value);
  return Number.isNaN(instant.getTime()) ? value : auditTimeFormat.format(instant);
}

const interventionStatusLabels: Record<string, string> = {
  CANDIDATE: "候選", ELIGIBILITY_CHECKING: "適用性檢查中", ELIGIBLE: "符合適用條件",
  INELIGIBLE: "不符適用條件", ACTION_PROPOSED: "已提出處置", CONFLICT_CHECKING: "衝突檢查中",
  PENDING_APPROVAL: "待核准", APPROVED: "已核准", REJECTED: "已駁回", EXECUTING: "執行中",
  OBSERVING: "觀察中", EVALUATING: "評估中", COMPLETED: "已完成", CLOSED: "已結束",
  STOPPED: "已停止", ROLLED_BACK: "已回復",
};

const interventionKindLabels: Record<string, string> = {
  PRICE_CHANGE: "價格調整", AD_CAMPAIGN: "廣告活動", PROMOTION: "促銷活動", CRM_RECALL: "顧客回流",
  MAINTENANCE: "設備維護", CLEANING: "清潔", OPENING_CAMPAIGN: "開幕活動", EXTERNAL_SHOCK: "外部衝擊",
};

function statusLabel(value: string): string {
  if (Object.hasOwn(STORE_OPS_STATUS_LABELS, value)) return STORE_OPS_STATUS_LABELS[value as keyof typeof STORE_OPS_STATUS_LABELS];
  return Object.hasOwn(interventionStatusLabels, value) ? interventionStatusLabels[value] : value;
}

function interventionLabel(value: string): string {
  const kind = /^(\w+) intervention$/.exec(value)?.[1];
  return kind && Object.hasOwn(interventionKindLabels, kind) ? `${interventionKindLabels[kind]}干預` : value;
}

function subjectLabel(value: string): string {
  // A subject identifier is not a person's display name. Keep the full ID in
  // the title while making the queue and activity rail readable.
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
    ? `帳號 ${value.slice(0, 4)}…${value.slice(-4)}` : value;
}

function queueTimeLabel(value: string): string {
  // Preserve the source offset; this display does not choose a business timezone
  // or recompute an SLA from an unrelated created-at field.
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts || Number.isNaN(new Date(value).getTime())) return value;
  const zone = parts[6] === "Z" || parts[6] === "+00:00" ? "UTC" : `UTC${parts[6]}`;
  return `${parts[2]}/${parts[3]} ${parts[4]}:${parts[5]} ${zone}`;
}

function auditCategoryLabel(value: string): string {
  if (value === "security.authorization") return "權限檢查";
  return value === "security.authentication" ? "身分驗證" : value;
}

function auditDetailLabel(event: ShellAuditEvent): string {
  // Authorization to approve is not a completed business approval. Only map
  // these exact security-event forms, retaining every original value in title.
  if (event.category === "security.authentication" && event.detail === "authenticate auth/local: success") return "本機登入驗證成功";
  if (event.category !== "security.authorization") return event.detail;
  const parts = /^(view|approve) (operator_console|intervention(?:\/[^: ]+)?): (allow|deny)$/.exec(event.detail);
  if (!parts) return event.detail;
  const action = parts[1] === "approve" ? "核准" : "檢視";
  const resource = parts[2] === "operator_console" ? "營運工作台" : "干預";
  return `${resource}${action}權限：${parts[3] === "allow" ? "允許" : "拒絕"}`;
}

export function TodayWorkspace({
  envelope,
  onTargetSelect,
}: {
  envelope: OperatorShellEnvelope;
  /** Compatibility for callers; Home decisions always enter the full owner review. */
  onApprovalDecision?: (approvalId: string, status: "approved" | "rejected" | "returned", payload: Record<string, string>) => void;
  onTargetSelect: (target: ShellTarget, label: string) => void;
}) {
  const today = envelope.today;
  const greeting = today.hero.name
    ? `早安，${today.hero.name}${today.hero.roleLabel ? ` — ${today.hero.roleLabel}` : ""}`
    : "今日工作";

  return (
    <div
      className={styles.todayWorkspaceApi}
      data-screen-label="Today 今日工作"
      data-testid="operator-today-workspace"
      data-visual-layout="package-10-r7"
    >
      <header className={styles.todayHero}>
        <div>
          <h1>{greeting}</h1>
          {today.hero.scope ? <p>資料範圍：{today.hero.scope}</p> : null}
        </div>
        <div className={styles.todayHeroMeta}>
          {today.hero.dateLabel ? <time>{today.hero.dateLabel}</time> : null}
          {today.hero.roleLabel ? <strong>目前視角：{today.hero.roleLabel}</strong> : null}
          <span className={styles.todaySourceMetadata} data-testid="operator-envelope-source">
            {envelope.meta.source ?? "api"}
          </span>
        </div>
      </header>

      <section
        className={styles.todayKpiGrid}
        aria-label="Today KPI cards"
        data-testid="operator-today-kpis"
        data-visual-layout="six-column-kpi"
      >
        {today.kpis.length ? (
          today.kpis.map((metric) => (
            <article className={styles.todayKpiCard} data-tone={metric.tone ?? "neutral"} data-availability={metric.availability} key={metric.label}>
              <span className={styles.todayKpiLabel}>
                <i aria-hidden="true" />
                {metric.label}
              </span>
              <strong>{metric.value || "—"}</strong>
              {metric.delta || metric.meta ? (
                <small>
                  {metric.delta ? <b>{metric.delta}</b> : null}
                  {metric.meta}
                </small>
              ) : null}
            </article>
          ))
        ) : (
          <div className={styles.todayEmptyState} data-testid="operator-today-kpis-empty">
            目前沒有可顯示的 KPI 資料。
          </div>
        )}
      </section>

      <div className={styles.todayGrid} data-visual-layout="main-rail">
        <div className={styles.todayPrimary}>
          <section className={styles.todayQueuePanel} aria-labelledby="today-queue-title">
            <header className={styles.todayPanelHeader}>
              <div>
                <h2 id="today-queue-title">今天最需要處理</h2>
                <span>依嚴重度與 SLA 排序</span>
              </div>
              <strong>{today.queue.length} 項</strong>
            </header>
            <div className={styles.queueList} data-testid="operator-today-queue">
              {today.queue.length ? (
                today.queue.map((item) => (
                  <button
                    className={styles.todayQueueRow}
                    data-target-entity={item.target.entityId}
                    data-target-tab={item.target.tab}
                    data-target-workspace={item.target.workspace}
                    key={item.id}
                    onClick={() => onTargetSelect(item.target, item.id)}
                    type="button"
                  >
                    <i aria-hidden="true" className={styles.todayToneDot} data-tone={item.tone ?? "neutral"} />
                    <span className={styles.todayQueueMain}>
                      <span className={styles.todayQueueTitle}>
                        <small>{item.id}</small>
                        <strong title={item.title}>{interventionLabel(item.title)}</strong>
                      </span>
                      <span className={styles.todayQueueContext}>
                        {item.description ? <span>{item.description}</span> : null}
                        {item.meta ? <span>{item.meta}</span> : null}
                      </span>
                    </span>
                    <span className={styles.todayQueueState}>
                      {item.status ? <StatusBadge tone={item.tone}>{statusLabel(item.status)}</StatusBadge> : null}
                      {item.time ? <time dateTime={queueTimeLabel(item.time) === item.time ? undefined : item.time} title={item.time}>{queueTimeLabel(item.time)}</time> : null}
                    </span>
                    <span className={styles.todayQueueOwner}>
                      <small>Owner</small>
                      <strong title={item.owner}>{subjectLabel(item.owner) || "未指派"}</strong>
                    </span>
                    <span className={styles.todayQueueCta}>前往處理 →</span>
                  </button>
                ))
              ) : (
                <div className={styles.todayEmptyState} data-testid="operator-today-queue-empty">
                  今天沒有需要立即處理的項目，佇列已清空。
                </div>
              )}
            </div>
          </section>
        </div>

        <aside className={styles.todayRail} aria-label="Today decision and risk rail">
          <section className={styles.todayRailPanel} data-today-rail-section="decisions">
            <header className={styles.todayRailHeader}>
              <h2>需要你決策</h2>
            </header>
            <div className={styles.decisionList} data-testid="operator-decision-rail">
              {today.decisions.length ? (
                today.decisions.map((decision) => (
                  <article
                    className={styles.todayDecisionCard}
                    data-target-entity={decision.target.entityId}
                    data-target-tab={decision.target.tab}
                    data-target-workspace={decision.target.workspace}
                    key={decision.id}
                  >
                    <button
                      className={styles.todayDecisionOpen}
                      onClick={() => onTargetSelect(decision.target, decision.id)}
                      type="button"
                    >
                      <span className={styles.todayDecisionTopline}>
                        <StatusBadge tone={decision.tone}>{statusLabel(decision.status)}</StatusBadge>
                        <small title={decision.meta}>{Object.hasOwn(interventionKindLabels, decision.meta) ? interventionKindLabels[decision.meta] : decision.meta}</small>
                      </span>
                      <strong title={decision.title}>{decision.title === "Intervention approval" ? "干預核准" : decision.title}</strong>
                    </button>
                    <button
                      aria-label="核准"
                      className={styles.todayDecisionAction}
                      onClick={() => onTargetSelect(decision.target, decision.id)}
                      type="button"
                    >
                      {decision.cta || "進行核准"} →
                    </button>
                  </article>
                ))
              ) : (
                <div className={styles.todayRailEmpty} data-testid="operator-decisions-empty">
                  目前沒有等待你決策的項目。
                </div>
              )}
            </div>
          </section>

          <section className={styles.todayRailPanel} data-today-rail-section="risk">
            <header className={styles.todayRailHeader}>
              <h2>門市風險快照</h2>
              <span>{today.riskRows.length} 門市</span>
            </header>
            {today.riskRows.length ? (
              <>
                <div className={styles.todayRiskPlot} aria-label="門市風險分布">
                  {today.riskRows.map((row, index) => {
                    const x = Math.max(12, Math.min(88, row.score));
                    const y = 22 + (index % 4) * 19;
                    return (
                      <i
                        aria-label={`${interventionLabel(row.label)} 風險分數 ${row.score}`}
                        data-tone={row.tone ?? "neutral"}
                        key={row.label}
                        role="img"
                        style={
                          {
                            "--today-risk-x": `${x}%`,
                            "--today-risk-y": `${y}%`,
                          } as CSSProperties
                        }
                      />
                    );
                  })}
                </div>
                <div className={styles.todayRiskList} data-testid="operator-risk-snapshot">
                  {today.riskRows.map((row) => (
                    <div key={row.label}>
                      <i aria-hidden="true" data-tone={row.tone ?? "neutral"} />
                      <strong title={row.label}>{interventionLabel(row.label)}</strong>
                      <span>{row.signal}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className={styles.todayRailEmpty} data-testid="operator-risk-empty">
                目前沒有可顯示的門市風險資料。
              </div>
            )}
          </section>

          <section className={styles.todayRailPanel} data-today-rail-section="audit">
            <header className={styles.todayRailHeader}>
              <h2>
                最近動態 <span>AUDIT FEED</span>
              </h2>
            </header>
            <div className={styles.todayAuditList} data-testid="operator-audit-feed">
              {today.auditFeed.length ? (
                today.auditFeed.map((event) => (
                  <article key={`${event.time}-${event.category}-${event.detail}`}>
                    <time dateTime={event.time} title={event.time}>{auditTimeLabel(event.time)}</time>
                    <p>
                      <strong title={event.actor}>{subjectLabel(event.actor)}</strong>{" "}
                      <span title={event.detail}>{auditDetailLabel(event)}</span>{" "}
                      <b title={event.category}>{auditCategoryLabel(event.category)}</b>
                    </p>
                  </article>
                ))
              ) : (
                <div className={styles.todayRailEmpty} data-testid="operator-audit-empty">
                  目前沒有最近動態。
                </div>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
