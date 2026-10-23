import { operatorSecurityHeaders } from "../operatorSecurityHeaders";
import { payloadContainsSeedData } from "../operatorDataMode";

const BASE = "/api/v1/audit/evidence";
const LIMIT = "recorded_decision_and_audit_references_only";
const HASH = /^[a-f0-9]{64}$/;
const FIELDS: Record<string, readonly string[]> = {
  decision_log: ["decision_id", "decision_version", "candidate_id", "report_id", "report_version", "report_sha256", "source_snapshot_ids", "recorded_model_reference", "recorded_feature_reference", "recorded_policy_reference", "previous_decision_id", "approval_id", "approval_status", "decision_actor", "decided_at", "recorded_network_action", "rationale_sha256", "source_digest"],
  audit: ["event_id", "event_hash", "action", "outcome", "actor", "correlation_id", "occurred_at", "decision_version", "content_sha256"],
};
type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const time = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(value));
const version = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const invalid = (): never => { throw new Error("正式證據來源尚未完整驗證，未建立或下載證據包。"); };

export type CanonicalEvidenceSource = {
  decision_id: string; decision_version: number; candidate_id: string; decided_at: string;
  period_start: string; period_end: string; source_digest: string;
};
export type CanonicalEvidenceSelection = {
  decision_id: string; expected_decision_version: number; sections: Array<"decision_log" | "audit">;
  reason: string; from_time: string; to_time: string; format: "json";
};
export type CanonicalEvidencePreview = {
  document: { source_digest: string; sections: Record<string, Row[]>; recorded_chain?: Row };
  artifact_sha256: string; decision_id: string; decision_version: number; reason: string;
  classification: "CONFIDENTIAL"; retention_class: string; retention_days: number;
};

// This is a read DTO allowlist, not a policy/measurement authority. It rejects
// arbitrary report/evidence objects before any field can enter the preview.
type Shape = ((value: unknown) => boolean) | { [name: string]: Shape } | [Shape];
const numeric = (v: unknown) => typeof v === "number" && Number.isFinite(v);
const hash = (v: unknown) => text(v) && HASH.test(v);
const nullableText = (v: unknown) => v === null || text(v);
const masked = (v: unknown) => text(v) && (v.includes("@") ? /^[^@*]?\**@[^@]+$/.test(v) : /^\*+$/.test(v));
const maskedReason = (v: unknown) => typeof v === "string" && /^\**$/.test(v);
function matches(value: unknown, shape: Shape): boolean {
  if (typeof shape === "function") return shape(value);
  if (Array.isArray(shape)) return Array.isArray(value) && value.every((item) => matches(item, shape[0]));
  return record(value) && JSON.stringify(Object.keys(value).sort()) === JSON.stringify(Object.keys(shape).sort())
    && Object.entries(shape).every(([name, rule]) => matches(value[name], rule));
}
const interval: Shape = { p10: numeric, p50: numeric, p90: numeric };
const chainShape: Shape = {
  schema: (v) => v === "sitescore.recorded-decision-chain.v1", source_digest: hash,
  prediction: { status: (v) => v === "recorded", report_id: text, report_version: version, report_sha256: hash,
    intervals: { m1: interval, m3: interval, m6: interval, m12: interval }, payback_period: interval, payback_p50_months: numeric,
    confidence: (v) => v === null || (numeric(v) && Number(v) >= 0 && Number(v) <= 1),
    confidence_status: (v) => ["measured", "unmeasured", "legacy_unknown"].includes(String(v)),
    model_reference: text, feature_reference: text, feature_snapshot_time: time, prediction_origin_time: time, scored_at: time, source_snapshot_ids: [text] },
  recommendation: { status: (v) => v === "recorded", recorded_recommendation: text, recorded_network_action: nullableText },
  human_decision: { status: (v) => v === "recorded", decision_id: text, decision_version: version, approval_id: text, approval_status: text,
    actor: masked, decided_at: time, policy_reference: text, reason_masked: maskedReason, reason_sha256: hash, actor_fields_masked: (v) => v === true,
    history: [{ version, action: text, from_status: text, to_status: text, actor: masked, reason_masked: maskedReason,
      reason_sha256: hash, occurred_at: time, approval_id: nullableText, correlation_id: nullableText }] },
  execution: { status: (v) => ["recorded", "unavailable", "not_applicable"].includes(String(v)), reason: nullableText,
    record: (v) => v === null || matches(v, { candidate_id: text, decision_id: text, recorded_at: time, site_status: text,
      baseline_trajectory: { m1: numeric, m3: numeric, m6: numeric, m12: numeric }, kind: (kind) => kind === "canonical_candidate_realization" }) },
  outcome: { status: (v) => v === "unavailable", record: (v) => v === null,
    reason: (v) => v === "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER" },
  coverage: { state: (v) => v === "degraded", complete: (v) => v === false,
    missing: [(v) => ["outcome", "execution", "input_snapshot_references"].includes(String(v))] },
  limits: { model_readiness_claimed: (v) => v === false, source_content_included: (v) => v === false,
    physical_opening_or_revenue_outcome_claimed: (v) => v === false, actor_and_reason_masked_under_existing_shared_policy: (v) => v === true },
};
export type CanonicalEvidenceHistory = {
  export_id: string; decision_id: string; reason: string; generated_at: string;
  delivery_status: string; external_acknowledged: boolean; download_ready: boolean;
  legal_hold: boolean; purged_at: string | null;
};

async function withResponse<T>(path: string, roleId: string | undefined, init: RequestInit,
    consume: (response: Response) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  // Same original Govern export transport deadline; body consumption included.
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(BASE + path, {
      ...init, signal: controller.signal, credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", ...operatorSecurityHeaders(roleId),
        "X-Correlation-Id": `corr-evidence-${crypto.randomUUID()}`, ...init.headers },
    });
    if (!response.ok) throw new Error(response.status === 410 ? "證據內容已清除，原稽核與投遞收據仍保留。"
      : response.status === 403 || response.status === 404 ? "目前登入者未獲此證據的匯出權限。"
      : "正式證據尚不可用，未宣稱外部投遞完成；可重試相同內容。");
    return await consume(response);
  } finally { clearTimeout(timeout); }
}
async function json(path: string, roleId: string | undefined, init: RequestInit = {}): Promise<Row> {
  const value: unknown = await withResponse(path, roleId, init, (response) => response.json());
  if (!record(value) || payloadContainsSeedData(value)) return invalid();
  return value;
}

export async function fetchCanonicalEvidenceSources(roleId?: string): Promise<CanonicalEvidenceSource[]> {
  const value = await json("/sources", roleId);
  if (JSON.stringify(value.supported_modules) !== '["SiteScore"]'
      || JSON.stringify(value.supported_sections) !== '["decision_log","audit"]'
      || ![LIMIT, "recorded_decision_audit_with_optional_partial_chain"].includes(String(value.limits)) || !Array.isArray(value.sources)) return invalid();
  const ids = new Set<string>();
  return value.sources.map((row) => {
    if (!record(row) || !text(row.decision_id) || !version(row.decision_version) || !text(row.candidate_id)
        || !time(row.decided_at) || !time(row.period_start) || !time(row.period_end)
        || Date.parse(row.period_start) > Date.parse(row.period_end) || !text(row.source_digest)
        || !HASH.test(row.source_digest) || ids.has(row.decision_id)) return invalid();
    ids.add(row.decision_id);
    return { decision_id: row.decision_id, decision_version: row.decision_version, candidate_id: row.candidate_id,
      decided_at: row.decided_at, period_start: row.period_start, period_end: row.period_end, source_digest: row.source_digest };
  });
}

export async function previewCanonicalEvidence(selection: CanonicalEvidenceSelection, roleId?: string): Promise<CanonicalEvidencePreview> {
  const value = await json("/preview", roleId, { method: "POST", body: JSON.stringify(selection) });
  const doc = value.document;
  const withChain = record(doc) && doc.schema === "sitescore.decision-audit.recorded-partial-chain.v2";
  if (!record(doc) || !["sitescore.decision-audit.references.v1", "sitescore.decision-audit.recorded-partial-chain.v2"].includes(String(doc.schema)) || !text(doc.source_digest)
      || JSON.stringify(Object.keys(doc).sort()) !== JSON.stringify(["schema", "source_digest", "classification", "masking_profile", "actor_fields_masked", "sections", "limits", ...(withChain ? ["recorded_chain"] : [])].sort())
      || !HASH.test(doc.source_digest) || doc.classification !== "CONFIDENTIAL" || doc.masking_profile !== "masked"
      || doc.actor_fields_masked !== true || !record(doc.limits)
      || doc.limits.content !== (withChain ? "recorded_decision_audit_and_partial_chain" : LIMIT)
      || (withChain && doc.limits.full_QA03_complete !== false)
      || doc.limits.source_content_included !== false || doc.limits.model_readiness_claimed !== false
      || doc.limits.subsidy_eligibility_claimed !== false || !record(doc.sections)
      || JSON.stringify(Object.keys(doc.sections).sort()) !== JSON.stringify([...selection.sections].sort())
      || value.decision_id !== selection.decision_id || value.decision_version !== selection.expected_decision_version
      || value.reason !== selection.reason.trim() || value.classification !== "CONFIDENTIAL"
      || !text(value.artifact_sha256) || !HASH.test(value.artifact_sha256) || !text(value.retention_class)
      || !Number.isSafeInteger(value.retention_days) || Number(value.retention_days) <= 0
      || JSON.stringify(value.formats) !== '["json","csv","pdf"]'
      || value.retained !== false || value.external_acknowledged !== false) return invalid();
  const sections: Record<string, Row[]> = {};
  for (const [name, rows] of Object.entries(doc.sections)) {
    if (!FIELDS[name] || !Array.isArray(rows) || rows.length === 0 || (name === "decision_log" && rows.length !== 1)) return invalid();
    sections[name] = rows.map((row) => {
      if (!record(row) || JSON.stringify(Object.keys(row).sort()) !== JSON.stringify([...FIELDS[name]].sort())) return invalid();
      for (const [field, v] of Object.entries(row)) {
        if (field === "source_snapshot_ids") { if (!Array.isArray(v) || !v.every(text)) return invalid(); }
        else if (field === "decision_version" || field === "report_version") { if (!version(v)) return invalid(); }
        else if (field === "previous_decision_id" || field === "recorded_network_action") { if (v !== null && !text(v)) return invalid(); }
        else if (!text(v)) return invalid();
        if (["report_sha256", "rationale_sha256", "source_digest", "event_hash", "content_sha256"].includes(field)
            && (typeof v !== "string" || !HASH.test(v))) return invalid();
        if ((field === "actor" || field === "decision_actor") && typeof v === "string"
            && !(v.includes("@") ? /^[^@*]?\**@[^@]+$/.test(v) : /^\*+$/.test(v))) return invalid();
      }
      return { ...row };
    });
  }
  let chain: Row | undefined;
  if (withChain) {
    if (!record(doc.recorded_chain) || !matches(doc.recorded_chain, chainShape) || doc.recorded_chain.source_digest !== doc.source_digest) return invalid();
    const human = doc.recorded_chain.human_decision;
    const prediction = doc.recorded_chain.prediction;
    const execution = doc.recorded_chain.execution;
    if (!record(human) || human.decision_id !== selection.decision_id || human.decision_version !== selection.expected_decision_version) return invalid();
    if (!record(prediction) || !record(execution)
        || (prediction.confidence_status === "measured" ? !numeric(prediction.confidence) : prediction.confidence !== null)
        || (execution.status === "recorded" ? execution.record === null || execution.reason !== null : execution.record !== null)) return invalid();
    const reference = sections.decision_log?.[0];
    if (!reference || reference.decision_id !== selection.decision_id || reference.decision_version !== selection.expected_decision_version
        || reference.source_digest !== doc.source_digest) return invalid();
    const joins = [
      [prediction.report_id, reference.report_id], [prediction.report_version, reference.report_version], [prediction.report_sha256, reference.report_sha256],
      [prediction.model_reference, reference.recorded_model_reference], [prediction.feature_reference, reference.recorded_feature_reference],
      [human.approval_id, reference.approval_id], [human.approval_status, reference.approval_status], [human.actor, reference.decision_actor],
      [human.decided_at, reference.decided_at], [human.policy_reference, reference.recorded_policy_reference], [human.reason_sha256, reference.rationale_sha256],
    ];
    if (joins.some(([actual, expected]) => actual !== expected)
        || JSON.stringify(prediction.source_snapshot_ids) !== JSON.stringify(reference.source_snapshot_ids)) return invalid();
    if (execution.status === "recorded" && (!record(execution.record) || execution.record.decision_id !== reference.decision_id
        || execution.record.candidate_id !== reference.candidate_id)) return invalid();
    chain = doc.recorded_chain;
  } else if ("recorded_chain" in doc) return invalid();
  return { document: { source_digest: doc.source_digest, sections, ...(chain ? { recorded_chain: chain } : {}) }, artifact_sha256: value.artifact_sha256,
    decision_id: value.decision_id, decision_version: value.decision_version, reason: value.reason,
    classification: "CONFIDENTIAL", retention_class: value.retention_class, retention_days: Number(value.retention_days) };
}

export async function createCanonicalEvidence(selection: CanonicalEvidenceSelection, key: string, expectedDocumentSHA256: string, roleId?: string): Promise<string> {
  const value = await json("/export", roleId, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ ...selection, expected_document_sha256: expectedDocumentSHA256 }) });
  if (!text(value.export_id) || value.delivery_status !== "pending" || value.download_ready !== false
      || value.external_acknowledged !== false || value.artifact_sha256 !== expectedDocumentSHA256) return invalid();
  return value.export_id;
}

export async function fetchCanonicalEvidenceHistory(roleId?: string): Promise<CanonicalEvidenceHistory[]> {
  const value = await json("/exports", roleId);
  if (!Array.isArray(value.exports)) return invalid();
  return value.exports.map((row) => {
    if (!record(row) || !text(row.export_id) || !text(row.decision_id) || !text(row.reason) || !time(row.generated_at)
        || !["pending", "delivered"].includes(String(row.delivery_status)) || typeof row.external_acknowledged !== "boolean"
        || typeof row.download_ready !== "boolean" || typeof row.legal_hold !== "boolean"
        || !(row.purged_at === null || time(row.purged_at))
        || (row.download_ready && (row.delivery_status !== "delivered" || !row.external_acknowledged || row.purged_at !== null))) return invalid();
    return { export_id: row.export_id, decision_id: row.decision_id, reason: row.reason, generated_at: row.generated_at,
      delivery_status: String(row.delivery_status), external_acknowledged: row.external_acknowledged,
      download_ready: row.download_ready, legal_hold: row.legal_hold, purged_at: row.purged_at };
  });
}

export async function downloadCanonicalEvidence(id: string, format: "pdf" | "csv", roleId?: string): Promise<{ blob: Blob; filename: string }> {
  return withResponse(`/exports/${encodeURIComponent(id)}/download?format=${format}`, roleId, {}, async (response) => {
    const expected = response.headers.get("x-artifact-sha256");
    const media = format === "pdf" ? "application/pdf" : "application/zip";
    if (!expected || !HASH.test(expected) || !response.headers.get("content-type")?.startsWith(media)) return invalid();
    const bytes = await response.arrayBuffer();
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (v) => v.toString(16).padStart(2, "0")).join("");
    const prefix = new TextDecoder().decode(bytes.slice(0, format === "pdf" ? 5 : 4));
    if (hash !== expected || (format === "pdf" ? prefix !== "%PDF-" : prefix !== "PK\u0003\u0004")) return invalid();
    return { blob: new Blob([bytes], { type: media }), filename: `audit-evidence.${format === "pdf" ? "pdf" : "zip"}` };
  });
}
