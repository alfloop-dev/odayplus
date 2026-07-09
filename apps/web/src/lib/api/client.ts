import { createOdpApiClient, type OdpApiClient } from "@oday-plus/openapi-client";

/**
 * Resolve the backend client for server components. Returns `null` when no
 * API base URL is configured (`ODP_API_BASE_URL` /
 * `NEXT_PUBLIC_ODP_API_BASE_URL`), so callers fall back to bundled fixtures
 * and the product still renders without a backend.
 */
export function getServerApiClient(): OdpApiClient | null {
  return createOdpApiClient();
}
