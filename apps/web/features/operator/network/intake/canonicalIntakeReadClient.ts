import type { CanonicalIntakePage, CanonicalIntakeSummary, MatchCaseSummary } from "@oday-plus/openapi-client";

export type CanonicalIntakeReadContext = {
  fetchImpl: typeof fetch;
  contextKey: string;
  headers: HeadersInit;
};

// The native generated state vocabulary includes CANCELLED; the assisted
// workflow's older hand-written label map does not cover that native state.
export const CANONICAL_INTAKE_STAGE_LABEL: Record<CanonicalIntakeSummary["processing_state"], string> = {
  SUBMITTED: "已送出", CHECKING_IDENTITY: "識別檢查", READY: "可決策",
  CHECKING_SOURCE_POLICY: "來源政策", RETRIEVING: "擷取中", AWAITING_ASSISTED_ENTRY: "待人工補錄",
  PARSING: "解析中", MATCHING: "比對中", NEEDS_REVIEW: "待人工覆核", QUARANTINED: "已隔離",
  FAILED: "處理失敗", CANCELLED: "已取消",
};

export class CanonicalIntakeReadError extends Error {
  constructor(public status: number, public code: string, public correlationId: string) {
    super(code);
  }
}

const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const version = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) > 0;
const nullableUuid = (v: unknown) => v === null || uuid(v);
const nullableVersion = (v: unknown) => v === null || version(v);
const text = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0 && v.length <= 200;

function intake(v: unknown): v is CanonicalIntakeSummary {
  return object(v) && uuid(v.intake_id) && uuid(v.tenant_id) && uuid(v.submitter_subject_id)
    && uuid(v.correlation_id) && version(v.version) && typeof v.processing_state === "string"
    && Object.hasOwn(CANONICAL_INTAKE_STAGE_LABEL, v.processing_state)
    && ["URL", "MANUAL", "CSV", "APPROVED_FEED", "OPERATOR_SNAPSHOT"].includes(String(v.intake_method))
    && typeof v.submitted_at === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v.submitted_at)
    && Number.isFinite(Date.parse(v.submitted_at)) && object(v.scope)
    && ["brand_id", "region_id", "assigned_area_id", "heat_zone_id"].every((key) => nullableUuid((v.scope as Record<string, unknown>)[key]));
}

function page(v: unknown): v is CanonicalIntakePage {
  return object(v) && Array.isArray(v.items) && v.items.length <= 25 && v.items.every(intake)
    && new Set(v.items.map((row) => row.intake_id)).size === v.items.length
    && new Set(v.items.map((row) => row.tenant_id)).size <= 1
    && (v.next_cursor === null || typeof v.next_cursor === "string" && v.next_cursor.length > 0 && v.next_cursor.length <= 2048)
    && (v.items.length > 0 || v.next_cursor === null);
}

function matchCase(v: unknown): v is MatchCaseSummary {
  return object(v) && uuid(v.match_case_id) && uuid(v.intake_id) && version(v.version)
    && version(v.intake_version) && text(v.status) && text(v.outcome)
    && Array.isArray(v.candidates) && v.candidates.length <= 100
    && new Set(v.candidates.map((row) => object(row) ? row.match_candidate_id : null)).size === v.candidates.length
    && v.candidates.every((row) => object(row) && uuid(row.match_candidate_id)
      && nullableUuid(row.property_id) && nullableUuid(row.listing_id) && Boolean(row.property_id || row.listing_id)
      && version(row.rank) && nullableVersion(row.observed_property_version) && nullableVersion(row.observed_listing_version)
      && (typeof row.confidence === "number" || typeof row.confidence === "string" && /^(0(?:\.\d+)?|1(?:\.0+)?)$/.test(row.confidence))
      && Number.isFinite(Number(row.confidence)) && Number(row.confidence) >= 0 && Number(row.confidence) <= 1);
}

async function read(context: CanonicalIntakeReadContext, path: string, signal: AbortSignal): Promise<unknown> {
  signal.throwIfAborted();
  const headers = new Headers(context.headers);
  const correlationId = crypto.randomUUID();
  headers.set("accept", "application/json");
  headers.set("X-Correlation-Id", correlationId);
  const response = await context.fetchImpl(path, { headers, cache: "no-store", credentials: "same-origin", signal });
  signal.throwIfAborted();
  const receivedCorrelation = response.headers.get("x-correlation-id") || correlationId;
  if (!response.ok) {
    let code = "CANONICAL_READ_FAILED";
    try {
      const body: unknown = await response.json();
      signal.throwIfAborted();
      if (object(body) && body.detail === "CANONICAL_MATCH_CASE_NOT_FOUND") code = body.detail;
    } catch (error) {
      // Preserve the account-bound transport's rejection across body reads.
      if (!(error instanceof SyntaxError)) throw error;
    }
    throw new CanonicalIntakeReadError(response.status, code, receivedCorrelation);
  }
  const body: unknown = await response.json();
  signal.throwIfAborted();
  return body;
}

export async function readCanonicalIntakePage(context: CanonicalIntakeReadContext, cursor: string | null, signal: AbortSignal) {
  const query = new URLSearchParams({ page_size: "25" });
  if (cursor) query.set("cursor", cursor);
  const value = await read(context, `/api/v1/canonical-intakes?${query}`, signal);
  if (!page(value) || cursor !== null && value.next_cursor === cursor) throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return value;
}

export async function readCanonicalIntakeCase(context: CanonicalIntakeReadContext, intakeId: string, signal: AbortSignal) {
  if (!uuid(intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  const parent = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(intakeId)}`, signal);
  if (!intake(parent) || parent.intake_id !== intakeId) throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  let selected: unknown;
  try { selected = await read(context, `/api/v1/intakes/${encodeURIComponent(intakeId)}/match-case`, signal); }
  catch (error) {
    if (error instanceof CanonicalIntakeReadError && error.status === 404 && error.code === "CANONICAL_MATCH_CASE_NOT_FOUND") return { intake: parent, matchCase: null };
    throw error;
  }
  if (!matchCase(selected) || selected.intake_id !== parent.intake_id) throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  if (selected.intake_version !== parent.version) throw new CanonicalIntakeReadError(409, "CANONICAL_INTAKE_VERSION_CHANGED", "");
  return { intake: parent, matchCase: selected };
}
