/**
 * Product mode (ODP-PGAP-SHELL-001, acceptance §8).
 *
 * The rest of the app treats "no API base URL" as an implicit fixture mode and
 * degrades read regions to bundled sample rows with a disclosure badge. That is
 * right for a POC build and wrong for a product: a shell that silently shows
 * invented tasks, notifications or permissions is worse than one that says it
 * is degraded, because an operator cannot tell the difference.
 *
 * So the shell declares mode explicitly rather than inferring it:
 *
 * - `production` — never renders placeholder or fixture copy. A missing base
 *   URL or a failing API surfaces a recovery state, not sample data.
 * - `poc`        — the legacy behaviour: fixture fallback with disclosure.
 *
 * Resolution order: `ODP_PRODUCT_MODE` / `NEXT_PUBLIC_ODP_PRODUCT_MODE` when
 * set, otherwise `production` whenever `NODE_ENV === "production"`. Defaulting
 * a production build to `production` mode is the fail-closed direction: the
 * cost of a wrong guess is a visible "unavailable" state rather than fake data
 * presented as real.
 */

export type ProductMode = "production" | "poc";

export const PRODUCT_MODE_ENV_KEYS = [
  "ODP_PRODUCT_MODE",
  "NEXT_PUBLIC_ODP_PRODUCT_MODE",
] as const;

function readEnv(): Record<string, string | undefined> {
  return typeof process !== "undefined" && process.env ? process.env : {};
}

export function resolveProductMode(env: Record<string, string | undefined> = readEnv()): ProductMode {
  if (env.NODE_ENV === "production") return "production";
  for (const key of PRODUCT_MODE_ENV_KEYS) {
    const value = env[key]?.trim().toLowerCase();
    if (value === "production" || value === "poc") return value;
  }
  return "poc";
}

/** True when placeholder routes and fixture copy must not render. */
export function isProductionMode(env?: Record<string, string | undefined>): boolean {
  return resolveProductMode(env) === "production";
}
