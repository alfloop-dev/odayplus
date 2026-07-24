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

export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (!isProductionWebRuntime()) return NextResponse.next();

  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);
  if (session) return NextResponse.next();

  const loginUrl = new URL("/login", request.url);
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

