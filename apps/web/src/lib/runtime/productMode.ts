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

function currentRuntimeEnvironment(): Record<string, string | undefined> {
  return {
    NODE_ENV: process.env.NODE_ENV,
    ODP_DEPLOY_ENV: process.env.ODP_DEPLOY_ENV,
    ODAY_ENV: process.env.ODAY_ENV,
    ODP_ENV: process.env.ODP_ENV,
    ODP_PRODUCT_MODE: process.env.ODP_PRODUCT_MODE,
    ODP_E2E_MODE: process.env.ODP_E2E_MODE,
    ODP_REQUIRE_LIVE_DATA: process.env.ODP_REQUIRE_LIVE_DATA,
    NEXT_PUBLIC_ODP_DEPLOY_ENV: process.env.NEXT_PUBLIC_ODP_DEPLOY_ENV,
    NEXT_PUBLIC_ODP_PRODUCT_MODE: process.env.NEXT_PUBLIC_ODP_PRODUCT_MODE,
    NEXT_PUBLIC_ODP_E2E_MODE: process.env.NEXT_PUBLIC_ODP_E2E_MODE,
    NEXT_PUBLIC_PRODUCTION_MODE: process.env.NEXT_PUBLIC_PRODUCTION_MODE,
  };
}

export function isIsolatedE2eRuntime(
  environment: Record<string, string | undefined> = currentRuntimeEnvironment(),
): boolean {
  const deployEnv = normalized(
    environment.ODP_DEPLOY_ENV ??
      environment.ODAY_ENV ??
      environment.ODP_ENV ??
      environment.NEXT_PUBLIC_ODP_DEPLOY_ENV,
  );
  const productMode = normalized(
    environment.ODP_PRODUCT_MODE ??
      environment.NEXT_PUBLIC_ODP_PRODUCT_MODE,
  );
  const e2eMode = enabled(
    environment.ODP_E2E_MODE ??
      environment.NEXT_PUBLIC_ODP_E2E_MODE,
  );

  return (
    deployEnv === "e2e" &&
    productMode === "poc" &&
    e2eMode &&
    !enabled(environment.ODP_REQUIRE_LIVE_DATA) &&
    !enabled(environment.NEXT_PUBLIC_PRODUCTION_MODE)
  );
}

/**
 * Resolve the data mode from server-owned runtime controls.
 *
 * Public flags can make a build stricter, but never downgrade a production
 * runtime to POC. The browser receives the same three explicit markers only in
 * the isolated E2E image so a production Next build can render its test data.
 * Local fixture mode must be selected explicitly with ODP_PRODUCT_MODE=poc.
 */
export function resolveProductMode(
  environment: Record<string, string | undefined> = currentRuntimeEnvironment(),
): ProductMode {
  const nodeEnv = normalized(environment.NODE_ENV);
  const deployEnv = normalized(
    environment.ODP_DEPLOY_ENV ??
      environment.ODAY_ENV ??
      environment.ODP_ENV,
  );
  const serverMode = normalized(environment.ODP_PRODUCT_MODE);
  const publicMode = normalized(environment.NEXT_PUBLIC_ODP_PRODUCT_MODE);
  const isolatedE2eRuntime =
    isIsolatedE2eRuntime(environment) &&
    !PRODUCTION_PRODUCT_MODES.has(publicMode);

  // The release-gate container runs a production Next build, so NODE_ENV alone
  // cannot distinguish it from a deployable runtime. Require three independent
  // E2E-image markers before allowing the isolated fixture lane; any production
  // or require-live marker still wins.
  if (isolatedE2eRuntime) {
    return "poc";
  }

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
