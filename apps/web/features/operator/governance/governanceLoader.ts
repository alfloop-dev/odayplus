/**
 * Govern workspace (治理稽核) API loader — ODP-OC-R4-009.
 *
 * Dual-mode: runtime API client + embedded fixture fallback.  Mirrors the
 * Growth workspace view-model pattern (growthViewModel.ts):
 *
 *   Read  — fetchGovernanceSnapshot()
 *     GET /api/v1/operator/governance/snapshot → { approvals, decisions,
 *     auditRows, statusBoard, evidencePackages }.  On any network/parse error
 *     it returns null so the workspace keeps rendering its embedded fixtures
 *     (the Govern workspace never breaks when the API is unreachable).
 *
 *   Write — submitGovernanceDecision() / exportEvidencePackage()
 *     POST /api/v1/operator/governance/decisions        (approve/return/reject)
 *     POST /api/v1/operator/governance/evidence-package (export)
 *     Both carry Idempotency-Key + X-Correlation-Id.  The server enforces the
 *     return/reject-requires-reason policy; a 422 is surfaced as a typed error
 *     so the workspace can show the reason requirement.
 *
 * Design source: canonical package 6 (r4-20260707-package-6),
 * data-screen-label "Govern 治理稽核".
 */
import type {
  GovernanceApproval,
  GovernanceAuditRow,
  GovernanceDecisionAction,
  GovernanceDecisionRow,
} from "../governanceTypes";
import { operatorSecurityHeaders } from "../operatorSecurityHeaders";

const GOVERNANCE_API_BASE = "/api/v1/operator/governance";

/** A single status-board row (Data Quality / Model / Connector / SLA / Users). */
export type GovernanceStatusRow = {
  source?: string;
  name?: string;
  version?: string;
  status: string;
  good: boolean;
  note: string;
};

/** The five status-board panels every value builder must expose. */
export type GovernanceStatusBoard = {
  dataQuality: GovernanceStatusRow[];
  models: GovernanceStatusRow[];
  connectors: GovernanceStatusRow[];
  sla: GovernanceStatusRow[];
  users: GovernanceStatusRow[];
  runbooks?: GovernanceStatusRow[];
};

/** An evidence-package history row. */
export type GovernanceEvidencePackage = {
  id: string;
  range: string;
  mod: string;
  fmt: string;
  t: string;
  by: string;
};

/** The full Govern workspace snapshot returned by the API. */
export type GovernanceSnapshot = {
  approvals: GovernanceApproval[];
  decisions: GovernanceDecisionRow[];
  auditRows: GovernanceAuditRow[];
  statusBoard: GovernanceStatusBoard;
  evidencePackages: GovernanceEvidencePackage[];
  correlationId?: string;
  source?: string;
};

function newIdempotencyKey(): string {
  return `ik-govern-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function newCorrelationId(): string {
  return `corr-web-govern-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

async function apiFetch<T>(
  path: string,
  options: RequestInit & { correlationId?: string; roleId?: string } = {},
): Promise<{ ok: true; status: number; data: T } | { ok: false; status: number; data: null }> {
  const { correlationId, roleId, ...fetchOptions } = options;
  try {
    const res = await fetch(`${GOVERNANCE_API_BASE}${path}`, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        "X-Correlation-Id": correlationId ?? newCorrelationId(),
        ...operatorSecurityHeaders(roleId),
        ...(fetchOptions.headers ?? {}),
      },
    });
    if (!res.ok) {
      return { ok: false, status: res.status, data: null };
    }
    return { ok: true, status: res.status, data: (await res.json()) as T };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

/**
 * Fetch the Govern snapshot.  Returns null when the API is unreachable so the
 * caller falls back to embedded fixtures.
 */
export async function fetchGovernanceSnapshot(
  roleId?: string,
): Promise<GovernanceSnapshot | null> {
  const headers: Record<string, string> = {};
  if (roleId) headers["X-Operator-Role"] = roleId;
  const result = await apiFetch<{
    approvals: GovernanceApproval[];
    decisions: GovernanceDecisionRow[];
    auditRows: GovernanceAuditRow[];
    statusBoard: GovernanceStatusBoard;
    evidencePackages: GovernanceEvidencePackage[];
    correlation_id?: string;
    source?: string;
  }>("/snapshot", { method: "GET", headers, roleId });
  if (!result.ok || !result.data) return null;
  const data = result.data;
  return {
    approvals: data.approvals ?? [],
    decisions: data.decisions ?? [],
    auditRows: data.auditRows ?? [],
    statusBoard: data.statusBoard,
    evidencePackages: data.evidencePackages ?? [],
    correlationId: data.correlation_id,
    source: data.source,
  };
}

/** Typed result for a decision write. */
export type GovernanceDecisionResult =
  | { ok: true; finalDecision: string; status: string; correlationId?: string }
  | { ok: false; policyError: boolean; status: number; detail: string };

/**
 * Submit an approve / return / reject decision.  The server enforces the
 * return/reject-requires-reason policy; a 422 comes back as
 * ``{ ok: false, policyError: true }``.
 */
export async function submitGovernanceDecision(params: {
  approvalId: string;
  action: GovernanceDecisionAction;
  reason?: string;
  role?: string;
  actorName?: string;
  roleId?: string;
}): Promise<GovernanceDecisionResult> {
  const headers: Record<string, string> = { "Idempotency-Key": newIdempotencyKey() };
  if (params.roleId) headers["X-Operator-Role"] = params.roleId;
  const result = await apiFetch<{
    finalDecision: string;
    status: string;
    correlation_id?: string;
  }>("/decisions", {
    method: "POST",
    headers,
    roleId: params.roleId,
    body: JSON.stringify({
      approvalId: params.approvalId,
      action: params.action,
      reason: params.reason ?? "",
      role: params.role ?? "營運主管",
      actorName: params.actorName,
    }),
  });
  if (result.ok && result.data) {
    return {
      ok: true,
      finalDecision: result.data.finalDecision,
      status: result.data.status,
      correlationId: result.data.correlation_id,
    };
  }
  return {
    ok: false,
    policyError: result.status === 422,
    status: result.status,
    detail: result.status === 422 ? "退回或駁回理由需至少 10 個字" : "決策未送出（API 無法連線）",
  };
}

/** The recorded scope/range/format/actor/correlation/retention of an export. */
export type EvidencePackageRecord = {
  id: string;
  file: string;
  size: string;
  range: string;
  scope: { dateFrom: string; dateTo: string; modules: string[]; contents: string[] };
  format: string;
  actor: string;
  role: string;
  correlationId: string;
  retentionPolicy: string;
  generatedAt: string;
};

/**
 * Export an Evidence Package.  Returns the recorded package (scope, range,
 * format, actor, correlation, retention) or null on API failure.
 */
export async function exportEvidencePackage(params: {
  dateFrom: string;
  dateTo: string;
  modules: string[];
  contents: string[];
  format?: string;
  role?: string;
  actorName?: string;
  retentionPolicy?: string;
  roleId?: string;
}): Promise<EvidencePackageRecord | null> {
  const headers: Record<string, string> = { "Idempotency-Key": newIdempotencyKey() };
  if (params.roleId) headers["X-Operator-Role"] = params.roleId;
  const result = await apiFetch<{ package: EvidencePackageRecord }>("/evidence-package", {
    method: "POST",
    headers,
    roleId: params.roleId,
    body: JSON.stringify({
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      modules: params.modules,
      contents: params.contents,
      format: params.format ?? "PDF",
      role: params.role ?? "營運主管",
      actorName: params.actorName,
      retentionPolicy: params.retentionPolicy,
    }),
  });
  if (!result.ok || !result.data) return null;
  return result.data.package;
}
