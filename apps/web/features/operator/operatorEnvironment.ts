/**
 * Deployment identity for the Operator header badge.
 *
 * The badge names the environment the console is deployed to. It is resolved
 * from the deploy configuration (`ODP_DEPLOY_ENV`, set per Cloud Run service by
 * Terraform), never from the fixture / live-data policy: refusing fixtures
 * means "require real data", not "this is production".
 */
export type OperatorEnvironmentId =
  | "production"
  | "staging"
  | "dev"
  | "e2e"
  | "local"
  | "unset";

export type OperatorEnvironment = {
  id: OperatorEnvironmentId;
  label: string;
};

const ENVIRONMENT_ALIASES: Record<string, OperatorEnvironmentId> = {
  prod: "production",
  production: "production",
  staging: "staging",
  stage: "staging",
  dev: "dev",
  development: "dev",
  e2e: "e2e",
  test: "e2e",
  local: "local",
};

const ENVIRONMENT_LABELS: Record<OperatorEnvironmentId, string> = {
  production: "PRODUCTION",
  staging: "STAGING",
  dev: "DEV",
  e2e: "E2E",
  local: "LOCAL",
  unset: "ENV UNSET",
};

export function resolveOperatorEnvironment(value: string | null | undefined): OperatorEnvironment {
  const normalized = value?.trim().toLowerCase() ?? "";
  const id = ENVIRONMENT_ALIASES[normalized] ?? "unset";
  return { id, label: ENVIRONMENT_LABELS[id] };
}

/** Server-side lookup; the page passes the result to the client console. */
export function readDeploymentEnvironment(
  environment: Record<string, string | undefined> = process.env,
): string | undefined {
  return (
    environment.ODP_DEPLOY_ENV ??
    environment.ODAY_ENV ??
    environment.ODP_ENV ??
    environment.NEXT_PUBLIC_ODP_DEPLOY_ENV
  );
}
