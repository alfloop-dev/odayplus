export const WEB_SESSION_COOKIE = "__Host-oday_web_session";
export const OIDC_TRANSACTION_COOKIE = "__Host-oday_oidc_transaction";

export const SESSION_COOKIE_MAX_AGE_SECONDS = 8 * 60 * 60;
export const OIDC_TRANSACTION_MAX_AGE_SECONDS = 10 * 60;

export function isProductionWebRuntime(
  environment: NodeJS.ProcessEnv = process.env,
): boolean {
  return isProductionMode(environment);
}

export function allowLegacyTrustedHeaders(
  environment: NodeJS.ProcessEnv = process.env,
): boolean {
  if (isProductionWebRuntime(environment)) return false;
  return environment.ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS !== "false";
}

export function safeReturnTo(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/operator";
  }

  if (/[\u0000-\u001f\u007f]/.test(value)) {
    return "/operator";
  }

  try {
    const parsed = new URL(value, "https://oday.plus");
    if (parsed.origin !== "https://oday.plus") return "/operator";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/operator";
  }
}

export function resolveWebBaseUrl(
  requestOrigin: string,
  environment: NodeJS.ProcessEnv = process.env,
): string {
  const configured = environment.ODP_WEB_BASE_URL?.trim();
  if (isProductionWebRuntime(environment) && !configured) {
    throw new Error("ODP_WEB_BASE_URL is required in production");
  }
  const result = new URL(configured || requestOrigin);

  if (
    isProductionWebRuntime(environment) &&
    result.protocol !== "https:" &&
    result.hostname !== "localhost" &&
    result.hostname !== "127.0.0.1"
  ) {
    throw new Error("ODP_WEB_BASE_URL must use https in production");
  }

  return result.origin;
}
import { isProductionMode } from "../runtime/productMode";
