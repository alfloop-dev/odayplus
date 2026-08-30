import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import {
  isOidcEnabled,
  resolveWebBaseUrl,
  safeReturnTo,
  verifyCsrfOrigin,
} from "../../lib/auth/runtime";
import {
  oidcTransactionCookieName,
  oidcTransactionCookieOptions,
  readWebSession,
  sealOidcTransaction,
  sealWebSessionReference,
  webSessionCookieName,
  webSessionCookieOptions,
  type WebSession,
} from "../../lib/auth/session";
import { createAuthorizationRequest } from "../../lib/auth/oidc";
import {
  authenticateLocalCredentials,
  mintLocalJwt,
} from "../../lib/auth/localAuth";
import {
  DEFAULT_SESSION_IDLE_TIMEOUT_MS,
  getDefaultSessionStore,
  MAX_SESSION_ABSOLUTE_LIFETIME_MS,
} from "../../lib/auth/sessionStore";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderLoginFormHtml(options: {
  returnTo: string;
  error?: string | null;
  showOidc: boolean;
}): string {
  const returnToEscaped = escapeHtml(options.returnTo);

  let errorMessageHtml = "";
  if (options.error === "AUTH_INVALID_CREDENTIALS") {
    errorMessageHtml = `<div class="error-banner" role="alert">帳號或密碼錯誤，請重新輸入。</div>`;
  } else if (options.error === "AUTH_ACCOUNT_LOCKED") {
    errorMessageHtml = `<div class="error-banner" role="alert">帳號已被暫時鎖定，請稍後再試。</div>`;
  } else if (options.error === "CSRF_VERIFICATION_FAILED") {
    errorMessageHtml = `<div class="error-banner" role="alert">請求驗證失敗，請重新整理頁面後重試。</div>`;
  } else if (options.error) {
    errorMessageHtml = `<div class="error-banner" role="alert">登入失敗，請稍後重試。</div>`;
  }

  const oidcButtonHtml = options.showOidc
    ? `<div class="divider"><span>或</span></div>
       <a href="/login?provider=oidc&returnTo=${encodeURIComponent(
         options.returnTo,
       )}" class="btn btn-secondary oidc-btn">
         使用 OIDC 登入
       </a>`
    : "";

  return `<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>登入 - ODay Plus</title>
  <style>
    :root {
      --font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      --bg-primary: #f8fafc;
      --card-bg: #ffffff;
      --text-primary: #0f172a;
      --text-secondary: #475569;
      --border-color: #e2e8f0;
      --primary-color: #2563eb;
      --primary-hover: #1d4ed8;
      --error-bg: #fef2f2;
      --error-text: #b91c1c;
      --error-border: #fecaca;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-family);
      background-color: var(--bg-primary);
      color: var(--text-primary);
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 1rem;
    }
    .login-card {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 0.75rem;
      box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
      width: 100%;
      max-width: 26rem;
      padding: 2rem;
    }
    .login-header {
      text-align: center;
      margin-bottom: 1.5rem;
    }
    .brand-title {
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin-bottom: 0.25rem;
    }
    .brand-subtitle {
      font-size: 0.875rem;
      color: var(--text-secondary);
    }
    .form-group {
      margin-bottom: 1.25rem;
    }
    label {
      display: block;
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-primary);
      margin-bottom: 0.5rem;
    }
    input[type="text"],
    input[type="password"] {
      width: 100%;
      padding: 0.625rem 0.75rem;
      border: 1px solid var(--border-color);
      border-radius: 0.375rem;
      font-size: 0.875rem;
      outline: none;
      transition: border-color 0.15s ease-in-out;
    }
    input[type="text"]:focus,
    input[type="password"]:focus {
      border-color: var(--primary-color);
      box-shadow: 0 0 0 1px var(--primary-color);
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      padding: 0.625rem 1rem;
      font-size: 0.875rem;
      font-weight: 500;
      border-radius: 0.375rem;
      cursor: pointer;
      text-decoration: none;
      transition: background-color 0.15s ease-in-out;
      border: none;
    }
    .btn-primary {
      background-color: var(--primary-color);
      color: #ffffff;
    }
    .btn-primary:hover {
      background-color: var(--primary-hover);
    }
    .btn-secondary {
      background-color: #f1f5f9;
      color: var(--text-primary);
      border: 1px solid var(--border-color);
    }
    .btn-secondary:hover {
      background-color: #e2e8f0;
    }
    .error-banner {
      background-color: var(--error-bg);
      color: var(--error-text);
      border: 1px solid var(--error-border);
      border-radius: 0.375rem;
      padding: 0.75rem;
      font-size: 0.875rem;
      margin-bottom: 1.25rem;
    }
    .divider {
      display: flex;
      align-items: center;
      text-align: center;
      margin: 1.25rem 0;
      color: var(--text-secondary);
      font-size: 0.75rem;
    }
    .divider::before,
    .divider::after {
      content: '';
      flex: 1;
      border-bottom: 1px solid var(--border-color);
    }
    .divider span {
      padding: 0 0.5rem;
    }
  </style>
</head>
<body>
  <div class="login-card">
    <div class="login-header">
      <h1 class="brand-title">ODay Plus</h1>
      <p class="brand-subtitle">請登入您的帳號以繼續</p>
    </div>
    ${errorMessageHtml}
    <form action="/login" method="POST">
      <input type="hidden" name="returnTo" value="${returnToEscaped}" />
      <div class="form-group">
        <label for="username">使用者帳號</label>
        <input type="text" id="username" name="username" autocomplete="username" required autofocus />
      </div>
      <div class="form-group">
        <label for="password">密碼</label>
        <input type="password" id="password" name="password" autocomplete="current-password" required />
      </div>
      <button type="submit" class="btn btn-primary">登入</button>
    </form>
    ${oidcButtonHtml}
  </div>
</body>
</html>`;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const returnTo = safeReturnTo(request.nextUrl.searchParams.get("returnTo"));
  const provider = request.nextUrl.searchParams.get("provider");
  const errorParam = request.nextUrl.searchParams.get("error");

  // 1. If user already has a valid session, redirect directly to returnTo.
  const existingSession = await readWebSession(
    request.cookies.get(webSessionCookieName)?.value,
  ).catch(() => null);
  if (existingSession) {
    const response = NextResponse.redirect(
      new URL(returnTo, resolveWebBaseUrl(request.nextUrl.origin)),
    );
    if (existingSession.legacyUpgrade) {
      response.cookies.set(
        webSessionCookieName,
        await sealWebSessionReference(existingSession),
        {
          ...webSessionCookieOptions,
          maxAge: Math.max(
            1,
            existingSession.expiresAt - Math.floor(Date.now() / 1000),
          ),
        },
      );
    }
    return response;
  }

  // 2. Explicit OIDC flow request
  if (provider === "oidc") {
    let oidcActive = false;
    try {
      oidcActive = isOidcEnabled(process.env);
    } catch {
      oidcActive = false;
    }

    if (!oidcActive) {
      return NextResponse.json(
        {
          error: {
            code: "WEB_AUTH_PROVIDER_DISABLED",
            summary: "OIDC authentication provider is disabled.",
          },
        },
        { status: 503, headers: { "cache-control": "no-store" } },
      );
    }

    try {
      const { url, transaction } = await createAuthorizationRequest({
        requestOrigin: request.nextUrl.origin,
        returnTo,
      });
      const response = NextResponse.redirect(url);
      response.cookies.set(
        oidcTransactionCookieName,
        await sealOidcTransaction(transaction),
        oidcTransactionCookieOptions,
      );
      response.headers.set("cache-control", "no-store");
      return response;
    } catch {
      return NextResponse.json(
        {
          error: {
            code: "WEB_AUTH_NOT_CONFIGURED",
            summary: "Web authentication is not configured.",
          },
        },
        { status: 503, headers: { "cache-control": "no-store" } },
      );
    }
  }

  // 3. Default: Password-first Login Screen
  let showOidc = false;
  try {
    showOidc = isOidcEnabled(process.env);
  } catch {
    showOidc = false;
  }

  const html = renderLoginFormHtml({
    returnTo,
    error: errorParam,
    showOidc,
  });

  return new NextResponse(html, {
    status: errorParam ? 401 : 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  // 1. CSRF Verification
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

  // 2. Parse request payload
  let username = "";
  let password = "";
  let returnTo = "/operator";
  const contentType = request.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    try {
      const json = (await request.json()) as Record<string, unknown>;
      username = typeof json.username === "string" ? json.username : "";
      password = typeof json.password === "string" ? json.password : "";
      if (typeof json.returnTo === "string") {
        returnTo = json.returnTo;
      }
    } catch {
      // JSON parse error
    }
  } else if (
    contentType.includes("application/x-www-form-urlencoded") ||
    contentType.includes("multipart/form-data")
  ) {
    try {
      const formData = await request.formData();
      username = String(formData.get("username") || "");
      password = String(formData.get("password") || "");
      const r = formData.get("returnTo");
      if (r) returnTo = String(r);
    } catch {
      // Form parse error
    }
  }

  const targetReturnTo = safeReturnTo(returnTo);

  // 3. Authenticate credentials
  const authResult = await authenticateLocalCredentials(username, password);

  if (!authResult.ok) {
    const status = authResult.code === "AUTH_ACCOUNT_LOCKED" ? 423 : 401;

    // Check if client expects JSON
    const accept = request.headers.get("accept") || "";
    if (accept.includes("application/json") || contentType.includes("application/json")) {
      return NextResponse.json(
        {
          error: {
            code: authResult.code,
            summary: authResult.summary,
          },
        },
        { status, headers: { "cache-control": "no-store" } },
      );
    }

    // HTML Form Submission: Redirect back to /login with error query
    const loginRedirectUrl = new URL("/login", request.nextUrl.origin);
    loginRedirectUrl.searchParams.set("error", authResult.code);
    loginRedirectUrl.searchParams.set("returnTo", targetReturnTo);

    const redirectResponse = NextResponse.redirect(loginRedirectUrl, 303);
    redirectResponse.headers.set("cache-control", "no-store");
    return redirectResponse;
  }

  // 4. Session & Token Creation
  const now = Math.floor(Date.now() / 1000);
  const sid = crypto.randomUUID();
  const accessToken = await mintLocalJwt({
    // The API contract requires sub=account_id, never a browser-facing name.
    subject: authResult.account.id,
    sid,
    tenantId: authResult.account.tenantId,
    nowSeconds: now,
  });

  const session: WebSession = {
    kind: "web-session",
    accessToken,
    tokenType: "Bearer",
    subject: authResult.account.username,
    accountId: authResult.account.id,
    tenantId: authResult.account.tenantId,
    sid,
    provider: "local_password",
    issuedAt: now,
    expiresAt: now + webSessionCookieOptions.maxAge,
  };

  const store = getDefaultSessionStore(process.env);
  if (!store) {
    return NextResponse.json(
      {
        error: {
          code: "WEB_AUTH_NOT_CONFIGURED",
          summary: "Web authentication is not configured.",
        },
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
  try {
    await store.createSession({
      sessionId: sid,
      accountId: authResult.account.id,
      provider: "local_password",
      accessToken,
      subject: authResult.account.username,
      tenantId: authResult.account.tenantId,
      idleTimeoutMs: DEFAULT_SESSION_IDLE_TIMEOUT_MS,
      absoluteLifetimeMs: MAX_SESSION_ABSOLUTE_LIFETIME_MS,
    });
  } catch {
    // Never issue a cookie that cannot be checked against the authoritative
    // session store. This is fail-closed on a database outage.
    return NextResponse.json(
      {
        error: {
          code: "WEB_AUTH_UNAVAILABLE",
          summary: "Web authentication is temporarily unavailable.",
        },
      },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }

  // Only the opaque sid reference is sealed into the browser cookie. The
  // bearer remains in identity.sessions for the BFF to retrieve server-side.
  const sealedCookie = await sealWebSessionReference(session);

  // 5. Build Response
  const accept = request.headers.get("accept") || "";
  let response: NextResponse;

  if (accept.includes("application/json") || contentType.includes("application/json")) {
    response = NextResponse.json(
      {
        ok: true,
        subject: session.subject,
        returnTo: targetReturnTo,
      },
      { status: 200, headers: { "cache-control": "no-store" } },
    );
  } else {
    const destination = new URL(
      targetReturnTo,
      resolveWebBaseUrl(request.nextUrl.origin),
    );
    response = NextResponse.redirect(destination, 303);
    response.headers.set("cache-control", "no-store");
  }

  response.cookies.set(webSessionCookieName, sealedCookie, webSessionCookieOptions);
  return response;
}
