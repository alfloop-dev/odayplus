import { createHash } from "node:crypto";
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

const ACCEPT_SCRIPT = `
const form = document.getElementById("accept-invitation");
const result = document.getElementById("result");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  result.textContent = "接受邀請中…";
  try {
    const data = new FormData(form);
    const payload = Object.fromEntries(data.entries());
    const response = await fetch("/auth/invitations", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(payload), cache: "no-store", redirect: "error",
    });
    if (response.status === 201) {
      form.reset();
      form.hidden = true;
      result.textContent = "帳號已建立。請前往登入；營運邀請首次登入需再次改密碼。";
    } else {
      result.textContent = "無法接受邀請。請檢查憑證、登入名稱與密碼政策，或聯絡邀請管理員確認到期與撤銷狀態。";
    }
  } catch {
    result.textContent = "服務暫時無法連線；請確認邀請是否已接受後再嘗試。";
  } finally {
    button.disabled = false;
  }
});
`;

/** Manual private capability entry: nothing in URLs or browser storage. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  if (request.nextUrl.search) return failure(422, "INVITATION_INPUT_INVALID");
  const hash = createHash("sha256").update(ACCEPT_SCRIPT).digest("base64");
  return new NextResponse(`<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>接受帳號邀請</title><h1>接受帳號邀請</h1>
<p>使用管理員私下交付的憑證；請勿將 token 放入網址。密碼至少 12 字元，不得包含登入名稱或 email。</p>
<form id="accept-invitation" autocomplete="off">
<label>邀請 ID <input name="invitation_id" required maxlength="36"></label><br>
<label>邀請 token <input name="token" type="password" required maxlength="43" autocomplete="off"></label><br>
<label>登入名稱 <input name="username" required minlength="3" maxlength="64"></label><br>
<label>密碼 <input name="password" type="password" required minlength="12" maxlength="1024" autocomplete="new-password"></label><br>
<button type="submit">接受邀請並建立帳號</button></form>
<p id="result" role="status"></p><a href="/login">前往登入</a>
<script>${ACCEPT_SCRIPT}</script></html>`, { headers: {
    "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
    "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
    "content-security-policy": `default-src 'none'; script-src 'sha256-${hash}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`,
  } });
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
      if (receipt.invitation_id.toLowerCase() !== (payload.invitation_id as string).toLowerCase()) throw new Error("mismatched receipt");
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
