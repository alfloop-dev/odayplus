import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  webSessionCookieName,
} from "./lib/auth/session";
import {
  isProductionWebRuntime,
  safeReturnTo,
} from "./lib/auth/runtime";

function resolveRequestOrigin(request: NextRequest): string {
  const configured = process.env.ODP_WEB_BASE_URL?.trim();
  if (configured) {
    try {
      return new URL(configured).origin;
    } catch {
      // Fall through to header inspection
    }
  }

  const forwardedProto = request.headers.get("x-forwarded-proto");
  const forwardedHost = request.headers.get("x-forwarded-host");
  const host = forwardedHost || request.headers.get("host");

  if (host) {
    const proto = (forwardedProto || request.nextUrl.protocol || "https").replace(/:$/, "");
    return `${proto}://${host}`;
  }

  return request.nextUrl.origin;
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (!isProductionWebRuntime()) return NextResponse.next();

  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);
  if (session) return NextResponse.next();

  const baseOrigin = resolveRequestOrigin(request);
  const loginUrl = new URL("/login", baseOrigin);
  loginUrl.searchParams.set(
    "returnTo",
    safeReturnTo(`${request.nextUrl.pathname}${request.nextUrl.search}`),
  );
  const response = NextResponse.redirect(loginUrl);
  response.cookies.set(webSessionCookieName, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}

export const config = {
  matcher: [
    "/((?!api/v1(?:/|$)|avm(?:/|$)|login(?:/|$)|auth/callback(?:/|$)|auth/logout(?:/|$)|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)",
  ],
};

