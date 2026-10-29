import type {
  CanonicalIdentityPlanningContext, CanonicalIdentityReceipt, CanonicalIdentitySummary,
  CanonicalIntakeAssistedEntryRequestReceipt, CanonicalIntakeDetail, CanonicalIntakePage, CanonicalIntakeReceipt, CanonicalIntakeSummary, CanonicalIntakeWorkflow,
  CanonicalIntakeHistoryPage, CanonicalIntakeSourceInfo, CanonicalIntakeEvidencePage, CanonicalIntakeParserFields, CanonicalIntakeParserRunMetadata,
  CanonicalMatchCasePropertyPage, CreateMatchCaseRequest, MatchCaseReceipt,
  DuplicateProposal, IdentityReviewRequest, ListingConfirmation, MatchCaseSummary, MergeProposal, NativeAssistedEntryCommand, SplitProposal, SubmitCanonicalIntakeRequest,
  NativeReceiptCommitted_CanonicalIntakeReceipt_, NativeReceiptNotObserved,
} from "@oday-plus/openapi-client";

import { CanonicalIntakeReadError, type CanonicalIntakeReadContext } from "./canonicalIntakeTransport";
export { CanonicalIntakeReadError, type CanonicalIntakeReadContext } from "./canonicalIntakeTransport";

// The native generated state vocabulary includes CANCELLED; the assisted
// workflow's older hand-written label map does not cover that native state.
export const CANONICAL_INTAKE_STAGE_LABEL: Record<CanonicalIntakeSummary["processing_state"], string> = {
  SUBMITTED: "已送出", CHECKING_IDENTITY: "識別檢查", READY: "可決策",
  CHECKING_SOURCE_POLICY: "來源政策", RETRIEVING: "擷取中", AWAITING_ASSISTED_ENTRY: "待人工補錄",
  AWAITING_SOURCE_AUTHORIZATION: "待來源帳號授權",
  PARSING: "解析中", MATCHING: "比對中", NEEDS_REVIEW: "待人工覆核", QUARANTINED: "已隔離",
  FAILED: "處理失敗", CANCELLED: "已取消",
};

const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const version = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) > 0;
const nullableUuid = (v: unknown) => v === null || uuid(v);
const nullableVersion = (v: unknown) => v === null || version(v);
const text = (v: unknown, maximum = 200): v is string => typeof v === "string" && v.trim().length > 0 && v.length <= maximum;
const timestamp = (v: unknown): v is string => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
const FIELD_CLASSIFICATIONS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "HIGHLY_RESTRICTED"];

function intake(v: unknown): v is CanonicalIntakeSummary {
  return object(v) && uuid(v.intake_id) && uuid(v.tenant_id) && uuid(v.submitter_subject_id)
    && uuid(v.correlation_id) && version(v.version) && typeof v.processing_state === "string"
    && Object.hasOwn(CANONICAL_INTAKE_STAGE_LABEL, v.processing_state)
    && ["URL", "MANUAL", "CSV", "APPROVED_FEED", "OPERATOR_SNAPSHOT"].includes(String(v.intake_method))
    && timestamp(v.submitted_at) && object(v.scope)
    && ["brand_id", "region_id", "assigned_area_id", "heat_zone_id"].every((key) => nullableUuid((v.scope as Record<string, unknown>)[key]));
}

function page(v: unknown): v is CanonicalIntakePage {
  return object(v) && Array.isArray(v.items) && v.items.length <= 25 && v.items.every(intake)
    && new Set(v.items.map((row) => row.intake_id)).size === v.items.length
    && new Set(v.items.map((row) => row.tenant_id)).size <= 1
    && (v.next_cursor === null || typeof v.next_cursor === "string" && v.next_cursor.length > 0 && v.next_cursor.length <= 2048)
    && (v.items.length > 0 || v.next_cursor === null);
}

function persistedFields(v: unknown): boolean {
  return Array.isArray(v) && v.length <= 100
    && v.every((field) => object(field) && text(field.field_path)
      && (field.classification === null || text(field.classification))
      && (field.effective === null || typeof field.effective === "string" || typeof field.effective === "boolean"
        || typeof field.effective === "number" && Number.isFinite(field.effective))
      && typeof field.masked === "boolean"
      && (field.masked ? field.effective === null && field.mask_reason_code === "FIELD_MASKED"
        : FIELD_CLASSIFICATIONS.includes(String(field.classification))
          && (field.mask_reason_code === undefined || field.mask_reason_code === null)))
    && new Set(v.map((field) => field.field_path)).size === v.length;
}

function workflow(v: unknown): v is CanonicalIntakeWorkflow {
  if (!object(v)) return false;
  return uuid(v.assignment_id) && version(v.assignment_version)
    && typeof v.assignment_status === "string"
    && ["UNASSIGNED", "ASSIGNED", "CLAIMED", "TRANSFERRED", "ESCALATED", "COMPLETED"].includes(v.assignment_status)
    && nullableUuid(v.owner_subject_id) && (v.owner_role === null || text(v.owner_role)) && text(v.queue_key)
    && uuid(v.sla_instance_id) && version(v.sla_version)
    && typeof v.sla_state === "string"
    && ["ON_TRACK", "DUE_SOON", "OVERDUE", "BREACHED", "COMPLETED", "PAUSED"].includes(v.sla_state)
    && text(v.policy_version) && text(v.calendar_version)
    && timestamp(v.started_at) && timestamp(v.due_soon_at) && timestamp(v.due_at) && timestamp(v.breach_at)
    && Date.parse(v.started_at) <= Date.parse(v.due_soon_at) && Date.parse(v.due_soon_at) <= Date.parse(v.due_at)
    && Date.parse(v.due_at) <= Date.parse(v.breach_at);
}

function detail(v: unknown): v is CanonicalIntakeDetail {
  if (!object(v)) return false;
  const result = v.resolution;
  const savedWorkflow = v.workflow;
  const source = v.source_info, history = v.processing_history, evidence = v.processing_evidence;
  return intake(v) && sourceInfo(source) && historyPage(history, v)
    && history.previous_cursor === null && evidencePage(evidence, v, source.source_id) && evidence.previous_cursor === null
    && (savedWorkflow === undefined || savedWorkflow === null || workflow(savedWorkflow))
    && (result === undefined || result === null || object(result)
    && uuid(result.listing_id) && version(result.listing_version) && nullableUuid(result.current_revision_id)
    && ["ACTIVE", "REMOVED", "EXPIRED", "STALE", "QUARANTINED", "ARCHIVED"].includes(String(result.lifecycle_state))
    && uuid(result.property_id_at_creation) && uuid(result.effective_property_id) && version(result.property_version)
    && (result.fields === undefined || persistedFields(result.fields)));
}

const readCursor = (v: unknown): v is string | null => v === null
  || typeof v === "string" && v.length > 0 && v.length <= 2048 && /^[A-Za-z0-9_-]+={0,2}$/.test(v);

function sourceInfo(v: unknown): v is CanonicalIntakeSourceInfo {
  return object(v) && (v.source_id === null || text(v.source_id))
    && (v.recorded_policy_state === null || ["APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED", "SOURCE_BLOCKED", "POLICY_UNKNOWN"].includes(String(v.recorded_policy_state)))
    && timestamp(v.last_transition_at) && (v.completed_at === null || timestamp(v.completed_at))
    && persistedFields(v.url_fields) && Array.isArray(v.url_fields) && v.url_fields.length === 2
    && v.url_fields.every((field) => object(field) && ["originalUrl", "canonicalUrl"].includes(String(field.field_path))
      && (field.effective === null || typeof field.effective === "string")
      && (field.classification === "CONFIDENTIAL" || field.classification === null && field.masked === true));
}

function historyPage(v: unknown, parent: CanonicalIntakeSummary, through?: number): v is CanonicalIntakeHistoryPage {
  if (!object(v) || v.intake_id !== parent.intake_id || v.intake_version !== parent.version
    || !Number.isSafeInteger(v.through_sequence) || Number(v.through_sequence) < 0
    || through !== undefined && v.through_sequence !== through
    || !readCursor(v.next_cursor) || !readCursor(v.previous_cursor) || !Array.isArray(v.items) || v.items.length > 25) return false;
  const items = v.items;
  const valid = items.every((row) => object(row) && uuid(row.transition_id) && version(row.sequence_no)
    && row.sequence_no <= Number(v.through_sequence)
    && (row.from_state === null || typeof row.from_state === "string" && Object.hasOwn(CANONICAL_INTAKE_STAGE_LABEL, row.from_state))
    && typeof row.to_state === "string" && Object.hasOwn(CANONICAL_INTAKE_STAGE_LABEL, row.to_state)
    && nullableUuid(row.actor_subject_id) && (row.service_principal === null || text(row.service_principal))
    && !(row.actor_subject_id !== null && row.service_principal !== null)
    && text(row.permission) && (row.reason_code === null || text(row.reason_code))
    && nullableVersion(row.expected_version) && version(row.resulting_version) && row.resulting_version <= parent.version
    && (row.resulting_version !== parent.version || row.to_state === parent.processing_state)
    && nullableUuid(row.job_id) && nullableVersion(row.source_policy_version) && nullableUuid(row.parser_release_id)
    && nullableUuid(row.source_snapshot_id) && nullableUuid(row.match_case_id) && uuid(row.correlation_id)
    && nullableUuid(row.causation_id) && timestamp(row.occurred_at));
  if (!valid || new Set(items.map((row) => row.transition_id)).size !== items.length
    || !items.every((row, i) => i === 0 || row.sequence_no > items[i - 1].sequence_no)) return false;
  if (!items.length) return v.through_sequence === 0 && v.next_cursor === null && v.previous_cursor === null;
  return Boolean(v.next_cursor) === (items.at(-1).sequence_no < Number(v.through_sequence))
    && (v.previous_cursor === null || items[0].sequence_no > 1);
}

const onlyKeys = (v: Record<string, unknown>, keys: readonly string[]) => Object.keys(v).every((key) => keys.includes(key));
const snapshotMetadataKeys = ["source_snapshot_id", "intake_id", "source_id", "content_sha256", "media_type", "byte_length",
  "captured_at", "observed_at", "stored_at", "capture_method", "version", "retention_class", "purge_after", "legal_hold",
  "object_generation", "residency_mode"];
const parserMetadataKeys = ["parser_run_id", "intake_id", "source_snapshot_id", "parser_release_id", "source_id", "status",
  "version", "created_at", "started_at", "completed_at", "correlation_id", "semantic_version", "package_name", "output_schema_version"];

function evidencePage(v: unknown, parent: CanonicalIntakeSummary, sourceId: string | null, through?: string): v is CanonicalIntakeEvidencePage {
  if (!object(v) || !onlyKeys(v, ["intake_id", "intake_version", "through_at", "observed_at", "items", "next_cursor", "previous_cursor"])
    || v.intake_id !== parent.intake_id || v.intake_version !== parent.version
    || !timestamp(v.through_at) || !timestamp(v.observed_at) || Date.parse(v.through_at) > Date.parse(v.observed_at)
    || through !== undefined && v.through_at !== through || !readCursor(v.next_cursor) || !readCursor(v.previous_cursor)
    || !Array.isArray(v.items) || v.items.length > 25) return false;
  const items = v.items;
  const valid = items.every((item) => {
    if (!object(item) || !onlyKeys(item, ["kind", "recorded_at", "metadata"]) || !timestamp(item.recorded_at)
      || Date.parse(item.recorded_at) > Date.parse(v.through_at as string) || !object(item.metadata)) return false;
    const m = item.metadata;
    if (m.intake_id !== parent.intake_id || sourceId === null || m.source_id !== sourceId || !version(m.version)) return false;
    if (item.kind === "SNAPSHOT") return onlyKeys(m, snapshotMetadataKeys) && uuid(m.source_snapshot_id)
      && typeof m.content_sha256 === "string" && /^[0-9a-f]{64}$/.test(m.content_sha256)
      && text(m.media_type, 256) && Number.isSafeInteger(m.byte_length) && Number(m.byte_length) >= 0
      && timestamp(m.captured_at) && timestamp(m.observed_at) && timestamp(m.stored_at) && m.stored_at === item.recorded_at
      && typeof m.capture_method === "string" && ["SERVER_RETRIEVAL", "OPERATOR_UPLOAD", "APPROVED_FEED", "MANUAL_ENTRY"].includes(m.capture_method)
      && text(m.retention_class, 64) && (m.purge_after === null || timestamp(m.purge_after)) && typeof m.legal_hold === "boolean"
      && (m.object_generation === null || typeof m.object_generation === "string" && /^[1-9][0-9]{0,18}$/.test(m.object_generation))
      && typeof m.residency_mode === "string" && ["TW_ONLY", "APPROVED_APAC_DR"].includes(m.residency_mode);
    if (item.kind === "PARSER_RUN") return onlyKeys(m, parserMetadataKeys) && uuid(m.parser_run_id)
      && uuid(m.source_snapshot_id) && uuid(m.parser_release_id) && uuid(m.correlation_id)
      && typeof m.status === "string" && ["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(m.status)
      && timestamp(m.created_at) && m.created_at === item.recorded_at
      && (m.started_at === null || timestamp(m.started_at)) && (m.completed_at === null || timestamp(m.completed_at))
      && text(m.semantic_version, 128) && text(m.package_name, 256) && text(m.output_schema_version, 256);
    return false;
  });
  if (!valid || new Set(items.map((item) => item.kind + ":" + (item.kind === "SNAPSHOT"
    ? item.metadata.source_snapshot_id : item.metadata.parser_run_id))).size !== items.length) return false;
  if (!items.every((item, index) => index === 0 || Date.parse(item.recorded_at) >= Date.parse(items[index - 1].recorded_at))) return false;
  return items.length > 0 || v.next_cursor === null && v.previous_cursor === null;
}

function matchCase(v: unknown): v is MatchCaseSummary {
  return object(v) && uuid(v.match_case_id) && uuid(v.intake_id) && version(v.version)
    && version(v.intake_version) && text(v.status) && text(v.outcome)
    && (v.identity_decision_ids === undefined || Array.isArray(v.identity_decision_ids)
      && v.identity_decision_ids.length <= 100 && v.identity_decision_ids.every(uuid)
      && new Set(v.identity_decision_ids).size === v.identity_decision_ids.length)
    && Array.isArray(v.candidates) && v.candidates.length <= 100
    && new Set(v.candidates.map((row) => object(row) ? row.match_candidate_id : null)).size === v.candidates.length
    && v.candidates.every((row) => object(row) && uuid(row.match_candidate_id)
      && nullableUuid(row.property_id) && nullableUuid(row.listing_id) && Boolean(row.property_id || row.listing_id)
      && version(row.rank) && nullableVersion(row.observed_property_version) && nullableVersion(row.observed_listing_version)
      && (row.manual_context_version === undefined || nullableVersion(row.manual_context_version))
      && (typeof row.confidence === "number" || typeof row.confidence === "string" && /^(0(?:\.\d+)?|1(?:\.0+)?)$/.test(row.confidence))
      && Number.isFinite(Number(row.confidence)) && Number(row.confidence) >= 0 && Number(row.confidence) <= 1);
}

async function read(context: CanonicalIntakeReadContext, path: string, signal: AbortSignal,
  command?: { body: unknown; key: string; expectedVersion?: number },
  receiptReference?: { key: string; expectedVersion?: number }): Promise<unknown> {
  signal.throwIfAborted();
  const headers = new Headers(context.headers);
  const correlationId = crypto.randomUUID();
  headers.set("accept", "application/json");
  headers.set("X-Correlation-Id", correlationId);
  const reference = command ?? receiptReference;
  if (reference) {
    if (command && receiptReference || reference.expectedVersion !== undefined && !version(reference.expectedVersion)
      || !/^[A-Za-z0-9._:-]{16,128}$/.test(reference.key)) {
      throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", correlationId);
    }
    headers.set("Idempotency-Key", reference.key);
  }
  if (command) {
    headers.set("content-type", "application/json");
    if (command.expectedVersion !== undefined) headers.set("If-Match", `W/"${command.expectedVersion}"`);
  }
  const response = await context.fetchImpl(path, { headers, cache: "no-store", credentials: "same-origin", signal,
    ...(command ? { method: "POST", body: JSON.stringify(command.body) } : {}) });
  signal.throwIfAborted();
  const receivedCorrelation = response.headers.get("x-correlation-id") || correlationId;
  if (!response.ok) {
    let code = command ? "CANONICAL_COMMAND_FAILED" : "CANONICAL_READ_FAILED";
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

export type CanonicalReceiptObservation<R> = NativeReceiptNotObserved
  | (Omit<NativeReceiptCommitted_CanonicalIntakeReceipt_, "receipt"> & { receipt: R });

async function readReceipt<R>(context: CanonicalIntakeReadContext, path: string, signal: AbortSignal,
  reference: { key: string; expectedVersion?: number }, status: 200 | 201 | 202,
  validate: (value: unknown) => R): Promise<CanonicalReceiptObservation<R>> {
  // The same scoped transport carries only an exact reference. A receipt
  // observation never activates read()'s business-POST/body branch.
  const value = await read(context, path, signal, undefined, reference);
  if (!object(value) || !timestamp(value.observed_at))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  if (value.observation === "NOT_OBSERVED"
    && Object.keys(value).every((key) => ["observation", "observed_at"].includes(key))) return value as NativeReceiptNotObserved;
  if (value.observation !== "RECEIPT_COMMITTED" || value.original_http_status !== status
    || !Object.keys(value).every((key) => ["observation", "observed_at", "original_http_status", "receipt"].includes(key)))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return { observation: "RECEIPT_COMMITTED", observed_at: value.observed_at,
    original_http_status: status, receipt: validate(value.receipt) };
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
  const parent = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(intakeId)}?include_source=true&include_history=true&include_evidence=true`, signal);
  if (!detail(parent) || parent.intake_id !== intakeId) throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
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

export async function readCanonicalIntakeHistory(context: CanonicalIntakeReadContext, parent: CanonicalIntakeSummary,
  cursor: string, through: number, signal: AbortSignal): Promise<CanonicalIntakeHistoryPage> {
  if (!uuid(parent.intake_id) || !version(parent.version) || cursor === null || !readCursor(cursor)
    || !Number.isSafeInteger(through) || through < 1) throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  const query = new URLSearchParams({ intake_version: String(parent.version), cursor });
  const v = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(parent.intake_id)}/processing-history?${query}`, signal);
  if (object(v) && version(v.intake_version) && v.intake_version !== parent.version)
    throw new CanonicalIntakeReadError(409, "CANONICAL_INTAKE_VERSION_CHANGED", "");
  if (!historyPage(v, parent, through) || v.next_cursor === cursor || v.previous_cursor === cursor)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v;
}

export async function readCanonicalIntakeEvidence(context: CanonicalIntakeReadContext, parent: CanonicalIntakeDetail,
  cursor: string, through: string, signal: AbortSignal): Promise<CanonicalIntakeEvidencePage> {
  if (!uuid(parent.intake_id) || !version(parent.version) || cursor === null || !readCursor(cursor) || !timestamp(through))
    throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  const query = new URLSearchParams({ intake_version: String(parent.version), cursor });
  const v = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(parent.intake_id)}/processing-evidence?${query}`, signal);
  if (object(v) && version(v.intake_version) && v.intake_version !== parent.version)
    throw new CanonicalIntakeReadError(409, "CANONICAL_INTAKE_VERSION_CHANGED", "");
  if (!evidencePage(v, parent, parent.source_info?.source_id ?? null, through) || v.next_cursor === cursor || v.previous_cursor === cursor)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v;
}

export async function readCanonicalIntakeParserFields(context: CanonicalIntakeReadContext, parent: CanonicalIntakeDetail,
  selected: CanonicalIntakeParserRunMetadata, signal: AbortSignal): Promise<CanonicalIntakeParserFields> {
  if (!uuid(parent.intake_id) || !version(parent.version) || selected.intake_id !== parent.intake_id
    || !uuid(selected.parser_run_id) || !version(selected.version) || !uuid(selected.source_snapshot_id)
    || !uuid(selected.parser_release_id) || !text(selected.source_id, 1024)
    || selected.source_id !== parent.source_info?.source_id || !timestamp(selected.created_at))
    throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  const query = new URLSearchParams({ intake_version: String(parent.version), parser_run_version: String(selected.version) });
  const v = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(parent.intake_id)}/parser-runs/${encodeURIComponent(selected.parser_run_id)}/fields?${query}`, signal);
  const scalar = (x: unknown) => x === null || typeof x === "string" && x.length <= 1_048_576
    || typeof x === "number" && Number.isFinite(x);
  const fieldKeys = ["correction_id", "field_path", "classification", "status", "version", "identity_affecting",
    "proposed_by", "created_at", "reviewed_by", "reviewed_at", "parsed", "normalized", "corrected", "confidence",
    "correction_reason", "masked", "mask_reason_code"];
  const fields = object(v) ? v.fields : null;
  const validFields = Array.isArray(fields) && fields.length >= 3 && fields.length <= 7 && fields.every((field) =>
    object(field) && Object.keys(field).every((key) => fieldKeys.includes(key))
    && uuid(field.correction_id) && typeof field.field_path === "string"
    && ["address", "rent", "areaPing", "providerListingId", "floor", "listingType", "listingStatus"].includes(field.field_path)
    && (field.classification === null || text(field.classification))
    && typeof field.status === "string" && ["PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED", "REVERSED"].includes(field.status)
    && version(field.version) && typeof field.identity_affecting === "boolean" && uuid(field.proposed_by)
    && timestamp(field.created_at) && nullableUuid(field.reviewed_by)
    && (field.reviewed_at === null || timestamp(field.reviewed_at))
    && scalar(field.parsed) && scalar(field.normalized) && scalar(field.corrected)
    && (field.confidence === null || typeof field.confidence === "number" && Number.isFinite(field.confidence)
      && field.confidence >= 0 && field.confidence <= 1)
    && (field.correction_reason === null || text(field.correction_reason, 16_384)) && typeof field.masked === "boolean"
    && (field.masked ? [field.parsed, field.normalized, field.corrected, field.confidence, field.correction_reason].every((x) => x === null)
      && field.mask_reason_code === "FIELD_MASKED"
      : FIELD_CLASSIFICATIONS.includes(String(field.classification))
        && (field.mask_reason_code === null || field.mask_reason_code === undefined)))
    && new Set(fields.map((field) => field.correction_id)).size === fields.length
    && new Set(fields.map((field) => field.field_path)).size === fields.length
    && ["address", "rent", "areaPing"].every((path) => fields.some((field) => field.field_path === path));
  if (!object(v) || !Object.keys(v).every((key) => ["intake_id", "intake_version", "source_id", "parser_run_id",
    "parser_run_version", "status", "parser_job_id", "parser_job_version", "job_status", "source_snapshot_id",
    "parser_release_id", "observed_at", "fields"].includes(key))
    || v.intake_id !== parent.intake_id || v.intake_version !== parent.version
    || v.parser_run_id !== selected.parser_run_id || v.parser_run_version !== selected.version
    || v.source_snapshot_id !== selected.source_snapshot_id || v.parser_release_id !== selected.parser_release_id
    || v.source_id !== selected.source_id || v.status !== selected.status
    || !uuid(v.parser_job_id) || !version(v.parser_job_version)
    || typeof v.job_status !== "string" || !["QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED", "CANCELLED"].includes(v.job_status)
    || !timestamp(v.observed_at) || Date.parse(v.observed_at) < Date.parse(selected.created_at) || !validFields)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIntakeParserFields;
}

const rows = (v: unknown, maximum: number, valid: (row: Record<string, unknown>) => boolean): v is Record<string, unknown>[] =>
  Array.isArray(v) && v.length <= maximum && v.every((row) => object(row) && valid(row));

export async function readCanonicalMatchCaseProperties(context: CanonicalIntakeReadContext, intake: CanonicalIntakeSummary,
  cursor: string | null, signal: AbortSignal): Promise<CanonicalMatchCasePropertyPage> {
  if (!uuid(intake.intake_id) || !version(intake.version) || cursor !== null && !uuid(cursor))
    throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  const query = new URLSearchParams({ page_size: "20" });
  if (cursor) query.set("cursor", cursor);
  const v = await read(context, `/api/v1/intakes/${encodeURIComponent(intake.intake_id)}/match-case/property-options?${query}`, signal);
  if (!object(v) || v.intake_id !== intake.intake_id || !version(v.intake_version) || !nullableUuid(v.next_cursor)
    || !rows(v.items, 25, (row) => uuid(row.property_id) && version(row.version) && nullableUuid(row.region_id)
      && (row.normalized_address === null || typeof row.normalized_address === "string") && typeof row.address_masked === "boolean"
      && (!row.address_masked || row.normalized_address === null))
    || new Set(v.items.map((row) => row.property_id)).size !== v.items.length || !v.items.length && v.next_cursor !== null)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  if (v.intake_version !== intake.version) throw new CanonicalIntakeReadError(409, "CANONICAL_INTAKE_VERSION_CHANGED", "");
  return v as CanonicalMatchCasePropertyPage;
}

export type CanonicalMatchCaseCommand = { intakeId: string; expectedVersion: number; key: string;
  body: CreateMatchCaseRequest & { outcome: "POSSIBLE_MATCH" } };
export async function submitCanonicalMatchCase(context: CanonicalIntakeReadContext, command: CanonicalMatchCaseCommand,
  signal: AbortSignal): Promise<MatchCaseReceipt> {
  if (!uuid(command.intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  const append = command.body.append_to_case;
  if (append && (!uuid(append.match_case_id) || !version(append.version)))
    throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  const v = await read(context, `/api/v1/intakes/${encodeURIComponent(command.intakeId)}/match-case`, signal, command);
  return matchCaseReceipt(v, command);
}

function matchCaseReceipt(v: unknown, command: CanonicalMatchCaseCommand): MatchCaseReceipt {
  const append = command.body.append_to_case;
  if (!object(v) || v.intake_id !== command.intakeId || !uuid(v.match_case_id) || v.status !== "PENDING_REVIEW" || !version(v.version)
    || !version(v.intake_version) || v.intake_version < command.expectedVersion || v.intake_version > command.expectedVersion + 1
    || !uuid(v.audit_event_id) || !uuid(v.correlation_id) || !Array.isArray(v.candidate_ids)
    || v.candidate_ids.length !== command.body.candidates.length || !v.candidate_ids.every(uuid)
    || new Set(v.candidate_ids).size !== v.candidate_ids.length)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  if (append && (v.match_case_id !== append.match_case_id || v.version !== append.version + 1 || v.intake_version !== command.expectedVersion))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as MatchCaseReceipt;
}

export async function readCanonicalMatchCaseReceipt(context: CanonicalIntakeReadContext, command: CanonicalMatchCaseCommand,
  signal: AbortSignal): Promise<CanonicalReceiptObservation<MatchCaseReceipt>> {
  if (!uuid(command.intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  return readReceipt(context, `/api/v1/intakes/${encodeURIComponent(command.intakeId)}/match-case/receipt`, signal,
    command, 201, (value) => matchCaseReceipt(value, command));
}
const versions = (v: unknown): v is Record<string, number> => object(v)
  && Object.keys(v).length <= 1000 && Object.entries(v).every(([key, value]) => uuid(key) && version(value));
const hash = (v: unknown) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const inputBinding = (v: unknown) => object(v) && uuid(v.parser_job_id) && uuid(v.parser_run_id)
  && version(v.parser_run_version) && uuid(v.source_snapshot_id) && hash(v.original_input_sha256)
  && hash(v.normalized_payload_sha256) && versions(v.expected_correction_versions);
const scalar = (v: unknown) => v === null || typeof v === "string" || typeof v === "number" && Number.isFinite(v);
const states = ["DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED", "REVERSAL_PENDING", "REVERSED", "SUPERSEDED"];
const jobState = (v: unknown) => v === null || typeof v === "string"
  && ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRYING", "CANCELLED", "DEAD_LETTER"].includes(v);

function planning(v: unknown, requireInput: boolean): v is CanonicalIdentityPlanningContext {
  if (!object(v) || !uuid(v.match_case_id) || !uuid(v.intake_id) || !version(v.match_case_version)
    || !version(v.intake_version) || !rows(v.anchors, 100, (r) => uuid(r.match_candidate_id)
      && uuid(r.effective_property_id) && nullableUuid(r.listing_id) && nullableVersion(r.listing_version))
    || !rows(v.properties, 100, (r) => uuid(r.property_id) && version(r.version) && text(r.status))
    || !rows(v.listings, 1000, (r) => uuid(r.listing_id) && nullableUuid(r.property_id) && version(r.version) && text(r.lifecycle_state))
    || !rows(v.source_identity_edges, 1000, (r) => uuid(r.edge_id) && uuid(r.property_id) && nullableUuid(r.listing_id) && version(r.edge_version))
    || !rows(v.candidate_sites, 1000, (r) => uuid(r.candidate_site_id) && uuid(r.property_id) && version(r.version) && text(r.status) && text(r.target_format_code))) return false;
  for (const [name, key] of [["properties", "property_id"], ["listings", "listing_id"], ["source_identity_edges", "edge_id"], ["candidate_sites", "candidate_site_id"]]) {
    const entries = v[name] as Record<string, unknown>[];
    if (new Set(entries.map((r) => r[key])).size !== entries.length) return false;
  }
  const input = v.listing_input;
  if (!requireInput && input == null) return true;
  if (!object(input) || !uuid(input.parser_job_id) || !uuid(input.parser_run_id) || !version(input.parser_run_version)
    || !uuid(input.source_snapshot_id) || !hash(input.original_input_sha256) || !hash(input.normalized_payload_sha256)
    || !timestamp(input.manual_entry_valid_until)
    || !Array.isArray(input.proposed_correction_ids) || input.proposed_correction_ids.length > 100
    || !input.proposed_correction_ids.every(uuid)
    || !rows(input.fields, 100, (r) => uuid(r.correction_id) && text(r.field_path) && text(r.classification)
      && version(r.version) && uuid(r.proposed_by) && typeof r.identity_affecting === "boolean"
      && ["PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED", "REVERSED"].includes(String(r.status))
      && scalar(r.parsed) && scalar(r.normalized) && scalar(r.corrected)
      && (r.masked === undefined || typeof r.masked === "boolean")
      && (!r.masked || r.parsed === null && r.normalized === null && r.corrected === null))) return false;
  return new Set(input.fields.map((r) => (r as Record<string, unknown>).correction_id)).size === input.fields.length;
}

export async function readCanonicalIdentityContext(context: CanonicalIntakeReadContext, selected: MatchCaseSummary, signal: AbortSignal, includeListingInput = true) {
  const value = await read(context, `/api/v1/match-cases/${encodeURIComponent(selected.match_case_id)}/identity-context?include_listing_input=${includeListingInput}`, signal);
  if (!planning(value, includeListingInput) || value.match_case_id !== selected.match_case_id || value.intake_id !== selected.intake_id)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  if (value.match_case_version !== selected.version || value.intake_version !== selected.intake_version)
    throw new CanonicalIntakeReadError(409, "CANONICAL_INTAKE_VERSION_CHANGED", "");
  if (new Set(value.anchors.map((r) => r.match_candidate_id)).size !== value.anchors.length
    || value.anchors.length !== selected.candidates.length
    || value.anchors.some((r) => !selected.candidates.some((c) => c.match_candidate_id === r.match_candidate_id && c.listing_id === r.listing_id)))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return value;
}

export async function readCanonicalIdentityDecision(context: CanonicalIntakeReadContext, selected: MatchCaseSummary,
  decisionId: string, signal: AbortSignal): Promise<CanonicalIdentitySummary> {
  if (!uuid(decisionId) || !selected.identity_decision_ids?.includes(decisionId))
    throw new CanonicalIntakeReadError(422, "CANONICAL_DECISION_SELECTION_REQUIRED", "");
  const v = await read(context, `/api/v1/canonical-identity-decisions/${encodeURIComponent(decisionId)}`, signal);
  if (!object(v) || v.decision_id !== decisionId || v.match_case_id !== selected.match_case_id || v.intake_id !== selected.intake_id
    || !version(v.version) || !version(v.match_case_version) || !version(v.intake_version) || !states.includes(String(v.status))
    || !["MERGE", "SPLIT", "CREATE", "REVISE", "DUPLICATE"].includes(String(v.decision_type))
    || !nullableUuid(v.proposer_subject_id) || !nullableUuid(v.reviewer_subject_id) || !nullableUuid(v.job_id)
    || !nullableVersion(v.job_version) || !jobState(v.job_status) || !object(v.plan)
    || ![v.plan.expected_property_versions, v.plan.expected_listing_versions, v.plan.expected_edge_versions].every(versions)
    || !rows(v.plan.candidate_reassignment_plan, 1000, (r) => uuid(r.candidate_site_id) && version(r.expected_version)
      && ["KEEP_HISTORICAL", "REQUIRE_REVIEW", "REASSIGN"].includes(String(r.disposition)) && nullableUuid(r.target_property_id))
    || ["CREATE", "REVISE", "DUPLICATE"].includes(String(v.decision_type))
      && (!version(v.plan.expected_intake_version) || !inputBinding(v.plan.input_binding))
    || v.decision_type === "MERGE" && (!uuid(v.plan.target_property_id) || !Array.isArray(v.plan.source_property_ids)
      || v.plan.source_property_ids.length > 100 || !v.plan.source_property_ids.every(uuid))
    || v.decision_type === "SPLIT" && (!uuid(v.plan.source_property_id) || !rows(v.plan.partitions, 100, (r) =>
      uuid(r.target_property_id) && Array.isArray(r.source_identity_edge_ids) && r.source_identity_edge_ids.length <= 1000 && r.source_identity_edge_ids.every(uuid)))
    || (v.proposer_service_principal == null ? !uuid(v.proposer_subject_id)
      : v.proposer_service_principal !== "native-intake-identity" || v.proposer_subject_id !== null
        || v.reviewer_subject_id !== null || v.decision_type !== "DUPLICATE"))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIdentitySummary;
}

export type CanonicalListingProposal = ListingConfirmation | DuplicateProposal;
export type CanonicalGraphProposal = MergeProposal | SplitProposal;
export type CanonicalIdentityCommand = { path: string; key: string; expectedVersion: number;
  body: CanonicalListingProposal | CanonicalGraphProposal | IdentityReviewRequest; matchCaseId: string; decisionId?: string };

function identityCommandPath(command: CanonicalIdentityCommand): string {
  const path = command.decisionId === undefined
    ? `/api/v1/match-cases/${encodeURIComponent(command.matchCaseId)}/identity-decisions`
    : `/api/v1/canonical-identity-decisions/${encodeURIComponent(command.decisionId)}/actions/review`;
  if (!uuid(command.matchCaseId) || command.decisionId !== undefined && !uuid(command.decisionId) || command.path !== path)
    throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", "");
  return path;
}

export async function submitCanonicalIdentity(context: CanonicalIntakeReadContext, command: CanonicalIdentityCommand,
  signal: AbortSignal): Promise<CanonicalIdentityReceipt> {
  // The component captures this exact command once. An uncertain response is
  // retried with the same key, body and versions, never a second proposal.
  const v = await read(context, identityCommandPath(command), signal, command);
  return identityReceipt(v, command);
}

function identityReceipt(v: unknown, command: CanonicalIdentityCommand): CanonicalIdentityReceipt {
  if (!object(v) || !uuid(v.decision_id) || command.decisionId !== undefined && v.decision_id !== command.decisionId
    || v.match_case_id !== command.matchCaseId || !version(v.match_case_version) || !version(v.version)
    || !["PENDING_REVIEW", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED"].includes(String(v.status))
    || !uuid(v.audit_event_id) || !uuid(v.correlation_id) || !nullableUuid(v.job_id)
    || v.job_status !== undefined && !jobState(v.job_status))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIdentityReceipt;
}

export async function readCanonicalIdentityReceipt(context: CanonicalIntakeReadContext, command: CanonicalIdentityCommand,
  signal: AbortSignal): Promise<CanonicalReceiptObservation<CanonicalIdentityReceipt>> {
  return readReceipt(context, identityCommandPath(command) + "/receipt", signal, command,
    command.decisionId === undefined ? 201 : 200, (value) => identityReceipt(value, command));
}

export type CanonicalURLCommand = { key: string; body: SubmitCanonicalIntakeRequest & { intake_method: "URL" } };
export async function submitCanonicalURL(context: CanonicalIntakeReadContext, command: CanonicalURLCommand,
  signal: AbortSignal): Promise<CanonicalIntakeReceipt> {
  const v = await read(context, "/api/v1/canonical-intakes", signal, command);
  return urlReceipt(v);
}

function urlReceipt(v: unknown): CanonicalIntakeReceipt {
  if (!object(v) || !uuid(v.intake_id) || v.processing_state !== "SUBMITTED" || v.intake_method !== "URL" || !version(v.version)
    || !timestamp(v.submitted_at)
    || ![v.correlation_id, v.transition_id, v.audit_event_id, v.outbox_event_id].every(uuid))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIntakeReceipt;
}

export async function readCanonicalURLReceipt(context: CanonicalIntakeReadContext, command: CanonicalURLCommand,
  signal: AbortSignal): Promise<CanonicalReceiptObservation<CanonicalIntakeReceipt>> {
  return readReceipt(context, "/api/v1/canonical-intakes/submissions/receipt", signal, command, 201, urlReceipt);
}

export type CanonicalAssistedEntryCommand = { key: string; intakeId: string; expectedVersion: number; body: NativeAssistedEntryCommand };
export async function submitCanonicalAssistedEntry(context: CanonicalIntakeReadContext, command: CanonicalAssistedEntryCommand,
  signal: AbortSignal): Promise<CanonicalIntakeAssistedEntryRequestReceipt> {
  if (!uuid(command.intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  const v = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(command.intakeId)}/assisted-entry-completions`, signal, command);
  return assistedEntryReceipt(v, command);
}

function assistedEntryReceipt(v: unknown, command: CanonicalAssistedEntryCommand): CanonicalIntakeAssistedEntryRequestReceipt {
  if (!object(v) || v.intake_id !== command.intakeId || v.status !== "REQUESTED" || !version(v.version)
    || ![v.request_id, v.correlation_id, v.audit_event_id, v.outbox_event_id].every(uuid)
    || !timestamp(v.requested_at)
    || !Array.isArray(v.correction_ids) || v.correction_ids.length < 3 || v.correction_ids.length > 7
    || !v.correction_ids.every(uuid) || new Set(v.correction_ids).size !== v.correction_ids.length)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIntakeAssistedEntryRequestReceipt;
}

export async function readCanonicalAssistedEntryReceipt(context: CanonicalIntakeReadContext, command: CanonicalAssistedEntryCommand,
  signal: AbortSignal): Promise<CanonicalReceiptObservation<CanonicalIntakeAssistedEntryRequestReceipt>> {
  if (!uuid(command.intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  return readReceipt(context, `/api/v1/canonical-intakes/${encodeURIComponent(command.intakeId)}/assisted-entry-completions/receipt`,
    signal, command, 202, (value) => assistedEntryReceipt(value, command));
}
