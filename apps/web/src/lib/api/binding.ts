import type { OdpApiClient } from "@oday-plus/openapi-client";

/**
 * Outcome of attempting to bind a workspace region to live backend data.
 *
 * - `ready`        — the API responded with one or more rows (render live).
 * - `empty`        — the API responded but the store is cold (fixture fallback).
 * - `error`        — the API was unreachable or returned a non-2xx (fallback).
 * - `unconfigured` — no API base URL is set (fixture-only build).
 */
export type BindingState = "ready" | "empty" | "error" | "unconfigured";

export type ApiBinding<T> = {
  state: BindingState;
  /** Live rows from the API; empty for every non-`ready` state. */
  items: T[];
  /** `api` once the backend has actually served the rendered rows. */
  source: "api" | "fixture";
  error?: string;
  baseUrl?: string;
  fetchedAt: string;
};

/**
 * Fetch a list from the backend and classify the result. Never throws —
 * a cold store, an unreachable API, or a missing base URL all degrade to a
 * `fixture` source so the workspace keeps rendering its documented fallback.
 */
export async function loadApiBinding<T>(options: {
  client: OdpApiClient | null;
  fetcher: (client: OdpApiClient) => Promise<T[]>;
}): Promise<ApiBinding<T>> {
  const fetchedAt = new Date().toISOString();
  const { client, fetcher } = options;
  if (!client) {
    return { state: "unconfigured", items: [], source: "fixture", fetchedAt };
  }
  try {
    const items = await fetcher(client);
    if (items.length === 0) {
      return { state: "empty", items: [], source: "fixture", baseUrl: client.baseUrl, fetchedAt };
    }
    return { state: "ready", items, source: "api", baseUrl: client.baseUrl, fetchedAt };
  } catch (error) {
    return {
      state: "error",
      items: [],
      source: "fixture",
      error: error instanceof Error ? error.message : String(error),
      baseUrl: client.baseUrl,
      fetchedAt,
    };
  }
}
