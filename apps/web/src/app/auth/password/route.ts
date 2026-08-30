import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  rotateWebSession,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import {
  isProductionWebRuntime,
  verifyCsrfOrigin,
} from "../../../lib/auth/runtime";
import {
  mintLocalJwt,
  validatePasswordPolicy,
} from "../../../lib/auth/localAuth";
import {
  getDefaultIdentityStore,
  type IdentityAccount,
} from "../../../lib/auth/identityStore";
import {
  DEFAULT_SESSION_IDLE_TIMEOUT_MS,
  getDefaultSessionStore,
} from "../../../lib/auth/sessionStore";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorResponse(
  status: number,
  code: string,
  summary: string,
): NextResponse {
  return NextResponse.json(
    { error: { code, summary } },
    { status, headers: { "cache-control": "no-store" } },
  );
}

async function parsePasswords(
  request: NextRequest,
): Promise<{ currentPassword: string; newPassword: string }> {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const json = (await request.json()) as Record<string, unknown>;
      return {
        currentPassword:
          typeof json.currentPassword === "string" ? json.currentPassword : "",
        newPassword:
          typeof json.newPassword === "string" ? json.newPassword : "",
      };
    } catch {
      return { currentPassword: "", newPassword: "" };
    }
  }
  if (
    contentType.includes("application/x-www-form-urlencoded") ||
    contentType.includes("multipart/form-data")
  ) {
    try {
      const formData = await request.formData();
      return {
        currentPassword: String(formData.get("currentPassword") || ""),
        newPassword: String(formData.get("newPassword") || ""),
      };
    } catch {
      return { currentPassword: "", newPassword: "" };
    }
  }
  return { currentPassword: "", newPassword: "" };
}

function accountForSession(
  session: Awaited<ReturnType<typeof readWebSession>>,
  store: NonNullable<ReturnType<typeof getDefaultIdentityStore>>,
): Promise<IdentityAccount | null> {
  if (session?.accountId && store.findAccountById) {
    return store.findAccountById(session.accountId);
  }
  // This fallback only supports pre-P2 non-production fixtures. A production
  // session is always resolved with accountId from identity.sessions.
  if (session?.subject && !isProductionWebRuntime()) {
    return store.findAccountByUsername(session.subject);
  }
  return Promise.resolve(null);
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!verifyCsrfOrigin(request)) {
    return errorResponse(
      403,
      "CSRF_VERIFICATION_FAILED",
      "CSRF verification failed.",
    );
  }

  // Reject cross-site requests before resolving or touching the authoritative
  // session. A rejected password request must not slide its idle timeout.
  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);
  if (!session) {
    return errorResponse(
      401,
      "WEB_SESSION_REQUIRED",
      "A valid web session is required.",
    );
  }

  const { currentPassword, newPassword } = await parsePasswords(request);
  const normalizedCurrentPassword = currentPassword.normalize("NFKC");
  const normalizedNewPassword = newPassword.normalize("NFKC");
  if (!normalizedCurrentPassword || !normalizedNewPassword) {
    return errorResponse(
      400,
      "AUTH_INVALID_CREDENTIALS",
      "Current and new passwords are required.",
    );
  }

  const identityStore = getDefaultIdentityStore();
  let account: IdentityAccount | null = null;
  if (identityStore) {
    try {
      account = await accountForSession(session, identityStore);
      if (!account || account.status !== "active") {
        return errorResponse(
          401,
          "AUTH_INVALID_CREDENTIALS",
          "Current password verification failed.",
        );
      }
      const credential = await identityStore.getPasswordCredential(
        account.accountId,
      );
      if (!credential) {
        return errorResponse(
          401,
          "AUTH_INVALID_CREDENTIALS",
          "Current password verification failed.",
        );
      }
      const current = await identityStore.verifyPassword(
        credential.phcHash,
        normalizedCurrentPassword,
      );
      if (!current.valid) {
        return errorResponse(
          401,
          "AUTH_INVALID_CREDENTIALS",
          "Current password verification failed.",
        );
      }

      const policyResult = validatePasswordPolicy(
        normalizedNewPassword,
        account.username,
        account.email,
      );
      if (!policyResult.valid) {
        return errorResponse(400, policyResult.code, policyResult.reason);
      }

      // Hash exactly the normalized value that policy checked. IdentityStore
      // implementations use Argon2id and persist only its PHC representation.
      const newHash = await identityStore.hashPassword(normalizedNewPassword);
      await identityStore.changePassword(account.accountId, newHash);
    } catch {
      return errorResponse(
        503,
        "WEB_AUTH_UNAVAILABLE",
        "Web authentication is temporarily unavailable.",
      );
    }
  } else {
    // No identity store means there is no authoritative password to update.
    // Keep the small non-production UX fixture usable, but never claim success
    // in a production deployment without durable credential storage.
    const policyResult = validatePasswordPolicy(
      normalizedNewPassword,
      session.subject,
    );
    if (!policyResult.valid) {
      return errorResponse(400, policyResult.code, policyResult.reason);
    }
    if (isProductionWebRuntime()) {
      return errorResponse(
        503,
        "WEB_AUTH_NOT_CONFIGURED",
        "Web authentication is not configured.",
      );
    }
  }

  const sessionStore = getDefaultSessionStore(process.env);
  if (!sessionStore) {
    return errorResponse(
      503,
      "WEB_AUTH_UNAVAILABLE",
      "Web authentication is temporarily unavailable.",
    );
  }

  const accountId = account?.accountId || session.accountId || session.subject;
  if (!accountId) {
    return errorResponse(
      401,
      "WEB_SESSION_REQUIRED",
      "A valid web session is required.",
    );
  }
  const subject = account?.username || session.subject || accountId;
  const tenantId = account?.tenantId || session.tenantId;
  const now = Math.floor(Date.now() / 1000);
  const rotatedSession = await rotateWebSession(session, {
    nowSeconds: now,
    ttlSeconds: Math.max(1, session.expiresAt - now),
  });
  // Mint after rotation so the token and identity.sessions row agree exactly.
  const accessToken = await mintLocalJwt({
    subject: accountId,
    sid: rotatedSession.sid as string,
    tenantId,
    nowSeconds: now,
  });
  const finalSession = { ...rotatedSession, accessToken };

  try {
    await sessionStore.createSession({
      sessionId: finalSession.sid as string,
      accountId,
      provider: "local_password",
      accessToken,
      subject,
      tenantId,
      idleTimeoutMs: DEFAULT_SESSION_IDLE_TIMEOUT_MS,
      absoluteLifetimeMs: Math.max(1, (session.expiresAt - now) * 1000),
      rotatedFrom: session.sid,
    });
    // Password rotation invalidates this session and every other session for
    // the account. Excluding the new row keeps the response usable.
    await sessionStore.revokeAllForAccount(
      accountId,
      "password_change",
      finalSession.sid as string,
    );
  } catch {
    // Do not issue a new cookie when rotation/revocation was not durable.
    return errorResponse(
      503,
      "WEB_AUTH_UNAVAILABLE",
      "Web authentication is temporarily unavailable.",
    );
  }

  const response = NextResponse.json(
    { ok: true, summary: "Password changed successfully." },
    { status: 200, headers: { "cache-control": "no-store" } },
  );
  response.cookies.set(
    webSessionCookieName,
    await sealWebSessionReference(finalSession),
    {
      ...webSessionCookieOptions,
      maxAge: Math.max(1, finalSession.expiresAt - now),
    },
  );
  return response;
}
