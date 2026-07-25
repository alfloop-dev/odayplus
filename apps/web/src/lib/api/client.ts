import { createOdpApiClient, type OdpApiClient } from "@oday-plus/openapi-client";
import { cookies, headers } from "next/headers";
import {
  readWebSession,
  webSessionCookieName,
} from "../auth/session";
import {
  allowLegacyTrustedHeaders,
  isProductionWebRuntime,
} from "../auth/runtime";

/**
 * Resolve the backend client for server components. Production requests are
 * authenticated only from the encrypted HttpOnly web session.
 */
export async function getServerApiClient(): Promise<OdpApiClient | null> {
  const defaultHeaders: Record<string, string> = {};
  const production = isProductionWebRuntime();
  try {
    const cookieStore = await cookies();
    const session = await readWebSession(
      cookieStore.get(webSessionCookieName)?.value,
    );
    if (session) {
      defaultHeaders.authorization = `Bearer ${session.accessToken}`;
    } else if (production) {
      return null;
    }

    const requestHeaders = await headers();
    const correlation = requestHeaders.get("x-correlation-id");
    const operatorRole = requestHeaders.get("x-operator-role");
    if (correlation) defaultHeaders["x-correlation-id"] = correlation;
    if (operatorRole) defaultHeaders["x-operator-role"] = operatorRole;

    if (!session && allowLegacyTrustedHeaders()) {
      for (const name of ["x-subject-id", "x-roles", "x-tenant-id"]) {
        const value = requestHeaders.get(name);
        if (value) defaultHeaders[name] = value;
      }
    }
  } catch {
    if (production) return null;
  }

  if (production) {
    const baseUrl = process.env.ODP_API_BASE_URL?.trim();
    if (!baseUrl) return null;
    return createOdpApiClient({ baseUrl, defaultHeaders });
  }
  return createOdpApiClient({ defaultHeaders });
}
