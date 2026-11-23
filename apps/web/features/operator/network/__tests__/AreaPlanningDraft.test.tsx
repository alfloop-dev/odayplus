import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AreaPlanningDraftDialog } from "../AreaPlanningDraftDialog";
import { createAreaPlanningDraftApi, isAreaNativeScenario, isAreaPlanningDraft, type AreaPlanningDraft } from "../areaPlanningDraftClient";

// HTTP doubles isolate UI recovery and untrusted DTOs; PostgreSQL ownership,
// signed principal admission and transactions have separate real-DB evidence.
const zone = "00000000-0000-0000-0000-000000000011";
const id = "00000000-0000-0000-0000-000000000021";
const actor = "00000000-0000-0000-0000-000000000031";
const tenant = "00000000-0000-0000-0000-000000000041";
const audit = "00000000-0000-0000-0000-000000000051";
const candidate = "00000000-0000-0000-0000-000000000061";
function row(updates: Partial<AreaPlanningDraft> = {}): AreaPlanningDraft {
  return { draftId: id, tenantId: tenant, createdBy: actor, heatZoneId: zone, name: "Synthetic area draft",
    quarter: null, budget: null, candidateIds: [], version: 1, status: "DRAFT",
    createdAt: "2026-10-16T12:00:00Z", updatedAt: "2026-10-16T12:00:00Z",
    nativeScenarioId: null, materializedBy: null, ...updates };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { "Content-Type": "application/json" } });
const receipt = (draft = row()) => ({ draft, auditEventIds: [audit], receiptId: "synthetic-private-command", replayed: false });
const headers = {}, guard = async () => true;
function dialog(onClose = vi.fn(), sessionGuard = guard) {
  return render(<AreaPlanningDraftDialog zoneId={zone} zoneLabel="Synthetic area" candidates={[{ id: candidate, title: "Synthetic candidate" }]}
    headers={headers} guard={sessionGuard} onClose={onClose} />);
}
const fill = () => fireEvent.change(screen.getByLabelText("草稿名稱"), { target: { value: row().name } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Quarterly draft DTO admission", () => {
  it.each([
    { heatZoneId: "other" }, { tenantId: "invalid" }, { version: 0 }, { quarter: "2026Q5" },
    { budget: "0" }, { budget: -1 }, { candidateIds: "ABC" }, { candidateIds: [candidate, candidate] },
    { updatedAt: "2020-01-01T00:00:00Z" }, { status: "APPROVED" }, { nativeScenarioId: "forged" },
  ])("rejects malformed or foreign draft %j", updates => {
    expect(isAreaPlanningDraft({ ...row(), ...updates }, zone)).toBe(false);
  });
  it("rejects a seed-marked command despite valid receipt fields", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...receipt(), source: "seed" }, 201)));
    expect((await createAreaPlanningDraftApi(zone, headers, guard).command("create", row(), "synthetic-command")).ok).toBe(false);
  });
  it("re-admits the current session after parsing a response", async () => {
    let current = true;
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => {
      current = false; return { heatZoneId: zone, items: [row()] };
    } })));
    expect(await createAreaPlanningDraftApi(zone, headers, async () => current).list()).toEqual({ ok: false, superseded: true });
  });
});

describe("Quarterly draft user flow and recovery", () => {
  it("saves blank planning inputs without inventing a quarter, budget, candidates or authority", async () => {
    let saved: AreaPlanningDraft | null = null;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { const body = JSON.parse(String(init.body)); saved = row(body); return response(receipt(saved), 201); }
      return response(url.endsWith(id) ? saved : { heatZoneId: zone, items: saved ? [saved] : [] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    await screen.findByText("本區尚無規劃草稿。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));
    await screen.findByText(/已核對區域規劃草稿.*v1/);
    expect(JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body)))
      .toEqual({ name: row().name, quarter: null, budget: null, candidateIds: [], heatZoneId: zone });
    expect(screen.getByLabelText("展店預算（元）")).toHaveValue(null);
    expect(screen.getByLabelText("規劃季度")).toHaveValue("");
  });
  it("reopens and updates the current version with an explicitly entered zero and chosen candidate", async () => {
    let saved = row();
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); expect(body.expectedVersion).toBe(1);
        saved = row({ ...body, version: 2 }); return response(receipt(saved)); }
      return response(url.endsWith(id) ? saved : { heatZoneId: zone, items: [saved] });
    });vi.stubGlobal("fetch", fetcher);dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));
    await screen.findByDisplayValue(row().name);
    fireEvent.change(screen.getByLabelText("規劃季度"), { target: { value: "2027Q2" } });
    fireEvent.change(screen.getByLabelText("展店預算（元）"), { target: { value: "0" } });
    fireEvent.click(screen.getByLabelText("Synthetic candidate"));
    fireEvent.click(screen.getByRole("button", { name: "保存草稿修改" }));
    await screen.findByText(/已核對區域規劃草稿.*v2/);
    expect(saved).toMatchObject({ quarter: "2027Q2", budget: 0, candidateIds: [candidate] });
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("locks an uncertain create and retries the identical key and payload", async () => {
    let writes = 0;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { if (++writes === 1) throw new Error("Synthetic lost reply"); return response(receipt(), 201); }
      return response(url.endsWith(id) ? row() : { heatZoneId: zone, items: [] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    await screen.findByText("本區尚無規劃草稿。");fill();
    fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));await screen.findByRole("alert");
    expect(screen.getByLabelText("草稿名稱")).toBeDisabled();
    expect(screen.getByRole("button", { name: "建立另一份草稿" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重試上次保存" }));
    await screen.findByText(/已核對區域規劃草稿.*v1/);
    const commands = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(commands).toHaveLength(2);
    expect(commands[0][1]?.body).toEqual(commands[1][1]?.body);
    expect((commands[0][1]?.headers as Record<string,string>)["Idempotency-Key"])
      .toBe((commands[1][1]?.headers as Record<string,string>)["Idempotency-Key"]);
  });
  it("preserves an accepted identity across failed readback and reads its newer revision without creating again", async () => {
    let readbacks = 0;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response(receipt(), 201);
      if (url.endsWith(id)) return ++readbacks === 1 ? response({}, 503) : response(row({ name: "Concurrent update", version: 2 }));
      return response({ heatZoneId: zone, items: [] });
    });vi.stubGlobal("fetch", fetcher);dialog();await screen.findByText("本區尚無規劃草稿。");fill();
    fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "保存草稿修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "建立另一份草稿" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新讀取草稿" }));
    await screen.findByText("已讀取目前草稿 Concurrent update v2。");
    expect(screen.getByLabelText("草稿名稱")).toHaveValue("Concurrent update");
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
  it("keeps edits after definite CAS rejection and requires the current version before success", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") return response({ detail: "AREA_PLANNING_COMMAND_CONFLICT" }, 409);
      return response(url.endsWith(id) ? row() : { heatZoneId: zone, items: [row()] });
    });vi.stubGlobal("fetch", fetcher);dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));await screen.findByDisplayValue(row().name);
    fireEvent.change(screen.getByLabelText("草稿名稱"), { target: { value: "Retained edit" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿修改" }));await screen.findByRole("alert");
    expect(screen.getByLabelText("草稿名稱")).toHaveValue("Retained edit");
    expect(screen.queryByRole("button", { name: "重試上次保存" })).toBeNull();
    expect(screen.queryByText(/已核對區域規劃草稿/)).toBeNull();
  });
  it.each(["denied", "superseded"])("clears data when an accepted command readback is %s", async kind => {
    let accepted = false;
    const close = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { accepted = true; return response(receipt(), 201); }
      return response(accepted ? {} : { heatZoneId: zone, items: [] }, accepted ? 403 : 200);
    }));
    let afterWrite = 0;
    dialog(close, async () => kind !== "superseded" || !accepted || ++afterWrite <= 2);
    await screen.findByText("本區尚無規劃草稿。");fill();
    fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("草稿名稱")).toHaveValue("");
    expect(screen.queryByText(new RegExp(id))).toBeNull();
  });
});

it("keeps a 409 incomplete durable command locked to its original key", async () => {
  let writes = 0;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return ++writes === 1
      ? response({ detail: "AREA_PLANNING_COMMAND_INCOMPLETE", error: { code: "conflict" } }, 409)
      : response({ ...receipt(), replayed: true }, 201);
    return response(url.endsWith(id) ? row() : { heatZoneId: zone, items: [] });
  }); vi.stubGlobal("fetch", fetcher); dialog();await screen.findByText("本區尚無規劃草稿。");fill();
  fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "建立另一份草稿" })).toBeDisabled();
  expect(screen.getByLabelText("草稿名稱")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "重試上次保存" }));await screen.findByText(/已核對區域規劃草稿.*v1/);
  const writesSent = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
  expect((writesSent[0][1]?.headers as Record<string,string>)["Idempotency-Key"])
    .toBe((writesSent[1][1]?.headers as Record<string,string>)["Idempotency-Key"]);
});

it.each([
  { tenantId: "00000000-0000-0000-0000-000000000099" },
  { createdBy: "00000000-0000-0000-0000-000000000099" },
  { createdAt: "2026-10-16T11:00:00Z" },
  { updatedAt: "2026-10-16T12:01:00Z" },
])("rejects a known-version readback whose facts change: %j", async changed => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return response(receipt(), 201);
    return response(url.endsWith(id) ? row(changed) : { heatZoneId: zone, items: [] });
  }));dialog();await screen.findByText("本區尚無規劃草稿。");fill();
  fireEvent.click(screen.getByRole("button", { name: "保存區域草稿" }));await screen.findByRole("alert");
  expect(screen.queryByText(/已核對區域規劃草稿/)).toBeNull();
  expect(screen.getByRole("button", { name: "建立另一份草稿" })).toBeDisabled();
  expect(screen.getByLabelText("草稿名稱")).toHaveValue(row().name);
});

it.each([
  row({ version: 1 }), row({ version: 3, tenantId: "00000000-0000-0000-0000-000000000099" }),
])("rejects stale or identity-swapped open after a v2 listing: %j", async loaded => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(url.endsWith(id)
    ? loaded : { heatZoneId: zone, items: [row({ version: 2 })] })));
  dialog();fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v2` }));
  await screen.findByRole("alert");expect(screen.getByLabelText("草稿名稱")).toHaveValue("");
});

describe("Area draft explicit native NetPlan DRAFT", () => {
  const scenarioId = "netplan-scenario-area-draft-" + id;
  const complete = row({ quarter: "2027Q1", budget: 2500000, candidateIds: [candidate] });
  const linked = row({ quarter: "2027Q1", budget: 2500000, candidateIds: [candidate], version: 2, status: "MATERIALIZED",
    updatedAt: "2026-10-16T12:05:00Z", nativeScenarioId: scenarioId, materializedBy: actor });
  const nativeDetail = { scenario_id: scenarioId, tenant_id: tenant, scenario_name: complete.name, planning_horizon: "2027Q1",
    status: "draft", created_at: "2026-10-16T12:05:00Z", constraints: { max_budget: 2500000 }, solve: null, approvals: [],
    options_by_entity: { [candidate]: [
      { entity_id: candidate, action: "OPEN", expected_gross_margin: 0, budget_cost: 0, risk_score: 0.3, capacity_delta: 1,
        source_snapshot_ids: [], notes: [] },
      { entity_id: candidate, action: "KEEP", expected_gross_margin: 0, budget_cost: 0, risk_score: 0, capacity_delta: 0,
        source_snapshot_ids: [], notes: ["defer_candidate_site"] }] } };
  const isNative = (url: string) => url.startsWith("/api/v1/netplan/scenarios/");
  const linkedReceipt = { draft: linked, auditEventIds: [audit, "00000000-0000-0000-0000-000000000052"],
    receiptId: "synthetic-private-command", replayed: false };

  it("creates the original native DRAFT on explicit request and shows fresh area and native readback", async () => {
    let materialized = false;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { materialized = true; return response(linkedReceipt); }
      if (isNative(url)) return response(nativeDetail);
      const current = materialized ? linked : complete;
      return response(url.endsWith(id) ? current : { heatZoneId: zone, items: [current] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    fireEvent.click(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" }));
    await screen.findByText(`已核對區域規劃草稿 ${complete.name} 與其 NetPlan 情境草稿。`);
    const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(post[0]).toBe(`/api/v1/operator/area-planning-drafts/${id}/materialize`);
    expect(JSON.parse(String(post[1]?.body))).toEqual({ expectedVersion: 1 });
    expect(fetcher.mock.calls.some(([url]) => url === `/api/v1/netplan/scenarios/${scenarioId}`)).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => /solve|submit|decide/.test(String(url)))).toBe(false);
    const readback = screen.getByRole("region", { name: "NetPlan 情境草稿讀回" });
    expect(readback).toHaveTextContent(`${complete.name} · 2027Q1 · 狀態 draft · 預算上限 2500000`);
    expect(readback).toHaveTextContent("此情境尚未解算或送審");
    expect(readback).toHaveTextContent("期望毛利／成本／風險／產能：未提供來源，數值尚未核實（不代表 0 或預設值）");
    expect(readback).not.toHaveTextContent("期望毛利 0");
    expect(readback).not.toHaveTextContent("產能 1");
    expect(screen.getByLabelText("草稿名稱")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "建立 NetPlan 情境草稿" })).toBeNull();
  });
  it("keeps the same key and expected version for an uncertain native creation retry", async () => {
    let writes = 0;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { if (++writes === 1) throw new Error("Synthetic lost reply"); return response({ ...linkedReceipt, replayed: true }); }
      if (isNative(url)) return response(nativeDetail);
      const current = writes > 1 ? linked : complete;
      return response(url.endsWith(id) ? current : { heatZoneId: zone, items: [current] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    fireEvent.click(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "建立另一份草稿" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重試建立 NetPlan 情境草稿" }));
    await screen.findByText(`已核對區域規劃草稿 ${complete.name} 與其 NetPlan 情境草稿。`);
    const posts = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0][1]?.body).toEqual(posts[1][1]?.body);
    expect((posts[0][1]?.headers as Record<string, string>)["Idempotency-Key"])
      .toBe((posts[1][1]?.headers as Record<string, string>)["Idempotency-Key"]);
  });
  it("reads the linked native DRAFT when a recovered update readback was concurrently materialized", async () => {
    const saved = { ...complete, version: 2, updatedAt: "2026-10-16T12:01:00Z" };
    const concurrent = { ...linked, version: 3 };
    let writes = 0;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        if (++writes === 1) throw new Error("Synthetic lost reply");
        return response({ ...receipt(saved), replayed: true });
      }
      if (isNative(url)) return response(nativeDetail);
      const current = writes > 1 ? concurrent : complete;
      return response(url.endsWith(id) ? current : { heatZoneId: zone, items: [current] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿修改" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "重試上次保存" }));
    await screen.findByText(`已核對區域規劃草稿 ${complete.name} v3；目前紀錄已連結 NetPlan 情境草稿。`);
    const readback = screen.getByRole("region", { name: "NetPlan 情境草稿讀回" });
    expect(readback).toHaveTextContent(`${complete.name} · 2027Q1 · 狀態 draft · 預算上限 2500000`);
    expect(readback).not.toHaveTextContent("讀取 NetPlan 情境草稿中…");
    expect(fetcher.mock.calls.filter(([url]) => url === `/api/v1/netplan/scenarios/${scenarioId}`)).toHaveLength(1);
    const writesSent = fetcher.mock.calls.filter(([, init]) => init?.method && init.method !== "GET");
    expect(writesSent).toHaveLength(2);
    expect(writesSent.every(([url, init]) => init?.method === "PUT" && !String(url).endsWith("/materialize"))).toBe(true);
    expect(writesSent[0][1]?.body).toEqual(writesSent[1][1]?.body);
    expect(screen.getByLabelText("草稿名稱")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "建立 NetPlan 情境草稿" })).toBeNull();
  });
  it("offers native creation only for saved complete inputs without unsaved edits", async () => {
    let current = row({ quarter: "2027Q1", candidateIds: [candidate] });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => response(url.endsWith(id) ? current : { heatZoneId: zone, items: [current] })));
    dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    expect(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" })).toBeDisabled();
    cleanup(); current = complete; dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    expect(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("展店預算（元）"), { target: { value: "100" } });
    expect(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" })).toBeDisabled();
  });
  it("keeps a definite native refusal visible without retaining a retry", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response({ detail: "NATIVE_SCENARIO_ID_ALREADY_EXISTS" }, 409);
      return response(url.endsWith(id) ? complete : { heatZoneId: zone, items: [complete] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v1` }));
    await screen.findByDisplayValue(complete.name);
    fireEvent.click(screen.getByRole("button", { name: "建立 NetPlan 情境草稿" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "重試建立 NetPlan 情境草稿" })).toBeNull();
    expect(screen.getByLabelText("草稿名稱")).toHaveValue(complete.name);
  });
  it("reopens a linked draft from native GET and keeps the area draft when native read is denied", async () => {
    let nativeStatus = 200;
    const fetcher = vi.fn(async (url: string) => {
      if (isNative(url)) return response(nativeStatus === 200 ? nativeDetail : { detail: "NATIVE_OBJECT_SCOPE_DENIED" }, nativeStatus);
      return response(url.endsWith(id) ? linked : { heatZoneId: zone, items: [linked] });
    }); vi.stubGlobal("fetch", fetcher);
    const close = vi.fn(); dialog(close);
    fireEvent.click(await screen.findByRole("button", { name: `${complete.name} · v2 · 已建立 NetPlan` }));
    expect(await screen.findByText(`${complete.name} · 2027Q1 · 狀態 draft · 預算上限 2500000`)).toBeVisible();
    nativeStatus = 403;
    fireEvent.click(screen.getByRole("button", { name: "重新讀取草稿" }));
    await screen.findByText("目前身分無法讀取此 NetPlan 情境；區域草稿仍保留。");
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByLabelText("草稿名稱")).toHaveValue(complete.name);
    expect(screen.queryByText(scenarioId)).toBeNull();
  });
  it.each([{ tenant_id: "00000000-0000-0000-0000-000000000099" }, { scenario_id: "netplan-scenario-other" },
    { constraints: {} }, { options_by_entity: { [candidate]: [{ entity_id: "other", action: "OPEN" }] } }])(
    "rejects a foreign or malformed native readback %j", changed => {
      expect(isAreaNativeScenario({ ...nativeDetail, ...changed }, linked)).toBe(false);
    });
});
