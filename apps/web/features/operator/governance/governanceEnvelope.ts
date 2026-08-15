/**
 * Govern workspace external-row admission — ODP-P10-CAN-001-R3C.
 *
 * The Govern workspace consumes rows from two independent producers:
 *
 *   1. `/api/v1/operator/governance/snapshot` — the governance source of truth.
 *   2. `/api/v1/operator/bootstrap` — the Operator shell envelope, which may
 *      carry governance-shaped side channels (`governanceApprovals`,
 *      `governanceDecisions`, `governanceAuditRows`).
 *
 * Neither producer is typed at runtime, and the shell envelope's own
 * `approvals` field is an alias of the Today decision cards
 * (`{ id, title, meta, status, cta, tone, target }`) rather than
 * `GovernanceApproval` rows.  Feeding those cards into the workspace made the
 * module/status badge helpers call `.toLowerCase()` on missing fields and take
 * the whole Govern route down once a delayed shell envelope landed.
 *
 * These functions are an admission gate, not a repair shop:
 *
 *   - a record that does not carry the identifying governance fields is a
 *     foreign or malformed row and is dropped;
 *   - a field that is present and usable is carried through verbatim;
 *   - a field that is absent or unusable stays absent — no module, requestor,
 *     time, risk or status value is ever invented, because a fabricated field
 *     would be indistinguishable from governance truth downstream.
 *
 * Absent fields are surfaced by the workspace as an explicit unavailable
 * marker, so a partial payload degrades visibly instead of crashing or
 * pretending to be complete.
 *
 * Owned by: ODP-P10-CAN-001-R3C
 * Composes with: apps/web/features/operator/GovernanceWorkspace.tsx,
 *                apps/web/features/operator/OperatorConsole.tsx
 */
import type {
  GovernanceApproval,
  GovernanceAuditRow,
  GovernanceDecisionRow,
  GovernanceEvidence,
} from "../governanceTypes";
import type {
  GovernanceEvidencePackage,
  GovernanceStatusBoard,
  GovernanceStatusRow,
} from "./governanceLoader";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/**
 * A usable text field: a string or a finite number, trimmed and non-empty.
 * Everything else (objects, arrays, booleans, null, undefined, blank strings)
 * is unusable and reported as `undefined` so the caller can drop the row or the
 * field rather than substitute a value for it.
 */
function usableText(value: unknown): string | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

/** Spread helper: `{ ...field("risk", record.risk) }` keeps absent fields absent. */
function field<K extends string>(key: K, value: unknown): Partial<Record<K, string>> {
  const normalized = usableText(value);
  return normalized === undefined ? {} : ({ [key]: normalized } as Record<K, string>);
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function normalizeList<T>(value: unknown, normalize: (item: unknown) => T | null): T[] {
  return asArray(value)
    .map((item) => normalize(item))
    .filter((item): item is T => item !== null);
}

/** Evidence entries need an id and a label to be selectable and readable. */
function normalizeEvidence(value: unknown): GovernanceEvidence | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = usableText(record.id);
  const label = usableText(record.label);
  if (!id || !label) return null;
  return {
    id,
    label,
    ...field("type", record.type),
    ...field("href", record.href),
    ...field("state", record.state),
  };
}

/**
 * Admit one governance approval.  A record must identify itself (`id`), state
 * which governance module owns it (`module`), name the item under review
 * (`title`) and report its approval state (`status`).  Shell decision cards
 * carry no `module`, so they are rejected here instead of being rendered as
 * governance approvals with an invented module.
 */
export function normalizeGovernanceApproval(value: unknown): GovernanceApproval | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = usableText(record.id);
  const module = usableText(record.module);
  const title = usableText(record.title);
  const status = usableText(record.status);
  if (!id || !module || !title || !status) return null;

  return {
    id,
    module,
    title,
    status,
    ...field("requestor", record.requestor),
    ...field("submittedAt", record.submittedAt),
    ...field("priority", record.priority),
    ...field("owner", record.owner),
    ...field("sla", record.sla),
    ...field("entityRef", record.entityRef),
    ...field("summary", record.summary),
    ...field("systemRecommendation", record.systemRecommendation),
    ...field("risk", record.risk),
    ...field("roleNote", record.roleNote),
    ...field("reason", record.reason),
    ...(Array.isArray(record.evidence)
      ? { evidence: normalizeList(record.evidence, normalizeEvidence) }
      : {}),
  };
}

export function normalizeGovernanceApprovals(value: unknown): GovernanceApproval[] {
  return normalizeList(value, normalizeGovernanceApproval);
}

/**
 * Admit one Decision Log row.  Identity is `id` + `module` + `item`; the
 * decision content is carried through only when it is present and textual, so
 * a structured `systemRecommendation` cannot reach the render path.
 */
export function normalizeGovernanceDecisionRow(value: unknown): GovernanceDecisionRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = usableText(record.id);
  const module = usableText(record.module);
  const item = usableText(record.item);
  if (!id || !module || !item) return null;

  return {
    id,
    module,
    item,
    ...field("systemRecommendation", record.systemRecommendation),
    ...field("finalDecision", record.finalDecision),
    ...field("reason", record.reason),
    ...field("actor", record.actor),
    ...field("decidedAt", record.decidedAt),
    ...field("model", record.model),
    ...field("datasetSnapshot", record.datasetSnapshot),
    ...field("approvalId", record.approvalId),
  };
}

export function normalizeGovernanceDecisionRows(value: unknown): GovernanceDecisionRow[] {
  return normalizeList(value, normalizeGovernanceDecisionRow);
}

/**
 * Admit one Audit Trail row.  An audit entry must identify itself (`id`) and
 * say what happened (`action`).  Canonical Governance API transitions timestamp
 * with `occurredAt` and carry no category, so `timestamp` accepts that alias
 * and an absent category is left absent rather than defaulted to "system".
 */
export function normalizeGovernanceAuditRow(value: unknown): GovernanceAuditRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = usableText(record.id);
  const action = usableText(record.action);
  if (!id || !action) return null;

  return {
    id,
    action,
    ...field("category", record.category),
    ...field("timestamp", record.timestamp ?? record.occurredAt),
    ...field("actor", record.actor),
    ...field("module", record.module),
    ...field("entityRef", record.entityRef),
    ...field("summary", record.summary),
    ...field("reason", record.reason),
    ...field("correlationId", record.correlationId),
  };
}

export function normalizeGovernanceAuditRows(value: unknown): GovernanceAuditRow[] {
  return normalizeList(value, normalizeGovernanceAuditRow);
}

/**
 * Admit one status-board row.  A row is meaningful only as a named subject with
 * a reported state, so `status`, the boolean health flag and the note must all
 * be present; a row missing any of them is dropped instead of shown as healthy
 * or unhealthy on invented evidence.
 */
function normalizeStatusRow(value: unknown): GovernanceStatusRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const source = usableText(record.source);
  const name = usableText(record.name);
  const status = usableText(record.status);
  if ((!source && !name) || !status) return null;
  if (typeof record.good !== "boolean" || typeof record.note !== "string") return null;
  return {
    ...(source ? { source } : {}),
    ...(name ? { name } : {}),
    ...field("version", record.version),
    status,
    good: record.good,
    note: record.note,
  };
}

function normalizeStatusRows(value: unknown): GovernanceStatusRow[] {
  return normalizeList(value, normalizeStatusRow);
}

/**
 * Normalize the status board.  Returns null when the payload carries no usable
 * board so the workspace keeps treating it as unavailable rather than rendering
 * an invented one.
 */
export function normalizeGovernanceStatusBoard(value: unknown): GovernanceStatusBoard | null {
  const record = asRecord(value);
  if (!record) return null;
  return {
    dataQuality: normalizeStatusRows(record.dataQuality),
    models: normalizeStatusRows(record.models),
    connectors: normalizeStatusRows(record.connectors),
    sla: normalizeStatusRows(record.sla),
    users: normalizeStatusRows(record.users),
    runbooks: normalizeStatusRows(record.runbooks),
  };
}

/**
 * Admit evidence-package history rows.  The Governance API emits the whole
 * record together (id / range / modules / format / time / actor); a partial row
 * would misdescribe what was exported, so it is dropped rather than completed.
 */
export function normalizeGovernanceEvidencePackages(value: unknown): GovernanceEvidencePackage[] {
  return normalizeList(value, (item) => {
    const record = asRecord(item);
    if (!record) return null;
    const id = usableText(record.id);
    const range = usableText(record.range);
    const mod = usableText(record.mod);
    const fmt = usableText(record.fmt);
    const t = usableText(record.t);
    const by = usableText(record.by);
    if (!id || !range || !mod || !fmt || !t || !by) return null;
    return { id, range, mod, fmt, t, by };
  });
}
