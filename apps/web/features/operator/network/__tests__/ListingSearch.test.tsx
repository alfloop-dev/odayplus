import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ListingSearchDialog } from "../ListingSearchDialog";
import { createListingSearchApi, type PersonalListingSearch, type ListingQuery } from "../listingSearchClient";

// Explicit HTTP doubles for UI/DTO failure handling. Durable ownership and
// automatic receipt matching are proved separately against real PostgreSQL.
const zone = "00000000-0000-0000-0000-000000000011";
const id = "00000000-0000-0000-0000-000000000021";
const owner = "00000000-0000-0000-0000-000000000031";
const tenant = "00000000-0000-0000-0000-000000000041";
const audit = "00000000-0000-0000-0000-000000000051";
const stamp = "2026-10-16T09:00:00Z";
const query: ListingQuery = { kind: "listing-search.v1", heatZoneId: zone,
  minAreaPing: null, maxAreaPing: null, maxRentPerMonth: null, groundFloorOnly: false };
function row(updates: Partial<PersonalListingSearch> = {}): PersonalListingSearch {
  return { saved_view_id: id, owner_subject_id: owner, tenant_id: tenant, visibility: "PRIVATE",
    name: "Synthetic personal criteria", query, version: 1, created_at: stamp, deleted_at: null, ...updates };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status,
  headers: { "Content-Type": "application/json" } });
const command = (search = row()) => ({ search, auditEventIds: [audit], receiptId: "synthetic-command-receipt", replayed: false });
const guard = async () => true;
function dialog() { render(<ListingSearchDialog zoneId={zone} zoneLabel="Synthetic zone" headers={{}}
  guard={guard} onClose={vi.fn()} onOpenIntake={vi.fn()} />); }
const fill = () => fireEvent.change(screen.getByLabelText("條件名稱"), { target: { value: row().name } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Personal criteria HTTP binding", () => {
  it.each(["wrong-zone", "shared", "bad-version", "fake-audit", "bad-query", "seed"])("rejects %s command data", async kind => {
    let search: unknown = row(), value: unknown = command();
    if (kind === "wrong-zone") search = row({ query: { ...query, heatZoneId: "other-zone" } });
    if (kind === "shared") search = { ...row(), visibility: "TENANT" };
    if (kind === "bad-version") search = row({ version: 2 });
    if (kind === "bad-query") search = { ...row(), query: { ...query, maxRentPerMonth: "60000" } };
    value = { ...command(), search, ...(kind === "fake-audit" ? { auditEventIds: [] } : {}), ...(kind === "seed" ? { source: "seed" } : {}) };
    vi.stubGlobal("fetch", vi.fn(async () => response(value, 201)));
    expect((await createListingSearchApi(zone, {}, guard).command("create", { name: row().name, query }, "synthetic-command")).ok).toBe(false);
  });

  it("checks the current session again after receiving the body", async () => {
    let current = true;
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => {
      current = false; return { heatZoneId: zone, items: [row()] };
    } })));
    expect(await createListingSearchApi(zone, {}, async () => current).list()).toEqual({ ok: false, superseded: true });
  });

  it("does not start a command after the session guard is lost", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    expect(await createListingSearchApi(zone, {}, async () => false).command("create", { name: row().name, query }, "synthetic-command"))
      .toEqual({ ok: false, superseded: true });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("Personal criteria dialog", () => {
  it("saves and reopens real response identities with blank limits, without inventing rent or size", async () => {
    let saved: PersonalListingSearch | null = null;
    const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") { const value = JSON.parse(String(init.body)); saved = row({ name: value.name, query: value.query }); return response(command(saved), 201); }
      if (input.endsWith("/results")) return response({ search: saved, items: [], total: 0 });
      if (input.endsWith(id)) return response(saved);
      return response({ heatZoneId: zone, items: saved ? [saved] : [] });
    }); vi.stubGlobal("fetch", fetcher); dialog();
    await screen.findByText("本區尚無個人搜尋條件。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
    await screen.findByText(/已核對個人搜尋條件.*v1/);
    const posted = fetcher.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(posted[1]?.body))).toEqual({ name: row().name, query });
    expect(JSON.parse(String(posted[1]?.body))).not.toHaveProperty("tenant_id");
    fireEvent.click(screen.getByRole("button", { name: "重新讀取此條件與結果" }));
    await screen.findByRole("region", { name: "目前可見的符合進件" });
    expect(screen.getByLabelText("條件名稱")).toHaveValue(row().name);
    expect(screen.getByLabelText("每月租金上限（元）")).toHaveValue(null);
  });

  it("retries an uncertain create with the same idempotency key and preserves input", async () => {
    let posts = 0;
    const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") { if (++posts === 1) throw new Error("Synthetic lost response"); return response(command(), 201); }
      return response(input.endsWith(id) ? row() : { heatZoneId: zone, items: [] });
    });vi.stubGlobal("fetch", fetcher); dialog();
    await screen.findByText("本區尚無個人搜尋條件。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
    await screen.findByRole("alert");expect(screen.getByLabelText("條件名稱")).toHaveValue(row().name);
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
    await screen.findByText(/已核對個人搜尋條件.*v1/);
    const requests = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(requests).toHaveLength(2);
    expect((requests[0][1]?.headers as Record<string, string>)["Idempotency-Key"])
      .toBe((requests[1][1]?.headers as Record<string, string>)["Idempotency-Key"]);
  });

  it("keeps an accepted create locked until the recorded identity is read back; refresh never creates again", async () => {
    let saved = false, getFailed = false;
    const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") { saved = true; return response(command(), 201); }
      if (input.endsWith(id) && !getFailed) { getFailed = true; return response({}, 503); }
      return response(input.endsWith(id) ? row() : { heatZoneId: zone, items: saved ? [row()] : [] });
    });vi.stubGlobal("fetch", fetcher); dialog();
    await screen.findByText("本區尚無個人搜尋條件。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "保存修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "建立另一個條件" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "重新讀取已保存條件" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存修改" })).not.toBeDisabled());
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("edits with the selected revision and confirms deletion without another create", async () => {
    let saved: PersonalListingSearch | null = row();
    const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); expect(body.expectedVersion).toBe(1);
        saved = row({ version: 2, name: body.name, query: body.query });return response(command(saved)); }
      if (init?.method === "DELETE") { expect(JSON.parse(String(init.body))).toEqual({ expectedVersion: 2 });
        const deleted = row({ ...saved!, version: 3, deleted_at: stamp });saved = null;return response(command(deleted)); }
      if (input.endsWith("/results")) return response({ search: saved, items: [], total: 0 });
      return response(input.endsWith(id) ? saved : { heatZoneId: zone, items: saved ? [saved] : [] });
    });vi.stubGlobal("fetch", fetcher); dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));
    await screen.findByRole("region", { name: "目前可見的符合進件" });
    fireEvent.change(screen.getByLabelText("每月租金上限（元）"), { target: { value: "45000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));await screen.findByText(/已核對個人搜尋條件.*v2/);
    fireEvent.click(screen.getByRole("button", { name: "刪除個人條件" }));
    fireEvent.click(screen.getByRole("button", { name: "確認刪除" }));await screen.findByText(/已核對：個人搜尋條件已刪除/);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  });

  it("retains edited input on a 409 and clears private data on a current 403", async () => {
    let deny = false;
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "PUT") return response({}, 409);
      if (deny) return response({}, 403);
      return response(input.endsWith("/results") ? { search: row(), items: [], total: 0 } : { heatZoneId: zone, items: [row()] });
    }));dialog();
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));
    await screen.findByRole("region", { name: "目前可見的符合進件" });
    fireEvent.change(screen.getByLabelText("條件名稱"), { target: { value: "My retained edit" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));await screen.findByRole("alert");
    expect(screen.getByLabelText("條件名稱")).toHaveValue("My retained edit");
    deny = true;fireEvent.click(screen.getByRole("button", { name: "重新讀取已保存條件" }));
    await waitFor(() => expect(screen.getByLabelText("條件名稱")).toHaveValue(""));
    expect(screen.queryByRole("button", { name: `${row().name} · v1` })).toBeNull();
  });
});

it.each(["create", "delete"] as const)("clears private UI when %s readback is superseded after the command was accepted", async operation => {
  let writes = 0, postWriteChecks = 0;
  const capturedGuard = async () => writes === 0 || ++postWriteChecks <= 2;
  const close = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (["POST", "DELETE"].includes(init?.method ?? "")) {
      writes++;
      return response(command(operation === "delete" ? row({ version: 2, deleted_at: stamp }) : row()), operation === "create" ? 201 : 200);
    }
    return response(input.endsWith("/results") ? { search: row(), items: [], total: 0 }
      : { heatZoneId: zone, items: operation === "create" ? [] : [row()] });
  }));
  render(<ListingSearchDialog zoneId={zone} zoneLabel="Synthetic zone" headers={{}} guard={capturedGuard} onClose={close} onOpenIntake={vi.fn()} />);
  if (operation === "create") {
    await screen.findByText("本區尚無個人搜尋條件。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
  } else {
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));
    await screen.findByRole("region", { name: "目前可見的符合進件" });
    fireEvent.click(screen.getByRole("button", { name: "刪除個人條件" }));
    fireEvent.click(screen.getByRole("button", { name: "確認刪除" }));
  }
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText("條件名稱")).toHaveValue("");
  expect(screen.queryByText(new RegExp(id))).toBeNull();
  expect(screen.queryByRole("button", { name: `${row().name} · v1` })).toBeNull();
  expect(writes).toBe(1);
});

it.each(["create", "delete"] as const)("clears private UI on current 403 in the accepted %s readback", async operation => {
  let accepted = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (["POST", "DELETE"].includes(init?.method ?? "")) {
      accepted = true;
      return response(command(operation === "delete" ? row({ version: 2, deleted_at: stamp }) : row()), operation === "create" ? 201 : 200);
    }
    if (accepted) return response({}, 403);
    return response(input.endsWith("/results") ? { search: row(), items: [], total: 0 }
      : { heatZoneId: zone, items: operation === "create" ? [] : [row()] });
  }));dialog();
  if (operation === "create") {
    await screen.findByText("本區尚無個人搜尋條件。"); fill();
    fireEvent.click(screen.getByRole("button", { name: "保存個人條件" }));
  } else {
    fireEvent.click(await screen.findByRole("button", { name: `${row().name} · v1` }));
    await screen.findByRole("region", { name: "目前可見的符合進件" });
    fireEvent.click(screen.getByRole("button", { name: "刪除個人條件" }));
    fireEvent.click(screen.getByRole("button", { name: "確認刪除" }));
  }
  await screen.findByRole("alert");
  expect(screen.getByLabelText("條件名稱")).toHaveValue("");
  expect(screen.queryByRole("button", { name: `${row().name} · v1` })).toBeNull();
});
