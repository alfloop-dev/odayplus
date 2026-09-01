import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
} from "./session";
import {
  allowLegacyTrustedHeaders,
  isProductionWebRuntime,
} from "./runtime";
import {
  resolveGoogleMetadataIdentityToken,
  type ServiceIdentityTokenResolver,
} from "./cloudRunIdentity";

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "accept-language",
  "content-type",
  "if-match",
  "idempotency-key",
  "x-correlation-id",
  "x-operator-role",
] as const;

const LEGACY_IDENTITY_HEADERS = [
  "x-subject-id",
  "x-tenant-id",
  "x-roles",
] as const;

const FORWARDED_RESPONSE_HEADERS = [
  "content-disposition",
  "content-type",
  "etag",
  "idempotency-replayed",
  "last-modified",
  "location",
  "retry-after",
  "www-authenticate",
  "x-correlation-id",
] as const;

const UPSTREAM_TIMEOUT_MS = 10_000;

export function buildUpstreamHeaders(options: {
  requestHeaders: Headers;
  accessToken?: string | null;
  allowLegacyIdentity?: boolean;
  serviceIdentityToken?: string | null;
}): Headers {
  const result = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = options.requestHeaders.get(name);
    if (value) result.set(name, value);
  }

  if (options.allowLegacyIdentity) {
    for (const name of LEGACY_IDENTITY_HEADERS) {
      const value = options.requestHeaders.get(name);
      if (value) result.set(name, value);
    }
  }

  if (options.accessToken) {
    result.set("authorization", `Bearer ${options.accessToken}`);
  }
  if (options.serviceIdentityToken) {
    result.set(
      "x-serverless-authorization",
      `Bearer ${options.serviceIdentityToken}`,
    );
  }
  return result;
}

function authError(
  status: number,
  code: string,
  summary: string,
  retryable = false,
): NextResponse {
  return NextResponse.json(
    {
      error: {
        code,
        summary,
        retryable,
      },
    },
    {
      status,
      headers: {
        "cache-control": "no-store",
      },
    },
  );
}

function resolveApiBaseUrl(environment: NodeJS.ProcessEnv): URL {
  const configured = environment.ODP_API_BASE_URL?.trim();
  if (!configured && isProductionWebRuntime(environment)) {
    throw new Error("ODP_API_BASE_URL is required in production");
  }
  return new URL(configured || "http://127.0.0.1:8099");
}

function upstreamUrl(baseUrl: URL, request: NextRequest, path: string): URL {
  if (!path.startsWith("/api/v1/") && path !== "/api/v1" && !path.startsWith("/avm/") && path !== "/avm") {
    throw new Error("Unsupported BFF upstream path");
  }
  const target = new URL(path, `${baseUrl.origin}/`);
  target.search = request.nextUrl.search;
  if (target.origin === request.nextUrl.origin) {
    throw new Error("ODP_API_BASE_URL must not point to the web BFF itself");
  }
  return target;
}

export async function proxyApiRequest(
  request: NextRequest,
  path: string,
  environment: NodeJS.ProcessEnv = process.env,
  dependencies: {
    resolveServiceIdentityToken?: ServiceIdentityTokenResolver;
    upstreamTimeoutMs?: number;
  } = {},
): Promise<Response> {
  const production = isProductionWebRuntime(environment);
  let session = null;
  try {
    session = await readWebSession(
      request.cookies.get(webSessionCookieName)?.value,
      { environment },
    );
  } catch {
    if (production) {
      return authError(
        503,
        "WEB_AUTH_NOT_CONFIGURED",
        "Web authentication is not configured.",
      );
    }
  }

  if (production && !session) {
    return authError(
      401,
      "WEB_SESSION_REQUIRED",
      "A valid web session is required.",
    );
  }

  let baseUrl: URL;
  try {
    baseUrl = resolveApiBaseUrl(environment);
  } catch {
    return authError(
      503,
      "WEB_API_NOT_CONFIGURED",
      "The upstream API is not configured.",
    );
  }

  let serviceIdentityToken: string | null = null;
  if (production) {
    const audience = environment.ODP_API_SERVICE_AUDIENCE?.trim();
    if (!audience) {
      return authError(
        503,
        "WEB_SERVICE_IDENTITY_NOT_CONFIGURED",
        "The Web BFF service identity audience is not configured.",
      );
    }
    try {
      serviceIdentityToken = await (
        dependencies.resolveServiceIdentityToken ??
        resolveGoogleMetadataIdentityToken
      )(audience);
    } catch {
      return authError(
        503,
        "WEB_SERVICE_IDENTITY_UNAVAILABLE",
        "The Web BFF could not obtain its service identity.",
        true,
      );
    }
  }

  const requestHeaders = buildUpstreamHeaders({
    requestHeaders: request.headers,
    accessToken: session?.accessToken,
    allowLegacyIdentity:
      !production && !session && allowLegacyTrustedHeaders(environment),
    serviceIdentityToken,
  });
  const upstreamTimeoutMs =
    dependencies.upstreamTimeoutMs !== undefined &&
    Number.isFinite(dependencies.upstreamTimeoutMs) &&
    dependencies.upstreamTimeoutMs > 0
      ? dependencies.upstreamTimeoutMs
      : UPSTREAM_TIMEOUT_MS;
  const upstreamSignal = AbortSignal.timeout(upstreamTimeoutMs);

  try {
    const response = await fetch(upstreamUrl(baseUrl, request, path), {
      method: request.method,
      headers: requestHeaders,
      body:
        request.method === "GET" || request.method === "HEAD"
          ? undefined
          : await request.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
      signal: upstreamSignal,
    });
    const responseHeaders = new Headers({
      "cache-control": "no-store",
    });
    for (const name of FORWARDED_RESPONSE_HEADERS) {
      const value = response.headers.get(name);
      if (!value) continue;
      if (name === "location") {
        const location = new URL(value, baseUrl);
        responseHeaders.set(
          name,
          location.origin === baseUrl.origin
            ? `${location.pathname}${location.search}${location.hash}`
            : value,
        );
      } else {
        responseHeaders.set(name, value);
      }
    }
    const proxiedResponse = new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
    if (session?.legacyUpgrade) {
      proxiedResponse.cookies.set(
        webSessionCookieName,
        await sealWebSessionReference(session),
        {
          ...webSessionCookieOptions,
          maxAge: Math.max(
            1,
            session.expiresAt - Math.floor(Date.now() / 1000),
          ),
        },
      );
    }
    return proxiedResponse;
  } catch {
    if (upstreamSignal.aborted) {
      return authError(
        504,
        "WEB_API_UPSTREAM_TIMEOUT",
        "The upstream API timed out.",
        true,
      );
    }
    return authError(
      502,
      "WEB_API_UPSTREAM_UNAVAILABLE",
      "The upstream API is unavailable.",
    );
  }
}
