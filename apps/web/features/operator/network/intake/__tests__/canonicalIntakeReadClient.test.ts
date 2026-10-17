import { describe, expect, it, vi } from "vitest";
import {
  CanonicalIntakeReadError, readCanonicalIntakeCase, readCanonicalIntakePage,
  type CanonicalIntakeReadContext,
} from "../canonicalIntakeReadClient";

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
const summary = (n = 1) => ({ intake_id: id(n), tenant_id: id(10), submitter_subject_id: id(11), correlation_id: id(12),
  intake_method: "MANUAL", processing_state: "NEEDS_REVIEW", version: 3, submitted_at: "2026-10-15T08:00:00Z",
  scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } });
const caseSummary = () => ({ match_case_id: id(20), intake_id: id(1), intake_version: 3, version: 1,
  status: "PENDING_REVIEW", outcome: "POSSIBLE_MATCH", candidates: [{ match_candidate_id: id(21),
    property_id: id(22), listing_id: null, rank: 1, confidence: "0.9000", observed_property_version: 2, observed_listing_version: null }] });
function context(...responses: Response[]) {
  const fetchImpl = vi.fn<typeof fetch>(); responses.forEach((response) => fetchImpl.mockResolvedValueOnce(response));
  const value: CanonicalIntakeReadContext = { fetchImpl, contextKey: "synthetic-A", headers: { "X-Operator-Role": "expansion-manager" } };
  return { value, fetchImpl, signal: new AbortController().signal };
}

describe("canonical Intake read transport", () => {
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
