"use client";

import { Button, Chip, EvidenceCard, MetricCard, QueueRow, SectionPanel, StatusBadge, type Tone } from "./components";
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

function normalizeRole(value: unknown): ShellRole {
  if (!isObject(value)) return fallbackEnvelope.meta.role;
  return {
    allowedWorkspaces: Array.isArray(value.allowedWorkspaces)
      ? (value.allowedWorkspaces.filter((item: unknown) => typeof item === "string") as WorkspaceId[])
      : fallbackEnvelope.meta.role.allowedWorkspaces,
    heroName: typeof value.heroName === "string" ? value.heroName : undefined,
    id: typeof value.id === "string" ? value.id : fallbackEnvelope.meta.role.id,
    label: typeof value.label === "string" ? value.label : fallbackEnvelope.meta.role.label,
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

export function normalizeShellEnvelope(payload?: unknown): OperatorShellEnvelope {
  if (!isObject(payload)) return fallbackEnvelope;
  const role = normalizeRole(isObject(payload.meta) ? payload.meta.role : undefined);
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
      roles: normalizeList(navigation.roles, normalizeRole),
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
        dateLabel: isObject(today.hero) && typeof today.hero.dateLabel === "string" ? today.hero.dateLabel : fallbackEnvelope.today.hero.dateLabel,
        name: isObject(today.hero) && typeof today.hero.name === "string" ? today.hero.name : role.heroName ?? fallbackEnvelope.today.hero.name,
        roleLabel: isObject(today.hero) && typeof today.hero.roleLabel === "string" ? today.hero.roleLabel : role.label,
        scope: isObject(today.hero) && typeof today.hero.scope === "string" ? today.hero.scope : fallbackEnvelope.today.hero.scope,
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

  return (
    <div className={styles.todayWorkspaceApi} data-screen-label="Today 今日工作" data-testid="operator-today-workspace">
      <header className={styles.todayHero}>
        <div>
          <h1>
            早安，{today.hero.name} — {today.hero.roleLabel}
          </h1>
          <p>{today.hero.scope}</p>
        </div>
        <div className={styles.todayHeroMeta}>
          <span>{today.hero.dateLabel}</span>
          <strong data-testid="operator-envelope-source">{envelope.meta.source ?? "api"}</strong>
        </div>
      </header>

      <section className={styles.metricGrid} aria-label="Today KPI cards" data-testid="operator-today-kpis">
        {today.kpis.map((metric) => (
          <MetricCard
            delta={metric.delta}
            key={metric.label}
            label={metric.label}
            meta={metric.meta}
            tone={metric.tone}
            value={metric.value}
          />
        ))}
      </section>

      <div className={styles.todayGrid}>
        <div className={styles.todayPrimary}>
          <SectionPanel
            actions={<Chip tone={envelope.meta.counts.critical ? "danger" : "success"}>{envelope.meta.counts.taskCenter} 項</Chip>}
            eyebrow="Role-aware queue"
            title="今天最需要處理"
          >
            <div className={styles.queueList} data-testid="operator-today-queue">
              {today.queue.map((item) => (
                <QueueRow
                  description={item.description}
                  id={item.id}
                  key={item.id}
                  meta={item.meta}
                  onClick={() => onTargetSelect(item.target, item.id)}
                  owner={item.owner}
                  status={item.status}
                  targetEntityId={item.target.entityId}
                  targetTab={item.target.tab}
                  targetWorkspace={item.target.workspace}
                  time={item.time}
                  title={item.title}
                  tone={item.tone}
                />
              ))}
            </div>
          </SectionPanel>

          <div className={styles.lowerGrid}>
            <SectionPanel eyebrow="Store signal" title="Risk snapshot">
              <div className={styles.riskList}>
                {today.riskRows.map((row) => (
                  <div className={styles.apiRiskRow} data-tone={row.tone} key={row.label}>
                    <span>
                      <strong>{row.label}</strong>
                      <small>{row.signal}</small>
                    </span>
                    <b>{row.score}</b>
                  </div>
                ))}
              </div>
            </SectionPanel>

            <SectionPanel eyebrow="Evidence health" title="Operational signals">
              <div className={styles.evidenceGrid}>
                <EvidenceCard label="Data freshness" tone="success" value="06:00">
                  ForecastOps, payment, review connectors refreshed.
                </EvidenceCard>
                <EvidenceCard label="Camera privacy" tone="warning" value="Locked">
                  Purpose flow required before any media surface.
                </EvidenceCard>
                <EvidenceCard label="Model confidence" tone="info" value="0.84">
                  {envelope.meta.counts.search} deep-linked entities indexed.
                </EvidenceCard>
              </div>
            </SectionPanel>
          </div>
        </div>

        <aside className={styles.todayRail}>
          <SectionPanel
            actions={<Chip tone={envelope.meta.counts.approvals ? "warning" : "success"}>{envelope.meta.counts.approvals}</Chip>}
            eyebrow="Approval center"
            title="需要你決策"
          >
            <div className={styles.decisionList} data-testid="operator-decision-rail">
              {today.decisions.map((decision) => (
                <article
                  className={styles.decisionCard}
                  data-target-entity={decision.target.entityId}
                  data-target-tab={decision.target.tab}
                  data-target-workspace={decision.target.workspace}
                  key={decision.id}
                >
                  <div className={styles.decisionTopline}>
                    <span>{decision.id}</span>
                    <StatusBadge tone={decision.tone}>{decision.status}</StatusBadge>
                  </div>
                  <h3>{decision.title}</h3>
                  <p>{decision.meta}</p>
                  <div className={styles.decisionActions}>
                    <Button onClick={() => onTargetSelect(decision.target, decision.id)} size="sm" variant="secondary">
                      {decision.cta}
                    </Button>
                    <Button
                      onClick={() =>
                        onApprovalDecision(decision.id, "approved", {
                          actorName: today.hero.name,
                          actorRoleId: envelope.meta.role.id,
                          reason: `Approved from Today rail for ${decision.id}`,
                        })
                      }
                      size="sm"
                      variant="primary"
                    >
                      核准
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          </SectionPanel>

          <SectionPanel eyebrow="Traceability" title="最近動態">
            <div className={styles.auditList}>
              {today.auditFeed.map((event) => (
                <article className={styles.apiAuditRow} key={`${event.time}-${event.category}-${event.detail}`}>
                  <div>
                    <strong>{event.category}</strong>
                    <time>{event.time}</time>
                  </div>
                  <p>{event.detail}</p>
                  <span>{event.actor}</span>
                </article>
              ))}
            </div>
          </SectionPanel>
        </aside>
      </div>
    </div>
  );
}
