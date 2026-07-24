import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  webSessionCookieName,
} from "../../../lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
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

  return NextResponse.json(
    { subject: session.subject, expiresAt: session.expiresAt },
    { headers: { "cache-control": "no-store" } },
  );
}

