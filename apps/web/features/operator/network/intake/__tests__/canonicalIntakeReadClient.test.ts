import { describe, expect, it, vi } from "vitest";
import {
  CanonicalIntakeReadError, readCanonicalIntakeCase, readCanonicalIntakePage,
  readCanonicalMatchCaseProperties, submitCanonicalMatchCase,
  type CanonicalIntakeReadContext, type CanonicalMatchCaseCommand,
} from "../canonicalIntakeReadClient";

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
const summary = (n = 1) => ({ intake_id: id(n), tenant_id: id(10), submitter_subject_id: id(11), correlation_id: id(12),
  intake_method: "MANUAL" as const, processing_state: "NEEDS_REVIEW" as const, version: 3, submitted_at: "2026-10-15T08:00:00Z",
  scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } });
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
    expect(fetchImpl.mock.calls.map(([path]) => path)).toEqual([`/api/v1/canonical-intakes/${id(1)}`, `/api/v1/intakes/${id(1)}/match-case`]);
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
