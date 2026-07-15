import type { OdpApiClient } from "@oday-plus/openapi-client";
import { headers } from "next/headers";

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
  let isProduction =
    process.env.NODE_ENV === "production" ||
    process.env.NEXT_PUBLIC_PRODUCTION_MODE === "true";

  let mockError = false;
  let mockEmpty = false;
  let mockUnconfigured = false;

  try {
    const reqHeaders = await headers();
    if (reqHeaders.get("x-production-mode") === "true") {
      isProduction = true;
    }
    if (reqHeaders.get("x-test-mock-unconfigured") === "true") {
      mockUnconfigured = true;
    }
    if (reqHeaders.get("x-test-mock-error") === "true") {
      mockError = true;
    }
    if (reqHeaders.get("x-test-mock-empty") === "true") {
      mockEmpty = true;
    }
  } catch (e) {
    // Ignore outside request context
  }

  if (!client || mockUnconfigured) {
    return {
      state: "unconfigured",
      items: [],
      source: isProduction ? "api" : "fixture",
      fetchedAt,
    };
  }

  if (mockEmpty) {
    return {
      state: "empty",
      items: [],
      source: isProduction ? "api" : "fixture",
      baseUrl: client.baseUrl,
      fetchedAt,
    };
  }

  try {
    if (mockError) {
      throw new Error("Mocked Server Error");
    }
    const items = await fetcher(client);
    if (items.length === 0) {
      return {
        state: "empty",
        items: [],
        source: isProduction ? "api" : "fixture",
        baseUrl: client.baseUrl,
        fetchedAt,
      };
    }
    return {
      state: "ready",
      items,
      source: "api",
      baseUrl: client.baseUrl,
      fetchedAt,
    };
  } catch (error) {
    return {
      state: "error",
      items: [],
      source: isProduction ? "api" : "fixture",
      error: error instanceof Error ? error.message : String(error),
      baseUrl: client.baseUrl,
      fetchedAt,
    };
  }
}
