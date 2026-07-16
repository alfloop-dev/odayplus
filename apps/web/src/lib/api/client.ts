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
    const operatorRole = requestHeaders.get("x-operator-role");

    if (subject) defaultHeaders["x-subject-id"] = subject;
    if (roles) defaultHeaders["x-roles"] = roles;
    if (tenant) defaultHeaders["x-tenant-id"] = tenant;
    if (correlation) defaultHeaders["x-correlation-id"] = correlation;
    // The Operator Console role must be forwarded too (ODP-PGAP-SHELL-001).
    // The shell's reads are role-scoped, and the browser sends this header on
    // writes; dropping it here made a read resolve to the principal's default
    // role while the matching write applied to the selected one — so switching
    // role appeared to do nothing, and a preference saved as one role was read
    // back as another. Forwarding is safe: the server re-checks the requested
    // role against the principal's own roles and denies at operator.role_scope,
    // so this cannot be used to widen access.
    if (operatorRole) defaultHeaders["x-operator-role"] = operatorRole;
  } catch (e) {
    // next/headers might throw if called outside request context (e.g. static generation)
  }
  return createOdpApiClient({ defaultHeaders });
}
