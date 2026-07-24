export type ProductMode = "production" | "poc";

const PRODUCTION_DEPLOYMENTS = new Set(["prod", "production", "staging"]);
const PRODUCTION_PRODUCT_MODES = new Set(["live", "prod", "production"]);
const LOCAL_DEPLOYMENTS = new Set(["dev", "development", "local", "test"]);

function normalized(value: string | undefined): string {
  return value?.trim().toLowerCase() ?? "";
}

function enabled(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes(normalized(value));
}

/**
 * Resolve the data mode from server-owned runtime controls.
 *
 * Public flags can make a build stricter, but never downgrade a production
 * runtime to POC. Tests are isolated automatically; local fixture mode must be
 * selected explicitly with ODP_PRODUCT_MODE=poc.
 */
export function resolveProductMode(
  environment: Record<string, string | undefined> = process.env,
): ProductMode {
  const nodeEnv = normalized(environment.NODE_ENV);
  const deployEnv = normalized(
    environment.ODP_DEPLOY_ENV ??
      environment.ODAY_ENV ??
      environment.ODP_ENV,
  );
  const serverMode = normalized(environment.ODP_PRODUCT_MODE);
  const publicMode = normalized(environment.NEXT_PUBLIC_ODP_PRODUCT_MODE);

  if (
    nodeEnv === "production" ||
    PRODUCTION_DEPLOYMENTS.has(deployEnv) ||
    PRODUCTION_PRODUCT_MODES.has(serverMode) ||
    PRODUCTION_PRODUCT_MODES.has(publicMode) ||
    enabled(environment.ODP_REQUIRE_LIVE_DATA) ||
    enabled(environment.NEXT_PUBLIC_PRODUCTION_MODE)
  ) {
    return "production";
  }

  if (nodeEnv === "test" || deployEnv === "test") {
    return "poc";
  }

  if (
    serverMode === "poc" &&
    (!deployEnv || LOCAL_DEPLOYMENTS.has(deployEnv)) &&
    nodeEnv !== "production"
  ) {
    return "poc";
  }

  if (
    publicMode === "poc" &&
    LOCAL_DEPLOYMENTS.has(deployEnv) &&
    (nodeEnv === "development" || nodeEnv === "test")
  ) {
    return "poc";
  }

  return "production";
}

export function isProductionMode(
  environment?: Record<string, string | undefined>,
): boolean {
  return resolveProductMode(environment) === "production";
}
