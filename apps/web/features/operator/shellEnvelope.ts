// Shell response normalization has no runtime UI or owner imports.
import type { Tone } from "./components";
import type { WorkspaceId } from "./navigation";

export type ShellTarget = {
  entityId?: string;
  candidateId?: string;
  reviewId?: string;
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
  availability?: "available" | "degraded" | "unavailable";
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
    allowedTabs?: { network: string[] };
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
    ...(workspace === "network" && value.tab === "review" && typeof value.candidateId === "string" && typeof value.reviewId === "string"
      ? { candidateId: value.candidateId, reviewId: value.reviewId } : {}),
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
    availability: value.availability === "available" || value.availability === "degraded" || value.availability === "unavailable" ? value.availability : undefined,
    delta: typeof value.delta === "string" ? value.delta : undefined,
    label: value.label,
    meta: typeof value.meta === "string" ? value.meta : undefined,
    tone: typeof value.tone === "string" ? (value.tone as Tone) : "neutral",
    value: value.availability === "unavailable" ? "" : String(value.value ?? ""),
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
      allowedTabs: {
        network: isObject(navigation.allowedTabs) && Array.isArray(navigation.allowedTabs.network)
          ? navigation.allowedTabs.network.filter((tab: unknown): tab is string => typeof tab === "string")
          : [],
      },
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

