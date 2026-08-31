import { base64UrlDecode, base64UrlEncode } from "./crypto";
import { isProductionWebRuntime } from "./runtime";

const encoder = new TextEncoder();

// The API trust boundary resolves the plain Secret Manager-backed
// ODP_IDENTITY_TOKEN_SIGNING_KEY as this single local key. Keep the JOSE key
// id explicit here so a Web-issued token can never drift from that resolver.
export const LOCAL_IDENTITY_KEY_ID = "local-default";
export const LOCAL_IDENTITY_ISSUER = "urn:odp:identity:local";

export function localJwtExpiresAt(token: string): number | null {
  const segments = token.split(".");
  if (segments.length !== 3 || !segments[1]) return null;
  try {
    const claims = JSON.parse(
      new TextDecoder().decode(base64UrlDecode(segments[1])),
    ) as Record<string, unknown>;
    return typeof claims.exp === "number" && Number.isSafeInteger(claims.exp)
      ? claims.exp
      : null;
  } catch {
    return null;
  }
}

export function localJwtNeedsRefresh(
  token: string,
  nowSeconds = Math.floor(Date.now() / 1000),
  refreshWindowSeconds = 30,
): boolean {
  const expiresAt = localJwtExpiresAt(token);
  const boundedWindow = Math.min(Math.max(refreshWindowSeconds, 0), 60);
  return expiresAt === null || expiresAt <= nowSeconds + boundedWindow;
}

export async function mintLocalJwt(options: {
  subject: string;
  sid: string;
  tenantId?: string;
  issuer?: string;
  audience?: string;
  expiresInSeconds?: number;
  nowSeconds?: number;
  signingSecret?: string;
  environment?: Record<string, string | undefined>;
}): Promise<string> {
  const environment = options.environment ?? process.env;
  const now = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const requestedTtl = options.expiresInSeconds ?? 120;
  const ttl = Math.min(Math.max(requestedTtl, 1), 300);
  const header = {
    alg: "HS256",
    typ: "JWT",
    kid: LOCAL_IDENTITY_KEY_ID,
  };
  const claims = {
    iss:
      options.issuer ||
      environment.ODP_AUTH_LOCAL_ISSUER ||
      LOCAL_IDENTITY_ISSUER,
    aud: options.audience || environment.ODP_AUTH_AUDIENCES || "oday-plus",
    sub: options.subject,
    sid: options.sid,
    tenant_id: options.tenantId || "default",
    iat: now,
    exp: now + ttl,
  };

  const headerB64 = base64UrlEncode(encoder.encode(JSON.stringify(header)));
  const payloadB64 = base64UrlEncode(encoder.encode(JSON.stringify(claims)));
  const signingInput = `${headerB64}.${payloadB64}`;

  const configuredSecret =
    options.signingSecret ?? environment.ODP_IDENTITY_TOKEN_SIGNING_KEY;
  if (isProductionWebRuntime(environment) && !configuredSecret) {
    throw new Error("ODP_IDENTITY_TOKEN_SIGNING_KEY is required in production");
  }
  const rawSecret =
    configuredSecret ??
    environment.ODP_WEB_SESSION_SECRET ??
    "default-secret-with-at-least-32-characters-key";

  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(rawSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(signingInput),
  );
  const signatureB64 = base64UrlEncode(new Uint8Array(signature));

  return `${signingInput}.${signatureB64}`;
}
