"use client";

import type { CSSProperties } from "react";
import { StatusBadge, type Tone } from "./components";
import type { WorkspaceId } from "./navigation";
import styles from "./operator.module.css";

export type ShellTarget = {
  entityId?: string;
  tab?: string;
  workspace: WorkspaceId;
};

export type ShellRole = {
  id: string;
  label: string;
  subtitle: string;
  allowedWorkspaces: WorkspaceId[];
  heroName?: string;
};

export type ShellWorkspace = {
  id: WorkspaceId;
  label: string;
  shortLabel: string;
  description: string;
  allowed?: boolean;
};

export type ShellMetric = {
  label: string;
  value: string;
  delta?: string;
  meta?: string;
  tone?: Tone;
};

export type ShellQueueItem = {
  id: string;
  title: string;
  description?: string;
  meta: string;
  owner: string;
  status: string;
  time: string;
  tone?: Tone;
  workspace: WorkspaceId;
  target: ShellTarget;
};

export type ShellDecision = {
  id: string;
  title: string;
  meta: string;
  status: string;
  cta: string;
  tone?: Tone;
  target: ShellTarget;
};

export type ShellRiskRow = {
  label: string;
  score: number;
  signal: string;
  tone?: Tone;
};

export type ShellAuditEvent = {
  actor: string;
  category: string;
  detail: string;
  time: string;
};

export type ShellNotification = {
  id?: string;
  title: string;
  detail: string;
  tone?: Tone;
  target?: ShellTarget;
};

export type ShellSearchItem = {
  id: string;
  entityId: string;
  label: string;
  description: string;
  keywords?: string;
  target: ShellTarget;
};

export type ShellCounts = {
  notifications: number;
  approvals: number;
  taskCenter: number;
  critical: number;
  search: number;
};

export type OperatorShellEnvelope = {
  meta: {
    generatedAt?: string;
    correlationId?: string | null;
    role: ShellRole;
    counts: ShellCounts;
    source?: string;
  };
  navigation: {
    roles: ShellRole[];
    workspaces: ShellWorkspace[];
    allowedWorkspaces: WorkspaceId[];
  };
  header: {
    counts: ShellCounts;
    taskCenter?: { label: string; count: number };
  };
  today: {
    hero: {
      name: string;
      roleLabel: string;
      scope: string;
      dateLabel: string;
    };
    kpis: ShellMetric[];
    queue: ShellQueueItem[];
    decisions: ShellDecision[];
    riskRows: ShellRiskRow[];
    auditFeed: ShellAuditEvent[];
  };
  search: {
    items: ShellSearchItem[];
    count: number;
  };
  notifications: ShellNotification[];
  approvals: ShellDecision[];
  workQueue: ShellQueueItem[];
  kpis: ShellMetric[];
  decisions: ShellDecision[];
  riskRows: ShellRiskRow[];
  auditFeed: ShellAuditEvent[];
};

const fallbackEnvelope: OperatorShellEnvelope = {
  meta: {
    role: {
      id: "ops-lead",
      label: "營運主管",
      subtitle: "全域監控、跨域指派與核准",
      allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
      heroName: "林承翰",
    },
    counts: {
      approvals: 0,
      critical: 0,
      notifications: 0,
      search: 0,
      taskCenter: 0,
    },
    source: "fallback",
  },
  navigation: {
    allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
    roles: [],
    workspaces: [],
  },
  header: {
    counts: {
      approvals: 0,
      critical: 0,
      notifications: 0,
      search: 0,
      taskCenter: 0,
    },
  },
  today: {
    hero: {
      dateLabel: "2026/07/05 ・週日",
      name: "林承翰",
      roleLabel: "營運主管",
      scope: "全品牌・12 門市・北北桃",
    },
    auditFeed: [],
    decisions: [],
    kpis: [],
    queue: [],
    riskRows: [],
  },
  approvals: [],
  auditFeed: [],
  decisions: [],
  kpis: [],
  notifications: [],
  riskRows: [],
  search: { count: 0, items: [] },
  workQueue: [],
};

const emptyEnvelope: OperatorShellEnvelope = {
  meta: {
    role: {
      id: "",
      label: "",
      subtitle: "",
      allowedWorkspaces: [],
    },
    counts: {
      approvals: 0,
      critical: 0,
      notifications: 0,
      search: 0,
      taskCenter: 0,
    },
  },
  navigation: {
    allowedWorkspaces: [],
    roles: [],
    workspaces: [],
  },
  header: {
    counts: {
      approvals: 0,
      critical: 0,
      notifications: 0,
      search: 0,
      taskCenter: 0,
    },
  },
  today: {
    hero: {
      dateLabel: "",
      name: "",
      roleLabel: "",
      scope: "",
    },
    auditFeed: [],
    decisions: [],
    kpis: [],
    queue: [],
    riskRows: [],
  },
  approvals: [],
  auditFeed: [],
  decisions: [],
  kpis: [],
  notifications: [],
  riskRows: [],
  search: { count: 0, items: [] },
  workQueue: [],
};

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null;
}

function normalizeTarget(value: unknown, fallbackWorkspace: WorkspaceId = "today"): ShellTarget {
  if (!isObject(value)) return { workspace: fallbackWorkspace };
  const workspace = typeof value.workspace === "string" ? (value.workspace as WorkspaceId) : fallbackWorkspace;
  return {
    entityId: typeof value.entityId === "string" ? value.entityId : undefined,
    tab: typeof value.tab === "string" ? value.tab : undefined,
    workspace,
  };
}

function normalizeRole(
  value: unknown,
  fallbackRole: ShellRole = fallbackEnvelope.meta.role,
): ShellRole {
  if (!isObject(value)) return fallbackRole;
  return {
    allowedWorkspaces: Array.isArray(value.allowedWorkspaces)
      ? (value.allowedWorkspaces.filter((item: unknown) => typeof item === "string") as WorkspaceId[])
      : fallbackRole.allowedWorkspaces,
    heroName: typeof value.heroName === "string" ? value.heroName : undefined,
    id: typeof value.id === "string" ? value.id : fallbackRole.id,
    label: typeof value.label === "string" ? value.label : fallbackRole.label,
    subtitle: typeof value.subtitle === "string" ? value.subtitle : "",
  };
}

function normalizeMetric(value: unknown): ShellMetric | null {
  if (!isObject(value) || typeof value.label !== "string") return null;
  return {
    delta: typeof value.delta === "string" ? value.delta : undefined,
    label: value.label,
    meta: typeof value.meta === "string" ? value.meta : undefined,
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
    value: String(value.value ?? ""),
  };
}

function normalizeQueueItem(value: unknown): ShellQueueItem | null {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.title !== "string") return null;
  const workspace = typeof value.workspace === "string" ? (value.workspace as WorkspaceId) : "today";
  return {
    description: typeof value.description === "string" ? value.description : undefined,
    id: value.id,
    meta: typeof value.meta === "string" ? value.meta : "",
    owner: typeof value.owner === "string" ? value.owner : "",
    status: typeof value.status === "string" ? value.status : "",
    target: normalizeTarget(value.target, workspace),
    time: typeof value.time === "string" ? value.time : "",
    title: value.title,
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
    workspace,
  };
}

function normalizeDecision(value: unknown): ShellDecision | null {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.title !== "string") return null;
  return {
    cta: typeof value.cta === "string" ? value.cta : "Open",
    id: value.id,
    meta: typeof value.meta === "string" ? value.meta : "",
    status: typeof value.status === "string" ? value.status : "",
    target: normalizeTarget(value.target, "govern"),
    title: value.title,
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
  };
}

function normalizeRisk(value: unknown): ShellRiskRow | null {
  if (!isObject(value) || typeof value.label !== "string") return null;
  return {
    label: value.label,
    score: Number(value.score ?? 0),
    signal: typeof value.signal === "string" ? value.signal : "",
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
  };
}

function normalizeAudit(value: unknown): ShellAuditEvent | null {
  if (!isObject(value) || typeof value.detail !== "string") return null;
  return {
    actor: typeof value.actor === "string" ? value.actor : "system",
    category: typeof value.category === "string" ? value.category : "Audit",
    detail: value.detail,
    time: typeof value.time === "string" ? value.time : "",
  };
}

function normalizeNotification(value: unknown): ShellNotification | null {
  if (!isObject(value) || typeof value.title !== "string") return null;
  return {
    detail: typeof value.detail === "string" ? value.detail : "",
    id: typeof value.id === "string" ? value.id : value.title,
    target: isObject(value.target) ? normalizeTarget(value.target) : undefined,
    title: value.title,
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
  };
}

function normalizeSearch(value: unknown): ShellSearchItem | null {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.label !== "string") return null;
  return {
    description: typeof value.description === "string" ? value.description : "",
    entityId: typeof value.entityId === "string" ? value.entityId : value.id,
    id: value.id,
    keywords: typeof value.keywords === "string" ? value.keywords : undefined,
    label: value.label,
    target: normalizeTarget(value.target),
  };
}

function normalizeList<T>(value: unknown, normalizer: (item: unknown) => T | null): T[] {
  if (!Array.isArray(value)) return [];
  return value.map(normalizer).filter((item): item is T => item !== null);
}

export function normalizeShellEnvelope(
  payload?: unknown,
  options: { allowFixtureFallback?: boolean } = {},
): OperatorShellEnvelope {
  const baseEnvelope =
    options.allowFixtureFallback === false ? emptyEnvelope : fallbackEnvelope;
  if (!isObject(payload)) return baseEnvelope;
  const role = normalizeRole(
    isObject(payload.meta) ? payload.meta.role : undefined,
    baseEnvelope.meta.role,
  );
  const counts = isObject(payload.meta) && isObject(payload.meta.counts) ? payload.meta.counts : {};
  const today = isObject(payload.today) ? payload.today : {};
  const navigation = isObject(payload.navigation) ? payload.navigation : {};
  const header = isObject(payload.header) ? payload.header : {};
  const headerCounts = isObject(header.counts) ? header.counts : counts;
  const kpis = normalizeList(today.kpis ?? payload.kpis, normalizeMetric);
  const queue = normalizeList(today.queue ?? payload.workQueue, normalizeQueueItem);
  const decisions = normalizeList(today.decisions ?? payload.decisions, normalizeDecision);
  const riskRows = normalizeList(today.riskRows ?? payload.riskRows, normalizeRisk);
  const auditFeed = normalizeList(today.auditFeed ?? payload.auditFeed, normalizeAudit);
  const notifications = normalizeList(payload.notifications, normalizeNotification);
  const searchItems = normalizeList(isObject(payload.search) ? payload.search.items : [], normalizeSearch);
  const normalizedCounts = {
    approvals: Number(headerCounts.approvals ?? counts.approvals ?? decisions.length),
    critical: Number(headerCounts.critical ?? counts.critical ?? queue.filter((item) => item.tone === "danger").length),
    notifications: Number(headerCounts.notifications ?? counts.notifications ?? notifications.length),
    search: Number(headerCounts.search ?? counts.search ?? searchItems.length),
    taskCenter: Number(headerCounts.taskCenter ?? counts.taskCenter ?? queue.length),
  };

  return {
    approvals: decisions,
    auditFeed,
    decisions,
    header: {
      counts: normalizedCounts,
      taskCenter: isObject(header.taskCenter)
        ? { count: Number(header.taskCenter.count ?? normalizedCounts.taskCenter), label: String(header.taskCenter.label ?? "Task Center") }
        : { count: normalizedCounts.taskCenter, label: "Task Center" },
    },
    kpis,
    meta: {
      correlationId: isObject(payload.meta) && typeof payload.meta.correlationId === "string" ? payload.meta.correlationId : null,
      counts: normalizedCounts,
      generatedAt: isObject(payload.meta) && typeof payload.meta.generatedAt === "string" ? payload.meta.generatedAt : undefined,
      role,
      source: isObject(payload.meta) && typeof payload.meta.source === "string" ? payload.meta.source : undefined,
    },
    navigation: {
      allowedWorkspaces: Array.isArray(navigation.allowedWorkspaces)
        ? (navigation.allowedWorkspaces.filter((item: unknown) => typeof item === "string") as WorkspaceId[])
        : role.allowedWorkspaces,
      roles: normalizeList(navigation.roles, (item) =>
        normalizeRole(item, baseEnvelope.meta.role),
      ),
      workspaces: normalizeList(navigation.workspaces, (item) => {
        if (!isObject(item) || typeof item.id !== "string") return null;
        return {
          allowed: typeof item.allowed === "boolean" ? item.allowed : role.allowedWorkspaces.includes(item.id as WorkspaceId),
          description: typeof item.description === "string" ? item.description : "",
          id: item.id as WorkspaceId,
          label: typeof item.label === "string" ? item.label : item.id,
          shortLabel: typeof item.shortLabel === "string" ? item.shortLabel : item.id,
        };
      }),
    },
    notifications,
    riskRows,
    search: { count: searchItems.length, items: searchItems },
    today: {
      auditFeed,
      decisions,
      hero: {
        dateLabel: isObject(today.hero) && typeof today.hero.dateLabel === "string" ? today.hero.dateLabel : baseEnvelope.today.hero.dateLabel,
        name: isObject(today.hero) && typeof today.hero.name === "string" ? today.hero.name : role.heroName ?? baseEnvelope.today.hero.name,
        roleLabel: isObject(today.hero) && typeof today.hero.roleLabel === "string" ? today.hero.roleLabel : role.label,
        scope: isObject(today.hero) && typeof today.hero.scope === "string" ? today.hero.scope : baseEnvelope.today.hero.scope,
      },
      kpis,
      queue,
      riskRows,
    },
    workQueue: queue,
  };
}

export function TodayWorkspace({
  envelope,
  onApprovalDecision,
  onTargetSelect,
}: {
  envelope: OperatorShellEnvelope;
  onApprovalDecision: (approvalId: string, status: "approved" | "rejected" | "returned", payload: Record<string, string>) => void;
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
            <article className={styles.todayKpiCard} data-tone={metric.tone ?? "neutral"} key={metric.label}>
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
                        <strong>{item.title}</strong>
                      </span>
                      <span className={styles.todayQueueContext}>
                        {item.description ? <span>{item.description}</span> : null}
                        {item.meta ? <span>{item.meta}</span> : null}
                      </span>
                    </span>
                    <span className={styles.todayQueueState}>
                      {item.status ? <StatusBadge tone={item.tone}>{item.status}</StatusBadge> : null}
                      {item.time ? <time>{item.time}</time> : null}
                    </span>
                    <span className={styles.todayQueueOwner}>
                      <small>Owner</small>
                      <strong>{item.owner || "未指派"}</strong>
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
                        <StatusBadge tone={decision.tone}>{decision.status}</StatusBadge>
                        <small>{decision.meta}</small>
                      </span>
                      <strong>{decision.title}</strong>
                    </button>
                    <button
                      aria-label="核准"
                      className={styles.todayDecisionAction}
                      onClick={() =>
                        onApprovalDecision(decision.id, "approved", {
                          actorName: today.hero.name,
                          actorRoleId: envelope.meta.role.id,
                          reason: `Approved from Today rail for ${decision.id}`,
                        })
                      }
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
                        aria-label={`${row.label} 風險分數 ${row.score}`}
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
                      <strong>{row.label}</strong>
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
                    <time>{event.time}</time>
                    <p>
                      <strong>{event.actor}</strong> {event.detail} <b>{event.category}</b>
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
