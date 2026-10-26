import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CanonicalIntakeCasePicker } from "../CanonicalIntakeCasePicker";
import type { CanonicalIntakeReadContext } from "../canonicalIntakeReadClient";
import type { CanonicalIdentityPlanningContext } from "@oday-plus/openapi-client";

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const intake = (n: number) => ({ intake_id: id(n), tenant_id: id(10), submitter_subject_id: id(11), correlation_id: id(12),
  intake_method: "MANUAL", processing_state: "NEEDS_REVIEW", version: 3, submitted_at: "2026-10-15T08:00:00Z",
  scope: { brand_id: null, region_id: null, assigned_area_id: null, heat_zone_id: null } });
const matchCase = (n: number) => ({ match_case_id: id(n + 20), intake_id: id(n), intake_version: 3, version: 1,
  status: "PENDING_REVIEW", outcome: "POSSIBLE_MATCH", candidates: [{ match_candidate_id: id(n + 30), property_id: id(n + 40),
    listing_id: null, rank: 1, confidence: "0.9000", observed_property_version: 2, observed_listing_version: null }] });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
const headers = { "X-Operator-Role": "expansion-manager" };
function makeContext(handler?: (path: string, init?: RequestInit) => Response | Promise<Response>, key = "A"): CanonicalIntakeReadContext {
  return { contextKey: key, headers, fetchImpl: vi.fn(async (input, init) => {
    const path = String(input);
    if (handler) return handler(path, init);
    if (path.startsWith("/api/v1/canonical-intakes?")) return json({ items: [intake(1), intake(2)], next_cursor: null });
    const selected = path.includes(id(2)) ? 2 : 1;
    return json(path.endsWith("/match-case") ? matchCase(selected) : intake(selected));
  }) };
}
const open = () => fireEvent.click(screen.getByRole("button", { name: "選取收件／比對案件" }));
const select = (n: number) => fireEvent.click(screen.getByRole("button", { name: new RegExp(id(n)) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("native Intake and case selection", () => {
  it("shows fresh persisted assignment and SLA before a match case exists without exposing unconnected actions", async () => {
    const parent = { ...intake(1), processing_state: "SUBMITTED", workflow: {
      assignment_id: id(61), assignment_version: 2, assignment_status: "UNASSIGNED", owner_subject_id: null, owner_role: null,
      queue_key: "existing-synthetic-queue", sla_instance_id: id(62), sla_version: 3, sla_state: "ON_TRACK",
      policy_version: "existing-policy-v1", calendar_version: "existing-calendar-v1", started_at: "2000-01-01T08:00:00Z",
      due_soon_at: "2000-01-01T09:00:00Z", due_at: "2000-01-01T10:00:00Z", breach_at: "2000-01-01T11:00:00Z",
    } };
    const context = makeContext((path) => path.startsWith("/api/v1/canonical-intakes?") ? json({ items: [parent], next_cursor: null })
      : path.endsWith("/match-case") ? json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404) : json(parent));
    const view = render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    const summary = await screen.findByTestId("assignment-sla-summary");
    expect(summary).toHaveTextContent("尚未指派");
    expect(summary).toHaveTextContent("existing-synthetic-queue");
    expect(within(summary).getByTestId("asg-sla-status")).toHaveTextContent("正常 (On Track)");
    expect(summary).not.toHaveTextContent(id(61));
    expect(summary).not.toHaveTextContent(id(62));
    expect(within(summary).queryByRole("button")).not.toBeInTheDocument();
    expect(summary).not.toHaveTextContent("RESOURCE_VERSION_UNAVAILABLE");
    expect(posts(context)).toHaveLength(0);
    expect(screen.getByText("這筆收件目前沒有比對案件。")).toBeVisible();
    view.rerender(<CanonicalIntakeCasePicker context={{ ...context, contextKey: "B" }} />);
    expect(screen.queryByTestId("assignment-sla-summary")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("appends observed manual roots to the original case, retries the captured versions, and requires fresh read before a plan", async () => {
    const original = matchCase(1);
    const properties = [41, 42].map((n) => ({ property_id: id(n), version: n - 39,
      normalized_address: `Observed Property ${n}`, region_id: null, address_masked: false }));
    const sent: { body: unknown; headers: Headers }[] = [];
    let appended = false;
    const context = makeContext((path, init) => {
      if (init?.method === "POST") {
        sent.push({ body: JSON.parse(String(init.body)), headers: new Headers(init.headers) });
        if (sent.length === 1) throw new TypeError("isolated uncertain append reply");
        appended = true;
        return json({ intake_id: id(1), intake_version: 3, match_case_id: original.match_case_id, version: 2,
          status: "PENDING_REVIEW", audit_event_id: id(80), correlation_id: id(81), candidate_ids: [id(83)] }, 201);
      }
      if (path.includes("/match-case/property-options?")) return json({ intake_id: id(1), intake_version: 3, items: properties, next_cursor: null });
      if (path.endsWith("/match-case")) return json(appended ? { ...original, version: 2, candidates: [...original.candidates,
        { match_candidate_id: id(83), property_id: id(42), listing_id: null, rank: 2, confidence: "0.3750",
          observed_property_version: 3, observed_listing_version: null, manual_context_version: 2 }] } : original);
      return path.startsWith("/api/v1/canonical-intakes?") ? json({ items: [intake(1)], next_cursor: null }) : json(intake(1));
    });
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "追加人工覆核關聯" }));
    const extra = await screen.findByRole("checkbox", { name: new RegExp(id(42)) });
    expect(extra).not.toBeChecked();
    expect(screen.queryByRole("checkbox", { name: new RegExp(id(41)) })).not.toBeInTheDocument();
    fireEvent.click(extra);
    fireEvent.change(screen.getByLabelText(/人工判斷信心值/), { target: { value: "0.3750" } });
    fireEvent.change(screen.getByLabelText("人工關聯依據"), { target: { value: "已核對這個額外正式主體與原案件的實地關聯。" } });
    fireEvent.change(screen.getByLabelText("追加關聯原因"), { target: { value: "追加人工關聯供獨立覆核。" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我已核對所選正式主體、版本與人工關聯依據" }));
    fireEvent.click(screen.getByRole("button", { name: "提交人工關聯" }));
    fireEvent.click(await screen.findByRole("button", { name: "重試同一案件請求" }));
    await screen.findByText(new RegExp(`已追加人工關聯至案件 ${original.match_case_id}`));
    await waitFor(() => expect(screen.queryByRole("button", { name: "提出合併／拆分計畫" })).not.toBeInTheDocument());
    expect(sent).toHaveLength(2);
    expect(sent[0].body).toEqual(sent[1].body);
    expect(sent[0].headers.get("Idempotency-Key")).toBe(sent[1].headers.get("Idempotency-Key"));
    expect(sent[0].headers.get("If-Match")).toBe('W/"3"');
    expect(sent[0].body).toMatchObject({ append_to_case: { match_case_id: original.match_case_id, version: 1 },
      candidates: [{ property_id: id(42), property_version: 3, confidence: "0.3750" }] });
    fireEvent.click(screen.getByRole("button", { name: "重新讀取" }));
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    await screen.findByText("追加人工關聯 · 案件版本 2");
    expect(await screen.findByRole("button", { name: "提出合併／拆分計畫" })).toBeEnabled();
  });

  it("creates human case context from explicitly observed roots and reuses the same captured command after an uncertain response", async () => {
    const parent = { ...intake(1), processing_state: "MATCHING" };
    const properties = [41, 42].map((n) => ({ property_id: id(n), version: n - 34,
      normalized_address: `Observed Property ${n}`, region_id: null, address_masked: false }));
    const sent: { body: unknown; headers: Headers }[] = [];
    const context = makeContext((path, init) => {
      if (init?.method === "POST") {
        sent.push({ body: JSON.parse(String(init.body)), headers: new Headers(init.headers) });
        if (sent.length === 1) throw new TypeError("isolated uncertain response");
        return json({ intake_id: id(1), intake_version: 4, match_case_id: id(20), version: 2, status: "PENDING_REVIEW",
          audit_event_id: id(80), correlation_id: id(81), candidate_ids: [id(82), id(83)] }, 201);
      }
      if (path.includes("/match-case/property-options?")) return json({ intake_id: id(1), intake_version: 3, items: properties, next_cursor: null });
      if (path.endsWith("/match-case")) return json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404);
      return path.startsWith("/api/v1/canonical-intakes?") ? json({ items: [parent], next_cursor: null }) : json(parent);
    });
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "建立人工覆核案件" }));
    const first = await screen.findByRole("checkbox", { name: new RegExp(id(41)) });
    expect(first).not.toBeChecked();
    expect(screen.getByRole("button", { name: "建立人工覆核案件" })).toBeDisabled();
    fireEvent.click(first); fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(id(42)) }));
    fireEvent.change(screen.getByLabelText(/人工判斷信心值/), { target: { value: "0.75" } });
    fireEvent.change(screen.getByLabelText("人工關聯依據"), { target: { value: "已核對兩筆正式主體的實地觀察。" } });
    fireEvent.change(screen.getByLabelText("建立案件原因"), { target: { value: "需要人工合併／拆分覆核。" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "我已核對所選正式主體、版本與人工關聯依據" }));
    fireEvent.click(screen.getByRole("button", { name: "建立人工覆核案件" }));
    const retry = await screen.findByRole("button", { name: "重試同一案件請求" });
    expect(screen.getByLabelText(/人工判斷信心值/)).toBeDisabled();
    fireEvent.click(retry);
    await screen.findByText(new RegExp(`人工覆核案件 ${id(20)}`));
    expect(sent).toHaveLength(2);
    expect(sent[0].body).toEqual(sent[1].body);
    expect(sent[0].headers.get("Idempotency-Key")).toBe(sent[1].headers.get("Idempotency-Key"));
    expect(sent[0].headers.get("If-Match")).toBe('W/"3"');
    expect(sent[0].body).toMatchObject({ outcome: "POSSIBLE_MATCH", confidence: "0.75",
      candidates: properties.map((row) => ({ property_id: row.property_id, property_version: row.version, confidence: "0.75" })) });
    expect(screen.queryByText("這筆收件目前沒有比對案件。")).not.toBeInTheDocument();
  });

  it("shows only the actual persisted resolution after a fresh detail read, alongside the historical case", async () => {
    const parent = { ...intake(1), processing_state: "READY", resolution: { listing_id: id(51), listing_version: 14,
      lifecycle_state: "ACTIVE", current_revision_id: id(52), property_id_at_creation: id(41), effective_property_id: id(42), property_version: 12,
      fields: [{ field_path: "rent", classification: "INTERNAL", effective: 25000, masked: false, mask_reason_code: null },
        { field_path: "address", classification: null, effective: null, masked: true, mask_reason_code: "FIELD_MASKED" }] } };
    const context = makeContext((path) => path.startsWith("/api/v1/canonical-intakes?")
      ? json({ items: [intake(1)], next_cursor: null }) : path.endsWith("/match-case") ? json(matchCase(1)) : json(parent));
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    const persisted = await screen.findByRole("region", { name: "已落庫物件" });
    expect(persisted).toHaveTextContent(`物件 ${id(51)} · 版本 14 · ACTIVE`);
    expect(persisted).toHaveTextContent(`目前主體 ${id(42)} · 版本 12`);
    expect(persisted).toHaveTextContent(`建立時主體 ${id(41)}`);
    expect(persisted).toHaveTextContent(`目前修訂 ${id(52)}`);
    expect(screen.getByRole("table", { name: "已落庫欄位" })).toHaveTextContent("rent25000INTERNAL");
    expect(persisted).toHaveTextContent("address無法檢視分類未提供");
    expect(persisted).toHaveTextContent("部分欄位因權限或分類資料不足而遮罩");
    expect(posts(context)).toHaveLength(0);
  });

  it("does no eager read, selects no default row and reads only an explicitly chosen actual context", async () => {
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("global fetch must not be used"); }));
    const context = makeContext(); render(<CanonicalIntakeCasePicker context={context} />);
    expect(context.fetchImpl).not.toHaveBeenCalled();
    open(); await screen.findByRole("button", { name: new RegExp(id(1)) });
    expect(screen.queryByRole("region", { name: "所選收件與比對案件" })).not.toBeInTheDocument();
    expect(context.fetchImpl).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: new RegExp(id(1)) })).toHaveAttribute("aria-pressed", "false");
    select(2);
    const detail = await screen.findByRole("region", { name: "所選收件與比對案件" });
    await within(detail).findByText(new RegExp(`比對案件 ${id(22)}`));
    expect(within(detail).getByText(new RegExp(`主體 ${id(42)}`))).toBeVisible();
    expect(vi.mocked(context.fetchImpl).mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/canonical-intakes?page_size=25", `/api/v1/canonical-intakes/${id(2)}`, `/api/v1/intakes/${id(2)}/match-case`,
    ]);
    select(2);
    expect(within(detail).getByText(new RegExp(`比對案件 ${id(22)}`))).toBeVisible();
    expect(context.fetchImpl).toHaveBeenCalledTimes(3);
  });

  it.each([401, 403, 503])("renders denied/unavailable %s as an error rather than an empty collection", async (status) => {
    render(<CanonicalIntakeCasePicker context={makeContext(() => json({ detail: "unavailable" }, status))} />); open();
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("目前可讀範圍沒有收件。")).not.toBeInTheDocument();
  });

  it("shows an empty collection only after an actual successful empty read", async () => {
    render(<CanonicalIntakeCasePicker context={makeContext(() => json({ items: [], next_cursor: null }))} />); open();
    expect(await screen.findByText("目前可讀範圍沒有收件。")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["CANONICAL_MATCH_CASE_NOT_FOUND", "PROPERTY_NOT_FOUND"])("distinguishes absent case from other 404: %s", async (detail) => {
    const normal = makeContext();
    const context = makeContext((path) => path.endsWith("/match-case") ? json({ detail }, 404) : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    if (detail === "CANONICAL_MATCH_CASE_NOT_FOUND") expect(await screen.findByText("這筆收件目前沒有比對案件。")).toBeVisible();
    else { expect(await screen.findByRole("alert")).toBeVisible(); expect(screen.queryByText("這筆收件目前沒有比對案件。")).not.toBeInTheDocument(); }
    expect(vi.mocked(context.fetchImpl).mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
  });

  it("discards late detail JSON when another Intake is explicitly selected", async () => {
    const body = deferred<unknown>(); const reading = deferred<boolean>();
    const late = json({}); late.json = () => { reading.resolve(true); return body.promise; };
    const normal = makeContext();
    const context = makeContext((path) => path === `/api/v1/canonical-intakes/${id(1)}` ? late : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1); await reading.promise;
    select(2);
    const detail = screen.getByRole("region", { name: "所選收件與比對案件" });
    await within(detail).findByText(new RegExp(`比對案件 ${id(22)}`));
    await act(async () => { body.resolve(intake(1)); });
    expect(within(detail).queryByText(new RegExp(id(1)))).not.toBeInTheDocument();
    expect(within(detail).getByText(new RegExp(`比對案件 ${id(22)}`))).toBeVisible();
    expect(vi.mocked(context.fetchImpl).mock.calls.map(([path]) => path)).not.toContain(`/api/v1/intakes/${id(1)}/match-case`);
  });

  it.each(["contextKey", "transport", "headers"])("closes and unmounts old data immediately on %s change", async (change) => {
    const body = deferred<unknown>(); const reading = deferred<boolean>(); const late = json({});
    late.json = () => { reading.resolve(true); return body.promise; };
    const context = makeContext(() => late); const view = render(<CanonicalIntakeCasePicker context={context} />);
    open(); await reading.promise;
    const replacement = change === "contextKey" ? { ...context, contextKey: "B" }
      : change === "headers" ? { ...context, headers: { "X-Operator-Role": "pm-audit" } }
      : { ...context, fetchImpl: makeContext().fetchImpl };
    view.rerender(<CanonicalIntakeCasePicker context={replacement} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => { body.resolve({ items: [intake(1)], next_cursor: null }); });
    expect(screen.queryByText(new RegExp(id(1)))).not.toBeInTheDocument();
  });

  it("clears selected details on page changes and passes only the received cursor", async () => {
    const normal = makeContext(); const context = makeContext((path) => path.startsWith("/api/v1/canonical-intakes?")
      ? json({ items: [intake(path.includes("cursor=") ? 2 : 1)], next_cursor: path.includes("cursor=") ? null : "opaque+/=" }) : normal.fetchImpl(path));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    await screen.findByText(new RegExp(`比對案件 ${id(21)}`));
    fireEvent.click(screen.getByRole("button", { name: "下一頁" }));
    expect(screen.queryByRole("region", { name: "所選收件與比對案件" })).not.toBeInTheDocument();
    await screen.findByRole("button", { name: new RegExp(id(2)) });
    expect(screen.queryByRole("button", { name: new RegExp(id(1)) })).not.toBeInTheDocument();
    const lastPath = String(vi.mocked(context.fetchImpl).mock.calls.at(-1)?.[0]);
    expect(new URL(lastPath, "https://unit.invalid").searchParams.get("cursor")).toBe("opaque+/=");
  });

  it("clears old rows immediately while refreshing and retains a failed read as an error", async () => {
    const refresh = deferred<Response>(); const normal = makeContext(); let calls = 0;
    const context = makeContext((path) => ++calls === 1 ? normal.fetchImpl(path) : refresh.promise);
    render(<CanonicalIntakeCasePicker context={context} />); open();
    await screen.findByRole("button", { name: new RegExp(id(1)) });
    fireEvent.click(screen.getByRole("button", { name: "重新讀取" }));
    expect(screen.queryByRole("button", { name: new RegExp(id(1)) })).not.toBeInTheDocument();
    await act(async () => { refresh.resolve(json({ detail: "denied" }, 403)); });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("目前可讀範圍沒有收件。")).not.toBeInTheDocument();
  });

  it("uses the existing dialog Escape and focus restoration contract", async () => {
    render(<CanonicalIntakeCasePicker context={makeContext()} />); const trigger = screen.getByRole("button", { name: "選取收件／比對案件" });
    trigger.focus(); open(); await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});

const nativePlanning = () => ({ match_case_id: id(21), match_case_version: 1, intake_id: id(1), intake_version: 3,
  anchors: [{ match_candidate_id: id(31), effective_property_id: id(41), listing_id: id(51), listing_version: 13 }],
  properties: [{ property_id: id(41), version: 7, status: "ACTIVE" }],
  listings: [{ listing_id: id(51), property_id: id(41), version: 13, lifecycle_state: "ACTIVE" }],
  source_identity_edges: [{ edge_id: id(81), property_id: id(41), listing_id: id(51), edge_version: 2 }], candidate_sites: [],
  listing_input: { parser_job_id: id(90), parser_run_id: id(91), parser_run_version: 4, source_snapshot_id: id(92),
    original_input_sha256: "a".repeat(64), normalized_payload_sha256: "b".repeat(64), proposed_correction_ids: [id(61)],
    manual_entry_valid_until: "2099-10-19T08:00:00Z", fields: [{ correction_id: id(61), field_path: "rent", classification: "INTERNAL",
      status: "PROPOSED", version: 4, identity_affecting: true, proposed_by: id(11), parsed: 100, normalized: 100, corrected: 120, masked: false }] } });
const nativeReceipt = (status = "APPROVED") => ({ decision_id: id(71), match_case_id: id(21), match_case_version: 2,
  status, version: 2, job_id: status === "APPROVED" ? id(72) : null, job_status: status === "APPROVED" ? "QUEUED" : null,
  audit_event_id: id(73), correlation_id: id(74) });
function nativeContext(outcome = "NEW", post?: (init: RequestInit) => Response | Promise<Response>, changePlan?: (plan: ReturnType<typeof nativePlanning>) => void,
  recorded = false) {
  const planning = nativePlanning();
  if (outcome === "NEW") { planning.anchors = []; planning.properties = []; planning.listings = []; planning.source_identity_edges = []; }
  changePlan?.(planning);
  if (recorded) planning.match_case_version = 2;
  const selected = { ...matchCase(1), status: outcome === "POSSIBLE_MATCH" ? "PENDING_REVIEW" : "PROPOSED", outcome,
    version: recorded ? 2 : 1,
    candidates: outcome === "NEW" ? [] : [{ ...matchCase(1).candidates[0], listing_id: id(51), observed_listing_version: 13 }],
    identity_decision_ids: recorded ? [id(71)] : [] };
  const plan = { expected_intake_version: 3, expected_property_versions: { [id(41)]: 7 }, expected_listing_versions: { [id(51)]: 13 },
    expected_edge_versions: { [id(81)]: 2 }, candidate_reassignment_plan: [], input_binding: {
      parser_job_id: id(90), parser_run_id: id(91), parser_run_version: 4, source_snapshot_id: id(92),
      original_input_sha256: "a".repeat(64), normalized_payload_sha256: "b".repeat(64), expected_correction_versions: { [id(61)]: 4 } } };
  const decision = { decision_id: id(71), decision_type: "DUPLICATE", status: "PENDING_REVIEW", version: 5,
    match_case_id: id(21), match_case_version: 2, intake_id: id(1), intake_version: 3, proposer_subject_id: id(75),
    proposer_service_principal: null, reviewer_subject_id: null, plan, job_id: null, job_status: null, job_version: null };
  const parent = intake(1);
  const context = makeContext((path, init) => {
    if (init?.method === "POST") return post ? post(init) : json(nativeReceipt(), 201);
    if (path.startsWith("/api/v1/canonical-intakes?")) return json({ items: [parent], next_cursor: null });
    if (path.endsWith("/match-case")) return json(selected);
    if (path.includes("/identity-context?")) return json(planning);
    if (path === `/api/v1/canonical-identity-decisions/${id(71)}`) return json(decision);
    return json(parent);
  });
  return { context, selected, planning, decision, parent };
}
async function openPlanning(context: CanonicalIntakeReadContext) {
  render(<CanonicalIntakeCasePicker context={context} />); open();
  await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
  fireEvent.click(await screen.findByRole("button", { name: "檢視確認資料" }));
  await screen.findByRole("table", { name: "本次確認的補錄欄位" });
}
function acknowledge(kind: "CREATE" | "REVISE" | "DUPLICATE", listing?: string) {
  fireEvent.change(screen.getByLabelText("確認方式"), { target: { value: kind } });
  if (listing) fireEvent.change(screen.getByLabelText("所選既有物件"), { target: { value: listing } });
  fireEvent.change(screen.getByLabelText(/決策／覆核原因/), { target: { value: "已檢視實際補錄內容、候選與版本，確認此次正式決策的影響" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
}
const posts = (context: CanonicalIntakeReadContext) => vi.mocked(context.fetchImpl).mock.calls.filter(([, init]) => init?.method === "POST");

function entryContext(stage: string, post: (init: RequestInit) => Response | Promise<Response>) {
  const parent = { ...intake(1), intake_method: "URL", processing_state: stage, scope: { ...intake(1).scope, region_id: id(88) } };
  return makeContext((path, init) => {
    if (init?.method === "POST") return post(init);
    if (path.startsWith("/api/v1/canonical-intakes?")) return json({ items: [parent], next_cursor: null });
    if (path.endsWith("/match-case")) return json({ detail: "CANONICAL_MATCH_CASE_NOT_FOUND" }, 404);
    return json(parent);
  });
}

function graphContext(post?: (init: RequestInit) => Response | Promise<Response>, withCandidate = false) {
  const properties = [41, 42, 43].map((n, index) => ({ property_id: id(n), version: [7, 11, 3][index], status: "ACTIVE" }));
  const listings = [51, 52, 53, 54].map((n, index) => ({ listing_id: id(n), property_id: id([41, 41, 42, 43][index]),
    version: [13, 19, 23, 29][index], lifecycle_state: "ACTIVE" }));
  const planning: CanonicalIdentityPlanningContext = { match_case_id: id(21), match_case_version: 1, intake_id: id(1), intake_version: 3,
    anchors: [31, 32, 33].map((n, index) => ({ match_candidate_id: id(n), effective_property_id: id(41 + index),
      listing_id: id([51, 53, 54][index]), listing_version: [13, 23, 29][index] })), properties, listings,
    source_identity_edges: [81, 82, 83, 84].map((n, index) => ({ edge_id: id(n), property_id: id([41, 41, 42, 43][index]),
      listing_id: id(51 + index), edge_version: [5, 9, 4, 6][index] })),
    candidate_sites: withCandidate ? [{ candidate_site_id: id(91), property_id: id(41), version: 41, status: "DRAFT", target_format_code: "SYNTHETIC_FORMAT" }] : [] };
  const selected = { ...matchCase(1), candidates: planning.anchors.map((row, index) => ({ match_candidate_id: row.match_candidate_id,
    property_id: row.effective_property_id, listing_id: row.listing_id, rank: index + 1, confidence: "0.9000",
    observed_property_version: properties[index].version, observed_listing_version: row.listing_version })), identity_decision_ids: [] };
  const context = makeContext((path, init) => {
    if (init?.method === "POST") return post ? post(init) : json(nativeReceipt("PENDING_REVIEW"), 201);
    if (path.startsWith("/api/v1/canonical-intakes?")) return json({ items: [intake(1)], next_cursor: null });
    if (path.endsWith("/match-case")) return json(selected);
    if (path.includes("/identity-context?")) {
      expect(path).toContain("include_listing_input=false");
      return json(planning);
    }
    return json(intake(1));
  });
  return { context, planning };
}
async function openGraph(context: CanonicalIntakeReadContext, kind: "MERGE" | "SPLIT") {
  render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
  fireEvent.click(await screen.findByRole("button", { name: "提出合併／拆分計畫" }));
  fireEvent.change(await screen.findByLabelText("身分計畫類型"), { target: { value: kind } });
}
function acknowledgeGraph() {
  fireEvent.change(screen.getByLabelText(/決策／覆核原因/), { target: { value: "已依正式案件觀察的主體、來源關聯與候選點版本明確提出此計畫" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
}

describe("canonical confirmation and independent review in the existing picker", () => {
  it("continues an unresolved URL Intake through the existing planning and confirmation command after graph execution", async () => {
    const { context, selected, parent, planning } = nativeContext("POSSIBLE_MATCH",
      () => json({ ...nativeReceipt(), decision_id: id(79), match_case_version: 3 }, 201), undefined, true);
    parent.intake_method = "URL"; selected.status = "EXECUTED";
    planning.properties[0].version = 8; planning.source_identity_edges[0].edge_version = 3;
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "繼續確認原收件" }));
    await screen.findByRole("table", { name: "本次確認的補錄欄位" });
    expect(screen.queryByRole("button", { name: "提出合併／拆分計畫" })).not.toBeInTheDocument();
    acknowledge("REVISE", id(51)); fireEvent.click(screen.getByRole("button", { name: "確認更新既有物件" }));
    expect(await screen.findByText(/已收到正式回執/)).toHaveTextContent(id(79));
    const [path, init] = posts(context)[0];
    expect(path).toBe(`/api/v1/match-cases/${id(21)}/identity-decisions`);
    expect(new Headers(init?.headers).get("if-match")).toBe('W/"2"');
    expect(JSON.parse(String(init?.body))).toMatchObject({ decision_type: "REVISE", plan: {
      expected_intake_version: 3, expected_property_versions: { [id(41)]: 8 }, expected_edge_versions: { [id(81)]: 3 } } });
  });

  it("shows an unproven graph continuation as a read failure and sends no confirmation", async () => {
    const { context, selected, parent } = nativeContext("POSSIBLE_MATCH", undefined, undefined, true);
    parent.intake_method = "URL"; selected.status = "EXECUTED";
    const original = context.fetchImpl;
    context.fetchImpl = vi.fn((input, init) => String(input).includes("/identity-context?")
      ? Promise.resolve(json({ detail: "IDENTITY_GRAPH_CONTINUATION_PROOF_REQUIRED" }, 409)) : original(input, init));
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "繼續確認原收件" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("table", { name: "本次確認的補錄欄位" })).not.toBeInTheDocument();
    expect(posts(context)).toHaveLength(0);
  });

  it("does not offer continuation for an Intake already READY", async () => {
    const { context, selected, parent } = nativeContext("POSSIBLE_MATCH", undefined, undefined, true);
    parent.intake_method = "URL"; parent.processing_state = "READY"; selected.status = "EXECUTED";
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    await screen.findByRole("button", { name: `檢視決策 ${id(71)}` });
    expect(screen.queryByRole("button", { name: "繼續確認原收件" })).not.toBeInTheDocument();
    expect(context.fetchImpl).not.toHaveBeenCalledWith(expect.stringContaining("/identity-context?"), expect.anything());
  });

  it("submits actual parser/field versions with scoped headers and shows a receipt only after the server answers", async () => {
    const pending = deferred<Response>(); const { context } = nativeContext("NEW", () => pending.promise);
    await openPlanning(context); acknowledge("CREATE");
    const button = screen.getByRole("button", { name: "確認為新物件" });
    fireEvent.click(button); fireEvent.click(button);
    expect(posts(context)).toHaveLength(1);
    expect(screen.queryByText(/已收到正式回執/)).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" }); expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新讀取" })).toBeDisabled();
    const [path, init] = posts(context)[0]; const body = JSON.parse(String(init?.body)); const sent = new Headers(init?.headers);
    expect(path).toBe(`/api/v1/match-cases/${id(21)}/identity-decisions`);
    expect(sent.get("if-match")).toBe('W/"1"'); expect(sent.get("idempotency-key")).toMatch(/^canonical-ui:/);
    expect(sent.get("X-Operator-Role")).toBe("expansion-manager");
    expect(body).toMatchObject({ decision_type: "CREATE", risk_acknowledged: true, plan: { expected_intake_version: 3,
      expected_property_versions: {}, expected_listing_versions: {}, expected_edge_versions: {},
      input_binding: { parser_job_id: id(90), parser_run_version: 4, expected_correction_versions: { [id(61)]: 4 } } } });
    expect(body).not.toHaveProperty("actor_id"); expect(body).not.toHaveProperty("reviewer_id");
    await act(async () => pending.resolve(json(nativeReceipt(), 201)));
    expect(await screen.findByText(/已收到正式回執/)).toHaveTextContent("APPROVED");
    expect(screen.getByText(/工作派送回執尚不代表物件已完成變更/)).toBeVisible();
  });

  it.each(["REVISION", "POSSIBLE_MATCH"])("requires an explicit candidate and binds only its actual resources for %s", async (outcome) => {
    const { context } = nativeContext(outcome); await openPlanning(context); acknowledge("REVISE");
    expect(screen.getByRole("button", { name: "確認更新既有物件" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("所選既有物件"), { target: { value: id(51) } });
    // Changing the observed target requires a fresh acknowledgement.
    expect(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    fireEvent.click(screen.getByRole("button", { name: "確認更新既有物件" }));
    await screen.findByText(/已收到正式回執/);
    expect(JSON.parse(String(posts(context)[0][1]?.body)).plan).toMatchObject({ expected_property_versions: { [id(41)]: 7 },
      expected_listing_versions: { [id(51)]: 13 }, expected_edge_versions: { [id(81)]: 2 } });
  });

  it("records a human duplicate proposal as awaiting independent review without reporting completed execution", async () => {
    const { context } = nativeContext("POSSIBLE_MATCH", () => json(nativeReceipt("PENDING_REVIEW"), 201));
    await openPlanning(context); acknowledge("DUPLICATE", id(51));
    fireEvent.click(screen.getByRole("button", { name: "提案為重複物件，交獨立覆核" }));
    expect(await screen.findByText(/已收到正式回執/)).toHaveTextContent("PENDING_REVIEW");
    expect(screen.getByText(/尚未派送，等待獨立覆核/)).toBeVisible();
    expect(JSON.parse(String(posts(context)[0][1]?.body)).plan).not.toHaveProperty("exact_authority");
  });

  it.each(["masked", "expired", "version"])("blocks confirmation for %s input", async (failure) => {
    const { context } = nativeContext("NEW", undefined, (plan) => {
      if (failure === "masked") Object.assign(plan.listing_input.fields[0], { masked: true, parsed: null, normalized: null, corrected: null });
      else if (failure === "expired") plan.listing_input.manual_entry_valid_until = "2000-01-01T00:00:00Z";
      else plan.match_case_version++;
    });
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "檢視確認資料" }));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(posts(context)).toHaveLength(0);
    if (failure !== "version") { acknowledge("CREATE"); expect(screen.getByRole("button", { name: "確認為新物件" })).toBeDisabled(); }
    if (failure === "masked") expect(screen.getAllByText("無權檢視")).toHaveLength(3);
  });

  it("retains the same command, version and key after an uncertain network response", async () => {
    let tries = 0; const { context } = nativeContext("NEW", () => { if (++tries === 1) throw new TypeError("network unavailable"); return json(nativeReceipt(), 201); });
    await openPlanning(context); acknowledge("CREATE"); fireEvent.click(screen.getByRole("button", { name: "確認為新物件" }));
    fireEvent.click(await screen.findByRole("button", { name: "重試同一請求" })); await screen.findByText(/已收到正式回執/);
    const sent = posts(context); expect(sent).toHaveLength(2); expect(sent[0][1]?.body).toBe(sent[1][1]?.body);
    for (const name of ["idempotency-key", "if-match"]) expect(new Headers(sent[0][1]?.headers).get(name)).toBe(new Headers(sent[1][1]?.headers).get(name));
  });

  it("lets an independent reviewer reject the recorded proposal without requesting positive parser/source input", async () => {
    const { context } = nativeContext("POSSIBLE_MATCH", () => json(nativeReceipt("REJECTED")), undefined, true);
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: `檢視決策 ${id(71)}` }));
    fireEvent.change(await screen.findByLabelText("覆核結果"), { target: { value: "REJECT" } });
    fireEvent.change(screen.getByLabelText(/決策／覆核原因/), { target: { value: "拒絕此提案，維持現有物件" } });
    fireEvent.click(screen.getByRole("button", { name: "確認拒絕" }));
    expect(await screen.findByText(/已收到正式回執/)).toHaveTextContent("REJECTED");
    const [path, init] = posts(context)[0];
    expect(path).toBe(`/api/v1/canonical-identity-decisions/${id(71)}/actions/review`);
    expect(new Headers(init?.headers).get("if-match")).toBe('W/"5"');
    expect(JSON.parse(String(init?.body))).toMatchObject({ decision: "REJECT", expected_case_version: 2, risk_acknowledged: false });
    expect(vi.mocked(context.fetchImpl).mock.calls.some(([path]) => String(path).includes("identity-context"))).toBe(false);
  });

  it("discards a late write receipt after the account-bound context changes", async () => {
    const pending = deferred<Response>(); const { context } = nativeContext("NEW", () => pending.promise);
    const view = render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "檢視確認資料" })); await screen.findByRole("table");
    acknowledge("CREATE"); fireEvent.click(screen.getByRole("button", { name: "確認為新物件" }));
    view.rerender(<CanonicalIntakeCasePicker context={{ ...context, contextKey: "another-account" }} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => pending.resolve(json(nativeReceipt(), 201)));
    expect(screen.queryByText(/已收到正式回執/)).not.toBeInTheDocument();
  });

  it("shows the actual duplicate input before approval and retains a server-side self-review refusal", async () => {
    const { context } = nativeContext("POSSIBLE_MATCH", () => json({ detail: "SELF_REVIEW_DENIED" }, 403), undefined, true);
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: `檢視決策 ${id(71)}` }));
    fireEvent.change(await screen.findByLabelText("覆核結果"), { target: { value: "APPROVE" } });
    await screen.findByRole("table", { name: "本次確認的補錄欄位" });
    fireEvent.change(screen.getByLabelText(/決策／覆核原因/), { target: { value: "確認實際內容及影響範圍後核准" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    fireEvent.click(screen.getByRole("button", { name: "確認核准" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("獨立覆核");
    expect(screen.queryByText(/已收到正式回執/)).not.toBeInTheDocument();
    expect(JSON.parse(String(posts(context)[0][1]?.body))).not.toHaveProperty("reviewer_id");
  });

  it("refuses a success receipt belonging to another case", async () => {
    const { context } = nativeContext("NEW", () => json({ ...nativeReceipt(), match_case_id: id(99) }, 201));
    await openPlanning(context); acknowledge("CREATE"); fireEvent.click(screen.getByRole("button", { name: "確認為新物件" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("回傳資料無法辨識");
    expect(screen.queryByText(/已收到正式回執/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重試同一請求" })).toBeEnabled();
  });

  it("submits a native URL with the actual selected scope and records only the SUBMITTED receipt", async () => {
    const receipt = { intake_id: id(95), intake_method: "URL", processing_state: "SUBMITTED", version: 1,
      submitted_at: "2026-10-19T08:00:00Z", correlation_id: id(96), transition_id: id(97), audit_event_id: id(98), outbox_event_id: id(99) };
    const context = entryContext("AWAITING_SOURCE_AUTHORIZATION", () => json(receipt, 201));
    render(<CanonicalIntakeCasePicker context={context} />); open();
    fireEvent.click(await screen.findByRole("button", { name: "從網址建立正式收件" }));
    expect(screen.queryByTestId("intake-area-select")).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://source.example/new?utm_source=original" } });
    fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "提交這個來源頁的正式收件" } });
    fireEvent.click(screen.getByTestId("intake-submit-button"));
    expect(await screen.findByText(/正式收件 .*已送出/)).toHaveTextContent(id(95));
    const [path, init] = posts(context)[0]; expect(path).toBe("/api/v1/canonical-intakes");
    expect(new Headers(init?.headers).has("if-match")).toBe(false);
    expect(JSON.parse(String(init?.body))).toEqual({ intake_method: "URL", original_url: "https://source.example/new?utm_source=original",
      reason: "提交這個來源頁的正式收件", scope: { brand_id: null, region_id: id(88), assigned_area_id: null, heat_zone_id: null } });
    expect(screen.getByText(/提交回執僅表示已收件/)).toBeVisible();
  });

  it("freezes and retries the same native URL after an uncertain response", async () => {
    let tries = 0; const context = entryContext("AWAITING_SOURCE_AUTHORIZATION", () => {
      if (++tries === 1) throw new TypeError("uncertain synthetic response");
      return json({ detail: "source submission denied" }, 403);
    });
    render(<CanonicalIntakeCasePicker context={context} />); open();
    fireEvent.click(await screen.findByRole("button", { name: "從網址建立正式收件" }));
    fireEvent.change(screen.getByTestId("intake-url-input"), { target: { value: "https://source.example/new" } });
    fireEvent.change(screen.getByLabelText("正式收件範圍"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("送件原因"), { target: { value: "提交新網址並檢查來源權限" } });
    fireEvent.click(screen.getByTestId("intake-submit-button"));
    fireEvent.click(await screen.findByRole("button", { name: "重試同一請求" }));
    await waitFor(() => expect(posts(context)).toHaveLength(2));
    const requests = posts(context); expect(requests[0][1]?.body).toBe(requests[1][1]?.body);
    expect(new Headers(requests[0][1]?.headers).get("idempotency-key")).toBe(new Headers(requests[1][1]?.headers).get("idempotency-key"));
    expect(screen.getByTestId("intake-url-input")).toBeDisabled();
    expect(screen.queryByText(/正式收件 .*已送出/)).not.toBeInTheDocument();
  });

  it("submits initial manual fields as typed proposals with the actual Intake version", async () => {
    const context = entryContext("AWAITING_ASSISTED_ENTRY", () => json({ intake_id: id(1), status: "REQUESTED", version: 3,
      request_id: id(91), requested_at: "2026-10-19T08:00:00Z", audit_event_id: id(92), outbox_event_id: id(93),
      correlation_id: id(94), correction_ids: [id(61), id(62), id(63)] }, 202));
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.change(await screen.findByTestId("assisted-address"), { target: { value: "正式來源頁顯示的地址" } });
    fireEvent.change(screen.getByTestId("assisted-rent"), { target: { value: "43210" } });
    fireEvent.change(screen.getByTestId("assisted-area"), { target: { value: "42.5" } });
    fireEvent.change(screen.getByLabelText("補錄原因"), { target: { value: "依來源頁可見內容提出補錄" } });
    fireEvent.click(screen.getByTestId("assisted-risk-ack"));
    const risk = screen.getByTestId("intake-assisted-risk-summary").textContent;
    fireEvent.click(screen.getByRole("button", { name: "提交補錄提案" }));
    expect(await screen.findByText(/補錄提案 .*已受理/)).toBeVisible();
    const [path, init] = posts(context)[0]; expect(path).toBe(`/api/v1/canonical-intakes/${id(1)}/assisted-entry-completions`);
    expect(new Headers(init?.headers).get("if-match")).toBe('W/"3"');
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "依來源頁可見內容提出補錄", risk_summary: risk,
      risk_acknowledged: true, fields: { address: "正式來源頁顯示的地址", rent: 43210, areaPing: 42.5 } });
    expect(screen.getByText(/此回執不代表解析或比對已完成/)).toBeVisible();
  });

  it("offers no manual form while waiting for source authorization", async () => {
    const context = entryContext("AWAITING_SOURCE_AUTHORIZATION", () => { throw new Error("No request permitted"); });
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    expect(await screen.findByText(/人工補錄須先由既定流程確認另有明確核准/)).toBeVisible();
    expect(screen.queryByTestId("intake-assisted-entry")).not.toBeInTheDocument(); expect(posts(context)).toHaveLength(0);
  });

  it("binds the complete affected graph for a merge and requires an explicit candidate disposition", async () => {
    const { context } = graphContext(undefined, true); await openGraph(context, "MERGE");
    fireEvent.change(screen.getByLabelText("合併目標主體"), { target: { value: id(42) } });
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`來源 ${id(41)}`) })); acknowledgeGraph();
    expect(screen.getByRole("button", { name: "提交合併提案" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`候選點 ${id(91)} 的處置`), { target: { value: "KEEP_HISTORICAL" } });
    expect(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    fireEvent.click(screen.getByRole("button", { name: "提交合併提案" }));
    expect(await screen.findByText(/已收到正式回執/)).toHaveTextContent("PENDING_REVIEW");
    const [path, init] = posts(context)[0]; expect(path).toBe(`/api/v1/match-cases/${id(21)}/identity-decisions`);
    expect(new Headers(init?.headers).get("if-match")).toBe('W/"1"');
    expect(JSON.parse(String(init?.body))).toMatchObject({ decision_type: "MERGE", risk_acknowledged: true, plan: {
      source_property_ids: [id(41)], target_property_id: id(42), expected_property_versions: { [id(41)]: 7, [id(42)]: 11 },
      expected_listing_versions: { [id(51)]: 13, [id(52)]: 19, [id(53)]: 23 }, expected_edge_versions: { [id(81)]: 5, [id(82)]: 9 },
      candidate_reassignment_plan: [{ candidate_site_id: id(91), expected_version: 41, disposition: "KEEP_HISTORICAL", target_property_id: null }] } });
    const plan = JSON.parse(String(init?.body)).plan;
    expect(plan.expected_property_versions).not.toHaveProperty(id(43)); expect(plan.expected_listing_versions).not.toHaveProperty(id(54));
    expect(plan.expected_edge_versions).not.toHaveProperty(id(83));
  });

  it("requires all source edges to be assigned to at least two distinct split targets", async () => {
    const { context } = graphContext(); await openGraph(context, "SPLIT");
    fireEvent.change(screen.getByLabelText("拆分來源主體"), { target: { value: id(41) } });
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`目標 ${id(42)}`) }));
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`目標 ${id(43)}`) }));
    fireEvent.change(screen.getByLabelText(`關聯 ${id(81)} 的拆分目標`), { target: { value: id(42) } }); acknowledgeGraph();
    expect(screen.getByRole("button", { name: "提交拆分提案" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`關聯 ${id(82)} 的拆分目標`), { target: { value: id(42) } });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    expect(screen.getByRole("button", { name: "提交拆分提案" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`關聯 ${id(82)} 的拆分目標`), { target: { value: id(43) } });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    fireEvent.click(screen.getByRole("button", { name: "提交拆分提案" })); await screen.findByText(/已收到正式回執/);
    const body = JSON.parse(String(posts(context)[0][1]?.body)); expect(body.decision_type).toBe("SPLIT");
    expect(body.plan.partitions).toEqual([{ target_property_id: id(42), source_identity_edge_ids: [id(81)] },
      { target_property_id: id(43), source_identity_edge_ids: [id(82)] }]);
    expect(body.plan.expected_property_versions).toEqual({ [id(41)]: 7, [id(42)]: 11, [id(43)]: 3 });
    expect(body.plan.expected_listing_versions).toEqual({ [id(51)]: 13, [id(52)]: 19, [id(53)]: 23, [id(54)]: 29 });
    expect(body.plan.expected_edge_versions).toEqual({ [id(81)]: 5, [id(82)]: 9 });
  });

  it("requires an explicit eligible target before reassigning a Candidate", async () => {
    const { context } = graphContext(undefined, true); await openGraph(context, "MERGE");
    fireEvent.change(screen.getByLabelText("合併目標主體"), { target: { value: id(42) } });
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`來源 ${id(41)}`) }));
    fireEvent.change(screen.getByLabelText(`候選點 ${id(91)} 的處置`), { target: { value: "REASSIGN" } }); acknowledgeGraph();
    expect(screen.getByRole("button", { name: "提交合併提案" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(`候選點 ${id(91)} 的目標`), { target: { value: id(42) } });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已閱讀上述影響/ }));
    fireEvent.click(screen.getByRole("button", { name: "提交合併提案" })); await screen.findByText(/已收到正式回執/);
    expect(JSON.parse(String(posts(context)[0][1]?.body)).plan.candidate_reassignment_plan).toEqual([
      { candidate_site_id: id(91), expected_version: 41, disposition: "REASSIGN", target_property_id: id(42) }]);
  });

  it("freezes graph choices and reuses the captured proposal after an uncertain response", async () => {
    let tries = 0; const { context } = graphContext(() => { if (++tries === 1) throw new TypeError("uncertain graph response");
      return json(nativeReceipt("PENDING_REVIEW"), 201); });
    await openGraph(context, "MERGE"); fireEvent.change(screen.getByLabelText("合併目標主體"), { target: { value: id(42) } });
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`來源 ${id(41)}`) })); acknowledgeGraph();
    fireEvent.click(screen.getByRole("button", { name: "提交合併提案" }));
    fireEvent.click(await screen.findByRole("button", { name: "重試同一請求" })); await screen.findByText(/已收到正式回執/);
    const requests = posts(context); expect(requests).toHaveLength(2); expect(requests[0][1]?.body).toBe(requests[1][1]?.body);
    expect(new Headers(requests[0][1]?.headers).get("idempotency-key")).toBe(new Headers(requests[1][1]?.headers).get("idempotency-key"));
    expect(screen.getByLabelText("合併目標主體")).toBeDisabled();
  });

  it("refuses a stale graph-only context without offering a proposal", async () => {
    const { context, planning } = graphContext(); planning.match_case_version++;
    render(<CanonicalIntakeCasePicker context={context} />); open(); await screen.findByRole("button", { name: new RegExp(id(1)) }); select(1);
    fireEvent.click(await screen.findByRole("button", { name: "提出合併／拆分計畫" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("版本");
    expect(screen.queryByLabelText("身分計畫類型")).not.toBeInTheDocument(); expect(posts(context)).toHaveLength(0);
  });
});
