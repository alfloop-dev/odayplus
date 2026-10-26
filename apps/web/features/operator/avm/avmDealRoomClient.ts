import { payloadContainsSeedData } from "../operatorDataMode";

export type Context = { subjectId: string; tenantId: string; contextKey: string; canCreate: boolean };
export type Operation = "normalize" | "value" | "approve" | "build" | "export";
export type ValuationInput = { store_id: string; gm_ttm: number; forecast_gm_next_12m: number; asset_book_value: number; equipment_fair_value: number } & Record<string, unknown>;
export type Approval = { decision_id: string; actor_id: string; approved_at: string; decision_reason: string; policy_version: string; correlation_id: string; reserve_price?: number };
export type Margin = { gm_ttm: number; gm_fwd: number; normalized_gm: number; adjustment_reasons: string[]; confidence: string };
export type Lens = { lens: string; p10: number; p50: number; p90: number; method: string; evidence: Record<string, unknown> };
export type Report = { report_id: string; case_id: string; store_id: string; valuation_version: number;
  fair_price: { p10: number; p50: number; p90: number }; reserve_price: number | null; asking_price: number | null;
  confidence: string; model_version: string; feature_version: string; depreciation_version: string;
  prediction_origin_time: string; valued_at: string; lenses: Lens[]; finance_approval: Approval | null };
export type Case = { case_id: string; store_id: string; status: string; created_by: string; created_at: string;
  status_history: Array<{ from_status: string; to_status: string; actor: string; reason: string; timestamp: string; correlation_id: string }> };
export type Room = { dataroom_id: string; case_id: string; checklist: Array<{ document_id: string; name: string; status: string; source_snapshot_id: string | null }>;
  completeness: number; is_complete: boolean; missing_documents: string[]; valuation_card: Record<string, unknown> };
export type Artifact = { exportId: string; sha256: string; bytes: number; filename: string };
export type Detail = { context: Context; expectedVersion: string; case: Case; report: Report | null; reportHistory: Report[];
  creatorVerification: { verified: boolean; reasonCode?: string }; financeVerification: { verified: boolean; reasonCode?: string };
  normalizedMargin: Margin | null; dataRoom: Room | null; sourceSnapshotIds: string[]; sensitiveValuesVisible: boolean;
  exportCount: number; reportAvailability: { state: "available" | "unavailable"; reasonCode: string | null };
  actions: Record<Operation, boolean>; exports: Array<{ export_id: string; actor: string; reason: string; exported_at: string; correlation_id: string; artifact: Artifact | null }>;
  attachmentProvider: { available: false; reasonCode: string } };
export type Row = Pick<Detail, "case" | "report" | "dataRoom" | "expectedVersion" | "actions" | "sensitiveValuesVisible" | "reportAvailability" | "exportCount">;
export type Receipt = { sourceId: string; operation: string; actor: string; auditEventId: string; correlationId: string; artifact: Artifact | null; deliveryStatus: string };
export class SupersededAVMRead extends Error {
  constructor() { super("登入身分或授權範圍已變更，請重新核對來源。"); }
}
const hash = /^[a-f0-9]{64}$/;
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function text(value: unknown): value is string { return typeof value === "string" && Boolean(value.trim()); }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function invalid(): never { throw new Error("AVM 來源格式或物件識別無法核對。"); }
export function parseContext(value: unknown): Context {
  if (!object(value) || !text(value.subjectId) || !text(value.tenantId) || typeof value.contextKey !== "string" || !hash.test(value.contextKey) || typeof value.canCreate !== "boolean") invalid();
  return value as Context;
}
function parseReport(value: unknown, parent: Case): Report | null {
  if (value === null) return null;
  if (!object(value) || !text(value.report_id) || value.case_id !== parent.case_id || value.store_id !== parent.store_id
    || !Number.isInteger(value.valuation_version) || (value.valuation_version as number) < 1
    || !object(value.fair_price) || ![value.fair_price.p10, value.fair_price.p50, value.fair_price.p90].every(finite)
    || !Array.isArray(value.lenses) || value.lenses.some((lens) => !object(lens) || !text(lens.lens) || !text(lens.method)
      || ![lens.p10, lens.p50, lens.p90].every(finite) || !object(lens.evidence))
    || ![value.reserve_price, value.asking_price].every((v) => v === null || finite(v))
    || ![value.confidence, value.model_version, value.feature_version, value.depreciation_version, value.prediction_origin_time, value.valued_at].every(text)) invalid();
  if (value.finance_approval !== null && (!object(value.finance_approval)
    || !["decision_id", "actor_id", "approved_at", "decision_reason", "policy_version", "correlation_id"].every((key) => text((value.finance_approval as Record<string, unknown>)[key])))) invalid();
  return value as unknown as Report;
}
function parseRow(value: unknown): Row {
  if (!object(value) || !object(value.case) || !["case_id", "store_id", "status", "created_by", "created_at"].every((key) => text((value.case as Record<string, unknown>)[key]))
    || !Array.isArray(value.case.status_history) || typeof value.expectedVersion !== "string" || !hash.test(value.expectedVersion)
    || !object(value.actions) || !["normalize", "value", "approve", "build", "export"].every((key) => typeof (value.actions as Record<string, unknown>)[key] === "boolean")
    || !Number.isInteger(value.exportCount) || (value.exportCount as number) < 0
    || typeof value.sensitiveValuesVisible !== "boolean" || !object(value.reportAvailability)
    || !["available", "unavailable"].includes(String(value.reportAvailability.state))
    || value.reportAvailability.reasonCode !== null && !text(value.reportAvailability.reasonCode)) invalid();
  const parent = value.case as unknown as Case; const report = parseReport(value.report, parent);
  if ((value.reportAvailability.state === "available") !== (report !== null) || parent.status_history.some((entry) => !object(entry)
    || ![entry.from_status, entry.to_status, entry.actor, entry.reason, entry.timestamp, entry.correlation_id].every((v) => typeof v === "string"))) invalid();
  if (!value.sensitiveValuesVisible && report && (report.reserve_price !== null || report.asking_price !== null || report.finance_approval?.reserve_price !== undefined)) invalid();
  if (value.dataRoom !== null && (!object(value.dataRoom) || value.dataRoom.case_id !== parent.case_id || !text(value.dataRoom.dataroom_id)
    || !Array.isArray(value.dataRoom.checklist) || !finite(value.dataRoom.completeness) || value.dataRoom.completeness < 0 || value.dataRoom.completeness > 1
    || typeof value.dataRoom.is_complete !== "boolean" || !Array.isArray(value.dataRoom.missing_documents)
    || !object(value.dataRoom.valuation_card))) invalid();
  if (object(value.dataRoom) && (value.dataRoom.checklist as unknown[]).some((item) => !object(item)
    || ![item.document_id, item.name, item.status].every(text) || item.source_snapshot_id !== null && !text(item.source_snapshot_id))) invalid();
  return { case: parent, report, dataRoom: value.dataRoom as Room | null, expectedVersion: value.expectedVersion,
    actions: value.actions as Row["actions"], sensitiveValuesVisible: value.sensitiveValuesVisible, reportAvailability: value.reportAvailability as Row["reportAvailability"], exportCount: value.exportCount as number };
}
export function parseDetail(value: unknown, caseId: string): Detail {
  const base = parseRow(value);
  if (!object(value) || base.case.case_id !== caseId || !Array.isArray(value.reportHistory)
    || !Array.isArray(value.sourceSnapshotIds) || !value.sourceSnapshotIds.every(text) || !Array.isArray(value.exports)
    || !object(value.attachmentProvider) || value.attachmentProvider.available !== false || !text(value.attachmentProvider.reasonCode)) invalid();
  parseContext(value.context); value.reportHistory.forEach((report) => parseReport(report, base.case));
  if (!object(value.creatorVerification) || typeof value.creatorVerification.verified !== "boolean"
    || !object(value.financeVerification) || typeof value.financeVerification.verified !== "boolean") invalid();
  if (value.normalizedMargin !== null && (!object(value.normalizedMargin)
    || ![value.normalizedMargin.gm_ttm, value.normalizedMargin.gm_fwd, value.normalizedMargin.normalized_gm].every(finite)
    || !Array.isArray(value.normalizedMargin.adjustment_reasons) || !value.normalizedMargin.adjustment_reasons.every(text))) invalid();
  if (value.exports.some((row) => !object(row) || ![row.export_id, row.actor, row.reason, row.exported_at, row.correlation_id].every(text)
    || row.artifact !== null && (!object(row.artifact) || row.artifact.exportId !== row.export_id
      || typeof row.artifact.sha256 !== "string" || !hash.test(row.artifact.sha256)
      || !Number.isInteger(row.artifact.bytes) || (row.artifact.bytes as number) < 1 || !text(row.artifact.filename)))) invalid();
  return { ...value, ...base } as unknown as Detail;
}
export function createAVMDealRoomClient(current: () => boolean, fetcher: typeof fetch = fetch) {
  async function session() {
    const response = await fetcher("/auth/session", { credentials: "include", cache: "no-store", headers: { accept: "application/json" } });
    if (!current()) throw new SupersededAVMRead();
    if (!response.ok) throw new Error(`登入狀態無法核對（HTTP ${response.status}）。`);
    const value: unknown = await response.json(); if (!current()) throw new SupersededAVMRead();
    if (!object(value) || !text(value.subject)) throw new Error("請重新登入後再讀取 AVM。"); return value.subject;
  }
  async function context() {
    const subject = await session();
    const response = await fetcher("/api/v1/operator/avm/context", { credentials: "include", cache: "no-store" });
    if (!current()) throw new SupersededAVMRead();
    if (!response.ok) throw new Error(`AVM 權威來源無法取得（HTTP ${response.status}）。`);
    const value: unknown = await response.json(); if (!current()) throw new SupersededAVMRead();
    if (payloadContainsSeedData(value)) throw new Error("AVM 不使用示範資料代替目前案件。");
    const result = parseContext(value); const after = await session();
    if (after !== subject || result.subjectId !== subject) throw new SupersededAVMRead();
    return result;
  }
  async function verify(expected: Context) {
    const actual = await context();
    if (!current() || expected.subjectId !== actual.subjectId || expected.contextKey !== actual.contextKey || expected.tenantId !== actual.tenantId) throw new SupersededAVMRead();
  }
  async function request(path: string, expected: Context, init?: RequestInit) {
    await verify(expected);
    let response: Response;
    try { response = await fetcher(`/api/v1/operator/avm${path}`, { ...init, credentials: "include", cache: "no-store" }); }
    catch (failure) { await verify(expected); throw failure; }
    await verify(expected);
    if (!response.ok) throw new Error(`AVM 操作未完成（HTTP ${response.status}）。`);
    let value: unknown;
    try { value = await response.json(); } catch (failure) { await verify(expected); throw failure; }
    await verify(expected);
    if (payloadContainsSeedData(value)) throw new Error("AVM 回應不可使用示範來源。");
    return value;
  }
  return { context, verify,
    async list(expected: Context) {
      const value = await request("/cases", expected);
      if (!object(value) || !Array.isArray(value.items) || value.count !== value.items.length) invalid();
      const actual = parseContext(value.context); if (actual.contextKey !== expected.contextKey) throw new SupersededAVMRead();
      return value.items.map(parseRow);
    },
    async detail(id: string, expected: Context) {
      const detail = parseDetail(await request(`/cases/${encodeURIComponent(id)}`, expected), id);
      if (detail.context.contextKey !== expected.contextKey) throw new SupersededAVMRead(); return detail;
    },
    async create(expected: Context, valuationInput: ValuationInput, key: string) {
      const value = await request("/cases", expected, { method: "POST",
        headers: { "Content-Type": "application/json", "If-Actor-Match": expected.subjectId, "Idempotency-Key": key },
        body: JSON.stringify({ valuationInput }),
      });
      if (!object(value) || !object(value.receipt) || !object(value.detail) || !object(value.detail.case)
        || value.receipt.operation !== "create" || value.receipt.actor !== expected.subjectId
        || value.receipt.sourceId !== value.detail.case.case_id || !text(value.receipt.auditEventId) || !text(value.receipt.correlationId)) invalid();
      const result = parseDetail(value.detail, String(value.receipt.sourceId));
      if (result.context.contextKey !== expected.contextKey) throw new SupersededAVMRead();
      return { detail: result, receipt: value.receipt as unknown as Receipt };
    },
    async command(detail: Detail, operation: Operation, reason: string, reservePrice: number | null, key: string) {
      const path = { normalize: "normalize", value: "value", approve: "finance-approval", build: "dataroom", export: "dataroom/exports" }[operation];
      const value = await request(`/cases/${encodeURIComponent(detail.case.case_id)}/${path}`, detail.context, {
        method: "POST", headers: { "Content-Type": "application/json", "If-Actor-Match": detail.context.subjectId, "Idempotency-Key": key },
        body: JSON.stringify({ expectedVersion: detail.expectedVersion, reason, ...(operation === "approve" ? { reservePrice } : {}) }),
      });
      if (!object(value) || !object(value.receipt) || value.receipt.actor !== detail.context.subjectId || value.receipt.operation !== operation
        || !text(value.receipt.sourceId) || !text(value.receipt.auditEventId) || !text(value.receipt.correlationId)) invalid();
      const next = parseDetail(value.detail, detail.case.case_id);
      if (next.context.contextKey !== detail.context.contextKey) throw new SupersededAVMRead();
      return { detail: next, receipt: value.receipt as unknown as Receipt };
    },
    async download(detail: Detail, artifact: Artifact) {
      await verify(detail.context);
      let response: Response;
      try { response = await fetcher(`/api/v1/operator/avm/cases/${encodeURIComponent(detail.case.case_id)}/dataroom/exports/${encodeURIComponent(artifact.exportId)}`, {
          credentials: "include", cache: "no-store", headers: { "If-Actor-Match": detail.context.subjectId },
        }); } catch (failure) { await verify(detail.context); throw failure; }
      await verify(detail.context);
      if (!response.ok) throw new Error(`匯出讀取未完成（HTTP ${response.status}）。`);
      let bytes: ArrayBuffer;
      try { bytes = await response.arrayBuffer(); } catch (failure) { await verify(detail.context); throw failure; }
      await verify(detail.context);
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (v) => v.toString(16).padStart(2, "0")).join("");
      await verify(detail.context);
      if (digest !== artifact.sha256 || bytes.byteLength !== artifact.bytes || response.headers.get("X-AVM-Export-Id") !== artifact.exportId
        || response.headers.get("X-Content-SHA256") !== digest || !/^avm-export-[a-f0-9-]+\.json$/.test(artifact.filename)) invalid();
      return new Blob([bytes], { type: "application/json" });
    },
  };
}
