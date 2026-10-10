import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { isProductionWebRuntime, verifyCsrfOrigin } from "../../../lib/auth/runtime";
import { buildUpstreamHeaders } from "../../../lib/auth/proxy";
import { resolveGoogleMetadataIdentityToken } from "../../../lib/auth/cloudRunIdentity";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SAFE_ERRORS: Record<string, number> = {
  INVITATION_UNAVAILABLE: 409,
  INVITATION_ACCOUNT_EXISTS: 409,
  INVITATION_ACCOUNT_INPUT_INVALID: 422,
  INVITATION_INPUT_INVALID: 422,
  INVITATION_PASSWORD_REJECTED: 422,
  INVITATION_PRESET_INVALID: 422,
  INVITATION_RATE_LIMITED: 429,
  IDENTITY_PERSISTENCE_UNAVAILABLE: 503,
};

function failure(status: number, code: string): NextResponse {
  return NextResponse.json({ error: { code } }, {
    status, headers: { "cache-control": "no-store" },
  });
}

async function boundedJson(body: ReadableStream<Uint8Array> | null): Promise<unknown> {
  if (!body) throw new Error("missing body");
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 8192) throw new Error("body limit");
      chunks.push(value);
    }
    const buffer = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      buffer.set(chunk, offset);
      offset += chunk.length;
    }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer));
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/** No session is created/rotated/read here; capability acceptance is not login.
 * Uses the canonical Cloud Run transport and header builder, but does not send
 * any cookie, user bearer or caller identity header upstream. The API consumes
 * the admin-issued capability and writes the single authoritative identity.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!request.headers.get("origin") || !verifyCsrfOrigin(request)) {
    return failure(403, "CSRF_VERIFICATION_FAILED");
  }
  // Capabilities/passwords must never travel in URLs, including query strings.
  if (request.nextUrl.search) return failure(422, "INVITATION_INPUT_INVALID");
  if (request.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") {
    return failure(415, "INVITATION_JSON_REQUIRED");
  }
  let payload: Record<string, unknown>;
  try {
    const parsed = await boundedJson(request.body);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid");
    payload = parsed as Record<string, unknown>;
    const required = ["invitation_id", "token", "username", "password"];
    if (Object.keys(payload).some((key) => ![...required, "display_name"].includes(key)) ||
        required.some((key) => typeof payload[key] !== "string") ||
        !UUID.test(payload.invitation_id as string) ||
        !/^[A-Za-z0-9_-]{43}$/.test(payload.token as string) ||
        !/^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(payload.username as string) ||
        !(payload.password as string).length || (payload.password as string).length > 1024 ||
        (payload.display_name !== undefined && (typeof payload.display_name !== "string" || payload.display_name.length > 255))) {
      throw new Error("invalid");
    }
  } catch {
    return failure(422, "INVITATION_INPUT_INVALID");
  }

  let target: URL;
  let transport: string | null = null;
  try {
    const configured = process.env.ODP_API_BASE_URL;
    if (!configured) throw new Error("unconfigured");
    const base = new URL(configured);
    if (base.username || base.password || base.search || base.hash ||
        base.origin === request.nextUrl.origin ||
        (isProductionWebRuntime() && base.protocol !== "https:")) throw new Error("invalid");
    target = new URL("/api/v1/auth/invitations/accept", base.origin);
    if (isProductionWebRuntime()) {
      const audience = process.env.ODP_API_SERVICE_AUDIENCE?.trim();
      if (!audience) throw new Error("unconfigured");
      transport = await resolveGoogleMetadataIdentityToken(audience);
      if (!transport) throw new Error("unavailable");
    }
  } catch {
    return failure(503, "WEB_AUTH_UNAVAILABLE");
  }

  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers: buildUpstreamHeaders({
        requestHeaders: new Headers({ "content-type": "application/json" }),
        serviceIdentityToken: transport,
      }),
      body: JSON.stringify(payload),
      redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(10_000),
    });
    const result = await boundedJson(upstream.body) as Record<string, unknown> | null;
    if (upstream.status === 201 && result?.status === "accepted") {
      const receipt: Record<string, string> = { status: "accepted" };
      for (const key of ["invitation_id", "account_id", "tenant_id", "audit_event_id"]) {
        if (typeof result[key] !== "string" || !UUID.test(result[key] as string)) throw new Error("invalid receipt");
        receipt[key] = result[key] as string;
      }
      if (receipt.invitation_id !== payload.invitation_id) throw new Error("mismatched receipt");
      return NextResponse.json(receipt, { status: 201, headers: { "cache-control": "no-store" } });
    }
    const code = (result?.error as { code?: unknown } | undefined)?.code;
    if (typeof code === "string" && SAFE_ERRORS[code] === upstream.status) {
      return failure(upstream.status, code);
    }
    // No passthrough of arbitrary upstream errors, redirects or body fields.
    return failure(503, "WEB_AUTH_UNAVAILABLE");
  } catch {
    return failure(503, "WEB_AUTH_UNAVAILABLE");
  }
}
