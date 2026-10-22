import { payloadContainsSeedData } from "../operatorDataMode";
import type { AreaPlanningDraftResponse, AreaPlanningDraftCommandResponse } from "@oday-plus/openapi-client";

export type AreaPlanningFields = Pick<AreaPlanningDraftResponse, "name" | "quarter" | "budget" | "candidateIds">;
export type AreaPlanningDraft = AreaPlanningDraftResponse;
export type AreaPlanningCommand = AreaPlanningDraftCommandResponse;
export type AreaPlanningResult<T> = { ok: true; value: T } | { ok: false; superseded: true }
  | { ok: false; message: string; denied?: boolean; rejected?: boolean };
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const text = (x: unknown): x is string => typeof x === "string" && x.trim().length > 0 && x === x.trim();
const uuid = (x: unknown): x is string => typeof x === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
const revision = (x: unknown): x is number => typeof x === "number" && Number.isInteger(x) && x > 0;
const stamp = (x: unknown): x is string => typeof x === "string" && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(x) && Number.isFinite(Date.parse(x));
export function isAreaPlanningFields(x: unknown): x is AreaPlanningFields & Record<string, unknown> {
  return object(x) && text(x.name) && x.name.length <= 120
    && (x.quarter === null || typeof x.quarter === "string" && /^[1-9][0-9]{3}Q[1-4]$/.test(x.quarter))
    && (x.budget === null || typeof x.budget === "number" && Number.isFinite(x.budget) && x.budget >= 0)
    && Array.isArray(x.candidateIds) && x.candidateIds.every(id => text(id) && id.length <= 200)
    && new Set(x.candidateIds).size === x.candidateIds.length;
}
export function isAreaPlanningDraft(x: unknown, zone: string): x is AreaPlanningDraft {
  if (!object(x) || !isAreaPlanningFields(x) || !uuid(x.draftId) || !uuid(x.tenantId) || !uuid(x.createdBy)
    || x.heatZoneId !== zone || !revision(x.version) || !stamp(x.createdAt) || !stamp(x.updatedAt)
    || Date.parse(x.updatedAt) < Date.parse(x.createdAt)) return false;
  return x.status === "DRAFT" ? x.nativeScenarioId === null && x.materializedBy === null
    : x.status === "MATERIALIZED" && x.quarter !== null && x.budget !== null && x.candidateIds.length > 0
      && x.nativeScenarioId === "netplan-scenario-area-draft-" + x.draftId && uuid(x.materializedBy);
}
export const sameAreaPlanningFields = (a: AreaPlanningFields, b: AreaPlanningFields) =>
  a.name === b.name && a.quarter === b.quarter && a.budget === b.budget
    && a.candidateIds.length === b.candidateIds.length && a.candidateIds.every((id, index) => b.candidateIds[index] === id);
export const sameAreaPlanningIdentity = (a: AreaPlanningDraft, b: AreaPlanningDraft) =>
  a.draftId === b.draftId && a.tenantId === b.tenantId && a.createdBy === b.createdBy
    && a.heatZoneId === b.heatZoneId && Date.parse(a.createdAt) === Date.parse(b.createdAt);
export function admitsCurrentAreaPlanningDraft(known: AreaPlanningDraft, fresh: AreaPlanningDraft): boolean {
  if (!sameAreaPlanningIdentity(known, fresh) || fresh.version < known.version
    || Date.parse(fresh.updatedAt) < Date.parse(known.updatedAt)) return false;
  if (fresh.version === known.version) return sameAreaPlanningFields(known, fresh)
    && known.status === fresh.status && known.nativeScenarioId === fresh.nativeScenarioId
    && known.materializedBy === fresh.materializedBy && Date.parse(known.updatedAt) === Date.parse(fresh.updatedAt);
  return known.status === "DRAFT" || fresh.status === "MATERIALIZED"
    && sameAreaPlanningFields(known, fresh) && known.nativeScenarioId === fresh.nativeScenarioId
    && known.materializedBy === fresh.materializedBy;
}

export function createAreaPlanningDraftApi(zone: string, headers: Record<string, string>, guard: () => Promise<boolean>) {
  const base = "/api/v1/operator/area-planning-drafts";
  async function request<T>(path: string, validate: (value: unknown) => value is T,
    command?: { method: string; body: unknown; key: string }): Promise<AreaPlanningResult<T>> {
    const current = async () => { try { return await guard(); } catch { return false; } };
    const superseded = (): AreaPlanningResult<T> => ({ ok: false, superseded: true });
    const cid = crypto.randomUUID();
    try {
      if (!await current()) return superseded();
      const response = await fetch(base + path, { method: command?.method ?? "GET", cache: "no-store",
        headers: { ...headers, "Content-Type": "application/json", "X-Correlation-Id": cid,
          ...(command ? { "Idempotency-Key": command.key } : {}) },
        ...(command ? { body: JSON.stringify(command.body) } : {}) });
      if (!await current()) return superseded();
      const value: unknown = await response.json();
      if (!await current()) return superseded();
      if (!response.ok) return { ok: false, denied: [401, 403, 404].includes(response.status),
        // Incomplete durable receipts are also 409: their effect is unknown.
        rejected: [400, 422, 428].includes(response.status)
          || response.status === 409 && object(value) && value.detail === "AREA_PLANNING_COMMAND_CONFLICT",
        message: `區域草稿 ${response.status}；${response.status === 409 ? "版本已變更，請重新讀取後再確認。" : "請保留輸入後重新讀取或重試。"}追蹤：${cid}` };
      if (payloadContainsSeedData(value) || !validate(value)) throw new Error("invalid-area-planning-response");
      return { ok: true, value };
    } catch {
      if (!await current()) return superseded();
      return { ok: false, message: `草稿回應尚未核驗；請保留相同輸入與命令識別重試，或重新讀取。追蹤：${cid}` };
    }
  }
  return {
    list: () => request("?" + new URLSearchParams({ heatZoneId: zone }), (x): x is { items: AreaPlanningDraft[] } =>
      object(x) && x.heatZoneId === zone && Array.isArray(x.items)
        && x.items.every(row => isAreaPlanningDraft(row, zone))
        && new Set(x.items.map(row => row.draftId)).size === x.items.length),
    get: (id: string) => request("/" + encodeURIComponent(id), (x): x is AreaPlanningDraft =>
      isAreaPlanningDraft(x, zone) && x.draftId === id),
    command: (operation: "create" | "update", input: AreaPlanningFields & { expectedVersion?: number }, key: string, id?: string,
      previous?: AreaPlanningDraft) =>
      request(id ? "/" + encodeURIComponent(id) : "", (x): x is AreaPlanningCommand => object(x)
        && isAreaPlanningDraft(x.draft, zone) && x.draft.status === "DRAFT"
        && (!id || x.draft.draftId === id)
        && (!previous || sameAreaPlanningIdentity(previous, x.draft)
          && Date.parse(x.draft.updatedAt) >= Date.parse(previous.updatedAt))
        && x.draft.version === (operation === "create" ? 1 : (input.expectedVersion ?? 0) + 1)
        && sameAreaPlanningFields(x.draft, input)
        && Array.isArray(x.auditEventIds) && x.auditEventIds.length > 0 && x.auditEventIds.every(uuid)
        && text(x.receiptId) && typeof x.replayed === "boolean",
      { method: operation === "create" ? "POST" : "PUT", body: { ...input, heatZoneId: zone }, key }),
  };
}
