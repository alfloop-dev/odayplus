/**
 * Govern workspace external-row normalizers — ODP-P10-CAN-001-R3C.
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
 * These normalizers are total: any input shape produces a renderable row set,
 * unknown records are dropped, and every field the workspace reads is coerced
 * to a defined value.  They fill in presentational defaults only — they never
 * fabricate operational rows, so a missing or unusable payload still yields an
 * empty list and lets the workspace fail closed.
 *
 * Owned by: ODP-P10-CAN-001-R3C
 * Composes with: apps/web/features/operator/GovernanceWorkspace.tsx,
 *                apps/web/features/operator/OperatorConsole.tsx
 */
import type {
  GovernanceApproval,
  GovernanceApprovalStatus,
  GovernanceAuditRow,
  GovernanceDecisionRow,
  GovernanceEvidence,
} from "../governanceTypes";
import type {
  GovernanceEvidencePackage,
  GovernanceStatusBoard,
  GovernanceStatusRow,
} from "./governanceLoader";

const APPROVAL_STATUSES: GovernanceApprovalStatus[] = [
  "pending",
  "approved",
  "returned",
  "rejected",
  "escalated",
];

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** Coerce to a trimmed string; objects, arrays, null and undefined become "". */
function text(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return "";
}

function textOr(value: unknown, fallback: string): string {
  return text(value) || fallback;
}

/** Optional string field: preserved when usable, dropped otherwise. */
function optionalText(value: unknown): string | undefined {
  const normalized = text(value);
  return normalized ? normalized : undefined;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function normalizeList<T>(value: unknown, normalize: (item: unknown, index: number) => T | null): T[] {
  return asArray(value)
    .map((item, index) => normalize(item, index))
    .filter((item): item is T => item !== null);
}

function normalizeApprovalStatus(value: unknown): GovernanceApprovalStatus {
  const normalized = text(value).toLowerCase();
  const known = APPROVAL_STATUSES.find((status) => status === normalized);
  return known ?? "pending";
}

function normalizeEvidence(value: unknown, index: number): GovernanceEvidence | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = textOr(record.id, `evidence-${index + 1}`);
  return {
    id,
    label: textOr(record.label ?? record.name ?? record.title, id),
    ...(optionalText(record.type) ? { type: text(record.type) } : {}),
    ...(optionalText(record.href ?? record.url) ? { href: text(record.href ?? record.url) } : {}),
    ...(optionalText(record.state) ? { state: text(record.state) } : {}),
  };
}

/**
 * Normalize one governance approval.  Records without a usable id are dropped
 * so a malformed payload cannot produce anonymous, undecidable queue rows.
 */
export function normalizeGovernanceApproval(value: unknown): GovernanceApproval | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = text(record.id ?? record.approvalId ?? record.approval_id);
  if (!id) return null;

  return {
    id,
    module: textOr(record.module, "Govern"),
    title: textOr(record.title ?? record.item ?? record.name, id),
    requestor: textOr(record.requestor ?? record.requestedBy ?? record.requester, "未提供申請人"),
    submittedAt: textOr(record.submittedAt ?? record.submitted_at ?? record.createdAt, "未提供送出時間"),
    status: normalizeApprovalStatus(record.status),
    priority: textOr(record.priority ?? record.severity, "medium"),
    ...(optionalText(record.owner) ? { owner: text(record.owner) } : {}),
    ...(optionalText(record.sla) ? { sla: text(record.sla) } : {}),
    ...(optionalText(record.entityRef ?? record.entity_ref)
      ? { entityRef: text(record.entityRef ?? record.entity_ref) }
      : {}),
    ...(optionalText(record.summary ?? record.meta)
      ? { summary: text(record.summary ?? record.meta) }
      : {}),
    ...(optionalText(record.systemRecommendation ?? record.system_recommendation)
      ? { systemRecommendation: text(record.systemRecommendation ?? record.system_recommendation) }
      : {}),
    ...(optionalText(record.risk) ? { risk: text(record.risk) } : {}),
    ...(optionalText(record.roleNote ?? record.role_note)
      ? { roleNote: text(record.roleNote ?? record.role_note) }
      : {}),
    ...(optionalText(record.reason) ? { reason: text(record.reason) } : {}),
    evidence: normalizeList(record.evidence, normalizeEvidence),
  };
}

export function normalizeGovernanceApprovals(value: unknown): GovernanceApproval[] {
  return normalizeList(value, normalizeGovernanceApproval);
}

export function normalizeGovernanceDecisionRow(value: unknown): GovernanceDecisionRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = text(record.id ?? record.decisionId ?? record.decision_id);
  if (!id) return null;

  return {
    id,
    module: textOr(record.module, "Govern"),
    item: textOr(record.item ?? record.title, id),
    systemRecommendation: textOr(
      record.systemRecommendation ?? record.system_recommendation,
      "—",
    ),
    finalDecision: textOr(record.finalDecision ?? record.final_decision ?? record.status, "—"),
    reason: textOr(record.reason, "未提供決策理由"),
    actor: textOr(record.actor ?? record.decidedBy, "未提供決策人"),
    decidedAt: textOr(record.decidedAt ?? record.decided_at ?? record.timestamp, "未提供決策時間"),
    ...(optionalText(record.model) ? { model: text(record.model) } : {}),
    ...(optionalText(record.datasetSnapshot ?? record.dataset_snapshot)
      ? { datasetSnapshot: text(record.datasetSnapshot ?? record.dataset_snapshot) }
      : {}),
    ...(optionalText(record.approvalId ?? record.approval_id)
      ? { approvalId: text(record.approvalId ?? record.approval_id) }
      : {}),
  };
}

export function normalizeGovernanceDecisionRows(value: unknown): GovernanceDecisionRow[] {
  return normalizeList(value, normalizeGovernanceDecisionRow);
}

export function normalizeGovernanceAuditRow(
  value: unknown,
  index = 0,
): GovernanceAuditRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const id = textOr(record.id ?? record.auditEventId ?? record.audit_event_id, `audit-${index + 1}`);

  return {
    id,
    category: textOr(record.category, "system"),
    timestamp: textOr(record.timestamp ?? record.time ?? record.occurredAt, "未提供時間"),
    actor: textOr(record.actor, "未提供 actor"),
    action: textOr(record.action ?? record.detail, "未提供事件"),
    ...(optionalText(record.module) ? { module: text(record.module) } : {}),
    ...(optionalText(record.entityRef ?? record.entity_ref)
      ? { entityRef: text(record.entityRef ?? record.entity_ref) }
      : {}),
    ...(optionalText(record.summary ?? record.detail)
      ? { summary: text(record.summary ?? record.detail) }
      : {}),
    ...(optionalText(record.reason) ? { reason: text(record.reason) } : {}),
    ...(optionalText(record.correlationId ?? record.correlation_id)
      ? { correlationId: text(record.correlationId ?? record.correlation_id) }
      : {}),
  };
}

export function normalizeGovernanceAuditRows(value: unknown): GovernanceAuditRow[] {
  return normalizeList(value, normalizeGovernanceAuditRow);
}

function normalizeStatusRow(value: unknown): GovernanceStatusRow | null {
  const record = asRecord(value);
  if (!record) return null;
  const source = optionalText(record.source);
  const name = optionalText(record.name);
  if (!source && !name) return null;
  return {
    ...(source ? { source } : {}),
    ...(name ? { name } : {}),
    ...(optionalText(record.version) ? { version: text(record.version) } : {}),
    status: textOr(record.status, "未提供狀態"),
    good: record.good === true,
    note: textOr(record.note, ""),
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
  const board: GovernanceStatusBoard = {
    dataQuality: normalizeStatusRows(record.dataQuality),
    models: normalizeStatusRows(record.models),
    connectors: normalizeStatusRows(record.connectors),
    sla: normalizeStatusRows(record.sla),
    users: normalizeStatusRows(record.users),
    runbooks: normalizeStatusRows(record.runbooks),
  };
  return board;
}

export function normalizeGovernanceEvidencePackages(value: unknown): GovernanceEvidencePackage[] {
  return normalizeList(value, (item) => {
    const record = asRecord(item);
    if (!record) return null;
    const id = text(record.id);
    if (!id) return null;
    return {
      id,
      range: textOr(record.range, "未提供範圍"),
      mod: textOr(record.mod ?? record.modules, "未提供模組"),
      fmt: textOr(record.fmt ?? record.format, "PDF"),
      t: textOr(record.t ?? record.generatedAt ?? record.time, "未提供時間"),
      by: textOr(record.by ?? record.actor, "未提供匯出者"),
      };
  });
}
