import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { readWebSession, sealWebSessionReference, webSessionCookieName, webSessionCookieOptions } from "../../../lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The same durable identity.sessions lookup used by the API BFF supplies this
// context. Display persona, storage and the OIDC display subject are unrelated.
export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = await readWebSession(request.cookies.get(webSessionCookieName)?.value).catch(() => null);
  if (!session || typeof session.accountId !== "string" || !session.accountId.trim()
    || typeof session.tenantId !== "string" || !session.tenantId.trim()) {
    return NextResponse.json({ error: { code: "OPERATOR_CONTEXT_REQUIRED", summary: "A current actor and tenant are required." } },
      { status: 401, headers: { "cache-control": "no-store" } });
  }
  const response = NextResponse.json({ actorSubjectId: session.accountId, tenantId: session.tenantId, expiresAt: session.expiresAt },
    { headers: { "cache-control": "no-store" } });
  if (session.legacyUpgrade) response.cookies.set(webSessionCookieName, await sealWebSessionReference(session),
    { ...webSessionCookieOptions, maxAge: Math.max(1, session.expiresAt - Math.floor(Date.now() / 1000)) });
  return response;
}
