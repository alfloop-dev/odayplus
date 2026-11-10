import { describe, expect, it, vi } from "vitest";
import {
  CanonicalIntakeReadError, readCanonicalIntakeCase, readCanonicalIntakePage,
  readCanonicalMatchCaseProperties, submitCanonicalMatchCase,
  readCanonicalIntakeHistory,
  readCanonicalIntakeEvidence,
  readCanonicalIntakeParserFields,
  readCanonicalURLReceipt, readCanonicalMatchCaseReceipt, readCanonicalIdentityReceipt, readCanonicalAssistedEntryReceipt,
  canonicalMatchCaseReceiptReference,
  type CanonicalURLCommand, type CanonicalIdentityCommand, type CanonicalAssistedEntryCommand,
  type CanonicalIntakeReadContext, type CanonicalMatchCaseCommand,
} from "../canonicalIntakeReadClient";

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
const summary = (n = 1) => ({ intake_id: id(n), tenant_id: id(10), submitter_subject_id: id(11), correlation_id: id(12),
  intake_method: "MANUAL" as const, processing_state: "NEEDS_REVIEW" as const, version: 3, submitted_at: "2026-10-15T08:00:00Z",
  scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null },
  source_info: { source_id: null, recorded_policy_state: null, last_transition_at: "2026-10-15T08:00:00Z", completed_at: null,
    url_fields: ["originalUrl", "canonicalUrl"].map((field_path) => ({ field_path, classification: "CONFIDENTIAL",
      effective: null, masked: true, mask_reason_code: "FIELD_MASKED" as const })) },
  processing_history: { intake_id: id(n), intake_version: 3, through_sequence: 0, items: [], next_cursor: null, previous_cursor: null },
  processing_evidence: { intake_id: id(n), intake_version: 3, through_at: "2026-10-15T08:00:00Z", observed_at: "2026-10-15T08:00:00Z", items: [], next_cursor: null, previous_cursor: null } });
const caseSummary = () => ({ match_case_id: id(20), intake_id: id(1), intake_version: 3, version: 1,
  status: "PENDING_REVIEW", outcome: "POSSIBLE_MATCH", candidates: [{ match_candidate_id: id(21),
    property_id: id(22), listing_id: null, rank: 1, confidence: "0.9000", observed_property_version: 2, observed_listing_version: null }] });
const resolution = () => ({ listing_id: id(51), listing_version: 14, lifecycle_state: "ACTIVE", current_revision_id: id(52),
  property_id_at_creation: id(41), effective_property_id: id(42), property_version: 12 });
const workflow = () => ({ assignment_id: id(61), assignment_version: 2, assignment_status: "UNASSIGNED", owner_subject_id: null,
  owner_role: null, queue_key: "existing-synthetic-queue", sla_instance_id: id(62), sla_version: 3, sla_state: "ON_TRACK",
  policy_version: "existing-policy-v1", calendar_version: "existing-calendar-v1", started_at: "2026-10-15T08:00:00Z",
  due_soon_at: "2026-10-15T09:00:00Z", due_at: "2026-10-15T10:00:00Z", breach_at: "2026-10-15T11:00:00Z" });
function context(...responses: Response[]) {
  const fetchImpl = vi.fn<typeof fetch>(); responses.forEach((response) => fetchImpl.mockResolvedValueOnce(response));
  const value: CanonicalIntakeReadContext = { fetchImpl, contextKey: "synthetic-A", headers: { "X-Operator-Role": "expansion-manager" } };
  return { value, fetchImpl, signal: new AbortController().signal };
}

describe("canonical Intake read transport", () => {
  it.each([undefined, null, workflow()])("accepts only the supplied optional workflow facts: %j", async (saved) => {
    const { value, signal } = context(json({ ...summary(), workflow: saved }), json(caseSummary()));
    expect((await readCanonicalIntakeCase(value, id(1), signal)).intake.workflow).toEqual(saved);
  });

  it.each([
    { assignment_id: "ASG-legacy" }, { assignment_version: 0 }, { assignment_status: "INVENTED" }, { assignment_status: ["ASSIGNED"] },
    { owner_subject_id: "unknown" }, { owner_role: 7 }, { queue_key: " " },
    { sla_instance_id: null }, { sla_version: 1.5 }, { sla_state: "INVENTED" }, { sla_state: ["ON_TRACK"] },
    { policy_version: "" }, { calendar_version: " " }, { due_soon_at: null },
    { started_at: "2026-10-15T08:00:00" }, { due_at: "2026-10-15T10:00:00" },
    { due_soon_at: "2026-10-15T07:00:00Z" }, { due_at: "2026-10-15T08:30:00Z" },
    { breach_at: "2026-10-15T09:30:00Z" },
  ])("refuses malformed saved workflow before fetching another resource: %j", async (change) => {
    const { value, signal, fetchImpl } = context(json({ ...summary(), workflow: { ...workflow(), ...change } }), json(caseSummary()));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([{ match_case_id: id(99) }, { version: 3 }, { intake_version: 4 }])("refuses an append receipt outside its captured case and versions: %j", async (change) => {
    const receipt = { intake_id: id(1), intake_version: 3, match_case_id: id(20), version: 2,
      status: "PENDING_REVIEW", audit_event_id: id(80), correlation_id: id(81), candidate_ids: [id(83)] };
    const { value, signal } = context(json({ ...receipt, ...change }, 201));
    const command: CanonicalMatchCaseCommand = { intakeId: id(1), expectedVersion: 3, key: "synthetic-manual-context-001",
      body: { outcome: "POSSIBLE_MATCH", confidence: "0.3750", append_to_case: { match_case_id: id(20), version: 1 },
        reason: "Explicit additional actual root", risk_acknowledged: true,
        candidates: [{ property_id: id(42), property_version: 3, confidence: "0.3750", evidence: {} }],
        supporting_signals: [{ kind: "EXPLICIT_HUMAN_CONTEXT" }], contradictory_signals: [] } };
    await expect(submitCanonicalMatchCase(value, command, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });

  it.each([
    { intake_id: id(2) }, { intake_version: 4 },
    { items: [{ property_id: id(41), version: 7, region_id: null, normalized_address: "private-marker", address_masked: true }] },
  ])("refuses an unrelated, stale or leaking property catalog: %j", async (change) => {
    const { value, signal } = context(json({ intake_id: id(1), intake_version: 3, items: [], next_cursor: null, ...change }));
    await expect(readCanonicalMatchCaseProperties(value, summary(), null, signal)).rejects.toBeInstanceOf(CanonicalIntakeReadError);
  });

  it.each([
    [{ field_path: "rent", classification: "INTERNAL", effective: 20000, masked: true, mask_reason_code: "FIELD_MASKED" }],
    [{ field_path: "rent", classification: null, effective: 20000, masked: false }],
    [{ field_path: "rent", classification: "CREDENTIAL", effective: "private-marker", masked: false }],
    [{ field_path: "rent", classification: "INTERNAL", effective: {}, masked: false }],
  ])("refuses invalid saved fields before reading another resource: %j", async (field) => {
    const { value, signal, fetchImpl } = context(json({ ...summary(), resolution: { ...resolution(), fields: [field] } }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("accepts explicitly masked saved fields whose classification is unavailable", async () => {
    const fields = [{ field_path: "address", classification: null, effective: null, masked: true, mask_reason_code: "FIELD_MASKED" }];
    const { value, signal } = context(json({ ...summary(), resolution: { ...resolution(), fields } }), json(caseSummary()));
    expect((await readCanonicalIntakeCase(value, id(1), signal)).intake.resolution?.fields).toEqual(fields);
  });

  it("keeps a current persisted Listing resolution distinct from historical matching candidates", async () => {
    const { value, signal } = context(json({ ...summary(), processing_state: "READY", resolution: resolution() }), json(caseSummary()));
    const result = await readCanonicalIntakeCase(value, id(1), signal);
    expect(result.intake.resolution).toEqual(resolution());
    expect(result.matchCase?.candidates[0].property_id).toBe(id(22));
  });

  it.each([{ listing_id: "legacy-L-1" }, { listing_version: 0 }, { lifecycle_state: "INVENTED" },
    { current_revision_id: "unknown" }, { effective_property_id: null }, { property_version: 1.5 }])(
    "rejects malformed persisted resolution metadata before reading another resource", async (change) => {
      const { value, fetchImpl, signal } = context(json({ ...summary(), resolution: { ...resolution(), ...change } }));
      await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

  it("accepts the native CANCELLED state without borrowing the assisted workflow's narrower state vocabulary", async () => {
    const { value, signal } = context(json({ items: [{ ...summary(), processing_state: "CANCELLED" }], next_cursor: null }));
    await expect(readCanonicalIntakePage(value, null, signal)).resolves.toMatchObject({ items: [{ processing_state: "CANCELLED" }] });
  });
  it("uses only the injected scoped GET transport and passes the opaque cursor unchanged", async () => {
    const { value, fetchImpl, signal } = context(json({ items: [summary()], next_cursor: "opaque+/=" }));
    await expect(readCanonicalIntakePage(value, "previous+/=", signal)).resolves.toMatchObject({ items: [{ intake_id: id(1) }] });
    const [path, init] = fetchImpl.mock.calls[0];
    expect(new URL(String(path), "https://unit.invalid").searchParams.get("cursor")).toBe("previous+/=");
    expect(init).toMatchObject({ cache: "no-store", credentials: "same-origin", signal });
    expect(init?.method).toBeUndefined();
    expect(new Headers(init?.headers).get("X-Operator-Role")).toBe("expansion-manager");
  });

  it.each([
    { items: [{ ...summary(), intake_id: "legacy-intake-1" }], next_cursor: null },
    { items: [summary(), summary()], next_cursor: null },
    { items: [summary(), { ...summary(2), tenant_id: id(99) }], next_cursor: null },
    { items: [{ ...summary(), processing_state: "INVENTED" }], next_cursor: null },
    { items: [{ ...summary(), version: 0 }], next_cursor: null },
    { items: [{ ...summary(), submitted_at: "2026-10-15T08:00:00" }], next_cursor: null },
    { items: [{ ...summary(), scope: {} }], next_cursor: null },
    { items: [], next_cursor: "next" },
    { items: [summary()], next_cursor: "" },
    { items: Array.from({ length: 26 }, (_, n) => summary(n + 1)), next_cursor: null },
  ])("rejects malformed success pages without inventing empty results", async (body) => {
    const { value, signal } = context(json(body));
    await expect(readCanonicalIntakePage(value, null, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });

  it("rejects a repeated next cursor", async () => {
    const { value, signal } = context(json({ items: [summary()], next_cursor: "same" }));
    await expect(readCanonicalIntakePage(value, "same", signal)).rejects.toBeInstanceOf(CanonicalIntakeReadError);
  });

  it("reads the selected native parent before its exact case and never creates a resource", async () => {
    const { value, fetchImpl, signal } = context(json(summary()), json(caseSummary()));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).resolves.toMatchObject({ intake: { version: 3 }, matchCase: { match_case_id: id(20) } });
    expect(fetchImpl.mock.calls.map(([path]) => path)).toEqual([`/api/v1/canonical-intakes/${id(1)}?include_source=true&include_history=true&include_evidence=true`, `/api/v1/intakes/${id(1)}/match-case`]);
    expect(fetchImpl.mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
  });

  it("refuses a mismatched selected parent before reading a case", async () => {
    const { value, fetchImpl, signal } = context(json(summary(2)));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([
    { ...caseSummary(), intake_id: id(2) },
    { ...caseSummary(), intake_version: 4 },
    { ...caseSummary(), candidates: [{ ...caseSummary().candidates[0], match_candidate_id: "not-native" }] },
    { ...caseSummary(), candidates: [{ ...caseSummary().candidates[0], confidence: "NaN" }] },
  ])("refuses a foreign, stale or malformed case", async (body) => {
    const { value, signal } = context(json(summary()), json(body));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toBeInstanceOf(CanonicalIntakeReadError);
  });

  it("only the specific case-not-found receipt means there is no case", async () => {
    const { value, signal } = context(json(summary()), json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).resolves.toMatchObject({ matchCase: null });
  });

  it.each(["PROPERTY_NOT_FOUND", "INTAKE_NOT_FOUND", "not found"])("keeps an unrelated 404 %s as a read failure", async (detail) => {
    const { value, signal } = context(json(summary()), json({ detail }, 404));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ status: 404, code: "CANONICAL_READ_FAILED" });
  });

  it.each([401, 403, 404, 503])("preserves parent denial/unavailability %s and never queries a case", async (status) => {
    const { value, fetchImpl, signal } = context(json({ detail: "unavailable" }, status));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ status });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("preserves a guarded error-body rejection across account rotation", async () => {
    const rejection = new Error("current session changed");
    const response = json({}, 404); response.json = vi.fn().mockRejectedValue(rejection);
    const { value, signal } = context(json(summary()), response);
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toBe(rejection);
  });

  it("aborts a late parent body without starting another scoped request", async () => {
    let resolve!: (body: unknown) => void;
    const response = json({}); response.json = () => new Promise((done) => { resolve = done; });
    const { value, fetchImpl } = context(response);
    const controller = new AbortController();
    const pending = readCanonicalIntakeCase(value, id(1), controller.signal);
    await vi.waitFor(() => expect(resolve).toBeDefined());
    controller.abort(); resolve(summary());
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});


const retainedTransition = (sequence = 1) => ({ transition_id: id(100 + sequence), sequence_no: sequence,
  from_state: sequence === 1 ? null : "SUBMITTED", to_state: "CHECKING_IDENTITY",
  actor_subject_id: sequence === 1 ? id(11) : null, service_principal: sequence === 1 ? null : "synthetic-history-worker",
  permission: "intake.process", reason_code: null, expected_version: sequence === 1 ? null : sequence - 1,
  resulting_version: sequence, job_id: null, source_policy_version: null, parser_release_id: null,
  source_snapshot_id: null, match_case_id: null, correlation_id: id(12), causation_id: null,
  occurred_at: "2026-10-15T08:00:00Z" });
const retainedPage = () => ({ intake_id: id(1), intake_version: 3, through_sequence: 2,
  items: [retainedTransition(2)], previous_cursor: "earlier-page", next_cursor: null });

describe("native source and recorded processing history transport", () => {
  it.each([
    undefined, null,
    { ...summary().source_info, recorded_policy_state: "CURRENT_REGISTRY_APPROVED" },
    { ...summary().source_info, last_transition_at: "2026-10-15T08:00:00" },
    { ...summary().source_info, url_fields: [] },
    { ...summary().source_info, url_fields: [summary().source_info.url_fields[0], summary().source_info.url_fields[0]] },
    { ...summary().source_info, url_fields: summary().source_info.url_fields.map((f) => ({ ...f, classification: "PUBLIC", masked: false, effective: "private-marker" })) },
    { ...summary().source_info, url_fields: summary().source_info.url_fields.map((f) => ({ ...f, effective: "private-marker" })) },
    { ...summary().source_info, url_fields: summary().source_info.url_fields.map((f) => ({ ...f, field_path: "parserBody" })) },
  ])("rejects missing, contradictory or leaking source metadata without querying a case: %j", async (source_info) => {
    const { value, fetchImpl, signal } = context(json({ ...summary(), source_info }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([
    undefined, null, { ...summary().processing_history, intake_id: id(2) },
    { ...summary().processing_history, intake_version: 4 },
    { ...summary().processing_history, through_sequence: 1 },
    { ...summary().processing_history, previous_cursor: "earlier-page" },
    { ...summary().processing_history, next_cursor: "next-page" },
  ])("rejects an absent or unrelated embedded first history page: %j", async (processing_history) => {
    const { value, fetchImpl, signal } = context(json({ ...summary(), processing_history }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("uses the original scoped GET with captured parent version and unchanged opaque cursor", async () => {
    const { value, signal, fetchImpl } = context(json(retainedPage()));
    await expect(readCanonicalIntakeHistory(value, summary(), "next-page", 2, signal)).resolves.toEqual(retainedPage());
    const [path, init] = fetchImpl.mock.calls[0];
    const url = new URL(String(path), "https://unit.invalid");
    expect(url.pathname).toBe(`/api/v1/canonical-intakes/${id(1)}/processing-history`);
    expect(Object.fromEntries(url.searchParams)).toEqual({ intake_version: "3", cursor: "next-page" });
    expect(init).toMatchObject({ cache: "no-store", credentials: "same-origin", signal });
    expect(init?.method).toBeUndefined(); expect(init?.body).toBeUndefined();
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Operator-Role")).toBe("expansion-manager");
    expect(headers.has("If-Match")).toBe(false); expect(headers.has("Idempotency-Key")).toBe(false);
  });

  it.each([
    { intake_id: id(2) }, { through_sequence: 3 }, { items: [] },
    { next_cursor: "next-page" }, { previous_cursor: "next-page" },
    { items: [retainedTransition(2), retainedTransition(2)] },
    { items: [retainedTransition(2), retainedTransition(1)] },
    { items: [{ ...retainedTransition(2), actor_subject_id: id(11) }] },
    { items: [{ ...retainedTransition(2), resulting_version: 4 }] },
    { items: [{ ...retainedTransition(2), resulting_version: 3, to_state: "READY" }] },
    { items: [{ ...retainedTransition(2), sequence_no: 3 }] },
    { items: [{ ...retainedTransition(2), occurred_at: "2026-10-15T08:00:00" }] },
    { items: [{ ...retainedTransition(2), job_id: "legacy-job-1" }] },
  ])("refuses unrelated, corrupt or cursor-repeating history instead of guessing progress: %j", async (change) => {
    const { value, signal } = context(json({ ...retainedPage(), ...change }));
    await expect(readCanonicalIntakeHistory(value, summary(), "next-page", 2, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });

  it("reports a changed parent version as a conflict", async () => {
    const { value, signal } = context(json({ ...retainedPage(), intake_version: 4 }));
    await expect(readCanonicalIntakeHistory(value, summary(), "next-page", 2, signal)).rejects.toMatchObject({ status: 409, code: "CANONICAL_INTAKE_VERSION_CHANGED" });
  });

  it.each([401, 403, 409, 503])("preserves current history read denial or unavailability %s", async (status) => {
    const { value, signal, fetchImpl } = context(json({ detail: "unavailable" }, status));
    await expect(readCanonicalIntakeHistory(value, summary(), "next-page", 2, signal)).rejects.toMatchObject({ status });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});


function exactReceiptOperation(kind: "url" | "case" | "proposal" | "review" | "manual") {
  const key = "synthetic-exact-key-123456789";
  const common = { audit_event_id: id(80), correlation_id: id(81) };
  if (kind === "url") {
    const packet: CanonicalURLCommand = { key, body: { intake_method: "URL", original_url: "https://source.example/private-input",
      scope: {}, reason: "Original synthetic URL intent" } };
    const receipt = { ...common, intake_id: id(1), processing_state: "SUBMITTED", intake_method: "URL", version: 1,
      submitted_at: "2026-10-29T06:00:00Z", transition_id: id(82), outbox_event_id: id(83) };
    return { key, receipt, status: 201, path: "/api/v1/canonical-intakes/submissions/receipt",
      lookup: (value: CanonicalIntakeReadContext, signal: AbortSignal) => readCanonicalURLReceipt(value, packet, signal) };
  }
  if (kind === "case") {
    const packet: CanonicalMatchCaseCommand = { key, intakeId: id(1), expectedVersion: 3,
      body: { outcome: "POSSIBLE_MATCH", confidence: "0.5000", reason: "Original synthetic association", risk_acknowledged: true,
        supporting_signals: [{}], contradictory_signals: [], candidates: [{ property_id: id(41), property_version: 7, confidence: "0.5000", evidence: {} }] } };
    const receipt = { ...common, match_case_id: id(20), intake_id: id(1), status: "PENDING_REVIEW", version: 1,
      intake_version: 4, candidate_ids: [id(21)] };
    return { key, receipt, status: 201, path: `/api/v1/intakes/${id(1)}/match-case/receipt`,
      lookup: (value: CanonicalIntakeReadContext, signal: AbortSignal) => readCanonicalMatchCaseReceipt(value, canonicalMatchCaseReceiptReference(packet), signal) };
  }
  if (kind === "manual") {
    const packet: CanonicalAssistedEntryCommand = { key, intakeId: id(1), expectedVersion: 3,
      body: { fields: { address: "Synthetic private original address", rent: 43210, areaPing: 42.5 }, reason: "Original human proposal",
        risk_summary: "Explicit original risk", risk_acknowledged: true } };
    const receipt = { ...common, request_id: id(95), intake_id: id(1), status: "REQUESTED", version: 3,
      requested_at: "2026-10-29T06:00:00Z", outbox_event_id: id(95), correction_ids: [id(30), id(31), id(32)] };
    return { key, receipt, status: 202, path: `/api/v1/canonical-intakes/${id(1)}/assisted-entry-completions/receipt`,
      lookup: (value: CanonicalIntakeReadContext, signal: AbortSignal) => readCanonicalAssistedEntryReceipt(value, packet, signal) };
  }
  const packet: CanonicalIdentityCommand = kind === "proposal"
    ? { key, expectedVersion: 2, matchCaseId: id(20), path: `/api/v1/match-cases/${id(20)}/identity-decisions`,
      body: { decision_type: "MERGE", reason: "Explicit synthetic graph proposal", risk_acknowledged: true,
        plan: { source_property_ids: [id(41)], target_property_id: id(42), expected_property_versions: { [id(41)]: 7, [id(42)]: 11 },
          expected_listing_versions: {}, expected_edge_versions: {}, candidate_reassignment_plan: [] } } }
    : { key, expectedVersion: 2, matchCaseId: id(20), decisionId: id(90), path: `/api/v1/canonical-identity-decisions/${id(90)}/actions/review`,
      body: { decision: "REJECT", expected_case_version: 3, reason: "Explicit independent human rejection", risk_acknowledged: false } };
  const receipt = { ...common, decision_id: id(90), match_case_id: id(20), match_case_version: 3, version: 2,
    status: kind === "proposal" ? "PENDING_REVIEW" : "REJECTED", job_id: null };
  return { key, receipt, status: kind === "proposal" ? 201 : 200, path: packet.path + "/receipt",
    lookup: (value: CanonicalIntakeReadContext, signal: AbortSignal) => readCanonicalIdentityReceipt(value, packet, signal) };
}

describe("exact original command receipt observations", () => {
  it.each(["url", "case", "proposal", "review", "manual"] as const)("uses the original %s namespace only as a scoped GET with no packet or If-Match", async (kind) => {
    const operation = exactReceiptOperation(kind);
    const envelope = { observation: "RECEIPT_COMMITTED", observed_at: "2026-10-29T07:00:00Z",
      original_http_status: operation.status, receipt: operation.receipt };
    const { value, signal, fetchImpl } = context(json(envelope));
    expect(await operation.lookup(value, signal)).toEqual(envelope);
    const [path, init] = fetchImpl.mock.calls[0];
    expect(path).toBe(operation.path); expect(init?.method).toBeUndefined(); expect(init?.body).toBeUndefined();
    expect(new Headers(init?.headers).get("idempotency-key")).toBe(operation.key);
    expect(new Headers(init?.headers).has("if-match")).toBe(false);
    expect(new Headers(init?.headers).has("content-type")).toBe(false);
    expect(new Headers(init?.headers).get("X-Operator-Role")).toBe("expansion-manager");
  });
  it.each(["url", "case", "proposal", "review", "manual"] as const)("keeps a valid no-row %s observation distinct from execution or a new command", async (kind) => {
    const { value, signal, fetchImpl } = context(json({ observation: "NOT_OBSERVED", observed_at: "2026-10-29T07:00:00Z" }));
    expect(await exactReceiptOperation(kind).lookup(value, signal)).toEqual({ observation: "NOT_OBSERVED", observed_at: "2026-10-29T07:00:00Z" });
    expect(fetchImpl).toHaveBeenCalledTimes(1); expect(fetchImpl.mock.calls[0][1]?.method).toBeUndefined();
  });
  it.each([
    { observation: "NOT_EXECUTED" }, { observed_at: "2026-10-29T07:00:00" }, { original_http_status: 202 },
    { receipt: { wrong: "not the original typed result" } },
  ])("rejects a malformed committed observation rather than settling an unknown command: %j", async (change) => {
    const operation = exactReceiptOperation("url");
    const { value, signal } = context(json({ observation: "RECEIPT_COMMITTED", observed_at: "2026-10-29T07:00:00Z",
      original_http_status: 201, receipt: operation.receipt, ...change }));
    await expect(operation.lookup(value, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it.each([
    ["case", { candidate_ids: [id(21), id(22)] }], ["case", { intake_version: 6 }],
    ["proposal", { match_case_id: id(99) }], ["review", { decision_id: id(99) }], ["manual", { intake_id: id(99) }],
  ] as const)("retains the original packet-dependent %s receipt checks: %j", async (kind, change) => {
    const operation = exactReceiptOperation(kind);
    const { value, signal } = context(json({ observation: "RECEIPT_COMMITTED", observed_at: "2026-10-29T07:00:00Z",
      original_http_status: operation.status, receipt: { ...operation.receipt, ...change } }));
    await expect(operation.lookup(value, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it("refuses a purported absence that includes a receipt", async () => {
    const operation = exactReceiptOperation("url");
    const { value, signal } = context(json({ observation: "NOT_OBSERVED", observed_at: "2026-10-29T07:00:00Z", receipt: operation.receipt }));
    await expect(operation.lookup(value, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it.each(["url", "case", "proposal", "review", "manual"] as const)("preserves a current authorization error on %s lookup", async (kind) => {
    const { value, signal, fetchImpl } = context(json({ detail: "current authority refused" }, 403));
    await expect(exactReceiptOperation(kind).lookup(value, signal)).rejects.toMatchObject({ status: 403, code: "CANONICAL_READ_FAILED" });
    expect(fetchImpl).toHaveBeenCalledTimes(1); expect(fetchImpl.mock.calls[0][1]?.method).toBeUndefined();
  });
});

const evidenceSnapshot = () => ({ kind: "SNAPSHOT" as const, recorded_at: "2026-10-15T08:00:00Z", metadata: {
  source_snapshot_id: id(71), intake_id: id(1), source_id: "synthetic-source", content_sha256: "a".repeat(64), media_type: "application/json",
  byte_length: 4, captured_at: "2026-10-15T08:00:00Z", observed_at: "2026-10-15T08:00:00Z", stored_at: "2026-10-15T08:00:00Z",
  capture_method: "MANUAL_ENTRY" as const, version: 1, retention_class: "STANDARD", purge_after: null, legal_hold: false,
  object_generation: "9223372036854775798", residency_mode: "TW_ONLY" as const } });
const evidenceParser = () => ({ kind: "PARSER_RUN" as const, recorded_at: "2026-10-15T08:00:00Z", metadata: {
  parser_run_id: id(72), intake_id: id(1), source_snapshot_id: id(71), parser_release_id: id(73), source_id: "synthetic-source",
  status: "PARTIAL" as const, version: 3, created_at: "2026-10-15T08:00:00Z", started_at: null, completed_at: null,
  correlation_id: id(12), semantic_version: "1.0.0", package_name: "synthetic.manual", output_schema_version: "synthetic-output/1" } });
const evidenceProjection = () => ({ intake_id: id(1), intake_version: 3, through_at: "2026-10-15T08:00:00Z",
  observed_at: "2026-10-15T08:00:01Z", items: [evidenceSnapshot(), evidenceParser()], previous_cursor: null, next_cursor: null });
const evidenceParent = () => ({ ...summary(), source_info: { ...summary().source_info, source_id: "synthetic-source" },
  processing_evidence: evidenceProjection() });

const parserFields = () => ({ intake_id: id(1), intake_version: 3, source_id: "synthetic-source",
  parser_run_id: id(72), parser_run_version: 3, status: "PARTIAL", parser_job_id: id(74), parser_job_version: 4,
  job_status: "SUCCEEDED", source_snapshot_id: id(71), parser_release_id: id(73), observed_at: "2026-10-15T08:00:02Z",
  fields: ["address", "rent", "areaPing"].map((field_path, i) => ({ correction_id: id(800 + i), field_path,
    classification: "INTERNAL", status: "PROPOSED", version: 2, identity_affecting: true, proposed_by: id(11),
    created_at: "2026-10-15T08:00:00Z", reviewed_by: null, reviewed_at: null, parsed: "PRIVATE_PARSED",
    normalized: null, corrected: "PRIVATE_PROPOSAL", confidence: null, correction_reason: "PRIVATE_REASON",
    masked: false, mask_reason_code: null })) });

describe("selected native parser field history read", () => {
  it.each(["optional_field_path", "field_status", "job_status"])("rejects array substitution in %s before rendering fields", async (target) => {
    const body = parserFields();
    if (target === "optional_field_path") {
      body.fields.push({ ...body.fields[0]!, correction_id: id(804), field_path: ["floor"] as unknown as string });
    } else if (target === "field_status") {
      body.fields[0]!.status = ["PROPOSED"] as unknown as string;
    } else {
      body.job_status = ["SUCCEEDED"] as unknown as string;
    }
    const { value, signal } = context(json(body));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal))
      .rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it("binds the explicit run and original parent versions with one readonly GET", async () => {
    const body = parserFields(), { value, signal, fetchImpl } = context(json(body));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal)).resolves.toEqual(body);
    const url = new URL(String(fetchImpl.mock.calls[0]![0]), "https://same-origin.invalid");
    expect(url.pathname).toBe(`/api/v1/canonical-intakes/${id(1)}/parser-runs/${id(72)}/fields`);
    expect(Object.fromEntries(url.searchParams)).toEqual({ intake_version: "3", parser_run_version: "3" });
    expect(fetchImpl).toHaveBeenCalledTimes(1); expect(fetchImpl.mock.calls[0]![1]?.method).toBeUndefined();
  });
  it.each([
    { classification: null }, { classification: "FUTURE_CLASS" }, { masked: true }, { confidence: true },
    { confidence: -0.01 }, { confidence: 1.01 }, { parsed: false }, { corrected: {} }, { effective: "PRIVATE_SAVED_VALUE" },
    { correction_id: "synthetic-legacy-label" }, { version: 0 }, { version: 1.5 }, { status: "APPROVED" },
    { field_path: "contactPhone" }, { reviewed_by: "synthetic-user" }, { created_at: "yesterday" },
  ])("rejects unmasked, unclassified or malformed private field data: %j", async (change) => {
    const body = parserFields(); body.fields[0] = { ...body.fields[0]!, ...change } as typeof body.fields[number];
    const { value, signal, fetchImpl } = context(json(body));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal))
      .rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
  it("allows unknown classification only as a fully masked observation", async () => {
    const body = parserFields();
    const masked = { ...body, fields: body.fields.map((field) => ({ ...field, classification: null,
      parsed: null, normalized: null, corrected: null, confidence: null, correction_reason: null,
      masked: true, mask_reason_code: "FIELD_MASKED" })) };
    const { value, signal } = context(json(masked));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal)).resolves.toEqual(masked);
  });
  it.each([
    { intake_id: id(2) }, { intake_version: 4 }, { parser_run_id: id(99) }, { parser_run_version: 4 },
    { source_snapshot_id: id(99) }, { parser_release_id: id(99) }, { source_id: "foreign-source" },
    { status: "SUCCEEDED" }, { job_status: "INVENTED" }, { parser_job_version: 0 },
    { observed_at: "2026-10-15T07:00:00Z" }, { fields: [] }, { manual_entry_valid_until: "2099-01-01T00:00:00Z" },
  ])("refuses a different reference or a positive grant on the history shape: %j", async (change) => {
    const { value, signal } = context(json({ ...parserFields(), ...change }));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal))
      .rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it.each(["id", "path"])("refuses duplicate correction %s references", async (kind) => {
    const body = parserFields();
    body.fields[1] = { ...body.fields[1]!, ...(kind === "id"
      ? { correction_id: body.fields[0]!.correction_id } : { field_path: body.fields[0]!.field_path }) };
    const { value, signal } = context(json(body));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal))
      .rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });
  it.each([401, 403, 409, 503])("retains the current server refusal %s", async (status) => {
    const { value, signal } = context(json({ detail: "refused" }, status));
    await expect(readCanonicalIntakeParserFields(value, evidenceParent(), evidenceParser().metadata, signal)).rejects.toMatchObject({ status });
  });
});

describe("native saved snapshot and parser lifecycle read transport", () => {
  it("accepts the actual typed records and preserves generation precision and unknown execution timestamps", async () => {
    const parent = evidenceParent(); const { value, signal, fetchImpl } = context(json(parent), json(caseSummary()));
    const result = await readCanonicalIntakeCase(value, id(1), signal);
    expect(result.intake.processing_evidence).toEqual(parent.processing_evidence);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]![0]).toBe(`/api/v1/canonical-intakes/${id(1)}?include_source=true&include_history=true&include_evidence=true`);
  });

  it.each([
    undefined, null, { ...evidenceProjection(), intake_id: id(2) }, { ...evidenceProjection(), intake_version: 4 },
    { ...evidenceProjection(), previous_cursor: "earlier-page" },
    { ...evidenceProjection(), through_at: "2026-10-15T08:00:02Z" },
    { ...evidenceProjection(), observed_at: "2026-10-15T08:00:00" },
    { ...evidenceProjection(), parsed_payload: "PRIVATE_UNEXPECTED_BODY" },
    { ...evidenceProjection(), items: Array.from({ length: 26 }, () => evidenceSnapshot()) },
  ])("rejects missing, foreign or unbounded embedded metadata before another resource is read: %j", async (processing_evidence) => {
    const { value, signal, fetchImpl } = context(json({ ...evidenceParent(), processing_evidence }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([
    { intake_id: id(2) }, { source_id: "another-source" }, { object_generation: 9223372036854775000 },
    { object_generation: "0" }, { object_generation: "01" }, { content_sha256: "invalid" },
    { version: 0 }, { byte_length: -1 }, { legal_hold: "false" }, { capture_method: ["MANUAL_ENTRY"] },
    { captured_at: "2026-10-15T08:00:00" }, { raw_object_uri: "PRIVATE_OBJECT" }, { encryption_key_ref: "PRIVATE_KEY" },
  ])("refuses contradictory or leaking snapshot metadata: %j", async (change) => {
    const page = { ...evidenceProjection(), items: [{ ...evidenceSnapshot(), metadata: { ...evidenceSnapshot().metadata, ...change } }] };
    const { value, signal, fetchImpl } = context(json({ ...evidenceParent(), processing_evidence: page }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([
    { intake_id: id(2) }, { parser_run_id: "derived-job-label" }, { source_snapshot_id: null }, { parser_release_id: "unknown" },
    { status: "VERIFIED" }, { status: ["SUCCEEDED"] }, { version: 0 }, { completed_at: "2026-10-15T08:00:00" },
    { parsed_payload: { secret: "PRIVATE_BODY" } }, { validation_errors: ["PRIVATE_DETAIL"] },
  ])("refuses unproven, malformed or body-bearing parser metadata: %j", async (change) => {
    const page = { ...evidenceProjection(), items: [{ ...evidenceParser(), metadata: { ...evidenceParser().metadata, ...change } }] };
    const { value, signal } = context(json({ ...evidenceParent(), processing_evidence: page }));
    await expect(readCanonicalIntakeCase(value, id(1), signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });

  it("uses the existing injected GET and exact parent/captured time bound for later pages", async () => {
    const parent = evidenceParent(), page = { ...evidenceProjection(), previous_cursor: "earlier-evidence" };
    const { value, signal, fetchImpl } = context(json(page));
    await expect(readCanonicalIntakeEvidence(value, parent, "next-evidence", page.through_at, signal)).resolves.toEqual(page);
    const [path, init] = fetchImpl.mock.calls[0]!; const url = new URL(String(path), "https://unit.invalid");
    expect(url.pathname).toBe(`/api/v1/canonical-intakes/${id(1)}/processing-evidence`);
    expect(Object.fromEntries(url.searchParams)).toEqual({ intake_version: "3", cursor: "next-evidence" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal });
    expect(init?.method).toBeUndefined(); expect(init?.body).toBeUndefined();
    expect(new Headers(init?.headers).get("X-Operator-Role")).toBe("expansion-manager");
    expect(new Headers(init?.headers).has("If-Match")).toBe(false);
    expect(new Headers(init?.headers).has("Idempotency-Key")).toBe(false);
  });

  it.each([
    { intake_id: id(2) }, { through_at: "2026-10-15T07:00:00Z" }, { next_cursor: "next-evidence" },
    { previous_cursor: "next-evidence" }, { items: [] , next_cursor: "another-page" },
    { items: [evidenceSnapshot(), evidenceSnapshot()] },
  ])("rejects later pages which change binding or repeat a cursor: %j", async (change) => {
    const parent = evidenceParent(), page = evidenceProjection();
    const { value, signal } = context(json({ ...page, ...change }));
    await expect(readCanonicalIntakeEvidence(value, parent, "next-evidence", page.through_at, signal)).rejects.toMatchObject({ code: "CANONICAL_RESPONSE_INVALID" });
  });

  it.each([401, 403, 409, 503])("preserves current evidence read failure %s", async (status) => {
    const parent = evidenceParent(); const { value, signal } = context(json({ detail: "unavailable" }, status));
    await expect(readCanonicalIntakeEvidence(value, parent, "next-evidence", parent.processing_evidence.through_at, signal)).rejects.toMatchObject({ status });
  });
});
