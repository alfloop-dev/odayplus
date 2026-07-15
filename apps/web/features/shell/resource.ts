/**
 * Single-resource API binding for the shell (ODP-PGAP-SHELL-001).
 *
 * `loadApiBinding` in src/lib/api/binding.ts classifies *lists* and treats a
 * cold store as `empty` → fixture. Every shell endpoint returns one object
 * (home, tasks envelope, inbox, admin view…), and an empty object is not a
 * fixture signal — a shell with zero tasks is a real, healthy state that must
 * render as "no tasks", not as sample data.
 *
 * This binding therefore classifies an object endpoint and, unlike the list
 * binding, distinguishes authorization failures from outages: a 403 is a
 * product state the shell must render honestly (you cannot see this), not an
 * error to retry.
 */
import { OdpApiError, type OdpApiClient } from "@oday-plus/openapi-client";

export type ResourceState = "ready" | "forbidden" | "unauthorized" | "error" | "unconfigured";

export type ApiResource<T> = {
  state: ResourceState;
  data: T | null;
  source: "api" | "none";
  /** HTTP status when the API answered; absent when it never did. */
  status?: number;
  /** Operator-facing refusal text from the server, when it supplied one. */
  detail?: string;
  error?: string;
  correlationId?: string;
  baseUrl?: string;
  fetchedAt: string;
};

/**
 * Fetch one resource and classify the outcome. Never throws — every failure
 * becomes a state the caller renders. `data` is non-null only when `ready`.
 */
export async function loadApiResource<T>(options: {
  client: OdpApiClient | null;
  fetcher: (client: OdpApiClient) => Promise<T>;
}): Promise<ApiResource<T>> {
  const fetchedAt = new Date().toISOString();
  const { client, fetcher } = options;
  if (!client) {
    return { state: "unconfigured", data: null, source: "none", fetchedAt };
  }
  try {
    const data = await fetcher(client);
    return { state: "ready", data, source: "api", baseUrl: client.baseUrl, fetchedAt };
  } catch (error) {
    if (error instanceof OdpApiError) {
      const state: ResourceState =
        error.status === 403 ? "forbidden" : error.status === 401 ? "unauthorized" : "error";
      return {
        state,
        data: null,
        source: "none",
        status: error.status,
        detail: error.detail,
        correlationId: error.correlationId,
        error: error.message,
        baseUrl: client.baseUrl,
        fetchedAt,
      };
    }
    return {
      state: "error",
      data: null,
      source: "none",
      error: error instanceof Error ? error.message : String(error),
      baseUrl: client.baseUrl,
      fetchedAt,
    };
  }
}
