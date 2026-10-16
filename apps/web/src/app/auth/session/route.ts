import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { getDefaultIdentityStore, PostgresIdentityStore } from "../../../lib/auth/identityStore";
import { getDefaultSessionStore, PostgresSessionStore } from "../../../lib/auth/sessionStore";
import {
  readWebSession,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const contextHeaders = {
  "cache-control": "private, no-store",
  vary: "Cookie",
};

function contextUnavailable(): NextResponse {
  return NextResponse.json(
    { error: { code: "OPERATOR_CONTEXT_UNAVAILABLE", summary: "Authenticated context is unavailable." } },
    { status: 503, headers: contextHeaders },
  );
}

function validIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 256 &&
    value === value.trim() && !/[\u0000-\u0020\u007f]/.test(value);
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const approvalContext = request.nextUrl.searchParams.get("view") === "operator-approval-context";
  let session;
  try {
    session = await readWebSession(request.cookies.get(webSessionCookieName)?.value);
  } catch {
    // Preserve the original default GET's public refusal contract.
    if (approvalContext) return contextUnavailable();
    session = null;
  }
  if (!session) {
    return NextResponse.json(
      {
        error: {
          code: "WEB_SESSION_REQUIRED",
          summary: "A valid web session is required.",
        },
      },
      { status: 401, headers: { "cache-control": "no-store" } },
    );
  }

  let body: Record<string, string | number | undefined> = {
    subject: session.subject, expiresAt: session.expiresAt,
  };
  if (approvalContext) {
    try {
      const sessions = getDefaultSessionStore();
      const identities = getDefaultIdentityStore();
      // This representation has no decoder-only or dev-memory authority path.
      // Do not infer durable ownership from optional WebSession/cookie fields.
      if (!(sessions instanceof PostgresSessionStore) ||
          !(identities instanceof PostgresIdentityStore) || !validIdentifier(session.sid)) {
        return contextUnavailable();
      }
      const record = await sessions.validateSession(session.sid);
      if (!record) {
        return NextResponse.json(
          { error: { code: "WEB_SESSION_REQUIRED", summary: "A valid web session is required." } },
          { status: 401, headers: contextHeaders },
        );
      }
      const now = Date.now();
      const absolute = record.absoluteExpiresAt.getTime();
      const idle = record.idleExpiresAt.getTime();
      if (record.sessionId !== session.sid || record.revokedAt !== null ||
          !Number.isFinite(absolute) || !Number.isFinite(idle) || absolute <= now || idle <= now ||
          !validIdentifier(record.accountId) || !validIdentifier(record.tenantId) ||
          !validIdentifier(record.subject) || !record.accessToken ||
          !["local_password", "oidc"].includes(record.provider) ||
          session.provider !== record.provider || session.accountId !== record.accountId ||
          session.tenantId !== record.tenantId || session.subject !== record.subject) {
        return contextUnavailable();
      }
      const account = await identities.findAccountById(record.accountId);
      if (!account || account.status !== "active" || account.accountId !== record.accountId ||
          !validIdentifier(account.tenantId) || account.tenantId !== record.tenantId ||
          (record.subject !== account.accountId && record.subject !== account.username)) {
        return contextUnavailable();
      }
      const expiresAt = Math.min(session.expiresAt, Math.floor(absolute / 1000), Math.floor(idle / 1000));
      if (!Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(Date.now() / 1000)) {
        return contextUnavailable();
      }
      // Stable actor is the CURRENT account id, never the mutable display name.
      // This is a self-read snapshot, not permission or an approval receipt.
      body = { subject: account.accountId, accountId: account.accountId, tenantId: account.tenantId, expiresAt };
    } catch {
      return contextUnavailable();
    }
  }
  const response = NextResponse.json(
    body,
    { headers: approvalContext ? contextHeaders : { "cache-control": "no-store" } },
  );
  if (session.legacyUpgrade) {
    response.cookies.set(
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
  return response;
}
