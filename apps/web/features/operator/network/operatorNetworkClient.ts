"use client";

// Shared typed-API transport for the Operator Console network surfaces
// (ODP-OC-R5-011).
//
// Owned layer  : how a network write reaches the backend — always through the
//                typed @oday-plus/openapi-client, always with a correlation ID,
//                never with raw fetch or hardcoded auth headers. Identity comes
//                from the console's active role via operatorSecurityHeaders().
// Not changing : the backend routes, or the panels that still use legacy fetch
//                for read-only snapshots.
// Composes with: intake/intakeClient.ts (assisted intake) and listingsClient.ts
//                (Listing Radar merge), which add their own error vocabularies
//                on top of this transport.
//
// This module deliberately holds only the parts that are NOT specific to one
// surface. Error *copy* and error *codes* belong to the surface, because the
// next action an operator should take differs per surface.

import {
  OdpApiError,
  createOdpApiClient,
  type OdpApiClient,
} from "@oday-plus/openapi-client";
import { operatorSecurityHeaders } from "../operatorSecurityHeaders";
import type { OperatorSecurityContext } from "../operatorSecurityHeaders";

/**
 * Structured, renderable failure. `summary` is the server's own operator-facing
 * copy (zh-TW policy/permission text); we never overwrite it with an invented
 * message, and `correlationId` + `code` are surfaced so an operator can quote
 * them in a governance ticket (design §7 error requirements).
 */
export type OperatorApiError = {
  status: number;
  code: string;
  summary: string;
  nextAction: string;
  correlationId: string | null;
  occurredAt: string;
  retryable: boolean;
};

export type OperatorApiResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: OperatorApiError };

/** Per-status error vocabulary supplied by each surface. */
export type StatusErrorSpec = { code: string; next: string; retryable: boolean };

/**
 * The network UI runs in the browser, where the backend is reached same-origin
 * through the Next rewrite of `/api/v1/:path*` (apps/web/next.config.mjs) — the
 * client's paths already carry that prefix. An explicit
 * NEXT_PUBLIC_ODP_API_BASE_URL still wins when the API is served from another
 * origin. Note `createOdpApiClient` treats an empty baseUrl as "unconfigured"
 * and returns null, so same-origin must be passed as the concrete origin.
 */
export function buildOperatorNetworkClient(
  roleId?: string | null,
  subjectId?: string | null,
  securityContext: OperatorSecurityContext = {},
): OdpApiClient | null {
  if (
    securityContext.authoritative &&
    (
      !roleId?.trim() ||
      !subjectId?.trim() ||
      !securityContext.tenantId?.trim() ||
      !securityContext.systemRoles?.length
    )
  ) {
    return null;
  }
  const configured = process.env.NEXT_PUBLIC_ODP_API_BASE_URL?.trim();
  const baseUrl =
    configured || (typeof window !== "undefined" ? window.location.origin : undefined);
  if (!baseUrl) return null;

  return createOdpApiClient({
    baseUrl,
    defaultHeaders: operatorSecurityHeaders(roleId, subjectId, securityContext),
  });
}

/**
 * Map a thrown error onto the design's error contract: summary, next action,
 * error code, correlation ID and occurred time (design §7). Status drives the
 * next-action copy because the server's `detail` explains *what* was refused
 * but not *what the operator should do about it*.
 */
export function toOperatorApiError(
  error: unknown,
  options: {
    byStatus: Record<number, StatusErrorSpec>;
    fallbackPrefix: string;
    roleDenied: string;
    timeoutSummary: string;
    networkSummary: string;
    transportNextAction: string;
  },
): OperatorApiError {
  const occurredAt = new Date().toISOString();

  if (error instanceof OdpApiError) {
    const mapped = options.byStatus[error.status];
    return {
      status: error.status,
      code: mapped?.code ?? `${options.fallbackPrefix}-HTTP-${error.status}`,
      summary: error.detail ?? (error.status === 403 ? options.roleDenied : error.message),
      nextAction:
        mapped?.next ?? "請稍後重試；若持續發生，請附上 correlation ID 通報平台維運。",
      correlationId: error.correlationId ?? null,
      occurredAt,
      // 5xx is transient from the operator's point of view; 4xx is not.
      retryable: mapped ? mapped.retryable : error.status >= 500,
    };
  }

  // AbortError (client timeout) and network failures never reach the server.
  const isAbort = error instanceof Error && error.name === "AbortError";
  return {
    status: 0,
    code: isAbort ? `${options.fallbackPrefix}-TIMEOUT` : `${options.fallbackPrefix}-NETWORK`,
    summary: isAbort ? options.timeoutSummary : options.networkSummary,
    nextAction: options.transportNextAction,
    correlationId: null,
    occurredAt,
    retryable: true,
  };
}

/** Run a typed client call, converting a throw into a structured result. */
export async function guardCall<T>(
  run: () => Promise<T>,
  mapError: (error: unknown) => OperatorApiError,
): Promise<OperatorApiResult<T>> {
  try {
    return { ok: true, value: await run() };
  } catch (error) {
    return { ok: false, error: mapError(error) };
  }
}

export function newCorrelationId(): string {
  return `corr-${randomToken()}`;
}

export function randomToken(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}
