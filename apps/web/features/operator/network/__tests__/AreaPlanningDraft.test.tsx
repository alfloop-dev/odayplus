import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AreaPlanningDraftDialog } from "../AreaPlanningDraftDialog";
import { createAreaPlanningDraftApi, isAreaPlanningDraft, type AreaPlanningDraft } from "../areaPlanningDraftClient";

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
