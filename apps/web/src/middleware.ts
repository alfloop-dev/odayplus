import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  readWebSession,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
} from "./lib/auth/session";
import {
  isProductionWebRuntime,
  safeReturnTo,
} from "./lib/auth/runtime";

// The middleware resolves every request against identity.sessions so that
// revocation and expiry are honoured on the server (see readWebSession). That
// lookup goes through PostgresSessionStore, which loads the `pg` driver, and
// `pg` is not loadable in the Edge Runtime: on Edge the dynamic import throws,
// readWebSession rejects, and the catch below turns every authenticated request
// into a /login redirect. Pinning the middleware to the Node.js runtime is what
// makes the durable session lookup actually work, and it is also why the build
// no longer reports Edge Runtime warnings for pg and its dependencies.
export const runtime = "nodejs";

export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (!isProductionWebRuntime()) return NextResponse.next();

  const session = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);
  if (session) {
    const response = NextResponse.next();
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
