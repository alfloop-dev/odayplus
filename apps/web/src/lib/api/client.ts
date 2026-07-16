import { createOdpApiClient, type OdpApiClient } from "@oday-plus/openapi-client";
import { headers } from "next/headers";

/**
 * Resolve the backend client for server components. Returns `null` when no
 * API base URL is configured (`ODP_API_BASE_URL` /
 * `NEXT_PUBLIC_ODP_API_BASE_URL`), so callers fall back to bundled fixtures
 * and the product still renders without a backend.
 */
export async function getServerApiClient(): Promise<OdpApiClient | null> {
  const defaultHeaders: Record<string, string> = {};
  try {
    const requestHeaders = await headers();

    const subject = requestHeaders.get("x-subject-id");
    const roles = requestHeaders.get("x-roles");
    const tenant = requestHeaders.get("x-tenant-id");
    const correlation = requestHeaders.get("x-correlation-id");

    if (subject) defaultHeaders["x-subject-id"] = subject;
    if (roles) defaultHeaders["x-roles"] = roles;
    if (tenant) defaultHeaders["x-tenant-id"] = tenant;
    if (correlation) defaultHeaders["x-correlation-id"] = correlation;
  } catch (e) {
    // next/headers might throw if called outside request context (e.g. static generation)
  }
  return createOdpApiClient({ defaultHeaders });
}
