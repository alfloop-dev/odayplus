import { payloadContainsSeedData } from "../operatorDataMode";
import { isSurveyAssignment, type SurveyAssignment, type SurveyCreated, type SurveyResult } from "./surveyAssignmentClient";

export type SurveyAttachment = { mediaId: string; assignmentId: string; sha256: string; sizeBytes: number;
  declaredContentType: string; mediaKind: string; submittedCapturedAt: string; caption: string | null;
  uploadedBy: string; recordedAt: string };
export type SurveyWorkRead = { assignment: SurveyAssignment; currentSubjectId: string; canRequestUpdate: boolean; items: SurveyAttachment[] };
export type SurveyUpload = { expectedRevision: number; dataBase64: string; contentType: string; mediaKind: string;
  capturedAt: string; caption: string | null; reason: string };
export type SurveyPhysicalAttributes = { legal_use_restrictions?: boolean; frontage_meters?: number;
  utilities_power_capacity_amp?: number; utilities_water_pressure_psi?: number; utilities_drainage_available?: boolean;
  loading_zone_available?: boolean; temporary_stop_allowed?: boolean;
  flood_risk_level?: "LOW" | "MEDIUM" | "HIGH"; site_restrictions?: "none" | "approval_required" | "prohibited" };
export type SurveySubmission = { expectedRevision: number; location: { latitude: number; longitude: number; srid: 4326 };
  surveyedAt: string; attributes: { observation: string } & SurveyPhysicalAttributes; mediaIds: string[]; confidence: number | null; reason: string };
export type SurveySubmitted = SurveyCreated & { surveyId: string; observationId: string };
export type SurveyUploaded = { attachment: SurveyAttachment; auditEventIds: string[]; receiptId: string; replayed: boolean };
export type SurveyReviewStatus = "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "NEEDS_REVISION";
export type SurveyEvidence = { survey_id: string; assignment_id: string; tenant_id: string;
  target_entity_kind: string; target_entity_id: string; submitter_id: string; revision: number;
  currentSubjectId: string; canRequestReview: boolean; review_status: SurveyReviewStatus;
  surveyed_at: string; submitted_at: string; attributes: Record<string, unknown>;
  location: { latitude: number; longitude: number }; media_attachments: SurveyAttachment[];
  review_record: { review_status: SurveyReviewStatus; reviewer_id: string; reviewed_at: string;
    review_comment?: string; review_checklist: Record<string, unknown>; conditions: string[] } | null };
export type SurveyReview = { surveyId: string; expectedRevision: number; decision: Exclude<SurveyReviewStatus, "PENDING_REVIEW">;
  reason: string; reviewChecklist: Record<string, unknown>; conditions: string[] };
export type SurveyReviewed = { evidence: SurveyEvidence; auditEventIds: string[]; receiptId: string; replayed: boolean };
export type SurveyEvidenceSelection = Pick<SurveyAssignment,
  "assignment_id" | "tenant_id" | "target_entity_kind" | "target_entity_id" | "survey_id">;
export type SurveyEvidenceApi = {
  read: (assignment: SurveyAssignment) => Promise<SurveyResult<SurveyWorkRead>>;
  change: (assignment: SurveyAssignment, operation: "claim" | "start" | "cancel", reason: string, key: string) => Promise<SurveyResult<SurveyCreated>>;
  upload: (assignment: SurveyAssignment, input: SurveyUpload, key: string) => Promise<SurveyResult<SurveyUploaded>>;
  submit: (assignment: SurveyAssignment, input: SurveySubmission, key: string) => Promise<SurveyResult<SurveySubmitted>>;
  download: (assignment: SurveyAssignment, attachment: SurveyAttachment) => Promise<SurveyResult<Blob>>;
  evidence: (assignment: SurveyEvidenceSelection) => Promise<SurveyResult<SurveyEvidence>>;
  review: (assignment: SurveyEvidenceSelection, input: SurveyReview, key: string) => Promise<SurveyResult<SurveyReviewed>>;
};
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && !!v.trim();
const time = (v: unknown): v is string => text(v) && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
const digest = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
export function isSurveyEvidence(v: unknown, selected: SurveyEvidenceSelection): v is SurveyEvidence {
  if (!object(v) || v.survey_id !== selected.survey_id || v.assignment_id !== selected.assignment_id
    || v.tenant_id !== selected.tenant_id || v.target_entity_kind !== selected.target_entity_kind
    || v.target_entity_id !== selected.target_entity_id || !text(v.submitter_id) || !text(v.currentSubjectId)
    || typeof v.canRequestReview !== "boolean" || v.canRequestReview && v.currentSubjectId === v.submitter_id
    || typeof v.revision !== "number" || !Number.isSafeInteger(v.revision) || v.revision < 1
    || !["PENDING_REVIEW", "APPROVED", "REJECTED", "NEEDS_REVISION"].includes(String(v.review_status))
    || !time(v.surveyed_at) || !time(v.submitted_at) || !object(v.attributes) || !object(v.location)
    || typeof v.location.latitude !== "number" || !Number.isFinite(v.location.latitude) || Math.abs(v.location.latitude) > 90
    || typeof v.location.longitude !== "number" || !Number.isFinite(v.location.longitude) || Math.abs(v.location.longitude) > 180
    || !Array.isArray(v.media_attachments) || !v.media_attachments.every(item => isSurveyAttachment(item, selected.assignment_id))
    || new Set(v.media_attachments.map(item => item.mediaId)).size !== v.media_attachments.length) return false;
  if (v.review_status === "PENDING_REVIEW") return v.review_record === null;
  const record = v.review_record;
  return object(record) && record.review_status === v.review_status && text(record.reviewer_id)
    && record.reviewer_id !== v.submitter_id && time(record.reviewed_at) && object(record.review_checklist)
    && (record.review_comment === undefined || typeof record.review_comment === "string")
    && Array.isArray(record.conditions) && record.conditions.every(text);
}
export function isSurveyAttachment(value: unknown, assignmentId: string): value is SurveyAttachment {
  return object(value) && text(value.mediaId) && /^media-[a-f0-9]{64}$/.test(value.mediaId) && value.assignmentId === assignmentId
    && digest(value.sha256) && typeof value.sizeBytes === "number" && Number.isSafeInteger(value.sizeBytes) && value.sizeBytes > 0
    && text(value.declaredContentType) && text(value.mediaKind) && ["PHOTO", "VIDEO", "AUDIO", "DOCUMENT", "SIGNATURE", "OTHER"].includes(value.mediaKind)
    && time(value.submittedCapturedAt) && time(value.recordedAt) && text(value.uploadedBy)
    && (value.caption === null || typeof value.caption === "string");
}
function command(value: unknown, selected: SurveyAssignment): value is SurveyCreated {
  return object(value) && isSurveyAssignment(value.assignment, { kind: selected.target_entity_kind, id: selected.target_entity_id })
    && value.assignment.assignment_id === selected.assignment_id && text(value.receiptId) && typeof value.replayed === "boolean"
    && Array.isArray(value.auditEventIds) && value.auditEventIds.length > 0 && value.auditEventIds.every(text);
}
function submitted(value: unknown, selected: SurveyAssignment): value is SurveySubmitted {
  if (!object(value)) return false;
  const { surveyId, observationId } = value;
  return text(surveyId) && text(observationId) && command(value, selected)
    && value.assignment.status === "SUBMITTED" && value.assignment.survey_id === surveyId;
}
export function createSurveyEvidenceApi(headers: Record<string, string>, capture: () => (() => Promise<boolean>) | undefined): SurveyEvidenceApi {
  async function request(selected: Pick<SurveyAssignment, "assignment_id">, suffix: string, body?: unknown, key?: string): Promise<SurveyResult<unknown>> {
    const guard = capture(); const cid = crypto.randomUUID();
    const current = async () => { try { return !!guard && await guard(); } catch { return false; } };
    try {
      if (!await current()) return { ok: false, superseded: true };
      const response = await fetch(`/api/v1/market-survey/assignments/${encodeURIComponent(selected.assignment_id)}${suffix}`, {
        method: body === undefined ? "GET" : "POST", credentials: "include", cache: "no-store",
        headers: { ...headers, "Content-Type": "application/json", "X-Correlation-Id": cid, ...(key ? { "Idempotency-Key": key } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!await current()) return { ok: false, superseded: true };
      const value: unknown = await response.json();
      if (!await current()) return { ok: false, superseded: true };
      if (!response.ok) return { ok: false, denied: [401, 403, 404].includes(response.status), message: `現勘回報 HTTP ${response.status}；請保留原輸入後重讀或重試。追蹤：${cid}` };
      if (payloadContainsSeedData(value)) throw new Error("seed-source");
      return { ok: true, value };
    } catch {
      return await current() ? { ok: false, message: `現勘回應未確認；保留相同命令識別與輸入重試。追蹤：${cid}` } : { ok: false, superseded: true };
    }
  }
  return {
    async evidence(selected) {
      const result = await request(selected, "/evidence"); if (!result.ok) return result;
      return isSurveyEvidence(result.value, selected) ? { ok: true, value: result.value }
        : { ok: false, message: "現勘證據的來源、版本或審查紀錄無法核對。" };
    },
    async review(selected, input, key) {
      const result = await request(selected, "/review", input, key); if (!result.ok) return result;
      const v = result.value;
      return object(v) && isSurveyEvidence(v.evidence, selected) && v.evidence.survey_id === input.surveyId && v.evidence.review_status === input.decision
        && v.evidence.revision === input.expectedRevision + 1 && v.evidence.review_record?.reviewer_id === v.evidence.currentSubjectId
        && v.evidence.review_record.review_comment === input.reason
        && text(v.receiptId) && typeof v.replayed === "boolean" && Array.isArray(v.auditEventIds)
        && v.auditEventIds.length > 0 && v.auditEventIds.every(text)
        ? { ok: true, value: { evidence: v.evidence, receiptId: v.receiptId, replayed: v.replayed, auditEventIds: v.auditEventIds } }
        : { ok: false, message: "現勘審查的保存結果與命令收條無法核對。" };
    },
    async read(selected) {
      const result = await request(selected, "/attachments"); if (!result.ok) return result;
      const v = result.value;
      if (!object(v) || v.assignmentId !== selected.assignment_id || !text(v.currentSubjectId) || typeof v.canRequestUpdate !== "boolean"
        || !isSurveyAssignment(v.assignment, { kind: selected.target_entity_kind, id: selected.target_entity_id })
        || v.assignment.assignment_id !== selected.assignment_id || !Array.isArray(v.items) || !v.items.every(row => isSurveyAttachment(row, selected.assignment_id))
        || new Set(v.items.map(row => row.mediaId)).size !== v.items.length) return { ok: false, message: "現勘回報來源或附件識別無法核對。" };
      return { ok: true, value: { assignment: v.assignment, currentSubjectId: v.currentSubjectId, canRequestUpdate: v.canRequestUpdate, items: v.items } };
    },
    async change(selected, operation, reason, key) {
      const result = await request(selected, `/${operation}`, { expectedRevision: selected.revision, reason }, key); if (!result.ok) return result;
      return command(result.value, selected) ? { ok: true, value: result.value } : { ok: false, message: "現勘狀態命令收條無法核對。" };
    },
    async upload(selected, input, key) {
      const result = await request(selected, "/attachments", input, key); if (!result.ok) return result;
      const v = result.value;
      return object(v) && isSurveyAttachment(v.attachment, selected.assignment_id) && text(v.receiptId) && typeof v.replayed === "boolean"
        && Array.isArray(v.auditEventIds) && v.auditEventIds.length > 0 && v.auditEventIds.every(text)
        ? { ok: true, value: { attachment: v.attachment, receiptId: v.receiptId, replayed: v.replayed, auditEventIds: v.auditEventIds } }
        : { ok: false, message: "實際附件上傳收條無法核對。" };
    },
    async submit(selected, input, key) {
      const result = await request(selected, "/submit", input, key); if (!result.ok) return result;
      const v = result.value;
      return submitted(v, selected)
        ? { ok: true, value: { ...v, surveyId: v.surveyId, observationId: v.observationId } }
        : { ok: false, message: "現勘回報的實際 survey／observation 收條無法核對。" };
    },
    async download(selected, attachment) {
      const guard = capture(); const current = async () => { try { return !!guard && await guard(); } catch { return false; } };
      try {
        if (!isSurveyAttachment(attachment, selected.assignment_id) || !await current()) return { ok: false, superseded: true };
        const response = await fetch(`/api/v1/market-survey/assignments/${encodeURIComponent(selected.assignment_id)}/attachments/${encodeURIComponent(attachment.mediaId)}/content`,
          { credentials: "include", cache: "no-store", headers });
        if (!await current()) return { ok: false, superseded: true };
        if (!response.ok) return { ok: false, denied: [401, 403, 404].includes(response.status), message: `附件下載 HTTP ${response.status}；未顯示為可讀內容。` };
        if (response.headers.get("content-type") !== "application/octet-stream" || response.headers.get("x-content-type-options") !== "nosniff"
          || !response.headers.get("content-disposition")?.startsWith("attachment;")) throw new Error("unsafe-response");
        const bytes = await response.arrayBuffer(); if (!await current()) return { ok: false, superseded: true };
        const actual = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(v => v.toString(16).padStart(2, "0")).join("");
        if (!await current()) return { ok: false, superseded: true };
        if (bytes.byteLength !== attachment.sizeBytes || actual !== attachment.sha256
          || response.headers.get("x-content-sha256") !== actual) throw new Error("bytes-mismatch");
        return { ok: true, value: new Blob([bytes], { type: "application/octet-stream" }) };
      } catch {
        return await current() ? { ok: false, message: "附件實際 bytes／雜湊尚未核驗；未開啟下載。" } : { ok: false, superseded: true };
      }
    },
  };
}
