import type { SurveyEvidenceApi } from "./surveyEvidenceClient";
import { payloadContainsSeedData } from "../operatorDataMode";

export type SurveyTarget = { kind: "CANDIDATE_SITE" | "HEAT_ZONE"; id: string };
export type SurveyAssignment = {
  assignment_id: string; tenant_id: string; target_entity_id: string;
  target_entity_kind: SurveyTarget["kind"]; status: string; revision: number;
  created_by: string; created_at: string; assigned_to: string | null;
  expires_at: string; updated_at: string | null; survey_id?: string | null;
  mediaAvailability: { state: "unavailable"; reasonCode: "SURVEY_CONTENT_OWNER_UNAVAILABLE" }
    | { state: "configured"; reasonCode: "SURVEY_CONTENT_READBACK_REQUIRED" };
};
export type SurveyAssignee = { subjectId: string; displayName: string };
export type SurveyRead = { items: SurveyAssignment[]; assignees: SurveyAssignee[]; canAssign: boolean; assigneeError: string | null };
export type SurveyCreated = { assignment: SurveyAssignment; auditEventIds: string[]; receiptId: string; replayed: boolean };
export type SurveyResult<T> = { ok: true; value: T } | { ok: false; superseded: true } | { ok: false; message: string; denied?: boolean };
export type SurveyAssignmentApi = {
  work?: SurveyEvidenceApi;
  read: (target: SurveyTarget) => Promise<SurveyResult<SurveyRead>>;
  create: (target: SurveyTarget, input: { assignedTo: string; expiresAt: string; reason: string }, key: string) => Promise<SurveyResult<SurveyCreated>>;
};
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const timestamp = (value: unknown): value is string => text(value)
  && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const assignee = (value: unknown): value is SurveyAssignee => object(value)
  && text(value.subjectId) && text(value.displayName);
export function isSurveyAssignment(value: unknown, target: SurveyTarget): value is SurveyAssignment {
  return object(value) && text(value.assignment_id) && text(value.tenant_id)
    && value.target_entity_id === target.id && value.target_entity_kind === target.kind
    && text(value.status) && ["UNASSIGNED", "ASSIGNED", "CLAIMED", "IN_PROGRESS", "SUBMITTED", "EXPIRED", "CANCELLED"].includes(value.status)
    && typeof value.revision === "number" && Number.isInteger(value.revision) && value.revision >= 1
    && text(value.created_by) && timestamp(value.created_at) && timestamp(value.expires_at)
    && (value.assigned_to === null || text(value.assigned_to))
    && (value.updated_at === null || timestamp(value.updated_at))
    && (value.survey_id === undefined || value.survey_id === null || text(value.survey_id))
    && object(value.mediaAvailability) && (value.mediaAvailability.state === "unavailable"
      && value.mediaAvailability.reasonCode === "SURVEY_CONTENT_OWNER_UNAVAILABLE"
      || value.mediaAvailability.state === "configured" && value.mediaAvailability.reasonCode === "SURVEY_CONTENT_READBACK_REQUIRED");
}

export function createSurveyAssignmentApi(headers: Record<string, string>,
  captureGuard: () => (() => Promise<boolean>) | undefined): SurveyAssignmentApi {
  async function request(target: SurveyTarget, operation?: {
    input: { assignedTo: string; expiresAt: string; reason: string }; key: string;
  }): Promise<SurveyResult<SurveyRead | SurveyCreated>> {
    const guard = captureGuard();
    if (!guard) return { ok: false, message: "尚無已驗證的工作階段；請重新載入。" };
    const current = async () => { try { return await guard(); } catch { return false; } };
    const stale = (): SurveyResult<SurveyRead | SurveyCreated> => ({ ok: false, superseded: true });
    const cid = crypto.randomUUID();
    try {
      if (!await current()) return stale();
      const query = new URLSearchParams({ targetKind: target.kind, targetId: target.id });
      const response = await fetch(`/api/v1/market-survey/assignments${operation ? "" : `?${query}`}`, {
        method: operation ? "POST" : "GET", cache: "no-store",
        headers: { ...headers, "Content-Type": "application/json", "X-Correlation-Id": cid,
          ...(operation ? { "Idempotency-Key": operation.key } : {}) },
        ...(operation ? { body: JSON.stringify({ targetKind: target.kind, targetId: target.id, ...operation.input }) } : {}),
      });
      if (!await current()) return stale();
      const value: unknown = await response.json();
      if (!await current()) return stale();
      if (!response.ok) return { ok: false, denied: [401, 403, 404].includes(response.status),
        message: `現勘任務 ${response.status}；請保留輸入後重讀或重試。追蹤：${cid}` };
      if (payloadContainsSeedData(value) || !object(value)) throw new Error("invalid-source");
      if (operation) {
        if (!isSurveyAssignment(value.assignment, target) || !text(value.receiptId)
          || typeof value.replayed !== "boolean" || !Array.isArray(value.auditEventIds)
          || value.auditEventIds.length === 0 || !value.auditEventIds.every(text)) throw new Error("invalid-command");
        return { ok: true, value: { assignment: value.assignment, receiptId: value.receiptId,
          replayed: value.replayed, auditEventIds: value.auditEventIds } };
      }
      if (value.targetKind !== target.kind || value.targetId !== target.id || !Array.isArray(value.items)
        || !value.items.every(row => isSurveyAssignment(row, target))
        || new Set(value.items.map(row => row.assignment_id)).size !== value.items.length) throw new Error("invalid-read");
      let assignees: SurveyAssignee[] = [];
      let valid = false;
      let assigneeError: string | null = "無法取得可指派帳號；現有任務仍可閱讀。";
      try {
        const recipients = await fetch(`/api/v1/market-survey/assignees?${query}`, {
          method: "GET", cache: "no-store", headers: { ...headers, "X-Correlation-Id": cid },
        });
        if (!await current()) return stale();
        const recipientData: unknown = await recipients.json();
        if (!await current()) return stale();
        if (recipients.ok && !payloadContainsSeedData(recipientData)
          && object(recipientData) && Array.isArray(recipientData.items)
          && recipientData.items.every(assignee)
          && new Set(recipientData.items.map(row => row.subjectId)).size === recipientData.items.length) {
          valid = true; assignees = recipientData.items; assigneeError = null;
        }
      } catch { if (!await current()) return stale(); }
      return { ok: true, value: { items: value.items,
        assignees, canAssign: valid, assigneeError } };
    } catch {
      if (!await current()) return stale();
      return { ok: false, message: `現勘任務回應尚未核驗；請保留輸入並重新讀取。追蹤：${cid}` };
    }
  }
  return {
    async read(target) {
      const result = await request(target);
      if (!result.ok) return result;
      return "items" in result.value ? { ok: true, value: result.value }
        : { ok: false, message: "任務讀取回應類型不符。" };
    },
    async create(target, input, key) {
      const result = await request(target, { input, key });
      if (!result.ok) return result;
      return "assignment" in result.value ? { ok: true, value: result.value }
        : { ok: false, message: "任務指派回應類型不符。" };
    },
  };
}
