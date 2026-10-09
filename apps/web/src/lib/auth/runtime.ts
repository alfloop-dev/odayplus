import { isProductionMode } from "../runtime/productMode";

export const WEB_SESSION_COOKIE = "__Host-oday_web_session";
export const OIDC_TRANSACTION_COOKIE = "__Host-oday_oidc_transaction";

export const SESSION_COOKIE_MAX_AGE_SECONDS = 8 * 60 * 60;
export const OIDC_TRANSACTION_MAX_AGE_SECONDS = 10 * 60;

export const AUTH_MODE_PLACEHOLDER_VALUES = new Set([
  "changeme",
  "change-me",
  "dummy",
  "example",
  "fixture",
  "mock",
  "placeholder",
  "seed",
  "todo",
]);

export function isProductionWebRuntime(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  return isProductionMode(environment);
}

export function allowLegacyTrustedHeaders(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  if (isProductionWebRuntime(environment)) return false;
  return environment.ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS !== "false";
}

export function safeReturnTo(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/operator";
  }

  if (/[\u0000-\u001f\u007f]/.test(value)) {
    return "/operator";
  }

  try {
    const parsed = new URL(value, "https://oday.plus");
    if (parsed.origin !== "https://oday.plus") return "/operator";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/operator";
  }
}

export function resolveWebBaseUrl(
  requestOrigin: string,
  environment: Record<string, string | undefined> = process.env,
): string {
  const configured = environment.ODP_WEB_BASE_URL?.trim();
  if (isProductionWebRuntime(environment) && !configured) {
    throw new Error("ODP_WEB_BASE_URL is required in production");
  }
  const result = new URL(configured || requestOrigin);

  if (
    isProductionWebRuntime(environment) &&
    result.protocol !== "https:" &&
    result.hostname !== "localhost" &&
    result.hostname !== "127.0.0.1"
  ) {
    throw new Error("ODP_WEB_BASE_URL must use https in production");
  }

  return result.origin;
}

export function normalizeAuthModeValue(value?: string | null): string {
  return (value || "").trim().toLowerCase();
}

export function isConfiguredAuthValue(value?: string | null): boolean {
  const normalized = normalizeAuthModeValue(value);
  return Boolean(normalized && !AUTH_MODE_PLACEHOLDER_VALUES.has(normalized));
}

export function resolveAuthMode(
  environment: Record<string, string | undefined> = process.env,
): "local" | "oidc" {
  const mode = normalizeAuthModeValue(environment.ODP_AUTH_MODE);
  const legacyFlag = normalizeAuthModeValue(environment.ODP_AUTH_OIDC_ENABLED);

  if (legacyFlag && legacyFlag !== "true" && legacyFlag !== "false") {
    throw new Error(
      `ODP_AUTH_OIDC_ENABLED must be 'true' or 'false', got '${legacyFlag}'`,
    );
  }

  if (mode) {
    if (mode !== "local" && mode !== "oidc") {
      throw new Error(`ODP_AUTH_MODE must be 'local' or 'oidc', got '${mode}'`);
    }
    if (legacyFlag) {
      const expectedFlag = mode === "oidc" ? "true" : "false";
      if (legacyFlag !== expectedFlag) {
        throw new Error(
          `ODP_AUTH_MODE=${mode} conflicts with ODP_AUTH_OIDC_ENABLED=${legacyFlag}`,
        );
      }
    }
    return mode;
  }

  if (legacyFlag) {
    return legacyFlag === "true" ? "oidc" : "local";
  }

  if (isConfiguredAuthValue(environment.ODP_WEB_OIDC_ISSUER)) {
    return "oidc";
  }

  return "local";
}

export function isOidcConfigured(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  return (
    isConfiguredAuthValue(environment.ODP_WEB_OIDC_ISSUER) &&
    isConfiguredAuthValue(environment.ODP_WEB_OIDC_CLIENT_ID) &&
    isConfiguredAuthValue(environment.ODP_WEB_OIDC_CLIENT_SECRET)
  );
}

export function isOidcEnabled(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  const mode = resolveAuthMode(environment);
  if (mode === "local") {
    return false;
  }

  if (!isOidcConfigured(environment)) {
    throw new Error(
      "OIDC mode requires complete configuration (ODP_WEB_OIDC_ISSUER, ODP_WEB_OIDC_CLIENT_ID, ODP_WEB_OIDC_CLIENT_SECRET)",
    );
  }

  return true;
}

function extractOrigin(urlOrOrigin: string): string | null {
  try {
    const parsed = new URL(urlOrOrigin);
    return `${parsed.protocol}//${parsed.host}`.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * Cloud Run serves one Web service under several hostnames: the configured
 * public URL (`<service>-<project-number>.<region>.run.app`), the legacy hash
 * URL (`<service>-<hash>-<region-code>.a.run.app`) and tagged revision URLs
 * (`<tag>---<either host>`) used to validate a candidate before traffic moves.
 * A browser on any of them posts same-origin forms, and the session cookie is
 * host-only (`__Host-`), so the sign-in flow must stay on the host it started
 * on. Returns that origin when the request's own Host is one of this service's
 * Cloud Run hostnames; otherwise null (callers fall back to the configured
 * origin).
 *
 * Only the `Host` header is read: Cloud Run passes the hostname the browser
 * used, while `X-Forwarded-Host` is client-controllable and ignored.
 */
export function trustedRequestOrigin(
  request: { headers: Headers },
  environment: Record<string, string | undefined> = process.env,
): string | null {
  const configured = environment.ODP_WEB_BASE_URL?.trim();
  const host = request.headers.get("host")?.trim().toLowerCase();
  if (!configured || !host) return null;

  let configuredHost: string;
  try {
    const parsed = new URL(configured);
    if (parsed.protocol !== "https:") return null;
    configuredHost = parsed.host.toLowerCase();
  } catch {
    return null;
  }
  if (host === configuredHost) return `https://${host}`;

  // Aliases are derived only from a Cloud Run public URL of the form
  // <service>-<project-number>.<region>.run.app; custom domains get none.
  const match = /^([a-z0-9-]+?)-(\d+)\.([a-z0-9-]+)\.run\.app$/.exec(configuredHost);
  if (!match) return null;
  const [, service, projectNumber, region] = match;
  const tag = "(?:[a-z0-9-]+---)?";
  const aliases = [
    new RegExp(`^${tag}${service}-${projectNumber}\\.${region}\\.run\\.app$`),
    new RegExp(`^${tag}${service}-[a-z0-9]+-[a-z]{2,4}\\.a\\.run\\.app$`),
  ];
  return aliases.some((pattern) => pattern.test(host)) ? `https://${host}` : null;
}

/** Base URL for same-host redirects in the browser sign-in flow. */
export function resolveRequestWebBaseUrl(
  request: { headers: Headers; nextUrl: { origin: string } },
  environment: Record<string, string | undefined> = process.env,
): string {
  return (
    trustedRequestOrigin(request, environment) ??
    resolveWebBaseUrl(request.nextUrl.origin, environment)
  );
}

export function verifyCsrfOrigin(
  request: { headers: Headers; nextUrl?: { origin?: string; href?: string }; url?: string },
  environment: Record<string, string | undefined> = process.env,
): boolean {
  const originHeader =
    request.headers.get("origin") || request.headers.get("Origin");
  const refererHeader =
    request.headers.get("referer") || request.headers.get("Referer");

  let targetOrigin: string | null = null;
  if (originHeader) {
    targetOrigin = extractOrigin(originHeader);
  } else if (refererHeader) {
    targetOrigin = extractOrigin(refererHeader);
  }

  if (!targetOrigin) {
    return false;
  }

  // When the Host is one of this service's Cloud Run hostnames, the browser
  // addressed exactly that origin, so a same-origin form must carry it. Any
  // other origin — including the configured public URL or a sibling alias —
  // is a different site for the host-only session cookie and is refused.
  const selfOrigin = trustedRequestOrigin(request, environment);
  if (selfOrigin) return targetOrigin === selfOrigin;

  const allowedOrigins = new Set<string>();

  if (request.nextUrl?.origin) {
    const orig = extractOrigin(request.nextUrl.origin);
    if (orig) allowedOrigins.add(orig);
  }
  if (request.nextUrl?.href) {
    const orig = extractOrigin(request.nextUrl.href);
    if (orig) allowedOrigins.add(orig);
  }
  if (request.url) {
    const orig = extractOrigin(request.url);
    if (orig) allowedOrigins.add(orig);
  }

  try {
    const reqOrigin =
      request.nextUrl?.origin ||
      (request.url ? new URL(request.url).origin : "https://oday.plus");
    const canonical = resolveWebBaseUrl(reqOrigin, environment);
    const orig = extractOrigin(canonical);
    if (orig) allowedOrigins.add(orig);
  } catch {
    // If canonical URL does not resolve in test mode
  }

  return allowedOrigins.has(targetOrigin);
}
