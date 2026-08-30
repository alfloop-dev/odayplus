import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  rotateWebSession,
  sealWebSession,
  webSessionCookieName,
  webSessionCookieOptions,
} from "../../../lib/auth/session";
import { verifyCsrfOrigin } from "../../../lib/auth/runtime";
import {
  mintLocalJwt,
  validatePasswordPolicy,
} from "../../../lib/auth/localAuth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  // 1. Session verification
  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);

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

  // 2. CSRF Verification
  if (!verifyCsrfOrigin(request)) {
    return NextResponse.json(
      {
        error: {
          code: "CSRF_VERIFICATION_FAILED",
          summary: "CSRF verification failed.",
        },
      },
      { status: 403, headers: { "cache-control": "no-store" } },
    );
  }

  // 3. Parse payload
  let currentPassword = "";
  let newPassword = "";
  const contentType = request.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    try {
      const json = (await request.json()) as Record<string, unknown>;
      currentPassword =
        typeof json.currentPassword === "string" ? json.currentPassword : "";
      newPassword = typeof json.newPassword === "string" ? json.newPassword : "";
    } catch {
      // JSON parse error
    }
  } else if (
    contentType.includes("application/x-www-form-urlencoded") ||
    contentType.includes("multipart/form-data")
  ) {
    try {
      const formData = await request.formData();
      currentPassword = String(formData.get("currentPassword") || "");
      newPassword = String(formData.get("newPassword") || "");
    } catch {
      // Form parse error
    }
  }

  if (!currentPassword || !newPassword) {
    return NextResponse.json(
      {
        error: {
          code: "AUTH_INVALID_CREDENTIALS",
          summary: "Current and new passwords are required.",
        },
      },
      { status: 400, headers: { "cache-control": "no-store" } },
    );
  }

  // 4. Validate new password against policy
  const policyResult = validatePasswordPolicy(newPassword, session.subject);
  if (!policyResult.valid) {
    return NextResponse.json(
      {
        error: {
          code: policyResult.code,
          summary: policyResult.reason,
        },
      },
      { status: 400, headers: { "cache-control": "no-store" } },
    );
  }

  // 5. Rotate session upon password change (Contract §5.3.2)
  const now = Math.floor(Date.now() / 1000);
  const newSid = crypto.randomUUID();
  const newAccessToken = await mintLocalJwt({
    subject: session.subject,
    sid: newSid,
    nowSeconds: now,
  });

  const rotatedSession = await rotateWebSession(session, {
    newAccessToken,
    nowSeconds: now,
  });

  const sealedCookie = await sealWebSession(rotatedSession);

  const response = NextResponse.json(
    {
      ok: true,
      summary: "Password changed successfully.",
    },
    { status: 200, headers: { "cache-control": "no-store" } },
  );

  response.cookies.set(
    webSessionCookieName,
    sealedCookie,
    webSessionCookieOptions,
  );

  return response;
}
