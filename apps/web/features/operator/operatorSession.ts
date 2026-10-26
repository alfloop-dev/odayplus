/**
 * Browser-side session actions for the Operator surfaces.
 *
 * Task: ODP-DEV-ADMIN-RELEASE-READINESS-001
 * Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5, §7.2
 *
 * Sign-out goes through the real `/auth/logout` route, which revokes the
 * durable identity.sessions row before clearing the cookie. The UI only
 * navigates away once that route confirms the revocation; a failed sign-out is
 * reported, never presented as success.
 */

export const LOGIN_PATH = "/login";
export const ADMIN_PATH = "/operator?view=admin";
export const PASSWORD_CHANGE_PATH = "/operator?view=password";
export const PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED";

export type SignOutResult = { ok: true } | { ok: false; status: number | null };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

function defaultNavigate(path: string): void {
  window.location.assign(path);
}

export async function signOutOperator(
  fetchImpl: FetchLike = (input, init) => fetch(input, init),
  navigate: (path: string) => void = defaultNavigate,
): Promise<SignOutResult> {
  let response: Response;
  try {
    response = await fetchImpl("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
  } catch {
    return { ok: false, status: null };
  }
  if (!response.ok) return { ok: false, status: response.status };
  navigate(LOGIN_PATH);
  return { ok: true };
}

export type OperatorAccessDenial = "password_change_required" | "forbidden" | "unauthenticated";

/**
 * Classify an API refusal so a screen can send the user somewhere useful:
 * a pending first-login password rotation, an account without this
 * surface's permission (for example a pure platform_admin on the business
 * shell), or a missing/revoked session.
 */
export async function classifyAccessDenial(response: Response): Promise<OperatorAccessDenial | null> {
  if (response.status === 401) return "unauthenticated";
  if (response.status !== 403) return null;
  let detail = "";
  try {
    const body = (await response.clone().json()) as { detail?: unknown };
    detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? "");
  } catch {
    detail = "";
  }
  return detail.includes(PASSWORD_CHANGE_REQUIRED) ? "password_change_required" : "forbidden";
}
