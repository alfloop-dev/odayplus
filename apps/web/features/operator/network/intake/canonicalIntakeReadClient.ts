import type {
  CanonicalIdentityPlanningContext, CanonicalIdentityReceipt, CanonicalIdentitySummary,
  CanonicalIntakeAssistedEntryRequestReceipt, CanonicalIntakeDetail, CanonicalIntakePage, CanonicalIntakeReceipt, CanonicalIntakeSummary,
  CanonicalMatchCasePropertyPage, CreateMatchCaseRequest, MatchCaseReceipt,
  DuplicateProposal, IdentityReviewRequest, ListingConfirmation, MatchCaseSummary, MergeProposal, NativeAssistedEntryCommand, SplitProposal, SubmitCanonicalIntakeRequest,
} from "@oday-plus/openapi-client";

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
  AWAITING_SOURCE_AUTHORIZATION: "待來源帳號授權",
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
const timestamp = (v: unknown): v is string => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));

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
        : ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "HIGHLY_RESTRICTED"].includes(String(field.classification))
          && (field.mask_reason_code === undefined || field.mask_reason_code === null)))
    && new Set(v.map((field) => field.field_path)).size === v.length;
}

function detail(v: unknown): v is CanonicalIntakeDetail {
  if (!object(v)) return false;
  const result = v.resolution;
  return intake(v) && (result === undefined || result === null || object(result)
    && uuid(result.listing_id) && version(result.listing_version) && nullableUuid(result.current_revision_id)
    && ["ACTIVE", "REMOVED", "EXPIRED", "STALE", "QUARANTINED", "ARCHIVED"].includes(String(result.lifecycle_state))
    && uuid(result.property_id_at_creation) && uuid(result.effective_property_id) && version(result.property_version)
    && (result.fields === undefined || persistedFields(result.fields)));
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
  command?: { body: unknown; key: string; expectedVersion?: number }): Promise<unknown> {
  signal.throwIfAborted();
  const headers = new Headers(context.headers);
  const correlationId = crypto.randomUUID();
  headers.set("accept", "application/json");
  headers.set("X-Correlation-Id", correlationId);
  if (command) {
    if (command.expectedVersion !== undefined && !version(command.expectedVersion) || !/^[A-Za-z0-9._:-]{16,128}$/.test(command.key)) {
      throw new CanonicalIntakeReadError(422, "CANONICAL_COMMAND_INVALID", correlationId);
    }
    headers.set("content-type", "application/json");
    headers.set("Idempotency-Key", command.key);
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

export async function submitCanonicalIdentity(context: CanonicalIntakeReadContext, command: CanonicalIdentityCommand,
  signal: AbortSignal): Promise<CanonicalIdentityReceipt> {
  // The component captures this exact command once. An uncertain response is
  // retried with the same key, body and versions, never a second proposal.
  const v = await read(context, command.path, signal, command);
  if (!object(v) || !uuid(v.decision_id) || command.decisionId !== undefined && v.decision_id !== command.decisionId
    || v.match_case_id !== command.matchCaseId || !version(v.match_case_version) || !version(v.version)
    || !["PENDING_REVIEW", "APPROVED", "REJECTED", "EXECUTING", "EXECUTED", "FAILED"].includes(String(v.status))
    || !uuid(v.audit_event_id) || !uuid(v.correlation_id) || !nullableUuid(v.job_id)
    || v.job_status !== undefined && !jobState(v.job_status))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIdentityReceipt;
}

export type CanonicalURLCommand = { key: string; body: SubmitCanonicalIntakeRequest & { intake_method: "URL" } };
export async function submitCanonicalURL(context: CanonicalIntakeReadContext, command: CanonicalURLCommand,
  signal: AbortSignal): Promise<CanonicalIntakeReceipt> {
  const v = await read(context, "/api/v1/canonical-intakes", signal, command);
  if (!object(v) || !uuid(v.intake_id) || v.processing_state !== "SUBMITTED" || v.intake_method !== "URL" || !version(v.version)
    || !timestamp(v.submitted_at)
    || ![v.correlation_id, v.transition_id, v.audit_event_id, v.outbox_event_id].every(uuid))
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIntakeReceipt;
}

export type CanonicalAssistedEntryCommand = { key: string; intakeId: string; expectedVersion: number; body: NativeAssistedEntryCommand };
export async function submitCanonicalAssistedEntry(context: CanonicalIntakeReadContext, command: CanonicalAssistedEntryCommand,
  signal: AbortSignal): Promise<CanonicalIntakeAssistedEntryRequestReceipt> {
  if (!uuid(command.intakeId)) throw new CanonicalIntakeReadError(422, "CANONICAL_INTAKE_ID_INVALID", "");
  const v = await read(context, `/api/v1/canonical-intakes/${encodeURIComponent(command.intakeId)}/assisted-entry-completions`, signal, command);
  if (!object(v) || v.intake_id !== command.intakeId || v.status !== "REQUESTED" || !version(v.version)
    || ![v.request_id, v.correlation_id, v.audit_event_id, v.outbox_event_id].every(uuid)
    || !timestamp(v.requested_at)
    || !Array.isArray(v.correction_ids) || v.correction_ids.length < 3 || v.correction_ids.length > 7
    || !v.correction_ids.every(uuid) || new Set(v.correction_ids).size !== v.correction_ids.length)
    throw new CanonicalIntakeReadError(200, "CANONICAL_RESPONSE_INVALID", "");
  return v as CanonicalIntakeAssistedEntryRequestReceipt;
}
