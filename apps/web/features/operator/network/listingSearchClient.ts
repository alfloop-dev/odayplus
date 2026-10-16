import { payloadContainsSeedData } from "../operatorDataMode";

export type ListingQuery = { kind: "listing-search.v1"; heatZoneId: string;
  minAreaPing: number | null; maxAreaPing: number | null; maxRentPerMonth: number | null; groundFloorOnly: boolean };
export type PersonalListingSearch = { saved_view_id: string; owner_subject_id: string; tenant_id: string;
  visibility: "PRIVATE"; name: string; query: ListingQuery; version: number; created_at: string;
  deleted_at: string | null };
export type ListingSearchMatches = { search: PersonalListingSearch;
  items: { intakeId: string; intakeVersion: number; recordedAt: string }[]; total: number };
export type ListingSearchCommand = { search: PersonalListingSearch; auditEventIds: string[]; receiptId: string; replayed: boolean };
export type SearchResult<T> = { ok: true; value: T } | { ok: false; superseded: true }
  | { ok: false; message: string; denied?: boolean };
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const text = (x: unknown): x is string => typeof x === "string" && x.trim().length > 0;
const uuid = (x: unknown): x is string => typeof x === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
const revision = (x: unknown): x is number => typeof x === "number" && Number.isInteger(x) && x > 0;
const stamp = (x: unknown): x is string => typeof x === "string" && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(x) && Number.isFinite(Date.parse(x));
const numberOrNull = (x: unknown) => x === null || typeof x === "number" && Number.isFinite(x) && x >= 0;
export function isListingQuery(x: unknown, zone: string): x is ListingQuery {
  return object(x) && x.kind === "listing-search.v1" && x.heatZoneId === zone
    && numberOrNull(x.minAreaPing) && numberOrNull(x.maxAreaPing) && numberOrNull(x.maxRentPerMonth)
    && (x.minAreaPing === null || x.maxAreaPing === null || (x.minAreaPing as number) <= (x.maxAreaPing as number))
    && typeof x.groundFloorOnly === "boolean";
}
export function isPersonalListingSearch(x: unknown, zone: string): x is PersonalListingSearch {
  return object(x) && uuid(x.saved_view_id) && uuid(x.owner_subject_id) && uuid(x.tenant_id)
    && x.visibility === "PRIVATE" && text(x.name) && revision(x.version) && stamp(x.created_at)
    && (x.deleted_at === null || x.deleted_at === undefined || stamp(x.deleted_at)) && isListingQuery(x.query, zone);
}
export const sameListingQuery = (a: ListingQuery, b: ListingQuery) =>
  a.kind === b.kind && a.heatZoneId === b.heatZoneId && a.minAreaPing === b.minAreaPing
    && a.maxAreaPing === b.maxAreaPing && a.maxRentPerMonth === b.maxRentPerMonth && a.groundFloorOnly === b.groundFloorOnly;

export function createListingSearchApi(zone: string, headers: Record<string, string>, guard: () => Promise<boolean>) {
  const base = "/api/v1/operator/network-listings/searches";
  async function request<T>(path: string, valid: (x: unknown) => x is T,
    command?: { method: string; body: unknown; key: string }): Promise<SearchResult<T>> {
    const current = async () => { try { return await guard(); } catch { return false; } };
    const stale = (): SearchResult<T> => ({ ok: false, superseded: true });
    const cid = crypto.randomUUID();
    try {
      if (!await current()) return stale();
      const response = await fetch(base + path, { method: command?.method ?? "GET", cache: "no-store",
        headers: { ...headers, "Content-Type": "application/json", "X-Correlation-Id": cid,
          ...(command ? { "Idempotency-Key": command.key } : {}) },
        ...(command ? { body: JSON.stringify(command.body) } : {}) });
      if (!await current()) return stale();
      const value: unknown = await response.json();
      if (!await current()) return stale();
      if (!response.ok) return { ok: false, denied: [401, 403, 404].includes(response.status),
        message: `搜尋條件 ${response.status}；${response.status === 409 ? "版本已變更，請重新讀取後再確認。" : "請保留輸入後重新讀取或重試。"}追蹤：${cid}` };
      if (payloadContainsSeedData(value) || !valid(value)) throw new Error("invalid-search-response");
      return { ok: true, value };
    } catch {
      if (!await current()) return stale();
      return { ok: false, message: `回應尚未核驗；請保留相同輸入與命令識別重試，或重新讀取。追蹤：${cid}` };
    }
  }
  return {
    list: () => request("?" + new URLSearchParams({ heatZoneId: zone }), (x): x is { items: PersonalListingSearch[] } =>
      object(x) && x.heatZoneId === zone && Array.isArray(x.items) && x.items.every(row => isPersonalListingSearch(row, zone) && !row.deleted_at)
        && new Set(x.items.map(row => row.saved_view_id)).size === x.items.length),
    get: (id: string) => request("/" + encodeURIComponent(id), (x): x is PersonalListingSearch =>
      isPersonalListingSearch(x, zone) && x.saved_view_id === id && !x.deleted_at),
    results: (id: string) => request("/" + encodeURIComponent(id) + "/results", (x): x is ListingSearchMatches =>
      object(x) && isPersonalListingSearch(x.search, zone) && x.search.saved_view_id === id && !x.search.deleted_at
        && Array.isArray(x.items) && x.items.every(row => object(row) && uuid(row.intakeId) && revision(row.intakeVersion) && stamp(row.recordedAt))
        && x.total === x.items.length && new Set(x.items.map(row => row.intakeId)).size === x.items.length),
    command: (operation: "create" | "update" | "delete", input: { name?: string; query?: ListingQuery; expectedVersion?: number }, key: string, id?: string) =>
      request(id ? "/" + encodeURIComponent(id) : "", (x): x is ListingSearchCommand => object(x)
        && isPersonalListingSearch(x.search, zone) && (!id || x.search.saved_view_id === id)
        && x.search.version === (operation === "create" ? 1 : (input.expectedVersion ?? 0) + 1)
        && (operation === "delete" ? stamp(x.search.deleted_at) : !x.search.deleted_at && x.search.name === input.name
          && !!input.query && sameListingQuery(x.search.query, input.query))
        && Array.isArray(x.auditEventIds) && x.auditEventIds.length > 0 && x.auditEventIds.every(uuid)
        && text(x.receiptId) && typeof x.replayed === "boolean",
      { method: operation === "create" ? "POST" : operation === "update" ? "PUT" : "DELETE", body: input, key }),
  };
}

export type ListingSearchApi = ReturnType<typeof createListingSearchApi>;
